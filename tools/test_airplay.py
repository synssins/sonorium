#!/usr/bin/env python3
"""
AirPlay Test Script for Sonorium

Tests AirPlay streaming to Arylic/Linkplay speakers using pyatv.
Generates a pleasant test tone and plays it at specified volume.

Usage:
    python test_airplay.py [--host IP] [--volume VOL] [--duration SEC]

Requirements:
    pip install pyatv aiohttp numpy av
"""

import asyncio
import argparse
import sys
import io
import struct
import math
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "app" / "core"))


async def get_arylic_status(host: str) -> dict:
    """Get current status from Arylic device via HTTP API."""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://{host}/httpapi.asp?command=getPlayerStatus",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        print(f"  Warning: Could not get Arylic status: {e}")
    return {}


async def set_arylic_volume(host: str, volume: int) -> bool:
    """Set volume on Arylic device via HTTP API."""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://{host}/httpapi.asp?command=setPlayerCmd:vol:{volume}",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                return resp.status == 200
    except Exception as e:
        print(f"  Warning: Could not set volume: {e}")
    return False


async def stop_arylic_playback(host: str) -> bool:
    """Stop playback on Arylic device via HTTP API."""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://{host}/httpapi.asp?command=setPlayerCmd:stop",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                return resp.status == 200
    except Exception as e:
        print(f"  Warning: Could not stop playback: {e}")
    return False


def generate_test_tone_mp3(duration_sec: float = 5.0, frequency: float = 440.0,
                           sample_rate: int = 44100) -> bytes:
    """Generate a pleasant test tone as MP3 data.

    Creates a gentle sine wave with fade in/out to avoid clicks.
    """
    import numpy as np
    import av

    print(f"  Generating {duration_sec}s test tone at {frequency}Hz...")

    # Generate samples
    num_samples = int(sample_rate * duration_sec)
    t = np.linspace(0, duration_sec, num_samples, dtype=np.float32)

    # Create sine wave with gentle harmonics for a pleasant sound
    wave = (
        0.5 * np.sin(2 * np.pi * frequency * t) +       # Fundamental
        0.2 * np.sin(2 * np.pi * frequency * 2 * t) +   # 2nd harmonic
        0.1 * np.sin(2 * np.pi * frequency * 3 * t)     # 3rd harmonic
    )

    # Normalize
    wave = wave / np.max(np.abs(wave)) * 0.7

    # Apply fade in/out (0.2 seconds each)
    fade_samples = int(0.2 * sample_rate)
    fade_in = np.linspace(0, 1, fade_samples)
    fade_out = np.linspace(1, 0, fade_samples)
    wave[:fade_samples] *= fade_in
    wave[-fade_samples:] *= fade_out

    # Convert to stereo 16-bit PCM
    wave_stereo = np.column_stack([wave, wave])
    pcm_data = (wave_stereo * 32767).astype(np.int16).tobytes()

    # Encode to MP3 using PyAV
    output_buffer = io.BytesIO()

    with av.open(output_buffer, mode='w', format='mp3') as container:
        stream = container.add_stream('mp3', rate=sample_rate)
        stream.channels = 2
        stream.layout = 'stereo'
        stream.bit_rate = 192000

        # Create audio frame
        frame = av.AudioFrame(format='s16', layout='stereo', samples=num_samples)
        frame.rate = sample_rate
        frame.pts = 0

        # Copy PCM data to frame
        frame.planes[0].update(pcm_data)

        # Encode and write
        for packet in stream.encode(frame):
            container.mux(packet)

        # Flush encoder
        for packet in stream.encode(None):
            container.mux(packet)

    mp3_data = output_buffer.getvalue()
    print(f"  Generated {len(mp3_data)} bytes of MP3 data")
    return mp3_data


async def discover_airplay_devices(host: str = None, timeout: int = 10):
    """Discover AirPlay devices on the network."""
    import pyatv

    print("\n=== Discovering AirPlay Devices ===")

    if host:
        print(f"Scanning specific host: {host}")
        devices = await pyatv.scan(asyncio.get_event_loop(), hosts=[host], timeout=timeout)
    else:
        print(f"Scanning network (timeout: {timeout}s)...")
        devices = await pyatv.scan(asyncio.get_event_loop(), timeout=timeout)

    print(f"Found {len(devices)} device(s):")
    for device in devices:
        print(f"  - {device.name} @ {device.address}")
        for service in device.services:
            print(f"      Protocol: {service.protocol}, Port: {service.port}")

    return devices


