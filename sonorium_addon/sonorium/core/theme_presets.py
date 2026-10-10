"""
Theme files on disk (Themes 2.0).

A theme folder holds its audio files plus:
  metadata.json  - theme id, name, track settings, spec_version, groups
  presets.json   - {"presets": {preset_id: {...}}}; other top-level keys
                   (e.g. a future "sequences") are kept untouched

Themes 1.0 kept presets inside metadata.json. load_presets() still reads
those, so a folder that could not be converted keeps working.

Every write is atomic: temp file, read back and compare, then os.replace.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from sonorium.obs import logger

METADATA_FILE = "metadata.json"
PRESETS_FILE = "presets.json"
SPEC_VERSION = 2


class BrokenJsonError(ValueError):
    """A theme JSON file that can't be parsed."""

    def __init__(self, path: Path, detail: str, line: Optional[int] = None):
        self.path = Path(path)
        self.detail = detail
        self.line = line
        where = f" (line {line})" if line else ""
        super().__init__(f"{self.path.name}{where}: {detail}")


def read_json(path: Path) -> Optional[dict]:
    """A JSON object from path, None if the file is missing; BrokenJsonError if unreadable."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as e:
        raise BrokenJsonError(path, f"not UTF-8 text ({e.reason})") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise BrokenJsonError(path, e.msg, e.lineno) from e
    if not isinstance(data, dict):
        raise BrokenJsonError(path, "expected a JSON object")
    return data


def write_json_atomic(path: Path, data: dict) -> None:
    """Write data as JSON via a temp file that is read back and checked. Raises OSError on failure."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(data, indent=2, ensure_ascii=False)
    try:
        tmp.write_text(text, encoding="utf-8")
        if json.loads(tmp.read_text(encoding="utf-8")) != json.loads(text):
            raise OSError(f"{tmp.name} did not read back the same")
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def keep_broken(path: Path) -> Optional[Path]:
    """Rename a broken file to '<name>.broken-<YYYYMMDD-HHMMSS>'; None if it couldn't be renamed."""
    path = Path(path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.broken-{stamp}")
    counter = 1
    while target.exists():
        target = path.with_name(f"{path.name}.broken-{stamp}-{counter}")
        counter += 1
    try:
        os.replace(path, target)
        return target
    except OSError as e:
        logger.warning(f"Could not rename broken file {path}: {e}")
        return None


def _presets_of(doc: Optional[dict]) -> dict:
    presets = (doc or {}).get("presets")
    return dict(presets) if isinstance(presets, dict) else {}


def load_presets_doc(folder: Path) -> dict:
    """The whole presets.json document ({} if missing). Raises BrokenJsonError if unreadable."""
    doc = read_json(Path(folder) / PRESETS_FILE)
    if doc is None:
        return {}
    if "presets" in doc and not isinstance(doc["presets"], dict):
        raise BrokenJsonError(Path(folder) / PRESETS_FILE, '"presets" is not an object')
    return doc


def legacy_presets(folder: Path) -> dict:
    """Presets still stored in a 1.0 metadata.json ({} if none or unreadable)."""
    try:
        return _presets_of(read_json(Path(folder) / METADATA_FILE))
    except BrokenJsonError:
        return {}


def load_presets(folder: Path) -> dict:
    """
    A theme's presets ({preset_id: data}). Reads presets.json; presets still
    in a 1.0 metadata.json are included, presets.json wins on the same id.
    Never raises for bad files (they are logged and skipped).
    """
    folder = Path(folder)
    try:
        current = _presets_of(load_presets_doc(folder))
    except BrokenJsonError as e:
        logger.warning(f"Theme folder '{folder.name}': {e}")
        current = {}
    merged = legacy_presets(folder)
    merged.update(current)
    return merged


def save_presets(folder: Path, presets: dict) -> None:
    """Write presets to presets.json, keeping its other top-level keys. Raises OSError on failure."""
    folder = Path(folder)
    try:
        doc = load_presets_doc(folder)
    except BrokenJsonError:
        doc = {}
    doc = dict(doc)
    doc["presets"] = dict(presets or {})
    write_json_atomic(folder / PRESETS_FILE, doc)


def recover_theme_id(path: Path) -> Optional[str]:
    """The theme id from a broken metadata.json, if it can still be found."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r'"id"\s*:\s*"([^"\\]{1,100})"', text)
    return match.group(1) if match else None
