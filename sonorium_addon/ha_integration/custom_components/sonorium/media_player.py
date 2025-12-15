"""Media player platform for Sonorium channels."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SonoriumDataUpdateCoordinator, SonoriumApiClient
from .const import DOMAIN, NUM_CHANNELS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sonorium media player entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: SonoriumDataUpdateCoordinator = data["coordinator"]
    client: SonoriumApiClient = data["client"]

    entities = []
    for channel_id in range(1, NUM_CHANNELS + 1):
        entities.append(
            SonoriumChannelEntity(
                coordinator=coordinator,
                client=client,
                channel_id=channel_id,
                entry_id=entry.entry_id,
            )
        )

    async_add_entities(entities)


class SonoriumChannelEntity(CoordinatorEntity, MediaPlayerEntity):
    """Representation of a Sonorium channel as a media player."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )

    def __init__(
        self,
        coordinator: SonoriumDataUpdateCoordinator,
        client: SonoriumApiClient,
        channel_id: int,
        entry_id: str,
    ) -> None:
        """Initialize the channel entity."""
        super().__init__(coordinator)
        self._client = client
        self._channel_id = channel_id
        self._entry_id = entry_id

        self._attr_unique_id = f"{entry_id}_channel_{channel_id}"
        self._attr_name = f"Channel {channel_id}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Sonorium",
            manufacturer="Sonorium",
            model="Ambient Sound System",
            sw_version=self.coordinator.data.get("status", {}).get("version", "unknown"),
        )

    @property
    def _channel_data(self) -> dict[str, Any] | None:
        """Get current channel data from coordinator."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("channels", {}).get(self._channel_id)

    @property
    def state(self) -> MediaPlayerState:
        """Return the state of the player."""
        channel = self._channel_data
        if not channel:
            return MediaPlayerState.IDLE

        state = channel.get("state", "idle")
        if state == "playing":
            return MediaPlayerState.PLAYING
        return MediaPlayerState.IDLE

    @property
    def media_title(self) -> str | None:
        """Return the title of current playing media."""
        channel = self._channel_data
        if channel:
            return channel.get("current_theme_name")
        return None

    @property
    def media_content_id(self) -> str | None:
        """Return the content ID of current playing media."""
        channel = self._channel_data
        if channel:
            return channel.get("current_theme")
        return None

    @property
    def source(self) -> str | None:
        """Return the current theme as source."""
        channel = self._channel_data
        if channel:
            return channel.get("current_theme_name") or channel.get("current_theme")
        return None

    @property
    def source_list(self) -> list[str] | None:
        """Return list of available themes as sources."""
        if not self.coordinator.data:
            return None
        themes = self.coordinator.data.get("themes", [])
        return [t.get("name", t.get("id", "Unknown")) for t in themes]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        channel = self._channel_data
        attrs = {
            "channel_id": self._channel_id,
        }
        if channel:
            attrs["client_count"] = channel.get("client_count", 0)
            attrs["stream_path"] = channel.get("stream_path")
            attrs["theme_id"] = channel.get("current_theme")
        return attrs

    async def async_media_play(self) -> None:
        """Play media - requires selecting a source/theme first."""
        # If there's a current theme, resume it
        channel = self._channel_data
        if channel and channel.get("current_theme"):
            await self._client.async_play_theme_on_channel(
                self._channel_id, channel["current_theme"]
            )
            await self.coordinator.async_request_refresh()

    async def async_media_stop(self) -> None:
        """Stop playback."""
        await self._client.async_stop_channel(self._channel_id)
        await self.coordinator.async_request_refresh()

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level (0.0 to 1.0)."""
        volume_int = int(volume * 100)
        await self._client.async_set_channel_volume(self._channel_id, volume_int)
        await self.coordinator.async_request_refresh()

    async def async_select_source(self, source: str) -> None:
        """Select a theme to play."""
        # Find theme ID from name
        themes = self.coordinator.data.get("themes", [])
        theme_id = None
        for theme in themes:
            if theme.get("name") == source or theme.get("id") == source:
                theme_id = theme.get("id")
                break

        if theme_id:
            await self._client.async_play_theme_on_channel(self._channel_id, theme_id)
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.warning("Theme not found: %s", source)

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a theme by ID."""
        await self._client.async_play_theme_on_channel(self._channel_id, media_id)
        await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
