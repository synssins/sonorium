"""
Automation pieces: "when", "only if" and "do", for command maps (commands.py).
No rules screen of its own: a command map is the rule ("when this code comes
in, only if ..., do ...").

    when     a code on a command map: a remote's button, an MQTT topic, a
             webhook, or anything a plugin hands over (self.commands.received)
    only if  conditions on an entry ("only_if"): all must hold, else the
             entry is skipped. Sonorium's own (CONDITIONS) and plugins'
    do       the entry's action: Sonorium's own (commands.ACTIONS) and plugins'

A plugin adds pieces as data (manifest "automation", or get_automation()):

    {"actions":    [{"id": "flash", "title": "Flash the lights", "channel": false,
                     "args": {"ms": {"type": "number", "label": "Length", "min": 20, "max": 2000}}}],
     "conditions": [{"id": "dark", "title": "It's dark outside", "args": {}}]}

and runs them: automation_action(id, channel, args, value) and
automation_condition(id, channel, args) -> bool. In a map their ids are
"<plugin id>:<id>". A piece outside the contract is refused and listed on the
plugin's row; every limit is in LIMITS. A plugin switched off keeps the
entries that use its pieces; they don't run (and its conditions don't hold)
until it is on again.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from datetime import datetime
from typing import Optional

from sonorium.obs import logger

LIMITS = {"actions_per_plugin": 20, "conditions_per_plugin": 20, "title_max": 40, "args_max": 3,
          "conditions_per_entry": 5, "action_timeout": 30.0, "condition_timeout": 2.0}
PIECE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
ARG_TYPES = ("number", "string", "url", "time", "select", "boolean")
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# Sonorium's own conditions
CONDITIONS: dict[str, dict] = {
    "channel.playing": {"title": "The channel is playing", "channel": True, "args": {}},
    "channel.stopped": {"title": "The channel is stopped", "channel": True, "args": {}},
    "time.between": {"title": "The time is between", "channel": False,
                     "args": {"from": {"type": "time", "label": "From", "required": True},
                              "to": {"type": "time", "label": "To", "required": True}}},
}


class AutomationError(ValueError):
    """A piece, or an entry's use of one, that isn't valid (the message is shown to the user)."""


def _normalize_args(args, where: str) -> dict:
    if args is None:
        return {}
    if not isinstance(args, dict) or len(args) > LIMITS["args_max"]:
        raise AutomationError(f"{where}: at most {LIMITS['args_max']} arguments")
    out = {}
    for name, field in args.items():
        if not isinstance(name, str) or not PIECE_ID.match(name) or not isinstance(field, dict):
            raise AutomationError(f"{where}: argument {name!r} needs a name and a field")
        if field.get("type") not in ARG_TYPES:
            raise AutomationError(f"{where}: argument {name} has a type of {', '.join(ARG_TYPES)}")
        clean = {"type": field["type"], "label": str(field.get("label") or name)[:LIMITS["title_max"]]}
        for key in ("min", "max", "default", "required"):
            if key in field:
                clean[key] = field[key]
        if field["type"] == "select":
            options = field.get("options")
            if not isinstance(options, list) or not options or not all(isinstance(o, str) for o in options):
                raise AutomationError(f"{where}: argument {name} needs options")
            clean["options"] = list(options)
        out[name] = clean
    return out


def normalize_pieces(decl, plugin_id: str) -> tuple[dict, list]:
    """({"actions": {id: piece}, "conditions": {id: piece}}, problems)."""
    pieces = {"actions": {}, "conditions": {}}
    problems = []
    if not decl:
        return pieces, problems
    if not isinstance(decl, dict):
        return pieces, ["Automation: an object with actions and conditions"]
    for kind in ("actions", "conditions"):
        items = decl.get(kind) or []
        if not isinstance(items, list):
            problems.append(f"Automation {kind}: a list")
            continue
        for item in items:
            if len(pieces[kind]) >= LIMITS[f"{kind}_per_plugin"]:
                problems.append(f"Automation: at most {LIMITS[f'{kind}_per_plugin']} {kind}")
                break
            pid = item.get("id") if isinstance(item, dict) else None
            where = f"Automation {kind[:-1]} {pid!r}"
            try:
                if not isinstance(pid, str) or not PIECE_ID.match(pid) or pid in pieces[kind]:
                    raise AutomationError(f"{where}: an id of a-z, 0-9, _ and -, once")
                title = item.get("title")
                if not isinstance(title, str) or not title.strip() or len(title.strip()) > LIMITS["title_max"]:
                    raise AutomationError(f"{where}: a title of 1 to {LIMITS['title_max']} characters")
                pieces[kind][pid] = {"id": f"{plugin_id}:{pid}", "title": title.strip(),
                                     "channel": bool(item.get("channel", False)),
                                     "args": _normalize_args(item.get("args"), where), "plugin_id": plugin_id}
            except AutomationError as e:
                problems.append(str(e))
    return pieces, problems


def check_args(spec: dict, args, title: str) -> dict:
    """An entry's arguments for a piece, checked (AutomationError)."""
    args = args or {}
    if not isinstance(args, dict) or any(k not in spec["args"] for k in args):
        raise AutomationError(f"'{title}' takes only: {', '.join(spec['args']) or 'nothing'}")
    for name, field in spec["args"].items():
        value = args.get(name)
        if value is None:
            if field.get("required"):
                raise AutomationError(f"'{title}' needs {field['label']}")
            continue
        kind = field["type"]
        if kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or \
                    not field.get("min", value) <= value <= field.get("max", value):
                raise AutomationError(f"{field['label']} must be {field.get('min')}-{field.get('max')}")
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise AutomationError(f"{field['label']} must be on or off")
        elif kind == "time":
            if not isinstance(value, str) or not TIME.match(value):
                raise AutomationError(f"{field['label']} must be a time (HH:MM)")
        elif kind == "select":
            if value not in field.get("options", []):
                raise AutomationError(f"{field['label']} must be one of {', '.join(field.get('options', []))}")
        elif not isinstance(value, str):
            raise AutomationError(f"{field['label']} must be text")
    return dict(args)


