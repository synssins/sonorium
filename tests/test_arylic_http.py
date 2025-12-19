#!/usr/bin/env python3
"""
Arylic HTTP API Streaming Test

Tests audio streaming to Arylic/Linkplay speakers using their HTTP API.
This bypasses AirPlay/RAOP and uses the simpler HTTP-based control.

API Documentation: https://developer.arylic.com/httpapi/
"""

import asyncio
import aiohttp
import logging
import argparse
import socket
from aiohttp import web
import io
import numpy as np
import threading

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Test configuration
DEFAULT_TARGET_IP = "192.168.1.74"
TEST_DURATION = 10  # seconds
TONE_FREQUENCY = 440  # Hz
SAMPLE_RATE = 44100
VOLUME = 0.3


def get_local_ip():
    """Get local IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.74", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def generate_mp3_bytes(duration: float, frequency: float) -> bytes:
    """Generate MP3 test tone using PyAV."""
    import av

    logger.info(f"Generating {duration}s MP3 at {frequency}Hz...")

    # Generate stereo sine wave
    num_samples = int(SAMPLE_RATE * duration)
    t = np.linspace(0, duration, num_samples, dtype=np.float32)
    mono = (VOLUME * 32767 * np.sin(2 * np.pi * frequency * t)).astype(np.int16)

    # Create stereo (planar format)
    stereo_planar = np.vstack([mono, mono])

    # Encode to MP3
    buffer = io.BytesIO()
    container = av.open(buffer, mode='w', format='mp3')
    stream = container.add_stream('mp3', rate=SAMPLE_RATE)
    stream.bit_rate = 128000

    # Encode in chunks
    chunk_size = SAMPLE_RATE
    for i in range(0, stereo_planar.shape[1], chunk_size):
        chunk = stereo_planar[:, i:i + chunk_size]
        if chunk.shape[1] == 0:
            break
        frame = av.AudioFrame.from_ndarray(chunk, format='s16p', layout='stereo')
        frame.sample_rate = SAMPLE_RATE
        frame.pts = i
        for packet in stream.encode(frame):
            container.mux(packet)

    for packet in stream.encode(None):
        container.mux(packet)
    container.close()

    mp3_data = buffer.getvalue()
    logger.info(f"Generated {len(mp3_data)} bytes of MP3")
    return mp3_data


class SimpleAudioServer:
    """Simple HTTP server to serve audio file."""

    def __init__(self, audio_data: bytes, port: int = 8765):
        self.audio_data = audio_data
        self.port = port
        self.app = web.Application()
        self.app.router.add_get('/audio.mp3', self.handle_audio)
        self.runner = None
        self.site = None

    async def handle_audio(self, request):
        """Serve the audio file."""
        logger.info(f"Audio request from {request.remote}")
        return web.Response(
            body=self.audio_data,
            content_type='audio/mpeg',
            headers={
                'Content-Length': str(len(self.audio_data)),
                'Accept-Ranges': 'bytes'
            }
        )

    async def start(self):
        """Start the server."""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '0.0.0.0', self.port)
        await self.site.start()
        logger.info(f"Audio server started on port {self.port}")

    async def stop(self):
        """Stop the server."""
        if self.runner:
            await self.runner.cleanup()
        logger.info("Audio server stopped")


async def get_device_status(session: aiohttp.ClientSession, ip: str) -> dict:
    """Get device status."""
    url = f"http://{ip}/httpapi.asp?command=getStatusEx"
    try:
        async with session.get(url, timeout=5) as resp:
            text = await resp.text()
            import json
            return json.loads(text)
    except Exception as e:
        logger.error(f"Failed to get status: {e}")
        return {}


async def get_player_status(session: aiohttp.ClientSession, ip: str) -> dict:
    """Get player status."""
    url = f"http://{ip}/httpapi.asp?command=getPlayerStatus"
    try:
        async with session.get(url, timeout=5) as resp:
            text = await resp.text()
            import json
            return json.loads(text)
    except Exception as e:
        logger.error(f"Failed to get player status: {e}")
        return {}


async def play_url(session: aiohttp.ClientSession, ip: str, audio_url: str) -> bool:
    """Tell device to play URL."""
    url = f"http://{ip}/httpapi.asp?command=setPlayerCmd:play:{audio_url}"
    try:
        async with session.get(url, timeout=5) as resp:
            text = await resp.text()
            logger.info(f"Play command response: {text}")
            return text == "OK"
    except Exception as e:
        logger.error(f"Play command failed: {e}")
        return False


async def stop_playback(session: aiohttp.ClientSession, ip: str) -> bool:
    """Stop playback."""
    url = f"http://{ip}/httpapi.asp?command=setPlayerCmd:stop"
    try:
        async with session.get(url, timeout=5) as resp:
            return True
    except:
        return False


async def set_volume(session: aiohttp.ClientSession, ip: str, vol: int) -> bool:
    """Set volume (0-100)."""
    url = f"http://{ip}/httpapi.asp?command=setPlayerCmd:vol:{vol}"
    try:
        async with session.get(url, timeout=5) as resp:
            return True
    except:
        return False


async def test_http_streaming(target_ip: str):
    """Test streaming via Arylic HTTP API."""
    print("\n" + "#" * 60)
    print("# Arylic HTTP API Streaming Test")
    print("#" * 60)

    # Generate test audio
    mp3_data = generate_mp3_bytes(TEST_DURATION, TONE_FREQUENCY)

    # Start local audio server
    local_ip = get_local_ip()
    server_port = 8765
    server = SimpleAudioServer(mp3_data, server_port)
    await server.start()

    audio_url = f"http://{local_ip}:{server_port}/audio.mp3"
    logger.info(f"Audio URL: {audio_url}")

    async with aiohttp.ClientSession() as session:
        # Get device info
        print("\n--- Device Status ---")
        status = await get_device_status(session, target_ip)
        if status:
            print(f"  Device: {status.get('DeviceName', 'Unknown')}")
            print(f"  UUID: {status.get('uuid', 'Unknown')}")
            print(f"  Firmware: {status.get('firmware', 'Unknown')}")

        # Set volume
        print("\n--- Setting Volume to 40% ---")
        await set_volume(session, target_ip, 40)

        # Play the URL
        print(f"\n--- Playing {audio_url} ---")
        success = await play_url(session, target_ip, audio_url)

        if success:
            print("  Play command accepted!")

            # Monitor playback
            for i in range(TEST_DURATION + 5):
                await asyncio.sleep(1)
                player = await get_player_status(session, target_ip)
                status = player.get('status', 'unknown')
                pos = player.get('curpos', '0')
                print(f"  [{i}s] Status: {status}, Position: {pos}ms")

                if status == 'stop' and i > 2:
                    break
        else:
            print("  Play command FAILED!")

        # Stop playback
        print("\n--- Stopping Playback ---")
        await stop_playback(session, target_ip)

    await server.stop()

    print("\n" + "=" * 60)
    print("Test Complete")
    print("=" * 60)

    return success


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arylic HTTP API test")
    parser.add_argument('--ip', type=str, default=DEFAULT_TARGET_IP,
                       help=f'Target IP (default: {DEFAULT_TARGET_IP})')
    args = parser.parse_args()

    try:
        asyncio.run(test_http_streaming(args.ip))
    except KeyboardInterrupt:
        print("\n\nTest interrupted")
