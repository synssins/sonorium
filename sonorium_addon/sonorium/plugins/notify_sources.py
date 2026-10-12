"""
Notification sources built into Sonorium, so rules work without a plugin.
A rule's "when" is one of:

    {"type": "ha", "entity_id": "binary_sensor.front_doorbell", "state": "on"}
        A Home Assistant entity changes to that state ("" or left out: any
        change of its state). For an event entity the state may also be the
        event type ("pressed"); for a button or input_button any press is a
        change. Uses Sonorium's own Home Assistant connection (the Supervisor
        on the add-on, the linked URL and token on Docker), subscribed only to
        the entities rules use.

    {"type": "webhook"}
        A URL of its own: GET or POST /api/notify/<secret>. The secret is made
        per rule, kept in plugin_secrets.json and is the only credential (it
        works with Require login on). Optional fields, as JSON, a form or the
        query string: {"title", "message"}.

    {"type": "mqtt", "topic": "home/doorbell", "payload": "ON"}
        A message on the topic (MQTT wildcards allowed); with "payload", only
        a message that is exactly that (spaces around it ignored). Needs MQTT.

Plugin sources ("sources": [...], notifications.py) work beside these.

The Say text of a rule may use {title}, {message} ({body} is the same),
{entity} (the entity's name), {state} and {topic}. A rule's title is the
webhook's "title" if it sent one, else the rule's name.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import re
import secrets as _secrets
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from sonorium.auth.ratelimit import RateLimiter
from sonorium.obs import logger

KINDS = ("ha", "webhook", "mqtt")
ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
SECRET = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
SECRETS_SECTION = "notification_webhooks"
WEBHOOK_PATH = "/api/notify/"
MQTT_OWNER = "notifications"

# What the entity picker offers: things that "happen"
HA_DOMAINS = ("binary_sensor", "event", "input_button", "button", "input_boolean", "switch", "sensor",
              "lock", "cover", "alarm_control_panel", "person", "device_tracker", "input_select", "timer",
              "automation", "script")
# Domains whose state is the time of the last press/event: any change is "it happened"
PRESS_DOMAINS = ("event", "button", "input_button")
NOT_A_STATE = ("unavailable",)

# Webhook calls: per rule bursts of 10, then 2 a second; wrong secrets per client address
# 10, then one more every 6 seconds (as webhook triggers)
WEBHOOK_RATE, WEBHOOK_BURST = 2.0, 10
AUTH_FAIL_RATE, AUTH_FAIL_BURST = 1 / 6, 10
MAX_FIELD = 500


class SourceError(ValueError):
    """A rule's "when" that isn't valid."""


def check_when(when) -> Optional[dict]:
    """A rule's "when", cleaned; None when it has none."""
    if when in (None, {}, False):
        return None
    if not isinstance(when, dict) or when.get("type") not in KINDS:
        raise SourceError("'when' needs a type: ha, webhook or mqtt")
    kind = when["type"]
    if kind == "ha":
        entity_id = str(when.get("entity_id") or "").strip().lower()
        if not ENTITY_ID.match(entity_id) or len(entity_id) > 255:
            raise SourceError("'entity_id' must be a Home Assistant entity id (binary_sensor.front_door)")
        state = when.get("state")
        if (state is not None and not isinstance(state, str)) or len(str(state or "")) > 255:
            raise SourceError("'state' must be text")
        clean = {"type": "ha", "entity_id": entity_id, "state": (state or "").strip()}
        label = when.get("label")
        if isinstance(label, str) and label.strip():
            clean["label"] = label.strip()[:120]  # the entity's name, for the rules list
        return clean
    if kind == "mqtt":
        from sonorium.plugins.mqtt import check_filter
        try:
            topic = check_filter(str(when.get("topic") or "").strip())
        except ValueError as e:
            raise SourceError(f"'topic': {e}")
        payload = when.get("payload")
        if (payload is not None and not isinstance(payload, str)) or len(str(payload or "")) > 1000:
            raise SourceError("'payload' must be text")
        return {"type": "mqtt", "topic": topic, "payload": (payload or "").strip()}
    return {"type": "webhook"}


# --- Matching ---------------------------------------------------------------------

def _name(state: Optional[dict], entity_id: str) -> str:
    attributes = (state or {}).get("attributes") or {}
    return str(attributes.get("friendly_name") or entity_id)


