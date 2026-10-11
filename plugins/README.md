# Sonorium Plugins

Downloadable plugins for Sonorium. These plugins are included by default in new installations but can be removed and reinstalled as needed.

## Installation

1. Open Sonorium and go to **Settings → Plugins**
2. Click the **Add Plugin** button
3. Select the `.zip` file for the plugin you want to install
4. The plugin will appear in your plugins list

## Available Plugins

### Ambient Mixer Importer

**File:** `ambient_mixer_v3.1.2.zip`

Import soundscapes from [Ambient-Mixer.com](https://ambient-mixer.com) directly into Sonorium.

**Features:**
- Import any public Ambient Mixer soundscape by URL
- Create new themes or add as presets to existing themes
- Automatic duplicate detection (filename and audio hash)
- Preserves each sound's volume, mute and loop settings; sounds the mix plays at random play Intermittent, about as often (within Settings → Advanced's Wait between plays)
- Attribution tracking for Creative Commons content

**Usage:**
1. Enable the plugin in Settings → Plugins
2. Find a soundscape on ambient-mixer.com and copy its URL
3. Open the plugin, paste the URL
4. Choose to create a new theme or add to an existing one
5. Click "Import Soundscape"

---

### Theme Merge

**File:** `theme_merge_v2.0.0.zip`

Combine two themes into a new theme, or one theme into the other.

**Features:**
- Copies the audio files with their settings, groups (subfolders) and group settings
- Combines presets (a clashing name gets the source theme's name); every preset sounds as before
- Linked files to either theme become that theme's track; other linked files stay linked
- Same-named tracks: skip (keep the existing one) or rename the new one
- Checks everything first: a merge completes or changes nothing

**Usage:**
1. Enable the plugin in Settings → Plugins
2. Select the source themes you want to merge
3. Choose a target theme (new or existing)
4. Choose how to handle same-named tracks and click "Merge Themes"

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

See the existing plugins for examples of the structure and API. The settings schema
format is described in `sonorium/plugins/settings_schema.py`; a plugin that creates or
changes themes uses `sonorium/plugins/theme_api.py` (`self.new_theme()`,
`self.open_theme()`, `self.list_themes()`) and returns `"refresh_themes": true` so
Sonorium rescans the themes.
