"""
Live volume and mute of one speaker (PUT /api/speakers/{id}/volume and
/muted, ability sp.mute "Mute and volume"; docs/APP_API.md G6).

These act on the speaker itself, not on a channel: the channel's volume and
its speakers-muted switch are unchanged, and the speaker's volume offset
(Settings > Speakers) isn't added. A merged speaker is reached the way its
"play via" setting says.

- Home Assistant speakers: media_player.volume_set and volume_mute.
- Network speakers have no mute of their own: muting sets the volume to 0
  and unmuting brings back the volume it had (or was given meanwhile).

What was set here is remembered (in memory) and listed by GET /api/speakers
as "volume" and "muted"; a speaker changed elsewhere isn't followed.
"""
from __future__ import annotations

from typing import Callable, Optional

NETWORK_PREFIX = "net:"
# Speakers Sonorium plays itself and keeps the volume of (sonorium/virtual_speakers.py):
# speaker id prefix -> handler with set_volume(id, volume) and set_muted(id, muted), each -> bool
LOCAL_HANDLERS: dict = {}


def _local_handler(speaker_id: str):
    return next((h for prefix, h in list(LOCAL_HANDLERS.items()) if speaker_id.startswith(prefix)), None)


class SpeakerLive:
    def __init__(self, controller: Callable[[], object]):
        """controller(): the session manager's media controller (SpeakerOutputs), or None."""
        self._controller = controller
        self.known: dict[str, dict] = {}  # speaker id -> {"volume": 0-100, "muted": bool}

    def state(self, speaker_id: str) -> dict:
        """What is known of a speaker's volume and mute ({} when nothing was set here)."""
        return dict(self.known.get(speaker_id) or {})

    def _route(self, speaker_id: str) -> tuple[Optional[object], str]:
        """(the controller that reaches the speaker, the id to send) honouring play_via."""
        mc = self._controller()
        if mc is None:
            return None, speaker_id
        if hasattr(mc, "target_for"):  # SpeakerOutputs: play_via picks the path
            return getattr(mc, "controller", None), mc.target_for(speaker_id)
        return mc, speaker_id

    @staticmethod
    async def _volume(controller, target: str, volume: int) -> bool:
        if controller is None:
            return False
        result = await controller.set_volume_multi([target], volume / 100.0)
        return bool((result or {}).get(target))

    async def set_volume(self, speaker_id: str, volume: int) -> bool:
        """Set the speaker's volume (0-100). False: the speaker didn't take it."""
        local = _local_handler(speaker_id)
        if local is not None:
            ok = bool(local.set_volume(speaker_id, volume))
            if ok:
                self.known.setdefault(speaker_id, {})["volume"] = volume
            return ok
        controller, target = self._route(speaker_id)
        known = self.known.setdefault(speaker_id, {})
        if known.get("muted") and target.startswith(NETWORK_PREFIX):
            known["volume"] = volume  # muted by volume 0: kept for unmuting
            return True
        ok = await self._volume(controller, target, volume)
        if ok:
            known["volume"] = volume
        return ok

    async def set_muted(self, speaker_id: str, muted: bool) -> bool:
        """Mute or unmute the speaker. False: it couldn't be done."""
        local = _local_handler(speaker_id)
        if local is not None:
            ok = bool(local.set_muted(speaker_id, muted))
            if ok:
                self.known.setdefault(speaker_id, {})["muted"] = bool(muted)
            return ok
        controller, target = self._route(speaker_id)
        known = self.known.setdefault(speaker_id, {})
        if target.startswith(NETWORK_PREFIX):
            if muted:
                ok = await self._volume(controller, target, 0)
            else:
                ok = await self._volume(controller, target, int(known.get("volume", 50)))
        else:
            ha = getattr(controller, "ha_controller", controller)  # standalone: SpeakerRouter
            ok = bool(ha is not None and hasattr(ha, "mute") and await ha.mute(target, muted))
        if ok:
            known["muted"] = bool(muted)
        return ok