def ha_change_matches(when: dict, entity_id: str, old: Optional[dict], new: Optional[dict]) -> bool:
    """Whether a state change of an entity is what a rule's "when" waits for."""
    if when.get("type") != "ha" or when.get("entity_id") != entity_id or not isinstance(new, dict):
        return False
    new_state = str(new.get("state", ""))
    old_state = str((old or {}).get("state", "")) if isinstance(old, dict) else None
    if old_state is None or old_state in NOT_A_STATE:
        return False  # coming back after a restart is not a change
    if old_state == new_state:
        return False  # only attributes changed
    wanted = (when.get("state") or "").strip().lower()
    if not wanted:
        return new_state not in NOT_A_STATE
    if new_state.lower() == wanted:
        return True
    event_type = str(((new.get("attributes") or {}).get("event_type")) or "").lower()
    return entity_id.split(".", 1)[0] == "event" and event_type == wanted


def ha_fields(entity_id: str, new: Optional[dict]) -> dict:
    state = str((new or {}).get("state", ""))
    attributes = (new or {}).get("attributes") or {}
    if entity_id.split(".", 1)[0] == "event" and attributes.get("event_type"):
        state = str(attributes["event_type"])
    return {"entity": _name(new, entity_id), "state": state, "entity_id": entity_id}


def mqtt_matches(when: dict, topic: str, payload: str) -> bool:
    from sonorium.plugins.mqtt import topic_matches
    if when.get("type") != "mqtt" or not topic_matches(when.get("topic", ""), topic):
        return False
    wanted = when.get("payload") or ""
    return not wanted or (payload or "").strip() == wanted


def mqtt_fields(topic: str, payload: str) -> dict:
    text = (payload or "").strip()
    fields = {"topic": topic, "message": text[:MAX_FIELD], "state": text[:MAX_FIELD]}
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            fields.update(payload_fields(data))
    return fields


def payload_fields(data: dict) -> dict:
    """{"title", "message"} (or "body") of a webhook's or MQTT message's JSON."""
    fields = {}
    for key, value in (("title", data.get("title")), ("message", data.get("message", data.get("body")))):
        if isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value).strip():
            fields[key] = str(value).strip()[:MAX_FIELD]
    return fields


class _Fields(dict):
    def __missing__(self, key):
        return ""


def render(text: str, fields: dict) -> str:
    """The Say text with {title}, {message}, {body}, {entity}, {state}, {topic} filled in (others: empty)."""
    values = _Fields({k: v for k, v in fields.items() if isinstance(v, str)})
    values.setdefault("body", values.get("message", ""))
    values.setdefault("message", values.get("body", ""))
    try:
        return text.format_map(values).strip()
    except (ValueError, IndexError, AttributeError):
        return text


def message_for_rule(rule: dict, fields: dict, origin: str) -> dict:
    """The message a built-in source sends through a rule."""
    title = fields.get("title") or rule.get("name") or rule.get("id") or "Notification"
    body = fields.get("message") or (f"{fields['entity']} {fields.get('state', '')}".strip()
                                     if fields.get("entity") else "")
    clean = {k: str(v)[:MAX_FIELD] for k, v in fields.items() if isinstance(v, (str, int, float))}
    clean.update(title=str(title)[:300], message=str(body)[:MAX_FIELD])
    return {"title": clean["title"], "body": clean["message"], "level": "info",
            "time": datetime.now(timezone.utc).isoformat(), "origin": origin, "event": None, "fields": clean}


def sample_fields(rule: dict) -> dict:
    """Stand-in words for trying a rule: what its source would send."""
    when = rule.get("when") or {}
    if when.get("type") == "ha":
        return {"entity": when.get("label") or when["entity_id"], "state": when.get("state") or "on", "entity_id": when["entity_id"]}
    if when.get("type") == "mqtt":
        return {"topic": when["topic"], "message": when.get("payload") or "", "state": when.get("payload") or ""}
    return {}


# --- Home Assistant: state changes of the entities rules use -----------------------

