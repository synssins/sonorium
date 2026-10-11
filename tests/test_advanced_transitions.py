"""
Advanced transitions (core/transitions.py): the envelope math, how the
runner plans a step's transitions (pre-fade before the step), the real mixer
playing them (levels measured from the output), storage and API checks, the
per-chunk cost, the "Playback:" log lines, and the web preview's copy of the
envelope (app.js) giving the same numbers.
"""

import json
import logging
import math
import random
import re
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")

from playback_helpers import CHUNK, RATE, addon_modules, envelope, tone_mp3  # noqa: E402

m = SimpleNamespace()
ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "sonorium_addon" / "sonorium" / "web" / "static" / "js" / "app.js"


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.transitions", "sonorium.core.channel", "sonorium.theme", "sonorium.recording",
                       "sonorium.core.sequences", "sonorium.core.session_manager", "sonorium.core.state",
                       "sonorium.core.playback_runner", "sonorium.mixing") as mods:
        m.__dict__.update(mods.__dict__)
        yield


def env(side, styles, values=None, a=0.0, b=0.8, window=60.0, cap=None, seed=1, pa=1.0, pb=1.0):
    return m.transitions.Envelope(side, styles, values or {}, a, b, window, cap, seed, pa=pa, pb=pb)


def levels(e, step=0.1):
    return np.array([e.level(i * step) for i in range(int(e.window / step) + 1)])


# --- Envelope math ---

def test_gust_pattern_is_deterministic_from_its_seed():
    T = m.transitions
    seed = T.gust_seed("in", "storm", "Wind/Howl")
    assert seed == T.gust_seed("in", "storm", "Wind/Howl")
    assert seed != T.gust_seed("out", "storm", "Wind/Howl") != T.gust_seed("in", "rain", "Wind/Howl")
    one = levels(env("in", ["gusts"], {"gusts": 70}, seed=seed))
    two = levels(env("in", ["gusts"], {"gusts": 70}, seed=seed))
    other = levels(env("in", ["gusts"], {"gusts": 70}, seed=seed + 1))
    assert np.array_equal(one, two)
    assert np.abs(one - other).max() > 0.02
    # Known values: the preview (app.js) must produce exactly these
    assert T.fnv1a("in|storm|Wind/Howl") == seed
    rng = T.mulberry32(12345)
    assert [round(next(rng), 12) for _ in range(3)] == [round(x, 12) for x in _mulberry_reference(12345, 3)]


def _mulberry_reference(seed, n):
    """mulberry32 written out with Python ints, as the C original."""
    out, a = [], seed
    for _ in range(n):
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = (a ^ (a >> 15)) * (a | 1) & 0xFFFFFFFF
        t = (t + ((t ^ (t >> 7)) * (t | 61) & 0xFFFFFFFF)) & 0xFFFFFFFF ^ t
        out.append(((t ^ (t >> 14)) & 0xFFFFFFFF) / 2 ** 32)
    return out


def test_gusts_swing_while_rising_capped_and_land_on_the_target():
    e = env("in", ["gusts"], {"gusts": 90}, a=0.4, b=0.86, window=60.0, cap=0.7, seed=99)
    lv = levels(e)
    base = 0.4 + 0.46 * np.linspace(0, 1, len(lv))
    assert lv[0] == pytest.approx(0.4, abs=1e-6)  # rides from the current volume...
    assert lv[-1] == pytest.approx(0.86)  # ...to the new one
    assert np.all(lv <= np.maximum(0.7, base) + 1e-9)  # peaks never above Max volume (unless the ramp is)
    swings = lv - base
    assert swings.max() > 0.05 and swings.min() < -0.05  # uneven up and down
    assert np.sum(np.diff(np.sign(np.diff(lv))) != 0) > 4  # several surges, not one ramp


def test_swell_is_an_s_curve_over_its_speed():
    e = env("in", ["swell"], {"swell": 20}, a=0.0, b=0.8, window=75.0)
    lv = levels(e)
    assert np.all(np.diff(lv) >= -1e-12)  # only up
    assert lv[0] == 0.0 and e.level(20.0) == pytest.approx(0.8) and e.level(50.0) == pytest.approx(0.8)
    assert e.level(10.0) == pytest.approx(0.4, abs=1e-6)  # half way at half its time
    assert e.level(2.0) < 0.5 * 0.8 * 2 / 20  # slow start: under half of a straight line's
    # Speed is bounded by the fade window
    assert env("in", ["swell"], {"swell": 500}, window=30.0).swell == 30.0
    assert env("in", ["swell"], {"swell": 0}, window=30.0).swell == 5.0
    out = levels(env("out", ["swell"], {"swell": 30}, a=0.7, b=0.0, window=40.0))
    assert np.all(np.diff(out) <= 1e-12) and out[-1] == 0.0


