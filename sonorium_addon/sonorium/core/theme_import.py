"""
Where a theme zip's files go when it is imported (POST /api/themes/import).

Two layouts are accepted:

- one top folder holding the theme, as Export and "zip the folder" make it:
  Thunderstorm/metadata.json, Thunderstorm/Rain.wav, Thunderstorm/Wind/Wind 1.wav
  The theme folder is named after the top folder.
- the theme's files at the root of the zip, as "zip the contents" makes it:
  metadata.json, Rain.wav, Wind/Wind 1.wav
  The theme folder is named after the theme (metadata.json's "name"), else
  after the zip file.

In both, group folders keep their place inside the theme. Folders and files
a computer adds by itself (__MACOSX/, .DS_Store, Thumbs.db, desktop.ini) are
left out and don't count as a top folder. A path that would leave the theme
folder (absolute, a drive, "..") makes the whole zip invalid.

Only a theme's kinds of files are extracted (IMPORT_EXTENSIONS; the rest
are left out and listed), at most a number of files and bytes unpacked
(Settings > Advanced import_file_limit, import_unpacked_limit_mb), counted
as they are copied: a zip's own sizes can lie.
"""

from __future__ import annotations

from typing import Optional

_JUNK_FOLDERS = {"__MACOSX"}
_JUNK_FILES = {".ds_store", "thumbs.db", "desktop.ini"}
_NOT_IN_NAMES = '/\\:*?"<>|'
MAX_FOLDER_NAME = 100
DEFAULT_FOLDER = "Imported theme"


class ZipLayoutError(ValueError):
    """A zip that can't be imported as a theme: the message says why."""


def _parts(member: str) -> list[str]:
    """A zip member's path as its parts ("" and "." dropped). ZipLayoutError for one leaving the theme."""
    path = member.replace("\\", "/")
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if path.startswith("/") or any(p == ".." or ":" in p for p in parts):
        raise ZipLayoutError(f"Invalid path in zip: {member}")
    return parts


def folder_name(text) -> str:
    """
    A theme folder name from a theme's or a file's name: characters a file
    system refuses become "_", no leading "." (a hidden folder isn't a theme),
    no trailing "." or space, at most MAX_FOLDER_NAME characters. "" if
    nothing is left.
    """
    if not isinstance(text, str):
        return ""
    name = "".join("_" if c in _NOT_IN_NAMES or ord(c) < 32 else c for c in text).strip()
    name = name.lstrip(".").strip()[:MAX_FOLDER_NAME].rstrip(". ")
    return "" if name.strip("_") == "" else name


def _zip_stem(zip_filename) -> str:
    name = str(zip_filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    return name[:-4] if name.lower().endswith(".zip") else name


def root_metadata_member(names) -> Optional[str]:
    """The zip member holding metadata.json at the root of the zip, if there is one."""
    for member in names:
        try:
            parts = _parts(member)
        except ZipLayoutError:
            continue
        if parts == ["metadata.json"] and not member.endswith("/"):
            return member
    return None


def plan_import(names, zip_filename, theme_name: Optional[str] = None) -> tuple[str, list[tuple[str, str]]]:
    """
    The theme folder's name and each file to extract, as (zip member, path
    inside the theme folder with "/"), for the zip members `names`.
    `theme_name` (the root metadata.json's "name") names the folder of a zip
    without a top folder. Raises ZipLayoutError.
    """
    files = []
    for member in names:
        if member.endswith("/") or member.endswith("\\"):
            continue  # a folder entry: folders come with their files
        parts = _parts(member)
        if not parts or parts[0] in _JUNK_FOLDERS or parts[-1].casefold() in _JUNK_FILES:
            continue
        files.append((member, parts))
    if not files:
        raise ZipLayoutError("The zip holds no files")

    tops = {parts[0] for _, parts in files}
    if len(tops) == 1 and all(len(parts) > 1 for _, parts in files):
        top = next(iter(tops))
        folder = folder_name(top) or folder_name(_zip_stem(zip_filename)) or DEFAULT_FOLDER
        return folder, [(member, "/".join(parts[1:])) for member, parts in files]

    folder = folder_name(theme_name) or folder_name(_zip_stem(zip_filename)) or DEFAULT_FOLDER
    return folder, [(member, "/".join(parts)) for member, parts in files]


# What a theme zip may bring: audio, the theme's JSON files, notes and pictures
IMPORT_EXTENSIONS = frozenset({".mp3", ".wav", ".flac", ".ogg", ".json", ".txt", ".md",
                               ".png", ".jpg", ".jpeg", ".webp", ".gif"})
COPY_CHUNK = 1024 * 1024


class ImportLimitError(ValueError):
    """Over a limit (413) or not importable (400): the message says why."""

    def __init__(self, message: str, status: int = 413):
        super().__init__(message)
        self.status = status


def _mb(limit: int) -> str:
    return f"{limit / (1024 * 1024):g} MB"


def check_members(zip_file, members, file_limit: Optional[int], unpacked_limit: Optional[int]):
    """
    (members kept, paths left out): only IMPORT_EXTENSIONS; ImportLimitError
    past file_limit files or unpacked_limit bytes as the zip states them
    (None: no limit).
    """
    import posixpath
    kept, left_out = [], []
    for member, relative in members:
        if posixpath.splitext(relative)[1].lower() in IMPORT_EXTENSIONS:
            kept.append((member, relative))
        else:
            left_out.append(relative)
    if not kept:
        raise ImportLimitError("The zip holds no theme files", 400)
    if file_limit is not None and len(kept) > file_limit:
        raise ImportLimitError(f"Too many files: at most {file_limit}")
    if unpacked_limit is not None:
        total = sum(zip_file.getinfo(member).file_size for member, _ in kept)
        if total > unpacked_limit:
            raise ImportLimitError(f"Too large unpacked: at most {_mb(unpacked_limit)}")
    return kept, left_out


def extract_members(zip_file, members, target, unpacked_limit: Optional[int]) -> int:
    """
    Copy members into the folder `target` in chunks, counting the bytes
    (ImportLimitError past unpacked_limit); every path stays inside target.
    The number of files written. The caller removes target on failure.
    """
    from pathlib import Path
    target = Path(target)
    root = target.resolve()
    total = 0
    for member, relative in members:
        output = target / relative
        if root not in output.resolve().parents:
            raise ImportLimitError(f"Invalid path in zip: {member}", 400)
        output.parent.mkdir(parents=True, exist_ok=True)
        with zip_file.open(member) as src, open(output, "wb") as out:
            while True:
                chunk = src.read(COPY_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if unpacked_limit is not None and total > unpacked_limit:
                    raise ImportLimitError(f"Too large unpacked: at most {_mb(unpacked_limit)}")
                out.write(chunk)
    return len(members)
