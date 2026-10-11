"""
MQTT for plugins: publish and subscribe on the broker Sonorium is connected
to, next to Sonorium's own Home Assistant entities.

Every message from the broker goes to Sonorium's entity manager first, then
to each plugin subscription whose filter matches (MQTT wildcards: "+" one
level, "#" the rest). A plugin's subscriptions go when it's disabled.
Without a broker (standalone without MQTT) publishing does nothing and
`available` is False; subscriptions wait and start when a broker is linked.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Awaitable, Callable, Optional

from sonorium.obs import logger

MAX_SUBSCRIPTIONS_PER_PLUGIN = 100
HANDLER_TIMEOUT_SECONDS = 30


def topic_matches(topic_filter: str, topic: str) -> bool:
    f, t = topic_filter.split("/"), topic.split("/")
    for i, part in enumerate(f):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(f) == len(t)


def check_filter(topic_filter) -> str:
    if not isinstance(topic_filter, str) or not topic_filter or len(topic_filter) > 256 or "\x00" in topic_filter:
        raise ValueError("A topic filter is text (1-256 characters)")
    parts = topic_filter.split("/")
    for i, part in enumerate(parts):
        if "#" in part and (part != "#" or i != len(parts) - 1):
            raise ValueError("'#' may only be the last level of a topic filter")
        if "+" in part and part != "+":
            raise ValueError("'+' must be a whole level of a topic filter")
    return topic_filter


class MqttRouter:
    def __init__(self, prefix: str = "sonorium"):
        self.client = None                  # sonorium.client.MQTTClient, once connected
        self.prefix = prefix
        self.device_info = {"identifiers": [f"{prefix}_device"], "name": "Sonorium",
                            "model": "Ambient Soundscape Mixer", "manufacturer": "Sonorium"}
        self.core_handler: Optional[Callable[[str, str], Awaitable[None]]] = None
        self._subs: list[tuple[str, str, Callable]] = []   # (owner, filter, handler)

    @property
    def available(self) -> bool:
        client = self.client
        return client is not None and bool(getattr(client, "is_connected", False))

    def bind(self, client, core_handler=None) -> None:
        """The broker connection, and Sonorium's own handler (the entity manager's commands)."""
        self.client = client
        self.core_handler = core_handler
        if client is not None:
            client.set_message_handler(self.dispatch)
            for _, topic_filter, _ in self._subs:
                self._subscribe(topic_filter)

    def _subscribe(self, topic_filter: str) -> None:
        if self.client is not None:
            try:
                self.client.subscribe(topic_filter)
            except Exception as e:
                logger.warning(f"MQTT: could not subscribe to {topic_filter}: {e}")

    async def dispatch(self, topic: str, payload: str) -> None:
        if self.core_handler is not None:
            try:
                await self.core_handler(topic, payload)
            except Exception as e:
                logger.error(f"MQTT command {topic} failed: {e}")
        for owner, topic_filter, handler in list(self._subs):
            if not topic_matches(topic_filter, topic):
                continue
            try:
                result = handler(topic, payload)
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, HANDLER_TIMEOUT_SECONDS)
            except Exception as e:
                logger.warning(f"Plugin {owner}: MQTT handler for {topic} failed: {e}")

    # --- For plugins ---

    def subscribe(self, owner: str, topic_filter: str, handler: Callable) -> None:
        topic_filter = check_filter(topic_filter)
        if sum(1 for o, _, _ in self._subs if o == owner) >= MAX_SUBSCRIPTIONS_PER_PLUGIN:
            raise ValueError(f"At most {MAX_SUBSCRIPTIONS_PER_PLUGIN} subscriptions per plugin")
        self._subs.append((owner, topic_filter, handler))
        self._subscribe(topic_filter)

    def unsubscribe(self, owner: str, topic_filter: Optional[str] = None) -> int:
        before = len(self._subs)
        self._subs = [s for s in self._subs if not (s[0] == owner and topic_filter in (None, s[1]))]
        return before - len(self._subs)

    def unsubscribe_owner(self, owner: str) -> int:
        return self.unsubscribe(owner)

    async def publish(self, topic: str, payload, retain: bool = False) -> bool:
        """Publish (text, or JSON for a dict/list). False without a broker."""
        if not isinstance(topic, str) or not topic or "+" in topic or "#" in topic:
            raise ValueError("A topic to publish to is text without wildcards")
        if not self.available:
            return False
        if not isinstance(payload, (str, bytes)):
            import json
            payload = json.dumps(payload)
        client = self.client
        await asyncio.get_running_loop().run_in_executor(None, lambda: client.publish(topic, payload, retain=retain))
        return True
