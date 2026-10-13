"""
Theme packages for offline playback (docs/APP_API.md section 10): the
listing, the file list with checksums and its content hash, single files with
Range and ETag, the zip export from a temporary file, and the reference
render, through the real routes on a generated theme (tests/engine_theme.py).
"""

import hashlib
import io
import json
import sys
import wave
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

from engine_theme import FAST_VALUES, make_theme, sine, write_wav  # noqa: E402


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


LINKER_ID = "linker-theme"


@pytest.fixture
def library(tmp_path):
    """Two themes: "Test Theme" and "Linker", which links Test Theme's Drone into its group "Low"."""
    root = tmp_path / "audio"
    source = make_theme(root)
    linker = root / "Linker"
    write_wav(linker / "Hum.wav", sine(110.0, 2.0))
    write_wav(linker / "Low" / "Rumble.wav", sine(55.0, 2.0))
    (linker / "ATTRIBUTION.md").write_text("Made for the tests\n", encoding="utf-8")
    (linker / "MANIFEST.json").write_text(json.dumps({
        "source": {"site": "example.org", "url": "https://example.org/linker", "creator": "tests"},
        "license": {"name": "CC0 1.0", "url": "https://creativecommons.org/publicdomain/zero/1.0/",
                    "requires_attribution": False}}), encoding="utf-8")
    (linker / "metadata.json").write_text(json.dumps({
        "spec_version": 2, "id": LINKER_ID, "name": "Linker",
        "tracks": {"Hum": {"volume": 0.5}, "Low/Rumble": {"volume": 0.4}, "Low/Drone": {"volume": 0.7}},
        "links": {"Low/Drone": {"theme": "engine-test-theme", "track": "Drone"}},
    }), encoding="utf-8")
    (linker / "presets.json").write_text(json.dumps({"presets": {"soft": {"name": "Soft", "tracks": {}}}}),
                                         encoding="utf-8")
    (linker / "metadata.json.bak").write_text("{}", encoding="utf-8")  # a backup: never in the package
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
        from sonorium import playback_values
        playback_values.set_source(lambda: dict(FAST_VALUES))
        app = FastAPI()

        @app.get("/api/themes/packages")
        async def packages():  # api.py's WebApp route, without the WebApp
            from sonorium.core.theme_package import list_packages
            return {"packages": list_packages(root)}

        app.include_router(router)
        with TestClient(app) as c:
            yield c


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- listing ------------------------------------------------------------------

def test_listing_has_every_theme_with_hash_size_and_licence(client):
    packages = client.get("/api/themes/packages").json()["packages"]
    assert [p["name"] for p in packages] == ["Linker", "Test Theme"]
    linker = packages[0]
    assert linker["theme_id"] == LINKER_ID and linker["spec_version"] == 2
    assert linker["content_hash"].startswith("sha256:") and len(linker["content_hash"]) == 7 + 64
    assert linker["license"]["name"] == "CC0 1.0" and linker["redistributable"] is True
    assert linker["source"]["site"] == "example.org"
    assert linker["track_count"] == 3  # Hum, Low/Rumble and the linked Low/Drone
    package = client.get(f"/api/themes/{LINKER_ID}/package").json()
    assert linker["file_count"] == len(package["files"])
    assert linker["size_bytes"] == sum(f["size"] for f in package["files"])
    assert linker["content_hash"] == package["content_hash"]
    test_theme = packages[1]
    assert test_theme["license"] is None and test_theme["source"] is None and test_theme["redistributable"] is None


def test_redistributable_rules():
    from sonorium.core.theme_package import redistributable
    assert redistributable(None) is None
    assert redistributable({"name": "Creative Commons Sampling Plus 1.0"}) is True
    assert redistributable({"name": "All rights reserved"}) is False
    assert redistributable({"name": "Some licence"}) is None
    assert redistributable({"name": "CC BY 4.0", "redistributable": False}) is False


# --- manifest and content hash ------------------------------------------------

