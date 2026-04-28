"""Config flow for Eniris SmartgridOne."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EnirisApiClient, EnirisAuthClient, EnirisAuthError, EnirisTwoFactorRequired
from .const import CONF_CONTROLLER_ID, CONF_CONTROLLER_SERIAL, CONF_REFRESH_TOKEN, DOMAIN
from .models import EnirisController, group_controllers, parse_devices

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class EnirisConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle an Eniris SmartgridOne config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]

            session = async_get_clientsession(self.hass)
            auth_client = EnirisAuthClient(session)

            try:
                refresh_token = await auth_client.login(username, password)
                api_client = EnirisApiClient(session, refresh_token, auth_client)
                await api_client.async_get_access_token()
                controllers = await _async_discover_controllers(api_client)
            except EnirisTwoFactorRequired:
                errors["base"] = "two_factor_required"
            except EnirisAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error validating Eniris credentials")
                errors["base"] = "cannot_connect"
            else:
                if not controllers:
                    errors["base"] = "no_controllers"
                else:
                    await self._async_import_additional_controllers(
                        username,
                        refresh_token,
                        controllers[1:],
                    )
                    return await self._async_create_controller_entry(
                        username,
                        refresh_token,
                        controllers[0],
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Import an additional controller discovered from the same account."""
        controller_serial = import_data[CONF_CONTROLLER_SERIAL]
        await self.async_set_unique_id(controller_serial)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=controller_serial, data=import_data)

    async def _async_create_controller_entry(
        self,
        username: str,
        refresh_token: str,
        controller: EnirisController,
    ) -> config_entries.ConfigFlowResult:
        """Create the first controller-scoped entry."""
        data = _entry_data(username, refresh_token, controller)
        controller_serial = data[CONF_CONTROLLER_SERIAL]
        await self.async_set_unique_id(controller_serial)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=controller_serial, data=data)

    async def _async_import_additional_controllers(
        self,
        username: str,
        refresh_token: str,
        controllers: list[EnirisController],
    ) -> None:
        """Start import flows for the other controllers in the account."""
        configured = {
            entry.unique_id
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.unique_id
        }
        for controller in controllers:
            data = _entry_data(username, refresh_token, controller)
            if data[CONF_CONTROLLER_SERIAL] in configured:
                continue
            self.hass.async_create_task(
                self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": config_entries.SOURCE_IMPORT},
                    data=data,
                )
            )


async def _async_discover_controllers(api_client: EnirisApiClient) -> list[EnirisController]:
    """Discover accessible SmartgridOne controllers for an account."""
    payload = await api_client.devices()
    return group_controllers(parse_devices(payload or {}))


def _entry_data(
    username: str,
    refresh_token: str,
    controller: EnirisController,
) -> dict[str, Any]:
    """Build config-entry data for one controller."""
    return {
        CONF_USERNAME: username,
        CONF_REFRESH_TOKEN: refresh_token,
        CONF_CONTROLLER_ID: controller.id,
        CONF_CONTROLLER_SERIAL: controller.serial_number,
    }
