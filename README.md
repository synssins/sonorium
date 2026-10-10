10/6/2026 - This is still alive. Just getting back into things.

# Sonorium

![Sonorium](logo.png)

**Multi-Zone Ambient Soundscape Mixer**

Sonorium lets you create immersive ambient audio environments. Stream richly layered soundscapes—from distant thunder and rainfall to forest ambiance and ocean waves—to speakers throughout your home or directly through your computer.

## Ways to Use Sonorium

### Standalone Windows App

Download and run without any dependencies. Perfect for desktop ambient sound.

**[Download Latest Release](https://github.com/synssins/sonorium/releases)** | **[Installation Guide](https://github.com/synssins/sonorium/wiki/Standalone-App)**

- Single portable executable—no installation required
- Local audio playback through your default speakers
- Stream to DLNA network speakers
- Automatic updates built-in

### Home Assistant Addon

Integrate with your smart home for whole-house audio.

[![Add Repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsynssins%2Fsonorium)

- One-click install from addon store
- Use any Home Assistant media_player
- Organize speakers by room, floor, or area
- Control from the HA dashboard

### Docker

Runs on any Docker host, with or without Home Assistant.

- Finds Google Cast, Sonos, DLNA, AirPlay, LinkPlay and HEOS speakers on your network
- Home Assistant and MQTT are optional, set up from the web UI
- Image: `ghcr.io/synssins/sonorium:latest`

## Automated Tests

The Tests workflow runs on pull requests and pushes to `main`, and can also be
started manually from GitHub Actions. It runs all automatically collected tests
on Python 3.11 and uploads a JUnit test report, including when tests fail.

Run the same suite locally:

```sh
python -m pip install 'pytest>=8,<9' 'numpy<2.4' av
python -m pytest tests -v
```

The AirConnect, AirPlay streaming, Arylic HTTP, and RTSP diagnostic scripts need
LAN speakers and are excluded from automated collection. The live Linkplay
streaming test is reported as skipped; its offline detection test runs in CI.
Run hardware diagnostics directly on a machine with access to the speakers,
for example `python tests/test_linkplay_integration.py --ip <speaker-ip>`.

Add-on version numbers are set automatically from commit messages; see
[docs/VERSIONING.md](docs/VERSIONING.md).

## Screenshots

### Channels
Each channel plays its own theme on its own speakers.

![Channels](screenshots/Channels.png)

### Edit Channel
Search themes, then pick speakers by floor, room or one at a time.

![Edit Channel](screenshots/Channel_Editor.png)

### Themes
Your theme library, with favorites and categories.

![Themes](screenshots/Themes.png)

### Settings → Speakers
Switch speakers on or off, set their room and volume offset, and play a test sound.

![Speakers](screenshots/Settings_Speakers.png)

### Add Speaker (Docker)
Add a speaker the network scan didn't find, by its address.

![Add Speaker](screenshots/Add_Speaker.png)

### Settings → Connection (Docker)
Connect Home Assistant and MQTT, both optional.

![Connection](screenshots/Settings_Connection.png)

### Settings → Logs
Recent messages with filters, search, copy and download.

![Logs](screenshots/Settings_Logs.png)

---

## What's New

### v1.4.0

- **Docker.** A new image, `ghcr.io/synssins/sonorium`, runs Sonorium on any Docker host, with or without Home Assistant. It finds network speakers on its own, and Home Assistant and MQTT are set up from the web UI (#32).
- **New channel editor.** Search themes, and pick speakers by floor, room or one at a time; ticking a floor or room selects everything in it.
- **Settings → Speakers.** Rename speakers, set their room and a volume offset, and play a short test sound. Only speakers switched on here appear in channels, and **Hide offline** tidies the list.
- **Settings → Logs.** See, search, copy and download recent log messages from the web UI. If Sonorium fails to start, the web UI shows the logs instead.
- **Speaker screens.** Nest Hub and other Google Cast displays show the Sonorium logo while playing.
- **Presets follow the theme.** Changing a channel's theme switches to that theme's default preset.
- **Smaller fixes.** Denon/Marantz receivers are recognised, settings pages stay readable on wide screens, and the browser tab shows Sonorium's icon.

Full history: [sonorium_addon/CHANGELOG.md](sonorium_addon/CHANGELOG.md).

---

## Features

### Multi-Zone Audio
- **Multiple Channels**: Run up to 10 independent audio channels simultaneously (configurable)
- **Per-Channel Themes**: Each channel plays its own theme
- **Flexible Speaker Selection**: Target individual speakers, entire rooms, floors, or custom speaker groups
- **Live Speaker Management**: Add or remove speakers from active channels without interrupting playback

### Speakers
- **Speaker Settings**: Rename speakers, set their room, and add a volume offset for speakers that play louder or quieter than the rest
- **Test Sound**: Play a short, quiet chime to check a speaker
- **Choose What Sonorium Uses**: Speakers switched off in Settings never appear in channels
- **Network Speakers (Docker)**: Finds Google Cast, Sonos, DLNA, AirPlay, LinkPlay and HEOS speakers on your network, or add one by address
- **One Speaker, Two Paths (Docker)**: A speaker found both in Home Assistant and on the network is shown once, and you choose which way it plays

### Theme System
- **Theme-Based Organization**: Audio files organized into theme folders (Thunder, Forest, Ocean, etc.)
- **Automatic Mixing**: All recordings in a theme blend together seamlessly
- **Theme Favorites**: Star your most-used themes for quick access
- **Custom Categories**: Organize themes into categories like "Weather", "Nature", "Urban"
- **Theme Icons**: Visual icons for easy theme identification
- **Bundled Themes**: Includes Sleigh Ride, Tavern, and "A Rainy Day... Or is it?" out of the box

### Track Mixer
Fine-tune how each audio file plays within a theme:

- **How often** - Set how often each track is heard in the mix (0-100%)
- **Per-Track Volume** - Adjust amplitude independent of presence
- **Playback Modes**:
  - **Auto** - Picks for you: sounds under 15 seconds play Intermittent and longer ones play Ebb & Flow; at 100% "how often", either plays as Background
  - **Background** - Plays all the time, looping with a smooth crossfade (rain, wind, a crackling fire)
  - **Intermittent** - Plays once, then goes quiet for a while before playing again; "how often" sets the wait (bird calls, thunder claps, a door creaking)
  - **Ebb & Flow** - Fades in, plays for a while, fades out, then stays quiet before coming back; "how often" sets how much of the time it's heard (distant traffic, a passing crowd)
- **Groups** - A group is a folder inside the theme whose tracks take turns: only one plays at a time, never overlapping, with a random gap (in minutes) between them. On its turn, an Intermittent track plays once and an Ebb & Flow track fades in, plays a while and fades out. Deleting a group keeps its files.

### Presets
- **Save/Load Presets** - Store track settings as named presets
- **Quick Switching** - Select presets directly on channel cards
- **Import/Export** - Share presets with the community
- **Follows the Theme** - Changing a channel's theme switches to that theme's default preset

### Home Assistant Dashboard Integration (Addon)
- **MQTT Entities** - Full dashboard control via MQTT (session select, theme/preset dropdowns, play/stop, volume)
- **Human-Readable Names** - Theme and preset dropdowns show names instead of UUIDs
- **Status Sensors** - See playback status and assigned speakers from your dashboard
- **Automation Support** - Use HA automations to trigger soundscapes (morning alarms, schedules, etc.)

### Modern Web Interface
- **Responsive Design**: Works on desktop and mobile
- **Dark Theme**: Easy on the eyes
- **Real-Time Status**: See what's playing across all channels
- **Drag & Drop**: Upload audio files directly through the UI
- **Logs Page**: Recent messages with filters, search and download, even when startup fails
- **Speaker Screens**: Google Cast displays show the Sonorium logo while playing

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

Sonorium needs the **Mosquitto broker** add-on (Settings → Add-ons → Add-on Store), started with **Start on boot** on.

1. **Install** the addon using the button above
2. **Open Sonorium** from your Home Assistant sidebar
3. **Add Themes**: Create themes and upload audio via the web interface
4. **Create a Channel**: Select a theme and speakers
5. **Play**: Hit the play button

### Docker

```yaml
services:
  sonorium:
    image: ghcr.io/synssins/sonorium:latest
    container_name: sonorium
    network_mode: host        # needed to find speakers on your network
    environment:
      - PUID=1000             # optional: owner of the files Sonorium writes
      - PGID=1000
      - TZ=Etc/UTC            # your time zone, for log times
    volumes:
      - ./sonorium/config:/config          # settings and channels
      - ./sonorium/media:/media/sonorium   # themes and audio
    restart: unless-stopped
```

1. **Start** it with `docker compose up -d`
2. **Open** `http://<docker-host>:8008`
3. **Optional:** connect Home Assistant and MQTT under **Settings → Connection**
4. **Create a Channel** and press play

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

### Docker
- Google Cast (Chromecast, Nest Hub, Google Home)
- Sonos
- DLNA/UPnP speakers
- AirPlay
- Arylic/LinkPlay
- HEOS (Denon/Marantz)
- Plus every Home Assistant speaker, if you connect Home Assistant

### Home Assistant Addon
- Any media_player entity in Home Assistant
- **Google Cast** (Chromecast, Nest Hub, Google Home), played through Home Assistant's Cast integration
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
