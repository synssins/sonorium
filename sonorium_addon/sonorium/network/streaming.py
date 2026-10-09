"""
Playing Sonorium streams on network speakers, ported from the Windows app
(app/core/sonorium/streaming.py).

Most speakers pull the stream themselves: Sonorium hands them the channel
URL (Chromecast, Sonos, DLNA, LinkPlay HTTP API, HEOS). AirPlay is the
exception: Sonorium reads its own stream and pushes it over RAOP (pyatv).
"""
from __future__ import annotations

import asyncio
import html
import ipaddress
import time
import uuid as uuid_lib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from sonorium.obs import logger
from sonorium.network.models import NetworkSpeaker, SpeakerType

STREAM_TITLE = "Sonorium"

# Name/model/manufacturer patterns of LinkPlay/Arylic devices, which also
# advertise AirPlay but play far more reliably via their HTTP API
LINKPLAY_PATTERNS = ["arylic", "linkplay", "up2stream", "a50", "a30", "office_c"]


class StreamingState(str, Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    PLAYING = "playing"
    ERROR = "error"


@dataclass
class StreamingSession:
    """An active stream to one network speaker."""
    speaker_id: str
    speaker_type: SpeakerType
    stream_url: str
    state: StreamingState = StreamingState.CONNECTING
    error_message: Optional[str] = None
    device: Any = field(default=None, repr=False)
    # Protocol-specific state (AirPlay tasks, LinkPlay host, ...)
    handles: dict = field(default_factory=dict, repr=False)


def is_linkplay_device(speaker: NetworkSpeaker) -> bool:
    """Whether an AirPlay speaker is LinkPlay/Arylic hardware with the HTTP API."""
    fields = [speaker.name, speaker.manufacturer, speaker.model, str(speaker.extra.get("identifier") or "")]
    text = " ".join(f or "" for f in fields).lower()
    return any(pattern in text for pattern in LINKPLAY_PATTERNS)


def didl_metadata(stream_url: str, title: str = STREAM_TITLE) -> str:
    """DIDL-Lite metadata with an explicit MP3 MIME type (some renderers need it)."""
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="1" parentID="0" restricted="1">'
        f"<dc:title>{html.escape(title)}</dc:title>"
        "<upnp:class>object.item.audioItem.musicTrack</upnp:class>"
        '<res protocolInfo="http-get:*:audio/mpeg:DLNA.ORG_PN=MP3;DLNA.ORG_OP=01;'
        'DLNA.ORG_FLAGS=01700000000000000000000000000000">'
        f"{html.escape(stream_url)}</res>"
        "</item></DIDL-Lite>"
    )


