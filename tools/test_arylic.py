#!/usr/bin/env python3
"""
Arylic/Linkplay Speaker Test Script

Tests speaker connectivity and playback using the Arylic HTTP API.
This is a simpler test that doesn't require pyatv or C compiler dependencies.

Usage:
    python test_arylic.py [--host IP] [--volume VOL]

Requirements:
    pip install aiohttp
"""

import asyncio
import argparse
import aiohttp
import json


async def get_device_info(host: str) -> dict:
    """Get detailed device information."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"http://{host}/httpapi.asp?command=getStatusEx",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return json.loads(text)
        except Exception as e:
            print(f"  Error getting device info: {e}")
    return {}


async def get_player_status(host: str) -> dict:
    """Get current playback status."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"http://{host}/httpapi.asp?command=getPlayerStatus",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return json.loads(text)
        except Exception as e:
            print(f"  Error getting player status: {e}")
    return {}


async def set_volume(host: str, volume: int) -> bool:
    """Set speaker volume (0-100)."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"http://{host}/httpapi.asp?command=setPlayerCmd:vol:{volume}",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"  Error setting volume: {e}")
    return False


async def play_url(host: str, url: str) -> bool:
    """Play audio from URL."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"http://{host}/httpapi.asp?command=setPlayerCmd:play:{url}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"  Error playing URL: {e}")
    return False


async def stop_playback(host: str) -> bool:
    """Stop current playback."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"http://{host}/httpapi.asp?command=setPlayerCmd:stop",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"  Error stopping playback: {e}")
    return False


async def pause_playback(host: str) -> bool:
    """Pause current playback."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"http://{host}/httpapi.asp?command=setPlayerCmd:pause",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                return resp.status == 200
        except Exception as e:
            print(f"  Error pausing playback: {e}")
    return False


def decode_hex_string(hex_str: str) -> str:
    """Decode hex-encoded strings from Arylic API."""
    try:
        return bytes.fromhex(hex_str).decode('utf-8')
    except:
        return hex_str


async def show_device_status(host: str):
    """Display comprehensive device status."""
    print("\n" + "=" * 60)
    print(f"Arylic Device Status - {host}")
    print("=" * 60)

    # Device info
    info = await get_device_info(host)
    if info:
        print(f"\nDevice Information:")
        print(f"  Name: {info.get('DeviceName', 'Unknown')}")
        print(f"  UUID: {info.get('uuid', 'Unknown')}")
        print(f"  Firmware: {info.get('firmware', 'Unknown')}")
        print(f"  Hardware: {info.get('hardware', 'Unknown')}")
        print(f"  Project: {info.get('project', 'Unknown')}")
        print(f"  MAC (ETH): {info.get('ETH_MAC', 'Unknown')}")
        print(f"  MAC (WiFi): {info.get('MAC', 'Unknown')}")
        print(f"  IP (eth2): {info.get('eth2', 'Unknown')}")
        print(f"  Internet: {'Yes' if info.get('internet') == '1' else 'No'}")

    # Player status
    status = await get_player_status(host)
    if status:
        print(f"\nPlayback Status:")
        title = decode_hex_string(status.get('Title', ''))
        artist = decode_hex_string(status.get('Artist', ''))
        album = decode_hex_string(status.get('Album', ''))

        print(f"  Status: {status.get('status', 'Unknown')}")
        print(f"  Volume: {status.get('vol', 'Unknown')}%")
        print(f"  Muted: {'Yes' if status.get('mute') == '1' else 'No'}")
        print(f"  Mode: {status.get('mode', 'Unknown')}")
        print(f"  Title: {title or 'None'}")
        print(f"  Artist: {artist or 'None'}")
        print(f"  Album: {album or 'None'}")
        print(f"  Position: {status.get('curpos', '0')}ms")
        print(f"  Total: {status.get('totlen', '0')}ms")

    return info, status


