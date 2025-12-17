"""
Sonorium Plugin Base Class

All plugins must inherit from BasePlugin and implement the required methods.
"""

from __future__ import annotations

from abc import ABC
from pathlib import Path
from typing import Any, Optional


class BasePlugin(ABC):
    """
    Base class that all Sonorium plugins must inherit from.

    Plugins are directory-based with the following structure:
    plugins/
    └── plugin_name/
        ├── manifest.json       # Auto-generated if missing
        └── plugin.py           # Contains the plugin class

    The plugin class should define class attributes:
        id: str - Unique identifier (e.g., "ambient_mixer")
        name: str - Display name (e.g., "Ambient Mixer Importer")
        version: str - Semantic version (e.g., "1.0.0")
        description: str - Brief description
        author: str - Plugin author
    """

    # Override these in your plugin
    id: str = ""
    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    builtin: bool = False  # True for plugins shipped with Sonorium

    def __init__(self, plugin_dir: Path, settings: dict, audio_path: Optional[Path] = None):
        """
        Initialize the plugin.

        Args:
            plugin_dir: Path to the plugin directory
            settings: Plugin settings from config
            audio_path: Path to audio/themes directory
        """
        self.plugin_dir = plugin_dir
        self.settings = settings
        self.audio_path = audio_path or Path("themes")
        self._enabled = False
        self._builtin = self.builtin  # Can be overridden by manifest

    @property
    def enabled(self) -> bool:
        """Check if plugin is enabled."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        """Set plugin enabled state."""
        self._enabled = value

    # Lifecycle hooks

    async def on_load(self) -> None:
        """
        Called when the plugin is loaded.
        Override to perform initialization tasks.
        """
        pass

    async def on_unload(self) -> None:
        """
        Called when the plugin is being unloaded.
        Override to perform cleanup tasks.
        """
        pass

    async def on_enable(self) -> None:
        """
        Called when the plugin is enabled.
        Override to start any background tasks.
        """
        pass

    async def on_disable(self) -> None:
        """
        Called when the plugin is disabled.
        Override to stop any background tasks.
        """
        pass

    # UI Integration

    def get_ui_schema(self) -> dict:
        """
        Return the UI schema for the plugin settings and actions.

        The schema describes what form fields and action buttons to display.

        Returns:
            dict with structure:
            {
                "type": "form",  # or "custom"
                "fields": [
                    {
                        "name": "url",
                        "type": "url",  # string, number, boolean, url, select
                        "label": "URL to import",
                        "required": True,
                        "placeholder": "https://..."
                    }
                ],
                "actions": [
                    {
                        "id": "import",
                        "label": "Import",
                        "primary": True
                    }
                ]
            }
        """
        return {}

    def get_settings_schema(self) -> dict:
        """
        Return the schema for plugin settings that persist across sessions.

        Returns:
            dict mapping setting names to their type info:
            {
                "download_path": {
                    "type": "string",
                    "default": "/media/sonorium",
                    "label": "Download Path"
                },
                "auto_create_metadata": {
                    "type": "boolean",
                    "default": True,
                    "label": "Auto-create metadata"
                }
            }
        """
        return {}

    async def handle_action(self, action: str, data: dict) -> dict:
        """
        Handle an action triggered from the UI.

        Args:
            action: The action ID (e.g., "import")
            data: Form data from the UI

        Returns:
            dict with result:
            {
                "success": True,
                "message": "Import completed successfully",
                "data": {...}  # Optional additional data
            }
        """
        return {"success": False, "message": f"Unknown action: {action}"}

    # Theme Integration Hooks

    async def on_theme_created(self, theme_id: str, theme_path: Path) -> None:
        """
        Called when a new theme is created.

        Args:
            theme_id: The new theme's ID
            theme_path: Path to the theme directory
        """
        pass

    async def on_theme_deleted(self, theme_id: str) -> None:
        """
        Called when a theme is deleted.

        Args:
            theme_id: The deleted theme's ID
        """
        pass

    # Utility methods

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a setting value with fallback to default."""
        return self.settings.get(key, default)

    def update_settings(self, new_settings: dict) -> None:
        """Update plugin settings (call save separately)."""
        self.settings.update(new_settings)

    def to_dict(self) -> dict:
        """Serialize plugin info to dict."""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "enabled": self.enabled,
            "builtin": self._builtin,
            "settings": self.settings,
            "ui_schema": self.get_ui_schema(),
            "settings_schema": self.get_settings_schema(),
        }
