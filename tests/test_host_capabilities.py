"""
What a host (Docker, the HA app, a Windows service...) switches on with
environment variables (sonorium/host.py, docs/HOSTING.md): restart by exit code
75 (SONORIUM_SUPERVISED) and supervise.sh, the secret helper
(SONORIUM_SECRET_HELPER), the console credential (SONORIUM_CONSOLE_TOKEN_FILE),
SONORIUM_HTTP_PORT, SONORIUM_BUNDLED_THEMES, switching speaker discovery off
through the API, and state.json written atomically.
"""
import asyncio
import importlib.util
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("fastapi")  # api.py needs it (test_connection_remove's app fixture)

from test_connection_remove import app  # noqa: E402,F401  (fixture: standalone runtime + api module)

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "sonorium_addon" / "sonorium"
SUPERVISE = REPO / "sonorium_addon" / "supervise.sh"


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, PACKAGE / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- Restart: SONORIUM_SUPERVISED and exit code 75 ------------------------------------

class Exited(Exception):
    pass


@pytest.fixture
def host(monkeypatch):
    module = load("host_under_test", "host.py")
    calls = SimpleNamespace(exit=None, execv=None)

    def fake_exit(code):
        calls.exit = code
        raise Exited(code)

    def fake_execv(path, argv):
        calls.execv = argv
        raise Exited("execv")

    monkeypatch.setattr(module.os, "_exit", fake_exit)
    monkeypatch.setattr(module.os, "execv", fake_execv)
    module.calls = calls
    yield module
    module._reset_for_tests()  # no watchdog left to fire after the test


def test_supervised_restart_stops_cleanly_then_exits_75(host, monkeypatch):
    monkeypatch.setenv("SONORIUM_SUPERVISED", "1")
    stopped = []
    host.set_stopper(lambda: stopped.append(True))
    assert host.request_restart("test") is True
    assert stopped == [True] and host.pending() == "restart"
    assert host.calls.exit is None  # nothing exits until the shutdown is over
    assert host.request_restart("again") is False  # one restart at a time
    with pytest.raises(Exited):
        host.finish()  # entrypoint, after the web server has shut down
    assert host.calls.exit == 75 == host.RESTART_EXIT_CODE


def test_unsupervised_restart_executes_again_in_place(host, monkeypatch):
    monkeypatch.delenv("SONORIUM_SUPERVISED", raising=False)
    host.set_stopper(lambda: None)
    host.request_restart()
    with pytest.raises(Exited):
        host.finish()
    assert host.calls.exit is None
    assert host.calls.execv == [sys.executable, "-m", "sonorium.entrypoint"]


def test_finish_does_nothing_without_a_restart(host, monkeypatch):
    monkeypatch.setenv("SONORIUM_SUPERVISED", "1")
    host.finish()
    assert host.calls.exit is None and host.calls.execv is None


def test_a_hung_shutdown_still_restarts(host, monkeypatch):
    monkeypatch.setenv("SONORIUM_SUPERVISED", "1")
    monkeypatch.setattr(host, "SHUTDOWN_TIMEOUT_SECONDS", 0.2)

    # in the timer's thread: record only (the real os._exit never returns)
    monkeypatch.setattr(host.os, "_exit", lambda code: setattr(host.calls, "exit", code))
    monkeypatch.setattr(host.os, "execv", lambda path, argv: None)
    host.set_stopper(lambda: None)  # the server never stops
    host.request_restart()
    deadline = time.time() + 5
    while host.calls.exit is None and time.time() < deadline:
        time.sleep(0.05)
    assert host.calls.exit == 75


