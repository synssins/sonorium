"""
Theme audio in one format: every track of a theme on the server is an MP3,
320 kb/s constant bit rate, 44.1 kHz, stereo.

- A WAV, FLAC or Ogg file (anything that is not an MP3) is converted:
  "Rain.wav" becomes "Rain.mp3". A mono file becomes stereo with the same
  samples on both sides.
- An MP3 that is already 44.1 kHz stereo is kept as it is (never encoded
  again, whatever its bit rate). Any other MP3 (another rate, mono) is
  encoded again under its own name.
- A track's key is its file name without the extension (theme_files.py), so
  "Rain.wav" -> "Rain.mp3" keeps the key "Rain": its settings, presets,
  sequences and the links of other themes follow without a change. Only a
  name that is taken changes: with "Rain.wav" and "Rain.mp3" both in a
  folder (one key for two files), the WAV becomes "Rain (2).mp3", key
  "Rain (2)", and it gets a copy of what "Rain" had (track settings, preset
  entries, sequence transitions and While playing layers). MANIFEST.json's
  "local_filename" entries follow the renamed file.
- The original is kept (KEEP_ORIGINALS) in the theme's ORIGINALS_FOLDER,
  "_originals", at the same place inside it ("_originals/Lute/Song 1.wav").
  The name starts with "_", so it is never a group, never listed or played,
  and theme_package leaves it out of exports and packages.
  "_originals/originals.json" lists what each original became (and is the
  journal that lets a conversion cut off by a restart finish or roll back).
  KEEP_ORIGINALS = False deletes originals once converted, and the
  originals a theme still keeps the next time it is checked.

Conversion needs PyAV with FFmpeg's MP3 encoder (LAME) and muxer; available()
says whether this Python has them. Without them (the Android app's copy of
Core) every function here does nothing and says so: themes stay as they are
and still play (the decoders read every format).

Server side, Mp3Service converts in the background, one theme at a time and
one file at a time, at low priority: a theme that came in (import, upload,
copy-in, a plugin) first, then every theme already present (once, after an
update; a theme is checked again only when its audio files change).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from sonorium.obs import logger

TARGET_RATE = 44100
TARGET_BITRATE = 320_000
TARGET_LAYOUT = "stereo"
TARGET_SUFFIX = ".mp3"
_ENCODE_FORMAT = "fltp"  # what LAME takes

# Originals are kept while the conversion is being tested. False: deleted
# once their MP3 is written, and those still kept are deleted on the next check.
KEEP_ORIGINALS = True

ORIGINALS_FOLDER = "_originals"
ORIGINALS_RECORD = "originals.json"
TEMP_PREFIX = ".sonorium-mp3-"     # an MP3 being written (hidden: never a track)
STATE_FILE = "theme_mp3.json"      # in the config folder: themes checked, by their audio files

_MP3_CODECS = ("mp3", "mp3float")


# ---------------------------------------------------------------------------
# What this Python can do
# ---------------------------------------------------------------------------

_available: Optional[bool] = None


def available() -> bool:
    """PyAV is installed with FFmpeg's MP3 encoder and muxer."""
    global _available
    if _available is None:
        try:
            import av
            av.codec.Codec("mp3", "w")
            av.format.ContainerFormat("mp3", "w")
            _available = True
        except Exception:
            _available = False
    return _available


@dataclass
class Probe:
    codec: str
    rate: int
    channels: int


def probe(path: Path) -> Optional[Probe]:
    """The file's first audio stream (codec, rate, channels), or None if PyAV can't read it."""
    try:
        import av
    except Exception:
        return None
    try:
        with av.open(str(path)) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                return None
            ctx = stream.codec_context
            return Probe(codec=ctx.name, rate=int(ctx.sample_rate or 0), channels=int(ctx.channels or 0))
    except Exception:
        return None


