"""
Sign-in (sonorium/auth) with the built-in Users and Integration plugins:
passwords, browser sessions and CSRF, roles on a sample of routes per area,
Link App pairing (single use, expiry, HTTPS), one-time sign-in codes, keys
(abilities, channels, changes in flight, expiry, revoking), the bypass, HA
ingress trust, web_ui_only_through_ha, upgrades (Require login off), a
switched-off or missing sign-in plugin, and Home Assistant's users on the
add-on (the Supervisor's auth API, mocked).
"""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("argon2")
pytest.importorskip("cryptography")
pytest.importorskip("segno")

from playback_helpers import PACKAGE, addon_modules  # noqa: E402

m = SimpleNamespace()
BUILTIN = PACKAGE / "plugins" / "builtin"
CHANNEL = "ch1"


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.auth.service", "sonorium.auth.middleware", "sonorium.auth.routes",
                       "sonorium.auth.permissions", "sonorium.auth.config", "sonorium.auth.qr", "sonorium.auth.announce",
                       "sonorium.auth.tokens", "sonorium.plugins.loader", "sonorium.runtime") as mods:
        m.__dict__.update(mods.__dict__)
        yield


class Clock:
    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now


def load_plugin(name):
    folder = BUILTIN / name
    manifest = json.loads((folder / "manifest.json").read_text())
    cls = m.loader.load_plugin_class(folder, manifest)
    return cls(plugin_dir=folder, settings={}, audio_path=None)


