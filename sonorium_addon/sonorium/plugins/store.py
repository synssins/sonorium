"""
Where the plugin framework keeps its data, next to state.json:

  plugin_framework.json  scheduled jobs, the non-secret
                         values of plugin connections, framework settings
                         (time zone)
  plugin_secrets.json    owner-only file (0600): hashes of trigger and event
                         stream tokens, the secret values of plugin connections

Kept out of state.json on purpose: the scheduler writes after every job, and
secrets never sit in a file that is shown, backed up or edited as freely.
Every write is atomic (temp file, then replace).
"""

from __future__ import annotations

from pathlib import Path

from sonorium.auth.secrets import JsonFile  # noqa: F401  (the core's; imported from here before)

FRAMEWORK_FILE = "plugin_framework.json"
SECRETS_FILE = "plugin_secrets.json"


class FrameworkStore:
    """Both files of the framework, in one folder (the folder of state.json)."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.main = JsonFile(self.folder / FRAMEWORK_FILE)
        self.secrets = JsonFile(self.folder / SECRETS_FILE, private=True)
