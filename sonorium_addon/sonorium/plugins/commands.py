"""
Command maps: buttons on a remote (IR, RF, a keypad), MQTT topics, webhook
calls and other inputs, mapped by the user to what Sonorium does.

An input plugin receives a code ("KEY_VOLUMEUP", "0x20DF10EF", a topic's
payload) and hands it over:

    await self.commands.received("remote", "KEY_VOLUMEUP")

The plugin declares its maps (get_command_maps()); the user fills each one,
code by code, with an action from the catalogue below, usually by pressing
the button while the map is learning. The map is kept in
plugin_framework.json and a page can show it ({"type": "command_map"}).

An entry:
    {"code": "KEY_VOLUMEUP", "label": "Volume +", "action": "channel.volume_up",
     "channel": "<channel id>", "args": {"step": 5}, "enabled": true,
     "only_if": [{"condition": "channel.playing", "channel": "<channel id>"}]}

"only_if" and actions of plugins ("<plugin id>:<id>") are the automation
pieces (automation.py): when a code comes in, only if every condition holds,
do the action.

The actions run through the plugin's actions API, so they behave like the
same change in the UI. Every code received publishes command.received.
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

from sonorium.obs import logger

MAP_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_ENTRIES = 300
MAX_CODE = 200
LEARN_MAX_SECONDS = 60

# action -> title, whether it needs a channel, and its arguments (page field vocabulary)
ACTIONS: dict[str, dict] = {
    "channel.play": {"title": "Play", "channel": True, "args": {}},
    "channel.stop": {"title": "Stop", "channel": True, "args": {}},
    "channel.toggle": {"title": "Play or stop", "channel": True, "args": {}},
    "channel.volume": {"title": "Set volume", "channel": True,
                       "args": {"volume": {"type": "number", "label": "Volume", "min": 0, "max": 100}}},
    "channel.volume_up": {"title": "Volume up", "channel": True,
                          "args": {"step": {"type": "number", "label": "Step", "min": 1, "max": 50, "default": 5}}},
    "channel.volume_down": {"title": "Volume down", "channel": True,
                            "args": {"step": {"type": "number", "label": "Step", "min": 1, "max": 50, "default": 5}}},
    "channel.mute": {"title": "Mute or unmute speakers", "channel": True, "args": {}},
    "channel.next": {"title": "Next", "channel": True, "args": {}},
    "channel.theme": {"title": "Play a theme", "channel": True,
                      "args": {"theme_id": {"type": "string", "label": "Theme", "required": True}}},
    "channel.preset": {"title": "Pick a preset", "channel": True,
                       "args": {"preset_id": {"type": "string", "label": "Preset", "required": True}}},
    "channel.sequence": {"title": "Run a sequence", "channel": True,
                         "args": {"sequence_id": {"type": "string", "label": "Sequence", "required": True}}},
    "channel.playlist": {"title": "Play a playlist", "channel": True,
                         "args": {"playlist_id": {"type": "string", "label": "Playlist", "required": True}}},
    "channel.sound": {"title": "Play a sound", "channel": True,
                      "args": {"url": {"type": "url", "label": "Sound URL", "required": True},
                               "volume": {"type": "number", "label": "Volume", "min": 0, "max": 200, "default": 100}}},
    "stop_all": {"title": "Stop every channel", "channel": False, "args": {}},
    "nothing": {"title": "Nothing", "channel": False, "args": {}},
}


class CommandError(ValueError):
    """A map or an entry that isn't valid (the message is shown to the user)."""


def catalogue(pieces=None) -> list[dict]:
    """Every action: Sonorium's, then the plugins' (with their plugin_id)."""
    own = [{"id": k, "title": v["title"], "channel": v["channel"], "args": v["args"]} for k, v in ACTIONS.items()]
    return own + (pieces.actions() if pieces is not None else [])


def conditions(pieces=None) -> list[dict]:
    """Every condition for "only if": Sonorium's, then the plugins'."""
    from sonorium.plugins.automation import CONDITIONS
    own = [{"id": k, **v} for k, v in CONDITIONS.items()]
    return own + (pieces.conditions() if pieces is not None else [])


