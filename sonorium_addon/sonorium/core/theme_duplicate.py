"""
Duplicate a theme (POST /api/themes/{id}/duplicate): a full, independent copy.

The copy gets its own id and its own folder, named from the new name as
Create theme names one (core/theme_names.py; "_1", "_2", ... when the folder
exists). Everything in the theme folder is copied: the audio files and
groups, metadata.json (track and group settings, links, categories,
description, icon, attribution, plugin attributes) and presets.json
(presets and sequences). metadata.json gets the new id and name, and
is_favorite false.

Linked files stay links: a link is an entry in metadata.json pointing at a
file of another theme (theme id + track), so the copy links to the same
files. Left out: "_originals" (the audio kept from before the MP3
conversion, core/theme_mp3.py; never played) and the MP3 converter's
unfinished files. The source is copied under its MP3 lock, so a conversion
running on it is never copied halfway.

The copy is made in a hidden folder (the theme scan skips it) and renamed
into place when complete; on failure nothing is left behind (after a crash,
the start-up clean-up removes it).
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from sonorium.core import theme_names, theme_presets
from sonorium.obs import logger

# The hidden staging name of a new theme: one a crash left is removed at start-up
# (plugins/theme_api.py remove_stale_staging)
STAGING_PREFIX = ".sonorium-new-"


class DuplicateError(ValueError):
    """The copy couldn't be made. status: the HTTP status the server answers with."""
    status = 400

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _ignore(source: Path):
    from sonorium.core import theme_mp3
    top = Path(source).resolve()

    def ignore(directory, names):
        here = Path(directory).resolve()
        skip = {n for n in names if n.startswith(theme_mp3.TEMP_PREFIX)}
        if here == top and theme_mp3.ORIGINALS_FOLDER in names:
            skip.add(theme_mp3.ORIGINALS_FOLDER)
        return skip
    return ignore


def _copy(source: Path, target: Path) -> None:
    from sonorium.core import theme_mp3
    with theme_mp3._theme_lock(source):
        shutil.copytree(source, target, ignore=_ignore(source))


def _set_identity(folder: Path, name: str) -> str:
    """The copy's metadata.json: a new id, the new name, not a favorite. Returns the id."""
    new_id = str(uuid.uuid4())
    path = folder / theme_presets.METADATA_FILE
    try:
        raw = theme_presets.read_json(path)
    except theme_presets.BrokenJsonError:
        raw = None
    if raw is not None:
        # As it is, every other key kept
        raw.update({"id": new_id, "name": name, "is_favorite": False})
        theme_presets.write_json_atomic(path, raw)
        return new_id
    # No metadata.json (a folder of audio files) or a broken one: as the theme loads it
    from sonorium.core.theme_metadata import load_theme_folder, save_theme_folder
    metadata = load_theme_folder(folder)
    metadata.id, metadata.name, metadata.is_favorite = new_id, name, False
    if not save_theme_folder(folder, metadata):
        raise DuplicateError("Couldn't save the theme files", 500)
    return new_id


def duplicate(source: Path, name: str) -> dict:
    """
    A copy of the theme in source, beside it, named name. Returns {"theme_id",
    "name", "folder"}. Raises DuplicateError (no name, no folder name from it,
    copy failed) or theme_names.NameTaken.
    """
    source = Path(source)
    root = source.parent
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        raise DuplicateError("Name is required")
    folder_name = theme_names.folder_name_for(name)
    if not folder_name:
        raise DuplicateError("Invalid theme name - could not generate folder name")

    with theme_names.reserve(root, name):
        staging = root / f"{STAGING_PREFIX}{uuid.uuid4().hex[:12]}"
        try:
            _copy(source, staging)
            new_id = _set_identity(staging, name)
            target = theme_names.free_folder(root, folder_name)
            os.rename(staging, target)
        except DuplicateError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except Exception as e:
            shutil.rmtree(staging, ignore_errors=True)
            logger.error(f"Couldn't duplicate theme '{source.name}': {e}")
            raise DuplicateError(f"Couldn't copy the theme: {e}", 500) from e

    logger.info(f"Duplicated theme '{source.name}' as '{name}' ({target.name})")
    return {"theme_id": new_id, "name": name, "folder": target}
