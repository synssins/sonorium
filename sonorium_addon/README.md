# Sonorium

![Sonorium](https://raw.githubusercontent.com/synssins/sonorium/main/logo.png)

**Multi-Zone Ambient Soundscape Mixer for Home Assistant**

[![Add Repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsynssins%2Fsonorium)

Sonorium lets you create immersive ambient audio environments throughout your home. Stream richly layered soundscapes—from distant thunder and rainfall to forest ambiance and ocean waves—to any combination of media players in your Home Assistant setup.

## What's New in v1.2.3

### Mobile UI Improvements (v1.2.3)
- **Responsive Track Mixer** - Track controls now stack vertically on mobile for easy editing
- **Mobile Menu Overlay** - Tap outside the sidebar to close it on mobile devices
- **Improved Tooltips** - All track controls now have clear, layman-friendly explanations
- **"Gapless" Rename** - "Seamless" renamed to "Gapless" for clarity

### Track Mixer with Advanced Controls (v1.2)
Fine-tune how each audio file plays within a theme:

- **Presence Control** - Set how often each track appears in the mix (0-100%). Low presence tracks fade in and out naturally rather than playing constantly.
- **Per-Track Volume** - Adjust the amplitude of individual tracks independent of presence.
- **Playback Modes** - Choose how each track behaves:
  - **Auto** - Automatically selects the best mode based on file length
  - **Continuous** - Loop continuously with seamless crossfade
  - **Sparse** - Play once at full volume, then wait before repeating (great for short sounds like bird calls or thunder claps)
  - **Presence** - Fade in/out based on presence setting

### Broadcast Audio Model
Rewrote channel streaming to use a radio-station model. All speakers tuned to the same channel hear the exact same audio stream, joining mid-stream rather than starting their own independent playback.

### Live Speaker Management
Add or remove speakers from an active channel without stopping playback. Changes take effect immediately.

### Short File Handling
Audio files under 15 seconds (configurable per theme) automatically use sparse playback when presence is below 100%. This prevents short sounds from looping annoyingly—a horse whinny or door creak plays once, then waits 30-300 seconds before playing again based on the presence setting.

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

## Audio Setup

Create theme folders in `/media/sonorium/` with your audio files:

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
├── Ocean/
│   ├── waves_gentle.mp3
│   └── seagulls.mp3
└── Rain/
    └── steady_rain.mp3  (single files loop seamlessly!)
```

**Supported formats:** `.mp3`, `.wav`, `.flac`, `.ogg`

**Single-File Themes:** Themes with one audio file loop seamlessly using crossfade blending—no jarring restarts!

## Quick Start

1. **Install** the addon and start it
2. **Add Audio** to `/media/sonorium/` (create theme folders with audio files)
3. **Open Sonorium** from your Home Assistant sidebar
4. **Create a Channel**: Click "New Channel", select a theme and speakers
5. **Play**: Hit the play button and enjoy your ambient soundscape

## How It Works

### Channels
A channel is an independent audio stream. Each channel:
- Plays one theme at a time
- Streams to one or more speakers
- Has its own volume control
- Can be started/stopped independently

Any media player that supports HTTP audio streams can tune into an active channel's stream URL.

### Audio Mixing
When you play a theme:
1. All audio files in that theme folder are loaded
2. Sonorium mixes all tracks together in real-time using sqrt(n) normalization
3. The mix streams to your selected speakers
4. Tracks loop with 1.5-second equal-power crossfades for seamless playback

## Configuration

### Addon Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `sonorium__stream_url` | `http://homeassistant.local:8008` | Base URL for streams |
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
