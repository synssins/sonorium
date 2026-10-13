"""
Sonorium Plugin Base Class

All plugins must inherit from BasePlugin and implement the required methods.
"""

from __future__ import annotations

from abc import ABC
from pathlib import Path
from sonorium.paths import audio_dir
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
        self.audio_path = audio_path or audio_dir()
        self._enabled = False
        self._builtin = self.builtin  # Can be overridden by manifest
        self._manifest_settings_schema: dict = {}  # Set by the manager from manifest.json
        self._manifest: dict = {}  # manifest.json, set by the manager
        self._host = None  # the PluginHost (plugins/host.py), set by the manager
        self._actions_override = None

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

    def _require(self, permission: str) -> None:
        host = getattr(self, "_host", None)
        permissions = getattr(host, "permissions", None) if host is not None else None
        if permissions is not None:
            permissions.require(self.id, permission)

    def new_theme(self, name: str, **kwargs):
        """A ThemeEdit for a new theme (description, icon, attribution, categories); written by its commit()."""
        self._require("themes.write")
        from sonorium.plugins.theme_api import ThemeEdit
        return ThemeEdit.new(self.audio_path, name, **kwargs)

    def open_theme(self, theme_id: str):
        """A ThemeEdit for an existing theme (by id or folder name); ThemeEditError if not found or broken."""
        self._require("themes.write")
        from sonorium.plugins.theme_api import ThemeEdit
        return ThemeEdit.open(self.audio_path, theme_id)

    # --- The plugin framework (docs/PLUGIN_DEVELOPMENT.md) ---
    # Sonorium's services through self.host; each raises PluginFrameworkUnavailable
    # when the plugin runs without them (e.g. a unit test that made it by hand).

    @property
    def host(self):
        """The PluginHost: event bus, actions, scheduler, triggers, audio and the other services."""
        if getattr(self, "_host", None) is None:
            from sonorium.plugins.host import PluginFrameworkUnavailable
            raise PluginFrameworkUnavailable(f"Plugin {self.id} has no plugin host")
        return self._host

    @property
    def actions(self):
        """What the plugin may do with Sonorium (plugins/actions.py): channels, themes, speakers."""
        if getattr(self, "_actions_override", None) is not None:
            return self._actions_override
        return self.host.actions_for(self.id)

    @actions.setter
    def actions(self, value):
        self._actions_override = value

    # Attributes on Sonorium's objects (plugins/attributes.py, core/plugin_data.py)

    def get_attributes(self) -> dict:
        """
        The attributes the plugin keeps on Sonorium's objects: {object class:
        {name: field}}, classes theme, track, file, preset, channel, speaker,
        user. Declared for it when it loads, before on_load. Default: the
        manifest's "attributes".
        """
        return dict((getattr(self, "_manifest", None) or {}).get("attributes") or {})

    @property
    def attributes(self):
        """The plugin's attribute values on objects: declare(), schema(), get(ref), set(ref, values), clear(ref)."""
        return self.host.attributes.for_plugin(self.id)

    # Contributions to the web UI's screens (plugins/ui_contract.py, docs/PLUGIN_DEVELOPMENT.md)

    def get_ui(self) -> list[dict]:
        """
        What the plugin adds to the web UI: a list of elements, each with its
        "target" and "spot" (GET /api/ui/targets) and its "id" and "kind".
        Checked against each spot's contract when the plugin is enabled; an
        element outside it is refused and listed on the plugin's row in
        Settings > Plugins. Default: the manifest's "ui".
        """
        return list((getattr(self, "_manifest", None) or {}).get("ui") or [])

    async def ui_action(self, element_id: str, ref: Optional[dict], value: Any = None) -> dict:
        """
        An action element was pressed, or an action-bound field changed (value:
        its new value). ref: the object the screen shows ({"kind", "id",
        "theme"}), or None on a screen or page. Returns {"success", "message",
        "refresh"}; "value" sets the field's shown value.
        """
        return {"success": False, "message": f"Unknown action: {element_id}"}

    @property
    def ui(self):
        """The plugin's contributions and live values: register(), warnings(), set_value()."""
        return self.host.ui.for_plugin(self)

    def ui_warnings(self) -> list[str]:
        """What was refused by its contract: screen additions, device kinds, routes, Home Assistant entities."""
        host = getattr(self, "_host", None)
        if host is None or getattr(host, "ui", None) is None:
            return []
        return (host.ui.warnings(self) + list((getattr(host, "device_problems", None) or {}).get(self.id) or [])
                + list((getattr(host, "extra_problems", None) or {}).get(self.id) or []))

    # Events

    def subscribe(self, patterns, handler, **kwargs):
        """
        Call handler(event) for events matching patterns ("channel.started",
        "channel.*", "*"). Removed automatically when the plugin is disabled.
        Returns the Subscription (its close() removes it sooner).
        """
        return self.host.bus.subscribe(patterns, handler, owner=self.id, **kwargs)

    def emit(self, name: str, data: Optional[dict] = None):
        """Publish the plugin's own event "plugin.<id>.<name>". Returns the Event."""
        from sonorium.plugins.events import plugin_event_type
        return self.host.bus.publish(plugin_event_type(self.id, name), data, origin=f"plugin:{self.id}")

    # Scheduler

    def schedule(self, job_id: str, kind: str, spec: Optional[dict] = None, *, name: str = "",
                 data: Optional[dict] = None, missed: str = "skip", callback=None,
                 timezone: Optional[str] = None) -> dict:
        """
        Add (or replace) a persistent job: kind once/daily/weekly/cron/interval
        with its spec (plugins/scheduler.py). When due, callback(job) runs if
        given (it lasts while the plugin runs: register it again in on_enable),
        else on_schedule(job). ScheduleError if the job isn't valid.
        """
        job = self.host.scheduler.add(self.id, job_id, kind, spec, name=name, data=data, missed=missed,
                                      timezone_name=timezone)
        self.host.set_job_callback(self.id, job_id, callback)
        return job

    def unschedule(self, job_id: str) -> bool:
        self.host.set_job_callback(self.id, job_id, None)
        return self.host.scheduler.remove(self.id, job_id)

    def scheduled_jobs(self) -> list[dict]:
        return self.host.scheduler.list(self.id)

    async def on_schedule(self, job: dict) -> None:
        """A job of this plugin is due (job: its id, name, kind, spec, data, scheduled_for, missed)."""
        pass

    # Webhook triggers

    def get_triggers(self) -> list[dict]:
        """
        The plugin's webhook triggers: [{"id", "title", "description",
        "methods": ["POST", "GET"]}]. Default: the manifest's "triggers".
        """
        return list((getattr(self, "_manifest", None) or {}).get("triggers") or [])

    async def on_trigger(self, trigger: str, payload: dict, context: dict) -> None:
        """A trigger was called (already authenticated). context: method, client, event_id."""
        pass

    # Pages (a left-menu entry each; plugins/extensions.py has the schema)

    def get_pages(self) -> list[dict]:
        """[{"id", "title", "icon"}]. Default: the manifest's "pages"."""
        return list((getattr(self, "_manifest", None) or {}).get("pages") or [])

    async def get_page(self, page_id: str) -> dict:
        """The page's schema, with current values: {"title", "sections": [...]}."""
        return {"title": "", "sections": []}

    async def page_action(self, page_id: str, action: str, data: dict) -> dict:
        """A page button or row action: data {"values", "row"}. Returns {"success", "message", "refresh"}."""
        return {"success": False, "message": f"Unknown action: {action}"}

    # Connections (a section in Settings > Connections each)

    def get_connections(self) -> list[dict]:
        """[{"id", "title", "description", "icon", "fields", "test"}]. Default: the manifest's "connections"."""
        return list((getattr(self, "_manifest", None) or {}).get("connections") or [])

    async def test_connection(self, connection_id: str, values: dict) -> dict:
        """Try the connection with these values (saved ones plus the form's). {"success", "message"}."""
        return {"success": False, "message": "This connection has no test"}

    def connection_status(self, connection_id: str) -> dict:
        """{"state": connected|disconnected|error|not_configured|unknown, "message"}."""
        return {"state": "unknown", "message": ""}

    async def on_connection_changed(self, connection_id: str, values: dict) -> None:
        """The connection's values were saved (values include secrets)."""
        pass

    def connection_values(self, connection_id: str) -> dict:
        """The connection's saved values, secrets included (KeyError for an unknown connection)."""
        return self.host.connection_values(self, connection_id)

    # --- Plugin API 1.1 services (plugins/services.py); manifest "api_version": "1.1" ---

    def _service(self, name: str):
        from sonorium.plugins import compat
        from sonorium.plugins.services import facade
        cache = self.__dict__.setdefault("_facades", {})
        if name not in cache:
            major = (compat.parse((getattr(self, "_manifest", None) or {}).get("api_version")) or (1, 0))[0]
            cache[name] = facade(self.host, self.id, name, major)
        return cache[name]

    @property
    def audio(self):
        """Sounds over channels, announcements, text to speech (AudioV1)."""
        return self._service("audio")

    @property
    def ramps(self):
        """Gradual volume ramps on channels (RampsV1)."""
        return self._service("ramps")

    @property
    def audio_outputs(self):
        """Register audio output types; send channels to their devices (OutputsV1)."""
        return self._service("audio_outputs")

    @property
    def commands(self):
        """Hand over codes from remotes and other inputs to the user's command maps (CommandsV1)."""
        return self._service("commands")

    @property
    def mqtt(self):
        """Publish and subscribe on Sonorium's MQTT broker (MqttV1)."""
        return self._service("mqtt")

    @property
    def homeassistant(self):
        """Home Assistant's API through Sonorium's connection (HomeAssistantV1)."""
        return self._service("homeassistant")

    @property
    def timeline(self):
        """Cues: which sound is heard on which speaker and when, ahead of time (TimelineV1)."""
        return self._service("timeline")

    # Devices of any kind (core/devices.py): lights, displays, buttons, ...

    @property
    def devices(self):
        """Device kinds, their devices, what they follow, actions (DevicesV1)."""
        return self._service("devices")

    def get_device_kinds(self) -> list[dict]:
        """
        The kinds of device the plugin brings: [{"id", "title", "icon",
        "actions": [{"id", "label"}], "latency_ms", "one_channel"}]. Registered
        when it is switched on. Default: the manifest's "device_kinds".
        """
        return list((getattr(self, "_manifest", None) or {}).get("device_kinds") or [])

    # Its own HTTP routes (plugins/http_routes.py) and Home Assistant entities (plugins/ha_entities.py)

    def get_http_routes(self) -> list[dict]:
        """[{"id", "path", "methods", "ability"}] under /api/plugins/<id>/http/. Default: the manifest's "http"."""
        return list((getattr(self, "_manifest", None) or {}).get("http") or [])

    async def http_request(self, route_id: str, request: dict) -> dict:
        """
        One of its routes was called (the caller's ability already checked).
        request: method, path, params, query, json, body, caller. Returns
        {"status", "json"} or {"status", "text"} or {"status", "body", "content_type"}.
        """
        return {"status": 404, "json": {"detail": "Not found"}}

    def get_ha_entities(self) -> list[dict]:
        """Home Assistant entities: [{"id", "type", "name", "bind", ...}]. Default: the manifest's "ha_entities"."""
        return list((getattr(self, "_manifest", None) or {}).get("ha_entities") or [])

    async def ha_action(self, action: str, value: Any = None) -> dict:
        """An action-bound entity was used in Home Assistant (a button: value None). {"value"} sets its state."""
        return {}

    async def ha_set_state(self, entity_id: str, value: Any) -> None:
        """The state Home Assistant shows for an action- or live-bound entity."""
        await self.host.ha_entities.set_state(self.id, entity_id, value)

    async def discover_devices(self, kind: str) -> list[dict]:
        """The devices of one of its kinds now: [{"id" (its own id for it), "name", "model"}]."""
        return []

    async def device_action(self, kind: str, device: dict, action: str, params: dict) -> dict:
        """
        One of the kind's actions on a device (a button, or another plugin's
        self.devices.invoke): device is core's view of it ({"id", "device": the
        plugin's own id, "name", "area", "channels", "areas", ...}).
        Returns {"success", "message"}.
        """
        return {"success": False, "message": f"Unknown action: {action}"}

    def get_command_maps(self) -> list[dict]:
        """
        The plugin's command maps, for the user to fill: [{"id", "title",
        "codes": [{"code", "label"}] (known codes, optional)}]. Default: the
        manifest's "command_maps".
        """
        return list((getattr(self, "_manifest", None) or {}).get("command_maps") or [])

    def binary(self, name: str) -> Optional[Path]:
        """A program the plugin ships (manifest "binaries"), for this platform; None if it has none for it."""
        return (getattr(self, "_binaries", None) or {}).get(name)

    # Utility methods

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a setting value with fallback to default."""
        return self.settings.get(key, default)

    def update_settings(self, new_settings: dict) -> None:
        """Update plugin settings (call save separately)."""
        self.settings.update(new_settings)

    def short_name(self) -> str:
        """The name on the plugin badge: manifest "short_name" (up to 16 characters), else the name."""
        from sonorium.plugins.ui_contract import COMMON
        short = (getattr(self, "_manifest", None) or {}).get("short_name")
        if isinstance(short, str) and 0 < len(short.strip()) <= COMMON["short_name_max"]:
            return short.strip()
        return self.name or self.id

    def _manifest_icon(self) -> Optional[dict]:
        from sonorium.plugins.extensions import check_icon
        icon = (getattr(self, "_manifest", None) or {}).get("icon")
        return check_icon(icon) if isinstance(icon, str) else None

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
            # Guards sign-in (no plugin does since Users became part of Sonorium)
            "auth_provider": bool(self._manifest.get("auth_provider")),
            "settings": self.settings,
            "ui_schema": self.get_ui_schema(),
            "settings_schema": self.settings_schema(),
            "api_version": str((self._manifest or {}).get("api_version") or "1.0"),
            # The plugin badge: a short name (manifest "short_name", default the name) and its icon
            "short_name": self.short_name(),
            "icon": self._manifest_icon(),
            "ui_warnings": self.ui_warnings() if self.enabled else [],
            "compatible": True,
            **self._permissions_dict(),
        }

    def _permissions_dict(self) -> dict:
        """What it asks for (None: no list), and what waits for the user's approval (plugins/permissions.py)."""
        host = getattr(self, "_host", None)
        permissions = getattr(host, "permissions", None) if host is not None else None
        if permissions is None:
            return {}
        try:
            return permissions.describe(self)
        except Exception:
            return {}
