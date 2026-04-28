"""Tests for Eniris sensor naming."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import sys
import types


def _install_homeassistant_stubs() -> None:
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    sensor = types.ModuleType("homeassistant.components.sensor")
    config_entries = types.ModuleType("homeassistant.config_entries")
    const = types.ModuleType("homeassistant.const")
    core = types.ModuleType("homeassistant.core")
    helpers = types.ModuleType("homeassistant.helpers")
    device_registry = types.ModuleType("homeassistant.helpers.device_registry")
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    restore_state = types.ModuleType("homeassistant.helpers.restore_state")
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
    util = types.ModuleType("homeassistant.util")
    dt = types.ModuleType("homeassistant.util.dt")

    class SensorDeviceClass:
        BATTERY = "battery"
        CURRENT = "current"
        ENERGY = "energy"
        FREQUENCY = "frequency"
        POWER = "power"
        VOLTAGE = "voltage"

    class SensorEntity:
        pass

    class SensorEntityDescription:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class UnitOfElectricCurrent:
        AMPERE = "A"

    class UnitOfElectricPotential:
        VOLT = "V"

    class UnitOfEnergy:
        WATT_HOUR = "Wh"

    class UnitOfFrequency:
        HERTZ = "Hz"

    class UnitOfPower:
        WATT = "W"

    class CoordinatorEntity:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, coordinator):
            self.coordinator = coordinator

    class RestoreEntity:
        pass

    sensor.SensorDeviceClass = SensorDeviceClass
    sensor.SensorEntity = SensorEntity
    sensor.SensorEntityDescription = SensorEntityDescription
    config_entries.ConfigEntry = object
    const.PERCENTAGE = "%"
    const.UnitOfElectricCurrent = UnitOfElectricCurrent
    const.UnitOfElectricPotential = UnitOfElectricPotential
    const.UnitOfEnergy = UnitOfEnergy
    const.UnitOfFrequency = UnitOfFrequency
    const.UnitOfPower = UnitOfPower
    core.HomeAssistant = object
    core.callback = lambda func: func
    device_registry.DeviceInfo = dict
    entity_platform.AddEntitiesCallback = object
    restore_state.RestoreEntity = RestoreEntity
    update_coordinator.CoordinatorEntity = CoordinatorEntity
    dt.utcnow = MagicMock()
    dt.as_utc = lambda value: value

    modules = {
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.sensor": sensor,
        "homeassistant.config_entries": config_entries,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.device_registry": device_registry,
        "homeassistant.helpers.entity_platform": entity_platform,
        "homeassistant.helpers.restore_state": restore_state,
        "homeassistant.helpers.update_coordinator": update_coordinator,
        "homeassistant.util": util,
        "homeassistant.util.dt": dt,
    }
    sys.modules.update(modules)


class TestSensorNames(TestCase):
    """Tests for readable sensor names."""

    def test_sensor_names_are_readable_and_include_retention_policy(self) -> None:
        """Names include s/m suffix and avoid unit artifacts."""
        _install_homeassistant_stubs()
        with patch.dict(sys.modules, {"custom_components.eniris_smartgridone.coordinator": MagicMock()}):
            from custom_components.eniris_smartgridone.sensor import (
                ENERGY_DIRECTION_EXPORT,
                ENERGY_DIRECTION_IMPORT,
                _directional_power,
                _is_integrable_power_field,
                _sensor_name,
            )

            self.assertEqual(
                _sensor_name("actualPowerTot_W", "submeteringMetrics:rp_one_s:beauvent:nodeId=meter"),
                "Actual Power Total (s)",
            )
            self.assertEqual(
                _sensor_name(
                    "exportedEnergyDeltaTot_Wh",
                    "submeteringMetrics:rp_one_m:beauvent:nodeId=meter",
                ),
                "Exported Energy Delta Total (m)",
            )
            self.assertTrue(_is_integrable_power_field("actualPowerTot_W"))
            self.assertFalse(_is_integrable_power_field("actualPowerL1_W"))
            self.assertFalse(_is_integrable_power_field("powerSetpoint_W"))
            self.assertEqual(_directional_power(42.0, ENERGY_DIRECTION_IMPORT), 42.0)
            self.assertEqual(_directional_power(-42.0, ENERGY_DIRECTION_IMPORT), 0.0)
            self.assertEqual(_directional_power(42.0, ENERGY_DIRECTION_EXPORT), 0.0)
            self.assertEqual(_directional_power(-42.0, ENERGY_DIRECTION_EXPORT), 42.0)
