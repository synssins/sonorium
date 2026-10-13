"""
Theme backups from the app (core/backups.py, core/backup_load.py,
web/backup_routes.py): each account's own, per phone; phones write only their
own; mixes as JSON, whole themes as packages (resumable, by fingerprint);
restore; the space limit; "load my phone presets into themes" and what the
next backup does to loaded presets; path and size checks; nothing reaches the
server's themes except through loading.
"""

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "sonorium_addon"
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from engine_theme import make_theme  # noqa: E402

THEME_ID = "engine-test-theme"


def _ours(name):
    return name == "sonorium" or name.startswith("sonorium.")


@pytest.fixture(autouse=True)
def _real_sonorium():
    saved_modules = {name: module for name, module in sys.modules.items() if _ours(name)}
    saved_path = list(sys.path)
    for name in saved_modules:
        del sys.modules[name]
    sys.path.insert(0, str(ADDON))
    try:
        yield
    finally:
        for name in [n for n in sys.modules if _ours(n)]:
            del sys.modules[name]
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


class Service:
    """The parts of AuthService the routes use: the folder, phones, keys and users."""

    def __init__(self, folder):
        self.folder = folder
        self.phones = {"pa1": {"id": "pa1", "name": "Galaxy S25 Ultra", "user_id": "alice", "platform": "android",
                               "model": "SM-S938"},
                       "pa2": {"id": "pa2", "name": "Pixel 9", "user_id": "alice"},
                       "pb1": {"id": "pb1", "name": "Bob's phone", "user_id": "bob"}}
        self.keys = {"k1": {"data": {"created_by": "alice"}}, "k0": {"data": {}}}
        self.users = {"alice": {"role": "admin"}, "bob": {"role": "user"}}
        self.tokens = SimpleNamespace(get=lambda key_id: self.keys.get(key_id))
        self.authenticators = {"users": SimpleNamespace(accounts=SimpleNamespace(user=self.users.get))}

    def device(self, device_id):
        return self.phones.get(device_id)


class Who:
    """The caller from X-Test-* headers, as the auth middleware would set it."""

    def __init__(self, app, service):
        self.app, self.service = app, service

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            from sonorium.auth.principal import Principal
            h = {k.decode(): v.decode() for k, v in scope["headers"]}
            principal = Principal(h.get("x-kind", "device"), frozenset(filter(None, h.get("x-perms", "").split(","))),
                                  user_id=h.get("x-user") or None, device_id=h.get("x-device") or None,
                                  key_id=h.get("x-key") or None, session_id=h.get("x-session") or None)
            scope.setdefault("state", {})["auth"] = principal
            scope["state"]["auth_service"] = self.service
        return await self.app(scope, receive, send)


ADMIN = "ch.view,th.view,th.edit,th.upload,self"
USER = "ch.view,th.view,self"


def phone(user, device, perms=ADMIN):
    return {"X-Kind": "device", "X-User": user, "X-Device": device, "X-Perms": perms + ",device"}


def browser(user, perms=ADMIN):
    return {"X-Kind": "session", "X-User": user, "X-Session": "s-" + user, "X-Perms": perms}


A1, A2, B1 = phone("alice", "pa1"), phone("alice", "pa2"), phone("bob", "pb1", USER)
ALICE_WEB = browser("alice")


@pytest.fixture
def t(tmp_path):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    import sonorium.paths
    from sonorium import playback_values
    from sonorium.core.theme_metadata import ThemeMetadataManager
    from sonorium.web.backup_routes import add_backup_routes

    audio = tmp_path / "audio"
    folder = make_theme(audio)
    manager = ThemeMetadataManager(audio)
    manager.scan_themes()
    limit = {"backup_limit_mb": 0.0}
    playback_values.set_source(lambda: limit)
    service = Service(tmp_path / "config")
    router = APIRouter(prefix="/api")
    session_manager = SimpleNamespace(theme_metadata_manager=manager, saved=[],
                                      preset_saved=lambda ids, pid: session_manager.saved.append(pid))
    add_backup_routes(router, session_manager=session_manager, root=tmp_path / "config" / "backups")
    app = FastAPI()
    app.include_router(router)
    with patch.object(sonorium.paths, "audio_dir", lambda: audio):
        yield SimpleNamespace(client=TestClient(Who(app, service)), service=service, audio=audio, folder=folder,
                              manager=manager, limit=limit, root=tmp_path / "config" / "backups",
                              session_manager=session_manager)
    playback_values.set_source(None)


