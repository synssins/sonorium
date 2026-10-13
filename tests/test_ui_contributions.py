"""
Plugins add to any screen (plugins/ui_contract.py, plugins/ui.py, web/ui_api.py,
static/js/ext/contrib.js): the catalogue of targets and their open spots, the
contracts core enforces, and a plugin that adds to every spot of every target.
"""
import asyncio
import importlib.util
import json
import os
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from playback_helpers import addon_modules

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "sonorium_addon" / "sonorium" / "web"
EVERYWHERE = Path(__file__).parent / "data" / "ui_everywhere"
m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.plugin_data", "sonorium.core.state", "sonorium.plugins.ui_contract",
                       "sonorium.plugins.ui", "sonorium.plugins.host", "sonorium.plugins.manager",
                       "sonorium.plugins.loader", "sonorium.plugins.compat", "sonorium.plugins.settings_schema",
                       "sonorium.web.plugin_framework_api", "sonorium.auth.principal",
                       "sonorium.auth.permissions") as mods:
        m.__dict__.update(mods.__dict__)
        m.c = mods.ui_contract
        spec = importlib.util.spec_from_file_location("ui_everywhere_plugin", EVERYWHERE / "plugin.py")
        m.everywhere = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m.everywhere)
        yield


def _attrs():
    return m.plugin_data.normalize_declarations(m.everywhere.EverywherePlugin.get_attributes(None), "ui_everywhere")


def _settings():
    return m.settings_schema.normalize_schema(m.everywhere.SETTINGS, "ui_everywhere")


def validate(elements, plugin_id="p"):
    return m.c.validate(plugin_id, elements, attributes=_attrs(), settings=_settings())


ALL_SPOTS = None


def all_spots():
    return {(t["id"], s) for t in m.c.TARGETS for s in t["spots"]}


def web_spots():
    """The spots the web UI draws (other clients, such as the Windows tray, draw their own)."""
    return {(t["id"], s) for t in m.c.TARGETS if t.get("client", "web") == "web" for s in t["spots"]}


# --- the catalogue ---------------------------------------------------------------------------

def test_the_catalogue_names_every_target_once_with_its_contracts():
    ids = [t["id"] for t in m.c.TARGETS]
    assert len(ids) == len(set(ids))
    cat = m.c.catalogue()
    json.dumps(cat)
    assert cat["version"] == m.compat.API_VERSION
    for t in cat["targets"]:
        assert t["spots"], t["id"]
        assert not set(t["spots"]) & set(t["fixed"]), t["id"]
        for spot, contract in t["spots"].items():
            assert spot in m.c.SPOT_TYPES
            for key in ("kinds", "max_per_plugin", "label_max", "text_max", "field_types", "bindings",
                        "max_fields_per_item", "collapsible", "position"):
                assert key in contract, (t["id"], spot, key)
            if not t["attributes"]:
                assert "attribute" not in contract["bindings"], t["id"]
    # every screen, every editor, the menus and the left menu are there
    assert {"screen.sessions", "screen.themes", "screen.status", "sidebar", "settings.nav", "channel.editor",
            "theme.editor", "theme.track", "theme.file", "theme.group", "theme.preset", "playlist.editor",
            "sequence.editor", "speaker.row", "speaker_group.editor", "user.row", "plugin.row"} <= set(ids)


def test_every_limit_lives_in_one_place():
    source = (ROOT / "sonorium_addon" / "sonorium" / "plugins" / "ui_contract.py").read_text(encoding="utf-8")
    body = source.split("def _text(", 1)[1]
    # the checks read the limits from COMMON / PAGE / SPOT_TYPES, never a number of their own
    literals = [n for n in re.findall(r"(?<![\w.\[])(\d{2,})(?![\w\]])", body)]
    assert not literals, literals


# --- a plugin that adds to every spot -----------------------------------------------------------

