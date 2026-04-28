"""Config flow for Eniris SmartgridOne."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EnirisApiClient, EnirisAuthClient, EnirisAuthError, EnirisTwoFactorRequired
from .const import CONF_REFRESH_TOKEN, DOMAIN

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

            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            auth_client = EnirisAuthClient(session)

            try:
                refresh_token = await auth_client.login(username, password)
                api_client = EnirisApiClient(session, refresh_token, auth_client)
                await api_client.async_get_access_token()
                await api_client.devices()
            except EnirisTwoFactorRequired:
                errors["base"] = "two_factor_required"
            except EnirisAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error validating Eniris credentials")
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=username,
                    data={
                        CONF_USERNAME: username,
                        CONF_REFRESH_TOKEN: refresh_token,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
