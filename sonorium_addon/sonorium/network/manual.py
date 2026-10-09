"""
Manually added network speakers (standalone mode): speakers discovery can't
find (other subnet, multicast blocked, ...) added by address in
Settings > Speakers > Add speaker.

Adding one probes the address over the speaker's own protocol (or every
supported protocol, for "Detect automatically") and stores what it found in
<config_dir>/manual_speakers.json, next to network_speakers.json. IDs are
net:<protocol>:manual-<address>, e.g. net:sonos:manual-192.168.1.50.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import socket
from pathlib import Path
from typing import Optional

from sonorium.obs import logger
from sonorium.network import make_speaker_id
from sonorium.network.models import NetworkSpeaker, SpeakerStatus, SpeakerType

# Types the user can pick. LinkPlay/Arylic devices play through their HTTP
# API, which the streaming code uses for AirPlay speakers that look like
# LinkPlay hardware: they're stored as AirPlay with manufacturer "LinkPlay".
MANUAL_TYPES = ("cast", "sonos", "dlna", "airplay", "linkplay", "heos")
DEFAULT_PORTS = {"cast": 8009, "sonos": 1400, "dlna": 49152, "airplay": 7000, "linkplay": 80, "heos": 1255}
TYPE_LABELS = {
    "cast": "Google Cast", "sonos": "Sonos", "dlna": "DLNA / UPnP", "airplay": "AirPlay",
    "linkplay": "LinkPlay / Arylic", "heos": "HEOS",
}
# "Detect automatically": the first protocol that answers, in this order
AUTO_ORDER = ("sonos", "cast", "linkplay", "heos", "dlna", "airplay")
SPEAKER_TYPES = {
    "cast": SpeakerType.CHROMECAST, "sonos": SpeakerType.SONOS, "dlna": SpeakerType.DLNA,
    "airplay": SpeakerType.AIRPLAY, "linkplay": SpeakerType.AIRPLAY, "heos": SpeakerType.HEOS,
}
DLNA_PORTS = (49152, 80, 8080, 49153, 49494, 1400)
PROBE_TIMEOUT = 4.0

_ADDRESS_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]_]+$")


class ProbeError(Exception):
    """The address couldn't be checked or no speaker answered (message is shown to the user)."""


def validate_address(address: str) -> str:
    """An IP address or host name, without scheme, path or port."""
    address = (address or "").strip()
    if not address:
        raise ProbeError("Enter the speaker's IP address or network name")
    if len(address) > 253 or not _ADDRESS_RE.match(address) or address.count(":") == 1:
        raise ProbeError("Enter just an IP address or network name, e.g. 192.168.1.50 or speaker.local")
    return address


def manual_speaker_id(kind: str, address: str) -> str:
    return make_speaker_id(SPEAKER_TYPES[kind].value, f"manual-{address.lower()}")


def manual_kind(speaker: NetworkSpeaker) -> str:
    """The type the user picked/was detected (linkplay is stored as airplay)."""
    return speaker.extra.get("kind") or {
        SpeakerType.CHROMECAST: "cast", SpeakerType.SONOS: "sonos", SpeakerType.DLNA: "dlna",
        SpeakerType.AIRPLAY: "airplay", SpeakerType.HEOS: "heos",
    }[speaker.speaker_type]


async def resolve_host(address: str) -> str:
    """IPv4 address for an IP or host name."""
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(address, None, family=socket.AF_INET, type=socket.SOCK_STREAM), PROBE_TIMEOUT
        )
    except (OSError, asyncio.TimeoutError):
        raise ProbeError(f"Can't find {address} on the network")
    if not infos:
        raise ProbeError(f"Can't find {address} on the network")
    return infos[0][4][0]


async def tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True


# --- Protocol probes: each returns speaker details, or None if it didn't answer ---

def _xml(text: str, tag: str) -> Optional[str]:
    match = re.search(rf"<{tag}>([^<]+)</{tag}>", text, re.IGNORECASE)
    return match.group(1).strip() if match else None


async def _get(session, url: str):
    async with session.get(url) as response:
        if response.status != 200:
            return None
        return await response.text()


async def probe_sonos(session, ip: str, port: Optional[int]) -> Optional[dict]:
    port = port or 1400
    text = await _get(session, f"http://{ip}:{port}/xml/device_description.xml")
    if not text or "sonos" not in text.lower():
        return None
    udn = _xml(text, "UDN") or ""
    uid = udn[5:] if udn.lower().startswith("uuid:") else (udn or ip)
    return {
        "name": _xml(text, "roomName") or _xml(text, "friendlyName"),
        "model": _xml(text, "modelName"), "manufacturer": "Sonos", "uuid": uid, "port": port, "extra": {},
    }