def is_standard(path: Path) -> bool:
    """An MP3 file at 44.1 kHz, stereo: kept as it is."""
    path = Path(path)
    if path.suffix.lower() != TARGET_SUFFIX:
        return False
    found = probe(path)
    return bool(found and found.codec in _MP3_CODECS and found.rate == TARGET_RATE and found.channels == 2)


# ---------------------------------------------------------------------------
# One file
# ---------------------------------------------------------------------------

class ConvertError(Exception):
    """The file couldn't be read or encoded (the message says why)."""


def convert_file(source: Path, target: Path, pause: float = 0.0, every: int = 64) -> None:
    """
    Encode source as an MP3 file at target: 320 kb/s CBR, 44.1 kHz, stereo
    (a mono source on both sides, unchanged). `pause`: seconds slept after
    every `every` frames, so a background conversion leaves the CPU to
    playback. Raises ConvertError (a partly written target is removed).
    """
    import av
    import numpy as np
    source, target = Path(source), Path(target)
    try:
        with av.open(str(source)) as inp:
            stream = next((s for s in inp.streams if s.type == "audio"), None)
            if stream is None:
                raise ConvertError("the file has no audio")
            mono = (stream.codec_context.channels or 0) == 1
            resampler = av.AudioResampler(format=_ENCODE_FORMAT, layout="mono" if mono else TARGET_LAYOUT,
                                          rate=TARGET_RATE)
            with av.open(str(target), "w", format="mp3") as out:
                ostream = out.add_stream("mp3", rate=TARGET_RATE)
                ctx = ostream.codec_context
                ctx.layout = TARGET_LAYOUT
                ctx.format = _ENCODE_FORMAT
                ctx.bit_rate = TARGET_BITRATE
                ostream.bit_rate = TARGET_BITRATE

                def encode(frames) -> None:
                    for frame in frames:
                        if mono:  # the same samples on both sides
                            plane = frame.to_ndarray().reshape(1, -1)
                            frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(np.vstack([plane, plane])),
                                                               format=_ENCODE_FORMAT, layout=TARGET_LAYOUT)
                            frame.rate = TARGET_RATE
                        frame.pts = None
                        for packet in ostream.encode(frame):
                            out.mux(packet)

                for n, frame in enumerate(inp.decode(stream)):
                    encode(resampler.resample(frame))
                    if pause and n % every == every - 1:
                        time.sleep(pause)
                encode(resampler.resample(None))
                for packet in ostream.encode(None):
                    out.mux(packet)
    except ConvertError:
        _unlink(target)
        raise
    except Exception as e:
        _unlink(target)
        raise ConvertError(str(e) or type(e).__name__) from e


def _unlink(path: Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# One theme
# ---------------------------------------------------------------------------

@dataclass
class ThemeResult:
    folder: Path
    converted: list = field(default_factory=list)       # [(from, to)] relative paths
    renamed: dict = field(default_factory=dict)         # old key -> new key (a taken name)
    failed: list = field(default_factory=list)          # [(relative path, message)]: unreadable files
    retry: list = field(default_factory=list)           # [(relative path, message)]: try again later (in use)
    pending: int = 0                                    # files that needed converting
    unavailable: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.converted) or bool(self.renamed)


_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _theme_lock(folder: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(Path(folder).resolve()), threading.Lock())


def _rel(folder: Path, path: Path) -> str:
    return Path(path).relative_to(folder).as_posix()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_originals_path(theme_folder: Path, path: Path) -> bool:
    """A path inside the theme's originals folder (left out of exports and packages)."""
    try:
        relative = Path(path).relative_to(theme_folder)
    except ValueError:
        return False
    return bool(relative.parts) and relative.parts[0] == ORIGINALS_FOLDER


def is_temp_file(path: Path) -> bool:
    """An MP3 still being written."""
    return Path(path).name.startswith(TEMP_PREFIX)


