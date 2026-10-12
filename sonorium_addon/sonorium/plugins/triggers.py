"""
Webhook triggers: a plugin declares triggers, and anything on the network
that has the token can call them:

    POST /api/triggers/{plugin_id}/{trigger}     JSON body = payload
    GET  /api/triggers/{plugin_id}/{trigger}?k=v query values = payload (simple devices)

The token goes in "Authorization: Bearer <token>", "X-Sonorium-Token", or
"?token=" (for devices that can only call a URL). It is either the plugin's
token (every trigger of the plugin) or the trigger's own token.

Sonorium answers 202 at once; the plugin's on_trigger(trigger, payload,
context) runs afterwards, and "trigger.received" is published. Calls are
rate limited per trigger, and failed logins per client address.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Callable, Optional

from sonorium.auth.ratelimit import RateLimiter  # noqa: F401  (the core's; imported from here before)
from sonorium.obs import logger

TRIGGER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
METHODS = ("POST", "GET")
MAX_PAYLOAD_BYTES = 16 * 1024
HANDLER_TIMEOUT_SECONDS = 60

# Each trigger: bursts of 10, then 2 calls a second. A client address: 10
# failed logins, then one more every 6 seconds.
TRIGGER_RATE, TRIGGER_BURST = 2.0, 10
AUTH_FAIL_RATE, AUTH_FAIL_BURST = 1 / 6, 10


def normalize_triggers(declared, plugin_id: str = "") -> list[dict]:
    """A plugin's trigger declarations, with only well-formed ones (others logged)."""
    triggers, seen = [], set()
    for item in declared or []:
        if not isinstance(item, dict) or not TRIGGER_ID.match(str(item.get("id", ""))):
            logger.warning(f"Plugin {plugin_id}: trigger {item!r} has no valid id, ignored")
            continue
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        methods = [m.upper() for m in (item.get("methods") or ["POST"]) if str(m).upper() in METHODS]
        triggers.append({
            "id": item["id"],
            "title": str(item.get("title") or item["id"]),
            "description": str(item.get("description") or ""),
            "methods": methods or ["POST"],
            "payload": item.get("payload") if isinstance(item.get("payload"), dict) else {},
        })
    return triggers


class Triggers:
    def __init__(self, tokens, publish: Callable, clock: Callable[[], float] = time.monotonic):
        self.tokens = tokens
        self.publish = publish
        self.trigger_limiter = RateLimiter(TRIGGER_RATE, TRIGGER_BURST, clock)
        self.auth_limiter = RateLimiter(AUTH_FAIL_RATE, AUTH_FAIL_BURST, clock)
        self._running: set[asyncio.Task] = set()

    @staticmethod
    def plugin_scope(plugin_id: str) -> str:
        return f"plugin:{plugin_id}"

    @staticmethod
    def trigger_scope(plugin_id: str, trigger: str) -> str:
        return f"trigger:{plugin_id}/{trigger}"

    def declared(self, plugin) -> list[dict]:
        try:
            return normalize_triggers(plugin.get_triggers(), plugin.id)
        except Exception as e:
            logger.warning(f"Plugin {plugin.id}: get_triggers failed: {e}")
            return []

    def find(self, plugin, trigger: str) -> Optional[dict]:
        return next((t for t in self.declared(plugin) if t["id"] == trigger), None)

    def describe(self, plugin) -> dict:
        """The plugin's triggers with their URLs and which tokens are set (never a token)."""
        return {
            "plugin_id": plugin.id,
            "plugin_token": self.tokens.info(self.plugin_scope(plugin.id)),
            "triggers": [dict(t, url=f"/api/triggers/{plugin.id}/{t['id']}",
                              token=self.tokens.info(self.trigger_scope(plugin.id, t["id"])))
                         for t in self.declared(plugin)],
        }

    def authenticate(self, plugin_id: str, trigger: str, token: Optional[str]) -> bool:
        # Both checked every time: how long it takes says nothing about which token exists
        by_trigger = self.tokens.check(self.trigger_scope(plugin_id, trigger), token)
        by_plugin = self.tokens.check(self.plugin_scope(plugin_id), token)
        return by_trigger or by_plugin

    def fire(self, plugin, trigger: dict, payload: dict, method: str, client: str = "") -> Optional[int]:
        """Publish trigger.received and run the plugin's handler in the background. Returns the event id."""
        event = self.publish("trigger.received", {"plugin_id": plugin.id, "trigger": trigger["id"],
                                                  "payload": payload, "method": method})
        context = {"method": method, "client": client, "event_id": event.id if event else None}
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return event.id if event else None
        task = loop.create_task(self._run(plugin, trigger["id"], payload, context))
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        return event.id if event else None

    async def _run(self, plugin, trigger: str, payload: dict, context: dict) -> None:
        from sonorium.plugins.events import current_origin
        current_origin.set(f"plugin:{plugin.id}")
        try:
            await asyncio.wait_for(plugin.on_trigger(trigger, payload, context), HANDLER_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            message = "timed out" if isinstance(e, asyncio.TimeoutError) else str(e) or type(e).__name__
            logger.warning(f"Plugin {plugin.id}: trigger '{trigger}' failed: {message}")
            self.publish("error", {"where": "trigger", "message": message, "plugin_id": plugin.id,
                                   "trigger": trigger})

    async def wait_running(self) -> None:
        if self._running:
            await asyncio.gather(*list(self._running), return_exceptions=True)