MIX = {
    "name": "Test Theme",
    "presets": {
        "night": {"name": "Night mix", "is_default": True,
                  "tracks": {"Drone": {"volume": 0.4, "muted": False, "bogus": 3},
                             "Wind": {"presence": 7, "playback_mode": "nonsense"},
                             "Not a track": {"volume": 0.2}},
                  "groups": {"Birds": {"volume": 0.5, "gap_min": 2, "mode": "x"}, "Nope": {"volume": 1}}},
        "day": {"name": "Day", "tracks": {"Chirp": {"presence": 0.5}}},
    },
    "sequences": {"evening": {"name": "Evening", "presets": ["day", "night", "missing"], "at_end": "stop"}},
    "tracks": {"Drone": {"volume": 0.5}},
}


def files_of(t, name):
    return sorted(p.relative_to(t.audio).as_posix() for p in t.audio.rglob("*") if p.is_file())


def server_presets(t):
    return json.loads((t.folder / "presets.json").read_text(encoding="utf-8"))


# --- mixes, accounts and phones -------------------------------------------------

def test_a_mix_is_kept_apart_and_only_its_account_sees_it(t):
    before = files_of(t, None)
    r = t.client.put(f"/api/me/backups/self/themes/{THEME_ID}", json=MIX, headers=A1)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "mix" and body["presets"] == 2 and body["sequences"] == 1 and body["loaded"] == []
    assert files_of(t, None) == before  # nothing in the server's themes changed
    listing = t.client.get("/api/me/backups", headers=ALICE_WEB).json()
    [device] = listing["devices"]
    assert device["device_id"] == "pa1" and device["name"] == "Galaxy S25 Ultra" and device["linked"] is True
    assert device["themes"][0]["key"] == THEME_ID and device["themes"][0]["size_bytes"] > 0
    assert listing["storage"]["used_bytes"] > 0 and listing["storage"]["limit_bytes"] is None
    got = t.client.get(f"/api/me/backups/pa1/themes/{THEME_ID}", headers=A2).json()
    assert got["mix"]["presets"]["night"]["tracks"]["Drone"]["volume"] == 0.4  # stored as sent
    # Bob (and Bob's admin rights, had he any) never sees Alice's
    assert t.client.get("/api/me/backups", headers=B1).json()["devices"] == []
    assert t.client.get(f"/api/me/backups/pa1/themes/{THEME_ID}", headers=B1).status_code == 404
    assert t.client.delete("/api/me/backups/pa1", headers=B1).status_code == 404


def test_only_a_phone_writes_and_only_its_own(t):
    url = f"/api/me/backups/{{}}/themes/{THEME_ID}"
    assert t.client.put(url.format("pa1"), json=MIX, headers=A2).status_code == 403  # another phone's
    assert t.client.put(url.format("pa1"), json=MIX, headers=ALICE_WEB).status_code == 403  # a browser
    assert t.client.put(url.format("pa1"), json=MIX, headers=A1).status_code == 200
    assert t.client.put(url.format("pb1"), json=MIX, headers=A1).status_code == 403
    assert t.client.put("/api/me/backups/pa1/playlists", json={"playlists": []}, headers=A2).status_code == 403


def test_latest_wins_per_phone_and_phones_never_overwrite_each_other(t):
    url = f"/api/me/backups/self/themes/{THEME_ID}"
    t.client.put(url, json={"presets": {"a": {"name": "A"}}}, headers=A1)
    t.client.put(url, json={"presets": {"b": {"name": "B"}}}, headers=A2)
    t.client.put(url, json={"presets": {"c": {"name": "C"}}}, headers=A1)
    get = lambda d: t.client.get(f"/api/me/backups/{d}/themes/{THEME_ID}", headers=ALICE_WEB).json()
    assert list(get("pa1")["mix"]["presets"]) == ["c"]
    assert list(get("pa2")["mix"]["presets"]) == ["b"]


