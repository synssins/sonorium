"""
Sounds over a channel's mix (core/overlay.py) and volume ramps (core/ramps.py),
measured from the samples: the duck's curve, the maximum length, priorities
and policies, and ramps that rise smoothly instead of in steps.
"""
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("av")

from playback_helpers import CHUNK, RATE, addon_modules  # noqa: E402

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.overlay", "sonorium.core.ramps", "sonorium.core.decode",
                       "sonorium.core.channel") as mods:
        m.__dict__.update(mods.__dict__)
        yield


BED = 8000.0  # the channel's own mix: a constant level, easy to measure


def bed():
    return np.full((1, CHUNK), BED, dtype=np.float32).astype(np.int16)


def tone(seconds, amp=4000.0, freq=1000.0):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def run(overlay, seconds):
    """The mixed output for `seconds` of a constant bed."""
    return np.concatenate([overlay.mix(bed()).reshape(-1) for _ in range(int(seconds * RATE / CHUNK))]).astype(np.float64)


def bed_level(out, window=441):
    """The bed's level in each 10 ms window (the tone averages out over whole cycles)."""
    n = len(out) // window
    return out[:n * window].reshape(n, window).mean(axis=1)


def events_of(log, kind):
    return [(s.sound_id, r) for k, s, r in log if k == kind]


def make(log=None):
    return m.overlay.ChannelOverlay(RATE, on_event=(lambda k, s, r: log.append((k, s, r))) if log is not None else None)


def sound(sid, seconds=1.0, **kw):
    return m.overlay.Sound(sound_id=sid, pcm=tone(seconds), **kw)


# --- Ducking ---

def test_duck_goes_down_over_attack_holds_then_comes_back_over_release():
    log = []
    overlay = make(log)
    duck = m.overlay.Duck(db=12.0, attack=0.5, release=1.0)
    overlay.add(sound("a", 1.0, duck=duck))
    out = run(overlay, 3.5)
    level = bed_level(out) / BED  # 10 ms windows
    down = 10 ** (-12 / 20)  # 0.251
    assert level[0] > 0.9                         # starts at full level
    assert abs(level[25] - 10 ** (-6 / 20)) < 0.05  # half way (in dB) after 0.25 s
    assert abs(level[60] - down) < 0.01           # down after the attack
    assert np.all(np.diff(level[:50]) <= 1e-3)    # only down during the attack
    # The sound started once the duck was reached and lasted 1 s
    assert events_of(log, "started") == [("a", "")]
    assert events_of(log, "ended") == [("a", "finished")]
    assert abs(level[140] - down) < 0.01          # still down while it plays (0.5 + 1.0 s)
    assert abs(level[200] - 10 ** (-6 / 20)) < 0.06  # half way back (0.5 s into the release)
    assert level[-5:].min() > 0.99                # back to full level
    assert not overlay.busy


def test_sound_starts_after_the_attack():
    overlay = make()
    overlay.add(sound("a", 0.5, duck=m.overlay.Duck(db=20.0, attack=0.4, release=0.1)))
    out = run(overlay, 1.5)
    ac = np.abs(np.diff(out))                     # the tone shows as fast changes; the bed moves slowly
    first = np.argmax(ac > 50) / RATE
    assert 0.38 < first < 0.42


def test_no_duck_plays_at_once_over_the_mix():
    overlay = make()
    overlay.add(sound("a", 0.5))
    out = run(overlay, 1.0)
    assert abs(bed_level(out)[2] / BED - 1.0) < 0.01  # the bed isn't touched
    assert np.abs(np.diff(out[:2000])).max() > 50     # the tone is there at once


# --- Maximum length ---

def test_cap_cuts_with_a_fade():
    pcm, capped = m.overlay.cap(tone(3.0), int(1.0 * RATE), RATE)
    assert capped and pcm.shape[0] == RATE
    assert abs(pcm[-1]) < 1.0                     # faded to silence at the cut
    pcm2, capped2 = m.overlay.cap(tone(0.5), int(1.0 * RATE), RATE)
    assert not capped2 and pcm2.shape[0] == int(0.5 * RATE)


# --- Policies and priorities ---

def test_queue_plays_one_after_another_by_priority():
    log = []
    overlay = make(log)
    assert overlay.add(sound("a", 0.3)) == "waiting"
    assert overlay.add(sound("low", 0.3, priority=0)) == "queued"
    assert overlay.add(sound("high", 0.3, priority=5)) == "queued"
    run(overlay, 1.5)
    assert [sid for sid, _ in events_of(log, "started")] == ["a", "high", "low"]


def test_now_mixes_over_what_plays():
    log = []
    overlay = make(log)
    overlay.add(sound("a", 1.0))
    assert overlay.add(sound("fx", 0.2, policy="now")) == "waiting"
    run(overlay, 0.1)
    assert [sid for sid, _ in events_of(log, "started")] == ["a", "fx"]


def test_replace_cuts_same_or_lower_priority_but_waits_for_higher():
    log = []
    overlay = make(log)
    overlay.add(sound("a", 2.0, priority=1))
    run(overlay, 0.1)
    overlay.add(sound("b", 0.3, priority=1, policy="replace"))
    run(overlay, 0.1)
    assert ("a", "replaced") in events_of(log, "ended")
    assert "b" in [sid for sid, _ in events_of(log, "started")]

    log.clear()
    overlay.add(sound("vip", 1.0, priority=9))
    run(overlay, 0.5)  # b ends, vip plays
    assert overlay.add(sound("c", 0.2, priority=2, policy="replace")) == "queued"
    run(overlay, 0.2)
    assert ("vip", "replaced") not in events_of(log, "ended")


