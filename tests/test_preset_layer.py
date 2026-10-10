"""Each channel's preset is its own layer: two channels on one theme can use different presets."""

from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")
pytest.importorskip("av")

from test_crossfade_loop import recording  # noqa: E402  (loads recording.py on its own)


def theme_track(name="Rain", volume=1.0):
    meta = SimpleNamespace(name=name, is_short_file=lambda threshold: False)
    track = recording.RecordingThemeInstance(meta)
    track.volume = volume
    return track


def test_preset_converts_saved_settings():
    overrides = recording.preset_track_overrides({
        "Rain": {"volume": 0.4, "presence": 0.5, "muted": True, "playback_mode": "sparse", "seamless_loop": True},
        "Wind": {"playback_mode": "nonsense"},
    })
    rain = overrides["Rain"]
    assert (rain["volume"], rain["presence"], rain["is_enabled"], rain["crossfade_enabled"]) == (0.4, 0.5, False, False)
    assert rain["playback_mode"] == recording.PlaybackMode.SPARSE
    assert overrides["Wind"]["playback_mode"] == recording.PlaybackMode.AUTO


def test_two_channels_on_one_theme_keep_their_own_presets():
    track = theme_track(volume=0.9)
    calm = {"Rain": {"volume": 0.2}}
    stormy = {"Rain": {"volume": 1.0}}
    channel_a, channel_b, no_preset = (recording.TrackView(track, o) for o in (calm, stormy, {}))
    assert (channel_a.volume, channel_b.volume, no_preset.volume) == (0.2, 1.0, 0.9)

    # The track mixer edits the theme: channels without a preset for that setting follow it live
    track.volume = 0.7
    track.presence = 0.3
    assert (channel_a.volume, no_preset.volume, channel_a.presence) == (0.2, 0.7, 0.3)

    # A preset change on one channel, made in place, leaves the other alone
    calm["Rain"] = {"volume": 0.5}
    assert (channel_a.volume, channel_b.volume) == (0.5, 1.0)


def test_track_view_resolves_playback_mode_with_channel_values():
    track = theme_track()
    track.presence = 1.0
    view = recording.TrackView(track, {"Rain": {"presence": 0.4}})
    assert track._resolve_playback_mode() == recording.PlaybackMode.CONTINUOUS
    assert view._resolve_playback_mode() == recording.PlaybackMode.PRESENCE
    assert view.name == "Rain"


def test_preset_only_sets_what_it_saved():
    track = theme_track(volume=0.6)
    track.presence = 0.3
    view = recording.TrackView(track, recording.preset_track_overrides({"Rain": {"muted": True}}))
    assert (view.is_enabled, view.volume, view.presence) == (False, 0.6, 0.3)  # the rest follows the theme


def test_theme_editing_sets_presets_aside_and_brings_them_back():
    from types import SimpleNamespace
    from test_enabled_speakers import load_session_manager
    module, state = load_session_manager()
    manager = module.SessionManager.__new__(module.SessionManager)
    session = SimpleNamespace(id="s1", is_playing=True, theme_id="rain", preset_id="calm")
    other = SimpleNamespace(id="s2", is_playing=True, theme_id="forest", preset_id="calm")
    manager.state = SimpleNamespace(sessions={"s1": session, "s2": other})
    manager._editing_themes = {}
    layers = {"s1": None, "s2": None}
    channels = {"s1": SimpleNamespace(set_track_overrides=lambda o: layers.__setitem__("s1", o)),
                "s2": SimpleNamespace(set_track_overrides=lambda o: layers.__setitem__("s2", o))}
    manager._channel_for = lambda s: channels[s.id]
    manager.preset_overrides = lambda theme_id, preset_id: {"Rain": {"volume": 0.2}}

    assert manager.set_theme_editing("rain", True) == 1
    assert layers == {"s1": {}, "s2": None}  # only the edited theme's channel follows the editor
    assert manager._layer_for(session) == {}  # a preset change meanwhile keeps following the editor
    assert manager.set_theme_editing("rain", False) == 1
    assert layers["s1"] == {"Rain": {"volume": 0.2}} and "rain" not in manager._editing_themes
