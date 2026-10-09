"""
Network speaker service (standalone mode): owns discovery and streaming,
scans in the background (at startup, then periodically) and plays to
network speakers by their Sonorium ID (net:<type>:<device id>).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Optional

from sonorium.obs import logger
from sonorium.network.discovery import NetworkSpeakerDiscovery
from sonorium.network.models import NetworkSpeaker
from sonorium.network.streaming import NetworkStreamingManager

# Background rescans skip the LinkPlay subnet probe; startup and manual
# refreshes run the full scan.
DEFAULT_REFRESH_INTERVAL = 600  # seconds
DEFAULT_DISCOVERY_TIMEOUT = 10.0


class NetworkSpeakerService:

    def __init__(
        self,
        config_dir: Path,
        discovery: NetworkSpeakerDiscovery = None,
        streaming: NetworkStreamingManager = None,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
        discovery_timeout: float = DEFAULT_DISCOVERY_TIMEOUT,
    ):
        self.discovery = discovery if discovery is not None else NetworkSpeakerDiscovery(config_dir)
        self.streaming = streaming if streaming is not None else NetworkStreamingManager()
        self.refresh_interval = refresh_interval
        self.discovery_timeout = discovery_timeout
        # Called (no arguments) after each scan, e.g. to update the speaker hierarchy
        self.on_change: Optional[Callable[[], Any]] = None
        self._scan_task: Optional[asyncio.Task] = None
        self._loop_task: Optional[asyncio.Task] = None
        self._last_available: Optional[set[str]] = None

    # --- Speakers ---

    @property
    def speakers(self) -> dict[str, NetworkSpeaker]:
        return self.discovery.speakers

    def get_speaker(self, speaker_id: str) -> Optional[NetworkSpeaker]:
        return self.discovery.get_speaker(speaker_id)

    def hierarchy_speakers(self) -> list[dict]:
        """Known speakers as speaker-hierarchy entries (ha.registry.Speaker fields)."""
        return [
            {"entity_id": s.id, "name": s.display_name, "ip_address": s.host or None}
            for s in self.discovery.speakers.values()
        ]

    # --- Discovery ---

    def start(self):
        """Start background discovery (returns immediately)."""
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.get_running_loop().create_task(self._run())
            logger.debug(f"Network speaker discovery scheduled (rescan every {int(self.refresh_interval)}s)")

    async def _run(self):
        await self.discover(full=True)
        while True:
            await asyncio.sleep(self.refresh_interval)
            await self.discover(full=False)

    async def discover(self, full: bool = True) -> int:
        """Scan now, or wait for the scan already running. Returns the number of known speakers."""
        if self._scan_task is None or self._scan_task.done():
            self._scan_task = asyncio.get_running_loop().create_task(self._scan(full))
        # shield: a cancelled HTTP request mustn't abort the scan
        await asyncio.shield(self._scan_task)
        return len(self.discovery.speakers)

    async def _scan(self, full: bool):
        logger.debug(f"Network speaker discovery started ({'full' if full else 'quick'} scan)")
        try:
            speakers = await self.discovery.discover_all(timeout=self.discovery_timeout, full=full)
        except Exception as e:
            logger.error(f"Network speaker discovery failed: {e}")
            return
        available = {s.id for s in speakers if s.available}
        message = f"Network speakers: {len(available)} found on the network ({len(speakers)} known)"
        if available != self._last_available:
            logger.info(message)
        else:
            logger.debug(message)
        self._last_available = available
        self._notify()

    def _notify(self):
        if self.on_change:
            try:
                self.on_change()
            except Exception as e:
                logger.warning(f"Could not update speakers after network discovery: {e}")

    async def stop(self):
        """Stop background discovery and every stream Sonorium started."""
        for task in (self._loop_task, self._scan_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
        await self.streaming.stop_all()

    # --- Playback (IDs are net:<type>:<device id>) ---

    async def _each(self, speaker_ids: list[str], action) -> dict[str, bool]:
        async def run(speaker_id):
            speaker = self.get_speaker(speaker_id)
            if not speaker:
                logger.warning(f"Unknown network speaker {speaker_id} (not found by discovery yet?)")
                return False
            return await action(speaker)

        results = await asyncio.gather(*(run(sid) for sid in speaker_ids), return_exceptions=True)
        status = {}
        for speaker_id, result in zip(speaker_ids, results):
            if isinstance(result, BaseException):
                logger.error(f"Network speaker {speaker_id}: {result}")
                status[speaker_id] = False
            else:
                status[speaker_id] = bool(result)
        return status

    async def play_multi(self, speaker_ids: list[str], stream_url: str) -> dict[str, bool]:
        return await self._each(speaker_ids, lambda s: self.streaming.start_streaming(s, stream_url))

    async def stop_multi(self, speaker_ids: list[str]) -> dict[str, bool]:
        results = await asyncio.gather(
            *(self.streaming.stop_streaming(sid) for sid in speaker_ids), return_exceptions=True
        )
        return {sid: r is True for sid, r in zip(speaker_ids, results)}

    async def set_volume_multi(self, speaker_ids: list[str], level: float) -> dict[str, bool]:
        return await self._each(speaker_ids, lambda s: self.streaming.set_volume(s, level))

    def playing_states(self, speaker_ids: list[str]) -> dict[str, bool]:
        return {sid: self.streaming.is_playing(sid) for sid in speaker_ids}
