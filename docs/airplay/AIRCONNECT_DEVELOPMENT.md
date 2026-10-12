# AirPlay Development with AirConnect

This document describes how to use AirConnect as a development environment for testing Sonorium's AirPlay streaming functionality.

## Overview

AirConnect is an AirPlay bridge that exposes UPnP/Chromecast devices as AirPlay receivers. It provides a reliable AirPlay 1 target for development and testing without needing to use production speakers.

**Benefits:**
- Simulates real AirPlay devices
- Bridges to Chromecast/UPnP devices
- Avoids disturbing production speakers during development
- Full RAOP protocol implementation

## Setup

### 1. Download AirConnect

Download from: https://github.com/philippe44/AirConnect/releases

- `airupnp` - Bridges AirPlay to UPnP/DLNA speakers
- `aircast` - Bridges AirPlay to Chromecast devices

### 2. Run AirConnect

```bash
# For Chromecast bridging
aircast.exe

# For UPnP bridging
airupnp.exe
```

AirConnect will:
- Bind to a local IP (e.g., 192.168.1.198)
- Discover Chromecast/UPnP devices on the network
- Advertise them as AirPlay receivers via mDNS

### 3. Verify Discovery

The bridged devices will appear in mDNS discovery with:
- Service: `_raop._tcp`
- Model: `airupnp` or `aircast`
- Session: `DEADBEEF` (hex format)

## pyatv Patches Required

AirConnect has some non-standard behaviors that require patches:

### 1. CSeq-less Responses

AirConnect's 501 responses lack CSeq headers, which breaks pyatv's request/response matching.

**Patch:** `patch_rtsp_session()` - Matches responses to single pending request when CSeq is missing.

### 2. Empty /info Bodies

GET /info returns 501 with empty body instead of binary plist.

**Patch:** `patch_empty_bplist()` - Returns empty dict instead of raising exception.

### 3. Hex Session IDs

SETUP returns "DEADBEEF" instead of decimal integer.

**Patch:** `patch_hex_session_id()` - Parses hex session IDs with fallback to random.

### Applying Patches

```python
from sonorium.pyatv_patches import apply_patches

# Call once at startup before any pyatv operations
apply_patches()
```

## Test Harness

Use `tests/test_airplay_airconnect.py` for development testing:

```bash
# Discover AirConnect devices only
python tests/test_airplay_airconnect.py --discover

# Stream test tone
python tests/test_airplay_airconnect.py --stream

# Target specific IP
python tests/test_airplay_airconnect.py --target 192.168.1.198 --stream

# Full test suite
python tests/test_airplay_airconnect.py
```

The test harness:
- Filters to only AirConnect devices (model = airupnp/aircast)
- Excludes Arylic/Linkplay devices
- Generates test tones at moderate volume
- Applies patches automatically

## RTSP Protocol Flow

Successful AirConnect streaming follows this sequence:

```
Client                          AirConnect
   |                                |
   |-- GET /info ------------------>|
   |<-- 501 Not Implemented --------|  (expected)
   |                                |
   |-- ANNOUNCE ------------------->|
   |<-- 200 OK ---------------------|
   |                                |
   |-- SETUP ---------------------->|
   |<-- 200 OK + Session: DEADBEEF -|
   |                                |
   |-- SET_PARAMETER (volume) ----->|
   |<-- 200 OK ---------------------|
   |                                |
   |-- SET_PARAMETER (metadata) --->|
   |<-- 200 OK ---------------------|
   |                                |
   |-- RECORD --------------------->|
   |<-- 200 OK ---------------------|
   |                                |
   |-- FLUSH ---------------------->|
   |<-- 200 OK ---------------------|
   |                                |
   |== RTP Audio Packets ===========>|  (UDP)
   |== NTP Timing Packets ==========>|  (UDP)
```

## Troubleshooting

### Device Not Found

1. Verify AirConnect is running: Check console output
2. Check IP binding: AirConnect binds to specific interface
3. Verify mDNS: Use `python tests/test_airplay_airconnect.py --discover`

### Connection Drops After SETUP

Usually caused by:
- Missing patches (especially hex session ID)
- Firewall blocking UDP ports
- NTP timing sync issues

### WinError 1234

"No service is operating at the destination network endpoint"

This often appears during cleanup after successful streaming - not a real error if audio played.

### No Audio on Speaker

Check AirConnect logs for:
- `1st audio packet received` - Audio reached AirConnect
- `CastPlay: Queuing PLAY` - Chromecast received stream
- `now playing` - Playback started

## Files

| File | Purpose |
|------|---------|
| `tests/test_airplay_airconnect.py` | AirConnect test harness |
| `docs/airplay/AIRCONNECT_DEVELOPMENT.md` | This documentation |

## Known Limitations

1. **Cleanup Errors** - pyatv may report "not connected" during TEARDOWN even after successful streaming
2. **Dynamic Ports** - AirConnect uses different ports on each restart
3. **POST Not Supported** - AirConnect returns 501 for POST requests (artwork)
