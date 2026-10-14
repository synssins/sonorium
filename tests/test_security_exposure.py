"""
Exposure fixes: request body limits (413), trusted reverse proxies
(X-Forwarded-For / -Proto), a per-username sign-in limit, who may play a
stream while Require login is on (stream links, the LAN, signed in) and how
many, event streams per caller and what they see, the public address in
/api/info and Link App codes, the ports' settings, reserved sign-in ids,
first-run setup from outside the LAN, and the backups' total and free-disk
limits. Sign-in is the real one (tests/test_auth.py's World).
"""
import asyncio
import io
import json
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("argon2")
pytest.importorskip("cryptography")
pytest.importorskip("segno")

import test_auth as ta  # noqa: E402  (its World; its tests stay in its own module)
from playback_helpers import addon_modules  # noqa: E402

m = ta.m
LOCAL = ("127.0.0.1", 50000)
OUTSIDE = ("93.184.216.34", 50000)  # a public address (203.0.113.x counts as private to ipaddress)


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.auth.service", "sonorium.auth.middleware", "sonorium.auth.routes",
                       "sonorium.auth.permissions", "sonorium.auth.config", "sonorium.auth.qr", "sonorium.auth.announce",
                       "sonorium.auth.tokens", "sonorium.auth.keys", "sonorium.auth.users",
                       "sonorium.auth.accounts", "sonorium.auth.passwords", "sonorium.plugins.loader",
                       "sonorium.runtime", "sonorium.auth.proxies", "sonorium.auth.stream_links",
                       "sonorium.limits", "sonorium.playback_values", "sonorium.core.backups",
                       "sonorium.web.plugin_framework_api", "sonorium.auth.principal") as mods:
        m.__dict__.update(mods.__dict__)
        yield


@pytest.fixture
def values():
    """Settings > Advanced values for the test (none: the defaults)."""
    stored = {}
    m.playback_values.set_source(lambda: stored)
    yield stored
    m.playback_values.set_source(None)


class World(ta.World):
    """test_auth's World with the app's outer middlewares (body limits, trusted proxies) and stream links."""

    def __init__(self, tmp_path, monkeypatch, public_url=None, **kw):
        if public_url is not None:
            real = m.routes.create_core_auth_router
            monkeypatch.setattr(m.routes, "create_core_auth_router",
                                lambda s, **k: real(s, public_url=lambda: public_url, https_listen_port=lambda: 8808, **k))
        monkeypatch.delenv(m.proxies.ENV, raising=False)
        monkeypatch.delenv("SONORIUM_SETUP_TOKEN", raising=False)
        super().__init__(tmp_path, monkeypatch, **kw)
        self.app.add_middleware(m.limits.BodyLimitMiddleware)
        self.app.add_middleware(m.proxies.ForwardedClient)
        m.stream_links.bind(self.service, clock=self.clock)


@pytest.fixture
def w(tmp_path, monkeypatch, values):
    return World(tmp_path, monkeypatch)


# --- 2. Request bodies ---------------------------------------------------------------

