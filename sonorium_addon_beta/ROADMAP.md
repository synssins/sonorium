# Sonorium Roadmap & Feature Tracker

This document tracks planned features, known issues, and the development roadmap for Sonorium.

## Current Version
- **Stable (v1):** 1.3.2 - Single theme streaming, basic web UI
- **Beta (v2):** 2.0.0b5 - Multi-zone sessions, channel-based streaming, theme cycling

---

## v2.0 Architecture (In Progress)

### Completed ✅
- [x] Session management system (create, update, delete sessions)
- [x] Speaker groups with floor/area/speaker hierarchy
- [x] Home Assistant registry integration (floors, areas, speakers)
- [x] Multi-speaker playback (send stream to multiple media_players)
- [x] Fire-and-forget play pattern (instant UI response)
- [x] State persistence (sessions/groups survive restarts)
- [x] Channel-based streaming architecture
- [x] Seamless theme crossfading within channels
- [x] Remove track enable/disable feature (all tracks always active)
- [x] Auto-naming sessions based on speaker selection
- [x] Fix channel concurrency (each client gets independent stream)
- [x] Theme cycling with configurable interval (1 min to 24 hours)
- [x] Theme randomization option for cycling
- [x] Skip to next theme in cycle
- [x] Per-session cycle configuration
- [x] CycleManager background task for automatic transitions

### In Progress 🔄
- [ ] UI redesign with left navigation menu
- [ ] Theme cycling UI controls

### Planned 📋

#### Core Features
- [ ] Speaker group management UI
- [ ] Volume per-speaker overrides within a session
- [ ] Pause/resume functionality (currently only play/stop)
- [ ] Session duplication (clone existing session)

#### Theme Cycling & Scheduling
- [x] Auto-cycle themes on timer (1 min to 24 hours)
- [x] Randomize theme order during cycling
- [ ] Schedule-based playback (e.g., "play rain forest 6am-8am")
- [ ] Crossfade duration per-session setting
- [ ] Include/exclude specific themes from rotation

#### Home Assistant Integration
- [ ] Expose sessions as media_player entities
- [ ] Create HA automations from Sonorium
- [ ] Scene integration (link sessions to HA scenes)
- [ ] Presence-based auto-play/stop

#### Audio & Streaming
- [ ] Per-track volume adjustment
- [ ] Custom audio upload via web UI
- [ ] Audio visualization in UI
- [ ] Stereo/spatial audio support
- [ ] Icecast/Shoutcast protocol support

#### UI/UX Improvements
- [ ] Left navigation menu (see UI Design below)
- [ ] Dark/light theme toggle
- [ ] Mobile-optimized responsive design
- [ ] Drag-and-drop speaker assignment
- [ ] Quick play buttons for favorite themes
- [ ] Session templates/presets

---

## UI Design Specification

### Configuration Philosophy

**Hardware Settings (HA Addon Config Only)**
These settings affect system resources and should only be configured through the Home Assistant addon configuration panel:
- `max_channels` - Maximum concurrent streaming channels
- `path_audio` - Audio file storage location
- `stream_url` - Base URL for streaming

**User Settings (Web UI)**
All user-facing features should be configurable through the Sonorium web interface.

### Left Navigation Menu Structure

```
┌─────────────────────────────────────────────────────┐
│  🎵 SONORIUM                              [≡]      │
├─────────────────────────────────────────────────────┤
│                                                     │
│  📻 Sessions                    ← Main dashboard    │
│     • Now Playing                                   │
│     • All Sessions                                  │
│     • Create New                                    │
│                                                     │
│  🔊 Speakers                                        │
│     • All Speakers                                  │
│     • Speaker Groups            ← Group management  │
│     • Refresh from HA                               │
│                                                     │
│  🎨 Themes                                          │
│     • Browse Themes                                 │
│     • Theme Cycling             ← Cycling settings  │
│                                                     │
│  ⚙️ Settings                                        │
│     • Playback Defaults         ← Default volume,   │
│     • Crossfade Duration           crossfade time   │
│     • UI Preferences            ← Dark/light mode   │
│                                                     │
│  📊 Status                                          │
│     • Active Channels                               │
│     • System Info                                   │
│                                                     │
├─────────────────────────────────────────────────────┤
│  v2.0.0b5                       [?] Help           │
└─────────────────────────────────────────────────────┘
```

