"""
Speaker router (standalone mode): stands in for HAMediaController in the
session manager and sends each speaker ID to the right place. Network
speaker IDs (net:...) go to the network speaker service, everything else
(media_player.* entities) to Home Assistant, when it's configured. A session
can mix both.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

from sonorium.obs import logger
from sonorium.network import is_network_speaker_id

if TYPE_CHECKING:
    from sonorium.ha.media_controller import HAMediaController
    from sonorium.network.service import NetworkSpeakerService


def split_speaker_ids(speaker_ids: list[str]) -> tuple[list[str], list[str]]:
    """(Home Assistant entity IDs, network speaker IDs), order kept."""
    ha_ids = [sid for sid in speaker_ids if not is_network_speaker_id(sid)]
    net_ids = [sid for sid in speaker_ids if is_network_speaker_id(sid)]
    return ha_ids, net_ids


class SpeakerRouter:

    def __init__(self, ha_controller: Optional["HAMediaController"], network_service: "NetworkSpeakerService"):
        self.ha_controller = ha_controller
        self.network = network_service

    async def _route(self, speaker_ids, ha_call, net_call) -> dict[str, bool]:
        ha_ids, net_ids = split_speaker_ids(list(speaker_ids or []))
        status: dict[str, bool] = {}
        groups, calls = [], []
        if ha_ids:
            if self.ha_controller is None:
                logger.warning(f"Home Assistant isn't connected: can't reach {', '.join(ha_ids)}")
                status.update({sid: False for sid in ha_ids})
            else:
                groups.append(ha_ids)
                calls.append(ha_call(ha_ids))
        if net_ids:
            groups.append(net_ids)
            calls.append(net_call(net_ids))

        for ids, result in zip(groups, await asyncio.gather(*calls, return_exceptions=True)):
            if isinstance(result, BaseException):
                logger.error(f"Speaker command failed for {', '.join(ids)}: {result}")
                status.update({sid: False for sid in ids})
            else:
                status.update(result or {})
        return status

    async def play_media_multi(self, entity_ids: list[str], media_url: str, media_type: str = "music") -> dict[str, bool]:
        return await self._route(
            entity_ids,
            lambda ids: self.ha_controller.play_media_multi(ids, media_url, media_type),
            lambda ids: self.network.play_multi(ids, media_url),
        )

    async def stop_multi(self, entity_ids: list[str]) -> dict[str, bool]:
        return await self._route(
            entity_ids,
            lambda ids: self.ha_controller.stop_multi(ids),
            lambda ids: self.network.stop_multi(ids),
        )

    async def pause_multi(self, entity_ids: list[str]) -> dict[str, bool]:
        # Network speakers play a live stream: pausing stops it, play resumes it
        return await self._route(
            entity_ids,
            lambda ids: self.ha_controller.pause_multi(ids),
            lambda ids: self.network.stop_multi(ids),
        )

    async def set_volume_multi(self, entity_ids: list[str], volume_level: float) -> dict[str, bool]:
        return await self._route(
            entity_ids,
            lambda ids: self.ha_controller.set_volume_multi(ids, volume_level),
            lambda ids: self.network.set_volume_multi(ids, volume_level),
        )

    async def get_playing_states(self, entity_ids: list[str]) -> dict[str, bool]:
        async def network_states(ids):
            return self.network.playing_states(ids)

        return await self._route(
            entity_ids,
            lambda ids: self.ha_controller.get_playing_states(ids),
            network_states,
        )

    def __getattr__(self, name):
        # Anything else (get_state, play_media, ...) is Home Assistant only
        ha_controller = self.__dict__.get("ha_controller")
        if ha_controller is None:
            raise AttributeError(name)
        return getattr(ha_controller, name)
