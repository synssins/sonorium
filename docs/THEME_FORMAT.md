# Sonorium theme format, version 2

This document describes what a Sonorium theme folder contains and exactly how the
server turns it into sound. It is written for two readers:

- **Theme generator authors** (for example an ElevenLabs-based tool) who produce theme folders.
- **Mixer porters** (for example Android/Kotlin) who must reproduce the server's output.

Every behavioural rule cites the current Python code in `sonorium_addon/sonorium/`
as `file:line`. Line numbers are for commit `ad8e643` on branch `fixes` ("leave headroom
for MP3 encoding in the soft limiter", after "groups work like a mixer bus"). Where the
code and this document disagree, the code wins and this document has a bug.

**Words used here**

| Word | Meaning |
|---|---|
| Track | One audio file in a theme. |
| Version | One of several tracks that are variations of the same sound (for example three lute songs). |
| Group | A named exclusive group: a subfolder of a theme. Only one of its tracks plays at a time, and the group works like a mixer bus over its tracks (3.6). |
| Preset | A saved set of track (and optionally group) settings for one theme. |
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
    metadata.json              <- theme info, track settings, group settings
    presets.json               <- presets (see 1.4)
    metadata.json.pre-presets.bak  <- written once by the 1.0 -> 2.0 conversion (1.4)
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
- Audio files at the top of the theme folder are tracks (`theme_files.py:46`).
- Each subfolder holding at least one audio file is a **group**; its audio files are tracks in that group (`theme_files.py:34-40`, `:49-50`).
- One level deep only. Files in sub-subfolders are not found (`theme_files.py:40`, `:50` only list the group folder itself).
- Folders and files whose name starts with `.` or `_` are ignored (`theme_files.py:30-31`, `:37`, `:46`, `:50`).
- Track order: top-level files sorted by file name, then each group (groups sorted by folder name) with its files sorted by file name (`theme_files.py:43-51`). The sort key is the plain name string (`theme_files.py:25-27`), so the order is code point order (case-sensitive) on every OS. This order matters for porters (see 3.10).
- A folder is a theme only if `theme_audio_files()` finds at least one track (`core/theme_metadata.py:387-397`, `device.py:105-113`, `api.py:1168-1178`).

### 1.2 Track keys (built)

A track's key is its path inside the theme without the extension, with `/` between group and
file name: `"Fireplace"`, `"Lute/Lute song 1"` (`theme_files.py:64-67`). The key is used in
`metadata.json` `tracks`, in presets, and in the API (`recording.py:213-221`, `:226-228`). The
UI shows only the part after the last `/` (`theme_files.py:70-72`).

Themes without subfolders keep exactly the keys they had in version 1 (the file stem).

### 1.3 metadata.json (built)

Holds theme info, per-track settings, `spec_version` and group settings. Since Themes 2.0
it no longer holds presets (they are in `presets.json`). Full schema in section 2.

### 1.4 presets.json and the automatic conversion (built)

Presets are stored in `presets.json`:

```json
{ "presets": { "<preset_id>": { ... } } }
```

Other top-level keys in `presets.json` (for example a later `"sequences"`, section 5) are kept
untouched when presets are saved (`core/theme_presets.py:135-144`). Every write of
`metadata.json` or `presets.json` is atomic: a temp file is written, read back and compared,
then renamed over the original (`core/theme_presets.py:60-75`).

**Automatic conversion** happens whenever a theme folder is loaded
(`core/theme_metadata.py:259-318` `load_theme_folder`, called by the scan at `:411-420`):

- A `metadata.json` is version 1 if `spec_version` is missing, not an integer, or below 2 (`core/theme_metadata.py:179-182`).
- Presets found in `metadata.json` `"presets"` are merged with `presets.json`; on the same preset ID, `presets.json` wins (`:282-291`).
- `presets.json` is written and read back to check it (`:248-256`, `:300-304`).
- `metadata.json` is copied once to `metadata.json.pre-presets.bak` (never overwritten if it exists) (`:169`, `:305-306`).
- `metadata.json` is rewritten without `"presets"`, with `spec_version: 2` and, if any track has `exclusive: true`, a legacy group entry `"Exclusive": {"legacy_exclusive": true}` (the tracks keep their flag, so playback does not change) (`:185-193`, `:293-296`, `:307-308`, `:124-141`).
- A `metadata.json` without `id` is also rewritten, so the new ID is kept (`:296`).
- The conversion is idempotent. If the folder cannot be written, the files are used as they are (presets still read from `metadata.json` in memory) and a warning is logged once per theme (`:309-310`, `:204-209`).

**Broken JSON files** (not UTF-8, not valid JSON, not a JSON object, `"presets"` not an
object, or content the dataclasses reject) raise `BrokenJsonError`
(`core/theme_presets.py:42-57`, `:100-107`; `core/theme_metadata.py:219-222`). Then:

- The broken file is renamed to `<name>.broken-<YYYYMMDD-HHMMSS>` (`-1`, `-2`, ... on a clash) (`core/theme_presets.py:78-92`, `core/theme_metadata.py:196-201`).
- A broken `metadata.json` is rebuilt with defaults: every track with default settings, folder name as name, and the old `id` if a regex can still find it in the broken text (`core/theme_metadata.py:212-226`, `:172-176`, `core/theme_presets.py:147-154`).
- A broken `presets.json` is refilled from the `"presets"` in `metadata.json.pre-presets.bak` if that backup has any, otherwise left empty (`core/theme_metadata.py:229-245`).
- Each problem is added to `metadata.problems` (not saved to disk) and shown in the theme list as `"problems"` (`core/theme_metadata.py:105`, `:317`, `api.py:1085`).
- Any other load failure gives default metadata for that theme only, so one bad theme never stops the others (`core/theme_metadata.py:411-420`).

Theme export always writes the 2.0 layout (`metadata.json` without presets plus `presets.json`)
without changing the folder (`core/theme_metadata.py:338-353`, `web/api_v2.py:1773-1778`).

**Generator rule:** write `presets.json` and set `"spec_version": 2` in `metadata.json`. A theme
with presets inside `metadata.json` (version 1) still works: the server converts it on first load.

### 1.5 Per-user presets (planned, not built)

The plan includes per-user preset files. They are kept on the server, not in the theme
folder, and a generator never writes them.

---

## 2. metadata.json schema

Defined by the dataclasses in `core/theme_metadata.py`. Loaded by `load_theme_folder`
(`core/theme_metadata.py:259-318`) and written by `save_theme_folder` (`:321-335`) as UTF-8
JSON, 2-space indent, non-ASCII kept as-is (`core/theme_presets.py:60-75`). `presets.json` is
written first, so a failed save never loses presets.

### 2.1 Top-level fields

| Field | Type | Default | Meaning | Code |
|---|---|---|---|---|
| `spec_version` | integer | `2` | Format version. Missing or below 2 means version 1 and triggers the conversion (1.4). | `core/theme_metadata.py:97`, `:179-182` |
| `id` | string | new random UUID4 | Permanent theme ID. Folder renames keep it. A missing `id` is generated and saved on load. | `:71`, `:107-110`, `:296` |
| `name` | string | folder name | Display name. If empty on load, set to the folder name and saved. | `:74`, `:297-299` |
| `description` | string | `""` | Free text. | `:77` |
| `icon` | string | `""` | An emoji; empty means the UI picks one. | `:78` |
| `is_favorite` | bool | `false` | User favourite flag. | `:81` |
| `categories` | list of string | `[]` | Category names. | `:82` |
| `short_file_threshold` | number (seconds) | `15.0` | Tracks shorter than this count as "short" when `playback_mode` is `auto` (3.2), and for grouped tracks (3.6). Must be `>= 0` (`web/api_v2.py:1688-1691`). | `:85`, `recording.py:255-257`, `:288-293` |
| `tracks` | object: key -> TrackSettings | `{}` | Per-track settings, keyed by track key (1.2). | `:88`, `:112-116` |
| `groups` | object: group name -> group settings | `{}` | Group master settings (2.5). | `:102` |
| `attribution` | object or absent | absent | Source and licence info (6.3). Written only when non-empty. | `:94`, `:139-140` |
| `presets` | object | absent | **Version 1 only.** Moved to `presets.json` by the conversion (1.4); never written back. | `:91`, `:124-141` |

### 2.2 TrackSettings (one entry in `tracks`)

| Field | Type | Default | Meaning | Code |
|---|---|---|---|---|
| `volume` | number 0.0-1.0 | `1.0` | Amplitude multiplier for the track. The API clamps to 0-1 (`api.py:1471`); the loader does not. | `core/theme_metadata.py:46` |
| `presence` | number 0.0-1.0 | `1.0` | How often the track is heard (meaning depends on mode, 3.3-3.5). API clamps to 0-1 (`api.py:1397`). | `:44` |
| `muted` | bool | `false` | Track is left out of the mix. | `:45` |
| `playback_mode` | string | `"auto"` | `auto`, `continuous`, `sparse` or `presence` (3.2). Unknown strings become `auto` on apply (`api.py:503-506`). | `:47` |
| `seamless_loop` | bool | `false` | `true` turns the loop **crossfade off** (hard cut at the loop point). | `:48`, `api.py:507` |
| `exclusive` | bool | `false` | Version 1 way to put a top-level track in the single legacy group "Exclusive" (3.6). Ignored for tracks in a group folder (always exclusive). | `:49`, `recording.py:332`, `:452-453`, `:467-473` |

A track with no entry in `tracks` uses all defaults (`api.py:489-497`, `:1249-1255`).
Entries whose key matches no track are kept in the file but ignored (the bundled Tavern
theme has stale `"...mp3"` keys from an older naming scheme).

### 2.3 How the server applies metadata

- At start-up, each track's live settings are copied from `tracks` and the theme's `groups`
  are attached to the theme (`api.py:457-508`, groups at `:485`), mapping `muted` ->
  `is_enabled = not muted` and `seamless_loop` -> `crossfade_enabled = not seamless_loop`.
  A theme refresh copies `tracks` again (`api.py:1218-1255`) but not `groups` (quirk Q18).
- Each per-track API call changes the live value and saves that one field
  (`api.py:984-1005` `_save_track_setting_to_metadata`; for example presence `api.py:1410-1413`).
- Each group API call changes the live group settings and saves them (`api.py:1607-1647`, 2.5).
- Theme info edits from the v2 web API read and write the raw JSON
  (`web/api_v2.py:1652-1706`).

### 2.4 Fields kept and dropped on save (built behaviour; important)

`ThemeMetadata.from_dict` keeps only `id, name, description, icon, is_favorite, categories,
short_file_threshold, presets, attribution, spec_version, groups` and `tracks`
(`core/theme_metadata.py:143-164`); `TrackSettings.from_dict` keeps only its six fields
(`:54-58`); `to_dict` writes `spec_version`, `id`, `name`, `description`, `icon`,
`is_favorite`, `categories`, `short_file_threshold`, `tracks`, `groups` and (when non-empty)
`attribution` (`:124-141`). So `spec_version` and `groups` survive every save, but any
**other** top-level field, and any unknown field inside a track entry, is **removed** by the
first save through the metadata manager (any track or group setting change, preset
create/update/delete) and by the conversion. Each group's settings object is stored as-is,
so unknown keys inside a group entry survive. Inside `presets.json`, each preset dict is kept
as-is, so unknown preset fields survive, and other top-level keys of `presets.json` survive too.

### 2.5 groups (built)

Keyed by group name (the group folder name, 1.1). Every field is optional; a missing field
means "no change" (100 %, not muted, default gap).

```json
"groups": {
  "Lute":      { "volume": 0.8, "presence": 0.5, "muted": false, "gap_min": 60, "gap_max": 240 },
  "Exclusive": { "legacy_exclusive": true }
}
```

| Field | Type | Meaning | Code |
|---|---|---|---|
| `volume` | number 0.0-1.0 | Group master volume. **Multiplies** each grouped track's own volume (track 50 % x group 50 % = 25 %). | `recording.py:338`, `:454-456` |
| `presence` | number 0.0-1.0 | Group master presence. **Multiplies** each grouped track's own presence. | same |
| `muted` | bool | `true` mutes every track in the group. | `:338`, `:457-459` |
| `gap_min`, `gap_max` | number, seconds | The group's random rest after any of its tracks finishes: uniform in `[gap_min, gap_max]` each time. Never scaled by presence. If only one is set, it is used for both; if reversed, they are swapped. Without either, the rest is 120 s (3.6). | `:354-363`, `:36-39` |
| `legacy_exclusive` | bool | Written by the conversion for version 1 themes with `exclusive` tracks (1.4). Read by nothing. | `core/theme_metadata.py:185-193` |

API (`api.py:151-152`):

- `GET /api/themes/{id}/groups` lists the theme's **folder** groups, sorted by name, each with
  `name`, `settings` (only the five keys above) and `tracks` (track keys) (`api.py:1581-1605`).
- `PUT /api/themes/{id}/groups/{group}` changes any of `presence`, `volume` (clamped 0-1),
  `muted`, `gap_min`, `gap_max` (clamped `>= 0`); `null` removes a key; other keys give 400; a
  group that is not a folder group gives 404 (`api.py:1607-1647`). Volume, presence and mute
  apply live to playing channels; the gap applies the next time the theme stream starts
  (`api.py:1645`, `theme.py:130-131`).

---

## 3. Track settings and exact mixer behaviour

### 3.1 Signal format (built)

| Item | Value | Code |
|---|---|---|
| Sample rate | 44100 Hz | `recording.py:199` |
| Channels | mono. Every file is decoded and resampled to s16 mono 44100 Hz by ffmpeg's resampler (PyAV `AudioResampler`), so stereo is down-mixed by libswresample defaults | `recording.py:488`, `:545`, `:771` |
| Chunk size | 1024 samples (about 23.2 ms) for every stream and the mix | `recording.py:484`, `:537`, `:719`, `:932`; `theme.py:123` |
| Per-track chunk type | int16, shape (1, 1024). Float results are clipped to [-32768, 32767] and cast with truncation toward zero | `recording.py:682`, `:891`, `:1088` |
| Stream output | MP3, 128 kbit/s, 44100 Hz, mono | `core/channel.py:422-441`, `theme.py:156-158` |
| Pacing | real time: the generator sleeps when ahead of the wall clock | `core/channel.py:281-288` |

Generators should deliver 44.1 kHz mono files so no resampling or down-mix happens.

### 3.2 playback_mode and how "auto" resolves (built)

UI labels (the stored values don't change): `auto` = **Auto**, `continuous` = **Background**,
`sparse` = **Intermittent**, `presence` = **Ebb & Flow**. `exclusive` has no label of its own: groups are how tracks take turns.

Resolved once, when the track's stream is created (`recording.py:295-304`, `:306-324`), with
the values the channel hears (preset and group applied, 4.2 and 3.6):

| playback_mode | Track shorter than `short_file_threshold` | Track at or above threshold |
|---|---|---|
| `continuous` | Loop (3.3) | Loop (3.3) |
| `sparse` | Sparse (3.4) | Sparse (3.4) |
| `presence` | Loop, faded in and out (3.5) if `presence < 1`; plain loop if `presence >= 1` | same |
| `auto`, `presence < 1.0` | Sparse | Presence |
| `auto`, `presence >= 1.0` | Continuous | Continuous |

"Shorter" is `duration_seconds < threshold`, where duration comes from the container
header, or a full decode if the header has none, or 60 s if the file cannot be opened
(`recording.py:231-257`).

Then (`recording.py:310-324`):

- Sparse -> `SparsePlaybackStream`.
- Otherwise `CrossfadeRecordingStream` if `crossfade_enabled` (that is `seamless_loop == false`), else `RecordingThemeStream` (hard-cut loop).
- Presence mode wraps that loop in `PresenceMixingStream` if `presence < 1.0`, **or** if the track is exclusive and has a group coordinator (then always, even at 100 %, so it waits for its group's turn) (`recording.py:319-322`).

**Tracks in a group folder** (`TrackView._in_group`, `recording.py:450-465`):

| Setting | What the track gets |
|---|---|
| `exclusive` | always `true` |
| `playback_mode` `auto` or `continuous` | `sparse` if the file is shorter than `short_file_threshold`, else `presence`. Never a continuous loop. |
| `playback_mode` `sparse` or `presence` | as set |
| `volume`, `presence` | track value x group master (3.6) |
| `muted` | muted if the track or the group is muted |

So a grouped long track in presence mode always gets the presence wrapper with the group's
coordinator.

### 3.3 Continuous looping (built)

**With crossfade** (`seamless_loop: false`, `CrossfadeRecordingStream`, `recording.py:530-699`):

- Crossfade length: 1.5 s = 66150 samples (`recording.py:195`, `:201`).
- Curve: equal power. Outgoing `cos(linspace(0, pi/2, 66150))`, incoming `sin(linspace(0, pi/2, 66150))`, float32 (`recording.py:592-593`).
- Start point: when `samples_played >= duration_samples - 66150`, checked at chunk boundaries, so the fade begins on the first chunk boundary at or after that point (`recording.py:577`, `:621`). A second decoder of the same file starts from sample 0.
- The fade runs per sample inside each chunk; the last fading chunk pads the outgoing curve with 0 and the incoming with 1 (`recording.py:650-665`).
- If the file ends before the fade finishes (header duration was long, or the file is shorter than 1.5 s), the outgoing side is filled with silence and the real length replaces the header length (`recording.py:605-618`).
- Each decoder flushes the resampler at the end of the file (`recording.py:564-566`).
- After 65 chunks (66560 samples) the incoming decoder becomes the current one (`recording.py:670-679`).
- `volume` multiplies each decoded frame, read live (`recording.py:552-557`).
- Each output chunk is clipped and truncated to int16 (`recording.py:682`).

**Without crossfade** (`seamless_loop: true`, `RecordingThemeStream`, `recording.py:480-527`):
the file is decoded start to end, then reopened. No fade. `volume` multiplies each frame
(int16 multiply then truncation, `recording.py:506`). Leftover samples shorter than one chunk
at the end of each pass are dropped (`recording.py:499`, buffer reset per pass), and the
resampler is not flushed. Use this only for files cut to loop perfectly.

### 3.4 Sparse playback (built)

`SparsePlaybackStream` (`recording.py:702-917`). Plays the whole file once, then silence,
then again.

**Gap formula** (`recording.py:748-767`):

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

For a grouped track, `presence` here is the effective value (track x group master).

Constants: `SPARSE_MIN_INTERVAL = 180`, `SPARSE_MAX_INTERVAL = 1800`,
`SPARSE_INTERVAL_VARIANCE = 0.30` (`recording.py:189-192`).

**Loop of one sparse stream** (`recording.py:830-911`):

1. Read `presence` live (`:832`).
2. First pass only: initial delay `int(gap_samples * uniform(0.0, 1.0))` samples, a fresh gap draw times a second draw, rounded down to whole chunks of silence (`:836-845`).
3. If exclusive and the group is blocked: silence for the wait (3.6), then back to step 1 (`:848-854`).
4. If exclusive, try to claim the group; if refused, silence for the wait, back to step 1 (`:857-863`).
5. Decode the whole file, multiply by `volume` (read at this moment), apply fades, output in chunks; the last chunk is zero-padded (`:866-898`).
6. If exclusive, tell the group it finished (`:901`).
7. Silence for a new gap (formula above, with the current presence) (`:904-911`).

**Fades:** fade length `min(6.0, duration_seconds / 3)` seconds, `fade_samples = int(that * 44100)`,
computed once when the stream starts (`recording.py:743-746`). Fade in `sin(linspace(0, pi/2, n))`
on the first n samples, fade out `cos(...)` on the last n samples, each applied only if the
decoded audio is at least n samples long (`recording.py:873-878`).

### 3.5 Presence mode (built)

`PresenceMixingStream` wraps a loop (`recording.py:920-1097`). The track fades in and out at
random. Active and silent times (`:958-961`, `:963-979`):

- Active time: `(30 + 90 * presence) s * uniform(0.7, 1.3)`; infinite at `presence >= 1`.
- Silent time: `(90 - 70 * presence) s * uniform(0.7, 1.3)`; infinite at `presence <= 0`.

**Not in a group** (no coordinator):

- Start: active if `random() < presence` (`:993`); gain starts at 1 or 0 with no fade. At `presence >= 1` it starts active, at `<= 0` silent (`:982-995`).
- The countdown drops by 1024 per chunk; at or below 0 (and only while `0 < presence < 1`) the state flips and a new time is drawn (`:1031`, `:1051-1055`).
- Live presence changes: a change to `>= 1` fades in, `<= 0` fades out; changes between those values only affect the next drawn time (`:1016-1028`).

**In a group** (coordinator passed, `recording.py:937-943`, `:997-1005`, `:1032-1050`):

- Starts silent, whatever `presence` is. The start-state draw (`:993`) still happens and is discarded.
- Every `RETRY_SECONDS = 2.0` s while silent and `presence > 0`, it asks the group for its turn (`:935`, `:998`, `:1032-1041`). The claim it asks for lasts the active time plus one 6 s fade, or 1e9 s ("forever") at `presence >= 1` (`:1035-1036`).
- With the turn it fades in and stays for the active time; at `presence >= 1` it never lets go (`:1038-1039`).
- Then it fades out and hands the turn back only when the fade-out has finished; the group's gap (3.6) starts at that moment (`:1042-1047`, `:1078-1080`).
- A live change to `presence <= 0` while it has the turn fades it out and hands the turn back the same way (`:1048-1050`). Other live presence changes only affect the next active time.
- The silent-time formula is not used: the rest between plays comes from the group (its gap and waiting for other tracks).

Both cases:

- Fade: 6.0 s = 264600 samples (`recording.py:197`, `:202`). Fade in `sin(p * pi/2)`, fade out `cos(p * pi/2)`, with `p = fade_position / 264600` measured at the start of the chunk. **The gain is constant across each chunk** (stepped every 1024 samples), not per sample (`:1059-1082`).
- The underlying loop keeps running while silent, so the file position advances.

### 3.6 Groups: exclusive turns and mixer bus (built)

**One coordinator per group.** Each theme stream creates one `ExclusionGroupCoordinator` per
group name, with that group's gap range (`theme.py:125-133`). The group name is the track's
folder, or `"Exclusive"` for a top-level track with `exclusive: true`
(`recording.py:332`, `:467-473`). Different groups do not wait for each other. Sparse streams
(`recording.py:721-727`, `:795-824`) and presence wrappers (`:937-943`, `:1032-1050`) talk to
the coordinator; continuous loops do not.

Rules (`recording.py:13-170`):

| Rule | Value | Code |
|---|---|---|
| Nothing in the group plays during the first 60 s after the stream starts | `INITIAL_DELAY = 60.0` | `recording.py:34`, `:75-76`, `:122-123` |
| Only one track of the group plays at a time | a claim lasts the time the track asked for (sparse: the file's header duration; presence: 3.5), or until it reports it finished | `:79-88`, `:100-101`, `:105-114` |
| Gap after any track of the group finishes | `uniform(gap_min, gap_max)` s drawn each time if the group sets a gap (2.5), else `MIN_GAP_AFTER_EXCLUSIVE = 120.0` s | `:32`, `:36-39`, `:84`, `:113`, `:129` |
| The track that played last cannot play next, if the group has more than one registered track | strict "not the same twice in a row" | `:95-97`, `:140-142` |
| Sparse wait when blocked | `get_wait_time()` (time to the end of the initial delay, or remaining play time + `gap_min` (120 s without a gap), or remaining gap) plus `uniform(0.5, 3.0)` s; if that is 0, `uniform(1.0, 3.0)` s; converted to whole chunks | `:146-165`, `recording.py:815-824` |
| Presence wait when refused | ask again every 2 s | `:1040-1041` |

Each sparse group track also keeps its own sparse gap after it plays (3.4 step 7), so a
track's effective `presence` still controls how often that track is picked.

The coordinator uses the wall clock (`time.time()`), not the sample count (`recording.py:51`, `:72`).

**Mixer bus.** A grouped track's settings are worked out by `TrackView`
(`recording.py:410-477`) in this order:

1. The track's own value (`_track_value`, `:430-436`): the channel preset's value for that track and field, if the preset saved it; otherwise the track's theme value (`metadata.json` `tracks`).
2. Then the group master (`_group_master`, `:438-448`): the channel preset's value for the group and field (`"groups"` in the preset, 4.1), otherwise `metadata.json` `groups[name]`, otherwise none.
3. Combined (`_in_group`, `:450-465`): `volume` and `presence` are multiplied by the master (`GROUP_TRACK_SETTINGS`, `:338`); the track is muted if it or the group is muted; no master means no change.

| | Track 100 % | Track 50 % |
|---|---|---|
| Group 100 % (or not set) | 100 % | 50 % |
| Group 50 % | 50 % | 25 % |

The gap (`gap_min`, `gap_max`) is the group's own rest in seconds and is never multiplied.
Presets can set group `volume`, `presence` and `muted`, not the gap
(`recording.py:399-407`). Group masters apply only to tracks in a group **folder**; the legacy
`"Exclusive"` group shares turns but has no master controls (quirk Q22).

### 3.7 What is read live and what is fixed at stream creation

A theme stream (and its track streams) is created when a channel starts a theme or
crossfades to a new one (`core/channel.py:173-204`, `:299-341`). A preset change on the same
theme only swaps the override values in place (`core/channel.py:164-171`,
`core/session_manager.py:558-562`).

| Setting | When it applies |
|---|---|
| `volume` (track and group) | Live. Loops: every decoded frame. Sparse: at the start of each play. |
| `muted` (track and group) | Live, every chunk (`theme.py:144`). A muted stream is not advanced: it pauses and resumes where it stopped. |
| `presence` (track and group) | Live for sparse gaps and for presence-mode timing (in a group: the next active time). It does **not** change the resolved mode, and outside a group a presence wrapper is added only if `presence < 1` at creation. |
| `playback_mode`, `short_file_threshold` | Fixed at creation (mode resolution). |
| `seamless_loop` | Fixed at creation (stream class choice). |
| `exclusive`, group membership | Fixed at creation (`theme.py:126-133`, `recording.py:725-727`, `:940-943`). |
| Group `gap_min`, `gap_max` | Fixed at creation (`theme.py:130-131`). |
| Output gain (master volume) | Live, every chunk (`theme.py:150`). |

### 3.8 The mix (built)

`ThemeStream.iter_chunks` (`theme.py:141-153`) and `MixLevel.mix` (`mixing.py:58-72`):

1. Pull one chunk from every **unmuted** track stream, in track order. If none, use one silent chunk.
2. Stack as float32. `active` = number of rows whose peak `|x| > 100` (about -50 dBFS) (`mixing.py:23`, `:61`).
3. `target = 1 / sqrt(max(1, active))` (`mixing.py:30-32`).
4. One-pole smoothing per chunk: if `target < gain` use attack, else release (`mixing.py:63-64`):
   - `attack = 1 - exp(-(1024/44100) / 0.3) = 0.0744803` (`mixing.py:24`, `:54`)
   - `release = 1 - exp(-(1024/44100) / 3.0) = 0.0077101` (`mixing.py:25`, `:55`)
   - `new_gain = gain + (target - gain) * rate`
5. `ramp = linspace(gain, new_gain, 1024)` (float32, both ends included), then `gain = new_gain` (`mixing.py:67-68`). Initial `gain = 1.0` (`mixing.py:56`).
6. `mixed = sum(rows) * ramp * output_gain` (`mixing.py:70`).
7. Soft limiter (`mixing.py:35-45`): `LIMIT_KNEE = 0.7`, `LIMIT_CEILING = 0.89` (about -1 dBFS) (`mixing.py:22`, `:26-27`). Knee `k = 0.7 * 32767 = 22936.9`, headroom `h = (0.89 - 0.7) * 32767 = 6225.73`. For `|x| > k`: `sign(x) * (k + h * tanh((|x| - k) / h))`, so peaks bend toward `k + h = 29162.6` and never reach it. Below the knee unchanged. The headroom leaves room for the MP3 encoder's overshoot.
8. Clip to [-32768, 32767], cast to int16 with truncation (`mixing.py:72`).

**Output gain** is `device.master_volume`, default **6.0** (`device.py:59`, `theme.py:23`, `:150`).
The HA "Master Volume" number maps 0-100 % to 0-10 (`controls.py:62-68`), so the default is 60 %.
With one track sounding, any sample above `22936.9 / 6 = 3823` (about -18.7 dBFS) reaches the limiter.

### 3.9 Theme-to-theme crossfade on a channel (built)

When a playing channel changes theme (`core/channel.py:299-341`):

- A new theme stream is created with the channel's preset for the new theme (`:311-313`). Its mix gain starts at 1.0 and all its sparse initial delays and group 60 s delays start over.
- Length 3.0 s = 132300 samples (`core/channel.py:32-33`), equal power: old `cos(linspace(0, pi/2, 132300))`, new `sin(...)` (`:120-121`).
- Applied per sample, chunk by chunk, 130 chunks; the last chunk pads old with 0 and new with 1 (`core/channel.py:206-235`, `:320-330`).
- The sum is clipped and truncated to int16 with **no** limiter (`core/channel.py:234`).

### 3.10 Sources of randomness

All use Python's global `random` module, which is **never seeded** (`import random` at the
top of `recording.py:2` and inside the generators, `recording.py:732`, `:948`):

| # | Where | Call | Distribution |
|---|---|---|---|
| 1 | Sparse gap jitter | `random.uniform(0.7, 1.3)` | uniform, `recording.py:763` |
| 2 | Sparse initial delay | `random.uniform(0.0, 1.0)` times a gap (which itself draws #1) | uniform, `recording.py:839` |
| 3 | Exclusive wait jitter | `random.uniform(0.5, 3.0)` | uniform, `recording.py:821` |
| 4 | Exclusive default wait | `random.uniform(1.0, 3.0)` | uniform, `recording.py:824` |
| 5 | Presence start state | `random.random() < presence` (drawn and discarded in a group) | uniform [0, 1), `recording.py:993` |
| 6 | Presence active time jitter | `random.uniform(0.7, 1.3)` | uniform, `recording.py:973` |
| 7 | Presence silent time jitter | `random.uniform(0.7, 1.3)` | uniform, `recording.py:978` |
| 8 | Group gap | `random.uniform(gap_min, gap_max)`, only if the group sets a gap | uniform, `recording.py:38` |

Other non-deterministic inputs: the group coordinator's wall clock (3.6), and new theme
IDs from `uuid.uuid4()` (`core/theme_metadata.py:110`, not audio).

Draw order: track streams are generators, so each draws its first values on its first
pulled chunk, and streams are pulled in track order (1.1) every chunk. The group gap (#8) is
drawn when a track finishes (from inside whichever stream reports it). All channels share
the one global generator, so draws interleave across channels and threads.

**Planned rule (not built):** each theme stream owns a `random.Random` instance that can be
seeded, passed to every track stream in track order and to its group coordinators, and used
for all eight draws above, so golden tests can compare Python and Kotlin output sample for
sample. For the same reason the group coordinator should count time in samples of that
stream rather than wall-clock seconds.

---

## 4. Presets

### 4.1 Structure (built)

In `presets.json` (inside `metadata.json` in version 1 themes, converted on load, 1.4):

```json
{
  "presets": {
    "slow_night": {
      "name": "Slow night",
      "is_default": true,
      "tracks": {
        "Fireplace":        { "volume": 1.0, "presence": 1.0,  "playback_mode": "continuous", "seamless_loop": false, "exclusive": false, "muted": false },
        "Lute/Lute song 1": { "volume": 0.6, "presence": 0.3,  "playback_mode": "sparse",     "seamless_loop": false, "exclusive": false, "muted": false }
      },
      "groups": {
        "Lute": { "volume": 0.8, "presence": 0.5, "muted": false }
      }
    }
  }
}
```

- Preset ID: the name lower-cased with every run of non `[a-z0-9]` characters replaced by `_`, trimmed of `_`; on a clash `_1`, `_2`, ... is added (`api.py:1834-1836`, `:1845-1850`).
- `is_default`: the first preset created becomes default (`api.py:1853`); setting a default clears the flag on the others (`api.py:2046-2081`); deleting the default makes the first remaining preset default (`api.py:2012-2018`). Imported presets are never default (`api.py:2173`, `:2206`). Only one should have it; if several do, the first in file order wins (`core/session_manager.py:133-135`).
- `tracks`: track key -> any of the six TrackSettings fields (2.2). A preset saved from the UI lists every track with all six fields (`api.py:1721-1737`). Import drops keys that match no track, fills missing fields with defaults and coerces types (`api.py:2128-2147`).
- `groups` (optional): group name -> any of `volume`, `presence`, `muted` (2.5); other keys (including the gap) are ignored (`recording.py:399-407`). Saving a preset (create or update) captures the groups' current master settings, and loading one in the theme editor applies them.

### 4.2 How a preset applies to a channel (built)

Each channel has its own preset layer; the theme's shared settings are not changed.

1. Which preset: the requested one if the theme has it; `""` means none; otherwise the theme's default preset; otherwise none. A preset never carries over to a different theme (`core/session_manager.py:122-136`, `:511-516`).
2. The preset's `tracks` become overrides with `preset_track_overrides`, and its `groups` with `preset_group_overrides` (stored under `"@group:<name>"`) (`core/session_manager.py:138-147`, `recording.py:339`, `:369-407`).
3. `TrackView` returns the preset's value for a field if the preset saved that field for that track, otherwise the theme's live value; for grouped tracks it then applies the group master (3.6) (`recording.py:410-477`).

Consequences:

- **Only saved fields change.** A track not in the preset, or a field the preset leaves out, follows the theme's own setting from `metadata.json` `tracks` (`recording.py:374-395`).
- **Group masters per field:** a preset that sets only a group's `presence` still uses `metadata.json` `groups[name].volume` (`recording.py:440-447`).
- Switching preset on a playing channel changes `volume`, `presence` and `muted` (track and group) at once, but `playback_mode`, `seamless_loop` and group membership only on the next stream creation (3.7).

The mixer editor's "load preset" is different: it copies the preset's `tracks` into the
theme's own settings, filling missing fields with defaults, and saves them to
`metadata.json`; it also applies the preset's `groups` to the theme's group master settings.

### 4.3 Generator rule

List **every track in every preset**, with all six fields and an explicit `playback_mode`
(`continuous`, `sparse` or `presence`, never `auto`), so the preset does not depend on the
theme's current values. Add `groups` for any group whose master level should differ in that
preset. Mark exactly one preset `"is_default": true`.

---

## 5. Planned, not built

Short list of agreed direction. Nothing here is in the code yet; field names may change.

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
- Keep levels modest: with the default output gain of 6, one track's peaks above about -18.7 dBFS reach the limiter (3.8).

### 6.2 Beds and events

- **Beds** (rain, fire, room tone): one long seamless loop, 2-5 minutes, top level, `playback_mode: "continuous"`, `seamless_loop: false` (a 1.5 s crossfade hides a slightly imperfect loop point; set `true` only for a file cut to loop perfectly). Never put a bed in a group folder: grouped tracks are never continuous (3.2).
- **Events** (door, thunder, a lute song): several distinct versions as separate files in one group folder, for example `Thunder/Thunder 1.wav` ... `Thunder/Thunder 5.wav`. Grouped tracks play one at a time, never the same version twice in a row (3.6); short files play sparse, long ones fade in and out. Set each version's `presence` to control how often it comes up, the group's `presence` and `volume` to scale the whole group, and `gap_min`/`gap_max` for the rest between plays. Keep grouped long tracks below 100 % effective presence (quirk Q19).
- Single top-level events: `playback_mode: "sparse"`, set `presence`.

### 6.3 metadata.json

- Always write `"spec_version": 2`, `id` (a new UUID4) and `name`; see quirk Q1.
- Write `short_file_threshold: 15.0`, an entry in `tracks` for every track with all six fields, and explicit `playback_mode`.
- Write `groups` with an entry per group folder when the defaults (100 %, not muted, 120 s gap) do not fit (2.5).
- Presets go in `presets.json`: every track in every preset (4.3); one default preset.
- UTF-8 without BOM; escaping non-ASCII (`\uXXXX`) is safest (quirk Q11).
- Licence: if the theme carries source or licence info, add `attribution`. Fields used by the bundled themes (written by the Ambient Mixer importer, `plugins/builtin/ambient_mixer/plugin.py:315-330`, `:726-736`):

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
when it is missing (`plugins/builtin/ambient_mixer/plugin.py:315-330`). Bundled themes
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
| Groups | none | `metadata.json` `groups`: master volume, presence, mute (multiplying the tracks) and gap; presets may set volume, presence, mute (built) |
| Presets | Inside `metadata.json` | `presets.json`, converted automatically with a `.pre-presets.bak` backup (built) |
| Version marker | none | `"spec_version": 2` (built) |
| Sequences | none | planned (section 5) |

---

## Known quirks

Q1. **A theme's ID can change once on first start.** The loader now generates and saves an
`id` when `metadata.json` has none (`core/theme_metadata.py:107-110`, `:296`, `:307-308`), but
`device.py:116-123` reads `id` from the raw file when it first builds the theme list and falls
back to the sanitized folder name, so on the start that adds the ID the two disagree until
the next refresh or restart (`api.py:1193-1201` reads it again). In a folder that cannot be
written, a theme without `id` gets a new random ID on every scan.

Q2. **Unknown fields are dropped on save** through the metadata manager and by the
conversion (2.4): any top-level `metadata.json` field other than the known ones, and any
unknown field in a track entry. `spec_version` and `groups` are now kept.

Q3. **Two paths write `metadata.json` directly**, bypassing the manager's cache
(`api.py:962-982` `_write_theme_metadata`, used only when the manager is unavailable, and
`web/api_v2.py:1669-1703`). A later save through the manager writes its cached copy and can
undo those edits (for example a description change).

Q4. **Legacy `exclusive` works only in sparse and presence mode.** A top-level track marked
exclusive whose mode resolves to continuous (for example `auto` with `presence: 1.0`) ignores
its group and plays all the time (`recording.py:306-324`; only `SparsePlaybackStream` and
`PresenceMixingStream` use the coordinator).

Q5. **Group tracks ignore `exclusive`, and `auto`/`continuous` from metadata and presets**:
exclusive is forced on, and `auto` or `continuous` become sparse or presence by file length
(`recording.py:450-465`). The exclusive API call, and setting `continuous`, still save values
that have no effect on those tracks.

Q6. **A group folder named `Exclusive`** shares its coordinator with legacy exclusive
top-level tracks (`recording.py:332`, `:467-473`, `theme.py:128-133`), and its `groups` entry
with the `"legacy_exclusive"` marker the conversion writes.

Q7. **Preset changes on a playing channel do not change `playback_mode`, `seamless_loop` or
group membership** until the theme is restarted (3.7).

Q8. **Muted streams pause** instead of running silently, so unmuting resumes a loop where it
stopped and a sparse track mid-gap continues its gap (`theme.py:144`).

Q9. **Presence fades step once per 1024-sample chunk** (`recording.py:1059-1082`). Outside a
group, a live presence change to 1 or 0 during a fade restarts the fade at position 0, which
can jump the gain (for example a fade-in at 0.7 restarts from `sin(0) = 0`) (`:1023-1028`).

Q10. **Crossfade loop timing drifts by one chunk**: after a crossfade `samples_played` is set
to 66560 and then 1024 more is added in the same pass (`recording.py:677`, `:685`), so the next
loop's crossfade starts one chunk (about 23 ms) early.

Q11. **Some readers use `read_text()` without an encoding** (`device.py:120`, `api.py:1198`,
`core/session_manager.py:114`, `web/api_v2.py:1640`, `:1673`). On a system whose default
encoding is not UTF-8 (Windows standalone), non-ASCII text such as an emoji `icon` can fail to
load unless written as `\uXXXX` escapes. The manager reads with `utf-8-sig` and writes raw
UTF-8 (`core/theme_presets.py:48`, `:64-66`); the v2 metadata edit writes escaped ASCII
(`web/api_v2.py:1703`). If that edit's read fails it starts from an empty object and writes
back only the edited fields, wiping the rest of `metadata.json` (`web/api_v2.py:1670-1675`, `:1703`).

Q12. **Theme crossfade chunks are not counted in real-time pacing.** `_do_crossfade_in_thread`
pushes about 130 chunks without adding to `audio_time` (`core/channel.py:299-341` vs `:281-288`),
and the broadcast buffer holds only 50 chunks (`core/channel.py:44`), so a slow listener can
miss part of the crossfade and the channel runs about 3 s ahead of the clock afterwards.

Q13. **Group timing uses the wall clock** while sparse gaps and presence times count samples
(3.6), so the clocks only agree because output is paced in real time.

Q14. **Sparse docstring numbers are wrong** (`recording.py:708-711` says about 165 s at
presence 0.5 and continuous play at 1.0; the code gives about 16.5 min and 3 min). The gap
table in 3.4 is from the code.

Q15. **Hard-cut loops lose the tail** shorter than one chunk on each pass and do not flush the
resampler; the sparse decoder does not flush it either (`recording.py:491-521`, `:769-793`).
The crossfade decoder does flush (`:564-566`).

Q16. **The standalone API inverts `seamless_loop`**: `web_api.py:1709` (the `seamless_loop`
route) and `:1805` (preset load) set `crossfade_enabled = seamless_loop`, the opposite of the
add-on (`api.py:507`). (`web_api.py:1691` is the separate `crossfade` route and is correct.)

Q17. **`SessionManager.apply_preset_to_theme` is unused** (`core/session_manager.py:155-198`);
channel presets go through `preset_overrides` only.


Q19. **A grouped presence-mode track at 100 % effective presence keeps the group's turn
forever** (claim of 1e9 s, never released, `recording.py:1035-1039`), so the group's other
tracks never play while that stream runs. A grouped presence track muted while it has the turn
is paused (Q8) and cannot fade out, so the group stays blocked until its claim runs out
(`recording.py:79-85`); for a 100 % track that is never.



Q22. **The legacy `Exclusive` group is half a group.** It shares turns (3.6) and a hand-written
`gap_min`/`gap_max` in its `groups` entry is used (`theme.py:130`), but group masters
(`volume`, `presence`, `muted`) apply only to folder groups (`recording.py:423-428`), and the
groups API neither lists it nor lets it be changed (`api.py:1586-1592`, `:1614-1615`).
