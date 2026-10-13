"""
The cue timeline, mixer side: every player announces the sounds it starts
through its mix's CueRelay. An Intermittent play is decided and announced
Look-ahead early (Settings > Advanced) and confirmed when it starts on that
very block; a group's play, a loop's pass, an Ebb & Flow pass and a
Merry-go-round file are announced as they start; a play that won't happen is
called off. Rendered offline with the engine (tests/engine_theme.py).
"""
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "sonorium_addon", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from engine_theme import FAST_VALUES, RATE, make_theme  # noqa: E402


class Recorder:
    """A relay target that notes each call with the engine's sample clock (the start of the block being made)."""

    def __init__(self, engine):
        self.engine = engine
        self.calls = []
        self.count = 0

    def announce(self, stream, start, track, duration, lead):
        self.count += 1
        self.calls.append({"call": "announce", "token": self.count, "start": start, "track": track,
                           "duration": duration, "lead": lead, "at": self.engine.clock.samples})
        return self.count

    def confirm(self, token):
        self.calls.append({"call": "confirm", "token": token, "at": self.engine.clock.samples})

    def cancel(self, token, reason):
        self.calls.append({"call": "cancel", "token": token, "reason": reason, "at": self.engine.clock.samples})

    def of(self, call, **match):
        return [c for c in self.calls if c["call"] == call and all(c.get(k) == v for k, v in match.items())]


def _engine(folder, values, seed=4, **kwargs):
    from sonorium.engine import Engine, PcmFileDecoder
    return Engine(folder, playback_values=values, seed=seed, decoder=PcmFileDecoder(), **kwargs)


def _render(engine, seconds):
    """Render block by block; returns {track: [sample where each play started]} for Intermittent tracks."""
    from sonorium.recording import BLOCK_SAMPLES
    starts, last = {}, {}
    for _ in range(int(seconds * RATE / BLOCK_SAMPLES)):
        before = engine.clock.samples
        engine.render(BLOCK_SAMPLES)
        for stream in engine.mix.sources:
            name = stream.instance.name
            key = (getattr(stream, "phase", None), getattr(stream, "phase_end", None))
            if key[0] == "play" and last.get(name) != key:
                starts.setdefault(name, []).append(before)
            last[name] = key
    return starts


def test_an_intermittent_play_is_announced_look_ahead_early_and_starts_on_time(tmp_path):
    from sonorium.recording import BLOCK_SAMPLES
    engine = _engine(make_theme(tmp_path), FAST_VALUES)
    rec = Recorder(engine)
    engine.mix.cue_relay.target = rec
    starts = _render(engine, 40.0)
    end = engine.clock.samples
    early = [c for c in rec.of("announce", track="Chirp") if c["lead"] > 0 and c["at"] + c["lead"] < end]
    assert len(early) >= 3
    for cue in early:
        assert cue["start"] == "intermittent"
        assert 0 < cue["lead"] <= 0.5 * RATE  # the Look-ahead by default
        assert cue["duration"] == int(0.6 * RATE)
        planned = cue["at"] + cue["lead"]
        assert planned in starts["Chirp"]  # decided early, played exactly then
        confirm = rec.of("confirm", token=cue["token"])
        assert len(confirm) == 1 and confirm[0]["at"] == planned
    # every play of the track was announced (early, or as it started)
    announced_at = {c["at"] + c["lead"] for c in rec.of("announce", track="Chirp")}
    assert set(starts["Chirp"]) <= announced_at
    assert not rec.of("cancel")
    assert BLOCK_SAMPLES == 1024


def test_group_plays_loops_and_ebb_passes_are_announced_as_they_start(tmp_path):
    engine = _engine(make_theme(tmp_path), FAST_VALUES)
    rec = Recorder(engine)
    engine.mix.cue_relay.target = rec
    _render(engine, 30.0)
    drone = rec.of("announce", track="Drone")
    assert len(drone) >= 4 and all(c["start"] == "loop" and c["lead"] == 0 for c in drone)  # 6 s file, 30 s
    assert drone[0]["at"] == 0 and drone[0]["duration"] == 6 * RATE
    wind = rec.of("announce", track="Wind")
    assert wind and all(c["start"] == "ebb" for c in wind)