def test_distance_starts_muffled_and_quiet_and_ends_clear():
    e = env("in", ["dist"], {"dist": 80}, a=0.0, b=1.0, window=40.0)
    cutoffs = [e.cutoff(t) for t in np.arange(0.5, 39.5, 0.5)]
    assert all(c is not None for c in cutoffs)
    assert np.all(np.diff(cutoffs) > 0)  # the low-pass opens up
    assert cutoffs[0] < 600 and e.cutoff(40.0) is None  # far: dull; at the end: clear
    assert e.level(8.0) < 0.2 and e.level(40.0) == pytest.approx(1.0)  # faint, then at the preset volume
    recede = env("out", ["dist"], {"dist": 50}, a=0.8, b=0.0, window=40.0)
    falling = [recede.cutoff(t) for t in np.arange(1.0, 39.0, 1.0)]
    assert np.all(np.diff(falling) < 0) and recede.level(40.0) == 0.0


def test_build_and_thin_out_ramp_interval_and_volume():
    e = env("in", ["build"], {"build": 60}, a=0.0, b=0.8, window=30.0, pb=0.5)
    assert e.presence(0.0) == pytest.approx(0.5 * 0.4)  # interval 20% -> 50%
    assert e.presence(30.0) == pytest.approx(0.5)
    assert e.level(0.0) == pytest.approx(0.8 * (1 - 0.7 * 0.6))  # quieter at first
    assert e.level(30.0) == pytest.approx(0.8)
    assert np.all(np.diff([e.presence(t) for t in range(31)]) > 0)
    thin = env("out", ["build"], {"build": 100}, a=0.7, b=0.0, window=30.0, pa=0.8)
    assert thin.presence(0.0) == pytest.approx(0.8) and thin.presence(30.0) == pytest.approx(0.01)
    assert thin.level(27.0) == pytest.approx(0.7 * 0.3, rel=0.15)  # down to the lower level...
    assert thin.level(30.0) == 0.0  # ...then off


def test_combined_styles_multiply():
    gusts = env("in", ["gusts"], {"gusts": 80}, b=1.0, window=40.0, seed=5)
    both = env("in", ["gusts", "dist"], {"gusts": 80, "dist": 50}, b=1.0, window=40.0, seed=5)
    dist = env("in", ["dist"], {"dist": 50}, b=1.0, window=40.0, seed=5)
    for t in (10.0, 20.0, 30.0):
        gain = 10 ** (-(6 + 24 * 0.5) * dist.distance(t) / 20)
        assert both.level(t) == pytest.approx(min(gusts.level(t) * gain, max(1.0, t / 40 * gain)), rel=1e-6)


def test_lowpass_matches_a_plain_loop_across_chunks():
    f = m.transitions.OnePoleLowpass(RATE)
    x = np.random.default_rng(1).normal(0, 3000, 4096)
    out = np.concatenate([f.process(x[i:i + CHUNK], 900.0) for i in range(0, 4096, CHUNK)])
    alpha = 1 - math.exp(-2 * math.pi * 900 / RATE)
    s1 = s2 = x[0]
    ref = []
    for v in x:
        s1 += alpha * (v - s1)
        s2 += alpha * (s1 - s2)
        ref.append(s2)
    assert np.abs(out - np.array(ref)).max() < 1e-6


# --- Storage and checks ---

def test_transitions_are_checked_and_smooth_ones_dropped():
    T = m.transitions
    good = {"in": {"Wind": {"styles": ["dist", "gusts", "gusts"], "values": {"gusts": 80, "dist": 45},
                            "max_volume": 0.85, "pre_fade": 45},
                   "Rain": {"styles": [], "pre_fade": 0}},
            "out": {}}
    assert T.clean_step(good) == {"in": {"Wind": {"styles": ["gusts", "dist"], "values": {"gusts": 80.0, "dist": 45.0},
                                                  "pre_fade": 45.0, "max_volume": 0.85}}}
    for bad, why in [({"in": {"W": {"styles": ["wobble"]}}}, "styles"),
                     ({"in": {"W": {"styles": ["gusts"], "values": {"gusts": 120}}}}, "between"),
                     ({"in": {"W": {"styles": ["gusts"], "pre_fade": 91}}}, "pre_fade"),
                     ({"in": {"W": {"styles": ["gusts"], "max_volume": 1.5}}}, "max_volume"),
                     ({"in": {"W": {"styles": ["gusts"], "speed": 3}}}, "unknown"),
                     ({"sideways": {}}, "side")]:
        with pytest.raises(T.TransitionError, match=why):
            T.clean_step(bad)
    with pytest.raises(T.TransitionError, match="not a preset"):
        T.clean_transitions({"nope": good}, ["a", "b"])


