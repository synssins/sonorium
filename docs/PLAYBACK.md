# Playback: fades, sequences, playlists and Home Assistant entities

How a channel starts, stops and changes what it plays, for API clients (the web UI, the
phone app) and Home Assistant automations. The theme file format, including how sequences
are stored in a theme's `presets.json`, is in [THEME_FORMAT.md](THEME_FORMAT.md) (section 5).

In the API a channel is a **session** (`/api/sessions/{id}`).

## Smooth changes

| Change | What you hear | Length |
|---|---|---|
| Channel starts | The whole mix fades in from silence | Settings > Audio Settings > Crossfade (default 3 s) |
| Channel stops or pauses | The mix fades out, then the speakers are told to stop or pause | Crossfade |
| Theme changes (by hand or by a playlist) | Old theme crossfades into the new one | Crossfade |
| Preset picked by hand (web UI, API, MQTT) | Volume and interval glide to the new values; tracks turning on or off fade | 3 s |
| Preset changed by a sequence (also a playlist entry's sequence) | Same glide; tracks turning on or off fade over the same time | The sequence's ramp time (its own, else the theme's sequence default, default 30 s) |
| Track muted or unmuted (mute button) | The track fades out or in | 3 s |

"Next" on a running sequence is a step by hand: its glide and on/off fades take 3 s.

A track that turns on (a preset, a sequence step, unmute) fades in from silence. An
**Ebb & Flow** track (outside a group) also starts afresh when it turns on: from the
beginning of its file, its level rising from silence to its first level over 6 s
(with the on fade above on top), instead of resuming where it paused while it was off. Tracks that stay on are not touched, and grouped tracks
(Intermittent, Merry-go-round) keep their own rules.

## Ebb & Flow

An Ebb & Flow track rises and falls between the two knobs of its Volume slider
(`volume_low` and `volume`). It drifts: each move is a step from where it is (12% to
30-70% of the range, smaller at shorter durations) and usually keeps the direction of
the last one (it turns back about a third of the time, and always at a knob), so it
never just swings between the two knobs. After each move it holds, now and then (12%)
two to four times longer than usual.

**Duration** (the track's `presence`, labelled Duration for Ebb & Flow) sets how long
each rise and fall takes: at 0 (shorter) the glides take 2-6 s and the holds 2-10 s;
at 1 (longer) the glides take 15-40 s and the holds 20-60 s, in proportion between.
Earlier versions called the same value Interval, and it meant the opposite (higher was
quicker); each theme's stored values are flipped once when it loads (`ebb_duration`
in metadata.json; the files as they were are kept as `*.pre-duration.bak`).

**Rise** (`rise`, 0 gradual .. 1 sudden) sets how fast it rises compared with how it
falls. Gradual: every glide is the same S-curve. Sudden: a rise takes up to 85% less
time and starts fast (it eases into the new level), a fall takes up to 50% longer.
An Ebb & Flow group can set one Rise for every file (its `rise`; unset = each file's own).

A group's Volume and Duration scale its tracks' values down (both knobs).

With **Pause between plays** each play starts at the file's beginning, rising from
silence, runs once to the file's end (fading out over one glide time so it is silent
at the end), then pauses for a random time in the track's Pause range (`pause_min` to
`pause_max`, seconds). The first start is staggered the same way. A paused track is not
decoded or mixed.

An **Ebb & Flow group** (`"mode": "ebb_flow"`) plays every file at once, each as Ebb &
Flow, no turns. With the group's Pause between plays on, every file pauses between
plays: the first file to start plays at once, the others start staggered, and a file
only fades out for its pause while another unmuted file of the group is playing (else
it plays another pass), and a paused file comes back at once if none is playing. So
the group is never silent. With the group's switch off, each file follows its own
Pause between plays and nothing keeps the group from falling silent.

**Unassigned** (`playback_mode: "unassigned"`) tracks don't play and aren't decoded.

## A sequence step's While playing layer

A sequence can change tracks' and groups' settings for one of its steps (presets.json,
`sequences[id]["while"][preset_id]`, `{"tracks": {key: {...}}, "groups": {name: {...}}}`).
Every entry lists every field (tracks: volume, volume_low, presence, muted, rise,
pause_between, pause_min, pause_max; groups: volume, presence, muted, gap_min, gap_max,
pause_between, rise); `null` means "not changed here: the preset's". Wherever a
channel's settings are worked out (a start, a step, a preset save, a theme change)
the step's non-null values go over the preset's while that sequence plays that step;
picking a preset by hand stops the sequence and with it the layer. Saving a step's
layer (`PUT /api/v2/themes/{id}/sequences/{sid}/while/{preset}`) glides channels on
that step to it.

A track of the old Exclusive switch (in a group) still takes turns: it fades in on its
turn, plays a while, fades out and hands the turn back.

The channel's volume setting never changes during a fade: the fade is applied to the
channel's audio. Settings > Audio Settings > Master output gain is the output gain of every mix.

## Sequences

A sequence is a theme's presets in order (see THEME_FORMAT.md section 5). A channel runs
one sequence of the theme it plays: each preset holds for a random time between the hold
minimum and maximum, then the channel glides to the next one over the ramp time. At the end
it goes back in reverse, starts again, or stops on the last preset.

**A sequence is picked like a preset.** Wherever a channel's preset is chosen (web UI, API,
Home Assistant), the theme's sequences are choices too. In the API the choice is the preset
id `seq:<sequence_id>`; picking it starts the sequence from its first preset.

