"""
Announcements: the one path every announcement takes, whatever sent it (a
notification rule, Home Assistant's notify entity of a channel, the MQTT
announce command, POST /api/announce, a plugin's self.audio.announce()).

    await announce(overlay, tts, ["kitchen"], text="Dinner is ready",
                   sound="https://.../chime.mp3", volume=90)

1. A chime (a sound URL, or the bytes of a sound), then
2. the text, spoken by a text to speech provider (plugins/tts.py),
both over the channels' mix: the mix ducks, they play one after the other,
the mix comes back. Announcements are admin-level: they override zones and
quiet hours, and they start a stopped channel for the time they play.
"""
from __future__ import annotations

from typing import Optional

from sonorium.core.overlay import OverlayError
from sonorium.obs import logger

ANNOUNCE_PRIORITY = 10


async def announce(overlay, tts, channels, *, text: Optional[str] = None, sound=None, volume: float = 100,
                   duck=True, provider: Optional[str] = None, language: Optional[str] = None,
                   start_stopped: bool = True, override: bool = True, priority: int = ANNOUNCE_PRIORITY,
                   origin: str = "sonorium", label: str = "") -> dict:
    """
    Play the chime and/or speak the text on the channels. Returns
    {"chime": play() result or None, "speech": play() result or None,
     "skipped": the channels with announcements off (nothing plays there)}.
    OverlayError when there's nothing to play; TTS errors pass through.
    """
    if overlay is None:
        raise OverlayError("Playing sounds isn't available yet")
    if not text and sound is None:
        raise OverlayError("An announcement needs text to speak or a sound to play")
    sessions = getattr(overlay, "sessions", None)
    off = [c for c in channels if sessions is not None and sessions.get(str(c)) is not None
           and not getattr(sessions.get(str(c)), "announcements", True)]
    result = {"chime": None, "speech": None, "skipped": off}
    if off:
        logger.info(f"Announcement ({origin}): announcements are off on {len(off)} channel(s), skipped")
    channels = [c for c in channels if c not in off]
    if not channels:
        return result  # nothing to play: no speech is made either
    common = dict(volume=volume, duck=duck, policy="queue", priority=priority, start_stopped=start_stopped,
                  override=override, origin=origin, announcement=True)
    if sound is not None:
        result["chime"] = await overlay.play(channels, sound, label=label or "Chime", **common)
    if text:
        if tts is None:
            raise OverlayError("No text to speech is available")
        audio = await tts.speak(text, provider, **({"language": language} if language else {}))
        result["speech"] = await overlay.play(channels, audio, label=label or text[:80], **common)
    logger.info(f"Announcement ({origin}): {label or (text or '')[:60]!r} on {len(channels)} channel(s)")
    return result
