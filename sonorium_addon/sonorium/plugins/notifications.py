"""
Notifications: "something on the network needs your attention" (dinner is
ready, the doorbell rang, motion on a camera, a leak sensor). Their main job
is to take over the audio on channels: a rule announces them (duck what
plays, a chime and/or the message spoken, then restore), as an admin, over
zones and quiet hours. Sending them to an outside notifier (Discord, a
phone) is an optional extra on a rule.

They come from Sonorium's own sources (a rule's "when": a Home Assistant
entity changing, a webhook URL of the rule's own, an MQTT topic; see
notify_sources.py) and from outside sources that plugins declare (their
notification sources and webhook triggers). Sonorium's own problems (a speaker offline, a
plugin error, a lost connection) are not notifications: they go to the log,
and serious ones show an alert bar in the web UI (sonorium/alerts.py).

Sources (GET /api/notifications lists them):
    plugin.<plugin_id>.<source>    a plugin's declared source (get_notification_sources());
                                   the plugin raises it with self.notify(..., source=...)
                                   or self.emit(<source>, {"title", "body"})
    trigger:<plugin_id>/<trigger>  a webhook trigger being called

A rule:
    {"id", "name", "when": {"type": "ha" | "webhook" | "mqtt", ...}, "sources": [...],
     "announce": {"channels": [...], "sound": "https://.../chime.mp3",
                  "text": "Someone is at the door", "volume": 80,
                  "duck": {"db": 15, "attack": 0.5, "release": 1.5}},
     "notifiers": ["discord.main"], "enabled": true}
"text" may use the message's fields: {title}, {message} (or {body}), and from
Sonorium's own sources {entity}, {state}, {topic}. An announcement with
neither "text" nor "sound" speaks the title. "duck" left out: the Advanced
page's values. Rules may also list raw event patterns in "events" (and a
"min_level"); the web UI doesn't offer that.

Outside notifiers:

- A notifier plugin declares notifiers (get_notifiers()) and delivers with
  deliver_notification(notifier_id, notification). Its notifiers are
  registered while the plugin is enabled, as "<plugin_id>.<notifier_id>".
- A message is a dict: {"title", "body", "level" (info, warning, error),
  "time", "origin", "event" (the event that caused it, or null)}.
- Rules are kept in plugin_framework.json.
- notify() (any plugin, through BasePlugin.notify) publishes a
  "notification" event: the rules route it like any other event. With
  `to=[...]` it is delivered to those notifiers directly instead.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from sonorium.obs import logger
from sonorium.plugins.events import CATALOGUE, Event, check_pattern, matches, summary

LEVELS = ("info", "warning", "error")
DELIVERY_TIMEOUT_SECONDS = 30
NOTIFIER_ID = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{1,64}$")
RULE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_RULES = 100

# Events that are worth a warning or an error by default (the rest are info)
EVENT_LEVELS = {"error": "error", "speaker.unavailable": "warning", "speaker.removed": "warning"}

Deliver = Callable[[dict], Awaitable[None]]


class NotificationError(ValueError):
    """A rule or a message that isn't valid."""


@dataclass
class Notifier:
    id: str
    title: str
    deliver: Deliver
    owner: Optional[str] = None
    kind: str = ""                  # what it delivers to ("Discord channel"), from the plugin's declaration
    last_result: Optional[str] = None   # "ok" or the last error, None before the first message
    last_time: Optional[str] = None

    def to_dict(self) -> dict:
        state = "unknown" if self.last_result is None else "ok" if self.last_result == "ok" else "failing"
        return {"id": self.id, "title": self.title, "plugin_id": self.owner, "kind": self.kind,
                "state": state, "last_result": self.last_result, "last_time": self.last_time}


def level_rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else 0


def check_level(level) -> str:
    if level not in LEVELS:
        raise NotificationError(f"level must be one of {', '.join(LEVELS)}")
    return level


def event_level(event: Event) -> str:
    if event.type == "notification":
        return event.data.get("level") if event.data.get("level") in LEVELS else "info"
    return EVENT_LEVELS.get(event.type, "info")


