"""
Theme names are unique (core/theme_names.py) and a theme can be duplicated
(core/theme_duplicate.py, POST /api/themes/{id}/duplicate): the copy has its
own id, folder, audio files, metadata and presets, and changing one never
changes the other. Create, rename, import and duplicate refuse a taken name
(trimmed, case ignored). Several zips imported one after another through the
import route, one of them failing, as the web UI's bulk import sends them.
"""

import io
import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "sonorium_addon"
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from engine_theme import make_theme, sine, write_wav  # noqa: E402

TAKEN = "A theme with this name already exists"


def _ours(name):
    return name == "sonorium" or name.startswith("sonorium.")


@pytest.fixture(autouse=True)
def _real_sonorium():
    """The add-on's real package for the test; whatever was imported before is put back after."""
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


@pytest.fixture
def library(tmp_path):
    """"Test Theme" (groups, presets, sequences) and "Linker", which links Test Theme's Drone."""
    root = tmp_path / "audio"
    source = make_theme(root)
    linker = root / "Linker"
    write_wav(linker / "Hum.wav", sine(110.0, 1.0))
    (linker / "metadata.json").write_text(json.dumps({
        "spec_version": 2, "id": "linker-theme", "name": "Linker", "is_favorite": True,
        "categories": ["Night"], "description": "Hums", "icon": "🌙",
        "tracks": {"Hum": {"volume": 0.5}, "Low/Drone": {"volume": 0.7}},
        "links": {"Low/Drone": {"theme": "engine-test-theme", "track": "Drone"}},
    }), encoding="utf-8")
    (linker / "presets.json").write_text(json.dumps({"presets": {"soft": {"name": "Soft", "tracks": {}}}}),
                                         encoding="utf-8")
    (linker / "_originals").mkdir()
    (linker / "_originals" / "Hum.wav").write_bytes(b"kept original")
    return root, source, linker


@pytest.fixture
def client(library):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import sonorium.paths
    from sonorium.web import api_v2

    root = library[0]
    with patch.object(sonorium.paths, "audio_dir", lambda: root):
        router = api_v2.create_api_router(
            session_manager=MagicMock(), group_manager=MagicMock(), ha_registry=MagicMock(),
            state_store=MagicMock(), channel_manager=MagicMock(), plugin_manager=MagicMock(),
            mqtt_manager=MagicMock())
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as c:
            yield c


def _meta(folder: Path) -> dict:
    return json.loads((folder / "metadata.json").read_text(encoding="utf-8"))


def _files(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*")
            if p.is_file() and p.name not in ("metadata.json",)}


# --- names ----------------------------------------------------------------------

def test_next_copy_name_takes_the_lowest_free_number():
    from sonorium.core.theme_names import next_copy_name
    assert next_copy_name("Rain", []) == "Rain 01"
    assert next_copy_name("Rain", ["Rain", "rain 01", " RAIN 02 "]) == "Rain 03"
    assert next_copy_name("Rain", ["Rain 02"]) == "Rain 01"          # a gap is used first
    assert next_copy_name("  Rain  ", ["rain 01"]) == "Rain 02"      # trimmed
    many = [f"Rain {n:02d}" for n in range(1, 100)]
    assert next_copy_name("Rain", many) == "Rain 100"                  # 100 and up keep counting
    assert next_copy_name("Rain", many + ["Rain 100"]) == "Rain 101"


def test_names_are_compared_trimmed_and_case_ignored(library):
    from sonorium.core import theme_names
    root, source, linker = library
    assert theme_names.is_taken(root, "  test THEME ")
    assert theme_names.is_taken(root, "linker")
    assert not theme_names.is_taken(root, "Test Theme 01")
    assert not theme_names.is_taken(root, "test theme", except_folder=source)  # its own name
    # A folder without metadata.json is named after the folder; hidden folders aren't themes
    write_wav(root / "Plain" / "a.wav", sine(300.0, 0.2))
    (root / ".hidden").mkdir()
    (root / ".hidden" / "metadata.json").write_text('{"name": "Ghost"}', encoding="utf-8")
    assert theme_names.is_taken(root, "PLAIN") and not theme_names.is_taken(root, "Ghost")


def test_a_reserved_name_is_taken_until_released(library):
    from sonorium.core import theme_names
    root = library[0]
    with theme_names.reserve(root, "Storm"):
        assert theme_names.is_taken(root, " storm ")
        with pytest.raises(theme_names.NameTaken):
            with theme_names.reserve(root, "STORM"):
                pass
    assert not theme_names.is_taken(root, "Storm")
    with pytest.raises(theme_names.NameTaken):
        with theme_names.reserve(root, "linker"):
            pass


# --- duplicate --------------------------------------------------------------------

