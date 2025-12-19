"""
Network speaker streaming for Sonorium.
Handles streaming audio to Chromecast, Sonos, and DLNA devices.
"""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum

logger = logging.getLogger(__name__)


class StreamingState(Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    BUFFERING = "buffering"
    PLAYING = "playing"
    ERROR = "error"


@dataclass
class StreamingSession:
    """Represents an active streaming session to a network speaker."""
    speaker_id: str
    speaker_type: str  # chromecast, sonos, dlna
    stream_url: str
    state: StreamingState = StreamingState.STOPPED
    error_message: Optional[str] = None
    _device: any = field(default=None, repr=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, repr=False)


class NetworkStreamingManager:
    """
    Manages streaming to network speakers.

    The streaming approach:
    1. Sonorium hosts an HTTP audio stream endpoint
    2. Network speakers connect to this stream URL
    3. Audio is mixed and served in real-time
    """

    def __init__(self, stream_base_url: str | None = None, port: int = 8008):
        # Auto-detect IP if no URL provided
        if stream_base_url is None:
            from sonorium.config import get_stream_base_url
            stream_base_url = get_stream_base_url(port)
            logger.info(f"Auto-detected stream URL: {stream_base_url}")

        self.stream_base_url = stream_base_url
        self.sessions: dict[str, StreamingSession] = {}
        self._lock = threading.Lock()

    def set_stream_base_url(self, url: str):
        """Update the base URL for streaming (e.g., when server starts)."""
        self.stream_base_url = url
        logger.info(f"Stream base URL set to: {url}")

    def get_stream_url(self, theme_id: str) -> str:
        """Get the HTTP stream URL for a theme (legacy, direct theme streaming)."""
        return f"{self.stream_base_url}/stream/{theme_id}"

    def get_channel_stream_url(self, channel_id: int) -> str:
        """Get the HTTP stream URL for a channel (persistent streaming)."""
        return f"{self.stream_base_url}/stream/channel{channel_id}"

    async def start_streaming(self, speaker_id: str, speaker_type: str,
                             speaker_info: dict, theme_id: str,
                             channel_id: int | None = None) -> bool:
        """
        Start streaming to a network speaker.

        Args:
            speaker_id: Unique speaker identifier
            speaker_type: 'chromecast', 'sonos', or 'dlna'
            speaker_info: Speaker details (host, port, etc.)
            theme_id: Theme to stream (used for legacy direct streaming)
            channel_id: Channel ID for persistent streaming (preferred over theme_id)

        Returns:
            True if streaming started successfully
        """
        # Use channel-based URL if channel_id provided, otherwise fall back to theme URL
        if channel_id is not None:
            stream_url = self.get_channel_stream_url(channel_id)
            logger.info(f"Using channel-based streaming: channel {channel_id}")
        else:
            stream_url = self.get_stream_url(theme_id)
            logger.info(f"Using legacy theme-based streaming: {theme_id}")

        with self._lock:
            # Stop existing session if any
            if speaker_id in self.sessions:
                await self.stop_streaming(speaker_id)

            session = StreamingSession(
                speaker_id=speaker_id,
                speaker_type=speaker_type,
                stream_url=stream_url,
                state=StreamingState.CONNECTING
            )
            self.sessions[speaker_id] = session

        try:
            if speaker_type == 'chromecast':
                success = await self._start_chromecast(session, speaker_info)
            elif speaker_type == 'sonos':
                success = await self._start_sonos(session, speaker_info)
            elif speaker_type == 'dlna':
                success = await self._start_dlna(session, speaker_info)
            elif speaker_type == 'airplay':
                success = await self._start_airplay(session, speaker_info)
            else:
                logger.error(f"Unknown speaker type: {speaker_type}")
                session.state = StreamingState.ERROR
                session.error_message = f"Unknown speaker type: {speaker_type}"
                return False

            if success:
                session.state = StreamingState.PLAYING
                logger.info(f"Streaming started to {speaker_id} ({speaker_type})")
            else:
                session.state = StreamingState.ERROR

            return success

        except Exception as e:
            logger.error(f"Failed to start streaming to {speaker_id}: {e}")
            session.state = StreamingState.ERROR
            session.error_message = str(e)
            return False

    async def stop_streaming(self, speaker_id: str) -> bool:
        """Stop streaming to a specific speaker."""
        with self._lock:
            session = self.sessions.get(speaker_id)
            if not session:
                return True

        try:
            session._stop_event.set()

            if session.speaker_type == 'chromecast':
                await self._stop_chromecast(session)
            elif session.speaker_type == 'sonos':
                await self._stop_sonos(session)
            elif session.speaker_type == 'dlna':
                await self._stop_dlna(session)
            elif session.speaker_type == 'airplay':
                await self._stop_airplay(session)

            session.state = StreamingState.STOPPED

            with self._lock:
                del self.sessions[speaker_id]

            logger.info(f"Streaming stopped to {speaker_id}")
            return True

        except Exception as e:
            logger.error(f"Error stopping stream to {speaker_id}: {e}")
            return False

    async def stop_all(self):
        """Stop all active streaming sessions."""
        speaker_ids = list(self.sessions.keys())
        for speaker_id in speaker_ids:
            await self.stop_streaming(speaker_id)

    def get_session(self, speaker_id: str) -> Optional[StreamingSession]:
        """Get streaming session for a speaker."""
        return self.sessions.get(speaker_id)

    def get_active_sessions(self) -> list[StreamingSession]:
        """Get all active streaming sessions."""
        return list(self.sessions.values())

    # --- Chromecast Implementation ---

    async def _start_chromecast(self, session: StreamingSession, speaker_info: dict) -> bool:
        """Start streaming to a Chromecast device."""
        try:
            import pychromecast
            from pychromecast.controllers.media import MediaController

            host = speaker_info.get('host')
            if not host:
                session.error_message = "No host specified for Chromecast"
                return False

            logger.info(f"Connecting to Chromecast at {host}...")

            # Connect to Chromecast (blocking, run in thread)
            loop = asyncio.get_event_loop()

            def connect():
                chromecasts, browser = pychromecast.get_chromecasts()
                browser.stop_discovery()

                # Find by host/IP
                for cc in chromecasts:
                    if str(cc.host) == host:
                        cc.wait()
                        return cc

                # Try direct connection by IP
                try:
                    cc = pychromecast.Chromecast(host)
                    cc.wait()
                    return cc
                except Exception as e:
                    logger.warning(f"Direct Chromecast connection failed: {e}")
                    return None

            cast = await loop.run_in_executor(None, connect)

            if not cast:
                session.error_message = f"Could not connect to Chromecast at {host}"
                return False

            session._device = cast

            # Get media controller and play the stream
            mc = cast.media_controller

            # Play the audio stream
            # Using audio/mpeg for MP3 stream compatibility
            def play():
                mc.play_media(
                    session.stream_url,
                    content_type='audio/mpeg',
                    title='Sonorium',
                    stream_type='LIVE'
                )
                mc.block_until_active(timeout=10)

            await loop.run_in_executor(None, play)

            logger.info(f"Chromecast {host} now playing {session.stream_url}")
            return True

        except ImportError:
            session.error_message = "pychromecast not installed"
            logger.error("pychromecast not installed")
            return False
        except Exception as e:
            session.error_message = str(e)
            logger.error(f"Chromecast streaming error: {e}")
            return False

    async def _stop_chromecast(self, session: StreamingSession):
        """Stop Chromecast playback."""
        if session._device:
            try:
                loop = asyncio.get_event_loop()

                def stop():
                    session._device.media_controller.stop()
                    session._device.quit_app()

                await loop.run_in_executor(None, stop)
            except Exception as e:
                logger.warning(f"Error stopping Chromecast: {e}")

    # --- Sonos Implementation ---

    async def _start_sonos(self, session: StreamingSession, speaker_info: dict) -> bool:
        """Start streaming to a Sonos device."""
        try:
            import soco

            host = speaker_info.get('host')
            if not host:
                session.error_message = "No host specified for Sonos"
                return False

            logger.info(f"Connecting to Sonos at {host}...")

            loop = asyncio.get_event_loop()

            def connect_and_play():
                device = soco.SoCo(host)

                # Stop current playback
                device.stop()

                # Clear the queue
                device.clear_queue()

                # Play the URI directly
                # Sonos can play HTTP streams with play_uri
                device.play_uri(
                    uri=session.stream_url,
                    title='Sonorium'
                )

                return device

            device = await loop.run_in_executor(None, connect_and_play)
            session._device = device

            logger.info(f"Sonos {host} now playing {session.stream_url}")
            return True

        except ImportError:
            session.error_message = "soco not installed"
            logger.error("soco not installed")
            return False
        except Exception as e:
            session.error_message = str(e)
            logger.error(f"Sonos streaming error: {e}")
            return False

    async def _stop_sonos(self, session: StreamingSession):
        """Stop Sonos playback."""
        if session._device:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, session._device.stop)
            except Exception as e:
                logger.warning(f"Error stopping Sonos: {e}")

    # --- DLNA Implementation ---

    def _create_didl_metadata(self, stream_url: str, title: str = "Sonorium") -> str:
        """Create DIDL-Lite metadata XML for DLNA streaming."""
        # Escape special characters in URL for XML
        import html
        safe_url = html.escape(stream_url)
        safe_title = html.escape(title)

        return f'''<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">
<item id="1" parentID="0" restricted="1">
<dc:title>{safe_title}</dc:title>
<upnp:class>object.item.audioItem.musicTrack</upnp:class>
<res protocolInfo="http-get:*:audio/mpeg:DLNA.ORG_PN=MP3;DLNA.ORG_OP=01;DLNA.ORG_FLAGS=01700000000000000000000000000000">{safe_url}</res>
</item>
</DIDL-Lite>'''

    async def _start_dlna(self, session: StreamingSession, speaker_info: dict) -> bool:
        """Start streaming to a DLNA device."""
        try:
            from async_upnp_client.aiohttp import AiohttpRequester
            from async_upnp_client.client_factory import UpnpFactory
            from async_upnp_client.profiles.dlna import DmrDevice

            location = speaker_info.get('extra', {}).get('location')
            if not location:
                session.error_message = "No DLNA location URL available"
                return False

            logger.info(f"Connecting to DLNA device at {location}...")

            # Create UPnP client
            requester = AiohttpRequester()
            factory = UpnpFactory(requester)

            device = await factory.async_create_device(location)
            logger.info(f"DLNA device created: {device.name}")

            # Create DMR (Digital Media Renderer) profile
            dmr = DmrDevice(device, None)

            session._device = dmr

            # Log device capabilities
            logger.info(f"DLNA device transport state: {dmr.transport_state}")

            # Construct metadata with explicit MIME type for reliable playback
            # This avoids issues with HEAD request failures or missing Content-Type
            logger.info(f"Setting transport URI: {session.stream_url}")

            # Use our own DIDL-Lite metadata for maximum compatibility
            # Some devices are picky about the format
            meta_data = self._create_didl_metadata(session.stream_url, 'Sonorium')
            logger.info(f"Using custom DIDL-Lite metadata for DLNA")
            logger.debug(f"DIDL metadata: {meta_data}")

            await dmr.async_set_transport_uri(
                session.stream_url,
                'Sonorium',
                meta_data=meta_data
            )

            # Small delay to allow device to process
            await asyncio.sleep(0.5)

            # Check state after setting URI
            logger.info(f"DLNA transport state after SetAVTransportURI: {dmr.transport_state}")

            # Send play command
            await dmr.async_play()

            # Another small delay
            await asyncio.sleep(0.5)

            # Log final state
            logger.info(f"DLNA device now playing {session.stream_url}")
            logger.info(f"DLNA transport state after Play: {dmr.transport_state}")

            return True

        except ImportError as e:
            session.error_message = "async-upnp-client not installed"
            logger.error(f"DLNA import error: {e}")
            return False
        except Exception as e:
            session.error_message = str(e)
            logger.error(f"DLNA streaming error: {e}", exc_info=True)
            return False

    async def _stop_dlna(self, session: StreamingSession):
        """Stop DLNA playback."""
        if session._device:
            try:
                await session._device.async_stop()
            except Exception as e:
                logger.warning(f"Error stopping DLNA: {e}")

    # --- AirPlay Implementation ---

    async def _start_airplay(self, session: StreamingSession, speaker_info: dict) -> bool:
        """Start streaming to an AirPlay device using pyatv.

        Uses pyatv's stream_file() with aiohttp to fetch our HTTP MP3 stream.
        This works for both Apple TV and audio-only AirPlay speakers (HomePod,
        AirPort Express, Linkplay/Arylic, etc.) via the RAOP protocol.

        For Linkplay/Arylic devices, we use their HTTP API instead of pyatv
        since it's more reliable for these devices.

        Fully portable - uses only Python libraries, no system dependencies.
        """
        # Check if this is a Linkplay/Arylic device - use HTTP API instead
        if self._is_linkplay_device(speaker_info):
            speaker_name = speaker_info.get('name', speaker_info.get('host', 'unknown'))
            logger.info(f"AirPlay: Detected Linkplay device '{speaker_name}', using HTTP API")
            return await self._start_linkplay_http(session, speaker_info)

        try:
            import pyatv
            from pyatv.conf import AppleTV, RaopService
            from pyatv.const import Protocol
            import aiohttp

            host = speaker_info.get('host')
            if not host:
                session.error_message = "No host specified for AirPlay"
                logger.error("AirPlay: No host specified")
                return False

            speaker_name = speaker_info.get('name', host)
            port = speaker_info.get('port', 7000)
            extra = speaker_info.get('extra', {})

            logger.info(f"AirPlay: Connecting to {speaker_name} at {host}:{port}...")

            loop = asyncio.get_event_loop()

            # Try to build config manually from stored discovery info first
            # This avoids re-scanning which may timeout for non-Apple devices
            device_config = None
            identifier = extra.get('identifier')

            if identifier:
                # Build device config manually using stored info
                logger.info(f"AirPlay: Building config from stored info (identifier: {identifier})")
                device_config = AppleTV(host, speaker_name)

                # Add RAOP service for audio streaming
                raop_service = RaopService(
                    identifier=identifier,
                    port=port,
                    properties={}
                )
                device_config.add_service(raop_service)
                logger.info(f"AirPlay: Added RAOP service on port {port}")

            else:
                # Fall back to scanning if no stored identifier
                logger.info(f"AirPlay: No stored identifier, scanning for device...")
                devices = await pyatv.scan(loop, hosts=[host], timeout=10)

                if not devices:
                    session.error_message = f"Could not find AirPlay device at {host}"
                    logger.error(f"AirPlay: No device found at {host}")
                    return False

                device_config = devices[0]
                protocols = [str(s.protocol) for s in device_config.services]
                logger.info(f"AirPlay: Found device '{device_config.name}' with protocols: {protocols}")

            # Connect to the device
            atv = await pyatv.connect(device_config, loop)
            session._device = atv

            logger.info(f"AirPlay: Connected to {speaker_name}")

            # Check if device supports streaming interface
            if not atv.stream:
                session.error_message = (
                    f"AirPlay device '{device_config.name}' does not expose streaming interface"
                )
                logger.error(f"AirPlay: Device {device_config.name} has no stream interface")
                await atv.close()
                return False

            # Use stream_file() with aiohttp to pipe our HTTP MP3 stream
            # This approach works for audio-only AirPlay speakers via RAOP
            logger.info(f"AirPlay: Starting stream to {speaker_name} via RAOP: {session.stream_url}")

            # Create aiohttp session for streaming
            http_session = aiohttp.ClientSession()
            session._airplay_http_session = http_session

            # Flag to signal stop
            session._airplay_stop_flag = False

            # Start streaming in a background task so we can return immediately
            # The stream_file call blocks until streaming completes or is cancelled
            async def stream_task():
                response = None
                try:
                    logger.info(f"AirPlay: Connecting to stream URL: {session.stream_url}")
                    response = await http_session.get(session.stream_url)

                    if response.status != 200:
                        error_msg = f"HTTP {response.status} from stream URL"
                        logger.error(f"AirPlay: {error_msg}")
                        session.state = StreamingState.ERROR
                        session.error_message = error_msg
                        return

                    logger.info(f"AirPlay: Streaming started to {speaker_name}")

                    # Create StreamReader and pre-buffer initial MP3 data
                    # pyatv/miniaudio needs to see MP3 headers immediately
                    reader = asyncio.StreamReader()

                    # Buffer initial chunks before passing to pyatv
                    # This ensures miniaudio sees valid MP3 data when it starts reading
                    initial_buffer = bytearray()
                    min_buffer_size = 32768  # 32KB should contain MP3 headers

                    logger.info(f"AirPlay: Pre-buffering {min_buffer_size} bytes...")
                    async for chunk in response.content.iter_chunked(8192):
                        initial_buffer.extend(chunk)
                        if len(initial_buffer) >= min_buffer_size:
                            break
                        if session._airplay_stop_flag:
                            logger.info("AirPlay: Stop requested during buffering")
                            return

                    if len(initial_buffer) < min_buffer_size:
                        logger.warning(f"AirPlay: Only buffered {len(initial_buffer)} bytes")

                    # Feed the initial buffer to the reader
                    reader.feed_data(bytes(initial_buffer))
                    logger.info(f"AirPlay: Pre-buffered {len(initial_buffer)} bytes, starting stream")

                    async def feed_reader():
                        """Continue feeding HTTP response chunks into the StreamReader."""
                        try:
                            async for chunk in response.content.iter_chunked(8192):
                                if session._airplay_stop_flag:
                                    break
                                reader.feed_data(chunk)
                            reader.feed_eof()
                        except asyncio.CancelledError:
                            reader.feed_eof()
                        except Exception as e:
                            logger.error(f"AirPlay: Error feeding stream: {e}")
                            reader.feed_eof()

                    # Start feeding remaining data in background
                    feed_task = asyncio.create_task(feed_reader())
                    session._airplay_feed_task = feed_task

                    # Stream to AirPlay device (now reader has initial data)
                    await atv.stream.stream_file(reader)
                    logger.info(f"AirPlay: Stream completed to {speaker_name}")

                except asyncio.CancelledError:
                    logger.info(f"AirPlay: Stream cancelled for {speaker_name}")
                except Exception as e:
                    logger.error(f"AirPlay: Stream error for {speaker_name}: {e}")
                    session.state = StreamingState.ERROR
                    session.error_message = str(e)
                finally:
                    # Cleanup response
                    if response:
                        try:
                            response.close()
                        except Exception:
                            pass

            # Create and store the task
            session._airplay_task = asyncio.create_task(stream_task())

            # Give it a moment to start and check for immediate failures
            await asyncio.sleep(1)

            # Check if task failed immediately
            if session._airplay_task.done():
                try:
                    session._airplay_task.result()
                except Exception as e:
                    session.error_message = f"Failed to start stream: {e}"
                    logger.error(f"AirPlay: Task failed immediately: {e}")
                    await http_session.close()
                    await atv.close()
                    return False

            logger.info(f"AirPlay: {speaker_name} streaming from {session.stream_url}")
            return True

        except ImportError as e:
            session.error_message = f"Required library not installed: {e}"
            logger.error(f"AirPlay: Import error: {e}")
            return False
        except Exception as e:
            error_msg = str(e)
            session.error_message = f"AirPlay error: {error_msg}"
            logger.error(f"AirPlay streaming error for {speaker_info.get('name', 'unknown')}: {error_msg}", exc_info=True)
            return False

    async def _stop_airplay(self, session: StreamingSession):
        """Stop AirPlay playback."""
        # Check if this was a Linkplay HTTP session
        if hasattr(session, '_linkplay_host') and session._linkplay_host:
            await self._stop_linkplay_http(session)
            return

        # Set stop flag
        if hasattr(session, '_airplay_stop_flag'):
            session._airplay_stop_flag = True

        # Cancel the feed task
        if hasattr(session, '_airplay_feed_task') and session._airplay_feed_task:
            try:
                session._airplay_feed_task.cancel()
                await asyncio.wait_for(session._airplay_feed_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as e:
                logger.warning(f"Error cancelling AirPlay feed task: {e}")

        # Cancel the streaming task
        if hasattr(session, '_airplay_task') and session._airplay_task:
            try:
                session._airplay_task.cancel()
                await asyncio.wait_for(session._airplay_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as e:
                logger.warning(f"Error cancelling AirPlay task: {e}")

        # Close the aiohttp session
        if hasattr(session, '_airplay_http_session') and session._airplay_http_session:
            try:
                await session._airplay_http_session.close()
            except Exception as e:
                logger.warning(f"Error closing AirPlay HTTP session: {e}")

        # Close the pyatv connection
        if session._device:
            try:
                # Try to stop playback if supported
                if hasattr(session._device, 'remote_control') and session._device.remote_control:
                    await session._device.remote_control.stop()

                # Close the connection
                await session._device.close()
            except Exception as e:
                logger.warning(f"Error stopping AirPlay: {e}")

    def _is_linkplay_device(self, speaker_info: dict) -> bool:
        """Check if the device is a Linkplay/Arylic device that supports HTTP API.

        These devices advertise via AirPlay but work better with their HTTP API.
        They can be detected by name patterns or manufacturer info.
        """
        name = speaker_info.get('name', '').lower()
        manufacturer = speaker_info.get('manufacturer', '').lower()
        model = speaker_info.get('model', '').lower()
        extra = speaker_info.get('extra', {})

        # Check for known Linkplay/Arylic patterns
        linkplay_patterns = ['arylic', 'linkplay', 'up2stream', 'a50', 'a30', 'office_c']

        for pattern in linkplay_patterns:
            if pattern in name or pattern in manufacturer or pattern in model:
                return True

        # Check if device has Linkplay-specific identifiers
        identifier = extra.get('identifier', '').lower()
        if 'linkplay' in identifier or 'arylic' in identifier:
            return True

        return False

    async def _start_linkplay_http(self, session: StreamingSession, speaker_info: dict) -> bool:
        """Start streaming to Linkplay/Arylic device via HTTP API.

        Uses the Linkplay HTTP API (setPlayerCmd:play:{url}) which is more reliable
        than AirPlay/RAOP for these devices. The speaker fetches audio from our
        stream URL (pull model, like DLNA).

        API Reference: https://developer.arylic.com/httpapi/
        """
        try:
            import aiohttp

            host = speaker_info.get('host')
            if not host:
                session.error_message = "No host specified for Linkplay"
                logger.error("Linkplay: No host specified")
                return False

            speaker_name = speaker_info.get('name', host)
            logger.info(f"Linkplay HTTP: Starting stream to {speaker_name} at {host}")

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as http_session:
                # First, verify the device responds to HTTP API
                status_url = f"http://{host}/httpapi.asp?command=getStatusEx"
                try:
                    async with http_session.get(status_url) as resp:
                        if resp.status == 200:
                            import json
                            try:
                                status = json.loads(await resp.text())
                                device_name = status.get('DeviceName', 'Unknown')
                                logger.info(f"Linkplay HTTP: Device confirmed: {device_name}")
                            except json.JSONDecodeError:
                                logger.warning("Linkplay HTTP: Could not parse status, continuing anyway")
                        else:
                            logger.warning(f"Linkplay HTTP: Status check returned {resp.status}")
                except Exception as e:
                    logger.warning(f"Linkplay HTTP: Status check failed: {e}, continuing anyway")

                # Set volume to reasonable level (optional, can be removed if not desired)
                # vol_url = f"http://{host}/httpapi.asp?command=setPlayerCmd:vol:50"
                # await http_session.get(vol_url)

                # Tell device to play our stream URL
                play_url = f"http://{host}/httpapi.asp?command=setPlayerCmd:play:{session.stream_url}"
                logger.info(f"Linkplay HTTP: Sending play command: {play_url}")

                async with http_session.get(play_url) as resp:
                    response_text = await resp.text()
                    logger.info(f"Linkplay HTTP: Play response: {response_text}")

                    if response_text.strip() == "OK":
                        logger.info(f"Linkplay HTTP: {speaker_name} now playing {session.stream_url}")

                        # Store connection info for stop command
                        session._linkplay_host = host
                        return True
                    else:
                        session.error_message = f"Play command failed: {response_text}"
                        logger.error(f"Linkplay HTTP: Play failed with response: {response_text}")
                        return False

        except ImportError as e:
            session.error_message = f"Required library not installed: {e}"
            logger.error(f"Linkplay HTTP: Import error: {e}")
            return False
        except Exception as e:
            session.error_message = f"Linkplay HTTP error: {e}"
            logger.error(f"Linkplay HTTP: Error for {speaker_info.get('name', 'unknown')}: {e}", exc_info=True)
            return False

    async def _stop_linkplay_http(self, session: StreamingSession):
        """Stop Linkplay HTTP playback."""
        if hasattr(session, '_linkplay_host') and session._linkplay_host:
            try:
                import aiohttp
                host = session._linkplay_host
                stop_url = f"http://{host}/httpapi.asp?command=setPlayerCmd:stop"

                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as http_session:
                    async with http_session.get(stop_url) as resp:
                        logger.info(f"Linkplay HTTP: Stop command sent to {host}")
            except Exception as e:
                logger.warning(f"Linkplay HTTP: Error stopping playback: {e}")


# Global streaming manager instance
_streaming_manager: Optional[NetworkStreamingManager] = None


def get_streaming_manager() -> NetworkStreamingManager:
    """Get the global streaming manager instance."""
    global _streaming_manager
    if _streaming_manager is None:
        _streaming_manager = NetworkStreamingManager()
    return _streaming_manager


def init_streaming_manager(stream_base_url: str | None = None, port: int = 8008) -> NetworkStreamingManager:
    """Initialize the streaming manager with the server URL (auto-detects IP if not provided)."""
    global _streaming_manager
    _streaming_manager = NetworkStreamingManager(stream_base_url, port)
    return _streaming_manager
