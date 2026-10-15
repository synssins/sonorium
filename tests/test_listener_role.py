"""
The Listener role and scopes (sonorium/auth/permissions.py ROLES and SCOPES,
auth/scopes.py), through the real middleware, Users and Keys: what a Listener
may do (every route of the permission table), its channel scope (a hidden
channel is missing and answers 404), one account signed in many times at once,
a shared password nobody signed in to it can change, and per-address counting
of a shared account's event streams.
"""
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("argon2")
pytest.importorskip("cryptography")
pytest.importorskip("segno")

from playback_helpers import addon_modules  # noqa: E402
import test_auth  # noqa: E402

m = test_auth.m
H = test_auth.H
LISTENER = "listener"
MINE, OTHER = "ch1", "ch2"
# What a Listener may do, and nothing else
LISTENER_PERMS = {"ch.view", "ch.play", "ch.select", "pl.run", "ev.read"}


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.auth.service", "sonorium.auth.middleware", "sonorium.auth.routes",
                       "sonorium.auth.permissions", "sonorium.auth.config", "sonorium.auth.qr", "sonorium.auth.announce",
                       "sonorium.auth.tokens", "sonorium.auth.keys", "sonorium.auth.users", "sonorium.auth.scopes",
                       "sonorium.auth.accounts", "sonorium.auth.passwords", "sonorium.plugins.loader",
                       "sonorium.runtime", "sonorium.auth.principal") as mods:
        m.__dict__.update(mods.__dict__)
        yield


def fill(value):
    return {"session_id": MINE}.get(value, "x")


def concrete(path: str) -> str:
    """A path template with values: {session_id} is the Listener's channel."""
    import re
    path = path.replace("{channel_id:int}", "1")
    return re.sub(r"\{([a-z_]+)(:[a-z]+)?\}", lambda g: fill(g.group(1)), path)


class World(test_auth.World):
    """test_auth's World with a stand-in handler for every route of the core's permission table."""

    def __init__(self, tmp_path, monkeypatch):
        from fastapi import Request
        super().__init__(tmp_path, monkeypatch)
        taken = {(method, getattr(r, "path", "")) for r in self.app.routes for method in getattr(r, "methods", ())}

        def record(name):
            async def handler(request: Request):
                return {"ok": name}
            return handler

        for (method, path) in m.permissions.ROUTES:
            if (method, path) not in taken:
                self.app.add_api_route(path, record(f"{method} {path}"), methods=[method])

    def listener(self, admin, csrf, username="demo", channels=None):
        body = {"username": username, "password": "listener password", "role": LISTENER}
        if channels is not None:
            body["scopes"] = {"channels": channels}
        r = admin.post("/api/users", json=body, headers=H(None, csrf))
        assert r.status_code == 200, r.text
        return r.json()["user"]


@pytest.fixture
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def call(c, method, path, csrf):
    return c.request(method, path, headers=H(None, csrf), json={})


# --- The role --------------------------------------------------------------------------------

def test_the_listener_role_is_data(w):
    P = m.permissions
    assert set(P.ROLES[LISTENER]["perms"]) == LISTENER_PERMS
    assert P.ROLES[LISTENER]["scopes"] == ("channels",) and P.ROLES[LISTENER]["shared"] is True
    assert m.accounts.ROLES == ("admin", "user", LISTENER)
    assert m.accounts.ROLE_LABELS[LISTENER] == "Listener"
    # role-only abilities never go on a key
    assert not (P.ROLE_ONLY & set(P.clean_abilities(P.ROLE_ONLY)))
    assert P.ROLE_ONLY <= P.NEVER_FOR_KEYS


