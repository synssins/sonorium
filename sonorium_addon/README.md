# Sonorium

![Sonorium](https://raw.githubusercontent.com/synssins/sonorium/main/sonorium_addon/logo.png)

**Ambient Soundscape Mixer for Home Assistant**

Sonorium lets you create immersive ambient audio environments throughout your home. Stream richly layered sounds—from distant thunder and rainfall to forest ambiance and ocean waves—to any media player in your Home Assistant setup.

## Acknowledgements

Sonorium is a fork of [Amniotic](https://github.com/fmtr/amniotic) by [fmtr](https://github.com/fmtr). The original Amniotic project laid the groundwork for this addon with its innovative approach to ambient soundscape mixing in Home Assistant. We're grateful for the time, effort, and creativity that went into building the foundation that Sonorium is built upon.

## Features

- **Theme-Based Organization**: Audio files organized into theme folders (e.g., "Thunder", "Forest", "Ocean")
- **Automatic Mixing**: All recordings in a theme are mixed together seamlessly
- **Crossfade Looping**: Single-file themes loop seamlessly with 3-second equal-power crossfades
- **Simple Controls**: Just select a theme, pick a speaker, and hit play
- **Master Volume**: Single volume control for the entire mix
- **Any Media Player**: Works with any Home Assistant media_player entity that supports HTTP streams
- **No External Dependencies**: Uses only built-in Home Assistant REST API—no HACS integrations required

## Quick Start

### 1. Audio Setup

Create theme folders in `/media/sonorium/` with your audio files:

```
/media/sonorium/
├── Thunder/
│   ├── distant_thunder.mp3
│   └── rain_on_roof.mp3
├── Forest/
│   ├── birds_morning.mp3
│   └── wind_leaves.mp3
└── Ocean/
    └── waves_gentle.mp3
```

Supported formats: `.mp3`, `.wav`, `.flac`, `.ogg`

### 2. Configure the Add-on

After installation, configure:
- **media_dir**: Path to your soundscape files (default: `/media/sonorium`)
- **host**: IP address for the stream server (default: `0.0.0.0`)
- **port**: Port for the audio stream (default: `8007`)

### 3. Start and Use

1. Start the add-on
2. Select a theme from `select.sonorium_theme`
3. Choose your speaker from `select.sonorium_media_player`
4. Toggle `switch.sonorium_play` to start streaming!

## Entities Created

| Entity | Type | Description |
|--------|------|-------------|
| `select.sonorium_theme` | Select | Choose the soundscape theme |
| `select.sonorium_media_player` | Select | Target speaker/media player |
| `number.sonorium_master_volume` | Number | Master volume (0-100%) |
| `switch.sonorium_play` | Switch | Play/Pause toggle |
| `sensor.sonorium_stream_url` | Sensor | Current stream URL |

## How It Works

**Multi-Track Mixing:** When a theme has multiple audio files, they're all mixed together in real-time using sqrt(n) normalization for balanced output.

**Crossfade Looping:** For single-file themes, Sonorium implements seamless looping by starting a second decoder 3 seconds before the track ends and applying equal-power crossfade curves—no more jarring restarts!

## Web Interface

Access the built-in web UI at `http://[your-ha-ip]:8007/` for:
- Theme overview with track counts
- Enable/disable tracks within a theme
- Direct stream playback in browser

## Version

1.3.1

## Links

- [Full Documentation](https://github.com/synssins/sonorium)
- [Original Amniotic Project](https://github.com/fmtr/amniotic)
