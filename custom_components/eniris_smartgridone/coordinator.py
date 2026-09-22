"""Data coordinator for Eniris SmartgridOne."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
from typing import Any, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EnirisApiClient, EnirisApiError, EnirisAuthError, EnirisRateLimitError
from .const import (
    CONF_REFRESH_TOKEN,
    CONF_REFRESH_TOKEN_CREATED_AT,
    DEFAULT_SCAN_INTERVAL,
    FAST_RETENTION_POLICY,
    METADATA_REFRESH_INTERVAL,
    MINUTE_SCAN_INTERVAL,
    DOMAIN,
    REFRESH_TOKEN_RENEW_INTERVAL,
    TELEMETRY_FIELDS,
)
from .models import EnirisController, EnirisDevice, TelemetrySource, group_controllers, parse_devices
from .telemetry import SensorKey, SensorValue, build_query, parse_telemetry_responses

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")


@dataclass(slots=True)
class EnirisData:
    """Cached coordinator data."""

    controllers: list[EnirisController] = field(default_factory=list)
    sensors: dict[SensorKey, SensorValue] = field(default_factory=dict)
    expected: dict[SensorKey, tuple[EnirisDevice, TelemetrySource]] = field(default_factory=dict)
    companies: list[dict[str, Any]] = field(default_factory=list)
    roles: list[dict[str, Any]] = field(default_factory=list)
    monitors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def devices(self) -> list[EnirisDevice]:
        """Return all physical devices discovered under controllers."""
        result: list[EnirisDevice] = []
        for controller in self.controllers:
            if controller.device.id:
                result.append(controller.device)
            result.extend(controller.children)
        return result


class EnirisDataUpdateCoordinator(DataUpdateCoordinator[EnirisData]):
    """Coordinate Eniris discovery and telemetry polling."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api_client: EnirisApiClient,
        controller_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{controller_id}",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.config_entry = entry
        self.api_client = api_client
        self.controller_id = controller_id
        self._metadata: EnirisData | None = None
        self._metadata_fetched_at: datetime | None = None
        self._slow_values: dict[SensorKey, SensorValue] = {}
        self._slow_fetched_at: datetime | None = None

    async def _async_update_data(self) -> EnirisData:
        """Fetch metadata when due, 1 s telemetry every tick and 1 min telemetry when due."""
        now = datetime.now(UTC)
        try:
            await self._async_renew_refresh_token_if_needed()
            if _is_due(self._metadata_fetched_at, METADATA_REFRESH_INTERVAL, now):
                self._metadata = await self._async_fetch_metadata()
                self._metadata_fetched_at = now
            metadata = self._metadata
            assert metadata is not None
            controller_devices = [
                device
                for device in metadata.controllers[0].children
                if device.should_expose_as_device
            ]

            fetch_slow = _is_due(self._slow_fetched_at, MINUTE_SCAN_INTERVAL, now)
            fetched = await self._async_fetch_sensor_values(
                controller_devices,
                lambda source: fetch_slow or source.retention_policy == FAST_RETENTION_POLICY,
            )
            if fetch_slow:
                self._slow_values = {
                    key: value
                    for key, value in fetched.items()
                    if value.source.retention_policy != FAST_RETENTION_POLICY
                }
                self._slow_fetched_at = now
            fast_values = {
                key: value
                for key, value in fetched.items()
                if value.source.retention_policy == FAST_RETENTION_POLICY
            }
            sensors = {**self._slow_values, **fast_values}
        except EnirisAuthError as err:
            raise ConfigEntryAuthFailed(f"Eniris authentication failed: {err}") from err
        except EnirisRateLimitError as err:
            raise UpdateFailed(f"Eniris rate limit reached: {err}") from err
        except EnirisApiError as err:
            raise UpdateFailed(f"Error communicating with Eniris: {err}") from err

        return EnirisData(
            controllers=metadata.controllers,
            sensors=sensors,
            expected=metadata.expected,
            companies=metadata.companies,
            roles=metadata.roles,
            monitors=metadata.monitors,
        )

    async def _async_fetch_metadata(self) -> EnirisData:
        """Fetch companies, roles, monitors and the device tree for this controller."""
        companies = await self.api_client.companies()
        roles = await self.api_client.roles()
        monitors = await self.api_client.monitors()
        device_payload = await self.api_client.devices()
        devices = parse_devices(device_payload or {})
        controllers = group_controllers(devices)
        controller = self._controller_from_discovery(controllers)
        if controller is None:
            raise UpdateFailed(f"Controller {self.controller_id} was not found")
        controller_devices = [
            device for device in controller.children if device.should_expose_as_device
        ]
        return EnirisData(
            controllers=[controller],
            expected=_expected_sensor_keys(controller_devices),
            companies=companies,
            roles=roles,
            monitors=monitors,
        )

    async def _async_renew_refresh_token_if_needed(self) -> None:
        """Renew the refresh token before Eniris' 14-day expiry window."""
        token_created_at = self.config_entry.data.get(CONF_REFRESH_TOKEN_CREATED_AT)
        if not _refresh_token_needs_renewal(token_created_at):
            return

        old_refresh_token = self.config_entry.data[CONF_REFRESH_TOKEN]
        new_refresh_token = await self.api_client.async_renew_refresh_token()
        token_created_at = _utcnow_iso()
        _async_update_entries_sharing_refresh_token(
            self.hass,
            old_refresh_token,
            new_refresh_token,
            token_created_at,
        )
        _LOGGER.debug("Renewed Eniris refresh token")

    def _controller_from_discovery(
        self, controllers: list[EnirisController]
    ) -> EnirisController | None:
        """Return the controller for this config entry."""
        for controller in controllers:
            if self.controller_id in {
                controller.id,
                controller.serial_number,
                str(controller.device.id),
            }:
                return controller
        return None

    async def _async_fetch_sensor_values(
        self,
        devices: list[EnirisDevice],
        include: Callable[[TelemetrySource], bool] = lambda _source: True,
    ) -> dict[SensorKey, SensorValue]:
        """Fetch latest telemetry values for the selected sources of the given devices."""
        requests: list[tuple[EnirisDevice, TelemetrySource, dict[str, Any]]] = []
        for device in devices:
            for source in device.telemetry_sources:
                if not include(source):
                    continue
                query = build_query(source, list(TELEMETRY_FIELDS))
                if query is not None:
                    requests.append((device, source, query))

        values: dict[SensorKey, SensorValue] = {}
        for chunk in _chunks(requests, 1000):
            responses = await self.api_client.telemetry([query for _, _, query in chunk])
            values.update(parse_telemetry_responses(chunk, responses))
            values.update(await self._async_retry_failed_sources(chunk, responses))
        return values

    async def _async_retry_failed_sources(
        self,
        requests: list[tuple[EnirisDevice, TelemetrySource, dict[str, Any]]],
        responses: list[dict[str, Any]],
    ) -> dict[SensorKey, SensorValue]:
        """Retry failed grouped telemetry queries field-by-field."""
        retry_requests: list[tuple[EnirisDevice, TelemetrySource, dict[str, Any]]] = []
        for response in responses:
            statement_id = response.get("statement_id")
            if not response.get("error") or not isinstance(statement_id, int):
                continue
            if statement_id >= len(requests):
                continue
            device, source, _query = requests[statement_id]
            fields = source.fields or tuple(TELEMETRY_FIELDS)
            for field in fields:
                query = build_query(source, [field])
                if query is not None:
                    retry_requests.append((device, source, query))

        values: dict[SensorKey, SensorValue] = {}
        for chunk in _chunks(retry_requests, 1000):
            responses = await self.api_client.telemetry([query for _, _, query in chunk])
            values.update(parse_telemetry_responses(chunk, responses))
        return values


