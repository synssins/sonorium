"""
Sonorium Auto-Update Module

Handles checking for updates from GitHub releases and launching the updater.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from sonorium.obs import logger


# Configuration
GITHUB_REPO = "synssins/sonorium"  # Update with actual repo
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
USER_AGENT = "Sonorium/1.0"


@dataclass
class ReleaseInfo:
    """Information about a GitHub release."""
    version: str
    tag_name: str
    name: str
    body: str  # Release notes
    published_at: str
    download_url: str
    download_size: int = 0
    html_url: str = ""


@dataclass
class UpdatePreferences:
    """User preferences for updates."""
    # Version to ignore (won't prompt again for this version)
    ignored_version: Optional[str] = None
    # Last time we checked for updates
    last_check: Optional[str] = None
    # Whether user chose "remind later"
    remind_later: bool = False
    remind_later_time: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "ignored_version": self.ignored_version,
            "last_check": self.last_check,
            "remind_later": self.remind_later,
            "remind_later_time": self.remind_later_time,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UpdatePreferences":
        return cls(
            ignored_version=data.get("ignored_version"),
            last_check=data.get("last_check"),
            remind_later=data.get("remind_later", False),
            remind_later_time=data.get("remind_later_time"),
        )


def get_current_version() -> str:
    """Get the current application version."""
    try:
        from sonorium import __version__
        return __version__
    except ImportError:
        return "0.0.0"


def parse_version(version_str: str) -> tuple:
    """Parse version string into comparable tuple."""
    # Remove 'v' prefix if present
    v = version_str.lstrip('v')
    # Split and convert to integers
    parts = v.split('.')
    try:
        return tuple(int(p) for p in parts[:3])
    except ValueError:
        return (0, 0, 0)


def is_newer_version(remote: str, current: str) -> bool:
    """Check if remote version is newer than current."""
    return parse_version(remote) > parse_version(current)


def check_for_updates() -> Optional[ReleaseInfo]:
    """
    Check GitHub for a newer release.

    Returns:
        ReleaseInfo if a newer version is available, None otherwise.
    """
    current_version = get_current_version()
    logger.info(f"Checking for updates (current: {current_version})...")

    try:
        req = Request(
            GITHUB_API_URL,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github.v3+json",
            }
        )

        with urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode())

        # Extract version from tag
        tag_name = data.get("tag_name", "")
        remote_version = tag_name.lstrip('v')

        if not is_newer_version(remote_version, current_version):
            logger.info(f"No update available (latest: {remote_version})")
            return None

        # Find downloadable asset
        download_url = ""
        download_size = 0

        for asset in data.get("assets", []):
            name = asset.get("name", "").lower()
            if "sonorium" in name and (name.endswith(".zip") or name.endswith(".exe")):
                download_url = asset.get("browser_download_url", "")
                download_size = asset.get("size", 0)
                break

        if not download_url:
            logger.warning("No suitable download asset found in release")
            return None

        release = ReleaseInfo(
            version=remote_version,
            tag_name=tag_name,
            name=data.get("name", f"Version {remote_version}"),
            body=data.get("body", ""),
            published_at=data.get("published_at", ""),
            download_url=download_url,
            download_size=download_size,
            html_url=data.get("html_url", ""),
        )

        logger.info(f"Update available: {remote_version}")
        return release

    except (URLError, HTTPError) as e:
        logger.warning(f"Failed to check for updates: {e}")
        return None
    except Exception as e:
        logger.error(f"Error checking for updates: {e}")
        return None


def get_updater_path() -> Path:
    """Get the path to the updater executable."""
    if getattr(sys, 'frozen', False):
        # Running as compiled EXE
        app_dir = Path(sys.executable).parent
    else:
        # Running as script
        app_dir = Path(__file__).parent.parent

    updater_path = app_dir / "updater.exe"

    if not updater_path.exists():
        # Try in updater subdirectory
        updater_path = app_dir / "updater" / "updater.exe"

    return updater_path


def launch_updater(release: ReleaseInfo, launch_after: bool = True) -> bool:
    """
    Launch the updater to install an update.

    Args:
        release: Release info with download URL
        launch_after: Whether to launch the app after updating

    Returns:
        True if updater was launched successfully
    """
    updater_path = get_updater_path()

    if not updater_path.exists():
        logger.error(f"Updater not found at {updater_path}")
        return False

    # Get application directory
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys.executable).parent
    else:
        app_dir = Path(__file__).parent.parent

    # Build command
    cmd = [
        str(updater_path),
        "--app-dir", str(app_dir),
        "--download-url", release.download_url,
        "--version", release.version,
    ]

    if launch_after:
        cmd.append("--launch-after")

    # Get current process ID so updater can wait for us
    cmd.extend(["--wait-pid", str(os.getpid())])

    logger.info(f"Launching updater: {' '.join(cmd)}")

    try:
        # Start updater as detached process
        if sys.platform == "win32":
            # Windows: use CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(
                cmd,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                close_fds=True,
            )
        else:
            # Unix: use nohup-like behavior
            subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        return True

    except Exception as e:
        logger.error(f"Failed to launch updater: {e}")
        return False


class UpdateChecker:
    """
    Manages update checking and user preferences.

    Usage:
        checker = UpdateChecker(config_path)
        release = checker.check()
        if release and checker.should_notify(release):
            # Show notification to user
            pass
    """

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.prefs = self._load_prefs()

    def _load_prefs(self) -> UpdatePreferences:
        """Load update preferences from disk."""
        prefs_file = self.config_path / "update_prefs.json"

        if prefs_file.exists():
            try:
                data = json.loads(prefs_file.read_text())
                return UpdatePreferences.from_dict(data)
            except Exception as e:
                logger.warning(f"Failed to load update prefs: {e}")

        return UpdatePreferences()

    def _save_prefs(self):
        """Save update preferences to disk."""
        prefs_file = self.config_path / "update_prefs.json"

        try:
            self.config_path.mkdir(parents=True, exist_ok=True)
            prefs_file.write_text(json.dumps(self.prefs.to_dict(), indent=2))
        except Exception as e:
            logger.error(f"Failed to save update prefs: {e}")

    def check(self) -> Optional[ReleaseInfo]:
        """Check for updates and update last check time."""
        self.prefs.last_check = datetime.now().isoformat()
        self._save_prefs()
        return check_for_updates()

    def should_notify(self, release: ReleaseInfo) -> bool:
        """
        Check if we should notify the user about this release.

        Returns False if:
        - User chose to ignore this version
        - User chose "remind later" and it hasn't been long enough
        """
        # Check if ignored
        if self.prefs.ignored_version == release.version:
            logger.debug(f"Version {release.version} is ignored")
            return False

        # Check remind later
        if self.prefs.remind_later and self.prefs.remind_later_time:
            try:
                remind_time = datetime.fromisoformat(self.prefs.remind_later_time)
                # Remind after 24 hours
                if (datetime.now() - remind_time).total_seconds() < 86400:
                    logger.debug("Remind later is active, skipping notification")
                    return False
            except Exception:
                pass

        return True

    def ignore_version(self, version: str):
        """Mark a version to be ignored (don't prompt again)."""
        self.prefs.ignored_version = version
        self.prefs.remind_later = False
        self.prefs.remind_later_time = None
        self._save_prefs()
        logger.info(f"Ignoring version {version}")

    def remind_later(self):
        """Set remind later flag."""
        self.prefs.remind_later = True
        self.prefs.remind_later_time = datetime.now().isoformat()
        self._save_prefs()
        logger.info("Update reminder set for later")

    def clear_remind_later(self):
        """Clear remind later flag (after user is notified again)."""
        self.prefs.remind_later = False
        self.prefs.remind_later_time = None
        self._save_prefs()
