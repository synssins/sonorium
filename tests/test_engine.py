"""
The playback engine on its own (sonorium/engine): imports nothing of the
server, renders the same samples for the same seed, reads files through any
decoder, and follows presets and volume as the server does.

The theme is generated on the spot (tests/engine_theme.py).
"""

import os
import subprocess
import sys
import threading
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "sonorium_addon"
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from engine_theme import FAST_VALUES, RATE, make_theme, noise, sine, write_wav  # noqa: E402

# What the engine must never load: the web server, Home Assistant, MQTT,
# speakers, the MP3 side, plugins, sign-in. PyAV too, when rendering with PcmFileDecoder.
BLOCKED_PACKAGES = ("fastapi", "uvicorn", "starlette", "pydantic_settings", "pydantic", "paho", "pychromecast",
                    "zeroconf", "httpx", "av", "pyatv", "aiohttp")
BLOCKED_SONORIUM = ("sonorium.mp3_out", "sonorium.web", "sonorium.web_server", "sonorium.api", "sonorium.plugins",
                    "sonorium.auth", "sonorium.ha", "sonorium.settings", "sonorium.network", "sonorium.device",
                    "sonorium.core.session_manager", "sonorium.core.group_manager", "sonorium.core.state",
                    "sonorium.client", "sonorium.alerts")


def _env(**extra):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ADDON), str(TESTS)])
    env["SONORIUM_LOG_LEVEL"] = "warning"
    env["PYTHONUTF8"] = "1"
    env.update(extra)
    return env


def _ours(name):
    return name == "sonorium" or name.startswith("sonorium.")


@pytest.fixture(autouse=True)
def _real_sonorium():
    """The add-on's real package for the test, and whatever was imported before put back after (other tests stub it)."""
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


def _engine_modules():
    import sonorium.engine as engine
    return engine


@pytest.fixture
def theme(tmp_path):
    return make_theme(tmp_path)


# --- imports ---

ISOLATION_SCRIPT = r'''
import sys
BLOCKED = {blocked!r}
BLOCKED_SONORIUM = {blocked_sonorium!r}

class Blocker:
    def find_spec(self, name, path=None, target=None):
        top = name.split(".")[0]
        if top in BLOCKED or any(name == b or name.startswith(b + ".") for b in BLOCKED_SONORIUM):
            raise ImportError(f"blocked for the engine: {{name}}")
        return None

sys.meta_path.insert(0, Blocker())
for name in BLOCKED:
    sys.modules[name] = None  # a sentinel: importing it raises ImportError

from engine_theme import make_theme, make_second_theme, FAST_VALUES
from sonorium.engine import Engine, PcmFileDecoder
folder = make_theme(sys.argv[1])
second = make_second_theme(sys.argv[1])
engine = Engine(folder, playback_values=FAST_VALUES, seed=3, decoder=PcmFileDecoder(), sequence_id="walk")
pcm = engine.render(5 * 44100)
engine.set_theme(second)
engine.render(4 * 44100)
engine.start_playlist({{"name": "P", "play_min": 1, "play_max": 2, "entries": [
    {{"theme_id": "engine-test-theme", "sequence_id": "bounce"}}, {{"theme_id": "engine-second-theme"}}]}})
engine.render(8 * 44100)
engine.next(); engine.previous(); engine.status()
engine.close()
assert pcm.dtype.name == "int16" and pcm.shape == (5 * 44100,), (pcm.dtype, pcm.shape)
assert abs(pcm.astype("float64")).mean() > 100, "silent"
loaded = sorted(m for m in sys.modules if sys.modules[m] is not None and (
    m.split(".")[0] in BLOCKED or any(m == b or m.startswith(b + ".") for b in BLOCKED_SONORIUM)))
assert not loaded, loaded
print("ENGINE-OK")
'''


def test_engine_imports_and_renders_without_the_server(tmp_path):
    script = ISOLATION_SCRIPT.format(blocked=BLOCKED_PACKAGES, blocked_sonorium=BLOCKED_SONORIUM)
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=_env(), capture_output=True,
                            text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-3000:]
    assert "ENGINE-OK" in result.stdout


def test_only_the_pyav_decoder_imports_av():
    """No module of the engine's import graph imports PyAV at the top: only decoders.PyAvDecoder.open does."""
    import ast
    package = ADDON / "sonorium"
    files = [package / name for name in ("recording.py", "theme.py", "mixing.py", "playback_values.py",
                                         "decoders.py", "rng.py", "theme_files.py", "utils.py", "obs.py")]
    files += [package / "core" / name for name in ("__init__.py", "channel.py", "transitions.py", "theme_groups.py",
                                                   "theme_metadata.py", "theme_presets.py", "sequences.py",
                                                   "intrusions.py", "playback_runner.py", "playlists.py")]
    files += sorted((package / "engine").glob("*.py"))
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module level only
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not any(n == "av" or n.startswith("av.") for n in names), f"{path.name} imports av"