def test_sequence_keeps_transitions_of_its_presets_only(tmp_path):
    S = m.sequences
    step = {"in": {"Wind": {"styles": ["gusts"]}}}
    seq = S.clean_sequence({"name": "S", "presets": ["a", "b", "c"], "transitions": {"b": step}}, ["a", "b", "c"])
    assert seq["transitions"] == {"b": {"in": {"Wind": {"styles": ["gusts"], "values": {}, "pre_fade": 0.0}}}}
    assert S.to_response("s", seq, S.DEFAULTS)["transitions"]["b"]["in"]["Wind"]["styles"] == ["gusts"]
    later = S.clean_sequence({"presets": ["a", "c"]}, ["a", "b", "c"], seq)  # b taken out: its transitions go
    assert "transitions" not in later
    with pytest.raises(S.SequenceError):
        S.clean_sequence({"name": "S", "presets": ["a"], "transitions": {"b": step}}, ["a", "b"])
    # A deleted preset leaves the saved sequences' transitions too
    (tmp_path / "presets.json").write_text(json.dumps({"presets": {}, "sequences": {"s": seq}}), encoding="utf-8")
    assert S.drop_preset(tmp_path, "b") == ["s"]
    assert "b" not in S.load_sequences(tmp_path)["s"].get("transitions", {})
    # One step at a time
    updated = S.set_step_transitions(seq, "c", {"out": {"Rain": {"styles": ["fresh"], "pre_fade": 10}}})
    assert set(updated["transitions"]) == {"b", "c"}
    assert "c" not in S.set_step_transitions(updated, "c", {})["transitions"]


# --- Planning a step (runner) ---

def _instances(names):
    R = m.recording
    theme = SimpleNamespace(groups={}, short_file_threshold=15.0, name="Storm")
    return [R.ThemeTrack(R.AudioFile(Path(f"{n}.mp3"), key=n), theme) for n in names]


def test_step_plans_pick_tracks_that_really_come_in_or_leave():
    R, T = m.recording, m.transitions
    insts = _instances(["Wind", "Rain", "Drums"])
    before = R.preset_track_overrides({"Wind": {"muted": True}, "Rain": {"volume": 0.4}, "Drums": {"volume": 0.5}})
    after = R.preset_track_overrides({"Wind": {"volume": 0.9}, "Rain": {"volume": 0.86}, "Drums": {"muted": True}})
    cfg_in = {"Wind": {"styles": ["gusts"], "pre_fade": 30}, "Rain": {"styles": ["swell"]},
              "Drums": {"styles": ["dist"]}}  # Drums leaves at this step: its "in" is not used
    cfg_out = {"Drums": {"styles": ["fresh"]}, "Wind": {"styles": ["dist"]}}
    plans = T.step_plans(insts, before, after, cfg_in, cfg_out, "storm", "rain", lambda i, o: R.TrackView(i, o))
    by = {p["track"]: p for p in plans}
    assert set(by) == {"Wind", "Rain", "Drums"}
    assert by["Wind"]["side"] == "in" and by["Wind"]["target_volume"] == 0.9 and by["Wind"]["pre_fade"] == 30
    assert by["Rain"]["side"] == "in" and by["Rain"]["target_volume"] == 0.86
    assert by["Drums"]["side"] == "out" and by["Drums"]["target_volume"] == 0.0
    assert by["Wind"]["seed"] == T.gust_seed("in", "storm", "Wind")
    assert by["Drums"]["seed"] == T.gust_seed("out", "rain", "Drums")


