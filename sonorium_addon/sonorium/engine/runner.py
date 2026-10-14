"""
Sequences and playlists in the engine: the server's own runner
(core/playback_runner.PlaybackRunner) driven by the engine's audio clock.

The runner takes its clock and random numbers from its caller and talks to a
"manager" for everything else (the theme's presets and sequences, applying a
preset or a theme to the channel, advanced transitions). EngineHost is that
manager for one engine: the same calls the session manager answers, done on
the engine's own channel. The runner's timers are checked before every mixed
block, so steps and entries change on block boundaries, counted in audio
mixed, never on the wall clock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from sonorium.core.playback_runner import NEVER, PRESET_GLIDE_SECONDS, PlaybackRunner

if TYPE_CHECKING:
    from sonorium.engine.engine import Engine

# How many of the channel's recent changes status() keeps
RECENT_CHANGES = 20


@dataclass
class EngineSession:
    """The engine's one channel, as the runner sees a channel (the fields of core/state.Session it reads)."""
    id: str = "engine"
    name: str = "Engine"
    theme_id: Optional[str] = None
    preset_id: Optional[str] = None
    sequence_id: Optional[str] = None
    playlist_id: Optional[str] = None
    playlist_position: int = 0
    playlist_order: list = field(default_factory=list)
    is_playing: bool = True
    recent: list = field(default_factory=list)
    owner: Optional[str] = None


class _State:
    """What the runner reads of the server's state: the channel and the playlists (nothing is saved)."""

    def __init__(self, session: EngineSession):
        self.sessions = {session.id: session}
        self.playlists: dict = {}

    def save(self) -> None:
        pass


class EngineHost:
    """The session manager's part the runner calls, for one engine."""

    def __init__(self, engine: Engine, session: EngineSession):
        self.engine = engine
        self.session = session
        self.state = _State(session)

    # --- themes ---

    def get_theme(self, theme_id: Optional[str]):
        folder = self.engine._folder_for(theme_id)
        return folder.theme if folder is not None else None

    def _theme_presets(self, theme_id: Optional[str]) -> dict:
        folder = self.engine._folder_for(theme_id)
        return dict(folder.presets) if folder is not None else {}

    def theme_sequences(self, theme_id: Optional[str]) -> tuple[dict, dict]:
        return self.engine._sequences_of(theme_id)

    def preset_for_theme(self, theme_id: Optional[str], requested: Optional[str] = None) -> Optional[str]:
        """As session_manager.preset_for_theme."""
        if requested == "" or not theme_id:
            return None
        presets = self._theme_presets(theme_id)
        if requested and requested in presets:
            return requested
        for preset_id, data in presets.items():
            if (data or {}).get("is_default"):
                return preset_id
        return None

    def preset_name(self, theme_id: Optional[str], preset_id: Optional[str]) -> str:
        if not preset_id:
            return "Default settings"
        return (self._theme_presets(theme_id).get(preset_id) or {}).get("name", preset_id)

    def crossfade_seconds(self) -> float:
        return self.engine.crossfade_seconds

    # --- the channel ---

    def _live(self, session: EngineSession) -> bool:
        return session.is_playing

    def _find(self, session_id):
        return self.session if session_id == self.session.id else None

    def _all_sessions(self) -> list:
        return [self.session]

    def session_overrides(self, session: EngineSession, preset_id: Optional[str] = None) -> dict:
        """What the channel plays: its preset, with its sequence step's While playing values on top."""
        preset_id = preset_id if preset_id is not None else session.preset_id
        return self.engine._overrides_for(session.theme_id, preset_id, self.step_layer(session, preset_id))

    def step_layer(self, session: EngineSession, preset_id: Optional[str] = None) -> Optional[dict]:
        """The While playing layer of the step the channel is on (the engine's own `step` when no sequence runs)."""
        preset_id = preset_id if preset_id is not None else session.preset_id
        if not session.sequence_id:
            return self.engine._manual_step
        if not preset_id:
            return None
        sequence = self.engine._runner.sequence(session.theme_id, session.sequence_id)
        return ((sequence or {}).get("while") or {}).get(preset_id)

    def _apply_preset(self, session: EngineSession, preset_id: Optional[str], glide: float,
                      fade: Optional[float] = None) -> None:
        """As session_manager._apply_preset: glide to a preset of the channel's theme."""
        session.preset_id = preset_id or None
        if session.theme_id:
            self.engine._channel.set_track_overrides(self.session_overrides(session),
                                                     glide_seconds=glide, fade_seconds=fade)

    def _apply_theme(self, session: EngineSession, theme_id: str, preset_id: Optional[str]) -> None:
        """As session_manager._apply_theme: a new theme crossfades in, the same theme glides to the preset."""
        if theme_id == session.theme_id:
            self._apply_preset(session, self.preset_for_theme(theme_id, preset_id), PRESET_GLIDE_SECONDS)
            return
        session.theme_id = theme_id
        session.preset_id = self.preset_for_theme(theme_id, preset_id)
        self.engine._crossfade_to(theme_id)

    def transition_plans(self, session: EngineSession, from_preset: Optional[str], to_preset: str,
                         step_in: Optional[dict], step_out: Optional[dict]) -> list:
        """As session_manager.transition_plans (core/transitions.step_plans on the channel's theme)."""
        from sonorium.core.transitions import step_plans
        from sonorium.recording import GROUP_MODE_MERRY_GO_ROUND, TrackView, group_mode
        theme = self.get_theme(session.theme_id) if session.theme_id else None
        if theme is None:
            return []
        groups = getattr(theme, "groups", None) or {}

        def bed(instance):
            group = getattr(instance.meta, "group", None)
            return bool(group) and group_mode(groups.get(group)) == GROUP_MODE_MERRY_GO_ROUND

        return step_plans(list(theme.tracks),
                          self.session_overrides(session, from_preset) if from_preset else {},
                          self.session_overrides(session, to_preset),
                          step_in, step_out, to_preset, from_preset or "",
                          lambda instance, overrides: TrackView(instance, overrides), skip=bed)

    def schedule_transitions(self, session: EngineSession, plans: list) -> None:
        self.engine._channel.schedule_transitions(plans)

    def cancel_transitions(self, session: EngineSession) -> None:
        self.engine._channel.cancel_transitions()

    # --- what the server shows or sends elsewhere ---

    def note(self, session: EngineSession, text: str) -> None:
        """The channel's recent changes, timed in seconds of audio mixed."""
        recent = session.recent
        if recent and recent[-1]["text"] == text:
            return
        recent.append({"at_seconds": round(self.engine._mix_seconds(), 3), "text": text})
        del recent[:-RECENT_CHANGES]

    def publish(self, event_type: str, session: EngineSession, **data) -> None:
        pass

    async def stop(self, session_id: str, reason: str = "stopped") -> bool:
        """A playlist that doesn't repeat has ended: the mix fades out (as the server stops the channel)."""
        self.engine._end(reason)
        return True


class EngineRunner(PlaybackRunner):
    """The server's runner, plus when its next timer is due (the engine mixes up to it)."""

    def next_due(self) -> float:
        """When the next timer (a step or an entry) is due, in the runner's clock; inf if none runs."""
        due = NEVER
        for run in self._sequences.values():
            due = min(due, run.next_at)
        for run in self._playlists.values():
            due = min(due, run.next_at)
        return due


def drive(coroutine) -> None:
    """Run one of the runner's coroutines to its end (with the engine's host it never waits on anything)."""
    try:
        coroutine.send(None)
    except StopIteration:
        return
    coroutine.close()
    raise RuntimeError("The engine's runner waited on something; it must not")
