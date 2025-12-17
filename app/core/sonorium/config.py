"""
Configuration management for standalone Sonorium.

Stores settings in a JSON file in the user's config directory.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from sonorium.obs import logger


def get_local_ip() -> str:
    """
    Get the local network IP address of this machine.

    This is the IP address that network speakers will use to connect
    to the Sonorium stream endpoint.
    """
    try:
        # Create a UDP socket and connect to an external address
        # This doesn't actually send data, but determines which interface would be used
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        # Connect to Google's DNS - doesn't actually send anything
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception as e:
        logger.warning(f"Could not detect local IP: {e}, falling back to 127.0.0.1")
        return "127.0.0.1"


def get_stream_base_url(port: int = 8008) -> str:
    """
    Get the base URL for the audio stream endpoint.

    Network speakers connect to this URL to receive audio.
    Uses the detected local IP address so speakers can reach it.
    """
    ip = get_local_ip()
    return f"http://{ip}:{port}"


def get_config_dir() -> Path:
    """Get the config directory - located next to the EXE in a 'config' folder."""
    # Config folder is next to the executable/script
    config_dir = get_app_dir() / 'config'
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir
    except PermissionError as e:
        logger.error(f'Permission denied creating config directory {config_dir}: {e}')
        # Fall back to user's app data if we can't write next to EXE
        if os.name == 'nt':
            base = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming'))
        elif os.name == 'posix':
            if 'darwin' in os.uname().sysname.lower():
                base = Path.home() / 'Library' / 'Application Support'
            else:
                base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
        else:
            base = Path.home()
        config_dir = base / 'Sonorium'
        config_dir.mkdir(parents=True, exist_ok=True)
        logger.warning(f'Using fallback config directory: {config_dir}')
        return config_dir


def get_app_dir() -> Path:
    """Get the application root directory (where themes, plugins, config folders are)."""
    import sys
    if getattr(sys, 'frozen', False):
        # Running as compiled EXE - app root is the same folder as the EXE
        # User places Sonorium.exe in a folder, and themes/, config/, etc. are created there
        return Path(sys.executable).parent
    else:
        # Running as script - this file is in app/core/sonorium/, app root is app/
        # app/core/sonorium/config.py -> parent = sonorium/, parent.parent = core/, parent.parent.parent = app/
        return Path(__file__).parent.parent.parent


def get_default_audio_dir() -> Path:
    """Get the default audio/themes directory (next to the EXE)."""
    audio_dir = get_app_dir() / 'themes'
    audio_dir.mkdir(parents=True, exist_ok=True)
    return audio_dir


def get_bundled_themes_dir() -> Path:
    """Get the bundled themes directory (inside the app package)."""
    import sys
    if getattr(sys, 'frozen', False):
        # Running as compiled EXE - bundled themes are extracted to temp dir
        return Path(sys._MEIPASS) / 'themes'
    else:
        # Running as script - themes are in app/themes/
        return get_app_dir() / 'themes'


def copy_bundled_themes(target_dir: Path) -> None:
    """Copy bundled themes to the target directory if they don't exist."""
    import shutil
    bundled_dir = get_bundled_themes_dir()

    if not bundled_dir.exists():
        logger.warning(f'Bundled themes directory not found: {bundled_dir}')
        return

    for theme_dir in bundled_dir.iterdir():
        if theme_dir.is_dir() and theme_dir.name != '__pycache__':
            target_theme = target_dir / theme_dir.name
            if not target_theme.exists():
                logger.info(f'Copying bundled theme: {theme_dir.name}')
                try:
                    shutil.copytree(theme_dir, target_theme)
                except Exception as e:
                    logger.error(f'Failed to copy theme {theme_dir.name}: {e}')


@dataclass
class SessionConfig:
    """Saved channel/session configuration."""
    id: str = ''
    name: str = ''
    theme_id: str = ''
    preset_id: str = ''
    volume: int = 80
    created_at: str = ''


@dataclass
class AppConfig:
    """Application configuration."""

    # Audio settings
    audio_path: str = ''
    audio_device_id: int | str | None = None
    master_volume: float = 0.8

    # UI settings
    window_width: int = 1200
    window_height: int = 800
    start_minimized: bool = False
    minimize_to_tray: bool = True

    # Server settings
    server_port: int = 8008
    auto_start_server: bool = True

    # Playback settings
    last_theme: str = ''
    auto_play_on_start: bool = False

    # Saved channels/sessions
    sessions: list = field(default_factory=list)

    # Plugin settings (keyed by plugin_id)
    plugin_settings: dict = field(default_factory=dict)

    # Enabled plugins list
    enabled_plugins: list = field(default_factory=list)

    # Enabled network speakers (persisted IDs)
    enabled_network_speakers: list = field(default_factory=list)

    # Network streaming settings
    output_gain: float = 6.0  # Output gain multiplier for network streams (0.0 - 10.0)
    max_channels: int = 4     # Maximum number of concurrent streaming channels

    def __post_init__(self):
        if not self.audio_path:
            self.audio_path = str(get_default_audio_dir())

    @classmethod
    def load(cls, path: Path | None = None) -> 'AppConfig':
        """Load config from file."""
        if path is None:
            path = get_config_dir() / 'config.json'

        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                logger.info(f'Loaded config from {path}')
                config = cls(**data)

                # Migrate old Music\Sonorium path to new themes folder next to EXE
                if config.audio_path and 'Music' in config.audio_path and 'Sonorium' in config.audio_path:
                    new_path = get_default_audio_dir()
                    logger.info(f'Migrating audio path from {config.audio_path} to {new_path}')
                    config.audio_path = str(new_path)
                    config.save(path)

                return config
            except Exception as e:
                logger.error(f'Failed to load config: {e}')

        # Return default config
        config = cls()
        config.save(path)
        return config

    def save(self, path: Path | None = None):
        """Save config to file."""
        if path is None:
            path = get_config_dir() / 'config.json'

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(asdict(self), f, indent=2)
            logger.info(f'Saved config to {path}')
        except Exception as e:
            logger.error(f'Failed to save config: {e}')

    def update(self, **kwargs):
        """Update config values and save."""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.save()


# Global config instance
_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Get the global config instance."""
    global _config
    if _config is None:
        _config = AppConfig.load()
    return _config


def save_config():
    """Save the global config."""
    if _config is not None:
        _config.save()
