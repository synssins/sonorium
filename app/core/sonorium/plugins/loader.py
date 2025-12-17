"""
Sonorium Plugin Loader

Handles discovery and dynamic loading of plugins from the plugins directory.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Optional, Type

from sonorium.plugins.base import BasePlugin
from sonorium.obs import logger


def get_plugins_dir() -> Path:
    """Get the plugins directory (next to the EXE in a 'plugins' folder)."""
    import sys

    if getattr(sys, 'frozen', False):
        # Running as compiled EXE - plugins folder next to EXE
        plugins_dir = Path(sys.executable).parent / 'plugins'
    else:
        # Running as script - plugins folder next to project root
        plugins_dir = Path(__file__).parent.parent.parent / 'plugins'

    plugins_dir.mkdir(parents=True, exist_ok=True)
    return plugins_dir


def discover_plugins(plugins_dir: Optional[Path] = None) -> list[Path]:
    """
    Discover plugin directories.

    Each plugin must be a directory containing at least a plugin.py file.

    Args:
        plugins_dir: Root directory to scan for plugins

    Returns:
        List of paths to valid plugin directories
    """
    if plugins_dir is None:
        plugins_dir = get_plugins_dir()

    if not plugins_dir.exists():
        logger.info(f"Plugins directory does not exist: {plugins_dir}")
        return []

    plugin_dirs = []
    for item in plugins_dir.iterdir():
        if item.is_dir():
            plugin_file = item / "plugin.py"
            if plugin_file.exists():
                plugin_dirs.append(item)
                logger.debug(f"Found plugin directory: {item.name}")
            else:
                logger.debug(f"Skipping {item.name}: no plugin.py found")

    return plugin_dirs


def load_manifest(plugin_dir: Path) -> dict:
    """
    Load or generate a manifest for a plugin.

    If manifest.json exists, load it. Otherwise, try to generate one
    from the plugin class attributes.

    Args:
        plugin_dir: Path to the plugin directory

    Returns:
        Manifest dictionary
    """
    manifest_path = plugin_dir / "manifest.json"

    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text())
        except Exception as e:
            logger.warning(f"Failed to read manifest from {manifest_path}: {e}")

    # Generate default manifest
    return {
        "id": plugin_dir.name,
        "name": plugin_dir.name.replace("_", " ").title(),
        "version": "1.0.0",
        "description": "",
        "author": "Unknown",
        "entry_point": "plugin.py",
        "plugin_class": None,  # Will be auto-detected
    }


def save_manifest(plugin_dir: Path, manifest: dict) -> bool:
    """
    Save a manifest to disk.

    Args:
        plugin_dir: Path to the plugin directory
        manifest: Manifest data to save

    Returns:
        True if saved successfully
    """
    try:
        manifest_path = plugin_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        return True
    except Exception as e:
        logger.error(f"Failed to save manifest to {plugin_dir}: {e}")
        return False


def load_plugin_class(plugin_dir: Path, manifest: dict) -> Optional[Type[BasePlugin]]:
    """
    Dynamically load the plugin class from a plugin directory.

    Args:
        plugin_dir: Path to the plugin directory
        manifest: Plugin manifest

    Returns:
        Plugin class (not instance) if found, None otherwise
    """
    entry_point = manifest.get("entry_point", "plugin.py")
    plugin_file = plugin_dir / entry_point

    if not plugin_file.exists():
        logger.error(f"Plugin entry point not found: {plugin_file}")
        return None

    try:
        # Create a unique module name to avoid conflicts
        module_name = f"sonorium_plugin_{plugin_dir.name}"

        # Load the module dynamically
        spec = importlib.util.spec_from_file_location(module_name, plugin_file)
        if spec is None or spec.loader is None:
            logger.error(f"Failed to create module spec for {plugin_file}")
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # Find the plugin class
        plugin_class_name = manifest.get("plugin_class")

        if plugin_class_name:
            # Use explicitly specified class
            plugin_class = getattr(module, plugin_class_name, None)
        else:
            # Auto-detect: find first class that inherits from BasePlugin
            plugin_class = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BasePlugin)
                    and attr is not BasePlugin
                ):
                    plugin_class = attr
                    break

        if plugin_class is None:
            logger.error(f"No BasePlugin subclass found in {plugin_file}")
            return None

        logger.debug(f"Loaded plugin class: {plugin_class.__name__} from {plugin_dir.name}")
        return plugin_class

    except Exception as e:
        logger.error(f"Failed to load plugin from {plugin_dir}: {e}")
        return None


def instantiate_plugin(
    plugin_class: Type[BasePlugin],
    plugin_dir: Path,
    settings: dict,
    audio_path: Optional[Path] = None,
) -> Optional[BasePlugin]:
    """
    Create an instance of a plugin.

    Args:
        plugin_class: The plugin class to instantiate
        plugin_dir: Path to the plugin directory
        settings: Plugin settings from config
        audio_path: Path to audio/themes directory

    Returns:
        Plugin instance if successful, None otherwise
    """
    try:
        instance = plugin_class(
            plugin_dir=plugin_dir,
            settings=settings,
            audio_path=audio_path,
        )
        logger.info(f"Instantiated plugin: {instance.name} v{instance.version}")
        return instance
    except Exception as e:
        logger.error(f"Failed to instantiate plugin {plugin_class.__name__}: {e}")
        return None