# --- determinism (the golden render) ---

def _golden(theme_dir, out, seed, hashseed, *extra):
    cmd = [sys.executable, "-m", "sonorium.engine.render", str(theme_dir), "--seconds", "20", "--seed", str(seed),
           "--values", '{"sparse_min_interval": 1, "sparse_max_interval": 4, "group_gap": 2}', "--out", str(out),
           *extra]
    result = subprocess.run(cmd, env=_env(PYTHONHASHSEED=str(hashseed)), capture_output=True, text=True,
                            timeout=180)
    assert result.returncode == 0, result.stderr[-3000:]
    return Path(out).read_bytes()


def test_golden_render_is_the_same_every_time_for_a_seed(theme, tmp_path):
    first = _golden(theme, tmp_path / "a.wav", 1, 1)
    again = _golden(theme, tmp_path / "b.wav", 1, 2)  # another process, another hash seed
    other = _golden(theme, tmp_path / "c.wav", 2, 1)
    assert first == again
    assert first != other
    with wave.open(str(tmp_path / "a.wav")) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, RATE, 20 * RATE)


def test_golden_render_with_a_preset_and_the_pcm_decoder(theme, tmp_path):
    pyav = _golden(theme, tmp_path / "a.wav", 5, 0, "--preset", "calm")
    pcm = _golden(theme, tmp_path / "b.wav", 5, 0, "--preset", "calm", "--decoder", "pcm")
    busy = _golden(theme, tmp_path / "c.wav", 5, 0, "--preset", "busy")
    assert pyav == pcm  # 16-bit mono WAV at 44100 Hz: both decoders give the same samples
    assert pyav != busy


def test_render_in_any_sizes_gives_the_same_samples(theme):
    engine = _engine_modules()
    one = engine.Engine(theme, playback_values=FAST_VALUES, seed=9, decoder=engine.PcmFileDecoder())
    whole = one.render(10 * RATE)
    one.close()
    two = engine.Engine(theme, playback_values=FAST_VALUES, seed=9, decoder=engine.PcmFileDecoder())
    pieces, left = [], 10 * RATE
    for size in (1, 333, 1024, 5000, 77777, 1, 4096):
        pieces.append(two.render(size))
        left -= size
    pieces.append(two.render(left))
    assert two.clock.samples == 10 * RATE
    two.close()
    assert np.array_equal(whole, np.concatenate(pieces))


def test_engine_leaves_other_threads_alone(theme):
    """An engine's seed, decoder and playback values apply in its own calls only."""
    import random
    from sonorium import decoders, playback_values
    engine = _engine_modules()
    e = engine.Engine(theme, playback_values={"group_gap": 7.0}, seed=1, decoder=engine.PcmFileDecoder())
    seen = {}

    def other():
        seen["gap"] = playback_values.get("group_gap")
        seen["decoder"] = type(decoders.current_decoder()).__name__

    random.seed(42)
    expected = random.random()
    random.seed(42)
    e.render(3 * RATE)
    thread = threading.Thread(target=other)
    thread.start()
    thread.join()
    assert random.random() == expected  # the shared generator wasn't drawn from
    assert playback_values.get("group_gap") == playback_values.DEFAULTS["group_gap"]
    assert seen == {"gap": playback_values.DEFAULTS["group_gap"], "decoder": "PyAvDecoder"}
    e.close()


# --- the API ---

def test_presets_volume_and_close(theme):
    engine = _engine_modules()
    e = engine.Engine(theme, preset_id="", playback_values=FAST_VALUES, seed=4, decoder=engine.PcmFileDecoder())
    assert e.preset_id is None
    assert e.presets == {"calm": "Calm", "busy": "Busy", "quiet": "Quiet"}
    assert e.tracks == ["Chirp", "Drone", "Wind", "Birds/Bird 1", "Birds/Bird 2", "Birds/Bird 3"]
    assert e.theme_id == "engine-test-theme"
    pcm = e.render(RATE, dtype="float32")
    assert pcm.dtype == np.float32 and np.abs(pcm).max() <= 1.0 and np.abs(pcm).mean() > 0.01
    e.set_preset("calm")
    assert e.preset_id == "calm"
    with pytest.raises(KeyError):
        e.set_preset("nope")
    e.set_volume(0.0)
    e.render(engine.BLOCK_SAMPLES * 2)  # the volume ramps down over one block
    assert not e.render(RATE).any()
    e.set_volume(1.0)
    e.render(engine.BLOCK_SAMPLES * 2)
    assert e.render(RATE).any()
    e.close()
    with pytest.raises(RuntimeError):
        e.render(10)


