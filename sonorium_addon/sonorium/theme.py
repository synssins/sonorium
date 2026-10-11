"""
Themes and their mixes.

A Theme is a theme folder as Sonorium loaded it: its tracks (ThemeTrack,
recording.py) with their settings, its groups and its id. A ThemeMix is one
running mix of a theme for one channel (or one direct listener): it holds a
player for each track, pulls a block from each, applies the on/off fades and
advanced transitions, and sums them (mixing.py). Changing a track's settings
changes every mix of the theme at the next block.
"""
import math
import re
import threading
import weakref
from functools import cached_property

import numpy as np

from sonorium.obs import logger
from sonorium.recording import BLOCK_SAMPLES, SAMPLE_RATE, ExclusionGroupCoordinator, ThemeTrack, close_stream, playback_values
from sonorium.utils import IndexList


def sanitize(text: str) -> str:
    """Sanitize a string to be safe for use as an ID/filename."""
    # Replace spaces and special chars with underscores
    text = re.sub(r'[^\w\-]', '_', text.lower())
    # Remove consecutive underscores
    text = re.sub(r'_+', '_', text)
    # Strip leading/trailing underscores
    return text.strip('_')

# Default output gain multiplier (now controlled via device.master_volume)
DEFAULT_OUTPUT_GAIN = 6.0

# Default threshold for short file detection (seconds): the default of Settings >
# Advanced's Short file threshold, which a theme without its own follows
DEFAULT_SHORT_FILE_THRESHOLD = 15.0

# A track turning on or off in a playing mix (a preset change, the mute
# button) fades in or out over this long instead of cutting. A sequence step
# fades the tracks it turns on or off over its ramp time instead. The default
# of Settings > Advanced's Switch fade (switch_fade_seconds()).
TRACK_TOGGLE_FADE_SECONDS = 3.0


def switch_fade_seconds() -> float:
    """The Switch fade now (Settings > Advanced), seconds."""
    return playback_values.get("switch_fade")

# A transition stopped half way (Next, a preset picked by hand) glides to
# what the channel plays now over this long
TRANSITION_RELEASE_SECONDS = 3.0
# Log the mix level when it has moved this far since the last line
MIX_LEVEL_LOG_DB = 1.0


class _Transition:
    """One track's advanced transition running in a mix (see core/transitions.py)."""

    def __init__(self, plan: dict, started: float, envelope, vref: float):
        self.plan = plan
        self.track = plan["track"]
        self.side = plan["side"]
        self.started = started  # audio seconds
        self.env = envelope
        self.vref = vref  # the volume the track's stream plays at while this runs (pinned)
        self.level = envelope.level(0.0)  # where it got to (volume 0..1)
        self.filter = None
        self.old_stream = None  # Start fresh over a sounding track: the old play, fading out
        self.old_level = 0.0
        self.finishing = self.side == "out" and "fresh" in envelope.styles
        self.finish_at = None  # audio seconds (looped files: the end of this pass)
        self.release = None  # {"at", "length", "from", "to" (a number, or None: what the channel plays now)}