def test_runner_plans_the_next_step_with_its_pre_fade(tmp_path):
    from test_sequences_playlists import at, build, run
    import test_sequences_playlists as runner_tests
    runner_tests.m.__dict__.update(m.__dict__)
    themes = {"storm": {
        "presets": {"a": {"name": "Drizzle", "is_default": True, "tracks": {"Wind": {"muted": True}}},
                    "b": {"name": "Rain", "tracks": {"Wind": {"volume": 0.9}}}},
        "sequences": {"s": {"name": "S", "presets": ["a", "b"], "at_end": "reverse",
                            "hold_min": 100, "hold_max": 100, "ramp": 30,
                            "transitions": {"b": {"in": {"Wind": {"styles": ["gusts"], "pre_fade": 45}}},
                                            "a": {"in": {"Wind": {"styles": ["swell"]}}}}}}}}
    t = build(tmp_path, themes)
    insts = _instances(["Wind"])
    t.manager.get_theme = lambda theme_id: SimpleNamespace(id=theme_id, name="Storm", instances=insts, groups={})
    scheduled, cancelled = [], []
    t.channel.schedule_transitions = lambda plans: scheduled.append((t.clock.now, plans))
    t.channel.cancel_transitions = lambda: cancelled.append(t.clock.now)
    run(t.manager.play(t.session.id))
    t.manager.start_sequence(t.session.id, "s")  # on a, glide 3 s; the step to b at 3 + 100 s
    (when, plans), = scheduled
    plan, = plans
    assert plan["track"] == "Wind" and plan["side"] == "in"
    assert plan["delay"] == pytest.approx(103 - 45)  # starts 45 s before the step
    assert plan["window"] == pytest.approx(45 + 30)  # pre-fade + ramp
    at(t, 103)  # the step to b: the reverse step back to a is planned (Wind leaves: no "out" set -> nothing)
    assert t.session.preset_id == "b" and len(scheduled) == 1
    # Next by hand drops the planned transitions
    run(t.manager.skip(t.session.id))
    assert cancelled


def test_pre_fade_never_starts_before_the_glide_into_the_current_preset(tmp_path):
    from test_sequences_playlists import build, run
    import test_sequences_playlists as runner_tests
    runner_tests.m.__dict__.update(m.__dict__)
    themes = {"storm": {
        "presets": {"a": {"name": "A", "is_default": True, "tracks": {"Wind": {"muted": True}}},
                    "b": {"name": "B", "tracks": {"Wind": {"volume": 0.9}}}},
        "sequences": {"s": {"name": "S", "presets": ["a", "b"], "hold_min": 20, "hold_max": 20, "ramp": 10,
                            "transitions": {"b": {"in": {"Wind": {"styles": ["swell"], "pre_fade": 90}}}}}}}}
    t = build(tmp_path, themes)
    insts = _instances(["Wind"])
    t.manager.get_theme = lambda theme_id: SimpleNamespace(id=theme_id, name="Storm", instances=insts, groups={})
    scheduled = []
    t.channel.schedule_transitions = lambda plans: scheduled.append(plans)
    run(t.manager.play(t.session.id))
    t.manager.start_sequence(t.session.id, "s")
    plan, = scheduled[0]
    assert plan["delay"] == pytest.approx(3.0)  # right after the 3 s glide, not 90 s before the step
    assert plan["pre_fade_used"] == pytest.approx(20.0) and plan["window"] == pytest.approx(30.0)


# --- The real mixer ---

@pytest.fixture(scope="module")
def tones(tmp_path_factory):
    pytest.importorskip("av")
    folder = tmp_path_factory.mktemp("tones")
    for name, hz in (("A", 1000.0), ("B", 300.0), ("C", 500.0), ("D", 700.0), ("E", 1300.0)):
        tone_mp3(folder / f"{name}.mp3", 40.0, hz)
    tone_mp3(folder / "Crack.mp3", 1.5, 2000.0)
    return folder


def make_theme(folder, names, groups=None):
    R = m.recording
    theme = SimpleNamespace(id="t", name="Storm", groups=groups or {}, short_file_threshold=15.0,
                            sonorium=SimpleNamespace(master_volume=1.0))
    theme.instances = []
    for n in names:
        key, group = (n, None) if "/" not in n else (n, n.split("/")[0])
        file = folder / f"{n.split('/')[-1]}.mp3"
        theme.instances.append(R.ThemeTrack(R.AudioFile(file, key=key, group=group), theme))
    theme.get_stream = lambda overrides=None: m.theme.ThemeStream(theme, overrides)
    return theme


def channel_on(theme, overrides):
    channel = m.channel.Channel(id=1, name="Bedroom")
    channel._ensure_generator_running = lambda: None
    channel.set_theme(theme, overrides)
    return channel


def pull(channel, seconds):
    return np.concatenate([channel.next_chunk().reshape(-1) for _ in range(int(seconds * RATE / CHUNK))]).astype(np.float64)


def plans_for(theme, before, after, cfg_in, cfg_out, delay, window):
    R = m.recording
    plans = m.transitions.step_plans(theme.instances, before, after, cfg_in, cfg_out, "next", "this",
                                     lambda i, o: R.TrackView(i, o))
    for plan in plans:
        plan.update(delay=delay, window=window, pre_fade_used=window - 10.0)
    return plans


