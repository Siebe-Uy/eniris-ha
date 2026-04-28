"""Tests for Eniris API helpers and telemetry parsing."""

from unittest import TestCase

from custom_components.eniris_smartgridone.api import normalize_token
from custom_components.eniris_smartgridone.models import TelemetrySource, parse_devices
from custom_components.eniris_smartgridone.telemetry import build_query, parse_telemetry_responses


class TestApiAndTelemetry(TestCase):
    """Tests for Eniris API helpers and telemetry parsing."""

    def test_normalize_token_accepts_plain_and_json_string_tokens(self) -> None:
        """The auth API docs show token responses as both text and quoted strings."""
        self.assertEqual(normalize_token("abc.def"), "abc.def")
        self.assertEqual(normalize_token('"abc.def"'), "abc.def")

    def test_build_query_uses_retention_policy_and_tags(self) -> None:
        """Telemetry queries include the source retention policy and device tags."""
        source = TelemetrySource(
            measurement="solarInverterMetrics",
            retention_policy="rp_one_s",
            database="site_telemetry",
            tags={"nodeId": "inverter-1"},
        )

        query = build_query(source, ["actualPowerTot_W"])

        self.assertEqual(
            query,
            {
                "select": ["actualPowerTot_W"],
                "from": {
                    "measurement": "solarInverterMetrics",
                    "database": "site_telemetry",
                    "retentionPolicy": "rp_one_s",
                },
                "orderBy": "DESC",
                "limit": 1,
                "where": {"tags": {"nodeId": "inverter-1"}},
            },
        )

    def test_build_query_limits_to_source_fields(self) -> None:
        """Queries only request fields listed in nodeInfluxSeries."""
        source = TelemetrySource(
            measurement="submeteringMetrics",
            retention_policy="rp_one_s",
            database="beauvent",
            tags={"nodeId": "meter-1"},
            fields=("actualPowerTot_W",),
        )

        query = build_query(source, ["actualPowerTot_W", "voltageL1N_V"])

        self.assertEqual(query["select"], ["actualPowerTot_W"])

    def test_parse_telemetry_response_extracts_latest_values(self) -> None:
        """Telemetry responses are converted into dynamic sensor values."""
        device = parse_devices(
            {
                "device": [
                    {
                        "id": 2,
                        "lastUpdate": "2026-04-28T09:00:00Z",
                        "properties": {"nodeId": "inverter-1"},
                        "userRights": {"propertyEditabilities": {}, "monitorManagement": False},
                    }
                ]
            }
        )[0]
        source = TelemetrySource(
            measurement="solarInverterMetrics",
            retention_policy="rp_one_m",
            database="site_telemetry",
            tags={"nodeId": "inverter-1"},
        )

        values = parse_telemetry_responses(
            [(device, source, {})],
            [
                {
                    "statement_id": 0,
                    "series": [
                        {
                            "name": "solarInverterMetrics",
                            "columns": ["time", "actualPowerTot_W", "stateOfCharge_frac"],
                            "values": [["2026-04-28T09:00:00Z", 1234, 0.42]],
                        }
                    ],
                }
            ],
        )

        by_field = {sensor.key.field: sensor.value for sensor in values.values()}
        self.assertEqual(by_field, {"actualPowerTot_W": 1234, "stateOfCharge_frac": 42.0})
