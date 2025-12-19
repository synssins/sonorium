# AirPlay Protocol Specification Reference

> Source: https://nto.github.io/AirPlay.html (Primary), https://openairplay.github.io/airplay-spec/
> Covers: Apple TV 5.0, iOS 5.1, iTunes 10.6

---

## Protocol Architecture

AirPlay audio streaming uses **RAOP (Remote Audio Output Protocol)**, built on:

| Layer | Protocol | Purpose |
|-------|----------|---------|
| Discovery | mDNS/Bonjour | `_raop._tcp` and `_airplay._tcp` services |
| Control | RTSP (RFC 2326) | Session management |
| Transport | RTP (RFC 3550) | Audio data packets |
| Time Sync | NTP (RFC 5905) | Clock synchronization |
| Encryption | AES-128-CBC | Audio payload encryption |

---

## Service Discovery (mDNS/Bonjour)

### RAOP Service (Audio)
- **Type:** `_raop._tcp`
- **Port:** 49152
- **Name format:** `MAC_ADDRESS@Device_Name` (e.g., `5855CA1AE288@Apple TV`)

**TXT Record Fields:**
| Field | Description | Example |
|-------|-------------|---------|
| `ch` | Audio channels | `2` (stereo) |
| `cn` | Codec support | PCM, ALAC, AAC, AAC-ELD |
| `et` | Encryption types | 0=none, 1=RSA, 3=FairPlay, 4=MFiSAP, 5=FairPlay SAPv2.5 |
| `sr` | Sample rate | `44100` |
| `ss` | Sample size | `16` (bits) |
| `tp` | Transport protocol | TCP/UDP |
| `vs` | Server version | |

### AirPlay Service (Video/Photos)
- **Type:** `_airplay._tcp`
- **Port:** 7000

**TXT Record Fields:**
| Field | Description |
|-------|-------------|
| `deviceid` | MAC address |
| `model` | Device model identifier |
| `features` | 32-64 bit hex bitfield |
| `flags` | 20-bit hex status flags |
| `pw` | Password protection boolean |
| `srcvers` | AirPlay version |
| `pk` | Public key (hex string) |
| `protovers` | Protocol version |

---

## Audio Specifications

| Parameter | Value |
|-----------|-------|
| Sample Rate | 44100 Hz |
| Channels | 2 (stereo) |
| Sample Size | 16-bit |
| Supported Codecs | PCM, Apple Lossless (ALAC), AAC, AAC-ELD |
| Encryption Types | None, RSA (AirPort Express), FairPlay, MFiSAP |
| Buffer/Latency | ~2 seconds |

---

## RTSP Audio Streaming

### Supported RTSP Methods
```
OPTIONS, ANNOUNCE, SETUP, RECORD, PAUSE, FLUSH, TEARDOWN, GET_PARAMETER, SET_PARAMETER, POST, GET
```

### RTSP Sequence
1. **OPTIONS** - Query supported methods
2. **ANNOUNCE** - Transmit stream properties via SDP
3. **SETUP** - Initialize UDP channels
4. **RECORD** - Start streaming
5. **SET_PARAMETER** - Volume, metadata
6. **FLUSH** - Clear buffers
7. **TEARDOWN** - End session

### ANNOUNCE (SDP Format)
The ANNOUNCE request contains SDP with:
- `rtpmap` - Payload type
- `fmtp` - Sample rate parameters (typically 44100 Hz)
- `fpaeskey` - Encrypted AES key (FairPlay)
- `aesiv` - AES initialization vector
- `min-latency` - Acceptable latency in samples

### SETUP Response
Initializes three UDP channels:
1. **server_port** - Audio data
2. **control_port** - Retransmit requests
3. **timing_port** - Time synchronization

### RECORD
Initiates streaming with `RTP-Info` header containing:
- Initial sequence number
- Initial RTP timestamp

---

## RTP Packet Formats

### Payload Types

| Type | Port | Purpose |
|------|------|---------|
| 82 | timing_port | Timing request |
| 83 | timing_port | Timing reply |
| 84 | control_port | Time sync |
| 85 | control_port | Retransmit request |
| 86 | control_port | Retransmit reply |
| 96 | server_port | Audio data |

### Audio Data Packets (Type 96)
- Standard RTP header
- Encrypted audio payload (AES-128-CBC)
- Marker bit set on first packet after RECORD/FLUSH

### Sync Packets (Type 84)
Sent once per second to control port:
```
[8 bytes] RTP header (without SSRC)
[8 bytes] Current NTP timestamp
[4 bytes] Next audio packet's RTP timestamp
```
Purpose: Correlate RTP timestamps to NTP time

### Retransmit Request (Type 85)
```
[8 bytes] RTP header (without SSRC)
[2 bytes] First lost packet sequence number
[2 bytes] Count of missing packets
```

### Retransmit Reply (Type 86)
Contains full audio RTP packets

### Timing Packets (Types 82/83)
Sent every 3 seconds for master clock sync:
```
[8 bytes] RTP header
[8 bytes] Origin timestamp (NTP)
[8 bytes] Receive timestamp (NTP)
[8 bytes] Transmit timestamp (NTP)
```

---

## Volume Control

**Method:** `SET_PARAMETER`

**Volume Range:**
- `-144` = Muted (special value)
- `-30` to `0` = Audible range (dB attenuation)
- `0` = Maximum volume

**Request Format:**
```
SET_PARAMETER rtsp://[device]/[session] RTSP/1.0
CSeq: [n]
Session: [session-id]
Content-Type: text/parameters
Content-Length: [len]

volume: -11.123877
```

---

## Metadata Protocol

Metadata sent via `SET_PARAMETER` with `RTP-Info` header specifying validity timestamp.

### Track Info (DAAP format)
- **Content-Type:** `application/x-dmap-tagged`
- Fields: `dmap.itemname`, `daap.songartist`, `daap.songalbum`

### Cover Art
- **Content-Type:** `image/jpeg`

### Playback Progress
- **Content-Type:** `text/parameters`
- Three RTP timestamps: start/current/end

---

## Authentication

### AirPort Express (RSA)
1. Client sends 128-bit random number in `Apple-Challenge` header
2. Client generates AES key, encrypts with server's RSA public key (OAEP)
3. Sends in `rsaaeskey` SDP attribute
4. Server signs challenge with RSA private key (PKCS#1)
5. Returns in `Apple-Response` header
6. Client verifies by decrypting with public key

### Password Protection (HTTP Digest)
- **AirTunes realm:** `raop`, username `iTunes`
- **AirPlay realm:** `AirPlay`, username `AirPlay`

---

## HTTP Headers

All AirPlay requests include:
- `X-Apple-Session-ID` - UUID
- `X-Apple-Device-ID` - MAC address
- `User-Agent: MediaControl/1.0`
- `DACP-ID` - 64-bit ID for remote control
- `Active-Remote` - Authentication token

---

## Remote Control (DACP)

When streaming, clients advertise DACP capability via:
- `_dacp._tcp` mDNS service
- Service name: `iTunes_Ctrl_$DACP_ID`
- Port: 3689

### Available Commands
HTTP requests to `/ctrl-int/1/$CMD`:
- `play`, `pause`, `playpause`, `stop`, `playresume`
- `nextitem`, `previtem`
- `beginff`, `beginrew`
- `volumeup`, `volumedown`, `mutetoggle`
- `shuffle_songs`

Authentication via `Active-Remote` header (no pairing required).