def test_gusts_with_pre_fade_start_before_the_step_and_reach_the_target(tones):
    R = m.recording
    theme = make_theme(tones, ["A", "B"])
    before = R.preset_track_overrides({"B": {"muted": True}})
    after = R.preset_track_overrides({"B": {"muted": False, "volume": 0.8}, "A": {"volume": 0.8}})
    channel = channel_on(theme, before)
    a_full = envelope(pull(channel, 1.0), 1000.0)[-5:].mean()
    # The step is 10 s from now with a 10 s ramp; B comes in with Gusts and a 5 s pre-fade
    cfg = {"B": {"styles": ["gusts"], "values": {"gusts": 80}, "pre_fade": 5}}
    channel.schedule_transitions(plans_for(theme, before, after, cfg, {}, delay=5.0, window=15.0))
    early = pull(channel, 10.0)
    channel.set_track_overrides(after, glide_seconds=10.0, fade_seconds=10.0)  # the step
    late = pull(channel, 16.0)
    b = envelope(np.concatenate([early, late]), 300.0) / a_full
    a = envelope(np.concatenate([early, late]), 1000.0) / a_full
    sec = lambda x, s: x[int(s * 20):int(s * 20) + 20].mean()
    assert b[:96].max() < 0.01  # silent before its pre-fade
    assert sec(b, 7.5) > 0.01 and sec(b, 9.0) > sec(b, 6.0)  # under way before the step (at 10 s)
    assert sec(b, 9.0) < 0.5  # ...but not yet in
    # In by the end of its window (20 s), at the preset volume next to A (same files: same ratio)
    assert sec(b, 21) / sec(a, 21) == pytest.approx(1.0, rel=0.05)
    assert sec(b, 25) / sec(a, 25) == pytest.approx(1.0, rel=0.05)
    assert channel._theme_stream.transitions_running() == []
    # Uneven: the gusts move it up and down on the way
    rising = np.array([sec(b, s) for s in np.arange(5.0, 20.0, 0.25)])
    assert np.sum(np.diff(rising) < -0.005) >= 2


def test_distance_entrance_filters_highs_at_first(tones):
    R = m.recording
    theme = make_theme(tones, ["A", "E"])
    before = R.preset_track_overrides({"E": {"muted": True}})
    after = R.preset_track_overrides({"E": {"muted": False}})
    channel = channel_on(theme, before)
    a_full = envelope(pull(channel, 1.0), 1000.0)[-5:].mean()
    cfg = {"E": {"styles": ["dist"], "values": {"dist": 100}}}
    channel.schedule_transitions(plans_for(theme, before, after, cfg, {}, delay=0.0, window=20.0))
    channel.set_track_overrides(after, glide_seconds=20.0, fade_seconds=20.0)
    out = pull(channel, 24.0)
    e = envelope(out, 1300.0) / a_full
    sec = lambda x, s: x[int(s * 20):int(s * 20) + 20].mean()
    assert sec(e, 3) < 0.03  # far: faint and dull (1.3 kHz well above a 250 Hz cutoff)
    assert sec(e, 3) < sec(e, 10) < sec(e, 17) < sec(e, 22)
    assert sec(e, 22) == pytest.approx(sec(envelope(out, 1000.0) / a_full, 22), rel=0.06)  # clear at the end


def test_exit_thin_out_gets_quieter_then_stops(tones, monkeypatch):
    R = m.recording
    monkeypatch.setattr(R.ExclusionGroupCoordinator, "INITIAL_DELAY", 0.0)  # the group starts at once
    monkeypatch.setattr(R.ExclusionGroupCoordinator, "COLLECT_SECONDS", 0.0)
    groups = {"Thunder": {"gap_min": 0.3, "gap_max": 0.3}}
    theme = make_theme(tones, ["A", "Thunder/Crack"], groups)
    on = R.preset_track_overrides({"Thunder/Crack": {"volume": 1.0, "presence": 0.8}})
    off = R.preset_track_overrides({"Thunder/Crack": {"muted": True}})
    channel = channel_on(theme, on)
    first = pull(channel, 6.0)
    a_full = envelope(first, 1000.0)[-20:].mean()
    crack = envelope(first, 2000.0) / a_full
    assert crack.max() > 0.5  # it plays (one crack after another, 0.3 s apart)
    cfg = {"Thunder/Crack": {"styles": ["build"], "values": {"build": 100}}}
    channel.schedule_transitions(plans_for(theme, on, off, {}, cfg, delay=0.0, window=20.0))
    channel.set_track_overrides(off, glide_seconds=20.0, fade_seconds=20.0)
    stream = channel._theme_stream
    view = stream._views["Thunder/Crack"]
    pull(channel, 1.0)
    assert view.presence == pytest.approx(0.8 - 0.79 * 1 / 20, abs=0.03)  # the interval thins out with it
    out = pull(channel, 24.0)
    c = envelope(out, 2000.0) / envelope(out, 1000.0).clip(1e-9)
    peak = lambda s0, s1: c[int(s0 * 20):int(s1 * 20)].max()
    assert peak(0, 4) > 0.75
    assert peak(14, 18) < 0.6 * peak(0, 4)  # quieter as it leaves
    assert peak(20.5, 24) < 0.01  # then stopped
    assert stream.transitions_running() == [] and stream._pins == {}