class NetworkStreamingManager:
    """Starts, stops and sets the volume of streams to network speakers."""

    def __init__(self):
        self.sessions: dict[str, StreamingSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, speaker_id: str) -> asyncio.Lock:
        return self._locks.setdefault(speaker_id, asyncio.Lock())

    def get_session(self, speaker_id: str) -> Optional[StreamingSession]:
        return self.sessions.get(speaker_id)

    def is_playing(self, speaker_id: str) -> bool:
        session = self.sessions.get(speaker_id)
        return bool(session and session.state == StreamingState.PLAYING)

    async def start_streaming(self, speaker: NetworkSpeaker, stream_url: str) -> bool:
        """Play stream_url on the speaker, replacing any stream Sonorium sent it before."""
        async with self._lock(speaker.id):
            if speaker.id in self.sessions:
                await self._stop_session(self.sessions.pop(speaker.id))

            session = StreamingSession(speaker.id, speaker.speaker_type, stream_url)
            self.sessions[speaker.id] = session
            starters = {
                SpeakerType.CHROMECAST: self._start_chromecast,
                SpeakerType.SONOS: self._start_sonos,
                SpeakerType.DLNA: self._start_dlna,
                SpeakerType.AIRPLAY: self._start_airplay,
                SpeakerType.HEOS: self._start_heos,
            }
            try:
                ok = await starters[speaker.speaker_type](session, speaker)
            except Exception as e:
                logger.debug(f"{speaker.id}: start failed", exc_info=True)
                session.error_message = str(e)
                ok = False

            if ok:
                session.state = StreamingState.PLAYING
                logger.info(f"Network speaker '{speaker.name}' ({speaker.speaker_type.value}) playing {stream_url}")
            else:
                session.state = StreamingState.ERROR
                logger.error(f"Could not play on network speaker '{speaker.name}' ({speaker.speaker_type.value}, {speaker.host}): {session.error_message or 'unknown error'}")
                await self._stop_session(session)
                self.sessions.pop(speaker.id, None)
            return ok

    async def stop_streaming(self, speaker_id: str) -> bool:
        """Stop the stream Sonorium sent to a speaker (no-op if there is none)."""
        async with self._lock(speaker_id):
            session = self.sessions.pop(speaker_id, None)
            if not session:
                return True
            ok = await self._stop_session(session)
            logger.info(f"Network speaker {speaker_id} stopped")
            return ok

    async def stop_all(self):
        await asyncio.gather(*(self.stop_streaming(sid) for sid in list(self.sessions)), return_exceptions=True)

    async def _stop_session(self, session: StreamingSession) -> bool:
        stoppers = {
            SpeakerType.CHROMECAST: self._stop_chromecast,
            SpeakerType.SONOS: self._stop_sonos,
            SpeakerType.DLNA: self._stop_dlna,
            SpeakerType.AIRPLAY: self._stop_airplay,
            SpeakerType.HEOS: self._stop_heos,
        }
        try:
            await stoppers[session.speaker_type](session)
            session.state = StreamingState.STOPPED
            return True
        except Exception as e:
            logger.warning(f"Error stopping network speaker {session.speaker_id}: {e}")
            return False

    async def set_volume(self, speaker: NetworkSpeaker, level: float) -> bool:
        """Set speaker volume, 0.0-1.0. Best effort: not every protocol supports it."""
        level = max(0.0, min(1.0, level))
        session = self.sessions.get(speaker.id)
        setters = {
            SpeakerType.CHROMECAST: self._volume_chromecast,
            SpeakerType.SONOS: self._volume_sonos,
            SpeakerType.DLNA: self._volume_dlna,
            SpeakerType.AIRPLAY: self._volume_airplay,
            SpeakerType.HEOS: self._volume_heos,
        }
        try:
            return bool(await setters[speaker.speaker_type](speaker, session, level))
        except Exception as e:
            logger.debug(f"{speaker.id}: volume not set: {e}")
            return False

    # --- Chromecast ---
    # Connects by IP like the add-on's ha/cast_player.py (no rediscovery).

    @staticmethod
    def _connect_chromecast(speaker: NetworkSpeaker):
        from pychromecast import Chromecast
        from pychromecast.models import CastInfo, HostServiceInfo

        port = speaker.port or 8009
        try:
            cast_uuid = uuid_lib.UUID(speaker.uuid) if speaker.uuid else uuid_lib.uuid4()
        except ValueError:
            cast_uuid = uuid_lib.uuid4()
        info = CastInfo(
            services={HostServiceInfo(speaker.host, port)},
            uuid=cast_uuid,
            model_name=speaker.model,
            friendly_name=speaker.name,
            host=speaker.host,
            port=port,
            cast_type=speaker.extra.get("cast_type") or "cast",
            manufacturer=speaker.manufacturer,
        )
        cast = Chromecast(cast_info=info)
        cast.wait(timeout=10)
        return cast

    async def _start_chromecast(self, session: StreamingSession, speaker: NetworkSpeaker) -> bool:
        def play():
            cast = self._connect_chromecast(speaker)
            session.device = cast
            mc = cast.media_controller
            mc.play_media(session.stream_url, "audio/mpeg", title=STREAM_TITLE)
            for _ in range(20):
                time.sleep(0.5)
                if mc.status.player_state in ("PLAYING", "BUFFERING"):
                    return True
                if mc.status.idle_reason:
                    session.error_message = f"Cast device went idle: {mc.status.idle_reason}"
                    return False
            session.error_message = "Timed out waiting for the Cast device to start playing"
            return False

        return await asyncio.get_running_loop().run_in_executor(None, play)

    async def _stop_chromecast(self, session: StreamingSession):
        cast = session.device
        if not cast:
            return

        def stop():
            try:
                cast.media_controller.stop()
                cast.quit_app()
            finally:
                cast.disconnect(timeout=5)

        await asyncio.get_running_loop().run_in_executor(None, stop)

    async def _volume_chromecast(self, speaker, session, level) -> bool:
        if not session or not session.device:
            return False
        cast = session.device
        # Chromecast.set_volume was dropped in pychromecast 14; the receiver controller has it
        set_volume = getattr(cast, "set_volume", None) or cast.receiver_controller.set_volume
        await asyncio.get_running_loop().run_in_executor(None, set_volume, level)
        return True

    # --- Sonos ---
    # Same call the add-on's ha/sonos_player.py uses (proven): force_radio=True
    # makes Sonos treat the endless stream as a radio station.

    async def _start_sonos(self, session: StreamingSession, speaker: NetworkSpeaker) -> bool:
        import soco

        def play():
            device = soco.SoCo(speaker.host)
            device.play_uri(session.stream_url, title=STREAM_TITLE, force_radio=True)
            return device

        session.device = await asyncio.get_running_loop().run_in_executor(None, play)
        return True

    async def _stop_sonos(self, session: StreamingSession):
        if session.device:
            await asyncio.get_running_loop().run_in_executor(None, session.device.stop)

    async def _volume_sonos(self, speaker, session, level) -> bool:
        import soco

        def set_volume():
            device = session.device if session and session.device else soco.SoCo(speaker.host)
            device.volume = int(round(level * 100))

        await asyncio.get_running_loop().run_in_executor(None, set_volume)
        return True

    # --- DLNA / UPnP ---

    async def _dmr_device(self, location: str):
        from async_upnp_client.aiohttp import AiohttpRequester
        from async_upnp_client.client_factory import UpnpFactory
        from async_upnp_client.profiles.dlna import DmrDevice

        factory = UpnpFactory(AiohttpRequester())
        device = await factory.async_create_device(location)
        return DmrDevice(device, None)

    async def _start_dlna(self, session: StreamingSession, speaker: NetworkSpeaker) -> bool:
        location = speaker.extra.get("location")
        if not location:
            session.error_message = "No UPnP description URL for this speaker"
            return False
        dmr = await self._dmr_device(location)
        session.device = dmr
        await dmr.async_set_transport_uri(session.stream_url, STREAM_TITLE, meta_data=didl_metadata(session.stream_url))
        await asyncio.sleep(0.5)
        await dmr.async_play()
        logger.debug(f"DLNA {speaker.host}: transport state {dmr.transport_state}")
        return True

    async def _stop_dlna(self, session: StreamingSession):
        if session.device:
            await session.device.async_stop()

    async def _volume_dlna(self, speaker, session, level) -> bool:
        dmr = session.device if session and session.device else None
        if dmr is None:
            location = speaker.extra.get("location")
            if not location:
                return False
            dmr = await self._dmr_device(location)
        if not dmr.has_volume_level:
            return False
        await dmr.async_set_volume_level(level)
        return True

    # --- AirPlay (RAOP via pyatv) ---

    async def _start_airplay(self, session: StreamingSession, speaker: NetworkSpeaker) -> bool:
        if is_linkplay_device(speaker):
            logger.debug(f"AirPlay: '{speaker.name}' looks like LinkPlay hardware, using its HTTP API")
            return await self._start_linkplay_http(session, speaker)

        import aiohttp
        import pyatv
        from pyatv.conf import AppleTV, RaopService

        loop = asyncio.get_running_loop()
        identifier = speaker.extra.get("identifier")
        if identifier:
            # Build the config from what discovery stored: rescanning can time
            # out for non-Apple devices
            config = AppleTV(ipaddress.ip_address(speaker.host), speaker.name)
            config.add_service(RaopService(
                identifier=identifier,
                port=speaker.extra.get("raop_port") or speaker.port or 7000,
                properties=speaker.extra.get("raop_properties") or {},
            ))
        else:
            devices = await pyatv.scan(loop, hosts=[speaker.host], timeout=10)
            if not devices:
                session.error_message = f"No AirPlay device found at {speaker.host}"
                return False
            config = devices[0]

        atv = await pyatv.connect(config, loop)
        session.device = atv
        if not atv.stream:
            session.error_message = "Device has no AirPlay streaming interface"
            return False

        http_session = aiohttp.ClientSession()
        session.handles["http_session"] = http_session
        session.handles["stop"] = False

        async def stream_task():
            response = None
            try:
                response = await http_session.get(session.stream_url)
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status} from stream URL")
                # Pre-buffer before handing the reader to pyatv: its decoder
                # must see MP3 headers straight away (race fixed in the app)
                reader = asyncio.StreamReader()
                buffered = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    buffered.extend(chunk)
                    if len(buffered) >= 32768 or session.handles.get("stop"):
                        break
                if session.handles.get("stop"):
                    return
                reader.feed_data(bytes(buffered))

                async def feed():
                    try:
                        async for chunk in response.content.iter_chunked(8192):
                            if session.handles.get("stop"):
                                break
                            reader.feed_data(chunk)
                    except Exception as e:
                        logger.debug(f"AirPlay {speaker.host}: feed ended: {e}")
                    finally:
                        reader.feed_eof()

                session.handles["feed_task"] = asyncio.create_task(feed())
                await atv.stream.stream_file(reader)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"AirPlay stream to '{speaker.name}' failed: {e}")
                session.state = StreamingState.ERROR
                session.error_message = str(e)
            finally:
                if response is not None:
                    response.close()

        task = asyncio.create_task(stream_task())
        session.handles["task"] = task
        await asyncio.sleep(1)  # catch immediate failures
        if task.done() and session.state == StreamingState.ERROR:
            return False
        return True

    async def _stop_airplay(self, session: StreamingSession):
        if session.handles.get("linkplay_host"):
            await self._stop_linkplay_http(session)
            return
        session.handles["stop"] = True
        for key in ("feed_task", "task"):
            task = session.handles.get(key)
            if task and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception as e:
                    logger.debug(f"AirPlay: {key} ended with {e}")
        http_session = session.handles.get("http_session")
        if http_session:
            await http_session.close()
        atv = session.device
        if atv:
            try:
                if getattr(atv, "remote_control", None):
                    await atv.remote_control.stop()
            except Exception as e:
                logger.debug(f"AirPlay: stop command failed: {e}")
            # pyatv's close() returns the set of tasks it started
            result = atv.close()
            if isinstance(result, (set, list, tuple)):
                await asyncio.gather(*result, return_exceptions=True)
            elif asyncio.isfuture(result) or asyncio.iscoroutine(result):
                await result

    async def _volume_airplay(self, speaker, session, level) -> bool:
        if is_linkplay_device(speaker):
            return await self._linkplay_command(speaker.host, f"setPlayerCmd:vol:{int(round(level * 100))}")
        atv = session.device if session else None
        if not atv or not getattr(atv, "audio", None):
            return False
        await atv.audio.set_volume(level * 100.0)
        return True

    # --- LinkPlay / Arylic HTTP API (https://developer.arylic.com/httpapi/) ---

    @staticmethod
    async def _linkplay_command(host: str, command: str, timeout: float = 10.0) -> bool:
        import aiohttp

        url = f"http://{host}/httpapi.asp?command={command}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as http:
            async with http.get(url) as response:
                text = (await response.text()).strip()
        logger.debug(f"LinkPlay {host}: {command} -> {text[:100]}")
        return response.status == 200 and text.upper() == "OK"

    async def _start_linkplay_http(self, session: StreamingSession, speaker: NetworkSpeaker) -> bool:
        ok = await self._linkplay_command(speaker.host, f"setPlayerCmd:play:{session.stream_url}")
        if ok:
            session.handles["linkplay_host"] = speaker.host
        else:
            session.error_message = "LinkPlay play command was not accepted"
        return ok

    async def _stop_linkplay_http(self, session: StreamingSession):
        await self._linkplay_command(session.handles["linkplay_host"], "setPlayerCmd:stop", timeout=5.0)

    # --- HEOS (beta) ---

    async def _start_heos(self, session: StreamingSession, speaker: NetworkSpeaker) -> bool:
        from sonorium.network.heos import heos_command, heos_succeeded

        pid = speaker.extra.get("pid")
        if pid is None:
            session.error_message = "No HEOS player ID"
            return False
        response = await heos_command(speaker.host, f"browse/play_stream?pid={pid}&url={session.stream_url}", timeout=10.0)
        if not heos_succeeded(response):
            session.error_message = f"HEOS: {response.get('heos', {}).get('message', 'command failed')}"
            return False
        session.handles["pid"] = pid
        session.handles["host"] = speaker.host
        return True

    async def _stop_heos(self, session: StreamingSession):
        from sonorium.network.heos import heos_command

        pid, host = session.handles.get("pid"), session.handles.get("host")
        if pid is not None and host:
            await heos_command(host, f"player/set_play_state?pid={pid}&state=stop")

    async def _volume_heos(self, speaker, session, level) -> bool:
        from sonorium.network.heos import heos_command, heos_succeeded

        pid = speaker.extra.get("pid")
        if pid is None:
            return False
        response = await heos_command(speaker.host, f"player/set_volume?pid={pid}&level={int(round(level * 100))}")
        return heos_succeeded(response)
