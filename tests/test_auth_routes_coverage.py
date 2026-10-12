"""
Every route has a permission (sonorium/auth/permissions.py, or the plugin's
PERMISSIONS): a new route without one fails here (and is refused at runtime).
"""
import importlib.util
import json
import sys
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from test_api_routes import PACKAGE, _exec, m  # noqa: E402,F401  (m: the app modules, stubbed)


def _routes(routes):
    """(method, path) of real routes; included routers are checked one by one below."""
    out = set()
    for r in routes:
        path = getattr(r, "path", None)
        if path is None or not getattr(r, "methods", None):
            continue
        for method in r.methods:
            if method != "HEAD":
                out.add((method, path))
    return out


def _plugin_permissions(name):
    text = (PACKAGE / "plugins" / "builtin" / name / "plugin.py").read_text(encoding="utf-8")
    block = text.split('PERMISSIONS = """', 1)[1].split('"""', 1)[0]
    return {tuple(line.split()[:2]) for line in block.strip().splitlines() if line.strip()}


def test_every_route_has_a_permission(m):
    permissions = _exec("sonorium.auth.permissions", PACKAGE / "auth" / "permissions.py")
    table = set(permissions.ROUTES)
    app = m.api.WebApp(MagicMock()).app
    routes = _routes(app.routes)
    routes |= _routes(m.v2.create_api_router(
        session_manager=MagicMock(), group_manager=MagicMock(), ha_registry=MagicMock(), state_store=MagicMock(),
        channel_manager=MagicMock(), plugin_manager=MagicMock(), mqtt_manager=MagicMock()).routes)
    framework = _exec("sonorium.web.plugin_framework_api", PACKAGE / "web" / "plugin_framework_api.py")
    routes |= _routes(framework.create_plugin_framework_router(MagicMock(), MagicMock()).routes)
    core = _exec("sonorium.auth.routes", PACKAGE / "auth" / "routes.py")
    service = MagicMock()
    routes |= _routes(core.create_core_auth_router(service, stream_url=lambda: "", features=dict, version="t",
                                                   https_host_port=lambda: 0).routes)
    missing = sorted(routes - table)
    assert not missing, f"Routes without a permission in sonorium/auth/permissions.py: {missing}"
    stale = sorted(k for k in table - routes if not k[1].startswith("/stream/"))
    assert not stale, f"Permissions for routes that don't exist: {stale}"


@pytest.mark.parametrize("name", ["users", "integration"])
def test_plugin_routes_have_permissions(m, name):
    pytest.importorskip("argon2")
    folder = PACKAGE / "plugins" / "builtin" / name
    spec = importlib.util.spec_from_file_location(f"sonorium_plugin_{name}_cov", folder / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    routes = _routes(module.create_router(MagicMock()).routes)
    declared = _plugin_permissions(name)
    assert routes == declared
    assert json.loads((folder / "manifest.json").read_text())["builtin"] is True


def test_core_and_plugins_never_share_a_route():
    text = (PACKAGE / "auth" / "permissions.py").read_text(encoding="utf-8")
    core = {tuple(line.split()[:2]) for line in text.split('_ROUTES = """', 1)[1].split('"""', 1)[0].strip().splitlines()
            if line.strip()}
    for name in ("users", "integration"):
        assert not core & _plugin_permissions(name)
