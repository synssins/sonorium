# Sonorium theme format, version 2

This document describes what a Sonorium theme folder contains and exactly how the
server turns it into sound. It is written for two readers:

- **Theme generator authors** (for example an ElevenLabs-based tool) who produce theme folders.
- **Mixer porters** (for example Android/Kotlin) who must reproduce the server's output.

Every behavioural rule cites the current Python code in `sonorium_addon/sonorium/`
as `file:line`. Line numbers are for commit `29cb7a1` ("theme group folders, one shared
theme scanner"). Where the code and this document disagree, the code wins and this
document has a bug.

**Words used here**

| Word | Meaning |
|---|---|
| Track | One audio file in a theme. |
| Version | One of several tracks that are variations of the same sound (for example three lute songs). |
| Group | A named exclusive group: a subfolder of a theme. Only one of its tracks plays at a time. |
| Preset | A saved set of track settings for one theme. |
| Sequence | Presets played in order inside one theme (planned). |
| Playlist | Themes played in order. |
| Channel | One live mix that speakers listen to. Each channel plays one theme with its own preset. |

Status labels: **built** means in the code now; **target** means Themes 2.0 work that is
partly built; **planned, not built** means design only.

---

## 1. Folder layout

### 1.1 Theme folder (built)

```
<themes root>/
  Tavern/                      <- one theme (folder name = default display name)
    metadata.json              <- theme info, track settings, presets (today)
    presets.json               <- presets (target, see 1.4)
    Fireplace.mp3              <- top-level track, key "Fireplace"
    Bar chatter.wav            <- top-level track, key "Bar chatter"
    Lute/                      <- group "Lute"
      Lute song 1.mp3          <- track in group, key "Lute/Lute song 1"
      Lute song 2.mp3          <- key "Lute/Lute song 2"
    _drafts/                   <- ignored (starts with "_")
    .cache/                    <- ignored (starts with ".")
    ATTRIBUTION.md             <- optional, not read by the mixer
    MANIFEST.json              <- optional, not read by the mixer
```

Rules, from `theme_files.py`:

- Audio files are those with extension `.mp3`, `.wav`, `.flac` or `.ogg`, case-insensitive (`theme_files.py:18-22`).
- Audio files at the top of the theme folder are tracks (`theme_files.py:41`).
- Each subfolder holding at least one audio file is a **group**; its audio files are tracks in that group (`theme_files.py:29-35`, `:44-45`).
- One level deep only. Files in sub-subfolders are not found (`theme_files.py:35`, `:45` only list the group folder itself).
- Folders and files whose name starts with `.` or `_` are ignored (`theme_files.py:25-26`, `:32`, `:41`, `:45`).
- Track order: top-level files sorted by path, then each group (groups sorted by name) with its files sorted by path (`theme_files.py:38-46`). This order matters for porters (see 3.10).
- A folder is a theme only if `theme_audio_files()` finds at least one track (`core/theme_metadata.py` scan, `device.py:105-113`, `api.py:1153-1162`).

### 1.2 Track keys (built)

A track's key is its path inside the theme without the extension, with `/` between group and
file name: `"Fireplace"`, `"Lute/Lute song 1"` (`theme_files.py:59-62`). The key is used in
`metadata.json` `tracks`, in presets, and in the API (`recording.py:204-219`). The UI shows
only the part after the last `/` (`theme_files.py:65-67`).

Themes without subfolders keep exactly the keys they had in version 1 (the file stem).

### 1.3 metadata.json (built; `spec_version` and `groups` are target)

Holds theme info, per-track settings and (today) presets. Full schema in section 2.

### 1.4 presets.json (target)

Presets are moving out of `metadata.json` into `presets.json`:

```json
{ "presets": { "<preset_id>": { ... } } }
```

A later version adds `"sequences"` beside `"presets"` (section 5). In released versions
(up to 1.4.1) presets still live in `metadata.json` under `"presets"`, and the current code
reads only that location (`core/session_manager.py:94-117`, `api.py:1693-1724`). The move
will include an automatic conversion from `metadata.json` to `presets.json`.

**Generator rule for now:** write presets inside `metadata.json` (that is what the server
reads). When the conversion ships, write `presets.json` and set `"spec_version": 2`.

### 1.5 Per-user presets (planned, not built)

The plan includes per-user preset files. They are kept on the server, not in the theme
folder, and a generator never writes them.

---

## 2. metadata.json schema

Defined by the dataclasses in `core/theme_metadata.py`. Loaded by
`ThemeMetadataManager._load_or_create_metadata` (`core/theme_metadata.py:190-217`) and
written by `_save_metadata` as UTF-8 JSON, 2-space indent, non-ASCII kept as-is
(`core/theme_metadata.py:219-231`).

### 2.1 Top-level fields

| Field | Type | Default | Meaning | Code |
|---|---|---|---|---|
| `id` | string | new random UUID4 | Permanent theme ID. Folder renames keep it. | `core/theme_metadata.py:52`, `:78-80` |
| `name` | string | folder name | Display name. If empty on load, set to the folder name and saved. | `:55`, `:200-202` |
| `description` | string | `""` | Free text. | `:58` |
| `icon` | string | `""` | An emoji; empty means the UI picks one. | `:59` |
| `is_favorite` | bool | `false` | User favourite flag. | `:62` |
| `categories` | list of string | `[]` | Category names. | `:63` |
| `short_file_threshold` | number (seconds) | `15.0` | Tracks shorter than this count as "short" when `playback_mode` is `auto` (3.2). Must be `>= 0` (`web/api_v2.py:1687-1691`). | `:66`, `recording.py:246-248` |
| `tracks` | object: key -> TrackSettings | `{}` | Per-track settings, keyed by track key (1.2). | `:69`, `:84-86` |
| `presets` | object: preset_id -> Preset | `{}` | Presets (section 4). Stored as plain dicts, not checked on load. | `:72` |
| `attribution` | object or absent | absent | Source and licence info (6.3). Written only when non-empty. | `:75`, `:108-109` |
| `spec_version` | integer | absent | **Target.** `2` for this format. Not read by current code. | none |
| `groups` | object | absent | **Planned, not built.** Group rules (section 5). | none |

### 2.2 TrackSettings (one entry in `tracks`)

| Field | Type | Default | Meaning | Code |
|---|---|---|---|---|
| `volume` | number 0.0-1.0 | `1.0` | Amplitude multiplier for the track. The API clamps to 0-1 (`api.py:1456`); the loader does not. | `core/theme_metadata.py:27` |
| `presence` | number 0.0-1.0 | `1.0` | How often the track is heard (meaning depends on mode, 3.3-3.5). API clamps to 0-1 (`api.py:1382`). | `:25` |
| `muted` | bool | `false` | Track is left out of the mix. | `:26` |
| `playback_mode` | string | `"auto"` | `auto`, `continuous`, `sparse` or `presence` (3.2). Unknown strings become `auto` on apply (`api.py:497-500`). | `:28` |
| `seamless_loop` | bool | `false` | `true` turns the loop **crossfade off** (hard cut at the loop point). | `:29`, `api.py:501` |
| `exclusive` | bool | `false` | Version 1 way to put a top-level track in the single legacy group "Exclusive" (3.6). | `:30`, `recording.py:321-322`, `:377-383` |

A track with no entry in `tracks` uses all defaults (`api.py:482-490`, `:1229-1235`).
Entries whose key matches no track are kept in the file but ignored (the bundled Tavern
theme has stale `"...mp3"` keys from an older naming scheme).

### 2.3 How the server applies metadata

- At start-up and on theme refresh, each track's live settings are copied from `tracks`
  (`api.py:453-503`, `api.py:1203-1235`), mapping `muted` -> `is_enabled = not muted` and
  `seamless_loop` -> `crossfade_enabled = not seamless_loop`.
- Each per-track API call changes the live value and saves that one field
  (`api.py:972-997` `_save_track_setting_to_metadata`; for example presence `api.py:1395-1398`).
- Theme info edits from the v2 web API read and write the raw JSON
  (`web/api_v2.py:1652-1705`).

### 2.4 Unknown fields are dropped (built behaviour; important)

`ThemeMetadata.from_dict` keeps only `id, name, description, icon, is_favorite, categories,
short_file_threshold, presets, attribution` and `tracks` (`core/theme_metadata.py:113-132`);
`TrackSettings.from_dict` keeps only its six fields (`:36-39`); `to_dict` writes only those
(`:94-111`). So the first save through the metadata manager (any track setting change,
preset create/update/delete) **removes** `spec_version`, `groups` and any other unknown
field. Inside `presets`, each preset dict is kept as-is, so unknown preset fields survive.
Version 2 support must add these fields to the dataclasses before generators rely on them.

---

## 3. Track settings and exact mixer behaviour

### 3.1 Signal format (built)

| Item | Value | Code |
|---|---|---|
| Sample rate | 44100 Hz | `recording.py:190` |
| Channels | mono. Every file is decoded and resampled to s16 mono 44100 Hz by ffmpeg's resampler (PyAV `AudioResampler`), so stereo is down-mixed by libswresample defaults | `recording.py:398`, `:455`, `:681` |
| Chunk size | 1024 samples (about 23.2 ms) for every stream and the mix | `recording.py:394`, `:447`, `:629`, `:842`; `theme.py:121` |
| Per-track chunk type | int16, shape (1, 1024). Float results are clipped to [-32768, 32767] and cast with truncation toward zero | `recording.py:592`, `:801` |
| Stream output | MP3, 128 kbit/s, 44100 Hz, mono | `core/channel.py:422-441`, `theme.py:150-152` |
| Pacing | real time: the generator sleeps when ahead of the wall clock | `core/channel.py:281-288` |

Generators should deliver 44.1 kHz mono files so no resampling or down-mix happens.

### 3.2 playback_mode and how "auto" resolves (built)

Resolved once, when the track's stream is created (`recording.py:286-295`, `:297-314`):

| playback_mode | Track shorter than `short_file_threshold` | Track at or above threshold |
|---|---|---|
| `continuous` | Loop (3.3) | Loop (3.3) |
| `sparse` | Sparse (3.4) | Sparse (3.4) |
| `presence` | Loop, faded in and out (3.5) if `presence < 1`; plain loop if `presence >= 1` | same |
| `auto`, `presence < 1.0` | Sparse | Presence |
| `auto`, `presence >= 1.0` | Continuous | Continuous |

"Shorter" is `duration_seconds < threshold`, where duration comes from the container
header, or a full decode if the header has none, or 60 s if the file cannot be opened
(`recording.py:221-248`).

Then (`recording.py:301-314`):

- Sparse -> `SparsePlaybackStream`.
- Otherwise `CrossfadeRecordingStream` if `crossfade_enabled` (that is `seamless_loop == false`), else `RecordingThemeStream` (hard-cut loop).
- Presence mode with `presence < 1.0` wraps that loop in `PresenceMixingStream`.

**Group tracks are always sparse and exclusive.** For a track in a group folder,
`TrackView` returns `playback_mode = sparse` and `exclusive = true` whatever the metadata or
preset says (`recording.py:363-370`).

### 3.3 Continuous looping (built)

**With crossfade** (`seamless_loop: false`, `CrossfadeRecordingStream`, `recording.py:440-609`):

- Crossfade length: 1.5 s = 66150 samples (`recording.py:186`, `:192`).
- Curve: equal power. Outgoing `cos(linspace(0, pi/2, 66150))`, incoming `sin(linspace(0, pi/2, 66150))`, float32 (`recording.py:502-503`).
- Start point: when `samples_played >= duration_samples - 66150`, checked at chunk boundaries, so the fade begins on the first chunk boundary at or after that point (`recording.py:487`, `:531`). A second decoder of the same file starts from sample 0.
- The fade runs per sample inside each chunk; the last fading chunk pads the outgoing curve with 0 and the incoming with 1 (`recording.py:560-575`).
- If the file ends before the fade finishes (header duration was long, or the file is shorter than 1.5 s), the outgoing side is filled with silence and the real length replaces the header length (`recording.py:515-528`).
- After 65 chunks (66560 samples) the incoming decoder becomes the current one (`recording.py:580-590`).
- `volume` multiplies each decoded frame, read live (`recording.py:462-467`).
- Each output chunk is clipped and truncated to int16 (`recording.py:592`).

**Without crossfade** (`seamless_loop: true`, `RecordingThemeStream`, `recording.py:390-437`):
the file is decoded start to end, then reopened. No fade. `volume` multiplies each frame
(int16 multiply then truncation, `recording.py:416`). Leftover samples shorter than one chunk
at the end of each pass are dropped (`recording.py:409`, buffer reset per pass), and the
resampler is not flushed. Use this only for files cut to loop perfectly.

### 3.4 Sparse playback (built)

`SparsePlaybackStream` (`recording.py:612-827`). Plays the whole file once, then silence,
then again.

**Gap formula** (`recording.py:658-677`):

```
base_seconds = 180 + (1800 - 180) * (1 - presence)
gap_seconds  = base_seconds * uniform(0.7, 1.3)
gap_samples  = int(gap_seconds * 44100)
gap_chunks   = gap_samples // 1024
```

| presence | base gap | range with jitter |
|---|---|---|
| 1.0 | 3 min | 2.1-3.9 min |
| 0.5 | 16.5 min | 11.6-21.5 min |
| 0.1 | 27.3 min | 19.1-35.5 min |
| 0.0 | 30 min | 21-39 min |

Constants: `SPARSE_MIN_INTERVAL = 180`, `SPARSE_MAX_INTERVAL = 1800`,
`SPARSE_INTERVAL_VARIANCE = 0.30` (`recording.py:180-183`).

**Loop of one sparse stream** (`recording.py:740-822`):

1. Read `presence` live (`:742`).
2. First pass only: initial delay `int(gap_samples * uniform(0.0, 1.0))` samples, a fresh gap draw times a second draw, rounded down to whole chunks of silence (`:746-755`).
3. If exclusive and the group is blocked: silence for the wait (3.6), then back to step 1 (`:758-764`).
4. If exclusive, try to claim the group; if refused, silence for the wait, back to step 1 (`:767-773`).
5. Decode the whole file, multiply by `volume` (read at this moment), apply fades, output in chunks; the last chunk is zero-padded (`:776-808`).
6. If exclusive, tell the group it finished (`:811`).
7. Silence for a new gap (formula above, with the current presence) (`:814-822`).

**Fades:** fade length `min(6.0, duration_seconds / 3)` seconds, `fade_samples = int(that * 44100)`,
computed once when the stream starts (`recording.py:653-656`). Fade in `sin(linspace(0, pi/2, n))`
on the first n samples, fade out `cos(...)` on the last n samples, each applied only if the
decoded audio is at least n samples long (`recording.py:783-789`).

### 3.5 Presence mode (built)

`PresenceMixingStream` wraps a loop (`recording.py:830-968`). The track fades in and out at
random.

- Start: active if `random() < presence` (`:896`); gain starts at 1 or 0 with no fade.
- Active time: `(30 + 90 * presence) s * uniform(0.7, 1.3)` (`:861-862`, `:873-877`).
- Silent time: `(90 - 70 * presence) s * uniform(0.7, 1.3)` (`:863-864`, `:878-882`).
- The countdown drops by 1024 per chunk; at or below 0 (and only while `0 < presence < 1`) the state flips and a new time is drawn (`:924-929`).
- Fade: 6.0 s = 264600 samples (`recording.py:188`, `:193`). Fade in `sin(p * pi/2)`, fade out `cos(p * pi/2)`, with `p = fade_position / 264600` measured at the start of the chunk. **The gain is constant across each chunk** (stepped every 1024 samples), not per sample (`:933-953`).
- Live presence changes: a change to `>= 1` fades in, `<= 0` fades out; changes between those values only affect the next drawn time (`:911-922`).
- The underlying loop keeps running while silent, so the file position advances.

### 3.6 Exclusive groups (built)

One `ExclusionGroupCoordinator` per group name per theme stream (`theme.py:113-127`).
The group name is the track's folder, or `"Exclusive"` for a top-level track with
`exclusive: true` (`recording.py:321-322`, `:377-383`). Different groups do not wait for each
other. Only sparse streams talk to the coordinator (`recording.py:632-637`, `:705-724`).

Rules (`recording.py:12-161`):

| Rule | Value | Code |
|---|---|---|
| Nothing in the group plays during the first 60 s after the stream starts | `INITIAL_DELAY = 60.0` | `recording.py:33`, `:66-67`, `:113-114` |
| Only one track of the group plays at a time | claim lasts the file's header duration | `:70-79`, `:91-92` |
| Gap after any track of the group finishes | `MIN_GAP_AFTER_EXCLUSIVE = 120.0` s | `:31`, `:75`, `:104` |
| The track that played last cannot play next, if the group has more than one registered track | strict "not the same twice in a row" | `:86-88`, `:131-133` |
| Wait when blocked | `get_wait_time()` (time to the end of the initial delay, or remaining play time + 120 s, or remaining gap) plus `uniform(0.5, 3.0)` s; if that is 0, `uniform(1.0, 3.0)` s; converted to whole chunks | `:137-156`, `recording.py:725-734` |

Each group track also keeps its own sparse gap after it plays (3.4 step 7), so a track's
`presence` still controls how often that track is picked.

The coordinator uses the wall clock (`time.time()`), not the sample count (`recording.py:42`, `:63`).

### 3.7 What is read live and what is fixed at stream creation

A theme stream (and its track streams) is created when a channel starts a theme or
crossfades to a new one (`core/channel.py:173-204`, `:299-341`). A preset change on the same
theme only swaps the override values in place (`core/channel.py:164-171`,
`core/session_manager.py:555-559`).

| Setting | When it applies |
|---|---|
| `volume` | Live. Loops: every decoded frame. Sparse: at the start of each play. |
| `muted` | Live, every chunk (`theme.py:138`). A muted stream is not advanced: it pauses and resumes where it stopped. |
| `presence` | Live for sparse gaps and for presence-mode timing. It does **not** change the resolved mode, and a presence wrapper is added only if `presence < 1` at creation. |
| `playback_mode`, `short_file_threshold` | Fixed at creation (mode resolution). |
| `seamless_loop` | Fixed at creation (stream class choice). |
| `exclusive` | Group membership fixed at creation (`theme.py:125-127`, `recording.py:636-637`). |
| Output gain (master volume) | Live, every chunk (`theme.py:144`). |

### 3.8 The mix (built)

`ThemeStream.iter_chunks` (`theme.py:135-147`) and `MixLevel.mix` (`mixing.py:49-70`):

1. Pull one chunk from every **unmuted** track stream, in track order. If none, use one silent chunk.
2. Stack as float32. `active` = number of rows whose peak `|x| > 100` (about -50 dBFS) (`mixing.py:22`, `:59`).
3. `target = 1 / sqrt(max(1, active))` (`mixing.py:28-30`).
4. One-pole smoothing per chunk: if `target < gain` use attack, else release (`mixing.py:61-62`):
   - `attack = 1 - exp(-(1024/44100) / 0.3) = 0.0744803` (`mixing.py:23`, `:52`)
   - `release = 1 - exp(-(1024/44100) / 3.0) = 0.0077101` (`mixing.py:24`, `:53`)
   - `new_gain = gain + (target - gain) * rate`
5. `ramp = linspace(gain, new_gain, 1024)` (float32, both ends included), then `gain = new_gain` (`mixing.py:65-66`). Initial `gain = 1.0` (`mixing.py:54`).
6. `mixed = sum(rows) * ramp * output_gain` (`mixing.py:68`).
7. Soft limiter (`mixing.py:33-43`): knee `k = 0.8 * 32767 = 26213.6`, headroom `h = 32767 - k`. For `|x| > k`: `sign(x) * (k + h * tanh((|x| - k) / h))`. Below the knee unchanged.
8. Clip to [-32768, 32767], cast to int16 with truncation (`mixing.py:70`).

**Output gain** is `device.master_volume`, default **6.0** (`device.py:59`, `theme.py:23`, `:144`).
The HA "Master Volume" number maps 0-100 % to 0-10 (`controls.py:62-68`), so the default is 60 %.
With one track sounding, any sample above `26213.6 / 6 = 4369` (about -17.5 dBFS) reaches the limiter.

### 3.9 Theme-to-theme crossfade on a channel (built)

When a playing channel changes theme (`core/channel.py:299-341`):

- A new theme stream is created with the channel's preset for the new theme (`:311-313`). Its mix gain starts at 1.0 and all its sparse initial delays and group 60 s delays start over.
- Length 3.0 s = 132300 samples (`core/channel.py:32-33`), equal power: old `cos(linspace(0, pi/2, 132300))`, new `sin(...)` (`:120-121`).
- Applied per sample, chunk by chunk, 130 chunks; the last chunk pads old with 0 and new with 1 (`core/channel.py:206-235`, `:320-330`).
- The sum is clipped and truncated to int16 with **no** limiter (`core/channel.py:234`).

### 3.10 Sources of randomness

All use Python's global `random` module, which is **never seeded** (`import random` inside
the generators, `recording.py:642`, `:851`):