- Picking a normal preset (or no preset), choosing a different theme, or deleting the
  sequence stops it.
- Stopping the channel stops the timer; playing again starts the sequence from its first preset.

One **pass** of a sequence is each of its presets once, first to last, with the ramps between
them: `pass_min` = presets x hold minimum + ramps, `pass_max` = presets x hold maximum + ramps.

## Advanced transitions

A sequence's preset can set, per track, how the track **comes in** at its step (tracks the step
turns on or makes louder) and how it **leaves** at the next step (tracks the next step turns off
or makes quieter). In the web UI: the sequence editor, a preset's ⋯ › Advanced…. Stored in the
sequence (THEME_FORMAT.md 5.2a); a track with nothing set follows the ramp (Smooth).

| Coming in | Leaving | What the mixer does to the track |
|---|---|---|
| Gusts | Gusts dying down | The level rides from the old volume to the new one with uneven swells (a seeded random curve, so the same step sounds the same every time and in the preview); Intensity sets how far and how fast it swings; Max volume caps the peaks |
| Swell | Smooth fade | One S-curve, over Speed seconds from the start of the change |
| Build | Thin out | Intermittent tracks: the interval (presence) and the volume start lower and rise to the preset's, or fall; Amount sets how far. To off, a short fade at the end |
| Distance | Recede | A low-pass filter whose cutoff rises (falls) and the level rising from far below (falling away); How far sets how muffled and quiet |
| Start fresh | Finish then stop | From the beginning of its file (a sounding track crossfades over 1 s); or the play going out finishes, then a 0.5 s fade |

Styles combine by multiplying: e.g. Distance + Gusts is the filter and level of the distance with
the gust swing on top. **Pre-fade** (0-90 s) starts the change that many seconds before the step:
the **fade window** is pre-fade + ramp, from `step - pre_fade` to `step + ramp`. A pre-fade never
starts before the glide into the current preset has finished (with a short hold it is shortened).

How it runs:

- When a step is taken (and when a sequence starts), the runner looks at the next step (the next
  preset, as `at_end` will take it) and plans its transitions on the channel's mix at once: each
  track's start time and window. Only tracks that really come in or leave at that step are used.
- In the mix (`theme.ThemeStream`), a track with a running transition gets its level from the
  envelope (`core/transitions.py` `Envelope`), applied per chunk as a ramp from the level at the
  chunk's start to its end, plus, for Distance / Recede, two one-pole low-pass filters (12 dB per
  octave, numpy only, state carried between chunks). For the window the track's volume, on/off and
  (Build / Thin out) interval are pinned, so the preset glide and the on/off fade leave it alone.
  At the end the track goes back to its preset values; if they differ from where the envelope
  ended (the step was taken by hand) it glides there over 3 s.
