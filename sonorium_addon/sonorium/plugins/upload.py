"""
Installing a plugin from an uploaded zip.

The zip is read and checked in full first (entry names, layout, manifest,
id, version, plugin.py) into a folder of its own; only a plugin that passes
replaces the installed one, and the swap puts the installed folder back if
it fails. A refused upload leaves the installed plugin as it was.

Layouts: the plugin's files inside one top folder, or at the zip's root.
"""

import json
import re
import zipfile
from pathlib import Path
from typing import Optional

from sonorium.plugins.loader import _replace_plugin_dir, is_valid_plugin_id

SEMVER = re.compile(r'^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$')
CLASS_VERSION = re.compile(r'^\s*version\s*[=:]\s*["\']([^"\']+)["\']', re.MULTILINE)
IGNORED_TOP = ("__MACOSX",)


class UploadError(ValueError):
    """A zip that can't be installed (the message is shown to the user)."""


def entry_parts(name: str) -> list[str]:
    """
    A zip entry name as path parts ([] for a folder entry like "a/").
    UploadError for ".." or an absolute path.
    """
    clean = name
    if clean.startswith("/"):
        raise UploadError(f"Invalid path in zip: {name}")
    parts = [p for p in clean.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise UploadError(f"Invalid path in zip: {name}")
    return parts


def _plugin_files(zf: zipfile.ZipFile) -> tuple[dict, Optional[str]]:
    """
    {relative path parts: zip entry} for the plugin's files, and the top
    folder's name (None when the files are at the zip's root).
    """
    files = {}
    for info in zf.infolist():
        parts = entry_parts(info.filename)
        if not parts or info.is_dir():
            continue
        if parts[0] in IGNORED_TOP:
            continue
        files[tuple(parts)] = info
    if ("plugin.py",) in files:
        return {parts: info for parts, info in files.items() if len(parts) == 1}, None
    tops = {parts[0] for parts in files}
    if len(tops) == 1 and all(len(parts) > 1 for parts in files):
        top = tops.pop()
        if (top, "plugin.py") in files:
            return {parts[1:]: info for parts, info in files.items()}, top
    raise UploadError("No plugin.py found in archive (put it at the zip's root or in one top folder)")


def _plugin_id(manifest: dict, top: Optional[str], zip_name: str) -> str:
    raw = manifest.get("id") if manifest.get("id") is not None else top
    if raw is None:
        raw = Path(zip_name or "").stem.lower().replace(" ", "_").replace("-", "_")
    if not isinstance(raw, str):
        raise UploadError("The manifest's 'id' must be text")
    plugin_id = "".join(c for c in raw if c.isalnum() or c == "_").lower() or "imported_plugin"
    if not is_valid_plugin_id(plugin_id):
        raise UploadError(f"Invalid plugin id '{raw}'")
    return plugin_id


def _check_version(plugin_py: str, manifest: dict) -> str:
    match = CLASS_VERSION.search(plugin_py)
    version = match.group(1) if match else manifest.get("version")
    if not version:
        raise UploadError("Plugin must define a 'version' attribute (e.g., version = \"1.0.0\"). "
                          "Use semantic versioning: MAJOR.MINOR.PATCH")
    if not isinstance(version, str) or not SEMVER.match(version):
        raise UploadError(f"Invalid version format '{version}'. "
                          f"Use semantic versioning: MAJOR.MINOR.PATCH (e.g., 1.0.0, 2.1.3-beta)")
    return version


def prepare(zip_path: Path, staging: Path, zip_name: str = "") -> str:
    """
    Read and check the zip, writing the plugin's files into staging (an
    empty folder). The plugin id; UploadError (or zipfile.BadZipFile) when
    it can't be installed.
    """
    staging = Path(staging)
    root = staging.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        files, top = _plugin_files(zf)
        for parts, info in files.items():
            target = staging.joinpath(*parts)
            if not target.resolve().is_relative_to(root):
                raise UploadError(f"Invalid path in zip: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                while chunk := src.read(1 << 16):
                    dst.write(chunk)
    manifest = {}
    manifest_file = staging / "manifest.json"
    if manifest_file.exists():
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as e:
            raise UploadError(f"manifest.json is not valid JSON: {e}")
        if not isinstance(manifest, dict):
            raise UploadError("manifest.json must be a JSON object")
    try:
        plugin_py = (staging / "plugin.py").read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise UploadError("plugin.py must be UTF-8 text")
    plugin_id = _plugin_id(manifest, top, zip_name)
    _check_version(plugin_py, manifest)
    return plugin_id


def install(staging: Path, target_dir: Path) -> None:
    """
    Put the checked plugin in place: copied beside the installed folder,
    then swapped in; the installed folder is put back if the swap fails.
    """
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    _replace_plugin_dir(Path(staging), target_dir)
