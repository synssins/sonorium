# Sonorium

![Sonorium](https://raw.githubusercontent.com/synssins/sonorium/main/logo.png)

**Multi-Zone Ambient Soundscape Mixer for Home Assistant**

[![Add Repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsynssins%2Fsonorium.dev)

Sonorium lets you create immersive ambient audio environments throughout your home. Stream richly layered soundscapes—from distant thunder and rainfall to forest ambiance and ocean waves—to any combination of media players in your Home Assistant setup.

## What's New in v1.2.66

### Google Cast Streaming Fixed

Sonorium now reliably streams to Google Cast devices (Chromecast, Nest Hub, Google Home) even in complex network setups:

- **HA API Fallback** - When Cast device IP cannot be discovered (e.g., device on a different VLAN where mDNS doesn't work), Sonorium automatically falls back to Home Assistant's `media_player.play_media` service. HA's native Cast integration already knows how to reach the device.
- **mDNS Discovery** - Added zeroconf/mDNS network discovery as an additional IP resolution method for Cast devices on the same network segment.
- **Improved Device Detection** - Broader recognition of Cast device types including Nest Hub displays, Chromecast variants, and Google Home speakers.

### Sonos WebSocket Fix

- **Large Installation Support** - Fixed "message too big" WebSocket error that occurred when querying the device registry in Home Assistant installations with many devices (9000+). Increased message limit from 1MB to 10MB.

### Settings → Speakers UI Restored

- **Floor/Room Hierarchy** - Fixed a regression where the Settings → Speakers page displayed a spinning circle instead of the proper floor/area/speaker tree view. The issue was caused by an erroneous merge of standalone app code into the HA addon codebase, which overwrote HA-specific speaker management functions with incompatible standalone implementations.

### Sparse Playback Timing

- **Exclusive Track Spacing** - Increased the minimum gap between exclusive tracks from 30 seconds to 2 minutes. This prevents multiple exclusive tracks (like different lute songs in a tavern theme) from playing back-to-back when their randomized initial delays happen to align.

---

## What's New in v1.2.40

### Home Assistant Dashboard Integration
Sonorium now publishes MQTT entities for seamless Home Assistant dashboard control:

- **Session Select** - Switch between channels from your dashboard
- **Theme/Preset Dropdowns** - Select themes and presets by name (not UUID)
- **Play/Stop Controls** - Toggle playback per channel or globally
- **Volume Sliders** - Adjust volume from dashboard cards
- **Status Sensors** - See playback status and assigned speakers

### Reliable MQTT Entity Discovery
Fixed timing issues where entities would show "unavailable" after addon restart:
- Proper delays between discovery config and state publication
- Automatic cleanup of stale entities from previous versions
- Auto-selection of first session on startup for immediate control

### Morning Alarm Automations
Use Sonorium with HA automations to wake up to ambient sounds:
```yaml
automation:
  - alias: "Morning Wakeup"
    trigger:
      - platform: time
        at: "07:00:00"
    action:
      - service: select.select_option
        target:
          entity_id: select.sonorium_channel_1_theme
        data:
          option: "Primeval Forest"
      - service: switch.turn_on
        target:
          entity_id: switch.sonorium_channel_1_play
```

---

## Previous Versions

### v1.2.17

- **Direct Sonos Support with SoCo** - Native Sonos streaming using SoCo library with `force_radio=True` mode for uninterrupted HTTP audio
- **Automatic Stream URL Detection** - No manual IP configuration required; speakers connect reliably with default `auto` setting
- **Sparse Playback Timing Fix** - Occasional sounds now correctly honor their presence-based timing from the start
- **Track Levels & Exclusive Groups** - Fixed volume levels when switching themes; exclusive tracks coordinate properly

---

## Acknowledgements

Sonorium is a fork of [Amniotic](https://github.com/fmtr/amniotic) by [fmtr](https://github.com/fmtr). The original Amniotic project laid the groundwork for this addon with its innovative approach to ambient soundscape mixing in Home Assistant. We're grateful for the time, effort, and creativity that went into building the foundation that Sonorium is built upon.

## Why Ambient Sound?

Ambient soundscapes aren't just background noise—they're a powerful tool for mental wellness and productivity. Research shows that ambient sounds can help with:

- **ADHD & Focus**: White noise and nature sounds can improve concentration by providing consistent auditory input that helps filter out distracting sounds. Studies suggest that background noise may trigger [stochastic resonance](https://pmc.ncbi.nlm.nih.gov/articles/PMC6481398/), potentially enhancing cognitive performance in individuals with ADHD.

- **Misophonia**: For those triggered by specific sounds, [ambient masking](https://www.getinflow.io/post/sound-sensitivity-and-adhd-auditory-processing-misophonia) with nature sounds or white noise can help "cover" trigger sounds and reduce emotional responses.

- **Sensory Processing**: Individuals with [sensory processing differences](https://pubmed.ncbi.nlm.nih.gov/17436843/), including those on the autism spectrum, may benefit from controlled ambient environments that provide predictable, soothing auditory input.

- **Anxiety & Stress**: Nature sounds like rain, ocean waves, and forest ambiance have been shown to activate the parasympathetic nervous system, promoting relaxation and reducing stress hormones.

- **Sleep**: Consistent ambient sound can mask disruptive noises and create a sleep-conducive environment.

- **Work & Study**: The "coffee shop effect"—moderate ambient noise can boost creative thinking and sustained attention.

## Screenshots

### Channels View
Create and manage multiple audio channels, each streaming to different speakers.

![Channels](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Channels.png)

### Theme Selection
Choose from your library of ambient themes for each channel.

![Theme Selection](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Channels_Theme_Selection.png)

### Themes Library
Organize your audio files into themes with favorites and categories.

![Themes](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Themes.png)

### Settings
Configure speakers, volume defaults, and other preferences.

![Settings](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Settings.png)

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

### Playback Control
- **Per-Channel Volume**: Independent volume control for each channel
- **Master Gain**: Global output level control
- **Crossfade Looping**: Seamless loops with equal-power crossfades
- **Play/Pause/Stop**: Full transport controls per channel

### Track Mixer
Fine-tune how each audio file plays within a theme:

- **Presence Control** - Set how often each track appears in the mix (0-100%). Low presence tracks fade in and out naturally rather than playing constantly.
- **Per-Track Volume** - Adjust the amplitude of individual tracks independent of presence.
- **Playback Modes** - Choose how each track behaves:
  - **Auto** - Automatically selects the best mode based on file length
  - **Continuous** - Loop continuously with seamless crossfade
  - **Sparse** - Play once at full volume, then wait before repeating (great for short sounds like bird calls or thunder claps)
  - **Presence** - Fade in/out based on presence setting

### Modern Web Interface
- **Responsive Design**: Works on desktop and mobile
- **Dark Theme**: Easy on the eyes
- **Real-Time Status**: See what's playing across all channels
- **Drag & Drop**: Upload audio files directly through the UI
- **Speaker Browser**: Visual hierarchy of floors, areas, and speakers

### Home Assistant Integration
- **Sidebar Access**: Appears in your HA sidebar for quick access
- **Ingress Support**: Secure access through Home Assistant's authentication
- **Media Player Discovery**: Automatically finds all media_player entities
- **Area & Floor Awareness**: Speakers organized by Home Assistant areas and floors

## Supported Speakers

Sonorium can stream to any `media_player` entity in your Home Assistant setup:

- **Google Cast devices** (Chromecast, Nest Hub, Google Home) - with automatic fallback for cross-VLAN setups
- **Sonos speakers** - Native support via SoCo library for direct device communication
- **Amazon Echo** (via HA integration)
- **VLC media player**
- **Music Assistant players**
- Most smart speakers with Home Assistant integration

## Theme Management

All theme management is done through the Sonorium web interface:

1. **Create Themes**: Click the + button in the Themes section
2. **Upload Audio**: Drag and drop audio files or click to upload
3. **Organize**: Set categories, icons, and favorites

**Supported formats:** `.mp3`, `.wav`, `.flac`, `.ogg`

**Single-File Themes:** Themes with one audio file loop seamlessly using crossfade blending—no jarring restarts!

**Bundled Themes:** Sleigh Ride, Tavern, and "A Rainy Day... Or is it?" are included out of the box.

## Quick Start

1. **Install** the addon and start it
2. **Open Sonorium** from your Home Assistant sidebar
3. **Add Themes**: Create themes and upload audio via the web interface
4. **Create a Channel**: Click "New Channel", select a theme and speakers
5. **Play**: Hit the play button and enjoy your ambient soundscape

## Configuration

### Addon Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `sonorium__stream_url` | `auto` | Base URL for streams (auto-detects HA IP) |
| `sonorium__path_audio` | `/media/sonorium` | Path to theme folders |
| `sonorium__max_channels` | `6` | Maximum concurrent channels (1-10) |

### Web UI Settings

Access Settings from the sidebar to configure:
- **Crossfade Duration**: Blend time between loops (0-10 seconds)
- **Default Volume**: Initial volume for new channels
- **Master Gain**: Global output level
- **Speaker Availability**: Enable/disable specific speakers from Sonorium

## API Reference

Sonorium provides a REST API for integration and automation:

### Streams
- `GET /stream/{theme_id}` - Direct audio stream for a theme
- `GET /stream/channel{n}` - Audio stream for channel N

### Channels
- `GET /api/channels` - List all channels
- `POST /api/sessions` - Create a new channel/session
- `POST /api/sessions/{id}/play` - Start playback
- `POST /api/sessions/{id}/stop` - Stop playback
- `POST /api/sessions/{id}/volume` - Set volume

### Themes
- `GET /api/themes` - List all themes
- `POST /api/themes/create` - Create a new theme
- `POST /api/themes/{id}/upload` - Upload audio file

### Status
- `GET /api/status` - Current system status

## Troubleshooting

### No Sound
- Check that your media player supports HTTP audio streams
- Verify the stream URL is accessible from your speaker
- Check the channel volume and master gain aren't set to 0

### Cast Device Not Playing
- This is usually a network/VLAN issue—v1.2.66 adds HA API fallback to handle this automatically
- Check addon logs for "Using HA API fallback" message
- Verify HA can control the Cast device (test volume control)

### Speakers Not Showing
- Ensure speakers are media_player entities in Home Assistant
- Check that speakers aren't disabled in Sonorium settings
- Try refreshing speakers from the Settings page

### Theme Not Loading
- Verify audio files are in supported formats
- Check file permissions on `/media/sonorium/`
- Look for errors in the addon logs

## License

See LICENSE file for details.

## Contributing

Contributions are welcome! Please see the [ROADMAP](https://github.com/synssins/sonorium/blob/main/ROADMAP.md) for planned features and development direction.
