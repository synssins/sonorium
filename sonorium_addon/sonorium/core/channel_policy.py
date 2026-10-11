"""
The hook a plugin uses to decide who may change a channel and which speakers
it plays on (Zones, in the built-in Users plugin).

SessionManager.policy is None by default: every channel is open and plays on
every speaker it has, Sonorium's behaviour without zones. A policy is any
object with some of these methods (all optional):

    check(action, session, **details)   may the current caller do this? Raise
                                        ChannelRefused to refuse. Actions:
                                        "play", "stop", "update" (details: theme,
                                        preset, name, volume, speakers: what
                                        changes), "volume", "mute", "playback"
                                        (sequence, playlist, next), "delete"
    speakers(session, speaker_ids)      the speakers the channel plays on now,
                                        from the ones it has (e.g. without the
                                        ones another channel uses)
    changed(session)                    its speakers changed (after update)
    released(session)                   it stopped or paused
    deleted(session)                    it was deleted

Who is calling: sonorium.auth.principal.current_caller() (None for MQTT,
timers and plugins: Home Assistant and Sonorium itself).
"""
from __future__ import annotations


class ChannelRefused(Exception):
    """A change the channel's policy refuses; the API answers `status` with {"detail": message}."""

    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.status = status
        self.detail = message