class Theme:
    """
    One theme as loaded from its folder: its tracks in order (`tracks`, each
    a ThemeTrack) and its settings. `library` is the Sonorium device that
    loaded it (its file index and master volume). It plays through a
    ThemeMix, made per channel or listener with new_mix().
    """

    def __init__(self, library, name, theme_id: str = None):
        self.library = library
        self.name = name
        self._theme_id = theme_id  # metadata.json's id; None: the folder name made safe (older themes)

        # Short file threshold (seconds) - files shorter than this use sparse playback
        # Can be customized per theme via metadata.json
        self.short_file_threshold = playback_values.get("short_file_threshold")

        # Group settings by group name (metadata.json "groups"), read by grouped tracks
        self.groups: dict[str, dict] = {}

        # The theme's own files; the whole library only for a theme scanned before files were per theme
        files = library.theme_metas.get(name) if name in library.theme_metas else library.metas
        self.tracks = IndexList(ThemeTrack(file, theme=self) for file in files)

        # Mixes of this theme still playing. Held weakly, so a mix nobody
        # plays any more is freed with its players and buffers; keeping
        # every mix ever made let memory grow with each play.
        self.live_mixes: weakref.WeakSet = weakref.WeakSet()

    @cached_property
    def listen_url(self) -> str:
        """Where a speaker can play this theme on its own (outside any channel)."""
        from sonorium.settings import settings
        return f'{settings.stream_url}/stream/{self.id}'

    @property
    def id(self):
        """The theme's id: from metadata.json, else its name made safe for a URL."""
        return self._theme_id or sanitize(self.name)

    def new_mix(self, overrides: dict | None = None):
        """A fresh mix of this theme. `overrides`: the channel's preset values (see TrackView)."""
        mix = ThemeMix(self, overrides)
        self.live_mixes.add(mix)
        logger.debug(f'Theme "{self.name}": new mix ({len(self.live_mixes)} playing)')
        return mix


