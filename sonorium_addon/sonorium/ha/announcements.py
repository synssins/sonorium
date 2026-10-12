"""
Announcements from Home Assistant and MQTT (a core feature, with the
Announcements switch in Settings > Advanced; on out of the box).

Sonorium doesn't decide when to announce: other systems send it announcements
and it plays them (core/announce.py). This module gives two of the routes:

- Home Assistant: each channel gets a notify entity over MQTT ("Kitchen
  Announce"). A message sent to it (notify.send_message) is spoken on the
  channel: Home Assistant's text to speech makes the audio, Sonorium ducks
  the channel's mix, plays it and brings the mix back. A message that is a
  JSON object takes the same fields as the MQTT command below (e.g. a chime
  "url").
- MQTT: sonorium/announce/set takes a JSON object:
  {"channels": "Kitchen" | ["<name or id>", ...] | "all", "text": "...",
   "url": "https://.../chime.mp3", "volume": 80, "duck": 18}: a chime and/or
  spoken text. sonorium/announce/<channel id>/set takes the text alone (or
  the same JSON without "channels") for one channel.

Home Assistant's text to speech is offered to the rest of Sonorium as the
"homeassistant" provider (POST /api/announce, plugins), whether the switch is
on or off.

Home Assistant and MQTT act as an admin: an announcement overrides a zone's
owner and quiet hours, and starts a stopped channel for its length. A channel
with its Announcements setting off is skipped.

Switched off (set_enabled(False), applies at once): the notify entities are
removed from Home Assistant and the topics aren't listened to; switched back
on, they come back.
"""
from __future__ import annotations

import json
import re

from sonorium.obs import logger

OWNER = "core.announcements"   # its MQTT and event subscriptions (a plugin id can't contain ".")
PROVIDER = "homeassistant"
CHANNEL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
VOLUME = 100


class Announcements:
    """host: the plugin host's services (mqtt, homeassistant, tts, overlay, actions, bus)."""

    def __init__(self, host):
        self.host = host
        self.enabled = False
        self._published: set[str] = set()

    @property
    def mqtt(self):
        return self.host.mqtt

    # --- Topics ---

    def _channel_topic(self, channel_id: str) -> str:
        return f"{self.mqtt.prefix}/announce/{channel_id}/set"

    def _discovery_topic(self, channel_id: str) -> str:
        return f"homeassistant/notify/{self.mqtt.prefix}_{channel_id}_announce/config"

    # --- Switch ---

    async def start(self, on: bool = True) -> None:
        """At startup: Home Assistant's text to speech, then the switch as set."""
        self.host.tts.register(OWNER, PROVIDER, "Home Assistant", self.speak, ready=self._tts_ready)
        await self.set_enabled(on)

    async def set_enabled(self, on: bool) -> None:
        on = bool(on)
        if on == self.enabled:
            return
        self.enabled = on
        if on:
            self.mqtt.subscribe(OWNER, f"{self.mqtt.prefix}/announce/+/set", self.on_channel_message)
            self.mqtt.subscribe(OWNER, f"{self.mqtt.prefix}/announce/set", self.on_command)
            # MQTT may connect after startup: publish then too
            self.host.bus.subscribe(["sonorium.started", "channel.created", "channel.deleted", "channel.changed"],
                                    self.on_channels_changed, owner=OWNER)
            await self.publish_entities()
        else:
            self.mqtt.unsubscribe_owner(OWNER)
            self.host.bus.unsubscribe_owner(OWNER)
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

    async def _channels(self) -> list[dict]:
        try:
            return await self.host.actions.list_channels()
        except Exception:
            return []

    async def publish_entities(self) -> None:
        if not self.enabled or not self.mqtt.available:
            return
        for channel in await self._channels():
            if not CHANNEL_ID.match(str(channel["id"])):
                continue
            slug = channel.get("entity_slug") or channel["id"]
            config = {
                "name": f"{channel['name']} Announce",
                "unique_id": f"{self.mqtt.prefix}_{channel['id']}_announce",
                "default_entity_id": f"notify.{self.mqtt.prefix}_{slug}_announce",
                "command_topic": self._channel_topic(channel["id"]),
                "icon": "mdi:bullhorn",
                "device": dict(self.mqtt.device_info),
            }
            await self.mqtt.publish(self._discovery_topic(channel["id"]), json.dumps(config), retain=True)
            self._published.add(channel["id"])

    async def remove_entities(self) -> None:
        if not self.mqtt.available:
            return
        ids = set(self._published) | {c["id"] for c in await self._channels()}
        for channel_id in ids:
            if CHANNEL_ID.match(str(channel_id)):
                await self.mqtt.publish(self._discovery_topic(channel_id), "", retain=True)
        self._published.clear()

    # --- Messages ---

    async def on_channel_message(self, topic: str, payload: str) -> None:
        """A notify entity's message: speak it on its channel (a JSON object: chime, text, volume, duck)."""
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
            if isinstance(data, dict):  # a JSON message (or a command_template sending JSON)
                await self.announce([channel_id], data)
                return
        await self.announce([channel_id], {"text": text})

    async def on_command(self, topic: str, payload: str) -> None:
        """sonorium/announce/set: {"channels", "text", "url", "volume", "duck"}."""
        try:
            data = json.loads(payload or "")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            logger.warning("Announcements: sonorium/announce/set needs a JSON object (channels, text, url)")
            return
        if data.get("channels") in (None, "", []):
            logger.warning("Announcements: sonorium/announce/set needs 'channels' (a name or id, a list, or \"all\")")
            return
        await self.announce(data["channels"], data)

    async def announce(self, channels, data: dict) -> None:
        from sonorium.core.announce import announce_to
        text = data.get("text", data.get("message"))
        url = data.get("url", data.get("chime"))
        if url is not None and (not isinstance(url, str) or not url.lower().startswith(("http://", "https://"))):
            logger.warning("Announcements: 'url' must be an http(s) URL")
            url = None
        text = text.strip() if isinstance(text, str) and text.strip() else None
        if text is None and url is None:
            logger.warning("Announcements: nothing to announce (send 'text', a chime 'url', or both)")
            return
        volume = data.get("volume", VOLUME)
        if isinstance(volume, bool) or not isinstance(volume, (int, float)):
            volume = VOLUME
        if self.host.overlay is None:
            logger.warning("Announcements: playing sounds isn't available yet")
            return
        try:
            result = await announce_to(self.host.overlay, self.host.tts, channels, text=text, sound=url,
                                       volume=volume, duck=data.get("duck", True), origin="mqtt")
        except Exception as e:  # off, unknown channel, bad value, no text to speech: logged, as before
            logger.warning(f"Announcements: {e.args[0] if e.args else e}")
            return
        if result.get("notice"):
            logger.warning(f"Announcements: {result['notice']}")

    # --- Text to speech for the rest of Sonorium ---

    def _tts_ready(self) -> bool:
        ha = self.host.homeassistant
        return ha is not None and bool(ha.available)

    async def speak(self, message: str, language=None, engine=None, **options) -> bytes:
        ha = self.host.homeassistant
        if ha is None:
            from sonorium.plugins.tts import TtsError
            raise TtsError("Home Assistant isn't linked")
        return await ha.tts(message, engine=engine or None, language=language or None,
                            options=options.get("options"))