def test_sign_in_bodies_are_capped_before_anyone_signs_in(w):
    c = w.client()
    big = {"username": "chris", "password": "x" * (20 * 1024)}
    assert c.post("/api/auth/login", json=big).status_code == 413
    assert c.post("/api/pair", json={"code": "x" * (20 * 1024)}).status_code == 413
    assert c.post("/api/auth/setup", json=big).status_code == 413

    def chunks():  # no Content-Length: counted as it arrives
        for _ in range(40):
            yield b" " * 1024
    r = c.post("/api/auth/login", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert c.post("/api/auth/login", json={"username": "chris", "password": "nope"}).status_code == 401


def test_auth_json_reader_caps_without_the_middleware(tmp_path, monkeypatch, values):
    w = ta.World(tmp_path, monkeypatch)  # sign-in routes alone: their own reader is capped too
    r = w.client().post("/api/auth/login", json={"username": "u", "password": "x" * (20 * 1024)})
    assert r.status_code == 413


def test_other_bodies_have_the_request_size_and_it_is_adjustable(w, values):
    c, csrf = w.setup_admin()
    h = ta.H(None, csrf)
    small = c.post("/api/sessions", content=b"{}", headers={**h, "Content-Type": "application/json"})
    assert small.status_code == 200
    big = b'{"x": "' + b"a" * (1100 * 1024) + b'"}'
    assert c.post("/api/sessions", content=big, headers={**h, "Content-Type": "application/json"}).status_code == 413
    values["request_body_limit_kb"] = 4096
    assert c.post("/api/sessions", content=big, headers={**h, "Content-Type": "application/json"}).status_code == 200
    values["request_body_limit_kb"] = 0  # no limit
    assert c.post("/api/sessions", content=big, headers={**h, "Content-Type": "application/json"}).status_code == 200


def test_which_limit_a_path_has():
    rule = m.limits.rule_for
    assert rule("/api/auth/login") == ("auth_body_limit_kb", False)
    assert rule("/api/pair") == ("auth_body_limit_kb", False)
    assert rule("/api/themes/t1/upload") == ("upload_limit_mb", True)
    assert rule("/api/themes/import") == ("upload_limit_mb", True)
    assert rule("/api/plugins/upload") == ("upload_limit_mb", True)
    assert rule("/api/me/backups/d/themes/k/files/a.wav") == ("upload_limit_mb", True)
    assert rule("/api/sessions") == ("request_body_limit_kb", False)
    assert rule("/api/plugins/x/http/y") == ("request_body_limit_kb", False)


def test_streamed_uploads_are_counted_and_refused_past_the_limit(values):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    values["upload_limit_mb"] = 0.01  # about 10 KB
    app = FastAPI()

    @app.post("/api/themes/import")
    async def upload(request: Request):
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
        return {"total": total}
    app.add_middleware(m.limits.BodyLimitMiddleware)
    c = TestClient(app)
    assert c.post("/api/themes/import", content=b"x" * 5000).json() == {"total": 5000}
    assert c.post("/api/themes/import", content=b"x" * 50000).status_code == 413  # by Content-Length

    def chunks():
        for _ in range(20):
            yield b"x" * 1024
    assert c.post("/api/themes/import", content=chunks()).status_code == 413  # counted


# --- 4. Trusted proxies, per-username sign-in limit --------------------------------------

def test_forwarded_client_only_from_a_trusted_proxy():
    nets = m.proxies.parse("10.0.0.0/8, 172.18.0.1")
    fc = m.proxies.forwarded_client
    assert fc("172.18.0.1", "203.0.113.9", nets) == "203.0.113.9"
    assert fc("172.18.0.1", "198.51.100.1, 203.0.113.9, 10.1.2.3", nets) == "203.0.113.9"  # last untrusted hop
    assert fc("192.168.1.9", "203.0.113.9", nets) == "192.168.1.9"  # not a trusted proxy: its own address
    assert fc("172.18.0.1", "", nets) == "172.18.0.1"
    assert fc("172.18.0.1", "garbage", nets) == "172.18.0.1"
    assert fc("172.18.0.1", "203.0.113.9", ()) == "172.18.0.1"  # none trusted (the default)
    with pytest.raises(ValueError):
        m.proxies.parse("10.0.0.0/8 nonsense")


def test_behind_a_trusted_proxy_rate_limits_see_each_caller(w, monkeypatch):
    w.setup_admin()
    c = w.client(client=("172.18.0.1", 40000))
    # Not trusted: everyone is the proxy, and one caller's failures block the next
    for _ in range(5):
        c.post("/api/auth/login", json={"username": "x1", "password": "nope"}, headers={"X-Forwarded-For": "203.0.113.1"})
    r = c.post("/api/auth/login", json={"username": "x2", "password": "nope"}, headers={"X-Forwarded-For": "203.0.113.2"})
    assert r.status_code == 429
    # Trusted: each caller has its own limit
    monkeypatch.setenv(m.proxies.ENV, "172.18.0.0/16")
    r = c.post("/api/auth/login", json={"username": "x3", "password": "nope"}, headers={"X-Forwarded-For": "203.0.113.3"})
    assert r.status_code == 401


def test_x_forwarded_proto_from_a_trusted_proxy_counts_as_https(w, monkeypatch):
    c, csrf = w.setup_admin()
    link = c.post("/api/link", headers=ta.H(None, csrf)).json()
    proxy = w.client(client=("172.18.0.1", 40000))
    headers = {"X-Forwarded-For": "192.168.1.20", "X-Forwarded-Proto": "https"}
    assert proxy.post("/api/pair", json={"code": link["code"]}, headers=headers).status_code == 403  # not trusted: HTTP
    monkeypatch.setenv(m.proxies.ENV, "172.18.0.1")
    r = proxy.post("/api/pair", json={"code": link["code"]}, headers=headers)
    assert r.status_code == 200 and r.json()["token"].startswith("sd_")
    # and a session cookie made through it is Secure
    w2 = w.client(client=("172.18.0.1", 40001))
    r = w2.post("/api/auth/login", json={"username": "chris", "password": "correct horse"}, headers=headers)
    assert r.status_code == 200 and "secure" in r.headers["set-cookie"].lower()


def test_one_username_is_limited_from_any_address(w, monkeypatch, values):
    w.setup_admin()
    monkeypatch.setenv(m.proxies.ENV, "172.18.0.1")
    c = w.client(client=("172.18.0.1", 40000))
    values["login_attempts_per_user"] = 3
    codes = [c.post("/api/auth/login", json={"username": "chris", "password": "nope"},
                    headers={"X-Forwarded-For": f"203.0.113.{i}"}).status_code for i in range(1, 6)]
    assert codes[:3] == [401] * 3 and codes[3:] == [429, 429]
    # another username from another address isn't held up
    assert c.post("/api/auth/login", json={"username": "alex", "password": "nope"},
                  headers={"X-Forwarded-For": "203.0.113.50"}).status_code == 401
    values["login_attempts_per_user"] = 0  # no limit per username
    assert c.post("/api/auth/login", json={"username": "chris", "password": "correct horse"},
                  headers={"X-Forwarded-For": "203.0.113.60"}).status_code == 200


# --- 5. Streams ---------------------------------------------------------------------

def test_streams_need_a_link_a_sign_in_or_the_lan_while_login_is_required(w, values):
    c, csrf = w.setup_admin()
    assert w.service.login_required()
    outside = w.client(client=OUTSIDE)
    assert outside.get("/stream/channel1").status_code == 401
    assert w.client(client=LOCAL).get("/stream/channel1").status_code == 200        # this computer
    assert w.client(client=("192.168.1.30", 1)).get("/stream/channel1").status_code == 200  # the LAN
    token = m.stream_links.token("/stream/channel1")
    assert outside.get(f"/stream/channel1?t={token}").status_code == 200
    assert outside.get(f"/stream/channel2?t={token}").status_code == 401  # another path
    assert outside.get("/stream/channel1?t=0.abc").status_code == 401
    w.clock.now += 13 * 3600  # Stream link time: 12 h
    assert outside.get(f"/stream/channel1?t={token}").status_code == 401
    # A signed-in caller (here: a linked phone's token) plays from anywhere
    https = w.client(base_url="https://testserver")
    link = c.post("/api/link", headers=ta.H(None, csrf)).json()
    phone = https.post("/api/pair", json={"code": link["code"]}).json()["token"]
    assert outside.get("/stream/channel1", headers=ta.H(phone)).status_code == 200
    assert outside.get(f"/stream/channel1?token={phone}").status_code == 200
    # LAN speakers without a link: off
    values["lan_streams_open"] = 0
    assert w.client(client=("192.168.1.30", 1)).get("/stream/channel1").status_code == 401
    # The virtual speaker's path has its own secret: never asked for a link
    assert outside.get("/stream/virtual/abc").status_code == 404


def test_streams_stay_open_while_login_is_off(tmp_path, monkeypatch, values):
    w = World(tmp_path, monkeypatch, existing=True)  # an existing install: Require login stays off
    assert not w.service.login_required()
    assert w.client(client=OUTSIDE).get("/stream/channel1").status_code == 200
    assert m.stream_links.signed("https://demo.example/stream/channel1") == "https://demo.example/stream/channel1"


def test_handed_out_addresses_carry_a_link_unless_on_the_lan(w, values):
    w.setup_admin()
    signed = m.stream_links.signed
    url = signed("https://demo.example/stream/channel1")
    assert url.startswith("https://demo.example/stream/channel1?t=")
    t = url.split("t=", 1)[1]
    assert m.stream_links.valid("/stream/channel1", t)
    assert signed("http://192.168.1.5:8008/stream/channel1") == "http://192.168.1.5:8008/stream/channel1"
    path = signed("/stream/channel1?local=1&listener=ab12cd34")  # a personal channel's listen_path
    assert path.startswith("/stream/channel1?local=1&listener=ab12cd34&t=")
    values["lan_streams_open"] = 0
    assert "?t=" in signed("http://192.168.1.5:8008/stream/channel1")
    assert signed("/stream/virtual/abc") == "/stream/virtual/abc"


def test_open_streams_per_address_and_in_all(w, values):
    w.setup_admin()
    slots = m.stream_links.STREAMS
    values.update(streams_per_address=1, streams_limit=0)
    assert slots.take("127.0.0.1", 1)
    try:
        r = w.client(client=LOCAL).get("/stream/channel1")
        assert r.status_code == 429
        assert w.client(client=("192.168.1.30", 1)).get("/stream/channel1").status_code == 200  # another address
        values.update(streams_per_address=0, streams_limit=1)
        assert w.client(client=("192.168.1.30", 1)).get("/stream/channel1").status_code == 429  # in all
    finally:
        slots.give("127.0.0.1")
    assert slots.held() == 0  # every finished stream gave its slot back


def test_slots():
    s = m.limits.Slots()
    assert s.take("a", 2, 3) and s.take("a", 2, 3) and not s.take("a", 2, 3)
    assert s.take("b", 2, 3) and not s.take("c", 2, 3)
    s.give("a")
    assert s.take("c", 2, 3) and s.held() == 3 and s.held("a") == 1
    assert s.take("z", None, None)


# --- 8. Event streams ----------------------------------------------------------------

def test_events_are_filtered_by_what_the_caller_may_see():
    P = m.principal.Principal
    visible = m.plugin_framework_api.event_visible
    ev = lambda t, **d: SimpleNamespace(type=t, data=d)  # noqa: E731
    viewer = P("key", frozenset({"ev.read", "ch.view"}))
    assert visible(viewer, ev("channel.started", channel_id="a"))
    assert not visible(viewer, ev("theme.changed"))
    assert not visible(viewer, ev("settings.changed"))
    assert visible(viewer, ev("plugin.enabled"))
    some = P("key", frozenset({"ev.read", "ch.view"}), channels=frozenset({"a"}))
    assert visible(some, ev("channel.started", channel_id="a"))
    assert not visible(some, ev("channel.started", channel_id="b"))
    assert visible(None, ev("settings.changed"))  # the old events token: everything


def test_event_streams_per_caller(values):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from unittest.mock import MagicMock
    api = m.plugin_framework_api
    values["event_streams_per_caller"] = 1
    host = MagicMock()
    host.tokens.check.return_value = False
    app = FastAPI()
    app.include_router(api.create_plugin_framework_router(MagicMock(), host))

    @app.middleware("http")
    async def signed_in(request: Request, call_next):
        request.scope.setdefault("state", {})["auth"] = m.principal.Principal(
            "session", frozenset({"ev.read"}), user_id="u1")
        return await call_next(request)
    key = "user:u1"
    assert api.EVENT_SLOTS.take(key, 1)
    try:
        r = TestClient(app).get("/api/events/stream")
        assert r.status_code == 429
    finally:
        api.EVENT_SLOTS.give(key)


# --- 9. The public address -----------------------------------------------------------

def test_public_address_is_only_a_proxy_or_internet_address():
    pub = m.routes.public_address
    assert pub("https://demo.example") == {"url": "https://demo.example", "scheme": "https", "host": "demo.example",
                                           "port": 443}
    assert pub("https://demo.example:8443/") ["url"] == "https://demo.example:8443"
    assert pub("http://sonorium.example.com")["port"] == 80
    assert pub("https://192.168.1.5:8808")["host"] == "192.168.1.5"  # https: given as it is
    for lan in ("auto", "", None, "http://192.168.1.5:8008", "http://10.0.0.2:8008", "http://sonorium.local:8008",
                "http://127.0.0.1:8008", "ftp://x"):
        assert pub(lan or "") is None, lan


def test_info_and_link_codes_give_the_public_address(tmp_path, monkeypatch, values):
    w = World(tmp_path, monkeypatch, public_url="https://demo.example")
    w.interfaces = [("eth0", "172.18.0.5")]
    c, csrf = w.setup_admin()
    info = w.client().get("/api/info").json()
    assert info["url"] == info["public_url"] == "https://demo.example"
    assert info["host"] == "demo.example" and info["ports"]["https"] == 443
    assert info["internal_ports"] == {"http": m.runtime.http_port(), "https": 8808}
    link = c.post("/api/link", headers=ta.H(None, csrf)).json()
    params = m.qr.parse_link(link["uri"])
    assert params["h"] == "demo.example" and params["s"] == "443" and params["url"] == "https://demo.example"
    assert link["hosts"] == ["demo.example"] and link["url"] == "https://demo.example"
    assert "172.18.0.5" not in link["uri"] and "127.0.0.1" not in link["uri"]


def test_without_a_public_address_info_is_as_before(w):
    info = w.client().get("/api/info").json()
    assert info["public_url"] is None and info["host"] == "192.168.1.5"
    assert info["ports"] == {"http": 8008, "https": 8808} and info["url"] == "https://192.168.1.5:8808"
    c, csrf = w.setup_admin()
    assert "url" not in m.qr.parse_link(c.post("/api/link", headers=ta.H(None, csrf)).json()["uri"])


# --- 10. Ports -----------------------------------------------------------------------

@pytest.fixture
def rt(tmp_path, monkeypatch):
    rt = m.runtime
    monkeypatch.setattr(rt, "STANDALONE", True)
    monkeypatch.setattr(rt, "INSTALL", "docker")
    monkeypatch.setattr(rt, "CONNECTION_FILE", tmp_path / "connection.json")
    for names in rt.PORT_ENV.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)
    return rt