class HaStateWatcher:
    """
    One WebSocket connection to Home Assistant while rules use entities:
    subscribe_trigger (a state trigger for just those entities), or, if Home
    Assistant refuses that, state_changed events filtered here. Reconnects
    with a growing pause; the entity list changing restarts it.
    """

    RETRY_FIRST, RETRY_MAX, IDLE_CHECK = 5.0, 60.0, 30.0

    def __init__(self, config: Callable[[], tuple[str, str]], available: Callable[[], bool],
                 on_change: Callable, connect=None):
        self._config = config
        self._available = available
        self._on_change = on_change   # async (entity_id, old_state, new_state)
        self._connect = connect       # tests: an async context manager factory (url) -> websocket
        self._entities: frozenset = frozenset()
        self._task: Optional[asyncio.Task] = None
        self.connected = False
        self.mode = ""                # "trigger" or "events" once subscribed

    @property
    def entities(self) -> frozenset:
        return self._entities

    def watch(self, entities) -> None:
        entities = frozenset(entities)
        if entities == self._entities and (self._task is not None or not entities):
            return
        self._entities = entities
        self.stop()
        if entities:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # started again by start() once the loop runs
            self._task = loop.create_task(self._run())

    def start(self) -> None:
        if self._entities and self._task is None:
            entities, self._entities = self._entities, frozenset()
            self.watch(entities)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.connected = False

    def _ws_connect(self, url: str):
        if self._connect is not None:
            return self._connect(url)
        import websockets
        return websockets.connect(url, max_size=16 * 1024 * 1024)

    async def _run(self) -> None:
        pause = self.RETRY_FIRST
        while True:
            if not self._available():
                await asyncio.sleep(self.IDLE_CHECK)
                continue
            try:
                await self._session()
                pause = self.RETRY_FIRST
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Notifications: Home Assistant states unavailable ({e}); trying again in {int(pause)} s")
            self.connected = False
            await asyncio.sleep(pause)
            pause = min(pause * 2, self.RETRY_MAX)

    async def _session(self) -> None:
        from sonorium.runtime import ha_websocket_url
        base, token = self._config()
        entities = sorted(self._entities)
        async with self._ws_connect(ha_websocket_url(base)) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if hello.get("type") != "auth_required":
                raise ConnectionError(f"unexpected answer {hello.get('type')!r}")
            await ws.send(json.dumps({"type": "auth", "access_token": token}))
            auth = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if auth.get("type") != "auth_ok":
                raise ConnectionError("Home Assistant refused the token")
            await ws.send(json.dumps({"id": 1, "type": "subscribe_trigger",
                                      "trigger": {"platform": "state", "entity_id": entities}}))
            self.mode = "trigger"
            wanted = set(entities)
            while True:
                message = json.loads(await ws.recv())
                kind = message.get("type")
                if kind == "result":
                    if message.get("success"):
                        self.connected = True
                        logger.info(f"Notifications: watching {len(entities)} Home Assistant "
                                    f"entit{'y' if len(entities) == 1 else 'ies'}")
                    elif message.get("id") == 1:  # e.g. not an admin token: every state change, filtered here
                        await ws.send(json.dumps({"id": 2, "type": "subscribe_events", "event_type": "state_changed"}))
                        self.mode = "events"
                    else:
                        raise ConnectionError(str((message.get("error") or {}).get("message") or "refused"))
                    continue
                if kind != "event":
                    continue
                change = self._change_of(message.get("event") or {})
                if change is not None and change[0] in wanted:
                    try:
                        await self._on_change(*change)
                    except Exception as e:
                        logger.warning(f"Notifications: handling {change[0]} failed: {e}")

    @staticmethod
    def _change_of(event: dict) -> Optional[tuple]:
        trigger = (event.get("variables") or {}).get("trigger")
        if isinstance(trigger, dict):
            return trigger.get("entity_id"), trigger.get("from_state"), trigger.get("to_state")
        if event.get("event_type") == "state_changed":
            data = event.get("data") or {}
            return data.get("entity_id"), data.get("old_state"), data.get("new_state")
        return None


# --- The sources together ------------------------------------------------------------

