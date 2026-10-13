"""
Theme audio in one format (core/theme_mp3.py): every track becomes an MP3,
320 kb/s CBR, 44.1 kHz, stereo. Conversion (rates, channels, MP3s kept as
they are), keys and settings following a renamed file, the background
service (idempotent, resumed after a restart), and the originals kept out of
listings, exports and packages.
"""

import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("numpy")
av = pytest.importorskip("av")
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "sonorium_addon"
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from engine_theme import sine, write_wav  # noqa: E402


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
def mp3():
    from sonorium.core import theme_mp3
    if not theme_mp3.available():
        pytest.skip("PyAV has no MP3 encoder here")
    return theme_mp3


# --- making sample files --------------------------------------------------------

def _tone(rate, seconds=1.0, freq=440.0, amp=8000.0):
    t = np.arange(int(rate * seconds)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def _write(path: Path, rate: int, channels: int, fmt: str, bit_rate: int = 128_000, seconds=1.0):
    """A tone as WAV, FLAC or MP3 (PyAV), mono or stereo (left louder than right)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    left = _tone(rate, seconds)
    data = left.reshape(1, -1) if channels == 1 else np.vstack([left, (left // 2).astype(np.int16)])
    layout = "mono" if channels == 1 else "stereo"
    codec = {"wav": "pcm_s16le", "flac": "flac", "mp3": "mp3"}[fmt]
    with av.open(str(path), "w", format=fmt) as out:
        stream = out.add_stream(codec, rate=rate)
        stream.codec_context.layout = layout
        if fmt == "mp3":
            stream.codec_context.format = "fltp"
            stream.codec_context.bit_rate = bit_rate
            frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(data.astype(np.float32) / 32768.0),
                                               format="fltp", layout=layout)
        else:
            stream.codec_context.format = "s16"
            frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(data.T).reshape(1, -1), format="s16",
                                               layout=layout)
        frame.rate = rate
        for packet in stream.encode(frame):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)


def _decode(path: Path) -> tuple:
    with av.open(str(path)) as c:
        s = c.streams.audio[0]
        ctx = s.codec_context
        info = (ctx.name, ctx.sample_rate, ctx.channels, ctx.bit_rate)
        planes = np.concatenate([f.to_ndarray() for f in c.decode(s)], axis=1)
    return info, planes


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --- one file ---------------------------------------------------------------------

@pytest.mark.parametrize("rate,channels,fmt", [
    (48000, 1, "wav"), (44100, 2, "wav"), (22050, 1, "flac"), (32000, 2, "flac"),
])
def test_convert_file_gives_320k_cbr_44100_stereo(mp3, tmp_path, rate, channels, fmt):
    source = tmp_path / f"in.{fmt}"
    _write(source, rate, channels, fmt)
    target = tmp_path / "out.mp3"
    mp3.convert_file(source, target)
    (codec, out_rate, out_channels, bit_rate), planes = _decode(target)
    assert codec in ("mp3", "mp3float") and out_rate == 44100 and out_channels == 2
    assert bit_rate == 320_000
    assert mp3.is_standard(target)
    # The length is kept (within one MP3 frame) and the level is the source's
    assert abs(planes.shape[1] - 44100) <= 1152
    assert 0.2 < float(np.abs(planes[0]).max()) < 0.3   # 8000 / 32768 = 0.244
    if channels == 1:
        assert np.array_equal(planes[0], planes[1])      # mono: the same on both sides
    else:
        assert float(np.abs(planes[1]).max()) < 0.75 * float(np.abs(planes[0]).max())  # the image is kept


def test_every_frame_is_320k(mp3, tmp_path):
    """CBR: every packet is the same size (320 kb/s at 44.1 kHz: 1044 or 1045 bytes with padding)."""
    _write(tmp_path / "in.wav", 44100, 1, "wav", seconds=2.0)
    mp3.convert_file(tmp_path / "in.wav", tmp_path / "out.mp3")
    with av.open(str(tmp_path / "out.mp3")) as c:
        sizes = {p.size for p in c.demux(c.streams.audio[0]) if p.size}
    assert sizes <= {1044, 1045}


def test_standard_mp3_check(mp3, tmp_path):
    _write(tmp_path / "ok.mp3", 44100, 2, "mp3", bit_rate=128_000)
    _write(tmp_path / "mono.mp3", 44100, 1, "mp3")
    _write(tmp_path / "rate.mp3", 48000, 2, "mp3")
    _write(tmp_path / "ok.wav", 44100, 2, "wav")
    (tmp_path / "broken.mp3").write_bytes(b"not audio")
    assert mp3.is_standard(tmp_path / "ok.mp3")            # any bit rate
    assert not mp3.is_standard(tmp_path / "mono.mp3")
    assert not mp3.is_standard(tmp_path / "rate.mp3")
    assert not mp3.is_standard(tmp_path / "ok.wav")        # not an MP3
    assert not mp3.is_standard(tmp_path / "broken.mp3")


def test_unreadable_file_raises_and_leaves_nothing(mp3, tmp_path):
    (tmp_path / "bad.wav").write_bytes(b"RIFF nonsense")
    with pytest.raises(mp3.ConvertError):
        mp3.convert_file(tmp_path / "bad.wav", tmp_path / "out.mp3")
    assert not (tmp_path / "out.mp3").exists()


# --- one theme -----------------------------------------------------------------------

def _theme(root: Path, name="Storm", theme_id="storm-id") -> Path:
    """Rain.wav (48 kHz mono), Thunder.mp3 (standard), Hum.mp3 (mono), Wind/Gust.flac, Wind/Breeze.mp3 (22 kHz)."""
    folder = root / name
    _write(folder / "Rain.wav", 48000, 1, "wav")
    _write(folder / "Thunder.mp3", 44100, 2, "mp3", bit_rate=192_000)
    _write(folder / "Hum.mp3", 44100, 1, "mp3")
    _write(folder / "Wind" / "Gust.flac", 44100, 2, "flac")
    _write(folder / "Wind" / "Breeze.mp3", 22050, 2, "mp3")
    tracks = {k: {"volume": v, "playback_mode": "continuous"} for k, v in
              (("Rain", 0.3), ("Thunder", 0.4), ("Hum", 0.5), ("Wind/Gust", 0.6), ("Wind/Breeze", 0.7))}
    (folder / "metadata.json").write_text(json.dumps({
        "spec_version": 2, "id": theme_id, "name": name, "tracks": tracks,
        "groups": {"Wind": {"mode": "merry_go_round"}}}), encoding="utf-8")
    (folder / "presets.json").write_text(json.dumps({"presets": {
        "calm": {"name": "Calm", "tracks": {"Rain": {"volume": 0.1}, "Wind/Gust": {"volume": 0.2}}}}}),
        encoding="utf-8")
    return folder


def _files(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def test_standardize_theme_converts_and_keeps_keys(mp3, tmp_path):
    folder = _theme(tmp_path)
    thunder = _sha(folder / "Thunder.mp3")
    meta_before = (folder / "metadata.json").read_bytes()
    presets_before = (folder / "presets.json").read_bytes()

    result = mp3.standardize_theme(folder)

    assert sorted(result.converted) == sorted([
        ("Rain.wav", "Rain.mp3"), ("Hum.mp3", "Hum.mp3"), ("Wind/Gust.flac", "Wind/Gust.mp3"),
        ("Wind/Breeze.mp3", "Wind/Breeze.mp3")])
    assert result.renamed == {} and result.failed == [] and result.retry == []
    from sonorium.theme_files import theme_audio_files, track_key
    files = theme_audio_files(folder)
    assert [track_key(folder, f) for f in files] == ["Hum", "Rain", "Thunder", "Wind/Breeze", "Wind/Gust"]
    assert all(f.suffix == ".mp3" and mp3.is_standard(f) for f in files)
    assert _sha(folder / "Thunder.mp3") == thunder                   # a standard MP3 isn't touched
    # Keys didn't change: metadata.json and presets.json as they were
    assert (folder / "metadata.json").read_bytes() == meta_before
    assert (folder / "presets.json").read_bytes() == presets_before
    # The originals, apart, with what each became
    assert (folder / "_originals" / "Rain.wav").exists() and (folder / "_originals" / "Hum.mp3").exists()
    assert (folder / "_originals" / "Wind" / "Gust.flac").exists()
    assert (folder / "_originals" / "Wind" / "Breeze.mp3").exists()
    entries = {e["original"]: e for e in mp3.originals(folder)}
    assert entries["Rain.wav"]["mp3"] == "Rain.mp3" and entries["Rain.wav"]["kept_as"] == "_originals/Rain.wav"
    assert all(e["state"] == "done" for e in entries.values())
    assert not any(p.name.startswith(mp3.TEMP_PREFIX) for p in folder.rglob("*"))


def test_standardize_theme_is_idempotent(mp3, tmp_path):
    folder = _theme(tmp_path)
    mp3.standardize_theme(folder)
    snapshot = {p: (p.stat().st_mtime_ns, _sha(p)) for p in folder.rglob("*") if p.is_file()}
    again = mp3.standardize_theme(folder)
    assert again.converted == [] and again.pending == 0 and not again.changed
    assert {p: (p.stat().st_mtime_ns, _sha(p)) for p in folder.rglob("*") if p.is_file()} == snapshot


def test_a_taken_name_gets_its_own_key_with_the_same_settings(mp3, tmp_path):
    """Rain.wav and Rain.mp3 shared the key "Rain": the converted WAV becomes "Rain (2)" with copies of its entries."""
    folder = tmp_path / "Pair"
    _write(folder / "Rain.wav", 44100, 1, "wav")
    _write(folder / "Rain.mp3", 44100, 2, "mp3")
    _write(folder / "Birds" / "Song.wav", 44100, 2, "wav")
    _write(folder / "Birds" / "Song.ogg.mp3", 44100, 2, "mp3")  # another stem: no clash
    (folder / "metadata.json").write_text(json.dumps({
        "spec_version": 2, "id": "pair", "name": "Pair", "extra_field": "kept",
        "tracks": {"Rain": {"volume": 0.3, "plugins": {"p": {"a": 1}}}}}), encoding="utf-8")
    (folder / "presets.json").write_text(json.dumps({
        "presets": {"one": {"name": "One", "tracks": {"Rain": {"volume": 0.2}}}},
        "sequences": {"s": {"name": "S", "presets": ["one"],
                            "transitions": {"one": {"in": {"Rain": {"styles": ["swell"]}}}},
                            "while": {"one": {"tracks": {"Rain": {"volume": 0.1}}}}}}}), encoding="utf-8")
    (folder / "MANIFEST.json").write_text(json.dumps({"channels": [{"local_filename": "Rain.wav"}]}),
                                          encoding="utf-8")

    result = mp3.standardize_theme(folder)

    assert ("Rain.wav", "Rain (2).mp3") in result.converted and result.renamed == {"Rain": "Rain (2)"}
    assert (folder / "Rain.mp3").exists() and (folder / "Rain (2).mp3").exists()
    meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["tracks"]["Rain (2)"] == meta["tracks"]["Rain"] == {"volume": 0.3, "plugins": {"p": {"a": 1}}}
    assert meta["extra_field"] == "kept"
    doc = json.loads((folder / "presets.json").read_text(encoding="utf-8"))
    assert doc["presets"]["one"]["tracks"]["Rain (2)"] == {"volume": 0.2}
    seq = doc["sequences"]["s"]
    assert seq["transitions"]["one"]["in"]["Rain (2)"] == {"styles": ["swell"]}
    assert seq["while"]["one"]["tracks"]["Rain (2)"] == {"volume": 0.1}
    manifest = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["channels"][0]["local_filename"] == "Rain (2).mp3"
    assert (folder / "Birds" / "Song.mp3").exists()


def test_a_linked_files_name_is_not_taken(mp3, tmp_path):
    folder = tmp_path / "Links"
    _write(folder / "Rain.wav", 44100, 1, "wav")
    (folder / "metadata.json").write_text(json.dumps({
        "spec_version": 2, "id": "links", "name": "Links",
        "links": {"Rain.mp3": {"theme": "other", "track": "x"}}}), encoding="utf-8")
    result = mp3.standardize_theme(folder)
    assert result.converted == [("Rain.wav", "Rain.mp3")]  # the link "Rain.mp3" is a key, not a file name
    _write(folder / "Drip.wav", 44100, 1, "wav")
    meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    meta["links"] = {"Drip": {"theme": "other", "track": "x"}}
    (folder / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    result = mp3.standardize_theme(folder)
    assert result.converted == [("Drip.wav", "Drip (2).mp3")]


def test_unreadable_files_stay_as_they_are(mp3, tmp_path):
    folder = tmp_path / "Bad"
    _write(folder / "Good.wav", 44100, 1, "wav")
    (folder / "Bad.wav").write_bytes(b"RIFF nonsense")
    result = mp3.standardize_theme(folder)
    assert result.converted == [("Good.wav", "Good.mp3")]
    assert [f for f, _ in result.failed] == ["Bad.wav"]
    assert (folder / "Bad.wav").exists() and not (folder / "_originals" / "Bad.wav").exists()


def test_a_file_in_use_is_tried_again_later(mp3, tmp_path, monkeypatch):
    folder = tmp_path / "Busy"
    _write(folder / "Rain.wav", 44100, 1, "wav")
    real = os.replace

    def refuse(src, dst):
        if Path(src).name == "Rain.wav":
            raise PermissionError("in use")
        return real(src, dst)
    monkeypatch.setattr(mp3.os, "replace", refuse)
    result = mp3.standardize_theme(folder)
    assert result.converted == [] and [f for f, _ in result.retry] == ["Rain.wav"]
    assert (folder / "Rain.wav").exists() and not (folder / "Rain.mp3").exists()
    assert mp3.originals(folder) == []
    assert not any(p.name.startswith(mp3.TEMP_PREFIX) for p in folder.iterdir())
    monkeypatch.setattr(mp3.os, "replace", real)
    assert mp3.standardize_theme(folder).converted == [("Rain.wav", "Rain.mp3")]


class _Crash(BaseException):
    """A restart in the middle of a conversion."""


def test_a_conversion_cut_off_by_a_restart_is_finished(mp3, tmp_path, monkeypatch):
    """Cut off after the original moved and before the MP3 took its place: the next run finishes it."""
    folder = tmp_path / "Cut"
    _write(folder / "Rain.wav", 44100, 1, "wav")
    real = os.replace

    def crash_on_mp3(src, dst):
        if Path(src).name.startswith(mp3.TEMP_PREFIX):
            raise _Crash()
        return real(src, dst)
    monkeypatch.setattr(mp3.os, "replace", crash_on_mp3)
    with pytest.raises(_Crash):
        mp3.standardize_theme(folder)
    monkeypatch.setattr(mp3.os, "replace", real)
    assert not (folder / "Rain.wav").exists() and not (folder / "Rain.mp3").exists()
    assert mp3.originals(folder)[0]["state"] == "converting"

    result = mp3.standardize_theme(folder)
    assert result.converted == []  # nothing left to convert: it was finished
    assert mp3.is_standard(folder / "Rain.mp3") and (folder / "_originals" / "Rain.wav").exists()
    assert [e["state"] for e in mp3.originals(folder)] == ["done"]
    assert not any(p.name.startswith(mp3.TEMP_PREFIX) for p in folder.iterdir())


def test_a_conversion_cut_off_before_its_mp3_was_written_is_undone(mp3, tmp_path):
    """The journal says converting, the original was moved, but no MP3 was kept: the original goes back."""
    folder = tmp_path / "Undo"
    _write(folder / "_originals" / "Rain.wav", 44100, 1, "wav")
    (folder / "_originals" / "originals.json").write_text(json.dumps({"files": [
        {"original": "Rain.wav", "kept_as": "_originals/Rain.wav", "mp3": "Rain.mp3", "state": "converting",
         "temp": mp3.TEMP_PREFIX + "gone.mp3"}]}), encoding="utf-8")
    (folder / (mp3.TEMP_PREFIX + "stray.mp3")).write_bytes(b"half")  # an encode cut off before the journal
    result = mp3.standardize_theme(folder)
    assert result.converted == [("Rain.wav", "Rain.mp3")]  # put back, then converted again
    assert mp3.is_standard(folder / "Rain.mp3")
    assert [e["state"] for e in mp3.originals(folder)] == ["done"]
    assert not (folder / (mp3.TEMP_PREFIX + "stray.mp3")).exists()


def test_originals_can_be_deleted_instead(mp3, tmp_path):
    folder = _theme(tmp_path)
    mp3.standardize_theme(folder)                       # kept (the default for now)
    assert mp3.KEEP_ORIGINALS is True
    assert (folder / "_originals" / "Rain.wav").exists()
    _write(folder / "Drip.wav", 44100, 1, "wav")
    mp3.standardize_theme(folder, keep_originals=False)  # the later switch: new and kept ones deleted
    assert not (folder / "_originals" / "Rain.wav").exists()
    assert not (folder / "_originals" / "Drip.wav").exists() and (folder / "Drip.mp3").exists()
    assert all(e.get("deleted") for e in mp3.originals(folder))


def test_nothing_happens_without_an_mp3_encoder(tmp_path, monkeypatch):
    from sonorium.core import theme_mp3
    folder = tmp_path / "Phone"
    write_wav(folder / "Rain.wav", sine(220.0, 0.5))
    monkeypatch.setattr(theme_mp3, "available", lambda: False)
    result = theme_mp3.standardize_theme(folder)
    assert result.unavailable and not result.changed
    assert _files(folder) == ["Rain.wav"]
    service = theme_mp3.Mp3Service(tmp_path, tmp_path / "config", start_delay=0)
    service.start()  # logs that it can't, starts nothing
    assert service._thread is None
    theme_mp3.request(folder)  # no service set: nothing happens


# --- originals stay out of the theme --------------------------------------------------

def test_originals_are_not_listed_exported_or_packaged(mp3, tmp_path):
    root = tmp_path / "audio"
    folder = _theme(root)
    mp3.standardize_theme(folder)
    (folder / (mp3.TEMP_PREFIX + "writing.mp3")).write_bytes(b"half")  # being written
    from sonorium.core import theme_groups, theme_package
    from sonorium.theme_files import group_folders, theme_audio_files
    assert not any("_originals" in p.parts for p in theme_audio_files(folder))
    assert [p.name for p in group_folders(folder)] == ["Wind"]
    assert theme_groups.group_folder_names(folder) == ["Wind"]
    package = theme_package.build_package(folder)
    paths = [f.path for f in package.files]
    assert paths == ["Hum.mp3", "Rain.mp3", "Thunder.mp3", "Wind/Breeze.mp3", "Wind/Gust.mp3",
                     "metadata.json", "presets.json"]
    assert package.track_count == 5
    out = io.BytesIO()
    theme_package.write_zip(package, out)
    names = zipfile.ZipFile(io.BytesIO(out.getvalue())).namelist()
    assert not any("_originals" in n or mp3.TEMP_PREFIX in n for n in names)
    assert all(n.endswith((".mp3", ".json")) for n in names)


# --- the background service -------------------------------------------------------------

def _service(mp3, root, config, **kwargs):
    seen = []
    service = mp3.Mp3Service(root, config, on_converted=lambda folder, result: seen.append(folder.name),
                             start_delay=0, pause=0, **kwargs)
    return service, seen


def test_service_converts_every_theme_once_and_resumes(mp3, tmp_path):
    root, config = tmp_path / "audio", tmp_path / "config"
    _theme(root, "Storm", "storm-id")
    _theme(root, "Calm", "calm-id")
    already = root / "Done"
    _write(already / "Tone.mp3", 44100, 2, "mp3")

    service, seen = _service(mp3, root, config)
    service.start()
    try:
        assert service.wait_idle(60)
    finally:
        service.stop()
    assert sorted(seen) == ["Calm", "Storm"]  # Done was standard already: no rescan for it
    state = json.loads((config / mp3.STATE_FILE).read_text(encoding="utf-8"))["themes"]
    assert set(state) == {"Calm", "Done", "Storm"} and all(v["done"] for v in state.values())

    # After a restart: nothing is converted or rescanned again
    again, seen_again = _service(mp3, root, config)
    assert all(again.is_done(root / name) for name in ("Calm", "Done", "Storm"))
    again.start()
    try:
        assert again.wait_idle(60)
        assert seen_again == []
        # A file put in by hand: found on the next check (a rescan asks for one)
        _write(root / "Calm" / "Drip.wav", 44100, 1, "wav")
        assert not again.is_done(root / "Calm")
        again.request_all()
        assert again.wait_idle(60)
    finally:
        again.stop()
    assert seen_again == ["Calm"] and (root / "Calm" / "Drip.mp3").exists()


def test_service_resumes_a_pass_cut_off_by_a_restart(mp3, tmp_path):
    """Themes finished before the restart are skipped; the rest are converted."""
    root, config = tmp_path / "audio", tmp_path / "config"
    _theme(root, "First", "first-id")
    _theme(root, "Second", "second-id")
    service, _ = _service(mp3, root, config)
    service._convert_theme(root / "First")       # the pass got this far
    restarted, seen = _service(mp3, root, config)
    assert restarted.is_done(root / "First") and not restarted.is_done(root / "Second")
    restarted.start()
    try:
        assert restarted.wait_idle(60)
    finally:
        restarted.stop()
    assert seen == ["Second"]


def test_an_import_goes_first(mp3, tmp_path):
    root, config = tmp_path / "audio", tmp_path / "config"
    for name in ("A", "B", "C"):
        _theme(root, name, f"{name}-id")
    service, seen = _service(mp3, root, config)
    service.request_all()
    service.request(root / "C")                  # just imported
    service._thread = None
    import threading
    service._thread = threading.Thread(target=service._run, daemon=True)  # no first pass delay
    service._thread.start()
    try:
        assert service.wait_idle(60)
    finally:
        service.stop()
    assert seen[0] == "C" and sorted(seen) == ["A", "B", "C"]


def test_a_theme_in_use_is_checked_again(mp3, tmp_path, monkeypatch):
    root, config = tmp_path / "audio", tmp_path / "config"
    folder = root / "Busy"
    _write(folder / "Rain.wav", 44100, 1, "wav")
    real = os.replace
    busy = {"on": True}

    def refuse(src, dst):
        if busy["on"] and Path(src).name == "Rain.wav":
            raise PermissionError("in use")
        return real(src, dst)
    monkeypatch.setattr(mp3.os, "replace", refuse)
    service, seen = _service(mp3, root, config, retry_after=0.2)
    service.start()
    try:
        assert service.wait_idle(60)
        assert not service.is_done(folder) and seen == []
        busy["on"] = False
        import time
        deadline = time.monotonic() + 30
        while not seen and time.monotonic() < deadline:
            time.sleep(0.05)
        assert service.wait_idle(60)
    finally:
        service.stop()
    assert seen == ["Busy"] and service.is_done(folder)


# --- through the routes -----------------------------------------------------------------

def test_an_imported_theme_becomes_mp3(mp3, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import sonorium.paths
    from sonorium.web import api_v2

    root, config = tmp_path / "audio", tmp_path / "config"
    root.mkdir()
    source = _theme(tmp_path / "src", "Storm", "storm-id")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in source.rglob("*"):
            if p.is_file():
                zf.write(p, "Storm/" + p.relative_to(source).as_posix())

    service, seen = _service(mp3, root, config)
    mp3.set_service(service)
    try:
        with patch.object(sonorium.paths, "audio_dir", lambda: root):
            router = api_v2.create_api_router(
                session_manager=MagicMock(), group_manager=MagicMock(), ha_registry=MagicMock(),
                state_store=MagicMock(), channel_manager=MagicMock(), plugin_manager=MagicMock(),
                mqtt_manager=MagicMock())
            app = FastAPI()
            app.include_router(router)
            with TestClient(app) as client:
                r = client.post("/api/themes/import", files={"file": ("Storm.zip", buf.getvalue(), "application/zip")})
                assert r.status_code == 200
                import threading
                service._thread = threading.Thread(target=service._run, daemon=True)
                service._thread.start()
                assert service.wait_idle(60)
                folder = root / "Storm"
                assert seen and seen[0] == "Storm"
                assert sorted(p.name for p in folder.iterdir() if p.is_file() and p.suffix != ".json") == [
                    "Hum.mp3", "Rain.mp3", "Thunder.mp3"]
                package = client.get("/api/themes/storm-id/package")
                if package.status_code == 404:  # the import gave the theme a new id
                    theme_id = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))["id"]
                    package = client.get(f"/api/themes/{theme_id}/package")
                paths = [f["path"] for f in package.json()["files"]]
                assert not any(p.startswith("_originals") for p in paths)
                assert [p for p in paths if not p.endswith(".json")] == [
                    "Hum.mp3", "Rain.mp3", "Thunder.mp3", "Wind/Breeze.mp3", "Wind/Gust.mp3"]
    finally:
        service.stop()
        mp3.set_service(None)


# --- the server ---------------------------------------------------------------------------

def test_the_server_converts_in_the_background_and_rescans(mp3, tmp_path):
    """WebApp starts the service; a converted theme is rescanned, so its tracks read the MP3s."""
    import asyncio
    from types import SimpleNamespace
    import sonorium.api
    from sonorium.core.theme_metadata import ThemeMetadataManager
    from sonorium.utils import IndexList

    root = tmp_path / "audio"
    _theme(root, "Storm", "storm-id")
    device = SimpleNamespace(audio_dir=root, themes=IndexList(), files_by_theme={}, all_files=IndexList(),
                             master_volume=1.0)
    api = object.__new__(sonorium.api.WebApp)
    api.backend = SimpleNamespace(device=device)
    api._theme_metadata_manager = ThemeMetadataManager(root)
    api._theme_refresh_task = None
    api._session_manager = None
    api._mqtt_manager = None
    api._state_store = SimpleNamespace(state_file=tmp_path / "config" / "state.json")
    api.MP3_START_DELAY = 0.0

    async def scenario():
        await api.refresh_themes()
        assert sorted(f.path.name for f in device.files_by_theme["Storm"]) == [
            "Breeze.mp3", "Gust.flac", "Hum.mp3", "Rain.wav", "Thunder.mp3"]
        api._start_mp3_conversion()
        assert mp3._service is api._mp3_service
        assert await asyncio.to_thread(api._mp3_service.wait_idle, 60)
        for _ in range(100):  # the rescan it scheduled
            if api._theme_refresh_task is not None:
                break
            await asyncio.sleep(0.05)
        await api._theme_refresh_task
        api._mp3_service.stop()
        mp3.set_service(None)

    asyncio.run(scenario())
    files = sorted(f.path.name for f in device.files_by_theme["Storm"])
    assert files == ["Breeze.mp3", "Gust.mp3", "Hum.mp3", "Rain.mp3", "Thunder.mp3"]
    assert sorted(t.name for t in device.themes[0].tracks) == ["Hum", "Rain", "Thunder", "Wind/Breeze", "Wind/Gust"]
