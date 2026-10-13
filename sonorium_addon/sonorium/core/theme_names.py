"""
Theme names: no two themes share one.

Two names are the same when they match with the spaces around them trimmed
and case ignored ("Rain", " rain "). A theme's name is metadata.json "name",
or its folder's name when it has none (as the theme loads). The themes are
the folders in the themes folder that the theme scan loads: not hidden
("."), with metadata.json or audio files.

Create, rename, import and duplicate all refuse a name that is taken
(NameTaken, HTTP 409, TAKEN_MESSAGE). A name being given right now by
another request (an import still extracting, a copy still copying) counts as
taken too: reserve() holds it until that request is done.

A duplicate's suggested name is "<name> 01", the lowest free number from 01
(two digits; 100 and up keep counting): next_copy_name().
"""
from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

TAKEN_MESSAGE = "A theme with this name already exists"

_lock = threading.Lock()
_reserved: dict[str, int] = {}  # name key -> requests holding it


class NameTaken(ValueError):
    """The name belongs to another theme. status: the HTTP status the server answers with."""
    status = 409

    def __init__(self, message: str = TAKEN_MESSAGE):
        super().__init__(message)


def name_key(name) -> str:
    """The form names are compared in: trimmed, case ignored."""
    return name.strip().casefold() if isinstance(name, str) else ""


def theme_name(folder: Path) -> str:
    """A theme folder's name as shown: metadata.json "name", else the folder's name."""
    folder = Path(folder)
    try:
        raw = json.loads((folder / "metadata.json").read_text(encoding="utf-8-sig"))
        name = raw.get("name") if isinstance(raw, dict) else None
        if isinstance(name, str) and name.strip():
            return name
    except (OSError, ValueError):
        pass
    return folder.name


def _is_theme(folder: Path) -> bool:
    if not folder.is_dir() or folder.name.startswith("."):
        return False
    if (folder / "metadata.json").is_file():
        return True
    from sonorium.theme_files import has_audio
    return has_audio(folder)


def theme_names(root: Path, except_folder: Optional[Path] = None) -> dict[str, Path]:
    """{name key: folder} of every theme in root, but except_folder."""
    root = Path(root)
    skip = Path(except_folder).resolve() if except_folder is not None else None
    names: dict[str, Path] = {}
    try:
        folders = sorted(root.iterdir())
    except OSError:
        return names
    for folder in folders:
        if not _is_theme(folder) or (skip is not None and folder.resolve() == skip):
            continue
        names.setdefault(name_key(theme_name(folder)), folder)
    return names


def taken_keys(root: Path, except_folder: Optional[Path] = None) -> set[str]:
    """The name keys in use in root (but except_folder's), and those being given right now."""
    with _lock:
        reserved = set(_reserved)
    return set(theme_names(root, except_folder)) | reserved


def is_taken(root: Path, name: str, except_folder: Optional[Path] = None) -> bool:
    return name_key(name) in taken_keys(root, except_folder)


def check_free(root: Path, name: str, except_folder: Optional[Path] = None) -> None:
    """Raises NameTaken when another theme in root has the name (or it is being given)."""
    if is_taken(root, name, except_folder):
        raise NameTaken()


@contextmanager
def reserve(root: Path, name: str, except_folder: Optional[Path] = None):
    """
    The name held for the request inside the block: checked against root's
    themes and every name held now (NameTaken), then held until the block ends.
    """
    key = name_key(name)
    with _lock:
        if key in _reserved or key in theme_names(root, except_folder):
            raise NameTaken()
        _reserved[key] = _reserved.get(key, 0) + 1
    try:
        yield
    finally:
        with _lock:
            count = _reserved.get(key, 0) - 1
            if count > 0:
                _reserved[key] = count
            else:
                _reserved.pop(key, None)


def next_copy_name(name: str, taken: Iterable[str]) -> str:
    """'<name> 01', '<name> 02', ...: the lowest number whose name isn't in taken (name keys)."""
    base = name.strip() if isinstance(name, str) else ""
    taken = {name_key(t) for t in taken}
    n = 1
    while True:
        candidate = f"{base} {n:02d}"
        if name_key(candidate) not in taken:
            return candidate
        n += 1


def folder_name_for(name: str) -> str:
    """
    The folder a new theme gets from its name (as Create theme makes it):
    lower case, only letters, digits, "_", "-" and spaces kept, runs of
    spaces and "-" as "_". "" when nothing is left.
    """
    folder = re.sub(r'[^\w\s-]', '', (name or "").lower())
    return re.sub(r'[-\s]+', '_', folder).strip('_')


def free_folder(root: Path, base: str) -> Path:
    """root/base, or root/base_1, base_2, ... when it exists (as an import's folder)."""
    root = Path(root)
    target, counter = root / base, 1
    while target.exists():
        target = root / f"{base}_{counter}"
        counter += 1
    return target