- Next, a preset picked by hand, a different theme, or stopping the sequence drops the planned
  transitions; running ones glide over 3 s to what the channel plays now.
- Cost: well under 1 ms per 23 ms chunk with four transitions running (tests measure it).
- The web UI's ▶ preview runs the same envelope (the same seeded gusts, a low-pass BiquadFilter)
  on this device and follows edits as they are made.

Each step's lines in the log start with `Playback:` (Settings > Logs > Playback): the step, the
transitions starting (track, styles, window, levels), tracks turning on or off, Ebb & Flow fades,
Intermittent plays, and the mix level whenever it moves 1 dB.

## Playlists

A playlist plays themes one after another on a channel. Playlists are global and stored in
the server's `state.json` (add-on: `/config/sonorium/state.json`) under `"playlists"`:

```json
{
  "name": "Evening",
  "entries": [
    {"theme_id": "6f1c...", "preset_id": "slow_night", "sequence_id": null},
    {"theme_id": "a93e...", "preset_id": null, "sequence_id": "gentle_storm"},
    {"theme_id": "0b7d...", "preset_id": null, "sequence_id": null}
  ],
  "order": "in_order",
  "repeat": true,
  "play_min": 1800,
  "play_max": 3600,
  "fit_sequences": false,
  "owner": null
}
```

| Field | Meaning |
|---|---|
| `entries` | Each entry is a theme, plus at most one of its presets or sequences. With neither, the theme's default preset plays. |
| `play_min`, `play_max` | Seconds (1 to 604800). One range for the whole playlist: every entry plays for a random time in it, drawn fresh each time. Default 1800-3600. An entry with a sequence ignores it unless `fit_sequences` is on. |
| `fit_sequences` | `false` (default): an entry with a sequence plays **one full pass** of it (each preset once, first to last, holds drawn in the sequence's range, its ramps between them), however long that takes, then the playlist moves on. `true`: the entry's time is drawn from `play_min`-`play_max` like any entry and the pass is squeezed (or stretched) to end exactly then: holds and ramps are scaled by the same factor; if a ramp would be longer than half a hold, the holds become equal shares and the ramp at most half of one. |
| `order` | `in_order`, or `shuffle`: each round plays every entry once in a random order, never the same theme twice in a row and, with 3 or more different themes, never A, B, A (also across rounds). |
| `repeat` | `true`: start a new round at the end. `false`: the channel stops (fading out) after the last entry; playing it again starts from the first entry. |
| `owner` | `null` (every playlist is shared for now; per-user playlists come later). |

On a channel:

- A channel plays a theme **or** a playlist. Choosing a theme by hand stops the playlist; the
  channel keeps playing.
- Each new entry crossfades to its theme (or glides to its preset on the same theme) and
  starts its sequence, if it has one (one pass, see `fit_sequences`; the sequence's `at_end`
  is not used).
- Stopping the playlist during a sequence entry leaves the sequence running as an ordinary
  (endless) sequence.
- The channel's playlist and its place in it are saved, so a stopped channel (or a restarted
  server) carries on with the entry it was on.
- An entry whose theme was deleted is skipped.

### Theme cycling became playlists

Older versions had per-channel theme cycling (`cycle_config`: interval, random order, theme
list). On the first start of this version each channel's cycling is converted once:

- cycling on: a playlist "*channel* cycle" with its themes (all themes if it listed none), each
  playing exactly the old interval, shuffled if it was random, repeating; the channel plays it,
  carrying on from its current theme;
- cycling off but with a theme list: the playlist is made but not played;
- cycling off without a list: nothing.

The log says what was converted. `cycle_config` is no longer saved, and the `/cycle` endpoints
are gone.

## REST API

