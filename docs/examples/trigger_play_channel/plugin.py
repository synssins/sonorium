"""
Trigger Plays a Channel: an example Sonorium plugin.

Calling its webhook trigger (a motion sensor, an ESP32 button, curl...)
plays a channel; another trigger stops it. It shows the main parts of the
plugin framework: settings, webhook triggers, the actions API, events and
a page. docs/PLUGIN_DEVELOPMENT.md walks through it.

    curl -X POST http://sonorium.local:8008/api/triggers/trigger_play_channel/play \
         -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
         -d '{"channel": "Channel 1"}'
"""

from __future__ import annotations

from datetime import datetime, timezone

from sonorium.plugins.actions import ActionError
from sonorium.plugins.base import BasePlugin

RECENT = 10


class TriggerPlayChannel(BasePlugin):
    id = "trigger_play_channel"
    name = "Trigger Plays a Channel"
    version = "1.0.0"
    description = "Webhook triggers that play or stop a channel."
    author = "Sonorium"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recent: list[dict] = []  # the latest calls, newest first (for the page)
        self._started = None

    # --- Settings ---

    def get_settings_schema(self) -> dict:
        return {
            "channel": {"type": "string", "label": "Channel", "default": "",
                        "help": "Channel name or id played when the call names none"},
            "volume": {"type": "number", "label": "Volume", "min": 0, "max": 100, "default": 40},
        }

    # --- Lifecycle ---

    async def on_enable(self) -> None:
        # Removed again automatically when the plugin is disabled
        self._started = self.subscribe("channel.started", self._on_channel_started)

    async def _on_channel_started(self, event) -> None:
        if event.origin == f"plugin:{self.id}":
            self.emit("played", {"channel_id": event.data["channel_id"]})

    # --- Triggers ---

    def get_triggers(self) -> list[dict]:
        return [
            {"id": "play", "title": "Play", "methods": ["POST", "GET"],
             "description": "Play a channel. Payload: channel (name or id), volume (0-100)."},
            {"id": "stop", "title": "Stop", "methods": ["POST", "GET"],
             "description": "Stop a channel. Payload: channel (name or id)."},
        ]

    async def on_trigger(self, trigger: str, payload: dict, context: dict) -> None:
        wanted = str(payload.get("channel") or self.get_setting("channel") or "")
        channel = await self._find_channel(wanted)
        if trigger == "play":
            volume = payload.get("volume", self.get_setting("volume"))
            if isinstance(volume, str) and volume.isdigit():  # GET query values are text
                volume = int(volume)
            if isinstance(volume, (int, float)):
                await self.actions.set_volume(channel["id"], volume)
            await self.actions.start_channel(channel["id"])
        elif trigger == "stop":
            await self.actions.stop_channel(channel["id"])
        self._remember(trigger, channel["name"], context)

    async def _find_channel(self, wanted: str) -> dict:
        channels = await self.actions.list_channels()
        for channel in channels:
            if wanted and wanted in (channel["id"], channel["name"]):
                return channel
        if not wanted and len(channels) == 1:
            return channels[0]
        raise ActionError("not_found", f"No channel '{wanted}'")

    def _remember(self, trigger: str, channel: str, context: dict) -> None:
        self.recent.insert(0, {"id": str(context.get("event_id")), "trigger": trigger, "channel": channel,
                               "at": datetime.now(timezone.utc).isoformat(), "client": context.get("client", "")})
        del self.recent[RECENT:]

    # --- A page (a left-menu entry) ---

    def get_pages(self) -> list[dict]:
        return [{"id": "calls", "title": "Trigger calls", "icon": "gesture-tap"}]

    async def get_page(self, page_id: str) -> dict:
        return {
            "title": "Trigger calls",
            "refresh": 10,
            "sections": [{
                "id": "recent",
                "title": "Latest calls",
                "items": [
                    {"type": "text", "style": "muted",
                     "text": "The last 10 calls of this plugin's triggers since Sonorium started."},
                    {"type": "table", "id": "calls", "empty": "No calls yet",
                     "columns": [{"key": "at", "label": "When", "format": "datetime"},
                                 {"key": "trigger", "label": "Trigger"},
                                 {"key": "channel", "label": "Channel"},
                                 {"key": "client", "label": "From"}],
                     "rows": self.recent},
                    {"type": "button", "action": "clear", "label": "Clear the list", "style": "secondary",
                     "confirm": "Clear the list of calls?"},
                ],
            }],
        }

    async def page_action(self, page_id: str, action: str, data: dict) -> dict:
        if action == "clear":
            self.recent.clear()
            return {"success": True, "message": "Cleared"}
        return await super().page_action(page_id, action, data)
