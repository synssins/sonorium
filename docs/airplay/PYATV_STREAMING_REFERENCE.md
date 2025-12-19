# pyatv Streaming Reference

> Source: https://pyatv.dev/development/stream/
> Library: https://github.com/postlund/pyatv

---

## Overview

pyatv is an asyncio Python library that supports audio streaming via AirPlay to:
- HomePod speakers
- AirPort Express devices
- Third-party AirPlay-compatible speakers

---

## Installation

```bash
pip install pyatv
```

---

## stream_file() Method

The primary streaming method is `stream.stream_file()`.

### Supported Input Types
1. **File path** (string)
2. **File buffer** (io.BufferedReader)
3. **asyncio.StreamReader** (for piped streams)
4. **HTTP(S) URLs** (experimental)

### Supported Audio Formats

| Format | Buffer/Stream Support | Notes |
|--------|----------------------|-------|
| **MP3** | Yes | Works reliably with non-seekable streams |
| **WAV** | Requires seeking | Must be seekable |
| **FLAC** | Requires seeking | Must be seekable |
| **OGG** | Requires seeking | Must be seekable |

**Recommendation:** Use MP3 for streaming from non-seekable sources (pipes, HTTP).

---

## Code Examples

### Basic File Streaming
```python
await stream.stream_file("sample.mp3")
```

### Buffer Streaming
```python
import io

with io.open("myfile.mp3", "rb") as source_file:
    await stream.stream_file(source_file)
```

### StreamReader Streaming (Piped Input)
```python
import asyncio

# Example: Pipe from ffmpeg
process = await asyncio.create_subprocess_exec(
    "ffmpeg", "-i", "input.wav", "-f", "mp3", "-",
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.DEVNULL
)

await stream.stream_file(process.stdout)
```

### With Custom Metadata
```python
from pyatv.interface import MediaMetadata

metadata = MediaMetadata(
    artist="pyatv",
    title="Look at me, I'm streaming"
)
await stream.stream_file("myfile.mp3", metadata=metadata)
```

### Selective Metadata Override
```python
metadata = MediaMetadata(title="Custom Title")
await stream.stream_file(
    "myfile.mp3",
    metadata=metadata,
    override_missing_metadata=True
)
```

---

## Important Limitations

### Playback Delay
- **~2 second delay** until audio starts playing
- This is normal RAOP buffering behavior

### Seeking Requirements
- WAV, FLAC, OGG formats require seekable streams
- For non-seekable sources (pipes, HTTP), use **MP3**
- Metadata extraction requires seeking back to file start

### Metadata
- Artwork must be **JPEG format**
- Metadata extraction fails for non-seekable streams

---

## Connection Flow

```python
import pyatv

# 1. Scan for devices
atvs = await pyatv.scan(loop)

# 2. Connect to device
atv = await pyatv.connect(atvs[0], loop)

# 3. Access stream interface
stream = atv.stream

# 4. Stream audio
await stream.stream_file("audio.mp3")

# 5. Close connection
atv.close()
```

---

## Full Example: HTTP Stream to AirPlay

```python
import asyncio
import pyatv

async def stream_to_airplay(device_config, stream_url):
    # Connect to AirPlay device
    atv = await pyatv.connect(device_config, asyncio.get_event_loop())

    try:
        # Use curl to fetch HTTP stream and pipe to pyatv
        process = await asyncio.create_subprocess_exec(
            "curl", "-s", "-N", stream_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # Stream the piped data
        await atv.stream.stream_file(process.stdout)

    finally:
        atv.close()
```

---

## Device Discovery

```python
import pyatv

async def discover_airplay_devices():
    atvs = await pyatv.scan(asyncio.get_event_loop())

    for atv in atvs:
        print(f"Name: {atv.name}")
        print(f"Address: {atv.address}")

        # Check for AirPlay support
        if pyatv.const.Protocol.AirPlay in atv.protocols:
            print("  -> Supports AirPlay")
```

---

## Notes

- Version 0.13.0+ improved buffer support for non-seekable streams
- HTTP(S) URL streaming is experimental
- Automatic format conversion based on device capabilities
