# Sonorium

**Ambient Soundscape Mixer for Home Assistant**

Sonorium lets you create immersive ambient audio environments throughout your home. Stream richly layered sounds—from distant thunder and rainfall to forest ambiance and ocean waves—to any media player in your Home Assistant setup.

## Features

- **Theme-Based Organization**: Audio files are organized into theme folders (e.g., "Thunder", "Forest", "Ocean")
- **Automatic Mixing**: All recordings in a theme are mixed together seamlessly
- **Simple Controls**: Just select a theme, pick a speaker, and hit play
- **Master Volume**: Single volume control for the entire mix
- **Any Media Player**: Works with any Home Assistant media_player entity that supports HTTP streams
- **No External Dependencies**: Uses only built-in Home Assistant REST API—no HACS integrations required

## Installation

### Home Assistant Add-on (Recommended)

1. Add this repository to your Home Assistant Add-on Store
2. Install the Sonorium add-on
3. Configure and start

### Audio Setup

Create theme folders in `/media/sonorium/` with audio files:

```
/media/sonorium/
├── Thunder/
│   ├── distant_thunder_1.mp3
│   ├── distant_thunder_2.mp3
│   └── rain_on_roof.mp3
├── Forest/
│   ├── birds_morning.mp3
│   ├── wind_leaves.mp3
│   └── stream_babbling.mp3
└── Ocean/
    ├── waves_gentle.mp3
    └── seagulls.mp3
```

Supported formats: `.mp3`, `.wav`, `.flac`, `.ogg`

## Dashboard

Add this to your Lovelace dashboard:

```yaml
type: vertical-stack
cards:
  - type: entities
    title: Sonorium
    entities:
      - entity: select.sonorium_theme
        name: Theme
      - entity: select.sonorium_media_player
        name: Stream To
      - entity: number.sonorium_master_volume
        name: Volume
  - type: custom:button-card
    entity: switch.sonorium_play
    show_name: false
    show_state: false
    styles:
      card:
        - height: 80px
      icon:
        - width: 40px
    state:
      - value: 'off'
        icon: mdi:play
        color: '#3b82f6'
        tap_action:
          action: toggle
      - value: 'on'
        icon: mdi:pause
        color: '#eab308'
        tap_action:
          action: toggle
```

*Note: Requires [button-card](https://github.com/custom-cards/button-card) from HACS for the play/pause styling*

## Entities

| Entity | Type | Description |
|--------|------|-------------|
| `select.sonorium_theme` | Select | Choose the soundscape theme |
| `select.sonorium_media_player` | Select | Target speaker/media player |
| `number.sonorium_master_volume` | Number | Master volume (0-100%) |
| `switch.sonorium_play` | Switch | Play/Pause toggle |
| `sensor.sonorium_stream_url` | Sensor | Current stream URL |

## How It Works

1. **Select a Theme**: All audio files in that theme folder are loaded
2. **Choose a Speaker**: Pick any media_player entity from your Home Assistant
3. **Press Play**: Sonorium mixes all tracks together in real-time and streams to your speaker
4. **Adjust Volume**: Use the master volume to control output level

The mixing uses sqrt(n) normalization to blend multiple tracks without clipping while maintaining good volume levels.

## Web UI

Access the built-in web interface at `http://[your-ha-ip]:8007/` for:
- Theme overview with track counts
- Enable/disable all tracks in a theme
- Direct stream playback in browser

## API Endpoints

- `GET /` - Web UI
- `GET /stream/{theme_id}` - Audio stream for theme
- `POST /api/enable_all/{theme_id}` - Enable all recordings
- `POST /api/disable_all/{theme_id}` - Disable all recordings
- `GET /api/status` - Current status JSON

## Version History

### v1.3.1
- Removed external MQTT media player dependency
- Simplified controls to single play/pause toggle
- Theme-based folder organization
- Master volume control
- Direct Home Assistant REST API integration

### Previous
- Fork from [fmtr/amniotic](https://github.com/fmtr/amniotic)
- Renamed to Sonorium
- Complete codebase refactor

## Credits

Originally forked from [Amniotic](https://github.com/fmtr/amniotic) by fmtr.

## License

See LICENSE file for details.