def test_cancel_and_clear():
    log = []
    overlay = make(log)
    overlay.add(sound("a", 2.0))
    overlay.add(sound("b", 1.0))
    run(overlay, 0.1)
    assert overlay.cancel("b")
    assert ("b", "cancelled") in events_of(log, "ended")
    overlay.clear("stopped")
    assert ("a", "stopped") in events_of(log, "ended")
    assert not overlay.busy


def test_bad_policy_and_duck_refused():
    overlay = make()
    with pytest.raises(m.overlay.OverlayError):
        overlay.add(sound("x", policy="sometimes"))
    with pytest.raises(m.overlay.OverlayError):
        m.overlay.Duck.check({"db": -3}, m.overlay.Duck())
    d = m.overlay.Duck.check({"db": 6}, m.overlay.Duck(15, 0.5, 1.5))
    assert (d.db, d.attack, d.release) == (6, 0.5, 1.5)
    assert m.overlay.Duck.check(False, m.overlay.Duck()) is None


# --- Decoding ---

def test_buffer_is_resampled_to_the_stream_rate():
    pcm = m.decode.from_samples(np.sin(np.linspace(0, 200 * np.pi, 22050)), 22050, RATE)
    assert abs(pcm.shape[0] - RATE) < 200
    assert 30000 < np.abs(pcm).max() <= 32767


def test_decode_stops_at_the_maximum(tmp_path):
    from playback_helpers import tone_mp3
    tone_mp3(tmp_path / "long.mp3", 5.0, 440.0)
    pcm = m.decode.decode(tmp_path / "long.mp3", RATE, max_seconds=1.0)
    assert pcm.shape[0] == RATE + 1  # one over: the caller knows it was longer
    with pytest.raises(m.decode.DecodeError):
        m.decode.decode(tmp_path / "missing.mp3", RATE)


# --- Ramps ---

def ramp_run(ramp, seconds):
    return np.concatenate([ramp.apply(bed()).reshape(-1) for _ in range(int(seconds * RATE / CHUNK))]).astype(np.float64)


@pytest.mark.parametrize("curve", ["linear", "db", "ease", "exp"])
def test_ramp_rises_smoothly(curve):
    done = []
    ramp = m.ramps.ChannelRamp(RATE, on_done=lambda rid, why: done.append((rid, why)))
    ramp.start("r1", 0.0, 1.0, 2.0, curve)
    out = ramp_run(ramp, 3.0) / BED
    assert out[0] < 0.01
    steps = np.diff(out[:2 * RATE])
    assert steps.min() >= -1e-6                  # only up
    assert steps.max() < 0.001                   # no step bigger than 0.1% between samples
    assert out[-1000:].min() > 0.999
    assert done == [("r1", "finished")]
    assert ramp.idle                             # at 1.0 and finished: nothing more to do


def test_db_ramp_is_even_in_decibels():
    ramp = m.ramps.ChannelRamp(RATE)
    ramp.start("r", 0.01, 1.0, 1.0, "db")        # -40 dB to 0 dB
    out = ramp_run(ramp, 1.0) / BED
    mid = 20 * np.log10(out[RATE // 2])
    assert abs(mid - (-20.0)) < 0.5


def test_ramp_down_holds_and_cancel_resets():
    ramp = m.ramps.ChannelRamp(RATE)
    ramp.start("r", 1.0, 0.0, 0.5, "linear")
    out = ramp_run(ramp, 1.0) / BED
    assert out[-1] == 0.0 and not ramp.idle      # holds silence
    ramp.start("r2", None, 0.5, 10.0, "linear")
    ramp_run(ramp, 1.0)
    level = ramp.cancel()
    assert 0.0 < level < 0.1
    held = ramp_run(ramp, 0.2) / BED
    assert abs(held[-1] - level) < 1e-3          # cancel holds where it got to
    ramp.cancel(reset=True)
    ramp_run(ramp, 0.2)
    assert ramp.idle


def test_bad_ramps_refused():
    with pytest.raises(m.ramps.RampError):
        m.ramps.check(0, 5, 10, "db")
    with pytest.raises(m.ramps.RampError):
        m.ramps.check(0, 1, -1, "db")
    with pytest.raises(m.ramps.RampError):
        m.ramps.check(0, 1, 1, "wobbly")


# --- In the channel ---

def test_channel_applies_ramp_then_overlay_and_stop_clears_them():
    channel = m.channel.Channel(id=1)
    channel._ensure_generator_running = lambda: None
    channel.run_silent()
    channel.ramp = m.ramps.ChannelRamp(RATE)
    log = []
    channel.overlay = make(log)
    channel.ramp.start("r", 0.0, 0.0, 0.0)       # the mix silent (it is anyway)
    channel.overlay.add(sound("a", 0.5))
    out = np.concatenate([channel.next_chunk().reshape(-1) for _ in range(10)])
    assert np.abs(out).max() > 3000              # the sound is heard over the ramped-down mix
    channel.stop()
    assert ("a", "stopped") in events_of(log, "ended")
    assert channel.ramp.idle
