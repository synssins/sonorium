"""
Network speaker discovery, ported from the Windows app (app/core/sonorium/
network_speakers.py): Chromecast (pychromecast), Sonos (SoCo), DLNA/UPnP
renderers (SSDP), mDNS (zeroconf), a LinkPlay/Arylic subnet probe, AirPlay
(pyatv) and HEOS (CLI on port 1255).

Discovered speakers are saved to <config_dir>/network_speakers.json and
loaded at startup, so they're selectable before the first scan finishes.
Speakers that a later scan doesn't find are kept (marked unavailable).

Needs multicast on the LAN: run the container with host networking.
"""
from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import re
import socket
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from sonorium.obs import logger
from sonorium.network import make_speaker_id
from sonorium.network.models import NetworkSpeaker, SpeakerStatus, SpeakerType, TYPE_PRIORITY

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
UPNP_PORT = 49152  # LinkPlay and many renderers serve description.xml here

DLNA_SEARCH_TARGETS = [
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:service:AVTransport:1",
    "ssdp:all",  # devices that don't answer the specific searches
]

MDNS_SERVICE_TYPES = [
    "_raop._tcp.local.",
    "_airplay._tcp.local.",
    "_spotify-connect._tcp.local.",
    "_http._tcp.local.",       # some LinkPlay devices
    "_linkplay._tcp.local.",
    "_arylic._tcp.local.",
]


# --- Helpers ---

