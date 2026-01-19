"""
Sonorium Plugin Manager

Manages the lifecycle and coordination of all loaded plugins.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from sonorium.plugins.base import BasePlugin
from sonorium.plugins.loader import (
    DEFAULT_PLUGINS_DIR,
    discover_plugins,
    load_manifest,
    load_plugin_class,
    instantiate_plugin,
    save_manifest,
    copy_builtin_plugins,
)
from sonorium.obs import logger

if TYPE_CHECKING:
    from sonorium.core.state import StateStore


class PluginManager:
    """
    Manages all Sonorium plugins.

    Handles:
    - Plugin discovery and loading
    - Enabling/disabling plugins
    - Plugin settings persistence
    - Routing actions to plugins
    """

    def __init__(
        self,
        state_store: StateStore,
        plugins_dir: Path = DEFAULT_PLUGINS_DIR,
        audio_path: Optional[Path] = None,
    ):
        """
        Initialize the plugin manager.

        Args:
            state_store: State store for persisting settings
            plugins_dir: Directory containing plugins
            audio_path: Path to audio/themes directory (from addon config)
        """
        self.state_store = state_store
        self.plugins_dir = plugins_dir
        self.audio_path = audio_path or Path("/media/sonorium")
        self.plugins: dict[str, BasePlugin] = {}
        self._initialized = False

    async def initialize(self) -> None:
        """
        Discover and load all plugins.

        Should be called during application startup.
        """
        if self._initialized:
            return

        logger.info("Initializing plugin manager...")

        # Ensure plugins directory exists
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

        # Copy built-in plugins to user directory if not present
        # Skip plugins that the user has deliberately deleted
        deleted_builtins = self.state_store.settings.deleted_builtin_plugins
        copy_builtin_plugins(self.plugins_dir, skip_plugins=deleted_builtins)

        # Discover and load plugins
        plugin_dirs = discover_plugins(self.plugins_dir)
        logger.info(f"Found {len(plugin_dirs)} plugin(s)")

        for plugin_dir in plugin_dirs:
            await self._load_plugin(plugin_dir)

        # Enable previously enabled plugins
        enabled_list = self.state_store.settings.enabled_plugins
        for plugin_id in enabled_list:
            if plugin_id in self.plugins:
                await self.enable_plugin(plugin_id)

        self._initialized = True
        logger.info(f"Plugin manager initialized with {len(self.plugins)} plugin(s)")

    async def _load_plugin(self, plugin_dir: Path) -> Optional[BasePlugin]:
        """Load a single plugin from its directory."""
        try:
            # Load manifest
            manifest = load_manifest(plugin_dir)

            # Get plugin settings from state
            plugin_id = manifest.get("id", plugin_dir.name)
            settings = self.state_store.settings.plugin_settings.get(plugin_id, {})

            # Load plugin class
            plugin_class = load_plugin_class(plugin_dir, manifest)
            if plugin_class is None:
                return None

            # Instantiate plugin with audio_path
            plugin = instantiate_plugin(plugin_class, plugin_dir, settings, self.audio_path)
            if plugin is None:
                return None

            # Set builtin flag from manifest if present
            if manifest.get("builtin", False):
                plugin._builtin = True

            # Update manifest with plugin info if it was auto-generated
            if not manifest.get("plugin_class"):
                manifest["plugin_class"] = plugin_class.__name__
                manifest["id"] = plugin.id or plugin_dir.name
                manifest["name"] = plugin.name or manifest["name"]
                manifest["version"] = plugin.version
                manifest["description"] = plugin.description
                manifest["author"] = plugin.author
                save_manifest(plugin_dir, manifest)

            # Call on_load hook
            await plugin.on_load()

            # Store plugin
            self.plugins[plugin.id] = plugin
            logger.info(f"Loaded plugin: {plugin.name} ({plugin.id})")

            return plugin

        except Exception as e:
            logger.error(f"Failed to load plugin from {plugin_dir}: {e}")
            return None

    async def reload_plugins(self) -> None:
        """Reload all plugins."""
        # Unload existing plugins
        for plugin_id in list(self.plugins.keys()):
            await self._unload_plugin(plugin_id)

        self.plugins.clear()
        self._initialized = False

        # Reinitialize
        await self.initialize()

    async def _unload_plugin(self, plugin_id: str) -> None:
        """Unload a single plugin."""
        plugin = self.plugins.get(plugin_id)
        if plugin is None:
            return

        try:
            if plugin.enabled:
                await plugin.on_disable()
            await plugin.on_unload()
        except Exception as e:
            logger.error(f"Error unloading plugin {plugin_id}: {e}")

    def list_plugins(self) -> list[dict]:
        """
        List all loaded plugins.

        Returns:
            List of plugin info dicts
        """
        return [plugin.to_dict() for plugin in self.plugins.values()]

    def get_plugin(self, plugin_id: str) -> Optional[BasePlugin]:
        """Get a plugin by ID."""
        return self.plugins.get(plugin_id)

    async def enable_plugin(self, plugin_id: str) -> bool:
        """
        Enable a plugin.

        Args:
            plugin_id: The plugin to enable

        Returns:
            True if enabled successfully
        """
        plugin = self.plugins.get(plugin_id)
        if plugin is None:
            logger.error(f"Plugin not found: {plugin_id}")
            return False

        if plugin.enabled:
            return True  # Already enabled

        try:
            await plugin.on_enable()
            plugin.enabled = True

            # Persist enabled state
            enabled_list = self.state_store.settings.enabled_plugins
            if plugin_id not in enabled_list:
                enabled_list.append(plugin_id)
                self.state_store.save()

            logger.info(f"Enabled plugin: {plugin.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to enable plugin {plugin_id}: {e}")
            return False

    async def disable_plugin(self, plugin_id: str) -> bool:
        """
        Disable a plugin.

        Args:
            plugin_id: The plugin to disable

        Returns:
            True if disabled successfully
        """
        plugin = self.plugins.get(plugin_id)
        if plugin is None:
            logger.error(f"Plugin not found: {plugin_id}")
            return False

        if not plugin.enabled:
            return True  # Already disabled

        try:
            await plugin.on_disable()
            plugin.enabled = False

            # Persist enabled state
            enabled_list = self.state_store.settings.enabled_plugins
            if plugin_id in enabled_list:
                enabled_list.remove(plugin_id)
                self.state_store.save()

            logger.info(f"Disabled plugin: {plugin.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to disable plugin {plugin_id}: {e}")
            return False

    async def call_action(
        self,
        plugin_id: str,
        action: str,
        data: dict,
    ) -> dict:
        """
        Call an action on a plugin.

        Args:
            plugin_id: The plugin to call
            action: The action to perform
            data: Action data/parameters

        Returns:
            Result dict from the plugin
        """
        plugin = self.plugins.get(plugin_id)
        if plugin is None:
            return {"success": False, "message": f"Plugin not found: {plugin_id}"}

        if not plugin.enabled:
            return {"success": False, "message": f"Plugin is not enabled: {plugin_id}"}

        try:
            result = await plugin.handle_action(action, data)
            return result
        except Exception as e:
            logger.error(f"Error calling action {action} on {plugin_id}: {e}")
            return {"success": False, "message": str(e)}

    def get_plugin_settings(self, plugin_id: str) -> dict:
        """Get settings for a plugin."""
        return self.state_store.settings.plugin_settings.get(plugin_id, {})

    def update_plugin_settings(self, plugin_id: str, settings: dict) -> bool:
        """
        Update settings for a plugin.

        Args:
            plugin_id: The plugin to update
            settings: New settings dict

        Returns:
            True if updated successfully
        """
        plugin = self.plugins.get(plugin_id)
        if plugin is None:
            return False

        # Update in-memory settings
        plugin.update_settings(settings)

        # Persist to state
        self.state_store.settings.plugin_settings[plugin_id] = settings
        self.state_store.save()

        return True

    # Theme Integration Hooks

    async def notify_theme_created(self, theme_id: str, theme_path: Path) -> None:
        """Notify all enabled plugins that a theme was created."""
        for plugin in self.plugins.values():
            if plugin.enabled:
                try:
                    await plugin.on_theme_created(theme_id, theme_path)
                except Exception as e:
                    logger.error(
                        f"Error in {plugin.id}.on_theme_created: {e}"
                    )

    async def notify_theme_deleted(self, theme_id: str) -> None:
        """Notify all enabled plugins that a theme was deleted."""
        for plugin in self.plugins.values():
            if plugin.enabled:
                try:
                    await plugin.on_theme_deleted(theme_id)
                except Exception as e:
                    logger.error(
                        f"Error in {plugin.id}.on_theme_deleted: {e}"
                    )
