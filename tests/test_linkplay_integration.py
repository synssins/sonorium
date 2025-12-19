#!/usr/bin/env python3
"""
Linkplay HTTP API Integration Test

Tests the new Linkplay HTTP API streaming integration in streaming.py.
This verifies that Arylic/Linkplay devices are properly detected and
use HTTP API instead of pyatv.
"""

import asyncio
import sys
import os
import logging

# Add core path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app', 'core'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_linkplay_detection():
    """Test the _is_linkplay_device detection logic."""
    print("\n" + "=" * 60)
    print("Testing Linkplay Device Detection")
    print("=" * 60)

    from sonorium.streaming import NetworkStreamingManager

    manager = NetworkStreamingManager(stream_base_url="http://192.168.1.100:8008")

    # Test cases - should be detected as Linkplay
    linkplay_devices = [
        {'name': 'Office_C97a', 'host': '192.168.1.74'},
        {'name': 'Arylic-livingroom', 'host': '192.168.1.254'},
        {'name': 'UP2Stream Pro', 'host': '192.168.1.100'},
        {'name': 'A50 Speaker', 'host': '192.168.1.101'},
        {'manufacturer': 'Arylic', 'host': '192.168.1.102'},
        {'manufacturer': 'Linkplay', 'host': '192.168.1.103'},
    ]

    # Test cases - should NOT be detected as Linkplay
    other_devices = [
        {'name': 'LG Soundbar', 'host': '192.168.1.117'},
        {'name': 'Marantz SR5011', 'host': '192.168.1.13'},
        {'name': 'Apple TV', 'manufacturer': 'Apple', 'host': '192.168.1.50'},
        {'name': 'HomePod', 'manufacturer': 'Apple', 'host': '192.168.1.51'},
    ]

    print("\nDevices that SHOULD be detected as Linkplay:")
    all_pass = True
    for device in linkplay_devices:
        result = manager._is_linkplay_device(device)
        status = "[PASS]" if result else "[FAIL]"
        print(f"  {status}: {device.get('name', device.get('manufacturer', 'unknown'))}")
        if not result:
            all_pass = False

    print("\nDevices that should NOT be detected as Linkplay:")
    for device in other_devices:
        result = manager._is_linkplay_device(device)
        status = "[PASS]" if not result else "[FAIL]"
        print(f"  {status}: {device.get('name', 'unknown')}")
        if result:
            all_pass = False

    return all_pass


async def test_linkplay_streaming(target_ip: str):
    """Test actual streaming to a Linkplay device."""
    print("\n" + "=" * 60)
    print(f"Testing Linkplay HTTP Streaming to {target_ip}")
    print("=" * 60)

    from sonorium.streaming import NetworkStreamingManager, StreamingSession, StreamingState

    # Create manager with a test stream URL
    # In production, this would be the actual Sonorium stream endpoint
    manager = NetworkStreamingManager(stream_base_url="http://192.168.1.100:8008")

    # Create a test session
    session = StreamingSession(
        speaker_id="test_arylic",
        speaker_type="airplay",
        stream_url="http://stream.live.vc.bbcmedia.co.uk/bbc_radio_one",  # BBC Radio 1 for testing
        state=StreamingState.CONNECTING
    )

    speaker_info = {
        'name': 'Office_C97a',
        'host': target_ip,
        'port': 4515,
        'manufacturer': 'Arylic',
    }

    print(f"\nDetecting device type...")
    is_linkplay = manager._is_linkplay_device(speaker_info)
    print(f"  Is Linkplay device: {is_linkplay}")

    if is_linkplay:
        print(f"\nStarting HTTP API stream...")
        success = await manager._start_linkplay_http(session, speaker_info)

        if success:
            print(f"[OK] Streaming started successfully!")
            print(f"  Playing: {session.stream_url}")

            # Let it play for a few seconds
            print(f"\nPlaying for 10 seconds...")
            await asyncio.sleep(10)

            # Stop playback
            print(f"\nStopping playback...")
            await manager._stop_linkplay_http(session)
            print(f"[OK] Playback stopped")
        else:
            print(f"[ERR] Streaming failed: {session.error_message}")
            return False
    else:
        print(f"[ERR] Device not detected as Linkplay")
        return False

    return True


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Linkplay integration test")
    parser.add_argument('--ip', type=str, default="192.168.1.74",
                       help='Target Arylic device IP')
    parser.add_argument('--detect-only', action='store_true',
                       help='Only test detection logic, skip streaming')
    args = parser.parse_args()

    print("\n" + "#" * 60)
    print("# Linkplay HTTP API Integration Test")
    print("#" * 60)

    # Test detection logic
    detection_ok = test_linkplay_detection()

    if args.detect_only:
        print("\n" + "=" * 60)
        print(f"Detection Test: {'PASSED' if detection_ok else 'FAILED'}")
        print("=" * 60)
        return detection_ok

    # Test actual streaming
    stream_ok = await test_linkplay_streaming(args.ip)

    print("\n" + "=" * 60)
    print("Test Results:")
    print(f"  Detection: {'PASSED' if detection_ok else 'FAILED'}")
    print(f"  Streaming: {'PASSED' if stream_ok else 'FAILED'}")
    print("=" * 60)

    return detection_ok and stream_ok


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nTest interrupted")
        sys.exit(130)
