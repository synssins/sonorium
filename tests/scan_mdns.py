#!/usr/bin/env python3
"""
Pure mDNS Scanner for AirPlay devices.

Uses zeroconf directly without pyatv to discover AirPlay/RAOP speakers.
"""

import asyncio
import socket
from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
import time


class AirPlayListener(ServiceListener):
    """Listener for AirPlay service discovery."""

    def __init__(self):
        self.devices = {}

    def add_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        info = zc.get_service_info(service_type, name)
        if info:
            # Get IP addresses
            addresses = [socket.inet_ntoa(addr) for addr in info.addresses]
            device_info = {
                'name': info.name,
                'server': info.server,
                'port': info.port,
                'addresses': addresses,
                'properties': {k.decode(): v.decode() if isinstance(v, bytes) else v
                              for k, v in info.properties.items()},
                'service_type': service_type
            }
            key = f"{addresses[0] if addresses else 'unknown'}:{info.port}"
            self.devices[key] = device_info
            print(f"\n  Found: {info.name}")
            print(f"    IP: {addresses}")
            print(f"    Port: {info.port}")
            print(f"    Type: {service_type}")
            if info.properties:
                print(f"    Properties: {dict(list(device_info['properties'].items())[:5])}")

    def remove_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        pass

    def update_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        self.add_service(zc, service_type, name)


def scan_for_devices(timeout: int = 10):
    """Scan for AirPlay and RAOP devices."""
    print("\n" + "=" * 60)
    print("mDNS Device Discovery")
    print("=" * 60)

    zc = Zeroconf()
    listener = AirPlayListener()

    # Service types for AirPlay/RAOP
    service_types = [
        "_raop._tcp.local.",      # Remote Audio Output Protocol (AirPlay audio)
        "_airplay._tcp.local.",   # AirPlay (video/screen mirroring)
    ]

    browsers = []
    for stype in service_types:
        print(f"\nScanning for {stype}...")
        browser = ServiceBrowser(zc, stype, listener)
        browsers.append(browser)

    # Wait for discovery
    print(f"\nScanning for {timeout} seconds...")
    time.sleep(timeout)

    # Cleanup
    for browser in browsers:
        browser.cancel()
    zc.close()

    # Summary
    print("\n" + "=" * 60)
    print("DISCOVERED DEVICES")
    print("=" * 60)

    if not listener.devices:
        print("\nNo AirPlay/RAOP devices found!")
        return []

    devices = list(listener.devices.values())
    for i, dev in enumerate(devices, 1):
        print(f"\n{i}. {dev['name']}")
        print(f"   Address: {dev['addresses'][0] if dev['addresses'] else 'unknown'}:{dev['port']}")
        print(f"   Type: {dev['service_type']}")
        props = dev['properties']
        if 'am' in props:
            print(f"   Model: {props.get('am', 'unknown')}")
        if 'et' in props:
            print(f"   Encryption Types: {props.get('et', 'unknown')}")
        if 'cn' in props:
            print(f"   Audio Codecs: {props.get('cn', 'unknown')}")

    return devices


if __name__ == "__main__":
    devices = scan_for_devices(timeout=10)
    print(f"\n\nTotal devices: {len(devices)}")
