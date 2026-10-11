"""
A channel fades its whole mix in when it starts and out before it stops,
crossfades between themes over the Crossfade setting, and glides a preset's
volume instead of jumping (real mixer, real mp3s, measured from the output).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("av")

from playback_helpers import CHUNK, RATE, addon_modules, envelope, tone_mp3  # noqa: E402

A_HZ, B_HZ = 1000.0, 300.0
m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.channel", "sonorium.theme", "sonorium.recording",
                       "sonorium.core.session_manager", "sonorium.core.state") as mods:
        m.__dict__.update(mods.__dict__)
        yield


@pytest.fixture(scope="module")
def tones(tmp_path_factory):
    folder = tmp_path_factory.mktemp("tones")
    tone_mp3(folder / "A.mp3", 25.0, A_HZ)
    tone_mp3(folder / "B.mp3", 25.0, B_HZ)
    return folder


def make_theme(folder, names, theme_id):
    theme = SimpleNamespace(id=theme_id, name=theme_id, groups={}, short_file_threshold=15.0,
                            sonorium=SimpleNamespace(master_volume=1.0))
    theme.instances = [m.recording.RecordingThemeInstance(m.recording.RecordingMetadata(folder / f"{n}.mp3", folder), theme)
                       for n in names]
    theme.get_stream = lambda overrides=None: m.theme.ThemeStream(theme, overrides)
    return theme


def channel_without_thread():
    channel = m.channel.Channel(id=1)
    channel._ensure_generator_running = lambda: None  # the test pulls the chunks itself
    return channel


def pull(channel, seconds):
    return np.concatenate([channel.next_chunk().reshape(-1) for _ in range(int(seconds * RATE / CHUNK))]).astype(np.float64)


def test_start_fades_the_mix_in(tones):
    channel = channel_without_thread()
    channel.set_theme(make_theme(tones, ["A"], "a"), {}, fade_in=2.0)
    level = envelope(pull(channel, 4.0), A_HZ)
    full = level[-10:].mean()
    assert level[0] < 0.08 * full  # starts from silence
    assert 0.55 * full < level[20] < 0.85 * full  # half way (equal-power curve: ~0.7)
    assert level[42:].min() > 0.97 * full  # full level after 2 s
    assert np.all(np.diff(level[:40]) > -0.01 * full)  # only up


def test_stop_fades_the_mix_out_then_stays_silent(tones):
    channel = channel_without_thread()
    channel.set_theme(make_theme(tones, ["A"], "a"), {})
    before = envelope(pull(channel, 1.0), A_HZ)[-5:].mean()
    channel.fade_out(2.0)
    assert not channel.is_faded_out
    level = envelope(pull(channel, 2.5), A_HZ)
    assert level[0] > 0.9 * before
    assert 0.55 * before < level[20] < 0.85 * before
    assert level[41:].max() < 0.01 * before
    assert np.all(np.diff(level[:40]) < 0.01 * before)  # only down
    assert channel.is_faded_out
    assert not np.any(pull(channel, 0.5))  # silent until it is stopped
    channel.fade_in(1.0)  # played again: back up
    assert envelope(pull(channel, 1.5), A_HZ)[-5:].mean() > 0.95 * before


def test_theme_change_crossfades_over_the_given_length(tones):
    channel = channel_without_thread()
    channel.set_theme(make_theme(tones, ["A"], "a"), {})
    a_full = envelope(pull(channel, 1.0), A_HZ)[-5:].mean()
    channel._generator_running = True  # as when playing: the change is queued for a crossfade
    channel.set_theme(make_theme(tones, ["B"], "b"), {}, crossfade=2.0)
    out = pull(channel, 3.0)
    a, b = envelope(out, A_HZ), envelope(out, B_HZ)
    b_full = b[-10:].mean()
    assert channel.current_theme_id == "b"
    assert a[0] > 0.9 * a_full and b[0] < 0.1 * b_full
    assert 0.5 * a_full < a[20] < 0.9 * a_full and 0.5 * b_full < b[20] < 0.9 * b_full
    assert a[41:].max() < 0.02 * a_full  # the old theme is gone after 2 s
    # Equal power: no dip and no bump in the middle
    power = (a[:40] / a_full) ** 2 + (b[:40] / b_full) ** 2
    assert power.min() > 0.8 and power.max() < 1.2


def test_zero_crossfade_cuts(tones):
    channel = channel_without_thread()
    channel.set_theme(make_theme(tones, ["A"], "a"), {})
    pull(channel, 0.5)
    channel._generator_running = True
    channel.set_theme(make_theme(tones, ["B"], "b"), {}, crossfade=0)
    out = pull(channel, 0.5)
    assert envelope(out, A_HZ)[1:].max() < 0.02 * envelope(out, B_HZ)[1:].mean()


def test_preset_glides_volume_over_three_seconds(tones):
    channel = channel_without_thread()
    channel.set_theme(make_theme(tones, ["A"], "a"), {})
    full = envelope(pull(channel, 1.0), A_HZ)[-5:].mean()
    preset = m.recording.preset_track_overrides({"A": {"volume": 0.2}})
    channel.set_track_overrides(preset, glide_seconds=3.0)
    assert channel.is_gliding
    level = envelope(pull(channel, 4.0), A_HZ) / full
    assert level[0] > 0.9
    assert 0.5 < level[30] < 0.7  # half way at 1.5 s: about 0.6
    assert abs(level[65:].mean() - 0.2) < 0.03  # there after 3 s
    assert np.all(np.diff(level[:60]) < 0.02)  # only down, no jump
    assert not channel.is_gliding
    assert channel._track_overrides == {"A": {"volume": 0.2}}  # exactly the preset at the end


def test_glide_back_to_theme_value_and_mute_is_immediate(tones):
    channel = channel_without_thread()
    overrides = m.recording.preset_track_overrides({"A": {"volume": 0.5, "presence": 1.0}})
    channel.set_theme(make_theme(tones, ["A"], "a"), overrides)
    pull(channel, 0.5)
    # A preset that leaves A out: A glides back to the theme's own volume (1.0)
    channel.set_track_overrides({}, glide_seconds=2.0)
    assert channel._track_overrides["A"]["volume"] == 0.5
    pull(channel, 1.0)
    assert 0.6 < channel._track_overrides["A"]["volume"] < 0.9
    pull(channel, 1.5)
    assert channel._track_overrides == {}
    # Fields that don't glide switch at once (the mute then fades by itself)
    channel.set_track_overrides(m.recording.preset_track_overrides({"A": {"muted": True}}), glide_seconds=3.0)
    assert channel._track_overrides["A"]["is_enabled"] is False
    assert not channel.is_gliding


def make_session_manager(tmp_path, tones, crossfade):
    state = m.state.StateStore(tmp_path / "state.json")
    state.settings.crossfade_duration = crossfade
    state.settings.enabled_speakers = ["media_player.x"]
    registry = MagicMock()
    registry.resolve_selection.return_value = ["media_player.x"]
    controller = MagicMock()
    events = []
    channels = m.channel.ChannelManager(max_channels=1)

    def recorder(name):
        async def record(ids, *args):
            channel = channels.get_channel(1)
            events.append((name, asyncio.get_running_loop().time(), channel.is_faded_out, channel.state))
            return {i: True for i in ids}
        return record
    for name in ("play_media_multi", "set_volume_multi", "stop_multi", "pause_multi"):
        setattr(controller, name, AsyncMock(side_effect=recorder(name)))
    manager = m.session_manager.SessionManager(state, registry, controller, "http://test", channel_manager=channels)
    theme = make_theme(tones, ["A"], "a")
    manager.get_theme = lambda theme_id: theme if theme_id == "a" else None
    manager._theme_presets = lambda theme_id: {}
    session = manager.create(theme_id="a", adhoc_selection=m.state.SpeakerSelection(include_speakers=["media_player.x"]))
    return manager, session, channels.get_channel(1), events


def test_session_stop_tells_speakers_only_after_the_fade(tmp_path, tones):
    manager, session, channel, events = make_session_manager(tmp_path, tones, crossfade=0.6)

    async def run():
        loop = asyncio.get_running_loop()
        await manager.play(session.id)
        assert channel.output_level < 0.3  # fading in from silence
        await asyncio.sleep(0.9)
        assert channel.output_level == 1.0
        started = loop.time()
        await manager.stop(session.id)
        stop = [e for e in events if e[0] == "stop_multi"]
        assert len(stop) == 1
        name, at, faded, _ = stop[0]
        assert at - started >= 0.55 and faded  # the speakers were told after the fade
        assert channel.state == m.channel.ChannelState.IDLE
        assert not session.is_playing
    asyncio.run(run())


def test_playing_again_during_the_fade_cancels_the_stop(tmp_path, tones):
    manager, session, channel, events = make_session_manager(tmp_path, tones, crossfade=0.8)

    async def run():
        await manager.play(session.id)
        await asyncio.sleep(1.0)
        stopping = asyncio.create_task(manager.stop(session.id))
        await asyncio.sleep(0.3)
        assert 0.0 < channel.output_level < 1.0
        await manager.play(session.id)
        await stopping
        assert not [e for e in events if e[0] == "stop_multi"]
        assert channel.state == m.channel.ChannelState.PLAYING and session.is_playing
        await asyncio.sleep(1.0)
        assert channel.output_level == 1.0
        channel.stop()
    asyncio.run(run())


def test_pause_fades_out_before_pausing_speakers(tmp_path, tones):
    manager, session, channel, events = make_session_manager(tmp_path, tones, crossfade=0.5)

    async def run():
        await manager.play(session.id)
        await asyncio.sleep(0.7)
        await manager.pause(session.id)
        pause = [e for e in events if e[0] == "pause_multi"]
        assert len(pause) == 1 and pause[0][2]  # faded out first
        assert channel.state == m.channel.ChannelState.PLAYING  # a paused channel keeps its stream
        await manager.play(session.id)  # resume: fades back in
        await asyncio.sleep(0.8)
        assert channel.output_level == 1.0
        channel.stop()
    asyncio.run(run())


def test_saving_a_preset_updates_channels_playing_it(tmp_path, tones):
    """A channel keeps its copy of a preset from when it was picked; saving the preset changes it."""
    manager, session, channel, events = make_session_manager(tmp_path, tones, crossfade=0.6)
    presets = {"heavy": {"tracks": {"A": {"muted": False}}}}
    manager._theme_presets = lambda theme_id: presets
    session.preset_id, session.is_playing = "heavy", True
    calls = []
    fake = SimpleNamespace(set_track_overrides=lambda overrides, glide_seconds=0.0: calls.append((overrides, glide_seconds)))
    manager._channel_for = lambda s: fake
    presets["heavy"] = {"tracks": {"A": {"muted": True}}}  # muted in the editor and saved
    assert manager.preset_saved({"a"}, "heavy") == 1
    assert calls[0][0]["A"]["is_enabled"] is False and calls[0][1] == m.session_manager.PRESET_GLIDE_SECONDS
    # Another preset, or another theme, leaves the channel alone
    assert manager.preset_saved({"a"}, "calm") == 0
    assert manager.preset_saved({"b"}, "heavy") == 0


def test_browsers_listening_here_are_counted_apart_from_speakers():
    channel = m.channel.Channel(id=1)
    channel._local_time -= 30  # long ago
    channel.client_connected()  # a speaker
    assert channel.local_idle_seconds() >= 30
    channel.client_connected(local=True)  # a browser (Listen here)
    assert channel.local_idle_seconds() == 0.0
    channel.client_disconnected(local=True)
    assert 0.0 <= channel.local_idle_seconds() < 5 and channel._client_count == 1
    channel._local_time -= 30
    channel.expect_local_listener()  # asked to listen: the wait for the browser counts from now
    assert channel.local_idle_seconds() < 5


def test_muting_the_speakers_silences_their_stream_but_not_a_browsers():
    channel = m.channel.Channel(id=1)
    speaker, browser = m.channel.ChannelStream(channel), m.channel.ChannelStream(channel, local=True)
    chunk = np.full((1, CHUNK), 10000, dtype=np.int16)
    assert np.array_equal(speaker._apply_mute(chunk), chunk)
    channel.speakers_muted = True
    ramp = speaker._apply_mute(chunk)  # fades out over one chunk
    assert ramp[0, 0] == 10000 and ramp[0, -1] == 0 and np.all(np.diff(ramp[0].astype(int)) <= 0)
    assert not speaker._apply_mute(chunk).any()
    assert np.array_equal(browser._apply_mute(chunk), chunk)
    channel.speakers_muted = False
    back = speaker._apply_mute(chunk)  # fades back in
    assert back[0, 0] == 0 and back[0, -1] == 10000
    channel.stop()
    assert channel.speakers_muted is False

def test_a_track_given_a_mode_while_playing_starts_playing(tones):
    """A file added to a playing theme joins as Unassigned (silent); picking its Mode in the editor makes it play."""
    theme = make_theme(tones, ["A", "B"], "ab")
    for inst in theme.instances:
        inst.playback_mode = m.recording.PlaybackMode.CONTINUOUS
    b = theme.instances[1]
    b.playback_mode = m.recording.PlaybackMode.UNASSIGNED
    channel = channel_without_thread()
    channel.set_theme(theme, {})
    before = pull(channel, 1.0)
    a_full = envelope(before, A_HZ)[-5:].mean()
    assert envelope(before, B_HZ).max() < 0.02 * a_full  # Unassigned: not played
    b.playback_mode = m.recording.PlaybackMode.CONTINUOUS  # the editor's Mode menu
    after = envelope(pull(channel, 4.0), B_HZ)
    assert after[0] < 0.1 * a_full  # fades in, no jump
    assert after[-10:].mean() > 0.8 * a_full  # then plays like A
    b.playback_mode = m.recording.PlaybackMode.UNASSIGNED  # moved back to Unassigned
    assert envelope(pull(channel, 1.0), B_HZ)[-5:].max() < 0.02 * a_full

