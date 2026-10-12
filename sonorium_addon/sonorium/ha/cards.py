"""
Home Assistant dashboard cards (web/static/ha/sonorium-cards.js).

The cards are installed into Home Assistant only when both Home Assistant and
MQTT are linked: they work through the entities Sonorium publishes over MQTT
(ha/mqtt_entities.py).

- HA add-on: the card file is copied to Home Assistant's www folder
  (/homeassistant/www/sonorium/, the add-on's homeassistant_config mapping) and
  registered as a dashboard resource at /local/sonorium/sonorium-cards.js.
- Docker / Windows: Home Assistant loads the card from Sonorium itself
  (/static/ha/sonorium-cards.js, CORS allowed for the linked HA address).

The resource URL carries ?v=<Sonorium version>, so browsers fetch the card
again after an update (and the card can tell it is out of date).

When the resource can't be registered (HA's dashboards in YAML mode, a token
that isn't an administrator's, an https Home Assistant with an http Sonorium)
nothing fails: the status says to add the card by hand, and Settings ›
Connection offers the file to download.

Also here: Home Assistant's entity IDs for Sonorium's entities, read from its
entity registry, so the cards find entities the user renamed.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import urlsplit

from sonorium.obs import logger

CARD_FILE = Path(__file__).resolve().parents[1] / "web" / "static" / "ha" / "sonorium-cards.js"
CARD_NAME = "sonorium-cards.js"
ADDON_WWW_DIR = Path("/homeassistant/www/sonorium")
ADDON_URL = f"/local/sonorium/{CARD_NAME}"
STATIC_PATH = f"/static/ha/{CARD_NAME}"

# Status states and the line Settings › Connection shows for each
INSTALLED = "installed"
INSTALLING = "installing"
NEEDS_HA = "needs_ha"
NEEDS_MQTT = "needs_mqtt"
BY_HAND = "by_hand"

LINES = {
    INSTALLED: "Dashboard cards: installed",
    INSTALLING: "Dashboard cards: installing…",
    NEEDS_HA: "",  # not shown: Settings › Connection hides the line without Home Assistant
    NEEDS_MQTT: "Dashboard cards need MQTT",
    BY_HAND: "Dashboard cards: add by hand",
}

# Why the cards have to be added by hand (a one-line tooltip)
REASONS = {
    "yaml": "Home Assistant's dashboard resources are set in YAML",
    "not_admin": "The access token isn't an administrator's",
    "mixed_content": "Home Assistant uses https and Sonorium http",
    "no_url": "Sonorium's address isn't known",
    "copy": "Couldn't write to Home Assistant's www folder",
    "unreachable": "Home Assistant didn't answer",
    "error": "Home Assistant refused the resource",
}

Ws = Callable[[list], Awaitable[list]]


def resource_url(install: str, version: str, sonorium_url: str = "") -> str:
    """The dashboard resource URL for this install (None-safe; "" when unknown)."""
    if install == "addon":
        base = ADDON_URL
    elif sonorium_url:
        base = sonorium_url.rstrip("/") + STATIC_PATH
    else:
        return ""
    return f"{base}?v={version}" if version else base


def is_ours(url: str) -> bool:
    """A resource that is Sonorium's card (any version, either install)."""
    path = urlsplit(url or "").path
    return path.endswith(f"/sonorium/{CARD_NAME}") or path.endswith(STATIC_PATH)


def mixed_content(ha_url: str, sonorium_url: str) -> bool:
    """An https Home Assistant can't load a script from an http Sonorium."""
    return (ha_url or "").lower().startswith("https://") and (sonorium_url or "").lower().startswith("http://")


def origin_of(url: str) -> str:
    """scheme://host[:port] of a URL ("" if it has none)."""
    parts = urlsplit((url or "").strip())
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else ""


def _reason_for(reply: dict) -> str:
    code = ((reply or {}).get("error") or {}).get("code", "")
    if code == "unknown_command":
        return "yaml"  # resources in YAML mode: no create/update commands
    if code == "unauthorized":
        return "not_admin"
    return "error"


