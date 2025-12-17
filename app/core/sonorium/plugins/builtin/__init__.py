"""
Sonorium Built-in Plugins

These plugins are shipped with Sonorium and are copied to config/plugins/
on first startup if not already present.
"""

from pathlib import Path

BUILTIN_PLUGINS_DIR = Path(__file__).parent

__all__ = ['BUILTIN_PLUGINS_DIR']