class AutomationPieces:
    """Every plugin's pieces while it is on. plugin_of(id): the plugin."""

    def __init__(self, plugin_of=None, now=None):
        self.plugin_of = plugin_of or (lambda pid: None)
        self.now = now or datetime.now
        self._pieces: dict[str, dict] = {}
        self.problems: dict[str, list] = {}

    def register_plugin(self, plugin) -> list:
        try:
            decl = plugin.get_automation()
        except Exception as e:
            decl, problems = {}, [f"get_automation() failed: {e}"]
        else:
            problems = []
        pieces, more = normalize_pieces(decl, plugin.id)
        problems += more
        for p in problems:
            logger.warning(f"Plugin {plugin.id}: {p}")
        if pieces["actions"] or pieces["conditions"]:
            self._pieces[plugin.id] = pieces
        else:
            self._pieces.pop(plugin.id, None)
        self.problems[plugin.id] = problems
        return problems

    def unregister_owner(self, plugin_id: str) -> None:
        self._pieces.pop(plugin_id, None)

    def actions(self) -> list[dict]:
        return [dict(p) for pieces in self._pieces.values() for p in pieces["actions"].values()]

    def conditions(self) -> list[dict]:
        return [dict(p) for pieces in self._pieces.values() for p in pieces["conditions"].values()]

    def _piece(self, kind: str, full_id: str) -> Optional[dict]:
        plugin_id, _, pid = str(full_id).partition(":")
        return ((self._pieces.get(plugin_id) or {}).get(kind) or {}).get(pid)

    def action(self, full_id: str) -> Optional[dict]:
        return self._piece("actions", full_id)

    def condition(self, full_id: str) -> Optional[dict]:
        if full_id in CONDITIONS:
            return dict(CONDITIONS[full_id], id=full_id)
        return self._piece("conditions", full_id)

    def check_conditions(self, only_if) -> list[dict]:
        """An entry's "only_if", checked (AutomationError)."""
        if only_if is None:
            return []
        if not isinstance(only_if, list) or len(only_if) > LIMITS["conditions_per_entry"]:
            raise AutomationError(f"At most {LIMITS['conditions_per_entry']} conditions")
        out = []
        for item in only_if:
            if not isinstance(item, dict):
                raise AutomationError("A condition is an object")
            cid = item.get("condition")
            spec = self.condition(cid) if isinstance(cid, str) else None
            if spec is None:
                if isinstance(cid, str) and ":" in cid:  # a plugin's, while it is off: kept as it is
                    out.append({"condition": cid, "channel": item.get("channel"), "args": dict(item.get("args") or {})})
                    continue
                raise AutomationError(f"Unknown condition '{cid}'")
            channel = item.get("channel")
            if spec["channel"] and not (isinstance(channel, str) and channel):
                raise AutomationError(f"'{spec['title']}' needs a channel")
            out.append({"condition": cid, "channel": channel if spec["channel"] else None,
                        "args": check_args(spec, item.get("args"), spec["title"])})
        return out

    # --- running ---

    async def holds(self, item: dict, actions) -> bool:
        cid, channel, args = item["condition"], item.get("channel"), item.get("args") or {}
        try:
            if cid in ("channel.playing", "channel.stopped"):
                info = await actions.get_channel(channel)
                return bool(info["is_playing"]) == (cid == "channel.playing")
            if cid == "time.between":
                now = self.now().strftime("%H:%M")
                start, end = args["from"], args["to"]
                return start <= now < end if start <= end else (now >= start or now < end)
            spec = self.condition(cid)
            plugin = self.plugin_of(cid.partition(":")[0]) if spec is not None else None
            if plugin is None or not plugin.enabled:
                return False  # its plugin is off: the condition doesn't hold
            result = plugin.automation_condition(cid.partition(":")[2], channel, dict(args))
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, LIMITS["condition_timeout"])
            return bool(result)
        except Exception as e:
            logger.warning(f"Condition {cid}: {getattr(e, 'message', None) or e or type(e).__name__}")
            return False

    async def all_hold(self, only_if, actions) -> bool:
        for item in only_if or []:
            if not await self.holds(item, actions):
                return False
        return True

    async def run_action(self, full_id: str, channel, args: dict, value=None) -> None:
        spec = self.action(full_id)
        plugin_id, _, pid = full_id.partition(":")
        plugin = self.plugin_of(plugin_id)
        if spec is None or plugin is None or not plugin.enabled:
            raise AutomationError("Its plugin is switched off")
        result = plugin.automation_action(pid, channel, dict(args or {}), value)
        if inspect.isawaitable(result):
            result = await asyncio.wait_for(result, LIMITS["action_timeout"])
        if isinstance(result, dict) and result.get("success") is False:
            raise AutomationError(str(result.get("message") or "The plugin couldn't do it"))
