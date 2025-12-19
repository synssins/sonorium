# AirPlay 2 Specification Reference

> For **sending audio TO devices** (Sonorium use case)

---

## AirPlay 2 vs AirPlay 1 — Key Differences

| Feature | AirPlay 1 | AirPlay 2 |
|---------|-----------|-----------|
| Stream Type | Realtime only (~2s latency) | Realtime + **Buffered** (~0.5s latency) |
| Multi-room | Not synchronized | **PTP-synchronized multi-room** |
| Time Sync | NTP | **PTP (IEEE 1588)** on ports 319/320 |
| Pairing | Optional RSA | **HomeKit (SRP/Curve25519/ChaCha20-Poly1305)** |
| Encryption | AES-128-CBC with RSA key exchange | **FairPlay v3** + AES-GCM |
| Codecs | ALAC, AAC, PCM | ALAC, **AAC (including AAC-ELD)**, **OPUS**, PCM |
| Sample Rates | 44.1kHz | 44.1kHz, **48kHz** |
| Surround | Stereo only | Stereo, **5.1, 7.1** |

---

## Protocol Flow for Sender Implementation

### 1. Service Discovery (mDNS/Bonjour)
Browse for `_airplay._tcp` and `_raop._tcp` services to find devices.

### 2. Pairing/Authentication
- **AirPlay 1**: RSA challenge-response (keys are public)
- **AirPlay 2**: HomeKit transient pairing using SRP protocol

### 3. RTSP Session Setup
```
ANNOUNCE → SETUP → RECORD → [stream] → FLUSH → TEARDOWN
```

### 4. Audio Streaming

| Stream Type | Protocol | Use Case |
|-------------|----------|----------|
| **Realtime** | RTP/UDP | Live audio, ~2s latency |
| **Buffered** | TCP | Pre-buffered content, ~0.5s latency |

### 5. Time Synchronization
- **AirPlay 1**: NTP timing packets every 3 seconds
- **AirPlay 2**: PTP (Precision Time Protocol) on UDP ports 319/320

---

## Best Python Option for Sending: pyatv

pyatv is an asyncio Python library that supports audio streaming via AirPlay to receivers like HomePod, AirPort Express, and third-party speakers. It implements both RAOP/AirTunes for audio and the AirPlay protocol.

### Install
```bash
pip install pyatv
```

### Stream Audio File to AirPlay Device
```python
import asyncio
import pyatv

async def stream_audio():
    # Discover devices
    atvs = await pyatv.scan(asyncio.get_event_loop(), timeout=5)

    if atvs:
        atv = await pyatv.connect(atvs[0], asyncio.get_event_loop())
        try:
            # Stream a file (MP3, WAV, FLAC, OGG supported)
            await atv.stream.stream_file("ambient.mp3")
        finally:
            await atv.close()

asyncio.run(stream_audio())
```

### Stream from Buffer/Stdin (For Real-Time Soundscape Mixing)
```python
import asyncio.subprocess as asp

# Pipe from ffmpeg or your audio mixer
process = await asp.create_subprocess_exec(
    "ffmpeg", "-i", "pipe:0", "-f", "mp3", "-",
    stdin=asp.PIPE, stdout=asp.PIPE, stderr=None,
)
await atv.stream.stream_file(process.stdout)
```

---

## AirPlay 2 Sender References

| Project | Language | Status | URL |
|---------|----------|--------|-----|
| **pyatv** | Python | Active, recommended | https://github.com/postlund/pyatv |
| **ap2-sender** | Objective-C | Stale (2020) | https://github.com/openairplay/ap2-sender |
| **airplay2-receiver** | Python | Receiver, useful for protocol study | https://github.com/openairplay/airplay2-receiver |
| **OwnTone (forked-daapd)** | C | Full sender implementation | https://github.com/owntone/owntone-server |

---

## Practical Recommendations for Sonorium

Given the use case of streaming mixed ambient soundscapes:

1. **Use pyatv** — handles discovery, pairing, and RAOP streaming
2. **Feed audio via buffer** — pipe mixed audio through ffmpeg to MP3 format, then stream via `stream_file()`
3. **For multi-room sync** — requires AirPlay 2 devices and PTP implementation (pyatv has basic support, bleeding edge)

### Key Limitation
**~2 second delay** until audio starts playing. This is part of the buffering mechanism and unavoidable.

For ambient soundscapes, the 2-second startup latency is acceptable since there's no real-time synchronization with video or user interaction.

---

## Sonorium Implementation Strategy

### Recommended Approach
```
Sonorium Theme Mixer
        ↓
    PCM Audio Chunks
        ↓
    MP3 Encoder (PyAV/FFmpeg)
        ↓
    HTTP Stream Endpoint (e.g., /stream.mp3)
        ↓
    curl/aiohttp fetches stream
        ↓
    Pipe to asyncio.StreamReader
        ↓
    pyatv.stream.stream_file(reader)
        ↓
    AirPlay Speaker
```

### Why This Works
- pyatv's `stream_file()` accepts `asyncio.StreamReader`
- MP3 format works with non-seekable streams
- HTTP endpoint allows multiple consumers (DLNA, browser, etc.)
- Curl/aiohttp handles HTTP → pipe conversion

### Code Pattern
```python
import asyncio
import asyncio.subprocess as asp
import pyatv

async def stream_to_airplay(device_config, http_stream_url):
    # Connect to AirPlay device
    atv = await pyatv.connect(device_config, asyncio.get_event_loop())

    try:
        # Pipe HTTP stream via curl
        process = await asp.create_subprocess_exec(
            "curl", "-s", "-N", http_stream_url,
            stdout=asp.PIPE,
            stderr=asp.PIPE
        )

        # Stream to AirPlay
        await atv.stream.stream_file(process.stdout)

    finally:
        await atv.close()
```

### Important Notes
- Use `-N` (no buffering) with curl for real-time streaming
- MP3 format required (WAV/OGG need seeking)
- Handle cleanup: cancel tasks, kill processes, close connections
- Store process/task handles for stop functionality
