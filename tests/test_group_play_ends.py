"""
A group's files (an Intermittent group such as Thunder) as "A Rainy Day"
played them: a file that ends cut off mid-sound must fade out, not stop dead;
an Ebb & Flow mode stored on a group member (as the one-time Auto conversion
left it) must not change how it plays: the group's mode decides; and
loading or saving the preset that is playing must change nothing audible.

The theme is generated on the spot (tests/engine_theme.py helpers).
"""
import json
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("av")

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "sonorium_addon", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from engine_theme import RATE, noise, write_wav  # noqa: E402

GAP = (2.0, 3.0)  # the group's gap between plays, seconds


def _cut_off(seconds: float, amp: float = 12000.0):
    """Loud noise to the very last sample (a clip cut from a longer recording)."""
    return noise(seconds, amp, seed=11)


def _decaying(seconds: float, amp: float = 12000.0):
    """A crack that decays to silence by itself."""
    data = noise(seconds, amp, seed=12).astype(np.float32)
    return (data * np.exp(-np.arange(len(data)) / (0.15 * RATE))).astype(np.int16)


def make_storm(root: Path) -> Path:
    folder = Path(root) / "Storm"
    write_wav(folder / "Rain.wav", noise(4.0, 1500.0, seed=3))
    write_wav(folder / "Thunder" / "Rumble.wav", _cut_off(5.0))
    write_wav(folder / "Thunder" / "Crack.wav", _decaying(2.0))
    tracks = {
        "Rain": {"volume": 1.0, "presence": 1.0, "playback_mode": "continuous"},
        # Ebb & Flow stored on group members: the group's mode decides
        "Thunder/Rumble": {"volume": 1.0, "presence": 0.2, "playback_mode": "presence", "seamless_loop": True},
        "Thunder/Crack": {"volume": 1.0, "presence": 0.2, "playback_mode": "presence", "exclusive": True},
    }
    metadata = {"spec_version": 2, "id": "storm-test", "name": "Storm", "ebb_duration": True,
                "auto_retired": True, "intrusions_retired": True, "tracks": tracks,
                "groups": {"Thunder": {"gap_min": GAP[0], "gap_max": GAP[1]}}}
    (folder / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    presets = {"presets": {"stormy": {"name": "Mild Summer Storm", "is_default": True,
                                      "tracks": json.loads(json.dumps(tracks)),
                                      "groups": {"Thunder": {"gap_min": GAP[0], "gap_max": GAP[1]}}}}}
    (folder / "presets.json").write_text(json.dumps(presets, indent=2), encoding="utf-8")
    return folder


def _engine(folder, seed=5):
    from sonorium.engine.engine import Engine
    return Engine(folder, preset_id="stormy", seed=seed)


def _plays(engine, seconds: float):
    """Render `seconds`; returns ([(start sample, track, length samples)], samples)."""
    from sonorium.recording import BLOCK_SAMPLES
    plays, last, out = [], {}, []
    for _ in range(int(seconds * RATE / BLOCK_SAMPLES)):
        out.append(engine.render(BLOCK_SAMPLES))
        for stream in engine.mix.sources:
            name = stream.instance.name
            key = (getattr(stream, "phase", None), getattr(stream, "phase_end", None))
            if key[0] == "play" and last.get(name) != key:
                start = engine.clock.samples - BLOCK_SAMPLES
                plays.append((start, name, key[1] - (stream.clock - BLOCK_SAMPLES)))
            last[name] = key
    return plays, np.concatenate(out).astype(np.float32)


def _level_db(samples):
    return 20 * np.log10(np.sqrt(np.mean(np.square(samples, dtype=np.float64))) + 1e-9)


def test_group_members_play_once_per_turn_with_the_gap(tmp_path):
    """Stored Ebb & Flow on a group member: it still plays Intermittent, one file at a time, gap between."""
    from sonorium.recording import SparsePlaybackStream
    engine = _engine(make_storm(tmp_path))
    plays, _ = _plays(engine, 100.0)
    kinds = {s.instance.name: type(s) for s in engine.mix.sources}
    assert kinds["Thunder/Rumble"] is SparsePlaybackStream
    assert kinds["Thunder/Crack"] is SparsePlaybackStream
    group = [(start, name, length) for start, name, length in plays if name.startswith("Thunder/")]
    assert len(group) >= 3
    for (start, _, length), (following, _, _) in zip(group, group[1:]):
        gap = (following - (start + length)) / RATE
        assert gap >= GAP[0] - 0.1  # never back to back, never overlapping
    for _, name, length in group:  # one pass of the file per turn, never looped within it
        assert length / RATE < (5.2 if name.endswith("Rumble") else 2.2)


def test_a_cut_off_group_file_fades_out(tmp_path):
    """A file ending loud fades out over the Track fade; it never drops 20 dB in 50 ms."""
    engine = _engine(make_storm(tmp_path))
    plays, audio = _plays(engine, 100.0)
    ends = [start + length for start, name, length in plays if name == "Thunder/Rumble"]
    assert ends
    window = RATE // 20
    for end in ends:
        if end + window > len(audio):
            continue
        before, after = audio[end - window:end], audio[end:end + window]
        assert _level_db(before) - _level_db(after) < 12.0
        # it was fading: the last half second is well below the middle of the play
        assert _level_db(audio[end - RATE // 2:end]) < _level_db(audio[end - 3 * RATE:end - 2 * RATE]) - 3.0


def test_a_decaying_group_file_keeps_its_ending():
    """A file that dies away by itself gets only the click-free edge (its natural ending is untouched)."""
    from sonorium.recording import ends_loud
    assert ends_loud(_cut_off(5.0))
    assert not ends_loud(_decaying(2.0))
    assert not ends_loud(np.zeros(RATE))


def test_loading_or_saving_the_playing_preset_changes_nothing(tmp_path):
    """The Theme Editor's Load (or Save) of the preset playing, mid-thunder: the same samples, no cut."""
    from sonorium.core import theme_edit
    folder = make_storm(tmp_path)

    def run(action):
        engine = _engine(folder)
        plays, first = _plays(engine, 70.0)
        assert any(name.startswith("Thunder/") for _, name, _ in plays)
        if action:
            action(engine)
        return np.concatenate([first, engine.render(30 * RATE).astype(np.float32)])

    def load(engine):
        loaded = engine._folder
        preset = loaded.presets["stormy"]
        theme_edit.apply_preset_to_tracks(loaded.theme, preset["tracks"])
        loaded.theme.groups = theme_edit.merge_preset_groups(loaded.theme.groups, preset.get("groups"))

    def save(engine):
        engine.reload_theme()

    control = run(None)
    for action in (load, save):
        assert np.array_equal(run(action), control)

