"""
What a plugin may bring besides its Python files: Python packages from PyPI,
and programs (binaries) per platform.

Manifest:

    "requirements": ["pyserial==3.5", "requests>=2.31"],
    "binaries": {
        "ffmpeg": {
            "linux-x86_64":  {"path": "bin/linux-x86_64/ffmpeg",  "sha256": "..."},
            "linux-aarch64": {"path": "bin/linux-aarch64/ffmpeg", "sha256": "..."},
            "windows-amd64": {"path": "bin/windows-amd64/ffmpeg.exe", "sha256": "..."}
        }
    }

Python packages (requirements)
- Off unless Settings > Advanced > Plugin packages is switched on: a package
  runs with Sonorium's rights, like the plugin itself, and comes from the
  internet. A plugin that needs packages while it's off is refused with that
  message.
- Approved by the user with the plugin's permissions (permissions.py): the
  approval lists each package and version, and nothing is installed (and
  plugin.py isn't imported) before it. An update that adds or changes a
  package asks again.
- Each requirement is a package name with optional extras and version
  conditions; no URLs, paths or pip options.
- Installed with pip, wheels only (no package builds or setup scripts run at
  install time), into the plugin's own folder
  <config>/plugin_packages/<plugin id>/, outside the plugin's folder so an
  update of the plugin can keep them. Installed again when the requirements
  or the Python version change.
- That folder is added to the END of Python's search path: Sonorium's own
  packages always win, so a plugin can't replace them.

Binaries
- Shipped inside the plugin's zip. Each is checked when the plugin loads:
  the path stays inside the plugin's folder (no links out of it) and the
  file's SHA-256 matches the manifest. A mismatch refuses the plugin.
- Made executable where the file system allows it. self.binary("ffmpeg")
  is the path for this platform, or None when the plugin has none for it.

Install types
- Home Assistant app: /config is writable and kept across updates; the image
  is Alpine Linux (musl), so packages need musllinux wheels. Binaries must be
  built for musl too (or static).
- Docker: the config volume is writable; Debian (glibc) image, manylinux wheels.
- Windows / Linux / macOS (native): the config folder; the platform's wheels.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Optional

from sonorium.obs import logger

MAX_REQUIREMENTS = 30
PIP_TIMEOUT_SECONDS = 600
MARKER = ".sonorium-packages.json"
_OP = r"(?:==|!=|>=|<=|~=|>|<)"
_VER = r"[A-Za-z0-9.*+!_-]+"
REQUIREMENT = re.compile(
    rf"^[A-Za-z0-9](?:[A-Za-z0-9._-]{{0,98}}[A-Za-z0-9])?(?:\[[A-Za-z0-9._,-]{{1,100}}\])?"
    rf"(?:\s*{_OP}\s*{_VER}(?:\s*,\s*{_OP}\s*{_VER})*)?$")


class PackageError(ValueError):
    """Packages or binaries that can't be used (the message is shown to the user)."""


def platform_key() -> str:
    """This machine as binaries are keyed: linux-x86_64, linux-aarch64, windows-amd64, darwin-arm64, ..."""
    system = sys.platform
    system = "windows" if system.startswith("win") else "darwin" if system == "darwin" else "linux" \
        if system.startswith("linux") else system
    machine = platform.machine().lower() or "unknown"
    machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine, machine) \
        if system == "linux" else {"x86_64": "amd64", "x64": "amd64"}.get(machine, machine) \
        if system == "windows" else {"aarch64": "arm64"}.get(machine, machine)
    return f"{system}-{machine}"


# --- Python packages ---

def check_requirements(requirements) -> list[str]:
    if requirements in (None, []):
        return []
    if not isinstance(requirements, list) or len(requirements) > MAX_REQUIREMENTS:
        raise PackageError(f"'requirements' must be a list of at most {MAX_REQUIREMENTS} package names")
    clean = []
    for item in requirements:
        if not isinstance(item, str) or not REQUIREMENT.match(item.strip()):
            raise PackageError(f"'{item}' isn't a package name with version conditions (e.g. \"pyserial==3.5\")")
        clean.append(item.strip())
    return clean


def packages_root() -> Path:
    base = Path(os.environ.get("SONORIUM_CONFIG_DIR") or "/config/sonorium")
    return base / "plugin_packages"


