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

2. **Port**: The addon uses port `8008` internally, mapped to `8008` externally.

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

---

## API Reference

### Base URL

```
http://[your-ha-ip]:8008/api/
```

### Sessions

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sessions` | GET | List all sessions |
| `/api/sessions` | POST | Create new session |
| `/api/sessions/{id}` | GET | Get session details |
| `/api/sessions/{id}` | PUT | Update session |
| `/api/sessions/{id}` | DELETE | Delete session |
| `/api/sessions/{id}/play` | POST | Start playback |
| `/api/sessions/{id}/stop` | POST | Stop playback |
| `/api/sessions/{id}/volume` | POST | Set volume |

### Theme Cycling

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sessions/{id}/cycle` | GET | Get cycle status |
| `/api/sessions/{id}/cycle` | PUT | Update cycle config |
| `/api/sessions/{id}/cycle/skip` | POST | Skip to next theme |

### Channels

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/channels` | GET | List all channels |
| `/api/channels/{id}` | GET | Get channel status |

### Themes & Speakers

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/themes` | GET | List all themes |
| `/api/speakers` | GET | List all speakers |
| `/api/speakers/hierarchy` | GET | Floor/area/speaker tree |
| `/api/speakers/refresh` | POST | Refresh from HA |

---

## Home Assistant Integration

### Native Services Used

| Service | Purpose |
|---------|--------|
| `media_player.play_media` | Send stream URL to speakers |
| `media_player.volume_set` | Set speaker volume |
| `media_player.media_stop` | Stop playback |
| `media_player.media_pause` | Pause playback |

### Current MQTT Entities (v1 Compatibility)

| Entity | Type | Description |
|--------|------|-------------|
| `select.sonorium_theme` | Select | Choose active theme |
| `select.sonorium_media_player` | Select | Target speaker |
| `number.sonorium_master_volume` | Number | Master volume (0-100) |
| `switch.sonorium_play` | Switch | Play/Pause toggle |

---

## Performance Tuning

### Optimization Strategies

1. **Reduce Track Count**: Themes with 5-10 tracks use less CPU than 50+ tracks
2. **Lower Max Channels**: Set to only what you need
3. **Longer Audio Files**: Reduces file switching overhead
4. **Consistent Sample Rates**: 44.1kHz across all files avoids resampling

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

#### Speakers Don't Play
1. Verify `stream_url` uses IP address, not hostname
2. Check speaker supports HTTP stream URLs
3. Test URL directly: `http://[ip]:8008/stream/channel1` in browser
4. Check addon logs for errors

#### Audio Stuttering
1. Reduce `max_channels`
2. Check HA system CPU usage
3. Reduce tracks per theme
4. Ensure stable network to speakers

#### Theme Not Appearing
1. Verify folder exists in `/media/sonorium/`
2. Check files have supported extensions
3. Restart addon to rescan themes

---

## Version Information

- **Current Version**: 2.0.0b5
- **Minimum HA Version**: 2024.1.0
- **Python Version**: 3.12

## Links

- [README](README.md)
- [Roadmap](ROADMAP.md)
- [Changelog](CHANGELOG.md)