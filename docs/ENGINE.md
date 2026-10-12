# The playback engine (developer note)

`sonorium.engine` runs Sonorium's mixer on its own: a theme folder in, mono
PCM out, pulled by the caller. It uses the same code as the server
(`theme.ThemeMix` for the tracks, `core.channel.Channel` for preset glides,
theme crossfades and fades, `core.playback_runner.PlaybackRunner` for
sequences and playlists) without the web server, Home Assistant, MQTT,
speakers, the MP3 encoder or any thread. Use it to play a theme without the
server, for renders, tests and comparisons.

```python
from sonorium.engine import Engine, PyAvDecoder

eng = Engine("media/Tavern", preset_id=None, seed=1234, decoder=PyAvDecoder())
pcm = eng.render(44100)      # numpy int16, shape (44100,): one second, mono, 44100 Hz
eng.set_preset("busy")       # glides over 3 s, as a preset picked on a playing channel
eng.set_volume(0.5)          # output volume 0..1, after the mix
eng.start_sequence("evening")  # steps through a sequence of the theme
eng.close()
```

## Arguments

| Argument | Meaning |
|---|---|
| `theme_dir` | A theme folder (audio files, group folders, `metadata.json`, `presets.json`). Read only: nothing is written. |
| `preset_id` | A preset of the theme. `None`: the theme's default preset, if one is marked. `""`: no preset. |
| `playback_values` | Settings > Advanced values that differ from the defaults (`sonorium/playback_values.py`), checked as the server checks them. |
| `seed` | Every random choice in the mix (a group's pick, a wait, an Ebb & Flow drift) comes from `random.Random(seed)`. |
| `sample_rate` | 44100 only. |
| `decoder` | How files are read (see below). `PyAvDecoder()` by default. |
| `clock` | A `SampleClock` that counts rendered samples. |
| `master_gain` | Settings > Audio Settings > Master output gain, 0-100 (default 60). |
| `volume`, `fade_in` | Output volume 0..1; seconds to fade the mix in at the start. |
| `step` | A sequence step's While playing values laid over the preset (by hand; a running sequence sets its own). |
| `themes_root` | Where other themes are: linked files, playlist entries, `set_theme`. Default: the theme folder's parent. |
| `crossfade` | Settings > Audio Settings > Crossfade: seconds a theme change crossfades (default 3). |
| `sequence_id` | A sequence of the theme to run from the start, on its first preset. |

`render(n, dtype="int16" | "float32")` returns the next `n` samples, any
length. Other calls: `set_preset(id, glide_seconds=3.0, fade_seconds=None,
step=None)`, `set_volume`, `set_master_gain`, `fade_in`, `fade_out`,
`schedule_transitions` / `cancel_transitions` (advanced transitions,
`core/transitions.py`), `reload_theme` (below), `close`. Read-only: `presets`,
`sequences`, `tracks`, `theme_id`, `theme_dir`, `preset_id`, `sequence_id`,
`missing_links`, `clock`.

### Edits while it plays

`reload_theme(glide_seconds=3.0)` re-reads `metadata.json` and `presets.json`
of every theme the engine has loaded, after a change was saved to them (for
example with `sonorium.core.theme_edit`, below), and the playing mix follows
as a server channel follows the Theme Editor:

- the theme's own track and group settings change at once (a track's volume
  eases over a quarter second, as when its slider moves);
- the preset playing glides to its saved values over `glide_seconds`, with the
  running sequence step's While playing values on top;
- the preset or sequence keeps playing. A preset no longer in `presets.json`
  keeps its values until another one is picked; a sequence no longer there
  stops at its next step.

Audio files and linked files stay as they were loaded: a new file needs a new
engine (or `set_theme`).

```python
from sonorium.core import theme_edit

theme_edit.set_track("media/Tavern", "Fireplace", volume=0.4)
eng.reload_theme()           # the fireplace eases down while it plays
```

## Sequences, playlists and theme changes

