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