def source_matches(source: str, event: Event) -> bool:
    """Whether an event comes from a notification source."""
    data = event.data or {}
    if source.startswith("trigger:"):
        return event.type == "trigger.received" and f"{data.get('plugin_id')}/{data.get('trigger')}" == source[8:]
    if event.type == "notification" and data.get("source") == source:
        return True
    return event.type == source


SOURCE_ID = re.compile(r"^(trigger:[A-Za-z0-9_-]+/[A-Za-z0-9_-]{1,64}|plugin\.[A-Za-z0-9_-]+\.[a-z0-9_][a-z0-9_.-]{0,63})$")


def message_for(event: Event) -> dict:
    """The message a rule sends for an event."""
    data = event.data or {}
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    if event.type == "notification":
        title, body = data.get("title", ""), data.get("body", "")
    elif event.type.startswith("plugin.") and (data.get("title") or data.get("body")):
        title, body = data.get("title") or summary(event), data.get("body", "")
    elif event.type == "trigger.received" and (payload.get("title") or payload.get("message")):
        title, body = payload.get("title") or payload.get("message"), payload.get("body") or ""
    else:
        title = summary(event)
        body = (CATALOGUE.get(event.type) or {}).get("description", "")
    return {"title": str(title), "body": str(body), "level": event_level(event), "time": event.time,
            "origin": event.origin, "event": event.to_dict()}