These run as on a server channel, with the server's runner, timed in audio
mixed: a step or an entry changes between two 1024-sample blocks once its
time has come, so `render()` moves them on and nothing runs between calls.

| Call | What it does |
|---|---|
| `start_sequence(id, glide_seconds=3.0)` | Runs a sequence of the theme from its first preset: each preset holds for a random time in hold_min..hold_max, then glides to the next over the ramp, with the step's While playing values and its Coming in / Leaving transitions. At the end it restarts, reverses or stops (on its last preset). Returns the sequence as it plays. `LookupError` if unknown, `ValueError` if it has no presets. |
| `stop_sequence()` | Stops it; the mix stays on the preset it is on. |
| `start_playlist(playlist, themes_root=None)` | Plays a playlist given as the server's playlist JSON (`name`, `entries` of `theme_id` plus `preset_id` or `sequence_id`, `order` `in_order`/`shuffle`, `repeat`, `play_min`/`play_max`, `fit_sequences`; `id` optional). Each entry's theme is found by its id under `themes_root`; a theme that isn't there is skipped. A new theme crossfades in; an entry with a sequence plays one pass of it. A playlist that doesn't repeat fades out after its last entry (`status()["playing"]` is then false; starting something new fades back in). `ValueError` if the playlist isn't valid or nothing in it can be played. |
| `stop_playlist()` | Stops it; the mix keeps the theme it is on (an entry's sequence carries on as an ordinary run). |
| `next()` | The next playlist entry if a playlist runs, else the sequence's next step (3 s glide). Returns `"playlist"`, `"sequence"`, `"end"` or `None`. |
| `previous()` | The previous playlist entry (from the first one of a repeating playlist: the last one), else the sequence's previous step. Before a sequence's first step it wraps to the last when At the end is restart, else the step starts again. |
| `set_theme(theme_dir, preset_id=None, sequence_id=None)` | Changes the theme while playing: the old one crossfades into the new one (equal power, `crossfade` seconds), no gap. As on the server, it stops the playlist and sequence that were running. The same theme glides to the preset. |
| `status()` | A JSON-serialisable dict: `playing`, `ended`, `seconds_rendered`, `theme_id`, `theme_name`, `preset_id`, `preset_name`, `crossfading`, `sequence` (`id`, `name`, `step`, `steps`, `at_end`, `single_pass`, `current_preset_id`, `seconds_left`, ...) or null, `playlist` (`id`, `name`, `position`, `entries`, `entry`, `theme_id`, `theme_name`, `seconds_left`, ...) or null, and `recent` (the changes made, with the second of audio each happened at). |

`seconds_left` counts audio mixed, which is ahead of the samples handed out
by at most one `render()` call's leftover (less than 1024 samples).

The runner's random choices (hold times, play times, shuffled order) come
from their own `random.Random` made from the seed, so the same calls at the
same positions give the same steps and entries.

## Decoders

Every read of an audio file, on the server and in the engine, goes through
the decoder in use (`sonorium/decoders.py`, called by `recording.AudioReader`).
A decoder has one method, `open(path)`, returning an object with:

- `has_audio` (bool)
- `header_length()`: the length in samples at 44100 Hz, or `None`
- `blocks(flush=True)`: 1-D arrays of mono samples at 44100 Hz, int16 or
  float32 on the int16 scale, any lengths, start to end; closes the file at the end
- `close()`

A loop does not seek: each pass opens the file again.

- `PyAvDecoder` (default): any file FFmpeg reads, resampled to s16 mono
  44100 Hz by PyAV's `AudioResampler`. The only playback code that imports `av`,
  and only when it opens a file.
- `PcmFileDecoder`: 16-bit PCM WAV at 44100 Hz (channels averaged to mono) or
  raw 16-bit little-endian mono (`.pcm`, `.raw`), with the standard library.
  No resampling. With `lookup=mirror_lookup(theme_dir, cache_dir)` it reads
  WAV copies made with `sonorium.engine.pcm_cache.predecode_theme`.

The engine sets its decoder, seed and playback values for the calling thread
only, during its own calls; the server's playback in other threads keeps the
defaults.

## Golden render

```
python -m sonorium.engine.render <theme_dir> --preset X --seconds 30 --seed 1 --out out.wav
```

Writes a 16-bit mono WAV. Options: `--decoder pyav|pcm`, `--pcm-cache DIR`
with `--predecode`, `--values '{"group_gap": 30}'`, `--master-gain 60`,
`--float`, `--sequence ID`, `--playlist FILE.json` with `--themes-root DIR`,
`--crossfade 3`. Run from `sonorium_addon/` (or with it on `PYTHONPATH`).

## Determinism

The same theme files, settings, seed, decoder and calls (at the same sample
positions) give the same samples: the mix's time is the count of samples
rendered (never the wall clock), and the result doesn't depend on how
`render` is sliced or on Python's hash seed (`tests/test_engine.py` and
`tests/test_engine_runners.py` check both, with a sequence and a playlist
running too). Bit-exact results assume the same numpy version and CPU architecture;
the two decoders give the same samples for 16-bit mono 44100 Hz WAV files, not
for compressed files.

