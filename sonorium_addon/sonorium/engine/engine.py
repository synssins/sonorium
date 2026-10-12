"""
The playback engine: a theme folder mixed into PCM, pulled by the caller.

It drives the server's own mixing classes (theme.ThemeMix for the tracks,
core.channel.Channel for preset glides, theme crossfades and the start/stop
fade, core.playback_runner.PlaybackRunner for sequences and playlists)
without the server around them: no web server, no speakers, no MP3, no
threads. Each render() mixes as many blocks as asked for, so time in the mix
is the count of samples rendered, never the wall clock.

Determinism: every random draw comes from random.Random(seed) and every file
is read through the engine's decoder (both set for the calling thread only
while the engine works, see sonorium/rng.py and sonorium/decoders.py), and
the playback values (Settings > Advanced) are the engine's own. The same
theme files, settings, seed and calls give the same samples every time.
"""
from __future__ import annotations

import contextlib
import random
from pathlib import Path
from typing import Optional

import numpy as np

from sonorium import decoders, playback_values, rng
from sonorium.core.channel import THEME_CROSSFADE_DURATION, Channel
from sonorium.engine.runner import EngineHost, EngineRunner, EngineSession, drive
from sonorium.engine.theme_loader import DEFAULT_MASTER_GAIN, ThemeFolder, load_theme, reload_settings
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
        # Nothing to start: the caller pulls. Marked running so a theme change crossfades.
        self._generator_running = True

    def _transition_clock(self) -> float:
        # A theme change queued with transitions starts at the next pulled block: no audio passes between
        return 0.0


