"""
Sign-in (sonorium/auth) with the built-in Users and Integration plugins:
passwords, browser sessions and CSRF, roles on a sample of routes per area,
Link App pairing (single use, expiry, HTTPS), one-time sign-in codes, keys
(abilities, channels, changes in flight, expiry, revoking), the bypass, HA
ingress trust, web_ui_only_through_ha, upgrades (Require login off) and a
switched-off or missing sign-in plugin.
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
                       "sonorium.auth.permissions", "sonorium.auth.config", "sonorium.auth.qr",
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
    def __init__(self, tmp_path, monkeypatch, *, plugins=("users", "integration"), existing=False):
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient
        monkeypatch.setenv("SONORIUM_CONFIG_DIR", str(tmp_path))
        for name in ("BYPASS_LOGIN", "SONORIUM__BYPASS_LOGIN", "SONORIUM__WEB_UI_ONLY_THROUGH_HA"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(m.runtime, "STANDALONE", True)
        if existing:
            (tmp_path / "state.json").write_text("{}")
        self.clock = Clock()
        self.service = s = m.service.AuthService(tmp_path, clock=self.clock)
        self.app = app = FastAPI()
        s.app = app
        app.add_middleware(m.middleware.AuthMiddleware, service=s)
        app.include_router(m.routes.create_core_auth_router(
            s, stream_url=lambda: "http://192.168.1.5:8008", features=lambda: {}, version="test",
            https_host_port=lambda: 8808))
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
    assert uri.startswith("sonorium://pair?v=1&id=") and "&h=192.168.1.5&p=8008&s=8808&fp=" in uri
    assert f"&c={link['code']}&u=chris" in uri and link["expires_in"] == 300 and len(link["words"]) == 4
    assert "<svg" in link["qr_svg"] and "data:image/png;base64," in link["qr_svg"]
    assert r.status_code == 200 and r.json()["token"].startswith("sd_") and r.json()["user"]["username"] == "chris"
    again = w.client(base_url="https://testserver").post("/api/pair", json={"code": link["code"]})
    assert again.status_code == 401


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
    users = c.get("/api/users").json()["users"]
    assert [d["name"] for d in users[0]["devices"] if d["kind"] == "phone"] == ["Pixel 8"]
    assert c.delete(f"/api/devices/{device_id}", headers=H(None, csrf)).status_code == 200
    r = phone.get("/api/sessions", headers=H(token))
    assert r.status_code == 401 and r.json()["pair"] is True


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


def test_info_is_public(w):
    info = w.client().get("/api/info").json()
    assert info["api"] == 1 and info["ports"] == {"http": 8008, "https": 8808} and len(info["fingerprint"]) == 64
    assert info["login_required"] is True and set(info["plugins"]) == {"users", "integration"}
