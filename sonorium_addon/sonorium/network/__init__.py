"""
Network speakers for standalone mode: Sonorium finds speakers on the LAN
itself (Chromecast, Sonos, DLNA/UPnP, AirPlay, LinkPlay/Arylic, HEOS) and
tells them to play its channel streams, without Home Assistant.

Only used when runtime.STANDALONE is set; the Home Assistant add-on never
imports this package. This module has no third-party imports so the ID
helpers can be used anywhere; the protocol libraries (pychromecast, soco,
zeroconf, async-upnp-client, pyatv, aiohttp) are imported lazily where used.

Speaker IDs are "net:<type>:<device id>", e.g. "net:sonos:RINCON_1234",
so they can't collide with Home Assistant entity IDs ("media_player.x").
"""
import re
from typing import Optional

NET_PREFIX = "net:"

# Speaker IDs end up inside HTML attributes and JS string literals in the web
# UI, so device IDs are limited to characters that are safe there.
_UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9._-]")
MAX_DEVICE_ID_LENGTH = 64


def sanitize_device_id(raw: str) -> str:
    """Device identifier reduced to [A-Za-z0-9._-], at most 64 characters."""
    cleaned = _UNSAFE_ID_CHARS.sub("_", str(raw).strip())
    return cleaned[:MAX_DEVICE_ID_LENGTH] or "unknown"


def make_speaker_id(speaker_type: str, device_id: str) -> str:
    """Sonorium speaker ID for a network speaker: net:<type>:<device id>."""
    return f"{NET_PREFIX}{speaker_type}:{sanitize_device_id(device_id)}"


def is_network_speaker_id(speaker_id: str) -> bool:
    """Whether an ID belongs to a network speaker (as opposed to an HA entity)."""
    return isinstance(speaker_id, str) and speaker_id.startswith(NET_PREFIX)


def parse_speaker_id(speaker_id: str) -> Optional[tuple[str, str]]:
    """(type, device id) for a network speaker ID, or None if it isn't one."""
    if not is_network_speaker_id(speaker_id):
        return None
    speaker_type, sep, device_id = speaker_id[len(NET_PREFIX):].partition(":")
    if not sep or not speaker_type or not device_id:
        return None
    return speaker_type, device_id
