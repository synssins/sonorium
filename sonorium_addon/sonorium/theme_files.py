"""
Which files in a theme folder are tracks (Themes 2.0).

- Audio files at the top of the theme folder are tracks: "Fireplace.wav".
- Each subfolder holding audio is a **group**, and its audio files are tracks
  in that group: "Lute/Lute song 1.wav". A group decides when its tracks play,
  and only one of them plays at a time; each file stays its own track.
- One level deep only. Folders starting with "." or "_" are ignored.

A track's key (used in metadata.json, presets and the API) is its path in the
theme without the extension, with "/" between folder and file:
"Fireplace", "Lute/Lute song 1". Older themes (no subfolders) keep exactly the
keys they had.
"""
from pathlib import Path
from typing import Optional

AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".flac", ".ogg"})


def is_audio_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS


def _ignored(path: Path) -> bool:
    return path.name.startswith((".", "_"))


def group_folders(theme_folder: Path) -> list[Path]:
    """The theme's group folders: subfolders with at least one audio file."""
    try:
        subfolders = sorted(p for p in theme_folder.iterdir() if p.is_dir() and not _ignored(p))
    except OSError:
        return []
    return [p for p in subfolders if any(is_audio_file(f) for f in p.iterdir())]


def theme_audio_files(theme_folder: Path) -> list[Path]:
    """Every track file in a theme: top level first, then each group's, sorted by name."""
    try:
        top = sorted(p for p in theme_folder.iterdir() if is_audio_file(p) and not _ignored(p))
    except OSError:
        return []
    grouped = [f for group in group_folders(theme_folder)
               for f in sorted(p for p in group.iterdir() if is_audio_file(p) and not _ignored(p))]
    return top + grouped


def has_audio(theme_folder: Path) -> bool:
    return bool(theme_audio_files(theme_folder))


def track_group(theme_folder: Path, path: Path) -> Optional[str]:
    """The group a track file is in, or None for a top-level track."""
    relative = path.relative_to(theme_folder)
    return relative.parts[0] if len(relative.parts) > 1 else None


def track_key(theme_folder: Path, path: Path) -> str:
    """'Fireplace' or 'Lute/Lute song 1'."""
    group = track_group(theme_folder, path)
    return f"{group}/{path.stem}" if group else path.stem


def track_display_name(key: str) -> str:
    """What the UI shows for a track: the file name without its group."""
    return key.rsplit("/", 1)[-1]
