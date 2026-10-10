"""Merry-go-round groups: a continuous bed, each file crossfading into the next, through the real ThemeStream."""

import logging
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
av = pytest.importorskip("av")

ADDON = str(Path(__file__).resolve().parents[1] / "sonorium_addon")
recording = ThemeStream = None
RATE = 44100
CHUNK = 1024


@pytest.fixture(autouse=True, scope="module")
def addon_package():
    """Imports the add-on's sonorium package for these tests only (other tests use another one)."""
    global recording, ThemeStream
    saved = {k: v for k, v in sys.modules.items() if k == "sonorium" or k.startswith("sonorium.")}
    for k in saved:
        del sys.modules[k]
    sys.path.insert(0, ADDON)
    try:
        theme = pytest.importorskip("sonorium.theme")
        recording, ThemeStream = sys.modules["sonorium.recording"], theme.ThemeStream
        yield
    finally:
        sys.path.remove(ADDON)
        for k in [k for k in sys.modules if k == "sonorium" or k.startswith("sonorium.")]:
            del sys.modules[k]
        sys.modules.update(saved)


@pytest.fixture(autouse=True)
def seeded():
    state = random.getstate()
    random.seed(1234)
    yield
    random.setstate(state)


def write_audio(path: Path, samples: np.ndarray, rate=RATE, layout="mono", codec="mp3"):
    """Write int16 samples (channels x n) with PyAV."""
    out = av.open(str(path), "w")
    stream = out.add_stream(codec, rate=rate)
    stream.layout = layout
    step = 1152
    for i in range(0, samples.shape[1], step):
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(samples[:, i:i + step]), format="s16p" if codec == "mp3" else "s16", layout=layout)
        frame.rate = rate
        for packet in stream.encode(frame):
            out.mux(packet)
    for packet in stream.encode(None):
        out.mux(packet)
    out.close()


def tone_file(path: Path, seconds: float, freq: float, amp=3000, rate=RATE, layout="mono"):
    t = np.arange(int(seconds * rate)) / rate
    mono = (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)
    channels = 2 if layout == "stereo" else 1
    write_audio(path, np.tile(mono, (channels, 1)), rate, layout)


def noise_file(path: Path, seconds: float, amp=3000):
    rng = np.random.default_rng(7)
    samples = np.clip(rng.normal(0, amp, int(seconds * RATE)), -32000, 32000).astype(np.int16)
    write_audio(path, samples.reshape(1, -1))


def theme_with(folder: Path, names, crossfade=1.0, mode="merry_go_round", muted=(), presence=None, group="Bed"):
    """A freshly scanned theme definition, as refresh_themes builds one."""
    theme = SimpleNamespace(name="Forest", short_file_threshold=15.0, sonorium=SimpleNamespace(master_volume=1.0),
                            groups={group: {"mode": mode, "crossfade": crossfade}})
    theme.instances = []
    for name in names:
        path = next((folder / group).glob(f"{name}.*"))
        instance = recording.RecordingThemeInstance(recording.RecordingMetadata(path, folder), theme)
        instance.is_enabled = name not in muted
        instance.presence = (presence or {}).get(name, 1.0)
        theme.instances.append(instance)
    return theme


def bed_of(stream):
    beds = [s for s in stream.recording_streams if isinstance(s, recording.MerryGoRoundStream)]
    assert len(beds) == 1
    return beds[0]


def short(name):
    return name.split("/")[-1]


def starts(bed, since=0):
    return [(t, short(n)) for t, kind, n in bed.events if kind == "start" and t >= since]


def stops(bed, since=0):
    return [(t, short(n)) for t, kind, n in bed.events if kind == "stop" and t >= since]


def play(chunks, seconds):
    for _ in range(int(seconds * RATE / CHUNK)):
        next(chunks)


def pull(bed, seconds):
    """The bed's own output (before the theme mix), as float samples."""
    return np.concatenate([next(bed).reshape(-1) for _ in range(int(seconds * RATE / CHUNK))]).astype(np.float64)