def originals(theme_folder: Path) -> list[dict]:
    """What originals.json lists: [{"original", "kept_as", "mp3", "state", "converted", ...}]."""
    return list(_Record(Path(theme_folder)).entries)


class _Record:
    """_originals/originals.json: each original, what it became and where it is kept (also the journal)."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.path = folder / ORIGINALS_FOLDER / ORIGINALS_RECORD
        self.entries: list[dict] = []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            files = data.get("files") if isinstance(data, dict) else None
            self.entries = [e for e in files or [] if isinstance(e, dict)]
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        from sonorium.core.theme_presets import write_json_atomic
        if not self.entries and not self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, {
            "about": "Audio files of this theme as they were before Sonorium converted them to MP3. "
                     "Not part of the theme: never played, listed or exported.",
            "files": self.entries,
        })


def _free_original_path(folder: Path, relative: str) -> Path:
    """Where an original is kept: _originals/<its path>, " (2)" etc. when an older one is there."""
    target = folder / ORIGINALS_FOLDER / relative
    if not target.exists():
        return target
    n = 2
    while True:
        candidate = target.with_name(f"{target.stem} ({n}){target.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _read_doc(path: Path) -> Optional[dict]:
    from sonorium.core.theme_presets import BrokenJsonError, read_json
    try:
        return read_json(path)
    except (BrokenJsonError, OSError):
        return None


def _link_names(folder: Path, group: Optional[str]) -> set:
    """Display names of the theme's linked files in group (None: ungrouped): taken for a file there."""
    from sonorium.core.theme_presets import METADATA_FILE
    links = (_read_doc(folder / METADATA_FILE) or {}).get("links")
    names = set()
    for key in links if isinstance(links, dict) else ():
        if isinstance(key, str):
            g, sep, name = key.rpartition("/")
            if (g if sep else None) == group:
                names.add(name)
    return names


def _copy_into(entries, old: str, new: str) -> bool:
    if isinstance(entries, dict) and old in entries and new not in entries:
        entries[new] = copy.deepcopy(entries[old])
        return True
    return False


def copy_track_key(folder: Path, old: str, new: str) -> bool:
    """
    A file that shared key `old` with another file now has its own key `new`:
    it gets a copy of what `old` has in metadata.json (tracks) and presets.json
    (each preset's tracks, each sequence's transitions and While playing
    layers). Nothing that `new` already has is replaced. True when a file
    was written. Raises OSError.
    """
    from sonorium.core.theme_presets import METADATA_FILE, PRESETS_FILE, write_json_atomic
    changed = False
    meta = _read_doc(folder / METADATA_FILE)
    if meta is not None and _copy_into(meta.get("tracks"), old, new):
        write_json_atomic(folder / METADATA_FILE, meta)
        changed = True
    doc = _read_doc(folder / PRESETS_FILE)
    if doc is not None:
        touched = False
        for preset in (doc.get("presets") or {}).values() if isinstance(doc.get("presets"), dict) else ():
            if isinstance(preset, dict):
                touched |= _copy_into(preset.get("tracks"), old, new)
        for sequence in (doc.get("sequences") or {}).values() if isinstance(doc.get("sequences"), dict) else ():
            if not isinstance(sequence, dict):
                continue
            for step in (sequence.get("transitions") or {}).values() if isinstance(sequence.get("transitions"), dict) else ():
                for side in ("in", "out"):
                    if isinstance(step, dict):
                        touched |= _copy_into(step.get(side), old, new)
            for layer in (sequence.get("while") or {}).values() if isinstance(sequence.get("while"), dict) else ():
                if isinstance(layer, dict):
                    touched |= _copy_into(layer.get("tracks"), old, new)
        if touched:
            write_json_atomic(folder / PRESETS_FILE, doc)
            changed = True
    return changed


