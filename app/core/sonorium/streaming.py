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