def check_entry(entry, pieces=None) -> dict:
    if not isinstance(entry, dict):
        raise CommandError("An entry must be an object")
    code = entry.get("code")
    if not isinstance(code, str) or not code.strip() or len(code) > MAX_CODE:
        raise CommandError("Each entry needs a code (text)")
    from sonorium.plugins.automation import AutomationError, AutomationPieces, check_args
    pieces = pieces if pieces is not None else AutomationPieces()
    try:
        only_if = pieces.check_conditions(entry.get("only_if"))
    except AutomationError as e:
        raise CommandError(str(e))
    action = entry.get("action")
    if isinstance(action, str) and ":" in action and action not in ACTIONS:
        spec = pieces.action(action)
        channel = entry.get("channel")
        if spec is None:  # a plugin's action while it is off: kept as it is, it doesn't run
            return {"code": code.strip(), "label": str(entry.get("label") or "")[:80], "action": action,
                    "channel": channel, "args": dict(entry.get("args") or {}),
                    "enabled": bool(entry.get("enabled", True)), "only_if": only_if}
        if spec["channel"] and not (isinstance(channel, str) and channel):
            raise CommandError(f"'{spec['title']}' needs a channel")
        try:
            args = check_args(spec, entry.get("args"), spec["title"])
        except AutomationError as e:
            raise CommandError(str(e))
        return {"code": code.strip(), "label": str(entry.get("label") or "")[:80], "action": action,
                "channel": channel if spec["channel"] else None, "args": args,
                "enabled": bool(entry.get("enabled", True)), "only_if": only_if}
    if action not in ACTIONS:
        raise CommandError(f"Unknown action '{action}'")
    spec = ACTIONS[action]
    channel = entry.get("channel")
    if spec["channel"] and not (isinstance(channel, str) and channel):
        raise CommandError(f"'{spec['title']}' needs a channel")
    args = entry.get("args") or {}
    if not isinstance(args, dict) or any(k not in spec["args"] for k in args):
        raise CommandError(f"'{spec['title']}' takes only: {', '.join(spec['args']) or 'nothing'}")
    for name, field in spec["args"].items():
        value = args.get(name)
        if value is None:
            if field.get("required"):
                raise CommandError(f"'{spec['title']}' needs {field['label']}")
            continue
        if field["type"] == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or \
                    not field.get("min", value) <= value <= field.get("max", value):
                raise CommandError(f"{field['label']} must be {field.get('min')}-{field.get('max')}")
        elif not isinstance(value, str):
            raise CommandError(f"{field['label']} must be text")
    return {"code": code.strip(), "label": str(entry.get("label") or "")[:80], "action": action,
            "channel": channel if spec["channel"] else None, "args": dict(args),
            "enabled": bool(entry.get("enabled", True)), "only_if": only_if}