class BuiltinSources:
    def __init__(self, notifications, store, mqtt, homeassistant: Callable[[], object],
                 clock: Callable[[], float] = time.monotonic, ws_connect=None):
        self.notifications = notifications
        self.store = store
        self.mqtt = mqtt
        self._ha = homeassistant          # the host's HomeAssistant (None until the app sets it)
        self.webhook_limiter = RateLimiter(WEBHOOK_RATE, WEBHOOK_BURST, clock)
        self.auth_limiter = RateLimiter(AUTH_FAIL_RATE, AUTH_FAIL_BURST, clock)
        self.watcher = HaStateWatcher(self._ha_config, self.ha_available, self.on_ha_change, connect=ws_connect)
        self._mqtt_filters: set = set()
        self._running: set[asyncio.Task] = set()

    # --- Home Assistant ---

    def ha_available(self) -> bool:
        ha = self._ha()
        return bool(ha is not None and ha.available)

    def _ha_config(self) -> tuple[str, str]:
        return self._ha()._config()

    async def ha_entities(self, query: str = "", limit: int = 100) -> list[dict]:
        """Entities for the picker: [{"entity_id", "name", "state", "domain"}], useful domains, searched."""
        ha = self._ha()
        if ha is None or not ha.available:
            return []
        status, states = await ha.request("GET", "/api/states")
        if status != 200 or not isinstance(states, list):
            from sonorium.plugins.homeassistant import HomeAssistantError
            raise HomeAssistantError(f"Home Assistant answered {status}")
        words = [w for w in (query or "").lower().split() if w]
        out = []
        for s in states:
            if not isinstance(s, dict):
                continue
            entity_id = str(s.get("entity_id", ""))
            domain = entity_id.split(".", 1)[0]
            if domain not in HA_DOMAINS:
                continue
            name = _name(s, entity_id)
            haystack = f"{entity_id} {name}".lower()
            if words and not all(w in haystack for w in words):
                continue
            state = str(s.get("state", ""))
            event_types = (s.get("attributes") or {}).get("event_types") if domain == "event" else None
            item = {"entity_id": entity_id, "name": name, "state": state, "domain": domain}
            if isinstance(event_types, list):
                item["event_types"] = [str(t) for t in event_types][:20]
            out.append(item)
        out.sort(key=lambda x: (HA_DOMAINS.index(x["domain"]), x["name"].lower()))
        return out[:max(1, min(int(limit or 100), 500))]

    async def on_ha_change(self, entity_id: str, old: Optional[dict], new: Optional[dict]) -> None:
        for rule in self._rules("ha"):
            if ha_change_matches(rule["when"], entity_id, old, new):
                await self.fire(rule, ha_fields(entity_id, new), "homeassistant")

    # --- MQTT ---

    async def on_mqtt(self, topic: str, payload: str) -> None:
        for rule in self._rules("mqtt"):
            if mqtt_matches(rule["when"], topic, payload):
                await self.fire(rule, mqtt_fields(topic, payload), "mqtt")

    # --- Webhooks ---

    def _secrets(self) -> dict:
        return self.store.secrets.section(SECRETS_SECTION, dict)

    def webhook_secret(self, rule_id: str) -> Optional[str]:
        return self._secrets().get(rule_id)

    def webhook_path(self, rule_id: str) -> Optional[str]:
        secret = self.webhook_secret(rule_id)
        return f"{WEBHOOK_PATH}{secret}" if secret else None

    def new_webhook_secret(self, rule_id: str) -> str:
        """Make (or replace) the rule's secret: the old URL stops working."""
        secret = _secrets.token_urlsafe(24)
        self._secrets()[rule_id] = secret
        self.store.secrets.save()
        return secret

    def rule_for_secret(self, secret: Optional[str]) -> Optional[dict]:
        """The enabled-or-not rule whose webhook secret this is (every secret compared, in constant time)."""
        if not isinstance(secret, str) or not SECRET.match(secret):
            return None
        found = None
        for rule_id, known in list(self._secrets().items()):
            if isinstance(known, str) and hmac.compare_digest(known.encode(), secret.encode()):
                found = rule_id
        if found is None:
            return None
        return next((r for r in self.notifications.rules() if r["id"] == found
                     and (r.get("when") or {}).get("type") == "webhook"), None)

    async def call_webhook(self, rule: dict, payload: dict) -> None:
        await self.fire(rule, payload_fields(payload), "webhook")

    # --- Rules changed: secrets, MQTT subscriptions, entities watched ---

    def _rules(self, kind: str) -> list[dict]:
        return [r for r in self.store.main.section("notification_rules", list)
                if r.get("enabled", True) and (r.get("when") or {}).get("type") == kind]

    def sync(self) -> None:
        rules = self.store.main.section("notification_rules", list)
        hooks = {r["id"] for r in rules if (r.get("when") or {}).get("type") == "webhook"}
        stored = self._secrets()
        changed = False
        for rule_id in list(stored):
            if rule_id not in hooks:
                stored.pop(rule_id, None)
                changed = True
        for rule_id in hooks - set(stored):
            stored[rule_id] = _secrets.token_urlsafe(24)
            changed = True
        if changed:
            self.store.secrets.save()
        filters = {r["when"]["topic"] for r in self._rules("mqtt")}
        if filters != self._mqtt_filters:
            self.mqtt.unsubscribe_owner(MQTT_OWNER)
            for topic_filter in sorted(filters):
                try:
                    self.mqtt.subscribe(MQTT_OWNER, topic_filter, self.on_mqtt)
                except ValueError as e:
                    logger.warning(f"Notifications: MQTT topic {topic_filter}: {e}")
            self._mqtt_filters = filters
        self.watcher.watch({r["when"]["entity_id"] for r in self._rules("ha")})

    def start(self) -> None:
        self.sync()
        self.watcher.start()

    def stop(self) -> None:
        self.watcher.stop()

    # --- Firing ---

    async def fire(self, rule: dict, fields: dict, origin: str) -> None:
        message = message_for_rule(rule, fields, origin)
        logger.info(f"Notification rule {rule.get('name') or rule['id']!r} ({origin})")
        await self.notifications.fire(rule, message)

    def fire_soon(self, rule: dict, fields: dict, origin: str) -> None:
        """Fire in the background (a webhook answers at once)."""
        task = asyncio.get_running_loop().create_task(self.fire(rule, fields, origin))
        self._running.add(task)
        task.add_done_callback(self._running.discard)

    async def wait_running(self) -> None:
        if self._running:
            await asyncio.gather(*list(self._running), return_exceptions=True)
