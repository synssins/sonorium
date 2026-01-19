# Sonorium Plugins

Downloadable plugins for Sonorium. These plugins are included by default in new installations but can be removed and reinstalled as needed.

## Installation

1. Open Sonorium and go to **Settings → Plugins**
2. Click the **Add Plugin** button
3. Select the `.zip` file for the plugin you want to install
4. The plugin will appear in your plugins list

## Available Plugins

### Ambient Mixer Importer

**File:** `ambient_mixer_v3.0.0.zip`

Import soundscapes from [Ambient-Mixer.com](https://ambient-mixer.com) directly into Sonorium.

**Features:**
- Import any public Ambient Mixer soundscape by URL
- Create new themes or add as presets to existing themes
- Automatic duplicate detection (filename and audio hash)
- Preserves volume and balance settings from original mix
- Attribution tracking for Creative Commons content

**Usage:**
1. Enable the plugin in Settings → Plugins
2. Find a soundscape on ambient-mixer.com and copy its URL
3. Open the plugin, paste the URL
4. Choose to create a new theme or add to an existing one
5. Click "Import Soundscape"

---

### MyNoise Importer

**File:** `mynoise_v1.0.0.zip`

Import soundscapes from [MyNoise.net](https://mynoise.net) into Sonorium.

**Features:**
- Import MyNoise generators by URL
- Configurable audio format (OGG, MP3, WAV)
- Automatic metadata creation

**Usage:**
1. Enable the plugin in Settings → Plugins
2. Find a generator on mynoise.net and copy its URL
3. Open the plugin, paste the URL and enter a theme name
4. Click "Import"

---

### Theme Merge

**File:** `theme_merge_v1.0.0.zip`

Combine multiple themes or presets into a single theme.

**Features:**
- Merge audio files from multiple source themes
- Combine presets from different themes
- Option to copy files or create references

**Usage:**
1. Enable the plugin in Settings → Plugins
2. Select the source themes you want to merge
3. Choose a target theme (new or existing)
4. Configure merge options and click "Merge"

---

## Uninstalling Plugins

1. Go to **Settings → Plugins**
2. Click the trash can icon next to the plugin you want to remove
3. Confirm removal

## Creating Your Own Plugins

Plugins are ZIP files containing:
- `manifest.json` - Plugin metadata and settings schema
- `plugin.py` - Plugin implementation (extends `BasePlugin`)
- `__init__.py` - Python package marker (can be empty)

See the existing plugins for examples of the structure and API.