def test_manifest_lists_the_export_files_with_checksums(client, library):
    _, source, linker = library
    body = client.get(f"/api/themes/{LINKER_ID}/package").json()
    paths = [f["path"] for f in body["files"]]
    assert paths == sorted(paths)
    assert set(paths) == {"ATTRIBUTION.md", "Hum.wav", "Low/Drone.wav", "Low/Rumble.wav", "MANIFEST.json",
                          "metadata.json", "presets.json"}
    by_path = {f["path"]: f for f in body["files"]}
    # The linked file is the source theme's file, at its exported path
    drone = (source / "Drone.wav").read_bytes()
    assert by_path["Low/Drone.wav"] == {"path": "Low/Drone.wav", "size": len(drone), "sha256": _sha(drone)}
    assert by_path["Hum.wav"]["sha256"] == _sha((linker / "Hum.wav").read_bytes())
    # metadata.json as exported: links dropped, their settings kept
    assert "links" not in body["metadata"] and body["metadata"]["tracks"]["Low/Drone"]["volume"] == 0.7
    assert body["presets"]["presets"]["soft"]["name"] == "Soft"
    assert body["playback_values"]["group_gap"] == 2.0
    # The hash definition
    lines = "".join(f"{f['path']}\t{f['size']}\t{f['sha256']}\n" for f in body["files"])
    assert body["content_hash"] == "sha256:" + _sha(lines.encode("utf-8"))