def test_a_well_behaved_plugin_is_accepted_in_every_spot():
    elements, warnings = validate(m.everywhere.everywhere(), "ui_everywhere")
    assert warnings == []
    assert {(e["target"], e["spot"]) for e in elements} == all_spots()
    kinds = {e["kind"] for e in elements}
    assert kinds == set(m.c.ELEMENT_KINDS)
    types = {e["type"] for e in elements if e["kind"] == "field"} | {
        f["type"] for e in elements for f in e.get("fields") or []}
    assert types == set(m.c.FIELD_TYPES)
    # attribute-bound fields on the objects that keep attributes, actions elsewhere
    for e in elements:
        for f in ([e] if e["kind"] == "field" else e.get("fields") or []):
            obj = m.c.target(e["target"])["object"]
            if "attribute" in f["bind"]:
                assert obj in m.c.ATTRIBUTE_KINDS
                if f["type"] != "colour":
                    assert "default" in f


# --- contracts are enforced, per spot ------------------------------------------------------------

BAD = [
    ({"target": "theme.track", "spot": "menu", "id": "long", "kind": "action", "label": "A label far too long for a menu"},
     "theme.track/menu", "the label is 31 characters; the limit here is 20"),
    ({"target": "channel.card", "spot": "badges", "id": "kind", "kind": "action", "label": "Go"},
     "channel.card/badges", "kind 'action' is not allowed here"),
    ({"target": "channel.card", "spot": "name", "id": "fixed", "kind": "badge", "text": "x"},
     "channel.card/name", "'name' is a fixed part of channel.card"),
    ({"target": "screen.sessions", "spot": "cards", "id": "fixed2", "kind": "info", "text": "x"},
     "screen.sessions/cards", "fixed part"),
    ({"target": "speaker.card", "spot": "menu", "id": "nowhere", "kind": "action", "label": "x"},
     "speaker.card/menu", "there is no target"),
    ({"target": "screen.playlists", "spot": "top", "id": "noattr", "kind": "field", "type": "toggle",
      "label": "On", "bind": {"attribute": "demo_on"}},
     "screen.playlists/top", "binding to an attribute is not allowed here"),
    ({"target": "channel.editor", "spot": "settings", "id": "undeclared", "kind": "field", "type": "toggle",
      "label": "On", "bind": {"attribute": "missing"}},
     "channel.editor/settings", "declares no attribute 'missing'"),
    ({"target": "channel.editor", "spot": "settings", "id": "badtype", "kind": "field", "type": "toggle",
      "label": "Lvl", "bind": {"attribute": "demo_level"}},
     "channel.editor/settings", "can't show the number attribute"),
    ({"target": "channel.card", "spot": "info", "id": "when", "kind": "info", "text": "x", "when": {"planet": "mars"}},
     "channel.card/info", "'when' fact 'planet'"),
    ({"target": "theme.card", "spot": "menu", "id": "danger", "kind": "action", "label": "Wipe", "style": "danger"},
     "theme.card/menu", "style is 'secondary'"),
    ({"target": "sequence.editor", "spot": "settings", "id": "slider", "kind": "field", "type": "slider",
      "label": "S", "bind": {"action": "s"}},
     "sequence.editor/settings", "a slider needs 'min' and 'max'"),
    ({"target": "theme.preset", "spot": "menu", "id": "grp", "kind": "group", "label": "Fade",
      "fields": [{"id": f"f{i}", "type": "number", "label": "N", "bind": {"action": "n"}} for i in range(3)]},
     "theme.preset/menu", "a group holds 3 fields; the limit here is 2"),
    ({"target": "screen.themes", "spot": "top", "id": "colour", "kind": "field", "type": "colour", "label": "C",
      "bind": {"action": "c"}},
     "screen.themes/top", "field type 'colour' is not allowed here"),
    ({"target": "status.channel", "spot": "badges", "id": "tolong", "kind": "badge", "text": "Thirteen chars"},
     "status.channel/badges", "the text is 14 characters; the limit here is 12"),
]


@pytest.mark.parametrize("element,where,rule", BAD, ids=[b[0]["id"] for b in BAD])
def test_a_misbehaving_element_is_refused_with_a_warning_naming_spot_and_rule(element, where, rule):
    good = {"target": "channel.card", "spot": "info", "id": "good", "kind": "info", "text": "Fine"}
    if element["target"] == "channel.card" and element.get("spot") == "info":
        good = {"target": "channel.card", "spot": "badges", "id": "good", "kind": "badge", "text": "Fine"}
    elements, warnings = validate([element, good])
    assert [e["id"] for e in elements] == ["good"]  # the rest of the plugin still counts
    assert len(warnings) == 1
    assert warnings[0].startswith(where), warnings
    assert rule in warnings[0], warnings
    assert warnings[0].endswith("refused")


