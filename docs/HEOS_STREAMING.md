# HEOS Streaming Support

## Status: Implemented (Beta)

**Version:** 0.2.48-dev

HEOS support has been implemented for the Windows/Docker standalone app. HEOS speakers are discovered automatically and can stream Sonorium audio.

## Summary

**HEOS can stream HTTP audio URLs** via the CLI Protocol's `play_stream` command. This makes it suitable for Sonorium integration.

## HEOS CLI Protocol

HEOS devices expose a telnet-based CLI on **port 1255**. Commands follow this format:
```
heos://command_group/command?attribute1=value1&attribute2=value2\r\n
```

Responses are JSON.

### Key Commands for Sonorium

#### Get Players
```
heos://player/get_players
```
Returns list of available HEOS players with their `pid` (player ID).

#### Play URL (The Critical Command)
```
heos://browse/play_stream?pid=player_id&url=url_path
```
- `pid`: Player ID from `get_players`
- `url`: Absolute path to a playable stream (e.g., `http://sonorium:8008/stream/channel1`)

#### Volume Control
```
heos://player/set_volume?pid=player_id&level=0-100
heos://player/set_mute?pid=player_id&state=on|off
```

#### Playback Control
```
heos://player/set_play_state?pid=player_id&state=play|pause|stop
```

## Python Libraries

### pyheos (Recommended)
- **Repository**: https://github.com/andrewsayre/pyheos
- **Install**: `pip install pyheos`
- **Used by**: Home Assistant HEOS integration
- **Features**: Async, auto-reconnection, event-based updates

#### Key API
```python
from pyheos import Heos

# Connect to any HEOS device (acts as bridge to all)
heos = await Heos.create_and_connect(host="192.168.1.x")

# Get players
players = await heos.get_players()

# Play URL on a specific player
player = players[player_id]
await player.play_url("http://sonorium:8008/stream/channel1")

# Volume control
await player.set_volume(50)
await player.set_mute(False)

# Disconnect
await heos.disconnect()
```

### heospy (Simpler, Archived)
- **Repository**: https://github.com/ping13/heospy
- **Note**: Archived, no longer maintained
- **Simpler command-line focused approach**

## Implementation Plan for Sonorium

### 1. Device Discovery
HEOS devices advertise via SSDP (same as DLNA) with:
- Search target: `urn:schemas-denon-com:device:ACT-Denon:1`
- Alternative: mDNS `_heos-audio._tcp.local.`

Or simpler: Connect to any known HEOS device IP - it acts as a bridge to all HEOS devices on the network.

### 2. Connection Model
```
Sonorium -> Telnet:1255 -> Any HEOS Device (Bridge)
                |
                v
         All HEOS Players on Network
```

A single telnet connection to any HEOS device provides control over ALL HEOS devices on the network.

### 3. Streaming Architecture
```
HEOS Speaker -> HTTP GET -> Sonorium:8008/stream/channel1
```
HEOS players pull the HTTP audio stream, same model as DLNA and Sonos.

### 4. Code Structure
Add to `app/core/sonorium/`:
- `heos_speaker.py` - HEOS device handling
- Update `network_speakers.py` - Add HEOS discovery
- Update `streaming.py` - Add HEOS streaming support

### 5. Dependencies
- `pyheos` - Primary library (async, well-maintained)
- No additional native dependencies required

## Protocol Comparison

| Feature | DLNA | Sonos | HEOS | AirPlay |
|---------|------|-------|------|---------|
| Discovery | SSDP | SSDP | SSDP/mDNS | mDNS |
| Control Port | HTTP | HTTP | Telnet 1255 | RAOP |
| Stream Model | HTTP Pull | HTTP Pull | HTTP Pull | RAOP Push |
| URL Playback | Yes (SetAVTransportURI) | Yes (play_uri) | Yes (play_stream) | No* |
| Python Library | dlnap/async_upnp_client | soco | pyheos | pyatv |

*AirPlay requires push streaming via RAOP protocol, not HTTP URLs.

## Testing Without Hardware

Since you don't have HEOS speakers, testing options:
1. Request beta testers with HEOS hardware
2. Use Home Assistant's HEOS integration as reference
3. Implement with stubbed responses, verify with real hardware later

## References

- HEOS CLI Protocol Specification v1.17: https://rn.dmglobal.com/usmodel/HEOS_CLI_ProtocolSpecification-Version-1.17.pdf
- pyheos GitHub: https://github.com/andrewsayre/pyheos
- Home Assistant HEOS Integration: https://www.home-assistant.io/integrations/heos/
- openHAB HEOS Binding: https://www.openhab.org/addons/bindings/heos/
