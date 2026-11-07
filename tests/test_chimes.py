"""
Chimes (core/chimes.py): Sonorium's tones, the chime files and the one
string that names a chime, and what an announcement may send as its chime.
"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")

from playback_helpers import addon_modules  # noqa: E402

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.chimes", "sonorium.core.speaker_test_tone", "sonorium.paths") as mods:
        m.__dict__.update(mods.__dict__)
        yield


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(m.paths, "audio_dir", lambda: tmp_path)
    folder = tmp_path / ".chimes"
    folder.mkdir()
    (folder / "ding.mp3").write_bytes(b"x")
    (folder / "notes.txt").write_bytes(b"x")
    return tmp_path


def test_two_tone_is_the_speaker_test_sound():
    made, test = m.chimes.tone("two-tone").astype(float), m.speaker_test_tone.tone_samples().astype(float)
    assert made.shape == test.shape and np.abs(made - test).max() < 0.05 * np.abs(test).max()


def test_tones_take_pitch_and_speed_in_range():
    assert len(m.chimes.tone("rising", speed=2.0)) < len(m.chimes.tone("rising"))
    for bad in (dict(tone_id="nope"), dict(tone_id="soft", pitch=13), dict(tone_id="soft", speed=0.1),
                dict(tone_id="soft", pitch=True)):
        with pytest.raises(ValueError):
            m.chimes.tone(**bad)


def test_a_chime_setting_is_checked(root):
    assert m.chimes.check(None) == "" and m.chimes.check("none") == "none"
    assert m.chimes.check("tone:doorbell") == "tone:doorbell"
    assert m.chimes.check("file:ding.mp3") == "file:ding.mp3"
    for bad in ("tone:nope", "file:gone.mp3", "file:notes.txt", "file:../x.mp3", "ding.mp3", 5):
        with pytest.raises(ValueError):
            m.chimes.check(bad)
    assert m.chimes.files() == ["ding.mp3"]


def test_a_chime_file_is_in_the_folder_itself(root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "secret.mp3"
    outside.write_bytes(b"x")
    os.symlink(outside, root / ".chimes" / "link.mp3")
    (root / ".chimes" / ".hidden.mp3").write_bytes(b"x")
    for name in ("link.mp3", ".hidden.mp3", "..", "a/b.mp3", "C:x.mp3"):
        with pytest.raises(ValueError):
            m.chimes.file_path(name)
    assert m.chimes.files() == ["ding.mp3"]


def test_play_args(root):
    assert m.chimes.play_args("none") is None and m.chimes.play_args("") is None
    assert m.chimes.play_args("file:ding.mp3") == {"source": root / ".chimes" / "ding.mp3"}
    args = m.chimes.play_args("tone:soft")
    assert args["sample_rate"] == m.chimes.RATE and len(args["samples"]) > 0


def test_what_an_announcement_may_send(root, tmp_path_factory):
    theme = root / "Rain"
    theme.mkdir()
    (theme / "drop.wav").write_bytes(b"x")
    outside = tmp_path_factory.mktemp("elsewhere") / "secret.mp3"
    outside.write_bytes(b"x")
    os.symlink(outside, theme / "link.mp3")
    assert m.chimes.from_request(False) is False and m.chimes.from_request("none") is False
    assert m.chimes.from_request("https://h/a.mp3") == "https://h/a.mp3"
    assert m.chimes.from_request("tone:soft") == "tone:soft"
    assert m.chimes.from_request("ding.mp3") == root / ".chimes" / "ding.mp3"
    assert m.chimes.from_request("file:ding.mp3") == root / ".chimes" / "ding.mp3"
    assert m.chimes.from_request(str(theme / "drop.wav")) == (theme / "drop.wav").resolve()
    assert m.chimes.from_request("Rain/drop.wav") == (theme / "drop.wav").resolve()
    for bad in (str(outside), str(theme / "link.mp3"), "../" + outside.name, str(root / "Rain" / ".." / ".." / outside.name), str(root / ".chimes" / "notes.txt"),
                "", 3, True, "tone:nope", "gone.mp3", "ftp://h/a.mp3"):
        with pytest.raises(ValueError):
            m.chimes.from_request(bad)


def test_a_channel_plays_its_own_chime_else_the_default():
    assert m.chimes.channel_chime(SimpleNamespace(chime="tone:soft"), "file:a.mp3") == "tone:soft"
    assert m.chimes.channel_chime(SimpleNamespace(chime=""), "file:a.mp3") == "file:a.mp3"
    assert m.chimes.channel_chime(None, "") == "none"
