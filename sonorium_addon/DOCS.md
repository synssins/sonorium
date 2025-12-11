# Sonorium Documentation

## Configuration

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `sonorium__stream_url` | Base URL for the audio stream | `http://homeassistant.local:8007` |
| `sonorium__path_audio` | Path to audio files | `/media/sonorium` |

## Audio Setup

Create theme folders in your media directory:

```
/media/sonorium/
├── Forest/
│   ├── birds.mp3
│   ├── wind.ogg
│   └── stream.wav
├── Ocean/
│   ├── waves.mp3
│   └── seagulls.ogg
└── Rain/
    └── rain_loop.mp3
```

Each folder becomes a selectable theme. All audio files in a theme folder are automatically mixed together.

## Entities

After starting the add-on, these entities are created:

| Entity | Type | Description |
|--------|------|-------------|
| `select.sonorium_theme` | Select | Choose active theme |
| `select.sonorium_media_player` | Select | Target playback device |
| `number.sonorium_master_volume` | Number | Master volume (0-100) |
| `switch.sonorium_play` | Switch | Play/Pause toggle |
| `sensor.sonorium_stream_url` | Sensor | Current stream URL |

## Web Interface

Access the web UI at `http://[your-ha-ip]:8007/`

## API Endpoints

- `GET /` - Web interface
- `GET /stream/{id}` - Audio stream
- `GET /api/status` - Current status JSON
- `POST /api/enable_all` - Enable all recordings
- `POST /api/disable_all` - Disable all recordings
