"""
Advanced transitions: how each track comes in or leaves at a sequence step
(docs/PLAYBACK.md "Advanced transitions", docs/THEME_FORMAT.md section 5).

Stored in a sequence (presets.json sequences[<id>]["transitions"]):

    "transitions": {
      "<preset_id>": {
        "in":  {"<track key>": {"styles": ["gusts", "dist"],
                                "values": {"gusts": 80, "dist": 45},
                                "max_volume": 0.85, "pre_fade": 45}},
        "out": {"<track key>": {...}}
      }
    }

"in" of a preset: the tracks its step turns on or makes louder. "out": the
tracks the NEXT step turns off or makes quieter (how they leave this preset).
A track with no entry follows the normal ramp (Smooth).

Styles (one name per side, the stored key is the same):

| key   | coming in   | leaving          | value (0-100 unless noted)          |
|-------|-------------|------------------|-------------------------------------|
| gusts | Gusts       | Gusts dying down | Intensity                            |
| swell | Swell       | Smooth fade      | Speed: seconds of the fade window   |
| build | Build       | Thin out         | Amount (Intermittent tracks)        |
| dist  | Distance    | Recede           | How far                              |
| fresh | Start fresh | Finish then stop | none                                 |

The envelope math here (level(), cutoff(), presence(), the gust pattern) is
mirrored line for line by the web UI's preview (app.js, "transition
envelope"), so a preview sounds like the speakers; tests compare the two.
"""

from __future__ import annotations

import math
from typing import Optional

STYLES = ("gusts", "swell", "build", "dist", "fresh")
VALUE_STYLES = ("gusts", "swell", "build", "dist")
SIDES = ("in", "out")
DEFAULT_VALUES = {"gusts": 50, "swell": 20, "build": 60, "dist": 50}
MAX_PRE_FADE = 90.0
MAX_SWELL = 7 * 24 * 3600.0
MIN_WINDOW = 0.05  # seconds: a step with no ramp and no pre-fade still ramps a little
FRESH_FADE = 1.0  # Start fresh alone: its short fade (seconds)
FINISH_FADE = 0.5  # Finish then stop: the fade once the play has finished
FINISH_MAX = 600.0  # ...waiting at most this long for it
EDGE = 0.1  # share of the window for the short in/out edges (Distance alone, Thin out to off)
EPS = 0.002  # volumes closer than this are the same


class TransitionError(ValueError):
    """Transitions that can't be saved: the message says why."""


# ---------------------------------------------------------------- storage