### Menu Sections Detail

#### 📻 Sessions
- **Now Playing**: Quick view of active sessions with play/pause/stop controls
- **All Sessions**: Full list with edit/delete capabilities
- **Create New**: Wizard for new session (theme → speakers → settings)

#### 🔊 Speakers
- **All Speakers**: Flat list of all discovered speakers with status
- **Speaker Groups**: Create, edit, delete speaker groups
  - Group name
  - Include by: Floor, Area, or Individual speakers
  - Exclude specific speakers
  - Preview which speakers are selected
- **Refresh from HA**: Manual refresh of speaker discovery

#### 🎨 Themes
- **Browse Themes**: Grid/list view of available themes with track counts
- **Theme Cycling**: Configure auto-rotation
  - Enable/disable cycling per session
  - Cycle interval (10m, 30m, 1h, 2h, custom)
  - Randomize order toggle
  - Include/exclude themes from rotation

#### ⚙️ Settings
- **Playback Defaults**: 
  - Default volume for new sessions
  - Default theme (optional)
- **Crossfade Duration**: Global default (3s), per-session override option
- **UI Preferences**:
  - Dark/light mode toggle
  - Compact/comfortable view density
  - Show/hide advanced options

#### 📊 Status
- **Active Channels**: Real-time channel usage and client counts
- **System Info**: Version, uptime, resource usage hints

### Responsive Behavior

| Viewport | Menu Behavior |
|----------|---------------|
| Desktop (>1024px) | Fixed left sidebar, always visible |
| Tablet (768-1024px) | Collapsible sidebar with hamburger toggle |
| Mobile (<768px) | Bottom navigation bar with key sections |

---

## Known Issues 🐛

### v2.0.0b5
1. **{count} formatting warning** - Logfire template issue in session_manager.py line 499
2. **Floor/area API JSON parse errors** - Non-fatal warnings during HA registry refresh

### v2.0.0b4 (Fixed in b5)
- None - cycling feature addition only

### v2.0.0b3 (Fixed in b4)
1. ~~**Generator concurrency error** - Multiple speakers connecting to same channel causes "generator already executing" error.~~ Fixed: Each client now gets independent audio stream.

### v1.3.x
- None actively tracked (stable)

---

## Configuration Options

### Hardware Settings (HA Addon Config)
```yaml
sonorium__stream_url: "http://192.168.1.104:8008"  # Must use IP, not mDNS
sonorium__path_audio: "/media/sonorium"
sonorium__max_channels: 6  # 1-10 concurrent channels (hardware-dependent)
```

### User Settings (Web UI)
```yaml
# Stored in /config/sonorium/state.json under "settings"
default_volume: 60
crossfade_duration: 3.0
default_cycle_interval: 60  # minutes
default_cycle_randomize: false
```

### Per-Session Cycle Config
```yaml
# Each session can have its own cycle configuration
cycle_config:
  enabled: false
  interval_minutes: 60  # 1 to 1440 (24 hours)
  randomize: false
  theme_ids: []  # Empty = all themes, or specific theme IDs
```

---

## API Endpoints

### Streaming
- `GET /stream/{theme_id}` - Legacy theme-based stream
- `GET /stream/channel{n}` - Channel-based stream (v2)

### Sessions
- `GET /api/sessions` - List all sessions
- `POST /api/sessions` - Create session
- `GET /api/sessions/{id}` - Get session details
- `PUT /api/sessions/{id}` - Update session
- `DELETE /api/sessions/{id}` - Delete session
- `POST /api/sessions/{id}/play` - Start playback
- `POST /api/sessions/{id}/stop` - Stop playback
- `POST /api/sessions/{id}/volume` - Set volume