def level_at(signal, center, freq, width=4096):
    """Amplitude of one frequency around a sample (Goertzel-style projection)."""
    part = signal[max(0, center - width // 2):center + width // 2]
    t = np.arange(len(part)) / RATE
    return 2 * abs(np.dot(part, np.exp(-2j * np.pi * freq * t))) / len(part)


@pytest.fixture
def forest(tmp_path):
    folder = tmp_path / "Forest"
    (folder / "Bed").mkdir(parents=True)
    return folder


FREQS = {"Wind A": 300.0, "Wind B": 500.0, "Wind C": 700.0, "Wind D": 900.0}


def make_files(folder, lengths, group="Bed"):
    for name, seconds in lengths.items():
        tone_file(folder / group / f"{name}.mp3", seconds, FREQS[name])


def test_files_crossfade_and_never_start_or_stop_together(forest):
    make_files(forest, {"Wind A": 4.0, "Wind B": 5.0, "Wind C": 6.0})
    stream = ThemeStream(theme_with(forest, ["Wind A", "Wind B", "Wind C"], crossfade=1.0))
    bed = bed_of(stream)
    assert len(stream.recording_streams) == 1  # the group plays as one stream
    out = pull(bed, 60)

    began, ended = starts(bed), stops(bed)
    assert began[0][0] == 0  # a bed: no initial delay
    assert len(began) >= 10
    assert len({t for t, _ in began}) == len(began)  # no two files start together
    assert len({t for t, _ in ended}) == len(ended)  # no two files stop together
    # Each file stops exactly one crossfade after the next one starts
    for (t_next, name_next), (t_stop, name_stop), (_, name_prev) in zip(began[1:], ended, began):
        assert name_stop == name_prev
        assert t_stop - t_next == RATE
    # And it sounds that way: both tones in the middle of an overlap, one tone in between
    for (t_next, name_next), (_, name_prev) in list(zip(began[1:], began))[:6]:
        mid = t_next + RATE // 2
        assert level_at(out, mid, FREQS[name_prev]) > 600
        assert level_at(out, mid, FREQS[name_next]) > 600
        solo = t_next + RATE + RATE // 2  # after the overlap, before the next one (files are 4 s or more)
        assert level_at(out, solo, FREQS[name_next]) > 2000
        assert level_at(out, solo, FREQS[name_prev]) < 100
    # Never silent after the first moment
    rms = [np.sqrt(np.mean(out[i:i + 4410] ** 2)) for i in range(4410, len(out) - 4410, 4410)]
    assert min(rms) > 1000


def test_no_immediate_repeats_and_the_order_varies(forest):
    make_files(forest, {"Wind A": 2.5, "Wind B": 2.5, "Wind C": 2.5})
    bed = bed_of(ThemeStream(theme_with(forest, ["Wind A", "Wind B", "Wind C"], crossfade=1.0)))
    pull(bed, 90)
    order = [name for _, name in starts(bed)]
    assert len(order) >= 40
    assert all(a != b for a, b in zip(order, order[1:]))
    # Not a fixed cycle: after a file, either of the other two comes next
    follows = {(a, b) for a, b in zip(order, order[1:])}
    assert len(follows) == 6


def test_interval_is_each_files_weight(forest, caplog):
    make_files(forest, {"Wind A": 2.2, "Wind B": 2.2, "Wind C": 2.2, "Wind D": 2.2})
    theme = theme_with(forest, ["Wind A", "Wind B", "Wind C", "Wind D"], crossfade=1.0, presence={"Wind D": 0.1})
    bed = bed_of(ThemeStream(theme))
    with caplog.at_level(logging.DEBUG, logger=recording.logger.name):
        pull(bed, 200)
    order = [name for _, name in starts(bed)]
    counts = {name: order.count(name) for name in FREQS}
    assert len(order) >= 120
    others = (counts["Wind A"] + counts["Wind B"] + counts["Wind C"]) / 3
    assert counts["Wind D"] < others / 2  # the low Interval comes up less often
    assert counts["Wind D"] > 0  # but still comes up
    picks = [r.getMessage() for r in caplog.records if "picked" in r.getMessage()]
    assert picks and "Wind" in picks[0] and "MerryGoRound" in picks[0]  # each pick is logged with weights


def test_a_single_file_crossfades_into_itself_without_a_dip(forest):
    noise_file(forest / "Bed" / "Rain.mp3", 3.0)
    theme = SimpleNamespace(name="Forest", short_file_threshold=15.0, sonorium=SimpleNamespace(master_volume=1.0),
                            groups={"Bed": {"mode": "merry_go_round", "crossfade": 1.0}})
    theme.instances = [recording.RecordingThemeInstance(
        recording.RecordingMetadata(forest / "Bed" / "Rain.mp3", forest), theme)]
    bed = bed_of(ThemeStream(theme))
    out = pull(bed, 20)
    began = starts(bed)
    assert [name for _, name in began] == ["Rain"] * len(began) and len(began) >= 8
    assert all(b - a < 3 * RATE for (a, _), (b, _) in zip(began, began[1:]))  # loops before it ends
    window = 2205  # 50 ms
    rms = np.array([np.sqrt(np.mean(out[i:i + window] ** 2)) for i in range(window, len(out) - window, window)])
    median = np.median(rms)
    assert rms.min() > 0.8 * median and rms.max() < 1.2 * median


def test_short_files_get_a_shorter_crossfade(forest):
    make_files(forest, {"Wind A": 3.0, "Wind B": 1.0})
    bed = bed_of(ThemeStream(theme_with(forest, ["Wind A", "Wind B"], crossfade=2.0)))
    pull(bed, 30)
    began, ended = starts(bed), stops(bed)
    for (t_next, name_next), (t_stop, _), (t_prev, name_prev) in zip(began[1:], ended, began):
        overlap = t_stop - t_next
        assert 0 < overlap <= 2 * RATE
        assert overlap <= (t_stop - t_prev) / 2 + 1  # never more than half the outgoing file
        if "Wind B" in (name_next, name_prev):
            assert overlap < 0.6 * RATE  # 1 s file: at most half of it


def test_files_added_and_removed_while_playing(forest):
    make_files(forest, {"Wind A": 2.5, "Wind B": 2.5, "Wind C": 2.5})
    stream = ThemeStream(theme_with(forest, ["Wind A", "Wind B"], crossfade=1.0))
    chunks = stream.iter_chunks()
    play(chunks, 15)
    bed = bed_of(stream)
    assert {name for _, name in starts(bed)} == {"Wind A", "Wind B"}

    # "Wind C" is uploaded: the theme is rescanned and the bed keeps playing
    stream.adopt(theme_with(forest, ["Wind A", "Wind B", "Wind C"], crossfade=1.0))
    assert bed_of(stream) is bed
    mark = bed._samples
    play(chunks, 15)
    assert "Wind C" in [name for _, name in starts(bed, mark)]

    # "Wind B" is removed: it finishes if it is playing, then never comes back
    stream.adopt(theme_with(forest, ["Wind A", "Wind C"], crossfade=1.0))
    assert bed_of(stream) is bed
    mark = bed._samples
    play(chunks, 20)
    later = [name for _, name in starts(bed, mark)]
    assert len(later) >= 6 and "Wind B" not in later


def test_a_muted_file_is_never_picked_and_muting_hands_over(forest):
    make_files(forest, {"Wind A": 4.0, "Wind B": 4.0, "Wind C": 4.0})
    theme = theme_with(forest, ["Wind A", "Wind B", "Wind C"], crossfade=1.0, muted={"Wind B"})
    stream = ThemeStream(theme)
    bed = bed_of(stream)
    pull(bed, 40)
    assert "Wind B" not in [name for _, name in starts(bed)]

    # Mute the file that is playing: it crossfades out at once
    while len(bed._voices) > 1 or bed._current.end is not None:  # not in a crossfade, not near its end
        next(bed)
    playing = bed._current.name
    next(i for i in theme.instances if i.name == playing).is_enabled = False
    mark = bed._samples
    pull(bed, 2)
    assert [name for _, name in stops(bed, mark)][:1] == [short(playing)]
    assert stops(bed, mark)[0][0] - mark <= RATE + CHUNK  # within one crossfade
    assert starts(bed, mark) and starts(bed, mark)[0][1] != short(playing)


def test_group_mute_and_volume_scale_the_bed(forest):
    make_files(forest, {"Wind A": 3.0, "Wind B": 3.0})
    theme = theme_with(forest, ["Wind A", "Wind B"], crossfade=1.0)
    bed = bed_of(ThemeStream(theme))
    loud = pull(bed, 4)
    theme.groups = {"Bed": {**theme.groups["Bed"], "volume": 0.5}}
    half = pull(bed, 4)
    assert 0.4 < np.sqrt(np.mean(half[RATE:] ** 2)) / np.sqrt(np.mean(loud[RATE:] ** 2)) < 0.6
    theme.groups = {"Bed": {**theme.groups["Bed"], "muted": True}}
    pull(bed, 0.1)
    assert np.abs(pull(bed, 3)).max() == 0


def test_other_formats_play(forest):
    tone_file(forest / "Bed" / "Wind A.mp3", 2.5, 300.0, rate=48000, layout="stereo")
    tone_file(forest / "Bed" / "Wind B.mp3", 2.5, 500.0)
    bed = bed_of(ThemeStream(theme_with(forest, ["Wind A", "Wind B"], crossfade=1.0)))
    out = pull(bed, 8)
    began = starts(bed)
    first = next(t for t, name in began if name == "Wind A")
    assert level_at(out, first + RATE + RATE // 2, 300.0) > 2000  # resampled to 44.1 kHz mono, same pitch


def test_mode_switch_rebuilds_the_group_on_a_playing_stream(forest):
    make_files(forest, {"Wind A": 2.5, "Wind B": 2.5})
    theme = theme_with(forest, ["Wind A", "Wind B"], crossfade=1.0, mode="intermittent")
    stream = ThemeStream(theme)
    chunks = stream.iter_chunks()
    play(chunks, 1)
    assert len(stream.recording_streams) == 2
    assert not any(isinstance(s, recording.MerryGoRoundStream) for s in stream.recording_streams)

    theme.groups = {"Bed": {"mode": "merry_go_round", "crossfade": 1.0}}  # as the group API sets it
    play(chunks, 3)
    bed = bed_of(stream)
    assert len(stream.recording_streams) == 1 and starts(bed)

    theme.groups = {"Bed": {"crossfade": 1.0}}  # no mode: Intermittent
    play(chunks, 1)
    assert len(stream.recording_streams) == 2
    assert all(isinstance(s, recording.SparsePlaybackStream) for s in stream.recording_streams)


def test_group_mode_and_crossfade_defaults():
    assert recording.group_mode(None) == "intermittent"
    assert recording.group_mode({"mode": "bogus"}) == "intermittent"
    assert recording.group_mode({"mode": "merry_go_round"}) == "merry_go_round"
    assert recording.group_crossfade({}) == 10.0
    assert recording.group_crossfade({"crossfade": 0.1}) == 1.0
    assert recording.group_crossfade({"crossfade": 500}) == 60.0
    assert recording.group_crossfade({"crossfade": "x"}) == 10.0
