"""
What a listener receives, locked down so the streaming code can be rebuilt
without anyone hearing a difference:

- the MP3 a stream sends: MPEG-1 Layer III, 128 kb/s constant, 44.1 kHz,
  two channels, for a theme's own stream (/stream/{id}) and a channel's;
- the real-time pacing: one chunk's worth of audio per chunk's worth of
  wall-clock time, never sleeping when the producer is behind;
- a known theme (hard-cut loop, crossfade loop with a file fade, Ebb & Flow,
  an Intermittent group, a Merry-go-round group) mixed for 12 seconds from a
  fixed seed, compared chunk by chunk with MIX_LOCK.

MIX_LOCK (tests/data/mix_lock.json) was recorded from the code as it was
before the rewrite. The files are 44.1 kHz mono WAV, so decoding is exact on
every PyAV version; the comparison allows a hair of float rounding.
"""

import io
import json
import random
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
av = pytest.importorskip("av")

from playback_helpers import CHUNK, RATE, addon_modules  # noqa: E402

MIX_LOCK = Path(__file__).parent / "data" / "mix_lock.json"
MIX_SECONDS = 12.0
CHUNK_SECONDS = CHUNK / RATE
# MPEG-1 Layer III bitrates (kb/s) by header index
MP3_BITRATES = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.channel", "sonorium.theme", "sonorium.recording") as mods:
        m.__dict__.update(mods.__dict__)
        yield


@pytest.fixture
def seeded():
    state = random.getstate()
    random.seed(20261011)
    yield
    random.setstate(state)


class FakeClock:
    """time.time and time.sleep for pacing: the clock moves only by sleeping (or by `drift` per reading)."""

    def __init__(self, drift: float = 0.0):
        self.now = 1_000_000.0
        self.drift = drift
        self.sleeps = []

    def time(self):
        self.now += self.drift
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(time, "time", fake.time)
    monkeypatch.setattr(time, "sleep", fake.sleep)
    return fake


# --- files and the theme ---

def write_wav(path: Path, samples: np.ndarray):
    """Mono int16 samples as a 44.1 kHz WAV (PCM, so decoding gives them back exactly)."""
    out = av.open(str(path), "w")
    stream = out.add_stream("pcm_s16le", rate=RATE)
    stream.layout = "mono"
    data = samples.astype(np.int16).reshape(1, -1)
    for i in range(0, data.shape[1], 4096):
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(data[:, i:i + 4096]), format="s16", layout="mono")
        frame.rate = RATE
        for packet in stream.encode(frame):
            out.mux(packet)
    for packet in stream.encode(None):
        out.mux(packet)
    out.close()


def tone(seconds, freq, amp, seed=None):
    t = np.arange(int(seconds * RATE)) / RATE
    signal = amp * np.sin(2 * np.pi * freq * t)
    if seed is not None:
        signal = signal + np.random.default_rng(seed).normal(0, amp / 4, len(t))
    return np.clip(signal, -32000, 32000)


@pytest.fixture(scope="module")
def lock_folder(tmp_path_factory):
    return write_lock_files(tmp_path_factory.mktemp("Lock"))


def write_lock_files(folder: Path) -> Path:
    (folder / "Birds").mkdir()
    (folder / "Waves").mkdir()
    write_wav(folder / "Hum.wav", tone(3.3, 440.0, 2500))
    write_wav(folder / "Bed.wav", tone(6.0, 220.0, 3000, seed=1))
    write_wav(folder / "Wind.wav", tone(20.0, 150.0, 2000, seed=2))
    write_wav(folder / "Birds" / "Chirp A.wav", tone(0.8, 2400.0, 4000))
    write_wav(folder / "Birds" / "Chirp B.wav", tone(1.1, 1900.0, 4000))
    write_wav(folder / "Waves" / "Swell A.wav", tone(3.0, 90.0, 3000, seed=3))
    write_wav(folder / "Waves" / "Swell B.wav", tone(2.5, 110.0, 3000, seed=4))
    return folder


TRACKS = ("Hum.wav", "Bed.wav", "Wind.wav", "Birds/Chirp A.wav", "Birds/Chirp B.wav",
          "Waves/Swell A.wav", "Waves/Swell B.wav")