async def stream_to_airplay(host: str, mp3_data: bytes, volume: int = 50):
    """Stream MP3 data to an AirPlay device using pyatv."""
    import pyatv
    from pyatv.const import Protocol

    print(f"\n=== Streaming to AirPlay Device at {host} ===")

    # Set volume first via Arylic API
    print(f"  Setting volume to {volume}%...")
    await set_arylic_volume(host, volume)

    # Discover device
    print(f"  Discovering device...")
    devices = await pyatv.scan(asyncio.get_event_loop(), hosts=[host], timeout=10)

    if not devices:
        print(f"  ERROR: No AirPlay device found at {host}")
        return False

    device = devices[0]
    print(f"  Found: {device.name}")

    # Check for RAOP/AirPlay support
    protocols = [s.protocol for s in device.services]
    print(f"  Protocols: {protocols}")

    if Protocol.RAOP not in protocols and Protocol.AirPlay not in protocols:
        print(f"  ERROR: Device does not support AirPlay audio streaming")
        return False

    # Connect
    print(f"  Connecting...")
    atv = await pyatv.connect(device, asyncio.get_event_loop())

    try:
        if not atv.stream:
            print(f"  ERROR: Device does not expose streaming interface")
            return False

        print(f"  Starting audio stream...")

        # Create a file-like object from MP3 data
        mp3_buffer = io.BytesIO(mp3_data)

        # Stream the audio
        await atv.stream.stream_file(mp3_buffer)

        print(f"  Stream completed successfully!")

        # Check status after playback
        await asyncio.sleep(1)
        status = await get_arylic_status(host)
        print(f"  Device status: {status.get('status', 'unknown')}")

        return True

    except Exception as e:
        print(f"  ERROR during streaming: {e}")
        return False

    finally:
        print(f"  Closing connection...")
        atv.close()


async def test_arylic_direct(host: str, volume: int = 50):
    """Test direct HTTP playback via Arylic API (not AirPlay)."""
    import aiohttp

    print(f"\n=== Testing Arylic Direct API at {host} ===")

    # Get device info
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"http://{host}/httpapi.asp?command=getStatusEx",
            timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            if resp.status == 200:
                info = await resp.json()
                print(f"  Device: {info.get('DeviceName', 'Unknown')}")
                print(f"  Firmware: {info.get('firmware', 'Unknown')}")
                print(f"  Hardware: {info.get('hardware', 'Unknown')}")

    # Get current status
    status = await get_arylic_status(host)
    print(f"  Current volume: {status.get('vol', 'Unknown')}")
    print(f"  Current status: {status.get('status', 'Unknown')}")

    # Set volume
    print(f"  Setting volume to {volume}%...")
    await set_arylic_volume(host, volume)

    return True


async def main():
    parser = argparse.ArgumentParser(description="Test AirPlay streaming to speakers")
    parser.add_argument("--host", default="192.168.1.74", help="Speaker IP address")
    parser.add_argument("--volume", type=int, default=50, help="Volume (0-100)")
    parser.add_argument("--duration", type=float, default=5.0, help="Tone duration in seconds")
    parser.add_argument("--frequency", type=float, default=440.0, help="Tone frequency in Hz")
    parser.add_argument("--discover", action="store_true", help="Only discover devices")
    parser.add_argument("--status", action="store_true", help="Only show device status")
    parser.add_argument("--stop", action="store_true", help="Stop playback")

    args = parser.parse_args()

    print("=" * 60)
    print("Sonorium AirPlay Test Script")
    print("=" * 60)

    if args.discover:
        await discover_airplay_devices(timeout=10)
        return

    if args.status:
        await test_arylic_direct(args.host, args.volume)
        return

    if args.stop:
        print(f"Stopping playback on {args.host}...")
        await stop_arylic_playback(args.host)
        return

    # Full test
    print(f"\nTest Configuration:")
    print(f"  Host: {args.host}")
    print(f"  Volume: {args.volume}%")
    print(f"  Duration: {args.duration}s")
    print(f"  Frequency: {args.frequency}Hz")

    # Check device status first
    await test_arylic_direct(args.host, args.volume)

    # Generate test tone
    try:
        mp3_data = generate_test_tone_mp3(
            duration_sec=args.duration,
            frequency=args.frequency
        )
    except Exception as e:
        print(f"\nERROR generating test tone: {e}")
        print("Make sure numpy and av are installed: pip install numpy av")
        return

    # Stream to device
    try:
        success = await stream_to_airplay(args.host, mp3_data, args.volume)

        if success:
            print("\n" + "=" * 60)
            print("TEST PASSED: Audio streamed successfully!")
            print("=" * 60)
        else:
            print("\n" + "=" * 60)
            print("TEST FAILED: Could not stream audio")
            print("=" * 60)

    except Exception as e:
        print(f"\nERROR during test: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
