# Sonorium Roadmap

This document outlines the planned features and development direction for Sonorium. Features are organized by priority and complexity.

## Current State (v1.1.17-dev)

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

---

## Recently Completed

### Track Mixer with Advanced Controls (v1.1.17-dev)
- Per-track volume control independent of presence
- Per-track playback mode selection (Auto, Continuous, Sparse, Presence)
- Advanced settings panel with gear toggle
- Configurable short file threshold per theme

### Broadcast Audio Model (v1.1.5-dev)
Radio-station style streaming where all speakers tuned to the same channel hear identical audio. New speakers join at the current playback position rather than starting independent streams.

### Track Presence Control (v1.1.4-dev)
Per-track "presence" setting (0-100%) that controls how often a track appears in the mix. Low presence tracks fade in and out naturally using equal-power crossfades.

---

## Near-Term Goals

### Plugin System

**Goal:** Extensible architecture allowing third-party plugins to add functionality.

**Features:**
- Plugin discovery and loading mechanism
- Plugin settings page in the UI
- API hooks for plugins to integrate with core functionality
- Sandboxed execution environment for security
- Plugin enable/disable controls

**Use Cases:**
- Custom audio source integrations
- Specialized theme management tools
- Third-party service integrations
- Community-contributed functionality extensions

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

### Per-Track Crossfade Control

**Goal:** Allow disabling crossfade on individual tracks for seamless loops.

**Features:**
- Per-track "Seamless Loop" checkbox in advanced settings
- When enabled, track loops without crossfade blending
- Works with both Sparse and Continuous playback modes
- Ideal for professionally edited loop files
- State persisted with other track settings

**Use Cases:**
- Tracks that are already perfectly looped at edit points
- Avoiding crossfade artifacts on certain audio files
- Mixing seamless and crossfaded tracks in the same theme

### Home Assistant Entity Integration

**Goal:** Expose each configured channel as a controllable Home Assistant entity.

**Features:**
- Each channel becomes a `media_player` entity in Home Assistant
- Control channels directly from HA dashboards, automations, and scripts
- Entity attributes include:
  - Current theme
  - Volume level
  - Playback state (playing/stopped/paused)
  - Target speakers
- Services for:
  - `sonorium.play` / `sonorium.stop`
  - `sonorium.set_theme`
  - `sonorium.set_volume`
  - `sonorium.set_speakers`

**Use Cases:**
- Add Sonorium controls to any Lovelace dashboard
- Create automations like "Play rain sounds when it's bedtime"
- Voice control via Google Home / Alexa: "Hey Google, play forest sounds in the bedroom"
- Include in scenes: "Movie Night" scene stops all ambient audio

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

### Live Speaker Management

**Goal:** Modify speakers on an active channel without disrupting playback.

**Status:** Partially implemented via Broadcast Audio Model (v1.1.5-dev). New speakers join at current playback position.

**Features:**
- ✅ Add speakers to a playing channel—new speakers immediately pick up the stream
- ✅ All speakers hear identical audio (broadcast model)
- Remove speakers from a playing channel—remaining speakers continue uninterrupted
- Real-time speaker status showing which speakers are actively receiving the stream
- Graceful handling of speaker disconnections

**Use Cases:**
- Extend ambient sound to another room without restarting
- Remove a speaker when someone enters that room
- Dynamically adjust coverage based on presence detection

---

## Medium-Term Goals

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

### Per-Track Volume Control

**Goal:** Fine-tune the mix by adjusting individual track volumes within a theme.

**Status:** ✅ Completed in v1.1.17-dev

**Features:**
- ✅ Presence slider for each track (controls frequency in mix)
- ✅ Enable/disable individual tracks
- ✅ Volume slider for each track in a theme
- ✅ Playback mode selection per track
- ✅ Save custom mix settings per theme
- ✅ Reset to default mix option

**Use Cases:**
- Reduce bird sounds while keeping rain prominent
- Create custom variations of existing themes
- Fine-tune problematic frequency ranges

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

### Quality of Life
- Theme import/export with track presets (includes per-track volume, presence, playback mode, seamless loop settings)
- Backup and restore functionality
- Usage statistics and analytics
- Multi-language support

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