class Engine:
    """
    A theme mixed to mono 16-bit PCM at 44100 Hz, with its sequences, playlists
    and theme changes run as a server channel runs them.

        eng = Engine("themes/Rain", preset_id="calm", seed=1234)
        block = eng.render(44100)     # one second, numpy int16, shape (44100,)
        eng.set_preset("storm")       # glides over 3 s, as on the server
        eng.start_sequence("gentle")  # steps through the sequence's presets
        eng.reload_theme()            # after edits saved to the theme's files (core/theme_edit.py)
        eng.close()

    theme_dir         a theme folder (audio files, group folders, metadata.json, presets.json)
    preset_id         a preset of the theme; None: the theme's default preset (if one is
                      marked), "": no preset (the theme's own settings)
    playback_values   Settings > Advanced values that differ from the defaults
                      ({"sparse_min_interval": 60, ...}, see sonorium/playback_values.py)
    seed              every random choice in the mix (and in sequences and playlists) comes from this
    sample_rate       44100 only (the mixer's rate)
    decoder           how files are read (decoders.Decoder); PyAvDecoder() by default
    clock             a SampleClock to count rendered samples on (one is made otherwise)
    master_gain       Settings > Audio Settings > Master output gain, 0-100 %
    volume            output volume 0..1, applied after the mix (like a speaker's volume)
    fade_in           seconds to fade the whole mix in from silence at the start (0: none)
    step              a sequence step's While playing values laid over the preset
                      (core/sequences.merge_step), or None
    themes_root       where other themes are (linked files, playlist entries; default: the
                      theme folder's parent)
    crossfade         Settings > Audio Settings > Crossfade: seconds a theme change crossfades
    sequence_id       a sequence of the theme to run from the start (its first preset), or None
    """

    def __init__(self, theme_dir, preset_id: Optional[str] = None, playback_values: Optional[dict] = None,
                 seed: int = 0, sample_rate: int = SAMPLE_RATE, decoder: Optional[decoders.Decoder] = None,
                 clock: Optional[SampleClock] = None, master_gain: float = DEFAULT_MASTER_GAIN,
                 volume: float = 1.0, fade_in: float = 0.0, step: Optional[dict] = None, themes_root=None,
                 crossfade: float = THEME_CROSSFADE_DURATION, sequence_id: Optional[str] = None):
        if int(sample_rate) != SAMPLE_RATE:
            raise ValueError(f"The mixer runs at {SAMPLE_RATE} Hz only (asked for {sample_rate})")
        self.sample_rate = SAMPLE_RATE
        self.seed = seed
        self.decoder = decoder if decoder is not None else decoders.PyAvDecoder()
        self.playback_values = _module_values().validate(dict(playback_values or {}))
        self.clock = clock if clock is not None else SampleClock()
        self.crossfade_seconds = max(0.0, float(crossfade))
        self._rng = random.Random(seed)
        self._volume = self._target_volume = _unit(volume)
        self._master_gain = master_gain
        self._held: list[np.ndarray] = []  # mixed samples not handed out yet
        self._held_count = 0
        self._mixed = 0  # samples mixed (the runner's clock; ahead of `clock` by what is held)
        self._closed = False
        self._ended: Optional[str] = None
        self._folders: dict[str, ThemeFolder] = {}  # theme id -> loaded theme
        self._sequence_cache: dict[str, tuple] = {}
        self._id_map: Optional[dict] = None
        self._retired: list[tuple] = []  # (mix, its blocks) of themes crossfading out
        self._manual_step = step
        self._session = EngineSession()
        self._host = EngineHost(self, self._session)
        self._runner = EngineRunner(self._host, clock=self._mix_seconds, wall_clock=lambda: 0.0,
                                    rng=random.Random(f"runner:{seed}"))
        with self._scope():
            folder = self._load(theme_dir)
            self._themes_root = Path(themes_root) if themes_root else folder.folder.parent
            self._session.theme_id = folder.theme.id
            first = self._runner.check_sequence(folder.theme.id, sequence_id)["presets"][0] if sequence_id else None
            self._session.preset_id = first or self._pick_preset(preset_id)
            self._channel = _PullChannel(id=0, name="Engine")
            self._channel.set_theme(folder.theme, self._host.session_overrides(self._session), fade_in=fade_in)
            if sequence_id:
                self._manual_step = None
                # As a channel that starts playing with a sequence: its first preset holds from now
                self._runner.start_sequence(self._session, sequence_id, glide=0.0, apply=False)
                self._channel.set_track_overrides(self._host.session_overrides(self._session))

    # --- what it plays ---

    @property
    def _folder(self) -> ThemeFolder:
        return self._folders[self._session.theme_id]

    @property
    def theme_id(self) -> str:
        return self._folder.theme.id

    @property
    def theme_name(self) -> str:
        return _theme_name(self._folder)

    @property
    def theme_dir(self) -> Path:
        return self._folder.folder

    @property
    def preset_id(self) -> Optional[str]:
        return self._session.preset_id

    @property
    def sequence_id(self) -> Optional[str]:
        return self._session.sequence_id

    @property
    def presets(self) -> dict:
        """The theme's presets: {preset_id: name}."""
        return {pid: (data or {}).get("name", pid) for pid, data in self._folder.presets.items()}

    @property
    def sequences(self) -> dict:
        """The theme's sequences: {sequence_id: name}."""
        return {sid: (data or {}).get("name", sid) for sid, data in self._sequences_of(self.theme_id)[0].items()}

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

    def _pick_preset(self, requested: Optional[str], folder: Optional[ThemeFolder] = None) -> Optional[str]:
        """As session_manager.preset_for_theme, but an unknown preset raises KeyError."""
        folder = folder or self._folder
        if requested == "":
            return None
        if requested:
            if requested not in folder.presets:
                raise KeyError(f"The theme has no preset '{requested}'")
            return requested
        return folder.default_preset()

    def _overrides_for(self, theme_id: Optional[str], preset_id: Optional[str], step: Optional[dict]) -> dict:
        """A theme's preset layer for the channel (session_manager.preset_overrides)."""
        folder = self._folder_for(theme_id)
        if not preset_id or folder is None:
            return {}
        from sonorium.core.sequences import merge_step
        preset = merge_step(folder.presets.get(preset_id) or {}, step)
        return {**preset_track_overrides(preset.get("tracks", {})), **preset_group_overrides(preset.get("groups", {}))}

    def _overrides(self, preset_id: Optional[str], step: Optional[dict]) -> dict:
        return self._overrides_for(self.theme_id, preset_id, step)

    # --- themes on disk ---

    def _load(self, theme_dir) -> ThemeFolder:
        """Load a theme folder (once per theme id; a folder loaded before is reused)."""
        path = Path(theme_dir)
        for folder in self._folders.values():
            if folder.folder == path:
                return folder
        root = getattr(self, "_themes_root", None)
        folder = load_theme(path, self._master_gain, root)
        self._folders[folder.theme.id] = folder
        return folder

    def _folder_for(self, theme_id: Optional[str]) -> Optional[ThemeFolder]:
        """A theme by id: one loaded already, else found under themes_root and loaded. None if not found."""
        if not theme_id:
            return None
        folder = self._folders.get(theme_id)
        if folder is not None:
            return folder
        path = self._theme_path(theme_id)
        if path is None:
            return None
        try:
            with self._scope():
                return self._load(path)
        except (OSError, ValueError) as e:
            from sonorium.obs import logger
            logger.warning(f'Theme "{path.name}" can\'t be played: {e}')
            return None

    def _theme_path(self, theme_id: str) -> Optional[Path]:
        """The folder under themes_root whose metadata.json id (or folder name made safe) is theme_id."""
        for fresh in (False, True):
            if self._id_map is None or fresh:
                self._id_map = _scan_themes(self._themes_root)
            path = self._id_map.get(theme_id)
            if path is not None:
                return path
        return None

    def _sequences_of(self, theme_id: Optional[str]) -> tuple[dict, dict]:
        """A theme's sequences ({id: data}) and sequence_defaults, read once from its presets.json."""
        from sonorium.core.sequences import DEFAULTS, load_defaults, load_sequences
        cached = self._sequence_cache.get(theme_id)
        if cached is not None:
            return cached
        folder = self._folder_for(theme_id)
        result = (load_sequences(folder.folder), load_defaults(folder.folder)) if folder else ({}, dict(DEFAULTS))
        if folder is not None:
            self._sequence_cache[theme_id] = result
        return result

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

    def _mix_seconds(self) -> float:
        return self._mixed / SAMPLE_RATE

    # --- rendering ---

    def render(self, n_samples: int, dtype: str = "int16") -> np.ndarray:
        """
        The next `n_samples` of the mix, mono: int16, or float32 in -1..1 with
        dtype="float32". Any length; the mix runs in blocks of BLOCK_SAMPLES
        and keeps what is left over for the next call. Sequence steps and
        playlist entries change between blocks, when their time comes.
        """
        self._check_open()
        n = int(n_samples)
        if n < 0:
            raise ValueError("n_samples can't be negative")
        with self._scope():
            while self._held_count < n:
                if self._runner.next_due() <= self._mix_seconds():
                    drive(self._runner.tick())
                chunk = self._channel.next_chunk().reshape(-1)
                self._mixed += len(chunk)
                if self._retired:
                    self._close_retired()
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

    # --- theme changes ---

    def _crossfade_to(self, theme_id: str) -> None:
        """Crossfade the channel to a loaded theme with the channel's preset for it (Settings > Crossfade)."""
        channel = self._channel
        if channel._pending_theme is None and channel._theme_stream is not None:
            self._retired.append((channel._theme_stream, channel._chunk_generator))
        folder = self._folders[theme_id]
        channel.set_theme(folder.theme, self._host.session_overrides(self._session),
                          crossfade=self.crossfade_seconds)

    def _close_retired(self) -> None:
        """Let go of the files of themes that have finished crossfading out."""
        channel = self._channel
        keep = []
        for mix, blocks in self._retired:
            if mix is channel._theme_stream or (blocks is not None and blocks is channel._xfade_old):
                keep.append((mix, blocks))
                continue
            if blocks is not None:
                blocks.close()
            for stream in list(getattr(mix, "sources", ()) or ()):
                close_stream(stream)
        self._retired = keep

    def set_theme(self, theme_dir, preset_id: Optional[str] = None, sequence_id: Optional[str] = None) -> None:
        """
        Change the theme while playing: the current theme crossfades into the
        new one over `crossfade` seconds (Settings > Audio Settings >
        Crossfade), with no gap. preset_id: None for the theme's default
        preset, "" for none; sequence_id runs one of its sequences from its
        first preset. As on the server, a theme change stops the playlist and
        the sequence that were running. The same theme glides to the preset.
        Raises KeyError (unknown preset), LookupError/ValueError (sequence).
        """
        self._check_open()
        with self._scope():
            folder = self._load(theme_dir)
            theme_id = folder.theme.id
            if sequence_id:
                preset = self._runner.check_sequence(theme_id, sequence_id)["presets"][0]
            else:
                preset = self._pick_preset(preset_id, folder)
            self._resume()
            self._runner.clear_playlist(self._session)
            self._runner.stop_sequence(self._session)
            self._manual_step = None
            if theme_id == self._session.theme_id:
                self._host._apply_preset(self._session, preset, PRESET_GLIDE_SECONDS)
            else:
                self._session.theme_id = theme_id
                self._session.preset_id = preset
                self._crossfade_to(theme_id)
            if sequence_id:
                self._runner.start_sequence(self._session, sequence_id, apply=False)
                self._channel.set_track_overrides(self._host.session_overrides(self._session),
                                                  glide_seconds=PRESET_GLIDE_SECONDS)

    def reload_theme(self, glide_seconds: float = PRESET_GLIDE_SECONDS) -> None:
        """
        Re-read the theme's metadata.json and presets.json (after a change was
        saved to them, e.g. with sonorium.core.theme_edit) and let the playing
        mix follow, as a server channel follows the Theme Editor:

        - the theme's own track and group settings change at once (a track's
          volume eases over a quarter second, as when its slider moves);
        - the preset playing glides to its saved values over `glide_seconds`
          (as when a playing preset is saved), with the running sequence
          step's While playing values on top. The preset or sequence keeps
          playing; a preset no longer in presets.json keeps its values until
          another one is picked, a sequence no longer there stops at its next step.

        Every theme the engine has loaded (a playlist's too) is re-read; their
        audio files and linked files stay as they were loaded.
        """
        self._check_open()
        with self._scope():
            for folder in self._folders.values():
                reload_settings(folder)
            self._sequence_cache.clear()
            session = self._session
            if session.preset_id and session.preset_id not in self._folder.presets:
                return
            self._channel.set_track_overrides(self._host.session_overrides(session), glide_seconds=glide_seconds)

    # --- changes while it plays ---

    def set_preset(self, preset_id: Optional[str], glide_seconds: float = PRESET_GLIDE_SECONDS,
                   fade_seconds: Optional[float] = None, step: Optional[dict] = None) -> None:
        """
        Switch to a preset of the theme (None or "": the theme's own settings),
        as the server does: volume and Interval glide over `glide_seconds`,
        tracks turning on or off fade over `fade_seconds` (default: the Switch
        fade), `step` lays a sequence step's While playing values on top.
        Picking another preset stops a running sequence (as on the server).
        """
        self._check_open()
        if preset_id and preset_id not in self._folder.presets:
            raise KeyError(f"The theme has no preset '{preset_id}'")
        with self._scope():
            if (preset_id or None) != self._session.preset_id:
                self._runner.stop_sequence(self._session)
            self._session.preset_id = preset_id or None
            self._manual_step = step
            self._channel.set_track_overrides(self._host.session_overrides(self._session),
                                              glide_seconds=glide_seconds, fade_seconds=fade_seconds)

    # --- sequences ---

    def start_sequence(self, sequence_id: str, glide_seconds: float = PRESET_GLIDE_SECONDS) -> dict:
        """
        Run a sequence of the theme from its first preset (gliding to it), as a
        server channel runs it: each preset holds for a random time in
        hold_min..hold_max, then glides to the next over the ramp, with the
        step's While playing values and Coming in / Leaving transitions; at
        the end it reverses, restarts or stops. Returns the sequence as it
        plays. Raises LookupError (unknown) or ValueError (no presets).
        """
        self._check_open()
        with self._scope():
            self._resume()
            self._manual_step = None
            return self._runner.start_sequence(self._session, sequence_id, glide=glide_seconds)

    def stop_sequence(self) -> bool:
        """Stop the sequence; the mix stays on the preset it is on. True if one ran."""
        self._check_open()
        with self._scope():
            return self._runner.stop_sequence(self._session)

    # --- playlists ---

    def start_playlist(self, playlist: dict, themes_root=None) -> dict:
        """
        Play a playlist (the server's playlist JSON: name, entries of theme_id
        plus preset_id or sequence_id, order, repeat, play_min/play_max,
        fit_sequences) from its first entry. Entry themes are found by id
        under `themes_root` (default: the engine's). Entries whose theme isn't
        there are skipped, as on the server. Raises ValueError (not a valid
        playlist, or no entry can be played).
        """
        self._check_open()
        from sonorium.core.playlists import PlaylistError, clean_playlist
        if not isinstance(playlist, dict):
            raise PlaylistError("expected a JSON object")
        if themes_root is not None:
            self._themes_root = Path(themes_root)
            self._id_map = None
        with self._scope():
            data = {k: v for k, v in playlist.items() if k in ("name", "entries", "order", "repeat", "play_min",
                                                                "play_max", "fit_sequences")}
            data.setdefault("name", playlist.get("id") or "Playlist")
            clean = clean_playlist(data, lambda _t: True, self._preset_ids, self._sequence_ids)
            playlist_id = str(playlist.get("id") or "playlist")
            self._host.state.playlists = {playlist_id: clean}
            self._resume()
            self._runner.clear_playlist(self._session)
            self._manual_step = None
            return self._runner.set_playlist(self._session, playlist_id)

    def _preset_ids(self, theme_id: str):
        folder = self._folder_for(theme_id)
        return set(folder.presets) if folder is not None else _Anything()

    def _sequence_ids(self, theme_id: str):
        return set(self._sequences_of(theme_id)[0]) if self._folder_for(theme_id) is not None else _Anything()

    def stop_playlist(self) -> bool:
        """Stop the playlist; the mix keeps playing the theme it is on (an entry's sequence carries on). True if one ran."""
        self._check_open()
        with self._scope():
            return self._runner.clear_playlist(self._session)

    # --- Next / Previous ---

    def next(self) -> Optional[str]:
        """
        Next playlist entry if a playlist runs, else the sequence's next step
        (a 3 s glide). Returns 'playlist', 'sequence', 'end' (a playlist that
        doesn't repeat was on its last entry: the mix fades out) or None.
        """
        self._check_open()
        with self._scope():
            result = self._runner.skip(self._session)
            if result == "end":
                self._end("ended")
            return result

    def previous(self) -> Optional[str]:
        """Previous playlist entry, else the sequence's previous step. Returns as next()."""
        self._check_open()
        with self._scope():
            result = self._runner.previous(self._session)
            if result == "end":
                self._end("ended")
            return result

    def _end(self, reason: str) -> None:
        """The playlist ended: fade out over the crossfade time (as the server stops the channel)."""
        self._ended = reason
        self._session.is_playing = False
        self._runner.on_stop(self._session.id)
        self._channel.fade_out(self.crossfade_seconds)

    def _resume(self) -> None:
        """Playing something new after a playlist ended: fade back in."""
        if self._ended is not None:
            self._ended = None
            self._session.is_playing = True
            self._channel.fade_in(self.crossfade_seconds)

    # --- status ---

    def status(self) -> dict:
        """
        What plays now, JSON-serialisable: theme, preset, the sequence step
        and the playlist entry, with the seconds until each changes (counted
        in audio mixed).
        """
        session, runner = self._session, self._runner
        now = self._mix_seconds()
        folder = self._folder
        channel = self._channel
        result = {
            "playing": session.is_playing,
            "ended": self._ended,
            "seconds_rendered": round(self.clock.seconds, 3),
            "theme_id": folder.theme.id,
            "theme_name": _theme_name(folder),
            "preset_id": session.preset_id,
            "preset_name": self._host.preset_name(session.theme_id, session.preset_id) if session.preset_id else None,
            "crossfading": channel._pending_theme is not None or channel._xfade_old is not None,
            "sequence": None,
            "playlist": None,
            "recent": [dict(r) for r in session.recent],
        }
        with self._scope():
            base = runner.status(session)
        sequence, playlist = base.get("sequence"), base.get("playlist")
        if sequence is not None:
            sequence.pop("next_change_at", None)
            run, playlist_run = runner.running_sequence(session.id), runner.running_playlist(session.id)
            at = run.next_at if run is not None else None
            if at == float("inf"):
                at = playlist_run.next_at if playlist_run is not None else None
            sequence["seconds_left"] = None if at is None else round(max(0.0, at - now), 3)
            result["sequence"] = sequence
        if playlist is not None:
            playlist.pop("next_change_at", None)
            run = runner.running_playlist(session.id)
            playlist["seconds_left"] = None if run is None else round(max(0.0, run.next_at - now), 3)
            entry_folder = self._folders.get(playlist.get("theme_id"))
            if entry_folder is not None:
                playlist["theme_name"] = _theme_name(entry_folder)
            entries = (runner.playlist(session.playlist_id) or {}).get("entries") or []
            index = playlist.get("entry_index")
            playlist["entry"] = dict(entries[index]) if index is not None and 0 <= index < len(entries) else None
            result["playlist"] = playlist
        return result

    # --- output ---

    def set_volume(self, volume: float) -> None:
        """Output volume 0..1 (after the mix; reached over the next block)."""
        self._target_volume = _unit(volume)

    @property
    def volume(self) -> float:
        return self._target_volume

    def set_master_gain(self, percent: float) -> None:
        """Master output gain, 0-100 % (the mix's own level, as Settings > Audio Settings)."""
        self._master_gain = percent
        for folder in self._folders.values():
            folder.theme.library.set_master_gain(percent)

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
            self._runner.on_stop(self._session.id)
            mixes = [self._channel._theme_stream] + [mix for mix, _ in self._retired]
            generators = [self._channel._xfade_old] + [blocks for _, blocks in self._retired]
            self._channel.stop()
            self._retired = []
            for blocks in generators:
                if blocks is not None:
                    blocks.close()
            closed = set()
            for mix in mixes:
                if mix is None or id(mix) in closed:
                    continue  # a theme change not started yet: the current mix is also the retired one
                closed.add(id(mix))
                for stream in list(getattr(mix, "sources", ()) or ()):
                    close_stream(stream)
        self._held, self._held_count = [], 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class _Anything:
    """Every id: a playlist entry's preset or sequence of a theme that isn't here (the entry is skipped)."""

    def __contains__(self, item) -> bool:
        return True


def _theme_name(folder: ThemeFolder) -> str:
    return folder.metadata.name or folder.folder.name


def _scan_themes(root: Path) -> dict:
    """{theme id: folder} for the theme folders under root (metadata.json id, else the folder name made safe)."""
    import json
    from sonorium.theme import sanitize
    found: dict = {}
    try:
        folders = sorted(p for p in Path(root).iterdir() if p.is_dir())
    except OSError:
        return found
    for folder in folders:
        theme_id = None
        try:
            data = json.loads((folder / "metadata.json").read_text(encoding="utf-8-sig"))
            if isinstance(data, dict) and isinstance(data.get("id"), str):
                theme_id = data["id"]
        except (OSError, ValueError):
            pass
        found.setdefault(theme_id or sanitize(folder.name), folder)
    return found


def _unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _module_values():
    """sonorium.playback_values (named so it doesn't clash with the Engine argument)."""
    return playback_values