### Theme Cycling
- `GET /api/sessions/{id}/cycle` - Get cycle status (includes next change time)
- `PUT /api/sessions/{id}/cycle` - Update cycle configuration
- `POST /api/sessions/{id}/cycle/skip` - Skip to next theme

### Channels
- `GET /api/channels` - List all channels
- `GET /api/channels/{id}` - Get channel status

### Themes
- `GET /api/themes` - List all themes
- `GET /api/themes/{id}` - Get theme details

### Speakers
- `GET /api/speakers` - List all speakers
- `GET /api/speakers/hierarchy` - Floor/area/speaker tree
- `POST /api/speakers/refresh` - Refresh from HA

### Settings
- `GET /api/settings` - Get user settings
- `PUT /api/settings` - Update user settings

---

## Development Notes

### Audio Pipeline
```
Theme Definition
    └── Recording Instances (tracks)
         └── CrossfadeStream (per-track looping with crossfade)
              └── ThemeStream (mixes all tracks)
                   └── Channel (tracks current theme, version counter)
                        └── ChannelStream (independent per-client, handles crossfade)
                             └── HTTP StreamingResponse (MP3 encoding)
```

### Theme Cycling Pipeline
```
CycleManager (background task, 10s interval)
    └── Check each playing session
         └── If cycle_config.enabled and interval elapsed
              └── Get next theme (sequential or random)
                   └── SessionManager.update(theme_id=next)
                        └── Channel.set_theme() triggers crossfade
```

### Key Files
- `sonorium/core/channel.py` - Channel streaming with per-client crossfade
- `sonorium/core/cycle_manager.py` - Background task for theme cycling
- `sonorium/core/session_manager.py` - Session CRUD and playback
- `sonorium/core/state.py` - Persistence layer (includes CycleConfig)
- `sonorium/theme.py` - Theme definitions and track mixing
- `sonorium/recording.py` - Individual track streaming
- `sonorium/api.py` - FastAPI endpoints, initializes CycleManager
- `sonorium/web/api_v2.py` - v2 REST API router with cycling endpoints
- `sonorium/web/static/` - Web UI assets (planned restructure)

### Testing Checklist
- [x] Single speaker playback
- [x] Multi-speaker playback (same theme)
- [x] Theme switching while playing (crossfade)
- [ ] Multiple simultaneous sessions (different themes)
- [ ] Session persistence across restart
- [ ] Speaker group creation/editing
- [ ] Volume control during playback
- [ ] Theme cycling (automatic interval)
- [ ] Theme cycling (skip to next)
- [ ] Theme cycling (random vs sequential)
- [ ] UI navigation menu

---

## Version History

### 2.0.0b5 (2024-12-11)
- Added CycleManager for automatic theme cycling
- Added CycleConfig to Session model
- Added cycling API endpoints (get/update/skip)
- Background task checks every 10 seconds for needed cycles
- Support for random or sequential cycling
- Configurable interval from 1 minute to 24 hours
- Optional theme whitelist for cycling

### 2.0.0b4 (2024-12-11)
- Fixed multi-client concurrency: each client gets independent audio generator
- Per-client crossfading when theme changes
- Thread-safe theme changes with version counter

### 2.0.0b3 (2024-12-11)
- Fixed route ordering for channel endpoints
- Channel-based streaming architecture
- Theme crossfading within channels

### 2.0.0b2 (2024-12-11)
- Added ChannelManager and Channel classes
- Added max_channels configuration
- Integrated channels with SessionManager

### 2.0.0b1 (2024-12-11)
- Initial v2 beta with session management
- Multi-zone support
- Speaker hierarchy from HA

### 1.3.2 (Stable)
- Single theme streaming
- Basic web UI
- MQTT integration
