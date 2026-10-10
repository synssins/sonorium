# Sonorium

![Sonorium](https://raw.githubusercontent.com/synssins/sonorium/main/logo.png)

## Announcements

> ## ⭐ Major Release: 1.5.0, Themes 2.0
>
> This is a major update. Themes can now hold **groups**, channels playing the same theme can each use their **own preset**, the **Theme Editor**, the **Themes** page and every **Settings** page are rebuilt, and the playback modes have **new names**.
>
> Before, two channels playing the same theme shared one preset: change it on one, and it changed on both. Now each channel is isolated.
>
> Your themes are converted automatically the first time this version starts. Each theme's old settings file is kept next to it as `metadata.json.pre-presets.bak`.

## About

**Multi-Zone Ambient Soundscape Mixer for Home Assistant**

[![Add Repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsynssins%2Fsonorium)

Sonorium lets you create immersive ambient audio environments throughout your home. Stream richly layered soundscapes—from distant thunder and rainfall to forest ambiance and ocean waves—to any combination of media players in your Home Assistant setup.

### Why Ambient Sound?

Ambient soundscapes aren't just background noise—they're a powerful tool for mental wellness and productivity. Research shows that ambient sounds can help with:

- **ADHD & Focus**: White noise and nature sounds can improve concentration by providing consistent auditory input that helps filter out distracting sounds. Studies suggest that background noise may trigger [stochastic resonance](https://pmc.ncbi.nlm.nih.gov/articles/PMC6481398/), potentially enhancing cognitive performance in individuals with ADHD.

- **Misophonia**: For those triggered by specific sounds, [ambient masking](https://www.getinflow.io/post/sound-sensitivity-and-adhd-auditory-processing-misophonia) with nature sounds or white noise can help "cover" trigger sounds and reduce emotional responses.

- **Sensory Processing**: Individuals with [sensory processing differences](https://pubmed.ncbi.nlm.nih.gov/17436843/), including those on the autism spectrum, may benefit from controlled ambient environments that provide predictable, soothing auditory input.

- **Anxiety & Stress**: Nature sounds like rain, ocean waves, and forest ambiance have been shown to activate the parasympathetic nervous system, promoting relaxation and reducing stress hormones.

- **Sleep**: Consistent ambient sound can mask disruptive noises and create a sleep-conducive environment.

- **Work & Study**: The "coffee shop effect"—moderate ambient noise can boost creative thinking and sustained attention.

### Screenshots

#### Channels
Each channel plays its own theme on its own speakers.

![Channels](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Channels.png)

#### Edit Channel
Search themes, then pick speakers by floor, room or one at a time.

![Edit Channel](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Channel_Editor.png)

#### Themes
Search, filter by category, and switch between cards and a list.

![Themes](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Themes.png)

#### Edit Theme
Tracks in one table, with groups, presets and a live preview.

![Edit Theme](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Theme_Editor.png)

#### Theme Editor help
The **?** button explains modes, groups and saving.

![Theme Editor help](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Theme_Editor_Help.png)

#### Settings → Connection
Home Assistant, MQTT and Streaming, each with its status (Docker).

![Settings → Connection](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Settings_Connection.png)

#### Settings → Audio Settings
Sound settings in one card, with Save and Cancel in the top bar.

![Settings → Audio Settings](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Settings_Audio.png)

#### Settings → Speakers
Speakers grouped by floor and area: rename, area, offset, test sound, on or off.

![Settings → Speakers](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Settings_Speakers.png)

#### Add Speaker
Add a speaker the network scan didn't find, by its address (Docker).

![Add Speaker](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Add_Speaker.png)

#### Settings → Logs
Recent messages with filters, search, copy and download.

![Settings → Logs](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Settings_Logs.png)

#### Help on every page
The **?** in the top bar explains the page you're on.

![Help on every page](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Page_Help.png)

#### On a phone
The menu opens from the top bar.

<img src="https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Phone_Themes.png" width="260" alt="Themes on a phone"> <img src="https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Phone_Menu.png" width="260" alt="Menu on a phone">

## Features

#### Multi-Zone Audio
- **Multiple Channels**: Run up to 10 independent audio channels simultaneously (configurable)
- **Per-Channel Themes**: Each channel plays its own theme
- **Flexible Speaker Selection**: Target individual speakers, entire rooms, floors, or custom speaker groups
- **Live Speaker Management**: Add or remove speakers from active channels without interrupting playback

#### Speakers
- **Speaker Settings**: Rename speakers, set their room, and add a volume offset for speakers that play louder or quieter than the rest
- **Test Sound**: Play a short, quiet chime to check a speaker
- **Choose What Sonorium Uses**: Speakers switched off in Settings never appear in channels

#### Theme System
- **Theme-Based Organization**: Audio files organized into theme folders (Thunder, Forest, Ocean, etc.)
- **Automatic Mixing**: All recordings in a theme blend together seamlessly
- **Theme Favorites**: Star your most-used themes for quick access
- **Custom Categories**: Organize themes into categories like "Weather", "Nature", "Urban"
- **Theme Icons**: Visual icons for easy theme identification

#### Playback Control
- **Per-Channel Volume**: Independent volume control for each channel
- **Master Gain**: Global output level control
- **Crossfade Looping**: Seamless loops with equal-power crossfades
- **Play/Pause/Stop**: Full transport controls per channel

#### Track Mixer
Fine-tune how each audio file plays within a theme:

- **Interval** - Set how often each track is heard in the mix (0-100%)
- **Per-Track Volume** - Adjust the amplitude of individual tracks independent of interval.
- **Playback Modes** - Choose how each track behaves:
  - **Auto** - Picks for you: sounds under 15 seconds play Intermittent and longer ones play Ebb & Flow; at 100% "interval", either plays as Background
  - **Background** - Plays all the time, looping with a smooth crossfade (rain, wind, a crackling fire)
  - **Intermittent** - Plays once, then goes quiet for a while before playing again; "interval" sets the wait (bird calls, thunder claps, a door creaking)
  - **Ebb & Flow** - Fades in, plays for a while, fades out, then stays quiet before coming back; "interval" sets how much of the time it's heard (distant traffic, a passing crowd)
- **Groups** - A group is a folder inside the theme whose tracks take turns: only one plays at a time, never overlapping, with a random gap (in minutes) between them. Every track in a group plays Intermittent: its whole file once, start to finish, with no fade. The group picks the next track by weight: each time a track plays it gets heavier and less likely to be picked, the weight wears off as other tracks play, and a track's interval sets how fast. The same track never plays twice in a row. Background and Ebb & Flow are for the ambience outside groups. Deleting a group keeps its files.

#### Home Assistant Dashboard Integration
- **MQTT Entities** - Full dashboard control via MQTT (session select, theme/preset dropdowns, play/stop, volume)
- **Human-Readable Names** - Theme and preset dropdowns show names instead of UUIDs
- **Status Sensors** - See playback status and assigned speakers from your dashboard
- **Automation Support** - Use HA automations to trigger soundscapes (morning alarms, schedules, etc.)

#### Modern Web Interface
- **Responsive Design**: Works on desktop and mobile; on a phone the menu opens from a top bar that's always there
- **Help on Every Page**: The **?** in the top bar explains the page you're on
- **Dark Theme**: Easy on the eyes
- **Real-Time Status**: See what's playing across all channels
- **Drag & Drop**: Upload audio files directly through the UI
- **Speaker Browser**: Visual hierarchy of floors, areas, and speakers
- **Logs Page**: Recent messages with filters, search and download, even when startup fails
- **Speaker Screens**: Google Cast displays show the Sonorium logo while playing

#### Home Assistant Integration
- **Sidebar Access**: Appears in your HA sidebar for quick access
- **Ingress Support**: Secure access through Home Assistant's authentication
- **Media Player Discovery**: Automatically finds all media_player entities
- **Area & Floor Awareness**: Speakers organized by Home Assistant areas and floors

### Supported Speakers

Sonorium can stream to any `media_player` entity in your Home Assistant setup:

- **Google Cast devices** (Chromecast, Nest Hub, Google Home) - with automatic fallback for cross-VLAN setups
- **Sonos speakers** - Native support via SoCo library for direct device communication
- **Amazon Echo** (via HA integration)
- **VLC media player**
- **Music Assistant players**
- Most smart speakers with Home Assistant integration

### Theme Management

All theme management is done through the Sonorium web interface:

1. **Create Themes**: Click the + button in the Themes section
2. **Upload Audio**: Drag and drop audio files or click to upload
3. **Organize**: Set categories, icons, and favorites

**Supported formats:** `.mp3`, `.wav`, `.flac`, `.ogg`

**Single-File Themes:** Themes with one audio file loop seamlessly using crossfade blending—no jarring restarts!

**Bundled Themes:** Sleigh Ride, Tavern, and "A Rainy Day... Or is it?" are included out of the box.

## Installation

### Requirements

- **An MQTT broker.** Install the **Mosquitto broker** add-on (Settings → Add-ons → Add-on Store), start it, and turn on **Start on boot**. Sonorium finds it automatically. Without a broker, Sonorium won't start and the sidebar shows **502: Bad Gateway**. Using a different broker? Set `sonorium__mqtt_host` (and port, username, password) in Sonorium's configuration.
- **Home Assistant in a virtual machine (Proxmox etc.):** if Sonorium's log shows a numpy or CPU error at startup, set the VM's CPU type to **host** (Proxmox: VM → Hardware → Processors → Type), then fully shut down and start the VM. The default `kvm64` type hides CPU features some audio libraries need.

### Quick Start

1. **Install** the addon and start it
2. **Open Sonorium** from your Home Assistant sidebar
3. **Add Themes**: Create themes and upload audio via the web interface
4. **Create a Channel**: Click "New Channel", select a theme and speakers
5. **Play**: Hit the play button and enjoy your ambient soundscape

### Configuration

#### Addon Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `log_level` | `info` | `info` shows a startup summary and errors; `debug` adds per-theme and per-speaker detail and library versions |
| `sonorium__stream_url` | `auto` | Base URL for streams (auto-detects HA IP) |
| `sonorium__path_audio` | `/media/sonorium` | Path to theme folders |
| `sonorium__max_channels` | `6` | Maximum concurrent channels (1-10) |
| `sonorium__mqtt_host` | `auto` | MQTT broker. `auto` uses the Mosquitto broker add-on; set a host only for a different broker (plus port, username, password) |

#### Where data is stored

- **Settings, channels and plugins:** the add-on's own config folder (`addon_configs/<id>_sonorium/sonorium` in your HA config share). Uninstalling with "delete data" removes it.
- **Themes and audio:** `/media/sonorium`. These are your files and are never deleted by an uninstall.

#### Web UI Settings

Access Settings from the sidebar to configure:
- **Crossfade Duration**: Blend time between loops (0-10 seconds)
- **Default Volume**: Initial volume for new channels
- **Master Gain**: Global output level
- **Speakers**: Switch speakers on or off, set their name, room and volume offset, and play a test sound
- **Speaker Groups**: Saved sets of speakers for quick channel setup
- **Logs**: Recent messages, with level filter, search, copy and download

### Troubleshooting

#### No Sound
- Check that your media player supports HTTP audio streams
- Verify the stream URL is accessible from your speaker
- Check the channel volume and master gain aren't set to 0

#### Cast Device Not Playing
- Update to 1.3.0 or later: earlier versions sent an option that made idle Cast devices ignore playback
- Check that Home Assistant itself can control the device (change its volume from HA)
- Cast devices take about 10 seconds to start: they fill a buffer before playing
- In the add-on's **Log** tab, the lines from "Playing session" to "speakers started" show what happened

#### Speakers Not Showing
- Ensure speakers are media_player entities in Home Assistant
- Check that speakers aren't disabled in Sonorium settings
- Try refreshing speakers from the Settings page

#### Theme Not Loading
- Verify audio files are in supported formats
- Check file permissions on `/media/sonorium/`
- Look for errors in the addon logs

### MQTT Discovery Troubleshooting

Channels keep the MQTT identity they were created with, so renaming a channel
changes its display name but not its entity IDs. Your automations keep working.

Versions before 1.3.0 could leave behind entities for channels that were
renamed or deleted. To remove one, find its discovery topic
(`homeassistant/<component>/<object_id>/config`, shown in the entity's MQTT
info), then go to Settings → Devices & services → MQTT → **Configure**, enter
that topic under **Publish a packet**, leave the payload empty, tick **Retain**
and publish. Only remove leftover topics, not current channels. Back up any
automations that use the entity first.

## What's new

### 1.5.0

**Themes and playback**
- **Groups.** A folder inside a theme is a group: its tracks take turns, one at a time, never overlapping, with a random gap between them. This replaces the need to check "Exclusive" on every track that needed to play independently of other exclusive tracks in a theme.
- **The group picks the next track by weight.** Each time a track plays it gets heavier and less likely to be picked; the weight wears off as other tracks play. A track's Interval sets how fast. The same track never plays twice in a row.
- **Grouped tracks just play.** On its turn a track plays its whole file once, start to finish, with no fade in or out. A thunder crack keeps its crack, a song its first notes.
- **A preset per channel.** Two channels can play the same theme with different presets. Before, they shared one: changing the preset on one changed it on both.
- **New files join right away.** A file added to a theme that is playing joins the mix at once, with its saved settings. A removed file leaves the mix. Nothing restarts.
- **Theme files stay tidy.** `metadata.json` and the presets follow the theme's files: a new file gets an entry with the default settings, and entries for removed files and group folders are deleted. The first time 1.5.0 starts it also clears out old leftover entries.
- **New mode names.** Continuous is now **Background**, Sparse is **Intermittent**, Presence is **Ebb & Flow**. The "How often" slider is now **Interval**. Your saved settings keep working.
- **Steadier sound.** The mix keeps an even level as tracks come and go, without clipping. Themes with many occasional sounds no longer play quietly.

**Theme Editor**
- **Redesigned.** Name, categories and description at the top; every track in one aligned table with its mode, volume, interval and mute; groups as sections you can collapse; upload, drag tracks between groups, and presets at the bottom.
- **Live changes.** What you change is heard on speakers playing the theme while you edit. **Cancel** reverts unsaved changes; **Save Theme** keeps them and closes.
- **Preview mix** plays the whole theme on the device you're editing from, without touching the speakers.
- **Help.** The **?** button explains modes, groups and saving.

**Themes page**
- Search, category chips, sort, and cards or a list. Each theme appears once, with its categories, a preview button and a badge for the channel playing it.
- **Create theme** asks for a name and opens the new theme in the editor.

**Every page**
- **New look.** Every page has the same top bar, lined up with the logo. Page buttons, including **Save** and **Cancel**, sit on the right of the top bar instead of at the bottom.
- **Help on every page.** The **?** at the far right of the top bar explains that page in plain words.
- **Settings pages** are compact and left-aligned:
  - **Connection** shows Home Assistant, MQTT and Streaming as cards, each with a status.
  - **Audio Settings** is a single card.
  - **Speakers** groups speakers by floor and area.
  - **Speaker Groups**, **Plugins**, **Advanced** and **Status** use the same layout.
- **"Add" everywhere.** Buttons that make something new read "Add": **Add speaker**, **Add group**, **Add floor**, **Add area**. Empty pages no longer repeat the top-bar button.
- **Confirmations** use a small prompt on the page instead of a browser pop-up.
- **Phones.** A top bar with the menu button is always there. The menu opens downward, closes when you pick a page or tap the button again, and Settings expands in place. Cards fill the screen width and nothing scrolls sideways.

**Docker and standalone**
- **Remove a connection without a restart.** The trash button on **Settings → Connection** removes Home Assistant, and its floors, areas and speakers disappear from every list at once. Removing MQTT also removes Sonorium's entities from Home Assistant.
- **Floors & Areas** can be added and edited outside the Home Assistant app.
- **Settings show what applies to your install.** One install check decides which settings each install shows.

**Fixes**
- A memory leak while playing.
- Tracks in a group could overlap after a stall.
- Presets changed settings they hadn't saved.
- The Settings menu cut off its last items.
- Adding floors and areas failed with an error.
- Deleting a speaker group in use said "session(s)" instead of "channel(s)".
- The Theme Editor opened with a preset already selected, which made it easy to overwrite.

**Known issue**
- In the Home Assistant app, **Crossfade** and **Master output gain** on **Settings → Audio Settings** are saved but don't change the sound yet.

#### How a theme plays

These are the same words as the **?** help in the Theme Editor.

A theme is a set of tracks (sound files) mixed together. Each track has a **mode**, a **volume** and an **interval**.


**Modes**

- **Background:** plays all the time, looping smoothly. Rain, wind, a fire.
- **Intermittent:** plays once, then goes quiet before playing again. *Interval* sets the wait. In a group, the group decides when it plays. Bird calls, a thunder crack, a door.
- **Ebb & Flow:** fades in, plays a while, fades out, then stays quiet before coming back. *Interval* sets how much of the time it's heard. Distant traffic, a passing crowd.
- **Auto:** picks for you. Under 15 seconds plays Intermittent, longer plays Ebb & Flow. At 100% *interval*, either plays as Background.

**Groups**

- **Take turns:** a group is a folder of tracks where only one plays at a time, never overlapping. Use it for sounds that shouldn't pile up, like thunder cracks or songs from one musician.
- **Gap:** after a track finishes, the group waits a random time in this range (minutes) before the next one starts. Different groups don't wait for each other.
- **Intermittent only:** on its turn, a track plays its whole file once, start to finish, then hands over. Background and Ebb & Flow are for the ambience outside groups.
- **Weight:** each time a track plays, it gets heavier and is less likely to be picked next. The weight wears off as other tracks play. The same track never plays twice in a row.
- **Interval:** how fast a track's weight wears off. Higher comes back sooner; lower plays less often.
- **Group volume, interval, mute:** scale every track in the group.
- **Moving tracks:** drag a track onto a group, or use its ⋯ menu.
- **Deleting a group:** keeps its files; they move back into the theme.

**Saving**

- **Live:** changes are heard on speakers playing this theme, and on this device with Preview mix.
- **Cancel:** Reverts unsaved changes.
- **Save Preset:** Saves loaded preset
- **Save Theme:** Saves theme with all changes (presets, name, etc) Required after saving a preset.
- **New:** makes a new preset from the current mix.

## API Reference

Sonorium provides a REST API for integration and automation:

#### Streams
- `GET /stream/{theme_id}` - Direct audio stream for a theme
- `GET /stream/channel{n}` - Audio stream for channel N

#### Channels
- `GET /api/channels` - List all channels
- `POST /api/sessions` - Create a new channel/session
- `POST /api/sessions/{id}/play` - Start playback
- `POST /api/sessions/{id}/stop` - Stop playback
- `POST /api/sessions/{id}/volume` - Set volume

#### Themes
- `GET /api/themes` - List all themes
- `POST /api/themes/create` - Create a new theme
- `POST /api/themes/{id}/upload` - Upload audio file

#### Status
- `GET /api/status` - Current system status

## Acknowledgements

Sonorium is a fork of [Amniotic](https://github.com/fmtr/amniotic) by [fmtr](https://github.com/fmtr). The original Amniotic project laid the groundwork for this addon with its innovative approach to ambient soundscape mixing in Home Assistant. We're grateful for the time, effort, and creativity that went into building the foundation that Sonorium is built upon.

## License

See LICENSE file for details.

## Contributing

Contributions are welcome! Please open an issue to discuss changes before submitting a PR.