def lock_theme(folder: Path):
    """The theme MIX_LOCK was recorded from."""
    theme = SimpleNamespace(id="lock", name="Lock", short_file_threshold=15.0,
                            library=SimpleNamespace(master_volume=6.0),
                            groups={"Birds": {"gap_min": 0.5, "gap_max": 1.5},
                                    "Waves": {"mode": "merry_go_round", "crossfade": 1.0}})
    theme.tracks = []
    for name in TRACKS:
        track = m.recording.ThemeTrack(m.recording.AudioFile(folder / name, folder), theme)
        theme.tracks.append(track)
    hum, bed, wind = theme.tracks[:3]
    hum.crossfade_enabled = False  # hard-cut loop
    hum.volume = 0.7
    bed.fade_in, bed.fade_out = 0.5, 0.4  # crossfade loop with the file's own fades
    wind.presence = 0.5  # Ebb & Flow
    return theme


def new_mix(theme, overrides=None):
    return m.theme.ThemeMix(theme, overrides)


def render(mix, seconds):
    chunks = mix.blocks()
    return np.concatenate([next(chunks).reshape(-1) for _ in range(int(seconds * RATE / CHUNK))])


def chunk_stats(signal):
    blocks = signal.reshape(-1, CHUNK).astype(np.float64)
    return [[round(float(np.sqrt(np.mean(b * b))), 2), int(np.max(np.abs(b)))] for b in blocks]


# --- MP3 ---

def mp3_headers(data: bytes):
    """Each MPEG audio frame header in a raw MP3 byte stream: (version, layer, kbps, rate index, channel mode)."""
    frames, i = [], 0
    while i + 4 <= len(data):
        h = int.from_bytes(data[i:i + 4], "big")
        assert h >> 21 == 0x7FF, f"no frame sync at byte {i}"
        version, layer = (h >> 19) & 3, (h >> 17) & 3
        kbps, rate_index = MP3_BITRATES[(h >> 12) & 0xF], (h >> 10) & 3
        padding, mode = (h >> 9) & 1, (h >> 6) & 3
        frames.append((version, layer, kbps, rate_index, mode))
        i += 144 * kbps * 1000 // 44100 + padding
    assert i == len(data), "stream ends inside a frame"
    return frames


def assert_listener_mp3(data: bytes, min_seconds: float):
    frames = mp3_headers(data)
    assert len(frames) >= int(min_seconds * RATE / 1152)
    # MPEG-1, Layer III, 128 kb/s constant, 44.1 kHz, never mono
    assert {f[:4] for f in frames} == {(3, 1, 128, 0)}
    assert all(f[4] != 3 for f in frames)
    with av.open(io.BytesIO(data), format="mp3") as container:
        stream = container.streams.audio[0]
        decoded = [frame for frame in container.decode(stream)]
    assert decoded and decoded[0].sample_rate == RATE
    assert len(decoded[0].layout.channels) == 2
    return np.concatenate([f.to_ndarray()[0] for f in decoded])


def take_bytes(iterator, clock, chunks):
    data = b""
    while len(clock.sleeps) < chunks:
        data += next(iterator)
    return data


def test_theme_stream_sends_128k_stereo_mp3_at_44100(lock_folder, clock, seeded):
    iterator = iter(new_mix(lock_theme(lock_folder)))
    data = take_bytes(iterator, clock, int(3.0 / CHUNK_SECONDS))
    iterator.close()
    pcm = assert_listener_mp3(data, 2.5)
    assert np.max(np.abs(pcm)) > 0.05  # the mix, not silence


def test_channel_stream_sends_128k_stereo_mp3_at_44100(lock_folder, seeded):
    channel = m.channel.Channel(id=1)
    channel.state = m.channel.ChannelState.PLAYING
    stream = channel.get_stream()
    mix = new_mix(lock_theme(lock_folder)).blocks()
    pending = [next(mix) for _ in range(int(2.0 / CHUNK_SECONDS))]
    handed = []

    def get_chunks_since(sequence):
        # Hand every chunk over once, then end the stream as a stop would
        if handed:
            return []
        handed.append(True)
        channel.state = m.channel.ChannelState.IDLE
        return list(enumerate(pending, start=sequence + 1))

    channel.get_chunks_since = get_chunks_since
    data = b"".join(stream)
    pcm = assert_listener_mp3(data, 1.5)
    assert np.max(np.abs(pcm)) > 0.05
    assert channel._client_count == 0  # the listener was counted out when the stream ended


