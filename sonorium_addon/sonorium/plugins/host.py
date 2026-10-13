"""
The plugin host: the services Sonorium gives every plugin, in one place.

    bus            event bus (events.py)
    actions        what plugins may do (actions.py), one facade per plugin
    scheduler      persistent jobs (scheduler.py)
    triggers       webhook triggers and their tokens (triggers.py, tokens.py)
    connections    saved connection values, secrets apart (extensions.py)
    auth           sign-in (sonorium/auth/service.py): Users and Keys register
                   who is calling and their routes there
    session_manager  the channels (Zones sets its policy,
                   core/channel_policy.py)
    overlay        sounds over channels (core/overlay_service.py)   } set by
    ramps          volume ramps (core/ramp_service.py)              } set_audio()
    outputs        audio output types (core/audio_outputs.py)       }
    tts            text to speech providers (tts.py)
    commands       command maps (commands.py)
    mqtt           the MQTT broker for plugins (mqtt.py)
    homeassistant  Home Assistant's API (homeassistant.py), when configured
    packages       plugins' Python packages (packages.py)
    attributes     plugin attributes on core objects (attributes.py, core/plugin_data.py)
    ui             what plugins add to the web UI's screens (ui.py, ui_contract.py)
    timeline       the cue timeline: which sound is heard where and when (core/timeline.py)
    devices        devices of any kind plugins bring: lights, displays, ... (core/devices.py)
    taps           read-only copies of what channels play; theme files decoded (core/taps.py)
    permissions    what each plugin may do, approved by the user (permissions.py)
    http           each plugin's own routes under /api/plugins/{id}/http/ (http_routes.py)
    ha_entities    Home Assistant entities plugins declare (ha_entities.py)

Plugins reach these through versioned facades (services.py): self.audio,
self.ramps, self.audio_outputs, self.commands, self.mqtt, self.homeassistant.

The plugin manager hands the host to each plugin (BasePlugin uses it for
self.actions, self.subscribe(), self.schedule(), ...) and tells it when a
plugin is enabled, disabled or removed, so what a plugin set up goes away
with it: its event subscriptions when it is disabled; its
jobs, tokens and saved connection values when it is uninstalled.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, Optional

from sonorium.obs import logger
from sonorium.plugins import events
from sonorium.plugins.actions import Actions
from sonorium.plugins.attributes import AttributeService
from sonorium.plugins.commands import CommandMaps
from sonorium.plugins.mqtt import MqttRouter
from sonorium.plugins.packages import Packages
from sonorium.plugins.tts import TtsProviders
from sonorium.plugins.extensions import ConnectionValues, normalize_connection_decls
from sonorium.plugins.scheduler import Scheduler
from sonorium.plugins.store import FrameworkStore
from sonorium.plugins.tokens import TokenStore
from sonorium.plugins.triggers import Triggers
from sonorium.plugins.ui import UiService


class PluginFrameworkUnavailable(RuntimeError):
    """The plugin runs without Sonorium's services (e.g. a unit test without a host)."""


