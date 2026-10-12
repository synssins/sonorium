# Sonorium

![Sonorium](https://raw.githubusercontent.com/synssins/sonorium/main/logo.png)

## Announcements

> ## ⭐ Sonorium has its own repository
>
> Sonorium began as a fork of [Amniotic](https://github.com/fmtr/amniotic). It has since been fully rewritten: all legacy code from the original repository it was forked from is gone. Sonorium now has its own repository at the same address, [synssins/sonorium](https://github.com/synssins/sonorium). The old fork is archived as [synssins/sonorium-legacy](https://github.com/synssins/sonorium-legacy). Home Assistant and Docker installs keep working as before.

> ## ⭐ 1.6.1: Refinements
>
> Refinements across Sonorium, with the Theme Editor and Channels page restructured.
>
> - **Sequences** play a theme's presets one after another, gliding from one to the next: a mild summer storm that becomes a heavy one.
> - **Playlists** play several themes one after another on a channel.
> - **Ebb & Flow:** tracks can be configured with a minimum and maximum volume, and will automatically move to any value between those two limits randomly for a period of time.
> - **Listen in your browser:** the headphones button on a channel plays it on the device you're using.
>
> Your themes are converted the first time this version starts: Auto tracks and Intrusion groups are retired. Each theme keeps backups of its old settings files (`*.pre-auto.bak`, `*.pre-links.bak`, `*.pre-duration.bak`).

> ## ⭐ 1.6.0: Merry-go-round and Intrusions
>
> Groups now have three modes. **Merry-go-round** turns a few recordings of the same scene into one continuous bed that crossfades from file to file and never repeats the same way. **Intrusion** lets a preset bring in sounds from another theme, like rain over a campsite, without copying any files. Tracks now fade in and out over 3 seconds when a preset turns them on or off.

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
Each channel plays a theme or a playlist on its own speakers, and can be heard in your browser too.

![Channels](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Channels.png)

#### Edit Channel
Search themes, then pick speakers by floor, room or one at a time.

![Edit Channel](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Channel_Editor.png)

#### Themes
Search, filter by category, and switch between cards and a list.

![Themes](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Themes.png)

#### Edit Theme
Tracks in sections by how they play: Unassigned, Background, Intermittent and Ebb & Flow, each with its own groups.

![Edit Theme](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Theme_Editor.png)

#### Ebb & Flow
An Ebb & Flow group, and a track's ⋯ menu with Fade in and Fade out, Rise, and Pause between plays.

![Ebb & Flow](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Ebb_Flow.png)

A steady breeze and gusts of wind, from the same wind recording: a narrow volume range with a longer Duration and a Gradual Rise, and a wide range with a shorter Duration and a Sudden Rise.

![A breeze, configured](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Ebb_Flow_Breeze.png)

![A gust, configured](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Ebb_Flow_Gust.png)

#### Theme Editor help
The **?** button explains sections, modes, groups, sequences and saving.

![Theme Editor help](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Theme_Editor_Help.png)

#### Edit sequence
A theme's presets in the order you choose, with the time on each and the ramp between them.

![Edit sequence](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Sequence_Editor.png)

#### A sequence step: While playing
Changes for one step of a sequence. Anything you don't change follows the preset.

![A sequence step: While playing](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Step_While_Playing.png)

#### Playlists
Themes that play one after another on a channel.

![Playlists](https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Playlists.png)

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

<img src="https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Phone_Channels.png" width="260" alt="Channels on a phone"> <img src="https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Phone_Themes.png" width="260" alt="Themes on a phone"> <img src="https://raw.githubusercontent.com/synssins/sonorium/main/screenshots/Phone_Menu.png" width="260" alt="Menu on a phone">

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

- **Sections** - Tracks sit in sections by how they play (Unassigned, Background, Intermittent, Ebb & Flow); change a track's Mode or drag it, and it moves there
- **Interval** - Set how often each track is heard in the mix (0-100%)
- **Per-Track Volume** - Adjust amplitude independent of interval
- **Playback Modes**:
  - **Background** - Plays all the time, looping with a smooth crossfade (rain, wind, a crackling fire)
  - **Intermittent** - Plays once, then goes quiet for a while before playing again; "interval" sets the wait (bird calls, thunder claps, a door creaking)
  - **Ebb & Flow** - Tracks can be configured with a minimum and maximum volume, and will automatically move to any value between those two limits randomly for a period of time. Duration increases or decreases the amount of time the track is at a value, while the Rise increases or decreases the speed at which the track reaches that value. With Pause between plays it plays the file once, then pauses for a while (wind, surf, rain on a tin roof)
  - **Unassigned** - Not played: where uploads land until you pick a mode
- **Groups** - A group is a folder inside the theme whose tracks take turns: only one plays at a time, never overlapping, with a random gap (in minutes) between them. Every track in an Intermittent group (the default) plays Intermittent: its whole file once, start to finish, with no fade. The group picks the next track by weight: each time a track plays it gets heavier and less likely to be picked, the weight wears off as other tracks play, and a track's interval sets how fast. The same track never plays twice in a row. Background and Ebb & Flow are for the ambience outside Intermittent groups. Deleting a group keeps its files.
- **Ebb & Flow groups** - A group in the Ebb & Flow section plays every file at once, each between its own minimum and maximum volume. With the group's Pause between plays on, the files pause at staggered times and at least one is always playing.
- **Merry-go-round groups** - A Background group is Merry-go-round, a continuous bed: one file plays, then crossfades into another picked at random, never the same one twice in a row. Each group sets its own crossfade (seconds). A single file crossfades into itself.
- **Files from another theme** - Link a file from another theme without copying it; it can sit in any section or group, and a preset turns it on.

#### Home Assistant Dashboard Integration
- **MQTT Entities** - Full dashboard control via MQTT (session select, theme/preset dropdowns, play/stop, volume)
- **Human-Readable Names** - Theme and preset dropdowns show names instead of UUIDs
- **Status Sensors** - See playback status and assigned speakers from your dashboard
- **Automation Support** - Use HA automations to trigger soundscapes (morning alarms, schedules, etc.)
- **Announcements** - Each channel has a notify entity (`notify.sonorium_<channel>_announce`) that speaks a message on that channel, and an Announcements switch (`switch.sonorium_<channel>_announcements`). See [Announcements: let other systems use Sonorium](#announcements-let-other-systems-use-sonorium)

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
| `web_ui_only_through_ha` | `false` | `true`: ports 8008 and 8808 serve only streams, linked phones and keys; the web UI is only in Home Assistant's sidebar |
| `bypass_login` | `false` | Only for forgotten logins: set to true to get in and reset the admin account, then set it back to false |

#### Sign-in

Home Assistant's users are Sonorium's users. Each person who signs in to Home Assistant
gets Sonorium with the role their Home Assistant account has:

| Home Assistant | Sonorium |
|---|---|
| Owner or Administrator | Admin: everything, including settings and users |
| User, Read-only | User: plays channels, playlists and sequences; sees themes and speakers |

- **HA's sidebar** opens Sonorium without a second sign-in, for every Home Assistant user.
- **Role changes** made in Home Assistant apply within a minute, on phones too.
- **Switching a user off or removing them** in Home Assistant signs them out of Sonorium,
  and their phones stop working.
- **Passwords** stay in Home Assistant. Sonorium has none to set for these users: Users &
  devices shows them with a **Home Assistant** label.

The direct ports are open on your network until you choose otherwise: in **Settings ›
Users & devices**, turn on **Require login**. From then on port 8008 (HTTP) and 8808
(HTTPS) need a sign-in with your Home Assistant username and password, a linked phone or
a key.

**The Sonorium app** links with **Link App** on the Channels page (a QR code; the phone
acts as you), or signs in with your Home Assistant username and password. It talks to port
8808 (HTTPS, self-signed; the app checks the certificate), never to HA's sidebar address.
Speakers keep using port 8008.

**Updating from an earlier version:** the accounts Sonorium made for Home Assistant users
now follow Home Assistant. Owners and administrators stay Admin; everyone else becomes a
User. A Sonorium password set on one of these accounts no longer works: use the Home
Assistant password.

**Forgotten password:** set `bypass_login` to `true` on the Configuration tab and restart.
Reset the account under **Settings › Users & devices**, then set it back to `false` and restart.

More: the [Sign-in and Users](https://github.com/synssins/sonorium/wiki/Sign-in-and-Users) wiki page and
[docs/AUTH.md](https://github.com/synssins/sonorium/blob/main/docs/AUTH.md).

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

### Announcements: let other systems use Sonorium

Home Assistant, Node-RED, a script or a doorbell can send Sonorium an announcement: a chime,
words to speak, or both. Sonorium plays it on the channels named. A playing channel gets
quieter while it plays and comes back after it, the way a radio presenter talks over the
music. A stopped channel has its speakers started for it and stopped again after it.

The other system decides when to announce and what to say; Sonorium only plays it, so there
is nothing to set up in Sonorium. Announcements play even on a channel with an owner or in
its quiet hours.

**Announcements on a channel.** Each channel has an **Announcements** switch in its editor,
on by default. Off, nothing announces on that channel, whichever way it is sent. Home
Assistant has it too, as `switch.sonorium_<channel>_announcements`, so an automation can
turn it off for a nap and on again after.

**What to send.** Only `channels` and `text` (or `url`) are needed:

| Field | | Example |
|---|---|---|
| `channels` | A channel's name or id, a list of them, or `"all"`. Names are not case-sensitive | `"Kitchen"`, `["Kitchen", "Office"]` |
| `text` | Words to speak, with Home Assistant's text to speech | `"Dinner is ready"` |
| `url` | A sound to play first (an http or https link to MP3, WAV, OGG, ...) | `"http://192.168.1.10/ding.mp3"` |
| `volume` | 0 to 200; 100 is the sound's own level | `80` |
| `duck` | How much quieter the channel gets, in dB; `false` to not duck. Left out: **Settings → Advanced** | `18` |

Text to speech comes from Home Assistant: its first `tts.*` engine, or the one a request
names in `provider`. Without Home Assistant (Docker on its own), send a `url`: the sound
plays and the answer says the text wasn't spoken.

**Switching announcements off.** Announcements are part of Sonorium and on by default. To
stop them everywhere at once, turn off **Announcements** in **Settings → Advanced**. It
works like a building's main switch next to the switch in each room: while it is off,
nothing announces on any channel, the `notify.sonorium_<channel>_announce` entities leave
Home Assistant, MQTT announcements are ignored and `POST /api/announce` answers 409. Each
channel's own Announcements switch keeps its setting. Turn it back on and everything
works again, without a restart.

#### From Home Assistant

Each channel has a notify entity, `notify.sonorium_<channel>_announce`:

```yaml
alias: Doorbell
triggers:
  - trigger: state
    entity_id: binary_sensor.doorbell
    to: "on"
actions:
  - action: notify.send_message
    target:
      entity_id: notify.sonorium_kitchen_announce
    data:
      message: Someone is at the door
```

For a chime first, send the message as JSON:

```yaml
    data:
      message: '{"text": "Someone is at the door", "url": "http://192.168.1.10/ding.mp3"}'
```

Several channels at once, through MQTT:

```yaml
action: mqtt.publish
data:
  topic: sonorium/announce/set
  payload: '{"channels": ["Kitchen", "Office"], "text": "Dinner is ready"}'
```

#### With a key (scripts, curl, other programs)

1. **Settings → Keys → New key**.
2. Name it and check **Make announcements**.
3. **Make key**, then copy the key. It is shown once.

A key limited to some channels announces only on those.

```bash
curl -X POST http://sonorium.local:8008/api/announce \
  -H "Authorization: Bearer sk_your_key" \
  -H "Content-Type: application/json" \
  -d '{"channels": "Kitchen", "text": "Dinner is ready"}'
```

Use Sonorium's address in place of `sonorium.local` when that name doesn't work on your
network. The answer lists the channels it plays on (`played`), the channels with
Announcements off (`skipped`), and a `notice` when only the chime played. An unknown channel
answers 404; text with no text to speech and no `url` answers 503.

#### From MQTT

On the broker Sonorium uses (**Settings → Connection**), publish to `sonorium/announce/set`:

```bash
mosquitto_pub -h 192.168.1.5 -u mqtt_user -P mqtt_password -t sonorium/announce/set \
  -m '{"channels": "all", "url": "http://192.168.1.10/ding.mp3", "text": "The washer is done"}'
```

A message without `channels` is ignored, so a mistake doesn't wake every room.

#### From Node-RED

A **function** node, then an **http request** node:

```javascript
msg.headers = {
    "Authorization": "Bearer sk_your_key",
    "Content-Type": "application/json"
};
msg.payload = { channels: "Kitchen", text: "Dinner is ready" };
return msg;
```

In the **http request** node: **Method** `POST`, **URL**
`http://sonorium.local:8008/api/announce`, **Return** `a parsed JSON object`.
Node-RED with an MQTT broker can publish to `sonorium/announce/set` with an **mqtt out**
node instead, without a key.

### Troubleshooting

**Which version am I on?** The version and the install type (Home Assistant app or Docker) are at the bottom left of the menu. On a phone, open the menu from the top bar to see them. Include both when you report a problem.

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

### Next release

**Sign-in**
- **Accounts and roles.** Sonorium has accounts with two roles. An **Admin** can change everything; a **User** can play and change channels, run and edit playlists and sequences, and see themes and speakers. Sonorium always keeps one Admin.
- **New installs** ask for an admin account on the first visit and need a sign-in from then on. **Updates** change nothing until you create the account and turn on **Require login**; the banner at the top offers both.
- **Users & devices.** **Settings → Users & devices** lists each person with the browsers and phones they use. Add users, change roles, reset passwords, and sign out one browser or every device.
- **Keys.** **Settings → Keys**: keys for devices and programs, each allowed only the items checked on it, for some channels or all, with an expiry. A key is shown once. Keys are part of Sonorium; turning off **Keys** in **Settings → Advanced** stops every key at once and hides the page, and turning it on again brings them back.
- **Link App.** The button on the **Channels** page shows a QR code that links a phone to your account. Each code works once, for 5 minutes.
- **HTTPS.** Port 8808 serves the web UI and the API over HTTPS, with a certificate Sonorium makes itself. Port 8008 is unchanged; speakers keep using it.
- **sonorium.local.** The Docker version announces itself on your network: `http://sonorium.local:8008`.
- **Home Assistant's users** (Home Assistant app). Everyone who signs in to Home Assistant gets Sonorium with the role their Home Assistant account has: owners and administrators are Admins, everyone else is a User. Phones and the app's sign-in use the Home Assistant username and password; switching a user off in Home Assistant switches them off in Sonorium.
- **Forgotten password.** `BYPASS_LOGIN=true` (Docker) or `bypass_login` (Home Assistant app) opens the web UI without a sign-in, to reset the account.

**Announcements**
- **Other systems announce through Sonorium.** Home Assistant (each channel's notify entity), MQTT (`sonorium/announce/set`), anything that can make a web request (`POST /api/announce` with a key) and plugins send a chime and/or words to speak; Sonorium plays them on the channels named. See [Announcements: let other systems use Sonorium](#announcements-let-other-systems-use-sonorium).
- **Announcements switch** on each channel, and in Home Assistant: off, nothing announces on that channel.
- **Announcements in Settings → Advanced** switches announcements off (or on) everywhere at once.

**Plugins**
- **Built-in plugins can be uninstalled** like any other. An uninstalled built-in plugin stays uninstalled after restarts and updates.
- **Plugin settings** show under each plugin's form on **Settings → Plugins**, and are saved as you change them.
- **Theme Merge 2.0** merges two themes into a new theme or into either one, with their tracks, groups, linked files and presets.
- **Ambient Mixer Importer 3.1** imports mixes with the current playback modes and gapless loops. Its random sounds follow **Wait between plays** on **Settings → Advanced**.
- **For plugin authors:** events, an actions API for channels, a scheduler, webhook triggers, announcements, menu pages and Connections sections. See [docs/PLUGIN_DEVELOPMENT.md](https://github.com/synssins/sonorium/blob/main/docs/PLUGIN_DEVELOPMENT.md).

**Settings → Advanced**
- **Playback values.** The values every theme's playback is built on, until now fixed: **Wait between plays**, **Wait randomness**, **Gap after a group's track**, **Loop crossfade**, **Merry-go-round crossfade range**, **Track fade**, **Switch fade**, **Long pause chance**, **Turn chance** and **Short file threshold**. Each is saved as you change it, playing channels follow it, and the arrow on its row puts the default back. **Advanced** is now always in the menu.

**Home Assistant dashboard cards**
- **Sonorium's cards** are added to Home Assistant automatically when Home Assistant and MQTT are linked. Search for **Sonorium** in the card picker.
- **Four layouts:** **Tile**, **Channel**, **Player** and **All channels**, changed in the card's editor along with what each one shows.
- **Channel popup.** Clicking a channel's name opens the channel as it looks on Sonorium's Channels page, with its recent changes.
- When the cards can't be added automatically, **Settings → Connection** says why and offers the file to download (Docker).

### 1.6.1

**Channels**
- **Theme or playlist.** The two buttons to the right of a channel's fields choose whether it plays one theme or a playlist.
- **Speaker mute.** The speaker button next to a channel's volume silences its speakers. It works on every kind of speaker. Speakers can take a few seconds to go quiet.
- **Listen in your browser.** The headphones button plays the channel on the device you're using, with its own volume, even while the speakers are stopped. Start listening on headphones, add speakers, then press Play: the speakers join the same stream without a break. Stop stops the speakers only.

**Theme Editor**
- **Sections.** Tracks are listed by how they play: **Unassigned**, **Background**, **Intermittent** and **Ebb & Flow**. Changing a track's Mode, or dragging it to another section, moves it there.
- **Unassigned.** Uploads and files added from another theme start here, and don't play until you give them a Mode.
- **Groups belong to a section.** A Background group is a Merry-go-round, an Intermittent group takes turns, an Ebb & Flow group plays every file at once.
- **Files from another theme** can go in any section or group. Intrusion groups are retired.
- **Auto is retired.** Each Auto track keeps playing the way it did.

**Sequences and playlists**
- **Sequences.** A sequence plays some of a theme's presets in the order you choose, each for a random time in a range, gliding from one to the next. At the end it plays the list backwards, starts again or stops. For example, a mild summer storm that becomes a heavy one. Make them from **Sequences** in the Theme Editor; pick them from a channel's Preset menu. In Home Assistant they are options of the channel's Preset select, as "*name* (sequence)".
- **While playing.** In a sequence, a preset's ⋯ › Advanced… has a **While playing** tab: the Theme Editor's controls, for that step only. Anything you don't change follows the preset.
- **Advanced transitions.** The same window sets how each track comes in and leaves at a step: Gusts, Swell, Build, Distance or Start fresh (and Gusts dying down, Smooth fade, Thin out, Recede or Finish then stop), in any combination, starting up to 90 seconds before the step.
- **Playlists.** The new **Playlists** page holds themes that play one after another on a channel, each with a preset or sequence if you want one, in order or shuffled, for a random time in the playlist's range, once or on repeat. Home Assistant gets a Playlist select, a Next button and a Step sensor for each channel.
- **Theme cycling became playlists.** A channel's theme cycling is turned into a playlist the first time this version starts.

**Playback**
- **Ebb & Flow.** Tracks can be configured with a minimum and maximum volume, and will automatically move to any value between those two limits randomly for a period of time. This interval is controlled by the Duration and Rise sliders. Duration increases or decreases the amount of time the track is at a value, while the Rise increases or decreases the speed at which the track reaches that value.
- **Pause between plays.** An Ebb & Flow track can play its file once, fade out at the end and stay quiet for a random time before it comes back. In an Ebb & Flow group, this keeps at least one file playing at all times.
- **File fades.** **Fade in** and **Fade out** in a track's ⋯ menu soften a file that starts or ends abruptly, every time it starts or ends. Leave them empty for sounds that should hit hard, like a thunder crack.
- **Gaps in presets.** An Intermittent group's **Gap** is saved in presets, so one preset can bring the thunder more often than another.
- **Channels fade in and out** when they start and stop, instead of cutting (#34).
- **Preset glides.** Picking a preset glides the mix to it over 3 seconds; a sequence glides over its ramp time.
- **Smooth volume changes.** Volume sliders ease to the new level instead of jumping.
- An exported theme keeps all of its settings, presets and sequences.

**Fixes**
- Saving a preset now changes channels that are playing it.
- **Crossfade** and **Master output gain** on **Settings → Audio Settings** now change the sound in the Home Assistant app. Crossfade also sets how long a channel fades in and out.
- After a confirmation on a settings page, an empty box could stay on the screen.

### 1.6.0

**Themes and playback**
- **Group modes.** A group's Mode is now **Intermittent** (one track at a time with a gap, as before), **Merry-go-round** or **Intrusion**. Pick it in the group's row in the Theme Editor.
- **Merry-go-round.** One file plays, then crossfades into another picked at random, never the same one twice in a row. The group's **Crossfade** sets the overlap. A single file crossfades into itself, which also fixes recordings that fade in and out on their own.
- **Intrusions.** An Intrusion group's tracks keep their own mode and settings. It can hold files **linked** from other themes: **⋯ › Add from another theme** opens a picker where you preview a theme's files and tick the ones you want. Linked files play from their own theme's folder, start muted, and are turned on by presets. A linked file whose source is gone shows **Missing**.
- **Presets show what they do.** ★ marks the default preset; ⚭ marks a preset that turns intrusion tracks on. Both show in the Theme Editor and on the channel cards, and in the channel's Edit dialog.
- **Smooth preset changes.** A track that a preset (or the mute button) turns on or off fades in or out over 3 seconds instead of cutting.
- **Moving linked files.** Dragging a linked file to a normal group or out to the theme asks first, then copies the file into this theme. Exports copy linked files in, so a shared theme is complete.

**Theme Editor**
- **Instant moves.** Moving a track, and renaming or deleting a group, show at once. The list no longer greys out for a few seconds.
- **Track preview** plays at the track's own volume, follows its Volume slider as you drag, and works for muted and linked tracks.
- **Messages** show as small notices at the top right instead of in the footer.
- **Group header.** Gap or Crossfade sits under the group's name.

**Themes**
- **New themes** can be edited, saved and given presets straight away, and keep the name you typed.

**Fixes**
- A new theme said "Theme not found" in the editor until Sonorium restarted.
- Preview mix showed "Could not play the preview" when it restarted after a change, while still playing.
- Dragging a track selected text across the list.

**Known issue**
- In the Home Assistant app, **Crossfade** and **Master output gain** on **Settings → Audio Settings** are saved but don't change the sound yet.

### 1.5.2

- **Clearer help** for Intermittent in groups and for track weight.
- **Version and install type** are shown at the bottom left of the menu; the README says where to find them.
- **Releases** no longer skip their version number after a merge.

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
- **Remove a connection without a restart.** The trash button on **Settings → Connection** removes Home Assistant, and its floors, areas and speakers disappear from every list at once. Removing MQTT (or saving it without a broker) also removes Sonorium's entities from Home Assistant, the Announce notify entities and plugins' entities included.
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

### How a theme plays

These are the same words as the **?** help in the Theme Editor.

A theme is a set of tracks (sound files) mixed together. This help follows the editor from top to bottom.


**Tracks**

- **Sequences:** Lists this theme's sequences. Pick one to edit it, or Add sequence.
- **Preview mix:** Plays the mix in this browser.
- **Upload:** Uploads to Unassigned, or to the group you pick.
- **Add from another theme:** Links files from another theme.
- **Reset all:** Resets every track and group to defaults.

**Sections**

- **Sections:** tracks sit in sections by how they play: Background, Intermittent and Ebb & Flow. Every section is always there, even empty. Change a track's Mode, or drag it to another section or into a group in one, and it moves there and plays that way. A track in a group plays its group's way: drag it out to change it.
- **Groups:** a group belongs to its section and plays that section's way: Background groups are Merry-go-round, Intermittent groups take turns, Ebb & Flow groups play every file at once. Make one with the section's New group.
- **Moving tracks:** drag a track onto a group, or use its ⋯ menu.

**Unassigned**

- **Unassigned:** uploads and files added from another theme start here. Nothing here plays. Pick each file's Mode, or drag it into a section or a group.

**Background**

- **Background:** plays all the time, looping smoothly. Rain, wind, a fire.
- ***Track***
  - **Gapless:** in the track's ⋯ menu, outside groups. Loops without a crossfade.
- ***Merry-go-round group***
  - **Merry-go-round:** one file plays, then crossfades into another picked at random, never the same one twice in a row. One file crossfades into itself. Use 3 or more files of the same scene for a bed that never repeats the same way.
  - **Crossfade:** under the group's name. Overlap between files (seconds).
  - **Volume, Mute:** apply to every track in the group.

**Intermittent**

- **Intermittent:** plays once, then goes quiet before playing again. In a group, the group decides when it plays. Bird calls, a thunder crack, a door.
- ***Track***
  - **Interval:** the wait between plays. In a group, how fast its weight wears off. Higher comes back sooner; lower plays less often.
- ***Intermittent group***
  - **Take turns:** only one track plays at a time, never overlapping. On its turn, a track plays its whole file once, start to finish, then hands over. Use it for sounds that shouldn't pile up, like thunder cracks or songs from one musician.
  - **Gap:** under the group's name: after a track finishes, the group waits a random time in this range (minutes) before the next one starts. Different groups don't wait for each other. Presets keep their own Gap.
  - **Weight:** each time a track plays, it gets heavier and is less likely to be picked next. The weight wears off as other tracks play. The same track never plays twice in a row.
  - **Volume, Interval, Mute:** apply to every track in the group.

**Ebb & Flow**

- **Ebb & Flow:** Tracks can be configured with a minimum and maximum volume, and will automatically move to any value between those two limits randomly for a period of time. Wind, surf, rain that gusts.
- ***Track***
  - **Volume:** minimum and maximum volume.
  - **Duration:** increases or decreases the amount of time the track is at a value.
  - *Its ⋯ menu*
    - **Rise:** increases or decreases the speed at which the track reaches that value.
    - **Pause between plays:** off, the file loops and is always heard. On, it plays once from start to finish, fades out at its end, then pauses before it comes back. Rain on a tin roof that comes and goes.
    - **Pause:** how long it stays quiet, in seconds, picked at random between the two numbers each time (600 is 10 minutes). It also staggers when tracks first start, so they don't all begin together.
- ***Ebb & Flow group***
  - **Ebb & Flow group:** every file plays at once, each between its own minimum and maximum volume. Each file keeps its own settings; the group's Volume and Duration scale them down.
  - **Pause between plays:** under the group's name. On, every file pauses between plays, at staggered times, so at least one is always playing. Off, each file follows its own Pause between plays.
  - **Volume, Duration, Mute:** apply to every track in the group.
  - *Its ⋯ menu*
    - **Same Rise for every file:** one Rise for the whole group instead of each file's own.

**A track's ⋯ menu**

- **Fade in / Fade out:** for a file that starts or ends abruptly. Set how many seconds it fades in each time it starts, and fades out each time it ends, including each loop. Empty is off. Sequences and advanced transitions use them too.
- **Move to:** Unassigned, or a group.
- **New group:** Creates a group in this section with the track in it.
- **Reset track:** Resets the track to defaults.

**A group's ⋯ menu**

- **Rename:** also by double-clicking the group name.
- **Delete group:** keeps its files; they move back into its section.

**Files from another theme**

- **Linked file:** plays here but stays in its own theme. Add one with Add from another theme above the list; it starts in Unassigned, and can go anywhere your own files can. If the file is gone, it shows Missing.
- **Presets:** turn linked files on and other tracks off, like rain that mutes the crickets. A preset that turns a linked file on shows ⚭.
- **Remove link:** in a linked file's ⋯ menu. The file stays in its own theme.

**Sequences**

- **Sequence:** plays presets of this theme in the order you choose, gliding from one to the next. Marked with the sequence icon (square, triangle, circle).
- **Picking one:** preset lists show the sequences after the presets. Picking a preset by hand stops a running sequence.
- **Edit:** pick a sequence in the preset field below, then Edit.
- ***Edit sequence***
  - **Time on each preset:** each preset plays for a random time in this range (minutes), drawn fresh each time.
  - **Ramp:** how long the glide to the next preset takes (seconds).
  - **At the end:** Reverse plays the list backwards, Start again goes back to the first preset, Stop stays on the last one.
  - **Order:** drag a preset, or use its ⋯ menu.
  - **Theme defaults:** empty fields use the theme's time and ramp, set at the bottom of the Sequences menu.
  - **Save:** saves the sequence at once. Every channel playing this theme can pick it.
- ***A step: ⋯ › Advanced***
  - **While playing:** changes how tracks play while this step plays, for this sequence only. The same controls as the Theme Editor. A value you change shows in green; anything you don't change follows the preset.
  - **Revert changes:** puts back the settings as they were when Save was last pressed.
  - **Coming in / Leaving:** tracks this step turns on or makes louder, and tracks the next step turns off or makes quieter.
  - *Coming in and Leaving*
    - **Smooth:** no button on. The track follows the sequence's ramp.
    - **Buttons:** click to turn a style on, click again to turn it off. Each style that's on shows its own setting under Setting.
    - **Pre-fade:** starts the change this many seconds before the step.
    - **Gusts / Gusts dying down:** rises or falls in uneven surges. Intensity sets how rough. Max volume caps the peaks at the preset volume.
    - **Swell / Smooth fade:** one curved fade. Speed sets how much of the fade time (pre-fade plus ramp) it uses.
    - **Build / Thin out:** Intermittent tracks play more often and louder over the fade, or less often and quieter. Amount sets how much.
    - **Distance / Recede:** starts faint and muffled, like far-off thunder, and ends clear at the preset volume; or drifts away. How far sets the distance.
    - **Start fresh / Finish then stop:** plays from the start of the file with a short fade, for music, voices and rhythm; or lets the current play finish, then stops.
    - **Preview:** ▶ plays that track's change on this device and follows your edits.
    - **File fades:** a file's own Fade in and Fade out apply on top of every style.
    - **Combining styles:** several styles can be on together. They all work over the same fade time (pre-fade plus ramp). For example:
      - **Gusts + Distance:** wind starts far off and muffled, and surges unevenly as it comes closer.
      - **Gusts + Swell:** the surges ride on one curved rise instead of a steady one.
      - **Gusts + Start fresh:** the file plays from its start and rises in uneven surges.
      - **Build + Distance:** thunder comes more often and louder as it moves from far off to close.
      - **Thin out + Recede:** thunder comes less often and quieter as it drifts away.
      - **Smooth fade + Finish then stop:** fades out over the fade time; if the current play ends first, it stops there with a short fade.

**Presets and saving**

- **Live:** changes are heard at once with Preview mix, and on channels playing this theme with no preset. A channel playing a preset hears them after Save Preset.
- **Preset:** the loaded preset. Current settings is the theme without one.
- **New:** makes a new preset from the current mix.
- **Save Preset:** Saves loaded preset
- **Save Theme:** Saves theme with all changes (presets, name, etc) Required after saving a preset.
- **Cancel:** Reverts unsaved changes.

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
- `POST /api/sessions/{id}/next` - Skip to the sequence's next step or the playlist's next entry
- `PUT /api/sessions/{id}/speakers-muted` - Mute or unmute the speakers (`{"muted": true}`)
- `POST /api/sessions/{id}/listen` / `DELETE` - Start or stop listening in a browser (the stream is `stream_path` with `?local=1`)

#### Themes
- `GET /api/themes` - List all themes
- `POST /api/themes/create` - Create a new theme
- `POST /api/themes/{id}/upload` - Upload audio file
- `GET /api/themes/{id}/sequences` - A theme's sequences

#### Playlists
- `GET /api/playlists` - List playlists
- `POST /api/playlists` - Create a playlist

#### Status
- `GET /api/status` - Current system status

## Acknowledgements

Sonorium was originally based on [Amniotic](https://github.com/fmtr/amniotic) by [fmtr](https://github.com/fmtr). Sonorium has since been fully rewritten, and none of Amniotic's code remains.

## License

Sonorium is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE.md): free to use, change and share for noncommercial purposes. Commercial use needs a separate license. Copyright 2026 Chris Kliewer. Third-party components keep their own licenses: see [THIRD_PARTY_NOTICES.md](https://github.com/synssins/sonorium/blob/main/THIRD_PARTY_NOTICES.md).

The components Sonorium is built on, and their licences, are listed in [THIRD_PARTY_NOTICES.md](https://github.com/synssins/sonorium/blob/main/THIRD_PARTY_NOTICES.md).

## Contributing

Contributions are welcome! Please open an issue to discuss changes before submitting a PR.