## Limits

- 44100 Hz mono only.
- Sounds over the mix (announcements, alerts) and volume ramps are not included.
- Sequence and playlist timers are checked before every 1024-sample block (the
  server checks them about once a second), so a step can come up to a second
  sooner than on a server channel with the same draws.
- Edits saved to a theme's files reach the mix when `reload_theme()` is
  called; new audio files need a new engine.
- Older themes are converted in memory as the server converts them on load,
  except an old intrusion group (that conversion moves files): it plays as it is.
- Log lines go to the `sonorium` logger (stdout) at `SONORIUM_LOG_LEVEL`.
- One caller at a time per engine.

Third-party imports: numpy; PyAV (`av`) for `PyAvDecoder` only. Python 3.10+.
`tests/test_engine.py` imports the engine with the server's packages blocked.

## Theme editing, playlists and network speakers without the server

The server's Theme Editor, playlist and sequence routes call plain modules,
so anything that runs the engine can change themes and playlists with the
same code, and gets the same files. None of them imports the web server, the
settings, Home Assistant, MQTT, plugins or sign-in (`tests/test_core_modules.py`
checks it in a separate process with those blocked).

### `sonorium.core.theme_edit`

Each call reads a theme folder's `metadata.json` and `presets.json` as the
server reads them (older themes are converted on the way, as when the server
loads them), makes the change and writes both back as the server writes them.
`tests/test_theme_edit.py` makes the same changes through the server's routes
and with these calls and checks the files are byte for byte the same.

| Call | What it does |
|---|---|
| `set_track(folder, key, **values)` | A track's settings: any of `presence`, `volume` (0..1), `muted`, `playback_mode` (`continuous`, `sparse`, `presence`, `auto`, `unassigned`), `seamless_loop`, `exclusive`, `fade_in`, `fade_out` (seconds, at most 30), `volume_low`, `pause_between`, `pause_min`, `pause_max`, `rise`. Checked and clamped as the Theme Editor does. Returns the track's settings as saved. |
| `track_settings(folder, key=None)` | One track's settings, or every track's. |
| `reset_theme_tracks(folder)` | Every track back to its defaults (linked files stay muted). |
| `list_groups(folder)` | `{group: {"settings", "tracks"}}`. |
| `set_group(folder, group, **values)` | A group's settings: any of `presence`, `volume`, `muted`, `gap_min`, `gap_max`, `mode` (`intermittent`, `merry_go_round`, `ebb_flow`), `crossfade`, `pause_between`, `rise`; `None` removes one. Returns the settings as saved. |
| `create_group(folder, name)`, `move_track(folder, key, group)` | A new empty group; a track moved into a group (made if missing) or, with `None`, to the top level. Its settings, preset values and sequence entries follow its new key; `move_track` returns it. |
| `current_mix(folder)` | The theme's own settings as a preset saves them: `{"tracks", "groups"}`. |
| `list_presets(folder)`, `get_preset(folder, id)` | The presets (`id`, `name`, `is_default`, `track_count`); one preset's `tracks` and `groups`. |
| `new_preset(folder, name=None)` | The theme's own settings saved as a new preset (the default if it is the first). Returns `preset_id`, `name`, `is_default`. |
| `save_preset(folder, id)` | The theme's own settings saved into a preset; its name and default mark stay. |
| `load_preset(folder, id)` | A preset's values become the theme's own settings, as the Theme Editor's Load does. Returns the preset. |
| `set_default_preset`, `rename_preset`, `delete_preset` | As in the Theme Editor; a deleted preset is taken out of the theme's sequences, and the first one left becomes the default if it was. |
| `create_sequence(folder, data)`, `update_sequence(folder, id, data)`, `delete_sequence(folder, id)` | Sequences in `presets.json`, checked as the server checks them (`core/sequences.py`). |

