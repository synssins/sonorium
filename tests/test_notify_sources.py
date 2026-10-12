"""
Notification rules' own sources (sonorium/plugins/notify_sources.py): a Home
Assistant entity changing, a webhook URL per rule, an MQTT topic. Nothing
here talks to a real Home Assistant, broker or speaker.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from playback_helpers import addon_modules

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.plugins.notify_sources", "sonorium.plugins.notifications", "sonorium.plugins.mqtt",
                       "sonorium.plugins.store", "sonorium.plugins.host", "sonorium.plugins.events") as mods:
        m.__dict__.update(mods.__dict__)
        yield


def ns():
    return m.notify_sources


# --- A rule's "when" ------------------------------------------------------------------

def test_when_is_checked_and_cleaned():
    check = ns().check_when
    assert check(None) is None
    assert check({"type": "ha", "entity_id": " Binary_Sensor.Front_Door ", "state": " on "}) == \
        {"type": "ha", "entity_id": "binary_sensor.front_door", "state": "on"}
    assert check({"type": "ha", "entity_id": "event.doorbell"}) == {"type": "ha", "entity_id": "event.doorbell",
                                                                     "state": ""}
    assert check({"type": "webhook", "url": "http://x/api/notify/abc", "secret": "x"}) == {"type": "webhook"}
    assert check({"type": "mqtt", "topic": "home/+/doorbell", "payload": "ON"}) == \
        {"type": "mqtt", "topic": "home/+/doorbell", "payload": "ON"}
    for bad in ({"type": "nope"}, {"type": "ha", "entity_id": "no dot"}, {"type": "ha"},
                {"type": "ha", "entity_id": "a.b", "state": 5}, {"type": "mqtt", "topic": ""},
                {"type": "mqtt", "topic": "a/#/b"}, "ha"):
        with pytest.raises(ns().SourceError):
            check(bad)


def S(state, **attributes):
    return {"state": state, "attributes": attributes}


def test_ha_state_change_matching():
    match = ns().ha_change_matches
    door = {"type": "ha", "entity_id": "binary_sensor.door", "state": "on"}
    assert match(door, "binary_sensor.door", S("off"), S("on"))
    assert match(door, "binary_sensor.door", S("off"), S("ON"))
    assert not match(door, "binary_sensor.door", S("on"), S("off"))
    assert not match(door, "binary_sensor.door", S("on"), S("on", friendly_name="x"))  # attributes only
    assert not match(door, "binary_sensor.door", S("unavailable"), S("on"))  # back after a restart
    assert not match(door, "binary_sensor.door", None, S("on"))
    assert not match(door, "binary_sensor.other", S("off"), S("on"))
    assert match(door, "binary_sensor.door", S("unknown"), S("on"))
    any_change = {"type": "ha", "entity_id": "input_button.dinner", "state": ""}
    assert match(any_change, "input_button.dinner", S("2026-10-11T10:00:00"), S("2026-10-11T18:00:00"))
    assert not match(any_change, "input_button.dinner", S("2026-10-11T10:00:00"), S("unavailable"))
    # An event entity: its state is a time; the state asked for may be the event type
    ring = {"type": "ha", "entity_id": "event.doorbell", "state": "pressed"}
    assert match(ring, "event.doorbell", S("t1", event_type="pressed"), S("t2", event_type="pressed"))
    assert not match(ring, "event.doorbell", S("t1"), S("t2", event_type="released"))
    assert ns().ha_fields("event.doorbell", S("t2", event_type="pressed", friendly_name="Doorbell")) == \
        {"entity": "Doorbell", "state": "pressed", "entity_id": "event.doorbell"}


def test_mqtt_matching_and_fields():
    match = ns().mqtt_matches
    rule = {"type": "mqtt", "topic": "home/+/doorbell", "payload": "ON"}
    assert match(rule, "home/front/doorbell", " ON\n")
    assert not match(rule, "home/front/doorbell", "OFF")
    assert not match(rule, "home/front/bell", "ON")
    assert match({"type": "mqtt", "topic": "home/#", "payload": ""}, "home/a/b", "anything")
    assert ns().mqtt_fields("t", '{"title": "Door", "message": "Front"}')["title"] == "Door"
    assert ns().mqtt_fields("t", "ON") == {"topic": "t", "message": "ON", "state": "ON"}


def test_say_template():
    render = ns().render
    fields = {"title": "Doorbell", "message": "Front door", "entity": "Front Door", "state": "on"}
    assert render("{entity} is {state}", fields) == "Front Door is on"
    assert render("{title}: {body}", fields) == "Doorbell: Front door"
    assert render("{nothing} here", fields) == "here"
    assert render("bad {", fields) == "bad {"


def test_message_for_a_rule():
    message = ns().message_for_rule({"id": "r", "name": "Doorbell"}, {"message": "Front"}, "webhook")
    assert message["title"] == "Doorbell" and message["body"] == "Front" and message["origin"] == "webhook"
    message = ns().message_for_rule({"id": "r", "name": "Doorbell"}, {"title": "Pizza"}, "webhook")
    assert message["title"] == "Pizza"


# --- The sources with the rules -------------------------------------------------------

class FakeMqtt:
    def __init__(self):
        self.subs = []

    def subscribe(self, owner, topic_filter, handler):
        self.subs.append((owner, topic_filter, handler))

    def unsubscribe_owner(self, owner):
        self.subs = [s for s in self.subs if s[0] != owner]


class FakeHa:
    available = True

    def __init__(self, states=None):
        self.states = states or []

    def _config(self):
        return "http://supervisor/core/api", "tok"

    async def request(self, method, path, json=None):
        assert (method, path) == ("GET", "/api/states")
        return 200, self.states


def world(tmp_path, ha=None, ws=None):
    store = m.store.FrameworkStore(tmp_path)
    notifications = m.notifications.Notifications(store, lambda *a, **k: None)
    fired = []

    async def announcer(spec, message):
        fired.append((spec, message))
    notifications.announcer = announcer
    mqtt = FakeMqtt()
    sources = ns().BuiltinSources(notifications, store, mqtt, lambda: ha, ws_connect=ws)
    notifications.on_rules_changed = sources.sync
    return SimpleNamespace(store=store, n=notifications, sources=sources, mqtt=mqtt, fired=fired)


ANNOUNCE = {"channels": ["kitchen"], "text": "{entity} is {state}"}


def test_webhook_secret_per_rule(tmp_path):
    w = world(tmp_path)
    w.n.put_rule({"id": "bell", "name": "Doorbell", "when": {"type": "webhook"}, "announce": ANNOUNCE})
    secret = w.sources.webhook_secret("bell")
    assert secret and len(secret) >= 20
    assert w.sources.webhook_path("bell") == f"/api/notify/{secret}"
    # Kept in the owner-only secrets file, not with the rules
    assert secret in (tmp_path / "plugin_secrets.json").read_text()
    assert secret not in (tmp_path / "plugin_framework.json").read_text()
    assert w.sources.rule_for_secret(secret)["id"] == "bell"
    assert w.sources.rule_for_secret(secret[:-1] + ("A" if secret[-1] != "A" else "B")) is None
    assert w.sources.rule_for_secret("short") is None and w.sources.rule_for_secret(None) is None
    # Saving again keeps the URL; a new secret replaces it; another source drops it
    w.n.put_rule({"id": "bell", "name": "Doorbell", "when": {"type": "webhook"}, "announce": ANNOUNCE})
    assert w.sources.webhook_secret("bell") == secret
    new = w.sources.new_webhook_secret("bell")
    assert new != secret and w.sources.rule_for_secret(secret) is None
    w.n.put_rule({"id": "bell", "when": {"type": "mqtt", "topic": "a/b"}, "announce": ANNOUNCE})
    assert w.sources.webhook_secret("bell") is None and w.sources.rule_for_secret(new) is None


def test_mqtt_rule_subscribes_and_fires(tmp_path):
    w = world(tmp_path)
    w.n.put_rule({"id": "bell", "name": "Doorbell", "when": {"type": "mqtt", "topic": "home/doorbell",
                                                            "payload": "ON"}, "announce": ANNOUNCE})
    assert [s[1] for s in w.mqtt.subs] == ["home/doorbell"]
    handler = w.mqtt.subs[0][2]
    asyncio.run(handler("home/doorbell", "OFF"))
    assert w.fired == []
    asyncio.run(handler("home/doorbell", "ON"))
    spec, message = w.fired[0]
    assert spec["channels"] == ["kitchen"] and message["title"] == "Doorbell"
    assert message["fields"]["topic"] == "home/doorbell"
    # A disabled rule unsubscribes
    w.n.put_rule({"id": "bell", "when": {"type": "mqtt", "topic": "home/doorbell"}, "announce": ANNOUNCE,
                  "enabled": False})
    assert w.mqtt.subs == []


def test_ha_change_fires_the_rule(tmp_path):
    w = world(tmp_path)

    async def go():
        w.n.put_rule({"id": "door", "name": "Doorbell", "when": {"type": "ha", "entity_id": "binary_sensor.door",
                                                                "state": "on"}, "announce": ANNOUNCE})
        assert w.sources.watcher.entities == {"binary_sensor.door"}
        await w.sources.on_ha_change("binary_sensor.door", S("off"), S("on", friendly_name="Front Door"))
        await w.sources.on_ha_change("binary_sensor.door", S("on"), S("off"))
        w.sources.stop()
    asyncio.run(go())
    assert len(w.fired) == 1
    message = w.fired[0][1]
    assert message["fields"]["entity"] == "Front Door" and message["fields"]["state"] == "on"


def test_entity_picker_filters_and_searches(tmp_path):
    states = [
        {"entity_id": "binary_sensor.front_doorbell", "state": "off", "attributes": {"friendly_name": "Front Doorbell"}},
        {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen"}},
        {"entity_id": "event.doorbell", "state": "unknown",
         "attributes": {"friendly_name": "Doorbell ring", "event_types": ["pressed"]}},
        {"entity_id": "input_button.dinner", "state": "unknown", "attributes": {"friendly_name": "Dinner is ready"}},
    ]
    w = world(tmp_path, ha=FakeHa(states))
    every = asyncio.run(w.sources.ha_entities())
    assert [e["entity_id"] for e in every] == ["binary_sensor.front_doorbell", "event.doorbell", "input_button.dinner"]
    assert every[1]["event_types"] == ["pressed"]
    found = asyncio.run(w.sources.ha_entities("door"))
    assert [e["entity_id"] for e in found] == ["binary_sensor.front_doorbell", "event.doorbell"]
    assert asyncio.run(w.sources.ha_entities("dinner ready"))[0]["name"] == "Dinner is ready"


# --- The WebSocket to Home Assistant --------------------------------------------------------

class FakeWs:
    def __init__(self, script, refuse_trigger=False):
        self.sent = []
        self.inbox = asyncio.Queue()
        self.refuse_trigger = refuse_trigger
        self.script = script
        self.inbox.put_nowait({"type": "auth_required"})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, text):
        msg = json.loads(text)
        self.sent.append(msg)
        if msg["type"] == "auth":
            await self.inbox.put({"type": "auth_ok" if msg["access_token"] == "tok" else "auth_invalid"})
        elif msg["type"] == "subscribe_trigger":
            if self.refuse_trigger:
                await self.inbox.put({"id": msg["id"], "type": "result", "success": False,
                                      "error": {"code": "unauthorized", "message": "Unauthorized"}})
            else:
                await self.inbox.put({"id": msg["id"], "type": "result", "success": True})
                for event in self.script:
                    await self.inbox.put({"id": msg["id"], "type": "event", "event": event})
        elif msg["type"] == "subscribe_events":
            await self.inbox.put({"id": msg["id"], "type": "result", "success": True})
            for event in self.script:
                await self.inbox.put({"id": msg["id"], "type": "event", "event": event})

    async def recv(self):
        return json.dumps(await self.inbox.get())


@pytest.mark.parametrize("refuse_trigger", [False, True])
def test_watcher_subscribes_and_reports_changes(tmp_path, refuse_trigger):
    if refuse_trigger:  # not an admin token: every state change, filtered here
        script = [{"event_type": "state_changed", "data": {"entity_id": "light.x", "old_state": S("off"),
                                                           "new_state": S("on")}},
                  {"event_type": "state_changed", "data": {"entity_id": "binary_sensor.door", "old_state": S("off"),
                                                           "new_state": S("on")}}]
    else:
        script = [{"variables": {"trigger": {"platform": "state", "entity_id": "binary_sensor.door",
                                             "from_state": S("off"), "to_state": S("on")}}}]
    sockets = []

    def connect(url):
        assert url == "ws://supervisor/core/api/websocket"
        sockets.append(FakeWs(script, refuse_trigger))
        return sockets[-1]

    w = world(tmp_path, ha=FakeHa(), ws=connect)

    async def go():
        w.n.put_rule({"id": "door", "name": "Doorbell", "when": {"type": "ha", "entity_id": "binary_sensor.door",
                                                                "state": "on"}, "announce": ANNOUNCE})
        for _ in range(200):
            if w.fired:
                break
            await asyncio.sleep(0.01)
        w.sources.stop()
    asyncio.run(go())
    assert len(w.fired) == 1
    sent = sockets[0].sent
    assert sent[1]["type"] == "subscribe_trigger"
    assert sent[1]["trigger"] == {"platform": "state", "entity_id": ["binary_sensor.door"]}
    assert w.sources.watcher.mode == ("events" if refuse_trigger else "trigger")


# --- Rule fires -> the announcement (host._announce_notification -> core/announce.py) ----------

class FakeOverlay:
    def __init__(self):
        self.played = []

    async def play(self, channels, source, **kw):
        self.played.append((channels, source, kw))
        return {"id": len(self.played)}


def test_announcement_speaks_the_template_over_a_ducked_mix(tmp_path):
    host = m.host.PluginHost(tmp_path, bus=m.events.EventBus())
    host.overlay = FakeOverlay()
    said = []

    async def speak(message, **options):
        said.append(message)
        return b"ID3audio"
    host.tts.register("t", "piper", "Piper", speak)
    rule = {"id": "door", "name": "Doorbell", "announce": {"channels": ["kitchen"], "sound": "https://x/chime.mp3",
                                                          "text": "{entity} is {state}", "volume": 80, "duck": True}}
    message = ns().message_for_rule(rule, {"entity": "Front Door", "state": "on"}, "homeassistant")
    result = asyncio.run(host.notifications.fire(rule, message))
    assert result["announced"] and not result["error"]
    assert said == ["Front Door is on"]
    (c1, chime, kw1), (c2, speech, kw2) = host.overlay.played
    assert chime == "https://x/chime.mp3" and speech == b"ID3audio"
    assert kw1["duck"] is True and kw1["volume"] == 80 and kw1["override"] is True


def test_without_text_to_speech_only_the_chime_plays(tmp_path):
    host = m.host.PluginHost(tmp_path, bus=m.events.EventBus())
    host.overlay = FakeOverlay()

    async def speak(message, **options):
        raise AssertionError("Home Assistant isn't linked: nothing may be spoken")
    host.tts.register("announcements", "homeassistant", "Home Assistant", speak)  # but no Home Assistant
    assert host.tts_ready() is False
    rule = {"id": "r", "name": "Doorbell", "announce": {"channels": ["k"], "sound": "https://x/c.mp3", "text": "Hi"}}
    result = asyncio.run(host.notifications.fire(rule, ns().message_for_rule(rule, {}, "webhook")))
    assert result["announced"] and [p[1] for p in host.overlay.played] == ["https://x/c.mp3"]
    rule["announce"]["sound"] = None
    result = asyncio.run(host.notifications.fire(rule, ns().message_for_rule(rule, {}, "webhook")))
    assert not result["announced"] and "Home Assistant" in result["error"]


# --- Announcements off on a channel (a nap) ------------------------------------------------

class SessionsWith:
    def __init__(self, **announcements):
        self.s = {k: SimpleNamespace(id=k, announcements=v) for k, v in announcements.items()}

    def get(self, sid):
        return self.s.get(sid)


def test_rule_skips_channels_with_announcements_off(tmp_path):
    host = m.host.PluginHost(tmp_path, bus=m.events.EventBus())
    host.overlay = FakeOverlay()
    host.overlay.sessions = SessionsWith(kitchen=True, kids=False)
    said = []

    async def speak(message, **options):
        said.append(message)
        return b"ID3audio"
    host.tts.register("t", "piper", "Piper", speak)
    rule = {"id": "r", "name": "Dinner", "announce": {"channels": ["kitchen", "kids"], "text": "Dinner"}}
    result = asyncio.run(host.notifications.fire(rule, ns().message_for_rule(rule, {}, "webhook")))
    assert result["announced"] is True and result["skipped"] == ["kids"]
    assert [p[0] for p in host.overlay.played] == [["kitchen"]]
    assert host.overlay.played[0][2]["announcement"] is True
    # Every channel off: nothing plays and nothing is spoken
    rule["announce"]["channels"] = ["kids"]
    result = asyncio.run(host.notifications.fire(rule, ns().message_for_rule(rule, {}, "webhook")))
    assert result["announced"] is False and result["skipped"] == ["kids"] and said == ["Dinner"]


def test_channel_announcements_setting_is_saved():
    with addon_modules("sonorium.core.state") as mods:
        Session = mods.state.Session
        s = Session(id="k", name="Kids")
        assert s.announcements is True
        s.announcements = False
        assert Session.from_dict(s.to_dict()).announcements is False
        assert Session.from_dict({"id": "o", "name": "Old"}).announcements is True  # state files from before
