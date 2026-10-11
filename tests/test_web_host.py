"""G14: the web UI's window.SONORIUM_HOST hooks for the Sonorium app (tests/web_host_check.mjs, needs node)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
JS = ROOT / "sonorium_addon" / "sonorium" / "web" / "static" / "js"


@pytest.mark.skipif(NODE is None, reason="node isn't installed")
def test_host_hooks_do_nothing_in_a_browser_and_serve_the_app():
    result = subprocess.run([NODE, str(ROOT / "tests" / "web_host_check.mjs")], capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "web host hooks: ok" in result.stdout


@pytest.mark.skipif(NODE is None, reason="node isn't installed")
@pytest.mark.parametrize("name", ["api.js", "slots.js", "app.js", "ext/core.js", "ext/pages.js", "ext/commandmaps.js",
                                  "ext/connections.js", "ext/notifications.js", "ext/alerts.js", "ext/outputs.js"])
def test_web_ui_scripts_parse(name):
    result = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_the_hook_versions_agree():
    """api.js SONORIUM_HOST_API is what GET /api/info reports as ui.host_api."""
    api_js = (JS / "api.js").read_text(encoding="utf-8")
    routes = (ROOT / "sonorium_addon" / "sonorium" / "auth" / "routes.py").read_text(encoding="utf-8")
    assert "const SONORIUM_HOST_API = 1;" in api_js and "UI_HOST_API = 1" in routes
