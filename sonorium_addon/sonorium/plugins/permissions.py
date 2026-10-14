"""
What each plugin may do: it lists what it needs (manifest "permissions"), the
user sees that list before the plugin first runs and approves it, and
Sonorium's services refuse calls outside it. Like a phone asking "Allow this
app to use the microphone?".

    "permissions": ["channels.control", "timeline", "devices.light", "attributes.track",
                    "ui", "network.lan"]

An update that asks for more is not switched on until the user approves the
new list (the plugin's row says what it asks for). A plugin whose manifest has
no "permissions" list says nothing about what it uses: it may use everything,
and the approval says so.

Python packages (manifest "requirements") are part of the approval: each one
is listed by name and version ("package:pyserial==3.5"), and none is
installed before the user allows it. An update that adds or changes a package
asks again.

Nothing of a plugin runs before its approval: the manager reads only its
manifest, so plugin.py is not imported (no top-level code runs) and no
package is installed until then (manager.py).

Built-in plugins need no approval: a plugin is built in when its folder holds
the copy bundled with this Sonorium (loader.is_bundled_copy), not because its
manifest says "builtin". A newer copy of a built-in plugin installed from
elsewhere (the catalog, an upload) asks like any other plugin.

Honest limit: a plugin's Python runs inside Sonorium. These checks are made
by core's services, so they stop mistakes and show intent; a plugin written
to cheat could go around them (docs/PLUGIN_DEVELOPMENT.md section 11). The
network permissions are shown, not enforced.

Approvals live in the framework store (section "permissions"):
{plugin_id: {"granted": [...], "full": bool, "requirements": [...]}}.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from sonorium.obs import logger

# id -> what the user is shown ("Allow <plugin> to: ...")
PERMISSIONS: dict[str, str] = {
    "channels.control": "Control your channels",
    "themes.write": "Change your themes",
    "audio.sounds": "Play sounds and announcements",
    "audio.outputs": "Add speakers",
    "audio.tap": "Listen to what plays",
    "timeline": "Know when sounds play",
    "devices": "Add devices",
    "attributes": "Keep values on your themes, channels and speakers",
    "ui": "Add to the screens",
    "http": "Answer web requests",
    "ha.entities": "Add entities to Home Assistant",
    "homeassistant": "Use your Home Assistant connection",
    "mqtt": "Use the MQTT broker",
    "commands": "Take commands from remotes and buttons",
    "packages": "Install Python packages",
    "network.lan": "Talk to devices on your network",
    "network.internet": "Reach the internet",
}
# "devices.<kind>" and "attributes.<object class>": one kind or class only
SCOPED = {"devices": "Add devices: {}", "attributes": "Keep values on: {}"}
_SCOPE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
FULL = "*"  # no list: everything
PACKAGE = "package:"  # "package:<requirement>": a Python package it will install
MAX_PERMISSIONS = 40


def label(permission: str) -> str:
    if permission == FULL:
        return "Everything a plugin can do"
    if permission.startswith(PACKAGE):
        return f"Install the Python package {permission[len(PACKAGE):]}"
    if permission in PERMISSIONS:
        return PERMISSIONS[permission]
    base, _, scope = permission.partition(".")
    if base in SCOPED and scope:
        return SCOPED[base].format(scope.replace("_", " "))
    return permission


def normalize(declared) -> tuple[Optional[list], list[str]]:
    """(the permissions as kept, or None for no list; the problems)."""
    if declared is None:
        return None, []
    if not isinstance(declared, list):
        return [], ["'permissions' must be a list"]
    kept, problems = [], []
    for item in declared[:MAX_PERMISSIONS]:
        if not isinstance(item, str):
            problems.append(f"permission {item!r}: not text")
            continue
        base, _, scope = item.partition(".")
        ok = item in PERMISSIONS or (base in SCOPED and bool(_SCOPE.match(scope)))
        if not ok:
            problems.append(f"unknown permission '{item}'")
            continue
        if item not in kept:
            kept.append(item)
    if len(declared) > MAX_PERMISSIONS:
        problems.append(f"at most {MAX_PERMISSIONS} permissions")
    return kept, problems


def covers(granted: Iterable[str], permission: str) -> bool:
    """A granted list allows this permission ("devices" covers "devices.light")."""
    granted = set(granted)
    if FULL in granted or permission in granted:
        return True
    base, _, scope = permission.partition(".")
    return base in SCOPED and bool(scope) and base in granted


class Forbidden(Exception):
    """A plugin asked for something it didn't list (ActionError "forbidden" in the facades)."""


