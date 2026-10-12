"""
The playback engine: one theme folder mixed into PCM, pulled by the caller.

It drives the server's own mixing classes (theme.ThemeMix for the tracks,
core.channel.Channel for preset glides and the start/stop fade) without the
server around them: no web server, no speakers, no MP3, no threads. Each
render() mixes as many blocks as asked for, so time in the mix is the count
of samples rendered, never the wall clock.

Determinism: every random draw comes from random.Random(seed) and every file
is read through the engine's decoder (both set for the calling thread only
while the engine works, see sonorium/rng.py and sonorium/decoders.py), and
the playback values (Settings > Advanced) are the engine's own. The same
theme files, settings, seed and calls give the same samples every time.
"""
from __future__ import annotations

import contextlib
import random
from typing import Optional

import numpy as np

from sonorium import decoders, playback_values, rng
from sonorium.core.channel import Channel
from sonorium.engine.theme_loader import DEFAULT_MASTER_GAIN, ThemeFolder, load_theme
from sonorium.recording import (BLOCK_SAMPLES, SAMPLE_RATE, close_stream, preset_group_overrides,
                                preset_track_overrides)

# A preset picked while playing glides in over this long (core/session_manager.PRESET_GLIDE_SECONDS)
PRESET_GLIDE_SECONDS = 3.0