| # | Where | Call | Distribution |
|---|---|---|---|
| 1 | Sparse gap jitter | `random.uniform(0.7, 1.3)` | uniform, `recording.py:673` |
| 2 | Sparse initial delay | `random.uniform(0.0, 1.0)` times a gap (which itself draws #1) | uniform, `recording.py:749` |
| 3 | Exclusive wait jitter | `random.uniform(0.5, 3.0)` | uniform, `recording.py:731` |
| 4 | Exclusive default wait | `random.uniform(1.0, 3.0)` | uniform, `recording.py:734` |
| 5 | Presence start state | `random.random() < presence` | uniform [0, 1), `recording.py:896` |
| 6 | Presence active time jitter | `random.uniform(0.7, 1.3)` | uniform, `recording.py:876` |
| 7 | Presence silent time jitter | `random.uniform(0.7, 1.3)` | uniform, `recording.py:881` |

Other non-deterministic inputs: the exclusive coordinator's wall clock (3.6), and new theme
IDs from `uuid.uuid4()` (`core/theme_metadata.py:80`, not audio).

Draw order: track streams are generators, so each draws its first values on its first
pulled chunk, and streams are pulled in track order (1.1) every chunk. All channels share
the one global generator, so draws interleave across channels and threads.

**Planned rule (not built):** each theme stream owns a `random.Random` instance that can be
seeded, passed to every track stream in track order, and used for all seven draws above, so
golden tests can compare Python and Kotlin output sample for sample. For the same reason the
group coordinator should count time in samples of that stream rather than wall-clock seconds.

---

## 4. Presets

### 4.1 Structure (built)

Inside `metadata.json` today (`presets.json` in the target, 1.4):

```json
"presets": {
  "slow_night": {
    "name": "Slow night",
    "is_default": true,
    "tracks": {
      "Fireplace":        { "volume": 1.0, "presence": 1.0,  "playback_mode": "continuous", "seamless_loop": false, "exclusive": false, "muted": false },
      "Lute/Lute song 1": { "volume": 0.6, "presence": 0.3,  "playback_mode": "sparse",     "seamless_loop": false, "exclusive": false, "muted": false }
    }
  }
}
```

- Preset ID: the name lower-cased with every run of non `[a-z0-9]` characters replaced by `_`, trimmed of `_`; on a clash `_1`, `_2`, ... is added (`api.py:1745-1762`).
- `is_default`: the first preset created becomes default (`api.py:1764`); setting a default clears the flag on the others (`api.py:1957-1992`); deleting the default makes the first remaining preset default (`api.py:1923-1929`). Imported presets are never default (`api.py:2082-2086`). Only one should have it; if several do, the first in file order wins (`core/session_manager.py:130-132`).
- `tracks`: track key -> the six TrackSettings fields (2.2). A saved preset lists every track with all six fields (`api.py:1632-1649`). Import drops keys that match no track and coerces types (`api.py:2038-2058`).

### 4.2 How a preset applies to a channel (built)

Each channel has its own preset layer; the theme's shared settings are not changed.

1. Which preset: the requested one if the theme has it; `""` means none; otherwise the theme's default preset; otherwise none. A preset never carries over to a different theme (`core/session_manager.py:119-133`, `:508-514`).
2. The preset's `tracks` become overrides with `preset_track_overrides` (`core/session_manager.py:135-144`, `recording.py:328-347`).
3. `TrackView` returns the override for a field if the preset lists that track, otherwise the theme's live value (`recording.py:350-375`).

Consequences:

- **Only listed tracks change.** A track not in the preset follows the theme's own settings from `metadata.json` `tracks`.
- **A listed track gets all six fields.** Missing fields are filled with defaults (`volume 1.0`, `presence 1.0`, `muted false`, `seamless_loop false`, `playback_mode auto`, `exclusive false`), not with the theme's values (`recording.py:339-346`).
- Switching preset on a playing channel changes `volume`, `presence` and `muted` at once, but `playback_mode`, `seamless_loop` and group membership only on the next stream creation (3.7).

The mixer editor's "load preset" is different: it copies the preset into the theme's own
settings and saves them to `metadata.json` (`api.py:1651-1691`, `:1814-1858`).

### 4.3 Generator rule

List **every track in every preset**, with all six fields and an explicit `playback_mode`
(`continuous` or `sparse`, never `auto`). Mark exactly one preset `"is_default": true`.

---

## 5. Planned, not built

Short list of agreed direction. Nothing here is in the code yet; field names may change.

- **Group rules** in `metadata.json` `"groups"`, keyed by group name: how often the group plays (a presence value), gap minimum-maximum in seconds, and mute.
- **Presets save group settings** next to track settings.
- **Sequences** in `presets.json` `"sequences"`: an ordered list of chosen presets; at the end `reverse`, `repeat` or `stop`; each step holds for a random time between hold minimum and maximum; changes ramp over a given number of seconds.
- **Preset changes glide** over a few seconds instead of switching at once.
- **Theme defaults** for hold and ramp times.
- **Seedable randomness** per stream (3.10).

---

## 6. Generator checklist

### 6.1 Files and names

- Track file names are the keys: choose short, readable names ("Fireplace", "Rain on roof"). Avoid two files with the same name and different extensions in one folder (same key).
- Do not start names with `.` or `_`. No folders deeper than one level.
- 44.1 kHz mono, `.wav`, `.flac`, `.ogg` or `.mp3`.
- Keep levels modest: with the default output gain of 6, one track's peaks above about -17.5 dBFS reach the limiter (3.8).

### 6.2 Beds and events

- **Beds** (rain, fire, room tone): one long seamless loop, 2-5 minutes, top level, `playback_mode: "continuous"`, `seamless_loop: false` (a 1.5 s crossfade hides a slightly imperfect loop point; set `true` only for a file cut to loop perfectly).
- **Events** (door, thunder, a lute song): several distinct versions as separate files in one group folder, for example `Thunder/Thunder 1.wav` ... `Thunder/Thunder 5.wav`. Group tracks always play sparse, one at a time, never the same version twice in a row (3.6). Set each version's `presence` to control how often it comes up.
- Single top-level events: `playback_mode: "sparse"`, set `presence`.

### 6.3 metadata.json

- Always write `id` (a new UUID4) and `name`; see quirk Q1.
- Write `short_file_threshold: 15.0`, an entry in `tracks` for every track with all six fields, and explicit `playback_mode`.
- Every track in every preset (4.3); one default preset.
- UTF-8 without BOM; escaping non-ASCII (`\uXXXX`) is safest (quirk Q12).
- Licence: if the theme carries source or licence info, add `attribution`. Fields used by the bundled themes (written by the Ambient Mixer importer, `plugins/builtin/ambient_mixer/plugin.py:310-324`, `:716-726`):

  | Field | Meaning |
  |---|---|
  | `source` | Source name, for example `"Ambient-Mixer.com"` |
  | `source_url` | Page the sounds came from |
  | `template_id` | Source's ID for the mix |
  | `license` | Licence name |
  | `license_url` | Licence URL |
  | `imported_date` | ISO 8601 UTC time |
  | `imported_by` | Tool that made the theme |

### 6.4 MANIFEST.json and ATTRIBUTION.md (optional)

The mixer never reads them. The importer reads `MANIFEST.json` only to fill `attribution`
when it is missing (`plugins/builtin/ambient_mixer/plugin.py:310-324`). Bundled themes
(`sonorium_addon/themes/*/MANIFEST.json`) use:

- `source`: `site`, `url`, `template_id`, `creator`, `harvested_at`
- `license`: `name`, `url`, `requires_attribution` (bool)
- `mix_name`, `category`
- `channels`: list of the original source's per-sound settings (`channel_num`, `name`, `audio_id`, `url`, `volume`, `balance`, `is_random`, `random_counter`, `random_unit`, `crossfade`, `mute`, `local_filename`, `file_hash`)

`ATTRIBUTION.md` is human-readable: source link, harvest time, licence with what it permits,
and per-file original name, source ID, default volume and balance. A generator should write
an `ATTRIBUTION.md` stating the generator, voice/sound model and licence terms, and may write
a `MANIFEST.json` with at least `source` and `license` in the shape above.

---

## 7. Version history

| | Spec v1 (released, up to 1.4.1) | Spec v2 (this document) |
|---|---|---|
| Layout | Flat folder; only top-level audio files are tracks | Top-level tracks plus one level of group folders |
| Track key | File stem | Path without extension: `"Fireplace"`, `"Lute/Lute song 1"` (v1 keys unchanged) |
| Exclusive | One `exclusive` flag per track; all flagged tracks share one group | Each group folder is its own group; the flag still forms the legacy group `"Exclusive"` |
| Presets | Inside `metadata.json` | `presets.json`, converted automatically (target) |
| Version marker | none | `"spec_version": 2` (target) |
| Group rules, sequences | none | planned (section 5) |

---

## Known quirks

Q1. **A `metadata.json` without `id` gets a different random ID on every scan** until
something saves it: `__post_init__` makes a new UUID (`core/theme_metadata.py:78-80`) but the
loader saves only when `name` is empty (`:200-202`). Meanwhile `device.py` and
`api.py:1179-1188` read `id` from the raw file and fall back to the sanitized folder name,
so the two disagree.

Q2. **Unknown fields are dropped on save** through the metadata manager (2.4), including the
planned `spec_version` and `groups`.

Q3. **Two paths write `metadata.json` directly**, bypassing the manager's cache
(`api.py:955-970`, `web/api_v2.py:1667-1702`). A later save through the manager writes its
cached copy and can undo those edits (for example a description change).

Q4. **`exclusive` only works in sparse mode.** A top-level track marked exclusive whose mode
resolves to continuous or presence (for example `auto` with `presence: 1.0`) ignores its
group and plays all the time (`recording.py:297-314`, coordinator used only in
`SparsePlaybackStream`).

Q5. **Group tracks ignore `playback_mode` and `exclusive`** from metadata and presets; both
are forced (`recording.py:363-370`). The exclusive and playback-mode API calls still save
values that have no effect.

Q6. **A group folder named `Exclusive`** shares its coordinator with legacy exclusive
top-level tracks (`recording.py:322`, `:377-383`, `theme.py:126`).

Q7. **Partial preset entries reset other fields to defaults**, not to the theme's values
(`recording.py:339-346`).

Q8. **Preset changes on a playing channel do not change `playback_mode`, `seamless_loop` or
group membership** until the theme is restarted (3.7).

Q9. **Muted streams pause** instead of running silently, so unmuting resumes a loop where it
stopped and a sparse track mid-gap continues its gap (`theme.py:138`).

Q10. **Presence fades step once per 1024-sample chunk** (`recording.py:933-953`). A live
presence change to 1 or 0 during a fade restarts the fade at position 0, which can jump the
gain (for example a fade-in at 0.7 restarts from `sin(0) = 0`) (`:916-922`).

Q11. **Crossfade loop timing drifts by one chunk**: after a crossfade `samples_played` is set
to 66560 and then 1024 more is added in the same pass (`recording.py:587`, `:595`), so the next
loop's crossfade starts one chunk (about 23 ms) early.

Q12. **Some readers use `read_text()` without an encoding** (`api.py:950`, `api.py:1183`,
`core/session_manager.py:112`, `web/api_v2.py:1672`). On a system whose default encoding is
not UTF-8 (Windows standalone), non-ASCII text such as an emoji `icon` can fail to load
unless written as `\uXXXX` escapes. `_write_theme_metadata` writes with `ensure_ascii`
(escaped) while the manager writes raw UTF-8.

Q13. **Theme crossfade chunks are not counted in real-time pacing.** `_do_crossfade_in_thread`
pushes about 130 chunks without adding to `audio_time` (`core/channel.py:299-341` vs `:281-288`),
and the broadcast buffer holds only 50 chunks (`core/channel.py:44`), so a slow listener can
miss part of the crossfade and the channel runs about 3 s ahead of the clock afterwards.

Q14. **Exclusive timing uses the wall clock** while sparse gaps count samples (3.6), so the
two clocks only agree because output is paced in real time.

Q15. **Sparse docstring numbers are wrong** (`recording.py:618-621` says about 165 s at
presence 0.5; the code gives about 16.5 min). The gap table in 3.4 is from the code.

Q16. **Hard-cut loops lose the tail** shorter than one chunk on each pass and do not flush the
resampler; the sparse decoder does not flush it either (`recording.py:401-432`, `:679-703`).

Q17. **Track order differs by OS**: `sorted()` on paths is case-insensitive on Windows and
case-sensitive (code point order) on Linux, which changes random-draw order (3.10). Porters
should match the Linux server: code point order.

Q18. **The standalone API inverts `seamless_loop`**: `web_api.py:1709` (the `seamless_loop`
route) and `:1805` (preset load) set `crossfade_enabled = seamless_loop`, the opposite of the
add-on (`api.py:501`). (`web_api.py:1691` is the separate `crossfade` route and is correct.)

Q19. **`SessionManager.apply_preset_to_theme` is unused** (`core/session_manager.py:152-196`);
channel presets go through `preset_overrides` only.
