"""
Sonorium Plugin Manager

Manages the lifecycle and coordination of all loaded plugins.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from sonorium.paths import audio_dir
from typing import TYPE_CHECKING, Optional

from sonorium.plugins.base import BasePlugin
from sonorium.plugins.permissions import PluginPermissions
from sonorium.plugins.loader import (
    DEFAULT_PLUGINS_DIR,
    discover_plugins,
    load_manifest,
    load_plugin_class,
    instantiate_plugin,
    is_bundled_copy,
    save_manifest,
    copy_builtin_plugins,
    remove_retired_plugins,
    RETIRED_BUILTIN_PLUGINS,
)
from sonorium.plugins.settings_schema import effective_settings, validate_settings
from sonorium.obs import logger

if TYPE_CHECKING:
    from sonorium.core.state import StateStore


class WaitingPlugin:
    """
    A plugin waiting for the user's approval, known from its manifest only:
    its plugin.py is not imported (none of its code has run) and its packages
    are not installed. Settings › Plugins lists it with what it asks for;
    approving and switching it on loads it (PluginManager.enable_plugin).
    """

    enabled = False

    def __init__(self, plugin_dir: Path, manifest: dict, host=None, bundled: bool = False):
        self.plugin_dir = plugin_dir
        self._manifest = manifest
        self._host = host
        self._bundled = bundled
        self._builtin = bool(manifest.get("builtin"))
        self.id = manifest.get("id") or plugin_dir.name
        self.name = manifest.get("name") or self.id
        self.version = str(manifest.get("version") or "")
        self.description = manifest.get("description") or ""
        self.author = manifest.get("author") or ""

    def to_dict(self) -> dict:
        info = {
            "id": self.id, "name": self.name, "version": self.version, "description": self.description,
            "author": self.author, "enabled": False, "builtin": self._builtin, "auth_provider": False,
            "settings": {}, "ui_schema": {}, "settings_schema": {},
            "api_version": str(self._manifest.get("api_version") or "1.0"), "compatible": True,
            "short_name": self._manifest.get("short_name") or self.name, "icon": None, "ui_warnings": [],
            "waiting_for_approval": True,
        }
        permissions = getattr(self._host, "permissions", None) if self._host is not None else None
        if permissions is not None:
            info.update(permissions.describe(self))
        return info


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
        host=None,
    ):
        """
        Initialize the plugin manager.

        Args:
            state_store: State store for persisting settings
            plugins_dir: Directory containing plugins
            audio_path: Path to audio/themes directory (from addon config)
            host: Optional PluginHost (plugins/host.py): the services plugins
                use (events, actions, scheduler, triggers)
        """
        self.host = host
        if host is not None:
            host.manager = self
        self.state_store = state_store
        self.plugins_dir = plugins_dir
        self.audio_path = audio_path or audio_dir()
        self.plugins: dict[str, BasePlugin] = {}
        # Plugins that can't run here (API version, capabilities, packages): id -> what Settings > Plugins shows
        self.refused: dict[str, dict] = {}
        # Plugins waiting for the user's approval, not loaded (none of their code has run): id -> WaitingPlugin
        self.waiting: dict[str, WaitingPlugin] = {}
        self.last_enable_error: dict[str, str] = {}  # why switching a plugin on failed (shown to the user)
        self._initialized = False

    async def initialize(self) -> None:
        """
        Discover and load all plugins.

        Should be called during application startup.
        """
        if self._initialized:
            return

        logger.debug("Initializing plugin manager...")

        # Ensure plugins directory exists
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

        # Built-in plugins that are part of Sonorium now (Keys, Announcements): their old copies go
        settings = self.state_store.settings
        had = [n for n in ("enabled_plugins", "known_builtin_plugins", "deleted_builtin_plugins")
               if set(getattr(settings, n, None) or []) & set(RETIRED_BUILTIN_PLUGINS)]
        if remove_retired_plugins(self.plugins_dir, settings) or had:
            self.state_store.save()

        # Copy built-in plugins to user directory if not present
        # Skip plugins that the user has deliberately deleted
        deleted_builtins = self.state_store.settings.deleted_builtin_plugins
        copy_builtin_plugins(self.plugins_dir, skip_plugins=deleted_builtins)

        # Discover and load plugins
        plugin_dirs = discover_plugins(self.plugins_dir)
        logger.debug(f"Found {len(plugin_dirs)} plugin(s)")

        for plugin_dir in plugin_dirs:
            await self._load_plugin(plugin_dir)

        # Enable previously enabled plugins
        enabled_list = self.state_store.settings.enabled_plugins
        for plugin_id in list(enabled_list):
            if plugin_id in self.plugins:
                await self.enable_plugin(plugin_id)
            elif plugin_id in self.waiting:  # an update asks for more: off (not loaded) until approved
                try:
                    from sonorium import alerts
                    alerts.report(f"Plugin {self.waiting[plugin_id].name} asks for more: "
                                  f"approve it in Settings › Plugins", "plugin")
                except Exception:
                    pass

        # Built-in plugins that start switched on (manifest "enabled_by_default"),
        # the first time they're seen only
        known = self.state_store.settings.known_builtin_plugins
        for plugin_id, plugin in list(self.plugins.items()):
            if not plugin._builtin or plugin_id in known:
                continue
            known.append(plugin_id)
            self.state_store.save()
            if plugin._manifest.get("enabled_by_default") and not plugin.enabled:
                logger.info(f"Switching on the built-in plugin {plugin.name}")
                await self.enable_plugin(plugin_id)

        self._initialized = True
        logger.debug(f"Plugin manager initialized with {len(self.plugins)} plugin(s)")

    async def _load_plugin(self, plugin_dir: Path) -> Optional[BasePlugin]:
        """Load a single plugin from its directory."""
        try:
            # Load manifest
            manifest = load_manifest(plugin_dir)

            # Get plugin settings from state
            plugin_id = manifest.get("id", plugin_dir.name)
            from sonorium.plugins.loader import is_valid_plugin_id
            if not is_valid_plugin_id(plugin_id):
                logger.error(f"Plugin in {plugin_dir.name}: the id '{plugin_id}' can't be used (letters, digits, "
                             f"_ and -; users and keys are reserved): not loaded")
                return None
            settings = self.state_store.settings.plugin_settings.get(plugin_id, {})

            # Can it run here? Decided before any of its code is loaded
            # (API version, capabilities, binaries)
            self.refused.pop(plugin_id, None)
            self.waiting.pop(plugin_id, None)
            problem, binaries = await self._check_runnable(plugin_id, plugin_dir, manifest)
            if problem:
                self._refuse(plugin_id, manifest, problem)
                return None

            # Approved by the user? Until then it is known from its manifest
            # only: plugin.py isn't imported and its packages aren't installed.
            # A built-in plugin (the copy bundled with Sonorium) needs no approval.
            bundled = is_bundled_copy(plugin_dir)
            waiting = WaitingPlugin(plugin_dir, manifest, self.host, bundled=bundled)
            permissions = self._permissions()
            if permissions is not None and not bundled:
                self._carry_over_approval(permissions, waiting)
                pending = permissions.pending(waiting)
                if pending:
                    self.waiting[plugin_id] = waiting
                    logger.info(f"Plugin {waiting.name} waits for your approval ({self._describe_pending(pending)}): "
                                f"not loaded")
                    return None

            # Its Python packages: the approved ones only (built-in: its own)
            problem = await self._ensure_packages(
                plugin_id, manifest, None if bundled or permissions is None else permissions.approved_requirements(plugin_id))
            if problem:
                self._refuse(plugin_id, manifest, problem)
                return None

            # Load plugin class
            plugin_class = load_plugin_class(plugin_dir, manifest)
            if plugin_class is None:
                return None

            # Instantiate plugin with audio_path
            plugin = instantiate_plugin(plugin_class, plugin_dir, settings, self.audio_path)
            if plugin is None:
                return None

            plugin._manifest = manifest
            plugin._host = self.host
            plugin._binaries = binaries
            plugin._bundled = bundled

            # Set builtin flag from manifest if present
            if manifest.get("builtin", False):
                plugin._builtin = True

            # Settings: the schema from the plugin (or its manifest), defaults applied
            if isinstance(manifest.get("settings_schema"), dict):
                plugin._manifest_settings_schema = manifest["settings_schema"]
            plugin.settings = effective_settings(plugin.settings_schema(), settings)

            # Update manifest with plugin info if it was auto-generated
            if not manifest.get("plugin_class"):
                manifest["plugin_class"] = plugin_class.__name__
                manifest["id"] = plugin.id or plugin_dir.name
                manifest["name"] = plugin.name or manifest["name"]
                manifest["version"] = plugin.version
                manifest["description"] = plugin.description
                manifest["author"] = plugin.author
                save_manifest(plugin_dir, manifest)

            # Its attributes on Sonorium's objects (core/plugin_data.py): declared
            # from the manifest or get_attributes(); a plugin may also declare in
            # on_load. Kept while it is installed, switched on or off.
            try:
                declared = plugin.get_attributes()
                listed = PluginPermissions.declared(plugin)
                if declared and listed is not None and isinstance(declared, dict):
                    from sonorium.plugins.permissions import covers
                    for kind in [k for k in declared if not covers(listed, f"attributes.{k}")]:
                        logger.warning(f"Plugin {plugin.id}: attributes on {kind} left out: not in its permissions")
                        declared = {k: v for k, v in declared.items() if k != kind}
                if declared:
                    from sonorium.core.plugin_data import registry as attribute_registry
                    attribute_registry.declare(plugin.id, declared)
            except Exception as e:
                logger.error(f"Plugin {plugin.id}: attributes not declared: {e}")

            # Call on_load hook
            await plugin.on_load()

            # Store plugin (a class's own id is checked like a manifest's)
            if not is_valid_plugin_id(plugin.id):
                logger.error(f"Plugin in {plugin_dir.name}: the id '{plugin.id}' can't be used: not loaded")
                return None
            self.plugins[plugin.id] = plugin
            logger.debug(f"Loaded plugin: {plugin.name} ({plugin.id})")

            return plugin

        except Exception as e:
            logger.error(f"Failed to load plugin from {plugin_dir}: {e}")
            return None

    async def _check_runnable(self, plugin_id: str, plugin_dir: Path, manifest: dict) -> tuple:
        """(why it can't run or None, its binaries {name: path})."""
        from sonorium.plugins import compat
        from sonorium.plugins.packages import PackageError, check_binaries, check_requirements
        problem = compat.check(manifest, self.host.capabilities() if self.host is not None else None)
        if problem:
            return problem, {}
        try:
            binaries = check_binaries(plugin_dir, manifest)
            check_requirements(manifest.get("requirements"))  # well formed (installed after approval)
        except PackageError as e:
            return str(e), {}
        return None, binaries

    async def _ensure_packages(self, plugin_id: str, manifest: dict, approved: Optional[list]) -> Optional[str]:
        """Its packages installed (only once approved): why they can't be, or None."""
        from sonorium.plugins.packages import PackageError
        if not manifest.get("requirements"):
            return None
        if self.host is None:
            return "Python packages need the plugin framework"
        try:
            await asyncio.to_thread(self.host.packages.ensure, plugin_id, manifest, approved)
        except PackageError as e:
            return str(e)
        return None

    def _permissions(self):
        return getattr(self.host, "permissions", None) if self.host is not None else None

    def _carry_over_approval(self, permissions, waiting: WaitingPlugin) -> None:
        """Approvals from before they covered everything: no new question for what already ran."""
        if not permissions.recorded(waiting.id):
            if waiting.id in self.state_store.settings.enabled_plugins:
                permissions.approve(waiting)  # it ran before approvals existed
            return
        requirements = permissions.requirements(waiting)
        installed = bool(requirements) and self.host.packages.installed(waiting.id, requirements)
        permissions.adopt_requirements(waiting.id, requirements if installed else [])

    @staticmethod
    def _describe_pending(pending: list) -> str:
        from sonorium.plugins.permissions import label
        return ", ".join(label(p) for p in pending)

    def _refuse(self, plugin_id: str, manifest: dict, problem: str) -> None:
        logger.warning(f"Plugin {plugin_id} not loaded: {problem}")
        self.refused[plugin_id] = {
            "id": plugin_id, "name": manifest.get("name") or plugin_id, "version": manifest.get("version") or "",
            "description": manifest.get("description") or "", "author": manifest.get("author") or "",
            "enabled": False, "builtin": bool(manifest.get("builtin")), "auth_provider": False,
            "settings": {}, "ui_schema": {}, "settings_schema": {},
            "api_version": manifest.get("api_version") or "1.0", "compatible": False, "problem": problem,
        }
        try:
            from sonorium import alerts
            alerts.report(f"Plugin {manifest.get('name') or plugin_id} was not loaded: {problem}", "plugin")
        except Exception:
            pass

    async def reload_plugins(self) -> None:
        """Reload all plugins (sign-in is part of Sonorium, so it isn't touched)."""
        # Unload existing plugins
        for plugin_id in list(self.plugins.keys()):
            await self._unload_plugin(plugin_id)

        self.plugins.clear()
        self.refused.clear()
        self.waiting.clear()
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
        if self.host is not None and plugin.enabled:
            self._host_call("plugin_disabled", plugin)

    def _host_call(self, name: str, *args) -> None:
        """Tell the plugin host (a failure there never fails the plugin manager)."""
        if self.host is None:
            return
        try:
            getattr(self.host, name)(*args)
        except Exception as e:
            logger.error(f"Plugin host {name} failed: {e}")

    def permissions_pending(self, plugin) -> list:
        """What the plugin asks for that the user hasn't approved (plugins/permissions.py)."""
        permissions = getattr(self.host, "permissions", None) if self.host is not None else None
        return permissions.pending(plugin) if permissions is not None else []

    def plugin_removed(self, plugin_id: str) -> None:
        """A plugin was uninstalled: the host forgets its jobs, tokens and connection values."""
        self._host_call("plugin_removed", plugin_id)

    def list_plugins(self) -> list[dict]:
        """
        List all loaded plugins.

        Returns:
            List of plugin info dicts
        """
        loaded = [plugin.to_dict() for plugin in self.plugins.values()]
        waiting = [w.to_dict() for pid, w in self.waiting.items() if pid not in self.plugins]
        return loaded + waiting + [dict(r) for pid, r in self.refused.items()
                                   if pid not in self.plugins and pid not in self.waiting]

    def get_plugin(self, plugin_id: str) -> Optional[BasePlugin]:
        """Get a plugin by ID (a loaded one: not one waiting for approval)."""
        return self.plugins.get(plugin_id)

    def find_plugin(self, plugin_id: str):
        """A loaded plugin, or one waiting for approval (WaitingPlugin), or None."""
        return self.plugins.get(plugin_id) or self.waiting.get(plugin_id)

    def approve_plugin(self, plugin_id: str) -> list:
        """
        The user allows what the plugin asks for now, its packages too.
        Nothing is installed or loaded here: switching it on does that
        (enable_plugin). Returns the granted permissions.
        """
        plugin = self.find_plugin(plugin_id)
        permissions = self._permissions()
        if plugin is None or permissions is None:
            raise KeyError(plugin_id)
        return permissions.approve(plugin)

    async def enable_plugin(self, plugin_id: str) -> bool:
        """
        Enable a plugin.

        Args:
            plugin_id: The plugin to enable

        Returns:
            True if enabled successfully
        """
        plugin = self.plugins.get(plugin_id)
        if plugin is None and plugin_id in self.waiting:
            # Not loaded yet: approved now? Then its packages are installed and its code loaded
            waiting = self.waiting[plugin_id]
            pending = self.permissions_pending(waiting)
            if pending:
                what = self._describe_pending(pending)
                self.last_enable_error[plugin_id] = f"Waiting for your approval: {what}"
                logger.info(f"Plugin {waiting.name} is not switched on until it is approved ({what})")
                return False
            plugin = await self._load_plugin(waiting.plugin_dir)
            if plugin is None:
                self.last_enable_error[plugin_id] = (self.refused.get(plugin_id) or {}).get("problem")                     or "The plugin could not be loaded"
                return False
        if plugin is None:
            logger.error(f"Plugin not found: {plugin_id}")
            return False

        if plugin.enabled:
            return True  # Already enabled

        pending = self.permissions_pending(plugin)
        if pending:
            what = self._describe_pending(pending)
            self.last_enable_error[plugin_id] = f"Waiting for your approval: {what}"
            logger.info(f"Plugin {plugin.name} is not switched on until its permissions are approved ({what})")
            return False

        try:
            await plugin.on_enable()
            plugin.enabled = True
            self._host_call("plugin_enabled", plugin)

            # Persist enabled state
            enabled_list = self.state_store.settings.enabled_plugins
            if plugin_id not in enabled_list:
                enabled_list.append(plugin_id)
                self.state_store.save()

            logger.debug(f"Enabled plugin: {plugin.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to enable plugin {plugin_id}: {e}")
            self.last_enable_error[plugin_id] = str(e) or type(e).__name__
            if self.host is not None:  # what it subscribed to and registered before failing
                self.host.bus.unsubscribe_owner(plugin_id)
                self.host._release_services(plugin_id)
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
            self._host_call("plugin_disabled", plugin)

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
        """Get settings for a plugin (defaults included once it is loaded)."""
        plugin = self.plugins.get(plugin_id)
        if plugin is not None:
            return dict(plugin.settings)
        return self.state_store.settings.plugin_settings.get(plugin_id, {})

    async def update_plugin_settings(self, plugin_id: str, settings: dict) -> dict:
        """
        Update settings for a plugin: the values given are checked against its
        settings schema and merged into its current settings, which are saved;
        then the plugin's on_settings_changed hook is called.

        Args:
            plugin_id: The plugin to update
            settings: Setting name -> new value (only the ones to change)

        Returns:
            The plugin's settings after the update (defaults included)

        Raises:
            KeyError: no such plugin
            SettingsError: a name the schema doesn't have, or a wrong value
        """
        plugin = self.plugins.get(plugin_id)
        if plugin is None:
            raise KeyError(plugin_id)

        schema = plugin.settings_schema()
        clean = validate_settings(schema, settings)
        updated = effective_settings(schema, {**plugin.settings, **clean})

        # Persist to state, then update in memory
        self.state_store.settings.plugin_settings[plugin_id] = dict(updated)
        self.state_store.save()
        plugin.settings = updated

        try:
            await plugin.on_settings_changed(dict(updated))
        except Exception as e:
            logger.error(f"Error in {plugin_id}.on_settings_changed: {e}")
        if plugin.enabled:
            self._host_call("plugin_settings_changed", plugin)

        return dict(updated)

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
