"""
Devices of any kind a plugin brings: lights, displays, fans, buttons. Core
lists, names, places and assigns them without knowing what they are, the
way a hotel's front desk keeps every guest's name and room without knowing
their job.

Core knows one kind of device itself: speakers, because only audio goes
through the mixer. A plugin's audio outputs stay speakers (core/
audio_outputs.py, Settings › Speakers); every other kind is a name to core.

A plugin declares a kind (manifest "device_kinds", get_device_kinds(), or
self.devices.register_kind()):

    {"id": "light", "title": "Lights", "icon": "lightbulb",
     "actions": [{"id": "test", "label": "Flash"}],   # buttons on each device
     "latency_ms": 20,                                # command to effect, for timing
     "one_channel": False}                            # a device follows one channel at most

and lists its devices when asked (discover_devices(kind) -> [{"id", "name",
"model"}]). Core remembers each one (store section "devices") by a stable id
"dev:<kind>:<plugin>:<16 hex of sha256(the plugin's id for it)>", even while
it is offline, and keeps for it what the user chose:

    name    the name shown (the plugin's until the user renames it)
    area    the area it is in (an area id, or "" for none)
    use     switched on (new devices are)
    channels, areas   what it follows: the channels it is assigned to, and
                      areas (it follows any channel playing on a speaker there)

Each device shows online or offline (in the plugin's last listing, or what
the plugin set with set_status) and the plugin's last error. Its kind's
actions call the plugin's device_action(); another plugin can call them too
(invoke), through the device's owner: the written contracts between plugins
(light, display, input, sensor in docs/PLUGIN_DEVELOPMENT.md) are agreements
between plugins, core only passes the call on.

Plugin attributes on a device: object class "device" (core/plugin_data.py).

A plugin switched off: its kinds go, its devices stay (offline, not shown);
uninstalled: its devices are forgotten.

Events: device.added, device.removed, device.changed, device.assigned,
device.available, device.unavailable.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
from typing import Callable, Optional

from sonorium.obs import logger

PREFIX = "dev:"
KIND_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
ACTION_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
# Speakers are core's own kind (audio goes through the mixer): plugins add audio outputs instead
RESERVED_KINDS = frozenset({"speaker", "speakers", "audio", "audio_output", "output"})
# Every limit in one place (adjustable here; a declaration over one is refused with the reason)
LIMITS = {
    "title_max": 24,
    "action_label_max": 24,
    "actions_max": 6,
    "kinds_per_plugin_max": 8,
    "name_max": 100,
    "model_max": 60,
    "error_max": 200,
    "discover_timeout": 15.0,
    "action_timeout": 15.0,
    "latency_max_ms": 600_000,
}


class DeviceError(ValueError):
    """A declaration, a device or a change that can't be used (the message is shown to the caller)."""


