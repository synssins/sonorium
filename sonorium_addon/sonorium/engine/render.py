"""
Golden render: a theme mixed by the engine to a WAV file, the same bytes
every time for the same theme files, preset, values and seed.

    python -m sonorium.engine.render <theme_dir> --preset X --seconds 30 --seed 1 --out out.wav

Options: --decoder pyav|pcm (pcm reads WAV copies from --pcm-cache, made
with --predecode), --values '{"group_gap": 30}' (Settings > Advanced),
--master-gain 60, --float (32-bit float WAV instead of 16-bit),
--sequence ID (run a sequence of the theme), --playlist FILE (play a
playlist, the server's playlist JSON; its themes are found under
--themes-root, default the theme folder's parent), --crossfade 3.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np


def write_wav(path, samples: np.ndarray, rate: int) -> None:
    """Mono 16-bit PCM WAV (the standard library writes the same header every time)."""
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(np.asarray(samples, dtype="<i2").tobytes())


def write_float_wav(path, samples: np.ndarray, rate: int) -> None:
    """Mono 32-bit float WAV (format 3), written by hand: the wave module writes integer PCM only."""
    import struct
    data = np.asarray(samples, dtype="<f4").tobytes()
    header = b"RIFF" + struct.pack("<I", 4 + 26 + 12 + 8 + len(data)) + b"WAVE"
    fmt = b"fmt " + struct.pack("<IHHIIHHH", 18, 3, 1, rate, rate * 4, 4, 32, 0)
    fact = b"fact" + struct.pack("<II", 4, len(samples))
    Path(path).write_bytes(header + fmt + fact + b"data" + struct.pack("<I", len(data)) + data)


def render_to_file(theme_dir, out, seconds: float, seed: int = 0, preset=None, decoder=None,
                   playback_values=None, master_gain=None, as_float: bool = False, block: int = 4096,
                   sequence=None, playlist=None, themes_root=None, crossfade=None) -> dict:
    """Render `seconds` of the theme to `out`; returns {"samples", "cpu_seconds", "wall_seconds"}."""
    from sonorium.core.channel import THEME_CROSSFADE_DURATION
    from sonorium.engine import DEFAULT_MASTER_GAIN, Engine, SAMPLE_RATE
    total = int(round(seconds * SAMPLE_RATE))
    wall, cpu = time.perf_counter(), time.process_time()
    engine = Engine(theme_dir, preset_id=preset, playback_values=playback_values, seed=seed, decoder=decoder,
                    master_gain=DEFAULT_MASTER_GAIN if master_gain is None else master_gain,
                    themes_root=themes_root, sequence_id=sequence,
                    crossfade=THEME_CROSSFADE_DURATION if crossfade is None else crossfade)
    try:
        if playlist is not None:
            engine.start_playlist(playlist)
        parts, done = [], 0
        while done < total:
            n = min(block, total - done)
            parts.append(engine.render(n, "float32" if as_float else "int16"))
            done += n
    finally:
        engine.close()
    cpu, wall = time.process_time() - cpu, time.perf_counter() - wall
    samples = np.concatenate(parts) if parts else np.zeros(0, np.int16)
    (write_float_wav if as_float else write_wav)(out, samples, SAMPLE_RATE)
    return {"samples": len(samples), "cpu_seconds": cpu, "wall_seconds": wall}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sonorium.engine.render",
                                     description="Render a theme to a WAV file with the Sonorium engine.")
    parser.add_argument("theme_dir", help="theme folder")
    parser.add_argument("--preset", default=None, help='preset id ("" for none; default: the theme\'s default preset)')
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", required=True, help="WAV file to write")
    parser.add_argument("--decoder", choices=("pyav", "pcm"), default="pyav")
    parser.add_argument("--pcm-cache", default=None, help="folder of WAV copies for --decoder pcm")
    parser.add_argument("--predecode", action="store_true", help="first write the WAV copies into --pcm-cache")
    parser.add_argument("--values", default=None, help="Settings > Advanced values as JSON")
    parser.add_argument("--master-gain", type=float, default=None, help="Master output gain, 0-100 (default 60)")
    parser.add_argument("--float", action="store_true", help="write 32-bit float samples")
    parser.add_argument("--sequence", default=None, help="run this sequence of the theme")
    parser.add_argument("--playlist", default=None, help="play this playlist (a JSON file)")
    parser.add_argument("--themes-root", default=None, help="where the playlist's themes are")
    parser.add_argument("--crossfade", type=float, default=None, help="theme change crossfade, seconds (default 3)")
    args = parser.parse_args(argv)

    from sonorium.decoders import PcmFileDecoder, PyAvDecoder, mirror_lookup
    decoder = PyAvDecoder()
    if args.decoder == "pcm":
        if args.pcm_cache:
            if args.predecode:
                from sonorium.engine.pcm_cache import predecode_theme
                predecode_theme(args.theme_dir, args.pcm_cache)
            decoder = PcmFileDecoder(lookup=mirror_lookup(args.theme_dir, args.pcm_cache))
        else:
            decoder = PcmFileDecoder()
    values = json.loads(args.values) if args.values else None
    playlist = json.loads(Path(args.playlist).read_text(encoding="utf-8-sig")) if args.playlist else None
    stats = render_to_file(args.theme_dir, args.out, args.seconds, args.seed, args.preset, decoder, values,
                           args.master_gain, args.float, sequence=args.sequence, playlist=playlist,
                           themes_root=args.themes_root, crossfade=args.crossfade)
    print(f"{args.out}: {stats['samples']} samples, {stats['cpu_seconds']:.2f}s CPU, "
          f"{stats['wall_seconds']:.2f}s wall", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