All paths are under `/api`. Errors: 400 bad input (the `detail` says why), 404 unknown
session, theme, sequence or playlist, 409 conflicts (a duplicate name, a sequence with no
presets left, Next with nothing running or on a stopped channel).

### Sequences of a theme

| Method | Path | Body / result |
|---|---|---|
| GET | `/themes/{theme_id}/sequences` | `{theme_id, sequence_defaults, sequences: [...]}` |
| POST | `/themes/{theme_id}/sequences` | `{name, presets, at_end?, hold_min?, hold_max?, ramp?}` -> 201, the sequence |
| GET | `/themes/{theme_id}/sequences/{sequence_id}` | the sequence |
| PUT | `/themes/{theme_id}/sequences/{sequence_id}` | any of the fields; only those change |
| DELETE | `/themes/{theme_id}/sequences/{sequence_id}` | `{status, sequence_id, stopped_on: [session ids]}` |
| GET | `/themes/{theme_id}/sequence_defaults` | `{theme_id, hold_min, hold_max, ramp}` |
| PUT | `/themes/{theme_id}/sequence_defaults` | any of `hold_min`, `hold_max`, `ramp` |
| GET | `/themes/{theme_id}/sequences/{sequence_id}/transitions/{preset_id}` | `{theme_id, sequence_id, preset_id, in: {track: {...}}, out: {...}}` (missing tracks are Smooth) |
| PUT | `/themes/{theme_id}/sequences/{sequence_id}/transitions/{preset_id}` | `{in?, out?}`: replaces that preset's transitions (`{}`: all Smooth); 400 bad values, 404 a preset not in the sequence |
| GET | `/themes/{theme_id}/transition_tracks?preset=&prev=&next=` | the tracks a step changes, for the Advanced dialog: `{in: [...], out: [...]}`, each `{key, name, group, mode, from, to, presence_from, presence_to, duration_seconds}` (volumes 0-1, 0 = off) |

A sequence's `transitions` (THEME_FORMAT.md 5.2a) can also be sent whole with the sequence's POST
or PUT (the web UI saves them that way, with the sequence); transitions of presets taken out of the
sequence are dropped.

A sequence is returned as `{id, name, presets, at_end, hold_min, hold_max, ramp, owner,
transitions, effective: {hold_min, hold_max, ramp}, pass_min, pass_max}`; `effective` is the timing it plays
with (its own, or the theme's defaults); `pass_min` and `pass_max` are the seconds one pass
takes at the shortest and longest holds.

The theme's preset list, `GET /themes/{theme_id}/presets`, has its `presets` as before plus
`sequences`: each sequence as above with `choice` (`"seq:<id>"`, the preset id that picks it)
and `has_intrusion` (true if any of its presets has an intrusion), so a preset menu can list
them after the presets.

### Playlists

| Method | Path | Body / result |
|---|---|---|
| GET | `/playlists` | `{playlists: [...]}` |
| POST | `/playlists` | `{name, entries, order?, repeat?, play_min?, play_max?, fit_sequences?}` -> 201, the playlist with its `id` |
| GET | `/playlists/{playlist_id}` | the playlist |
| PUT | `/playlists/{playlist_id}` | any of the fields; only those change |
| DELETE | `/playlists/{playlist_id}` | `{status, playlist_id, stopped_on: [session ids]}` |

### On a channel

| Method | Path | Body / result |
|---|---|---|
| PUT | `/sessions/{id}` | `{preset_id: "seq:<sequence_id>"}` (with `theme_id` too, to change theme): run that sequence of the (new) theme from its first preset. 404 unknown sequence, 409 no presets left; nothing changes then. `{preset_id: "<preset>"}` or `""` stops it. `POST /sessions` takes the same `preset_id`. |
| POST | `/sessions/{id}/sequence` | `{sequence_id}`: run a sequence of the channel's theme from its first preset (the same as the preset choice) |
| DELETE | `/sessions/{id}/sequence` | stop the sequence (the channel stays on its preset) |
| PUT | `/sessions/{id}/playlist` | `{playlist_id}`: play a playlist from its first entry |
| DELETE | `/sessions/{id}/playlist` | stop the playlist (the channel keeps its theme) |
| POST | `/sessions/{id}/next` | next playlist entry now; with no playlist, the sequence's next preset |
| GET | `/sessions/{id}/playback` | the status below |

On a stopped channel, a sequence or playlist is only set: it starts when the channel plays.
`GET /sessions` and `/sessions/{id}` include `sequence_id`, `preset_choice` (what the preset
menu shows: `"seq:<sequence_id>"` while a sequence is set, else `preset_id`), `playlist_id` and
the same `sequence` and `playlist` status objects:

```json
{
  "sequence": {"id": "gentle_storm", "name": "Gentle storm", "running": true, "single_pass": false, "step": 2, "steps": 3,
               "at_end": "reverse", "current_preset_id": "rain", "current_preset_name": "Rain",
               "next_change_at": "2026-10-10T21:14:03+00:00", "seconds_until_change": 412},
  "playlist": {"id": "1a2b3c4d", "name": "Evening", "running": true, "entry_index": 1, "position": 1,
               "entries": 3, "theme_id": "a93e...", "theme_name": "Storm",
               "next_change_at": "2026-10-10T21:40:00+00:00", "seconds_until_change": 1969}
}
```

`running` is false while the channel is stopped. `single_pass` is true for a playlist entry's
sequence (one pass); on its last preset `next_change_at` is the playlist's next change.
`entry_index` is the entry in `entries`; `position` is the place in this round's order (they
differ when shuffled).