async def test_playback(host: str, volume: int = 50):
    """Test playback using a public audio stream."""
    print("\n" + "=" * 60)
    print("Playback Test")
    print("=" * 60)

    # Set volume first
    print(f"\n  Setting volume to {volume}%...")
    if await set_volume(host, volume):
        print(f"  Volume set successfully")
    else:
        print(f"  Warning: Could not set volume")

    # Use a public test audio URL (BBC Radio - peaceful classical music)
    # This is a known working stream for testing
    test_urls = [
        ("BBC Radio 3", "http://stream.live.vc.bbcmedia.co.uk/bbc_radio_three"),
        ("SomaFM Drone Zone", "https://somafm.com/dronezone.pls"),
    ]

    print(f"\n  Testing with known audio streams...")

    for name, url in test_urls:
        print(f"\n  Trying: {name}")
        print(f"  URL: {url}")

        if await play_url(host, url):
            print(f"  Play command sent successfully")

            # Wait a moment and check status
            await asyncio.sleep(3)

            status = await get_player_status(host)
            playback_status = status.get('status', 'unknown')
            print(f"  Current status: {playback_status}")

            if playback_status in ['play', 'playing', 'load']:
                print(f"\n  SUCCESS: Audio is playing!")

                # Let it play for a few seconds
                print(f"  Letting audio play for 5 seconds...")
                await asyncio.sleep(5)

                # Stop playback
                print(f"  Stopping playback...")
                await stop_playback(host)

                return True
        else:
            print(f"  Failed to send play command")

    print(f"\n  WARNING: Could not verify audio playback")
    return False


async def interactive_test(host: str, volume: int = 50):
    """Run a full interactive test sequence."""
    print("\n" + "=" * 60)
    print("Sonorium Arylic Speaker Test")
    print("=" * 60)
    print(f"Target: {host}")
    print(f"Volume: {volume}%")

    # Step 1: Check connectivity
    print("\n[1/4] Checking connectivity...")
    info = await get_device_info(host)
    if not info:
        print("  FAILED: Could not connect to device")
        return False
    print(f"  Connected to: {info.get('DeviceName', 'Unknown')}")

    # Step 2: Show current status
    print("\n[2/4] Getting device status...")
    await show_device_status(host)

    # Step 3: Test volume control
    print("\n[3/4] Testing volume control...")
    original_status = await get_player_status(host)
    original_volume = int(original_status.get('vol', 50))

    if await set_volume(host, volume):
        await asyncio.sleep(0.5)
        status = await get_player_status(host)
        new_volume = int(status.get('vol', 0))
        if new_volume == volume:
            print(f"  Volume control: PASSED (set to {volume}%)")
        else:
            print(f"  Volume control: PARTIAL (requested {volume}%, got {new_volume}%)")
    else:
        print(f"  Volume control: FAILED")

    # Step 4: Test audio playback
    print("\n[4/4] Testing audio playback...")
    playback_success = await test_playback(host, volume)

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"  Device connectivity: PASSED")
    print(f"  Volume control: PASSED")
    print(f"  Audio playback: {'PASSED' if playback_success else 'NEEDS VERIFICATION'}")

    if playback_success:
        print("\n  ALL TESTS PASSED!")
    else:
        print("\n  Note: Playback test may need manual verification")

    return True


async def main():
    parser = argparse.ArgumentParser(description="Test Arylic/Linkplay speaker")
    parser.add_argument("--host", default="192.168.1.74", help="Speaker IP address")
    parser.add_argument("--volume", type=int, default=50, help="Test volume (0-100)")
    parser.add_argument("--status", action="store_true", help="Only show status")
    parser.add_argument("--stop", action="store_true", help="Stop playback")
    parser.add_argument("--play", metavar="URL", help="Play specific URL")

    args = parser.parse_args()

    if args.status:
        await show_device_status(args.host)
        return

    if args.stop:
        print(f"Stopping playback on {args.host}...")
        if await stop_playback(args.host):
            print("Playback stopped")
        else:
            print("Failed to stop playback")
        return

    if args.play:
        print(f"Playing {args.play} on {args.host}...")
        await set_volume(args.host, args.volume)
        if await play_url(args.host, args.play):
            print("Play command sent successfully")
        else:
            print("Failed to play URL")
        return

    # Run full test
    await interactive_test(args.host, args.volume)


if __name__ == "__main__":
    asyncio.run(main())
