"""
Sonorium Plugin Base Class

All plugins must inherit from BasePlugin and implement the required methods.
"""

from __future__ import annotations

from abc import ABC
from pathlib import Path
from typing import Any, Optional

from sonorium.plugins.settings_schema import normalize_schema


class BasePlugin(ABC):
    """
    Base class that all Sonorium plugins must inherit from.

    Plugins are directory-based with the following structure:
    /config/sonorium/plugins/
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
            settings: Plugin settings from state store
            audio_path: Path to audio/themes directory (from addon config)
        """
        self.plugin_dir = plugin_dir
        self.settings = settings
        self.audio_path = audio_path or Path("/media/sonorium")
        self._enabled = False
        self._builtin = self.builtin  # Can be overridden by manifest
        self._manifest_settings_schema: dict = {}  # Set by the manager from manifest.json

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

        Setting name -> field, with the same vocabulary as get_ui_schema()
        fields: "type" (string, url, number, boolean, select), "label",
        "help", "default", "options" (select), "min"/"max" (number),
        "required", "placeholder". The full format is in settings_schema.py.
        Values sent to PUT /api/plugins/{id}/settings are checked against it,
        and self.settings always holds the defaults of settings not set.

        The default is the manifest's "settings_schema" (if any).

        Returns:
            dict mapping setting names to their fields:
            {
                "hash_check_duplicates": {
                    "type": "boolean",
                    "default": True,
                    "label": "Use hash to detect duplicate files"
                }
            }
        """
        return dict(self._manifest_settings_schema)

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

    async def on_settings_changed(self, settings: dict) -> None:
        """
        Called after the settings were changed through the API, checked
        against the schema and saved. self.settings already holds them.

        Args:
            settings: The plugin's settings (defaults included)
        """
        pass

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

    # Theme files (see theme_api.py): never write metadata.json/presets.json yourself

    def list_themes(self) -> list[dict]:
        """The themes in audio_path for a theme picker: [{"id", "name", "folder", "track_count", "preset_count"}]."""
        from sonorium.plugins.theme_api import list_themes
        return list_themes(self.audio_path)

    def new_theme(self, name: str, **kwargs):
        """A ThemeEdit for a new theme (description, icon, attribution, categories); written by its commit()."""
        from sonorium.plugins.theme_api import ThemeEdit
        return ThemeEdit.new(self.audio_path, name, **kwargs)

    def open_theme(self, theme_id: str):
        """A ThemeEdit for an existing theme (by id or folder name); ThemeEditError if not found or broken."""
        from sonorium.plugins.theme_api import ThemeEdit
        return ThemeEdit.open(self.audio_path, theme_id)

    # Utility methods

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a setting value with fallback to default."""
        return self.settings.get(key, default)

    def update_settings(self, new_settings: dict) -> None:
        """Update plugin settings (call save separately)."""
        self.settings.update(new_settings)

    def settings_schema(self) -> dict:
        """get_settings_schema() with only well-formed fields (never raises)."""
        try:
            return normalize_schema(self.get_settings_schema(), self.id)
        except Exception:
            return {}

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
            "settings_schema": self.settings_schema(),
        }
