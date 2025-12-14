"""Config flow for Sonorium integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, CONF_URL, DEFAULT_URL

_LOGGER = logging.getLogger(__name__)


class SonoriumConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sonorium."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")

            # Test connection to Sonorium
            try:
                session = async_get_clientsession(self.hass)
                async with session.get(f"{url}/api/status", timeout=10) as response:
                    if response.status == 200:
                        # Success - create the entry
                        return self.async_create_entry(
                            title="Sonorium",
                            data={CONF_URL: url},
                        )
                    else:
                        errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Error connecting to Sonorium")
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_URL, default=DEFAULT_URL): str,
            }),
            errors=errors,
            description_placeholders={
                "default_url": DEFAULT_URL,
            },
        )
