# TODO - Sonorium Development Tasks

> **Note:** This file is NOT committed to git. It tracks pending work for the
> standalone app and Docker container. The HA addon is STABLE and not modified.

---

## Priority 1 - Critical Fixes

### Fix AirPlay Streaming to Audio Speakers
**Status:** In Progress (restarting clean)
**Affects:** `app/core/sonorium/streaming.py`
**Version:** v0.2.34-alpha

**Problem:**
- Can discover AirPlay speakers via mDNS
- Cannot stream audio to them
- pyatv's `stream_url()` only works with Apple TV devices
- Audio-only AirPlay speakers (HomePod, AirPort Express, Linkplay) need RAOP push

**Solution Approach:**
- Use pyatv's `stream_file()` with asyncio StreamReader
- Pipe HTTP stream via **aiohttp** (pure Python, no curl) to feed MP3 data to pyatv
- **Must be fully portable** - no OS-specific dependencies

**Reference Documentation:**
- See `docs/airplay/*.md` for protocol specs and implementation patterns
- Primary: `AIRPLAY2_SPEC.md` and `PYATV_STREAMING_REFERENCE.md`

**Test Devices:**
- Office_C97a: 192.168.1.74 (primary, confirmed working)
- Arylic-livingroom: 192.168.1.254
- Marantz SR-5011: 192.168.1.13
- LG Soundbar: discoverable via mDNS

**Testing needed:**
- Scan and discover AirPlay devices
- Test audio streaming to 192.168.1.74
- Verify continuous streaming works (not just file playback)
- Use pleasant tones for testing (dog in household)

---

### Verify Local Audio Device Detection
**Status:** Fixed in v0.2.30-alpha
**Affects:** `app/core/requirements.txt`

**Problem:** Local audio devices not detected in Windows app.
**Fix:** Added `sounddevice>=0.4.6` to requirements.txt

---

## Priority 2 - Platform Parity

### Sync Exclusive Track Logic to HA Addon
**Status:** Deferred (HA addon is stable)
**Note:** Only do this if explicitly requested

The standalone app has updated exclusive track logic that could be ported:
- ExclusionGroupCoordinator class
- Sparse playback timing constants
- No-repeat logic

**Files to compare:**
- Standalone: `app/core/sonorium/recording.py` (42KB)
- HA Addon: `sonorium_addon/sonorium/recording.py` (30KB)

---

## Priority 3 - Enhancements

### Docker Container Testing
**Status:** Pending
**Affects:** `app/docker/`

- Docker container deployed to NAS (192.168.1.150)
- Needs verification of:
  - Network speaker discovery
  - DLNA streaming
  - AirPlay streaming (once fixed)
  - Theme loading from mounted volume

### Channel-Based Streaming (Standalone App)
**Status:** Planned
**See:** Plan file for implementation details

Port channel system from HA addon:
- Persistent channels that survive theme changes
- Speakers don't need to reconnect
- Output gain control

---

## Priority 4 - Nice to Have

### Plugin System
**Status:** Planned
**Location:** `app/core/sonorium/plugins/`

- Ambient Mixer importer plugin
- Future integrations

### Web UI Modularization
**Status:** Planned

Break monolithic web_api.py into:
- Separate CSS/JS files
- Component-based architecture

---

## Notes

- **Version tags:** Use `v0.x.x-alpha` for pre-releases
- **Builds:** Always via GitHub Actions, never local PyInstaller
- **Testing:** Manual testing only, no test framework
- **HA Addon:** Do NOT modify unless explicitly requested
