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