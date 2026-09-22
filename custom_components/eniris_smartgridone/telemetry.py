"""Pure telemetry query and parsing helpers for Eniris."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from .const import TELEMETRY_FIELDS
from .models import EnirisDevice, TelemetrySource

_LOGGER = logging.getLogger(__name__)

# How far back a "latest value" query looks per retention policy.
# Eniris prices telemetry queries by the estimated scanned range. Without a time
# condition the server assumes a worst-case range (since 2000-01-01 for rp_one_m,
# about 140 tokens per field), which exceeds the free tier's 100-token bucket and
# returns HTTP 422 "The calculated cost exceeds your tier's maximum capacity".
# With limit=1 the cost is per started block of 1000 expected points (a "shard"),
# so any window up to ~1000 points costs the same: ~16 min for rp_one_s and
# ~16 h for rp_one_m.
LATEST_LOOKBACK: dict[str, timedelta] = {
    "rp_one_s": timedelta(minutes=15),
    "rp_one_m": timedelta(hours=6),
}
DEFAULT_LOOKBACK = timedelta(hours=1)
FUTURE_MARGIN = timedelta(minutes=1)


@dataclass(slots=True, frozen=True)
class SensorKey:
    """Unique key for a dynamic telemetry sensor."""

    device_id: int
    source_key: str
    field: str

    @property
    def unique_suffix(self) -> str:
        """Return a stable unique-id suffix."""
        safe_source = self.source_key.replace(":", "_").replace(",", "_").replace("=", "_")
        return f"{self.device_id}_{safe_source}_{self.field}"


@dataclass(slots=True)
class SensorValue:
    """Latest value and metadata for one telemetry field."""

    key: SensorKey
    device: EnirisDevice
    source: TelemetrySource
    value: Any
    timestamp: str | None = None


def build_query(
    source: TelemetrySource,
    fields: list[str],
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Build one latest-value telemetry query bounded to a short time window."""
    selected_fields = [field for field in fields if source.fields is None or field in source.fields]
    if not selected_fields:
        return None

    from_clause: dict[str, Any] = {"measurement": source.measurement}
    if source.database:
        from_clause["database"] = source.database
        from_clause["retentionPolicy"] = source.retention_policy
    elif source.namespace:
        namespace = dict(source.namespace)
        if namespace.get("version") == "1":
            namespace["retentionPolicy"] = source.retention_policy
        from_clause["namespace"] = namespace
    else:
        return None

    query: dict[str, Any] = {
        "select": selected_fields,
        "from": from_clause,
        "orderBy": "DESC",
        "limit": 1,
    }
    now = now or datetime.now(timezone.utc)
    lookback = LATEST_LOOKBACK.get(source.retention_policy, DEFAULT_LOOKBACK)
    where: dict[str, Any] = {
        "time": [
            {"operator": ">=", "value": _to_ms(now - lookback)},
            {"operator": "<", "value": _to_ms(now + FUTURE_MARGIN)},
        ]
    }
    if source.tags:
        where["tags"] = source.tags
    query["where"] = where

    return query


def _to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def parse_telemetry_responses(
    requests: list[tuple[EnirisDevice, TelemetrySource, dict[str, Any]]],
    responses: list[dict[str, Any]],
) -> dict[SensorKey, SensorValue]:
    """Parse telemetry API responses into sensor values."""
    values: dict[SensorKey, SensorValue] = {}

    for response in responses:
        statement_id = response.get("statement_id")
        if not isinstance(statement_id, int) or statement_id >= len(requests):
            continue

        device, source, _query = requests[statement_id]
        if response.get("error"):
            _LOGGER.debug(
                "Eniris telemetry query failed for device %s source %s: %s",
                device.id,
                source.key,
                response["error"],
            )
            continue

        for series in response.get("series", []) or []:
            columns = series.get("columns", [])
            rows = series.get("values", [])
            if not columns or not rows:
                continue

            row = rows[0]
            timestamp = _extract_timestamp(columns, row)
            for index, column in enumerate(columns):
                if column == "time" or column not in TELEMETRY_FIELDS or index >= len(row):
                    continue
                raw_value = row[index]
                if raw_value is None:
                    continue
                value = _normalize_value(column, raw_value)
                key = SensorKey(device.id, source.key, column)
                values[key] = SensorValue(
                    key=key,
                    device=device,
                    source=source,
                    value=value,
                    timestamp=timestamp,
                )

    return values


def _extract_timestamp(columns: list[str], row: list[Any]) -> str | None:
    if "time" not in columns:
        return None
    index = columns.index("time")
    if index >= len(row):
        return None
    value = row[index]
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
    return None


def _normalize_value(field: str, value: Any) -> Any:
    if field.endswith("_frac") and isinstance(value, int | float):
        return value * 100
    return value
