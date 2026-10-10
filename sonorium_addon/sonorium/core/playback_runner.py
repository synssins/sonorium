"""
Runs sequences and playlists on channels (docs/PLAYBACK.md).

- A **sequence** steps through presets of the channel's theme: each preset
  holds for a random time in [hold_min, hold_max] (drawn fresh each step),
  then the channel glides to the next one over the ramp time. At the end it
  goes back in reverse, restarts, or stops on the last preset.
- A **playlist** plays its entries (a theme, plus a preset or a sequence of
  it) one after another, each for a random time in [play_min, play_max],
  in order or shuffled (never the same entry twice in a row), then repeats
  or stops.

Times are counted on the monotonic wall clock. Only playing channels run:
stopping a channel stops its timers (the sequence and playlist it has are
kept, so playing again starts the sequence from its first preset and
carries on with the playlist entry it was on).

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
            **timing,
        }

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

    def _new_order(self, count: int, shuffle: bool, last: Optional[int] = None) -> list[int]:
        """Entry indexes for one round: in order, or shuffled without repeating the last one played."""
        order = list(range(count))
        if shuffle and count > 1:
            self.rng.shuffle(order)
            if last is not None and order[0] == last:
                swap = self.rng.randrange(1, count)
                order[0], order[swap] = order[swap], order[0]
        return order

    def _order_ok(self, session: Session, playlist: dict) -> bool:
        count = len(playlist.get("entries") or [])
        order = session.playlist_order or []
        return sorted(order) == list(range(count)) and 0 <= session.playlist_position < count

    # --- Sequences ---

    def start_sequence(self, session: Session, sequence_id: str, glide: float = PRESET_GLIDE_SECONDS) -> dict:
        """
        Run a sequence of the channel's theme from its first preset. On a
        stopped channel it is only set (it starts when the channel plays).
        Raises LookupError (unknown sequence) or ValueError (it has no presets).
        """
        sequence = self.sequence(session.theme_id, sequence_id)
        if sequence is None:
            raise LookupError(f"Sequence '{sequence_id}' not found for this channel's theme")
        if not sequence["presets"]:
            raise ValueError(f"Sequence '{sequence['name']}' has no presets")
        session.sequence_id = sequence_id
        self.manager._apply_preset(session, sequence["presets"][0], glide)
        if session.is_playing:
            self._sequences[session.id] = SequenceRun(
                sequence_id, session.theme_id, next_at=self.clock() + glide + self._hold(sequence))
        self.manager.state.save()
        logger.info(f"  Channel '{session.name}': sequence '{sequence['name']}' started "
                    f"({len(sequence['presets'])} presets, then {sequence['at_end']})")
        return sequence

    def stop_sequence(self, session: Session) -> bool:
        """Stop the channel's sequence; it stays on the preset it is on. True if it had one."""
        had = session.sequence_id is not None
        self._sequences.pop(session.id, None)
        session.sequence_id = None
        if had:
            logger.info(f"  Channel '{session.name}': sequence stopped")
        return had

    def _advance_sequence(self, session: Session, run: SequenceRun, glide: Optional[float] = None) -> None:
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
                self._sequences.pop(session.id, None)
                session.sequence_id = None
                return
            else:
                nxt = run.index  # one preset, reverse: nothing to change
        ramp = sequence["ramp"] if glide is None else glide
        run.index = nxt
        run.next_at = self.clock() + ramp + self._hold(sequence)
        if presets[nxt] != session.preset_id:
            self.manager._apply_preset(session, presets[nxt], ramp)
            logger.info(f"  Channel '{session.name}': sequence '{sequence['name']}' step {nxt + 1}/{count} "
                        f"'{presets[nxt]}' (glide {ramp:g}s)")

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
        session.playlist_order = self._new_order(count, playlist.get("order") == "shuffle")
        session.playlist_position = 0
        self._playlists.pop(session.id, None)
        if not self._enter_entry(session, playlist, tries=count):
            self.clear_playlist(session)
            self.manager.state.save()
            raise ValueError(f"Playlist '{playlist.get('name', playlist_id)}' has no playable entry")
        self.manager.state.save()
        logger.info(f"  Channel '{session.name}': playlist '{playlist.get('name', playlist_id)}' started")
        return playlist

    def playlist_changed(self, playlist_id: str) -> None:
        """A playlist was edited: channels playing it start a new round if its entries changed."""
        playlist = self.playlist(playlist_id)
        if playlist is None:
            return
        for session in self.manager.state.sessions.values():
            if session.playlist_id == playlist_id and not self._order_ok(session, playlist):
                session.playlist_order = self._new_order(len(playlist.get("entries") or []),
                                                         playlist.get("order") == "shuffle")
                session.playlist_position = 0

    def clear_playlist(self, session: Session) -> bool:
        """Stop the channel's playlist; the channel keeps playing what it plays now. True if it had one."""
        had = session.playlist_id is not None
        self._playlists.pop(session.id, None)
        session.playlist_id = None
        session.playlist_order = []
        session.playlist_position = 0
        if had:
            logger.info(f"  Channel '{session.name}': playlist stopped")
        return had

    def _entry(self, session: Session, playlist: dict) -> dict:
        return playlist["entries"][session.playlist_order[session.playlist_position]]

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
        self.manager._apply_theme(session, theme_id, preset_id)
        self._sequences.pop(session.id, None)
        session.sequence_id = sequence["id"] if sequence else None
        if session.is_playing:
            now = self.clock()
            if sequence:
                self._sequences[session.id] = SequenceRun(sequence["id"], theme_id, next_at=now + self._hold(sequence))
            self._playlists[session.id] = PlaylistRun(session.playlist_id, next_at=now + self._play_time(playlist))
        return True

    def _step_position(self, session: Session, playlist: dict) -> bool:
        """Move to the next entry; at the end start a new round if it repeats. False at the end of a non-repeating playlist."""
        count = len(playlist.get("entries") or [])
        if not self._order_ok(session, playlist):
            session.playlist_order = self._new_order(count, playlist.get("order") == "shuffle")
            session.playlist_position = 0
            return True
        session.playlist_position += 1
        if session.playlist_position < len(session.playlist_order):
            return True
        if not playlist.get("repeat", True):
            session.playlist_position = 0
            return False
        last = session.playlist_order[-1] if session.playlist_order else None
        session.playlist_order = self._new_order(count, playlist.get("order") == "shuffle", last)
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
                    session.playlist_order = self._new_order(len(playlist["entries"]), playlist.get("order") == "shuffle")
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
                self._playlists[session.id] = PlaylistRun(session.playlist_id, next_at=now + self._play_time(playlist))
        if session.sequence_id:
            sequence = self.sequence(session.theme_id, session.sequence_id)
            if sequence is not None and sequence["presets"]:
                self._sequences[session.id] = SequenceRun(session.sequence_id, session.theme_id,
                                                          next_at=now + self._hold(sequence))

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
            session = self.manager.state.sessions.get(session_id)
            if session is None or not session.is_playing:
                self.on_stop(session_id)
                continue
            if now >= run.next_at:
                if self._advance_playlist(session) == "end":
                    ended.append(session)
                changed[session_id] = session
        for session_id, run in list(self._sequences.items()):
            session = self.manager.state.sessions.get(session_id)
            if session is None or not session.is_playing:
                self._sequences.pop(session_id, None)
                continue
            if now >= run.next_at:
                self._advance_sequence(session, run)
                changed[session_id] = session
        if changed:
            self.manager.state.save()
        for session in ended:
            logger.info(f"  Channel '{session.name}': its playlist ended (no repeat), stopping the channel")
            await self.manager.stop(session.id)
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
            result["sequence"] = {
                "id": session.sequence_id,
                "name": sequence["name"] if sequence else session.sequence_id,
                "running": run is not None,
                "step": (run.index + 1) if run else None,
                "steps": len(sequence["presets"]) if sequence else 0,
                "at_end": sequence["at_end"] if sequence else None,
                "current_preset_id": current,
                "current_preset_name": (presets.get(current) or {}).get("name", current) if current else None,
                "next_change_at": self._iso(run.next_at) if run else None,
                "seconds_until_change": self._seconds_left(run.next_at) if run else None,
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