def _xml_field(text: str, tag: str) -> Optional[str]:
    match = re.search(rf"<{tag}>([^<]+)</{tag}>", text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def parse_device_description(text: str) -> dict:
    """Fields from a UPnP device description (description.xml)."""
    info = {}
    for key, tag in (("friendlyName", "friendlyName"), ("modelName", "modelName"), ("manufacturer", "manufacturer")):
        value = _xml_field(text, tag)
        if value:
            info[key] = value
    udn = _xml_field(text, "UDN")
    if udn:
        info["uuid"] = udn[5:] if udn.lower().startswith("uuid:") else udn
    info["is_renderer"] = "MediaRenderer" in text or "AVTransport" in text
    return info


def uuid_from_usn(usn: str) -> Optional[str]:
    """'uuid:abcd::urn:...' -> 'abcd'."""
    if not usn:
        return None
    first = usn.split("::")[0].strip()
    if first.lower().startswith("uuid:"):
        first = first[5:]
    return first or None


def local_ipv4() -> Optional[str]:
    """This host's LAN address (a UDP connect sends no packets)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def ssdp_search(search_target: str, timeout: float, mx: int = 3) -> list[dict]:
    """
    SSDP M-SEARCH (blocking): returns the response headers (upper-case keys)
    of every reply with a LOCATION, plus '_raw' (the whole reply).
    """
    responses = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(timeout)
        message = (
            "M-SEARCH * HTTP/1.1\r\n"
            f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
            'MAN: "ssdp:discover"\r\n'
            f"MX: {mx}\r\n"
            f"ST: {search_target}\r\n"
            "\r\n"
        )
        sock.sendto(message.encode(), (SSDP_ADDR, SSDP_PORT))
        while True:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                break
            text = data.decode("utf-8", errors="ignore")
            headers = {}
            for line in text.split("\r\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.upper().strip()] = value.strip()
            if "LOCATION" in headers:
                headers["_raw"] = text
                headers["_addr"] = addr[0]
                responses.append(headers)
    except OSError as e:
        logger.debug(f"SSDP search for {search_target} failed: {e}")
    finally:
        sock.close()
    return responses


_unicast_mdns_logged = False


class _PortInUseFilter(logging.Filter):
    """zeroconf logs an error before raising when port 5353 is taken; we handle that below."""

    def filter(self, record):
        return "Address in use when binding" not in record.getMessage()


def open_zeroconf():
    """
    An mDNS listener. Another mDNS service on the host (e.g. avahi on TrueNAS
    and many Linux systems) may hold UDP port 5353 exclusively; then query in
    unicast mode from our own port, and devices reply to us directly.
    """
    from zeroconf import Zeroconf
    global _unicast_mdns_logged
    zeroconf_logger = logging.getLogger("zeroconf")
    if not any(isinstance(f, _PortInUseFilter) for f in zeroconf_logger.filters):
        zeroconf_logger.addFilter(_PortInUseFilter())
    try:
        return Zeroconf()
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        if not _unicast_mdns_logged:
            logger.info("mDNS port 5353 is used by another service on this host; using unicast mDNS discovery")
            _unicast_mdns_logged = True
        return Zeroconf(unicast=True)


def browse_mdns(service_types: list[str], wait: float) -> list[dict]:
    """Browse mDNS service types for `wait` seconds (blocking, needs zeroconf)."""
    import time
    from zeroconf import ServiceBrowser, ServiceListener

    class Listener(ServiceListener):
        def __init__(self):
            self.services = []

        def add_service(self, zc, type_, name):
            try:
                info = zc.get_service_info(type_, name, timeout=3000)
            except Exception as e:
                logger.debug(f"mDNS: no info for {name}: {e}")
                return
            if not info:
                return
            addresses = [a for a in info.parsed_addresses() if ":" not in a]
            properties = {}
            for key, value in (info.properties or {}).items():
                key = key.decode(errors="ignore") if isinstance(key, bytes) else str(key)
                value = value.decode(errors="ignore") if isinstance(value, bytes) else value
                properties[key] = value
            self.services.append({
                "name": name, "type": type_, "server": info.server, "port": info.port,
                "addresses": addresses, "properties": properties,
            })

        def update_service(self, zc, type_, name):
            pass

        def remove_service(self, zc, type_, name):
            pass

    zc = open_zeroconf()
    listener = Listener()
    try:
        browsers = []
        for service_type in service_types:
            try:
                browsers.append(ServiceBrowser(zc, service_type, listener))
            except Exception as e:
                logger.debug(f"mDNS: cannot browse {service_type}: {e}")
        time.sleep(wait)
        return list(listener.services)
    finally:
        zc.close()


def dedupe_by_host(speakers: dict[str, NetworkSpeaker]) -> dict[str, NetworkSpeaker]:
    """
    One device often answers to several protocols (a Sonos is also a DLNA
    renderer and an AirPlay target). Per host, keep only the speakers of the
    best type (TYPE_PRIORITY), preferring types that are currently available.
    Several speakers of the same type on one host (e.g. Cast groups) all stay.
    """
    by_host: dict[str, list[NetworkSpeaker]] = {}
    kept: dict[str, NetworkSpeaker] = {}
    for speaker in speakers.values():
        if speaker.host:
            by_host.setdefault(speaker.host, []).append(speaker)
        else:
            kept[speaker.id] = speaker
    for group in by_host.values():
        types = {s.speaker_type for s in group}
        if len(types) > 1:
            candidates = {s.speaker_type for s in group if s.available} or types
            best = min(candidates, key=TYPE_PRIORITY.index)
            dropped = [s.id for s in group if s.speaker_type != best]
            logger.debug(f"Network speakers: {group[0].host} answers as {sorted(t.value for t in types)}; using {best.value}, dropping {dropped}")
            group = [s for s in group if s.speaker_type == best]
        for speaker in group:
            kept[speaker.id] = speaker
    return kept


# --- Discovery ---

class NetworkSpeakerDiscovery:
    """Finds network speakers across protocols and remembers them."""

    def __init__(self, config_dir: Path):
        self.speakers: dict[str, NetworkSpeaker] = {}
        self._speakers_file = Path(config_dir) / "network_speakers.json"
        self._load_speakers()

    # Persistence

    def _load_speakers(self):
        try:
            data = json.loads(self._speakers_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read saved network speakers ({self._speakers_file}): {e}")
            return
        for item in data.get("speakers", []):
            try:
                speaker = NetworkSpeaker.from_storage_dict(item)
            except Exception as e:
                logger.debug(f"Skipping saved network speaker {item!r}: {e}")
                continue
            self.speakers[speaker.id] = speaker
        logger.debug(f"Loaded {len(self.speakers)} saved network speaker(s)")

    def _save_speakers(self):
        try:
            self._speakers_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._speakers_file.with_suffix(".tmp")
            data = {"version": 1, "speakers": [s.to_storage_dict() for s in self.speakers.values()]}
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self._speakers_file)
        except OSError as e:
            logger.warning(f"Could not save network speakers: {e}")

    # Scan

    async def discover_all(self, timeout: float = 10.0, full: bool = True) -> list[NetworkSpeaker]:
        """
        Scan all protocols concurrently and merge the results with the known
        speakers. `full` adds the LinkPlay /24 subnet probe (254 HTTP requests),
        which periodic background scans leave out.
        """
        protocols = [
            ("Chromecast", self._discover_chromecast(timeout)),
            ("Sonos", self._discover_sonos(timeout)),
            ("DLNA", self._discover_dlna(timeout)),
            ("mDNS", self._discover_mdns(timeout)),
            ("AirPlay", self._discover_airplay(timeout)),
            ("HEOS", self._discover_heos(timeout)),
        ]
        if full:
            protocols.append(("LinkPlay", self._discover_linkplay(timeout)))

        results = await asyncio.gather(*(coro for _, coro in protocols), return_exceptions=True)

        found: dict[str, NetworkSpeaker] = {}
        for (name, _), result in zip(protocols, results):
            if isinstance(result, BaseException):
                logger.warning(f"{name} discovery failed: {result}")
                continue
            logger.debug(f"{name} discovery found {len(result)} device(s)")
            for speaker in result:
                found.setdefault(speaker.id, speaker)

        now = datetime.now().isoformat(timespec="seconds")
        for speaker in found.values():
            speaker.status = SpeakerStatus.AVAILABLE
            speaker.last_seen = now

        self.speakers = self.merge_discovered(found)
        self._save_speakers()
        return list(self.speakers.values())

    def merge_discovered(self, found: dict[str, NetworkSpeaker]) -> dict[str, NetworkSpeaker]:
        """Found speakers plus known ones that weren't found (as unavailable), deduplicated by host."""
        merged = dict(found)
        for speaker_id, saved in self.speakers.items():
            if speaker_id in merged:
                continue
            if saved.uuid and any(
                s.uuid == saved.uuid and s.speaker_type == saved.speaker_type for s in found.values()
            ):
                continue  # same device under a new ID
            saved.status = SpeakerStatus.UNAVAILABLE
            merged[speaker_id] = saved
        return dedupe_by_host(merged)

    def get_speaker(self, speaker_id: str) -> Optional[NetworkSpeaker]:
        return self.speakers.get(speaker_id)

    # Protocols. Each returns the speakers it found and doesn't touch self.speakers.

    async def _discover_chromecast(self, timeout: float) -> list[NetworkSpeaker]:
        try:
            import pychromecast
        except ImportError:
            logger.debug("pychromecast not installed: Chromecast discovery skipped")
            return []

        def discover():
            zc = open_zeroconf()
            try:
                services, browser = pychromecast.discovery.discover_chromecasts(timeout=timeout, zeroconf_instance=zc)
                pychromecast.discovery.stop_discovery(browser)
                return services
            finally:
                zc.close()

        services = await asyncio.get_running_loop().run_in_executor(None, discover)
        speakers = []
        for service in services:
            if not service.host:
                continue
            uuid = str(service.uuid)
            speakers.append(NetworkSpeaker(
                id=make_speaker_id(SpeakerType.CHROMECAST.value, uuid),
                name=service.friendly_name or uuid,
                speaker_type=SpeakerType.CHROMECAST,
                host=str(service.host),
                port=service.port or 8009,
                model=service.model_name,
                manufacturer=service.manufacturer,
                is_group=service.cast_type == "group",
                uuid=uuid,
                extra={"cast_type": service.cast_type},
            ))
        return speakers

    async def _discover_sonos(self, timeout: float) -> list[NetworkSpeaker]:
        try:
            import soco
        except ImportError:
            logger.debug("soco not installed: Sonos discovery skipped")
            return []

        def discover():
            # Every attribute below may be a network call: keep it all in this thread
            players = []
            for device in soco.discover(timeout=max(1, int(timeout))) or []:
                try:
                    info = device.get_speaker_info()
                    players.append({
                        "uid": device.uid,
                        "name": device.player_name,
                        "ip": device.ip_address,
                        "model": info.get("model_name"),
                        "zone_name": info.get("zone_name"),
                        "is_coordinator": device.is_coordinator,
                    })
                except Exception as e:
                    logger.debug(f"Sonos: skipping {getattr(device, 'ip_address', '?')}: {e}")
            return players

        players = await asyncio.get_running_loop().run_in_executor(None, discover)
        return [
            NetworkSpeaker(
                id=make_speaker_id(SpeakerType.SONOS.value, p["uid"]),
                name=p["name"] or p["ip"],
                speaker_type=SpeakerType.SONOS,
                host=p["ip"],
                port=1400,
                model=p["model"],
                manufacturer="Sonos",
                uuid=p["uid"],
                extra={"zone_name": p["zone_name"], "is_coordinator": p["is_coordinator"]},
            )
            for p in players
        ]

    async def _discover_dlna(self, timeout: float) -> list[NetworkSpeaker]:
        import aiohttp

        loop = asyncio.get_running_loop()
        per_search = min(timeout, 5.0)
        batches = await asyncio.gather(*(
            loop.run_in_executor(None, ssdp_search, target, per_search) for target in DLNA_SEARCH_TARGETS
        ))

        by_location: dict[str, dict] = {}
        for batch in batches:
            for headers in batch:
                by_location.setdefault(headers["LOCATION"], headers)

        async def describe(session, location):
            try:
                async with session.get(location) as response:
                    if response.status == 200:
                        return parse_device_description(await response.text())
            except Exception as e:
                logger.debug(f"DLNA: no description at {location}: {e}")
            return {}

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            locations = list(by_location)
            descriptions = await asyncio.gather(*(describe(session, loc) for loc in locations))

        speakers = {}
        for location, details in zip(locations, descriptions):
            headers = by_location[location]
            usn = headers.get("USN", "")
            search_type = (headers.get("ST", "") + " " + usn).lower()
            # Only renderers can be told to play a URL (the original also let
            # every ssdp:all reply through, listing routers and NASes as speakers)
            if not (details.get("is_renderer") or "mediarenderer" in search_type or "avtransport" in search_type):
                continue
            parsed = urlparse(location)
            host = parsed.hostname or headers.get("_addr", "")
            if not host:
                continue
            device_id = details.get("uuid") or uuid_from_usn(usn) or host
            speaker_id = make_speaker_id(SpeakerType.DLNA.value, device_id)
            if speaker_id in speakers:
                continue
            server = headers.get("SERVER", "")
            speakers[speaker_id] = NetworkSpeaker(
                id=speaker_id,
                name=details.get("friendlyName") or f"DLNA device ({host})",
                speaker_type=SpeakerType.DLNA,
                host=host,
                port=parsed.port or 80,
                model=details.get("modelName"),
                manufacturer=details.get("manufacturer") or server.split("/")[0] or None,
                uuid=details.get("uuid") or uuid_from_usn(usn),
                extra={"location": location, "usn": usn, "server": server},
            )
        return list(speakers.values())

    async def _probe_description(self, session, host: str, ports: list[int]) -> Optional[dict]:
        """A renderer's description.xml on one of the given ports, parsed, or None."""
        for port in ports:
            url = f"http://{host}:{port}/description.xml" if port != 80 else f"http://{host}/description.xml"
            try:
                async with session.get(url) as response:
                    if response.status != 200:
                        continue
                    info = parse_device_description(await response.text())
                    if info.get("is_renderer"):
                        info["location"] = url
                        info["port"] = port
                        return info
            except Exception:
                continue
        return None

    def _renderer_speaker(self, host: str, info: dict, fallback_name: str, extra: dict) -> NetworkSpeaker:
        return NetworkSpeaker(
            id=make_speaker_id(SpeakerType.DLNA.value, info.get("uuid") or host),
            name=info.get("friendlyName") or fallback_name,
            speaker_type=SpeakerType.DLNA,
            host=host,
            port=info.get("port") or UPNP_PORT,
            model=info.get("modelName"),
            manufacturer=info.get("manufacturer"),
            uuid=info.get("uuid"),
            extra={"location": info["location"], **extra},
        )

    async def _discover_mdns(self, timeout: float) -> list[NetworkSpeaker]:
        """Hosts advertising media services over mDNS that turn out to be UPnP renderers."""
        try:
            import zeroconf  # noqa: F401
        except ImportError:
            logger.debug("zeroconf not installed: mDNS discovery skipped")
            return []
        import aiohttp

        services = await asyncio.get_running_loop().run_in_executor(
            None, browse_mdns, MDNS_SERVICE_TYPES, min(timeout, 5.0)
        )
        hosts: dict[str, dict] = {}
        for service in services:
            if service["addresses"]:
                hosts.setdefault(service["addresses"][0], service)

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
            host_list = list(hosts)
            infos = await asyncio.gather(*(
                self._probe_description(session, host, [hosts[host]["port"] or 80, UPNP_PORT, 80])
                for host in host_list
            ))

        speakers = []
        for host, info in zip(host_list, infos):
            if not info:
                continue
            service = hosts[host]
            service_name = service["name"].split("._")[0]
            service_name = service_name.split("@")[-1]
            speakers.append(self._renderer_speaker(host, info, service_name, {"mdns_type": service["type"]}))
        return speakers

    async def _discover_linkplay(self, timeout: float) -> list[NetworkSpeaker]:
        """
        LinkPlay/Arylic devices often ignore SSDP but serve description.xml on
        port 49152: probe every address in this host's /24.
        """
        import aiohttp

        local_ip = local_ipv4()
        if not local_ip:
            logger.debug("LinkPlay probe skipped: local IP unknown")
            return []
        prefix = ".".join(local_ip.split(".")[:3])
        logger.debug(f"LinkPlay probe of {prefix}.0/24")

        speakers = []
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
            hosts = [f"{prefix}.{i}" for i in range(1, 255) if f"{prefix}.{i}" != local_ip]
            for start in range(0, len(hosts), 50):
                batch = hosts[start:start + 50]
                infos = await asyncio.gather(*(self._probe_description(session, h, [UPNP_PORT]) for h in batch))
                for host, info in zip(batch, infos):
                    if info:
                        speakers.append(self._renderer_speaker(host, info, f"LinkPlay ({host})", {}))
        return speakers

    async def _discover_airplay(self, timeout: float) -> list[NetworkSpeaker]:
        try:
            import pyatv
            from pyatv.const import Protocol
        except ImportError:
            logger.debug("pyatv not installed: AirPlay discovery skipped")
            return []

        loop = asyncio.get_running_loop()
        try:
            devices = await pyatv.scan(loop, timeout=int(timeout))
        except OSError as e:
            if e.errno != errno.EADDRINUSE:
                raise
            # mDNS port held by another service: find AirPlay hosts by unicast
            # mDNS, then let pyatv query just those hosts
            services = await loop.run_in_executor(
                None, browse_mdns, ["_raop._tcp.local.", "_airplay._tcp.local."], min(timeout, 3.0))
            hosts = sorted({address for service in services for address in service["addresses"]})
            devices = await pyatv.scan(loop, hosts=hosts, timeout=int(timeout)) if hosts else []
        speakers = []
        for device in devices:
            try:
                raop = device.get_service(Protocol.RAOP)
                airplay = device.get_service(Protocol.AirPlay)
                if not raop and not airplay:
                    continue
                host = str(device.address)
                identifier = str(device.identifier) if device.identifier else host
                raop_properties = dict(raop.properties) if raop and raop.properties else {}
                airplay_properties = dict(airplay.properties) if airplay and airplay.properties else {}
                model = getattr(device.device_info, "model_str", None) if device.device_info else None
                if not model or model.lower() == "unknown":
                    model = airplay_properties.get("model") or raop_properties.get("am")
                manufacturer = airplay_properties.get("manufacturer") or raop_properties.get("manufacturer")
                speakers.append(NetworkSpeaker(
                    id=make_speaker_id(SpeakerType.AIRPLAY.value, identifier),
                    name=device.name or f"AirPlay device ({host})",
                    speaker_type=SpeakerType.AIRPLAY,
                    host=host,
                    port=(raop.port if raop else None) or (airplay.port if airplay else None) or 7000,
                    model=model,
                    manufacturer=manufacturer,
                    uuid=identifier,
                    extra={
                        "identifier": identifier,
                        "raop_port": raop.port if raop else None,
                        "raop_properties": raop_properties,
                        "services": [str(s.protocol) for s in device.services],
                    },
                ))
            except Exception as e:
                logger.debug(f"AirPlay: skipping {getattr(device, 'name', '?')}: {e}")
        return speakers

    async def _discover_heos(self, timeout: float) -> list[NetworkSpeaker]:
        """
        HEOS (Denon/Marantz, beta): find any HEOS device via SSDP or mDNS, then
        ask it for all players on the network.
        """
        from sonorium.network.heos import heos_command

        loop = asyncio.get_running_loop()
        hosts: list[str] = []
        per_search = min(timeout / 2, 3.0)
        for target in ("urn:schemas-denon-com:device:ACT-Denon:1", "urn:schemas-upnp-org:device:MediaRenderer:1"):
            for headers in await loop.run_in_executor(None, ssdp_search, target, per_search, 2):
                raw = headers.get("_raw", "").lower()
                if any(k in raw for k in ("denon", "heos", "marantz")):
                    host = urlparse(headers["LOCATION"]).hostname
                    if host and host not in hosts:
                        hosts.append(host)
        if not hosts:
            try:
                import zeroconf  # noqa: F401
                services = await loop.run_in_executor(None, browse_mdns, ["_heos-audio._tcp.local."], min(timeout, 3.0))
                for service in services:
                    for address in service["addresses"]:
                        if address not in hosts:
                            hosts.append(address)
            except ImportError:
                pass
        if not hosts:
            return []

        for host in hosts:
            try:
                response = await heos_command(host, "player/get_players", timeout=min(timeout, 5.0))
            except Exception as e:
                logger.debug(f"HEOS: get_players via {host} failed: {e}")
                continue
            payload = response.get("payload")
            if not isinstance(payload, list):
                continue
            speakers = []
            for player in payload:
                pid, ip = player.get("pid"), player.get("ip")
                if pid is None or not ip:
                    continue
                model = player.get("model")
                speakers.append(NetworkSpeaker(
                    id=make_speaker_id(SpeakerType.HEOS.value, str(pid)),
                    name=player.get("name") or f"HEOS device ({ip})",
                    speaker_type=SpeakerType.HEOS,
                    host=ip,
                    port=1255,
                    model=model,
                    manufacturer="Marantz" if model and "marantz" in model.lower() else "Denon",
                    uuid=str(pid),
                    extra={"pid": pid, "version": player.get("version")},
                ))
            return speakers  # one HEOS device lists them all
        return []