class SampleClock:
    """The engine's time: samples rendered so far (and the same in seconds)."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, samples: int = 0):
        self.sample_rate = int(sample_rate)
        self.samples = int(samples)

    @property
    def seconds(self) -> float:
        return self.samples / self.sample_rate

    def advance(self, samples: int) -> None:
        self.samples += int(samples)

    def __call__(self) -> float:
        return self.seconds


class _PullChannel(Channel):
    """A Channel whose mix is pulled by the caller (next_chunk), with no generator thread."""

    def _ensure_generator_running(self):
        pass


class Engine:
    """
    One theme mixed to mono 16-bit PCM at 44100 Hz.

        eng = Engine("themes/Rain", preset_id="calm", seed=1234)
        block = eng.render(44100)     # one second, numpy int16, shape (44100,)
        eng.set_preset("storm")       # glides over 3 s, as on the server
        eng.close()

    theme_dir         a theme folder (audio files, group folders, metadata.json, presets.json)
    preset_id         a preset of the theme; None: the theme's default preset (if one is
                      marked), "": no preset (the theme's own settings)
    playback_values   Settings > Advanced values that differ from the defaults
                      ({"sparse_min_interval": 60, ...}, see sonorium/playback_values.py)
    seed              every random choice in the mix comes from this
    sample_rate       44100 only (the mixer's rate)
    decoder           how files are read (decoders.Decoder); PyAvDecoder() by default
    clock             a SampleClock to count rendered samples on (one is made otherwise)
    master_gain       Settings > Audio Settings > Master output gain, 0-100 %
    volume            output volume 0..1, applied after the mix (like a speaker's volume)
    fade_in           seconds to fade the whole mix in from silence at the start (0: none)
    step              a sequence step's While playing values laid over the preset
                      (core/sequences.merge_step), or None
    themes_root       where linked files' themes are (default: the theme folder's parent)
    """

    def __init__(self, theme_dir, preset_id: Optional[str] = None, playback_values: Optional[dict] = None,
                 seed: int = 0, sample_rate: int = SAMPLE_RATE, decoder: Optional[decoders.Decoder] = None,
                 clock: Optional[SampleClock] = None, master_gain: float = DEFAULT_MASTER_GAIN,
                 volume: float = 1.0, fade_in: float = 0.0, step: Optional[dict] = None, themes_root=None):
        if int(sample_rate) != SAMPLE_RATE:
            raise ValueError(f"The mixer runs at {SAMPLE_RATE} Hz only (asked for {sample_rate})")
        self.sample_rate = SAMPLE_RATE
        self.seed = seed
        self.decoder = decoder if decoder is not None else decoders.PyAvDecoder()
        self.playback_values = _module_values().validate(dict(playback_values or {}))
        self.clock = clock if clock is not None else SampleClock()
        self._rng = random.Random(seed)
        self._volume = self._target_volume = _unit(volume)
        self._held: list[np.ndarray] = []  # mixed samples not handed out yet
        self._held_count = 0
        self._closed = False
        with self._scope():
            self._folder: ThemeFolder = load_theme(theme_dir, master_gain, themes_root)
            self.preset_id = self._pick_preset(preset_id)
            self._step = step
            self._channel = _PullChannel(id=0, name="Engine")
            self._channel.set_theme(self._folder.theme, self._overrides(self.preset_id, step), fade_in=fade_in)

    # --- what it plays ---

    @property
    def theme_id(self) -> str:
        return self._folder.theme.id

    @property
    def theme_name(self) -> str:
        return self._folder.metadata.name or self._folder.folder.name

    @property
    def presets(self) -> dict:
        """The theme's presets: {preset_id: name}."""
        return {pid: (data or {}).get("name", pid) for pid, data in self._folder.presets.items()}

    @property
    def tracks(self) -> list[str]:
        """The theme's track keys ("Rain", "Lute/Song 1"), in mixing order."""
        return [track.name for track in self._folder.theme.tracks]

    @property
    def missing_links(self) -> list[str]:
        """Linked tracks whose source file wasn't found (left out of the mix)."""
        return list(self._folder.missing_links)

    @property
    def mix(self):
        """The running theme.ThemeMix (for inspection; changing it bypasses the engine)."""
        return self._channel._theme_stream

    def _pick_preset(self, requested: Optional[str]) -> Optional[str]:
        """As session_manager.preset_for_theme: the requested preset, "" for none, else the theme's default."""
        if requested == "":
            return None
        if requested:
            if requested not in self._folder.presets:
                raise KeyError(f"The theme has no preset '{requested}'")
            return requested
        return self._folder.default_preset()

    def _overrides(self, preset_id: Optional[str], step: Optional[dict]) -> dict:
        """The channel's preset layer (session_manager.preset_overrides)."""
        if not preset_id:
            return {}
        from sonorium.core.sequences import merge_step
        preset = merge_step(self._folder.presets.get(preset_id) or {}, step)
        return {**preset_track_overrides(preset.get("tracks", {})), **preset_group_overrides(preset.get("groups", {}))}

    # --- the scope every call runs in ---

    @contextlib.contextmanager
    def _scope(self):
        """This engine's random numbers, decoder and playback values, in this thread, for the call."""
        with rng.use_rng(self._rng), decoders.use_decoder(self.decoder), \
                _module_values().use_values(self.playback_values):
            yield

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("The engine is closed")

    # --- rendering ---

    def render(self, n_samples: int, dtype: str = "int16") -> np.ndarray:
        """
        The next `n_samples` of the mix, mono: int16, or float32 in -1..1 with
        dtype="float32". Any length; the mix runs in blocks of BLOCK_SAMPLES
        and keeps what is left over for the next call.
        """
        self._check_open()
        n = int(n_samples)
        if n < 0:
            raise ValueError("n_samples can't be negative")
        with self._scope():
            while self._held_count < n:
                chunk = self._channel.next_chunk().reshape(-1)
                self._held.append(self._apply_volume(chunk))
                self._held_count += len(chunk)
        joined = np.concatenate(self._held) if len(self._held) != 1 else self._held[0]
        out, rest = joined[:n], joined[n:]
        self._held = [rest] if len(rest) else []
        self._held_count = len(rest)
        self.clock.advance(n)
        if dtype == "int16":
            return out
        if dtype == "float32":
            return out.astype(np.float32) / 32768.0
        raise ValueError(f"dtype must be 'int16' or 'float32', not {dtype!r}")

    def blocks(self, n_samples: int = BLOCK_SAMPLES, dtype: str = "int16"):
        """render(n_samples) for ever, as an iterator."""
        while True:
            yield self.render(n_samples, dtype)

    def _apply_volume(self, chunk: np.ndarray) -> np.ndarray:
        """The output volume, ramped over the block when it changes (no click)."""
        start, end = self._volume, self._target_volume
        self._volume = end
        if start == end == 1.0:
            return chunk
        if start == end:
            gain = start
        else:
            gain = np.linspace(start, end, len(chunk), dtype=np.float32)
        return np.clip(chunk.astype(np.float32) * gain, -32768, 32767).astype(np.int16)

    # --- changes while it plays ---

    def set_preset(self, preset_id: Optional[str], glide_seconds: float = PRESET_GLIDE_SECONDS,
                   fade_seconds: Optional[float] = None, step: Optional[dict] = None) -> None:
        """
        Switch to a preset of the theme (None or "": the theme's own settings),
        as the server does: volume and Interval glide over `glide_seconds`,
        tracks turning on or off fade over `fade_seconds` (default: the Switch
        fade), `step` lays a sequence step's While playing values on top.
        """
        self._check_open()
        if preset_id and preset_id not in self._folder.presets:
            raise KeyError(f"The theme has no preset '{preset_id}'")
        with self._scope():
            self.preset_id = preset_id or None
            self._step = step
            self._channel.set_track_overrides(self._overrides(self.preset_id, step),
                                              glide_seconds=glide_seconds, fade_seconds=fade_seconds)

    def set_volume(self, volume: float) -> None:
        """Output volume 0..1 (after the mix; reached over the next block)."""
        self._target_volume = _unit(volume)

    @property
    def volume(self) -> float:
        return self._target_volume

    def set_master_gain(self, percent: float) -> None:
        """Master output gain, 0-100 % (the mix's own level, as Settings > Audio Settings)."""
        self._folder.theme.library.set_master_gain(percent)

    def fade_in(self, seconds: float) -> None:
        """Fade the whole mix up to full over `seconds` (Channel.fade_in)."""
        self._channel.fade_in(seconds)

    def fade_out(self, seconds: float) -> None:
        """Fade the whole mix down to silence over `seconds`; rendering goes on, silent (Channel.fade_out)."""
        self._channel.fade_out(seconds)

    def schedule_transitions(self, plans: list) -> None:
        """Advanced transitions (core/transitions.step_plans), timed in rendered audio."""
        self._channel.schedule_transitions(plans)

    def cancel_transitions(self) -> None:
        self._channel.cancel_transitions()

    def close(self) -> None:
        """Stop the mix and let go of every open file. Further render() calls raise."""
        if self._closed:
            return
        self._closed = True
        with self._scope():
            mix = self._channel._theme_stream
            self._channel.stop()
            for stream in list(getattr(mix, "sources", ()) or ()):
                close_stream(stream)
        self._held, self._held_count = [], 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _module_values():
    """sonorium.playback_values (named so it doesn't clash with the Engine argument)."""
    return playback_values
