# The playback engine (developer note)

`sonorium.engine` runs Sonorium's mixer on its own: a theme folder in, mono
PCM out, pulled by the caller. It uses the same mixing code as the server
(`theme.ThemeMix` for the tracks, `core.channel.Channel` for preset glides and
fades) without the web server, Home Assistant, MQTT, speakers, the MP3 encoder
or any thread. Use it for offline renders, tests and comparisons.

```python
from sonorium.engine import Engine, PyAvDecoder

eng = Engine("media/Tavern", preset_id=None, seed=1234, decoder=PyAvDecoder())
pcm = eng.render(44100)      # numpy int16, shape (44100,): one second, mono, 44100 Hz
eng.set_preset("busy")       # glides over 3 s, as a preset picked on a playing channel
eng.set_volume(0.5)          # output volume 0..1, after the mix
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
| `step` | A sequence step's While playing values laid over the preset. |

`render(n, dtype="int16" | "float32")` returns the next `n` samples, any
length. Other calls: `set_preset(id, glide_seconds=3.0, fade_seconds=None,
step=None)`, `set_volume`, `set_master_gain`, `fade_in`, `fade_out`,
`schedule_transitions` / `cancel_transitions` (advanced transitions,
`core/transitions.py`), `close`. Read-only: `presets`, `tracks`, `theme_id`,
`preset_id`, `missing_links`, `clock`.

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
`--float`. Run from `sonorium_addon/` (or with it on `PYTHONPATH`).

## Determinism

The same theme files, settings, seed, decoder and calls (at the same sample
positions) give the same samples: the mix's time is the count of samples
rendered (never the wall clock), and the result doesn't depend on how
`render` is sliced or on Python's hash seed (`tests/test_engine.py` checks
both). Bit-exact results assume the same numpy version and CPU architecture;
the two decoders give the same samples for 16-bit mono 44100 Hz WAV files, not
for compressed files.

## Limits

- 44100 Hz mono only.
- One theme per engine: no theme crossfade, sequence or playlist runner (pass
  `step` and call `set_preset` / `schedule_transitions` yourself). Sounds over
  the mix (announcements, alerts) and volume ramps are not included.
- Older themes are converted in memory as the server converts them on load,
  except an old intrusion group (that conversion moves files): it plays as it is.
- Log lines go to the `sonorium` logger (stdout) at `SONORIUM_LOG_LEVEL`.
- One caller at a time per engine.

Third-party imports: numpy; PyAV (`av`) for `PyAvDecoder` only. Python 3.10+.
`tests/test_engine.py` imports the engine with the server's packages blocked.