def test_default_preset_and_preset_glide(theme):
    """No preset asked for: the theme's default (busy). Picking one glides over 3 s, as on the server."""
    engine = _engine_modules()
    e = engine.Engine(theme, playback_values=FAST_VALUES, seed=2, decoder=engine.PcmFileDecoder())
    assert e.preset_id == "busy"
    e.close()
    with pytest.raises(KeyError):
        engine.Engine(theme, preset_id="nope", decoder=engine.PcmFileDecoder())

    def drone_level(preset, glide):
        e = engine.Engine(theme, preset_id="", seed=2, decoder=engine.PcmFileDecoder(),
                          playback_values={"sparse_min_interval": 10000, "sparse_max_interval": 10000})
        e.render(2 * RATE)
        e.set_preset(preset, glide_seconds=glide)
        level = e.mix._views["Drone"].volume
        e.render(RATE)
        later = e.mix._views["Drone"].volume
        e.render(3 * RATE)
        final = e.mix._views["Drone"].volume
        e.close()
        return level, later, final

    start, middle, end = drone_level("calm", 3.0)
    assert start == pytest.approx(0.8) and 0.3 < middle < 0.8 and end == pytest.approx(0.3)
    assert drone_level("calm", 0.0)[0] == pytest.approx(0.3)


def test_sample_rate_must_be_the_mixers(theme):
    engine = _engine_modules()
    with pytest.raises(ValueError):
        engine.Engine(theme, sample_rate=48000)
    with pytest.raises(ValueError):
        engine.Engine(theme, playback_values={"group_gap": -1}, decoder=engine.PcmFileDecoder())


# --- decoders ---

def test_pcm_decoder_reads_wav_raw_and_stereo(tmp_path):
    engine = _engine_modules()
    decoder = engine.PcmFileDecoder(block_frames=1000)
    tone = sine(440.0, 1.0)
    write_wav(tmp_path / "mono.wav", tone)
    (tmp_path / "mono.pcm").write_bytes(tone.astype("<i2").tobytes())
    stereo = np.stack([tone, (tone // 2).astype(np.int16)], axis=1).reshape(-1)
    write_wav(tmp_path / "stereo.wav", stereo, channels=2)
    for name, expected in (("mono.wav", tone), ("mono.pcm", tone),
                           ("stereo.wav", (tone.astype(np.float32) + (tone // 2)) / 2)):
        audio = decoder.open(tmp_path / name)
        assert audio.has_audio and audio.header_length() == RATE
        got = np.concatenate(list(audio.blocks()))
        assert np.allclose(got, expected), name
    write_wav(tmp_path / "48k.wav", sine(440.0, 0.5)[: 24000], rate=48000)
    with pytest.raises(ValueError):
        decoder.open(tmp_path / "48k.wav")


def test_pcm_decoder_reads_decoded_copies(theme, tmp_path):
    """Pre-decoded copies (the audio cache, cache_lookup) play the same as the files themselves."""
    pytest.importorskip("av")
    engine = _engine_modules()
    from sonorium.engine.pcm_cache import predecode_theme
    cache = tmp_path / "cache"
    written = predecode_theme(theme, cache)
    assert (cache / "Birds" / "Bird 1.flac") in written
    for path in theme.rglob("*.wav"):
        path.write_bytes(b"")  # the originals are only listed now: every read goes to the copies
    copies = engine.PcmFileDecoder(lookup=engine.cache_lookup(theme, cache))
    e = engine.Engine(theme, playback_values=FAST_VALUES, seed=6, decoder=copies)
    assert np.abs(e.render(3 * RATE).astype(np.float64)).mean() > 100
    e.close()


def test_server_reads_through_the_default_decoder(tmp_path, monkeypatch):
    """recording.AudioReader (every read on the server) goes through decoders.current_decoder()."""
    from sonorium import decoders, recording
    opened = []

    class Spy:
        def open(self, path):
            opened.append(Path(path).name)
            return decoders.PcmFileDecoder().open(path)

    write_wav(tmp_path / "n.wav", noise(1.0))
    monkeypatch.setattr(decoders, "_default", Spy())
    assert recording.file_length_samples(tmp_path / "n.wav") == RATE
    blocks = list(recording.decode_mono(tmp_path / "n.wav"))
    assert sum(len(b) for b in blocks) == RATE and all(b.dtype == np.float32 for b in blocks)
    assert opened == ["n.wav", "n.wav"]
