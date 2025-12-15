# Sonorium Roadmap

This document outlines the planned features and development direction for Sonorium. Features are organized by priority and complexity.

## Current State (v1.1.28-dev)

Sonorium is a fully functional multi-zone ambient soundscape mixer for Home Assistant with:

- Multi-channel audio streaming (up to 6 concurrent channels)
- Theme-based audio organization with favorites and categories
- Flexible speaker selection (individual, area, floor, or custom groups)
- Modern web interface with dark theme
- Home Assistant sidebar integration via ingress
- REST API for automation
- **Broadcast audio model** - All speakers on a channel hear the same stream
- **Track mixer with advanced controls** - Per-track volume, presence, and playback mode settings
- **Playback modes** - Auto, Continuous, Sparse, and Presence modes for fine-tuned control
- **Plugin system** - Extensible architecture with built-in Ambient Mixer importer
- **Local browser preview** - Preview tracks and full themes directly in the browser

---

## Recently Completed

### Home Assistant Entity Integration (v1.1.28-dev)
- Custom integration exposes channels as `media_player` entities
- `media_player.sonorium_channel_1` through `media_player.sonorium_channel_6`
- Control channels from HA dashboards, automations, and scripts
- Select source (theme) from dropdown, play/stop controls
- Dashboard YAML examples included

### Plugin System (v1.1.28-dev)
- Extensible plugin architecture with hot-loading
- Built-in Ambient Mixer plugin for importing soundscapes from ambient-mixer.com
- Plugin enable/disable controls in Settings
- Plugin upload and management UI

### Local Browser Preview (v1.1.28-dev)
- Preview individual tracks in the track mixer
- Preview full theme mix directly in browser
- Volume control and play/pause for theme preview
- No speaker required - plays on the device viewing the UI

### Live Speaker Management (v1.1.28-dev)
- Add speakers to a playing channel - instantly syncs to current position
- Remove speakers from a playing channel - remaining speakers continue uninterrupted
- All speakers hear identical audio (broadcast model)

### Track Mixer with Advanced Controls (v1.1.17-dev)
- Per-track volume control independent of presence
- Per-track playback mode selection (Auto, Continuous, Sparse, Presence)
- Seamless loop option (disable crossfade) per track
- Advanced settings panel with gear toggle
- Configurable short file threshold per theme

### Broadcast Audio Model (v1.1.5-dev)
Radio-station style streaming where all speakers tuned to the same channel hear identical audio. New speakers join at the current playback position rather than starting independent streams.

### Track Presence Control (v1.1.4-dev)
Per-track "presence" setting (0-100%) that controls how often a track appears in the mix. Low presence tracks fade in and out naturally using equal-power crossfades.

---

## Near-Term Goals

### Theme Presets

**Goal:** Save and restore named configurations of theme settings.

**Features:**
- Named presets storing all adjustable settings (layers, volumes, mix, playback modes)
- Global to theme (not zone-specific)
- Operations: save current as new preset, select preset, delete preset
- Default preset flag (auto-loads when theme first selected)
- State persistence across reboots/upgrades
- UI: Preset dropdown in channel playback, beneath theme selector
- Separate field/modal for pasting preset JSON into existing themes
- Validate pasted JSON: fail gracefully with clear message if references non-existent layers/settings
- Copy/paste JSON friendly for community sharing (forums, Reddit)

**Use Cases:**
- "Sleep" preset with lower volumes and sparse presence
- "Focus" preset with continuous background layers
- Share preset configurations with the community

### Theme Export/Import

**Goal:** Package themes for sharing and backup.

**Features:**
- Zip archive format containing audio files + metadata JSON
- Self-contained, shareable packages
- Presets bundled in export, travel with theme
- Versioning in export metadata for future compatibility
- Graceful validation on import with clear error messages

**Use Cases:**
- Share custom themes with other Sonorium users
- Backup themes before system changes
- Migrate themes between instances

### Collapsible Navigation Menu

**Goal:** Reorganize the sidebar navigation with expandable/collapsible sections.

**Features:**
- Section-based navigation with expand/collapse toggles
- Settings section with sub-pages:
  - Audio Settings
  - Speakers
  - Speaker Groups
  - Plugins