class CommandMaps:
    def __init__(self, store, publish=None, pieces=None):
        self.store = store
        self.publish = publish or (lambda *a, **k: None)
        self.pieces = pieces  # the plugins' automation pieces (automation.py), set by the plugin host
        self._learning: dict[tuple, asyncio.Future] = {}

    def _maps(self, plugin_id: str, create: bool = False) -> dict:
        section = self.store.main.section("command_maps")
        return section.setdefault(plugin_id, {}) if create else (section.get(plugin_id) or {})

    def entries(self, plugin_id: str, map_id: str) -> list[dict]:
        return [dict(e) for e in (self._maps(plugin_id).get(map_id) or {}).get("entries", [])]

    def set_entries(self, plugin_id: str, map_id: str, entries) -> list[dict]:
        if not MAP_ID.match(str(map_id)):
            raise CommandError("Invalid map id")
        if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
            raise CommandError(f"'entries' must be a list of at most {MAX_ENTRIES}")
        clean = [check_entry(e, self.pieces) for e in entries]
        self._maps(plugin_id, create=True)[map_id] = {"entries": clean}
        self.store.main.save()
        return self.entries(plugin_id, map_id)

    def forget_plugin(self, plugin_id: str) -> None:
        if self.store.main.section("command_maps").pop(plugin_id, None) is not None:
            self.store.main.save()

    # --- Learning: the next code received is the answer ---

    async def learn(self, plugin_id: str, map_id: str, seconds: float = 20.0) -> Optional[str]:
        """Wait for the next code the plugin receives on this map. None if none came in time."""
        seconds = max(1.0, min(float(seconds), LEARN_MAX_SECONDS))
        key = (plugin_id, map_id)
        old = self._learning.get(key)
        if old is not None and not old.done():
            old.cancel()
        future = asyncio.get_running_loop().create_future()
        self._learning[key] = future
        try:
            return await asyncio.wait_for(future, seconds)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return None
        finally:
            if self._learning.get(key) is future:
                self._learning.pop(key, None)

    def stop_learning(self, plugin_id: str) -> None:
        for key in [k for k in self._learning if k[0] == plugin_id]:
            future = self._learning.pop(key)
            if not future.done():
                future.cancel()

    # --- Receiving ---

    async def received(self, plugin_id: str, map_id: str, code, actions, value=None) -> dict:
        """
        A code came in: learnt if the map is learning, else the matching
        entries run. Returns {"learned": bool, "ran": [actions], "errors": [...]}.
        """
        code = str(code).strip()[:MAX_CODE]
        future = self._learning.get((plugin_id, map_id))
        if future is not None and not future.done():
            future.set_result(code)
            self.publish("command.received", {"plugin_id": plugin_id, "map_id": map_id, "code": code,
                                              "actions": [], "learned": True})
            return {"learned": True, "ran": [], "errors": []}
        ran, errors = [], []
        from sonorium.plugins.automation import AutomationPieces
        pieces = self.pieces if self.pieces is not None else AutomationPieces()
        for entry in self.entries(plugin_id, map_id):
            if not entry["enabled"] or entry["code"] != code:
                continue
            if entry.get("only_if") and not await pieces.all_hold(entry["only_if"], actions):
                continue  # only if: a condition doesn't hold
            try:
                await run_action(actions, entry, value, pieces)
                ran.append(entry["action"])
            except Exception as e:
                message = getattr(e, "message", None) or str(e) or type(e).__name__
                errors.append(message)
                logger.warning(f"Command {plugin_id}/{map_id} '{code}' -> {entry['action']}: {message}")
        self.publish("command.received", {"plugin_id": plugin_id, "map_id": map_id, "code": code,
                                          "actions": ran, "learned": False})
        return {"learned": False, "ran": ran, "errors": errors}


async def run_action(actions, entry: dict, value=None, pieces=None) -> None:
    """Do an entry's action through a plugin's actions API (value: what came with the code, e.g. a number)."""
    action, channel, args = entry["action"], entry.get("channel"), entry.get("args") or {}
    if action == "nothing":
        return
    if ":" in action and action not in ACTIONS:
        if pieces is None:
            raise CommandError("Plugins' actions aren't available here")
        await pieces.run_action(action, channel, args, value)
        return
    if action == "stop_all":
        await actions.stop_all()
    elif action == "channel.play":
        await actions.start_channel(channel)
    elif action == "channel.stop":
        await actions.stop_channel(channel)
    elif action == "channel.toggle":
        info = await actions.get_channel(channel)
        await (actions.stop_channel(channel) if info["is_playing"] else actions.start_channel(channel))
    elif action == "channel.volume":
        volume = args.get("volume")
        if volume is None:
            try:
                volume = float(value)
            except (TypeError, ValueError):
                raise CommandError("Set volume needs a volume (in the entry, or sent with the code)")
        await actions.set_volume(channel, max(0, min(100, int(volume))))
    elif action in ("channel.volume_up", "channel.volume_down"):
        info = await actions.get_channel(channel)
        step = int(args.get("step") or 5) * (1 if action.endswith("_up") else -1)
        await actions.set_volume(channel, max(0, min(100, int(info["volume"]) + step)))
    elif action == "channel.mute":
        info = await actions.get_channel(channel)
        await actions.mute_speakers(channel, not info["speakers_muted"])
    elif action == "channel.next":
        await actions.next_step(channel)
    elif action == "channel.theme":
        await actions.set_theme(channel, args["theme_id"])
    elif action == "channel.preset":
        await actions.set_preset(channel, args["preset_id"])
    elif action == "channel.sequence":
        await actions.start_sequence(channel, args["sequence_id"])
    elif action == "channel.playlist":
        await actions.set_playlist(channel, args["playlist_id"])
    elif action == "channel.sound":
        await actions.play_sound([channel], args["url"], volume=args.get("volume", 100))