def _num(value, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise TransitionError(f"{name} must be a number")
    if value < low or value > high:
        raise TransitionError(f"{name} must be between {low:g} and {high:g}")
    return float(value)


def clean_track(data, where: str = "track") -> Optional[dict]:
    """One track's transition, checked. None when it is Smooth (nothing on, no pre-fade)."""
    if not isinstance(data, dict):
        raise TransitionError(f"{where} must be an object")
    unknown = set(data) - {"styles", "values", "max_volume", "pre_fade"}
    if unknown:
        raise TransitionError(f"{where}: unknown field(s) {', '.join(sorted(unknown))}")
    styles = data.get("styles", [])
    if not isinstance(styles, list) or any(s not in STYLES for s in styles):
        raise TransitionError(f"{where}: styles must be a list of {', '.join(STYLES)}")
    styles = [s for s in STYLES if s in styles]  # known order, no repeats
    values = data.get("values") or {}
    if not isinstance(values, dict):
        raise TransitionError(f"{where}: values must be an object")
    clean_values = {}
    for key, value in values.items():
        if key not in VALUE_STYLES:
            raise TransitionError(f"{where}: unknown value '{key}'")
        if key == "swell":
            clean_values[key] = _num(value, f"{where}: swell", 0.0, MAX_SWELL)
        else:
            clean_values[key] = _num(value, f"{where}: {key}", 0.0, 100.0)
    max_volume = data.get("max_volume")
    if max_volume is not None:
        max_volume = _num(max_volume, f"{where}: max_volume", 0.0, 1.0)
    pre_fade = _num(data.get("pre_fade", 0) or 0, f"{where}: pre_fade", 0.0, MAX_PRE_FADE)
    if not styles and pre_fade <= 0:
        return None
    result = {"styles": styles, "values": clean_values, "pre_fade": pre_fade}
    if max_volume is not None:
        result["max_volume"] = max_volume
    return result


def clean_step(data, where: str = "transitions") -> dict:
    """A preset's transitions ({"in": {...}, "out": {...}}), checked; Smooth tracks are left out."""
    if not isinstance(data, dict):
        raise TransitionError(f"{where} must be an object")
    unknown = set(data) - set(SIDES)
    if unknown:
        raise TransitionError(f"{where}: unknown side(s) {', '.join(sorted(unknown))} (use in, out)")
    result = {}
    for side in SIDES:
        tracks = data.get(side) or {}
        if not isinstance(tracks, dict):
            raise TransitionError(f"{where}.{side} must be an object of tracks")
        cleaned = {}
        for key, track in tracks.items():
            if not isinstance(key, str) or not key:
                raise TransitionError(f"{where}.{side}: track keys must be names")
            value = clean_track(track, f"{side} '{key}'")
            if value is not None:
                cleaned[key] = value
        if cleaned:
            result[side] = cleaned
    return result


def clean_transitions(data, preset_ids) -> dict:
    """A sequence's whole transitions map, checked: every key must be one of the sequence's presets."""
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TransitionError("transitions must be an object")
    known = set(preset_ids)
    result = {}
    for preset_id, step in data.items():
        if preset_id not in known:
            raise TransitionError(f"transitions: '{preset_id}' is not a preset of this sequence")
        cleaned = clean_step(step, f"transitions '{preset_id}'")
        if cleaned:
            result[preset_id] = cleaned
    return result


def keep_presets(transitions, preset_ids) -> dict:
    """The transitions of the presets still in the sequence."""
    known = set(preset_ids)
    return {p: s for p, s in (transitions or {}).items() if p in known and isinstance(s, dict)}


# ---------------------------------------------------------------- gust pattern

def fnv1a(text: str) -> int:
    """32-bit FNV-1a of the UTF-8 text (the preview computes the same)."""
    h = 0x811C9DC5
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def mulberry32(seed: int):
    """A small deterministic random generator (floats in [0, 1)), the same as the preview's."""
    a = seed & 0xFFFFFFFF
    mask = 0xFFFFFFFF
    while True:
        a = (a + 0x6D2B79F5) & mask
        t = ((a ^ (a >> 15)) * (a | 1)) & mask
        t = ((t + (((t ^ (t >> 7)) * (t | 61)) & mask)) & mask) ^ t
        yield ((t ^ (t >> 14)) & mask) / 4294967296.0


def gust_seed(side: str, preset_id: str, track_key: str) -> int:
    """The gust pattern's seed: the step's preset, the side and the track."""
    return fnv1a(f"{side}|{preset_id}|{track_key}")


def gust_rate(intensity: float) -> float:
    """Gust knots per second: rougher gusts swing faster (0.15-0.6 per second)."""
    return 0.15 + 0.45 * intensity


def gust_knots(seed: int, window: float, rate: float) -> list:
    count = int(math.ceil(max(window, MIN_WINDOW) * rate)) + 2
    rng = mulberry32(seed)
    return [2.0 * next(rng) - 1.0 for _ in range(count)]


def gust_noise(knots: list, rate: float, t: float) -> float:
    """Smooth random value in [-1, 1] at t seconds: cosine steps between the knots."""
    x = max(0.0, t) * rate
    i = int(math.floor(x))
    f = x - i
    last = len(knots) - 1
    k0, k1 = knots[min(i, last)], knots[min(i + 1, last)]
    return k0 + (k1 - k0) * (1.0 - math.cos(math.pi * f)) / 2.0


def swell_curve(x: float) -> float:
    """S-curve 0..1 (raised cosine), flat at both ends."""
    x = min(1.0, max(0.0, x))
    return 0.5 - 0.5 * math.cos(math.pi * x)


# ---------------------------------------------------------------- envelope

class Envelope:
    """
    One track's level over its fade window (t = seconds since the change
    started; the window is the pre-fade plus the ramp).

    `a`, `b`: the track's volume before and after (0 = off). Coming in
    (side "in") b is louder; leaving ("out") b is quieter. `pa`, `pb`: its
    interval (presence) before and after, for Build / Thin out.
    """

    def __init__(self, side: str, styles, values: dict, a: float, b: float, window: float,
                 max_volume: Optional[float] = None, seed: int = 0, pa: float = 1.0, pb: float = 1.0):
        self.side = side
        self.styles = set(styles or ())
        v = dict(DEFAULT_VALUES)
        v.update(values or {})
        self.values = v
        self.a, self.b = max(0.0, float(a)), max(0.0, float(b))
        self.window = max(MIN_WINDOW, float(window))
        self.pa, self.pb = float(pa), float(pb)
        w = self.window
        self.swell = min(max(float(v["swell"]), min(5.0, w)), w)
        amount = float(v["build"]) / 100.0 if "build" in self.styles else 0.0
        self.amount = amount
        self.start, self.end = self.a, self.b
        if side == "in" and amount:
            self.start = max(self.a, self.b * (1.0 - 0.7 * amount))
        if side == "out" and amount:
            self.end = max(self.b, self.a * (1.0 - 0.7 * amount))
        # Thin out to off: down to its lower level, then a short fade at the end
        self.tail = min(2.0, EDGE * w) if (side == "out" and amount and self.b <= EPS) else 0.0
        self.gusts = float(v["gusts"]) / 100.0 if "gusts" in self.styles else 0.0
        self.rate = gust_rate(self.gusts)
        self.knots = gust_knots(seed, w, self.rate) if self.gusts else [0.0]
        top = max(self.a, self.b)
        self.cap = min(top, float(max_volume)) if max_volume is not None else top
        self.far = float(v["dist"]) / 100.0 if "dist" in self.styles else None
        self.fresh = "fresh" in self.styles
        # Finish then stop alone: the level holds until the play has finished
        self.holds = side == "out" and self.styles == {"fresh"}

    # The shape of the move from start to end (0..1) at t
    def _shape(self, t: float) -> float:
        w = self.window
        styles = self.styles
        if "swell" in styles:
            return swell_curve(t / self.swell)
        if self.far is not None and not (styles & {"gusts", "build"}):
            if self.side == "in" and self.start <= EPS:
                return min(1.0, t / (EDGE * w))  # in quickly: Distance makes it faint
            if self.side == "out" and self.end <= EPS:
                return min(1.0, max(0.0, (t - (1.0 - EDGE) * w) / (EDGE * w)))  # out at the very end
        if self.fresh and self.side == "in" and not (styles - {"fresh"}):
            return min(1.0, t / min(FRESH_FADE, w))
        if self.fresh and self.side == "out" and not (styles - {"fresh"}):
            return 0.0  # holds until the play has finished (the mixer ends it)
        span = w - self.tail
        return min(1.0, max(0.0, t / span)) if span > 0 else 1.0

    def distance(self, t: float) -> float:
        """How far away the track sounds (0 near and clear .. 1 as far as How far sets)."""
        if self.far is None:
            return 0.0
        u = min(1.0, max(0.0, t / self.window))
        if self.side == "in":
            return 1.0 - u if self.a <= EPS else (1.0 - u) * min(1.0, u / 0.15)
        return u if self.b <= EPS else u * min(1.0, (1.0 - u) / 0.15)

    def cutoff(self, t: float) -> Optional[float]:
        """The low-pass cutoff (Hz) at t, or None when it plays clear."""
        d = self.distance(t)
        if d < 0.002:
            return None
        far = 250.0 * 8.0 ** (1.0 - self.far)  # 250 Hz (far) .. 2 kHz (near)
        return 18000.0 * (far / 18000.0) ** d

    def level(self, t: float) -> float:
        """The track's volume (0..1 like its slider) at t seconds into the window."""
        w = self.window
        t = max(0.0, t)
        if self.holds:
            return self.start
        base = self.start + (self.end - self.start) * self._shape(t)
        if self.tail and t > w - self.tail:
            base *= max(0.0, (w - t) / self.tail)
        level = base
        if self.far is not None:
            level *= 10.0 ** (-(6.0 + 24.0 * self.far) * self.distance(t) / 20.0)
        if self.gusts:
            u = min(1.0, t / w)
            swing = 1.0 + 0.9 * self.gusts * math.sin(math.pi * u) * gust_noise(self.knots, self.rate, t)
            gusty = level * swing
            level = min(gusty, max(self.cap, level))
        if t >= w:
            return self.end if not self.tail else self.b
        return max(0.0, level)

    def presence(self, t: float) -> Optional[float]:
        """The interval (presence) at t for Build / Thin out, else None (it follows the preset)."""
        if not self.amount:
            return None
        u = min(1.0, max(0.0, t / self.window))
        if self.side == "in":
            low = max(0.01, self.pb * (1.0 - self.amount))
            return low + (self.pb - low) * u
        low = max(0.01, self.pa * (1.0 - self.amount))
        return self.pa + (low - self.pa) * u


# ---------------------------------------------------------------- low-pass

class OnePoleLowpass:
    """
    Two one-pole low-pass filters in a row (12 dB per octave), run on whole
    chunks with numpy and carrying their state from chunk to chunk. The
    cutoff is fixed for a chunk (it moves slowly). No scipy: the recurrence
    y[n] = b*y[n-1] + a*x[n] is solved in blocks with a cumulative sum.
    """

    def __init__(self, sample_rate: int):
        self.rate = sample_rate
        self.state = [0.0, 0.0]
        self.primed = False

    @staticmethod
    def _pole(x, alpha: float, state: float):
        import numpy as np
        beta = 1.0 - alpha
        if beta <= 1e-9:
            return x.copy(), float(x[-1])
        n = len(x)
        # Keep beta**-k within float range: blocks short enough that it stays below e^600
        size = max(1, min(n, int(600.0 / -math.log(beta))))
        k = np.arange(size, dtype=np.float64)
        up = beta ** -k
        down = beta ** k
        out = np.empty(n, dtype=np.float64)
        for start in range(0, n, size):
            seg = x[start:start + size]
            m = len(seg)
            acc = np.cumsum(seg * up[:m]) * alpha
            y = down[:m] * (beta * state + acc)
            out[start:start + m] = y
            state = float(y[-1])
        return out, state

    def process(self, data, cutoff: float):
        """Filter a chunk (any shape, int16 or float) at `cutoff` Hz; returns float64 of the same shape."""
        import numpy as np
        x = np.asarray(data, dtype=np.float64).reshape(-1)
        if not self.primed:
            self.state = [float(x[0]), float(x[0])]
            self.primed = True
        alpha = 1.0 - math.exp(-2.0 * math.pi * min(cutoff, self.rate * 0.45) / self.rate)
        y, self.state[0] = self._pole(x, alpha, self.state[0])
        y, self.state[1] = self._pole(y, alpha, self.state[1])
        return y.reshape(np.shape(data))


# ---------------------------------------------------------------- planning

def style_names(side: str, styles) -> str:
    """The styles as the UI names them, e.g. "Gusts + Distance"."""
    names = ({"gusts": "Gusts", "swell": "Swell", "build": "Build", "dist": "Distance", "fresh": "Start fresh"}
             if side == "in" else
             {"gusts": "Gusts dying down", "swell": "Smooth fade", "build": "Thin out", "dist": "Recede",
              "fresh": "Finish then stop"})
    return " + ".join(names[s] for s in STYLES if s in (styles or ())) or "Smooth"


def track_level(view) -> float:
    """A track's volume as a preset leaves it (0 when it is off)."""
    try:
        return float(view.volume) if view.is_enabled else 0.0
    except Exception:
        return 0.0


def step_plans(instances, before: dict, after: dict, step_in: dict, step_out: dict,
               in_preset: str, out_preset: str, view_factory, skip=None) -> list:
    """
    The tracks with an advanced transition at one step (preset A -> B), as
    plans for the mixer (without timing): side, track key, styles, values,
    max volume, pre-fade, seed, and B's values for the track (volume when on,
    0 off; presence). A track counts as coming in when B makes it louder and
    as leaving when B makes it quieter; a saved style for a track that does
    neither at this step is not used.
    """
    plans = []
    for instance in instances:
        key = instance.name
        if skip and skip(instance):
            continue
        cfg_in = (step_in or {}).get(key)
        cfg_out = (step_out or {}).get(key)
        if not cfg_in and not cfg_out:
            continue
        va, vb = view_factory(instance, before), view_factory(instance, after)
        la, lb = track_level(va), track_level(vb)
        side, cfg, preset_id = None, None, None
        if cfg_in and lb > la + EPS:
            side, cfg, preset_id = "in", cfg_in, in_preset
        elif cfg_out and lb < la - EPS:
            side, cfg, preset_id = "out", cfg_out, out_preset
        if side is None:
            continue
        try:
            pa, pb = float(va.presence), float(vb.presence)
        except Exception:
            pa = pb = 1.0
        plans.append({
            "track": key, "side": side,
            "styles": list(cfg.get("styles") or []), "values": dict(cfg.get("values") or {}),
            "max_volume": cfg.get("max_volume"), "pre_fade": float(cfg.get("pre_fade") or 0.0),
            "seed": gust_seed(side, preset_id, key),
            "target_volume": lb, "target_presence": pb, "from_presence": pa,
        })
    return plans


# ---------------------------------------------------------------- the Advanced dialog's track lists

def step_tracks(tracks, groups: Optional[dict], layer, preset: str, prev: Optional[str] = None,
                next: Optional[str] = None) -> dict:
    """
    The tracks a sequence step changes, for the Advanced dialog (the route
    GET /api/themes/{id}/transition_tracks and theme_edit.transition_tracks):
    {"in": the tracks `preset` turns on or makes louder after `prev`, "out":
    the tracks `next` turns off or makes quieter after `preset`}. Each row:
    key, name, group, mode ("Intermittent", "Ebb & Flow" or "Background"),
    from/to (volume 0..1, 0 = off), presence_from/presence_to and
    duration_seconds. Merry-go-round tracks are left out (their group's bed
    plays them). `tracks`: the theme's recording.ThemeTrack list; `groups`:
    its group settings; layer(preset_id): that preset's overrides (as a
    channel playing it has them).
    """
    from sonorium.recording import GROUP_MODE_MERRY_GO_ROUND, PlaybackMode, TrackView, group_mode
    groups = groups or {}
    labels = {PlaybackMode.SPARSE: "Intermittent", PlaybackMode.PRESENCE: "Ebb & Flow",
              PlaybackMode.CONTINUOUS: "Background"}

    def rows(before_id, after_id, side):
        if not before_id or not after_id:
            return []
        before = layer(before_id)
        after = layer(after_id)
        result = []
        for instance in list(tracks):
            group = getattr(instance.meta, "group", None)
            if group and group_mode(groups.get(group)) == GROUP_MODE_MERRY_GO_ROUND:
                continue
            va, vb = TrackView(instance, before), TrackView(instance, after)
            la, lb = track_level(va), track_level(vb)
            if (side == "in" and lb > la + EPS) or (side == "out" and lb < la - EPS):
                view = vb if side == "in" else va
                try:
                    mode = labels.get(view._resolve_playback_mode(), "Background")
                except Exception:
                    mode = "Background"
                try:
                    duration = round(float(instance.meta.duration_seconds), 1)
                except Exception:
                    duration = 0.0
                result.append({
                    "key": instance.name,
                    "name": instance.name.split("/", 1)[-1],
                    "group": group,
                    "mode": mode,
                    "from": round(la, 4), "to": round(lb, 4),
                    "presence_from": round(float(va.presence), 4), "presence_to": round(float(vb.presence), 4),
                    "duration_seconds": duration,
                })
        return result

    return {"in": rows(prev, preset, "in"), "out": rows(preset, next, "out")}
