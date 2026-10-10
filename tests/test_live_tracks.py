"""A theme playing on a channel follows its files: a track added mid-play joins the mix at once."""

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
av = pytest.importorskip("av")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sonorium_addon"))
pytest.importorskip("sonorium.theme")
from sonorium import recording  # noqa: E402
from sonorium.theme import ThemeStream  # noqa: E402


def silent_mp3(path: Path, seconds: float):
    out = av.open(str(path), "w")
    stream = out.add_stream("mp3", rate=44100)
    stream.layout = "mono"
    samples = np.zeros((1, int(seconds * 44100)), np.int16)
    for i in range(0, samples.shape[1], 1152):
        frame = av.AudioFrame.from_ndarray(samples[:, i:i + 1152], format="s16", layout="mono")
        frame.rate = 44100
        for packet in stream.encode(frame):
            out.mux(packet)
    for packet in stream.encode(None):
        out.mux(packet)
    out.close()


def theme_with(folder: Path, names: list[str], muted=()):
    """A freshly scanned theme definition, as refresh_themes builds one."""
    theme = SimpleNamespace(name="Storm", groups={"Thunder": {"gap_min": 0, "gap_max": 0}},
                            short_file_threshold=15.0, sonorium=SimpleNamespace(master_volume=1.0))
    theme.instances = []
    for name in names:
        meta = recording.RecordingMetadata(folder / "Thunder" / f"{name}.mp3", folder)
        instance = recording.RecordingThemeInstance(meta, theme)
        instance.is_enabled = name not in muted
        theme.instances.append(instance)
    return theme


class Starts(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.played = []

    def emit(self, record):
        msg = record.getMessage()
        if "starting playback" in msg:
            self.played.append(msg.split('"')[1].split("/")[-1])


def play_until(chunks, starts, count):
    while len(starts.played) < count:
        next(chunks)


def test_a_track_added_mid_play_joins_the_group(tmp_path):
    folder = tmp_path / "Storm"
    (folder / "Thunder").mkdir(parents=True)
    for name in ("Crack A", "Crack B", "Crack C", "Crack D"):
        silent_mp3(folder / "Thunder" / f"{name}.mp3", 2.0)

    starts = Starts()
    recording.logger.addHandler(starts)
    level = recording.logger.level
    recording.logger.setLevel(logging.DEBUG)
    try:
        stream = ThemeStream(theme_with(folder, ["Crack A", "Crack B", "Crack C"]))
        chunks = stream.iter_chunks()
        play_until(chunks, starts, 6)
        assert set(starts.played) <= {"Crack A", "Crack B", "Crack C"}

        # "Crack D" is uploaded: the theme is rescanned and the playing stream follows it
        stream.adopt(theme_with(folder, ["Crack A", "Crack B", "Crack C", "Crack D"]))
        before = len(starts.played)
        play_until(chunks, starts, before + 4)
        assert "Crack D" in starts.played[before:]  # no drag yet: an early pick

        # A track removed from the theme leaves the mix
        stream.adopt(theme_with(folder, ["Crack A", "Crack B", "Crack D"]))
        before = len(starts.played)
        play_until(chunks, starts, before + 8)
        assert "Crack C" not in starts.played[before + 1:]  # (it may be mid-turn when removed)
    finally:
        recording.logger.removeHandler(starts)
        recording.logger.setLevel(level)


def test_adopted_tracks_read_the_new_settings(tmp_path):
    folder = tmp_path / "Storm"
    (folder / "Thunder").mkdir(parents=True)
    for name in ("Crack A", "Crack B"):
        silent_mp3(folder / "Thunder" / f"{name}.mp3", 2.0)
    stream = ThemeStream(theme_with(folder, ["Crack A", "Crack B"]))
    first = stream.recording_streams[0]
    stream.adopt(theme_with(folder, ["Crack A", "Crack B"], muted={"Crack A"}))
    assert stream.recording_streams[0] is first  # same stream, still in place
    assert first.instance.is_enabled is False  # but it reads the rescanned theme's settings
