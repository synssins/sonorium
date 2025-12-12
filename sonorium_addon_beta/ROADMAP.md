# Sonorium Roadmap & Feature Tracker

This document tracks planned features, known issues, and the development roadmap for Sonorium.

## Current Version
- **Stable (v1):** 1.3.2 - Single theme streaming, basic web UI
- **Beta (v2):** 2.0.0b3 - Multi-zone sessions, channel-based streaming

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

### In Progress 🔄
- [ ] Fix channel concurrency (multiple clients sharing generator)
- [ ] Theme cycling with configurable interval
- [ ] Theme randomization option

### Planned 📋

#### Core Features
- [ ] Speaker group UI in web interface
- [ ] Volume per-speaker overrides within a session
- [ ] Pause/resume functionality (currently only play/stop)
- [ ] Session duplication (clone existing session)

#### Theme Cycling & Scheduling
- [ ] Auto-cycle themes on timer (10 min, 30 min, 1 hr, 2 hr, etc.)
- [ ] Randomize theme order during cycling
- [ ] Schedule-based playback (e.g., "play rain forest 6am-8am")
- [ ] Crossfade duration per-session setting

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
- [ ] Dark/light theme toggle
- [ ] Mobile-optimized responsive design
- [ ] Drag-and-drop speaker assignment
- [ ] Quick play buttons for favorite themes
- [ ] Session templates/presets

---

## Known Issues 🐛

### v2.0.0b3
1. **Generator concurrency error** - Multiple speakers connecting to same channel causes "generator already executing" error. Need per-client chunk generators.
2. **{count} formatting warning** - Logfire template issue in session_manager.py line 499
3. **Floor/area API JSON parse errors** - Non-fatal warnings during HA registry refresh

### v1.3.x
- None actively tracked (stable)

---

## Configuration Options

### Current (v2 Beta)
```yaml
sonorium__stream_url: "http://192.168.1.104:8008"  # Must use IP, not mDNS
sonorium__path_audio: "/media/sonorium"
sonorium__max_channels: 6  # 1-10 concurrent channels
```

### Planned
```yaml
sonorium__default_crossfade: 3.0  # seconds
sonorium__theme_cycle_interval: 0  # 0=disabled, minutes
sonorium__theme_cycle_random: false
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

---

## Development Notes

### Audio Pipeline
```
Theme Definition
    └── Recording Instances (tracks)
         └── CrossfadeStream (per-track looping with crossfade)
              └── ThemeStream (mixes all tracks)
                   └── Channel (handles theme transitions)
                        └── ChannelStream (MP3 encoding per client)
                             └── HTTP StreamingResponse
```

### Key Files
- `sonorium/core/channel.py` - Channel streaming with crossfade
- `sonorium/core/session_manager.py` - Session CRUD and playback
- `sonorium/core/state.py` - Persistence layer
- `sonorium/theme.py` - Theme definitions and track mixing
- `sonorium/recording.py` - Individual track streaming
- `sonorium/api.py` - FastAPI endpoints
- `sonorium/web/api_v2.py` - v2 REST API router

### Testing Checklist
- [ ] Single speaker playback
- [ ] Multi-speaker playback (same theme)
- [ ] Theme switching while playing (crossfade)
- [ ] Multiple simultaneous sessions
- [ ] Session persistence across restart
- [ ] Speaker group creation/editing
- [ ] Volume control during playback

---

## Version History

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
