#!/usr/bin/env python3
"""
RTSP Diagnostic Script

Examines raw RTSP responses from AirPlay/RAOP devices to understand
why pyatv fails with "Invalid file" on Arylic/Linkplay speakers.

The hypothesis: pyatv expects Apple-style binary plist from /info,
but Arylic returns something else.
"""

import asyncio
import socket
import logging
import argparse

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DEFAULT_TARGET_IP = "192.168.1.74"

# Known devices from mDNS discovery
DEVICES = {
    "arylic_office": ("192.168.1.74", 4515),
    "lg_soundbar": ("192.168.1.117", 7000),
    "marantz": ("192.168.1.13", 1024),
    "arylic_living": ("192.168.1.254", 4515),
}


async def send_rtsp_request(ip: str, port: int, request: str) -> tuple[str, bytes]:
    """Send raw RTSP request and return headers + body."""
    reader, writer = await asyncio.open_connection(ip, port)

    logger.info(f"Sending RTSP request to {ip}:{port}")
    logger.debug(f"Request:\n{request}")

    writer.write(request.encode('utf-8'))
    await writer.drain()

    # Read response
    response = await asyncio.wait_for(reader.read(65536), timeout=10)
    writer.close()
    await writer.wait_closed()

    # Split headers from body
    if b'\r\n\r\n' in response:
        header_end = response.index(b'\r\n\r\n')
        headers = response[:header_end].decode('utf-8', errors='replace')
        body = response[header_end + 4:]
    else:
        headers = response.decode('utf-8', errors='replace')
        body = b''

    return headers, body


def analyze_body(body: bytes) -> dict:
    """Analyze the response body format."""
    analysis = {
        'length': len(body),
        'is_empty': len(body) == 0,
        'is_binary_plist': False,
        'is_xml_plist': False,
        'is_text': False,
        'format_guess': 'unknown'
    }

    if len(body) == 0:
        analysis['format_guess'] = 'empty'
        return analysis

    # Check for binary plist magic bytes (bplist00 or bplist01)
    if body.startswith(b'bplist'):
        analysis['is_binary_plist'] = True
        analysis['format_guess'] = 'binary_plist'
        return analysis

    # Check for XML plist
    if b'<?xml' in body[:100] and b'plist' in body[:200]:
        analysis['is_xml_plist'] = True
        analysis['format_guess'] = 'xml_plist'
        return analysis

    # Check if it's plain text
    try:
        text = body.decode('utf-8')
        analysis['is_text'] = True
        if '=' in text and '\n' in text:
            analysis['format_guess'] = 'key_value_pairs'
        elif text.startswith('{') or text.startswith('['):
            analysis['format_guess'] = 'json'
        else:
            analysis['format_guess'] = 'plain_text'
    except UnicodeDecodeError:
        analysis['format_guess'] = 'binary_unknown'

    return analysis