def test_too_many_elements_refuse_the_whole_spot_never_cut_short():
    many = [{"target": "speaker.row", "spot": "menu", "id": f"m{i}", "kind": "action", "label": f"Item {i}"}
            for i in range(4)]
    elements, warnings = validate(many + [{"target": "speaker.row", "spot": "badges", "id": "b", "kind": "badge",
                                           "text": "ok"}])
    assert [e["id"] for e in elements] == ["b"]
    assert warnings == ["speaker.row/menu: 4 elements; a plugin may add at most 3 here; all 4 refused"]


def test_nothing_is_ever_truncated():
    long = "x" * 25
    elements, warnings = validate([{"target": "channel.editor", "spot": "settings", "id": "a", "kind": "action",
                                    "label": long}])
    assert elements == [] and "25 characters" in warnings[0]
    ok, _ = validate([{"target": "channel.editor", "spot": "settings", "id": "a", "kind": "action", "label": "x" * 24}])
    assert ok[0]["label"] == "x" * 24


def test_bad_ids_and_shapes():
    elements, warnings = validate([{"target": "channel.card", "spot": "badges", "id": "Bad Id", "kind": "badge"},
                                   "nope", {"target": "channel.card", "spot": "badges", "id": "b", "kind": "badge",
                                            "text": "x"},
                                   {"target": "channel.card", "spot": "info", "id": "b", "kind": "info", "text": "y"}])
    assert [e["id"] for e in elements] == ["b"]
    assert len(warnings) == 3
    assert any("used twice" in w for w in warnings)
    assert m.c.validate("p", {"not": "a list"})[1] == ["ui: contributions must be a list of elements"]


def test_one_plugin_never_reaches_another_plugins_spot_or_the_fixed_parts():
    a, _ = validate([{"target": "theme.track", "spot": "menu", "id": "x", "kind": "action", "label": "A"}], "a")
    b, warnings = validate([{"target": "theme.track", "spot": "menu", "id": f"x{i}", "kind": "action",
                             "label": "B"} for i in range(9)], "b")
    assert len(a) == 1 and b == [] and warnings  # b broke its own spot only; a still counts


# --- the service: registered on enable, gone when off or uninstalled, live values -----------------

@pytest.fixture
def world(tmp_path):
    store = m.state.StateStore(tmp_path / "state.json")
    m.plugin_data.registry.bind(lambda: store.settings.plugin_attributes, save=store.save)
    events = []
    host = m.host.PluginHost(tmp_path / "config", state_store=store)
    host.bus.publish = lambda kind, data=None, **kw: events.append((kind, data, kw))
    host.ui.publish = host.bus.publish
    plugins = tmp_path / "plugins"
    shutil.copytree(EVERYWHERE, plugins / "ui_everywhere")
    m.loader.BUILTIN_PLUGINS_DIR = None
    manager = m.manager.PluginManager(store, plugins_dir=plugins, audio_path=tmp_path / "audio", host=host)
    asyncio.run(manager.initialize())
    plugin = manager.get_plugin("ui_everywhere")
    host.permissions.approve(plugin)  # as the user does when switching it on
    asyncio.run(manager.enable_plugin("ui_everywhere"))
    yield SimpleNamespace(host=host, manager=manager, plugin=plugin, events=events, store=store, tmp=tmp_path)
    m.plugin_data.registry.unbind()


def test_the_loaded_plugin_adds_to_every_spot_and_its_row_has_no_warnings(world):
    elements = world.host.ui.elements(world.plugin)
    assert {(e["target"], e["spot"]) for e in elements} == all_spots()
    info = world.plugin.to_dict()
    assert info["ui_warnings"] == [] and info["short_name"] == "Every"


