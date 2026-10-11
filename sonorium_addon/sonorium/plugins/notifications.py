"""
Notification channels: plugins that deliver messages somewhere (Discord, a
phone, e-mail...), and the rules that say which events go to which of them.

- A notifier plugin declares notifiers (get_notifiers()) and delivers with
  deliver_notification(notifier_id, notification). Its notifiers are
  registered while the plugin is enabled, as "<plugin_id>.<notifier_id>".
- A message is a dict: {"title", "body", "level" (info, warning, error),
  "time", "origin", "event" (the event that caused it, or null)}.
- Rules (kept in plugin_framework.json) route events to notifiers:
  {"id", "name", "events": ["channel.started", "error"], "notifiers":
  ["discord.main"], "min_level": "info", "enabled": true}.
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

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "plugin_id": self.owner}


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


def message_for(event: Event) -> dict:
    """The message a rule sends for an event."""
    if event.type == "notification":
        title, body = event.data.get("title", ""), event.data.get("body", "")
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

    # --- Notifiers ---

    def register(self, notifier_id: str, title: str, deliver: Deliver, owner: Optional[str] = None) -> Notifier:
        if not NOTIFIER_ID.match(notifier_id):
            raise NotificationError(f"Invalid notifier id {notifier_id!r} (use '<plugin_id>.<name>')")
        notifier = self._notifiers[notifier_id] = Notifier(notifier_id, str(title or notifier_id), deliver, owner)
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

    # --- Rules ---

    def rules(self) -> list[dict]:
        return [dict(r) for r in self.store.main.section("notification_rules", list)]

    def check_rule(self, rule: dict) -> dict:
        if not isinstance(rule, dict):
            raise NotificationError("A rule must be an object")
        rule_id = rule.get("id") or uuid.uuid4().hex[:8]
        if not RULE_ID.match(str(rule_id)):
            raise NotificationError("Rule id: 1-64 letters, digits, '_' or '-'")
        events = rule.get("events")
        if not isinstance(events, list) or not events:
            raise NotificationError("'events' must list event types or patterns (e.g. 'channel.*')")
        try:
            events = [check_pattern(e) for e in events]
        except ValueError as e:
            raise NotificationError(str(e))
        notifiers = rule.get("notifiers")
        if not isinstance(notifiers, list) or not notifiers or \
                not all(isinstance(n, str) and NOTIFIER_ID.match(n) for n in notifiers):
            raise NotificationError("'notifiers' must list notifier ids ('<plugin_id>.<name>')")
        return {
            "id": str(rule_id),
            "name": str(rule.get("name") or rule_id)[:120],
            "events": events,
            "notifiers": list(dict.fromkeys(notifiers)),
            "min_level": check_level(rule.get("min_level", "info")),
            "enabled": bool(rule.get("enabled", True)),
        }

    def set_rules(self, rules: list) -> list[dict]:
        """Replace every rule (checked first: nothing changes if one is wrong)."""
        if not isinstance(rules, list) or len(rules) > MAX_RULES:
            raise NotificationError(f"rules must be a list of at most {MAX_RULES}")
        clean = [self.check_rule(r) for r in rules]
        if len({r["id"] for r in clean}) != len(clean):
            raise NotificationError("Two rules have the same id")
        self.store.main.data["notification_rules"] = clean
        self.store.main.save()
        return self.rules()

    def put_rule(self, rule: dict) -> dict:
        clean = self.check_rule(rule)
        rules = [r for r in self.store.main.section("notification_rules", list) if r["id"] != clean["id"]]
        if len(rules) >= MAX_RULES:
            raise NotificationError(f"At most {MAX_RULES} rules")
        rules.append(clean)
        self.store.main.data["notification_rules"] = rules
        self.store.main.save()
        return clean

    def delete_rule(self, rule_id: str) -> bool:
        rules = self.store.main.section("notification_rules", list)
        kept = [r for r in rules if r["id"] != rule_id]
        if len(kept) == len(rules):
            return False
        self.store.main.data["notification_rules"] = kept
        self.store.main.save()
        return True

    # --- Sending ---

    def notify(self, title: str, body: str = "", level: str = "info", data: Optional[dict] = None,
               origin: Optional[str] = None):
        """Publish a "notification" event; the rules deliver it. Returns the event."""
        check_level(level)
        payload = {"title": str(title)[:300], "body": str(body)[:4000], "level": level, "data": dict(data or {})}
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
        try:
            await asyncio.wait_for(notifier.deliver(dict(message)), DELIVERY_TIMEOUT_SECONDS)
            return "ok"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            text = "timed out" if isinstance(e, asyncio.TimeoutError) else str(e) or type(e).__name__
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
        for rule in self.store.main.section("notification_rules", list):
            if not rule.get("enabled", True) or level < level_rank(rule.get("min_level", "info")):
                continue
            if any(matches(p, event.type) for p in rule.get("events", [])):
                targets.extend(n for n in rule.get("notifiers", []) if n not in targets)
        if targets:
            await self.send(targets, message_for(event))
        return targets