Stop and pause return at once; the fade and the speaker commands follow in the background.

## Home Assistant (MQTT) entities

Each channel has these entities (`<channel>` is its entity slug, for example `bedroom`):

| Entity | What it does |
|---|---|
| `select.sonorium_<channel>_preset` | `(None)`, the theme's presets by name, then its sequences as "*name* (sequence)", for example `Gentle storm (sequence)`. A preset is picked with a 3 s glide and stops a running sequence; a sequence option starts that sequence; `(None)` clears the preset (the theme's own settings) and stops a sequence. While a sequence runs (picked here or by a playlist entry) the state is its option. The options follow the theme, its presets and its sequences. |
| `select.sonorium_<channel>_playlist` | `(None)` or one of all playlists. Picking one plays it on the channel; `(None)` stops the playlist (the channel keeps playing its theme). |
| `button.sonorium_<channel>_next` | Next playlist entry, or the sequence's next preset. Ignored when the channel is stopped or nothing runs. |
| `sensor.sonorium_<channel>_step` | What the playlist and sequence are on, for example `Evening: Storm (2/3) \| Gentle storm: Rain (2/3)`, or `Off`. Its attributes are the status object above. |

The "nothing" option is `(None)`, not `None`: Home Assistant's MQTT select reads a `None`
state as "no option" and would show the entity as unknown. Commands accept `None`, `(None)` or
an empty payload. Each select's state is published right after its discovery config, so it
starts as `(None)` (or the current choice), never unknown. The global `select.sonorium_preset`
(for the selected channel) has the same options.

Earlier builds had a separate `select.sonorium_<channel>_sequence`; it is gone (its sequences
are options of the Preset select). Sonorium empties its retained discovery config at startup,
so Home Assistant removes the entity.

Topics follow the existing pattern: discovery under `homeassistant/<component>/sonorium_<channel>_<name>/config`,
state on `sonorium/<channel>/<name>/state`, commands on `sonorium/<channel>/<name>/set`
(the button's payload is `PRESS`), the Step sensor's attributes on `sonorium/<channel>/step/attributes`.
Removing Sonorium's MQTT entities (disconnecting MQTT) removes these too.

Example automation step:

```yaml
- action: select.select_option
  target:
    entity_id: select.sonorium_bedroom_playlist
  data:
    option: Evening
- action: select.select_option
  target:
    entity_id: select.sonorium_bedroom_preset
  data:
    option: Gentle storm (sequence)
```