def test_a_groups_turn_is_announced_as_it_comes(tmp_path):
    from test_group_play_ends import make_storm
    engine = _engine(make_storm(tmp_path), {}, seed=5, preset_id="stormy")
    rec = Recorder(engine)
    engine.mix.cue_relay.target = rec
    starts = _render(engine, 120.0)
    thunder = [c for c in rec.calls if c["call"] == "announce" and str(c["track"]).startswith("Thunder/")]
    assert len(thunder) >= 3
    assert all(c["lead"] == 0 and c["start"] == "intermittent" for c in thunder)  # the turn is decided as it comes
    assert {c["at"] for c in thunder} == {s for k, v in starts.items() if k.startswith("Thunder/") for s in v}


def test_look_ahead_follows_settings_advanced(tmp_path):
    folder = make_theme(tmp_path)
    longer = _engine(folder, {**FAST_VALUES, "timeline_lookahead": 2.0})
    rec = Recorder(longer)
    longer.mix.cue_relay.target = rec
    _render(longer, 40.0)
    leads = [c["lead"] for c in rec.of("announce", track="Chirp")]
    assert max(leads) > 0.5 * RATE and max(leads) <= 2.0 * RATE

    none = _engine(folder, {**FAST_VALUES, "timeline_lookahead": 0.0})
    rec = Recorder(none)
    none.mix.cue_relay.target = rec
    _render(none, 30.0)
    assert rec.of("announce", track="Chirp") and all(c["lead"] == 0 for c in rec.of("announce", track="Chirp"))


def test_a_play_called_off_while_announced_is_cancelled(tmp_path):
    engine = _engine(make_theme(tmp_path), {**FAST_VALUES, "timeline_lookahead": 3.0})
    rec = Recorder(engine)
    engine.mix.cue_relay.target = rec
    from sonorium.recording import BLOCK_SAMPLES
    for _ in range(int(60 * RATE / BLOCK_SAMPLES)):
        engine.render(BLOCK_SAMPLES)
        pending = [c for c in rec.of("announce", track="Chirp") if c["lead"] > 0
                   and not rec.of("confirm", token=c["token"])]
        if pending:
            break
    assert pending, "no early announcement within a minute"
    # Interval to 0 (never) before it plays: the announced play is called off
    engine.mix.overrides.setdefault("Chirp", {})["presence"] = 0.0
    for _ in range(int(5 * RATE / BLOCK_SAMPLES)):
        engine.render(BLOCK_SAMPLES)
    cancelled = rec.of("cancel", token=pending[0]["token"])
    assert cancelled and cancelled[0]["reason"] == "skipped"
    assert not rec.of("confirm", token=pending[0]["token"])


def test_a_mix_nobody_follows_announces_nothing(tmp_path):
    engine = _engine(make_theme(tmp_path), FAST_VALUES)
    assert engine.mix.cue_relay.target is None  # the engine renders offline: nothing goes on the timeline
    _render(engine, 10.0)  # and plays as before


def test_a_server_channel_points_its_mix_at_the_timeline():
    from types import SimpleNamespace
    from sonorium.core.channel import Channel
    from sonorium.core.timeline import get_timeline
    from sonorium.recording import CueRelay
    mix = SimpleNamespace(label="", cue_relay=CueRelay())
    Channel(id=7)._label_stream(mix, SimpleNamespace(name="Storm", id="storm"))
    target = mix.cue_relay.target
    assert target.timeline is get_timeline() and target.channel == 7 and target.theme_id == "storm"
    assert target.announce(SimpleNamespace(instance=None), "loop", "Rain", 100, 0) is None  # nobody follows
