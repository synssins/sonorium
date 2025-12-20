#!/usr/bin/env python3
"""
Sync version across all HA addon files.

Single source of truth: sonorium/version

Run this script after updating the version file:
    python sync_version.py

This updates:
    - config.yaml (version field)
    - Dockerfile (LABEL default)
"""

import re
from pathlib import Path

# Get the directory containing this script
ADDON_DIR = Path(__file__).parent

# Single source of truth
VERSION_FILE = ADDON_DIR / "sonorium" / "version"

# Files to update
CONFIG_YAML = ADDON_DIR / "config.yaml"
DOCKERFILE = ADDON_DIR / "Dockerfile"


def get_version() -> str:
    """Read version from the source of truth."""
    return VERSION_FILE.read_text().strip()


def update_config_yaml(version: str) -> bool:
    """Update version in config.yaml."""
    content = CONFIG_YAML.read_text()
    new_content = re.sub(
        r'^version:\s*["\']?[\d.]+["\']?',
        f'version: "{version}"',
        content,
        flags=re.MULTILINE
    )
    if new_content != content:
        CONFIG_YAML.write_text(new_content)
        print(f"  Updated config.yaml to {version}")
        return True
    else:
        print(f"  config.yaml already at {version}")
        return False


def update_dockerfile(version: str) -> bool:
    """Update default version in Dockerfile LABEL."""
    content = DOCKERFILE.read_text()
    # Match: LABEL io.hass.version="${ADDON_VERSION:-X.X.X}"
    new_content = re.sub(
        r'(LABEL io\.hass\.version="\$\{ADDON_VERSION:-)[^}]+(}")',
        rf'\g<1>{version}\g<2>',
        content
    )
    if new_content != content:
        DOCKERFILE.write_text(new_content)
        print(f"  Updated Dockerfile LABEL default to {version}")
        return True
    else:
        print(f"  Dockerfile already at {version}")
        return False


def main():
    version = get_version()
    print(f"Syncing version: {version}")
    print()

    updated = False
    updated |= update_config_yaml(version)
    updated |= update_dockerfile(version)

    print()
    if updated:
        print("Done! Don't forget to commit the changes.")
    else:
        print("All files already in sync.")


if __name__ == "__main__":
    main()
