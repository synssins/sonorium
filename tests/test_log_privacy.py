"""The Logs page: sign-in and linking messages, and addresses, only for admins (logbuffer.redacted)."""

import asyncio
import importlib.util
import io
import logging
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium"


def _exec(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _record(name, level, msg, **extra):
    record = logging.LogRecord(name, level, __file__, 1, msg, None, None)
    record.__dict__.update(extra)
    return record


@pytest.fixture
def lb():
    spec = importlib.util.spec_from_file_location("logbuffer_privacy_test", PACKAGE / "logbuffer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_auth_messages_are_private_and_addresses_hidden(lb):
    logging.getLogger("sonorium").setLevel(logging.INFO)
    buf = lb.LogBuffer("sonorium")
    buf.handle(_record("sonorium.auth", logging.INFO, "Users: alice signed in from 203.0.113.5"))
    buf.handle(_record("sonorium.auth", logging.WARNING, "Sign-in with a one-time code failed for 'bob' from 10.0.0.9"))
    buf.handle(_record("sonorium", logging.INFO, "Link App: Pixel at 192.168.1.20 is linked", private=True))
    buf.handle(_record("sonorium", logging.INFO, "  Cast: Playing on media_player.hub (192.168.10.249)"))
    buf.handle(_record("sonorium", logging.WARNING, "Could not reach [fe80::1%eth0]:8009 or 2001:db8::5."))
    buf.handle(_record("sonorium", logging.INFO, "Channel 1 started at 10:15:30, version 1.7.3"))
    everything = buf.entries()
    assert len(everything) == 6  # admins: all of them, as written
    assert "203.0.113.5" in everything[0]["message"]

    shown = lb.redacted(everything)
    text = lb.as_text(shown)
    assert [e["message"] for e in shown] == [
        "  Cast: Playing on media_player.hub ([address])",
        "Could not reach [[address]]:8009 or [address].",
        "Channel 1 started at 10:15:30, version 1.7.3",
    ]
    for hidden in ("alice", "bob", "Pixel", "signed in", "203.0.113.5", "192.168", "fe80", "2001:db8"):
        assert hidden not in text
    # the seq numbers stay the buffer's, so polling with after= keeps working
    assert [e["seq"] for e in shown] == [everything[3]["seq"], everything[4]["seq"], everything[5]["seq"]]


def test_hide_addresses_leaves_other_text(lb):
    assert lb.hide_addresses("mac aa:bb:cc:dd:ee:ff, 999.1.1.1, 12:30:00, ::ffff:10.1.2.3 ok") == \
        "mac aa:bb:cc:dd:ee:ff, 999.1.1.1, 12:30:00, [address] ok"
    assert lb.hide_addresses("http://192.168.10.249:8008/stream/1") == "http://[address]:8008/stream/1"


def test_stdout_keeps_the_full_lines():
    """The auth logger passes its messages up to "sonorium"'s stdout handler unchanged (docker logs)."""
    parent = logging.getLogger("sonorium_privacy_stdout")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    parent.addHandler(handler)
    parent.setLevel(logging.INFO)
    try:
        parent.getChild("auth").info("Users: alice signed in from 203.0.113.5")
    finally:
        parent.removeHandler(handler)
    assert "alice signed in from 203.0.113.5" in stream.getvalue()


def test_every_auth_module_logs_as_sonorium_auth():
    for path in sorted((PACKAGE / "auth").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "logger." in text:
            assert 'getChild("auth")' in text, path.name


@pytest.fixture
def web():
    """api.py's WebApp with the real log buffer and principal; Sonorium's logger, themes and version stubbed."""
    package = types.ModuleType("sonorium")
    package.__path__ = [str(PACKAGE)]
    obs = types.ModuleType("sonorium.obs")
    obs.logger = logging.getLogger("sonorium-privacy-test")
    obs.logger.instrument = lambda *a, **k: (lambda f: f)
    theme = types.ModuleType("sonorium.theme")
    theme.LoadedTheme = object
    version = types.ModuleType("sonorium.version")
    version.__version__ = "test"
    stubs = {"sonorium": package, "sonorium.obs": obs, "sonorium.theme": theme, "sonorium.version": version}
    for name in ("core", "web", "ha", "auth"):
        sub = types.ModuleType(f"sonorium.{name}")
        sub.__path__ = [str(PACKAGE / name)]
        stubs[f"sonorium.{name}"] = sub
    with patch.dict(sys.modules, stubs):
        package.runtime = _exec("sonorium.runtime", PACKAGE / "runtime.py")
        logbuffer = _exec("sonorium.logbuffer", PACKAGE / "logbuffer.py")
        package.logbuffer = logbuffer
        principal = _exec("sonorium.auth.principal", PACKAGE / "auth" / "principal.py")
        api = _exec("sonorium.api", PACKAGE / "api.py")
        app = api.WebApp(MagicMock())  # before the buffer is set: nothing attached to uvicorn's logger
        buf = logbuffer.LogBuffer("sonorium")
        logging.getLogger("sonorium").setLevel(logging.INFO)
        for record in (_record("sonorium.auth", logging.INFO, "Users: alice signed in from 203.0.113.5"),
                       _record("sonorium.auth", logging.WARNING, "Link App: a device at 198.51.100.7 tried a wrong or expired code"),
                       _record("sonorium", logging.INFO, "Theme 'Rain' loaded"),
                       _record("sonorium", logging.WARNING, "  Cast: Failed to connect to 192.168.10.249: timeout")):
            buf.handle(record)
        logbuffer._buffer = buf
        yield types.SimpleNamespace(app=app, P=principal)


def _as(web, principal, call):
    token = web.P.CALLER.set(principal)
    try:
        return asyncio.run(call())
    finally:
        web.P.CALLER.reset(token)


USER = dict(kind="session", perms=frozenset({"st.view", "ch.view"}), user_id="u2", role="user")


@pytest.mark.parametrize("who", ["user", "key", "open", "nobody"])
def test_someone_not_an_admin_sees_no_sign_ins_and_no_addresses(web, who):
    principal = {"user": lambda: web.P.Principal(**USER),
                 "key": lambda: web.P.Principal("key", frozenset({"st.view"})),
                 "open": lambda: web.P.Principal("open", frozenset({"st.view", "admin.plugins"})),
                 "nobody": lambda: None}[who]()
    data = _as(web, principal, web.app.get_logs)
    messages = [e["message"] for e in data["entries"]]
    assert messages == ["Theme 'Rain' loaded", "  Cast: Failed to connect to [address]: timeout"]
    download = _as(web, principal, web.app.download_logs).body.decode()
    for hidden in ("alice", "signed in", "Link App", "203.0.113.5", "198.51.100.7", "192.168.10.249"):
        assert hidden not in download
    assert "Theme 'Rain' loaded" in download and "Failed to connect to [address]" in download


@pytest.mark.parametrize("who", ["admin", "admin_device", "local"])
def test_an_admin_sees_everything(web, who):
    P = web.P
    principal = {"admin": lambda: P.Principal("session", frozenset({"st.view", "admin.users"}), user_id="u1", role="admin"),
                 # a linked device never has admin.users; its user's role still counts
                 "admin_device": lambda: P.Principal("device", frozenset({"st.view", "device"}), user_id="u1",
                                                     role="admin", device_id="d1"),
                 "local": lambda: P.Principal("local", frozenset({"st.view", "admin.users"}))}[who]()
    data = _as(web, principal, web.app.get_logs)
    text = " ".join(e["message"] for e in data["entries"])
    assert "alice signed in from 203.0.113.5" in text
    assert "198.51.100.7" in text and "192.168.10.249" in text
    download = _as(web, principal, web.app.download_logs).body.decode()
    assert "alice signed in from 203.0.113.5" in download and "192.168.10.249" in download