class World:
    def __init__(self, tmp_path, monkeypatch, *, plugins=("users", "integration"), existing=False, ha=None):
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient
        monkeypatch.setenv("SONORIUM_CONFIG_DIR", str(tmp_path))
        for name in ("BYPASS_LOGIN", "SONORIUM__BYPASS_LOGIN", "SONORIUM__WEB_UI_ONLY_THROUGH_HA"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(m.runtime, "STANDALONE", ha is None)
        if ha is None:
            monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        else:  # the add-on, with Home Assistant's Supervisor (FakeSupervisor)
            monkeypatch.setenv("SUPERVISOR_TOKEN", FakeSupervisor.TOKEN)
        self.interfaces = [("lo", "127.0.0.1"), ("eth0", "192.168.1.5"), ("docker0", "172.17.0.1")]
        monkeypatch.setattr(m.announce, "interface_addresses", lambda: self.interfaces)
        self.mdns = None
        if existing:
            (tmp_path / "state.json").write_text("{}")
        self.clock = Clock()
        self.service = s = m.service.AuthService(tmp_path, clock=self.clock)
        if ha is not None:
            import httpx
            s.supervisor_transport = httpx.MockTransport(ha.handler)
            s.supervisor_ws_connect = ha.ws_connect
        self.app = app = FastAPI()
        s.app = app
        app.add_middleware(m.middleware.AuthMiddleware, service=s)
        app.include_router(m.routes.create_core_auth_router(
            s, stream_url=lambda: "http://192.168.1.5:8008", features=lambda: {}, version="test",
            https_host_port=lambda: 8808, mdns_host=lambda: self.mdns))
        seen = self.seen = []

        def record(name):
            async def handler(request: Request):
                p = m.middleware.principal_of(request)
                seen.append((name, p.kind))
                return {"ok": name, "kind": p.kind, "user": p.user_id}
            return handler

        for method, path in [("GET", "/"), ("GET", "/api/sessions"), ("POST", "/api/sessions"),
                             ("POST", "/api/sessions/{session_id}/play"), ("PUT", "/api/sessions/{session_id}"),
                             ("GET", "/api/connection"), ("GET", "/api/themes"), ("POST", "/api/themes/create"),
                             ("PUT", "/api/themes/{theme_id}/presets/{preset_id}"), ("GET", "/api/playlists"),
                             ("POST", "/api/playlists"), ("GET", "/api/speakers"),
                             ("PUT", "/api/speakers/{speaker_id}/settings"), ("GET", "/api/settings"),
                             ("PUT", "/api/settings"), ("GET", "/api/plugins/catalog"),
                             ("GET", "/api/plugins/{plugin_id}"), ("POST", "/api/channels/{channel_id}/play"),
                             ("GET", "/stream/channel{channel_id:int}"), ("GET", "/api/events/catalogue")]:
            app.add_api_route(path, record(f"{method} {path}"), methods=[method])
        s.note_install()
        self.plugins = {}
        host = SimpleNamespace(auth=s)
        for name in plugins:
            plugin = load_plugin(name)
            plugin._host = host
            asyncio.run(plugin.on_enable())
            self.plugins[name] = plugin
        s.mark_ready()
        self.TestClient = TestClient

    def client(self, **kw):
        return self.TestClient(self.app, **kw)

    def setup_admin(self, c=None, username="chris", password="correct horse"):
        c = c or self.client()
        r = c.post("/api/auth/setup", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return c, c.get("/api/auth/me").json()["csrf"]

    def sign_in(self, username, password):
        c = self.client()
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return c, c.get("/api/auth/me").json()["csrf"]

    def disable(self, name):
        asyncio.run(self.plugins[name].on_disable())


@pytest.fixture
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def H(token, csrf=None):
    return {"Authorization": f"Bearer {token}"} if token else {"X-CSRF-Token": csrf}


# --- Passwords ------------------------------------------------------------------

def test_passwords_are_argon2id_hashes(w):
    pw = w.plugins["users"].__class__.__module__
    passwords = __import__("sys").modules["sonorium_plugin_users_passwords"]
    stored = passwords.hash_password("correct horse")
    assert stored.startswith("$argon2id$") and "correct horse" not in stored
    assert passwords.verify_password(stored, "correct horse")
    assert not passwords.verify_password(stored, "wrong horse")
    assert not passwords.verify_password(None, "anything")
    assert passwords.password_problem("short") and passwords.password_problem("long enough") is None
    assert pw


def test_users_file_has_hashes_only(w, tmp_path):
    c, csrf = w.setup_admin()
    users = (tmp_path / "users.json").read_text()
    assert "correct horse" not in users and "$argon2id$" in users
    cookie = c.cookies.get("sonorium_session")
    assert cookie and cookie not in (tmp_path / "security.json").read_text()


# --- No sign-in plugin: today's open access -------------------------------------

def test_without_plugins_everything_is_local(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, plugins=())
    c = w.client()
    assert c.get("/api/connection").json()["kind"] == "local"
    assert c.post("/api/sessions/x/play").status_code == 200
    me = c.get("/api/auth/me").json()
    assert me["kind"] == "local" and me["plugins"] == []
    # A change from another site is refused even then
    assert c.post("/api/sessions/x/play", headers={"Origin": "http://evil.example"}).status_code == 403
    # The app still links and gets full access (less managing users and keys)
    link = c.post("/api/link").json()
    assert "&u=" not in link["uri"] and link["username"] is None
    https = w.client(base_url="https://testserver")
    token = https.post("/api/pair", json={"code": link["code"], "device_name": "Pixel"}).json()["token"]
    anon = w.client()
    assert anon.get("/api/connection", headers=H(token)).json()["kind"] == "device"


def test_unmapped_api_route_fails_closed(w):
    assert w.client().get("/api/heartbeat").status_code == 403


# --- First run and sessions ------------------------------------------------------

def test_new_install_needs_the_admin_account(w):
    c = w.client()
    r = c.get("/api/sessions")
    assert r.status_code == 401 and r.json()["setup"] is True
    assert c.get("/api/auth/me").json()["needs_setup"] is True
    assert c.get("/stream/channel1").status_code == 200  # speakers keep playing
    assert c.get("/").status_code == 200                 # the page shell (sign-in screen)
    w.setup_admin(c)
    assert c.get("/api/sessions").json()["kind"] == "session"
    assert w.client().post("/api/auth/setup", json={"username": "x", "password": "long enough"}).status_code == 409


def test_session_cookie_flags_and_csrf(w):
    c = w.client(base_url="https://testserver")
    r = c.post("/api/auth/setup", json={"username": "chris", "password": "correct horse"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie and "max-age=2592000" in cookie
    csrf = c.get("/api/auth/me").json()["csrf"]
    assert c.post("/api/sessions/x/play").status_code == 403
    assert c.post("/api/sessions/x/play", headers=H(None, "wrong")).status_code == 403
    assert c.post("/api/sessions/x/play", headers=H(None, csrf)).status_code == 200
    c.post("/api/auth/logout")
    assert c.get("/api/sessions").status_code == 401


def test_session_expires_when_unused(w):
    c, _ = w.setup_admin()
    w.clock.now += 15 * 86400
    assert c.get("/api/sessions").status_code == 401


def test_login_is_rate_limited(w):
    w.setup_admin()
    c = w.client()
    codes = [c.post("/api/auth/login", json={"username": "chris", "password": "nope"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert c.post("/api/auth/login", json={"username": "chris", "password": "correct horse"}).status_code == 429


# --- Roles -----------------------------------------------------------------------

def test_user_role_on_routes_per_area(w):
    admin, csrf = w.setup_admin()
    assert admin.post("/api/users", json={"username": "alex", "password": "alex password", "role": "user"},
                      headers=H(None, csrf)).status_code == 200
    user, ucsrf = w.sign_in("alex", "alex password")
    allowed = [("get", "/api/sessions"), ("post", "/api/sessions/a/play"), ("post", "/api/sessions"),
               ("get", "/api/themes"), ("get", "/api/playlists"), ("post", "/api/playlists"),
               ("get", "/api/speakers"), ("get", "/api/settings"), ("get", "/api/events/catalogue")]
    refused = [("get", "/api/connection"), ("post", "/api/themes/create"), ("put", "/api/themes/t/presets/p"),
               ("put", "/api/speakers/s/settings"), ("put", "/api/settings"), ("get", "/api/plugins/catalog"),
               ("get", "/api/plugins/x"), ("get", "/api/users"), ("get", "/api/keys")]
    for method, path in allowed:
        assert getattr(user, method)(path, headers=H(None, ucsrf)).status_code == 200, path
    for method, path in refused:
        r = getattr(user, method)(path, headers=H(None, ucsrf))
        assert r.status_code == 403, path
    for method, path in refused:
        assert getattr(admin, method)(path, headers=H(None, csrf)).status_code == 200, path


def test_last_admin_stays(w):
    admin, csrf = w.setup_admin()
    me = admin.get("/api/auth/me").json()["user"]["id"]
    assert admin.put(f"/api/users/{me}", json={"role": "user"}, headers=H(None, csrf)).status_code == 409
    assert admin.delete(f"/api/users/{me}", headers=H(None, csrf)).status_code == 409


def test_admin_signs_one_browser_out(w):
    admin, csrf = w.setup_admin()
    assert admin.post("/api/users", json={"username": "alex", "password": "alex password", "role": "user"},
                      headers=H(None, csrf)).status_code == 200
    laptop, _ = w.sign_in("alex", "alex password")
    tablet, _ = w.sign_in("alex", "alex password")
    users = admin.get("/api/users").json()["users"]
    alex = next(u for u in users if u["username"] == "alex")
    browsers = [d for d in alex["devices"] if d["kind"] == "browser"]
    assert len(browsers) == 2 and not any(d["this"] for d in browsers)
    me = next(u for u in users if u["you"])
    mine = next(d for d in me["devices"] if d["kind"] == "browser")
    assert mine["this"]
    # Another user's session id can't be ended through this user
    assert admin.delete(f"/api/users/{alex['id']}/sessions/{mine['id']}", headers=H(None, csrf)).status_code == 404
    r = admin.delete(f"/api/users/{alex['id']}/sessions/{browsers[0]['id']}", headers=H(None, csrf))
    assert r.status_code == 200 and r.json() == {"signed_out": True, "this_browser": False}
    left = [c.get("/api/sessions").status_code for c in (laptop, tablet)]
    assert sorted(left) == [200, 401]
    # A user can't use it
    user, ucsrf = w.sign_in("alex", "alex password")
    assert user.delete(f"/api/users/{me['id']}/sessions/{mine['id']}", headers=H(None, ucsrf)).status_code == 403
    # Your own browser: signed out, cookie cleared
    r = admin.delete(f"/api/users/{me['id']}/sessions/{mine['id']}", headers=H(None, csrf))
    assert r.json()["this_browser"] is True
    assert admin.get("/api/sessions").status_code == 401


# --- Link App and pairing ----------------------------------------------------------

def link_and_pair(w, c, csrf, name="Pixel 8"):
    link = c.post("/api/link", headers=H(None, csrf)).json()
    https = w.client(base_url="https://testserver")
    r = https.post("/api/pair", json={"code": link["code"], "device_name": name, "platform": "android"})
    return link, r


def test_link_app_qr_and_single_use_code(w):
    c, csrf = w.setup_admin()
    link, r = link_and_pair(w, c, csrf)
    uri = link["uri"]
    assert uri.startswith("https://sonorium.app/link#v=1&id=") and "&h=192.168.1.5&p=8008&s=8808&fp=" in uri
    assert f"&c={link['code']}&u=chris" in uri and link["expires_in"] == 300 and len(link["words"]) == 4
    assert "<svg" in link["qr_svg"] and "data:image/png;base64," in link["qr_svg"]
    assert r.status_code == 200 and r.json()["token"].startswith("sd_") and r.json()["user"]["username"] == "chris"
    again = w.client(base_url="https://testserver").post("/api/pair", json={"code": link["code"]})
    assert again.status_code == 401


def test_link_lists_every_lan_address_and_the_mdns_name(w):
    w.interfaces = [("eth1", "10.0.0.7"), ("eth0", "192.168.1.5"), ("veth12ab", "172.18.0.1"),
                    ("br-0a1b2c", "172.19.0.1"), ("wlan0", "169.254.3.3")]
    w.mdns = "sonorium-2.local"
    c, csrf = w.setup_admin()
    link = c.post("/api/link", headers=H(None, csrf)).json()
    assert link["hosts"] == ["192.168.1.5", "10.0.0.7", "sonorium-2.local"]
    params = m.qr.parse_link(link["uri"])
    assert params["h"] == "192.168.1.5,10.0.0.7,sonorium-2.local" and params["c"] == link["code"]
    assert w.client().get("/api/info").json()["hostname"] == "sonorium-2.local"


def test_pair_code_expires_and_needs_https(w):
    c, csrf = w.setup_admin()
    link = c.post("/api/link", headers=H(None, csrf)).json()
    assert w.client().post("/api/pair", json={"code": link["code"]}).status_code == 403  # plain HTTP
    w.clock.now += 301
    assert w.client(base_url="https://testserver").post("/api/pair", json={"code": link["code"]}).status_code == 401


def test_pairing_is_rate_limited(w):
    https = w.client(base_url="https://testserver")
    codes = [https.post("/api/pair", json={"code": "WRONG"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_phone_acts_as_its_user_and_is_ejected_at_once(w):
    c, csrf = w.setup_admin()
    _, r = link_and_pair(w, c, csrf)
    token, device_id = r.json()["token"], r.json()["device_id"]
    phone = w.client()
    assert phone.post("/api/sessions/a/play", headers=H(token)).json()["kind"] == "device"
    for path in ("/api/users", "/api/keys"):  # never users, devices or keys
        assert phone.get(path, headers=H(token)).status_code == 403
    listing = c.get("/api/users").json()
    users = listing["users"]
    assert [d["name"] for d in users[0]["devices"] if d["kind"] == "phone"] == ["Pixel 8"]
    assert listing["unlinked_devices"] == []  # the phone is its user's, not unlinked
    assert c.delete(f"/api/devices/{device_id}", headers=H(None, csrf)).status_code == 200
    r = phone.get("/api/sessions", headers=H(token))
    assert r.status_code == 401 and r.json()["pair"] is True


def test_a_phone_sees_renames_and_unlinks_itself(w):
    """G1: /api/devices/self (permission "device"): only for the phone calling."""
    c, csrf = w.setup_admin()
    _, r = link_and_pair(w, c, csrf)
    token, device_id = r.json()["token"], r.json()["device_id"]
    _, r2 = link_and_pair(w, c, csrf, name="Tablet")
    other_token, other_id = r2.json()["token"], r2.json()["device_id"]
    phone = w.client()
    me = phone.get("/api/devices/self", headers=H(token)).json()
    assert me["id"] == device_id and me["name"] == "Pixel 8" and me["platform"] == "android"
    assert me["user_id"] and me["created"] and me["last_seen"]
    # Rename: trimmed to 60 characters; empty is refused
    r = phone.put("/api/devices/self", json={"device_name": "  Chris's phone  "}, headers=H(token))
    assert r.status_code == 200 and r.json()["name"] == "Chris's phone"
    assert phone.put("/api/devices/self", json={"device_name": "x" * 80}, headers=H(token)).json()["name"] == "x" * 60
    assert phone.put("/api/devices/self", json={"device_name": " "}, headers=H(token)).status_code == 400
    assert [d["name"] for d in c.get("/api/devices").json()["devices"]][0] == "x" * 60
    # A browser has no "device" record of its own
    assert c.get("/api/devices/self").status_code == 403
    # A phone can't remove another phone (even of the same user), nor list them
    assert phone.delete(f"/api/devices/{other_id}", headers=H(token)).status_code == 403
    assert phone.get("/api/devices", headers=H(token)).status_code == 403
    # ...but may remove itself by id, or as "self": the token stops working at once
    assert phone.delete("/api/devices/self", headers=H(token)).json() == {"removed": True}
    r = phone.get("/api/devices/self", headers=H(token))
    assert r.status_code == 401 and r.json()["pair"] is True
    assert phone.delete(f"/api/devices/{other_id}", headers=H(other_token)).json() == {"removed": True}
    assert phone.get("/api/sessions", headers=H(other_token)).status_code == 401


def test_app_sign_in_with_password(w):
    w.setup_admin()
    https = w.client(base_url="https://testserver")
    r = https.post("/api/pair/login", json={"username": "chris", "password": "correct horse", "device_name": "Tab"})
    assert r.status_code == 200 and r.json()["token"].startswith("sd_") and len(r.json()["words"]) == 4
    assert https.post("/api/pair/login", json={"username": "chris", "password": "nope"}).status_code == 401
    assert w.client().post("/api/pair/login", json={"username": "chris", "password": "correct horse"}).status_code == 403


# --- One-time sign-in code (recovery) ------------------------------------------------

def test_recovery_code_then_new_password(w):
    c, csrf = w.setup_admin()
    _, r = link_and_pair(w, c, csrf)
    token = r.json()["token"]
    code = w.client().post("/api/devices/recovery-code", headers=H(token)).json()["code"]
    assert len(code) == 6 and code.isdigit()
    b = w.client()
    assert b.post("/api/auth/code-login", json={"username": "chris", "code": code}).json()["reset"] is True
    me = b.get("/api/auth/me").json()
    assert me["reset"] is True and b.get("/api/sessions").json()["reset"] is True
    assert b.post("/api/auth/new-password", json={"password": "brand new pw"},
                  headers=H(None, me["csrf"])).status_code == 200
    assert b.get("/api/sessions").status_code == 200
    assert c.get("/api/sessions").status_code == 401  # other browsers signed out
    assert w.client().get("/api/sessions", headers=H(token)).status_code == 200  # the phone stays linked
    assert w.client().post("/api/auth/code-login", json={"username": "chris", "code": code}).status_code == 401
    w.sign_in("chris", "brand new pw")


def test_recovery_code_wrong_tries_and_expiry(w):
    c, csrf = w.setup_admin()
    _, r = link_and_pair(w, c, csrf)
    token = r.json()["token"]
    code = w.client().post("/api/devices/recovery-code", headers=H(token)).json()["code"]
    wrong = "000000" if code != "000000" else "111111"
    accounts = w.service.plugin_state["users"]
    for _ in range(5):
        assert accounts.use_signin_code("chris", wrong) is None
    assert accounts.use_signin_code("chris", code) is None  # stopped working after 5 wrong tries
    code = w.client().post("/api/devices/recovery-code", headers=H(token)).json()["code"]
    w.clock.now += 301
    assert accounts.use_signin_code("chris", code) is None
    # Only a linked phone can ask for one
    assert c.post("/api/devices/recovery-code", headers=H(None, csrf)).status_code == 403


# --- Keys --------------------------------------------------------------------------

def make_key(c, csrf, **body):
    body.setdefault("name", "Discord bot")
    body.setdefault("expires", "90d")
    r = c.post("/api/keys", json=body, headers=H(None, csrf))
    assert r.status_code == 200, r.text
    return r.json()


def test_key_allows_only_what_is_checked(w, tmp_path):
    c, csrf = w.setup_admin()
    key = make_key(c, csrf, abilities=["ch.view", "ch.play", "admin.users", "admin.keys", "self"])
    assert key["key"].startswith("sk_") and key["key"] not in (tmp_path / "security.json").read_text()
    assert key["abilities"] == ["ch.view", "ch.play"] and key["chips"] == ["Channels: view, play and stop"]
    k = w.client()
    assert k.get("/api/sessions", headers=H(key["key"])).json()["kind"] == "key"
    assert k.post("/api/sessions/a/play", headers=H(key["key"])).status_code == 200  # no CSRF for keys
    for path in ("/api/themes", "/api/connection", "/api/users", "/api/keys"):
        assert k.get(path, headers=H(key["key"])).status_code == 403
    assert k.get("/api/sessions", headers=H("sk_not-a-key")).status_code == 401


def test_key_changes_in_flight_and_revocation(w):
    c, csrf = w.setup_admin()
    key = make_key(c, csrf, abilities=["ch.view"])
    k = w.client()
    assert k.get("/api/themes", headers=H(key["key"])).status_code == 403
    r = c.put(f"/api/keys/{key['id']}", json={"abilities": ["ch.view", "th.view"], "name": "Bot"}, headers=H(None, csrf))
    assert r.json()["chips"] == ["Channels: view", "Themes: view"] and r.json()["name"] == "Bot"
    assert k.get("/api/themes", headers=H(key["key"])).status_code == 200
    assert c.delete(f"/api/keys/{key['id']}", headers=H(None, csrf)).status_code == 200
    assert k.get("/api/sessions", headers=H(key["key"])).status_code == 401


def test_key_expiry_and_never(w):
    c, csrf = w.setup_admin()
    short = make_key(c, csrf, abilities=["ch.view"], expires="7d")
    never = make_key(c, csrf, abilities=["ch.view"], expires="never")
    assert never["expires"] is None and short["expires"]
    w.clock.now += 8 * 86400
    k = w.client()
    assert k.get("/api/sessions", headers=H(short["key"])).status_code == 401
    assert k.get("/api/sessions", headers=H(never["key"])).status_code == 200


def test_key_for_some_channels_only(w):
    c, csrf = w.setup_admin()
    key = make_key(c, csrf, abilities=["ch.view", "ch.play", "ch.add"], channels=[CHANNEL])
    k = w.client()
    assert k.post(f"/api/sessions/{CHANNEL}/play", headers=H(key["key"])).status_code == 200
    assert k.post("/api/sessions/other/play", headers=H(key["key"])).status_code == 403
    assert k.post("/api/channels/1/play", headers=H(key["key"])).status_code == 403
    assert k.post("/api/sessions", headers=H(key["key"])).status_code == 403  # a new channel isn't one of its own


# --- Bypass ------------------------------------------------------------------------

def test_bypass_login(w, monkeypatch):
    c, csrf = w.setup_admin()
    _, r = link_and_pair(w, c, csrf)
    token = r.json()["token"]
    monkeypatch.setenv("BYPASS_LOGIN", "true")
    anyone = w.client()
    assert anyone.get("/api/users").status_code == 200  # reset your account under Users & devices
    me = anyone.get("/api/auth/me").json()
    assert me["kind"] == "bypass" and me["bypass"] is True
    assert anyone.get("/api/sessions", headers=H(token)).json()["kind"] == "device"  # phones keep working
    monkeypatch.setenv("BYPASS_LOGIN", "false")
    assert w.client().get("/api/users").status_code == 401


# --- Home Assistant ingress (add-on) -------------------------------------------------

HA = {"X-Remote-User-Id": "abc123", "X-Remote-User-Name": "chris", "X-Remote-User-Display-Name": "Chris"}


def test_ingress_trusted_only_from_the_supervisor(w, monkeypatch):
    monkeypatch.setattr(m.runtime, "STANDALONE", False)
    sidebar = w.client(base_url="http://testserver:8099", client=("172.30.32.2", 40000))
    r = sidebar.get("/api/users", headers=HA)
    assert r.status_code == 200 and [u["username"] for u in r.json()["users"]] == ["chris"]
    assert r.json()["users"][0]["ha"] is True and r.json()["users"][0]["role"] == "admin"
    me = sidebar.get("/api/auth/me", headers=HA).json()
    assert me["kind"] == "ingress" and me["user"]["username"] == "chris"
    assert sidebar.post("/api/sessions/a/play", headers=HA).status_code == 403  # CSRF still needed
    assert sidebar.post("/api/sessions/a/play", headers={**HA, "X-CSRF-Token": me["csrf"]}).status_code == 200
    spoofed = w.client(base_url="http://testserver:8099", client=("192.168.1.66", 40000))
    assert spoofed.get("/api/users", headers=HA).status_code == 403
    direct = w.client(base_url="http://testserver:8080", client=("172.30.32.2", 40000))
    assert direct.get("/api/users", headers=HA).status_code == 401  # headers mean nothing on the direct port


def test_web_ui_only_through_ha(w, monkeypatch):
    c, csrf = w.setup_admin()
    _, r = link_and_pair(w, c, csrf)
    token = r.json()["token"]
    monkeypatch.setattr(m.runtime, "STANDALONE", False)
    monkeypatch.setenv("SONORIUM__WEB_UI_ONLY_THROUGH_HA", "true")
    direct = w.client(base_url="http://testserver:8080")
    assert direct.get("/").status_code == 404
    assert c.get("/api/sessions").status_code == 401  # browser sessions aren't used on the direct port
    assert direct.post("/api/auth/login", json={"username": "chris", "password": "correct horse"}).status_code == 404
    assert direct.get("/api/sessions", headers=H(token)).status_code == 200
    assert direct.get("/stream/channel1").status_code == 200
    sidebar = w.client(base_url="http://testserver:8099", client=("172.30.32.2", 1))
    assert sidebar.get("/", headers=HA).status_code == 200


# --- Upgrades, switching the Users plugin off, a missing sign-in plugin -------------

def test_existing_install_stays_open_until_require_login(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, existing=True)
    c = w.client()
    assert c.get("/api/sessions").json()["kind"] == "open"
    assert c.get("/api/users").status_code == 401  # managing users needs a sign-in
    me = c.get("/api/auth/me").json()
    assert me["require_login"] is False and me["needs_setup"] is False
    admin, csrf = w.setup_admin(c)
    assert w.client().get("/api/sessions").json()["kind"] == "open"  # nothing changes yet
    assert admin.put("/api/auth/require-login", json={"on": True}, headers=H(None, csrf)).status_code == 200
    assert w.client().get("/api/sessions").status_code == 401


def test_require_login_needs_an_admin_password(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, existing=True)
    accounts = w.service.plugin_state["users"]
    accounts.ha_user("abc", "chris")  # an HA sidebar account has no password
    with pytest.raises(Exception):
        accounts.set_require_login(True)


def test_switching_users_off_opens_sonorium(w):
    w.setup_admin()
    assert w.client().get("/api/sessions").status_code == 401
    w.disable("users")
    c = w.client()
    assert c.get("/api/sessions").json()["kind"] == "local"
    assert c.post("/api/auth/login", json={}).status_code == 404  # its routes are gone


def test_unbound_phone_must_relink_once_login_is_required(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, existing=True)
    c = w.client()
    link = c.post("/api/link").json()
    assert link["username"] is None
    token = w.client(base_url="https://testserver").post("/api/pair", json={"code": link["code"]}).json()["token"]
    assert w.client().get("/api/sessions", headers=H(token)).status_code == 200
    admin, csrf = w.setup_admin(c)
    admin.put("/api/auth/require-login", json={"on": True}, headers=H(None, csrf))
    r = w.client().get("/api/sessions", headers=H(token))
    assert r.status_code == 401 and r.json()["pair"] is True


def test_closed_until_the_sign_in_plugin_starts(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, plugins=())
    w.service.expected_providers = {"users"}  # switched on in state.json, but it didn't load
    r = w.client().get("/api/sessions")
    assert r.status_code == 503 and r.json()["closed"] is True
    assert w.client().get("/stream/channel1").status_code == 200
    w.service.ready = False
    assert w.client().get("/api/sessions").json()["starting"] is True


def test_notification_webhook_needs_no_sign_in(w):
    """A rule's webhook: the secret in its URL is the credential (the route checks it), even with Require login on."""
    async def hook(secret: str):
        return {"secret": secret}
    w.app.add_api_route("/api/notify/{secret}", hook, methods=["GET", "POST"])
    w.setup_admin()
    c = w.client()
    assert c.get("/api/sessions").status_code == 401  # login is required
    assert c.post("/api/notify/abc").json() == {"secret": "abc"}
    assert c.get("/api/notify/abc").status_code == 200
    # Not from another site's page in a browser
    assert c.post("/api/notify/abc", headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
                  ).status_code == 403
    assert m.tokens.redact("POST /api/notify/AbC_123-x HTTP/1.1") == "POST /api/notify/*** HTTP/1.1"


def test_info_is_public(w):
    info = w.client().get("/api/info").json()
    assert info["api"] == 1 and info["ports"] == {"http": 8008, "https": 8808} and len(info["fingerprint"]) == 64
    assert info["login_required"] is True and set(info["plugins"]) == {"users", "integration"}
    assert info["boot_id"] == m.runtime.BOOT_ID and len(info["boot_id"]) == 32  # G4: new at every start
    assert info["ui"] == {"version": "test", "host_api": 1}  # G14


# --- Home Assistant's users (add-on) ---------------------------------------------------

class FakeWebSocket:
    """HA's WebSocket API through the Supervisor: auth, then config/auth/list."""

    def __init__(self, fake):
        self.fake = fake
        self.out = [{"type": "auth_required", "ha_version": "2026.10.0"}]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def recv(self):
        return json.dumps(self.out.pop(0))

    async def send(self, text):
        message = json.loads(text)
        if message.get("type") == "auth":
            ok = message.get("access_token") == FakeSupervisor.TOKEN
            self.out.append({"type": "auth_ok" if ok else "auth_invalid"})
        elif message.get("type") == "config/auth/list":
            self.fake.lists += 1
            self.out.append({"id": message["id"], "type": "event", "event": {}})  # not the answer
            self.out.append({"id": message["id"], "type": "result", "success": True, "result": self.fake.users})


class FakeSupervisor:
    """Home Assistant through its Supervisor: config/auth/list (WebSocket) and POST /auth."""
    TOKEN = "supervisor-token"

    def __init__(self):
        self.users = [
            {"id": "ha-id-1", "username": "synthesis", "name": "Chris", "is_owner": True, "is_active": True,
             "system_generated": False, "group_ids": ["system-admin"], "credentials": [{"type": "homeassistant"}]},
            {"id": "ha-id-2", "username": "kid", "name": "Kid", "is_owner": False, "is_active": True,
             "system_generated": False, "group_ids": ["system-users"], "credentials": [{"type": "homeassistant"}]},
            {"id": "ha-id-sup", "username": None, "name": "Supervisor", "is_owner": False, "is_active": True,
             "system_generated": True, "group_ids": ["system-admin"], "credentials": []},
        ]
        self.passwords = {"synthesis": "ha password 1", "kid": "ha password 2"}
        self.lists = 0
        self.down = False

    def entry(self, username):
        return next(u for u in self.users if u["username"] == username)

    def ws_connect(self, url, **kw):
        assert url == "ws://supervisor/core/websocket"
        if self.down:
            raise OSError("connection refused")
        return FakeWebSocket(self)

    def handler(self, request):
        import httpx
        assert request.headers["authorization"] == f"Bearer {self.TOKEN}"
        if self.down:
            return httpx.Response(502)
        if request.method == "GET" and request.url.path == "/auth/list":
            return httpx.Response(403)  # needs an admin hassio_role, which Sonorium doesn't have
        if request.method == "POST" and request.url.path == "/auth":
            body = json.loads(request.content)
            ok = body.get("username") in self.passwords and self.passwords[body["username"]] == body.get("password")
            return httpx.Response(200 if ok else 401, json={"result": "ok" if ok else "error", "data": {}})
        return httpx.Response(404)


HA_IDS = {"synthesis": "ha-id-1", "kid": "ha-id-2"}


def sidebar(w, username):
    """HA's sidebar as an HA user: (client, headers with CSRF)."""
    c = w.client(base_url="http://testserver:8099", client=("172.30.32.2", 40000))
    headers = {"X-Remote-User-Id": HA_IDS[username], "X-Remote-User-Name": username,
               "X-Remote-User-Display-Name": username.title()}
    me = c.get("/api/auth/me", headers=headers).json()
    return c, {**headers, "X-CSRF-Token": me["csrf"]}, me


@pytest.fixture
def fake():
    return FakeSupervisor()


@pytest.fixture
def ha_world(tmp_path, monkeypatch, fake):
    return World(tmp_path, monkeypatch, ha=fake)


def test_ha_role_follows_home_assistant(ha_world, fake):
    w = ha_world
    _, _, me = sidebar(w, "synthesis")
    assert me["user"]["username"] == "synthesis" and me["user"]["role"] == "admin" and me["user"]["ha"] is True
    assert "admin.users" in me["perms"] and fake.lists == 1  # asked at once for an HA user Sonorium hadn't seen
    known = w.service.plugin_state["users"].ha.users
    assert [u["id"] for u in known] == ["ha-id-1", "ha-id-2"]  # not HA's system users (the Supervisor)
    kid, kid_h, me = sidebar(w, "kid")
    assert me["user"]["role"] == "user" and "admin.users" not in me["perms"]
    assert kid.get("/api/users", headers=kid_h).status_code == 403
    assert kid.put("/api/settings", headers=kid_h).status_code == 403
    assert kid.post("/api/sessions/a/play", headers=kid_h).status_code == 200

    fake.entry("kid")["group_ids"] = ["system-admin"]  # promoted in HA
    assert kid.put("/api/settings", headers=kid_h).status_code == 403  # the list is a minute old at most
    w.clock.now += 61
    assert kid.put("/api/settings", headers=kid_h).status_code == 200
    fake.entry("kid")["group_ids"] = ["system-read-only"]
    w.clock.now += 61
    assert kid.put("/api/settings", headers=kid_h).status_code == 403

    admin, admin_h, _ = sidebar(w, "synthesis")
    users = {u["username"]: u for u in admin.get("/api/users", headers=admin_h).json()["users"]}
    assert users["kid"]["ha"] is True and users["kid"]["role"] == "user"
    kid_id = users["kid"]["id"]
    r = admin.put(f"/api/users/{kid_id}", json={"role": "admin"}, headers=admin_h)
    assert r.status_code == 409 and r.json()["detail"] == "Set in Home Assistant"
    r = admin.post(f"/api/users/{kid_id}/password", json={"password": "a new password"}, headers=admin_h)
    assert r.status_code == 409


def test_ha_renamed_user_keeps_their_account(ha_world, fake, caplog):
    w = ha_world
    kid, kid_h, me = sidebar(w, "kid")
    link = kid.post("/api/link", headers=kid_h).json()
    token = w.client(base_url="https://testserver").post("/api/pair", json={"code": link["code"]}).json()["token"]
    fake.entry("kid")["username"] = "kiddo"
    fake.passwords["kiddo"] = fake.passwords.pop("kid")
    w.clock.now += 61
    assert w.client().get("/api/sessions", headers=H(token)).status_code == 200  # by HA's user id, not the name
    assert not any("switched off" in r.message for r in caplog.records)
    c = w.client(base_url="http://testserver:8099", client=("172.30.32.2", 1))
    me2 = c.get("/api/auth/me", headers={"X-Remote-User-Id": HA_IDS["kid"], "X-Remote-User-Name": "kiddo"}).json()
    assert me2["user"]["id"] == me["user"]["id"]
    https = w.client(base_url="https://testserver")
    r = https.post("/api/pair/login", json={"username": "kiddo", "password": "ha password 2"})
    assert r.status_code == 200 and r.json()["user"]["id"] == me["user"]["id"]


def test_ha_phone_follows_role_and_switch_off(ha_world, fake):
    w = ha_world
    kid, kid_h, _ = sidebar(w, "kid")
    link = kid.post("/api/link", headers=kid_h).json()
    assert link["username"] == "kid"
    token = w.client(base_url="https://testserver").post("/api/pair", json={"code": link["code"]}).json()["token"]
    phone = w.client()
    assert phone.get("/api/sessions", headers=H(token)).status_code == 200
    assert phone.put("/api/settings", headers=H(token)).status_code == 403
    fake.entry("kid")["group_ids"] = ["system-admin"]
    w.clock.now += 61
    assert phone.put("/api/settings", headers=H(token)).status_code == 200  # the phone's next request

    # As an admin, kid makes a key and signs in to a browser with HA's password
    r = kid.post("/api/keys", json={"name": "Bot", "expires": "90d", "abilities": ["ch.view"]}, headers=kid_h)
    key = r.json()["key"]
    assert w.client().get("/api/sessions", headers=H(key)).status_code == 200
    browser, _ = w.sign_in("kid", "ha password 2")
    assert browser.get("/api/sessions").status_code == 200

    fake.entry("kid")["is_active"] = False  # switched off in HA
    w.clock.now += 61
    r = phone.get("/api/sessions", headers=H(token))
    assert r.status_code == 401 and r.json()["pair"] is True
    assert browser.get("/api/sessions").status_code == 401  # signed out
    assert w.client().get("/api/sessions", headers=H(key)).status_code == 401
    assert w.client().post("/api/auth/login", json={"username": "kid", "password": "ha password 2"}).status_code == 401
    assert kid.get("/api/sessions", headers=kid_h).status_code == 403

    fake.entry("kid")["is_active"] = True  # switched on again: the phone works again
    w.clock.now += 61
    assert phone.get("/api/sessions", headers=H(token)).status_code == 200


def test_ha_removed_user_is_switched_off(ha_world, fake):
    w = ha_world
    kid, kid_h, _ = sidebar(w, "kid")
    link = kid.post("/api/link", headers=kid_h).json()
    token = w.client(base_url="https://testserver").post("/api/pair", json={"code": link["code"]}).json()["token"]
    fake.users = [u for u in fake.users if u["username"] != "kid"]
    assert len(fake.users) == 2
    w.clock.now += 61
    r = w.client().get("/api/sessions", headers=H(token))
    assert r.status_code == 401 and r.json()["pair"] is True


def test_ha_list_unavailable_keeps_what_is_known(ha_world, fake):
    w = ha_world
    kid, kid_h, _ = sidebar(w, "kid")
    fake.down = True
    w.clock.now += 61
    assert kid.post("/api/sessions/a/play", headers=kid_h).status_code == 200  # nobody switched off
    r = w.client(base_url="https://testserver").post("/api/pair/login",
                                                      json={"username": "kid", "password": "ha password 2"})
    assert r.status_code == 503  # HA can't check the password


def test_ha_credentials_sign_in_app_and_web(ha_world, fake, tmp_path):
    w = ha_world
    https = w.client(base_url="https://testserver")
    r = https.post("/api/pair/login", json={"username": "synthesis", "password": "ha password 1", "device_name": "Pixel"})
    assert r.status_code == 200 and r.json()["token"].startswith("sd_")
    assert r.json()["user"]["username"] == "synthesis" and r.json()["user"]["role"] == "admin"
    token = r.json()["token"]
    assert w.client().put("/api/settings", headers=H(token)).status_code == 200
    # The same Sonorium user when HA's sidebar shows the id later
    admin, admin_h, me = sidebar(w, "synthesis")
    assert me["user"]["id"] == r.json()["user"]["id"]
    assert len(admin.get("/api/users", headers=admin_h).json()["users"]) == 1

    c = w.client()
    assert c.post("/api/auth/login", json={"username": "Synthesis", "password": "ha password 1"}).status_code == 200
    assert c.get("/api/auth/me").json()["user"]["username"] == "synthesis"
    assert c.get("/api/settings").status_code == 200

    stored = (tmp_path / "users.json").read_text() + (tmp_path / "security.json").read_text()
    assert "ha password" not in stored
    codes = [https.post("/api/pair/login", json={"username": "kid", "password": "wrong"}).status_code
             for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429  # the same limits


def test_ha_user_wins_over_a_sonorium_only_user(tmp_path, monkeypatch, fake, caplog):
    from argon2 import PasswordHasher
    hasher = PasswordHasher()
    (tmp_path / "users.json").write_text(json.dumps({"require_login": True, "ha_users": 1, "users": {
        "u_kid": {"username": "kid", "role": "admin", "password": hasher.hash("sonorium password"), "ha_user_id": None},
        "u_dana": {"username": "dana", "role": "user", "password": hasher.hash("dana password"), "ha_user_id": None},
    }}))
    w = World(tmp_path, monkeypatch, ha=fake)
    c = w.client()
    with caplog.at_level("WARNING"):
        assert c.post("/api/auth/login", json={"username": "kid", "password": "sonorium password"}).status_code == 401
        assert c.post("/api/auth/login", json={"username": "kid", "password": "ha password 2"}).status_code == 200
    me = c.get("/api/auth/me").json()
    assert me["user"]["ha"] is True and me["user"]["role"] == "user" and me["user"]["username"] == "kid-2"
    assert sum("both a Home Assistant user and a Sonorium user" in r.message for r in caplog.records) == 1
    dana, _ = w.sign_in("dana", "dana password")  # Sonorium-only users keep working
    assert dana.get("/api/auth/me").json()["user"]["ha"] is False
    admin, admin_h, _ = sidebar(w, "synthesis")
    r = admin.post("/api/users", json={"username": "synthesis2", "password": "long enough", "role": "user"},
                   headers=admin_h)
    assert r.status_code == 200
    r = admin.post("/api/users", json={"username": "kid", "password": "long enough", "role": "user"}, headers=admin_h)
    assert r.status_code == 409  # HA's name


def test_addon_require_login_needs_no_password(tmp_path, monkeypatch, fake):
    w = World(tmp_path, monkeypatch, existing=True, ha=fake)
    admin, admin_h, me = sidebar(w, "synthesis")
    assert me["require_login"] is False
    r = admin.put("/api/auth/require-login", json={"on": True}, headers=admin_h)
    assert r.status_code == 200 and r.json()["require_login"] is True
    direct = w.client()
    assert direct.get("/api/sessions").status_code == 401
    me = direct.get("/api/auth/me").json()
    assert me["needs_setup"] is False and me["ha_users"] is True
    assert direct.post("/api/auth/setup", json={"username": "x", "password": "long enough"}).status_code == 409
    # web_ui_only_through_ha: no password needed either
    monkeypatch.setenv("SONORIUM__WEB_UI_ONLY_THROUGH_HA", "true")
    assert direct.get("/").status_code == 404
    assert admin.get("/", headers=admin_h).status_code == 200


def test_docker_still_needs_an_admin_password(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, existing=True)
    accounts = w.service.plugin_state["users"]
    assert accounts.ha is None
    accounts.ha_user("abc", "chris")
    with pytest.raises(Exception):
        accounts.set_require_login(True)


def test_ha_migration(tmp_path, monkeypatch, fake):
    from argon2 import PasswordHasher
    hasher = PasswordHasher()
    (tmp_path / "state.json").write_text("{}")
    (tmp_path / "users.json").write_text(json.dumps({"require_login": False, "users": {
        # made automatically for HA's sidebar before (as Admins); synthesis had set a Sonorium password
        "u_syn": {"username": "synthesis", "role": "admin", "password": hasher.hash("old sonorium password"),
                  "ha_user_id": HA_IDS["synthesis"]},
        "u_kid": {"username": "kid", "role": "admin", "password": None, "ha_user_id": HA_IDS["kid"]},
        "u_dana": {"username": "dana", "role": "user", "password": hasher.hash("dana password"), "ha_user_id": None},
    }}))
    w = World(tmp_path, monkeypatch, ha=fake)
    data = json.loads((tmp_path / "users.json").read_text())
    assert data["ha_users"] == 1
    assert data["users"]["u_syn"]["ha"] is True and data["users"]["u_syn"]["password"] is None
    assert data["users"]["u_kid"]["ha"] is True and "ha" not in data["users"]["u_dana"]
    w.client().get("/api/info")  # any request: HA's list arrives
    data = json.loads((tmp_path / "users.json").read_text())
    assert data["users"]["u_syn"]["role"] == "admin" and data["users"]["u_kid"]["role"] == "user"
    _, _, me = sidebar(w, "synthesis")
    assert me["user"]["id"] == "u_syn" and me["user"]["role"] == "admin"
    c = w.client()
    assert c.post("/api/auth/login", json={"username": "synthesis", "password": "old sonorium password"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "synthesis", "password": "ha password 1"}).status_code == 200
    assert c.get("/api/auth/me").json()["user"]["id"] == "u_syn"
    w.sign_in("dana", "dana password")
