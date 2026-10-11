"""
Plugin framework end to end: the example plugin in docs/examples (a webhook
trigger plays a channel), trigger tokens and limits, the actions API,
notifications, extension points (pages, connections with secrets), the
schedule endpoints and the event stream.
"""

import json
import logging
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient

from playback_helpers import ROOT, addon_modules  # noqa: E402
import test_sequences_playlists as runner_tests  # noqa: E402

m = SimpleNamespace()
EXAMPLE = ROOT / "docs" / "examples" / "trigger_play_channel"


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.session_manager", "sonorium.core.state", "sonorium.core.playback_runner",
                       "sonorium.core.sequences", "sonorium.core.playlists", "sonorium.plugins.events",
                       "sonorium.plugins.host", "sonorium.plugins.manager", "sonorium.plugins.loader",
                       "sonorium.plugins.actions", "sonorium.plugins.tokens", "sonorium.plugins.extensions",
                       "sonorium.plugins.scheduler", "sonorium.web.plugin_framework_api") as mods:
        m.__dict__.update(mods.__dict__)
        runner_tests.m.__dict__.update(mods.__dict__)
        yield


HELPER = '''
from sonorium.plugins.base import BasePlugin


class Helper(BasePlugin):
    id = "helper"
    name = "Helper"
    version = "1.0.0"
    delivered, page_calls, tested, changed, scheduled = [], [], [], [], []

    def get_notifiers(self):
        return [{"id": "inbox", "title": "Inbox"}, {"id": "broken", "title": "Broken"}]

    async def deliver_notification(self, notifier_id, notification):
        if notifier_id == "broken":
            raise RuntimeError("offline")
        Helper.delivered.append(notification)

    def get_pages(self):
        return [{"id": "main", "title": "Main", "icon": "alarm"}, {"id": "bad id!"},
                {"id": "svg", "title": "Svg", "icon": "<svg onload='steal()'></svg>"}]

    async def get_page(self, page_id):
        return {"title": "Main", "sections": [{"title": "Alarm", "items": [
            {"type": "field", "name": "time", "field": {"type": "time", "label": "Time"}, "value": "07:30"},
            {"type": "field", "name": "days", "field": {"type": "multiselect", "options": ["mon", "tue"]}},
            {"type": "html", "html": "<b>not allowed</b>"},
            {"type": "table", "id": "rows", "columns": [{"key": "name", "label": "Name"}],
             "rows": [{"id": "r1", "name": "One", "hidden": "x"}],
             "row_actions": [{"id": "remove", "label": "Remove", "style": "danger"}]},
            {"type": "button", "action": "save", "label": "Save", "style": "primary"},
        ]}]}

    async def page_action(self, page_id, action, data):
        Helper.page_calls.append((page_id, action, data))
        return {"success": True, "message": "Saved"}

    def get_connections(self):
        return [{"id": "hub", "title": "Hub", "test": True, "fields": {
            "host": {"type": "string", "label": "Host", "required": True},
            "port": {"type": "number", "label": "Port", "default": 8080, "min": 1, "max": 65535},
            "password": {"type": "secret", "label": "Password"}}}]

    async def test_connection(self, connection_id, values):
        Helper.tested.append(values)
        return {"success": values.get("password") == "pw", "message": "checked"}

    def connection_status(self, connection_id):
        return {"state": "connected", "message": "Hub 2"}

    async def on_connection_changed(self, connection_id, values):
        Helper.changed.append(values)

    def get_triggers(self):
        return [{"id": "boom", "title": "Boom"}]

    async def on_trigger(self, trigger, payload, context):
        raise RuntimeError("handler broke")

    async def on_schedule(self, job):
        Helper.scheduled.append(job["id"])
'''


