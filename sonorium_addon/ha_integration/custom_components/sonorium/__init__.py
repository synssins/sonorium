"""The Sonorium integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    DEFAULT_HOST,
    DEFAULT_PORT,
    API_STATUS,
    API_CHANNELS,
    API_THEMES,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER]


class SonoriumApiClient:
    """API client for Sonorium."""

    def __init__(self, host: str, port: int, session: aiohttp.ClientSession) -> None:
        """Initialize the API client."""
        self._host = host
        self._port = port
        self._session = session
        self._base_url = f"http://{host}:{port}"

    async def async_get_status(self) -> dict[str, Any]:
        """Get Sonorium status."""
        async with self._session.get(f"{self._base_url}{API_STATUS}") as response:
            response.raise_for_status()
            return await response.json()

    async def async_get_channels(self) -> list[dict[str, Any]]:
        """Get all channels."""
        async with self._session.get(f"{self._base_url}{API_CHANNELS}") as response:
            response.raise_for_status()
            return await response.json()

    async def async_get_themes(self) -> dict[str, Any]:
        """Get available themes."""
        async with self._session.get(f"{self._base_url}{API_THEMES}") as response:
            response.raise_for_status()
            return await response.json()

    async def async_play_theme_on_channel(
        self, channel_id: int, theme_id: str
    ) -> dict[str, Any]:
        """Play a theme on a specific channel."""
        url = f"{self._base_url}/api/channels/{channel_id}/play"
        async with self._session.post(
            url, json={"theme_id": theme_id}
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def async_stop_channel(self, channel_id: int) -> dict[str, Any]:
        """Stop playback on a channel."""
        url = f"{self._base_url}/api/channels/{channel_id}/stop"
        async with self._session.post(url) as response:
            response.raise_for_status()
            return await response.json()

    async def async_set_channel_volume(
        self, channel_id: int, volume: int
    ) -> dict[str, Any]:
        """Set volume for a channel (0-100)."""
        url = f"{self._base_url}/api/channels/{channel_id}/volume"
        async with self._session.post(url, json={"volume": volume}) as response:
            response.raise_for_status()
            return await response.json()

    async def async_test_connection(self) -> bool:
        """Test if connection works."""
        try:
            await self.async_get_status()
            return True
        except Exception:
            return False


class SonoriumDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Sonorium data."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SonoriumApiClient,
    ) -> None:
        """Initialize."""
        self.client = client
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Sonorium."""
        try:
            channels = await self.client.async_get_channels()
            status = await self.client.async_get_status()
            themes = await self.client.async_get_themes()

            return {
                "channels": {ch["id"]: ch for ch in channels},
                "status": status,
                "themes": themes.get("themes", []),
            }
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error communicating with Sonorium: {err}") from err


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Sonorium from a config entry."""
    host = entry.data.get(CONF_HOST, DEFAULT_HOST)
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)

    session = async_get_clientsession(hass)
    client = SonoriumApiClient(host, port, session)

    # Test connection
    if not await client.async_test_connection():
        _LOGGER.error("Cannot connect to Sonorium at %s:%s", host, port)
        return False

    coordinator = SonoriumDataUpdateCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
