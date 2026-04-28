"""Tests for Eniris discovery models."""

from unittest import TestCase

from custom_components.eniris_smartgridone.models import clean_controller_serial, group_controllers, parse_devices


class TestEnirisModels(TestCase):
    """Tests for Eniris discovery models."""

    def test_parse_devices_and_group_controller_children(self) -> None:
        """Devices are parsed and attached to their controller hub."""
        devices = parse_devices(
            {
                "device": [
                    {
                        "id": 1,
                        "lastUpdate": "2026-04-28T09:00:00Z",
                        "properties": {
                            "nodeId": "controller-1",
                            "nodeType": "smartgridoneController",
                            "name": "Main Controller",
                            "serialNumber": "OM12345",
                        },
                        "userRights": {"propertyEditabilities": {}, "monitorManagement": False},
                    },
                    {
                        "id": 2,
                        "lastUpdate": "2026-04-28T09:00:00Z",
                        "properties": {
                            "nodeId": "inverter-1",
                            "nodeType": "solarInverter",
                            "name": "Solar Inverter",
                            "controllerNodeId": "controller-1",
                        },
                        "userRights": {"propertyEditabilities": {}, "monitorManagement": False},
                    },
                ]
            }
        )

        controllers = group_controllers(devices)

        self.assertEqual(len(controllers), 1)
        self.assertEqual(controllers[0].name, "OM12345")
        self.assertEqual(controllers[0].serial_number, "OM12345")
        self.assertEqual([child.name for child in controllers[0].children], ["Solar Inverter"])

    def test_controller_serial_removes_site_suffix(self) -> None:
        """Controller entry names should use the serial without site suffixes."""
        self.assertEqual(
            clean_controller_serial("M1S240821VZLL5E230D_site_0"),
            "M1S240821VZLL5E230D",
        )

    def test_telemetry_sources_require_query_scope(self) -> None:
        """Telemetry sources are only emitted when Eniris database metadata exists."""
        device = parse_devices(
            {
                "device": [
                    {
                        "id": 1,
                        "lastUpdate": "2026-04-28T09:00:00Z",
                        "properties": {
                            "nodeId": "inverter-1",
                            "nodeType": "solarInverter",
                            "database": "site_telemetry",
                            "retentionPolicy": "rp_one_m",
                            "tags": {"nodeId": "inverter-1"},
                        },
                        "userRights": {"propertyEditabilities": {}, "monitorManagement": False},
                    }
                ]
            }
        )[0]

        self.assertEqual(device.telemetry_sources[0].measurement, "solarInverterMetrics")
        self.assertEqual(device.telemetry_sources[0].retention_policy, "rp_one_m")
        self.assertEqual(device.telemetry_sources[0].database, "site_telemetry")