async def probe_cast(session, ip: str, port: Optional[int]) -> Optional[dict]:
    text = await _get(session, f"http://{ip}:8008/setup/eureka_info?params=name,device_info")
    if not text:
        return None
    try:
        info = json.loads(text)
    except ValueError:
        return None
    if not isinstance(info, dict) or "name" not in info:
        return None
    device_info = info.get("device_info") or {}
    return {
        "name": info.get("name"), "model": device_info.get("model_name"),
        "manufacturer": device_info.get("manufacturer"), "uuid": info.get("ssdp_udn"),
        "port": port or 8009, "extra": {"cast_type": "cast"},
    }


async def probe_linkplay(session, ip: str, port: Optional[int]) -> Optional[dict]:
    host = f"{ip}:{port}" if port and port != 80 else ip
    text = await _get(session, f"http://{host}/httpapi.asp?command=getStatusEx")
    if not text:
        return None
    try:
        info = json.loads(text)
    except ValueError:
        return None
    if not isinstance(info, dict) or not (info.get("DeviceName") or info.get("uuid")):
        return None
    return {
        "name": info.get("DeviceName"), "model": info.get("project") or info.get("hardware"),
        "manufacturer": "LinkPlay", "uuid": info.get("uuid") or ip, "port": port or 80,
        "extra": {"identifier": info.get("uuid") or ip},
    }


async def probe_heos(session, ip: str, port: Optional[int]) -> Optional[dict]:
    from sonorium.network.heos import heos_command

    try:
        response = await heos_command(ip, "player/get_players", timeout=PROBE_TIMEOUT)
    except Exception:
        return None
    players = response.get("payload")
    if not isinstance(players, list) or not players:
        return None
    player = next((p for p in players if p.get("ip") == ip), players[0])
    if player.get("pid") is None:
        return None
    model = player.get("model")
    return {
        "name": player.get("name"), "model": model,
        "manufacturer": "Marantz" if model and "marantz" in model.lower() else "Denon",
        "uuid": str(player["pid"]), "port": 1255, "extra": {"pid": player["pid"], "version": player.get("version")},
        "host": player.get("ip") or ip,
    }


async def probe_dlna(session, ip: str, port: Optional[int]) -> Optional[dict]:
    from sonorium.network.discovery import parse_device_description

    for candidate in ([port] if port else DLNA_PORTS):
        url = f"http://{ip}:{candidate}/description.xml"
        try:
            text = await _get(session, url)
        except Exception:
            continue
        if not text:
            continue
        info = parse_device_description(text)
        if info.get("is_renderer"):
            return {
                "name": info.get("friendlyName"), "model": info.get("modelName"),
                "manufacturer": info.get("manufacturer"), "uuid": info.get("uuid"), "port": candidate,
                "extra": {"location": url},
            }
    return None


async def probe_airplay(session, ip: str, port: Optional[int]) -> Optional[dict]:
    try:
        import pyatv
        from pyatv.const import Protocol
    except ImportError:
        pyatv = None
    if pyatv is not None:
        try:
            devices = await pyatv.scan(asyncio.get_running_loop(), hosts=[ip], timeout=int(PROBE_TIMEOUT))
        except Exception:
            devices = []
        for device in devices:
            raop = device.get_service(Protocol.RAOP)
            airplay = device.get_service(Protocol.AirPlay)
            if not raop and not airplay:
                continue
            identifier = str(device.identifier) if device.identifier else ip
            return {
                "name": device.name, "model": None, "manufacturer": None, "uuid": identifier,
                "port": (raop.port if raop else None) or (airplay.port if airplay else None) or port or 7000,
                "extra": {
                    "identifier": identifier,
                    "raop_port": raop.port if raop else None,
                    "raop_properties": dict(raop.properties) if raop and raop.properties else {},
                },
            }
    # Without pyatv details, an open AirPlay port is enough: streaming scans the host itself
    if await tcp_reachable(ip, port or 7000):
        return {"name": None, "model": None, "manufacturer": None, "uuid": None, "port": port or 7000, "extra": {}}
    return None


PROBES = {
    "sonos": probe_sonos, "cast": probe_cast, "linkplay": probe_linkplay,
    "heos": probe_heos, "dlna": probe_dlna, "airplay": probe_airplay,
}