def test_hash_is_stable_and_changes_with_any_byte(client, library):
    _, source, linker = library
    first = client.get(f"/api/themes/{LINKER_ID}/package").json()["content_hash"]
    assert client.get(f"/api/themes/{LINKER_ID}/package").json()["content_hash"] == first
    # Same files in another folder: the same hash
    import shutil
    from sonorium.core.theme_package import build_package
    from sonorium.core.intrusions import folder_for_id_in
    copy_root = linker.parent.parent / "copy"
    shutil.copytree(linker.parent, copy_root)
    assert build_package(copy_root / "Linker", folder_for_id=folder_for_id_in(copy_root)).content_hash == first
    # One byte of an own file
    data = bytearray((linker / "Hum.wav").read_bytes())
    data[-1] ^= 1
    (linker / "Hum.wav").write_bytes(bytes(data))
    second = client.get(f"/api/themes/{LINKER_ID}/package").json()["content_hash"]
    assert second != first
    # One byte of the linked file (another theme's)
    data = bytearray((source / "Drone.wav").read_bytes())
    data[-1] ^= 1
    (source / "Drone.wav").write_bytes(bytes(data))
    third = client.get(f"/api/themes/{LINKER_ID}/package").json()["content_hash"]
    assert third not in (first, second)
    # A setting (metadata.json)
    meta = json.loads((linker / "metadata.json").read_text(encoding="utf-8"))
    meta["tracks"]["Hum"]["volume"] = 0.6
    (linker / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    assert client.get(f"/api/themes/{LINKER_ID}/package").json()["content_hash"] not in (first, second, third)


def test_unknown_theme_is_404(client):
    assert client.get("/api/themes/nope/package").status_code == 404
    assert client.get("/api/themes/nope/files/Hum.wav").status_code == 404
    assert client.get("/api/themes/%2E%2E/package").status_code == 404  # never the audio folder's parent
    assert client.get("/api/themes/%2E/package").status_code == 404


# --- single files ---------------------------------------------------------------

def test_every_file_downloads_with_its_checksum(client):
    body = client.get(f"/api/themes/{LINKER_ID}/package").json()
    for f in body["files"]:
        r = client.get(f"/api/themes/{LINKER_ID}/files/{f['path']}")
        assert r.status_code == 200, f["path"]
        assert _sha(r.content) == f["sha256"]
        assert r.headers["etag"] == f'"{f["sha256"]}"'
        assert int(r.headers["content-length"]) == f["size"]
    # metadata.json served is the exported one
    meta = client.get(f"/api/themes/{LINKER_ID}/files/metadata.json").json()
    assert meta == body["metadata"]


def test_range_resume_and_etag(client, library):
    full = (library[2] / "Hum.wav").read_bytes()
    url = f"/api/themes/{LINKER_ID}/files/Hum.wav"
    r = client.get(url, headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == full[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{len(full)}" and r.headers["content-length"] == "100"
    r = client.get(url, headers={"Range": "bytes=1000-"})
    assert r.status_code == 206 and r.content == full[1000:]
    r = client.get(url, headers={"Range": "bytes=-10"})
    assert r.status_code == 206 and r.content == full[-10:]
    assert client.get(url, headers={"Range": f"bytes={len(full)}-"}).status_code == 416
    etag = client.get(url).headers["etag"]
    assert client.get(url, headers={"If-None-Match": etag}).status_code == 304
    # If-Range with an old ETag: the whole (changed) file
    r = client.get(url, headers={"Range": "bytes=0-9", "If-Range": '"old"'})
    assert r.status_code == 200 and r.content == full
    r = client.get(url, headers={"Range": "bytes=0-9", "If-Range": etag})
    assert r.status_code == 206 and r.content == full[:10]


@pytest.mark.parametrize("path", ["nope.wav", "metadata.json.bak", "../Test Theme/Drone.wav", "..%2FTest%20Theme%2FDrone.wav",
                                  "Low/%2E%2E/Hum.wav", "%2E%2E/Test%20Theme/Drone.wav",
                                  "%2E%2E%2F%2E%2E%2Fsecret.txt", "/etc/passwd", "Drone.wav", "low/rumble.wav"])
def test_paths_not_in_the_manifest_are_404(client, library, path):
    (library[0].parent / "secret.txt").write_text("secret", encoding="utf-8")
    assert client.get(f"/api/themes/{LINKER_ID}/files/{path}").status_code == 404


# --- zip export -----------------------------------------------------------------

def test_export_is_a_file_with_content_length_matching_the_package(client):
    body = client.get(f"/api/themes/{LINKER_ID}/package").json()
    r = client.get(f"/api/themes/{LINKER_ID}/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert int(r.headers["content-length"]) == len(r.content)
    assert 'filename="Linker.zip"' in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        files = {n[len("Linker/"):]: zf.read(n) for n in zf.namelist()}
    assert {path: _sha(data) for path, data in files.items()} == {f["path"]: f["sha256"] for f in body["files"]}


def test_export_leaves_no_temporary_file(client, tmp_path):
    import tempfile
    with patch.object(tempfile, "tempdir", str(tmp_path / "tmp")):
        (tmp_path / "tmp").mkdir()
        assert client.get(f"/api/themes/{LINKER_ID}/export").status_code == 200
        assert list((tmp_path / "tmp").iterdir()) == []


# --- the documents are what the server uses ------------------------------------

def _add_stale_entries(linker):
    """Linker's metadata.json with entries no track has: old "<file>.ext" keys and a removed file."""
    meta = json.loads((linker / "metadata.json").read_text(encoding="utf-8"))
    meta["tracks"]["Hum"]["plugins"] = {"some-plugin": {"colour": "amber"}}
    meta["tracks"].update({"Hum.wav": {"volume": 0.1}, "Low/Rumble.mp3": {"volume": 0.2}, "Gone": {"muted": True}})
    (linker / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    presets = {"presets": {"soft": {"name": "Soft", "tracks": {"Hum": {"volume": 0.3}, "Hum.wav": {"volume": 0.4}}}}}
    (linker / "presets.json").write_text(json.dumps(presets), encoding="utf-8")
    return (linker / "metadata.json").read_bytes(), (linker / "presets.json").read_bytes()


def test_package_and_export_leave_out_entries_without_a_file(client, library):
    _, _, linker = library
    on_disk = _add_stale_entries(linker)
    names_before = sorted(p.name for p in linker.iterdir())
    body = client.get(f"/api/themes/{LINKER_ID}/package").json()
    tracks = body["metadata"]["tracks"]
    assert set(tracks) == {"Hum", "Low/Rumble", "Low/Drone"}
    assert tracks["Hum"]["volume"] == 0.5 and tracks["Low/Rumble"]["volume"] == 0.4
    assert tracks["Hum"]["plugins"] == {"some-plugin": {"colour": "amber"}}  # plugin attributes pass through
    assert body["presets"]["presets"]["soft"]["tracks"] == {"Hum": {"volume": 0.3}}
    # The served metadata.json is those documents, and the hash is of what is served
    served = client.get(f"/api/themes/{LINKER_ID}/files/metadata.json").content
    assert json.loads(served) == body["metadata"]
    by_path = {f["path"]: f for f in body["files"]}
    assert by_path["metadata.json"]["sha256"] == _sha(served)
    lines = "".join(f"{f['path']}\t{f['size']}\t{f['sha256']}\n" for f in body["files"])
    assert body["content_hash"] == "sha256:" + _sha(lines.encode("utf-8"))
    # The export holds the same files
    with zipfile.ZipFile(io.BytesIO(client.get(f"/api/themes/{LINKER_ID}/export").content)) as zf:
        assert zf.read("Linker/metadata.json") == served
    # Nothing is written to the theme folder
    assert ((linker / "metadata.json").read_bytes(), (linker / "presets.json").read_bytes()) == on_disk
    assert sorted(p.name for p in linker.iterdir()) == names_before


def test_package_documents_follow_a_file_added_later(client, library):
    _, _, linker = library
    _add_stale_entries(linker)
    first = client.get(f"/api/themes/{LINKER_ID}/package").json()
    assert "Low/Hiss" not in first["metadata"]["tracks"]
    import shutil
    shutil.copy2(linker / "Hum.wav", linker / "Low" / "Hiss.wav")
    second = client.get(f"/api/themes/{LINKER_ID}/package").json()
    assert second["metadata"]["tracks"]["Low/Hiss"]["playback_mode"] == "auto"  # a new file's default entry
    assert second["content_hash"] != first["content_hash"]


def test_export_documents_of_a_1_0_theme_with_old_keys(tmp_path):
    """A bundled 1.0 theme (as Tavern was): "<file>.mp3" keys beside the converted ones, Interval values."""
    from sonorium.core.theme_metadata import theme_documents_for_export
    folder = tmp_path / "Tavern"
    write_wav(folder / "Fire.wav", sine(220.0, 1.0))
    write_wav(folder / "Door.wav", sine(440.0, 1.0))
    raw = {"id": "tavern", "name": "Tavern", "tracks": {
        "Fire.mp3": {"presence": 1.0, "playback_mode": "auto"}, "Door.mp3": {"presence": 1.0, "playback_mode": "auto"},
        "Fire": {"presence": 1.0, "playback_mode": "continuous"}, "Door": {"presence": 0.3, "playback_mode": "presence"}},
        "presets": {"p": {"name": "P", "tracks": {"Door.mp3": {"volume": 0.2}, "Door": {"presence": 0.25}}}}}
    (folder / "metadata.json").write_text(json.dumps(raw), encoding="utf-8")
    meta, doc = theme_documents_for_export(folder)
    assert set(meta["tracks"]) == {"Fire", "Door"} and meta["spec_version"] == 2
    assert meta["ebb_duration"] is True and meta["tracks"]["Door"]["presence"] == 0.7  # Interval became Duration
    assert doc["presets"]["p"]["tracks"] == {"Door": {"presence": 0.25}}  # as the server loads it (no mode in the preset)
    from sonorium.core.theme_metadata import load_theme_folder
    loaded = load_theme_folder(folder, convert=False)  # the server's reading, in memory
    assert meta["tracks"]["Door"] == loaded.tracks["Door"].to_dict()
    assert doc["presets"]["p"]["tracks"]["Door"] == loaded.presets["p"]["tracks"]["Door"]
    assert json.loads((folder / "metadata.json").read_text(encoding="utf-8")) == raw  # written by nobody
    assert sorted(p.name for p in folder.iterdir()) == ["Door.wav", "Fire.wav", "metadata.json"]


# --- reference render -------------------------------------------------------------

def test_render_is_deterministic_wav(client):
    url = "/api/themes/engine-test-theme/render"
    one = client.post(url, json={"seconds": 8, "seed": 42})
    assert one.status_code == 200 and one.headers["content-type"] == "audio/wav"
    two = client.post(url, json={"seconds": 8, "seed": 42})
    assert one.content == two.content
    with wave.open(io.BytesIO(one.content)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 44100, 8 * 44100)
    other = client.post(url, json={"seconds": 8, "seed": 43})
    assert other.content != one.content


def test_render_checks_its_input_and_rate(client):
    url = "/api/themes/engine-test-theme/render"
    assert client.post(url, json={"seconds": 121}).status_code == 400
    assert client.post(url, json={"seconds": 0}).status_code == 400
    assert client.post(url, json={"seed": "x"}).status_code == 400
    assert client.post(url, json={"preset_id": "nope", "seconds": 1}).status_code == 404
    assert client.post("/api/themes/nope/render", json={"seconds": 1}).status_code == 404
    codes = [client.post(url, json={"seconds": 1}).status_code for _ in range(4)]
    assert 429 in codes


def test_render_is_admin_only_and_the_rest_is_view():
    from sonorium.auth import accounts, permissions as P
    render = P.for_route("POST", "/api/themes/{theme_id}/render")
    assert render.any_of <= P.ADMIN_ONLY
    assert not (render.any_of & accounts.role_perms("user"))   # a User (and their phone) can't
    assert not (render.any_of & P.ABILITIES)                   # no key can
    assert render.any_of & P.device_perms(accounts.role_perms("admin"))  # an admin's phone can
    for path in ("/api/themes/packages", "/api/themes/{theme_id}/package", "/api/themes/{theme_id}/files/{path:path}"):
        assert P.for_route("GET", path).any_of == {"th.view"}


# --- import ---------------------------------------------------------------------

def _zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _import(client, data: bytes, filename="Storm.zip"):
    return client.post("/api/themes/import", files={"file": (filename, data, "application/zip")})


def _storm(prefix=""):
    meta = json.dumps({"spec_version": 2, "id": "storm-id", "name": "Thunder Storm",
                       "tracks": {"Rain": {"playback_mode": "continuous"}, "Wind/Gust": {"volume": 0.5}}})
    return {f"{prefix}metadata.json": meta, f"{prefix}Rain.wav": b"rain",
            f"{prefix}Wind/Gust.wav": b"gust", f"{prefix}Wind/Breeze.wav": b"breeze"}


def test_import_a_zip_with_one_top_folder(client, library):
    root = library[0]
    r = _import(client, _zip({"Storm folder/": b"", **_storm("Storm folder/")}))
    assert r.status_code == 200 and r.json()["theme_folder"] == "Storm folder"
    theme = root / "Storm folder"
    assert (theme / "Rain.wav").read_bytes() == b"rain"
    assert (theme / "Wind" / "Gust.wav").read_bytes() == b"gust" and (theme / "Wind" / "Breeze.wav").exists()
    assert not (theme / "Gust.wav").exists()
    assert r.json()["files_extracted"] == 4


def test_import_a_zip_with_the_files_at_its_root_keeps_the_groups(client, library):
    root = library[0]
    r = _import(client, _zip({"__MACOSX/._Rain.wav": b"junk", ".DS_Store": b"junk", **_storm()}), "download (3).zip")
    assert r.status_code == 200
    assert r.json()["theme_folder"] == "Thunder Storm"  # named after the theme, not the zip
    theme = root / "Thunder Storm"
    assert (theme / "Rain.wav").read_bytes() == b"rain"
    assert (theme / "Wind" / "Gust.wav").read_bytes() == b"gust" and (theme / "Wind" / "Breeze.wav").exists()
    assert not (theme / "Gust.wav").exists() and not (theme / "__MACOSX").exists() and not (theme / ".DS_Store").exists()
    assert json.loads((theme / "metadata.json").read_text(encoding="utf-8"))["tracks"]["Wind/Gust"]["volume"] == 0.5
    # Again: a new folder beside it; without metadata.json the zip's name is used
    assert _import(client, _zip(_storm())).json()["theme_folder"] == "Thunder Storm_1"
    r = _import(client, _zip({"Rain.wav": b"rain", "Wind/Gust.wav": b"gust"}), "Night rain.zip")
    assert r.json()["theme_folder"] == "Night rain" and (root / "Night rain" / "Wind" / "Gust.wav").exists()


@pytest.mark.parametrize("bad", ["../evil.wav", "Storm/../../evil.wav", "/abs.wav", "C:/x.wav", "Storm/a:b.wav"])
def test_import_refuses_paths_leaving_the_theme_folder(client, library, bad):
    root = library[0]
    before = sorted(p.name for p in root.iterdir())
    r = _import(client, _zip({"Storm/Rain.wav": b"rain", bad: b"x"}))
    assert r.status_code == 400
    assert sorted(p.name for p in root.iterdir()) == before
    assert not (root.parent / "evil.wav").exists()


def test_import_plan_names_and_layouts():
    from sonorium.core.theme_import import ZipLayoutError, folder_name, plan_import
    assert plan_import(["T/", "T/a.wav", "T/G/b.wav"], "x.zip") == ("T", [("T/a.wav", "a.wav"), ("T/G/b.wav", "G/b.wav")])
    # A single group folder with metadata.json beside it is the root layout
    assert plan_import(["metadata.json", "G/b.wav"], "My theme.zip")[0] == "My theme"
    assert plan_import(["a.wav"], "x.zip", 'Rain: "heavy"/light') == ("Rain_ _heavy__light", [("a.wav", "a.wav")])
    assert folder_name("..hidden") == "hidden" and folder_name(" . ") == "" and folder_name("a. ") == "a"
    assert plan_import(["a.wav"], "...zip", "")[0] == "Imported theme"
    with pytest.raises(ZipLayoutError):
        plan_import(["T/"], "x.zip")