def _expected_sensor_keys(
    devices: list[EnirisDevice],
) -> dict[SensorKey, tuple[EnirisDevice, TelemetrySource]]:
    """Return one sensor key per field that device metadata says is recorded.

    Entities are created from this, so a device shows up (as unavailable) even
    when it has not reported recently, e.g. an inverter asleep at night.
    """
    expected: dict[SensorKey, tuple[EnirisDevice, TelemetrySource]] = {}
    for device in devices:
        for source in device.telemetry_sources:
            for telemetry_field in source.fields or ():
                if telemetry_field in TELEMETRY_FIELDS:
                    expected[SensorKey(device.id, source.key, telemetry_field)] = (device, source)
    return expected


def _is_due(last: datetime | None, interval: timedelta, now: datetime) -> bool:
    """Return true when something fetched at `last` should be fetched again.

    A small tolerance keeps a 60 s interval from slipping to 70 s when the
    coordinator ticks a few milliseconds early.
    """
    return last is None or now - last >= interval - timedelta(seconds=1)


def _chunks(values: list[_T], size: int) -> list[list[_T]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _refresh_token_needs_renewal(token_created_at: Any) -> bool:
    """Return true when a refresh token should be renewed proactively."""
    if not isinstance(token_created_at, str):
        return True

    try:
        created_at = datetime.fromisoformat(token_created_at)
    except ValueError:
        return True

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)

    return datetime.now(UTC) - created_at >= REFRESH_TOKEN_RENEW_INTERVAL


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _async_update_entries_sharing_refresh_token(
    hass: HomeAssistant,
    old_refresh_token: str,
    new_refresh_token: str,
    token_created_at: str,
) -> None:
    """Persist a renewed token for all controller entries from the same login."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(CONF_REFRESH_TOKEN) != old_refresh_token:
            continue

        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_REFRESH_TOKEN: new_refresh_token,
                CONF_REFRESH_TOKEN_CREATED_AT: token_created_at,
            },
        )
        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if coordinator is not None:
            coordinator.api_client.update_refresh_token(new_refresh_token)
