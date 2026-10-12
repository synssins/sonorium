"""
JSON files Sonorium keeps settings and secrets in: read once, written
atomically (temp file, then replace). A private file is created owner-only
(0600) before anything is written into it.

Used by the core (security.json), the plugin framework (plugin_framework.json,
plugin_secrets.json) and Users (users.json).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from sonorium.obs import logger


class JsonFile:
    """A JSON object on disk, read once and written atomically on save()."""

    def __init__(self, path: Path, private: bool = False):
        self.path = Path(path)
        self.private = private
        self._lock = threading.RLock()
        self.existed = self.path.exists()
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
