"""
Announcements from Home Assistant (built-in plugin).

- Each channel gets a Home Assistant notify entity over MQTT ("Kitchen
  Announce"). A message sent to it (notify.send_message) is spoken on the
  channel: Home Assistant's text to speech makes the audio, Sonorium ducks
  the channel's mix, plays it and brings the mix back.
- The MQTT command sonorium/announce/set takes a JSON object:
  {"channels": ["<channel id>"] or "all", "text": "...", "url": "https://.../chime.mp3",
   "volume": 80}: a chime and/or spoken text.
- Home Assistant's text to speech is offered to the rest of Sonorium as the
  "homeassistant" provider (POST /api/announce, notification rules).

Home Assistant and MQTT act as an admin: an announcement overrides a zone's
owner and quiet hours, and starts a stopped channel for its length (set
"Announce on stopped channels" off to skip stopped channels).

Every announcement takes the same path (sonorium/core/announce.py) through
the plugin API 1.1 services: self.audio, self.mqtt, self.homeassistant.
"""
from __future__ import annotations

import json
import re

from sonorium.obs import logger
from sonorium.plugins.actions import ActionError
from sonorium.plugins.base import BasePlugin

PROVIDER = "homeassistant"
CHANNEL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class AnnouncementsPlugin(BasePlugin):
    id = "announcements"
    name = "Announcements"
    version = "1.0.0"
    description = "Home Assistant announces on channels: a notify entity per channel, spoken with its text to speech"
    author = "Sonorium"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._published: set[str] = set()

    def get_settings_schema(self) -> dict:
        return {
            "tts_engine": {"type": "string", "label": "Text to speech engine", "default": "",
                           "placeholder": "tts.google_translate_en_com",
                           "help": "A tts.* entity of Home Assistant. Empty: its first one."},
            "language": {"type": "string", "label": "Language", "default": "", "placeholder": "en"},
            "volume": {"type": "number", "label": "Volume", "min": 0, "max": 200, "default": 100},
            "start_stopped": {"type": "boolean", "label": "Announce on stopped channels", "default": True},
            "notify_entities": {"type": "boolean", "label": "Notify entity for each channel", "default": True},
        }

    # --- Topics ---

    def _channel_topic(self, channel_id: str) -> str:
        return f"{self.mqtt.prefix}/announce/{channel_id}/set"

    def _discovery_topic(self, channel_id: str) -> str:
        return f"homeassistant/notify/{self.mqtt.prefix}_{channel_id}_announce/config"

    # --- Lifecycle ---

    async def on_enable(self) -> None:
        self.audio.register_tts(PROVIDER, "Home Assistant", self.speak)
        self.mqtt.subscribe(f"{self.mqtt.prefix}/announce/+/set", self.on_channel_message)
        self.mqtt.subscribe(f"{self.mqtt.prefix}/announce/set", self.on_command)
        # MQTT may connect after plugins start: publish then too
        self.subscribe(["sonorium.started", "channel.created", "channel.deleted", "channel.changed"],
                       self.on_channels_changed)
        await self.publish_entities()

    async def on_disable(self) -> None:
        await self.remove_entities()

    async def on_settings_changed(self, settings: dict) -> None:
        if settings.get("notify_entities", True):
            await self.publish_entities()
        else:
            await self.remove_entities()

    # --- Home Assistant entities ---

    async def on_channels_changed(self, event) -> None:
        if event.type == "channel.deleted":
            channel_id = (event.data or {}).get("channel_id")
            if channel_id in self._published:
                await self.mqtt.publish(self._discovery_topic(channel_id), "", retain=True)
                self._published.discard(channel_id)
            return
        if event.type == "channel.changed" and "name" not in ((event.data or {}).get("changes") or {}):
            return
        await self.publish_entities()

    async def publish_entities(self) -> None:
        if not self.get_setting("notify_entities", True) or not self.mqtt.available:
            return
        for channel in await self.actions.list_channels():
            if not CHANNEL_ID.match(str(channel["id"])):
                continue
            slug = channel.get("entity_slug") or channel["id"]
            config = {
                "name": f"{channel['name']} Announce",
                "unique_id": f"{self.mqtt.prefix}_{channel['id']}_announce",
                "default_entity_id": f"notify.{self.mqtt.prefix}_{slug}_announce",
                "command_topic": self._channel_topic(channel["id"]),
                "icon": "mdi:bullhorn",
                "device": self.mqtt.device_info,
            }
            await self.mqtt.publish(self._discovery_topic(channel["id"]), json.dumps(config), retain=True)
            self._published.add(channel["id"])

    async def remove_entities(self) -> None:
        if not self.mqtt.available:
            return
        ids = set(self._published)
        try:
            ids |= {c["id"] for c in await self.actions.list_channels()}
        except ActionError:
            pass
        for channel_id in ids:
            if CHANNEL_ID.match(str(channel_id)):
                await self.mqtt.publish(self._discovery_topic(channel_id), "", retain=True)
        self._published.clear()

    # --- Messages ---

    async def on_channel_message(self, topic: str, payload: str) -> None:
        """A notify entity's message: speak it on its channel."""
        parts = topic.split("/")
        channel_id = parts[-2] if len(parts) >= 4 else ""
        text = (payload or "").strip()
        if not text or not CHANNEL_ID.match(channel_id):
            return
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except ValueError:
                data = None
            if isinstance(data, dict):  # a command_template sending JSON
                await self.announce([channel_id], data)
                return
        await self.announce([channel_id], {"text": text})

    async def on_command(self, topic: str, payload: str) -> None:
        """sonorium/announce/set: {"channels", "text", "url", "volume"}."""
        try:
            data = json.loads(payload or "")
        except ValueError:
            logger.warning("Announcements: sonorium/announce/set needs a JSON object (channels, text, url)")
            return
        if not isinstance(data, dict):
            return
        channels = data.get("channels")
        if channels in ("all", "*", None):
            channels = [c["id"] for c in await self.actions.list_channels()]
        elif isinstance(channels, str):
            channels = [channels]
        if not isinstance(channels, list) or not channels:
            logger.warning("Announcements: 'channels' must list channel ids or be \"all\"")
            return
        await self.announce([str(c) for c in channels], data)

    async def announce(self, channels: list, data: dict) -> None:
        text = data.get("text", data.get("message"))
        url = data.get("url")
        if url is not None and (not isinstance(url, str) or not url.lower().startswith(("http://", "https://"))):
            logger.warning("Announcements: 'url' must be an http(s) URL")
            url = None
        volume = data.get("volume", self.get_setting("volume", 100))
        if isinstance(volume, bool) or not isinstance(volume, (int, float)):
            volume = self.get_setting("volume", 100)
        try:
            await self.audio.announce(channels, text if isinstance(text, str) and text.strip() else None, sound=url,
                                      volume=volume, start_stopped=bool(self.get_setting("start_stopped", True)))
        except ActionError as e:
            logger.warning(f"Announcements: {e.message}")

    # --- Text to speech for the rest of Sonorium ---

    async def speak(self, message: str, language=None, engine=None, **options) -> bytes:
        return await self.homeassistant.tts(message, engine=engine or self.get_setting("tts_engine") or None,
                                            language=language or self.get_setting("language") or None,
                                            options=options.get("options"))