def test_refused_contributions_show_on_the_plugins_row(world):
    warnings = world.plugin.ui.register([{"target": "theme.track", "spot": "mute", "id": "x", "kind": "action",
                                          "label": "Mute it"}])
    assert warnings and "'mute' is a fixed part of theme.track" in warnings[0]
    assert world.plugin.to_dict()["ui_warnings"] == warnings
    assert world.host.ui.elements(world.plugin) == []
    assert world.events[-1][0] == "ui.changed"


def test_switched_off_or_uninstalled_adds_nothing(world):
    asyncio.run(world.manager.disable_plugin("ui_everywhere"))
    assert world.host.ui.elements(world.plugin) == []
    assert world.plugin.to_dict()["ui_warnings"] == []
    asyncio.run(world.manager.enable_plugin("ui_everywhere"))
    assert world.host.ui.elements(world.plugin)
    world.host.plugin_removed("ui_everywhere")
    assert "ui_everywhere" not in world.host.ui._checked


def test_live_values_are_kept_and_published(world):
    eid = next(e["id"] for e in world.host.ui.elements(world.plugin) if e["kind"] == "column")
    world.plugin.ui.set_value(eid, {"text": "42", "state": "ok"}, {"kind": "speaker", "id": "s1"})
    assert world.host.ui.live(["ui_everywhere"])[f"ui_everywhere/{eid}"] == {"speaker||s1": {"text": "42", "state": "ok"}}
    kind, data, kw = world.events[-1]
    assert kind == "ui.value" and data["ref"] == "speaker||s1" and kw["origin"] == "plugin:ui_everywhere"
    with pytest.raises(ValueError):
        world.plugin.ui.set_value("no-such-element", 1)
    with pytest.raises(ValueError):
        world.plugin.ui.set_value(eid, "x" * 500)


# --- HTTP ---------------------------------------------------------------------------------------

@pytest.fixture
def client(world):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    perms = {"value": frozenset(m.permissions.ALL_PERMS)}
    app = FastAPI()

    @app.middleware("http")
    async def signed_in(request: Request, call_next):
        request.state.auth = m.principal.Principal("session", perms=perms["value"], user_id="u2")
        return await call_next(request)

    app.include_router(m.plugin_framework_api.create_plugin_framework_router(world.manager, world.host))
    with TestClient(app) as c:
        yield SimpleNamespace(c=c, perms=perms, world=world)


def test_routes_catalogue_contributions_and_actions(client):
    c = client.c
    cat = c.get("/api/ui/targets").json()
    assert len(cat["targets"]) == len(m.c.TARGETS)
    data = c.get("/api/ui/contributions").json()
    assert data["plugins"]["ui_everywhere"]["short_name"] == "Every"
    assert {(e["target"], e["spot"]) for e in data["elements"]} == all_spots()
    action = next(e for e in data["elements"] if e["kind"] == "action" and e["target"] == "speaker.row")
    r = c.post(f"/api/ui/actions/ui_everywhere/{action['id']}", json={"ref": {"kind": "speaker", "id": "s1"}})
    assert r.status_code == 200 and r.json()["success"]
    assert client.world.plugin.last_action == (action["id"], {"kind": "speaker", "id": "s1", "theme": None}, None)
    # an action-bound field: the plugin's ui_action gets the bound action and the value
    field = next(e for e in data["elements"] if e["kind"] == "field" and "action" in e["bind"])
    r = c.post(f"/api/ui/actions/ui_everywhere/{field['id']}", json={"ref": None, "value": 7})
    assert r.json()["value"] == 7 and client.world.plugin.last_action[0] == "changed"
    # badges and info lines are not actions; unknown ones are 404
    badge = next(e for e in data["elements"] if e["kind"] == "badge")
    assert c.post(f"/api/ui/actions/ui_everywhere/{badge['id']}", json={}).status_code == 404
    assert c.post("/api/ui/actions/ui_everywhere/nope", json={}).status_code == 404
    assert c.post("/api/ui/actions/ui_everywhere/bad id", json={}).status_code == 400


