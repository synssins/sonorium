"""
Stereo in the shared engine (sonorium/decoders.py, recording.py, mixing.py,
theme.py, core/channel.py, engine/): a mono file plays centred (a stereo mix
of mono files is the mono mix on both sides, sample for sample), a stereo
file keeps its image, every gain acts on both channels alike, the engine
switches between mono and stereo while it plays, the audio cache is stereo
FLAC read back bit-exact (old WAV entries still read), the server's Stereo
switch is off by default and saved, and each output gets the channel count
it should (MP3 for speakers, plugin sinks, taps).

The themes are generated on the spot (tests/engine_theme.py).
"""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("av")

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "sonorium_addon"
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from engine_theme import FAST_VALUES, RATE, make_theme, noise, sine, write_wav  # noqa: E402


def _ours(name):
    return name == "sonorium" or name.startswith("sonorium.")


@pytest.fixture(autouse=True)
def _real_sonorium():
    """The add-on's real package for the test, and whatever was imported before put back after."""
    saved_modules = {name: module for name, module in sys.modules.items() if _ours(name)}
    saved_path = list(sys.path)
    for name in saved_modules:
        del sys.modules[name]
    sys.path.insert(0, str(ADDON))
    try:
        yield
    finally:
        try:
            from sonorium import decoders
            decoders.set_default_channels(1)
        except Exception:
            pass
        for name in [n for n in sys.modules if _ours(n)]:
            del sys.modules[name]
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


