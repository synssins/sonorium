# AirPlay Implementation References

> Reference implementations for understanding RAOP/AirPlay internals

---

## shairport-sync (C-based Receiver)

> Source: https://github.com/mikebrady/shairport-sync

### Protocol Support
- **AirPlay 1** (Classic AirPlay)
- **AirPlay 2** (with PTP timing)

### Audio Format Support

| Parameter | Values |
|-----------|--------|
| Codec | ALAC (Apple Lossless) via Apple decoder library |
| Bit Depths | 8, 16, 24, 32 bits |
| Sample Rates | 44,100 Hz, 88,200 Hz, 176,400 Hz, 352,000 Hz |

### Audio Backends
- ALSA (Linux) - Primary, direct DAC access
- PulseAudio (desktop Linux)
- sndio (FreeBSD/OpenBSD)
- Jack Audio
- PipeWire (experimental)
- Unix pipes / STDOUT

### Synchronization Mechanism
- Monitors source timestamps against output device timing
- Uses frame insertion/deletion ("stuffing") at pseudo-random intervals
- Optional resampling via libsoxr
- Sub-millisecond accuracy

### Timing Protocols
- **Classic AirPlay:** NTP-variant synchronization
- **AirPlay 2:** PTP (Precision Time Protocol) via NQPTP companion app

### Latency
- AirPlay 1: ~2.0-2.25 seconds (source-specified)
- AirPlay 2: ~0.5 seconds (shorter latency enabled)

### Key Source Files
- `rtsp.c` - RTSP protocol handling
- `rtp.c` - RTP audio transport

---

## airplay2-receiver (Python Receiver)

> Source: https://github.com/openairplay/airplay2-receiver

### Supported Features

**Authentication & Pairing:**
- HomeKit transient pairing (SRP/Curve25519/ChaCha20-Poly1305)
- HomeKit non-transient pairing
- FairPlay v3 authentication and AES key decryption
- ANNOUNCE + RSA AES for unbuffered streaming (iTunes/Windows)

**Audio Handling:**
- REALTIME and BUFFERED audio streams
- Codecs: ALAC, AAC, OPUS, PCM
- Output latency compensation (multi-room)
- RFC2198 RTP Redundancy (basic)
- RTCP implementation

**Services:**
- mDNS/ZeroConf service publication
- Spotify and live media streams with AES keys
- Multi-room with concurrent connections

### Code Structure
```
airplay2-receiver/
├── ap2/                 # Core implementation
├── docker/              # Containerization
├── pairings/            # Persistent pairing data
└── ap2-receiver.py      # Entry point
```

### Known Limitations
- No FairPlay v2 support
- No accurate audio sync via PTP/NTP
- No MFi Authentication (requires hardware module)

### Status
"Experimental, yet fully functional" - First Python FairPlay v3 implementation

---

## pyatv (Python Client Library)

> Source: https://github.com/postlund/pyatv

### Purpose
Stream audio **TO** AirPlay devices (client role, not receiver)

### Supported Devices
- HomePod
- AirPort Express
- Third-party AirPlay speakers

### Key Feature
`stream_file()` accepts:
- File paths
- Buffers
- asyncio.StreamReader (for piped streams)

### Streaming Model
- **Push model** - Client streams data to device
- Uses RAOP protocol internally
- ~2 second buffering delay is normal

See: [PYATV_STREAMING_REFERENCE.md](./PYATV_STREAMING_REFERENCE.md)

---

## Protocol Comparison

| Aspect | AirPlay 1 | AirPlay 2 |
|--------|-----------|-----------|
| Timing | NTP-based | PTP-based |
| Latency | ~2 seconds | ~0.5 seconds |
| Multi-room | Limited | Full support |
| Buffered Audio | No | Yes |
| Authentication | RSA/FairPlay | HomeKit pairing |

---

## Key Implementation Insights

### For Streaming TO Devices (Sonorium Use Case)

1. **Use pyatv's stream_file()** - Handles RAOP complexity
2. **MP3 format preferred** - Works with non-seekable streams
3. **Expect ~2 second delay** - Normal RAOP buffering
4. **Pipe HTTP streams** - Use curl/aiohttp -> StreamReader -> pyatv

### RAOP Connection Flow
```
1. mDNS discovery (_raop._tcp)
2. RTSP OPTIONS
3. RTSP ANNOUNCE (SDP with codec/encryption)
4. RTSP SETUP (negotiate UDP ports)
5. RTSP RECORD (start streaming)
6. RTP audio packets (port 96)
7. RTP sync packets (port 84, every 1 second)
8. RTSP TEARDOWN (end session)
```

### Volume Control
- Via RTSP SET_PARAMETER
- Range: -144 (mute) to 0 (max), in dB
- Usable range: -30 to 0