def test_listener_may_exactly_what_the_table_allows(w):
    admin, csrf = w.setup_admin()
    w.listener(admin, csrf)
    c, lcsrf = w.sign_in("demo", "listener password")
    me = c.get("/api/auth/me").json()
    assert set(me["perms"]) == LISTENER_PERMS and me["scopes"] == {}
    perms = frozenset(LISTENER_PERMS)
    allowed, refused = [], []
    for (method, path), perm in m.permissions.ROUTES.items():
        if perm.public or perm.page or path.startswith("/stream/"):
            continue
        if perm.legacy and not perm.allows(perms):  # the route checks the plugin framework's own token
            continue
        status = call(c, method, concrete(path), lcsrf).status_code
        if perm.allows(perms):
            assert status == 200, (method, path, status)
            allowed.append((method, path))
        else:
            assert status == 403, (method, path, status)
            refused.append((method, path))
    # what a Listener needs: see channels, play and stop, choose theme / preset / sequence / playlist, listen
    for need in [("GET", "/api/sessions"), ("GET", "/api/sessions/{session_id}"),
                 ("POST", "/api/sessions/{session_id}/play"), ("POST", "/api/sessions/{session_id}/stop"),
                 ("PUT", "/api/sessions/{session_id}"),
                 ("POST", "/api/sessions/{session_id}/sequence"), ("DELETE", "/api/sessions/{session_id}/sequence"),
                 ("PUT", "/api/sessions/{session_id}/playlist"), ("DELETE", "/api/sessions/{session_id}/playlist"),
                 ("POST", "/api/sessions/{session_id}/listen"), ("DELETE", "/api/sessions/{session_id}/listen"),
                 ("GET", "/api/me/channel"), ("PUT", "/api/me/channel"),
                 ("POST", "/api/me/channel/listen"), ("DELETE", "/api/me/channel/listen"),
                 ("GET", "/api/themes"), ("GET", "/api/themes/{theme_id}/presets"),
                 ("GET", "/api/themes/{theme_id}/sequences"), ("GET", "/api/playlists"),
                 ("GET", "/api/sessions/{session_id}/playback"), ("GET", "/api/stream/url")]:
        assert need in allowed, need
    # nothing that edits, imports, exports, uploads or shows logs
    for edit in [("POST", "/api/sessions"), ("DELETE", "/api/sessions/{session_id}"),
                 ("POST", "/api/sessions/{session_id}/volume"), ("PUT", "/api/sessions/{session_id}/speakers-muted"),
                 ("POST", "/api/sessions/{session_id}/audio-outputs"),
                 ("PUT", "/api/themes/{theme_id}/presets/{preset_id}"), ("POST", "/api/themes/{theme_id}/presets"),
                 ("POST", "/api/themes/create"), ("POST", "/api/themes/import"), ("GET", "/api/themes/{theme_id}/export"),
                 ("GET", "/api/themes/{theme_id}/presets/{preset_id}/export"), ("POST", "/api/themes/{theme_id}/upload"),
                 ("GET", "/api/themes/{theme_id}/tracks/{track_name:path}/audio"), ("GET", "/api/themes/packages"),
                 ("PUT", "/api/themes/{theme_id}/sequences/{sequence_id}"), ("POST", "/api/playlists"),
                 ("PUT", "/api/playlists/{playlist_id}"), ("PUT", "/api/speakers/{speaker_id}/settings"),
                 ("GET", "/api/speakers"), ("PUT", "/api/settings"), ("GET", "/api/logs"), ("GET", "/api/logs/download"),
                 ("POST", "/api/plugins/upload"), ("GET", "/api/connection"), ("POST", "/api/link"),
                 ("PUT", "/api/me/backups/{device_id}/themes/{key}"), ("PUT", "/api/me/backups/settings")]:
        assert edit in refused, edit
    # Users and Keys (their own tables)
    for method, path in [("GET", "/api/users"), ("POST", "/api/users"), ("GET", "/api/keys"), ("POST", "/api/keys"),
                         ("PUT", "/api/zones/switch"), ("POST", "/api/links"), ("PUT", "/api/auth/require-login")]:
        assert call(c, method, path, lcsrf).status_code == 403, path


