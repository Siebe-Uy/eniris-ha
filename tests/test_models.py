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

    def test_real_device_metadata_uses_node_influx_series(self) -> None:
        """Real Eniris node metadata exposes telemetry via nodeInfluxSeries."""
        device = parse_devices(
            {
                "device": [
                    {
                        "id": 10,
                        "lastUpdate": "2026-04-28T10:39:37Z",
                        "properties": {
                            "name": "LGF Digital Smart Meter E360",
                            "nodeId": "VdqEOEgHDokACOOZ-LGF-E360",
                            "nodeParentsIds": ["M1S240821VZLL5E230D_site_0"],
                            "nodeType": "powerMeter",
                            "info": {
                                "manufacturer": "LGF",
                                "model": "Digital Smart Meter",
                                "serialNumber": "E360",
                            },
                            "nodeInfluxSeries": [
                                {
                                    "database": "beauvent",
                                    "fields": ["getMeasTime_s"],
                                    "measurement": "getMeasStats",
                                    "retentionPolicy": "rp_one_m",
                                    "tags": {"nodeId": "VdqEOEgHDokACOOZ-LGF-E360"},
                                },
                                {
                                    "database": "beauvent",
                                    "fields": [
                                        "actualPowerTot_W",
                                        "actualPowerL1_W",
                                        "importedAbsEnergyTot_Wh",
                                        "exportedEnergyDeltaTot_Wh",
                                        "voltageL1N_V",
                                        "currentL1_A",
                                    ],
                                    "measurement": "submeteringMetrics",
                                    "retentionPolicy": "rp_one_s",
                                    "tags": {
                                        "gatewayMAC": "VdqEOEgHDokACOOZ",
                                        "nodeId": "VdqEOEgHDokACOOZ-LGF-E360",
                                    },
                                },
                            ],
                        },
                        "userRights": {"propertyEditabilities": {}, "monitorManagement": False},
                    }
                ]
            }
        )[0]

        self.assertEqual(device.name, "LGF Digital Smart Meter E360")
        self.assertEqual(device.manufacturer, "LGF")
        self.assertEqual(device.model, "Digital Smart Meter")
        self.assertEqual(device.controller_node_id, "M1S240821VZLL5E230D_site_0")
        self.assertTrue(device.should_expose_as_device)
        self.assertEqual(len(device.telemetry_sources), 1)
        self.assertEqual(device.telemetry_sources[0].fields, ("actualPowerTot_W", "actualPowerL1_W", "voltageL1N_V", "currentL1_A"))

    def test_infrastructure_nodes_are_not_children(self) -> None:
        """Controller, controller site, and switchboard nodes are hidden."""
        devices = parse_devices(
            {
                "device": [
                    {
                        "id": 1,
                        "lastUpdate": "2026-04-28T09:00:00Z",
                        "properties": {
                            "nodeId": "M1S240821VZLL5E230D_site_0",
                            "nodeType": "smartgridControllerSite",
                            "name": "M1S240821VZLL5E230D_site_0",
                        },
                        "userRights": {"propertyEditabilities": {}, "monitorManagement": False},
                    },
                    {
                        "id": 2,
                        "lastUpdate": "2026-04-28T09:00:00Z",
                        "properties": {
                            "nodeId": "switchboard",
                            "nodeType": "switchboard",
                            "nodeParentsIds": ["M1S240821VZLL5E230D_site_0"],
                        },
                        "userRights": {"propertyEditabilities": {}, "monitorManagement": False},
                    },
                    {
                        "id": 3,
                        "lastUpdate": "2026-04-28T09:00:00Z",
                        "properties": {
                            "nodeId": "M1S240821VZLL5E230D",
                            "nodeType": "smartgridController",
                            "name": "M1S240821VZLL5E230D",
                        },
                        "userRights": {"propertyEditabilities": {}, "monitorManagement": False},
                    },
                ]
            }
        )

        controllers = group_controllers(devices)

        self.assertEqual(controllers[0].serial_number, "M1S240821VZLL5E230D")
        self.assertEqual(controllers[0].children, [])
