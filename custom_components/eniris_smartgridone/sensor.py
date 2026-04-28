"""Sensor platform for Eniris SmartgridOne."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent, UnitOfElectricPotential, UnitOfEnergy, UnitOfFrequency, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_CONTROLLER_ID,
    ATTR_DEVICE_ID,
    ATTR_MEASUREMENT,
    ATTR_RETENTION_POLICY,
    DOMAIN,
    TELEMETRY_FIELDS,
)
from .coordinator import EnirisDataUpdateCoordinator
from .models import EnirisController, EnirisDevice
from .telemetry import SensorKey


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eniris SmartgridOne sensor entities."""
    coordinator: EnirisDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    known_keys: set[SensorKey] = set()
    known_energy_keys: set[SensorKey] = set()

    @callback
    def add_new_entities() -> None:
        new_keys = set(coordinator.data.sensors) - known_keys
        new_energy_keys = _energy_helper_source_keys(coordinator) - known_energy_keys
        if not new_keys and not new_energy_keys:
            return
        known_keys.update(new_keys)
        known_energy_keys.update(new_energy_keys)
        entities = [EnirisSensor(coordinator, entry.entry_id, key) for key in new_keys]
        entities.extend(
            EnirisIntegratedEnergySensor(coordinator, entry.entry_id, key)
            for key in new_energy_keys
        )
        async_add_entities(entities)

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))


class EnirisSensor(CoordinatorEntity[EnirisDataUpdateCoordinator], SensorEntity):
    """A sensor backed by latest Eniris telemetry."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EnirisDataUpdateCoordinator,
        entry_id: str,
        key: SensorKey,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._key = key
        self.entity_description = _entity_description(key.field)
        self._attr_unique_id = f"{entry_id}_{key.unique_suffix}"
        self._attr_name = _sensor_name(key.field, key.source_key)

    @property
    def available(self) -> bool:
        """Return whether the entity has a current value."""
        return super().available and self._key in self.coordinator.data.sensors

    @property
    def native_value(self) -> Any:
        """Return the latest native value."""
        sensor = self.coordinator.data.sensors.get(self._key)
        return sensor.value if sensor else None

    @property
    def device_info(self) -> DeviceInfo:
        """Return Home Assistant device registry info."""
        sensor = self.coordinator.data.sensors[self._key]
        device = sensor.device
        controller = _controller_for_device(self.coordinator, device)
        is_controller = controller is not None and controller.device.id == device.id
        identifier = f"controller_{controller.id}" if is_controller else f"device_{device.id}"

        info: DeviceInfo = {
            "identifiers": {(DOMAIN, identifier)},
            "name": device.name,
            "manufacturer": device.manufacturer or "Eniris",
        }
        if device.model:
            info["model"] = device.model

        if controller and controller.device.id != device.id:
            info["via_device"] = (DOMAIN, f"controller_{controller.id}")
        return info

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the Eniris source."""
        sensor = self.coordinator.data.sensors.get(self._key)
        if sensor is None:
            return {}
        controller = _controller_for_device(self.coordinator, sensor.device)
        return {
            ATTR_DEVICE_ID: sensor.device.id,
            ATTR_CONTROLLER_ID: controller.id if controller else None,
            ATTR_MEASUREMENT: sensor.source.measurement,
            ATTR_RETENTION_POLICY: sensor.source.retention_policy,
            "last_sample": sensor.timestamp,
        }


