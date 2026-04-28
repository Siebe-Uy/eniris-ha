"""The Eniris SmartgridOne integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .api import EnirisApiClient, EnirisAuthClient
from .const import CONF_CONTROLLER_ID, CONF_CONTROLLER_SERIAL, CONF_REFRESH_TOKEN, DOMAIN, PLATFORMS
from .models import clean_controller_serial, group_controllers, parse_devices

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
else:
    ConfigEntry = Any
    HomeAssistant = Any

EnirisConfigEntry = ConfigEntry


async def async_setup_entry(hass: HomeAssistant, entry: EnirisConfigEntry) -> bool:
    """Set up Eniris SmartgridOne from a config entry."""
    from homeassistant.const import CONF_USERNAME
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from .coordinator import EnirisDataUpdateCoordinator

    session = async_get_clientsession(hass)
    api_client = EnirisApiClient(
        session,
        entry.data[CONF_REFRESH_TOKEN],
        EnirisAuthClient(session),
    )
    if CONF_CONTROLLER_ID not in entry.data:
        if not await _async_migrate_account_entry(hass, entry, api_client):
            return False
    elif entry.data[CONF_CONTROLLER_SERIAL] != clean_controller_serial(entry.data[CONF_CONTROLLER_SERIAL]):
        _async_update_controller_serial(hass, entry, clean_controller_serial(entry.data[CONF_CONTROLLER_SERIAL]))

    controller_id = entry.data[CONF_CONTROLLER_ID]
    coordinator = EnirisDataUpdateCoordinator(hass, api_client, controller_id)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    _async_register_controller_device(hass, entry, coordinator.data.controllers[0])

    if entry.title != entry.data.get(CONF_CONTROLLER_SERIAL):
        hass.config_entries.async_update_entry(entry, title=entry.data[CONF_CONTROLLER_SERIAL])

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnirisConfigEntry) -> bool:
    """Unload an Eniris SmartgridOne config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


def _async_update_controller_serial(
    hass: HomeAssistant,
    entry: EnirisConfigEntry,
    controller_serial: str,
) -> None:
    """Normalize a controller config entry serial."""
    hass.config_entries.async_update_entry(
        entry,
        title=controller_serial,
        unique_id=controller_serial,
        data={**entry.data, CONF_CONTROLLER_SERIAL: controller_serial},
    )


def _async_register_controller_device(
    hass: HomeAssistant,
    entry: EnirisConfigEntry,
    controller: Any,
) -> None:
    """Create the controller hub device even when it has no own entities."""
    from homeassistant.helpers import device_registry as dr

    device = controller.device
    device_registry = dr.async_get(hass)
    device_info: dict[str, Any] = {
        "config_entry_id": entry.entry_id,
        "identifiers": {(DOMAIN, f"controller_{controller.id}")},
        "manufacturer": device.manufacturer or "Eniris",
        "name": controller.serial_number,
    }
    if device.model:
        device_info["model"] = device.model
    device_registry.async_get_or_create(**device_info)


async def _async_migrate_account_entry(
    hass: HomeAssistant,
    entry: EnirisConfigEntry,
    api_client: EnirisApiClient,
) -> bool:
    """Convert old account-scoped entries into controller-scoped entries."""
    from homeassistant import config_entries
    from homeassistant.const import CONF_USERNAME

    payload = await api_client.devices()
    controllers = group_controllers(parse_devices(payload or {}))
    if not controllers:
        return False

    first_controller = controllers[0]
    data = {
        **entry.data,
        CONF_CONTROLLER_ID: first_controller.id,
        CONF_CONTROLLER_SERIAL: first_controller.serial_number,
    }
    hass.config_entries.async_update_entry(
        entry,
        title=first_controller.serial_number,
        unique_id=first_controller.serial_number,
        data=data,
    )

    configured = {
        configured_entry.unique_id
        for configured_entry in hass.config_entries.async_entries(DOMAIN)
        if configured_entry.unique_id
    }
    for controller in controllers[1:]:
        if controller.serial_number in configured:
            continue
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_IMPORT},
                data={
                    CONF_USERNAME: entry.data[CONF_USERNAME],
                    CONF_REFRESH_TOKEN: entry.data[CONF_REFRESH_TOKEN],
                    CONF_CONTROLLER_ID: controller.id,
                    CONF_CONTROLLER_SERIAL: controller.serial_number,
                },
            )
        )

    return True