Errors are `theme_edit.EditError` (a `ValueError`) with `status`: 400 for a
value that can't be used, `NotFound` (404), `Conflict` (409, a name already
taken) and `SaveFailed` (500). A sequence that can't be saved raises
`sequences.SequenceError`; a group name that can't be used,
`theme_groups.GroupError`. The checks themselves (`clean_track_value`,
`clean_ebb`, `clean_fades`, `clean_group_update`) and the changes on values
the server keeps in memory (`apply_track`, `store_track`, `add_preset`, ...)
are in the same module; the server's routes call those.

```python
from sonorium.core import theme_edit as te

te.set_track(folder, "Rain", volume=0.5, muted=False)
te.set_group(folder, "Birds", gap_min=10, gap_max=60)
made = te.new_preset(folder, "Evening")
te.load_preset(folder, made["preset_id"])
```

### `sonorium.core.playlists`

Playlists as the server stores them (`state.json`, under `"playlists"`).
`create_playlist(playlists, data, theme_exists, theme_presets,
theme_sequences)` checks a playlist (`clean_playlist`) and adds it to the
`{id: playlist}` dict; `update_playlist` changes only the fields sent;
`delete_playlist` takes one out. A name already used raises
`PlaylistConflict`, anything else that can't be saved `PlaylistError`, with
the server's messages. `folder_lookups(themes_root)` gives the three lookups
from the theme folders under a folder. `load_playlists(path)` reads the
playlists from `state.json` or any JSON file with a `"playlists"` object;
`save_playlists(path, playlists)` writes them there and keeps the file's
other keys. A playlist plays with `Engine.start_playlist`.

### `sonorium.network`

Network speaker discovery and streaming take what they need as arguments:
`NetworkSpeakerService(config_dir, refresh_interval=600, discovery_timeout=10.0)`
(it keeps `network_speakers.json` and the manual speakers in `config_dir`),
`discover(full=True)`, `play_multi(speaker_ids, stream_url)`,
`stop_multi`, `set_volume_multi`, `playing_states`. The server builds the
service with its own config folder and gives each speaker the stream URL it
serves. The protocol libraries (`pychromecast`, `soco`, `async-upnp-client`,
`pyatv`, `zeroconf`, `aiohttp`) are imported only when a protocol is used.

### Files

`sonorium/core/theme_edit.py` needs, besides the engine's files:
`core/theme_metadata.py`, `core/theme_presets.py`, `core/theme_groups.py`,
`core/intrusions.py`, `core/sequences.py`, `core/playlists.py`,
`theme_files.py`, `recording.py`, `obs.py`. Network speakers:
`sonorium/network/` (`__init__.py`, `models.py`, `discovery.py`,
`streaming.py`, `heos.py`, `manual.py`, `service.py`) and `display.py`.
Saving `metadata.json` or `presets.json` tells the server's plugins the
theme changed when they are there, and does nothing otherwise.