def test_listener_role_can_be_set_and_changed(w):
    admin, csrf = w.setup_admin()
    user = w.listener(admin, csrf)
    assert user["role"] == LISTENER and user["role_label"] == "Listener"
    listed = admin.get("/api/users").json()
    assert [r["id"] for r in listed["roles"]] == ["admin", "user", LISTENER]
    assert next(r for r in listed["roles"] if r["id"] == LISTENER)["scopes"] == ["channels"]
    r = admin.put(f"/api/users/{user['id']}", json={"role": "user"}, headers=H(None, csrf))
    assert r.json()["user"]["role"] == "user"
    # a User has no channels scope
    r = admin.put(f"/api/users/{user['id']}", json={"scopes": {"channels": [MINE]}}, headers=H(None, csrf))
    assert r.status_code == 409
    admin.put(f"/api/users/{user['id']}", json={"role": LISTENER}, headers=H(None, csrf))
    assert admin.put(f"/api/users/{user['id']}", json={"scopes": {"nope": []}}, headers=H(None, csrf)).status_code == 400


# --- One account, many people ------------------------------------------------------------------

def test_one_account_signed_in_many_times_at_once(w):
    admin, csrf = w.setup_admin()
    w.listener(admin, csrf)
    clients = [w.sign_in("demo", "listener password") for _ in range(12)]
    for c, lcsrf in clients:  # nobody signing in signed the others out
        assert c.get("/api/auth/me").json()["kind"] == "session"
        assert c.post(f"/api/sessions/{MINE}/listen", headers=H(None, lcsrf), json={}).status_code == 200
    user_id = clients[0][0].get("/api/auth/me").json()["user"]["id"]
    assert len(w.service.plugin_state["users"].sessions(user_id)) == 12


def test_a_shared_password_stays(w):
    admin, csrf = w.setup_admin()
    w.listener(admin, csrf)
    c, lcsrf = w.sign_in("demo", "listener password")
    r = c.post("/api/auth/new-password", json={"password": "a new password 1"}, headers=H(None, lcsrf))
    assert r.status_code == 403
    assert w.sign_in("demo", "listener password")  # still works
    # the app signs in with the password; its device can't show a one-time sign-in code (a password reset)
    w.service.https_only = lambda request: None
    linked = w.client().post("/api/pair/login", json={"username": "demo", "password": "listener password",
                                                       "device_name": "Pixel"})
    assert linked.status_code == 200, linked.text
    token = linked.json()["token"]
    d = w.client()
    assert d.get("/api/sessions", headers=H(token)).status_code == 200
    assert d.post("/api/devices/recovery-code", headers=H(token)).status_code == 403
    assert d.get("/api/themes/packages", headers=H(token)).status_code == 403
    assert d.put("/api/me/backups/self/playlists", headers=H(token), json={}).status_code == 403


def test_a_shared_accounts_event_streams_count_per_address(w):
    from sonorium.web import plugin_framework_api as pf
    P = m.principal.Principal
    listener = P("session", user_id="u1", role=LISTENER)
    user = P("session", user_id="u2", role="user")

    def request(principal, host):
        return SimpleNamespace(state=SimpleNamespace(auth=principal), client=SimpleNamespace(host=host))
    assert pf.event_caller_key(request(listener, "192.0.2.1")) != pf.event_caller_key(request(listener, "192.0.2.2"))
    assert pf.event_caller_key(request(user, "192.0.2.1")) == pf.event_caller_key(request(user, "192.0.2.2"))


# --- Scopes: only some channels ------------------------------------------------------------------

def test_a_listener_for_some_channels_doesnt_see_the_others(w):
    admin, csrf = w.setup_admin()
    w.listener(admin, csrf, "lobby", channels=[MINE])
    c, lcsrf = w.sign_in("lobby", "listener password")
    assert c.get("/api/auth/me").json()["scopes"] == {"channels": [MINE]}
    for (method, path) in m.permissions.ROUTES:
        if "{session_id}" not in path or not m.permissions.ROUTES[(method, path)].allows(frozenset(LISTENER_PERMS)):
            continue
        assert call(c, method, path.replace("{session_id}", MINE), lcsrf).status_code == 200, path
        # someone else's channel doesn't exist for it: 404, not 403
        r = call(c, method, path.replace("{session_id}", OTHER), lcsrf)
        assert r.status_code == 404, (method, path, r.status_code)
    assert call(c, "POST", "/api/sessions/stop-all", lcsrf).status_code == 403  # would reach the others
    # the admin sets it back to every channel
    user_id = c.get("/api/auth/me").json()["user"]["id"]
    admin.put(f"/api/users/{user_id}", json={"scopes": {"channels": None}}, headers=H(None, csrf))
    assert call(c, "POST", f"/api/sessions/{OTHER}/play", lcsrf).status_code == 200
    assert c.get("/api/auth/me").json()["scopes"] == {}