class ThemeMix:
    """
    One running mix of a theme, for one channel or listener. Every track gets
    its own player here, so two channels playing the same theme are
    independent: each has its own positions, turns, fades and preset values.

    blocks() gives the mixed audio a block at a time; iterating the mix gives
    it as MP3 for a listener (a theme's own /stream/{id}).
    """

    def __init__(self, theme: Theme, overrides: dict | None = None):
        self.theme = theme
        # This stream's preset values; changed in place when the channel's preset changes
        self.overrides = overrides if overrides is not None else {}

        # One coordinator per named group: only one track of a group plays at a
        # time, and groups don't wait for each other (Lute and Bar chatter can overlap)
        self.exclusion_coordinators: dict[str, ExclusionGroupCoordinator] = {}
        # Seconds of audio mixed so far: groups time their turns by this, not
        # the wall clock, so a stall can't let two tracks of a group overlap
        self.audio_seconds = 0.0

        from sonorium.mixing import MixLevel
        self.mix_level = MixLevel(SAMPLE_RATE, BLOCK_SAMPLES)
        self._silence = np.zeros((1, BLOCK_SAMPLES), np.int16)  # what plays when no track sounds

        # Track key -> its TrackView, and the streams in mixing order: one per
        # track, except a Merry-go-round group, which plays as one stream (its bed)
        self._views: dict[str, object] = {}
        self.sources = []
        self._beds: tuple = ()  # the groups playing as a merry-go-round
        self._intrusions: tuple = ()  # the groups in Intrusion mode (tracks play as ungrouped)
        self._ebbs: tuple = ()  # the groups in Ebb & Flow mode (every file at once)
        self.ebb_groups: dict = {}  # Ebb & Flow group -> its EbbGroup (the never-silent cycle)
        # Track key -> its on/off fade gain (1 on, 0 off), ramped each chunk (TRACK_TOGGLE_FADE_SECONDS)
        self._toggle_gain: dict[str, float] = {}
        # Track key -> on/off as last mixed, and the per-chunk step of its current on/off fade
        self._toggle_on: dict[str, bool] = {}
        self._toggle_step: dict[str, float] = {}
        # Track key -> fade length (seconds) for the on/off change it just had (a sequence step's ramp)
        self._next_fade: dict[str, float] = {}
        # Advanced transitions (core/transitions.py): planned ones (start in audio
        # seconds), running ones by track, and the values they pin on a track
        self._pending: list = []
        self._active: dict[str, _Transition] = {}
        self._pins: dict[str, dict] = {}
        self._cancel: float | None = None
        # Who plays this mix, for "Playback:" log lines (the channel sets it)
        self._context = {"label": theme.name}
        self._logged_gain: float | None = None
        self._lock = threading.Lock()  # adopt (API thread) and mode changes (mixing thread)
        self._build(theme)

    @property
    def label(self) -> str:
        return self._context.get("label") or self.theme.name

    @label.setter
    def label(self, value: str) -> None:
        self._context["label"] = value

    def _new_stream(self, track):
        from sonorium.recording import preset_gap_range

        group = track.exclusion_group
        if group and group not in self.exclusion_coordinators:
            # Read the group's gap each time (the channel's preset, else the
            # theme this stream plays now), so a preset, a sequence step or a
            # change in the editor applies from the next wait
            gap = lambda g=group: preset_gap_range(g, self.overrides, getattr(self.theme, "groups", None))
            self.exclusion_coordinators[group] = ExclusionGroupCoordinator(gap, clock=lambda: self.audio_seconds)
        coordinator = self.exclusion_coordinators.get(group) if group else None
        stream = track.open_stream(exclusion_coordinator=coordinator)
        stream.built_as = self._build_key(track)
        ebb = getattr(track, "ebb_group_name", None)
        if ebb and hasattr(stream, "ebb_group"):
            from sonorium.recording import EbbGroup, group_pauses
            if ebb not in self.ebb_groups:
                self.ebb_groups[ebb] = EbbGroup(
                    lambda g=ebb: group_pauses(g, self.overrides, getattr(self.theme, "groups", None)))
            self.ebb_groups[ebb].register(track.name, track)
            stream.ebb_group = self.ebb_groups[ebb]
        return stream

    @staticmethod
    def _build_key(track):
        """What a track's stream was built for: its mode and, for a loop, Gapless. A change needs a new stream."""
        from sonorium.recording import PlaybackMode
        try:
            mode = track._resolve_playback_mode()
        except Exception:
            return None
        looped = mode in (PlaybackMode.CONTINUOUS, PlaybackMode.PRESENCE)
        return mode, bool(getattr(track, "crossfade_enabled", True)) if looped else None

    def _rebuilt_tracks(self) -> list:
        """Tracks whose mode (or Gapless) changed since their stream was built (the editor, a preset)."""
        out = []
        for stream in list(self.sources):
            built = getattr(stream, "built_as", None)
            if built is not None and built != self._build_key(stream.instance):
                out.append(stream.instance.name)
        return out

    def _bed_groups(self, theme=None) -> tuple:
        """The groups set to Merry-go-round now."""
        from sonorium.recording import GROUP_MODE_MERRY_GO_ROUND, group_mode
        groups = getattr(theme or self.theme, "groups", None) or {}
        return tuple(sorted(g for g, s in list(groups.items()) if group_mode(s) == GROUP_MODE_MERRY_GO_ROUND))

    def _intrusion_groups(self, theme=None) -> tuple:
        """The groups set to Intrusion now (their tracks play as their own settings say)."""
        from sonorium.recording import GROUP_MODE_INTRUSION, group_mode
        groups = getattr(theme or self.theme, "groups", None) or {}
        return tuple(sorted(g for g, s in list(groups.items()) if group_mode(s) == GROUP_MODE_INTRUSION))

    def _ebb_groups_now(self, theme=None) -> tuple:
        """The groups set to Ebb & Flow now."""
        from sonorium.recording import GROUP_MODE_EBB_FLOW, group_mode
        groups = getattr(theme or self.theme, "groups", None) or {}
        return tuple(sorted(g for g, s in list(groups.items()) if group_mode(s) == GROUP_MODE_EBB_FLOW))

    def _new_bed(self, group: str):
        """A Merry-go-round group's stream. It reads the group's files and settings from this stream's theme now."""
        from sonorium.recording import MerryGoRoundStream

        def members():
            views = (self._views.get(i.name) for i in list(self.theme.tracks)
                     if getattr(i.meta, "group", None) == group)
            return [v for v in views if v is not None]

        def settings():
            return (getattr(self.theme, "groups", None) or {}).get(group)

        logger.info(f'ThemeMix "{self.theme.name}": group "{group}" plays as a merry-go-round')
        return MerryGoRoundStream(group, members, settings)

    def _build(self, theme):
        """
        Streams for `theme`, keeping the ones still right: a track still
        there keeps playing where it is and reads the new settings; a new track
        gets its stream; a Merry-go-round group keeps its bed (new files join
        its pool, removed ones finish first); a group whose mode changed gets
        new streams. Returns (tracks joined, tracks left).
        """
        from sonorium.recording import MerryGoRoundStream, TrackView

        beds = self._bed_groups(theme)
        intrusions = self._intrusion_groups(theme)
        ebbs = self._ebb_groups_now(theme)
        # Groups that became or stopped being Intrusion or Ebb & Flow: their
        # tracks get new streams (taking turns or not is decided when a stream is made)
        switched = (set(intrusions) ^ set(self._intrusions)) | (set(ebbs) ^ set(self._ebbs))
        old_tracks, old_beds = {}, {}
        for stream in self.sources:
            if isinstance(stream, MerryGoRoundStream):
                old_beds[stream.group] = stream
            else:
                old_tracks[stream.instance.name] = stream
        for group in beds + intrusions + ebbs:
            self.exclusion_coordinators.pop(group, None)  # turns start over if it goes back to Intermittent
        for group in [g for g in self.ebb_groups if g not in ebbs or g in switched]:
            self.ebb_groups.pop(group, None)
        self.theme = theme
        before = set(self._views)
        streams, seen = [], set()
        for instance in theme.tracks:
            view = self._views.get(instance.name)
            if view is None:
                view = self._views[instance.name] = TrackView(instance, self.overrides, self._pins, self._context)
            else:
                view._instance = instance  # same track: new settings, same place
            group = getattr(instance.meta, "group", None)
            if group in beds:
                old_tracks.pop(instance.name, None)  # its own stream (Intermittent before) stops
                if group not in seen:
                    seen.add(group)
                    streams.append(old_beds.pop(group, None) or self._new_bed(group))
                continue
            stream = old_tracks.pop(instance.name, None)
            if group in switched:
                stream = None
            elif stream is not None and getattr(stream, "built_as", None) not in (None, self._build_key(view)):
                stream = None  # its mode or Gapless changed: a new stream, faded in like a track turned on
                self._toggle_gain[instance.name] = 0.0
            streams.append(stream if stream is not None else self._new_stream(view))
        names = {instance.name for instance in theme.tracks}
        for name in [n for n in list(self._views) if n not in names]:
            self._views.pop(name, None)
        self.sources = streams  # one assignment: the mixer sees the old list or the new one
        self._beds = beds
        self._intrusions = intrusions
        self._ebbs = ebbs
        return sorted(names - before), sorted(before - names)

    def adopt(self, theme: Theme):
        """
        The theme was rescanned (a file uploaded, moved or deleted): follow the
        new definition without restarting. Tracks still there keep playing
        where they are and now read the new settings; new tracks join at once
        with their saved settings (in a group, with no drag, so they're a
        likely early pick); removed tracks leave.
        """
        with self._lock:
            added, left = self._build(theme)
        if added or left:
            logger.info(f'ThemeMix "{theme.name}": {len(added)} track(s) joined, {len(left)} left')

    def _follow_group_modes(self):
        """
        A group switched between Intermittent, Merry-go-round, Ebb & Flow and
        Intrusion, or a track's mode (or Gapless) changed: rebuild those streams.
        """
        changed = self._rebuilt_tracks()
        if changed:
            with self._lock:
                changed = self._rebuilt_tracks()
                if changed:
                    self._build(self.theme)
                    logger.info(f'ThemeMix "{self.theme.name}": {", ".join(changed)} now play(s) as set')
        if (self._bed_groups() == self._beds and self._intrusion_groups() == self._intrusions
                and self._ebb_groups_now() == self._ebbs):
            return
        with self._lock:
            if (self._bed_groups() != self._beds or self._intrusion_groups() != self._intrusions
                    or self._ebb_groups_now() != self._ebbs):
                self._build(self.theme)
                logger.info(f'ThemeMix "{self.theme.name}": group modes changed '
                            f'(merry-go-round: {", ".join(self._beds) or "none"})')

    @staticmethod
    def _holds_turn(stream) -> bool:
        coordinator = getattr(stream, "exclusion_coordinator", None)
        return coordinator is not None and coordinator.is_playing(stream.instance.name)

    def track_states(self) -> dict:
        """Each track's on/off as the mix reads it now ({track key: bool})."""
        return {name: bool(view.is_enabled) for name, view in list(self._views.items())}

    def fade_toggles(self, before: dict, seconds: float) -> None:
        """
        The tracks whose on/off now differs from `before` (track_states() taken
        just before a preset change) fade in or out over `seconds` instead of
        TRACK_TOGGLE_FADE_SECONDS. Taken up by the next chunk mixed.
        """
        for name, on in self.track_states().items():
            if name in before and before[name] != on:
                self._next_fade[name] = seconds

    @staticmethod
    def _fade_step(seconds: float) -> float:
        """How far an on/off fade of `seconds` moves per chunk (a fade of 0 still ramps over one chunk)."""
        if not seconds or seconds <= 0:
            return 1.0
        return min(1.0, BLOCK_SAMPLES / (seconds * SAMPLE_RATE))

    def _pull(self, step: float) -> list:
        """
        One chunk from each track stream, with its on/off fade: a track turned
        off fades out over TRACK_TOGGLE_FADE_SECONDS (a sequence step: its ramp,
        see fade_toggles) and is then no longer pulled (it pauses), except a
        group's turn holder, which runs on silently so it ends on time; a track
        turned on fades in from silence, and an Ebb & Flow track starts afresh
        (from its file's beginning, see PresenceMixingStream.restart). A track
        already on when the mix starts (or joins it) starts at full level.
        """
        self._start_due_transitions()
        chunks, gains = [], {}
        for stream in list(self.sources):
            name = stream.instance.name
            transition = self._active.get(name)
            if transition is not None:
                data, gains[name] = self._pull_transition(transition, stream)
                if data is not None:
                    chunks.append(data)
                continue
            enabled = bool(stream.instance.is_enabled)
            was = self._toggle_on.get(name)
            self._toggle_on[name] = enabled
            if was is not None and was != enabled:
                seconds = self._next_fade.get(name)
                self._toggle_step[name] = step if seconds is None else self._fade_step(seconds)
                fade = switch_fade_seconds() if seconds is None else seconds
                logger.info(f'Playback: {self.label}: {name} {"on, fading in" if enabled else "off, fading out"} over {fade:g}s')
            track_step = self._toggle_step.get(name, step)
            old = self._toggle_gain.get(name, 1.0 if enabled else 0.0)
            new = min(1.0, old + track_step) if enabled else max(0.0, old - track_step)
            gains[name] = new
            if old <= 0.0 and new <= 0.0:
                if self._holds_turn(stream):
                    next(stream)  # muted mid-play: runs on silently, so it ends on time
                continue
            if old <= 0.0:
                restart = getattr(stream, "restart", None)
                if restart is not None:
                    restart()  # turned on from silence: an Ebb & Flow track starts afresh
            data = next(stream)
            if old < 1.0 or new < 1.0:
                ramp = np.linspace(old, new, data.shape[-1], dtype=np.float32)
                data = (data.astype(np.float32) * ramp).astype(np.int16)
            chunks.append(data)
        for name in [n for n in self._active if n not in gains]:
            self._finish_transition(name)  # its track left the theme
        self._toggle_gain = gains
        self._next_fade.clear()
        for name in [n for n in self._toggle_on if n not in gains]:
            self._toggle_on.pop(name, None)
            self._toggle_step.pop(name, None)
        return chunks

    def blocks(self):
        """The mix, one int16 block (1, BLOCK_SAMPLES) at a time, for ever."""
        while True:
            self._follow_group_modes()
            sounding = self._pull(self._fade_step(switch_fade_seconds()))  # the Switch fade, read live
            self.audio_seconds += BLOCK_SAMPLES / SAMPLE_RATE
            # Sum the tracks with a smoothed level (sonorium/mixing.py); silence when none sounds
            gain = getattr(self.theme.library, 'master_volume', DEFAULT_OUTPUT_GAIN)
            block = self.mix_level.mix(sounding or [self._silence], gain)
            self._log_mix_level()
            yield block

    def _log_mix_level(self) -> None:
        """A "Playback:" line when the mixer's level has moved MIX_LEVEL_LOG_DB since the last one."""
        gain = float(getattr(self.mix_level, "gain", 1.0) or 0.0)
        if gain <= 0:
            return
        if self._logged_gain is None:
            self._logged_gain = gain
            return
        if abs(20.0 * math.log10(gain / self._logged_gain)) >= MIX_LEVEL_LOG_DB:
            self._logged_gain = gain
            count = float(getattr(self.mix_level, "count", 0.0) or 0.0)
            logger.info(f'Playback: {self.label}: mix level {20.0 * math.log10(gain):.1f} dB '
                        f'({count:.1f} tracks sounding)')

    # --- Advanced transitions (core/transitions.py, docs/PLAYBACK.md) ---

    def schedule_transitions(self, plans: list) -> None:
        """
        Plan tracks' advanced transitions. Each plan (transitions.step_plans
        plus "delay": seconds from now until it starts, and "window": its
        length in seconds) starts when that much audio has been mixed.
        """
        with self._lock:
            for plan in plans:
                plan = dict(plan)
                plan["start_at"] = self.audio_seconds + max(0.0, float(plan.get("delay", 0.0)))
                self._pending.append(plan)
            self._pending.sort(key=lambda p: p["start_at"])

    def cancel_transitions(self, seconds: float = TRANSITION_RELEASE_SECONDS) -> None:
        """Drop the planned transitions; running ones glide to what the channel plays now."""
        with self._lock:
            self._pending = []
            self._cancel = seconds

    def transitions_running(self) -> list:
        """The tracks with a transition running now."""
        return sorted(self._active)

    def _stream_of(self, name: str):
        from sonorium.recording import MerryGoRoundStream
        for stream in list(self.sources):
            if not isinstance(stream, MerryGoRoundStream) and stream.instance.name == name:
                return stream
        return None

    def _replace_stream(self, old, new) -> None:
        self.sources = [new if s is old else s for s in self.sources]

    def _start_due_transitions(self) -> None:
        now = self.audio_seconds
        if not self._pending and self._cancel is None:
            return
        with self._lock:
            due = [p for p in self._pending if p["start_at"] <= now + 1e-9]
            self._pending = [p for p in self._pending if p["start_at"] > now + 1e-9]
            cancel, self._cancel = self._cancel, None
        if cancel is not None:
            for transition in list(self._active.values()):
                self._begin_release(transition, now, cancel, None)
        for plan in due:
            try:
                self._begin_transition(plan, now)
            except Exception as e:
                logger.warning(f'Playback: {self.label}: transition for {plan.get("track")} not started: {e}')

    def _begin_transition(self, plan: dict, now: float) -> None:
        from sonorium.core.transitions import EPS, Envelope, OnePoleLowpass, style_names
        from sonorium.recording import SAMPLE_RATE as RATE
        name = plan["track"]
        view, stream = self._views.get(name), self._stream_of(name)
        if view is None or stream is None:
            return
        running = self._active.get(name)
        enabled = bool(view.unpinned("is_enabled"))
        if running is not None:
            a = running.level
        else:
            gain = self._toggle_gain.get(name, 1.0 if enabled else 0.0)
            a = float(view.unpinned("volume") or 0.0) * gain
        b = float(plan["target_volume"])
        side = plan["side"]
        if (side == "in" and b <= a + EPS) or (side == "out" and b >= a - EPS):
            return  # not louder (or quieter) any more: the normal ramp does it
        pa = float(view.unpinned("presence")) if side == "out" else float(plan.get("from_presence", 1.0))
        envelope = Envelope(side, plan.get("styles"), plan.get("values"), a, b, plan.get("window", 0.0),
                            plan.get("max_volume"), plan.get("seed", 0), pa=pa,
                            pb=float(plan.get("target_presence", 1.0)))
        transition = _Transition(plan, now, envelope, max(a, b, 0.001))
        if envelope.far is not None:
            transition.filter = OnePoleLowpass(RATE)
        pins = {"volume": transition.vref, "is_enabled": True}
        if envelope.amount:
            pins["interval_live"] = True
            pins["presence"] = envelope.presence(0.0)
        grouped = getattr(stream, "exclusion_coordinator", None) is not None
        if transition.finishing:
            if hasattr(stream, "playing"):
                pins["is_enabled"] = False  # no new play starts; the one going out finishes
                if not stream.playing:
                    transition.finish_at = now  # nothing playing: done at once
            elif getattr(stream, "length", 0):
                from sonorium.recording import loop_crossfade_samples
                left = stream.length - (getattr(stream, "overlap", None) or loop_crossfade_samples()) - stream.position
                if left <= 0:
                    left += stream.length
                transition.finish_at = now + min(left / RATE, 600.0)
            elif envelope.holds:
                transition.finish_at = now  # can't tell where the play is: stop now (short fade)
        if running is not None:
            running.release = None
            if running.old_stream is not None:
                close_stream(running.old_stream)
        self._pins[name] = pins
        self._active[name] = transition
        paused = running is None and self._toggle_gain.get(name, 1.0 if enabled else 0.0) <= 0.0
        if side == "in" and "fresh" in envelope.styles and not grouped:
            new = self._new_stream(view)
            if hasattr(new, "play_now"):
                new.play_now = True
            if not paused and a > EPS:
                transition.old_stream, transition.old_level = stream, a
            else:
                close_stream(stream)
            self._replace_stream(stream, new)
        elif side == "in" and paused:
            restart = getattr(stream, "restart", None)
            if restart is not None:
                restart()  # an Ebb & Flow track starts afresh
        pre = plan.get("pre_fade_used", 0.0)
        logger.info(f'Playback: {self.label}: {name} {"coming in" if side == "in" else "leaving"} '
                    f'({style_names(side, envelope.styles)}) over {envelope.window:.0f}s'
                    f'{f", starting {pre:.0f}s before the step" if pre else ""}: '
                    f'{round(a * 100)}% -> {round(b * 100)}%')

    def _begin_release(self, transition, now: float, seconds: float, target) -> None:
        """Glide from where the transition is to `target` (None: what the channel plays now)."""
        transition.release = {"at": now, "length": max(0.05, seconds), "from": transition.level, "to": target}
        pins = self._pins.get(transition.track)
        if pins is not None:
            pins.pop("presence", None)
            pins.pop("interval_live", None)
            if target is None:
                pins["is_enabled"] = True

    def _live_level(self, name: str) -> float:
        """A track's volume as the channel's preset has it now (0 when off)."""
        view = self._views.get(name)
        if view is None:
            return 0.0
        return float(view.unpinned("volume") or 0.0) if view.unpinned("is_enabled") else 0.0

    def _release_target(self, transition) -> float:
        release = transition.release
        if release["to"] is not None:
            return float(release["to"])
        return self._live_level(transition.track)

    def _pull_transition(self, transition, stream):
        """One chunk of a track with a transition running: its level from the envelope (and filter)."""
        from sonorium.recording import SAMPLE_RATE as RATE
        size = BLOCK_SAMPLES
        chunk_seconds = size / RATE
        now = self.audio_seconds
        name = transition.track
        pins = self._pins.get(name, {})
        env = transition.env
        t1 = now + chunk_seconds - transition.started
        start_level = transition.level
        if transition.release is None:
            end_level = env.level(t1)
            presence = env.presence(t1)
            if presence is not None:
                pins["presence"] = presence
        else:
            release = transition.release
            target = self._release_target(transition)
            if target > transition.vref:
                transition.vref = pins["volume"] = target
            share = min(1.0, (now + chunk_seconds - release["at"]) / release["length"])
            end_level = release["from"] + (target - release["from"]) * share
        data = next(stream)
        vref = transition.vref
        out = data.astype(np.float32).reshape(-1) * np.linspace(start_level / vref, end_level / vref, size,
                                                               dtype=np.float32)
        if transition.filter is not None:
            cutoff = env.cutoff(t1 - chunk_seconds / 2)
            if cutoff is not None:
                out = transition.filter.process(out, cutoff).astype(np.float32)
        if transition.old_stream is not None:
            # Start fresh over a sounding track: the old play fades out under the new one
            t0 = t1 - chunk_seconds
            from sonorium.core.transitions import FRESH_FADE
            g0 = transition.old_level * max(0.0, 1.0 - t0 / FRESH_FADE) / vref
            g1 = transition.old_level * max(0.0, 1.0 - t1 / FRESH_FADE) / vref
            old = next(transition.old_stream)
            out = out + old.astype(np.float32).reshape(-1) * np.linspace(g0, g1, size, dtype=np.float32)
            if g1 <= 0.0:
                close_stream(transition.old_stream)
                transition.old_stream = None
        transition.level = end_level
        data = np.clip(out, -32768, 32767).astype(np.int16).reshape(1, -1)

        # Finish then stop: once the play going out has finished, a short fade
        if transition.finishing and transition.release is None:
            ended = (hasattr(stream, "playing") and not stream.playing) or \
                    (transition.finish_at is not None and now + chunk_seconds >= transition.finish_at)
            if ended:
                from sonorium.core.transitions import FINISH_FADE
                self._begin_release(transition, now + chunk_seconds, FINISH_FADE, env.b)
                transition.release["from"] = end_level
        done = False
        if transition.release is not None:
            release = transition.release
            done = now + chunk_seconds >= release["at"] + release["length"]
        elif t1 >= env.window and not env.holds:
            if abs(self._live_level(name) - end_level) > 0.02:
                self._begin_release(transition, now + chunk_seconds, TRANSITION_RELEASE_SECONDS, None)
            else:
                done = True
        gain = 1.0 if end_level > 0 else 0.0
        if done:
            gain = self._finish_transition(name)
        return data, gain

    def _finish_transition(self, name: str) -> float:
        """The transition is over: the track goes back to its preset values (and on/off fades)."""
        transition = self._active.pop(name, None)
        self._pins.pop(name, None)
        if transition is not None and transition.old_stream is not None:
            close_stream(transition.old_stream)
        view = self._views.get(name)
        enabled = bool(view.is_enabled) if view is not None else False
        self._toggle_on[name] = enabled
        self._toggle_step.pop(name, None)
        if transition is not None:
            logger.info(f'Playback: {self.label}: {name} transition done ({round(transition.level * 100)}%)')
        return 1.0 if enabled else 0.0

    def __iter__(self):
        """
        This mix as an MP3 byte stream for one listener (a theme's own
        /stream/{id}), sent no faster than it plays (sonorium/mp3_out.py).
        """
        from sonorium.mp3_out import Mp3Encoder, RealtimePacer
        encoder, pacer = Mp3Encoder(), RealtimePacer()
        blocks = self.blocks()
        try:
            for block in blocks:
                yield from encoder.encode(block)
                pacer.advance(block.shape[-1] / SAMPLE_RATE)
        finally:
            logger.info(f'Theme mix "{self.label}": listener gone, MP3 stream closed')
            blocks.close()
            encoder.close()