def _rename_in_manifest(folder: Path, old_rel: str, new_rel: str) -> None:
    """MANIFEST.json "channels"[]."local_filename" (THEME_FORMAT.md 6.4) follows a converted file."""
    path = folder / "MANIFEST.json"
    doc = _read_doc(path)
    channels = doc.get("channels") if doc else None
    if not isinstance(channels, list):
        return
    old_name, new_name = old_rel.rsplit("/", 1)[-1], new_rel.rsplit("/", 1)[-1]
    touched = False
    for channel in channels:
        if isinstance(channel, dict) and channel.get("local_filename") in (old_rel, old_name):
            channel["local_filename"] = new_rel if channel["local_filename"] == old_rel else new_name
            touched = True
    if touched:
        try:
            path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            logger.debug(f"MANIFEST.json of '{folder.name}' not updated: {e}")


def _finish(folder: Path, record: _Record, entry: dict, keep: bool) -> None:
    """After the MP3 is in place: the copied key, MANIFEST.json, the original deleted if not kept, entry done."""
    keys = entry.get("copy_key")
    if isinstance(keys, list) and len(keys) == 2:
        copy_track_key(folder, keys[0], keys[1])
    _rename_in_manifest(folder, entry["original"], entry["mp3"])
    if not keep:
        _unlink(folder / entry["kept_as"])
        entry["deleted"] = True
    entry.pop("temp", None)
    entry["state"] = "done"
    record.save()


def _recover(folder: Path, record: _Record, keep: bool) -> None:
    """Finish or undo a conversion a restart cut off, and remove MP3s left half-written."""
    for entry in list(record.entries):
        if entry.get("state") != "converting":
            continue
        try:
            target = folder / entry["mp3"]
            kept = folder / entry["kept_as"]
            temp = target.parent / entry["temp"] if entry.get("temp") else None
            if target.exists() and kept.exists():
                if temp is not None:
                    _unlink(temp)
                _finish(folder, record, entry, keep)
            elif kept.exists() and temp is not None and temp.exists():
                os.replace(temp, target)
                _finish(folder, record, entry, keep)
            else:
                # The original never moved, or the MP3 is gone: as before the conversion
                original = folder / entry["original"]
                if kept.exists() and not original.exists():
                    os.replace(kept, original)
                if temp is not None:
                    _unlink(temp)
                record.entries.remove(entry)
                record.save()
        except (OSError, KeyError, TypeError) as e:
            logger.warning(f"Theme '{folder.name}': an interrupted MP3 conversion couldn't be finished ({e})")
    for place in [folder] + [p for p in _subfolders(folder)]:
        try:
            for stale in place.glob(f"{TEMP_PREFIX}*"):
                _unlink(stale)
        except OSError:
            pass


def _subfolders(folder: Path) -> list[Path]:
    try:
        return [p for p in folder.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))]
    except OSError:
        return []


def _remove_kept_originals(folder: Path, record: _Record) -> None:
    """KEEP_ORIGINALS off: originals still kept are deleted."""
    changed = False
    for entry in record.entries:
        if entry.get("state") == "done" and not entry.get("deleted") and entry.get("kept_as"):
            _unlink(folder / entry["kept_as"])
            entry["deleted"] = True
            changed = True
    if changed:
        record.save()


def files_to_convert(folder: Path) -> list[Path]:
    """The theme's audio files that aren't standard MP3s: MP3s first (they keep their names), then the rest."""
    from sonorium.theme_files import theme_audio_files
    files = [f for f in theme_audio_files(Path(folder)) if not is_standard(f)]
    return sorted(files, key=lambda f: (f.suffix.lower() != TARGET_SUFFIX,))