def test_restart_with_no_server_running_exits_at_once(host, monkeypatch):
    monkeypatch.setenv("SONORIUM_SUPERVISED", "1")
    with pytest.raises(Exited):
        host.request_restart()
    assert host.calls.exit == 75


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_web_server_shuts_down_on_restart_even_with_an_open_stream(monkeypatch):
    """The real web server: a restart request ends serve() although a stream never ends."""
    pytest.importorskip("uvicorn")
    pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")
    from fastapi.responses import StreamingResponse
    from playback_helpers import addon_modules

    with addon_modules("sonorium.web_server", "sonorium.host") as mods:
        web_server, host_module = mods.web_server, mods.host
        host_module._reset_for_tests()
        monkeypatch.setattr(web_server, "GRACEFUL_SHUTDOWN_SECONDS", 1)
        app = web_server.new_app("test")

        async def endless():
            while True:
                yield b"x" * 1024
                await asyncio.sleep(0.05)

        app.add_api_route("/stream", lambda: StreamingResponse(endless()), methods=["GET"])
        app.add_api_route("/ok", lambda: {"ok": True}, methods=["GET"])
        port = _free_port()
        config = web_server.listener(app, port)
        config.host = "127.0.0.1"

        async def scenario():
            server = asyncio.create_task(web_server.serve(config))
            async with httpx.AsyncClient() as client:
                for _ in range(100):
                    try:
                        if (await client.get(f"http://127.0.0.1:{port}/ok")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        await asyncio.sleep(0.05)
                async with client.stream("GET", f"http://127.0.0.1:{port}/stream") as stream:
                    async for _ in stream.aiter_bytes():
                        break
                    host_module.request_restart("test")
                    started = time.monotonic()
                    await asyncio.wait_for(server, timeout=10)
                    return time.monotonic() - started

        monkeypatch.setattr(host_module, "finish", lambda: None)
        took = asyncio.run(scenario())
        assert took < 8
        assert host_module.pending() == "restart"
        host_module._reset_for_tests()


def test_restart_soon_asks_host_after_the_answer(monkeypatch):
    from playback_helpers import addon_modules

    with addon_modules("sonorium.host") as mods:
        asked = []
        monkeypatch.setattr(mods.host, "request_restart", lambda reason="": asked.append(reason))
        api_source = (PACKAGE / "api.py").read_text(encoding="utf-8")
        assert "os.execv" not in api_source  # restarts all go through sonorium/host.py

        async def run():
            namespace = {}
            exec(_restart_soon_source(api_source), namespace)
            loop = asyncio.get_running_loop()
            delays = []
            original = loop.call_later

            def call_later(delay, callback, *args):
                delays.append(delay)
                return original(0, callback, *args)
            monkeypatch.setattr(loop, "call_later", call_later)
            namespace["_restart_soon"](None, "Settings > Advanced")
            await asyncio.sleep(0.05)
            return delays

        assert asyncio.run(run())[0] == 1.0
        assert asked == ["Settings > Advanced"]


def _restart_soon_source(source: str) -> str:
    """WebApp._restart_soon as a plain function (the class needs the whole app to build)."""
    start = source.index("    def _restart_soon(")
    end = source.index("\n    def ", start + 10)
    lines = [line[4:] if line.startswith("    ") else line for line in source[start:end].splitlines()]
    return "\n".join(lines)


# --- supervise.sh: both images run Sonorium under it ----------------------------------

CHILD = """
import os, sys
count = sys.argv[1]
n = int(open(count).read()) if os.path.exists(count) else 0
open(count, "w").write(str(n + 1))
open(count + ".env", "a").write(os.environ.get("SONORIUM_SUPERVISED", "") + "\\n")
sys.exit(75 if n < 2 else 3)
"""


def _sh():
    found = shutil.which("sh")
    if not found:
        pytest.skip("no POSIX sh here")
    return found


def test_supervise_starts_sonorium_again_on_exit_75(tmp_path):
    sh = _sh()
    child = tmp_path / "child.py"
    child.write_text(CHILD)
    count = tmp_path / "count"
    result = subprocess.run([sh, SUPERVISE.as_posix(), Path(sys.executable).as_posix(), child.as_posix(), count.as_posix()],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 3  # any other exit code ends it, passed on
    assert count.read_text() == "3"
    assert (tmp_path / "count.env").read_text().split() == ["1", "1", "1"]
    assert result.stdout.count("starting it again") == 2


@pytest.mark.skipif(not hasattr(os, "killpg") or sys.platform == "win32", reason="POSIX signals")
def test_supervise_passes_a_stop_on_and_does_not_restart(tmp_path):
    sh = _sh()
    child = tmp_path / "child.py"
    child.write_text("import signal, sys, time\n"
                     "signal.signal(signal.SIGTERM, lambda *a: sys.exit(75))\n"
                     "open(sys.argv[1], 'a').write('started\\n')\n"
                     "time.sleep(30)\n")
    log = tmp_path / "log"
    process = subprocess.Popen([sh, str(SUPERVISE), sys.executable, str(child), str(log)])
    deadline = time.time() + 10
    while not log.exists() and time.time() < deadline:
        time.sleep(0.05)
    process.terminate()
    assert process.wait(timeout=10) == 75
    assert log.read_text().count("started") == 1


def test_both_images_run_sonorium_under_supervise():
    entrypoint = (REPO / "docker" / "entrypoint.sh").read_text()
    run = (REPO / "sonorium_addon" / "run.sh").read_text()
    assert "exec /usr/local/bin/sonorium-supervise" in entrypoint
    assert "exec /usr/local/bin/sonorium-supervise python3 -m sonorium.entrypoint" in run
    for dockerfile, source in ((REPO / "docker" / "Dockerfile", "sonorium_addon/supervise.sh"),
                               (REPO / "sonorium_addon" / "Dockerfile", "supervise.sh")):
        assert f"COPY {source} /usr/local/bin/sonorium-supervise" in dockerfile.read_text()
    assert b"\r\n" not in SUPERVISE.read_bytes()


# --- Secret helper: SONORIUM_SECRET_HELPER --------------------------------------------

FAKE_HELPER = r"""
import json, os, sys
store = os.environ["FAKE_SECRETS"]
action, name = sys.argv[1], sys.argv[2]
data = json.load(open(store, encoding="utf-8")) if os.path.exists(store) else {}
log = open(store + ".log", "a", encoding="utf-8")
log.write(f"{action} {name}\n")
if os.environ.get("FAKE_FAIL") == action:
    sys.stderr.write("vault locked")
    sys.exit(2)
if action == "store":
    data[name] = sys.stdin.buffer.read().decode("utf-8")
elif action == "get":
    if name not in data:
        sys.exit(1)
    sys.stdout.buffer.write(data[name].encode("utf-8"))
    sys.exit(0)
elif action == "erase":
    data.pop(name, None)
json.dump(data, open(store, "w", encoding="utf-8"))
"""


def make_helper(folder: Path) -> Path:
    """One program path (with a space in it), no arguments: a .cmd on Windows, a script elsewhere."""
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "fake_helper.py"
    script.write_text(FAKE_HELPER, encoding="utf-8")
    if os.name == "nt":
        program = folder / "secret helper.cmd"
        program.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        program = folder / "secret helper"
        program.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        program.chmod(0o755)
    return program


ENV_SET_BY_RUNTIME = ("SONORIUM__HA_CORE_API", "SUPERVISOR_TOKEN", "SONORIUM__MQTT_HOST", "SONORIUM__MQTT_PORT",
                      "SONORIUM__MQTT_USERNAME", "SONORIUM__MQTT_PASSWORD", "SONORIUM__STREAM_URL",
                      "SONORIUM__TRUSTED_PROXIES")


@pytest.fixture
def secrets_env(monkeypatch, tmp_path):
    helper = make_helper(tmp_path / "Program Files" / "Sonorium")
    store = tmp_path / "vault.json"
    monkeypatch.setenv("FAKE_SECRETS", str(store))
    monkeypatch.delenv("FAKE_FAIL", raising=False)
    monkeypatch.setenv("SONORIUM_STANDALONE", "1")
    monkeypatch.setenv("SONORIUM_CONNECTION_FILE", str(tmp_path / "sonorium" / "connection.json"))
    for key in ENV_SET_BY_RUNTIME:  # load_connection_into_env sets them: put back after the test
        monkeypatch.setenv(key, "x")
        monkeypatch.delenv(key)
    runtime = load("runtime_secret_test", "runtime.py")
    log = logging.getLogger("sonorium-secret-test")
    monkeypatch.setattr(runtime, "_logger", lambda: log)
    return SimpleNamespace(helper=helper, store=store, runtime=runtime, monkeypatch=monkeypatch)


def vault(env):
    return json.loads(env.store.read_text(encoding="utf-8")) if env.store.exists() else {}


def test_secrets_go_to_the_helper_not_the_file(secrets_env):
    env, rt = secrets_env, secrets_env.runtime
    env.monkeypatch.setenv("SONORIUM_SECRET_HELPER", str(env.helper))
    rt.save_connection({"ha_url": "http://ha:8123", "ha_token": "tök€n", "mqtt_host": "broker",
                        "mqtt_password": "pw"})
    on_disk = json.loads(rt.CONNECTION_FILE.read_text())
    assert on_disk == {"ha_url": "http://ha:8123", "mqtt_host": "broker"}
    assert vault(env) == {"ha_token": "tök€n", "mqtt_password": "pw"}  # exact: UTF-8, no newline added
    rt._secret_cache.clear()  # a new start asks the helper
    assert rt.load_connection() == {"ha_url": "http://ha:8123", "ha_token": "tök€n", "mqtt_host": "broker",
                                    "mqtt_password": "pw"}
    # An empty secret in an update keeps the saved one
    rt.save_connection({"ha_token": "", "mqtt_password": None, "stream_url": "http://me:8008"})
    assert vault(env) == {"ha_token": "tök€n", "mqtt_password": "pw"}


def test_secret_helper_store_failure_saves_nothing(secrets_env):
    env, rt = secrets_env, secrets_env.runtime
    env.monkeypatch.setenv("SONORIUM_SECRET_HELPER", str(env.helper))
    rt.save_connection({"ha_url": "http://ha:8123"})
    env.monkeypatch.setenv("FAKE_FAIL", "store")
    with pytest.raises(rt.SecretStoreError, match="ha_token couldn't be saved.*vault locked"):
        rt.save_connection({"ha_url": "http://other:8123", "ha_token": "new"})
    assert json.loads(rt.CONNECTION_FILE.read_text()) == {"ha_url": "http://ha:8123"}


def test_secret_helper_without_a_secret_is_not_set(secrets_env, caplog):
    env, rt = secrets_env, secrets_env.runtime
    env.monkeypatch.setenv("SONORIUM_SECRET_HELPER", str(env.helper))
    with caplog.at_level(logging.WARNING):
        rt.save_connection({"ha_url": "http://ha:8123"})
        assert rt.load_connection() == {"ha_url": "http://ha:8123"}
    assert "ha_token" not in caplog.text  # nothing stored yet is normal: no warning
    # asked once per run
    rt.load_connection()
    log = (Path(str(env.store) + ".log")).read_text().split("\n")
    assert log.count("get ha_token") == 1


def test_secret_helper_missing_program_does_not_stop_start(secrets_env, tmp_path, caplog):
    env, rt = secrets_env, secrets_env.runtime
    env.monkeypatch.setenv("SONORIUM_SECRET_HELPER", str(tmp_path / "no such helper.exe"))
    with caplog.at_level(logging.ERROR):
        assert rt.load_connection() == {}
        rt.load_connection_into_env()
    assert "'get ha_token' failed" in caplog.text
    with pytest.raises(rt.SecretStoreError):
        rt.save_connection({"mqtt_password": "pw"})


def test_clearing_erases_from_the_helper(secrets_env):
    env, rt = secrets_env, secrets_env.runtime
    env.monkeypatch.setenv("SONORIUM_SECRET_HELPER", str(env.helper))
    rt.save_connection({"ha_url": "http://ha:8123", "ha_token": "t", "mqtt_host": "b", "mqtt_password": "pw"})
    rt.clear_connection(rt.HA_FIELDS)
    assert vault(env) == {"mqtt_password": "pw"}
    assert rt.load_connection() == {"mqtt_host": "b", "mqtt_password": "pw"}
    env.monkeypatch.setenv("FAKE_FAIL", "erase")
    with pytest.raises(rt.SecretStoreError, match="couldn't be removed"):
        rt.clear_connection(rt.MQTT_FIELDS)
    assert json.loads(rt.CONNECTION_FILE.read_text()) == {"mqtt_host": "b"}


def test_plain_secrets_move_into_a_new_helper(secrets_env, monkeypatch):
    env, rt = secrets_env, secrets_env.runtime
    rt.save_connection({"ha_url": "http://ha:8123", "ha_token": "plain", "mqtt_password": "pw"})
    assert json.loads(rt.CONNECTION_FILE.read_text())["ha_token"] == "plain"  # no helper: in the file
    monkeypatch.setenv("SONORIUM_SECRET_HELPER", str(env.helper))
    rt.load_connection_into_env()  # the next start
    assert json.loads(rt.CONNECTION_FILE.read_text()) == {"ha_url": "http://ha:8123"}
    assert vault(env) == {"ha_token": "plain", "mqtt_password": "pw"}
    assert os.environ["SUPERVISOR_TOKEN"] == "plain" and os.environ["SONORIUM__MQTT_PASSWORD"] == "pw"


def test_a_secret_the_helper_cant_take_stays_in_the_file(secrets_env, monkeypatch):
    env, rt = secrets_env, secrets_env.runtime
    rt.save_connection({"ha_token": "plain"})
    monkeypatch.setenv("SONORIUM_SECRET_HELPER", str(env.helper))
    monkeypatch.setenv("FAKE_FAIL", "store")
    rt.move_secrets_to_helper()
    assert json.loads(rt.CONNECTION_FILE.read_text()) == {"ha_token": "plain"}
    assert rt.load_connection()["ha_token"] == "plain"


def test_without_a_helper_secrets_stay_in_the_file(secrets_env):
    rt = secrets_env.runtime
    rt.save_connection({"ha_token": "t"})
    assert json.loads(rt.CONNECTION_FILE.read_text()) == {"ha_token": "t"}
    assert not secrets_env.store.exists()


def test_connection_save_answers_an_error_when_the_secret_isnt_saved(app, tmp_path, monkeypatch):
    from fastapi import HTTPException
    helper = make_helper(tmp_path / "Program Files")
    monkeypatch.setenv("FAKE_SECRETS", str(tmp_path / "vault.json"))
    monkeypatch.setenv("FAKE_FAIL", "store")
    monkeypatch.setenv("SONORIUM_SECRET_HELPER", str(helper))
    api = object.__new__(app.api.WebApp)
    api._restart_soon = Mock()
    request = SimpleNamespace(json=AsyncMock(return_value={"ha_url": "http://ha:8123", "ha_token": "x"}))
    with pytest.raises(HTTPException) as refused:
        asyncio.run(app.api.WebApp.put_connection(api, request))
    assert refused.value.status_code == 500 and "couldn't be saved" in refused.value.detail
    api._restart_soon.assert_not_called()
    assert not app.runtime.CONNECTION_FILE.exists()


# --- SONORIUM_HTTP_PORT, SONORIUM_BUNDLED_THEMES ---------------------------------------

@pytest.mark.parametrize("standalone, value, http, stream", [
    ("1", None, 8008, 8008),
    ("0", None, 8080, 8008),
    ("1", "9010", 9010, 9010),
    ("1", "nonsense", 8008, 8008),
])
def test_http_port_from_the_environment(monkeypatch, standalone, value, http, stream):
    monkeypatch.setenv("SONORIUM_STANDALONE", standalone)
    if value is None:
        monkeypatch.delenv("SONORIUM_HTTP_PORT", raising=False)
    else:
        monkeypatch.setenv("SONORIUM_HTTP_PORT", value)
    rt = load("runtime_port_test", "runtime.py")
    assert rt.http_port() == http and rt.stream_port() == stream


def test_http_port_used_by_web_app_and_sign_in():
    api = (PACKAGE / "api.py").read_text(encoding="utf-8")
    assert "WEB_PORT = runtime.http_port()" in api
    config = (PACKAGE / "auth" / "config.py").read_text(encoding="utf-8")
    assert "return runtime.http_port()" in config
    settings = (PACKAGE / "settings.py").read_text(encoding="utf-8")
    assert "stream_port: int = Field(default_factory=runtime.stream_port)" in settings
    for relative in ("auth/routes.py", "auth/announce.py"):
        assert "runtime.stream_port()" in (PACKAGE / relative).read_text(encoding="utf-8")


def test_bundled_themes_folder_from_the_environment(monkeypatch, tmp_path):
    pytest.importorskip("av")
    pytest.importorskip("numpy")
    from playback_helpers import addon_modules
    monkeypatch.setenv("SONORIUM_BUNDLED_THEMES", str(tmp_path / "themes"))
    with addon_modules("sonorium.device") as mods:
        assert mods.device.BUNDLED_THEMES == tmp_path / "themes"
    monkeypatch.delenv("SONORIUM_BUNDLED_THEMES")
    with addon_modules("sonorium.device") as mods:
        assert mods.device.BUNDLED_THEMES == Path("/app/themes")


# --- Speaker discovery switchable on every install --------------------------------------

@pytest.mark.parametrize("standalone", ["1", "0"])
def test_network_speakers_can_be_switched_off_everywhere(monkeypatch, standalone):
    monkeypatch.setenv("SONORIUM_STANDALONE", standalone)
    rt = load("runtime_feature_test", "runtime.py")
    default_on = standalone == "1"
    assert rt.feature_switchable("network_speakers")
    assert rt.feature_enabled("network_speakers") is default_on
    assert rt.feature_enabled("network_speakers", {"network_speakers": False}) is False
    assert rt.feature_enabled("network_speakers", {"network_speakers": True}) is True
    f = rt.features()["network_speakers"]
    assert f["switchable"] is True and f["overridable"] is (not default_on)  # Advanced lists only "off" ones
    # The others are as before
    assert rt.feature_switchable("space_editing") is False
    assert rt.feature_switchable("plugin_packages") is True


def test_install_features_route_switches_discovery_off_and_back(app):
    from fastapi import HTTPException
    settings = SimpleNamespace(feature_overrides={})
    api = object.__new__(app.api.WebApp)
    api._state_store = SimpleNamespace(settings=settings, save=Mock())
    api._restart_soon = Mock()

    def put(body):
        request = SimpleNamespace(json=AsyncMock(return_value=body))
        return asyncio.run(app.api.WebApp.put_install_features(api, request))

    answer = put({"network_speakers": False})
    assert settings.feature_overrides == {"network_speakers": False}
    assert answer["features"]["network_speakers"]["enabled"] is False and answer["restarting"] is False
    put({"network_speakers": True, "restart": True})
    assert settings.feature_overrides == {}  # back to the default
    api._restart_soon.assert_called_once()
    with pytest.raises(HTTPException):
        put({"space_editing": False})


# --- state.json is written atomically ---------------------------------------------------

def test_state_json_written_atomically(tmp_path, monkeypatch):
    from playback_helpers import addon_modules
    with addon_modules("sonorium.core.state") as mods:
        store = mods.state.StateStore(tmp_path / "state.json")
        store.load()
        store.save()
        good = (tmp_path / "state.json").read_text()

        def broken(src, dst):
            raise OSError("disk pulled")
        monkeypatch.setattr(mods.state.os, "replace", broken)
        store.state.settings.feature_overrides = {"network_speakers": False}
        with pytest.raises(OSError):
            store.save()
        assert (tmp_path / "state.json").read_text() == good  # the old file is whole
        json.loads(good)
