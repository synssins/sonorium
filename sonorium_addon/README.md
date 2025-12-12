# Sonorium v2 Beta

![Sonorium](https://raw.githubusercontent.com/synssins/sonorium/main/sonorium_addon/logo.png)

**Multi-Zone Ambient Soundscape Mixer for Home Assistant**

Sonorium creates immersive ambient audio environments throughout your home. Stream richly layered soundscapes—from thunderstorms and rainfall to forest ambiance and ocean waves—to any combination of speakers in your Home Assistant setup.

> ⚠️ **BETA VERSION**: This is the v2 development branch with new multi-zone and session management features. For stable single-zone streaming, use the main Sonorium addon.

## What's New in v2

- **Multi-Zone Sessions**: Play different themes to different rooms simultaneously
- **Channel-Based Streaming**: Seamless theme transitions without speaker reconnection
- **Speaker Groups**: Create reusable speaker configurations by floor, area, or individual device
- **Smart Auto-Naming**: Sessions automatically named based on selected speakers/areas
- **Live Theme Crossfading**: Switch themes mid-playback with smooth 3-second crossfades
- **Persistent State**: Sessions and groups survive addon restarts

## Acknowledgements

Sonorium is a fork of [Amniotic](https://github.com/fmtr/amniotic) by [fmtr](https://github.com/fmtr). The original Amniotic project laid the groundwork for this addon with its innovative approach to ambient soundscape mixing in Home Assistant.

---

## ⚡ Performance Considerations

Sonorium performs real-time audio mixing and MP3 encoding. Each active streaming channel consumes CPU and memory resources. Use the table below to configure `max_channels` appropriately for your hardware.

### Recommended Maximum Channels by Hardware

| Hardware | CPU | RAM | Max Channels | Notes |
|----------|-----|-----|--------------|-------|
| **Raspberry Pi 4** | 4-core 1.5GHz | 2-4GB | **2-3** | Adequate for small homes; avoid running during automations |
| **Raspberry Pi 5** | 4-core 2.4GHz | 4-8GB | **4-5** | Good performance; recommended minimum for multi-zone |
| **Odroid N2+** | 6-core 2.4GHz | 4GB | **4-5** | Similar to Pi 5 |
| **Intel NUC (Celeron)** | 2-4 core ~2GHz | 4-8GB | **4-6** | Efficient x86 option |
| **Intel NUC (i3/i5)** | 4-core 2-3GHz | 8-16GB | **6-8** | Excellent performance |
| **Mini PC (N100)** | 4-core 3.4GHz | 8-16GB | **6-8** | Great price/performance |
| **Mini PC (i5/Ryzen)** | 4-6 core 3+GHz | 16GB+ | **8-10** | Full capability |
| **VM/Container** | Varies | Varies | **Allocate accordingly** | Ensure dedicated CPU cores |

### Resource Usage Per Channel

| Resource | Per Active Channel | Notes |
|----------|-------------------|-------|
| **CPU** | ~5-15% of one core | Depends on track count per theme |
| **Memory** | ~50-100MB | Audio buffers and encoding state |
| **Network** | ~128kbps out per client | MP3 stream at 128kbps × connected speakers |

### Performance Tips

1. **Start Conservative**: Begin with `max_channels: 2` and increase if system remains responsive
2. **Monitor During Playback**: Check CPU usage in HA System Monitor while streaming
3. **Fewer Tracks Per Theme**: Themes with 5-10 tracks perform better than 50+ tracks
4. **Avoid Peak Times**: Heavy automations + Sonorium streaming can cause latency
5. **Consider Dedicated Hardware**: For whole-home audio, a dedicated mini PC is ideal

---

## 🔧 Dependencies

### System Requirements

- Home Assistant 2024.1.0 or newer
- MQTT broker (Mosquitto addon recommended)
- Media player entities that support HTTP streams

### HA Native Integrations Used

Sonorium is designed to leverage native Home Assistant capabilities wherever possible:

| Feature | HA Integration | Notes |
|---------|---------------|-------|
| **Speaker Discovery** | `media_player.*` | Any media_player supporting `play_media` service |
| **Area/Floor Hierarchy** | HA Registry API | Uses native floors, areas, and device assignments |
| **Playback Control** | `media_player.play_media` | Standard HA service calls |
| **Volume Control** | `media_player.volume_set` | Per-speaker volume via HA |
| **MQTT Entities** | MQTT Discovery | Native entity creation (v1 style) |
| **State Persistence** | Local JSON | `/config/sonorium/state.json` |

### Python Dependencies

Installed automatically in the addon container:

```
numpy          # Audio array operations
av (PyAV)      # FFmpeg bindings for audio encoding
fastapi        # REST API framework
uvicorn        # ASGI server
httpx          # HTTP client for HA API calls
pydantic       # Data validation
pyyaml         # Configuration parsing
fmtr.tools     # Utility libraries (logging, caching, etc.)
```

### External Services

| Service | Required | Purpose |
|---------|----------|--------|
| **MQTT Broker** | Yes | Entity publishing and state updates |
| **Home Assistant API** | Yes | Speaker discovery and media control |
| **FFmpeg** | Bundled | Audio decoding and MP3 encoding |

---

## 📋 Quick Start

### 1. Audio Setup

Create theme folders in `/media/sonorium/` with your audio files:

```
/media/sonorium/
├── Thunder/
│   ├── distant_thunder.mp3
│   ├── rain_heavy.mp3
│   └── wind_howling.ogg
├── Forest/
│   ├── birds_morning.mp3
│   ├── stream_babbling.wav
│   └── wind_leaves.mp3
├── Ocean/
│   └── waves_gentle.mp3
└── Fireplace/
    ├── fire_crackle.mp3
    └── room_ambiance.mp3
```

**Supported formats:** `.mp3`, `.wav`, `.flac`, `.ogg`

### 2. Configure the Add-on

In addon configuration:

```yaml
sonorium__stream_url: "http://192.168.1.100:8008"  # Your HA IP (not hostname!)
sonorium__path_audio: "/media/sonorium"
sonorium__max_channels: 6  # Adjust for your hardware
```

> ⚠️ **Important**: Use your Home Assistant's IP address, not `homeassistant.local`. Many speakers don't resolve mDNS hostnames.

### 3. Start and Use

1. Start the addon
2. Open the Web UI at `http://[your-ha-ip]:8008/`
3. Create a session, select speakers, choose a theme
4. Press Play!

---

## 🎛️ Configuration Options

| Option | Description | Default | Range |
|--------|-------------|---------|-------|
| `sonorium__stream_url` | Base URL for audio streams | `http://homeassistant.local:8008` | Valid URL |
| `sonorium__path_audio` | Path to audio files | `/media/sonorium` | Valid path |
| `sonorium__max_channels` | Maximum concurrent streams | `6` | 1-10 |

---

## 🌐 Web Interface

Access the v2 Web UI at `http://[your-ha-ip]:8008/`

### Features

- **Session Cards**: Create and manage multiple playback sessions
- **Theme Selector**: Visual theme picker with track counts
- **Speaker Hierarchy**: Browse speakers by floor → area → device
- **Live Status**: Real-time playback state and volume control
- **Channel Monitor**: View active streaming channels

### Legacy UI

The original v1 interface is available at `http://[your-ha-ip]:8008/v1`

---

## 📡 API Reference

### Streaming Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/stream/{theme_id}` | GET | Legacy theme-based audio stream |
| `/stream/channel{n}` | GET | Channel-based audio stream (v2) |

### Session Management

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
| `/api/sessions/stop-all` | POST | Stop all sessions |

### Speaker & Channel APIs

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/speakers` | GET | List all speakers |
| `/api/speakers/hierarchy` | GET | Floor/area/speaker tree |
| `/api/speakers/refresh` | POST | Refresh from HA |
| `/api/channels` | GET | List all channels |
| `/api/channels/{id}` | GET | Get channel status |
| `/api/themes` | GET | List all themes |

---

## 🏗️ Architecture

### Audio Pipeline

```
Audio Files (.mp3, .wav, .flac, .ogg)
    │
    ▼
┌─────────────────────────────────────────┐
│  Theme Definition                        │
│  └── Recording Instances (tracks)        │
│       └── CrossfadeStream                │
│            (per-track looping)           │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  ThemeStream                             │
│  (mixes all tracks with normalization)   │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  Channel                                 │
│  (tracks current theme, version counter) │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  ChannelStream (per connected client)    │
│  (independent crossfade, MP3 encoding)   │
└─────────────────────────────────────────┘
    │
    ▼
HTTP StreamingResponse → Media Players
```

### Key Concepts

- **Session**: A playback configuration (theme + speakers + volume)
- **Channel**: A persistent audio stream that speakers connect to
- **Theme**: A folder of audio files that are mixed together
- **Speaker Group**: A saved selection of speakers for reuse

---

## 🗺️ Roadmap

See [ROADMAP.md](ROADMAP.md) for:
- Completed features
- In-progress work
- Planned features
- Known issues
- Version history

### Upcoming Features

- **Theme Cycling**: Auto-rotate through themes on a timer
- **Randomization**: Shuffle theme order during cycling
- **HA Media Player Entities**: Expose sessions as native HA entities
- **Schedule-Based Playback**: Time-based automation support

---

## 🐛 Troubleshooting

### Speakers Not Playing

1. **Check stream URL**: Must be IP address, not hostname
2. **Verify speaker support**: Must support `play_media` with HTTP URLs
3. **Check logs**: Addon logs show detailed playback status

### Audio Stuttering

1. **Reduce max_channels**: Lower to match hardware capability
2. **Check network**: Ensure stable connection to speakers
3. **Simplify themes**: Fewer tracks = less CPU usage

### Theme Not Loading

1. **Check file formats**: Only `.mp3`, `.wav`, `.flac`, `.ogg` supported
2. **Verify path**: Files must be in `/media/sonorium/{theme_name}/`
3. **Restart addon**: Theme scanning happens at startup

---

## 📄 Version

**2.0.0b5** (Beta)

## 🔗 Links

- [Full Documentation](DOCS.md)
- [Development Roadmap](ROADMAP.md)
- [Original Amniotic Project](https://github.com/fmtr/amniotic)