def standardize_theme(folder: Path, keep_originals: Optional[bool] = None, pause: float = 0.0,
                      stop: Optional[threading.Event] = None,
                      progress: Optional[Callable[[int, int, str, str], None]] = None) -> ThemeResult:
    """
    Convert every audio file of the theme that isn't a standard MP3 (see the
    module notes). Idempotent: a theme of standard MP3s is left untouched.
    `stop`: set to end after the current file. `progress(i, n, from, to)`
    after each file. Never raises for a file: unreadable files are in
    .failed, files that couldn't be moved (in use) in .retry.
    """
    folder = Path(folder)
    keep = KEEP_ORIGINALS if keep_originals is None else bool(keep_originals)
    result = ThemeResult(folder)
    if not available():
        result.unavailable = True
        return result
    from sonorium.core import theme_groups
    from sonorium.theme_files import track_key
    with _theme_lock(folder):
        record = _Record(folder)
        _recover(folder, record, keep)
        if not keep:
            _remove_kept_originals(folder, record)
        todo = files_to_convert(folder)
        result.pending = len(todo)
        for i, source in enumerate(todo, 1):
            if stop is not None and stop.is_set():
                break
            if not source.exists():
                continue
            rel_source = _rel(folder, source)
            in_place = source.suffix.lower() == TARGET_SUFFIX
            parent = source.parent
            group = None if parent == folder else parent.name
            if in_place:
                target = source
            else:
                name = theme_groups.free_file_name(parent, source.stem + TARGET_SUFFIX, ignore=source,
                                                   reserved=_link_names(folder, group))
                target = parent / name
            temp = parent / f"{TEMP_PREFIX}{uuid.uuid4().hex[:12]}{TARGET_SUFFIX}"
            try:
                convert_file(source, temp, pause=pause)
            except ConvertError as e:
                result.failed.append((rel_source, str(e)))
                logger.warning(f"Theme '{folder.name}': '{rel_source}' couldn't be converted to MP3 ({e})")
                continue
            old_key, new_key = track_key(folder, source), track_key(folder, target)
            kept = _free_original_path(folder, rel_source)
            entry = {"original": rel_source, "kept_as": _rel(folder, kept), "mp3": _rel(folder, target),
                     "state": "converting", "temp": temp.name, "converted": _now()}
            if new_key != old_key:
                entry["copy_key"] = [old_key, new_key]
            try:
                record.entries.append(entry)
                record.save()
                kept.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, kept)
            except OSError as e:
                _unlink(temp)
                if entry in record.entries:
                    record.entries.remove(entry)
                    try:
                        record.save()
                    except OSError:
                        pass
                result.retry.append((rel_source, str(e)))
                logger.debug(f"Theme '{folder.name}': '{rel_source}' not converted yet ({e})")
                continue
            try:
                os.replace(temp, target)
            except OSError as e:
                # Put the original back where it was
                try:
                    os.replace(kept, source)
                except OSError:
                    pass
                _unlink(temp)
                record.entries.remove(entry)
                try:
                    record.save()
                except OSError:
                    pass
                result.retry.append((rel_source, str(e)))
                continue
            try:
                _finish(folder, record, entry, keep)
            except OSError as e:
                logger.warning(f"Theme '{folder.name}': '{rel_source}' converted, but its settings couldn't "
                               f"all be updated ({e})")
            result.converted.append((rel_source, entry["mp3"]))
            if new_key != old_key:
                result.renamed[old_key] = new_key
            if progress:
                progress(i, len(todo), rel_source, entry["mp3"])
    if result.changed:
        from sonorium.core import theme_presets
        theme_presets.theme_changed(folder)
    return result


# ---------------------------------------------------------------------------
# The server's background conversion
# ---------------------------------------------------------------------------