class PluginHost:
    def __init__(self, folder: Path, *, bus: Optional[events.EventBus] = None, session_manager=None,
                 state_store=None, registry=None, now: Optional[Callable] = None, auth=None):
        self.bus = bus or events.get_bus()
        self.store = FrameworkStore(folder)
        self.tokens = TokenStore(self.store)
        self.actions = Actions(session_manager, state_store, registry, on_change=self._changed)
        self.triggers = Triggers(self.tokens, self.bus.publish)
        self.connections = ConnectionValues(self.store)
        self.scheduler = Scheduler(self.store, dispatch=self._run_job, is_enabled=self._is_enabled,
                                   publish=self.bus.publish, now=now)
        self.auth = auth  # sonorium.auth.service.AuthService (None in tests without the web app)
        self.session_manager = session_manager
        self.overlay = None
        self.ramps = None
        self.outputs = None
        self.tts = TtsProviders()
        self.commands = CommandMaps(self.store, self.bus.publish)
        self.mqtt = MqttRouter()
        self.homeassistant = None
        # Settings > Advanced > Plugin packages (api.py points this at the setting)
        self.packages_allowed: Callable[[], bool] = lambda: False
        self.packages = Packages(self.store.folder / "plugin_packages", allowed=lambda: self.packages_allowed())
        self.attributes = AttributeService(state_store, session_manager, auth, publish=self.bus.publish)
        self.ui = UiService(self.attributes.registry, publish=self.bus.publish)
        from sonorium.core.timeline import get_timeline
        self.timeline = get_timeline()
        from sonorium.core.devices import Devices
        self.devices = Devices(self.store, session_manager, publish=self.bus.publish, plugin_of=self._plugin)
        self.device_problems: dict[str, list] = {}  # plugin id -> device kinds refused (on its row)
        from sonorium.core.taps import get_taps
        self.taps = get_taps()
        from sonorium.plugins.permissions import PluginPermissions
        self.permissions = PluginPermissions(self.store, plugin_of=self._plugin)
        self.attributes.permissions = self.permissions
        self.ui.permissions = self.permissions
        from sonorium.plugins.ha_entities import PluginEntities
        self.ha_entities = PluginEntities(self.mqtt, plugin_of=self._plugin, update_settings=self._update_settings)
        self.http_routes: dict[str, list] = {}   # plugin id -> its routes while it is on
        self.mqtt.on_bind.append(lambda: [self._spawn(self.ha_entities.publish(e["plugin_id"]))
                                          for e in {x["plugin_id"]: x for x in self.ha_entities.entities()}.values()])
        self.extra_problems: dict[str, list] = {}  # plugin id -> routes and entities refused (on its row)
        if session_manager is not None:
            self.taps.session_manager = session_manager
        self.manager = None  # the PluginManager, set by it
        self.on_change: Optional[Callable] = None  # async callback(session): Home Assistant entities
        self._job_callbacks: dict[tuple, Callable] = {}
        self._alerting: Optional[events.Subscription] = None
        self._started = False

    async def _changed(self, session) -> None:
        if self.on_change is not None:
            await self.on_change(session)

    def set_audio(self, overlay=None, ramps=None, outputs=None) -> None:
        """The audio services (made by the app once playback is set up)."""
        self.overlay, self.ramps, self.outputs = overlay, ramps, outputs
        self.actions.overlay = overlay
        if outputs is not None:
            self.timeline.attach(outputs=outputs)

    def capabilities(self) -> set:
        """What this Sonorium offers plugins now (compat.CAPABILITIES names)."""
        have = {"events", "actions", "scheduler", "triggers", "pages", "connections",
                "commands", "mqtt", "homeassistant", "audio.tts", "packages.pip", "packages.binaries",
                "attributes", "ui", "timeline", "devices", "audio.tap", "audio.decode", "permissions",
                "http", "ha.entities"}
        if self.overlay is not None:
            have.add("audio.sounds")
        if self.ramps is not None:
            have.add("audio.ramps")
        if self.outputs is not None:
            have.add("audio.outputs")
        return have

    def actions_for(self, plugin_id: str) -> Actions:
        actions = self.actions.for_plugin(plugin_id)
        actions.guard(lambda permission: self.permissions.require(plugin_id, permission))
        return actions

    def _plugin(self, plugin_id: str):
        return self.manager.get_plugin(plugin_id) if self.manager is not None else None

    def _is_enabled(self, plugin_id: str) -> bool:
        plugin = self._plugin(plugin_id)
        return bool(plugin is not None and plugin.enabled)

    # --- Start and stop ---

    async def start(self) -> None:
        """Bind the bus to the running loop, raise the alert bar for failures, run the scheduler."""
        self.bus.bind_loop()
        if self._alerting is None:
            self._alerting = self.bus.subscribe("error", self._alert, owner=None)
        self.timeline.attach(session_manager=self.session_manager, attributes=self.attributes,
                             outputs=self.outputs, bus=self.bus)
        self.timeline.bind_loop()
        self.taps.loop = asyncio.get_running_loop()
        await self.scheduler.start()
        self._started = True

    @staticmethod
    def _alert(event) -> None:
        """A failure nobody was waiting for (already logged): the web UI's alert bar."""
        from sonorium import alerts
        data = event.data or {}
        who = f"Plugin {data['plugin_id']}: " if data.get("plugin_id") else ""
        alerts.report(f"{who}{data.get('where', 'error')} failed: {data.get('message', '')}",
                      str(data.get("where") or ""), log=False)

    async def stop(self) -> None:
        await self.scheduler.stop()
        if self._alerting is not None:
            self.bus.unsubscribe(self._alerting)
            self._alerting = None
        self._started = False

    # --- Plugin lifecycle (called by the plugin manager) ---

    async def _update_settings(self, plugin_id: str, values: dict) -> dict:
        if self.manager is None:
            raise RuntimeError("no plugin manager")
        return await self.manager.update_plugin_settings(plugin_id, values)

    def plugin_settings_changed(self, plugin) -> None:
        """Its settings were saved: Home Assistant entities showing them follow."""
        self._spawn(self.ha_entities.publish_states(plugin.id))

    def _spawn(self, coro) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return
        task = loop.create_task(coro)
        self._discovery_tasks = getattr(self, "_discovery_tasks", set())
        self._discovery_tasks.add(task)
        task.add_done_callback(self._discovery_tasks.discard)

    def _open_http(self, plugin) -> list:
        """The plugin's own routes (http_routes.py), in the sign-in's table while it is on. Returns the problems."""
        from sonorium.plugins.http_routes import normalize_routes, permission_table
        try:
            decls = plugin.get_http_routes()
        except Exception as e:
            return [f"get_http_routes() failed: {e}"]
        routes, problems = normalize_routes(decls, plugin.id)
        if routes and not self.permissions.allowed(plugin.id, "http"):
            return problems + ["Routes: not in its permissions (http)"]
        self.http_routes[plugin.id] = routes
        registry = getattr(self.auth, "registry", None) if self.auth is not None else None
        if routes and registry is not None:
            try:
                registry.register(f"http:{plugin.id}", permission_table(routes))
            except ValueError as e:
                self.http_routes.pop(plugin.id, None)
                problems.append(f"Routes: {e}")
        return problems

    def _close_http(self, plugin_id: str) -> None:
        self.http_routes.pop(plugin_id, None)
        registry = getattr(self.auth, "registry", None) if self.auth is not None else None
        if registry is not None:
            registry.set_active(f"http:{plugin_id}", False)

    def plugin_enabled(self, plugin) -> None:
        problems = self._open_http(plugin)
        problems += self.ha_entities.declare(plugin, self.permissions.allowed(plugin.id, "ha.entities"))
        self.extra_problems[plugin.id] = problems
        if self.ha_entities.entities(plugin.id):
            self._spawn(self.ha_entities.publish(plugin.id))
        # its kinds of device, each one it lists in its permissions
        self.device_problems[plugin.id] = self.devices.register_plugin(
            plugin, allowed=lambda kind: self.permissions.allowed(plugin.id, f"devices.{kind}"))
        if any(k["plugin_id"] == plugin.id for k in self.devices.kinds()):
            self._spawn_discovery(plugin.id)
        self.bus.publish("plugin.enabled", {"plugin_id": plugin.id, "name": plugin.name})

    def _spawn_discovery(self, plugin_id: str) -> None:
        """List a plugin's devices as it is switched on (in the background; failures are logged)."""
        async def run():
            for kind in [k for k in self.devices.kinds() if k["plugin_id"] == plugin_id]:
                try:
                    await self.devices.discover(kind["id"])
                except Exception as e:
                    logger.warning(f"Plugin {plugin_id}: devices not listed: {e}")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(run())
        self._discovery_tasks = getattr(self, "_discovery_tasks", set())
        self._discovery_tasks.add(task)
        task.add_done_callback(self._discovery_tasks.discard)

    def plugin_disabled(self, plugin) -> None:
        if self.auth is not None:
            self.auth.unregister(plugin.id)  # its sign-in, routes and web UI go with it
        self.bus.unsubscribe_owner(plugin.id)
        self.timeline.unsubscribe_owner(plugin.id)
        self._release_services(plugin.id)
        for key in [k for k in self._job_callbacks if k[0] == plugin.id]:
            self._job_callbacks.pop(key, None)
        self.ui.forget(plugin.id)  # its contributions leave every screen
        self._close_http(plugin.id)
        self._spawn(self.ha_entities.remove(plugin.id))
        self.bus.publish("plugin.disabled", {"plugin_id": plugin.id, "name": plugin.name})

    def _release_services(self, plugin_id: str) -> None:
        """What a plugin registered with the services while it ran: its MQTT subscriptions, output types, ..."""
        self.mqtt.unsubscribe_owner(plugin_id)
        self.devices.unregister_owner(plugin_id)
        self.taps.close_owner(plugin_id)
        self.tts.unregister_owner(plugin_id)
        self.commands.stop_learning(plugin_id)
        if self.outputs is not None:
            self.outputs.unregister_owner(plugin_id)
        if self.overlay is not None:
            self.overlay.remove_gate(plugin_id)

    def plugin_removed(self, plugin_id: str) -> None:
        """The plugin was uninstalled: its jobs, tokens, connection values, command maps, packages and attributes go too."""
        try:  # its attribute schema, and its values on every object
            self.attributes.forget_plugin(plugin_id)
        except Exception as e:
            logger.error(f"Plugin {plugin_id}: attributes not removed: {e}")
        self.bus.unsubscribe_owner(plugin_id)
        self.timeline.unsubscribe_owner(plugin_id)
        self._release_services(plugin_id)
        if self.outputs is not None:  # its audio output devices leave Settings > Speakers and the channels
            self.outputs.forget_owner(plugin_id)
        self.devices.forget_owner(plugin_id)  # and its other devices are forgotten
        self.permissions.forget(plugin_id)  # an install again asks again
        self._close_http(plugin_id)
        self.extra_problems.pop(plugin_id, None)
        self.device_problems.pop(plugin_id, None)
        self.commands.forget_plugin(plugin_id)
        self.packages.remove(plugin_id)
        self.scheduler.remove_plugin(plugin_id)
        self.tokens.revoke(f"plugin:{plugin_id}")
        self.tokens.revoke_prefix(f"trigger:{plugin_id}/")
        self.connections.forget_plugin(plugin_id)
        self.ui.forget(plugin_id)

    # --- Scheduler ---

    def set_job_callback(self, plugin_id: str, job_id: str, callback: Optional[Callable]) -> None:
        if callback is None:
            self._job_callbacks.pop((plugin_id, job_id), None)
        else:
            self._job_callbacks[(plugin_id, job_id)] = callback

    async def _run_job(self, job: dict) -> None:
        plugin = self._plugin(job["plugin_id"])
        if plugin is None or not plugin.enabled:
            return
        token = events.current_origin.set(f"plugin:{plugin.id}")
        try:
            callback = self._job_callbacks.get((plugin.id, job["id"]))
            result = callback(job) if callback is not None else plugin.on_schedule(job)
            if asyncio.iscoroutine(result):
                await result
        finally:
            events.current_origin.reset(token)

    # --- Connections ---

    def connection_fields(self, plugin, connection_id: str) -> Optional[dict]:
        for decl in normalize_connection_decls(plugin.get_connections(), plugin.id):
            if decl["id"] == connection_id:
                return decl["fields"]
        return None

    def connection_values(self, plugin, connection_id: str) -> dict:
        fields = self.connection_fields(plugin, connection_id)
        if fields is None:
            raise KeyError(connection_id)
        return self.connections.values(plugin.id, connection_id, fields)