def test_ports_default_then_environment_then_setting(rt, monkeypatch):
    assert (rt.http_port(), rt.stream_port(), m.config.https_port()) == (8008, 8008, 8808)
    monkeypatch.setenv("SONORIUM__HTTP_PORT", "9000")
    monkeypatch.setenv("SONORIUM_HTTPS_PORT", "9443")
    assert (rt.http_port(), rt.stream_port(), m.config.https_port()) == (9000, 9000, 9443)
    rt.save_connection({"http_port": 8100, "https_port": 8900})
    assert (rt.http_port(), m.config.https_port()) == (8100, 8900)  # the setting wins
    monkeypatch.setenv("SONORIUM__HTTP_PORT", "70000")  # out of range: ignored
    rt.save_connection({"http_port": None})
    assert rt.http_port() == 8008
    monkeypatch.setattr(rt, "INSTALL", "windows")  # the Sonorium app on the computer sets them
    rt.save_connection({"http_port": 8100})
    monkeypatch.setenv("SONORIUM_HTTP_PORT", "8200")
    assert rt.http_port() == 8200 and not rt.ports_editable()


def test_port_settings_are_checked(rt):
    pytest.importorskip("fastapi")
    from fastapi import HTTPException
    with addon_modules("sonorium.api") as mods:
        check = mods.api.WebApp._check_ports
        mods.api.runtime.STANDALONE, mods.api.runtime.INSTALL = True, "docker"
        mods.api.runtime.CONNECTION_FILE = rt.CONNECTION_FILE
        for bad in ({"http_port": 0}, {"http_port": 70000}, {"https_port": "x"}, {"http_port": True},
                    {"http_port": 8808}, {"http_port": 9000, "https_port": 9000}):
            with pytest.raises(HTTPException) as e:
                check(dict(bad))
            assert e.value.status_code == 400, bad
        updates = {"http_port": "8100", "https_port": ""}
        check(updates)
        assert updates == {"http_port": 8100, "https_port": None}
        mods.api.runtime.INSTALL = "windows"
        with pytest.raises(HTTPException):
            check({"http_port": 8100})