def device_id_for(kind: str, plugin_id: str, device: str) -> str:
    digest = hashlib.sha256(device.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return f"{PREFIX}{kind}:{plugin_id}:{digest}"


def normalize_kind(decl, owner: str) -> dict:
    """A well-formed kind, or DeviceError naming what is wrong."""
    if not isinstance(decl, dict):
        raise DeviceError("A device kind is an object with id and title")
    kind_id = decl.get("id")
    if not isinstance(kind_id, str) or not KIND_ID.match(kind_id):
        raise DeviceError("A device kind's id: a-z, 0-9 and _, starting with a letter, up to 32 characters")
    if kind_id in RESERVED_KINDS:
        raise DeviceError(f"'{kind_id}' is a speaker: register an audio output type instead")
    title = decl.get("title")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > LIMITS["title_max"]:
        raise DeviceError(f"Device kind {kind_id}: a title of 1 to {LIMITS['title_max']} characters")
    actions = decl.get("actions") or []
    if not isinstance(actions, list) or len(actions) > LIMITS["actions_max"]:
        raise DeviceError(f"Device kind {kind_id}: at most {LIMITS['actions_max']} actions")
    clean_actions, seen = [], set()
    for action in actions:
        if not isinstance(action, dict) or not isinstance(action.get("id"), str) or not ACTION_ID.match(action["id"]):
            raise DeviceError(f"Device kind {kind_id}: an action needs an id (a-z, 0-9, _ and -)")
        label = action.get("label")
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > LIMITS["action_label_max"]:
            raise DeviceError(f"Device kind {kind_id}: action {action['id']} needs a label of 1 to "
                              f"{LIMITS['action_label_max']} characters")
        if action["id"] in seen:
            raise DeviceError(f"Device kind {kind_id}: action {action['id']} twice")
        seen.add(action["id"])
        clean_actions.append({"id": action["id"], "label": label.strip()})
    latency = decl.get("latency_ms", 0)
    if isinstance(latency, bool) or not isinstance(latency, (int, float)) or not 0 <= latency <= LIMITS["latency_max_ms"]:
        raise DeviceError(f"Device kind {kind_id}: latency_ms is 0 to {LIMITS['latency_max_ms']}")
    icon = None
    if decl.get("icon"):
        from sonorium.plugins.extensions import check_icon
        icon = check_icon(decl["icon"])
    return {"id": kind_id, "title": title.strip(), "icon": icon, "actions": clean_actions,
            "latency_ms": int(round(latency)), "one_channel": bool(decl.get("one_channel", False)),
            "plugin_id": owner}


class Devices:
    """
    store: the framework store (section "devices"); session_manager: channels,
    their speakers and the speakers' areas; publish: the event bus's publish;
    plugin_of(plugin_id): the plugin, for discover_devices and device_action.
    """

    def __init__(self, store=None, session_manager=None, publish: Optional[Callable] = None,
                 plugin_of: Optional[Callable] = None):
        self.store = store
        self.session_manager = session_manager
        self.publish = publish
        self.plugin_of = plugin_of or (lambda pid: None)
        self._kinds: dict[str, dict] = {}
        self._online: dict[str, bool] = {}
        self._errors: dict[str, str] = {}
        self._memory: dict = {}

    # --- storage ---

    def _records(self) -> dict:
        if self.store is None:
            return self._memory.setdefault("devices", {})
        return self.store.main.section("devices")

    def _save(self) -> None:
        if self.store is not None:
            self.store.main.save()

    def _emit(self, event_type: str, data: dict) -> None:
        if self.publish is not None:
            try:
                self.publish(event_type, data)
            except Exception as e:
                logger.debug(f"Devices: {event_type} not published: {e}")

    # --- kinds ---

    def register_kind(self, owner: str, decl) -> dict:
        kind = normalize_kind(decl, owner)
        current = self._kinds.get(kind["id"])
        if current is not None and current["plugin_id"] != owner:
            raise DeviceError(f"The device kind '{kind['id']}' is already registered by {current['plugin_id']}")
        mine = [k for k in self._kinds.values() if k["plugin_id"] == owner and k["id"] != kind["id"]]
        if len(mine) >= LIMITS["kinds_per_plugin_max"]:
            raise DeviceError(f"At most {LIMITS['kinds_per_plugin_max']} device kinds per plugin")
        self._kinds[kind["id"]] = kind
        return dict(kind)

    def register_plugin(self, plugin) -> list[str]:
        """The kinds a plugin declares (get_device_kinds), registered as it is switched on. Returns the problems."""
        problems = []
        try:
            decls = plugin.get_device_kinds() or []
        except Exception as e:
            return [f"get_device_kinds() failed: {e}"]
        for decl in decls if isinstance(decls, list) else []:
            try:
                self.register_kind(plugin.id, decl)
            except DeviceError as e:
                problems.append(str(e))
                logger.warning(f"Plugin {plugin.id}: {e}")
        return problems

    def unregister_owner(self, owner: str) -> None:
        """The plugin is off: its kinds go; its devices stay, offline."""
        for kind_id in [k for k, v in self._kinds.items() if v["plugin_id"] == owner]:
            self._kinds.pop(kind_id, None)
        for device_id, record in self._records().items():
            if record.get("plugin") == owner:
                self._set_online(device_id, False)

    def forget_owner(self, owner: str) -> None:
        """The plugin was uninstalled: its devices are forgotten."""
        self.unregister_owner(owner)
        gone = [d for d, r in self._records().items() if r.get("plugin") == owner]
        for device_id in gone:
            self._forget(device_id)
        if gone:
            self._save()

    def kinds(self) -> list[dict]:
        return [dict(k) for k in sorted(self._kinds.values(), key=lambda k: (k["plugin_id"], k["title"]))]

    def kind(self, kind_id: str) -> Optional[dict]:
        return self._kinds.get(kind_id)

    # --- devices ---

    def _as_dict(self, device_id: str, record: dict) -> dict:
        kind = self._kinds.get(record.get("kind"))
        return {
            "id": device_id, "kind": record.get("kind"), "plugin_id": record.get("plugin"),
            "device": record.get("device"), "name": record.get("name") or record.get("found_name") or device_id,
            "found_name": record.get("found_name") or "", "renamed": bool(record.get("renamed")),
            "model": record.get("model") or "", "area": record.get("area") or "",
            "use": record.get("use", True) is not False,
            "online": bool(self._online.get(device_id)) and kind is not None,
            "error": self._errors.get(device_id, ""),
            "channels": list(record.get("channels") or []), "areas": list(record.get("areas") or []),
            "kind_title": kind["title"] if kind else record.get("kind_title") or record.get("kind"),
            "actions": list(kind["actions"]) if kind else [],
            "latency_ms": kind["latency_ms"] if kind else None,
        }

    def list(self, kind: Optional[str] = None, plugin: Optional[str] = None, *, active_only: bool = True) -> list[dict]:
        """The remembered devices (of a kind, of a plugin). active_only: only kinds registered now (plugins on)."""
        out = []
        for device_id, record in self._records().items():
            if kind is not None and record.get("kind") != kind:
                continue
            if plugin is not None and record.get("plugin") != plugin:
                continue
            if active_only and record.get("kind") not in self._kinds:
                continue
            out.append(self._as_dict(device_id, record))
        out.sort(key=lambda d: (d["kind"] or "", d["name"].lower()))
        return out

    def get(self, device_id: str) -> dict:
        record = self._records().get(device_id)
        if record is None:
            raise LookupError(f"Device not found: {device_id}")
        return self._as_dict(device_id, record)

    def _kind_of_owner(self, kind_id: str) -> dict:
        kind = self._kinds.get(kind_id)
        if kind is None:
            raise LookupError(f"No device kind '{kind_id}'")
        return kind

    async def discover(self, kind_id: Optional[str] = None) -> list[dict]:
        """Ask the kinds' plugins for their devices (one kind, or every kind); each is remembered."""
        kinds = [self._kind_of_owner(kind_id)] if kind_id else list(self._kinds.values())
        out = []
        for kind in kinds:
            plugin = self.plugin_of(kind["plugin_id"])
            method = getattr(plugin, "discover_devices", None) if plugin is not None else None
            if method is None:
                continue
            try:
                found = method(kind["id"])
                if inspect.isawaitable(found):
                    found = await asyncio.wait_for(found, LIMITS["discover_timeout"])
            except asyncio.TimeoutError:
                raise DeviceError(f"{kind['title']}: the plugin didn't list its devices in time")
            except DeviceError:
                raise
            except Exception as e:
                logger.warning(f"Devices: {kind['plugin_id']} discover_devices({kind['id']}) failed: {e}")
                raise DeviceError(f"{kind['title']}: {e}")
            if self._kinds.get(kind["id"]) is kind:
                self._remember(kind, found or [])
            out.extend(self.list(kind["id"]))
        return out

    def _remember(self, kind: dict, found: list) -> None:
        records = self._records()
        seen, changed = set(), False
        for item in found:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                continue
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            device_id = device_id_for(kind["id"], kind["plugin_id"], item["id"])
            found_name = str(item.get("name") or item["id"])[:LIMITS["name_max"]]
            model = str(item.get("model") or "")[:LIMITS["model_max"]]
            record = records.get(device_id)
            if record is None:
                record = {"kind": kind["id"], "plugin": kind["plugin_id"], "device": item["id"], "found_name": found_name,
                          "model": model, "kind_title": kind["title"], "use": True, "area": "", "channels": [],
                          "areas": []}
                if isinstance(item.get("area"), str):
                    record["area"] = item["area"]
                records[device_id] = record
                changed = True
                self._emit("device.added", {"device_id": device_id, "kind": kind["id"], "plugin_id": kind["plugin_id"],
                                            "name": found_name})
            else:
                fresh = dict(record, found_name=found_name, model=model, kind_title=kind["title"])
                if fresh != record:
                    records[device_id] = fresh
                    changed = True
            self._set_online(device_id, True)
        for device_id, record in records.items():
            if record.get("kind") == kind["id"] and record.get("plugin") == kind["plugin_id"] \
                    and record.get("device") not in seen:
                self._set_online(device_id, False)
        if changed:
            self._save()

    def _set_online(self, device_id: str, online: bool) -> None:
        before = self._online.get(device_id)
        self._online[device_id] = online
        if before is not None and before != online:
            self._emit("device.available" if online else "device.unavailable", {"device_id": device_id})
        elif before is None and online is False:
            pass

    def set_status(self, owner: str, device: str, online: Optional[bool] = None, error: Optional[str] = None) -> dict:
        """The plugin says how one of its devices is: online or not, and its last error ("" clears it)."""
        device_id = self._own(owner, device)
        if online is not None:
            self._set_online(device_id, bool(online))
        if error is not None:
            text = str(error)[:LIMITS["error_max"]]
            if text:
                self._errors[device_id] = text
            else:
                self._errors.pop(device_id, None)
        return self.get(device_id)

    def _own(self, owner: str, device: str) -> str:
        """A plugin's device by core id or by its own id."""
        records = self._records()
        if device in records and records[device].get("plugin") == owner:
            return device
        for device_id, record in records.items():
            if record.get("plugin") == owner and record.get("device") == device:
                return device_id
        raise LookupError(f"Device not found: {device}")

    def update(self, device_id: str, changes: dict) -> dict:
        """The user's choices: name (None/"" = the plugin's), area ("" = none), use."""
        record = self._records().get(device_id)
        if record is None:
            raise LookupError(f"Device not found: {device_id}")
        if not isinstance(changes, dict):
            raise DeviceError("Expected an object")
        unknown = set(changes) - {"name", "area", "use"}
        if unknown:
            raise DeviceError(f"Unknown device setting: {', '.join(sorted(unknown))}")
        before = dict(record)
        if "name" in changes:
            name = changes["name"]
            if name is None or not str(name).strip() or str(name).strip() == record.get("found_name"):
                record.pop("name", None)
                record.pop("renamed", None)
            else:
                name = str(name).strip()
                if len(name) > LIMITS["name_max"]:
                    raise DeviceError(f"Name is too long (at most {LIMITS['name_max']} characters)")
                record["name"], record["renamed"] = name, True
        if "area" in changes:
            area = changes["area"]
            if area is not None and not isinstance(area, str):
                raise DeviceError("area: an area id, or empty for none")
            record["area"] = area or ""
        if "use" in changes:
            if not isinstance(changes["use"], bool):
                raise DeviceError("use: true or false")
            record["use"] = changes["use"]
        if record != before:
            self._save()
            self._emit("device.changed", {"device_id": device_id, "changes": sorted(changes)})
        return self.get(device_id)

    def assign(self, device_id: str, channels=None, areas=None) -> dict:
        """What the device follows: channel ids and area ids (None leaves that list as it is)."""
        record = self._records().get(device_id)
        if record is None:
            raise LookupError(f"Device not found: {device_id}")
        for name, value in (("channels", channels), ("areas", areas)):
            if value is None:
                continue
            if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
                raise DeviceError(f"{name}: a list of ids")
        kind = self._kinds.get(record.get("kind"))
        if channels is not None and kind is not None and kind["one_channel"] and len(set(channels)) > 1:
            raise DeviceError(f"{kind['title']} follow one channel at a time")
        if channels is not None:
            known = self._channel_ids()
            missing = [c for c in channels if known is not None and c not in known]
            if missing:
                raise DeviceError(f"Channel not found: {', '.join(missing)}")
            record["channels"] = list(dict.fromkeys(channels))
        if areas is not None:
            record["areas"] = list(dict.fromkeys(areas))
        self._save()
        self._emit("device.assigned", {"device_id": device_id, "channels": list(record.get("channels") or []),
                                       "areas": list(record.get("areas") or [])})
        return self.get(device_id)

    def remove(self, device_id: str) -> None:
        """Forget a device (only an offline one: an online one would come straight back)."""
        if device_id not in self._records():
            raise LookupError(f"Device not found: {device_id}")
        if self.get(device_id)["online"]:
            raise DeviceError("Only an offline device can be removed")
        self._forget(device_id)
        self._save()

    def _forget(self, device_id: str) -> None:
        record = self._records().pop(device_id, None)
        self._online.pop(device_id, None)
        self._errors.pop(device_id, None)
        if record is not None:
            self._emit("device.removed", {"device_id": device_id, "kind": record.get("kind"),
                                          "plugin_id": record.get("plugin")})

    # --- channels and areas ---

    def _channel_ids(self) -> Optional[set]:
        sm = self.session_manager
        lister = getattr(sm, "list", None)
        if lister is None:
            return None
        try:
            return {s.id for s in lister()}
        except Exception:
            return None

    def channel_areas(self, channel_id: str) -> set:
        """The areas of the channel's speakers now."""
        sm = self.session_manager
        if sm is None:
            return set()
        getter = getattr(sm, "get_any", None) or getattr(sm, "get", None)
        session = getter(channel_id) if getter is not None else None
        if session is None:
            return set()
        try:
            speakers = sm.get_resolved_speakers(session)
        except Exception:
            speakers = []
        registry = getattr(sm, "registry", None)
        areas = set()
        for sid in speakers:
            try:
                speaker = registry.get_speaker(sid) if registry is not None else None
            except Exception:
                speaker = None
            if speaker is not None and getattr(speaker, "area_id", None):
                areas.add(speaker.area_id)
        return areas

    def assigned(self, channel_id: str, kind: Optional[str] = None) -> list[dict]:
        """
        The devices (switched on) following a channel: assigned to it, or to
        an area one of its speakers is in. A one-channel kind's device
        assigned to another channel by name follows only that one.
        """
        areas = self.channel_areas(channel_id)
        out = []
        for device in self.list(kind):
            if not device["use"]:
                continue
            direct = channel_id in device["channels"]
            by_area = bool(areas & set(device["areas"]))
            kind_info = self._kinds.get(device["kind"]) or {}
            if kind_info.get("one_channel") and device["channels"] and not direct:
                continue
            if direct or by_area:
                device["follows_by"] = "channel" if direct else "area"
                out.append(device)
        return out

    def following(self, device_id: str) -> list[str]:
        """The channels a device follows now (assigned, or playing on a speaker in one of its areas)."""
        device = self.get(device_id)
        sm = self.session_manager
        out = list(device["channels"])
        if device["areas"] and sm is not None:
            lister = getattr(sm, "list", None)
            for session in (lister() if lister is not None else []):
                if session.id not in out and self.channel_areas(session.id) & set(device["areas"]):
                    out.append(session.id)
        return out

    # --- actions ---

    async def action(self, device_id: str, action: str, params: Optional[dict] = None) -> dict:
        """Run one of the kind's actions (or any action another plugin asks for, through the owner)."""
        record = self._records().get(device_id)
        if record is None:
            raise LookupError(f"Device not found: {device_id}")
        kind = self._kinds.get(record.get("kind"))
        if kind is None:
            raise DeviceError("This device's plugin is switched off")
        if not isinstance(action, str) or not ACTION_ID.match(action):
            raise DeviceError("Invalid action")
        if params is not None and not isinstance(params, dict):
            raise DeviceError("params: an object")
        plugin = self.plugin_of(kind["plugin_id"])
        method = getattr(plugin, "device_action", None) if plugin is not None else None
        if method is None:
            raise DeviceError("The plugin has no device actions")
        try:
            result = method(kind["id"], self.get(device_id), action, dict(params or {}))
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, LIMITS["action_timeout"])
        except asyncio.TimeoutError:
            raise DeviceError("The plugin didn't answer in time")
        except (DeviceError, LookupError):
            raise
        except Exception as e:
            logger.warning(f"Devices: {kind['plugin_id']} device_action({action}) failed: {e}")
            self._errors[device_id] = str(e)[:LIMITS["error_max"]]
            raise DeviceError(str(e) or type(e).__name__)
        if not isinstance(result, dict):
            result = {"success": bool(result)}
        return {"success": bool(result.get("success", True)), "message": str(result.get("message") or ""),
                **({"data": result["data"]} if "data" in result else {})}
