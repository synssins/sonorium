"""
Announcements: the one path every announcement takes, whatever sent it.

Sonorium doesn't decide when to announce. Other systems send it an
announcement and it plays it:

    Home Assistant   each channel's notify entity (Announcements plugin)
    MQTT             sonorium/announce/set (Announcements plugin)
    HTTP             POST /api/announce (a key with Make announcements, or signed in)
    plugins          self.audio.announce()

    await announce(overlay, tts, ["kitchen"], text="Dinner is ready",
                   sound="https://.../chime.mp3", volume=90)

1. A chime (a sound URL, or the bytes of a sound), then
2. the text, spoken by a text to speech provider (plugins/tts.py),
both over the channels' mix: the mix ducks, they play one after the other,
the mix comes back. A channel that is stopped is started for the time they
play, then stopped again. Announcements are admin-level: they override zones
and quiet hours. A channel with its Announcements setting off is left out
(the check is on the channel itself, so every channel has it).
"""
from __future__ import annotations

from typing import Optional

from sonorium.core.overlay import OverlayError
from sonorium.obs import logger

ANNOUNCE_PRIORITY = 10
MAX_TEXT = 1000
NO_TTS = ("No text to speech is available. It comes from Home Assistant: link it in "
          "Settings › Connection, or send a chime url instead")


class TtsUnavailable(OverlayError):
    """Text was sent, no chime, and nothing can speak it."""


def resolve_channels(sessions, wanted) -> list[str]:
    """
    Channel ids from what a caller sent: "all" (or "*", or nothing), one id or
    name, or a list of them. Names match without regard to case, as do Home
    Assistant entity slugs ("kids_room"). LookupError names the first unknown.
    """
    listed = list(sessions.list()) if sessions is not None else []
    if wanted is None or wanted in ("all", "*") or wanted in (["all"], ["*"]):
        return [s.id for s in listed]
    if isinstance(wanted, str):
        wanted = [part.strip() for part in wanted.split(",")] if "," in wanted else [wanted]
    if not isinstance(wanted, list) or not wanted or \
            not all(isinstance(c, (str, int)) and not isinstance(c, bool) and str(c).strip() for c in wanted):
        raise ValueError("'channels' must be a channel name or id, a list of them, or \"all\"")
    ids: list[str] = []
    if sessions is None:  # nothing to look names up in: ids as given
        return list(dict.fromkeys(str(c).strip() for c in wanted))
    for item in (str(c).strip() for c in wanted):
        if sessions.get(item) is not None:
            found = item
        else:
            key = item.casefold()
            found = next((s.id for s in listed if str(s.name).casefold() == key), None)
            if found is None:
                found = next((s.id for s in listed if hasattr(s, "get_entity_slug")
                              and s.get_entity_slug().casefold() == key), None)
        if found is None:
            raise LookupError(f"No channel '{item}'")
        if found not in ids:
            ids.append(found)
    return ids


def parse_duck(value):
    """True (the Advanced values), False, a number (how many dB, the rest from Advanced) or {db, attack, release}."""
    if value is None:
        return True
    if isinstance(value, bool) or isinstance(value, dict):
        return value
    if isinstance(value, (int, float)):
        return {"db": value}
    raise OverlayError("'duck' must be true, false, a number of dB, or an object (db, attack, release)")


def tts_ready(tts, provider: Optional[str] = None) -> bool:
    if tts is None:
        return False
    ready = getattr(tts, "ready", None)
    return bool(ready(provider)) if callable(ready) else bool(tts.providers())


async def announce(overlay, tts, channels, *, text: Optional[str] = None, sound=None, volume: float = 100,
                   duck=True, provider: Optional[str] = None, language: Optional[str] = None,
                   start_stopped: bool = True, override: bool = True, priority: int = ANNOUNCE_PRIORITY,
                   origin: str = "sonorium", label: str = "") -> dict:
    """
    Play the chime and/or speak the text on the channels. Returns
    {"played": channel ids it plays (or waits) on,
     "skipped": channel ids with Announcements off (nothing plays there),
     "channels": {id: {"state", "message"}},
     "chime": play() result or None, "speech": play() result or None,
     "notice": why the text wasn't spoken while the chime played, or None}.
    OverlayError when there's nothing to play; TtsUnavailable when only text
    was sent and nothing can speak it; other TTS errors pass through when
    there was no chime.
    """
    if overlay is None:
        raise OverlayError("Playing sounds isn't available yet")
    if isinstance(channels, str):
        channels = [channels]
    text = text.strip() if isinstance(text, str) else None
    if text and len(text) > MAX_TEXT:
        raise OverlayError(f"'text' is too long (at most {MAX_TEXT} characters)")
    if not text and sound is None:
        raise OverlayError("An announcement needs text to speak or a sound to play")
    sessions = getattr(overlay, "sessions", None)
    off = [c for c in channels if sessions is not None and sessions.get(str(c)) is not None
           and not getattr(sessions.get(str(c)), "announcements", True)]
    result = {"played": [], "skipped": off, "channels": {c: {"state": "skipped", "message": "Announcements are off"}
                                                          for c in off},
              "chime": None, "speech": None, "notice": None}
    if off:
        logger.info(f"Announcement ({origin}): announcements are off on {len(off)} channel(s), skipped")
    channels = [c for c in channels if c not in off]
    if not channels:
        return result  # nothing to play: no speech is made either
    if text and not tts_ready(tts, provider):
        if sound is None:
            raise TtsUnavailable(NO_TTS)
        result["notice"] = "Not spoken: " + NO_TTS[0].lower() + NO_TTS[1:]
        logger.info(f"Announcement ({origin}): no text to speech, only the chime plays")
        text = None
    common = dict(volume=volume, duck=duck, policy="queue", priority=priority, start_stopped=start_stopped,
                  override=override, origin=origin, announcement=True)
    if sound is not None:
        result["chime"] = await overlay.play(channels, sound, label=label or "Chime", **common)
    if text:
        try:
            audio = await tts.speak(text, provider, **({"language": language} if language else {}))
        except Exception as e:
            if result["chime"] is None:
                raise
            result["notice"] = f"Not spoken: {getattr(e, 'message', None) or e}"
            logger.warning(f"Announcement ({origin}): {result['notice']}")
        else:
            result["speech"] = await overlay.play(channels, audio, label=label or text[:80], **common)
    for part in (result["chime"], result["speech"]):
        for channel_id, state in ((part or {}).get("channels") or {}).items():
            if result["channels"].get(channel_id, {}).get("state") in (None, "skipped"):
                result["channels"][channel_id] = state
    result["played"] = [c for c, s in result["channels"].items() if s.get("state") != "skipped"]
    logger.info(f"Announcement ({origin}): {label or (text or 'chime')[:60]!r} on {len(result['played'])} channel(s)")
    return result