def test_advertised_ports_follow_the_settings(tmp_path, monkeypatch, values):
    w = World(tmp_path, monkeypatch)
    monkeypatch.setenv("SONORIUM_HTTP_PORT", "8100")
    assert m.runtime.stream_port() == 8100
    info = w.client().get("/api/info").json()
    assert info["ports"]["http"] == 8008  # the stream URL's own port (it names one)
    assert info["internal_ports"]["http"] == m.runtime.http_port()


# --- b. Reserved sign-in ids -------------------------------------------------------------

def test_core_sign_in_ids_are_reserved_and_never_unregistered(w):
    for reserved in ("users", "keys", "Users"):
        assert not m.loader.is_valid_plugin_id(reserved)
    assert m.loader.is_valid_plugin_id("ambient_mixer")
    w.setup_admin()
    w.service.unregister("users")  # a plugin switched off with that id: sign-in stays
    w.service.unregister("keys")
    assert "users" in w.service.authenticators and "keys" in w.service.authenticators
    assert not w.service.closed
    r = w.client(client=OUTSIDE).get("/api/sessions")
    assert r.status_code == 401  # still nobody signed in: not local, full access


# --- c. First-run setup --------------------------------------------------------------

def test_first_account_only_from_the_lan_or_with_the_setup_token(w, monkeypatch):
    body = {"username": "chris", "password": "correct horse"}
    r = w.client(client=OUTSIDE).post("/api/auth/setup", json=body)
    assert r.status_code == 403
    monkeypatch.setenv("SONORIUM_SETUP_TOKEN", "s3cret-setup")
    assert w.client(client=OUTSIDE).post("/api/auth/setup", json={**body, "setup_token": "wrong"}).status_code == 403
    r = w.client(client=OUTSIDE).post("/api/auth/setup", json=body,
                                      headers={"X-Sonorium-Setup-Token": "s3cret-setup"})
    assert r.status_code == 200