def test_theme_stream_paces_one_chunk_per_chunk_of_time(lock_folder, clock, seeded):
    iterator = iter(new_mix(lock_theme(lock_folder)))
    take_bytes(iterator, clock, 200)
    iterator.close()
    # Never ahead: each chunk sleeps off exactly its own length
    assert len(clock.sleeps) >= 200
    assert np.allclose(clock.sleeps, CHUNK_SECONDS, rtol=0, atol=1e-6)


def test_theme_stream_does_not_sleep_when_behind(lock_folder, monkeypatch, seeded):
    fake = FakeClock(drift=0.5)  # every reading of the clock is half a second later
    monkeypatch.setattr(time, "time", fake.time)
    monkeypatch.setattr(time, "sleep", fake.sleep)
    iterator = iter(new_mix(lock_theme(lock_folder)))
    data = b""
    while len(data) < 40_000:
        data += next(iterator)
    iterator.close()
    assert fake.sleeps == []


def test_channel_generator_paces_one_chunk_per_chunk_of_time(clock):
    channel = m.channel.Channel(id=2)
    channel.state = m.channel.ChannelState.PLAYING
    channel._generator_running = True
    made = []

    def next_chunk():
        made.append(True)
        if len(made) >= 150:
            channel._generator_running = False
        return np.zeros((1, CHUNK), dtype=np.int16)

    channel.next_chunk = next_chunk
    channel._generator_loop()
    assert len(made) == 150 and len(clock.sleeps) == 150
    assert np.allclose(clock.sleeps, CHUNK_SECONDS, rtol=0, atol=1e-6)
    assert channel.get_current_sequence() == 150


def test_channel_generator_does_not_sleep_when_behind(monkeypatch):
    fake = FakeClock(drift=0.5)
    monkeypatch.setattr(time, "time", fake.time)
    monkeypatch.setattr(time, "sleep", fake.sleep)
    channel = m.channel.Channel(id=3)
    channel.state = m.channel.ChannelState.PLAYING
    channel._generator_running = True
    count = [0]

    def next_chunk():
        count[0] += 1
        if count[0] >= 50:
            channel._generator_running = False
        return np.zeros((1, CHUNK), dtype=np.int16)

    channel.next_chunk = next_chunk
    channel._generator_loop()
    assert fake.sleeps == []


# --- the mix ---

def test_known_theme_mixes_as_recorded(lock_folder, seeded):
    signal = render(new_mix(lock_theme(lock_folder)), MIX_SECONDS)
    stats = chunk_stats(signal)
    expected = json.loads(MIX_LOCK.read_text(encoding="utf-8"))
    assert len(stats) == len(expected["chunks"])
    for i, ((rms, peak), (want_rms, want_peak)) in enumerate(zip(stats, expected["chunks"])):
        assert abs(rms - want_rms) <= 0.5 + 0.002 * want_rms, f"chunk {i}: rms {rms} != {want_rms}"
        assert abs(peak - want_peak) <= 2 + 0.002 * want_peak, f"chunk {i}: peak {peak} != {want_peak}"


def test_known_theme_mix_is_repeatable(lock_folder):
    """Same seed, same mix: the lock above can only fail for a real change."""
    runs = []
    for _ in range(2):
        random.seed(20261011)
        runs.append(render(new_mix(lock_theme(lock_folder)), 4.0))
    assert np.array_equal(runs[0], runs[1])


def record_mix_lock(folder: Path):  # pragma: no cover - run by hand to (re)record MIX_LOCK
    random.seed(20261011)
    signal = render(new_mix(lock_theme(folder)), MIX_SECONDS)
    MIX_LOCK.parent.mkdir(exist_ok=True)
    MIX_LOCK.write_text(json.dumps({"seconds": MIX_SECONDS, "chunk": CHUNK, "chunks": chunk_stats(signal)}),
                        encoding="utf-8")
