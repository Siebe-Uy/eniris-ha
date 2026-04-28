"""Sensor platform for Eniris SmartgridOne."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent, UnitOfElectricPotential, UnitOfEnergy, UnitOfFrequency, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

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

    @callback
    def add_new_entities() -> None:
        new_keys = set(coordinator.data.sensors) - known_keys
        if not new_keys:
            return
        known_keys.update(new_keys)
        async_add_entities(EnirisSensor(coordinator, entry.entry_id, key) for key in new_keys)

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
        self._attr_name = _humanize_field(key.field)

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


def _entity_description(field: str) -> SensorEntityDescription:
    unit, kind = TELEMETRY_FIELDS[field]
    return SensorEntityDescription(
        key=field,
        device_class=_device_class(kind),
        native_unit_of_measurement=_unit(unit),
        state_class=_state_class(kind),
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


def _state_class(kind: str) -> str | None:
    if kind in {"current", "frequency", "power", "voltage", "battery"}:
        return "measurement"
    if kind == "energy":
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


def _humanize_field(field: str) -> str:
    replacements: tuple[tuple[str, str], ...] = (
        ("Tot", " total"),
        ("Abs", " absolute"),
        ("Delta", " delta"),
        ("L1", " L1"),
        ("L2", " L2"),
        ("L3", " L3"),
        ("DC", " DC"),
        ("EV", " EV"),
        ("SoC", " SoC"),
        ("SoH", " SoH"),
        ("_W", ""),
        ("_Wh", ""),
        ("_A", ""),
        ("_V", ""),
        ("_Hz", ""),
        ("_VAr", ""),
        ("_frac", ""),
        ("_", " "),
    )
    value = field[0].upper() + field[1:]
    for old, new in replacements:
        value = value.replace(old, new)
    return " ".join(value.split())
