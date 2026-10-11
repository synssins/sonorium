"""
Where the plugin framework keeps its data, next to state.json:

  plugin_framework.json  scheduled jobs, notification rules, the non-secret
                         values of plugin connections, framework settings
                         (time zone)
  plugin_secrets.json    owner-only file (0600): hashes of trigger and event
                         stream tokens, the secret values of plugin connections

Kept out of state.json on purpose: the scheduler writes after every job, and
secrets never sit in a file that is shown, backed up or edited as freely.
Every write is atomic (temp file, then replace).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from sonorium.obs import logger

FRAMEWORK_FILE = "plugin_framework.json"
SECRETS_FILE = "plugin_secrets.json"


class JsonFile:
    """A JSON object on disk, read once and written atomically on save()."""

    def __init__(self, path: Path, private: bool = False):
        self.path = Path(path)
        self.private = private
        self._lock = threading.RLock()
        self.data: dict = self._read()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
            logger.warning(f"{self.path.name}: not a JSON object, starting empty")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.error(f"{self.path.name} could not be read ({e}), starting empty")
        return {}

    def section(self, name: str, default_factory=dict) -> Any:
        with self._lock:
            value = self.data.get(name)
            if value is None:
                value = self.data[name] = default_factory()
            return value

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            text = json.dumps(self.data, indent=2, sort_keys=True)
            if self.private:
                # Created owner-only, before any secret is written into it
                fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                try:
                    os.chmod(tmp, 0o600)
                except OSError:
                    pass
            else:
                tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)


class FrameworkStore:
    """Both files of the framework, in one folder (the folder of state.json)."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.main = JsonFile(self.folder / FRAMEWORK_FILE)
        self.secrets = JsonFile(self.folder / SECRETS_FILE, private=True)