def test_nobody_signed_in_is_refused_and_a_key_acts_for_its_maker(t):
    t.client.put(f"/api/me/backups/self/themes/{THEME_ID}", json=MIX, headers=A1)
    assert t.client.get("/api/me/backups", headers={"X-Kind": "anonymous"}).status_code == 401
    key = {"X-Kind": "key", "X-Key": "k1", "X-Perms": "th.view"}
    assert [d["device_id"] for d in t.client.get("/api/me/backups", headers=key).json()["devices"]] == ["pa1"]
    assert t.client.get("/api/me/backups", headers={"X-Kind": "key", "X-Key": "k0"}).status_code == 401
    assert t.client.put(f"/api/me/backups/pa1/themes/{THEME_ID}", json=MIX, headers=key).status_code == 403


def test_permission_table_writes_are_phones_only():
    from sonorium.auth import permissions as P
    for (method, path), perm in P.ROUTES.items():
        if path.startswith("/api/me/backups"):
            if method == "PUT" and not path.endswith("/settings"):
                assert perm.any_of == {"device"}, path
            assert not perm.public and not perm.page
    assert P.DEVICE in P.NEVER_FOR_KEYS


@pytest.mark.parametrize("body, status", [
    ([], 400),
    ({"presets": [], "x": 1}, 400),
    ({"evil": {}}, 400),
    ({"presets": {"p": "not an object"}}, 400),
    ({"presets": {"p": {"tracks": []}}}, 400),
    ({"presets": {"../p": {}}}, 400),
    ({"name": "x" * 300}, 400),
])
def test_a_mix_is_checked(t, body, status):
    assert t.client.put(f"/api/me/backups/self/themes/{THEME_ID}", json=body, headers=A1).status_code == status


def test_too_deep_or_too_big_is_refused(t):
    from sonorium.core import backups
    deep = {}
    node = deep
    for _ in range(30):
        node["x"] = {}
        node = node["x"]
    url = f"/api/me/backups/self/themes/{THEME_ID}"
    assert t.client.put(url, json={"plugins": deep}, headers=A1).status_code == 400
    with patch.object(backups, "MAX_JSON_BYTES", 1000):
        r = t.client.put(url, json={"name": "n", "tracks": {"Drone": {"note": "y" * 2000}}}, headers=A1)
    assert r.status_code == 413


def test_bad_keys_and_device_ids(t):
    assert t.client.put("/api/me/backups/self/themes/%2E%2E", json={}, headers=A1).status_code in (400, 404)
    assert t.client.get("/api/me/backups/self/themes/a%0Ab", headers=A1).status_code == 400


def test_playlists_round_trip(t):
    r = t.client.put("/api/me/backups/self/playlists", json={"playlists": [{"name": "Evening"}]}, headers=A1)
    assert r.status_code == 200 and r.json()["playlists"] == 1
    assert t.client.get("/api/me/backups/pa1/playlists", headers=ALICE_WEB).json()["playlists"] == [{"name": "Evening"}]
    assert t.client.put("/api/me/backups/self/playlists", json={"lists": []}, headers=A1).status_code == 400
    assert t.client.get("/api/me/backups/pa2/playlists", headers=A1).status_code == 404


# --- whole themes ---------------------------------------------------------------

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


FILES = {"metadata.json": b'{"id": "phone-1", "name": "Made on a phone"}', "Rain.mp3": b"R" * 5000,
         "Birds/Robin.ogg": b"B" * 3000}


def manifest(files=FILES, name="Made on a phone"):
    return {"name": name, "files": [{"path": p, "size": len(d), "sha256": sha(d)} for p, d in files.items()]}


