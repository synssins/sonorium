# Sonorium

![Sonorium](logo.png)

**Multi-Zone Ambient Soundscape Mixer**

Sonorium lets you create immersive ambient audio environments. Stream richly layered soundscapes—from distant thunder and rainfall to forest ambiance and ocean waves—to speakers throughout your home or directly through your computer.

## Two Ways to Use Sonorium

### Standalone Windows App

Download and run without any dependencies. Perfect for desktop ambient sound.

**[Download Latest Release](https://github.com/synssins/sonorium/releases)** | **[Installation Guide](https://github.com/synssins/sonorium/wiki/Standalone-App)**

- Single portable executable—no installation required
- Local audio playback through your default speakers
- Stream to DLNA network speakers
- Automatic updates built-in

### Home Assistant Addon

Integrate with your smart home for whole-house audio.

[![Add Repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsynssins%2Fsonorium.dev)

- One-click install from addon store
- Use any Home Assistant media_player
- Organize speakers by room, floor, or area
- Control from the HA dashboard

## Screenshots

### Channels View
Create and manage multiple audio channels, each streaming to different speakers.

![Channels](screenshots/Channels.png)

### Theme Selection
Choose from your library of ambient themes for each channel.

![Theme Selection](screenshots/Channels_Theme_Selection.png)

### Themes Library
Organize your audio files into themes with favorites and categories.

![Themes](screenshots/Themes.png)

### Settings
Configure speakers, volume defaults, and other preferences.

![Settings](screenshots/Settings.png)

---

## What's New

### Home Assistant Addon v1.2.66

> **Stable Release** - Google Cast and Sonos streaming now work reliably across network configurations.

#### Google Cast Streaming Fixed
- **HA API Fallback** - When Cast device IP cannot be discovered (e.g., device on different VLAN), Sonorium now falls back to Home Assistant's `media_player.play_media` service. This allows Cast streaming to work across VLANs without manual IP configuration.
- **mDNS Discovery** - Added zeroconf/mDNS discovery as an additional IP resolution method for Cast devices on the same network segment.
- **Improved Device Detection** - Broader Cast device recognition (Nest Hub, Chromecast, Google Home variants).

#### Sonos WebSocket Fix
- **Large Installation Support** - Fixed "message too big" error that occurred when querying device registry in Home Assistant installations with many devices. Increased WebSocket message limit from 1MB to 10MB.

#### Settings → Speakers UI Fix
- **Restored Floor/Room Hierarchy** - Fixed a regression where the Settings → Speakers page showed a spinning circle instead of the proper floor/area/speaker tree view. A previous erroneous merge of standalone app code into the HA addon broke the HA-specific speaker management functions.

#### Sparse Playback Timing
- **Exclusive Track Spacing** - Increased the minimum gap between exclusive tracks from 30 seconds to 2 minutes. This prevents multiple exclusive tracks (like different lute songs in a tavern theme) from playing back-to-back when their randomized initial delays happen to cluster together.

### Home Assistant Addon v1.2.40

- **Dashboard Integration** - MQTT entities for full dashboard control (session select, theme/preset dropdowns, play/stop, volume)
- **Human-Readable Names** - Theme and preset dropdowns show names instead of UUIDs
- **Reliable Entity Discovery** - Fixed timing issues where entities showed "unavailable" after restart
- **Auto-Session Selection** - First session auto-selected on startup for immediate control
- **Morning Alarms** - Use HA automations to wake up to ambient sounds

### Windows App v0.2.48-dev (Beta)

> ⚠️ **Beta Testing:** This version is available in the [dev channel](https://github.com/synssins/sonorium.dev) for testing. Please report issues!

- **HEOS Speaker Support (Beta)** - Denon/Marantz HEOS speakers discovered and controlled via CLI protocol
- **HEOS Discovery** - Automatic detection of HEOS devices via SSDP and mDNS
- **pyheos Integration** - Uses pyheos library for reliable HEOS communication (falls back to raw telnet)

### Windows App v0.2.47

- **Sonos Speaker Support** - Full Sonos integration using SoCo library for direct device communication
- **Arylic/Linkplay HTTP API** - Reliable streaming via native HTTP API (more stable than AirPlay)
- **Configuration Preservation** - Launcher settings now persist correctly when core saves changes
- **Sparse Playback Timing** - Fixed occasional sounds playing immediately at theme start
- **Track Level Fixes** - Tracks start at correct volume when switching themes
- **Audio Encoding** - Fixed PyAV frame format for proper stereo output

---

## Features

### Multi-Zone Audio
- **Multiple Channels**: Run up to 6 independent audio channels simultaneously
- **Per-Channel Themes**: Each channel plays its own theme
- **Flexible Speaker Selection**: Target individual speakers, entire rooms, floors, or custom speaker groups
- **Live Speaker Management**: Add or remove speakers from active channels without interrupting playback

### Theme System
- **Theme-Based Organization**: Audio files organized into theme folders (Thunder, Forest, Ocean, etc.)
- **Automatic Mixing**: All recordings in a theme blend together seamlessly
- **Theme Favorites**: Star your most-used themes for quick access
- **Custom Categories**: Organize themes into categories like "Weather", "Nature", "Urban"
- **Theme Icons**: Visual icons for easy theme identification
- **Bundled Themes**: Includes Sleigh Ride, Tavern, and "A Rainy Day... Or is it?" out of the box

### Track Mixer
Fine-tune how each audio file plays within a theme:

- **Presence Control** - Set how often each track appears in the mix (0-100%)
- **Per-Track Volume** - Adjust amplitude independent of presence
- **Playback Modes**:
  - **Auto** - Automatically selects best mode based on file length
  - **Continuous** - Loop with seamless crossfade
  - **Sparse** - Play once, wait before repeating (for short sounds)
  - **Presence** - Fade in/out based on presence setting

### Presets
- **Save/Load Presets** - Store track settings as named presets
- **Quick Switching** - Select presets directly on channel cards
- **Import/Export** - Share presets with the community

### Modern Web Interface
- **Responsive Design**: Works on desktop and mobile
- **Dark Theme**: Easy on the eyes
- **Real-Time Status**: See what's playing across all channels
- **Drag & Drop**: Upload audio files directly through the UI

## Why Ambient Sound?

Ambient soundscapes aren't just background noise—they're a powerful tool for mental wellness and productivity:

- **ADHD & Focus**: Background noise can improve concentration by providing consistent auditory input
- **Misophonia**: Ambient masking helps cover trigger sounds
- **Anxiety & Stress**: Nature sounds activate the parasympathetic nervous system
- **Sleep**: Consistent ambient sound masks disruptive noises
- **Work & Study**: Moderate ambient noise can boost creative thinking

## Quick Start

### Standalone App

1. **Download** `Sonorium.exe` from the [Releases page](https://github.com/synssins/sonorium/releases)
2. **Run** the executable (click "More info" → "Run anyway" if Windows SmartScreen appears)
3. **Create** a session, select a theme and speakers
4. **Play** and enjoy your ambient soundscape

### Home Assistant Addon

1. **Install** the addon using the button above
2. **Open Sonorium** from your Home Assistant sidebar
3. **Add Themes**: Create themes and upload audio via the web interface
4. **Create a Channel**: Select a theme and speakers
5. **Play**: Hit the play button

## Documentation

Full documentation is available in the **[Wiki](https://github.com/synssins/sonorium/wiki)**:

- [Getting Started](https://github.com/synssins/sonorium/wiki/Getting-Started) - Home Assistant installation
- [Standalone App](https://github.com/synssins/sonorium/wiki/Standalone-App) - Windows app guide with architecture details
- [Themes](https://github.com/synssins/sonorium/wiki/Themes) - Creating and organizing themes
- [Track Settings](https://github.com/synssins/sonorium/wiki/Track-Settings) - Playback modes explained
- [Presets](https://github.com/synssins/sonorium/wiki/Presets) - Saving track configurations
- [Speakers](https://github.com/synssins/sonorium/wiki/Speakers) - Speaker setup and management
- [API Reference](https://github.com/synssins/sonorium/wiki/API-Reference) - REST API for automation
- [Troubleshooting](https://github.com/synssins/sonorium/wiki/Troubleshooting) - Common issues

## Supported Formats

Audio files: `.mp3`, `.wav`, `.flac`, `.ogg`

Single-file themes loop seamlessly using crossfade blending—no jarring restarts!

## Supported Speakers

### Standalone App
- Local audio output (default speakers)
- DLNA/UPnP network speakers
- Sonos speakers (via SoCo library)
- Arylic/Linkplay speakers (via HTTP API)
- **HEOS speakers (Denon/Marantz)** - Beta, via CLI protocol
- *Coming soon: AirPlay (other devices), Chromecast*

### Home Assistant Addon
- Any media_player entity in Home Assistant
- **Google Cast** (Chromecast, Nest Hub, Google Home) - with HA API fallback for cross-VLAN setups
- **Sonos** - Native streaming via SoCo library
- Organized by floors, areas, and custom groups

## API Reference

Sonorium provides a REST API for integration:

### Streams
- `GET /stream/{theme_id}` - Direct audio stream for a theme
- `GET /stream/channel{n}` - Audio stream for channel N

### Sessions/Channels
- `GET /api/sessions` - List all sessions
- `POST /api/sessions` - Create a new session
- `POST /api/sessions/{id}/play` - Start playback
- `POST /api/sessions/{id}/stop` - Stop playback
- `POST /api/sessions/{id}/volume` - Set volume

### Themes
- `GET /api/themes` - List all themes
- `POST /api/themes/create` - Create a new theme
- `POST /api/themes/{id}/upload` - Upload audio file

## Acknowledgements

Sonorium is a fork of [Amniotic](https://github.com/fmtr/amniotic) by [fmtr](https://github.com/fmtr). The original project laid the groundwork with its innovative approach to ambient soundscape mixing.

## License

See LICENSE file for details.

## Contributing

Contributions are welcome! Please open an issue to discuss changes before submitting a PR.