def test_cancel_glides_back_to_what_the_channel_plays(tones):
    R = m.recording
    theme = make_theme(tones, ["A", "B"])
    before = R.preset_track_overrides({"B": {"muted": True}})
    after = R.preset_track_overrides({"B": {"muted": False}})
    channel = channel_on(theme, before)
    a_full = envelope(pull(channel, 1.0), 1000.0)[-5:].mean()
    cfg = {"B": {"styles": ["swell"], "values": {"swell": 30}, "pre_fade": 20}}
    channel.schedule_transitions(plans_for(theme, before, after, cfg, {}, delay=0.0, window=40.0))
    pull(channel, 15.0)
    channel.cancel_transitions()  # e.g. a preset picked by hand that keeps B off
    out = pull(channel, 5.0)
    b = envelope(out, 300.0) / a_full
    assert b[:5].mean() > 0.1 and b[-20:].max() < 0.01
    assert channel._theme_stream.transitions_running() == []


def test_per_chunk_cost_with_four_transitions(tones):
    R = m.recording
    names = ["A", "B", "C", "D", "E"]
    theme = make_theme(tones, names)
    before = R.preset_track_overrides({n: {"muted": True} for n in names[1:]})
    after = R.preset_track_overrides({n: {"muted": False} for n in names[1:]})
    cfg = {"B": {"styles": ["gusts", "dist"]}, "C": {"styles": ["swell", "dist"]},
           "D": {"styles": ["gusts", "swell"]}, "E": {"styles": ["dist"]}}

    def timed(with_transitions):
        channel = channel_on(theme, before if with_transitions else after)
        pull(channel, 1.0)
        if with_transitions:
            channel.schedule_transitions(plans_for(theme, before, after, cfg, {}, delay=0.0, window=30.0))
            channel.set_track_overrides(after, glide_seconds=30.0, fade_seconds=30.0)
            pull(channel, 0.1)
            assert len(channel._theme_stream.transitions_running()) == 4
        n = int(10 * RATE / CHUNK)
        start = time.perf_counter()
        for _ in range(n):
            channel.next_chunk()
        return (time.perf_counter() - start) / n * 1000.0

    plain = min(timed(False) for _ in range(2))
    busy = min(timed(True) for _ in range(2))
    print(f"\nper chunk: {plain:.3f} ms plain, {busy:.3f} ms with 4 transitions (a chunk is 23.2 ms)")
    assert busy < 5.0  # far inside the 23 ms a chunk lasts
    assert busy - plain < 2.0


# --- "Playback:" log lines ---

def test_mix_level_is_logged_when_it_moves_a_decibel(caplog):
    stream = m.theme.ThemeStream.__new__(m.theme.ThemeStream)
    stream._context = {"label": "Bedroom: Storm"}
    stream.theme_def = SimpleNamespace(name="Storm")
    stream._logged_gain = None
    stream.mix_level = SimpleNamespace(gain=1.0, count=1.0)
    with caplog.at_level(logging.INFO):
        stream._log_mix_level()  # the first level is only remembered
        stream.mix_level.gain, stream.mix_level.count = 10 ** (-0.9 / 20), 1.5
        stream._log_mix_level()  # -0.9 dB: not yet
        stream.mix_level.gain, stream.mix_level.count = 10 ** (-3.0 / 20), 2.0
        stream._log_mix_level()
        stream._log_mix_level()  # unchanged: nothing more
    lines = [r.getMessage() for r in caplog.records if "mix level" in r.getMessage()]
    assert lines == ["Playback: Bedroom: Storm: mix level -3.0 dB (2.0 tracks sounding)"]


def test_sequence_step_and_toggles_are_logged(tmp_path, caplog):
    from test_sequences_playlists import at, build, run
    import test_sequences_playlists as runner_tests
    runner_tests.m.__dict__.update(m.__dict__)
    t = build(tmp_path)
    run(t.manager.play(t.session.id))
    t.manager.start_sequence(t.session.id, "up_down")
    with caplog.at_level(logging.INFO):
        at(t, 13)
    steps = [r.getMessage() for r in caplog.records if "step 2/3" in r.getMessage()]
    assert steps and steps[0].startswith("Playback: Channel '")


