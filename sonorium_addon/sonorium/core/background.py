"""
Sonorium's background loop.

- Every second: moves sequences and playlists on (core/playback_runner.py)
  and stops channels whose sleep timer ran out (core/sleep_timer.py).
- Every 10 seconds: stops sessions nobody is listening to any more (#29).

It replaced the theme cycle manager: theme cycling became playlists
(core/playlists.migrate_cycling converts old cycling settings).
"""

from __future__ import annotations

import asyncio
from typing import Optional, TYPE_CHECKING

from sonorium.obs import logger

if TYPE_CHECKING:
    from sonorium.core.session_manager import SessionManager


class BackgroundLoop:
    """Runs the playback timers and the idle-session check."""

    def __init__(self, session_manager: SessionManager = None, tick_interval: float = 1.0,
                 idle_check_interval: float = 10.0):
        self.session_manager = session_manager
        self.tick_interval = tick_interval
        self.idle_check_interval = idle_check_interval
        self._task: Optional[asyncio.Task] = None
        self._running = False

        # Optional async callback(session) when an idle session is stopped,
        # so MQTT entities can reflect it
        self.on_session_stopped = None

    def set_session_manager(self, session_manager: SessionManager):
        self.session_manager = session_manager

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.debug("BackgroundLoop: started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("BackgroundLoop: stopped")

    async def _loop(self):
        loop = asyncio.get_running_loop()
        next_idle_check = loop.time() + self.idle_check_interval
        while self._running:
            try:
                if self.session_manager:
                    await self.session_manager.runner.tick()
            except Exception as e:
                logger.error(f"BackgroundLoop: error running sequences/playlists: {e}")
            try:
                sleep = getattr(self.session_manager, "sleep", None)
                if sleep is not None:
                    sleep.on_stopped = self.on_session_stopped
                    await sleep.tick()
            except Exception as e:
                logger.error(f"BackgroundLoop: error running sleep timers: {e}")

            if loop.time() >= next_idle_check:
                next_idle_check = loop.time() + self.idle_check_interval
                try:
                    await self._release_idle_sessions()
                except Exception as e:
                    logger.error(f"BackgroundLoop: error releasing idle sessions: {e}")

            await asyncio.sleep(self.tick_interval)

    async def _release_idle_sessions(self):
        """Stop sessions nobody is listening to any more (#29)."""
        if not self.session_manager:
            return
        for session in self.session_manager.release_idle_sessions():
            if self.on_session_stopped:
                await self.on_session_stopped(session)