def audio_signature(folder: Path) -> str:
    """The theme's audio files by path, size and modification time: a new one when any of them changes."""
    from sonorium.theme_files import theme_audio_files
    lines = []
    for f in theme_audio_files(Path(folder)):
        try:
            st = f.stat()
        except OSError:
            continue
        lines.append(f"{_rel(folder, f)}\t{st.st_size}\t{st.st_mtime_ns}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _has_kept_originals(folder: Path) -> bool:
    return any(e.get("state") == "done" and not e.get("deleted") for e in _Record(folder).entries)


class Mp3Service:
    """
    Converts themes in the background (one worker thread, one theme and one
    file at a time, at low priority). request() puts a theme first (it just
    came in); request_all() checks every theme. A theme is skipped while its
    audio files are as they were when it was last found all standard
    (STATE_FILE in state_dir), so after the first pass a check costs one
    stat() per file. A pass cut off by a stop or restart starts again where
    it was: finished themes are remembered, files are converted one by one.
    """

    def __init__(self, audio_dir: Path, state_dir: Path,
                 on_converted: Optional[Callable[[Path, ThemeResult], None]] = None,
                 start_delay: float = 30.0, pause: float = 0.002, retry_after: float = 600.0,
                 keep_originals: Optional[bool] = None):
        self.audio_dir = Path(audio_dir)
        self.state_path = Path(state_dir) / STATE_FILE
        self.on_converted = on_converted
        self.start_delay = start_delay
        self.pause = pause
        self.retry_after = retry_after
        self.keep_originals = keep_originals
        self._cond = threading.Condition()
        self._urgent: "OrderedDict[str, Path]" = OrderedDict()
        self._backlog: "OrderedDict[str, Path]" = OrderedDict()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._busy = False
        self._first_pass = False  # started, the first check of every theme not queued yet
        self._retry_at: Optional[float] = None
        self._state = self._load_state()

    # --- state --------------------------------------------------------------

    def _load_state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            themes = data.get("themes") if isinstance(data, dict) else None
            return {k: v for k, v in (themes or {}).items() if isinstance(v, dict)}
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_name(self.state_path.name + ".tmp")
            tmp.write_text(json.dumps({"themes": self._state}, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError as e:
            logger.debug(f"MP3 conversion state not saved: {e}")

    def is_done(self, folder: Path) -> bool:
        """The theme was found all standard and its audio files haven't changed since."""
        entry = self._state.get(Path(folder).name)
        return bool(entry and entry.get("done") and entry.get("signature") == audio_signature(folder))

    # --- queue --------------------------------------------------------------

    def _theme_folders(self) -> list[Path]:
        try:
            return sorted(p for p in self.audio_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            return []

    def request(self, folder, urgent: bool = True) -> None:
        """Convert this theme next (urgent) or after the ones waiting."""
        folder = Path(folder)
        key = str(folder)
        with self._cond:
            if urgent:
                self._backlog.pop(key, None)
                self._urgent[key] = folder
            elif key not in self._urgent:
                self._backlog.setdefault(key, folder)
            self._cond.notify_all()

    def request_all(self) -> None:
        """Check every theme (after a rescan: files may have come in by hand)."""
        for folder in self._theme_folders():
            self.request(folder, urgent=False)

    def start(self) -> None:
        if self._thread is not None:
            return
        if not available():
            logger.warning("Theme audio can't be converted to MP3 here: PyAV has no MP3 encoder. "
                           "Themes stay in their own formats")
            return
        self._first_pass = True
        self._thread = threading.Thread(target=self._run, name="theme-mp3", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Until nothing is waiting or being converted (tests)."""
        end = time.monotonic() + timeout
        with self._cond:
            while self._urgent or self._backlog or self._busy or self._first_pass:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(min(left, 0.1))
        return True

    # --- worker -------------------------------------------------------------

    @staticmethod
    def _lower_priority() -> None:
        """This thread only, where the system allows it (Linux: per thread)."""
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
        except (AttributeError, OSError):
            pass

    def _next(self) -> Optional[tuple[Path, bool]]:
        with self._cond:
            while not self._stop.is_set():
                if self._urgent:
                    _, folder = self._urgent.popitem(last=False)
                    self._busy = True
                    return folder, True
                if self._backlog:
                    _, folder = self._backlog.popitem(last=False)
                    self._busy = True
                    return folder, False
                timeout = None
                if self._retry_at is not None:
                    timeout = self._retry_at - time.monotonic()
                    if timeout <= 0:
                        self._retry_at = None
                        self.request_all()  # the condition's lock is re-entrant
                        continue
                self._cond.wait(timeout)
            return None

    def _run(self) -> None:
        self._lower_priority()
        if self.start_delay and self._stop.wait(self.start_delay):
            with self._cond:
                self._first_pass = False
            return
        folders = self._theme_folders()
        waiting = [f for f in folders if not self.is_done(f)]
        if waiting:
            logger.info(f"Checking {len(waiting)} theme(s) for audio to convert to MP3, in the background")
        self.request_all()
        with self._cond:
            self._first_pass = False
        checked = converted = 0
        while True:
            item = self._next()
            if item is None:
                return
            folder, urgent = item
            try:
                result = self._convert_theme(folder)
                checked += 1
                if result is not None and result.changed:
                    converted += 1
            except Exception as e:
                logger.error(f"Theme '{folder.name}': MP3 conversion failed ({e})")
            finally:
                with self._cond:
                    self._busy = False
                    idle = not self._urgent and not self._backlog
                    self._cond.notify_all()
            if idle and checked and converted:
                logger.info(f"MP3 conversion: {converted} theme(s) converted")
                checked = converted = 0
            elif idle:
                checked = converted = 0

    def _convert_theme(self, folder: Path) -> Optional[ThemeResult]:
        if not folder.is_dir():
            self._state.pop(folder.name, None)
            return None
        keep = KEEP_ORIGINALS if self.keep_originals is None else self.keep_originals
        if self.is_done(folder) and (keep or not _has_kept_originals(folder)):
            return None
        todo = files_to_convert(folder)
        name = folder.name
        if todo:
            logger.info(f"Theme '{name}': converting {len(todo)} audio file(s) to MP3 "
                        f"(320 kb/s, 44.1 kHz, stereo)")

        def progress(i, n, src, dst):
            logger.info(f"Theme '{name}': '{src}' converted to '{dst}' ({i} of {n})")

        result = standardize_theme(folder, keep_originals=keep, pause=self.pause, stop=self._stop,
                                   progress=progress)
        for old, new in result.renamed.items():
            logger.info(f"Theme '{name}': track '{old}' has two files; the converted one is now '{new}', "
                        f"with the same settings")
        if result.converted:
            kept = f"; the originals are kept in {ORIGINALS_FOLDER}" if keep else ""
            logger.info(f"Theme '{name}': {len(result.converted)} of {len(todo)} file(s) converted to MP3{kept}")
        stopped = self._stop.is_set() and len(result.converted) + len(result.failed) + len(result.retry) < len(todo)
        if result.retry:
            minutes = max(1, round(self.retry_after / 60))
            logger.info(f"Theme '{name}': {len(result.retry)} file(s) are in use and not converted yet; "
                        f"trying again in {minutes} minute(s)")
            with self._cond:
                self._retry_at = time.monotonic() + self.retry_after
        if not result.retry and not stopped:
            # Unreadable files stay as they are until they change (they still count in the signature)
            self._state[name] = {"signature": audio_signature(folder), "done": True, "checked": _now()}
        else:
            self._state.pop(name, None)
        self._save_state()
        if result.changed and self.on_converted is not None:
            try:
                self.on_converted(folder, result)
            except Exception as e:
                logger.debug(f"After converting '{name}': {e}")
        return result


# The server's service (set by the server; None elsewhere, e.g. in the app)
_service: Optional[Mp3Service] = None


def set_service(service: Optional[Mp3Service]) -> None:
    global _service
    _service = service


def request(folder) -> None:
    """A theme came in or changed: convert it next (nothing happens without the server's service)."""
    if _service is not None and folder:
        try:
            _service.request(Path(folder), urgent=True)
        except Exception as e:
            logger.debug(f"MP3 conversion not requested for {folder}: {e}")
