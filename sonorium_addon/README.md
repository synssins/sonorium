# Sonorium

![Sonorium](https://raw.githubusercontent.com/synssins/sonorium/main/logo.png)

**Multi-Zone Ambient Soundscape Mixer for Home Assistant**

[![Add Repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsynssins%2Fsonorium)

Sonorium lets you create immersive ambient audio environments throughout your home. Stream richly layered soundscapes—from distant thunder and rainfall to forest ambiance and ocean waves—to any combination of media players in your Home Assistant setup.

## What's New in v1.3.0

- **Google Cast fixed.** Cast speakers (Chromecast, Nest Hub, Google Home) that stayed silent now play, and playback starts about 5 seconds faster. Thanks to @sh00t2kill (#43).
- **No more gaps when tracks loop.** The loop crossfade was cut short and the track restarted from the beginning on every loop (#38).
- **Stopped speakers free their channel.** If a channel's speakers stop taking audio for 90 seconds (stopped from the speaker or from Home Assistant), Sonorium stops the channel. **Stop All** also stops paused channels (#29).
- **MQTT entities stay in sync.**
  - Channel renames, deletes and theme changes update Home Assistant's entities (#16, #33).
  - Commands from Home Assistant keep working after the Mosquitto broker restarts.
- **More reliable startup.**
  - Sonorium waits for the Mosquitto broker to start instead of failing (#42).
  - It runs on virtual machines with a basic virtual CPU, such as Proxmox's default `kvm64` (#18, #39).
- **Uninstall removes settings.** Settings, channels and plugins now live in the add-on's own config folder, and existing settings are copied there automatically on first start (#30). Themes stay in `/media/sonorium`.
- **Quieter, safer logs.**
  - A new `log_level` option: `info` shows a short summary; `debug` adds detail and library versions.
  - Debug logs no longer contain the MQTT password.
- **Name and plugins.** The add-on is now just "Sonorium" (#40), and the Plugin Browser works again.
- **Mobile.** Your browser and the HA app always load the current version of the page after an update (#28).

Full history: [CHANGELOG.md](CHANGELOG.md).

## MQTT Discovery Troubleshooting

Channels keep the MQTT identity they were created with, so renaming a channel
changes its display name but not its entity IDs. Your automations keep working.

Versions before 1.3.0 could leave behind entities for channels that were
renamed or deleted. To remove one, find its discovery topic
(`homeassistant/<component>/<object_id>/config`, shown in the entity's MQTT
info), then go to Settings → Devices & services → MQTT → **Configure**, enter
that topic under **Publish a packet**, leave the payload empty, tick **Retain**
and publish. Only remove leftover topics, not current channels. Back up any
automations that use the entity first.

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
- **Multiple Channels**: Run up to 10 independent audio channels simultaneously (configurable)
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

### Home Assistant Dashboard Integration
- **MQTT Entities** - Full dashboard control via MQTT (session select, theme/preset dropdowns, play/stop, volume)
- **Human-Readable Names** - Theme and preset dropdowns show names instead of UUIDs
- **Status Sensors** - See playback status and assigned speakers from your dashboard
- **Automation Support** - Use HA automations to trigger soundscapes (morning alarms, schedules, etc.)

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

## Requirements

- **An MQTT broker.** Install the **Mosquitto broker** add-on (Settings → Add-ons → Add-on Store), start it, and turn on **Start on boot**. Sonorium finds it automatically. Without a broker, Sonorium won't start and the sidebar shows **502: Bad Gateway**. Using a different broker? Set `sonorium__mqtt_host` (and port, username, password) in Sonorium's configuration.
- **Home Assistant in a virtual machine (Proxmox etc.):** if Sonorium's log shows a numpy or CPU error at startup, set the VM's CPU type to **host** (Proxmox: VM → Hardware → Processors → Type), then fully shut down and start the VM. The default `kvm64` type hides CPU features some audio libraries need.

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
| `log_level` | `info` | `info` shows a startup summary and errors; `debug` adds per-theme and per-speaker detail and library versions |
| `sonorium__stream_url` | `auto` | Base URL for streams (auto-detects HA IP) |
| `sonorium__path_audio` | `/media/sonorium` | Path to theme folders |
| `sonorium__max_channels` | `6` | Maximum concurrent channels (1-10) |
| `sonorium__mqtt_host` | `auto` | MQTT broker. `auto` uses the Mosquitto broker add-on; set a host only for a different broker (plus port, username, password) |

### Where data is stored

- **Settings, channels and plugins:** the add-on's own config folder (`addon_configs/<id>_sonorium/sonorium` in your HA config share). Uninstalling with "delete data" removes it.
- **Themes and audio:** `/media/sonorium`. These are your files and are never deleted by an uninstall.

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
- Update to 1.3.0 or later: earlier versions sent an option that made idle Cast devices ignore playback
- Check that Home Assistant itself can control the device (change its volume from HA)
- Cast devices take about 10 seconds to start: they fill a buffer before playing
- In the add-on's **Log** tab, the lines from "Playing session" to "speakers started" show what happened

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

Contributions are welcome! Please open an issue to discuss changes before submitting a PR.
