# Sonorium v2 Beta Documentation

## Table of Contents

- [Configuration](#configuration)
- [Audio Setup](#audio-setup)
- [Sessions & Channels](#sessions--channels)
- [Speaker Management](#speaker-management)
- [API Reference](#api-reference)
- [Home Assistant Integration](#home-assistant-integration)
- [Performance Tuning](#performance-tuning)
- [Troubleshooting](#troubleshooting)

---

## Configuration

### Addon Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `sonorium__stream_url` | string | `http://homeassistant.local:8008` | Base URL for audio streams. **Must use IP address for speaker compatibility.** |
| `sonorium__path_audio` | string | `/media/sonorium` | Path to theme folders containing audio files |
| `sonorium__max_channels` | integer | `6` | Maximum concurrent streaming channels (1-10) |

### Example Configuration

```yaml
sonorium__stream_url: "http://192.168.1.100:8008"
sonorium__path_audio: "/media/sonorium"
sonorium__max_channels: 4
```

### Important Notes

1. **Stream URL**: Use your Home Assistant's IP address (e.g., `192.168.1.100`), not `homeassistant.local`. Many speakers (Sonos, Chromecast, Echo) cannot resolve mDNS hostnames.

2. **Port**: The beta addon uses port `8008` internally, mapped to `8008` externally. This runs alongside the stable addon on port `8007`.

3. **Max Channels**: Set this based on your hardware. See the [Performance Guide](README.md#-performance-considerations) in the README.

---

## Audio Setup

### Directory Structure

```
/media/sonorium/
├── Thunder/
│   ├── distant_rumble.mp3
│   ├── rain_heavy.mp3
│   ├── rain_light.ogg
│   └── wind_storm.wav
├── Forest/
│   ├── birds_dawn.mp3
│   ├── creek_flowing.flac
│   └── wind_trees.mp3
├── Ocean/
│   └── waves_shore.mp3
└── Fireplace/
    ├── fire_crackle.mp3
    └── wood_pop.mp3
```

### Supported Audio Formats

| Format | Extension | Notes |
|--------|-----------|-------|
| MP3 | `.mp3` | Recommended for size/quality balance |
| WAV | `.wav` | Uncompressed, large files |
| FLAC | `.flac` | Lossless compression |
| Ogg Vorbis | `.ogg` | Open format alternative |

### Theme Guidelines

1. **Naming**: Folder name becomes the theme name (e.g., `Thunder/` → "Thunder")
2. **Multiple Tracks**: All files in a folder are mixed together automatically
3. **Single Track**: Single-file themes loop seamlessly with crossfade
4. **Track Count**: 5-20 tracks per theme recommended for best performance
5. **File Length**: Longer files (2+ minutes) work best for natural looping

### Audio Specifications

| Property | Recommendation |
|----------|---------------|
| Sample Rate | 44.1kHz or 48kHz |
| Bit Depth | 16-bit |
| Channels | Mono or Stereo (converted to mono for mixing) |
| Bitrate (MP3) | 128-192kbps |

---

## Sessions & Channels

### Concepts

**Session**: A named playback configuration consisting of:
- Selected theme
- Selected speakers (via group or ad-hoc)
- Volume setting
- Playback state

**Channel**: A persistent audio stream endpoint. When you play a session:
1. A channel is assigned to the session
2. The theme is loaded into the channel
3. Speakers connect to `/stream/channel{n}`
4. Theme changes trigger crossfade within the channel (no reconnection needed)

### Session Lifecycle

```
Create Session → Configure (theme, speakers) → Play → [Change Theme] → Stop → Delete
                                                ↓
                                        Channel assigned
                                                ↓
                                        Speakers connect
                                                ↓
                                        Audio streams
```

### Channel Allocation

- Channels are numbered 1 through `max_channels`
- When you press Play, the lowest available channel is assigned
- When you Stop, the channel is released
- Multiple speakers can connect to the same channel (same audio)
- Different sessions use different channels (different audio)

### Theme Crossfading

When you change themes on a playing session:
1. Each connected speaker detects the theme change
2. A 3-second equal-power crossfade begins
3. Old theme fades out while new theme fades in
4. Speakers never disconnect/reconnect

---

## Speaker Management

### Discovery

Sonorium discovers speakers from Home Assistant's entity registry:
- All `media_player.*` entities are discovered
- Entities are organized by their assigned floor and area
- Refresh manually via `/api/speakers/refresh`

### Speaker Hierarchy

```
Floor (e.g., "First Floor")
└── Area (e.g., "Living Room")
    └── Speaker (e.g., "media_player.living_room_sonos")
```

### Selection Methods

**By Floor**: Select all speakers on a floor
```json
{
  "include_floors": ["floor_first"],
  "exclude_speakers": ["media_player.kitchen_echo"]
}
```

**By Area**: Select all speakers in specific areas
```json
{
  "include_areas": ["area_living_room", "area_bedroom"]
}
```

**By Speaker**: Select individual speakers
```json
{
  "include_speakers": ["media_player.office_sonos"]
}
```

### Speaker Groups

Save frequently-used speaker combinations as groups:

```json
{
  "name": "Downstairs",
  "include_floors": ["floor_first"],
  "exclude_areas": ["area_garage"]
}
```

---

## API Reference

### Base URL

```
http://[your-ha-ip]:8008/api/
```

### Sessions

#### List Sessions
```http
GET /api/sessions
```

Response:
```json
[
  {
    "id": "abc12345",
    "name": "Living Room",
    "theme_id": "thunder",
    "is_playing": true,
    "volume": 50,
    "channel_id": 1,
    "speakers": ["media_player.living_room_sonos"],
    "speaker_summary": "Living Room Sonos"
  }
]
```

#### Create Session
```http
POST /api/sessions
Content-Type: application/json

{
  "theme_id": "thunder",
  "adhoc_selection": {
    "include_areas": ["area_living_room"]
  },
  "volume": 50
}
```

#### Update Session
```http
PUT /api/sessions/{id}
Content-Type: application/json

{
  "theme_id": "forest",
  "volume": 60
}
```

#### Play Session
```http
POST /api/sessions/{id}/play
```

Response:
```json
{
  "status": "playing",
  "channel_id": 1
}
```

#### Stop Session
```http
POST /api/sessions/{id}/stop
```

#### Set Volume
```http
POST /api/sessions/{id}/volume
Content-Type: application/json

{
  "volume": 75
}
```

### Channels

#### List Channels
```http
GET /api/channels
```

Response:
```json
[
  {
    "id": 1,
    "name": "Channel 1",
    "state": "playing",
    "current_theme": "thunder",
    "current_theme_name": "Thunder",
    "client_count": 3,
    "stream_path": "/stream/channel1"
  }
]
```

### Themes

#### List Themes
```http
GET /api/themes
```

Response:
```json
[
  {
    "id": "thunder",
    "name": "Thunder",
    "track_count": 4,
    "url": "http://192.168.1.100:8008/stream/thunder"
  }
]
```

### Speakers

#### Get Hierarchy
```http
GET /api/speakers/hierarchy
```

Response:
```json
{
  "floors": [
    {
      "id": "floor_first",
      "name": "First Floor",
      "areas": [
        {
          "id": "area_living_room",
          "name": "Living Room",
          "speakers": [
            {
              "entity_id": "media_player.living_room_sonos",
              "name": "Living Room Sonos"
            }
          ]
        }
      ]
    }
  ]
}
```

---

## Home Assistant Integration

### Native Services Used

| Service | Purpose |
|---------|---------|
| `media_player.play_media` | Send stream URL to speakers |
| `media_player.volume_set` | Set speaker volume |
| `media_player.media_stop` | Stop playback |
| `media_player.media_pause` | Pause playback |

### Future Native Integration Goals

We aim to maximize use of native HA features:

1. **Media Player Entities**: Sessions exposed as `media_player.sonorium_*` entities
2. **Media Browser**: Theme selection via HA Media Browser
3. **Scenes**: Link sessions to HA scenes
4. **Automations**: Trigger playback via HA automations
5. **Voice Assistants**: "Hey Google, play thunder sounds in the living room"

### Current MQTT Entities (v1 Compatibility)

| Entity | Type | Description |
|--------|------|-------------|
| `select.sonorium_theme` | Select | Choose active theme |
| `select.sonorium_media_player` | Select | Target speaker |
| `number.sonorium_master_volume` | Number | Master volume (0-100) |
| `switch.sonorium_play` | Switch | Play/Pause toggle |

---

## Performance Tuning

### Monitoring

Check addon logs for performance indicators:
```
Channel 1: Client connected (3 total)
CrossfadeStream: chunk #500, samples=512000
Started playback on 5/5 speakers
```

### Optimization Strategies

1. **Reduce Track Count**: Themes with 5-10 tracks use less CPU than 50+ tracks
2. **Lower Max Channels**: Set to only what you need
3. **Longer Audio Files**: Reduces file switching overhead
4. **Consistent Sample Rates**: 44.1kHz across all files avoids resampling

### Memory Usage Estimate

```
Base addon:        ~100MB
Per theme loaded:  ~20-50MB (depends on track count)
Per active stream: ~50-100MB (encoding buffers)
```

### CPU Usage Factors

| Factor | Impact |
|--------|--------|
| Track count per theme | High - each track is decoded and mixed |
| Number of active channels | High - each channel does independent encoding |
| Connected clients | Medium - each client has encoding overhead |
| Audio file format | Low - FLAC slightly higher than MP3 |

---

## Troubleshooting

### Common Issues

#### "Generator already executing" Error
**Fixed in v2.0.0b4**. Update to latest version.

#### Speakers Don't Play
1. Verify `stream_url` uses IP address, not hostname
2. Check speaker supports HTTP stream URLs
3. Test URL directly: `http://[ip]:8008/stream/channel1` in browser
4. Check addon logs for errors

#### Theme Not Appearing
1. Verify folder exists in `/media/sonorium/`
2. Check files have supported extensions
3. Restart addon to rescan themes

#### Audio Stuttering
1. Reduce `max_channels`
2. Check HA system CPU usage
3. Reduce tracks per theme
4. Ensure stable network to speakers

#### Crossfade Not Working
1. Must change theme while session is playing
2. Check logs for "Starting crossfade" message
3. Verify channel is in "playing" state

### Log Locations

- **Addon Logs**: Settings → Add-ons → Sonorium Beta → Logs
- **State File**: `/config/sonorium/state.json`
- **HA Core Logs**: Settings → System → Logs

### Debug Mode

Enable verbose logging in addon configuration (future feature).

---

## Version Information

- **Current Version**: 2.0.0b4
- **Minimum HA Version**: 2024.1.0
- **Python Version**: 3.12
- **Base Image**: hassio-addons/base:16.3.2

## Links

- [README](README.md)
- [Roadmap](ROADMAP.md)
- [Changelog](CHANGELOG.md)
