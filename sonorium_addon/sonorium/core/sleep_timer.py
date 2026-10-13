"""
Sleep timers on channels: after a set time (up to 4 hours) a playing channel
fades out (the normal stop, over the Crossfade setting) and stops.

    timers.set("bedroom", minutes=45)     # replaces a running timer
    timers.cancel("bedroom")
    timers.status(session)                # {"sleep_until", "seconds_left"}

- Only a playing channel takes a timer. Stopping or pausing the channel any
  way (Stop, idle, a playlist's end) drops its timer.
- The end time is saved with the channel (Session.sleep_until, UTC ISO
  time), so the web UI and the app see it after a reload.
- A server restart: a channel that plays again after a requested restart
  (core/restart_resume.py) keeps its timer and stops at the same time; one
  whose time ran out meanwhile is not played again. Every other channel
  starts stopped, so its timer is dropped.
- Event "channel.sleep_timer" (the events catalogue) on every change;
  the stop at the end is "channel.stopped" with reason "sleep".

tick() runs every second from the background loop (core/background.py).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

from sonorium.obs import logger

MAX_MINUTES = 240  # 4 hours


class SleepError(ValueError):
    """A bad value (400) or a channel that isn't playing (409)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def parse_until(value) -> Optional[float]:
    """Session.sleep_until as a timestamp (None when unset or unreadable)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def check_minutes(minutes) -> float:
    """Minutes as a number, more than 0 and at most MAX_MINUTES; SleepError otherwise."""
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes != minutes:
        raise SleepError("'minutes' must be a number")
    if not 0 < minutes <= MAX_MINUTES:
        raise SleepError(f"'minutes' must be more than 0 and at most {MAX_MINUTES} (4 hours)")
    return float(minutes)


class SleepTimers:
    def __init__(self, session_manager, clock: Callable[[], float] = time.time):
        self.sessions = session_manager
        self._clock = clock
        self._held: set[str] = set()     # channels about to play again after a restart
        self._ending: set[str] = set()   # channels stopping because their timer ran out
        self.on_stopped = None           # async callback(session) after a timer stopped a channel

    # --- Reading ---

    def status(self, session) -> dict:
        until = parse_until(getattr(session, "sleep_until", None))
        if until is None:
            return {"sleep_until": None, "seconds_left": None}
        return {"sleep_until": session.sleep_until, "seconds_left": max(0, int(round(until - self._clock())))}

    # --- Changing ---

    def _session(self, session_id: str):
        session = self.sessions.get(session_id) if isinstance(session_id, str) else None
        if session is None:
            raise LookupError(f"No channel '{session_id}'")
        return session

    def set(self, session_id: str, minutes) -> dict:
        """Start (or change) the channel's timer. LookupError, SleepError (400, or 409: not playing)."""
        minutes = check_minutes(minutes)
        session = self._session(session_id)
        self.sessions.guard("stop", session)
        if not session.is_playing:
            raise SleepError("The channel is not playing", status=409)
        session.sleep_until = _iso(self._clock() + minutes * 60)
        self.sessions.state.save()
        self._publish(session, "set")
        logger.info(f"  '{session.name}': sleep timer {minutes:g} min (until {session.sleep_until})")
        return self.status(session)

    def cancel(self, session_id: str, reason: str = "cancelled") -> bool:
        """Drop the channel's timer. False when it had none. LookupError for an unknown channel."""
        session = self._session(session_id)
        if reason == "cancelled":
            self.sessions.guard("stop", session)
        return self._clear(session, reason)

    def _clear(self, session, reason: str, save: bool = True) -> bool:
        if not getattr(session, "sleep_until", None):
            return False
        session.sleep_until = None
        if save:
            self.sessions.state.save()
        self._publish(session, reason)
        if reason != "set":
            logger.info(f"  '{session.name}': sleep timer {reason}")
        return True

    def _publish(self, session, reason: str) -> None:
        self.sessions.publish("channel.sleep_timer", session, reason=reason, **self.status(session))

    # --- A restart (core/restart_resume.py) ---

    def after_start(self, resuming: Iterable[str]) -> set[str]:
        """
        At start-up: keep the timers of the channels that play again after a
        requested restart (held until resume_done), drop every other one.
        Returns the channel ids whose time ran out meanwhile (not to be played again).
        """
        resuming = set(resuming)
        expired = set()
        now = self._clock()
        changed = False
        for session in list(self.sessions.state.sessions.values()):
            until = parse_until(getattr(session, "sleep_until", None))
            if until is None:
                if getattr(session, "sleep_until", None):  # unreadable
                    session.sleep_until = None
                    changed = True
                continue
            if session.id in resuming and until > now:
                self._held.add(session.id)
                continue
            if session.id in resuming:
                expired.add(session.id)
            session.sleep_until = None
            changed = True
        if changed:
            self.sessions.state.save()
        return expired

    def resume_done(self) -> None:
        """The channels to play again have started (or given up): their timers apply as usual."""
        self._held.clear()

    # --- Every second ---

    async def tick(self) -> None:
        now = self._clock()
        for session in list(self.sessions.state.sessions.values()):
            if not getattr(session, "sleep_until", None) or session.id in self._ending:
                continue
            until = parse_until(session.sleep_until)
            if not session.is_playing:
                if session.id not in self._held:
                    self._clear(session, "stopped")  # stopped another way: the timer goes with it
                continue
            if until is not None and until > now:
                continue
            self._held.discard(session.id)
            self._clear(session, "ended")
            self._ending.add(session.id)
            asyncio.get_running_loop().create_task(self._stop(session))

    async def _stop(self, session) -> None:
        """The timer ran out: the channel fades out and stops (in the background: the fade takes a while)."""
        try:
            await self.sessions.stop(session.id, reason="sleep")
            if self.on_stopped is not None:
                await self.on_stopped(session)
        except Exception as e:
            logger.warning(f"Sleep timer: could not stop '{session.name}': {e}")
        finally:
            self._ending.discard(session.id)