class PluginPermissions:
    """store: the framework store; plugin_of(plugin_id): the plugin (its manifest)."""

    def __init__(self, store=None, plugin_of=None):
        self.store = store
        self.plugin_of = plugin_of or (lambda pid: None)
        self._memory: dict = {}

    def _table(self) -> dict:
        if self.store is None:
            return self._memory
        return self.store.main.section("permissions")

    def _save(self) -> None:
        if self.store is not None:
            self.store.main.save()

    # --- what a plugin asks for ---

    @staticmethod
    def declared(plugin) -> Optional[list]:
        """The plugin's list (None: it gives none, so it may use everything)."""
        manifest = getattr(plugin, "_manifest", None) or {}
        kept, _ = normalize(manifest.get("permissions"))
        return kept

    @staticmethod
    def problems(plugin) -> list[str]:
        manifest = getattr(plugin, "_manifest", None) or {}
        return normalize(manifest.get("permissions"))[1]

    @staticmethod
    def requirements(plugin) -> list[str]:
        """The Python packages the plugin will install ([] if none or not well formed: then it is refused anyway)."""
        from sonorium.plugins.packages import PackageError, check_requirements
        manifest = getattr(plugin, "_manifest", None) or {}
        try:
            return check_requirements(manifest.get("requirements"))
        except PackageError:
            return []

    def granted(self, plugin_id: str) -> list:
        entry = self._table().get(plugin_id) or {}
        out = list(entry.get("granted") or [])
        if entry.get("full"):
            out.append(FULL)
        return out

    def approved_requirements(self, plugin_id: str) -> list[str]:
        """The packages the user allowed this plugin to install."""
        return list((self._table().get(plugin_id) or {}).get("requirements") or [])

    def adopt_requirements(self, plugin_id: str, requirements: list[str]) -> None:
        """
        An approval from before packages were part of it: the packages that
        were already installed for the plugin count as approved (it ran with
        them), so it isn't asked again for them.
        """
        entry = self._table().get(plugin_id)
        if entry is None or "requirements" in entry:
            return
        entry["requirements"] = list(requirements)
        self._save()

    def pending(self, plugin) -> list[str]:
        """
        What the plugin asks for and the user hasn't approved yet ([FULL] for a
        plugin without a list), then each package not approved ("package:<requirement>").
        """
        if plugin is None or getattr(plugin, "_bundled", False):
            return []
        declared = self.declared(plugin)
        granted = self.granted(plugin.id)
        if declared is None:
            out = [] if FULL in granted else [FULL]
        else:
            out = [p for p in declared if p not in granted and not (FULL in granted)]
        approved = set(self.approved_requirements(plugin.id))
        return out + [PACKAGE + r for r in self.requirements(plugin) if r not in approved]

    def approve(self, plugin) -> list:
        """The user approved what the plugin asks for now, its packages too. Returns the granted list."""
        declared = self.declared(plugin)
        requirements = self.requirements(plugin)
        table = self._table()
        if declared is None:
            table[plugin.id] = {"granted": [], "full": True, "requirements": requirements}
        else:
            table[plugin.id] = {"granted": list(declared), "full": False, "requirements": requirements}
        self._save()
        logger.info(f"Plugin {plugin.id}: permissions approved: {', '.join(declared) if declared is not None else 'everything'}"
                    + (f"; packages: {', '.join(requirements)}" if requirements else ""))
        return self.granted(plugin.id)

    def recorded(self, plugin_id: str) -> bool:
        """The user decided on this plugin's permissions before."""
        return plugin_id in self._table()

    def forget(self, plugin_id: str) -> None:
        if self._table().pop(plugin_id, None) is not None:
            self._save()

    # --- the checks the services make ---

    def allowed(self, plugin_id: str, permission: str) -> bool:
        plugin = self.plugin_of(plugin_id)
        if plugin is None or getattr(plugin, "_bundled", False):
            return True
        declared = self.declared(plugin)
        if declared is None:
            return True  # no list: everything (approved as such before it ran)
        return covers(declared, permission) and covers(self.granted(plugin_id), permission)

    def require(self, plugin_id: str, permission: str) -> None:
        """ActionError "forbidden" when the plugin may not do this."""
        if not self.allowed(plugin_id, permission):
            from sonorium.plugins.actions import ActionError
            plugin = self.plugin_of(plugin_id)
            name = getattr(plugin, "name", None) or plugin_id
            raise ActionError("forbidden", f"{name} may not {label(permission)[0].lower()}{label(permission)[1:]}: "
                                           f"add '{permission}' to its permissions")

    def describe(self, plugin) -> dict:
        """For the plugin's row: what it asks for, what is approved, what waits for approval."""
        declared = self.declared(plugin)
        pending = self.pending(plugin)
        return {
            "permissions": None if declared is None else [{"id": p, "label": label(p)} for p in declared],
            "permissions_pending": [{"id": p, "label": label(p)} for p in pending],
            "requirements": self.requirements(plugin),
            "permission_problems": self.problems(plugin),
        }


def catalogue() -> dict:
    return {"permissions": [{"id": k, "label": v} for k, v in PERMISSIONS.items()],
            "scoped": {k: v.format("<" + ("kind" if k == "devices" else "object class") + ">") for k, v in SCOPED.items()}}