- Persistent expand/collapse state
- Clean, organized menu structure

**Use Cases:**
- Better organization as features grow
- Quick access to frequently used sections
- Reduced visual clutter in the sidebar

### Advanced Track Settings UI Redesign

**Goal:** Improve the track mixer interface for better usability and information density.

**Features:**
- Two-row layout per track: Volume slider above Presence slider
- Labels positioned to the left of each slider
- Track name with word wrap to prevent excessive width
- Playback mode dropdown between track name and sliders
- Grid-aligned UI elements for visual consistency
- Seamless loop checkbox for perfectly looped tracks
- Fixed-width panel that doesn't stretch to fill the page

**Use Cases:**
- See full track names without truncation
- Quickly compare settings across tracks
- More intuitive control layout

### Automatic Theme Cycling

**Goal:** Automatically rotate through themes on a schedule.

**Features:**
- Per-channel cycling configuration
- Configurable interval (e.g., change theme every 30 minutes)
- Two modes:
  - **Sequential**: Cycle through themes in order
  - **Random**: Pick themes randomly
- Optional theme playlist: specify which themes to cycle through
- Smooth transitions between themes during cycling

**Use Cases:**
- Variety during long work sessions
- Different ambient sounds throughout the day
- Randomized nature sounds for meditation

### Auto-Refresh After Plugin Import

**Goal:** Automatically refresh the theme list when a plugin imports new content.

**Features:**
- Plugin API hook to trigger theme refresh
- UI updates immediately after import completes
- Toast notification of new themes added

**Use Cases:**
- Seamless workflow when importing from Ambient Mixer
- No manual refresh needed after plugin operations

---

## Medium-Term Goals

### Quality of Life Features

**UI/UX:**
- Keyboard shortcuts (space=play/pause, arrows=volume, number keys=switch channels)
- Channel quick-duplicate (copy current channel config to new channel)
- "Now playing" toast/notification when theme changes
- Waveform or activity visualization showing active tracks in mix
- Mobile-friendly bottom nav or swipe gestures
- Drag to reorder channels

**Audio Playback:**
- Fade-to-stop option (gradual fadeout vs abrupt stop)
- Sleep timer per channel (stop playback after X minutes)
- Schedule/automation hooks (play theme at sunset, stop at midnight)
- Audio ducking when HA announces (TTS integration) - HA addon only
- Crossfade between themes when switching (not just hard cut)

**Theme Management:**
- Bulk import from folder/zip
- Theme preview (30-second sample without committing to a channel)
- "Random theme" mode within a category
- Theme tagging beyond categories (mood tags: focus, sleep, energy, calm)
- Search/filter themes by name, category, tag
- Sort themes: alphabetical, recently used, favorites first

### Standalone Docker Deployment

**Goal:** Run Sonorium outside of Home Assistant as a standalone Docker container.

**Features:**
- Official Docker image on Docker Hub
- Docker Compose configuration
- Environment variable configuration
- Optional Home Assistant integration (for speaker discovery)
- Manual speaker configuration for non-HA setups
- Web-based speaker management

**Use Cases:**
- Users without Home Assistant
- Dedicated media servers
- Integration with other home automation platforms
- Development and testing environments

### Schedule-Based Automation

**Goal:** Built-in scheduling without requiring Home Assistant automations.

**Features:**
- Time-based playback schedules
- Day-of-week configuration
- Sunrise/sunset triggers (when integrated with HA)
- Volume scheduling (quieter at night)

**Use Cases:**
- Automatic morning bird sounds
- Reduced volume after bedtime
- Different themes for workdays vs. weekends

---

## Long-Term Vision

### Standalone Application

**Goal:** Native desktop/mobile application that doesn't require a server.

**Features:**
- Electron-based desktop app (Windows, Mac, Linux)
- Local audio playback (no streaming required)
- Cloud sync for themes and settings (optional)
- Mobile companion app for remote control
- System tray/menu bar presence (minimize to tray)
- Headless/daemon mode for server deployments
- Config file import/export (migration between instances)
- Built-in audio normalization on import (optional loudness leveling)
- Auto-start on boot option
- Web UI served locally (localhost:port) for configuration
- CLI controls for scripting (`sonorium play channel1 --theme Forest`)