def test_first_account_from_this_computer(w):
    assert w.client(client=LOCAL).post("/api/auth/setup",
                                       json={"username": "chris", "password": "correct horse"}).status_code == 200


# --- d. Backups: every account together, and free disk ------------------------------------

def test_backups_total_and_free_disk_limits(tmp_path, monkeypatch):
    B = m.backups
    total, free = {"mb": 0.0}, {"mb": 0.0}
    store = B.BackupStore(tmp_path / "backups", lambda: 0, lambda: total["mb"], lambda: free["mb"])
    (store.account_dir("user:a") / "blobs").mkdir(parents=True)
    (store.account_dir("user:a") / "blobs" / "x").write_bytes(b"x" * 600 * 1024)
    store._room_for("user:b", 600 * 1024)  # no limits
    total["mb"] = 1
    with pytest.raises(B.BackupError) as e:
        store._room_for("user:b", 600 * 1024)  # 0.6 MB of user a + 0.6 MB > 1 MB in all
    assert e.value.status == 507
    store._room_for("user:b", 100 * 1024)
    total["mb"] = 0
    monkeypatch.setattr(store, "_free_disk", lambda: 5 * 1024 * 1024)
    free["mb"] = 4.5
    with pytest.raises(B.BackupError):
        store._room_for("user:b", 1024 * 1024)  # would leave 4 MB free, 4.5 MB must stay
    store._room_for("user:b", 100 * 1024)
