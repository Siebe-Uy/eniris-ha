"""The Eniris SmartgridOne integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .api import EnirisApiClient, EnirisAuthClient
from .const import CONF_REFRESH_TOKEN, DOMAIN, PLATFORMS

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
    coordinator = EnirisDataUpdateCoordinator(hass, api_client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    if entry.title != entry.data.get(CONF_USERNAME):
        hass.config_entries.async_update_entry(entry, title=entry.data[CONF_USERNAME])

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnirisConfigEntry) -> bool:
    """Unload an Eniris SmartgridOne config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