async def diagnose_device(ip: str, port: int):
    """Run diagnostic RTSP requests against a device."""
    print("\n" + "=" * 70)
    print(f"RTSP DIAGNOSTIC: {ip}:{port}")
    print("=" * 70)

    # Test 1: OPTIONS request (basic connectivity)
    print("\n--- Test 1: OPTIONS Request ---")
    try:
        options_req = (
            f"OPTIONS * RTSP/1.0\r\n"
            f"CSeq: 1\r\n"
            f"User-Agent: Sonorium/1.0\r\n"
            f"\r\n"
        )
        headers, body = await send_rtsp_request(ip, port, options_req)
        print(f"Headers:\n{headers}")
        if body:
            print(f"Body ({len(body)} bytes):\n{body[:500]}")
    except Exception as e:
        print(f"OPTIONS failed: {e}")

    # Test 2: GET /info (this is what pyatv calls)
    print("\n--- Test 2: GET /info Request ---")
    try:
        # Try HTTP-style GET first (some devices use this)
        info_req_http = (
            f"GET /info RTSP/1.0\r\n"
            f"CSeq: 2\r\n"
            f"User-Agent: Sonorium/1.0\r\n"
            f"\r\n"
        )
        headers, body = await send_rtsp_request(ip, port, info_req_http)
        print(f"Headers:\n{headers}")

        analysis = analyze_body(body)
        print(f"\nBody Analysis:")
        print(f"  Length: {analysis['length']} bytes")
        print(f"  Format: {analysis['format_guess']}")
        print(f"  Binary Plist: {analysis['is_binary_plist']}")
        print(f"  XML Plist: {analysis['is_xml_plist']}")
        print(f"  Is Text: {analysis['is_text']}")

        if body:
            print(f"\nRaw Body (first 500 bytes):")
            # Show hex dump for binary data
            if not analysis['is_text']:
                hex_lines = []
                for i in range(0, min(len(body), 500), 16):
                    chunk = body[i:i+16]
                    hex_part = ' '.join(f'{b:02x}' for b in chunk)
                    ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
                    hex_lines.append(f"  {i:04x}: {hex_part:<48} {ascii_part}")
                print('\n'.join(hex_lines))
            else:
                print(body.decode('utf-8', errors='replace')[:500])

    except Exception as e:
        print(f"GET /info failed: {e}")

    # Test 3: Try Apple-style RTSP INFO
    print("\n--- Test 3: INFO Request (Apple Style) ---")
    try:
        info_req_apple = (
            f"INFO * RTSP/1.0\r\n"
            f"CSeq: 3\r\n"
            f"User-Agent: AirPlay/540.31\r\n"
            f"Content-Type: application/x-apple-binary-plist\r\n"
            f"X-Apple-ProtocolVersion: 1\r\n"
            f"\r\n"
        )
        headers, body = await send_rtsp_request(ip, port, info_req_apple)
        print(f"Headers:\n{headers}")

        analysis = analyze_body(body)
        print(f"\nBody Analysis:")
        print(f"  Length: {analysis['length']} bytes")
        print(f"  Format: {analysis['format_guess']}")

        if body:
            print(f"\nRaw Body (first 500 bytes):")
            if not analysis['is_text']:
                hex_lines = []
                for i in range(0, min(len(body), 500), 16):
                    chunk = body[i:i+16]
                    hex_part = ' '.join(f'{b:02x}' for b in chunk)
                    ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
                    hex_lines.append(f"  {i:04x}: {hex_part:<48} {ascii_part}")
                print('\n'.join(hex_lines))
            else:
                print(body.decode('utf-8', errors='replace')[:500])

    except Exception as e:
        print(f"INFO request failed: {e}")

    # Test 4: ANNOUNCE (start of actual streaming setup)
    print("\n--- Test 4: ANNOUNCE Request ---")
    try:
        # Minimal SDP for audio
        sdp = (
            "v=0\r\n"
            "o=- 0 0 IN IP4 192.168.1.100\r\n"
            "s=Sonorium\r\n"
            "c=IN IP4 192.168.1.100\r\n"
            "t=0 0\r\n"
            "m=audio 0 RTP/AVP 96\r\n"
            "a=rtpmap:96 AppleLossless\r\n"
        )

        announce_req = (
            f"ANNOUNCE rtsp://{ip}/stream RTSP/1.0\r\n"
            f"CSeq: 4\r\n"
            f"User-Agent: Sonorium/1.0\r\n"
            f"Content-Type: application/sdp\r\n"
            f"Content-Length: {len(sdp)}\r\n"
            f"\r\n"
            f"{sdp}"
        )
        headers, body = await send_rtsp_request(ip, port, announce_req)
        print(f"Headers:\n{headers}")
        if body:
            print(f"Body:\n{body.decode('utf-8', errors='replace')[:500]}")

    except Exception as e:
        print(f"ANNOUNCE failed: {e}")

    print("\n" + "=" * 70)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)


async def main(device_name: str = None):
    """Main diagnostic function."""
    print("\n" + "#" * 70)
    print("# RTSP/RAOP Diagnostic Tool")
    print("# Examining AirPlay device responses")
    print("#" * 70)

    if device_name and device_name in DEVICES:
        # Test specific device
        ip, port = DEVICES[device_name]
        await diagnose_device(ip, port)
    elif device_name:
        # Assume it's an IP:port or just IP
        if ":" in device_name:
            ip, port = device_name.split(":")
            await diagnose_device(ip, int(port))
        else:
            await diagnose_device(device_name, 7000)  # Default port
    else:
        # Test all known devices
        for name, (ip, port) in DEVICES.items():
            print(f"\n\n{'#' * 70}")
            print(f"# Testing: {name}")
            print(f"{'#' * 70}")
            try:
                await diagnose_device(ip, port)
            except Exception as e:
                print(f"Device {name} failed: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RTSP diagnostic for AirPlay devices")
    parser.add_argument('--device', type=str, default=None,
                       help=f'Device name ({", ".join(DEVICES.keys())}) or IP:port')
    parser.add_argument('--all', action='store_true',
                       help='Test all known devices')
    args = parser.parse_args()

    try:
        if args.all or args.device is None:
            asyncio.run(main(None))
        else:
            asyncio.run(main(args.device))
    except KeyboardInterrupt:
        print("\n\nDiagnostic interrupted")