def test_objects_route_reads_attributes_the_caller_may_see(client):
    c = client.c
    world = client.world
    world.store.state.sessions["c1"] = m.state.Session(id="c1", name="Den")
    world.host.attributes.state_store = world.store
    world.host.attributes.set({"kind": "channel", "id": "c1"}, "ui_everywhere", {"demo_on": True})
    r = c.post("/api/ui/objects", json={"refs": [{"kind": "channel", "id": "c1"}, {"kind": "playlist", "id": "p"},
                                                 {"kind": "channel", "id": "gone"}]})
    values = r.json()["values"]
    assert values["channel||c1"]["ui_everywhere"]["demo_on"] is True
    assert list(values) == ["channel||c1"]
    client.perms["value"] = frozenset({"th.view"})
    assert c.post("/api/ui/objects", json={"refs": [{"kind": "channel", "id": "c1"}]}).json()["values"] == {}
    assert c.post("/api/ui/objects", json={"refs": "x"}).status_code == 400


def test_every_ui_route_has_a_permission_entry():
    routes = m.permissions.ROUTES
    for method, path in [("GET", "/api/ui/targets"), ("GET", "/api/ui/contributions"), ("POST", "/api/ui/objects"),
                         ("POST", "/api/ui/actions/{plugin_id}/{element_id}")]:
        assert (method, path) in routes
    assert routes[("POST", "/api/ui/actions/{plugin_id}/{element_id}")].any_of == {"pg.use"}


# --- the web UI marks every open spot ------------------------------------------------------------

@pytest.mark.skipif(not os.environ.get("SONORIUM_UI_URL") or not shutil.which("node"),
                    reason="needs a running Sonorium with the ui_everywhere plugin (SONORIUM_UI_URL) and node")
def test_every_spot_is_in_the_dom_and_drawn_headless():
    """tests/ui_dom_check.mjs in headless Chrome against SONORIUM_UI_URL."""
    import subprocess
    result = subprocess.run([shutil.which("node"), str(Path(__file__).parent / "ui_dom_check.mjs"),
                             os.environ["SONORIUM_UI_URL"]], capture_output=True, text=True, encoding="utf-8",
                            timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "round trips:" in result.stdout and " ok" in result.stdout


def _web_sources():
    parts = [(WEB / "templates" / "index.html").read_text(encoding="utf-8")]
    for path in sorted((WEB / "static" / "js").rglob("*.js")):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_every_target_and_spot_is_marked_in_the_web_ui_and_nothing_else():
    """
    Each spot is drawn by the shared helpers (uiZone / uiAnchor / data-ui-target), so a
    target in the catalogue is a place on a screen, and a place on a screen is in the
    catalogue. Screens get theirs from showView() (screen.<view>: header, top, menu).
    """
    text = _web_sources()
    found = set()
    for target, spots in re.findall(r"uiZone\('([a-z_.]+)', '([a-z ]+)'", text) + \
            re.findall(r"uiAnchor\('([a-z_.]+)', '([a-z ]+)'", text) + \
            re.findall(r"setAnchor\([^,]+, '([a-z_.]+)', '([a-z ]+)'", text) + \
            re.findall(r'data-ui-target="([a-z_.]+)" data-ui-spot="([a-z ]+)"', text):
        for spot in spots.split():
            found.add((target, spot))
    # A row drawn for two kinds of object names both targets: target: a ? 'x.one' : 'x.two'
    for one, two in re.findall(r"target: \w+ \? '([a-z_.]+)' : '([a-z_.]+)'", text):
        for spots in re.findall(r"ui(?:Zone|Anchor)\(ui\.target, '([a-z ]+)'", text):
            for spot in spots.split():
                found |= {(one, spot), (two, spot)}
    screens = {(t["id"], s) for t in m.c.TARGETS if t["id"].startswith("screen.") for s in t["spots"]}
    assert "uiZone(`screen.${viewName}`, 'header'" in text and "uiZone(target, 'top'" in text
    assert "Sonorium.ui.setAnchor(btn, target, 'menu', null)" in text
    assert found | screens == web_spots(), (web_spots() - found - screens, found - web_spots())
    assert {t["id"] for t in m.c.TARGETS if t.get("client", "web") != "web"} == {"tray.menu"}