@pytest.fixture
def w(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    bus = m.events.EventBus()
    old_bus = m.events.set_bus(bus)
    t = runner_tests.build(tmp_path)
    host = m.host.PluginHost(tmp_path / "config", bus=bus, session_manager=t.manager, state_store=t.store)
    plugins_dir = tmp_path / "plugins"
    shutil.copytree(EXAMPLE, plugins_dir / "trigger_play_channel")
    (plugins_dir / "helper").mkdir()
    (plugins_dir / "helper" / "plugin.py").write_text(HELPER)
    m.loader.BUILTIN_PLUGINS_DIR = None
    manager = m.manager.PluginManager(t.store, plugins_dir=plugins_dir, audio_path=tmp_path / "audio", host=host)
    app = FastAPI()
    app.include_router(m.plugin_framework_api.create_plugin_framework_router(manager, host))
    with TestClient(app) as client:
        call = client.portal.call
        call(manager.initialize)
        call(host.start)  # as at Sonorium's start-up: the loop, notification routing, the scheduler
        for plugin_id in ("trigger_play_channel", "helper"):
            assert call(manager.enable_plugin, plugin_id)
        helper = manager.get_plugin("helper")
        for name in ("delivered", "page_calls", "tested", "changed", "scheduled"):
            getattr(type(helper), name).clear()
        yield SimpleNamespace(t=t, bus=bus, host=host, manager=manager, client=client, call=call, helper=helper,
                              config=tmp_path / "config")
        call(host.stop)
    m.events.set_bus(old_bus)


def wait_for(condition, seconds=3.0):
    give_up = time.monotonic() + seconds
    while time.monotonic() < give_up:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def types(w, pattern="*"):
    return [e.type for e in w.bus.history(0, [pattern])]


def plugin_token(w, plugin_id="trigger_play_channel", trigger=None):
    body = {} if trigger is None else {"trigger": trigger}
    r = w.client.post(f"/api/plugins/{plugin_id}/triggers/token", json=body)
    assert r.status_code == 200, r.text
    return r.json()["token"]


# --- The example plugin, end to end ----------------------------------------------

def test_example_trigger_plays_and_stops_a_channel(w):
    c, session = w.client, w.t.session
    token = plugin_token(w)
    r = c.post("/api/triggers/trigger_play_channel/play", json={"channel": session.name, "volume": 25},
               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 202 and r.json()["accepted"] is True
    assert wait_for(lambda: session.is_playing)
    assert session.volume == 25
    assert wait_for(lambda: "plugin.trigger_play_channel.played" in types(w))

    received = w.bus.history(0, ["trigger.received"])[-1]
    assert received.data == {"plugin_id": "trigger_play_channel", "trigger": "play", "method": "POST",
                             "payload": {"channel": session.name, "volume": 25}}
    started = w.bus.history(0, ["channel.started"])[-1]
    assert started.origin == "plugin:trigger_play_channel"

    # A device that can only call a URL: GET with the token in the query
    r = c.get(f"/api/triggers/trigger_play_channel/stop?channel={session.id}&token={token}")
    assert r.status_code == 202
    assert wait_for(lambda: not session.is_playing)

    page = c.get("/api/plugins/trigger_play_channel/pages/calls").json()
    rows = page["sections"][0]["items"][1]["rows"]
    assert [row["trigger"] for row in rows] == ["stop", "play"]
    r = c.post("/api/plugins/trigger_play_channel/pages/calls/actions/clear", json={})
    assert r.json() == {"success": True, "message": "Cleared", "refresh": True}


def test_example_trigger_for_an_unknown_channel_publishes_an_error(w):
    token = plugin_token(w)
    r = w.client.post("/api/triggers/trigger_play_channel/play", json={"channel": "nope"},
                      headers={"X-Sonorium-Token": token})
    assert r.status_code == 202
    assert wait_for(lambda: types(w, "error"))
    error = w.bus.history(0, ["error"])[-1].data
    assert error["where"] == "trigger" and "No channel 'nope'" in error["message"]


# --- Trigger tokens and limits --------------------------------------------------------

def test_tokens_are_stored_hashed_and_rotate(w):
    token = plugin_token(w)
    secrets_text = (w.config / "plugin_secrets.json").read_text()
    assert token not in secrets_text and "plugin:trigger_play_channel" in secrets_text
    url = "/api/triggers/trigger_play_channel/stop"
    assert w.client.post(url, headers={"Authorization": f"Bearer {token}"}).status_code == 202
    new = plugin_token(w)
    assert w.client.post(url, headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert w.client.post(url, headers={"Authorization": f"Bearer {new}"}).status_code == 202
    assert w.client.delete("/api/plugins/trigger_play_channel/triggers/token").json() == {"revoked": True}
    assert w.client.post(url, headers={"Authorization": f"Bearer {new}"}).status_code == 401


def test_trigger_token_only_opens_its_trigger(w):
    token = plugin_token(w, trigger="stop")
    headers = {"Authorization": f"Bearer {token}"}
    assert w.client.post("/api/triggers/trigger_play_channel/stop", headers=headers).status_code == 202
    assert w.client.post("/api/triggers/trigger_play_channel/play", headers=headers).status_code == 401
    listed = w.client.get("/api/plugins/trigger_play_channel/triggers").json()
    by_id = {t["id"]: t for t in listed["triggers"]}
    assert listed["plugin_token"] is None and by_id["stop"]["token"]["created"]
    assert by_id["play"]["url"] == "/api/triggers/trigger_play_channel/play"
    assert "hash" not in json.dumps(listed)
    assert w.client.post("/api/plugins/trigger_play_channel/triggers/token",
                         json={"trigger": "nope"}).status_code == 404


def test_trigger_refusals(w):
    c = w.client
    assert c.post("/api/triggers/trigger_play_channel/play").status_code == 401
    assert c.post("/api/triggers/trigger_play_channel/play", headers={"Authorization": "Bearer x"}).status_code == 401
    # An unknown plugin looks the same as a wrong token: nothing to enumerate
    assert c.post("/api/triggers/nobody/play", headers={"Authorization": "Bearer x"}).status_code == 401
    assert c.post("/api/triggers/bad%20id/play").status_code == 400
    helper_token = plugin_token(w, "helper")
    headers = {"Authorization": f"Bearer {helper_token}"}
    assert c.get("/api/triggers/helper/boom", headers=headers).status_code == 405  # POST only
    assert c.post("/api/triggers/helper/nothing", headers=headers).status_code == 404
    assert c.post("/api/triggers/helper/boom", content=b"[1]", headers=headers).status_code == 400
    assert c.post("/api/triggers/helper/boom", content=b"x" * 20000, headers=headers).status_code == 413
    w.call(w.manager.disable_plugin, "helper")
    assert c.post("/api/triggers/helper/boom", headers=headers).status_code == 409


def test_failed_logins_are_limited_per_client(w):
    c = w.client
    codes = [c.post("/api/triggers/trigger_play_channel/play", headers={"Authorization": "Bearer wrong"}).status_code
             for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
    r = c.post("/api/triggers/trigger_play_channel/play", headers={"Authorization": f"Bearer {plugin_token(w)}"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_calls_are_limited_per_trigger(w):
    headers = {"Authorization": f"Bearer {plugin_token(w)}"}
    codes = [w.client.post("/api/triggers/trigger_play_channel/stop", headers=headers).status_code
             for _ in range(12)]
    assert codes.count(202) == 10 and codes[-1] == 429


def test_failing_trigger_handler_publishes_an_error(w):
    headers = {"Authorization": f"Bearer {plugin_token(w, 'helper')}"}
    assert w.client.post("/api/triggers/helper/boom", json={"x": 1}, headers=headers).status_code == 202
    assert wait_for(lambda: types(w, "error"))
    assert w.bus.history(0, ["error"])[-1].data["message"] == "handler broke"


def test_tokens_are_masked_in_access_logs():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4", "GET", "/api/triggers/p/t?a=1&token=SECRETVALUE&b=2", "1.1", 202), None)
    m.tokens.RedactTokens().filter(record)
    assert "SECRETVALUE" not in record.getMessage() and "token=***&b=2" in record.getMessage()


# --- Actions API -----------------------------------------------------------------------

def test_actions_facade(w):
    actions = w.host.actions_for("helper")
    session = w.t.session
    call = w.call
    channels = call(actions.list_channels)
    assert channels[0]["id"] == session.id and channels[0]["theme_id"] == "storm"
    assert {p["id"] for p in call(actions.list_presets, "storm")} == {"a", "b", "c"}
    assert {s["id"] for s in call(actions.list_sequences, "storm")} >= {"up_down"}
    channel = call(actions.set_preset, session.id, "b")
    assert channel["preset_id"] == "b"
    assert w.bus.history(0, ["channel.changed"])[-1].origin == "plugin:helper"
    assert call(actions.mute_speakers, session.id, True)["speakers_muted"] is True
    with pytest.raises(m.actions.ActionError) as e:
        call(actions.set_preset, session.id, "nope")
    assert e.value.code == "not_found"
    with pytest.raises(m.actions.ActionError) as e:
        call(actions.next_step, session.id)
    assert e.value.code == "conflict"
    with pytest.raises(m.actions.ActionError) as e:
        call(actions.set_volume, session.id, 101)
    assert e.value.code == "invalid"
    with pytest.raises(m.actions.ActionError):
        call(actions.get_channel, "missing")
    created = call(actions.create_channel, "tavern", ["media_player.x"], None, "Kitchen")
    assert created["name"] == "Kitchen" and created["theme_id"] == "tavern"
    call(actions.start_channel, created["id"])
    assert call(actions.get_channel, created["id"])["is_playing"] is True


def test_plugin_without_a_host_says_so(w):
    plugin = type(w.helper)(plugin_dir=Path("."), settings={})
    with pytest.raises(m.host.PluginFrameworkUnavailable):
        plugin.actions


# --- Scheduler through plugins and the API ------------------------------------------------

def test_schedules_api_and_plugin_helper(w):
    c = w.client
    r = c.post("/api/plugins/helper/schedules", json={"id": "wake", "kind": "daily", "spec": {"time": "07:30"},
                                                      "name": "Wake up", "data": {"channel": "c"}})
    assert r.status_code == 201 and r.json()["next_run"]
    assert c.post("/api/plugins/helper/schedules", json={"id": "x", "kind": "hourly"}).status_code == 400
    w.helper.schedule("tick", "interval", {"seconds": 60})
    assert [j["id"] for j in c.get("/api/plugins/helper/schedules").json()["jobs"]] == ["tick", "wake"]
    assert len(c.get("/api/schedules").json()["jobs"]) == 2
    assert c.put("/api/plugins/helper/schedules/wake/enabled", json={"enabled": False}).json()["next_run"] is None
    assert c.delete("/api/plugins/helper/schedules/wake").json() == {"removed": True}
    assert c.delete("/api/plugins/helper/schedules/wake").status_code == 404
    assert c.put("/api/schedules/settings", json={"timezone": "Nowhere/Land"}).status_code == 400
    w.call(w.host._run_job, {"id": "tick", "plugin_id": "helper"})
    assert w.helper.scheduled == ["tick"]


def test_uninstall_removes_jobs_tokens_and_connection_values(w):
    w.helper.schedule("tick", "interval", {"seconds": 60})
    plugin_token(w, "helper")
    w.client.put("/api/plugins/helper/connections/hub", json={"values": {"host": "hub", "password": "pw"}})
    w.manager.plugin_removed("helper")
    assert w.host.scheduler.list("helper") == []
    assert w.host.tokens.info("plugin:helper") is None
    assert "pw" not in (w.config / "plugin_secrets.json").read_text()


# --- Notifications -------------------------------------------------------------------------

def test_notification_rules_route_events_to_notifiers(w):
    c = w.client
    overview = c.get("/api/notifications").json()
    assert {n["id"] for n in overview["notifiers"]} == {"helper.inbox", "helper.broken"}
    r = c.put("/api/notifications/rules", json={"rules": [
        {"id": "starts", "events": ["channel.started"], "notifiers": ["helper.inbox"]},
        {"id": "problems", "events": ["error"], "notifiers": ["helper.inbox", "helper.broken"], "min_level": "error"},
    ]})
    assert r.status_code == 200, r.text
    w.bus.publish("channel.started", {"channel_id": "c1", "name": "Kitchen"})
    assert wait_for(lambda: w.helper.delivered)
    message = w.helper.delivered[0]
    assert message["title"] == "Kitchen started" and message["event"]["type"] == "channel.started"

    w.bus.publish("error", {"where": "test", "message": "it broke"})
    assert wait_for(lambda: len(w.helper.delivered) == 2)
    # The broken notifier's failure is an error event that is not routed again
    assert wait_for(lambda: any(e.data.get("where") == "notifier" for e in w.bus.history(0, ["error"])))
    time.sleep(0.1)
    assert len(w.helper.delivered) == 2


def test_plugin_notify_and_test_endpoint(w):
    w.client.put("/api/notifications/rules/msgs", json={"events": ["notification"], "notifiers": ["helper.inbox"],
                                                       "min_level": "warning"})
    w.helper.notify("Just info")  # below the rule's level
    w.helper.notify("Door open", "Back door", level="warning")
    assert wait_for(lambda: w.helper.delivered)
    time.sleep(0.05)
    assert [d["title"] for d in w.helper.delivered] == ["Door open"]
    assert w.helper.delivered[0]["origin"] == "plugin:helper"
    r = w.client.post("/api/notifications/test", json={"notifier": "helper.broken"})
    assert r.json() == {"success": False, "message": "offline"}
    assert w.client.post("/api/notifications/test", json={"notifier": "x.y"}).status_code == 404


def test_rules_are_checked(w):
    c = w.client
    assert c.put("/api/notifications/rules", json={"rules": [{"events": [], "notifiers": ["a.b"]}]}).status_code == 400
    assert c.put("/api/notifications/rules/x", json={"events": ["bad pattern!"], "notifiers": ["a.b"]}).status_code == 400
    assert c.put("/api/notifications/rules/x", json={"events": ["*"], "notifiers": ["nodot"]}).status_code == 400
    assert c.delete("/api/notifications/rules/none").status_code == 404


def test_disabling_a_plugin_removes_its_notifiers_and_subscriptions(w):
    owners = {s.owner for s in w.bus.subscriptions()}
    assert "trigger_play_channel" in owners
    w.call(w.manager.disable_plugin, "trigger_play_channel")
    w.call(w.manager.disable_plugin, "helper")
    assert "trigger_play_channel" not in {s.owner for s in w.bus.subscriptions()}
    assert w.client.get("/api/notifications").json()["notifiers"] == []
    assert types(w, "plugin.*")[-2:] == ["plugin.disabled", "plugin.disabled"]


# --- Extension points ---------------------------------------------------------------------

def test_extensions_list_pages_and_connections(w):
    ext = w.client.get("/api/extensions").json()
    pages = {(p["plugin_id"], p["id"]): p for p in ext["pages"]}
    assert set(pages) == {("trigger_play_channel", "calls"), ("helper", "main"), ("helper", "svg")}
    assert pages[("helper", "main")]["icon"] == {"type": "name", "value": "alarm"}
    assert pages[("helper", "svg")]["icon"] is None  # an SVG with an event attribute is refused
    hub = ext["connections"][0]
    assert hub["id"] == "hub" and hub["status"] == {"state": "connected", "message": "Hub 2"}
    assert hub["fields"]["password"]["type"] == "secret" and hub["values"] == {"port": 8080, "password_set": False}


def test_page_schema_is_normalized(w):
    page = w.client.get("/api/plugins/helper/pages/main").json()
    items = page["sections"][0]["items"]
    assert [i["type"] for i in items] == ["field", "field", "table", "button"]  # no HTML item
    assert items[2]["rows"] == [{"id": "r1", "name": "One"}]  # only the declared columns
    assert w.client.get("/api/plugins/helper/pages/nope").status_code == 404


def test_page_actions_are_checked(w):
    c = w.client
    r = c.post("/api/plugins/helper/pages/main/actions/save", json={"values": {"time": "06:45", "days": ["mon"]}})
    assert r.json() == {"success": True, "message": "Saved", "refresh": True}
    assert w.helper.page_calls[-1] == ("main", "save", {"values": {"time": "06:45", "days": ["mon"]}, "row": None})
    r = c.post("/api/plugins/helper/pages/main/actions/remove", json={"row": "r1"})
    assert r.status_code == 200 and w.helper.page_calls[-1][2]["row"] == "r1"
    assert c.post("/api/plugins/helper/pages/main/actions/explode", json={}).status_code == 404
    assert c.post("/api/plugins/helper/pages/main/actions/save", json={"values": {"time": "25:00"}}).status_code == 400
    assert c.post("/api/plugins/helper/pages/main/actions/save", json={"values": {"evil": 1}}).status_code == 400
    assert c.post("/api/plugins/helper/pages/main/actions/save",
                  json={"values": {"days": ["sun"]}}).status_code == 400


def test_connection_secrets_are_write_only(w):
    c = w.client
    r = c.put("/api/plugins/helper/connections/hub", json={"values": {"host": "hub.local", "password": "pw"}})
    assert r.status_code == 200
    assert r.json()["values"] == {"host": "hub.local", "port": 8080, "password_set": True}
    assert "pw" not in json.dumps(c.get("/api/plugins/helper/connections/hub").json())
    assert '"pw"' not in (w.config / "plugin_framework.json").read_text()
    assert '"pw"' in (w.config / "plugin_secrets.json").read_text()
    assert w.helper.changed[-1]["password"] == "pw"

    # An empty secret keeps the saved one; the plugin reads it
    c.put("/api/plugins/helper/connections/hub", json={"values": {"port": 9000, "password": ""}})
    assert w.helper.connection_values("hub") == {"host": "hub.local", "port": 9000, "password": "pw"}

    # The test uses saved values with the form's on top
    assert c.post("/api/plugins/helper/connections/hub/test", json={"values": {"host": "other"}}).json() == \
        {"success": True, "message": "checked"}
    assert w.helper.tested[-1] == {"host": "other", "port": 9000, "password": "pw"}

    r = c.put("/api/plugins/helper/connections/hub", json={"clear": ["password"]})
    assert r.json()["values"]["password_set"] is False
    assert c.put("/api/plugins/helper/connections/hub", json={"values": {"port": 0}}).status_code == 400
    assert c.put("/api/plugins/helper/connections/hub", json={"values": {"nope": 1}}).status_code == 400
    assert c.get("/api/plugins/helper/connections/none").status_code == 404


# --- Events over HTTP -----------------------------------------------------------------------

def test_event_catalogue_endpoint(w):
    body = w.client.get("/api/events/catalogue").json()
    assert body["version"] == 1 and "channel.started" in body["events"]
    assert body["events"]["trigger.received"]["fields"]["trigger"]


def test_event_stream_needs_a_token_and_replays(w):
    c = w.client
    assert c.get("/api/events/stream").status_code == 401
    token = c.post("/api/events/token").json()["token"]
    assert c.get("/api/events/token").json()["set"] is True
    first = w.bus.publish("theme.changed", {"theme_id": "a", "folder": "a"})
    w.bus.publish("channel.started", {"channel_id": "x", "name": "X"})
    w.bus.publish("theme.deleted", {"theme_id": "a"})
    with c.stream("GET", f"/api/events/stream?types=theme.*&max_events=2&last_event_id={first.id - 1}",
                  headers={"Authorization": f"Bearer {token}"}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        text = "".join(r.iter_text())
    frames = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    assert [f["type"] for f in frames] == ["theme.changed", "theme.deleted"]
    assert c.get("/api/events/stream?types=bad!", headers={"Authorization": f"Bearer {token}"}).status_code == 400

    # G4: ids are "<boot_id>:<n>"; the same form replays, an id from an earlier run replays all of this one
    boot = frames[0]["boot_id"]
    ids = [line[4:] for line in text.splitlines() if line.startswith("id: ")]
    assert ids == [f"{boot}:{f['id']}" for f in frames]

    def replay(last):
        with c.stream("GET", "/api/events/stream?types=theme.*&max_events=1",
                      headers={"Authorization": f"Bearer {token}", "Last-Event-ID": last}) as r:
            return [json.loads(line[6:]) for line in "".join(r.iter_text()).splitlines() if line.startswith("data: ")]
    assert [f["type"] for f in replay(f"{boot}:{first.id}")] == ["theme.deleted"]
    assert replay(f"0123abcd:{first.id + 50}")[0]["id"] == first.id  # another run: from the start of this one
    c.delete("/api/events/token")
    assert c.get("/api/events/stream", headers={"Authorization": f"Bearer {token}"}).status_code == 401
