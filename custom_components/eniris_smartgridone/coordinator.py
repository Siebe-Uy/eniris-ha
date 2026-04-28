"""Data coordinator for Eniris SmartgridOne."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, TypeVar

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EnirisApiClient, EnirisApiError, EnirisRateLimitError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, TELEMETRY_FIELDS
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

    def __init__(self, hass: HomeAssistant, api_client: EnirisApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.api_client = api_client

    async def _async_update_data(self) -> EnirisData:
        """Fetch latest Eniris metadata and telemetry."""
        try:
            companies = await self.api_client.companies()
            roles = await self.api_client.roles()
            monitors = await self.api_client.monitors()
            device_payload = await self.api_client.devices()
            devices = parse_devices(device_payload or {})
            controllers = group_controllers(devices)
            sensors = await self._async_fetch_sensor_values(devices)
        except EnirisRateLimitError as err:
            raise UpdateFailed(f"Eniris rate limit reached: {err}") from err
        except EnirisApiError as err:
            raise UpdateFailed(f"Error communicating with Eniris: {err}") from err

        return EnirisData(
            controllers=controllers,
            sensors=sensors,
            companies=companies,
            roles=roles,
            monitors=monitors,
        )

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
            for field in TELEMETRY_FIELDS:
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
