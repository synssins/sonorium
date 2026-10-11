"""
The plugin host: the services Sonorium gives every plugin, in one place.

    bus            event bus (events.py)
    actions        what plugins may do (actions.py), one facade per plugin
    scheduler      persistent jobs (scheduler.py)
    triggers       webhook triggers and their tokens (triggers.py, tokens.py)
    notifications  notifiers and routing rules (notifications.py)
    connections    saved connection values, secrets apart (extensions.py)
    auth           sign-in (sonorium/auth/service.py): built-in sign-in plugins
                   register who is calling, their routes and their web UI there
    session_manager  the channels (built-in plugins: Zones sets its policy,
                   core/channel_policy.py)

The plugin manager hands the host to each plugin (BasePlugin uses it for
self.actions, self.subscribe(), self.schedule(), ...) and tells it when a
plugin is enabled, disabled or removed, so what a plugin set up goes away
with it: its event subscriptions and notifiers when it is disabled; its
jobs, tokens and saved connection values when it is uninstalled.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, Optional

from sonorium.obs import logger
from sonorium.plugins import events
from sonorium.plugins.actions import Actions
from sonorium.plugins.extensions import ConnectionValues, normalize_connection_decls
from sonorium.plugins.notifications import NOTIFIER_ID, Notifications
from sonorium.plugins.scheduler import Scheduler
from sonorium.plugins.store import FrameworkStore
from sonorium.plugins.tokens import TokenStore
from sonorium.plugins.triggers import Triggers


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
        self.notifications = Notifications(self.store, self.bus.publish)
        self.connections = ConnectionValues(self.store)
        self.scheduler = Scheduler(self.store, dispatch=self._run_job, is_enabled=self._is_enabled,
                                   publish=self.bus.publish, now=now)
        self.auth = auth  # sonorium.auth.service.AuthService (None in tests without the web app)
        self.session_manager = session_manager
        self.manager = None  # the PluginManager, set by it
        self.on_change: Optional[Callable] = None  # async callback(session): Home Assistant entities
        self._job_callbacks: dict[tuple, Callable] = {}
        self._router: Optional[events.Subscription] = None
        self._started = False

    async def _changed(self, session) -> None:
        if self.on_change is not None:
            await self.on_change(session)

    def actions_for(self, plugin_id: str) -> Actions:
        return self.actions.for_plugin(plugin_id)

    def _plugin(self, plugin_id: str):
        return self.manager.get_plugin(plugin_id) if self.manager is not None else None

    def _is_enabled(self, plugin_id: str) -> bool:
        plugin = self._plugin(plugin_id)
        return bool(plugin is not None and plugin.enabled)

    # --- Start and stop ---

    async def start(self) -> None:
        """Bind the bus to the running loop, route notifications, run the scheduler."""
        self.bus.bind_loop()
        if self._router is None:
            self._router = self.bus.subscribe("*", self.notifications.route, owner=None, queue_size=1024)
        await self.scheduler.start()
        self._started = True

    async def stop(self) -> None:
        await self.scheduler.stop()
        if self._router is not None:
            self.bus.unsubscribe(self._router)
            self._router = None
        self._started = False

    # --- Plugin lifecycle (called by the plugin manager) ---

    def plugin_enabled(self, plugin) -> None:
        for decl in self._notifier_decls(plugin):
            notifier_id = f"{plugin.id}.{decl['id']}"

            async def deliver(message, _plugin=plugin, _id=decl["id"]):
                await _plugin.deliver_notification(_id, message)
            try:
                self.notifications.register(notifier_id, decl.get("title") or decl["id"], deliver, owner=plugin.id)
            except ValueError as e:
                logger.warning(f"Plugin {plugin.id}: notifier not registered: {e}")
        self.bus.publish("plugin.enabled", {"plugin_id": plugin.id, "name": plugin.name})

    def plugin_disabled(self, plugin) -> None:
        if self.auth is not None:
            self.auth.unregister(plugin.id)  # its sign-in, routes and web UI go with it
        self.bus.unsubscribe_owner(plugin.id)
        self.notifications.unregister_owner(plugin.id)
        for key in [k for k in self._job_callbacks if k[0] == plugin.id]:
            self._job_callbacks.pop(key, None)
        self.bus.publish("plugin.disabled", {"plugin_id": plugin.id, "name": plugin.name})

    def plugin_removed(self, plugin_id: str) -> None:
        """The plugin was uninstalled: its jobs, tokens and saved connection values go too."""
        self.bus.unsubscribe_owner(plugin_id)
        self.notifications.unregister_owner(plugin_id)
        self.scheduler.remove_plugin(plugin_id)
        self.tokens.revoke(f"plugin:{plugin_id}")
        self.tokens.revoke_prefix(f"trigger:{plugin_id}/")
        self.connections.forget_plugin(plugin_id)

    @staticmethod
    def _notifier_decls(plugin) -> list[dict]:
        try:
            decls = plugin.get_notifiers() or []
        except Exception as e:
            logger.warning(f"Plugin {plugin.id}: get_notifiers failed: {e}")
            return []
        return [d for d in decls if isinstance(d, dict) and isinstance(d.get("id"), str)
                and NOTIFIER_ID.match(f"{plugin.id}.{d['id']}")]

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
