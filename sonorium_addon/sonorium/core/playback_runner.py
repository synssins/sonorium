"""
Runs sequences and playlists on channels (docs/PLAYBACK.md).

- A **sequence** steps through presets of the channel's theme: each preset
  holds for a random time in [hold_min, hold_max] (drawn fresh each step),
  then the channel glides to the next one over the ramp time. At the end it
  goes back in reverse, restarts, or stops on the last preset.
- A **playlist** plays its entries (a theme, plus a preset or a sequence of
  it) one after another, each for a random time in [play_min, play_max],
  in order or shuffled (never the same entry twice in a row), then repeats
  or stops. An entry with a sequence plays one pass of it instead (each
  preset once, first to last, at the sequence's hold and ramp times); with
  the playlist's fit_sequences the pass is scaled to fit a play time.

Times are counted on the monotonic wall clock. Only playing channels run:
stopping a channel stops its timers (the sequence and playlist it has are
kept, so playing again starts the sequence from its first preset and
carries on with the playlist entry it was on).

Advanced transitions (core/transitions.py): each time a step's time is
set, the next step's transitions are planned on the channel's mix right
away, so a track with a pre-fade starts that many seconds before the step.
Next, a preset picked by hand, or a stopped sequence cancels them.

The session manager owns one runner; a background loop calls tick().
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from sonorium.obs import logger

if TYPE_CHECKING:
    from sonorium.core.session_manager import SessionManager
    from sonorium.core.state import Session

# Picking a preset by hand (or Next) glides over this long; the same as a
# track turning on or off (theme.TRACK_TOGGLE_FADE_SECONDS)
PRESET_GLIDE_SECONDS = 3.0


@dataclass
class SequenceRun:
    sequence_id: str
    theme_id: str
    index: int = 0
    direction: int = 1
    next_at: float = 0.0
    # One pass for a playlist entry: the presets, each one's hold and the
    # ramp, planned when the entry starts (None: an ordinary, endless run)
    presets: Optional[list] = None
    holds: Optional[list] = None
    ramp: Optional[float] = None
    # The preset the next step's advanced transitions were planned for
    planned: Optional[str] = None

    @property
    def single_pass(self) -> bool:
        return self.holds is not None


# A pass's last preset: nothing more to step to (the playlist moves on)
NEVER = float("inf")


@dataclass
class PlaylistRun:
    playlist_id: str
    next_at: float = 0.0


class PlaybackRunner:
    """Sequence and playlist timers for every playing channel."""

    def __init__(self, manager: SessionManager, clock=time.monotonic, wall_clock=time.time, rng=None):
        self.manager = manager
        self.clock = clock
        self.wall_clock = wall_clock
        self.rng = rng or random.Random()
        self._sequences: dict[str, SequenceRun] = {}
        self._playlists: dict[str, PlaylistRun] = {}
        # Optional async callback(session) after the runner changed a channel
        # (a sequence step, a new playlist entry), so MQTT entities follow
        self.on_change = None

    # --- Definitions ---

    def sequence(self, theme_id: Optional[str], sequence_id: Optional[str]) -> Optional[dict]:
        """
        A theme's sequence as it plays now: name, at_end, timing (its own or
        the theme's defaults) and its presets that still exist. None if unknown.
        """
        if not theme_id or not sequence_id:
            return None
        sequences, defaults = self.manager.theme_sequences(theme_id)
        data = sequences.get(sequence_id)
        if data is None:
            return None
        from sonorium.core.sequences import AT_END, DEFAULT_AT_END, resolve_timing
        presets = self.manager._theme_presets(theme_id)
        timing = resolve_timing(data, defaults)
        return {
            "id": sequence_id,
            "name": data.get("name", sequence_id),
            "presets": [p for p in (data.get("presets") or []) if p in presets],
            "at_end": data.get("at_end") if data.get("at_end") in AT_END else DEFAULT_AT_END,
            "transitions": dict(data.get("transitions") or {}),
            "while": dict(data.get("while") or {}),
            **timing,
        }

    def _live(self, session: Session) -> bool:
        """The channel's mix runs (its speakers play, or a browser listens): its timers run."""
        live = getattr(self.manager, "_live", None)
        return live(session) if live is not None else session.is_playing

    def _session(self, session_id: str) -> Optional[Session]:
        """A channel by id, a personal one included."""
        find = getattr(self.manager, "_find", None)
        return find(session_id) if callable(find) else self.manager.state.sessions.get(session_id)

    def _sessions(self) -> list:
        """Every channel, personal ones included."""
        every = getattr(self.manager, "_all_sessions", None)
        return every() if callable(every) else list(self.manager.state.sessions.values())

    def playlist(self, playlist_id: Optional[str]) -> Optional[dict]:
        if not playlist_id:
            return None
        return self.manager.state.playlists.get(playlist_id)

    def _hold(self, sequence: dict) -> float:
        return self.rng.uniform(sequence["hold_min"], sequence["hold_max"])

    def _play_time(self, playlist: dict) -> float:
        low = float(playlist.get("play_min", 1800))
        high = float(playlist.get("play_max", low))
        return self.rng.uniform(min(low, high), max(low, high))

    def _new_order(self, count: int, shuffle: bool, last: Optional[int] = None,
                   themes: Optional[list] = None, recent: Optional[list] = None) -> list[int]:
        """
        Entry indexes for one round: in order, or shuffled. A shuffled round
        plays every entry once and never plays the same THEME twice in a row,
        nor the theme before that (no A, B, A) when the playlist has 3 or more
        different themes. `recent` holds the themes that just played (oldest
        first), so the rule also holds where one round meets the next. When an
        order can't avoid it (e.g. most entries are one theme), the closest
        one found is used.
        """
        order = list(range(count))
        if not shuffle or count < 2:
            return order
        themes = list(themes) if themes else [str(i) for i in range(count)]
        if recent is None:
            recent = [themes[last]] if last is not None and 0 <= last < count else []
        distinct = len(set(themes))

        def allowed(index, history, strict):
            theme = themes[index]
            if history and theme == history[-1]:
                return False
            if strict and distinct >= 3 and len(history) >= 2 and theme == history[-2]:
                return False
            return True

        fallback = None
        for _ in range(50):
            remaining = list(range(count))
            self.rng.shuffle(remaining)
            out, history, clean = [], list(recent), True
            while remaining:
                pick = next((i for i in remaining if allowed(i, history, True)), None)
                if pick is None:
                    clean = False
                    pick = next((i for i in remaining if allowed(i, history, False)), remaining[0])
                remaining.remove(pick)
                out.append(pick)
                history.append(themes[pick])
            if clean:
                return out
            fallback = fallback or out
        return fallback

    @staticmethod
    def _themes_of(playlist: dict) -> list:
        return [entry.get("theme_id") for entry in (playlist.get("entries") or [])]

    def _order_ok(self, session: Session, playlist: dict) -> bool:
        count = len(playlist.get("entries") or [])
        order = session.playlist_order or []
        return sorted(order) == list(range(count)) and 0 <= session.playlist_position < count

    # --- Sequences ---

    def check_sequence(self, theme_id: Optional[str], sequence_id: str) -> dict:
        """The sequence, if it can run. Raises LookupError (unknown) or ValueError (no presets)."""
        sequence = self.sequence(theme_id, sequence_id)
        if sequence is None:
            raise LookupError(f"Sequence '{sequence_id}' not found for this channel's theme")
        if not sequence["presets"]:
            raise ValueError(f"Sequence '{sequence['name']}' has no presets")
        return sequence

    def start_sequence(self, session: Session, sequence_id: str, glide: float = PRESET_GLIDE_SECONDS,
                       apply: bool = True) -> dict:
        """
        Run a sequence of the channel's theme from its first preset. On a
        stopped channel it is only set (it starts when the channel plays).
        With apply=False the channel is already on (or crossfading to) the
        first preset. Raises LookupError (unknown sequence) or ValueError
        (it has no presets).
        """
        sequence = self.check_sequence(session.theme_id, sequence_id)
        session.sequence_id = sequence_id
        if apply:
            self.manager._apply_preset(session, sequence["presets"][0], glide)
        else:
            session.preset_id = sequence["presets"][0]
        self._cancel_transitions(session)
        if self._live(session):
            run = self._sequences[session.id] = SequenceRun(
                sequence_id, session.theme_id, next_at=self.clock() + glide + self._hold(sequence))
            self._plan_transitions(session, run, glide)
        self.manager.note(session, f"Started: {sequence['name']}")
        self._publish("channel.changed", session, changes={"sequence_id": sequence_id,
                                                           "preset_id": session.preset_id})
        self._note_step(session, 1, len(sequence["presets"]), sequence["presets"][0])
        self.manager.state.save()
        logger.info(f"  Channel '{session.name}': sequence '{sequence['name']}' started "
                    f"({len(sequence['presets'])} presets, then {sequence['at_end']})")
        return sequence

    def stop_sequence(self, session: Session) -> bool:
        """Stop the channel's sequence; it stays on the preset it is on. True if it had one."""
        had = session.sequence_id is not None
        if had:
            sequence = self.sequence(session.theme_id, session.sequence_id)
            self.manager.note(session, f"Stopped: {sequence['name'] if sequence else session.sequence_id}")
        if self._sequences.pop(session.id, None) is not None:
            self._cancel_transitions(session)
        session.sequence_id = None
        if had:
            logger.info(f"  Channel '{session.name}': sequence stopped")
            self._publish("channel.changed", session, changes={"sequence_id": None})
        return had

    def _note_step(self, session: Session, step: int, steps: int, preset_id: Optional[str]) -> None:
        """A sequence step in the channel's recent changes (and a sequence.step event)."""
        self.manager.note(session, f"Step {step} of {steps}: {self.manager.preset_name(session.theme_id, preset_id)}")
        self._publish("sequence.step", session, theme_id=session.theme_id, sequence_id=session.sequence_id,
                      step=step, steps=steps, preset_id=preset_id)

    def _publish(self, event_type: str, session: Session, **data) -> None:
        publish = getattr(self.manager, "publish", None)
        if callable(publish):
            publish(event_type, session, **data)

    # --- Advanced transitions ---

    def _peek_next(self, session: Session, run: SequenceRun) -> Optional[tuple]:
        """The run's next step without taking it: (preset id, ramp, sequence), or None."""
        sequence = self.sequence(session.theme_id, run.sequence_id)
        if sequence is None or run.theme_id != session.theme_id:
            return None
        if run.single_pass:
            nxt = run.index + 1
            return (run.presets[nxt], float(run.ramp or 0.0), sequence) if nxt < len(run.presets) else None
        presets = sequence["presets"]
        count = len(presets)
        if not count:
            return None
        index = min(run.index, count - 1)
        nxt = index + run.direction
        if not 0 <= nxt < count:
            if sequence["at_end"] == "reverse" and count > 1:
                nxt = index - run.direction
            elif sequence["at_end"] == "restart":
                nxt = 0
            else:
                return None
        return presets[nxt], float(sequence["ramp"]), sequence

    def _plan_transitions(self, session: Session, run: SequenceRun, glide: float) -> None:
        """
        Plan the next step's advanced transitions on the channel: each track's
        starts its pre-fade before the step (never before the glide into the
        current preset has finished) and lasts pre-fade + ramp.
        """
        run.planned = None
        if not self._live(session) or run.next_at == NEVER:
            return
        peek = self._peek_next(session, run)
        if peek is None:
            return
        next_preset, ramp, sequence = peek
        transitions = sequence.get("transitions") or {}
        step_in = (transitions.get(next_preset) or {}).get("in")
        step_out = (transitions.get(session.preset_id) or {}).get("out") if session.preset_id else None
        run.planned = next_preset
        if not step_in and not step_out:
            return
        planner = getattr(self.manager, "transition_plans", None)
        if planner is None:
            return
        plans = planner(session, session.preset_id, next_preset, step_in, step_out)
        if not plans:
            return
        until_step = max(0.0, run.next_at - self.clock())
        for plan in plans:
            pre = min(plan["pre_fade"], max(0.0, until_step - glide))
            plan["delay"] = until_step - pre
            plan["window"] = pre + ramp
            plan["pre_fade_used"] = pre
        self.manager.schedule_transitions(session, plans)
        logger.info(f"Playback: Channel '{session.name}': {len(plans)} advanced transition(s) planned for "
                    f"the step to '{next_preset}' in {until_step:.0f}s")

    def _cancel_transitions(self, session: Session) -> None:
        cancel = getattr(self.manager, "cancel_transitions", None)
        if cancel is not None:
            cancel(session)

    def _check_planned(self, session: Session, run: SequenceRun, preset_id: str, glide: Optional[float]) -> None:
        """The step about to happen isn't the one planned (or is taken by hand): drop its transitions."""
        if run.planned is not None and (glide is not None or run.planned != preset_id):
            self._cancel_transitions(session)
        run.planned = None

    def _advance_pass(self, session: Session, run: SequenceRun, glide: Optional[float] = None) -> None:
        """A playlist entry's pass: the next preset, or nothing more after the last one."""
        nxt = run.index + 1
        if nxt >= len(run.presets):
            run.next_at = NEVER
            return
        ramp = run.ramp if glide is None else glide
        self._check_planned(session, run, run.presets[nxt], glide)
        run.index = nxt
        run.next_at = self.clock() + ramp + run.holds[nxt]
        self._note_step(session, nxt + 1, len(run.presets), run.presets[nxt])
        if run.presets[nxt] != session.preset_id:
            self.manager._apply_preset(session, run.presets[nxt], ramp, fade=ramp)
            logger.info(f"Playback: Channel '{session.name}': sequence '{run.sequence_id}' step {nxt + 1}/{len(run.presets)} "
                        f"'{run.presets[nxt]}' (glide {ramp:g}s, hold {run.holds[nxt]:.0f}s)")
        self._plan_transitions(session, run, ramp)

    def _advance_sequence(self, session: Session, run: SequenceRun, glide: Optional[float] = None) -> None:
        if run.single_pass and run.theme_id == session.theme_id:
            self._advance_pass(session, run, glide)
            return
        sequence = self.sequence(session.theme_id, run.sequence_id)
        if sequence is None or not sequence["presets"] or run.theme_id != session.theme_id:
            logger.info(f"  Channel '{session.name}': its sequence is gone, stopping it")
            self.stop_sequence(session)
            return
        presets = sequence["presets"]
        count = len(presets)
        run.index = min(run.index, count - 1)
        nxt = run.index + run.direction
        if not 0 <= nxt < count:
            if sequence["at_end"] == "reverse" and count > 1:
                run.direction = -run.direction
                nxt = run.index + run.direction
            elif sequence["at_end"] == "restart":
                run.direction = 1
                nxt = 0
            elif sequence["at_end"] == "stop":
                logger.info(f"  Channel '{session.name}': sequence '{sequence['name']}' finished on its last preset")
                self.manager.note(session, f"Stopped: {sequence['name']}")
                self._sequences.pop(session.id, None)
                session.sequence_id = None
                self._publish("channel.changed", session, changes={"sequence_id": None})
                return
            else:
                nxt = run.index  # one preset, reverse: nothing to change
        ramp = sequence["ramp"] if glide is None else glide
        self._check_planned(session, run, presets[nxt], glide)
        run.index = nxt
        run.next_at = self.clock() + ramp + self._hold(sequence)
        self._note_step(session, nxt + 1, count, presets[nxt])
        if presets[nxt] != session.preset_id:
            self.manager._apply_preset(session, presets[nxt], ramp, fade=ramp)
            logger.info(f"Playback: Channel '{session.name}': sequence '{sequence['name']}' step {nxt + 1}/{count} "
                        f"'{presets[nxt]}' (glide {ramp:g}s)")
        self._plan_transitions(session, run, ramp)

    # --- Playlists ---

    def set_playlist(self, session: Session, playlist_id: str) -> dict:
        """Play a playlist on the channel from its first entry. Raises LookupError if unknown, ValueError if empty."""
        playlist = self.playlist(playlist_id)
        if playlist is None:
            raise LookupError(f"Playlist '{playlist_id}' not found")
        count = len(playlist.get("entries") or [])
        if not count:
            raise ValueError(f"Playlist '{playlist.get('name', playlist_id)}' has no entries")
        session.playlist_id = playlist_id
        session.playlist_order = self._new_order(count, playlist.get("order") == "shuffle",
                                                 themes=self._themes_of(playlist))
        session.playlist_position = 0
        self._playlists.pop(session.id, None)
        if not self._enter_entry(session, playlist, tries=count):
            self.clear_playlist(session)
            self.manager.state.save()
            raise ValueError(f"Playlist '{playlist.get('name', playlist_id)}' has no playable entry")
        self.manager.note(session, f"Started: {playlist.get('name', playlist_id)}")
        self._publish("channel.changed", session, changes={"playlist_id": playlist_id})
        self._publish("playlist.started", session, playlist_id=playlist_id, playlist_name=playlist.get("name"))
        self._note_entry(session, playlist)
        self.manager.state.save()
        logger.info(f"  Channel '{session.name}': playlist '{playlist.get('name', playlist_id)}' started")
        return playlist

    def playlist_changed(self, playlist_id: str) -> None:
        """A playlist was edited: channels playing it start a new round if its entries changed."""
        playlist = self.playlist(playlist_id)
        if playlist is None:
            return
        for session in self._sessions():
            if session.playlist_id == playlist_id and not self._order_ok(session, playlist):
                session.playlist_order = self._new_order(len(playlist.get("entries") or []),
                                                         playlist.get("order") == "shuffle",
                                                         themes=self._themes_of(playlist))
                session.playlist_position = 0

    def clear_playlist(self, session: Session) -> bool:
        """
        Stop the channel's playlist; the channel keeps playing what it plays
        now. An entry's sequence pass carries on as an ordinary sequence.
        True if it had one.
        """
        had = session.playlist_id is not None
        had_id = session.playlist_id
        if had:
            self.manager.note(session, f"Stopped: {(self.playlist(session.playlist_id) or {}).get('name', session.playlist_id)}")
        self._playlists.pop(session.id, None)
        run = self._sequences.get(session.id)
        if run is not None and run.single_pass:
            run.presets = run.holds = run.ramp = None
            if run.next_at == NEVER:
                sequence = self.sequence(session.theme_id, run.sequence_id)
                run.next_at = self.clock() + (self._hold(sequence) if sequence else 0.0)
                self._plan_transitions(session, run, 0.0)
        session.playlist_id = None
        session.playlist_order = []
        session.playlist_position = 0
        if had:
            logger.info(f"  Channel '{session.name}': playlist stopped")
            self._publish("channel.changed", session, changes={"playlist_id": None})
            self._publish("playlist.stopped", session, playlist_id=had_id)
        return had

    def _entry(self, session: Session, playlist: dict) -> dict:
        return playlist["entries"][session.playlist_order[session.playlist_position]]

    def _note_entry(self, session: Session, playlist: dict) -> None:
        """The playlist entry the channel is on, in its recent changes."""
        theme = self.manager.get_theme(session.theme_id)
        self.manager.note(session, f"Entry {session.playlist_position + 1} of {len(playlist['entries'])}: "
                                   f"{theme.name if theme else session.theme_id}")
        self._publish("playlist.entry", session, playlist_id=session.playlist_id,
                      position=session.playlist_position + 1, entries=len(playlist["entries"]),
                      theme_id=session.theme_id)

    def _enter_entry(self, session: Session, playlist: dict, tries: int = 1) -> bool:
        """
        Play the entry at the channel's playlist position (crossfading if the
        theme changes). An entry whose theme is gone is skipped, up to `tries`
        entries. False if none could be played.
        """
        for _ in range(max(1, tries)):
            entry = self._entry(session, playlist)
            theme_id = entry.get("theme_id")
            if theme_id and self.manager.get_theme(theme_id):
                break
            logger.warning(f"  Channel '{session.name}': playlist entry theme '{theme_id}' not found, skipping it")
            if not self._step_position(session, playlist):
                return False
        else:
            return False

        sequence = self.sequence(theme_id, entry.get("sequence_id"))
        if sequence is not None and not sequence["presets"]:
            sequence = None
        preset_id = sequence["presets"][0] if sequence else entry.get("preset_id")
        self._cancel_transitions(session)
        self.manager._apply_theme(session, theme_id, preset_id)
        self._sequences.pop(session.id, None)
        session.sequence_id = sequence["id"] if sequence else None
        if self._live(session):
            self._start_entry_timers(session, playlist, sequence)
        return True

    def _plan_pass(self, session: Session, sequence: dict, playlist: dict) -> tuple[SequenceRun, float]:
        """
        One pass of an entry's sequence: each preset once, first to last. Each
        hold is drawn in the sequence's range; with the playlist's
        fit_sequences, holds and ramps are scaled so the pass lasts a play
        time drawn from play_min-play_max. Returns (run, length in seconds).
        """
        from sonorium.core.sequences import fit_pass
        presets = list(sequence["presets"])
        holds = [self._hold(sequence) for _ in presets]
        ramp = float(sequence["ramp"])
        if playlist.get("fit_sequences"):
            length = self._play_time(playlist)
            holds, ramp = fit_pass(holds, ramp, length)
        else:
            length = sum(holds) + (len(presets) - 1) * ramp
        run = SequenceRun(sequence["id"], session.theme_id, presets=presets, holds=holds, ramp=ramp)
        return run, length

    def _start_entry_timers(self, session: Session, playlist: dict, sequence: Optional[dict]) -> None:
        """Timers for the playlist entry the channel is on: its play time, or its sequence's pass."""
        now = self.clock()
        if sequence:
            run, length = self._plan_pass(session, sequence, playlist)
            run.next_at = now + run.holds[0] if len(run.presets) > 1 else NEVER
            self._sequences[session.id] = run
            self._plan_transitions(session, run, self._entry_glide())
        else:
            length = self._play_time(playlist)
        self._playlists[session.id] = PlaylistRun(session.playlist_id, next_at=now + length)

    def _entry_glide(self) -> float:
        """How long a playlist entry takes to come in (the theme crossfade)."""
        try:
            return float(self.manager.crossfade_seconds())
        except Exception:
            return PRESET_GLIDE_SECONDS

    def _step_position(self, session: Session, playlist: dict) -> bool:
        """Move to the next entry; at the end start a new round if it repeats. False at the end of a non-repeating playlist."""
        count = len(playlist.get("entries") or [])
        if not self._order_ok(session, playlist):
            session.playlist_order = self._new_order(count, playlist.get("order") == "shuffle",
                                                 themes=self._themes_of(playlist))
            session.playlist_position = 0
            return True
        session.playlist_position += 1
        if session.playlist_position < len(session.playlist_order):
            return True
        if not playlist.get("repeat", True):
            session.playlist_position = 0
            return False
        themes = self._themes_of(playlist)
        recent = [themes[i] for i in (session.playlist_order or [])[-2:] if 0 <= i < len(themes)]
        session.playlist_order = self._new_order(count, playlist.get("order") == "shuffle",
                                                 themes=themes, recent=recent)
        session.playlist_position = 0
        return True

    def _advance_playlist(self, session: Session) -> str:
        """Next playlist entry: 'changed', 'end' (a non-repeating playlist finished) or 'gone'."""
        playlist = self.playlist(session.playlist_id)
        if playlist is None or not playlist.get("entries"):
            logger.info(f"  Channel '{session.name}': its playlist is gone, stopping it")
            self.clear_playlist(session)
            return "gone"
        if not self._step_position(session, playlist):
            return "end"
        if not self._enter_entry(session, playlist, tries=len(playlist["entries"])):
            return "end"
        self._note_entry(session, playlist)
        entry = self._entry(session, playlist)
        logger.info(f"  Channel '{session.name}': playlist '{playlist.get('name')}' entry "
                    f"{session.playlist_order[session.playlist_position] + 1}/{len(playlist['entries'])} "
                    f"(theme {entry.get('theme_id')})")
        return "changed"

    # --- Hand controls ---

    def skip(self, session: Session) -> Optional[str]:
        """
        Next: the next playlist entry if a playlist runs, else the sequence's
        next preset (a quick glide). Returns 'playlist', 'sequence', 'end'
        (a non-repeating playlist was on its last entry) or None (nothing runs).
        """
        if session.playlist_id and session.id in self._playlists:
            result = self._advance_playlist(session)
            self.manager.state.save()
            return "end" if result == "end" else "playlist"
        run = self._sequences.get(session.id)
        if run is not None:
            self._cancel_transitions(session)
            run.planned = None
            self._advance_sequence(session, run, glide=PRESET_GLIDE_SECONDS)
            self.manager.state.save()
            return "sequence"
        return None

    # --- Channel lifecycle (called by the session manager) ---

    def before_play(self, session: Session) -> None:
        """Set the channel up for its playlist entry, or its sequence's first preset, before it starts."""
        if session.playlist_id:
            playlist = self.playlist(session.playlist_id)
            if playlist is None or not playlist.get("entries"):
                self.clear_playlist(session)
            else:
                if not self._order_ok(session, playlist):
                    session.playlist_order = self._new_order(len(playlist["entries"]), playlist.get("order") == "shuffle",
                                                             themes=self._themes_of(playlist))
                    session.playlist_position = 0
                if not self._enter_entry(session, playlist, tries=len(playlist["entries"])):
                    self.clear_playlist(session)
                return
        if session.sequence_id:
            sequence = self.sequence(session.theme_id, session.sequence_id)
            if sequence is None or not sequence["presets"]:
                self.stop_sequence(session)
            else:
                session.preset_id = sequence["presets"][0]

    def after_play(self, session: Session) -> None:
        """The channel started: start its timers from now."""
        now = self.clock()
        if session.playlist_id and session.id not in self._playlists:
            playlist = self.playlist(session.playlist_id)
            if playlist is not None:
                entry = self._entry(session, playlist) if playlist.get("entries") and self._order_ok(session, playlist) else {}
                sequence = None
                if (session.sequence_id and entry.get("sequence_id") == session.sequence_id
                        and entry.get("theme_id") == session.theme_id):
                    sequence = self.sequence(session.theme_id, session.sequence_id)
                    if sequence is not None and not sequence["presets"]:
                        sequence = None
                self._start_entry_timers(session, playlist, sequence)
                if sequence is not None:
                    return
        if session.sequence_id:
            sequence = self.sequence(session.theme_id, session.sequence_id)
            if sequence is not None and sequence["presets"]:
                run = self._sequences[session.id] = SequenceRun(session.sequence_id, session.theme_id,
                                                                next_at=now + self._hold(sequence))
                self._note_step(session, 1, len(sequence["presets"]), sequence["presets"][0])
                self._plan_transitions(session, run, self._entry_glide())

    def on_stop(self, session_id: str) -> None:
        """The channel stopped: its timers stop (its sequence and playlist stay set)."""
        self._sequences.pop(session_id, None)
        self._playlists.pop(session_id, None)

    forget = on_stop

    def running_sequence(self, session_id: str) -> Optional[SequenceRun]:
        return self._sequences.get(session_id)

    def running_playlist(self, session_id: str) -> Optional[PlaylistRun]:
        return self._playlists.get(session_id)

    # --- Timer ---

    async def tick(self) -> None:
        """Advance every sequence and playlist whose time has come."""
        now = self.clock()
        changed, ended = {}, []
        for session_id, run in list(self._playlists.items()):
            session = self._session(session_id)
            if session is None or not self._live(session):
                self.on_stop(session_id)
                continue
            if now >= run.next_at:
                if self._advance_playlist(session) == "end":
                    ended.append(session)
                changed[session_id] = session
        for session_id, run in list(self._sequences.items()):
            session = self._session(session_id)
            if session is None or not self._live(session):
                self._sequences.pop(session_id, None)
                continue
            if now >= run.next_at:
                self._advance_sequence(session, run)
                changed[session_id] = session
        if changed:
            self.manager.state.save()
        for session in ended:
            logger.info(f"  Channel '{session.name}': its playlist ended (no repeat), stopping the channel")
            await self.manager.stop(session.id, reason="ended")
        for session in changed.values():
            await self._notify(session)

    async def _notify(self, session: Session) -> None:
        if self.on_change:
            try:
                await self.on_change(session)
            except Exception as e:
                logger.warning(f"Could not update entities for '{session.name}': {e}")

    # --- Status ---

    def _iso(self, at: Optional[float]) -> Optional[str]:
        if at is None:
            return None
        return datetime.fromtimestamp(self.wall_clock() + (at - self.clock()), tz=timezone.utc).isoformat()

    def _seconds_left(self, at: Optional[float]) -> Optional[int]:
        return None if at is None else max(0, int(round(at - self.clock())))

    def status(self, session: Session) -> dict:
        """The channel's sequence and playlist, for the API and MQTT."""
        result = {"sequence": None, "playlist": None}
        if session.sequence_id:
            sequence = self.sequence(session.theme_id, session.sequence_id)
            run = self._sequences.get(session.id)
            presets = self.manager._theme_presets(session.theme_id) if session.theme_id else {}
            current = session.preset_id
            change_at = run.next_at if run else None
            if change_at == NEVER:  # a pass's last preset: the playlist moves on next
                playlist_run = self._playlists.get(session.id)
                change_at = playlist_run.next_at if playlist_run else None
            if run is not None and run.single_pass:
                steps = len(run.presets)
            else:
                steps = len(sequence["presets"]) if sequence else 0
            result["sequence"] = {
                "id": session.sequence_id,
                "name": sequence["name"] if sequence else session.sequence_id,
                "running": run is not None,
                "single_pass": bool(run is not None and run.single_pass),
                "step": (run.index + 1) if run else None,
                "steps": steps,
                "at_end": sequence["at_end"] if sequence else None,
                "current_preset_id": current,
                "current_preset_name": (presets.get(current) or {}).get("name", current) if current else None,
                "next_change_at": self._iso(change_at),
                "seconds_until_change": self._seconds_left(change_at),
            }
        if session.playlist_id:
            playlist = self.playlist(session.playlist_id) or {}
            run = self._playlists.get(session.id)
            entries = playlist.get("entries") or []
            entry_index = None
            if self._order_ok(session, playlist) if entries else False:
                entry_index = session.playlist_order[session.playlist_position]
            entry = entries[entry_index] if entry_index is not None else {}
            theme = self.manager.get_theme(entry.get("theme_id")) if entry.get("theme_id") else None
            result["playlist"] = {
                "id": session.playlist_id,
                "name": playlist.get("name", session.playlist_id),
                "running": run is not None,
                "entry_index": entry_index,
                "position": session.playlist_position,
                "entries": len(entries),
                "theme_id": entry.get("theme_id"),
                "theme_name": theme.name if theme else None,
                "next_change_at": self._iso(run.next_at) if run else None,
                "seconds_until_change": self._seconds_left(run.next_at) if run else None,
            }
        return result