def test_duplicate_is_a_full_copy_with_a_new_id(client, library):
    root, source, _ = library
    before = {p.name for p in root.iterdir()}
    r = client.post("/api/themes/engine-test-theme/duplicate", json={"name": "  Test Theme 01  "})
    assert r.status_code == 200, r.text
    body = r.json()
    new_id = body["theme_id"]
    assert new_id and new_id != "engine-test-theme" and body["theme"]["id"] == new_id
    assert body["theme"]["name"] == "Test Theme 01" and body["theme"]["is_favorite"] is False
    copy = root / body["theme"]["folder"]
    assert copy.name == "test_theme_01"                       # named as Create theme names folders
    assert {p.name for p in root.iterdir()} == before | {copy.name}   # no staging folder left
    # Every audio file (groups too), presets.json with presets and sequences
    assert _files(copy) == _files(source)
    assert (copy / "Birds" / "Bird 2.wav").exists()
    presets = json.loads((copy / "presets.json").read_text(encoding="utf-8"))
    assert set(presets["presets"]) == {"calm", "busy", "quiet"} and presets["sequences"]
    # metadata.json as it was, but id, name and is_favorite
    meta, original = _meta(copy), _meta(source)
    assert meta["id"] == new_id and meta["name"] == "Test Theme 01" and meta["is_favorite"] is False
    assert {k: v for k, v in meta.items() if k not in ("id", "name", "is_favorite")} == \
        {k: v for k, v in original.items() if k not in ("id", "name", "is_favorite")}
    assert original["id"] == "engine-test-theme" and original["name"] == "Test Theme"


def test_duplicate_keeps_links_categories_and_not_favorite(client, library):
    root, _, linker = library
    r = client.post("/api/themes/linker-theme/duplicate", json={"name": "Linker 01"})
    assert r.status_code == 200, r.text
    theme = r.json()["theme"]
    assert theme["categories"] == ["Night"] and theme["icon"] == "🌙" and theme["description"] == "Hums"
    copy = root / theme["folder"]
    meta = _meta(copy)
    assert meta["links"] == {"Low/Drone": {"theme": "engine-test-theme", "track": "Drone"}}  # still a link
    assert not (copy / "Low").exists()                       # the linked file isn't copied in
    assert meta["is_favorite"] is False and _meta(linker)["is_favorite"] is True
    assert not (copy / "_originals").exists()                # kept originals aren't copied
    assert (copy / "Hum.wav").exists()


def test_duplicate_and_source_change_independently(client, library):
    from sonorium.core import theme_edit
    root, source, _ = library
    copy = root / client.post("/api/themes/engine-test-theme/duplicate",
                              json={"name": "Copy"}).json()["theme"]["folder"]
    theme_edit.rename_theme(copy, "Copy renamed")
    theme_edit.set_track(copy, "Drone", volume=0.25)
    (copy / "Wind.wav").unlink()
    presets = json.loads((copy / "presets.json").read_text(encoding="utf-8"))
    presets["presets"].pop("calm")
    (copy / "presets.json").write_text(json.dumps(presets), encoding="utf-8")
    assert _meta(source)["name"] == "Test Theme"
    assert _meta(source)["tracks"]["Drone"]["volume"] == 0.8
    assert (source / "Wind.wav").exists()
    assert "calm" in json.loads((source / "presets.json").read_text(encoding="utf-8"))["presets"]
    # And the other way round
    theme_edit.set_track(source, "Chirp", volume=0.05)
    assert _meta(copy)["tracks"]["Chirp"]["volume"] == 0.7


def test_duplicate_refuses_a_taken_name_and_writes_nothing(client, library):
    root = library[0]
    before = sorted(p.name for p in root.iterdir())
    for name in ("Test Theme", " test theme ", "LINKER"):
        r = client.post("/api/themes/engine-test-theme/duplicate", json={"name": name})
        assert r.status_code == 409 and r.json()["detail"] == TAKEN
    assert client.post("/api/themes/engine-test-theme/duplicate", json={"name": "  "}).status_code == 400
    assert client.post("/api/themes/engine-test-theme/duplicate", json={}).status_code == 400
    assert client.post("/api/themes/nope/duplicate", json={"name": "X"}).status_code == 404
    assert sorted(p.name for p in root.iterdir()) == before
    # A second copy with the same name is refused; another name whose folder
    # name is in use gets "_1" after it
    first = client.post("/api/themes/linker-theme/duplicate", json={"name": "Night"})
    assert first.status_code == 200 and first.json()["theme"]["folder"] == "night"
    assert client.post("/api/themes/linker-theme/duplicate", json={"name": "night"}).status_code == 409
    r = client.post("/api/themes/linker-theme/duplicate", json={"name": "Night!"})
    assert r.status_code == 200 and r.json()["theme"]["folder"] == "night_1"
    assert _meta(root / "night_1")["name"] == "Night!"


