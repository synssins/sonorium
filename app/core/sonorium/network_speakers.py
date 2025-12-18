"""
Native network speaker discovery for Sonorium.
Discovers Chromecast, Sonos, and DLNA/UPnP devices on the local network.
Persists discovered speakers and validates them on startup.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class SpeakerType(Enum):
    CHROMECAST = "chromecast"
    SONOS = "sonos"
    DLNA = "dlna"
    AIRPLAY = "airplay"


class SpeakerStatus(Enum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    CHECKING = "checking"


@dataclass
class NetworkSpeaker:
    """Represents a discovered network speaker."""
    id: str
    name: str
    speaker_type: SpeakerType
    host: str
    port: int = 0
    model: Optional[str] = None
    manufacturer: Optional[str] = None
    is_group: bool = False
    members: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    # Persistence fields
    uuid: Optional[str] = None  # Unique device identifier (survives IP changes)
    status: SpeakerStatus = SpeakerStatus.UNKNOWN
    last_seen: Optional[str] = None  # ISO timestamp

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'type': self.speaker_type.value,
            'host': self.host,
            'port': self.port,
            'model': self.model,
            'manufacturer': self.manufacturer,
            'is_group': self.is_group,
            'members': self.members,
            'uuid': self.uuid,
            'status': self.status.value,
            'last_seen': self.last_seen,
            'available': self.status == SpeakerStatus.AVAILABLE,
            'extra': self.extra,  # Contains DLNA location URL, Chromecast info, etc.
        }

    def to_storage_dict(self) -> dict:
        """Return dict for persistent storage."""
        return {
            'id': self.id,
            'name': self.name,
            'type': self.speaker_type.value,
            'host': self.host,
            'port': self.port,
            'model': self.model,
            'manufacturer': self.manufacturer,
            'is_group': self.is_group,
            'members': self.members,
            'uuid': self.uuid or self.extra.get('uuid'),
            'extra': self.extra,
            'last_seen': self.last_seen,
        }

    @classmethod
    def from_storage_dict(cls, data: dict) -> 'NetworkSpeaker':
        """Create speaker from stored data."""
        return cls(
            id=data['id'],
            name=data['name'],
            speaker_type=SpeakerType(data['type']),
            host=data['host'],
            port=data.get('port', 0),
            model=data.get('model'),
            manufacturer=data.get('manufacturer'),
            is_group=data.get('is_group', False),
            members=data.get('members', []),
            extra=data.get('extra', {}),
            uuid=data.get('uuid'),
            status=SpeakerStatus.UNKNOWN,
            last_seen=data.get('last_seen'),
        )


class NetworkSpeakerDiscovery:
    """Handles discovery of network speakers across multiple protocols."""

    def __init__(self, config_dir: Optional[Path] = None):
        self.speakers: dict[str, NetworkSpeaker] = {}
        self._chromecast_browser = None
        self._discovery_lock = asyncio.Lock()
        self._discovering = False
        self._validating = False

        # Config file for persistence
        if config_dir:
            self._config_dir = Path(config_dir)
        else:
            # Default to user's app data
            import os
            app_data = os.environ.get('APPDATA', os.path.expanduser('~'))
            self._config_dir = Path(app_data) / 'Sonorium'

        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._speakers_file = self._config_dir / 'network_speakers.json'

        # Load saved speakers on init
        self._load_speakers()

    def _load_speakers(self):
        """Load speakers from persistent storage."""
        if not self._speakers_file.exists():
            logger.info("No saved network speakers found")
            return

        try:
            with open(self._speakers_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            for speaker_data in data.get('speakers', []):
                try:
                    speaker = NetworkSpeaker.from_storage_dict(speaker_data)
                    speaker.status = SpeakerStatus.UNKNOWN  # Will validate later
                    self.speakers[speaker.id] = speaker
                    logger.debug(f"Loaded saved speaker: {speaker.name} ({speaker.host})")
                except Exception as e:
                    logger.warning(f"Failed to load speaker: {e}")

            logger.info(f"Loaded {len(self.speakers)} saved network speakers")

        except json.JSONDecodeError as e:
            logger.error(f"Invalid speakers config file: {e}")
        except Exception as e:
            logger.error(f"Failed to load speakers: {e}")

    def _save_speakers(self):
        """Save speakers to persistent storage."""
        try:
            data = {
                'speakers': [s.to_storage_dict() for s in self.speakers.values()]
            }
            with open(self._speakers_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved {len(self.speakers)} network speakers")
        except Exception as e:
            logger.error(f"Failed to save speakers: {e}")

    async def validate_speakers(self) -> dict[str, bool]:
        """
        Validate all saved speakers are still reachable.
        Updates their status and IP if changed.
        Returns dict of speaker_id -> is_available.
        """
        if self._validating:
            logger.warning("Validation already in progress")
            return {}

        self._validating = True
        results = {}

        try:
            logger.info(f"Validating {len(self.speakers)} saved speakers...")

            tasks = []
            for speaker in self.speakers.values():
                speaker.status = SpeakerStatus.CHECKING
                tasks.append(self._validate_speaker(speaker))

            validation_results = await asyncio.gather(*tasks, return_exceptions=True)

            for speaker, result in zip(self.speakers.values(), validation_results):
                if isinstance(result, Exception):
                    logger.warning(f"Validation error for {speaker.name}: {result}")
                    speaker.status = SpeakerStatus.UNAVAILABLE
                    results[speaker.id] = False
                else:
                    results[speaker.id] = result

            # Save updated status
            self._save_speakers()

            available = sum(1 for v in results.values() if v)
            logger.info(f"Validation complete: {available}/{len(results)} speakers available")

        finally:
            self._validating = False

        return results

    async def _validate_speaker(self, speaker: NetworkSpeaker) -> bool:
        """Validate a single speaker is reachable and update its info."""
        import aiohttp
        from datetime import datetime

        # Try to reach the speaker's description.xml
        urls_to_try = []

        if speaker.speaker_type == SpeakerType.DLNA:
            # Try current host first, then common DLNA ports
            if speaker.port:
                urls_to_try.append(f"http://{speaker.host}:{speaker.port}/description.xml")
            urls_to_try.extend([
                f"http://{speaker.host}:49152/description.xml",
                f"http://{speaker.host}/description.xml",
            ])
        elif speaker.speaker_type == SpeakerType.CHROMECAST:
            # Chromecast uses port 8008 for device info
            urls_to_try.append(f"http://{speaker.host}:8008/ssdp/device-desc.xml")
        elif speaker.speaker_type == SpeakerType.SONOS:
            urls_to_try.append(f"http://{speaker.host}:1400/xml/device_description.xml")
        elif speaker.speaker_type == SpeakerType.AIRPLAY:
            # AirPlay speakers don't have HTTP endpoints - validate via TCP port check
            port = speaker.port or 7000
            try:
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                result = sock.connect_ex((speaker.host, port))
                sock.close()
                if result == 0:
                    speaker.last_seen = datetime.now().isoformat()
                    speaker.status = SpeakerStatus.AVAILABLE
                    logger.debug(f"Speaker {speaker.name} is available at {speaker.host}:{port}")
                    return True
                else:
                    speaker.status = SpeakerStatus.UNAVAILABLE
                    logger.info(f"Speaker {speaker.name} is unavailable at {speaker.host}:{port}")
                    return False
            except Exception as e:
                logger.debug(f"AirPlay validation error for {speaker.name}: {e}")
                speaker.status = SpeakerStatus.UNAVAILABLE
                return False

        for url in urls_to_try:
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=3)
                ) as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            text = await response.text()

                            # Verify it's the same device by checking UUID if available
                            if speaker.uuid:
                                import re
                                match = re.search(r'<UDN>uuid:([^<]+)</UDN>', text, re.IGNORECASE)
                                if match:
                                    found_uuid = match.group(1)
                                    if found_uuid != speaker.uuid:
                                        # Different device at this IP
                                        logger.warning(f"UUID mismatch for {speaker.name} at {speaker.host}")
                                        continue

                            # Update last seen
                            speaker.last_seen = datetime.now().isoformat()
                            speaker.status = SpeakerStatus.AVAILABLE
                            logger.debug(f"Speaker {speaker.name} is available at {speaker.host}")
                            return True

            except asyncio.TimeoutError:
                pass
            except aiohttp.ClientError:
                pass
            except Exception as e:
                logger.debug(f"Error checking {url}: {e}")

        # Speaker not reachable at known address - try to find it by UUID
        if speaker.uuid:
            new_host = await self._find_speaker_by_uuid(speaker)
            if new_host and new_host != speaker.host:
                logger.info(f"Speaker {speaker.name} moved from {speaker.host} to {new_host}")
                speaker.host = new_host
                speaker.last_seen = datetime.now().isoformat()
                speaker.status = SpeakerStatus.AVAILABLE
                return True

        speaker.status = SpeakerStatus.UNAVAILABLE
        logger.info(f"Speaker {speaker.name} is unavailable at {speaker.host}")
        return False

    async def _find_speaker_by_uuid(self, speaker: NetworkSpeaker) -> Optional[str]:
        """Try to find a speaker that moved to a different IP by its UUID."""
        # This does a quick subnet scan looking for the UUID
        import socket
        import aiohttp

        try:
            # Get local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()

            subnet_prefix = '.'.join(local_ip.split('.')[:3])

            async def check_host(host: str) -> Optional[str]:
                url = f"http://{host}:49152/description.xml"
                try:
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=1)
                    ) as session:
                        async with session.get(url) as response:
                            if response.status == 200:
                                text = await response.text()
                                import re
                                match = re.search(r'<UDN>uuid:([^<]+)</UDN>', text, re.IGNORECASE)
                                if match and match.group(1) == speaker.uuid:
                                    return host
                except Exception:
                    pass
                return None

            # Check likely candidates first (nearby IPs)
            old_last_octet = int(speaker.host.split('.')[-1])
            priority_hosts = [
                f"{subnet_prefix}.{old_last_octet + i}"
                for i in range(-5, 6) if 1 <= old_last_octet + i <= 254
            ]

            # Check priority hosts first
            tasks = [check_host(h) for h in priority_hosts]
            results = await asyncio.gather(*tasks)
            for result in results:
                if result:
                    return result

        except Exception as e:
            logger.debug(f"Error searching for speaker by UUID: {e}")

        return None

    async def discover_all(self, timeout: float = 10.0, merge_with_saved: bool = True) -> list[NetworkSpeaker]:
        """
        Discover all network speakers on the local network.

        Args:
            timeout: How long to wait for discovery (seconds)
            merge_with_saved: If True, merge newly discovered speakers with saved ones

        Returns:
            List of discovered speakers
        """
        from datetime import datetime

        async with self._discovery_lock:
            if self._discovering:
                logger.warning("Discovery already in progress")
                return list(self.speakers.values())

            self._discovering = True

            # Keep saved speakers if merging
            saved_speakers = dict(self.speakers) if merge_with_saved else {}
            self.speakers.clear()

            try:
                # Run all discovery methods concurrently
                tasks = [
                    asyncio.create_task(self._discover_chromecast(timeout)),
                    asyncio.create_task(self._discover_sonos(timeout)),
                    asyncio.create_task(self._discover_dlna(timeout)),
                    asyncio.create_task(self._discover_mdns(timeout)),
                    asyncio.create_task(self._discover_linkplay(timeout)),
                    asyncio.create_task(self._discover_airplay(timeout)),
                ]

                results = await asyncio.gather(*tasks, return_exceptions=True)

                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        protocol = ['Chromecast', 'Sonos', 'DLNA', 'mDNS', 'Linkplay', 'AirPlay'][i]
                        logger.error(f"{protocol} discovery failed: {result}")

                # Mark all discovered speakers as available
                now = datetime.now().isoformat()
                for speaker in self.speakers.values():
                    speaker.status = SpeakerStatus.AVAILABLE
                    speaker.last_seen = now
                    # Extract UUID from extra if not set
                    if not speaker.uuid and speaker.extra.get('uuid'):
                        speaker.uuid = speaker.extra['uuid']

                # Merge with saved speakers (keep saved ones that weren't re-discovered)
                if merge_with_saved:
                    for saved_id, saved_speaker in saved_speakers.items():
                        if saved_id not in self.speakers:
                            # Check if same device found with different ID (by UUID)
                            found_match = False
                            if saved_speaker.uuid:
                                for new_speaker in self.speakers.values():
                                    if new_speaker.uuid == saved_speaker.uuid:
                                        # Same device, different ID - update saved speaker's host
                                        logger.info(f"Speaker {saved_speaker.name} found with new ID, updating host")
                                        found_match = True
                                        break

                            if not found_match:
                                # Keep saved speaker but mark as unavailable (not found in this scan)
                                saved_speaker.status = SpeakerStatus.UNAVAILABLE
                                self.speakers[saved_id] = saved_speaker

                # Save all speakers
                self._save_speakers()

                logger.info(f"Discovery complete: found {len(self.speakers)} speakers")
                return list(self.speakers.values())

            finally:
                self._discovering = False

    async def _discover_chromecast(self, timeout: float) -> list[NetworkSpeaker]:
        """Discover Chromecast devices using pychromecast."""
        discovered = []

        try:
            import pychromecast

            logger.info("Starting Chromecast discovery...")

            # Run the blocking discovery in a thread
            def _discover():
                services, browser = pychromecast.discovery.discover_chromecasts(timeout=timeout)
                browser.stop_discovery()
                return services

            loop = asyncio.get_event_loop()
            services = await loop.run_in_executor(None, _discover)

            for service in services:
                try:
                    # Create speaker from service info
                    speaker_id = f"chromecast_{service.uuid}"
                    speaker = NetworkSpeaker(
                        id=speaker_id,
                        name=service.friendly_name or str(service.uuid),
                        speaker_type=SpeakerType.CHROMECAST,
                        host=str(service.host),
                        port=service.port or 8009,
                        model=service.model_name,
                        manufacturer=service.manufacturer,
                        extra={
                            'uuid': str(service.uuid),
                            'cast_type': service.cast_type,
                        }
                    )
                    self.speakers[speaker_id] = speaker
                    discovered.append(speaker)
                    logger.debug(f"Found Chromecast: {speaker.name} at {speaker.host}")
                except Exception as e:
                    logger.warning(f"Error processing Chromecast service: {e}")

            logger.info(f"Chromecast discovery found {len(discovered)} devices")

        except ImportError:
            logger.warning("pychromecast not installed - Chromecast discovery disabled")
        except Exception as e:
            logger.error(f"Chromecast discovery error: {e}")

        return discovered

    async def _discover_sonos(self, timeout: float) -> list[NetworkSpeaker]:
        """Discover Sonos devices using soco."""
        discovered = []

        try:
            import soco

            logger.info("Starting Sonos discovery...")

            # Run the blocking discovery in a thread
            def _discover():
                return list(soco.discover(timeout=timeout) or [])

            loop = asyncio.get_event_loop()
            devices = await loop.run_in_executor(None, _discover)

            for device in devices:
                try:
                    speaker_id = f"sonos_{device.uid}"
                    speaker = NetworkSpeaker(
                        id=speaker_id,
                        name=device.player_name,
                        speaker_type=SpeakerType.SONOS,
                        host=device.ip_address,
                        port=1400,
                        model=device.speaker_info.get('model_name'),
                        manufacturer="Sonos",
                        is_group=device.is_coordinator and len(device.group.members) > 1,
                        members=[m.uid for m in device.group.members] if device.is_coordinator else [],
                        extra={
                            'uid': device.uid,
                            'zone_name': device.speaker_info.get('zone_name'),
                            'is_coordinator': device.is_coordinator,
                        }
                    )
                    self.speakers[speaker_id] = speaker
                    discovered.append(speaker)
                    logger.debug(f"Found Sonos: {speaker.name} at {speaker.host}")
                except Exception as e:
                    logger.warning(f"Error processing Sonos device: {e}")

            logger.info(f"Sonos discovery found {len(discovered)} devices")

        except ImportError:
            logger.warning("soco not installed - Sonos discovery disabled")
        except Exception as e:
            logger.error(f"Sonos discovery error: {e}")

        return discovered

    async def _discover_dlna(self, timeout: float) -> list[NetworkSpeaker]:
        """Discover DLNA/UPnP media renderers using SSDP."""
        discovered = []

        try:
            import socket
            import aiohttp
            from urllib.parse import urlparse

            logger.info("Starting DLNA/SSDP discovery...")

            # SSDP multicast address
            SSDP_ADDR = "239.255.255.250"
            SSDP_PORT = 1900

            # Search for multiple device types
            search_targets = [
                "urn:schemas-upnp-org:device:MediaRenderer:1",
                "urn:schemas-upnp-org:service:AVTransport:1",
                "ssdp:all"  # Catch-all for devices that don't advertise specific types
            ]

            devices_found = {}

            async def fetch_device_info(location: str) -> dict:
                """Fetch device description XML to get friendly name and model."""
                try:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                        async with session.get(location) as response:
                            if response.status == 200:
                                text = await response.text()
                                import re
                                info = {}

                                # Extract friendlyName
                                match = re.search(r'<friendlyName>([^<]+)</friendlyName>', text, re.IGNORECASE)
                                if match:
                                    info['friendlyName'] = match.group(1)

                                # Extract modelName
                                match = re.search(r'<modelName>([^<]+)</modelName>', text, re.IGNORECASE)
                                if match:
                                    info['modelName'] = match.group(1)

                                # Extract manufacturer
                                match = re.search(r'<manufacturer>([^<]+)</manufacturer>', text, re.IGNORECASE)
                                if match:
                                    info['manufacturer'] = match.group(1)

                                # Check if it's a media renderer
                                if 'MediaRenderer' in text or 'AVTransport' in text:
                                    info['is_renderer'] = True

                                return info
                except Exception as e:
                    logger.debug(f"Failed to fetch device info from {location}: {e}")
                return {}

            def ssdp_search(search_target: str) -> list[dict]:
                """Perform SSDP M-SEARCH and collect responses."""
                responses = []

                # Create UDP socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.settimeout(timeout / len(search_targets))

                # M-SEARCH request
                search_msg = (
                    f"M-SEARCH * HTTP/1.1\r\n"
                    f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
                    f"MAN: \"ssdp:discover\"\r\n"
                    f"MX: 3\r\n"
                    f"ST: {search_target}\r\n"
                    f"\r\n"
                )

                try:
                    sock.sendto(search_msg.encode(), (SSDP_ADDR, SSDP_PORT))
                    logger.debug(f"Sent SSDP search for {search_target}")

                    while True:
                        try:
                            data, addr = sock.recvfrom(4096)
                            response_text = data.decode('utf-8', errors='ignore')

                            # Parse response headers
                            headers = {}
                            for line in response_text.split('\r\n'):
                                if ':' in line:
                                    key, value = line.split(':', 1)
                                    headers[key.upper().strip()] = value.strip()

                            if 'LOCATION' in headers:
                                headers['_addr'] = addr
                                responses.append(headers)
                                logger.debug(f"SSDP response from {addr}: {headers.get('LOCATION', 'no location')}")

                        except socket.timeout:
                            break
                        except Exception as e:
                            logger.debug(f"Error receiving SSDP response: {e}")
                            break
                finally:
                    sock.close()

                return responses

            # Run SSDP searches in thread pool
            loop = asyncio.get_event_loop()
            all_responses = []

            for st in search_targets:
                try:
                    responses = await loop.run_in_executor(None, ssdp_search, st)
                    all_responses.extend(responses)
                except Exception as e:
                    logger.warning(f"SSDP search for {st} failed: {e}")

            # Process unique devices
            for headers in all_responses:
                try:
                    location = headers.get('LOCATION', '')
                    usn = headers.get('USN', '')

                    # Skip if no location or already processed
                    if not location or location in devices_found:
                        continue

                    devices_found[location] = headers

                    # Parse host from location URL
                    parsed = urlparse(location)
                    host = parsed.hostname or ''
                    port = parsed.port or 80

                    # Fetch device details
                    device_details = await fetch_device_info(location)

                    # Skip devices that aren't media renderers (unless ssdp:all found something interesting)
                    server = headers.get('SERVER', '').lower()
                    is_media_device = (
                        device_details.get('is_renderer') or
                        'mediarenderer' in usn.lower() or
                        'avtransport' in usn.lower() or
                        'arylic' in server or
                        'linkplay' in server or
                        'dlna' in server
                    )

                    if not is_media_device and 'ssdp:all' in str(headers):
                        # For ssdp:all responses, only include if it's a known audio device
                        manufacturer = device_details.get('manufacturer', '').lower()
                        model = device_details.get('modelName', '').lower()
                        if not any(kw in manufacturer + model for kw in ['arylic', 'linkplay', 'audio', 'speaker', 'sonos']):
                            continue

                    # Create speaker ID from USN or location
                    if usn:
                        device_id = usn.split('::')[0].replace('uuid:', '')
                    else:
                        device_id = host.replace('.', '_')
                    speaker_id = f"dlna_{device_id[:20]}"

                    # Skip if we already have this speaker
                    if speaker_id in self.speakers:
                        continue

                    name = device_details.get('friendlyName', f"DLNA Device ({host})")
                    model = device_details.get('modelName')
                    manufacturer = device_details.get('manufacturer', headers.get('SERVER', '').split('/')[0])

                    speaker = NetworkSpeaker(
                        id=speaker_id,
                        name=name,
                        speaker_type=SpeakerType.DLNA,
                        host=host,
                        port=port,
                        model=model,
                        manufacturer=manufacturer,
                        extra={
                            'usn': usn,
                            'location': location,
                            'server': headers.get('SERVER', ''),
                        }
                    )
                    self.speakers[speaker_id] = speaker
                    discovered.append(speaker)
                    logger.info(f"Found DLNA: {speaker.name} ({model or 'unknown model'}) at {speaker.host}")

                except Exception as e:
                    logger.warning(f"Error processing DLNA device: {e}")

            logger.info(f"DLNA discovery found {len(discovered)} devices")

        except Exception as e:
            logger.error(f"DLNA discovery error: {e}")
            import traceback
            logger.error(traceback.format_exc())

        return discovered

    async def _discover_mdns(self, timeout: float) -> list[NetworkSpeaker]:
        """Discover devices via mDNS/Bonjour/Avahi using zeroconf."""
        discovered = []

        try:
            from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
            import aiohttp
            import socket
            import time

            logger.info("Starting mDNS discovery...")

            # Service types that media renderers typically advertise
            service_types = [
                "_raop._tcp.local.",      # AirPlay
                "_airplay._tcp.local.",   # AirPlay
                "_googlecast._tcp.local.", # Chromecast (backup)
                "_sonos._tcp.local.",     # Sonos
                "_spotify-connect._tcp.local.",  # Spotify Connect devices
                "_http._tcp.local.",      # Generic HTTP (some Linkplay)
                "_linkplay._tcp.local.",  # Linkplay specific
                "_arylic._tcp.local.",    # Arylic specific
            ]

            devices_found = {}

            class MDNSListener(ServiceListener):
                def __init__(self):
                    self.services = []

                def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                    try:
                        info = zc.get_service_info(type_, name, timeout=3000)
                        if info:
                            self.services.append({
                                'name': name,
                                'type': type_,
                                'server': info.server,
                                'port': info.port,
                                'addresses': [socket.inet_ntoa(addr) for addr in info.addresses] if info.addresses else [],
                                'properties': {k.decode() if isinstance(k, bytes) else k:
                                              v.decode() if isinstance(v, bytes) else v
                                              for k, v in info.properties.items()} if info.properties else {}
                            })
                            logger.debug(f"mDNS found: {name} ({type_}) at {info.server}:{info.port}")
                    except Exception as e:
                        logger.debug(f"Error getting service info for {name}: {e}")

                def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                    pass

                def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                    pass

            # Run mDNS discovery in thread
            loop = asyncio.get_event_loop()

            def run_mdns_scan():
                zc = Zeroconf()
                listener = MDNSListener()
                browsers = []

                try:
                    for stype in service_types:
                        try:
                            browser = ServiceBrowser(zc, stype, listener)
                            browsers.append(browser)
                        except Exception as e:
                            logger.debug(f"Could not browse {stype}: {e}")

                    # Wait for discovery
                    time.sleep(min(timeout, 5))

                    return listener.services
                finally:
                    zc.close()

            services = await loop.run_in_executor(None, run_mdns_scan)

            # Process discovered services
            async def fetch_device_info(host: str, port: int) -> dict:
                """Try to fetch UPnP description from the device."""
                urls_to_try = [
                    f"http://{host}:{port}/description.xml",
                    f"http://{host}:49152/description.xml",
                    f"http://{host}/description.xml",
                ]
                for url in urls_to_try:
                    try:
                        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
                            async with session.get(url) as response:
                                if response.status == 200:
                                    text = await response.text()
                                    if 'MediaRenderer' in text or 'AVTransport' in text:
                                        import re
                                        info = {'location': url}
                                        match = re.search(r'<friendlyName>([^<]+)</friendlyName>', text, re.IGNORECASE)
                                        if match:
                                            info['friendlyName'] = match.group(1)
                                        match = re.search(r'<modelName>([^<]+)</modelName>', text, re.IGNORECASE)
                                        if match:
                                            info['modelName'] = match.group(1)
                                        match = re.search(r'<manufacturer>([^<]+)</manufacturer>', text, re.IGNORECASE)
                                        if match:
                                            info['manufacturer'] = match.group(1)
                                        match = re.search(r'<UDN>uuid:([^<]+)</UDN>', text, re.IGNORECASE)
                                        if match:
                                            info['uuid'] = match.group(1)
                                        return info
                    except Exception:
                        pass
                return {}

            for service in services:
                try:
                    # Skip Chromecast and Sonos - handled by dedicated discovery
                    if '_googlecast._tcp' in service['type'] or '_sonos._tcp' in service['type']:
                        continue

                    addresses = service.get('addresses', [])
                    if not addresses:
                        continue

                    host = addresses[0]
                    port = service.get('port', 80)

                    # Skip if we've already found this host
                    if host in devices_found:
                        continue
                    devices_found[host] = True

                    # Try to get device info
                    device_info = await fetch_device_info(host, port)

                    if not device_info:
                        # Not a media renderer, skip
                        continue

                    # Create speaker
                    device_id = device_info.get('uuid', host.replace('.', '_'))[:20]
                    speaker_id = f"dlna_{device_id}"

                    # Skip if already found
                    if speaker_id in self.speakers:
                        continue

                    # Get name from mDNS service name or device info
                    service_name = service['name'].replace('._raop._tcp.local.', '').replace('._airplay._tcp.local.', '')
                    service_name = service_name.split('@')[-1] if '@' in service_name else service_name
                    name = device_info.get('friendlyName', service_name)
                    model = device_info.get('modelName')
                    manufacturer = device_info.get('manufacturer', 'Unknown')

                    speaker = NetworkSpeaker(
                        id=speaker_id,
                        name=name,
                        speaker_type=SpeakerType.DLNA,
                        host=host,
                        port=49152,  # Standard UPnP port for control
                        model=model,
                        manufacturer=manufacturer,
                        extra={
                            'location': device_info.get('location', ''),
                            'uuid': device_info.get('uuid', ''),
                            'mdns_type': service['type'],
                        }
                    )
                    self.speakers[speaker_id] = speaker
                    discovered.append(speaker)
                    logger.info(f"Found via mDNS: {name} ({model or 'unknown'}) at {host}")

                except Exception as e:
                    logger.debug(f"Error processing mDNS service: {e}")

            logger.info(f"mDNS discovery found {len(discovered)} devices")

        except ImportError:
            logger.warning("zeroconf not installed - mDNS discovery disabled")
        except Exception as e:
            logger.error(f"mDNS discovery error: {e}")
            import traceback
            logger.error(traceback.format_exc())

        return discovered

    async def _discover_linkplay(self, timeout: float) -> list[NetworkSpeaker]:
        """
        Discover Linkplay/Arylic devices by probing known ports on the local subnet.
        These devices often don't respond to SSDP but have a description.xml on port 49152.
        """
        discovered = []

        try:
            import socket
            import aiohttp
            from urllib.parse import urlparse

            logger.info("Starting Linkplay direct probe discovery...")

            # Get local IP to determine subnet
            def get_local_ip():
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    ip = s.getsockname()[0]
                    s.close()
                    return ip
                except Exception:
                    return None

            local_ip = get_local_ip()
            if not local_ip:
                logger.warning("Could not determine local IP for Linkplay scan")
                return discovered

            # Get subnet (assume /24)
            subnet_prefix = '.'.join(local_ip.split('.')[:3])
            logger.info(f"Scanning subnet {subnet_prefix}.0/24 for Linkplay devices...")

            # Linkplay devices typically listen on port 49152 for UPnP
            LINKPLAY_PORT = 49152

            async def probe_host(host: str) -> Optional[NetworkSpeaker]:
                """Probe a single host for Linkplay device."""
                url = f"http://{host}:{LINKPLAY_PORT}/description.xml"
                try:
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=2)
                    ) as session:
                        async with session.get(url) as response:
                            if response.status == 200:
                                text = await response.text()

                                # Check if it's a media renderer
                                if 'MediaRenderer' not in text and 'AVTransport' not in text:
                                    return None

                                import re
                                info = {}

                                # Extract device info
                                match = re.search(r'<friendlyName>([^<]+)</friendlyName>', text, re.IGNORECASE)
                                if match:
                                    info['friendlyName'] = match.group(1)

                                match = re.search(r'<modelName>([^<]+)</modelName>', text, re.IGNORECASE)
                                if match:
                                    info['modelName'] = match.group(1)

                                match = re.search(r'<manufacturer>([^<]+)</manufacturer>', text, re.IGNORECASE)
                                if match:
                                    info['manufacturer'] = match.group(1)

                                match = re.search(r'<UDN>uuid:([^<]+)</UDN>', text, re.IGNORECASE)
                                if match:
                                    info['uuid'] = match.group(1)

                                # Create speaker
                                device_id = info.get('uuid', host.replace('.', '_'))[:20]
                                speaker_id = f"dlna_{device_id}"

                                # Skip if already found by SSDP
                                if speaker_id in self.speakers:
                                    return None

                                name = info.get('friendlyName', f"Linkplay ({host})")
                                model = info.get('modelName')
                                manufacturer = info.get('manufacturer', 'Linkplay')

                                speaker = NetworkSpeaker(
                                    id=speaker_id,
                                    name=name,
                                    speaker_type=SpeakerType.DLNA,
                                    host=host,
                                    port=LINKPLAY_PORT,
                                    model=model,
                                    manufacturer=manufacturer,
                                    extra={
                                        'location': url,
                                        'uuid': info.get('uuid', ''),
                                    }
                                )
                                logger.info(f"Found Linkplay: {name} ({model}) at {host}")
                                return speaker

                except asyncio.TimeoutError:
                    pass
                except aiohttp.ClientError:
                    pass
                except Exception as e:
                    logger.debug(f"Error probing {host}: {e}")

                return None

            # Probe all hosts in subnet concurrently (in batches to avoid overwhelming)
            BATCH_SIZE = 50
            all_hosts = [f"{subnet_prefix}.{i}" for i in range(1, 255)]

            for batch_start in range(0, len(all_hosts), BATCH_SIZE):
                batch = all_hosts[batch_start:batch_start + BATCH_SIZE]
                tasks = [probe_host(host) for host in batch]
                results = await asyncio.gather(*tasks)

                for speaker in results:
                    if speaker and speaker.id not in self.speakers:
                        self.speakers[speaker.id] = speaker
                        discovered.append(speaker)

            logger.info(f"Linkplay probe found {len(discovered)} devices")

        except Exception as e:
            logger.error(f"Linkplay discovery error: {e}")
            import traceback
            logger.error(traceback.format_exc())

        return discovered

    async def _discover_airplay(self, timeout: float) -> list[NetworkSpeaker]:
        """Discover AirPlay devices using pyatv."""
        discovered = []

        try:
            import pyatv

            logger.info("Starting AirPlay discovery...")

            # Scan for AirPlay devices
            loop = asyncio.get_event_loop()
            devices = await pyatv.scan(loop, timeout=timeout)

            for device in devices:
                try:
                    # Check if device supports AirPlay
                    airplay_service = device.get_service(pyatv.const.Protocol.AirPlay)
                    raop_service = device.get_service(pyatv.const.Protocol.RAOP)

                    if not airplay_service and not raop_service:
                        logger.debug(f"Device {device.name} doesn't support AirPlay, skipping")
                        continue

                    # Use the device's identifier as unique ID
                    device_id = str(device.identifier) if device.identifier else device.address.replace('.', '_')
                    speaker_id = f"airplay_{device_id[:20]}"

                    # Skip if already found
                    if speaker_id in self.speakers:
                        continue

                    # Get the appropriate port
                    port = 7000  # Default AirPlay port
                    if airplay_service and airplay_service.port:
                        port = airplay_service.port
                    elif raop_service and raop_service.port:
                        port = raop_service.port

                    # Determine model from device info (convert to string for JSON serialization)
                    model = None
                    if device.device_info and device.device_info.model:
                        model = str(device.device_info.model)

                    speaker = NetworkSpeaker(
                        id=speaker_id,
                        name=device.name or f"AirPlay Device ({device.address})",
                        speaker_type=SpeakerType.AIRPLAY,
                        host=str(device.address),
                        port=port,
                        model=model,
                        manufacturer="Apple",
                        extra={
                            'identifier': str(device.identifier) if device.identifier else None,
                            'all_identifiers': [str(i) for i in device.all_identifiers] if device.all_identifiers else [],
                            'services': [str(s.protocol) for s in device.services],
                        }
                    )
                    self.speakers[speaker_id] = speaker
                    discovered.append(speaker)
                    logger.info(f"Found AirPlay: {device.name} at {device.address}")

                except Exception as e:
                    logger.debug(f"Error processing AirPlay device: {e}")

            logger.info(f"AirPlay discovery found {len(discovered)} devices")

        except ImportError:
            logger.warning("pyatv not installed - AirPlay discovery disabled")
        except Exception as e:
            logger.error(f"AirPlay discovery error: {e}")
            import traceback
            logger.error(traceback.format_exc())

        return discovered

    def get_speaker(self, speaker_id: str) -> Optional[NetworkSpeaker]:
        """Get a speaker by ID."""
        return self.speakers.get(speaker_id)

    def get_speakers_by_type(self, speaker_type: SpeakerType) -> list[NetworkSpeaker]:
        """Get all speakers of a specific type."""
        return [s for s in self.speakers.values() if s.speaker_type == speaker_type]


# Global instance - initialized with proper config dir
_discovery: NetworkSpeakerDiscovery | None = None


def _get_config_dir() -> Path:
    """Get the config directory, respecting SONORIUM_DATA_DIR for Docker."""
    import os
    data_dir = os.environ.get('SONORIUM_DATA_DIR')
    if data_dir:
        # Docker environment - use /app/data/config
        return Path(data_dir) / 'config'
    else:
        # Windows/local environment - use APPDATA
        app_data = os.environ.get('APPDATA', os.path.expanduser('~'))
        return Path(app_data) / 'Sonorium'


def _get_discovery() -> NetworkSpeakerDiscovery:
    """Get or create the global discovery instance."""
    global _discovery
    if _discovery is None:
        config_dir = _get_config_dir()
        logger.info(f"Initializing speaker discovery with config dir: {config_dir}")
        _discovery = NetworkSpeakerDiscovery(config_dir=config_dir)
    return _discovery


def init_speaker_discovery(config_dir: Path | str | None = None) -> NetworkSpeakerDiscovery:
    """
    Initialize the global speaker discovery with a specific config directory.
    Call this early in application startup to ensure speakers persist correctly.
    """
    global _discovery
    if config_dir is None:
        config_dir = _get_config_dir()
    elif isinstance(config_dir, str):
        config_dir = Path(config_dir)

    logger.info(f"Initializing speaker discovery with config dir: {config_dir}")
    _discovery = NetworkSpeakerDiscovery(config_dir=config_dir)
    return _discovery


async def discover_network_speakers(timeout: float = 10.0) -> list[dict]:
    """
    Discover all network speakers.

    Returns list of speaker dicts for API responses.
    """
    discovery = _get_discovery()
    speakers = await discovery.discover_all(timeout)
    return [s.to_dict() for s in speakers]


def get_discovered_speakers() -> list[dict]:
    """Get previously discovered speakers without re-scanning."""
    discovery = _get_discovery()
    return [s.to_dict() for s in discovery.speakers.values()]


def get_speaker(speaker_id: str) -> Optional[NetworkSpeaker]:
    """Get a specific speaker by ID."""
    discovery = _get_discovery()
    return discovery.get_speaker(speaker_id)


async def validate_network_speakers() -> dict[str, bool]:
    """
    Validate all saved speakers are still reachable.
    Updates their status and handles IP changes.

    Returns dict of speaker_id -> is_available.
    """
    discovery = _get_discovery()
    return await discovery.validate_speakers()


def get_available_speakers() -> list[dict]:
    """Get only speakers that are currently available."""
    discovery = _get_discovery()
    return [
        s.to_dict() for s in discovery.speakers.values()
        if s.status == SpeakerStatus.AVAILABLE
    ]


def is_speaker_available(speaker_id: str) -> bool:
    """Check if a specific speaker is available."""
    discovery = _get_discovery()
    speaker = discovery.get_speaker(speaker_id)
    if speaker:
        return speaker.status == SpeakerStatus.AVAILABLE
    return False