async def probe_speaker(address: str, kind: str = "auto", port: Optional[int] = None,
                        name: Optional[str] = None, probes: Optional[dict] = None) -> NetworkSpeaker:
    """
    Check what answers at `address` and return it as a manual NetworkSpeaker.
    Raises ProbeError when nothing (of the requested type) answers.
    """
    import aiohttp

    probes = probes or PROBES
    address = validate_address(address)
    kind = (kind or "auto").lower()
    if kind != "auto" and kind not in MANUAL_TYPES:
        raise ProbeError(f"Unknown speaker type '{kind}'")
    if port is not None and not 0 < int(port) < 65536:
        raise ProbeError("Port must be between 1 and 65535")
    ip = await resolve_host(address)

    kinds = list(AUTO_ORDER) if kind == "auto" else [kind]

    async def run(k):
        try:
            return await asyncio.wait_for(probes[k](session, ip, port), PROBE_TIMEOUT + 2)
        except Exception as e:
            logger.debug(f"Manual speaker probe {k} at {ip}: {e}")
            return None

    timeout = aiohttp.ClientTimeout(total=PROBE_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        results = await asyncio.gather(*(run(k) for k in kinds))

    for k, info in zip(kinds, results):
        if info:
            return build_manual_speaker(k, address, ip, info, name)

    if kind == "auto":
        raise ProbeError(f"Found {address} ({ip}) but no supported speaker answered. Pick the type to try again.")
    raise ProbeError(f"No {TYPE_LABELS[kind]} speaker answered at {address} ({ip})")


def build_manual_speaker(kind: str, address: str, ip: str, info: dict, name: Optional[str] = None) -> NetworkSpeaker:
    extra = dict(info.get("extra") or {})
    extra.update({"manual": True, "address": address, "kind": kind})
    return NetworkSpeaker(
        id=manual_speaker_id(kind, address),
        name=(name or "").strip() or info.get("name") or address,
        speaker_type=SPEAKER_TYPES[kind],
        host=info.get("host") or ip,
        port=int(info.get("port") or DEFAULT_PORTS[kind]),
        model=info.get("model"),
        manufacturer=info.get("manufacturer") or ("LinkPlay" if kind == "linkplay" else None),
        uuid=info.get("uuid"),
        extra=extra,
        status=SpeakerStatus.AVAILABLE,
    )


class ManualSpeakers:
    """Manually added speakers, saved in <config_dir>/manual_speakers.json."""

    def __init__(self, config_dir: Path):
        self._file = Path(config_dir) / "manual_speakers.json"
        self.speakers: dict[str, NetworkSpeaker] = {}
        self._load()

    def _load(self):
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read manual speakers ({self._file}): {e}")
            return
        for item in data.get("speakers", []):
            try:
                speaker = NetworkSpeaker.from_storage_dict(item)
            except Exception as e:
                logger.debug(f"Skipping saved manual speaker {item!r}: {e}")
                continue
            speaker.extra["manual"] = True
            self.speakers[speaker.id] = speaker

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        data = {"version": 1, "speakers": [s.to_storage_dict() for s in self.speakers.values()]}
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._file)

    def get(self, speaker_id: str) -> Optional[NetworkSpeaker]:
        return self.speakers.get(speaker_id)

    def add(self, speaker: NetworkSpeaker):
        self.speakers[speaker.id] = speaker
        self._save()

    def remove(self, speaker_id: str) -> bool:
        if self.speakers.pop(speaker_id, None) is None:
            return False
        self._save()
        return True

    async def refresh_status(self, discovered: dict[str, NetworkSpeaker]):
        """Mark each manual speaker available if discovery saw its host or its port answers."""
        seen = {s.host for s in discovered.values() if s.available}

        async def check(speaker: NetworkSpeaker):
            address = speaker.extra.get("address") or speaker.host
            try:
                speaker.host = await resolve_host(address)  # follows DHCP changes for host names
            except ProbeError:
                speaker.status = SpeakerStatus.UNAVAILABLE
                return
            if speaker.host in seen:
                speaker.status = SpeakerStatus.AVAILABLE
                return
            port = speaker.port or DEFAULT_PORTS.get(manual_kind(speaker), 80)
            ok = await tcp_reachable(speaker.host, port)
            speaker.status = SpeakerStatus.AVAILABLE if ok else SpeakerStatus.UNAVAILABLE

        await asyncio.gather(*(check(s) for s in self.speakers.values()), return_exceptions=True)
