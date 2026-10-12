"""
The console credential (sonorium/auth/console.py): SONORIUM_CONSOLE_TOKEN_FILE
makes an "sc_" token for the host's console, accepted only from this computer,
with an admin's rights, never listed or revocable through the API.
"""
import json
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("argon2")
pytest.importorskip("cryptography")
pytest.importorskip("segno")
from test_auth import H, World, m, modules  # noqa: E402,F401  (modules: the auth modules, autouse)


def console_module():
    return sys.modules["sonorium.auth.console"]


@pytest.fixture
def cw(tmp_path, monkeypatch):
    """Sign-in with accounts and Keys, an admin, and a console credential."""
    token_file = tmp_path / "host" / "console.token"
    monkeypatch.setenv("SONORIUM_CONSOLE_TOKEN_FILE", str(token_file))
    w = World(tmp_path, monkeypatch)
    w.admin, w.csrf = w.setup_admin()
    w.token_file = token_file
    w.token_id = console_module().ensure(w.service)
    w.token = token_file.read_text(encoding="utf-8")
    w.local = lambda host="127.0.0.1": w.client(client=(host, 50000))
    return w


def test_console_token_file_is_one_line_and_only_its_hash_is_kept(cw, tmp_path):
    raw = cw.token_file.read_bytes()
    assert raw.startswith(b"sc_") and b"\n" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    security = (tmp_path / "security.json").read_text()
    assert cw.token not in security
    record = cw.service.tokens.get(cw.token_id)
    assert record["kind"] == "console" and record["hash"] in security


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_console_accepted_from_loopback_as_admin(cw, address):
    c = cw.local(address)
    assert c.get("/api/sessions", headers=H(cw.token)).json()["kind"] == "console"
    assert c.post("/api/sessions/a/play", headers=H(cw.token)).status_code == 200  # no CSRF needed
    assert c.get("/api/users", headers=H(cw.token)).status_code == 200            # admin-only routes
    assert c.get("/api/keys", headers=H(cw.token)).status_code == 200
    me = c.get("/api/auth/me", headers=H(cw.token)).json()
    assert me["kind"] == "console" and "admin.users" in me["perms"]


@pytest.mark.parametrize("address", ["192.168.1.66", "10.0.0.2", "172.30.32.2", "fe80::1"])
def test_console_refused_from_another_computer(cw, address):
    c = cw.client(client=(address, 50000))
    r = c.get("/api/sessions", headers=H(cw.token))
    assert r.status_code == 401


def test_console_refused_through_forwarded_headers(cw):
    c = cw.client(client=("192.168.1.66", 50000))
    headers = {**H(cw.token), "X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"}
    assert c.get("/api/sessions", headers=headers).status_code == 401


def test_console_never_on_the_ingress_port(cw, monkeypatch):
    monkeypatch.setattr(m.runtime, "STANDALONE", False)  # the add-on's ingress port (8099)
    local = cw.client(base_url="http://testserver:8099", client=("127.0.0.1", 50000))
    assert local.get("/api/sessions", headers=H(cw.token)).status_code == 403  # only the Supervisor
    sidebar = cw.client(base_url="http://testserver:8099", client=("172.30.32.2", 50000))
    assert sidebar.get("/api/sessions", headers=H(cw.token)).json()["kind"] == "ingress"


def test_console_refused_without_the_variable(cw, monkeypatch):
    monkeypatch.delenv("SONORIUM_CONSOLE_TOKEN_FILE")
    assert cw.local().get("/api/sessions", headers=H(cw.token)).status_code == 401


def test_console_wrong_token_refused(cw):
    assert cw.local().get("/api/sessions", headers=H("sc_" + "x" * 43)).status_code == 401


def test_console_never_listed_or_revocable(cw):
    c, h = cw.admin, H(None, cw.csrf)
    keys = c.get("/api/keys").json()
    assert cw.token_id not in json.dumps(keys)
    assert cw.token_id not in json.dumps(c.get("/api/devices").json())
    assert cw.token_id not in json.dumps(c.get("/api/users").json())
    assert c.delete(f"/api/keys/{cw.token_id}", headers=h).status_code == 404
    assert c.delete(f"/api/devices/{cw.token_id}", headers=h).status_code == 404
    assert cw.local().get("/api/sessions", headers=H(cw.token)).json()["kind"] == "console"


def test_console_not_affected_by_switching_keys_off(cw):
    m.keys.set_enabled(cw.service, False)  # Settings > Advanced > Keys off
    assert cw.local().get("/api/sessions", headers=H(cw.token)).json()["kind"] == "console"


def test_console_token_rotation(cw):
    console = console_module()
    # Same file, same stored hash: kept
    assert console.ensure(cw.service) == cw.token_id
    assert cw.token_file.read_text(encoding="utf-8") == cw.token
    # File deleted: a new one, the old one stops working
    cw.token_file.unlink()
    new_id = console.ensure(cw.service)
    new = cw.token_file.read_text(encoding="utf-8")
    assert new != cw.token and new_id != cw.token_id
    assert cw.local().get("/api/sessions", headers=H(cw.token)).status_code == 401
    assert cw.local().get("/api/sessions", headers=H(new)).json()["kind"] == "console"
    # A file whose token doesn't match the stored hash: replaced too
    cw.token_file.write_text("sc_not-the-one", encoding="utf-8")
    console.ensure(cw.service)
    assert cw.token_file.read_text(encoding="utf-8") not in ("sc_not-the-one", new)
    assert [i for i, _ in cw.service.tokens.list("console")] != [new_id]
    assert len(list(cw.service.tokens.list("console"))) == 1
    assert not list(cw.token_file.parent.glob("*.tmp"))


def test_console_ensure_does_nothing_without_the_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("SONORIUM_CONSOLE_TOKEN_FILE", raising=False)
    w = World(tmp_path, monkeypatch, users=False)
    assert console_module().ensure(w.service) is None
    assert not list(w.service.tokens.list("console"))
