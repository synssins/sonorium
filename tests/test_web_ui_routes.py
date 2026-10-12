"""
G13: every API call the web UI makes is to a route the server has (one with a
permission in sonorium/auth/permissions.py, the PERMISSIONS in
sonorium/auth/users.py and keys.py, or a built-in plugin's). A call to a route that's gone answers 403 every time.
"""
import re

from playback_helpers import PACKAGE

WEB = PACKAGE / "web"
CALL = re.compile(r"""api\(\s*'(GET|POST|PUT|DELETE)'\s*,\s*(`[^`]*`|'[^']*'|"[^"]*")""")
FETCH = re.compile(r"""fetch\(\s*(?:BASE_PATH|BASE)\s*\+\s*'(/api/[^'?]*)'(?:\s*,\s*\{\s*method:\s*'(\w+)')?""")
FETCH_TEMPLATE = re.compile(r"""fetch\(\s*`\$\{(?:BASE_PATH|BASE)\}(/api/[^`?]*)[^`]*`(?:\s*,\s*\{\s*method:\s*'(\w+)')?""")


def _table():
    entries = []
    blocks = [(PACKAGE / "auth" / "permissions.py").read_text(encoding="utf-8").split('_ROUTES = """', 1)[1]]
    for path in (PACKAGE / "auth" / "users.py", PACKAGE / "auth" / "keys.py"):
        text = path.read_text(encoding="utf-8")
        blocks.append(text.split('PERMISSIONS = """', 1)[1])
    for block in blocks:
        for line in block.split('"""', 1)[0].strip().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                method, path = line.split()[:2]
                entries.append((method, path.strip("/").split("/")))
    return entries


def _segment_matches(ui: str, template: str) -> bool:
    return ui == template or "${" in ui or "{" in template


def _known(table, method, path) -> bool:
    ui = path.strip("/").split("/")
    return any(m == method and len(t) == len(ui) and all(map(_segment_matches, ui, t)) for m, t in table)


def _calls():
    files = list((WEB / "static" / "js").glob("*.js")) + list((WEB / "static" / "js" / "ext").glob("*.js"))         + list((PACKAGE / "plugins" / "builtin").glob("*/web/*.js"))
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for method, quoted in CALL.findall(line):
                path = re.sub(r"\$\{[^}]*\}", "${}", quoted[1:-1])  # a placeholder may hold a "?"
                yield f.name, n, method, "/api" + path.split("?")[0]
            for path, method in FETCH.findall(line) + FETCH_TEMPLATE.findall(line):
                yield f.name, n, method or "GET", path


def test_the_web_ui_calls_only_routes_that_exist():
    table = _table()
    calls = list(_calls())
    assert len(calls) > 100  # the scan finds the UI's calls
    missing = [f"{f}:{n} {method} {path}" for f, n, method, path in calls if not _known(table, method, path)]
    assert not missing, "The web UI calls routes the server doesn't have:\n" + "\n".join(missing)


def test_the_scan_catches_a_dead_route():
    table = _table()
    assert not _known(table, "POST", "/api/heartbeat")
    assert not _known(table, "GET", "/api/update/check")
    assert _known(table, "PUT", "/api/spaces/${kind}/${encodeURIComponent(id)}")