def test_a_key_for_some_channels_gets_404_for_the_others(w):
    admin, csrf = w.setup_admin()
    key = test_auth.make_key(admin, csrf, abilities=["ch.view", "ch.play", "pl.run"], channels=[MINE])
    k = w.client()
    assert k.get(f"/api/sessions/{MINE}", headers=H(key["key"])).status_code == 200
    assert k.get(f"/api/sessions/{OTHER}", headers=H(key["key"])).status_code == 404
    # a route of an ability outside the channel abilities is scoped too
    assert k.post(f"/api/sessions/{OTHER}/sequence", headers=H(key["key"]), json={}).status_code == 404


def test_a_hidden_channels_stream_answers_404(w):
    admin, csrf = w.setup_admin()
    w.listener(admin, csrf, "lobby", channels=[MINE])
    c, _ = w.sign_in("lobby", "listener password")
    m.scopes.bind_streams(lambda path: {"/stream/channel1": MINE, "/stream/channel2": OTHER}.get(path))
    try:
        assert c.get("/stream/channel1").status_code == 200
        assert c.get("/stream/channel2").status_code == 404
        assert w.client().get("/stream/channel2").status_code == 200  # a speaker (no sign-in, the LAN)
    finally:
        m.scopes.bind_streams(None)


def test_events_of_hidden_channels_arent_sent(w):
    from sonorium.web import plugin_framework_api as pf
    P = m.principal.Principal
    lobby = P("session", perms=frozenset(LISTENER_PERMS), channels=[MINE])
    ev = lambda type_, data: SimpleNamespace(type=type_, data=data)  # noqa: E731
    assert pf.event_visible(lobby, ev("channel.started", {"channel_id": MINE}))
    assert not pf.event_visible(lobby, ev("channel.started", {"channel_id": OTHER}))
    assert not pf.event_visible(lobby, ev("link.created", {"link_id": "l", "channels": [MINE, OTHER]}))
    assert pf.event_visible(lobby, ev("channel.changed", {"channel_id": "p1", "personal": True}))  # its My Channel
    assert not pf.event_visible(lobby, ev("theme.created", {"theme_id": "t"}))  # no th.view


def test_the_zones_view_without_hidden_channels(w):
    P = m.principal.Principal
    view = {"on": True, "zones": {MINE: {"owner": None}, OTHER: {"owner": None}},
            "speakers": {"s1": {"home": OTHER, "channels": [OTHER, MINE], "in_use": OTHER},
                         "s2": {"home": OTHER, "channels": [OTHER], "in_use": None}},
            "links": [{"id": "l1", "channels": [MINE, OTHER], "left": [OTHER]}, {"id": "l2", "channels": [OTHER], "left": []}]}
    out = m.users.scoped_zones(view, P("session", channels=[MINE]))
    assert list(out["zones"]) == [MINE]
    assert out["speakers"] == {"s1": {"home": MINE, "channels": [MINE], "in_use": None}}
    assert out["links"] == [{"id": "l1", "channels": [MINE], "left": []}]
    assert m.users.scoped_zones(view, P("session")) is view


def test_listings_follow_the_callers_scope(w):
    P = m.principal.Principal
    items = [SimpleNamespace(id=MINE), SimpleNamespace(id=OTHER)]
    assert [i.id for i in m.scopes.visible_channels(items, principal=P("session", channels=[MINE]))] == [MINE]
    assert len(m.scopes.visible_channels(items, principal=P("session"))) == 2
    token = m.principal.CALLER.set(P("key", channels=[OTHER]))
    try:
        assert [i.id for i in m.scopes.visible_channels(items)] == [OTHER]
        assert m.scopes.channel_visible(OTHER) and not m.scopes.channel_visible(MINE)
    finally:
        m.principal.CALLER.reset(token)
