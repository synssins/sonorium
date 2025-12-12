# Changelog

All notable changes to Sonorium are documented here.

## [2.0.0b4] - 2024-12-11

### Fixed
- **Multi-client streaming**: Each speaker now gets an independent audio generator, fixing "generator already executing" errors when multiple speakers connect to the same channel
- Per-client crossfading when themes change mid-playback

### Changed
- Channel architecture refactored: Channel tracks theme state, ChannelStream handles per-client audio generation
- Thread-safe theme changes via version counter mechanism

## [2.0.0b3] - 2024-12-11

### Fixed
- Route ordering: `/stream/channel{n}` now correctly registered before `/stream/{id}` catch-all

### Added
- Channel-based streaming endpoints
- Theme crossfading within channels (3-second equal-power crossfade)

## [2.0.0b2] - 2024-12-11

### Added
- `Channel` class for persistent streaming with theme management
- `ChannelManager` for channel pool management
- `max_channels` configuration option (1-10, default 6)
- Channel integration with SessionManager
- `/api/channels` endpoints for channel status

### Changed
- Sessions now use channel-based URLs (`/stream/channel{n}`) instead of theme URLs
- SessionManager tracks channel assignments per session

## [2.0.0b1] - 2024-12-11

### Added
- **Multi-zone session management**: Create multiple simultaneous playback sessions
- **Speaker groups**: Save speaker configurations by floor, area, or individual device
- **Session auto-naming**: Automatic names based on selected speakers/areas
- **v2 Web UI**: Modern interface for session and speaker management
- **Speaker hierarchy**: Floor → Area → Speaker organization from HA registry
- **State persistence**: Sessions and groups survive addon restarts
- **Fire-and-forget playback**: Instant UI response, background speaker communication

### Changed
- Architecture split: v1 MQTT entities still work, v2 REST API added
- Port changed to 8008 (beta runs alongside stable on 8007)

### Removed
- Track enable/disable feature: All tracks in a theme are always active

---

## [1.3.2] - 2024-12-10

### Fixed
- Stability improvements
- Minor bug fixes

## [1.3.1] - 2024-12-09

### Added
- Crossfade looping for seamless single-file playback (3-second equal-power crossfade)
- One-click Home Assistant add-on installation support
- Web UI for status and control
- Theme-based folder organization

### Changed
- Renamed from Amniotic to Sonorium
- Simplified controls: Single play/pause toggle instead of separate buttons
- Master volume control instead of per-recording volume
- Removed MQTT media player dependency - now uses HA REST API directly

### Removed
- Per-recording enable/disable switches
- Per-recording volume controls
- MQTT service requirement

---

## [1.2.3] - Amniotic (Original)

- Original Amniotic release by [fmtr](https://github.com/fmtr/amniotic)
- Basic ambient soundscape streaming
- MQTT integration
- Multi-track mixing

---

## Version Numbering

- **1.x.x**: Stable single-zone releases
- **2.0.0bX**: Beta multi-zone releases (v2 development)
- **2.0.0**: Future stable multi-zone release

## Links

- [README](README.md)
- [Documentation](DOCS.md)
- [Roadmap](ROADMAP.md)