class EnirisIntegratedEnergySensor(
    CoordinatorEntity[EnirisDataUpdateCoordinator],
    RestoreEntity,
    SensorEntity,
):
    """Derived energy sensor using a left Riemann sum over power."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EnirisDataUpdateCoordinator,
        entry_id: str,
        source_key: SensorKey,
    ) -> None:
        """Initialize the derived energy sensor."""
        super().__init__(coordinator)
        self._source_key = source_key
        self._attr_unique_id = f"{entry_id}_{source_key.unique_suffix}_integrated_energy"
        self._attr_name = f"{_humanize_field(source_key.field)} Energy {_retention_suffix(source_key.source_key) or ''}".strip()
        self.entity_description = SensorEntityDescription(
            key=f"{source_key.field}_integrated_energy",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
            state_class="total_increasing",
        )
        self._native_value = 0.0
        self._last_sample: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Restore accumulated energy across Home Assistant restarts."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state is not None:
            try:
                self._native_value = float(state.state)
            except (TypeError, ValueError):
                self._native_value = 0.0
            restored_sample = state.attributes.get("last_source_sample")
            if isinstance(restored_sample, str):
                self._last_sample = _parse_timestamp(restored_sample)

        if self._last_sample is None:
            source = self.coordinator.data.sensors.get(self._source_key)
            if source is not None:
                self._last_sample = _parse_timestamp(source.timestamp) or dt_util.utcnow()

    @property
    def available(self) -> bool:
        """Return whether the source power sensor is available."""
        return super().available and self._source_key in self.coordinator.data.sensors

    @property
    def native_value(self) -> float:
        """Return the accumulated energy in Wh."""
        return round(self._native_value, 3)

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the derived entity to the same device as its source sensor."""
        source = self.coordinator.data.sensors[self._source_key]
        return _device_info(self.coordinator, source.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the derived energy sensor."""
        source = self.coordinator.data.sensors.get(self._source_key)
        return {
            "integration_method": "left_riemann_sum",
            "source_field": self._source_key.field,
            "source_retention_policy": source.source.retention_policy if source else None,
            "last_source_sample": self._last_sample.isoformat() if self._last_sample else None,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update accumulated energy from the latest power sample."""
        source = self.coordinator.data.sensors.get(self._source_key)
        if source is None:
            self.async_write_ha_state()
            return

        sample_time = _parse_timestamp(source.timestamp) or dt_util.utcnow()
        if self._last_sample is not None and sample_time > self._last_sample:
            try:
                power_w = max(0.0, float(source.value))
            except (TypeError, ValueError):
                power_w = 0.0
            elapsed_hours = (sample_time - self._last_sample).total_seconds() / 3600
            self._native_value += power_w * elapsed_hours

        self._last_sample = sample_time
        self.async_write_ha_state()


def _controller_for_device(
    coordinator: EnirisDataUpdateCoordinator,
    device: EnirisDevice,
) -> EnirisController | None:
    for controller in coordinator.data.controllers:
        if controller.device.id == device.id:
            return controller
        if any(child.id == device.id for child in controller.children):
            return controller
    return None


def _device_info(
    coordinator: EnirisDataUpdateCoordinator,
    device: EnirisDevice,
) -> DeviceInfo:
    """Return shared HA device registry info for a discovered Eniris device."""
    controller = _controller_for_device(coordinator, device)
    is_controller = controller is not None and controller.device.id == device.id
    identifier = f"controller_{controller.id}" if is_controller else f"device_{device.id}"

    info: DeviceInfo = {
        "identifiers": {(DOMAIN, identifier)},
        "name": device.name,
        "manufacturer": device.manufacturer or "Eniris",
    }
    if device.model:
        info["model"] = device.model

    if controller and controller.device.id != device.id:
        info["via_device"] = (DOMAIN, f"controller_{controller.id}")
    return info


def _entity_description(field: str) -> SensorEntityDescription:
    unit, kind = TELEMETRY_FIELDS[field]
    return SensorEntityDescription(
        key=field,
        device_class=_device_class(kind),
        native_unit_of_measurement=_unit(unit),
        state_class=_state_class(kind, field),
    )


def _device_class(kind: str) -> SensorDeviceClass | str | None:
    mapping: dict[str, SensorDeviceClass | str] = {
        "battery": SensorDeviceClass.BATTERY,
        "current": SensorDeviceClass.CURRENT,
        "energy": SensorDeviceClass.ENERGY,
        "frequency": SensorDeviceClass.FREQUENCY,
        "power": SensorDeviceClass.POWER,
        "voltage": SensorDeviceClass.VOLTAGE,
    }
    return mapping.get(kind)


def _state_class(kind: str, field: str) -> str | None:
    if kind in {"current", "frequency", "power", "voltage", "battery"}:
        return "measurement"
    if kind == "energy" and _is_cumulative_energy_field(field):
        return "total_increasing"
    return None


def _unit(unit: str | None) -> str | None:
    mapping: dict[str, str] = {
        "%": PERCENTAGE,
        "A": UnitOfElectricCurrent.AMPERE,
        "Hz": UnitOfFrequency.HERTZ,
        "V": UnitOfElectricPotential.VOLT,
        "W": UnitOfPower.WATT,
        "Wh": UnitOfEnergy.WATT_HOUR,
        "VAr": "var",
    }
    return mapping.get(unit)


def _energy_helper_source_keys(coordinator: EnirisDataUpdateCoordinator) -> set[SensorKey]:
    """Return power sensor keys that need a derived cumulative energy entity."""
    return {
        key
        for key in coordinator.data.sensors
        if _is_integrable_power_field(key.field)
    }


def _is_integrable_power_field(field: str) -> bool:
    return field.startswith("actualPower") and field.endswith("_W")


def _is_cumulative_energy_field(field: str) -> bool:
    return field.endswith("AbsEnergyTot_Wh")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return dt_util.as_utc(parsed)
    return parsed


def _sensor_name(field: str, source_key: str) -> str:
    """Return a readable entity name with retention-policy suffix."""
    suffix = _retention_suffix(source_key)
    return f"{_humanize_field(field)} {suffix}" if suffix else _humanize_field(field)


def _retention_suffix(source_key: str) -> str | None:
    if ":rp_one_s:" in source_key:
        return "(s)"
    if ":rp_one_m:" in source_key:
        return "(m)"
    return None


def _humanize_field(field: str) -> str:
    base = re.sub(r"_(W|Wh|A|V|Hz|VAr|frac)$", "", field)
    words = re.findall(r"[A-Z]+(?=[A-Z][a-z]|[0-9]|\b)|[A-Z]?[a-z]+|[0-9]+", base)
    labels = [_FIELD_WORDS.get(word, word) for word in words]
    return " ".join(labels)


_FIELD_WORDS = {
    "Abs": "Absolute",
    "actual": "Actual",
    "battery": "Battery",
    "charged": "Charged",
    "children": "Children",
    "consumed": "Consumed",
    "current": "Current",
    "DC": "DC",
    "Delta": "Delta",
    "discharged": "Discharged",
    "energy": "Energy",
    "EV": "EV",
    "ev": "EV",
    "exported": "Exported",
    "frequency": "Frequency",
    "health": "Health",
    "imported": "Imported",
    "L1": "L1",
    "L2": "L2",
    "L3": "L3",
    "limit": "Limit",
    "mode": "Mode",
    "N": "N",
    "operation": "Operation",
    "other": "Other",
    "power": "Power",
    "produced": "Produced",
    "reactive": "Reactive",
    "reac": "Reactive",
    "requiring": "Requiring",
    "setpoint": "Setpoint",
    "signal": "Signal",
    "state": "State",
    "status": "Status",
    "storage": "Storage",
    "Tot": "Total",
    "voltage": "Voltage",
}