class DashboardCards:
    """Installs the cards into Home Assistant and keeps their status."""

    def __init__(
        self,
        ws: Optional[Ws],
        *,
        install: str,
        version: str,
        ha_linked: Callable[[], bool],
        mqtt_manager,
        ha_url: str = "",
        sonorium_url: str = "",
        www_dir: Path = ADDON_WWW_DIR,
        card_file: Path = CARD_FILE,
    ):
        self.ws = ws
        self.install_type = install
        self.version = version
        self.ha_linked = ha_linked
        self.mqtt_manager = mqtt_manager
        self.ha_url = ha_url
        self.sonorium_url = sonorium_url
        self.www_dir = Path(www_dir)
        self.card_file = Path(card_file)
        self._state = INSTALLING
        self._reason = ""
        self._refresh_task: Optional[asyncio.Task] = None

    # ----- status -----

    def mqtt_linked(self) -> bool:
        return self.mqtt_manager is not None and not getattr(self.mqtt_manager, "_removed", False)

    def status(self) -> dict:
        """{"state", "line", "reason"} for Settings › Connection."""
        if not self.ha_linked():
            state, reason = NEEDS_HA, ""
        elif not self.mqtt_linked():
            state, reason = NEEDS_MQTT, ""
        else:
            state, reason = self._state, self._reason
        return {"state": state, "line": LINES[state], "reason": REASONS.get(reason, "")}

    def _set(self, state: str, reason: str = "") -> dict:
        self._state, self._reason = state, reason
        if state == BY_HAND:
            logger.info(f"Dashboard cards: add by hand ({REASONS.get(reason, reason)})")
        elif state == INSTALLED:
            logger.info("Dashboard cards installed in Home Assistant")
        return self.status()

    # ----- install -----

    async def install(self) -> dict:
        """Put the card where Home Assistant can load it and register it as a dashboard resource."""
        if not self.ha_linked() or not self.mqtt_linked() or self.ws is None:
            return self.status()

        if self.install_type == "addon":
            try:
                self.www_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.card_file, self.www_dir / CARD_NAME)
            except OSError as e:
                logger.warning(f"Could not copy the dashboard cards to {self.www_dir}: {e}")
                return self._set(BY_HAND, "copy")
        else:
            if not self.sonorium_url:
                return self._set(BY_HAND, "no_url")
            if mixed_content(self.ha_url, self.sonorium_url):
                return self._set(BY_HAND, "mixed_content")
        url = resource_url(self.install_type, self.version, self.sonorium_url)

        try:
            (listed,) = await self.ws([{"type": "lovelace/resources"}])
            if not listed.get("success"):
                return self._set(BY_HAND, _reason_for(listed))
            ours = [r for r in (listed.get("result") or []) if is_ours(r.get("url", ""))]
            commands = []
            if not ours:
                commands.append({"type": "lovelace/resources/create", "res_type": "module", "url": url})
            elif ours[0].get("url") != url or ours[0].get("type") != "module":
                commands.append({"type": "lovelace/resources/update", "resource_id": ours[0]["id"],
                                 "res_type": "module", "url": url})
            # The card registered twice would load twice
            commands += [{"type": "lovelace/resources/delete", "resource_id": r["id"]} for r in ours[1:]]
            if commands:
                replies = await self.ws(commands)
                if replies and not replies[0].get("success"):
                    return self._set(BY_HAND, _reason_for(replies[0]))
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            logger.warning(f"Could not register the dashboard cards in Home Assistant: {e}")
            return self._set(BY_HAND, "unreachable")
        except Exception as e:
            logger.warning(f"Could not register the dashboard cards in Home Assistant: {e}")
            return self._set(BY_HAND, "error")
        return self._set(INSTALLED)

    # ----- entity IDs -----

    async def refresh_entity_ids(self) -> bool:
        """Read Home Assistant's IDs for Sonorium's MQTT entities and republish the channels list."""
        if self.ws is None or not self.ha_linked() or not self.mqtt_linked():
            return False
        prefix = f"{getattr(self.mqtt_manager, 'prefix', 'sonorium')}_"
        try:
            (reply,) = await self.ws([{"type": "config/entity_registry/list"}])
        except Exception as e:
            logger.debug(f"Could not read Home Assistant's entity registry: {e}")
            return False
        if not reply.get("success"):
            return False
        self.mqtt_manager.registered_entity_ids = {
            e["unique_id"]: e["entity_id"]
            for e in (reply.get("result") or [])
            if e.get("platform") == "mqtt" and str(e.get("unique_id", "")).startswith(prefix) and e.get("entity_id")
        }
        await self.mqtt_manager.publish_channels()
        return True

    def schedule_entity_refresh(self, delay: float = 5.0):
        """Read the entity IDs again shortly (HA registers newly discovered entities first); repeated calls merge."""
        if self._refresh_task and not self._refresh_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def later():
            await asyncio.sleep(delay)
            await self.refresh_entity_ids()

        self._refresh_task = loop.create_task(later())

    async def start(self, delay: float = 10.0, tries: int = 3, retry_after: float = 60.0):
        """At startup: install (retrying while Home Assistant doesn't answer), then read the entity IDs."""
        await asyncio.sleep(delay)
        for attempt in range(tries):
            result = await self.install()
            if not (result["state"] == BY_HAND and self._reason == "unreachable") or attempt == tries - 1:
                break
            self._state, self._reason = INSTALLING, ""  # trying again
            await asyncio.sleep(retry_after)
        await self.refresh_entity_ids()