**Cast Device Discovery:**
- **Google Cast (Chromecast)**: mDNS/DNS-SD discovery via `_googlecast._tcp.local`
- **AirPlay 2**: mDNS via `_airplay._tcp.local` and `_raop._tcp.local`
- **DLNA/UPnP**: SSDP discovery on `239.255.255.250:1900`
- **Snapcast**: JSON-RPC API over TCP (port 1705) for self-hosted multi-room
- Unified discovery service scanning all protocols
- Manual IP/port entry fallback for edge cases

### Raspberry Pi Deployment

**Mode 1: Streaming Server**
- Hosts Sonorium backend, serves HTTP audio streams to cast devices
- Run as systemd service or Docker container
- Pi 4 recommended for multiple concurrent channels
- Pi Zero 2 W minimum (single channel, light load)
- Wired ethernet preferred for multi-zone

**Mode 2: Dedicated Audio Output**
- Pi acts as endpoint with direct audio to amplifier
- DAC/Amp HAT options: HiFiBerry Amp2, IQaudio DigiAMP+, JustBoom Amp HAT
- I2S connection for high-quality audio (bypasses onboard audio)
- Can run headless as dedicated room player

**Hybrid Setup:**
- Pi 4 runs Sonorium server
- Multiple Pi Zeros with amp HATs as room endpoints
- Snapcast for synchronized multi-room from single source
- Or each Pi Zero tunes into Sonorium HTTP stream independently

### Multi-Room Synchronization

**Goal:** Synchronized playback across multiple speakers with precise timing.

**Features:**
- Sub-millisecond sync across speakers
- Support for speaker groups with sync compensation
- Seamless audio as you move between rooms

### Audio Visualization

**Goal:** Visual feedback of the ambient soundscape.

**Features:**
- Real-time waveform display
- Frequency spectrum analyzer
- Per-track activity indicators
- Optional ambient visualizations for displays

### Community Theme Library

**Goal:** Share and discover themes from other Sonorium users.

**Features:**
- Browse community-contributed themes
- One-click theme installation
- Theme ratings and reviews
- Attribution and licensing support

### AI-Powered Features

**Goal:** Intelligent ambient sound generation and management.

**Features:**
- AI-generated ambient sounds based on descriptions
- Automatic theme categorization
- Smart recommendations based on time, weather, or mood
- Adaptive mixing based on room acoustics

---

## Technical Improvements

### Performance
- Reduced memory footprint for large theme libraries
- Improved streaming efficiency
- Better handling of network interruptions

### Developer Experience
- Plugin architecture for custom features (see Near-Term Goals)
- Plugin development documentation and examples
- Comprehensive API documentation
- WebSocket support for real-time updates
- SDK for building integrations

### Documentation
- **GitHub Wiki** - Comprehensive documentation including:
  - Fresh installation guide
  - Initial setup and configuration
  - Theme creation and organization
  - Speaker setup and grouping
  - Track mixer settings (volume, presence, playback modes)
  - Known limitations (e.g., sparse mode volume changes apply on next play cycle)
  - Troubleshooting guide
  - API reference

### Data & Sync
- Backup/restore all settings, themes, presets
- Optional cloud sync for presets across instances (future consideration)
- Usage stats: most played themes, average session length (privacy-respecting, local only)

---

## Contributing

We welcome contributions! If you're interested in working on any of these features:

1. Check the [Issues](https://github.com/synssins/sonorium/issues) for existing discussions
2. Open a new issue to discuss your approach
3. Submit a pull request with your implementation

For major features, please open an issue first to discuss the design and ensure it aligns with the project direction.

---

## Feedback

Have ideas for features not listed here? We'd love to hear from you!

- Open an [Issue](https://github.com/synssins/sonorium/issues) on GitHub
- Tag it with `enhancement` or `feature-request`
- Describe your use case and how the feature would help

Your feedback directly shapes Sonorium's development priorities.