def test_a_whole_theme_uploads_resumes_and_restores(t):
    from sonorium.core import backups
    base = "/api/me/backups/self/themes/phone-1"
    r = t.client.put(base + "/package", json=manifest(), headers=A1).json()
    assert r["complete"] is False and sorted(f["path"] for f in r["missing"]) == sorted(FILES)
    for path, data in FILES.items():
        if path == "Rain.mp3":
            # half, then a wrong start, then the rest from "received"
            first = t.client.put(f"{base}/files/{path}", content=data[:2000], headers={
                **A1, "Content-Range": f"bytes 0-1999/{len(data)}"}).json()
            assert first["received"] == 2000 and first["complete"] is False
            wrong = t.client.put(f"{base}/files/{path}", content=data[1000:], headers={
                **A1, "Content-Range": f"bytes 1000-{len(data) - 1}/{len(data)}"})
            assert wrong.status_code == 409 and wrong.json()["received"] == 2000
            status = t.client.get(base + "/upload", headers=A1).json()
            assert next(f for f in status["missing"] if f["path"] == path)["received"] == 2000
            r = t.client.put(f"{base}/files/{path}", content=data[2000:], headers={
                **A1, "Content-Range": f"bytes 2000-{len(data) - 1}/{len(data)}"})
        else:
            r = t.client.put(f"{base}/files/{path}", content=data, headers=A1)
        assert r.status_code == 200, r.text
    assert r.json()["complete"] is True
    package = t.client.get("/api/me/backups/pa1/themes/phone-1/package", headers=ALICE_WEB).json()
    assert package["content_hash"] == backups.content_hash(manifest()["files"])
    assert {f["path"] for f in package["files"]} == set(FILES)
    got = t.client.get("/api/me/backups/pa1/themes/phone-1/files/Birds/Robin.ogg", headers=ALICE_WEB)
    assert got.content == FILES["Birds/Robin.ogg"] and got.headers["etag"] == f'"{sha(FILES["Birds/Robin.ogg"])}"'
    part = t.client.get("/api/me/backups/pa1/themes/phone-1/files/Rain.mp3", headers={**ALICE_WEB, "Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == b"R" * 10
    assert t.client.get("/api/me/backups/pa1/themes/phone-1/files/Nope.mp3", headers=ALICE_WEB).status_code == 404
    listing = t.client.get("/api/me/backups", headers=ALICE_WEB).json()
    assert listing["devices"][0]["themes"][0]["file_count"] == 3
    # the same files from another phone: nothing to send, complete at once
    again = t.client.put(base + "/package", json=manifest(), headers=A2).json()
    assert again["complete"] is True and again["missing"] == []
    assert not list(t.audio.rglob("Robin.ogg"))  # never in the server's themes


def test_a_file_that_doesnt_match_its_sha256_is_refused(t):
    base = "/api/me/backups/self/themes/phone-1"
    t.client.put(base + "/package", json=manifest(), headers=A1)
    r = t.client.put(f"{base}/files/Rain.mp3", content=b"X" * 5000, headers=A1)
    assert r.status_code == 422
    assert t.client.put(f"{base}/files/Rain.mp3", content=b"R" * 6000, headers=A1).status_code == 413
    assert t.client.put(f"{base}/files/Other.mp3", content=b"R", headers=A1).status_code == 404
    assert t.client.put(f"{base}/files/Rain.mp3", content=b"R", headers={
        **A1, "Content-Range": "bytes 0-0/7"}).status_code == 400


@pytest.mark.parametrize("path", ["../escape.mp3", "/abs.mp3", "C:/x.mp3", "a\\b.mp3", ".hidden/x.mp3",
                                  "x.exe", "page.html", "a/./b.mp3", "a//b.mp3", "a/b/c/d/e.mp3", "nul\x00.mp3",
                                  "noext", "trail./x.mp3"])
def test_paths_outside_a_theme_or_odd_types_are_refused(t, path):
    body = {"name": "x", "files": [{"path": path, "size": 1, "sha256": "0" * 64}]}
    assert t.client.put("/api/me/backups/self/themes/k/package", json=body, headers=A1).status_code == 400


@pytest.mark.parametrize("files", [
    [{"path": "a.mp3", "size": -1, "sha256": "0" * 64}],
    [{"path": "a.mp3", "size": 1, "sha256": "XYZ"}],
    [{"path": "a.mp3", "size": 1, "sha256": "0" * 64}, {"path": "A.MP3", "size": 1, "sha256": "1" * 64}],
    [{"path": "a.mp3", "size": 1, "sha256": "0" * 64, "extra": 1}],
    [],
])
def test_a_bad_file_list_is_refused(t, files):
    body = {"name": "x", "files": files}
    assert t.client.put("/api/me/backups/self/themes/k/package", json=body, headers=A1).status_code == 400


def test_a_new_upload_keeps_the_last_complete_one_until_it_is_complete(t):
    base = "/api/me/backups/self/themes/phone-1"
    small = {"metadata.json": b"{}"}
    t.client.put(base + "/package", json=manifest(small), headers=A1)
    t.client.put(f"{base}/files/metadata.json", content=b"{}", headers=A1)
    t.client.put(base + "/package", json=manifest(), headers=A1)  # bigger, not sent yet
    package = t.client.get(base + "/package", headers=A1).json()
    assert [f["path"] for f in package["files"]] == ["metadata.json"]


# --- space -----------------------------------------------------------------------

def test_the_space_limit_warns_then_refuses(t):
    t.limit["backup_limit_mb"] = 0.01  # 10 KB
    r = t.client.put("/api/me/backups/self/themes/phone-1/package",
                     json=manifest({"Big.mp3": b"x" * 20000}), headers=A1)
    assert r.status_code == 507 and "Backup space per user" in r.json()["detail"]
    assert r.json()["storage"]["limit_bytes"] == int(0.01 * 1024 * 1024)
    r = t.client.put("/api/me/backups/self/themes/phone-2/package",
                     json=manifest({"Mid.mp3": b"y" * 9600}), headers=A1)
    assert r.status_code == 200
    t.client.put("/api/me/backups/self/themes/phone-2/files/Mid.mp3", content=b"y" * 9600, headers=A1)
    storage = t.client.get("/api/me/backups", headers=A1).json()["storage"]
    assert storage["warning"] is True
    t.limit["backup_limit_mb"] = 0  # no limit
    assert t.client.put("/api/me/backups/self/themes/phone-1/package",
                        json=manifest({"Big.mp3": b"x" * 20000}), headers=A1).status_code == 200


# --- deleting ------------------------------------------------------------------------

def test_deleting_removes_files_no_backup_uses(t):
    base = "/api/me/backups/self/themes/phone-1"
    t.client.put(base + "/package", json=manifest(), headers=A1)
    for path, data in FILES.items():
        t.client.put(f"{base}/files/{path}", content=data, headers=A1)
    t.client.put(base + "/package", json=manifest(), headers=A2)  # shares the files
    account = next(t.root.iterdir())
    assert len(list((account / "blobs").iterdir())) == 3
    assert t.client.delete("/api/me/backups/pa1/themes/phone-1", headers=ALICE_WEB).status_code == 200
    assert len(list((account / "blobs").iterdir())) == 3  # pa2's backup still uses them
    assert t.client.delete("/api/me/backups/pa2", headers=A1).status_code == 200
    assert list((account / "blobs").iterdir()) == []
    assert t.client.get("/api/me/backups", headers=A1).json()["devices"] == []


def test_an_unlinked_phone_keeps_its_backups_and_name_until_the_account_goes(t):
    from sonorium.core import backups
    t.client.put(f"/api/me/backups/self/themes/{THEME_ID}", json=MIX, headers=A1)
    del t.service.phones["pa1"]
    [device] = t.client.get("/api/me/backups", headers=ALICE_WEB).json()["devices"]
    assert device["linked"] is False and device["name"] == "Galaxy S25 Ultra"
    backups.remove_account("user:alice", t.root)
    assert t.client.get("/api/me/backups", headers=ALICE_WEB).json()["devices"] == []


def test_removing_a_user_removes_their_backups():
    source = (ADDON / "sonorium" / "auth" / "users.py").read_text(encoding="utf-8")
    remove = source[source.index("async def remove_user"):source.index("# ------------------------------------------------------------ Zones")]
    assert 'remove_account(f"user:{user_id}"' in remove


# --- loading into server themes -------------------------------------------------------

def test_off_stores_only_and_on_loads_as_ordinary_presets(t):
    url = f"/api/me/backups/self/themes/{THEME_ID}"
    t.client.put(url, json=MIX, headers=A1)
    assert set(server_presets(t)["presets"]) == {"calm", "busy", "quiet"}
    assert t.client.get("/api/me/backups/settings", headers=ALICE_WEB).json() == {
        "load_into_themes": False, "can_load": True}
    r = t.client.put("/api/me/backups/settings", json={"load_into_themes": True}, headers=ALICE_WEB).json()
    assert r["load_into_themes"] is True and r["loaded"] == [{"theme_id": THEME_ID, "presets": 2, "sequences": 1}]
    presets = server_presets(t)["presets"]
    night = next(p for p in presets.values() if p["name"] == "Night mix (Galaxy S25 Ultra)")
    assert night["is_default"] is False
    assert night["tracks"] == {"Drone": {"volume": 0.4, "muted": False}, "Wind": {"presence": 1.0}}
    assert night["groups"] == {"Birds": {"volume": 0.5, "gap_min": 2.0}}
    assert presets["busy"]["is_default"] is True  # the theme's default stays
    sequences = server_presets(t)["sequences"]
    evening = next(s for s in sequences.values() if s["name"] == "Evening (Galaxy S25 Ultra)")
    day_id = next(i for i, p in presets.items() if p["name"] == "Day (Galaxy S25 Ultra)")
    night_id = next(i for i, p in presets.items() if p is night)
    assert evening["presets"] == [day_id, night_id] and evening["at_end"] == "stop"
    # the metadata manager's copy follows (the server sees them at once)
    assert night_id in t.manager.get_metadata(THEME_ID).presets


def test_a_user_who_may_not_edit_themes_cant_switch_it_on(t):
    r = t.client.put("/api/me/backups/settings", json={"load_into_themes": True}, headers=browser("bob", USER))
    assert r.status_code == 403
    assert t.client.get("/api/me/backups/settings", headers=B1).json()["can_load"] is False
    assert t.client.put("/api/me/backups/settings", json={"load_into_themes": "yes"}, headers=ALICE_WEB).status_code == 400


def test_while_on_each_backup_loads_and_the_next_follows_the_rules(t):
    url = f"/api/me/backups/self/themes/{THEME_ID}"
    t.client.put("/api/me/backups/settings", json={"load_into_themes": True}, headers=ALICE_WEB)
    first = {"presets": {"night": {"name": "Night", "tracks": {"Drone": {"volume": 0.4}}}}}
    assert t.client.put(url, json=first, headers=A1).json()["loaded"][0]["presets"] == 1

    def named(name):
        return [i for i, p in server_presets(t)["presets"].items() if p["name"].startswith(name)]
    [night] = named("Night (Galaxy")
    # unchanged on the phone: nothing happens
    assert t.client.put(url, json=first, headers=A1).json()["loaded"][0]["presets"] == 0
    # changed, server copy not edited: updated in place
    second = {"presets": {"night": {"name": "Night", "tracks": {"Drone": {"volume": 0.2}}}}}
    t.client.put(url, json=second, headers=A1)
    assert named("Night (Galaxy") == [night]
    assert server_presets(t)["presets"][night]["tracks"]["Drone"]["volume"] == 0.2
    # edited on the server: the next change from the phone is a new copy, the edit stays
    meta = t.manager.get_metadata(THEME_ID)
    meta.presets[night]["tracks"]["Drone"]["volume"] = 0.9
    t.manager.save_metadata(THEME_ID, meta)
    third = {"presets": {"night": {"name": "Night", "tracks": {"Drone": {"volume": 0.1}}}}}
    t.client.put(url, json=third, headers=A1)
    copies = named("Night (Galaxy")
    assert len(copies) == 2 and server_presets(t)["presets"][night]["tracks"]["Drone"]["volume"] == 0.9
    new = next(i for i in copies if i != night)
    assert server_presets(t)["presets"][new]["name"] == "Night (Galaxy S25 Ultra) 2"
    # another phone's presets are its own
    t.client.put(url, json=first, headers=A2)
    assert len(named("Night (Pixel 9)")) == 1
    # gone from the phone: the server copies stay
    t.client.put(url, json={"presets": {}}, headers=A1)
    assert len(named("Night (Galaxy")) == 2


def test_only_a_theme_with_exactly_that_id_is_used(t):
    t.client.put("/api/me/backups/settings", json={"load_into_themes": True}, headers=ALICE_WEB)
    before = (t.folder / "presets.json").read_text(encoding="utf-8")
    for key in ("Test Theme", "testtheme", "test-theme", "ENGINE-TEST-THEME"):
        r = t.client.put(f"/api/me/backups/self/themes/{key}", json=MIX, headers=A1).json()
        assert r["loaded"][0].get("skipped"), key
    assert (t.folder / "presets.json").read_text(encoding="utf-8") == before


def test_plugin_attributes_load_only_as_their_schema_allows(t):
    t.client.put("/api/me/backups/settings", json={"load_into_themes": True}, headers=ALICE_WEB)
    mix = {"presets": {"p": {"name": "P", "tracks": {}, "plugins": {"not_installed": {"x": 1}}}}}
    t.client.put(f"/api/me/backups/self/themes/{THEME_ID}", json=mix, headers=A1)
    loaded = next(p for p in server_presets(t)["presets"].values() if p["name"] == "P (Galaxy S25 Ultra)")
    assert "plugins" not in loaded