# --- create and rename ------------------------------------------------------------

def test_create_refuses_a_taken_name(client, library):
    root = library[0]
    for name in ("Test Theme", "  test theme  ", "LINKER"):
        r = client.post("/api/themes/create", json={"name": name})
        assert r.status_code == 409 and r.json()["detail"] == TAKEN
    r = client.post("/api/themes/create", json={"name": "Fresh"})
    assert r.status_code == 200 and _meta(root / "fresh")["name"] == "Fresh"
    assert client.post("/api/themes/create", json={"name": "FRESH "}).status_code == 409
    # A free name whose folder is another theme's: a folder beside it (was 409 "folder exists")
    (root / "fresh_note").mkdir()
    (root / "fresh_note" / "metadata.json").write_text('{"name": "Renamed long ago"}', encoding="utf-8")
    r = client.post("/api/themes/create", json={"name": "Fresh note"})
    assert r.status_code == 200 and Path(r.json()["path"]).name == "fresh_note_1"


def test_rename_refuses_a_taken_name(library):
    from sonorium.core import theme_edit
    _, source, linker = library
    for name in ("Linker", " linker ", "LINKER"):
        with pytest.raises(theme_edit.Conflict, match=TAKEN):
            theme_edit.rename_theme(source, name)
    assert _meta(source)["name"] == "Test Theme"
    # Its own name in another case, or a free one, is fine
    assert theme_edit.rename_theme(source, "test THEME") == ("Test Theme", "test THEME")
    assert theme_edit.rename_theme(linker, "Hum") == ("Linker", "Hum")
    with pytest.raises(theme_edit.Conflict):
        theme_edit.set_details(source, name="hum")


# --- import, several zips one after another -----------------------------------------

def _zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _theme_zip(name: str, theme_id: str) -> bytes:
    meta = json.dumps({"spec_version": 2, "id": theme_id, "name": name, "tracks": {"Rain": {"volume": 0.5}}})
    return _zip({"metadata.json": meta, "Rain.wav": b"rain", "Wind/Gust.wav": b"gust"})


def test_bulk_import_one_after_another_with_failures(client, library):
    root = library[0]
    batch = [
        ("Storm.zip", _theme_zip("Storm", "storm-id")),
        ("Broken.zip", b"this is not a zip"),
        ("Same storm.zip", _theme_zip("  storm ", "storm-2")),      # same name as the first: refused
        ("Linker.zip", _theme_zip("Linker", "x")),                  # a theme that was already there
        ("Notes.txt", b"not a zip"),
        ("Night rain.zip", _zip({"Rain.wav": b"rain"})),            # no metadata.json: the zip's name
        ("Fog.zip", _theme_zip("Fog", "fog-id")),
    ]
    results = []
    for filename, data in batch:
        r = client.post("/api/themes/import", files={"file": (filename, data, "application/zip")})
        results.append((filename, r.status_code, r.json()))
    codes = {f: code for f, code, _ in results}
    assert codes == {"Storm.zip": 200, "Broken.zip": 400, "Same storm.zip": 409, "Linker.zip": 409,
                     "Notes.txt": 400, "Night rain.zip": 200, "Fog.zip": 200}
    details = {f: body.get("detail") for f, code, body in results if code != 200}
    assert details["Same storm.zip"] == TAKEN and details["Linker.zip"] == TAKEN
    assert details["Broken.zip"] == "Invalid zip file"
    assert details["Notes.txt"] == "File must be a .zip archive"
    imported = {f: body for f, code, body in results if code == 200}
    assert imported["Storm.zip"]["name"] == "Storm" and imported["Night rain.zip"]["name"] == "Night rain"
    for body in imported.values():
        folder = root / body["theme_folder"]
        meta = _meta(folder)
        assert meta["id"] == body["theme_id"] and meta["name"] == body["name"]
        assert (folder / "Rain.wav").read_bytes() == b"rain"
    assert not (root / "Storm_1").exists() and not (root / "Linker_1").exists()


def test_import_without_metadata_keeps_the_name_when_its_folder_is_taken(client, library):
    root = library[0]
    # A theme renamed long ago still has the folder "Night rain"
    (root / "Night rain").mkdir()
    (root / "Night rain" / "metadata.json").write_text('{"name": "Old rain"}', encoding="utf-8")
    r = client.post("/api/themes/import", files={"file": ("Night rain.zip", _zip({"Rain.wav": b"r"}), "application/zip")})
    assert r.status_code == 200 and r.json()["theme_folder"] == "Night rain_1"
    assert _meta(root / "Night rain_1")["name"] == "Night rain"