def _fold(stereo):
    """(n, 2) int16 -> (n,) as decoders.fold_to_mono folds."""
    s = stereo.astype(np.int32)
    return ((s[:, 0] + s[:, 1] + 1) // 2).astype(np.int16)


def _render(theme, channels, seconds=12, **kw):
    from sonorium.engine import Engine
    kw.setdefault("playback_values", FAST_VALUES)
    eng = Engine(theme, seed=4, sequence_id="walk", channels=channels, **kw)
    try:
        return eng.render(seconds * RATE)
    finally:
        eng.close()


def _add_stereo_file(folder: Path, left, right, name="Wide", **settings):
    write_wav(folder / f"{name}.wav", np.stack([left, right], axis=1).reshape(-1), channels=2)
    meta_path = folder / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["tracks"][name] = {"volume": 0.8, "presence": 1.0, "playback_mode": "continuous", **settings}
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


# --- the mix ---

def test_mono_files_in_a_stereo_mix_are_the_mono_mix_on_both_sides(tmp_path):
    """Loops, Ebb & Flow, Intermittent, a group, a sequence with transitions: L == R == the mono render."""
    theme = make_theme(tmp_path)
    mono = _render(theme, 1)
    stereo = _render(theme, 2)
    assert mono.shape == (12 * RATE,) and stereo.shape == (12 * RATE, 2)
    assert np.abs(mono.astype(np.int32)).mean() > 100
    assert np.array_equal(stereo[:, 0], stereo[:, 1])
    assert np.array_equal(stereo[:, 0], mono)
    assert np.array_equal(_fold(stereo), mono)


def test_the_default_is_mono_as_before(tmp_path):
    from sonorium.engine import Engine
    theme = make_theme(tmp_path)
    eng = Engine(theme, seed=4, sequence_id="walk", playback_values=FAST_VALUES)
    try:
        assert eng.channels == 1 and eng.status()["channels"] == 1
        assert np.array_equal(eng.render(5 * RATE), _render(theme, 1, seconds=5))
    finally:
        eng.close()


def test_a_stereo_file_keeps_its_image(tmp_path):
    theme = make_theme(tmp_path)
    left = sine(330.0, 4.0, 8000.0)
    _add_stereo_file(theme, left, np.zeros_like(left))
    stereo = _render(theme, 2, master_gain=8).astype(np.int32)
    mono = _render(theme, 1, master_gain=8).astype(np.int32)
    # Left carries the 330 Hz file, right doesn't: they differ, and the left is louder
    assert not np.array_equal(stereo[:, 0], stereo[:, 1])
    assert np.abs(stereo[:, 0]).mean() > 1.3 * np.abs(stereo[:, 1]).mean()
    # Below the limiter, the fold of the stereo mix is the mono mix (the level moves alike)
    assert np.abs(_fold(stereo.astype(np.int16)).astype(np.int32) - mono).max() <= 2


def test_every_gain_acts_on_both_channels_alike(tmp_path):
    """A stereo file whose right is its left halved stays exactly so through glides, fades and transitions."""
    from sonorium.engine import Engine
    theme = tmp_path / "Half"
    left = (noise(5.0, 8000.0, seed=3) // 2 * 2).astype(np.int16)
    right = (left // 2).astype(np.int16)
    write_wav(theme / "Half.wav", np.stack([left, right], axis=1).reshape(-1), channels=2)
    meta = {"spec_version": 2, "id": "half", "name": "Half", "ebb_duration": True, "auto_retired": True,
            "intrusions_retired": True,
            "tracks": {"Half": {"volume": 0.9, "presence": 0.4, "playback_mode": "presence", "volume_low": 0.2,
                                "fade_in": 0.5}}}
    (theme / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    presets = {"presets": {"a": {"name": "A", "tracks": {"Half": {"volume": 0.3}}},
                           "b": {"name": "B", "tracks": {"Half": {"volume": 1.0}}}},
               "sequences": {"s": {"name": "S", "presets": ["a", "b"], "at_end": "restart", "hold_min": 1.0,
                                   "hold_max": 1.5, "ramp": 0.5,
                                   "transitions": {"b": {"in": {"Half": {"styles": ["dist"],
                                                                         "values": {"dist": 70}}}}}}}}
    (theme / "presets.json").write_text(json.dumps(presets), encoding="utf-8")
    eng = Engine(theme, seed=9, sequence_id="s", channels=2, master_gain=5, fade_in=1.0,
                 playback_values=FAST_VALUES)
    try:
        out = eng.render(4 * RATE)
        eng.set_volume(0.4)  # the output volume ramps too
        out = np.concatenate([out, eng.render(4 * RATE)]).astype(np.int32)
    finally:
        eng.close()
    assert np.abs(out[:, 0]).mean() > 200
    # The low-pass ("dist") smears each side by its own state, so compare levels, not samples, there:
    # outside the filter the right is the left halved to within rounding
    ratio = np.abs(out[:, 1]).sum() / np.abs(out[:, 0]).sum()
    assert 0.48 < ratio < 0.52
    close = np.abs(out[:, 1] * 2 - out[:, 0]) <= 3
    assert close.mean() > 0.95


def test_channel_mix_and_crossfade_between_mono_and_stereo_mixes():
    """A theme crossfade from a mono mix to a stereo one plays the mono side centred."""
    from sonorium import decoders
    from sonorium.core.channel import Channel
    ch = Channel(id=1)
    ch._xfade_old = (block for block in [np.full((1, 1024), 1000, np.int16)] * 4)
    ch._xfade_pos = 0
    from sonorium.core.channel import crossfade_curves
    ch._xfade_out, ch._xfade_in = crossfade_curves(2048)
    new = np.vstack([np.full(1024, 2000, np.int16), np.zeros(1024, np.int16)])
    out = ch._crossfade_step(new)
    assert out.shape == (2, 1024)
    assert out[1, 0] == 1000 and out[0, 0] == 1000  # the start: all old, centred
    assert out[0, -1] > out[1, -1] > 0
    # Stereo switched off while a stereo mix still plays: the channel folds it at once
    decoders.set_default_channels(1)
    ch._xfade_old = None
    ch._chunk_generator = iter([new])
    ch.state = __import__("sonorium.core.channel", fromlist=["ChannelState"]).ChannelState.PLAYING
    chunk = ch.next_chunk()
    assert chunk.shape == (1, 1024) and chunk[0, 0] == 1000


def test_server_mix_follows_the_default_channel_count(tmp_path):
    """recording/theme on the server: a mix made while Stereo is on is stereo for its whole life."""
    from sonorium import decoders
    from sonorium.engine.theme_loader import load_theme
    theme_dir = make_theme(tmp_path)
    folder = load_theme(theme_dir, 60, tmp_path)
    decoders.set_default_channels(2)
    stereo_mix = folder.theme.new_mix()
    decoders.set_default_channels(1)
    mono_mix = folder.theme.new_mix()
    s, m = stereo_mix.blocks(), mono_mix.blocks()
    for _ in range(50):
        a, b = next(s), next(m)
    assert a.shape == (2, 1024) and b.shape == (1, 1024)
    assert stereo_mix.channels == 2 and mono_mix.channels == 1
    s.close()
    m.close()


def test_engine_switches_channels_while_playing(tmp_path):
    from sonorium.engine import Engine
    theme = make_theme(tmp_path)
    left = sine(330.0, 4.0, 6000.0)
    _add_stereo_file(theme, left, np.zeros_like(left))
    ref = Engine(theme, seed=5, channels=2, playback_values=FAST_VALUES)
    eng = Engine(theme, seed=5, channels=2, playback_values=FAST_VALUES)
    try:
        assert np.array_equal(eng.render(3 * RATE + 100), ref.render(3 * RATE + 100))
        eng.set_channels(1)
        folded = eng.render(2 * RATE)
        assert folded.shape == (2 * RATE,) and eng.status()["channels"] == 1
        assert np.array_equal(folded, _fold(ref.render(2 * RATE)))
        eng.set_channels(2)
        assert np.array_equal(eng.render(RATE), ref.render(RATE))  # the stereo mix went on all along
    finally:
        eng.close()
        ref.close()
    # A mono engine switched to stereo: its mono mix on both sides (until its next theme)
    eng = Engine(theme, seed=5, playback_values=FAST_VALUES)
    try:
        eng.render(RATE)
        eng.set_channels(2)
        out = eng.render(RATE)
        assert out.shape == (RATE, 2) and np.array_equal(out[:, 0], out[:, 1])
    finally:
        eng.close()
    with pytest.raises(ValueError):
        Engine(theme, channels=3)


def test_fold_and_to_channels():
    from sonorium.decoders import fold_to_mono, to_channels
    stereo = np.array([[1000, 32767, 7, -3], [-2000, -32768, 8, -3]], dtype=np.int16)
    assert fold_to_mono(stereo).tolist() == [[-500, 0, 8, -3]]  # rounded half up, as FFmpeg's downmix
    centred = to_channels(np.array([5, -6], dtype=np.int16), 2)
    assert centred.tolist() == [[5, -6], [5, -6]]
    assert np.array_equal(fold_to_mono(centred), np.array([[5, -6]], dtype=np.int16))
    block = np.zeros((1, 4), np.int16)
    assert to_channels(block, 1) is block
    floats = to_channels(np.array([[1.0, 2.0], [3.0, 5.0]], np.float32), 1)
    assert floats.tolist() == [[2.0, 3.5]]


def test_decoders_give_stereo_blocks(tmp_path):
    from sonorium.decoders import PcmFileDecoder, PyAvDecoder
    tone = sine(440.0, 1.0)
    write_wav(tmp_path / "mono.wav", tone)
    other = (tone // 3).astype(np.int16)
    write_wav(tmp_path / "stereo.wav", np.stack([tone, other], axis=1).reshape(-1), channels=2)
    for decoder in (PyAvDecoder(), PcmFileDecoder(block_frames=1000)):
        got = np.concatenate(list(decoder.open(tmp_path / "mono.wav", channels=2).blocks()), axis=1)
        assert got.shape == (2, RATE) and np.array_equal(got[0], tone) and np.array_equal(got[1], tone)
        got = np.concatenate(list(decoder.open(tmp_path / "stereo.wav", channels=2).blocks()), axis=1)
        assert np.array_equal(got[0], tone) and np.array_equal(got[1], other)
        mono = np.concatenate(list(decoder.open(tmp_path / "mono.wav").blocks()))
        assert mono.ndim == 1 and np.array_equal(mono, tone)


def test_a_mono_only_decoder_is_centred_for_a_stereo_mix(tmp_path):
    from sonorium import decoders, recording
    tone = sine(440.0, 0.5)
    write_wav(tmp_path / "t.wav", tone)

    class MonoOnly:
        def open(self, path):
            return decoders.PcmFileDecoder().open(path)

    with decoders.use_decoder(MonoOnly()), decoders.use_channels(2):
        blocks = list(recording.decode_mono(tmp_path / "t.wav"))
    got = np.concatenate(blocks, axis=1)
    assert got.shape == (2, len(tone)) and np.array_equal(got[0], got[1]) and np.array_equal(got[0], tone)


# --- the audio cache ---

def test_cache_is_stereo_flac_read_back_bit_exact(tmp_path):
    from sonorium.decoders import PcmFileDecoder, PyAvDecoder, cache_lookup
    from sonorium.engine.pcm_cache import flac_available, predecode_theme
    if not flac_available():
        pytest.skip("this PyAV has no FLAC encoder")
    theme = make_theme(tmp_path / "themes")
    left, right = sine(330.0, 2.0, 9000.0), noise(2.0, 3000.0, seed=11)
    _add_stereo_file(theme, left, right)
    cache = tmp_path / "cache"
    written = predecode_theme(theme, cache)
    assert cache / "Wide.flac" in written and cache / "Birds" / "Bird 1.flac" in written
    assert not list(cache.rglob("*.wav"))
    copies = PcmFileDecoder(lookup=cache_lookup(theme, cache))
    # A stereo file: bit-exact both sides, folded to the channels' average in a mono mix
    got = np.concatenate(list(copies.open(theme / "Wide.wav", channels=2).blocks()), axis=1)
    assert np.array_equal(got[0], left) and np.array_equal(got[1], right)
    direct = np.concatenate(list(PyAvDecoder().open(theme / "Wide.wav", channels=2).blocks()), axis=1)
    assert np.array_equal(got, direct)
    mono = np.concatenate(list(copies.open(theme / "Wide.wav").blocks()))
    assert np.allclose(mono, (left.astype(np.float32) + right) / 2)
    # A mono file: stored centred, folds back to exactly its samples
    drone = np.concatenate(list(PyAvDecoder().open(theme / "Drone.wav").blocks()))
    assert np.array_equal(np.concatenate(list(copies.open(theme / "Drone.wav").blocks())), drone)
    both = np.concatenate(list(copies.open(theme / "Drone.wav", channels=2).blocks()), axis=1)
    assert np.array_equal(both[0], drone) and np.array_equal(both[1], drone)
    assert copies.open(theme / "Drone.wav").header_length() == len(drone)


def test_one_cache_plays_mono_and_stereo_as_the_files_do(tmp_path):
    from sonorium.decoders import PcmFileDecoder, cache_lookup
    from sonorium.engine.pcm_cache import flac_available, predecode_theme
    if not flac_available():
        pytest.skip("this PyAV has no FLAC encoder")
    theme = make_theme(tmp_path / "themes")
    cache = tmp_path / "cache"
    predecode_theme(theme, cache)
    for channels in (1, 2):
        from_files = _render(theme, channels, seconds=6)
        from_cache = _render(theme, channels, seconds=6, decoder=PcmFileDecoder(lookup=cache_lookup(theme, cache)))
        assert np.array_equal(from_files, from_cache), channels


def test_old_wav_cache_entries_still_play_and_are_replaced(tmp_path):
    from sonorium.decoders import PcmFileDecoder, cache_lookup, mirror_lookup
    from sonorium.engine.pcm_cache import flac_available, predecode_theme, write_wav as cache_wav
    theme = make_theme(tmp_path / "themes")
    cache = tmp_path / "cache"
    # An older cache: 16-bit mono WAV copies at the same places
    old = mirror_lookup(theme, cache)
    from sonorium.decoders import PyAvDecoder
    from sonorium.theme_files import theme_audio_files
    for source in theme_audio_files(theme):
        target = old(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        samples = np.concatenate(list(PyAvDecoder().open(source).blocks())).astype(np.int16)
        write_wav(target, samples)
    reader = PcmFileDecoder(lookup=cache_lookup(theme, cache))
    assert cache_lookup(theme, cache)(theme / "Drone.wav") == cache / "Drone.wav"
    for channels in (1, 2):
        assert np.array_equal(_render(theme, channels, seconds=4, decoder=reader), _render(theme, channels, seconds=4))
    assert callable(cache_wav)
    if not flac_available():
        return
    import gc
    gc.collect()  # let go of the copies the renders read (Windows can't remove an open file)
    predecode_theme(theme, cache)
    assert not list(cache.rglob("*.wav")) and (cache / "Drone.flac").exists()
    assert cache_lookup(theme, cache)(theme / "Drone.wav") == cache / "Drone.flac"


def test_flac_reading_stays_cheap(tmp_path):
    """A minute of stereo FLAC reads in well under a second (the cache is read as it plays)."""
    from sonorium.decoders import PcmFileDecoder
    from sonorium.engine.pcm_cache import flac_available, write_flac
    if not flac_available():
        pytest.skip("this PyAV has no FLAC encoder")
    data = np.vstack([noise(60.0, 6000.0, seed=1), noise(60.0, 6000.0, seed=2)])
    write_flac(tmp_path / "m.flac", [data[:, i:i + 4096] for i in range(0, data.shape[1], 4096)])
    started = time.perf_counter()
    got = np.concatenate(list(PcmFileDecoder().open(tmp_path / "m.flac", channels=2).blocks()), axis=1)
    assert time.perf_counter() - started < 1.0
    assert np.array_equal(got, data)


# --- outputs ---

def test_mp3_for_speakers_mono_as_before_and_stereo_kept_apart():
    import io
    import av
    from sonorium.mp3_out import Mp3Encoder
    rng = np.random.default_rng(1)
    blocks = [(rng.standard_normal(1024) * 3000).astype(np.int16).reshape(1, -1) for _ in range(60)]
    # Mono blocks: the same bytes as the encoder has always made
    codec = av.CodecContext.create("mp3", "w")
    codec.sample_rate, codec.bit_rate, codec.layout, codec.format = RATE, 128_000, "stereo", "s32p"
    before = b""
    for block in blocks:
        frame = av.AudioFrame.from_ndarray(block, format="s16", layout="mono")
        frame.rate = RATE
        before += b"".join(bytes(p) for p in codec.encode(frame))
    encoder = Mp3Encoder()
    assert b"".join(b"".join(encoder.encode(b)) for b in blocks) == before
    # Stereo blocks: the left only, heard on the left only; blocks may switch kind mid-stream
    encoder = Mp3Encoder()
    data = b"".join(b"".join(encoder.encode(np.vstack([b, np.zeros_like(b)]) if i % 7 else b))
                    for i, b in enumerate(blocks))
    container = av.open(io.BytesIO(data), format="mp3")
    pcm = np.concatenate([f.to_ndarray() for f in container.decode(audio=0)], axis=1)
    assert pcm.shape[0] == 2 and np.abs(pcm[0]).mean() > 4 * np.abs(pcm[1]).mean()


def _stereo_channel(blocks):
    from sonorium.core.channel import ChannelState

    class FakeChannel:
        state = ChannelState.PLAYING
        _generator_running = True
        speakers_muted = False

        def __init__(self):
            self.chunks = [(i + 1, b) for i, b in enumerate(blocks)]

        def client_connected(self, *a):
            pass

        def client_disconnected(self, *a):
            pass

        def get_current_sequence(self):
            return 0

        def get_chunks_since(self, seq):
            return [c for c in self.chunks if c[0] > seq]

        def wait_for_data(self, timeout=0.1):
            time.sleep(timeout)
            return False

        def mark_listener_active(self):
            pass
    return FakeChannel()


def test_plugin_sinks_get_mono_unless_their_type_takes_stereo():
    from sonorium.core.audio_outputs import Attachment, CaptureSink
    block = np.vstack([np.full(1024, 3000, np.int16), np.full(1024, 1000, np.int16)])
    mono_block = np.full((1, 1024), 500, np.int16)
    for stereo, expected in ((False, [(1, 2000), (1, 500)]), (True, [(2, 3000), (2, 500)])):
        store = []
        attachment = Attachment("a", "s", "t", "d", {}, stereo=stereo)
        attachment.start(_stereo_channel([block, mono_block]), CaptureSink(store))
        deadline = time.time() + 3
        while len(store) < 2 and time.time() < deadline:
            time.sleep(0.01)
        attachment.stop()
        attachment.join(2)
        assert [(b.shape[0], int(b[0, 0])) for b in store] == expected, stereo
        if stereo:
            assert int(store[0][1, 0]) == 1000 and int(store[1][1, 0]) == 500  # a mono mix on both sides


def test_taps_get_the_mono_fold():
    from sonorium.core.taps import _read_only, push_track_blocks
    block = np.vstack([np.full(1024, 3000, np.int16), np.full(1024, 1000, np.int16)])
    view = _read_only(block)
    assert view.shape == (1, 1024) and int(view[0, 0]) == 2000 and not view.flags.writeable
    mono = np.zeros((1, 1024), np.int16)
    assert _read_only(mono).base is mono  # mono: the same array, read-only, as before

    class Tap:
        got = []

        def push(self, block, sample):
            self.got.append(block)
    tap = Tap()
    push_track_blocks({"Rain": [tap]}, "Rain", block, 0)
    assert tap.got[0].shape == (1, 1024)


def test_sounds_over_a_stereo_mix_play_centred():
    from sonorium.core.overlay import ChannelOverlay, Duck, Sound
    overlay = ChannelOverlay(RATE)
    overlay.add(Sound("s1", np.full(4096, 1000.0, np.float32), volume=1.0,
                      duck=Duck(db=6.0, attack=0.0, release=0.0), policy="now"))
    chunk = np.vstack([np.full(1024, 2000, np.int16), np.full(1024, -2000, np.int16)])
    out = None
    for _ in range(3):
        out = overlay.mix(chunk).astype(np.int32)
    assert out.shape == (2, 1024)
    # Ducked alike (6 dB: about half) and the sound added to both sides
    assert abs((out[0, 500] - 1000) + (out[1, 500] - 1000)) <= 1
    assert 900 < out[0, 500] - 1000 < 1100


# --- the server's switch ---

def test_stereo_setting_is_off_by_default_and_saved(tmp_path):
    from sonorium.core.state import SonoriumSettings, StateStore
    assert SonoriumSettings().stereo is False
    store = StateStore(tmp_path / "state.json")
    assert store.settings.stereo is False
    store.settings.stereo = True
    store.save()
    again = StateStore(tmp_path / "state.json")
    again.load()
    assert again.settings.stereo is True


def test_settings_api_switches_stereo(tmp_path):
    pytest.importorskip("fastapi")
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sonorium import decoders
    from sonorium.core.state import StateStore
    from sonorium.web import api_v2
    state = StateStore(tmp_path / "state.json")
    app = FastAPI()
    app.include_router(api_v2.create_api_router(session_manager=MagicMock(), group_manager=MagicMock(),
                                                ha_registry=MagicMock(), state_store=state))
    client = TestClient(app)
    assert client.get("/api/settings").json()["stereo"] is False
    assert decoders.current_channels() == 1
    r = client.put("/api/settings", json={"stereo": True})
    assert r.status_code == 200 and r.json()["stereo"] is True
    assert decoders.default_channels() == 2
    again = StateStore(tmp_path / "state.json")
    again.load()
    assert again.settings.stereo is True
    client.put("/api/settings", json={"stereo": False})
    assert decoders.default_channels() == 1