def test_track_toggle_and_transition_start_are_logged(tones, caplog):
    R = m.recording
    theme = make_theme(tones, ["A", "B"])
    before = R.preset_track_overrides({"B": {"muted": True}})
    after = R.preset_track_overrides({"B": {"muted": False}})
    channel = channel_on(theme, before)
    pull(channel, 0.5)
    with caplog.at_level(logging.INFO):
        channel.set_track_overrides(after, glide_seconds=3.0)
        pull(channel, 0.5)
        channel.schedule_transitions(plans_for(theme, after, before, {}, {"B": {"styles": ["gusts", "dist"]}},
                                               delay=0.0, window=12.0))
        pull(channel, 0.2)
    text = [r.getMessage() for r in caplog.records]
    assert "Playback: Bedroom: Storm: B on, fading in over 3s" in text
    assert any(s.startswith("Playback: Bedroom: Storm: B leaving (Gusts dying down + Recede) over 12s") for s in text)


# --- The preview's copy of the envelope (app.js) ---

def _js_envelope_block():
    text = APP_JS.read_text(encoding="utf-8")
    match = re.search(r"// --- transition envelope.*?// --- end transition envelope ---", text, re.S)
    assert match, "app.js: transition envelope block not found"
    return match.group(0)


def test_preview_envelope_matches_the_server():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    cases = [
        ("in", ["gusts"], {"gusts": 80}, 0.0, 1.0, 75.0, 0.85, "in|ohs|Wind"),
        ("in", ["gusts", "dist"], {"gusts": 30, "dist": 45}, 0.4, 0.86, 60.0, None, "in|ohs|Rain"),
        ("in", ["swell", "build"], {"swell": 25, "build": 84}, 0.0, 0.8, 75.0, None, "in|x|T"),
        ("out", ["gusts", "swell"], {"gusts": 50, "swell": 40}, 0.7, 0.3, 50.0, 0.6, "out|x|W"),
        ("out", ["build"], {"build": 85}, 0.7, 0.0, 30.0, None, "out|y|C"),
        ("out", ["dist"], {"dist": 70}, 0.5, 0.0, 30.0, None, "out|y|D"),
        ("in", ["fresh"], {}, 0.0, 0.6, 30.0, None, "in|z|F"),
        ("in", [], {}, 0.2, 0.6, 30.0, None, "in|z|S"),
    ]
    times = [0.0, 0.7, 3.3, 9.9, 17.0, 24.5, 31.0, 44.4, 59.0, 74.9, 80.0]
    script = _js_envelope_block() + "\nconst cases = " + json.dumps(cases) + ";\nconst times = " + json.dumps(times) + ";\n" + """
const out = cases.map(c => {
  const e = txEnvelope(c[0], c[1], c[2], c[3], c[4], c[5], c[6], txFnv1a(c[7]), 0.6, 0.9);
  return times.map(t => [e.level(t), e.cutoff(t), e.presence(t)]);
});
console.log(JSON.stringify(out));
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    js = json.loads(result.stdout)
    T = m.transitions
    for case, rows in zip(cases, js):
        side, styles, values, a, b, window, cap, seed_text = case
        e = T.Envelope(side, styles, values, a, b, window, cap, T.fnv1a(seed_text), pa=0.6, pb=0.9)
        for t, (level, cutoff, presence) in zip(times, rows):
            assert level == pytest.approx(e.level(t), abs=1e-9), (case, t)
            assert (cutoff is None) == (e.cutoff(t) is None) and (cutoff is None or cutoff == pytest.approx(e.cutoff(t))), (case, t)
            assert (presence is None) == (e.presence(t) is None) and (presence is None or presence == pytest.approx(e.presence(t))), (case, t)


# --- API ---

@pytest.fixture
def api(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_sequences_playlists import build
    import test_sequences_playlists as runner_tests
    with addon_modules("sonorium.core.session_manager", "sonorium.core.state", "sonorium.core.playback_runner",
                       "sonorium.core.sequences", "sonorium.core.playlists", "sonorium.web.api_v2",
                       "sonorium.recording", "sonorium.core.transitions") as mods:
        runner_tests.m.__dict__.update(mods.__dict__)
        themes = {"storm": {
            "presets": {"a": {"name": "Drizzle", "tracks": {"Wind": {"muted": True}, "Rain": {"volume": 0.4},
                                                             "Drums": {"volume": 0.5}}},
                        "b": {"name": "Storm", "tracks": {"Wind": {"volume": 1.0}, "Rain": {"volume": 0.86},
                                                          "Drums": {"muted": True}}},
                        "c": {"name": "Clear", "tracks": {"Wind": {"volume": 0.3}}}},
            "sequences": {"s": {"name": "S", "presets": ["a", "b", "c"]}}}}
        t = build(tmp_path, themes)
        R = mods.recording
        theme = SimpleNamespace(groups={}, short_file_threshold=15.0, name="Storm")
        insts = [R.ThemeTrack(R.AudioFile(Path(f"{n}.mp3"), key=n), theme)
                 for n in ("Wind", "Rain", "Drums")]
        for inst in insts:
            inst.meta._duration_samples = 60 * RATE
        t.manager.get_theme = lambda theme_id: (SimpleNamespace(id=theme_id, name="Storm", instances=insts, groups={})
                                                if theme_id == "storm" else None)
        app = FastAPI()
        app.include_router(mods.api_v2.create_api_router(
            session_manager=t.manager, group_manager=MagicMock(), ha_registry=MagicMock(), state_store=t.store))
        t.client = TestClient(app)
        yield t


def test_step_transitions_api(api):
    c = api.client
    url = "/api/themes/storm/sequences/s/transitions/b"
    assert c.get(url).json()["in"] == {} and c.get(url).json()["out"] == {}  # missing = Smooth
    body = {"in": {"Wind": {"styles": ["gusts", "dist"], "values": {"gusts": 80, "dist": 45},
                            "max_volume": 0.85, "pre_fade": 45}},
            "out": {"Wind": {"styles": ["swell"], "values": {"swell": 40}}}}
    r = c.put(url, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["in"]["Wind"]["max_volume"] == 0.85
    saved = json.loads((api.folders["storm"] / "presets.json").read_text(encoding="utf-8"))
    assert saved["sequences"]["s"]["transitions"]["b"]["in"]["Wind"]["pre_fade"] == 45
    assert c.get("/api/themes/storm/sequences/s").json()["transitions"]["b"]["out"]["Wind"]["styles"] == ["swell"]
    assert c.put(url, json={"in": {"Wind": {"styles": ["warp"]}}}).status_code == 400
    assert c.put(url, json={"in": {"Wind": {"styles": ["gusts"], "pre_fade": 200}}}).status_code == 400
    assert c.put("/api/themes/storm/sequences/s/transitions/zzz", json={}).status_code == 404
    assert c.put("/api/themes/storm/sequences/nope/transitions/b", json={}).status_code == 404
    # The sequence's own PUT carries them too; presets taken out drop theirs
    r = c.put("/api/themes/storm/sequences/s", json={"presets": ["a", "c"]})
    assert r.status_code == 200 and r.json()["transitions"] == {}
    r = c.put("/api/themes/storm/sequences/s", json={"transitions": {"c": {"in": {"Rain": {"styles": ["fresh"]}}}}})
    assert r.status_code == 200 and r.json()["transitions"]["c"]["in"]["Rain"]["styles"] == ["fresh"]
    assert c.put("/api/themes/storm/sequences/s", json={"transitions": {"b": {}}}).status_code == 400
    # Empty: back to Smooth
    assert c.put("/api/themes/storm/sequences/s/transitions/c", json={}).json()["in"] == {}


def test_transition_tracks_lists_what_a_step_changes(api):
    r = api.client.get("/api/themes/storm/transition_tracks", params={"preset": "b", "prev": "a", "next": "c"})
    assert r.status_code == 200, r.text
    body = r.json()
    coming = {row["key"]: row for row in body["in"]}
    leaving = {row["key"]: row for row in body["out"]}
    assert set(coming) == {"Wind", "Rain"}  # on, and louder
    assert coming["Wind"]["from"] == 0 and coming["Wind"]["to"] == 1.0
    assert coming["Rain"]["from"] == 0.4 and coming["Rain"]["to"] == 0.86
    assert coming["Wind"]["mode"] == "Background"
    assert set(leaving) == {"Wind"}  # c turns Wind down; Rain (the theme's 100%) and Drums get louder
    assert leaving["Wind"]["from"] == 1.0 and leaving["Wind"]["to"] == 0.3
    r = api.client.get("/api/themes/storm/transition_tracks", params={"preset": "a"})  # sequence start, no next
    assert r.json()["in"] == [] and r.json()["out"] == []
    assert api.client.get("/api/themes/storm/transition_tracks", params={"preset": "zz"}).status_code == 404