class Packages:
    """Python packages of plugins, each in its own folder."""

    def __init__(self, root: Optional[Path] = None, allowed=lambda: False):
        self.root = Path(root) if root is not None else packages_root()
        self.allowed = allowed          # Settings > Advanced > Plugin packages
        self._status: dict[str, dict] = {}

    def folder(self, plugin_id: str) -> Path:
        return self.root / plugin_id

    def _wanted(self, requirements: list[str]) -> dict:
        return {"requirements": sorted(requirements), "python": f"{sys.version_info[0]}.{sys.version_info[1]}",
                "platform": platform_key()}

    def installed(self, plugin_id: str, requirements: list[str]) -> bool:
        try:
            marker = json.loads((self.folder(plugin_id) / MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return marker == self._wanted(requirements)

    def status(self, plugin_id: str, manifest: Optional[dict] = None) -> dict:
        requirements = []
        try:
            requirements = check_requirements((manifest or {}).get("requirements"))
        except PackageError:
            pass
        base = {"requirements": requirements, "allowed": bool(self.allowed()),
                "installed": self.installed(plugin_id, requirements) if requirements else True,
                "folder": str(self.folder(plugin_id)) if requirements else None}
        return {**base, **self._status.get(plugin_id, {})}

    def ensure(self, plugin_id: str, manifest: dict, approved: Optional[list] = None) -> Optional[Path]:
        """
        The plugin's packages ready (installed now if needed): its folder,
        added to the end of sys.path; None if it needs none. PackageError
        (refuses the plugin) when they're switched off or can't be installed.
        approved: the packages the user allowed (permissions.py); one not in
        it is never installed. None only for a built-in plugin.
        Blocking: pip may run for minutes.
        """
        requirements = check_requirements(manifest.get("requirements"))
        if not requirements:
            return None
        if approved is not None:
            missing = [r for r in requirements if r not in set(approved)]
            if missing:
                raise PackageError(f"{manifest.get('name') or plugin_id}: the packages {', '.join(missing)} "
                                   f"aren't approved")
        if not self.allowed():
            raise PackageError(f"{manifest.get('name') or plugin_id} needs Python packages "
                               f"({', '.join(requirements)}). Switch on Plugin packages in Settings › Advanced "
                               f"to let plugins install them.")
        folder = self.folder(plugin_id)
        if not self.installed(plugin_id, requirements):
            self.install(plugin_id, requirements)
        if str(folder) not in sys.path:
            sys.path.append(str(folder))
        return folder

    def install(self, plugin_id: str, requirements: list[str]) -> None:
        folder = self.folder(plugin_id)
        staging = folder.with_name(f".{plugin_id}.installing")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "pip", "install", "--target", str(staging), "--no-input",
                   "--disable-pip-version-check", "--no-cache-dir", "--only-binary=:all:",
                   "--no-warn-script-location", "--upgrade", *requirements]
        env = {**os.environ, "PIP_BREAK_SYSTEM_PACKAGES": "1", "PIP_NO_INPUT": "1"}
        logger.info(f"Plugin {plugin_id}: installing packages {', '.join(requirements)}")
        self._status[plugin_id] = {"state": "installing", "message": ""}
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=PIP_TIMEOUT_SECONDS, env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            shutil.rmtree(staging, ignore_errors=True)
            self._status[plugin_id] = {"state": "error", "message": str(e)}
            raise PackageError(f"Installing the packages failed: {e}")
        if result.returncode != 0:
            shutil.rmtree(staging, ignore_errors=True)
            lines = [l for l in (result.stderr or result.stdout or "").splitlines() if l.strip()]
            message = lines[-1] if lines else f"pip exited with {result.returncode}"
            self._status[plugin_id] = {"state": "error", "message": message[:500]}
            raise PackageError(f"Installing the packages failed: {message[:500]}")
        (staging / MARKER).write_text(json.dumps(self._wanted(requirements)), encoding="utf-8")
        shutil.rmtree(folder, ignore_errors=True)
        os.replace(staging, folder)
        self._status[plugin_id] = {"state": "installed", "message": ""}
        logger.info(f"Plugin {plugin_id}: packages installed in {folder}")

    def remove(self, plugin_id: str) -> None:
        """The plugin was uninstalled: its packages go too."""
        shutil.rmtree(self.folder(plugin_id), ignore_errors=True)
        self._status.pop(plugin_id, None)


# --- Binaries ---

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_binaries(plugin_dir: Path, manifest: dict, key: Optional[str] = None) -> dict[str, Path]:
    """
    The plugin's binaries for this platform, checked: {name: path}. A name
    with no entry for this platform is left out. PackageError if one is
    outside the plugin's folder, missing, or its SHA-256 doesn't match.
    """
    declared = manifest.get("binaries")
    if not declared:
        return {}
    if not isinstance(declared, dict):
        raise PackageError("'binaries' must map names to platforms")
    key = key or platform_key()
    root = Path(plugin_dir).resolve()
    found = {}
    for name, platforms in declared.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name) or not isinstance(platforms, dict):
            raise PackageError(f"Binary {name!r}: a name and its platforms")
        entry = platforms.get(key)
        if entry is None:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or \
                not re.fullmatch(r"[0-9a-fA-F]{64}", str(entry.get("sha256", ""))):
            raise PackageError(f"Binary {name} ({key}): needs a path and a sha256")
        raw = Path(plugin_dir) / entry["path"]
        if raw.is_symlink():
            raise PackageError(f"Binary {name} ({key}) is a link; ship the file itself")
        path = raw.resolve()
        if root not in path.parents:
            raise PackageError(f"Binary {name} ({key}) is outside the plugin's folder")
        if not path.is_file():
            raise PackageError(f"Binary {name} ({key}) is missing: {entry['path']}")
        if _sha256(path).lower() != entry["sha256"].lower():
            raise PackageError(f"Binary {name} ({key}) doesn't match its sha256: the plugin was changed or damaged")
        if os.name != "nt":
            try:
                mode = path.stat().st_mode
                path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP)
            except OSError as e:
                logger.warning(f"Binary {name}: could not make it executable ({e})")
        found[name] = path
    return found
