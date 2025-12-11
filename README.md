# Sonorium

**Ambient Soundscape Mixer for Home Assistant**

[![Add Repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsynssins%2Fsonorium)

Sonorium lets you create immersive ambient audio environments throughout your home. Stream richly layered sounds—from distant thunder and rainfall to forest ambiance and ocean waves—to any media player in your Home Assistant setup.

## Acknowledgements

Sonorium is a fork of [Amniotic](https://github.com/fmtr/amniotic) by [fmtr](https://github.com/fmtr). The original Amniotic project laid the groundwork for this addon with its innovative approach to ambient soundscape mixing in Home Assistant. We're grateful for the time, effort, and creativity that went into building the foundation that Sonorium is built upon.

## Features

- **Theme-Based Organization**: Audio files are organized into theme folders (e.g., "Thunder", "Forest", "Ocean")
- **Automatic Mixing**: All recordings in a theme are mixed together seamlessly
- **Crossfade Looping**: Single-file themes loop seamlessly with 3-second equal-power crossfades
- **Simple Controls**: Just select a theme, pick a speaker, and hit play
- **Master Volume**: Single volume control for the entire mix
- **Any Media Player**: Works with any Home Assistant media_player entity that supports HTTP streams
- **No External Dependencies**: Uses only built-in Home Assistant REST API—no HACS integrations required

## Installation

### Home Assistant Add-on (Recommended)

**One-Click Install:**

[![Add Repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsynssins%2Fsonorium)

**Manual Install:**

1. In Home Assistant, go to **Settings** → **Add-ons** → **Add-on Store**
2. Click the three dots (⋮) → **Repositories**
3. Add: `https://github.com/synssins/sonorium`
4. Find "Sonorium" in the store and click **Install**
5. Configure and start

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

**Single-File Themes:** If a theme folder contains only one audio file, Sonorium will loop it seamlessly using crossfade blending—no more jarring restarts!

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

**Crossfade Looping:** When a track reaches the last 3 seconds, Sonorium starts a new instance and blends them together using equal-power curves for seamless, infinite playback.

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
- Added crossfade looping for seamless single-file playback
- Removed external MQTT media player dependency
- Simplified controls to single play/pause toggle
- Theme-based folder organization
- Master volume control
- Direct Home Assistant REST API integration
- One-click Home Assistant add-on installation

### Previous
- Fork from [fmtr/amniotic](https://github.com/fmtr/amniotic)
- Renamed to Sonorium
- Complete codebase refactor

## License

See LICENSE file for details.
