"""Sonorium integration for Home Assistant."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# URL paths for frontend resources
LOVELACE_CARD_URL = "/sonorium/sonorium-card.js"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Sonorium component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Sonorium from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "url": entry.data.get("url"),
    }

    # Register the Lovelace card JavaScript
    await _async_register_frontend(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Register the frontend resources."""
    # Get the path to the card JS file
    card_path = Path(__file__).parent / "lovelace" / "sonorium-card.js"

    if not card_path.exists():
        _LOGGER.error("Sonorium card not found at %s", card_path)
        return

    # Register static path to serve the JS file
    await hass.http.async_register_static_paths([
        StaticPathConfig(LOVELACE_CARD_URL, str(card_path), False)
    ])

    # Add the JS URL so it's loaded by the frontend
    add_extra_js_url(hass, LOVELACE_CARD_URL)

    _LOGGER.info("Sonorium Lovelace card registered at %s", LOVELACE_CARD_URL)
