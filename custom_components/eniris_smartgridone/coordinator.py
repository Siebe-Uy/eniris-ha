"""Data coordinator for Eniris SmartgridOne."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
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

    async def _async_update_data(self) -> EnirisData:
        """Fetch latest Eniris metadata and telemetry."""
        try:
            await self._async_renew_refresh_token_if_needed()
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
            sensors = await self._async_fetch_sensor_values(controller_devices)
        except EnirisAuthError as err:
            raise ConfigEntryAuthFailed(f"Eniris authentication failed: {err}") from err
        except EnirisRateLimitError as err:
            raise UpdateFailed(f"Eniris rate limit reached: {err}") from err
        except EnirisApiError as err:
            raise UpdateFailed(f"Error communicating with Eniris: {err}") from err

        return EnirisData(
            controllers=[controller],
            sensors=sensors,
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
        self, devices: list[EnirisDevice]
    ) -> dict[SensorKey, SensorValue]:
        """Fetch latest telemetry values for all discovered devices."""
        requests: list[tuple[EnirisDevice, TelemetrySource, dict[str, Any]]] = []
        for device in devices:
            for source in device.telemetry_sources:
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
