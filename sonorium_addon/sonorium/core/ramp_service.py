"""
Volume ramps on channels (the gain itself is core/ramps.py): an alarm that
gets louder over 20 minutes, a sleep timer that fades out over an hour.

    service.start("bedroom", to_level=1.0, seconds=1200, from_level=0.0, curve="db")
    service.start("bedroom", to_level=0.0, seconds=3600, then="stop")
    service.cancel("bedroom", reset=False)

- The channel must be playing (its mix running), unless start=True: then it
  is played first and the ramp begins as it starts.
- then="stop" or "pause": when the ramp finishes, the channel is stopped or
  paused (a sleep timer).
- Events: ramp.started, ramp.finished, ramp.cancelled (core events catalogue).
  A ramp ends without an event when its channel stops; starting another ramp
  on the channel replaces it (ramp.cancelled, reason "replaced").
"""
from __future__ import annotations

import asyncio
import secrets
from typing import Callable, Optional

from sonorium.core.ramps import ChannelRamp, RampError, check
from sonorium.obs import logger

THEN = (None, "stop", "pause")


class RampService:
    def __init__(self, session_manager, publish: Optional[Callable] = None, rate: int = 44100,
                 on_change: Optional[Callable] = None):
        self.sessions = session_manager
        self.publish = publish or (lambda *a, **k: None)
        self.rate = rate
        self.on_change = on_change      # async callback(session) after a channel was stopped by a ramp
        self._ramps: dict[str, dict] = {}   # session id -> {"id", "then", "loop", "channel"}

    def _session(self, channel_id: str):
        session = self.sessions.get(channel_id) if self.sessions is not None and isinstance(channel_id, str) else None
        if session is None:
            raise LookupError(f"No channel '{channel_id}'")
        return session

    def _ramp_of(self, channel) -> ChannelRamp:
        if channel.ramp is None:
            channel.ramp = ChannelRamp(self.rate)
        channel.ramp.on_done = self._done
        return channel.ramp

    async def start(self, channel_id: str, to_level: float, seconds: float, from_level: Optional[float] = None,
                    curve: str = "db", then: Optional[str] = None, start: bool = False,
                    origin: str = "sonorium") -> dict:
        """Start a ramp; RampError for bad values, LookupError for an unknown channel."""
        check(0.0 if from_level is None else from_level, to_level, seconds, curve)
        if then not in THEN:
            raise RampError("'then' must be stop, pause or nothing")
        session = self._session(channel_id)
        channel = self.sessions.channel_of(session.id)
        from sonorium.core.channel import ChannelState
        if channel is None or channel.state != ChannelState.PLAYING:
            if not start:
                raise RampError("The channel is not playing")
            if not await self.sessions.play(session.id):
                raise RampError("The channel could not start (see the log)")
            channel = self.sessions.channel_of(session.id)
            if channel is None:
                raise RampError("The channel could not start (see the log)")
        ramp = self._ramp_of(channel)
        old = self._ramps.get(session.id)
        if old is not None and ramp.active:
            self._event("ramp.cancelled", session, old, reason="replaced", level=ramp.level)
        ramp_id = "r_" + secrets.token_hex(5)
        info = ramp.start(ramp_id, from_level, float(to_level), float(seconds), curve)
        record = {"id": ramp_id, "then": then, "loop": asyncio.get_running_loop(), "channel": channel,
                  "session_id": session.id, "info": info, "origin": origin}
        self._ramps[session.id] = record
        self._event("ramp.started", session, record, **{"from": info["from"], "to": info["to"],
                                                        "seconds": info["seconds"], "curve": curve, "then": then})
        logger.info(f"  '{session.name}': ramp {info['from']:g} -> {info['to']:g} over {info['seconds']:g}s ({curve})")
        if info["seconds"] == 0:
            self._done(ramp_id, "finished")
        return {"ramp_id": ramp_id, "channel_id": session.id, **{k: info[k] for k in ("from", "to", "seconds")},
                "curve": curve, "then": then}

    def _event(self, kind: str, session, record: dict, **data) -> None:
        self.publish(kind, {"channel_id": session.id, "name": session.name, "ramp_id": record["id"], **data})

    def _done(self, ramp_id: str, reason: str) -> None:
        """A ramp finished (called from the channel's generator thread)."""
        record = next((r for r in self._ramps.values() if r["id"] == ramp_id), None)
        if record is None:
            return
        self._ramps.pop(record["session_id"], None)
        session = self.sessions.get(record["session_id"])
        if session is None:
            return
        self._event("ramp.finished", session, record, level=record["info"]["to"], then=record["then"])
        if record["then"]:
            asyncio.run_coroutine_threadsafe(self._then(session.id, record["then"]), record["loop"])

    async def _then(self, session_id: str, then: str) -> None:
        try:
            if then == "stop":
                await self.sessions.stop(session_id)
            else:
                await self.sessions.pause(session_id)
            session = self.sessions.get(session_id)
            if session is not None and self.on_change is not None:
                await self.on_change(session)
        except Exception as e:
            logger.warning(f"Ramp: could not {then} the channel: {e}")

    def cancel(self, channel_id: str, reset: bool = False) -> Optional[dict]:
        """Stop the channel's ramp: hold its level, or (reset) back to full. None if none runs."""
        session = self._session(channel_id)
        record = self._ramps.pop(session.id, None)
        channel = self.sessions.channel_of(session.id)
        if record is None or channel is None or channel.ramp is None:
            if reset and channel is not None and channel.ramp is not None:
                channel.ramp.cancel(reset=True)
            return None
        level = channel.ramp.cancel(reset=reset)
        self._event("ramp.cancelled", session, record, reason="cancelled", level=level)
        return {"ramp_id": record["id"], "level": level, "reset": reset}

    def status(self, channel_id: str) -> dict:
        """The channel's ramp now: {"level", "ramp": {...} or None}."""
        session = self._session(channel_id)
        channel = self.sessions.channel_of(session.id)
        ramp = channel.ramp if channel is not None else None
        if ramp is None:
            return {"channel_id": session.id, "level": 1.0, "ramp": None}
        progress = ramp.progress()
        record = self._ramps.get(session.id)
        if progress is not None and record is not None:
            progress["then"] = record["then"]
        return {"channel_id": session.id, "level": round(ramp.level, 4), "ramp": progress}