class Notifications:
    def __init__(self, store, publish: Callable):
        self.store = store
        self.publish = publish
        self._notifiers: dict[str, Notifier] = {}
        self._running: set[asyncio.Task] = set()
        # Set by the plugin host: the sources plugins declare, and announcing on channels
        self.source_provider: Callable[[], list] = lambda: []
        self.announcer: Optional[Callable[[dict, dict], Awaitable[None]]] = None
        # Called after the rules change (the built-in sources follow them: notify_sources.py)
        self.on_rules_changed: Optional[Callable[[], None]] = None

    # --- Notifiers ---

    def register(self, notifier_id: str, title: str, deliver: Deliver, owner: Optional[str] = None,
                 kind: str = "") -> Notifier:
        if not NOTIFIER_ID.match(notifier_id):
            raise NotificationError(f"Invalid notifier id {notifier_id!r} (use '<plugin_id>.<name>')")
        notifier = self._notifiers[notifier_id] = Notifier(notifier_id, str(title or notifier_id), deliver, owner,
                                                           str(kind or "")[:60])
        return notifier

    def unregister(self, notifier_id: str) -> bool:
        return self._notifiers.pop(notifier_id, None) is not None

    def unregister_owner(self, owner: str) -> int:
        ids = [n.id for n in self._notifiers.values() if n.owner == owner]
        for notifier_id in ids:
            self._notifiers.pop(notifier_id, None)
        return len(ids)

    def notifiers(self) -> list[dict]:
        return [n.to_dict() for n in sorted(self._notifiers.values(), key=lambda n: n.id)]

    def sources(self) -> list[dict]:
        """What can raise a notification now: [{"id", "title", "description", "plugin_id", "kind"}]."""
        try:
            return list(self.source_provider() or [])
        except Exception as e:
            logger.warning(f"Notification sources could not be listed: {e}")
            return []

    # --- Rules ---

    def rules(self) -> list[dict]:
        return [dict(r) for r in self.store.main.section("notification_rules", list)]

    def check_rule(self, rule: dict) -> dict:
        if not isinstance(rule, dict):
            raise NotificationError("A rule must be an object")
        rule_id = rule.get("id") or uuid.uuid4().hex[:8]
        if not RULE_ID.match(str(rule_id)):
            raise NotificationError("Rule id: 1-64 letters, digits, '_' or '-'")
        sources = rule.get("sources") or []
        if not isinstance(sources, list) or not all(isinstance(x, str) and SOURCE_ID.match(x) for x in sources):
            raise NotificationError("'sources' must list notification sources (plugin.<id>.<name> or "
                                    "trigger:<plugin>/<trigger>)")
        events = rule.get("events") or []
        if not isinstance(events, list):
            raise NotificationError("'events' must list event types or patterns (e.g. 'channel.*')")
        try:
            events = [check_pattern(e) for e in events]
        except ValueError as e:
            raise NotificationError(str(e))
        from sonorium.plugins.notify_sources import SourceError, check_when
        try:
            when = check_when(rule.get("when"))
        except SourceError as e:
            raise NotificationError(str(e))
        if not sources and not events and when is None:
            raise NotificationError("A rule needs a source")
        notifiers = rule.get("notifiers") or []
        if not isinstance(notifiers, list) or \
                not all(isinstance(n, str) and NOTIFIER_ID.match(n) for n in notifiers):
            raise NotificationError("'notifiers' must list notifier ids ('<plugin_id>.<name>')")
        announce = self.check_announce(rule.get("announce"))
        if not notifiers and announce is None:
            raise NotificationError("A rule announces on channels, sends to a notifier, or both")
        return {
            "id": str(rule_id),
            "name": str(rule.get("name") or rule_id)[:120],
            "when": when,
            "sources": list(dict.fromkeys(sources)),
            "events": events,
            "announce": announce,
            "notifiers": list(dict.fromkeys(notifiers)),
            "min_level": check_level(rule.get("min_level", "info")),
            "enabled": bool(rule.get("enabled", True)),
        }

    @staticmethod
    def check_announce(announce) -> Optional[dict]:
        if announce in (None, False, {}):
            return None
        if not isinstance(announce, dict):
            raise NotificationError("'announce' must be an object: channels, sound, text, volume, duck")
        channels = announce.get("channels")
        if not isinstance(channels, list) or not channels or not all(isinstance(c, str) and c for c in channels):
            raise NotificationError("'announce' needs the channels to announce on")
        sound = announce.get("sound") or None
        if sound is not None and (not isinstance(sound, str) or not re.match(r"^https?://\S+$", sound)):
            raise NotificationError("'sound' must be an http(s) URL")
        text = announce.get("text") or None
        if text is not None and (not isinstance(text, str) or len(text) > 500):
            raise NotificationError("'text' must be text (at most 500 characters)")
        volume = announce.get("volume", 100)
        if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not 0 <= volume <= 200:
            raise NotificationError("'volume' must be 0-200")
        duck = announce.get("duck")
        if duck is not None and not isinstance(duck, (bool, dict)):
            raise NotificationError("'duck' must be true, false or an object (db, attack, release)")
        if isinstance(duck, dict):
            for key, value in duck.items():
                if key not in ("db", "attack", "release") or isinstance(value, bool) or \
                        not isinstance(value, (int, float)) or value < 0:
                    raise NotificationError("'duck' takes db, attack and release, each a number 0 or more")
        return {"channels": list(dict.fromkeys(channels)), "sound": sound, "text": text, "volume": volume,
                "duck": True if duck is None else duck}

    def set_rules(self, rules: list) -> list[dict]:
        """Replace every rule (checked first: nothing changes if one is wrong)."""
        if not isinstance(rules, list) or len(rules) > MAX_RULES:
            raise NotificationError(f"rules must be a list of at most {MAX_RULES}")
        clean = [self.check_rule(r) for r in rules]
        if len({r["id"] for r in clean}) != len(clean):
            raise NotificationError("Two rules have the same id")
        self.store.main.data["notification_rules"] = clean
        self.store.main.save()
        self._changed()
        return self.rules()

    def put_rule(self, rule: dict) -> dict:
        clean = self.check_rule(rule)
        rules = [r for r in self.store.main.section("notification_rules", list) if r["id"] != clean["id"]]
        if len(rules) >= MAX_RULES:
            raise NotificationError(f"At most {MAX_RULES} rules")
        rules.append(clean)
        self.store.main.data["notification_rules"] = rules
        self.store.main.save()
        self._changed()
        return clean

    def delete_rule(self, rule_id: str) -> bool:
        rules = self.store.main.section("notification_rules", list)
        kept = [r for r in rules if r["id"] != rule_id]
        if len(kept) == len(rules):
            return False
        self.store.main.data["notification_rules"] = kept
        self.store.main.save()
        self._changed()
        return True

    def _changed(self) -> None:
        if self.on_rules_changed is not None:
            try:
                self.on_rules_changed()
            except Exception as e:
                logger.warning(f"Notification sources could not follow the rules: {e}")

    # --- Sending ---

    def notify(self, title: str, body: str = "", level: str = "info", data: Optional[dict] = None,
               origin: Optional[str] = None, source: Optional[str] = None):
        """Publish a "notification" event (from a source "plugin.<id>.<name>"); the rules deliver it."""
        check_level(level)
        payload = {"title": str(title)[:300], "body": str(body)[:4000], "level": level, "data": dict(data or {}),
                   "source": source}
        return self.publish("notification", payload, origin=origin) if origin else \
            self.publish("notification", payload)

    async def send(self, notifier_ids: list[str], message: dict) -> dict:
        """Deliver a message to notifiers now. Returns {notifier_id: "ok" | error text}."""
        results = await asyncio.gather(*(self._deliver(nid, message) for nid in notifier_ids))
        return dict(zip(notifier_ids, results))

    async def _deliver(self, notifier_id: str, message: dict) -> str:
        notifier = self._notifiers.get(notifier_id)
        if notifier is None:
            return "not available"
        notifier.last_time = datetime.now(timezone.utc).isoformat()
        try:
            await asyncio.wait_for(notifier.deliver(dict(message)), DELIVERY_TIMEOUT_SECONDS)
            notifier.last_result = "ok"
            return "ok"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            text = "timed out" if isinstance(e, asyncio.TimeoutError) else str(e) or type(e).__name__
            notifier.last_result = text[:300]
            logger.warning(f"Notifier {notifier_id} failed: {text}")
            self.publish("error", {"where": "notifier", "message": text, "plugin_id": notifier.owner,
                                   "notifier": notifier_id})
            return text

    async def route(self, event: Event) -> list[str]:
        """Deliver an event to the notifiers of every rule it matches. Returns the notifier ids used."""
        if event.type == "error" and event.data.get("where") == "notifier":
            return []  # a failing notifier must not set off more notifications
        level = level_rank(event_level(event))
        targets: list[str] = []
        announcements: list[dict] = []
        for rule in self.store.main.section("notification_rules", list):
            if not rule.get("enabled", True) or level < level_rank(rule.get("min_level", "info")):
                continue
            if any(source_matches(s, event) for s in rule.get("sources", [])) or \
                    any(matches(p, event.type) for p in rule.get("events", [])):
                targets.extend(n for n in rule.get("notifiers", []) if n not in targets)
                if rule.get("announce"):
                    announcements.append(rule["announce"])
        if not targets and not announcements:
            return targets
        message = message_for(event)
        for announce in announcements:  # first: the audio takes over at once
            await self._announce(announce, message)
        if targets:
            await self.send(targets, message)
        return targets

    async def fire(self, rule: dict, message: dict) -> dict:
        """
        Do what a rule does with a message: announce it on its channels, then
        send it to its notifiers. Returns {"announced", "error", "sent"}.
        """
        result = {"announced": False, "error": "", "sent": {}}
        if rule.get("announce") and self.announcer is not None:
            try:
                await self.announcer(rule["announce"], message)
                result["announced"] = True
            except Exception as e:
                result["error"] = str(getattr(e, "message", None) or e)
                logger.warning(f"Announcing a notification failed: {result['error']}")
        if rule.get("notifiers"):
            result["sent"] = await self.send(rule["notifiers"], message)
        return result

    async def _announce(self, announce: dict, message: dict) -> None:
        if self.announcer is None:
            return
        try:
            await self.announcer(announce, message)
        except Exception as e:
            logger.warning(f"Announcing a notification failed: {e}")
