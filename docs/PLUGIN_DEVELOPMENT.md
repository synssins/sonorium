# Writing a Sonorium plugin

A plugin adds a feature to Sonorium without changing Sonorium: a connector to another home
system, a scheduler, a chat bot, an importer. It is a folder with a Python file. Sonorium
loads it, and through a set of services the plugin can follow what happens (events), act on
channels (the actions API), run at set times (the scheduler), be called from other devices
(webhook triggers), add pages and settings to the web UI (extension
points), play sounds over channels and ramp their volume, add audio outputs, take commands
from remotes, use MQTT and Home Assistant, and bring the packages and programs it needs.

Think of Sonorium as a phone and a plugin as an app: the app doesn't open the phone and
rewire it, it asks the phone's services. Those services are documented here and keep working
across Sonorium versions: names and fields are only ever added, never renamed or removed.

A complete example is in [`docs/examples/trigger_play_channel`](examples/trigger_play_channel/plugin.py):
a webhook trigger that plays a channel. The tests load it and run it end to end.

In the API a channel is a **session** (`/api/sessions/{id}`); in the actions API and in
events it is a channel.

## 1. Layout

```
trigger_play_channel/
├── manifest.json      what the plugin is (generated from the class if missing)
└── plugin.py          the plugin class
```

Installed plugins live in `/config/sonorium/plugins/` (kept across updates).

`manifest.json`:

| Key | Meaning |
|---|---|
| `id` | Unique id: letters, digits, `_` and `-`. Used in URLs, settings and event names. |
| `name`, `description`, `author` | Shown in Settings > Plugins. |
| `version` | Semantic version (`1.0.0`, `2.1.0-beta`). Required. |
| `entry_point` | The Python file (default `plugin.py`). |
| `plugin_class` | The class name (default: the first `BasePlugin` subclass in the file). |
| `settings_schema` | Settings fields (section 3), if the class doesn't give them. |
| `triggers`, `pages`, `connections` | Declarations (sections 7, 9, 10), if the class doesn't give them. |
| `attributes` | Attributes the plugin keeps on Sonorium's objects (section 23), if the class doesn't give them. |
| `ui` | What it adds to the screens (section 24), if the class doesn't give it. |
| `short_name`, `icon` | The plugin badge: a short name (up to 16 characters; default the name) and an icon name or small SVG. |

`plugin.py`:

```python
from sonorium.plugins.base import BasePlugin


class TriggerPlayChannel(BasePlugin):
    id = "trigger_play_channel"
    name = "Trigger Plays a Channel"
    version = "1.0.0"
    description = "Webhook triggers that play or stop a channel."
    author = "Sonorium"
```

Import only from `sonorium.plugins.*` (base, actions, events, scheduler) and, for an output
type, `sonorium.core.audio_outputs` (`OutputType`, `Sink`). Everything else in Sonorium is
internal and changes without notice.

More manifest keys (sections 15 to 22):

| Key | Meaning |
|---|---|
| `api_version` | The plugin API it was written for, e.g. `"1.2"` (none: `"1.0"`) |
| `requires` | Capabilities it can't work without, e.g. `["audio.sounds", "mqtt"]` |
| `requirements` | Python packages from PyPI, e.g. `["pyserial==3.5"]` |
| `binaries` | Programs it ships, per platform, with their SHA-256 |
| `command_maps` | Declarations, if the class doesn't give them |
| `device_kinds` | Kinds of device it brings (section 26), if the class doesn't give them |
| `permissions` | What it uses (section 28); none: everything, once the user allows it |
| `http`, `ha_entities` | Its own routes (section 29) and Home Assistant entities (section 30), if the class doesn't give them |
| `automation` | Its "do" actions and "only if" conditions (section 31), if the class doesn't give them |

## 1a. API versions and capabilities

The plugin API has a version, MAJOR.MINOR (now **1.2**). Adding a service or a field raises
the minor version; a plugin written for 1.0 keeps running on 1.1 and every later 1.x,
because nothing it uses is renamed or removed. The major version goes up only for a change
old plugins would notice; Sonorium then keeps the old facades next to the new ones for as
long as it can.

Sonorium reads `api_version` and `requires` before it loads any of the plugin's code. A
plugin that needs a newer API, another major version, or a capability this install doesn't
offer is refused with a sentence that says why, for example:

> Remote needs plugin API 1.2; this Sonorium has 1.1. Update Sonorium to use it.

A refused plugin is listed in Settings › Plugins with that sentence (`compatible: false`,
`problem` in `GET /api/plugins`), the web UI shows an alert, and none of its code runs.

| Capability | Since | What it is |
|---|---|---|
| `events`, `actions`, `scheduler`, `triggers`, `pages`, `connections` | 1.0 | Sections 4 to 10 |
| `audio.sounds` | 1.1 | Sounds over channels, announcements (section 15) |
| `audio.ramps` | 1.1 | Volume ramps (section 16) |
| `audio.outputs` | 1.1 | Output types (section 17). In 1.2 their devices are speakers, and `local` |
| `audio.tts` | 1.1 | Text to speech providers (section 15) |
| `commands` | 1.1 | Command maps (section 18) |
| `mqtt` | 1.1 | MQTT (section 19) |
| `homeassistant` | 1.1 | Home Assistant's API (section 20) |
| `packages.pip`, `packages.binaries` | 1.1 | Packages and programs (section 21) |
| `attributes` | 1.2 | Declared attributes on every object a screen shows: themes and their tracks, files, groups, presets, sequences and steps; channels, playlists and their entries, speakers, speaker groups, areas, users, keys, connections and plugins (section 23) |
| `ui` | 1.2 | Fields, buttons, menu items, badges, columns and pages on any screen (section 24) |
| `timeline` | 1.2 | Cues: which sound is heard on which speaker and when, ahead of time (section 25) |
| `audio.tap` | 1.2 | Read-only copies of what a channel plays, or of one track (section 27) |
| `audio.decode` | 1.2 | A theme's files decoded by Sonorium, for analysis (section 27) |
| `automation` | 1.2 | "Do" actions and "only if" conditions for command maps (section 31) |
| `permissions` | 1.2 | What a plugin uses, approved by the user (section 28) |
| `http` | 1.2 | Its own HTTP routes, an ability each (section 29) |
| `ha.entities` | 1.2 | Home Assistant entities declared as data (section 30) |
| `devices` | 1.2 | Devices of any kind (lights, displays, buttons): listed, named, placed and assigned by Sonorium (section 26) |

`GET /api/plugin-framework` gives the version, every capability and the ones this install
offers now.

Each 1.1 service is a facade on the plugin: `self.audio`, `self.ramps`, `self.audio_outputs`,
`self.commands`, `self.mqtt`, `self.homeassistant`; 1.2 adds `self.timeline` and `self.devices`. Every method that can fail raises
`ActionError` (`code` `invalid`, `not_found`, `conflict`, `unavailable`, or `forbidden` for
something the plugin's permissions don't cover, section 28), as `self.actions`
does.

## 2. Lifecycle

| Method | When |
|---|---|
| `async on_load()` | The plugin was loaded (Sonorium starts, or plugins were reloaded). |
| `async on_enable()` | The user enabled it (also at start-up if it was enabled). Start things here. |
| `async on_disable()` | The user disabled it. Stop what you started. |
| `async on_unload()` | Sonorium is unloading it. |

When a plugin is disabled, Sonorium removes its event subscriptions, MQTT
subscriptions, output types (closing their sinks), text to speech providers and sound gates.
When it is uninstalled, its scheduled jobs, trigger tokens, saved connection values, command
maps, Python packages and its output types' devices go too. A plugin that fails in
`on_enable` stays disabled, and what it registered before failing (subscriptions, output
types, text to speech providers) is removed.

Everything a plugin does runs on Sonorium's event loop: use `async` code, and run slow
blocking work with `await asyncio.to_thread(...)` so playback controls stay responsive.

## 3. Settings

Settings are values the user sets once, shown in the plugin's settings form. Declare them
with `get_settings_schema()` (or `settings_schema` in the manifest):

```python
def get_settings_schema(self):
    return {
        "channel": {"type": "string", "label": "Channel", "default": ""},
        "volume": {"type": "number", "label": "Volume", "min": 0, "max": 100, "default": 40},
    }
```

| Type | Value | Extra keys |
|---|---|---|
| `string` | text | `placeholder` |
| `url` | empty or an http(s) URL | |
| `number` | int or float | `min`, `max` |
| `boolean` | true or false | |
| `select` | one of the options | `options`: `[{"value", "label"}]` or plain values |

Every field may have `label`, `help`, `default` and `required`. Read values with
`self.get_setting("volume")`; `self.settings` always holds the defaults of settings not set.
`async on_settings_changed(settings)` runs after the user saves them.

Pages and connections (sections 9 and 10) use the same fields plus `textarea`, `time`
(`HH:MM`), `date` (`YYYY-MM-DD`), `datetime` (ISO 8601), `multiselect` (a list of option
values) and, in connections only, `secret`.

## 4. Events

Sonorium announces what happens as **events**: a type, a time, an origin and data. A plugin
subscribes to the types it cares about, like following a few channels on a news feed.

```python
async def on_enable(self):
    self.subscribe("channel.started", self.on_started)        # one type
    self.subscribe(["speaker.*", "error"], self.on_trouble)   # a prefix, or several

async def on_started(self, event):
    # event.type, event.data, event.time (ISO, UTC), event.origin, event.id
    print(event.data["name"], "started")
```

- A pattern is a type (`channel.started`), a prefix (`channel.*`) or `*` (everything).
- Each subscriber has its own queue. A slow handler only falls behind (after 256 waiting
  events the oldest are dropped); a handler that raises or takes longer than 30 s is logged
  and reported as an `error` event. Neither ever holds up playback or other plugins.
- An `async` handler runs on the event loop; a plain function runs in a worker thread.
- `event.origin` says who caused it: `sonorium` (the UI, the API, Home Assistant, timers)
  or `plugin:<id>` (a plugin, through the actions API). Check it to ignore your own changes.

A plugin publishes its own events with `self.emit(name, data)`. They are named
`plugin.<plugin_id>.<name>` (name: lowercase letters, digits, `_`, `-`, `.`), and other
plugins can subscribe to them.

### Event catalogue (version 1)

All events of a channel have `channel_id` and `name` (the channel's name).

| Type | When | Data |
|---|---|---|
| `sonorium.started` | Sonorium finished starting; plugins are enabled | `version` |
| `channel.created` | A channel was added | `channel_id`, `name`, `theme_id` |
| `channel.deleted` | A channel was deleted | `channel_id`, `name` |
| `channel.started` | A channel started playing on its speakers | `theme_id`, `preset_id`, `volume`, `speakers` |
| `channel.stopped` | A channel stopped | `reason`: `stopped`, `paused`, `idle` (no speaker pulled audio for 90 s), `ended` (a playlist without repeat finished) or `schedule` (Kid mode's hours ended) |
| `channel.changed` | A channel's settings changed | `changes`: only what changed, any of `theme_id`, `preset_id`, `sequence_id`, `playlist_id`, `volume`, `speakers_muted`, `name`, `speakers` |
| `sequence.step` | A sequence moved to a step (also step 1 when it starts) | `theme_id`, `sequence_id`, `step`, `steps`, `preset_id` |
| `playlist.entry` | A playlist moved to an entry | `playlist_id`, `position`, `entries`, `theme_id` |
| `zone.switched` | Zones were switched on or off (Settings › Advanced) | `on` |
| `zone.changed` | A channel's owner, Kid mode or hours changed | `channel_id`, `name`, `changes`: any of `owner`, `kid_mode`, `schedule` |
| `zone.speaker_taken` | A channel took a speaker another one was playing on (its home, or an admin) | `channel_id`, `name`, `speaker_id`, `from_channel_id`, `by`: `home` or `admin` |
| `zone.home_changed` | A speaker has a new home channel | `speaker_id`, `channel_id`, `name`, `reason`: `deleted`, `removed` or `admin` |
| `zone.schedule_ended` | Kid mode's hours ended: what the user started fades out | `channel_id`, `name` |
| `link.created` / `link.removed` | An admin linked channels, or unlinked them | `link_id`, `name` (created: also `channels`, `theme_id`, `preset_id`, `playlist_id`) |
| `link.changed` | A link's name, channels or what it plays changed | `link_id`, `name`, `changes` |
| `link.left` / `link.rejoined` | A channel left a link, or joined it again | `link_id`, `name`, `channel_id` (rejoined: also `by`: `owner` or `admin`) |
| `speaker.added` | A speaker appeared | `speaker_id`, `name`, `type` |
| `speaker.removed` | A speaker is no longer known | `speaker_id`, `name` |
| `speaker.unavailable` | A speaker went offline | `speaker_id`, `name` |
| `speaker.available` | An offline speaker is back | `speaker_id`, `name` |
| `theme.created` | A theme was created, uploaded or imported | `theme_id`, `folder` |
| `theme.changed` | A theme's files changed (several saves within a second are one event) | `theme_id`, `folder` |
| `theme.deleted` | A theme was deleted | `theme_id` |
| `plugin.enabled` / `plugin.disabled` | A plugin was enabled or disabled | `plugin_id`, `name` |
| `attribute.changed` | A plugin's attributes on an object changed (section 23) | `kind`, `id`, `theme_id` (theme objects), `plugin_id`, `keys` (null: all removed) |
| `ui.value` | A plugin sent a live value for one of its screen elements (section 24) | `plugin_id`, `element`, `ref` (`*` or `kind\|theme\|id`), `value` (null: removed) |
| `ui.changed` | A plugin registered its screen elements again (`self.ui.register()`) | `plugin_id` |
| `schedule.fired` | A scheduled job ran | `plugin_id`, `job_id`, `name`, `data`, `scheduled_for`, `missed` |
| `trigger.received` | A webhook trigger was called (and authenticated) | `plugin_id`, `trigger`, `payload`, `method` |
| `error` | Something failed that nobody was waiting for | `where` (`event_handler`, `schedule`, `trigger`, `channel.start`, a plugin method), `message`, `plugin_id` |

| `sound.started` / `sound.ended` | A sound started or ended over a channel (section 15) | `sound_id`, `label`, `origin`; ended: `reason` (`finished`, `replaced`, `cancelled`, `stopped`, `error`), `capped`, `played` |
| `ramp.started` / `ramp.finished` / `ramp.cancelled` | A channel's volume ramp (section 16) | `ramp_id`, `from`, `to`, `seconds`, `curve`, `then`; `level`; cancelled: `reason` |
| `command.received` | An input plugin handed over a code (section 18) | `plugin_id`, `map_id`, `code`, `actions`, `learned` |
| `device.added` / `device.removed` | A plugin listed a new device; a device was forgotten (section 26) | `device_id`, `kind`, `plugin_id` (added: also `name`) |
| `device.changed` / `device.assigned` | A device's name, area or Use changed; what it follows changed | `device_id`; `changes`; `channels`, `areas` |
| `device.available` / `device.unavailable` | A device came online or went offline | `device_id` |

`GET /api/events/catalogue` returns the same list.

### The event stream (outside programs)

Programs outside Sonorium (a Node-RED flow, a dashboard, a bot) can follow events as
[Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events):

```
POST /api/events/token          -> {"token": "..."}   (shown once; a new one replaces it)
GET  /api/events/stream?types=channel.*,error
     Authorization: Bearer <token>        (or X-Sonorium-Token, or ?token=)
```

A key from **Settings › Keys** with **Read events** works in place of the token, and so does
a linked phone or a signed-in browser (docs/AUTH.md).

Each event arrives as `id: <boot_id>:<n>`, `event: <type>`, `data: <JSON>`. `boot_id` is new
each time Sonorium starts (and `n` starts again at 1); `GET /api/info` has it too. A comment
line is sent every 15 s to keep the connection open. After a reconnect, the `Last-Event-ID`
header (or `?last_event_id=`) replays the events since then that Sonorium still holds (the
last 200). An id from before a restart replays all the events of the new run it still holds;
a bare `<n>` (older clients) is read as an id of the current run.
`?max_events=N` closes the stream after N events.

```sh
curl -N -H "Authorization: Bearer $TOKEN" "http://sonorium.local:8008/api/events/stream?types=channel.*"
```

`sonorium.local` is the name Docker (host networking) and the native installs announce on
mDNS (`sonorium-2.local` and so on when another device has it; the log says which). The Home
Assistant app doesn't announce it: use the Home Assistant machine's address there. The Link
App QR code (`https://sonorium.app/link#...`, or the older `sonorium://pair?...`) lists every
address the server can be reached on; docs/AUTH.md describes it.

## 5. Actions API

`self.actions` is what a plugin may do with Sonorium. Every method is `async`, returns plain
data, and does exactly what the same change in the UI does (events, Home Assistant entities,
fades). A failure raises `ActionError` (`from sonorium.plugins.actions import ActionError`)
with `code` `not_found`, `invalid`, `conflict` or `unavailable`, and a `message`.

| Method | Does |
|---|---|
| `list_channels()` / `get_channel(id)` | Channels: `id`, `name`, `theme_id`, `theme_name`, `preset_id`, `sequence_id`, `playlist_id`, `volume`, `is_playing`, `speakers_muted`, `speakers`, `speaker_group_id` |
| `create_channel(theme_id, speakers, speaker_group_id, name, preset_id, volume)` | A new channel (stopped); all arguments optional |
| `start_channel(id)` / `stop_channel(id)` / `pause_channel(id)` | Play, stop or pause (fades as set in Settings) |
| `stop_all()` | Stop every channel; returns how many |
| `set_theme(id, theme_id, preset_id=None)` | Change the theme (crossfades if playing); no preset = the theme's default |
| `set_preset(id, preset_id)` | Glide to a preset of the channel's theme (`""` = none) |
| `set_speakers(id, speakers=[...])` or `set_speakers(id, speaker_group_id=...)` | Which speakers play the channel |
| `set_volume(id, volume)` | 0-100 |
| `mute_speakers(id, muted=True)` | Silence what the speakers get (a browser listening keeps hearing it) |
| `start_sequence(id, sequence_id)` / `stop_sequence(id)` | Run or stop a sequence of the channel's theme |
| `set_playlist(id, playlist_id)` / `clear_playlist(id)` | Play or stop a playlist on the channel |
| `next_step(id)` | Next playlist entry, or the sequence's next preset; returns `playlist`, `sequence` or `end` |
| `list_themes()`, `list_presets(theme_id)`, `list_sequences(theme_id)`, `list_playlists()` | What there is to play |
| `list_speakers()`, `list_speaker_groups()` | Speakers (`id`, `name`, `type`, `online`, `area_id`, `area_name`, `enabled`; a device of an output type also has `"type": "output"`, `output_type` and `device`, section 17) and speaker groups |
| `play_sound(channels, source, volume=100, duck=True, policy="queue", priority=0, start_stopped=False)` / `cancel_sound(sound_id)` | A sound over channels (`self.audio` has every option, section 15) |

A channel also has `entity_slug`: the part of its Home Assistant entity ids after `sonorium_`.

```python
channel = await self.actions.create_channel(theme_id="rain", speakers=["media_player.bedroom"],
                                            name="Wake up", volume=20)
await self.actions.start_channel(channel["id"])
```

Themes themselves are read and written with `self.list_themes()`, `self.new_theme()` and
`self.open_theme()` (see `plugins/theme_api.py`); never write `metadata.json` or
`presets.json` directly.

## 6. Scheduler

A plugin registers **jobs**; Sonorium keeps them (they survive restarts) and calls the
plugin when one is due. Like an alarm clock that remembers its alarms through a power cut.

```python
async def on_enable(self):
    self.schedule("wake", "weekly", {"time": "07:00", "days": ["mon", "tue", "wed", "thu", "fri"]},
                  name="Weekday wake-up", data={"channel": "Bedroom"}, missed="skip")

async def on_schedule(self, job):
    await self.actions.start_channel(job["data"]["channel"])
```

| Kind | Spec | Runs |
|---|---|---|
| `once` | `{"at": "2026-12-24T07:30"}` | Once, then the job is removed |
| `daily` | `{"time": "07:30"}` | Every day |
| `weekly` | `{"time": "07:30", "days": ["mon", "fri"]}` | On those days |
| `cron` | `{"cron": "30 7 * * 1-5"}` | minute, hour, day of month, month, day of week (0 or 7 = Sunday; names like `mon`, `jan` work) |
| `interval` | `{"seconds": 900, "start": "..."}` | Every N seconds (at least 10), counted from `start` if given |

- `job_id`: letters, digits, `_`, `-`, `.`; adding a job with the same id replaces it.
- `data`: any JSON object; it comes back in `job["data"]`.
- `missed`: what happens to a job that was due while Sonorium wasn't running. `skip` (the
  default) moves on to the next time; `run_once` runs it once at start-up, with
  `job["missed"] = True`.
- Time zone: times without an offset are local time in the scheduler's time zone. That is
  the one set with `PUT /api/schedules/settings {"timezone": "Europe/London"}`, else the
  `TZ` environment variable (the container's), else the system's. A job may set its own
  with `timezone=`. On a daylight-saving change, a time that doesn't exist that day (in the
  skipped hour) runs at the same clock time after the change; a time that happens twice
  runs once.
- `callback=`: instead of `on_schedule`, a function for this job. It lasts while the plugin
  runs (set it again in `on_enable`); the job itself is kept regardless.
- Jobs of a disabled plugin don't run; they move on to their next time.
- `self.unschedule(job_id)` removes a job; `self.scheduled_jobs()` lists them with
  `next_run`, `last_run`, `last_status` (`ok`, `error`, `missed`, `skipped`) and `last_error`.

A handler that raises (or runs over 5 minutes) is recorded in `last_status`/`last_error` and
reported as an `error` event. Each run publishes `schedule.fired`.

## 7. Webhook triggers

A trigger is a URL that a device or another system calls to make the plugin act: a motion
sensor, an ESP32 button, an IR receiver, a Home Assistant automation.

```python
def get_triggers(self):
    return [{"id": "play", "title": "Play", "methods": ["POST", "GET"],
             "description": "Play a channel. Payload: channel, volume."}]

async def on_trigger(self, trigger, payload, context):
    # context: method, client (address), event_id
    ...
```

Sonorium serves each trigger at:

```
POST /api/triggers/{plugin_id}/{trigger}        JSON object body (or form fields) = payload
GET  /api/triggers/{plugin_id}/{trigger}?k=v    query values = payload (devices that can only open a URL)
```

`methods` lists what the trigger accepts (default `POST` only). Sonorium answers
`202 {"accepted": true, "event_id": n}` at once; `on_trigger` runs afterwards, so a slow
handler never keeps the device waiting. Query values arrive as text.

**Tokens.** Every call needs a token, sent as `Authorization: Bearer <token>`,
`X-Sonorium-Token: <token>`, or `?token=<token>` (for GET-only devices). A token is either
the plugin's (opens all its triggers) or one trigger's (opens only that one; give each
device its own):

```
POST   /api/plugins/{id}/triggers/token                       -> {"token": "..."}   the plugin's
POST   /api/plugins/{id}/triggers/token {"trigger": "play"}   -> {"token": "..."}   one trigger's
DELETE /api/plugins/{id}/triggers/token[?trigger=play]        revoke
GET    /api/plugins/{id}/triggers                             triggers, URLs, which tokens are set
```

A token is shown once, when it is made; making a new one replaces the old one.

A key from **Settings › Keys** with **Run triggers** opens every trigger of every plugin, and
is the simpler choice for a device you also want to do other things with. Triggers always
need a token or a key, even while sign-in is off.

| Answer | Why |
|---|---|
| 202 | Accepted |
| 400 | Bad id, or a body that isn't a JSON object |
| 401 | No token, or a wrong one (also for a plugin that doesn't exist) |
| 404 | The plugin has no such trigger |
| 405 | The trigger doesn't accept this method |
| 409 | The plugin is disabled |
| 413 | Body over 16 KB |
| 429 | Too many calls: more than 10 in a burst, then 2 a second, per trigger; or more than 10 wrong tokens from one address (one more allowed every 6 s). `Retry-After` says when to try again. |

From a shell:

```sh
curl -X POST http://sonorium.local:8008/api/triggers/trigger_play_channel/play \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"channel": "Kitchen", "volume": 30}'
```

From an ESP32 (Arduino), when a button is pressed:

```cpp
#include <WiFi.h>
#include <HTTPClient.h>

const char* URL = "http://192.168.1.20:8008/api/triggers/trigger_play_channel/play";
const char* TOKEN = "paste the token here";

void playKitchen() {
  HTTPClient http;
  http.begin(URL);
  http.addHeader("Authorization", String("Bearer ") + TOKEN);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST("{\"channel\": \"Kitchen\"}");   // 202 = accepted
  http.end();
}
```

Other devices reach Sonorium on port 8008: the Docker port, and the Home Assistant app's
direct-access port (8008 unless it was changed in the app's network settings). Calls through
Home Assistant's sidebar link need a Home Assistant login, which devices don't have.

Each accepted call publishes `trigger.received`. A handler that raises (or runs over 60 s)
is reported as an `error` event.

## 8. Announcements, and Sonorium's own problems

Sonorium has no notification rules and nothing to set up for announcements. It is an
endpoint: other systems (Home Assistant, Node-RED, a script, a doorbell) send it an
announcement and it plays it (README, "Announcements: let other systems use Sonorium").
A plugin is one of those systems: it announces with `self.audio.announce()` (section 15),
the same path every other route takes.

Sonorium's own problems are not announcements. A speaker going offline, a lost Home
Assistant or MQTT connection, a plugin error, a channel stopping on its own, an import that
failed, low disk space, sign-in events: these go to the log at their level, and the serious
ones also show a small alert bar at the top of the web UI that links to the log. A plugin
that fails is reported there for it; a plugin logs its own problems with the standard
`logging` module (the logger `sonorium`).

## 9. Pages

A plugin can add entries to the left menu, each with a page. The plugin describes the page as
data (sections of text, fields, tables and buttons) and Sonorium draws it in its own style, so
every plugin page looks and works like the rest of the app. A plugin can't send HTML or
scripts.

```python
def get_pages(self):
    return [{"id": "alarms", "title": "Alarms", "icon": "alarm"}]

async def get_page(self, page_id):
    return {
        "title": "Alarms",
        "intro": "Plays a channel at set times.",    # optional: one line under the title
        "actions": [{"action": "add", "label": "Add alarm", "style": "primary"}],   # header buttons
        "refresh": 30,                      # optional: reload every 30 s
        "sections": [{
            "id": "add", "title": "New alarm",
            "items": [
                {"type": "field", "name": "time", "field": {"type": "time", "label": "Time"}, "value": "07:00"},
                {"type": "field", "name": "days", "field": {"type": "multiselect", "label": "Days",
                                                            "options": ["mon", "tue", "wed", "thu", "fri"]}},
                {"type": "button", "action": "add", "label": "Add", "style": "primary"},
            ]}, {
            "id": "list", "title": "Alarms",
            "items": [
                {"type": "table", "id": "alarms", "empty": "No alarms yet",
                 "columns": [{"key": "time", "label": "Time"}, {"key": "days", "label": "Days"}],
                 "rows": [{"id": "a1", "time": "07:00", "days": "Mon-Fri"}],
                 "row_actions": [{"id": "delete", "label": "Delete", "style": "danger",
                                  "confirm": "Delete this alarm?"}]},
            ]}],
    }

async def page_action(self, page_id, action, data):
    # data: {"values": {field name: value}, "row": the row's id for a row action, else None}
    if action == "add":
        ...
        return {"success": True, "message": "Alarm added"}       # the page reloads
    return await super().page_action(page_id, action, data)
```

| Item | Keys |
|---|---|
| `text` | `text` (plain text), `style`: `normal`, `muted`, `success`, `warning`, `error` |
| `field` | `name`, `field` (section 3 field types), `value` (current value) |
| `table` | `id`, `columns` (`key`, `label`, `format`: `text`, `number`, `boolean`, `datetime`, `status`), `rows` (each may have an `id`), `empty`, `row_actions` (`id`, `label`, `style`, `confirm`) |
| `command_map` | `map`: one of the plugin's command maps (section 18), drawn as a list the user fills |
| `button` | `action`, `label`, `style`: `primary`, `secondary`, `danger`, `confirm` (a question asked first) |
| `status` | `state`: `ok`, `warning`, `error`, `off`, `unknown`; `text` |

A `status` column takes a state (`ok`, `warning`, `error`, `off`, `unknown`) or
`{"state": "ok", "text": "On"}` for a chip with its own words. On a phone the columns other
than the first, status and ⋯ fold into a line under the first.

The page opens from a **Plugins** section of the left menu (between Settings and System),
with the plugin's name at the bottom ("From the plugin ..."). A button sends the values of all the page's fields. Sonorium checks them against the fields
before calling `page_action` (and refuses actions the page doesn't show). An item it doesn't
know is left out. A result may set `"refresh": false` to keep the page as it is.

**Icons** are a name (`alarm`, `mdi:alarm-clock`) or a small inline SVG (at most 8 KB, no
scripts, event attributes, links or `foreignObject`; others are dropped).

## 10. Connections

A plugin that talks to another system (SmartThings, Hubitat, openHAB, Node-RED, a Discord
bot, Google Drive) adds a row to Settings › Connection, next to Home Assistant and MQTT:
its icon, name (marked Plugin), status, what it points at, Test, Edit (a dialog with the
fields and its own Test) and Remove. Saving a plugin's connection needs no restart.

```python
def get_connections(self):
    return [{"id": "hub", "title": "Hubitat", "icon": "hub", "test": True,
             "description": "The Maker API of your Hubitat hub.",
             "fields": {
                 "host": {"type": "string", "label": "Hub address", "required": True},
                 "app_id": {"type": "number", "label": "Maker API app id", "min": 1},
                 "token": {"type": "secret", "label": "Access token"},
             }}]

async def test_connection(self, connection_id, values):
    ok = await self.try_hub(values["host"], values.get("token"))
    return {"success": ok, "message": "Connected" if ok else "No answer from the hub"}

def connection_status(self, connection_id):
    return {"state": "connected", "message": "12 devices"}   # connected, disconnected, error, not_configured, unknown

async def on_connection_changed(self, connection_id, values):
    await self.reconnect(values)
```

The plugin reads the saved values, secrets included, with `self.connection_values("hub")`.

**Secret fields** are write-only: the API never returns them. `GET` shows `token_set: true`
instead. Saving with an empty secret keeps the saved one (the form never has to send it
back); `"clear": ["token"]` removes it. Secrets are kept apart from other values, in an
owner-only file (section 11).

| Endpoint | |
|---|---|
| `GET /api/plugins/{id}/connections/{connection_id}` | Fields, saved values (no secrets) and status |
| `PUT /api/plugins/{id}/connections/{connection_id}` `{"values": {...}, "clear": [...]}` | Save (checked against the fields first) |
| `POST /api/plugins/{id}/connections/{connection_id}/test` `{"values": {...}}` | Try saved values with the form's (unsaved) ones on top |

`GET /api/extensions` lists the pages and connections of every enabled plugin, for the UI.

## 11. Security

- **Tokens** (triggers, the event stream) are 32 random bytes. Sonorium stores only their
  SHA-256 hash and compares hashes in constant time, so a copy of the file doesn't reveal a
  token and timing doesn't either. A wrong token and a plugin that doesn't exist get the same
  answer. Tokens never appear in logs: `token=` in logged URLs is masked.
- **Where things are kept**, next to `state.json` in `/config/sonorium/`:
  `plugin_framework.json` (jobs, plain connection values, the scheduler
  time zone, command maps, which channels are sent to which audio outputs) and `plugin_secrets.json` (token hashes, secret connection values; created
  readable by its owner only).
- **The management endpoints** (making tokens, jobs, connection values) are part of
  Sonorium's API and need an Admin: Home Assistant's sidebar, or a signed-in Admin on the
  direct ports once Require login is on (docs/AUTH.md). The trigger and
  event stream endpoints are the ones meant for other devices, and they need a token or a
  key on every call.
- **Every API route has a permission.** A plugin's own HTTP routes (section 29) declare the
  ability each needs; the server refuses an API route that has none.
- **Permissions** (section 28): a plugin lists what it uses, the user approves the list
  before it first runs and again when an update asks for more, and Sonorium's services
  refuse what isn't on it.
- **Plugins run inside Sonorium** with its rights. Install plugins you trust, as you would an
  add-on.
- A plugin never logs secrets or tokens itself; `connection_values()` is for the plugin's use only.

## 12. Testing a plugin

The repository's tests load plugins without Home Assistant. The quickest way to test your own
is to copy the pattern of `tests/test_plugin_framework_api.py`:

1. Build a session manager with fake speakers (`build()` in `tests/test_sequences_playlists.py`).
2. Make a `PluginHost` (with a fresh `EventBus`) and a `PluginManager(state_store,
   plugins_dir=..., host=host)`, with your plugin folder copied into `plugins_dir`.
3. Mount `create_plugin_framework_router(manager, host)` in a FastAPI app and use
   `TestClient` as a context manager; run async calls with `client.portal.call(...)`
   (`manager.initialize`, `host.start`, `manager.enable_plugin`).
4. Call your triggers, pages and actions over HTTP and check the events in
   `bus.history()` and the channel's state.

Run the suite with `python -m pytest -q tests`.

## 13. Packaging and the catalog

- **Upload:** zip the plugin folder (`trigger_play_channel/plugin.py`,
  `trigger_play_channel/manifest.json`) and upload it in Settings > Plugins (or
  `POST /api/plugins/upload`). A plugin with the same id is replaced; its settings, jobs and
  connection values are kept.
  - **Layout:** the plugin's files inside one top folder, or straight at the zip's root.
    Subfolders (your own modules, data files) are kept in both. `plugin.py` must be at the
    root of that folder (or of the zip); anything else (two top folders, `plugin.py` only
    deeper down) is refused with `No plugin.py found in archive`.
  - **Entry names:** forward slashes, or backslashes (zips made by Windows PowerShell 5.1
    `Compress-Archive`): both are read as folders. A name with `..`, an absolute path, a drive
    letter or a `:` is refused (`Invalid path in zip: ...`).
  - **Checked first:** the whole zip (entry names, `plugin.py`, `manifest.json`, id, version)
    is checked before anything is installed. A refused upload (400 with the reason) leaves the
    installed plugin exactly as it was; a plugin that passes is swapped in whole.
- **Catalog:** plugins offered in Settings > Plugins > Catalog are listed in
  [`plugins/catalog.json`](../plugins/catalog.json) with their zip next to it:
  `{"id", "name", "description", "version", "author", "category", "zip_file"}`.
  Raise `version` for every release: Sonorium offers an update when the catalog's version
  differs from the installed one.

## 14. Built-in plugins: sign-in hooks and web UI slots

Built-in plugins (shipped in `sonorium/plugins/builtin/`) can go further than the schema-rendered pages above: they can recognise
callers, add HTTP routes and draw parts of the web UI. Third-party plugins keep the
schema-rendered pages, triggers, events and actions.

A built-in plugin is copied to the config folder's `plugins/` and replaced only when the
bundled `version` in `manifest.json` is newer: **raise it with every change**.

### Manifest fields

| Field | Meaning |
|---|---|
| `"builtin": true` | Shipped with Sonorium (shown on its row). Skipping the permission approval depends on the folder matching the bundled copy, not on this field (section 28) |
| `"enabled_by_default": true` | Switched on the first time Sonorium sees it, never again after the user switches it off |
| `"auth_provider": true` | Guards sign-in: switching it off or uninstalling it asks first. No plugin uses it since Users became part of Sonorium (`sonorium/auth/users.py`) |

### The sign-in service (`self._host.auth`)

The core's `AuthService` (`sonorium/auth/service.py`). In `on_enable`:

```python
service = self._host.auth
service.add_routes(self.id, create_router(service), PERMISSIONS)       # APIRouter + a permission per route
service.register_ui(self.id, self.plugin_dir / "web", js="x.js", css="x.css", version=self.version)
service.register_authenticator(self.id, MyAuth(), provider=False)
```

and `service.unregister(self.id)` in `on_disable` (the host does it too). While the plugin is
off, its routes answer 404. A plugin may register more than one UI bundle under ids of its
own. Sonorium's own Users and Keys use the same service (`sonorium/auth/users.py`,
`keys.py`), and their web UI is in `static/js/ext/` (`users.js`, `zones.js`, `keys.js`).

### Channel policy (`self._host.session_manager.policy`)

A built-in plugin may decide who changes a channel and which speakers it plays on (as
Sonorium's Zones do, `sonorium/core/zones.py`): see `sonorium/core/channel_policy.py`. Its `check(action, session,
**details)` raises `ChannelRefused(message, status)` to refuse; the API answers with that
status. `sonorium.auth.principal.current_caller()` is who is calling (None for MQTT,
timers and plugins). No policy: every channel is open, as before.

`PERMISSIONS` lists every route, one per line: `METHOD  /api/path/{param}  permission`.
A permission is `public`, an ability (`ch.view`, `th.edit`, ... see
`sonorium/auth/permissions.py`), or `a|b` for either. `add_routes` refuses a router with an
unlisted route.

An authenticator may have any of these methods (`sonorium/auth/principal.py`):

| Method | Called for | Returns |
|---|---|---|
| `authenticate(ctx)` | every request not from HA's sidebar or a linked phone | a `Principal`, a `Reject(status, body)`, or `None` |
| `fallback(ctx)` | nobody recognised | a `Principal` (e.g. open access) or `None` |
| `ingress(ctx, principal)` | HA's sidebar | the principal, e.g. with the HA user's account |
| `device(ctx, principal, record)` | a linked phone | the principal it acts as, or a `Reject` |
| `me(ctx, principal)` | `GET /api/auth/me` | extra fields |
| `user(user_id)` | showing a user | `{"username", "role", "role_label"}` |

`ctx` has `headers`, `token` (bearer, `X-Sonorium-Token` or `?token=`), `cookie(name)`,
`listener` (`http`, `https`, `ingress`), `client` and `web_ok` (false on the direct ports
with `web_ui_only_through_ha`). A principal with a `csrf` value is a browser: its changes
must carry that value as `X-CSRF-Token`. The service also gives `tokens` (issue, verify,
update, revoke hashed tokens with expiry and an idle timeout), `sign(purpose)` (an HMAC with
the server's secret), `devices()`, `add_device()` and `remove_devices_of()`.

### Web UI slots (`static/js/slots.js`)

The bundle's JavaScript runs before the app starts and calls
`Sonorium.register("<plugin id>", {...})` with any of:

| Slot | Items | Shown |
|---|---|---|
| `screens` | `{show(me)}`: return true to take over | A full-page screen instead of the app (sign-in, first run) |
| `headerButtons` | `{view, html(me), when(me)}` | Left of a page's own header buttons (`view`: `sessions`, `themes`, `plugin-<id>`, ...) |
| `settingsPages` | `{id, label, icon, title, perm, before, help, render(el, me), actions(me)}` | A Settings menu item and its page (`view-plugin-<id>`); `before` is a menu item's id (default, or when it isn't there: Advanced); `help` is `{title, html}` for the page's help panel |
| `banners` | `{html(me)}` | A strip above the page header |
| `sidebar` | `{html(me)}` | Above the version line at the bottom of the menu |
| `channelCard` | `{badges(session), strip(session), top(session), pickers(session), sub(session), cls(session)}` | Next to a channel's name, under its now-playing line, above its head, above its theme pickers, after its speakers, and extra classes of the card |
| `channelsTop` | `{html(me)}` | Above the channel cards |
| `channelEditor` | `{render(session), save(sessionId, session), top(session), speakers(session), speakerNote(session, speakerId)}` | Fields at the bottom of the channel editor, above Plays, above the speaker picker; a speaker's note in the picker (`{busy: text}`: can't be picked, or `{note: text}`); `save` runs after the channel is saved |
| `advanced` | `{html(me)}` | Sections at the top of Settings › Advanced's values |

`me` is `GET /api/auth/me`: `kind`, `user`, `perms`, `csrf`, plus the plugins' fields.
Helpers: `Sonorium.modal({id, title, body, footer, cls, onClose})`, `Sonorium.closeModal(id)`,
`Sonorium.showScreen(html)`, `Sonorium.api(method, path, body)`, `Sonorium.esc(text)`,
`Sonorium.toast(message, type)`, `Sonorium.can(perm)`. `fetch` already adds the CSRF token to
changes and reloads the page when the sign-in is lost.

Follow `docs/UI_STYLE.md`; structural screens are drawn as boards and approved first.

## 15. Sounds over channels and announcements

A plugin can play a sound on one or more channels: it is mixed into each channel's stream,
so every speaker, browser and cast device of the channel hears it. While it plays, the
channel's own mix ducks (gets quieter) and comes back afterwards, the way a radio presenter
talks over the music.

```python
await self.audio.play(["kitchen", "office"], "https://example.com/ghost.mp3",
                      volume=90, duck={"db": 18, "attack": 0.3, "release": 2.0},
                      policy="now", label="Ghost")
```

| Argument | |
|---|---|
| `channels` | Channel ids |
| `source` | A file path, an http(s) URL, or the bytes of an audio file (anything FFmpeg reads) |
| `samples`, `sample_rate` | Instead of `source`: a numpy array (mono `(n,)` or `(channels, n)`; float -1..1 or int16) |
| `volume` | 0-200 (100: the file's own level) |
| `duck` | `True` (Settings › Advanced › Duck amount, attack and release), `False`, or `{"db", "attack", "release"}` |
| `policy` | `now`: plays at once, over anything playing (sound effects). `queue` (default): after the sounds playing now, higher `priority` first. `replace`: cuts what plays or waits with the same or a lower priority; a higher one keeps playing and this one waits |
| `priority` | A whole number (announcements use 10) |
| `max_seconds` | Cut it sooner than Settings › Advanced › Longest sound (default 60 s; 0 there is no limit) |
| `start_stopped` | A stopped channel is started for the sound: its stream runs silent, its speakers are started, the sound plays once they are connected (at most Advanced › Wait for speakers, then Lead), and after it (and Tail) the speakers stop again. The channel never shows as playing. Default false: a stopped channel is skipped |
| `override` | Skip gates (zones, quiet hours): admin-level. For announcements and alarms |
| `label` | A name for it in events and lists |
| `announcement` | It is an announcement: a channel with Announcements off skips it (`skipped`, "Announcements are off") |

It returns `{"sound_id", "length", "capped", "channels": {id: {"state", "message"}}}`; a
channel's state is `playing`, `queued`, `starting` or `skipped` (with the reason). A channel
whose speakers are muted plays the sound to browsers only, and isn't started for one.
`await self.audio.cancel(sound_id)` stops it; `await self.audio.sounds()` lists what plays.
With nothing playing over a channel, the mixer skips this step.

**Announcements** take one path, whoever sends them (Home Assistant, MQTT,
`POST /api/announce`, a plugin):

```python
result = await self.audio.announce("Kids Room", "Dinner is ready", sound="https://.../chime.mp3")
```

A chime and/or the text spoken, ducking, admin-level (override), starting stopped channels.
`channels` takes ids, names (not case-sensitive), a list, or `"all"`; `duck` takes `True`,
`False`, a number of dB, or `{"db", "attack", "release"}`. It returns `{"played", "skipped",
"channels", "chime", "speech", "notice"}`: `played` the channel ids it plays on, `skipped`
those with Announcements off, `notice` why the text wasn't spoken when only the chime played
(no text to speech). Text alone with no text to speech raises `ActionError` (`unavailable`).
While announcements are switched off in **Settings › Advanced** (`features.announcements`
in `GET /api/install`), `announce()` and a sound played with `announcement=True` raise
`ActionError` (`conflict`, "Announcements are off. ...") and nothing plays.

From outside, `POST /api/announce` (the ability **Make announcements**, `an.send`) takes
`{"channels", "text", "url", "volume", "duck", "provider", "language", "start_stopped"}` and
answers the same. A key limited to some channels may only announce on those (`"all"` means
its channels; another channel answers 403). An unknown channel answers 404; text with no
text to speech and no `url`, 503; announcements switched off in Settings › Advanced, 409.

Each channel has an **Announcements** setting (on by default; the channel's
`announcements`, `PUT /api/sessions/{id}/announcements {"on": false}`, and a
`switch.sonorium_<channel>_announcements` in Home Assistant, e.g. for a nap). With it off,
no announcement plays there, from any path: `announce()` leaves the channel out and returns
it in `"skipped"` (nothing is spoken when every channel is off). The setting is the
channel's own, so it holds for any channel, listed or not.

**Text to speech.** A provider turns text into audio; the first one registered is the
default. Sonorium provides Home Assistant's (`homeassistant`, its `plugin_id` is `null` in
`GET /api/plugin-framework`), ready while Home Assistant is linked. `ready` (optional) tells announcements whether the provider
can speak now; when it can't, only the chime plays:

```python
self.audio.register_tts("piper", "Piper (local)", self.speak, ready=lambda: True)

async def speak(self, message, language=None, **options) -> bytes:
    return wav_bytes
```

`await self.audio.tts("Hello")` gives the bytes; `self.audio.tts_providers()` lists them.

**Gates.** A plugin that owns channels (zones) can refuse sounds that aren't admin-level:

```python
self.audio.add_gate(lambda channel, request: "Quiet hours" if self.quiet(channel.id) else None)
```

`request` has `sound_id`, `label`, `origin`, `priority`. A sound with `override=True` skips
every gate.

**Timing.** The sound is exact to the stream. Network speakers (Cast, Sonos, AirPlay)
buffer a few seconds, so they play it that much later than it was sent: fine for an
announcement, noticeable for a sound effect that has to match a motion sensor. Direct
outputs (section 17) and browsers are close to instant.

## 16. Volume ramps

A ramp makes a channel louder or quieter gradually: an alarm that rises over 20 minutes, a
sleep timer that fades out over an hour. The gain is worked out for every sample, so it is
smooth rather than in steps. It applies to the channel's mix only: announcements still
play at their own level, and the speakers' volume isn't touched (set it first with
`set_volume`, then ramp within it).

```python
await self.ramps.start("bedroom", to=1.0, seconds=1200, from_level=0.0, curve="db", start=True)
await self.ramps.start("bedroom", to=0.0, seconds=3600, then="stop")    # sleep timer
await self.ramps.cancel("bedroom", reset=False)                          # hold where it is
await self.ramps.status("bedroom")      # {"level", "ramp": {...} or None}
```

| Argument | |
|---|---|
| `to`, `from_level` | 0 (silent) to 1 (the mix as it is), up to 4 (+12 dB). No `from_level`: from where it is |
| `seconds` | How long (0: at once) |
| `curve` | `db` (default): even to the ear. `linear`: in a straight line. `ease`: slow, faster, slow. `exp`: slow at first, fast at the end |
| `then` | `stop` or `pause` the channel when it ends |
| `start` | Play the channel first if it isn't playing (the ramp starts with it) |

A finished ramp holds its level until the channel stops or another ramp starts; one that
ends at 1.0 costs nothing afterwards. Events: `ramp.started`, `ramp.finished`,
`ramp.cancelled`. The API: `GET|POST|DELETE /api/sessions/{id}/ramp`.

## 17. Audio outputs

A channel normally plays on network speakers that pull its stream. An output type adds
another kind of speaker: the sound devices of the machine Sonorium runs on, a Bluetooth
speaker the OS exposes, a file. The plugin gives the data (which devices there are) and the
playback (a sink that takes a channel's audio); Sonorium does everything the user sees. Each
device is a speaker in Settings › Speakers, in a section per type, with the Use switch, a
name, a room, Offset and Test like any speaker, and it is picked for channels like any
speaker. A plugin doesn't add a page that lists or tests its devices, and Sonorium has no
code for any particular plugin.

```python
from sonorium.core.audio_outputs import OutputError, OutputType, Sink


class HostAudio(OutputType):
    id = "host_audio"
    title = "This computer"
    local = True                          # sound devices of the machine Sonorium runs on

    def available(self):                 # called often, on the event loop: no I/O
        return (True, "") if HAVE_SOUNDDEVICE else (False, "sounddevice isn't installed")

    async def discover(self):             # 10 s at most; blocking work in a thread
        import sounddevice
        devices = await asyncio.to_thread(sounddevice.query_devices)
        return [{"id": d["name"], "name": d["name"], "api": "PortAudio",
                 "channels": d["max_output_channels"]} for d in devices if d["max_output_channels"] > 0]

    def open(self, device_id, sample_rate, options):   # quick: no device I/O here
        return HostSink(device_id, sample_rate)


class HostSink(Sink):
    latency = 0.1

    def __init__(self, device, rate):
        self.device, self.rate, self.stream = device, rate, None

    def write(self, block):               # its own thread; int16, shape (1, n), mono, read-only
        if self.stream is None:           # open the device here, in the writer thread
            import sounddevice
            self.stream = sounddevice.OutputStream(device=self.device, samplerate=self.rate,
                                                   channels=1, dtype="int16")
            self.stream.start()
        self.stream.write(block.reshape(-1, 1))

    def close(self):                      # once, after the last write()
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()


class HostAudioPlugin(BasePlugin):
    id = "host_audio"
    ...
    async def on_enable(self):
        self.audio_outputs.register_type(HostAudio())
```

That is an example, not part of Sonorium: `sounddevice` isn't in every install (the plugin
brings it with `"requirements": ["sounddevice"]`, section 21). A real sink keeps a buffer of
its own and holds its level, since the device's clock drifts from the computer's.

**The type.** `id` (letters, digits, `_`, `.`, `-`; 1-64; unique across plugins), `title`
(the section title; empty: the id), `local` (read at `register_type()`),
`available()` → `(usable, why not)`, `async discover()`, `open(device_id, sample_rate,
options)` → a new `Sink`. `OutputType` and `Sink` are plain classes with no `__init__`.
`open()` raises `OutputError("sentence for the user")` when it can't give a sink.

**Devices.** `discover()` returns dicts: `id` (str, required, stable; only ever sent in JSON
bodies), `name` (required), `api` (shown under the name), `is_default` (the **Default**
marker), `channels` (int) and `sample_rates` (int list), shown with the device. Entries that
aren't dicts or have no string `id` are dropped; the first of two with the same `id` is kept.
Sonorium calls it when the type is registered, when Settings › Speakers or the channel
editor's speaker picker opens, on Rescan, and for `devices()` and the devices route.

Every listed device is remembered (`output_devices` in the framework store: type, device,
name, api, ...) as a speaker whose id is `output:<type id>:<first 16 hex of
sha256(device id)>`. The user's name, room and Offset are ordinary speaker settings, Use is
the speakers' switched-on list (a new device is off). A device missing from the latest
`discover()`, or whose plugin is off, is offline but stays in Settings › Speakers and in the
channels that have it; Remove on an offline device forgets it and takes it out of the
channels; uninstalling the plugin does that for all of its devices.

**Playing.** When a channel starts, Sonorium calls `open(device_id, 44100, options)` for
each of its switched-on devices (listed now or not) and a writer thread per sink reads the
channel's mix and calls `write(block)`: int16, `(1, 1024)`, mono, the same read-only array
every listener gets, in real time (a 50-block buffer; a slow sink falls behind alone). The
blocks are already scaled by the channel's volume with the device's Offset (a linear gain,
`volume / 100`, on the samples; neither Sonorium nor the plugin changes the device's own
volume) and ramp to silence while the speakers are muted. Stop and Pause fade the mix out,
then the writer ends and calls `close()`; a device doesn't keep the mix running after Stop.
`open()` is also called when the type is registered again (for playing channels) and when a
device is added to a playing channel. An `open()` failure is logged and shown on the
device's row (`output.error`), the channel plays on its other speakers, and there is no
retry until the next of those occasions. When `write()` raises, Sonorium logs `Audio output
<type>/<device> stopped: <message>`, calls `close()`, and doesn't re-open it until then
either. `write()` and `close()` never overlap; `close()` comes exactly once per sink.

**Local devices.** Set `local = True` when the type's devices are hardware of the machine
Sonorium runs on: its sound cards, USB and HDMI audio, Bluetooth paired to that machine.
Leave it `False` for devices on the network. One local device plays at a time, across every
local type (every plugin's) and every channel: a channel has at most one local device, never
two local sinks are open (the old sink's `close()` has returned before the next `open()`),
and a channel starting on a local device while another channel plays a local device asks to
take over: yes takes that device out of the other channel's speakers for good (it keeps
playing on its other speakers, and stops if none are left), no starts without it. Over HTTP:
`POST /api/sessions/{id}/play` answers 409 `<device> is playing '<channel>'` with the header
`X-Sonorium-Take-Over: ask` unless its body has `"take_over": true` (take over) or `false`
(start without it); a start from elsewhere (Home Assistant, MQTT, a plugin, a schedule) never
takes over. `PUT /api/sessions/{id}` and `POST /api/sessions` take `"take_over": true` too
(the channel editor's Move): the channels playing a local device lose it, and a playing
channel starts on its own. Test on a local device is refused while a local device plays.
In the channel editor local devices are picked with round (choose-one) buttons across all
local sections, a device playing elsewhere shows `In use by <channel>`, and picking one asks
`Move “<device>” to <channel>?` first.

The setting **Allow output to more than one local device** (Settings › Plugins, ⋯; off by
default; `allow_multiple_local_outputs` in `GET`/`PUT /api/settings`) lifts this: local
devices then play like network speakers (checkboxes, several at once, no take-over).

**Test** in Settings › Speakers (`POST /api/speakers/{speaker_id}/test`) calls `open(device,
44100, {})`, writes the test chime (3 s) at 20 % in 1024-sample blocks paced in real time,
then `close()`. 409 `This speaker is playing '<channel>'. Stop it first.` (or `<device> is
playing '<channel>'. Stop it first.` for a local device while one plays); an error from
`open()` answers 502 with its message.

**Switching off.** `on_disable()` runs first; then the types go and every sink's writer
finishes its `write()` and calls `close()`. If `on_enable()` raises, the types it registered
are removed.

| `self.audio_outputs` | Does |
|---|---|
| `register_type(output_type)` | Register a type (`ActionError` `invalid`: a bad id, or another plugin's) |
| `unregister_types()` | Remove the plugin's types now (Sonorium does it when the plugin is off) |
| `await types()` | `[{"id", "title", "plugin_id", "available", "message", "local"}]` |
| `await devices(type_id)` | The type's `discover()`, filtered; `not_found`, `invalid` (a timeout) |
| `await attach(channel_id, type_id, device_id, options=None, take_over=False)` | Add the device to the channel's speakers; it plays at once if the channel plays. `invalid`: the type isn't usable, the device is switched off (`Switch the speaker on in Settings › Speakers first`), 8 devices already, `open()`'s `OutputError`; `conflict`: a local device while another channel plays one |
| `await detach(channel_id, attachment_id)` | Take it out of the channel's speakers, close its sink; False if the channel doesn't have it |
| `await attachments(channel_id=None)` | `[{"attachment_id", "channel_id", "type", "device", "options", "running", "latency", "error", "speaker_id"}]` |

`options` given to `attach()` (or the POST route) are kept with the channel's device and
passed unchanged to every `open()` for it; everywhere else they are `{}`.

| Route | Ability | Answer |
|---|---|---|
| `GET /api/audio-outputs` | Channels: View | `{"types": [...]}` as `types()` |
| `POST /api/audio-outputs/discover` | Speakers: View | Every type lists its devices: `{"types": [type + "error", "speakers": [speaker, ...]]}` |
| `GET /api/audio-outputs/{type_id}/devices` | Speakers: View | `{"devices": [...]}`; 404 unknown type, 400 timeout |
| `DELETE /api/audio-outputs/speakers/{speaker_id}` | Speakers: Change | Remove an offline device (400 while online, 404 unknown) |
| `GET /api/sessions/{id}/audio-outputs` | Channels: View | `{"attachments": [...]}` |
| `POST /api/sessions/{id}/audio-outputs` | Channels: Change speakers | `{"type", "device", "options"?, "take_over"?}`: 201 with the output; 400, 404, 409 (with `X-Sonorium-Take-Over: ask`) |
| `DELETE /api/sessions/{id}/audio-outputs/{attachment_id}` | Channels: Change speakers | `{"removed": true}`; 404 |

A device speaker in `GET /api/speakers` and the hierarchy has `"type": "output"`,
`output_type`, `device` and `output`: `{"type", "type_title", "plugin_id", "device", "api",
"is_default", "channels", "sample_rates", "local", "type_available",
"playing": [channel ids], "error"}`.

Errors from your output type (`available()`, `discover()`, `open()`) on these routes:
`OutputError` is a 400 with your message; `ActionError` keeps its code (`not_found` 404,
`invalid` 400, `conflict` 409, `unavailable` 503) with your message; any other exception is
logged with its traceback and answered 502 `<your type's title>: <message>` (never a 500).
`sonorium.core.audio_outputs.CaptureOutput` is a test double that keeps what it receives.

| Install | Local sound devices |
|---|---|
| Windows app | Yes |
| Linux, macOS (from the Python sources) | Yes, as the user Sonorium runs as |
| Docker on Linux | With `--device /dev/snd` or the host's sound server socket mounted; with `PUID`/`PGID` the group must be allowed to use `/dev/snd` (often `audio`) |
| Home Assistant app | No: the app has no sound device |

## 18. Commands: remotes, MQTT topics, webhooks

An input plugin turns what it receives (an IR code, a button on an RF remote, an MQTT
message, a webhook call) into a **code**, and hands it over:

```python
def get_command_maps(self):
    return [{"id": "remote", "title": "TV remote",
             "codes": [{"code": "KEY_VOLUMEUP", "label": "Volume +"}]}]    # known codes, optional

async def on_ir(self, code):
    await self.commands.received("remote", code)
```

The user decides what each code does in a command map: a list of codes, each with an
action, a channel and its arguments. The plugin shows it on its page with
`{"type": "command_map", "map": "remote"}`; there **Learn** waits for the next code the
plugin receives (press the button), and **Add** adds one by hand. The actions run through
the plugin's actions API, so they behave like the same change in the UI:

| Action | Arguments |
|---|---|
| `channel.play`, `channel.stop`, `channel.toggle`, `channel.next`, `channel.mute` | |
| `channel.volume` | `volume` (or the number sent with the code: `received(map, code, value=42)`) |
| `channel.volume_up`, `channel.volume_down` | `step` (default 5) |
| `channel.theme`, `channel.preset`, `channel.sequence`, `channel.playlist` | the id |
| `channel.sound` | `url`, `volume` |
| `stop_all`, `nothing` | |

`GET /api/command-actions` lists them; `GET /api/plugins/{id}/command-maps`,
`PUT .../command-maps/{map}` `{"entries": [...]}`, `POST .../learn`, `POST .../test {"code"}`.
Every code received publishes `command.received`.

Network IR (a Broadlink, an ESP32 with an IR receiver sending MQTT or a webhook) works on
every install. A USB IR receiver needs the device: native installs, or Docker on Linux with
the device passed in; not the Home Assistant app.

## 19. MQTT

The broker Sonorium is connected to (the Mosquitto app on Home Assistant; the one set in
Settings › Connection elsewhere):

```python
self.mqtt.subscribe("zigbee2mqtt/front_door", self.on_door)   # "+" and "#" wildcards
await self.mqtt.publish("myplugin/state", {"armed": True}, retain=True)   # a dict is sent as JSON

async def on_door(self, topic, payload):
    ...
```

`self.mqtt.available` is false without a broker; publishing then does nothing, and
subscriptions start when one is linked. `self.mqtt.prefix` is Sonorium's topic prefix
(`sonorium`) and `self.mqtt.device_info` the "Sonorium" device, to put a plugin's Home
Assistant entities next to Sonorium's. Messages reach Sonorium's own entities first, then
plugins.

## 20. Home Assistant

Calls to Home Assistant's REST API through the connection Sonorium already has (the
Supervisor on the Home Assistant app; the saved URL and token on Docker and Windows). The
token never reaches the plugin, and only Home Assistant's `/api/...` paths are allowed:

```python
if self.homeassistant.available:
    status, states = await self.homeassistant.request("GET", "/api/states")
    audio = await self.homeassistant.tts("Dinner is ready", engine="tts.piper")
```

`tts()` asks Home Assistant's `tts_get_url` for the audio and fetches it through the same
connection (its TTS proxy), so Home Assistant's own external URL doesn't matter.
`tts_engines()` lists the `tts.*` entities.

### Announcements from Home Assistant and MQTT (part of Sonorium)

Announcements are part of Sonorium (`sonorium/ha/announcements.py`), on by default, with a
switch in **Settings › Advanced**. Each channel gets a notify entity over MQTT
("Kitchen Announce"). In Home Assistant:

```yaml
action: notify.send_message
target:
  entity_id: notify.sonorium_kitchen_announce
data:
  message: Dinner is ready
```

The message is spoken on that channel with Home Assistant's text to speech: the channel
ducks, the message plays, the channel comes back. A stopped channel is started for it and
stopped again. A message that is a JSON object takes the fields below (e.g. a chime `url`).
An MQTT message to `sonorium/announce/set`

```json
{"channels": ["Kitchen", "<channel id>"], "text": "Someone is at the door", "url": "https://.../chime.mp3", "volume": 80, "duck": 18}
```

does the same for any channels (`"all"` for every one), with an optional chime first; one
without `channels` is ignored. Home
Assistant and MQTT act as an admin: an announcement overrides a zone's owner and quiet
hours, and starts a stopped channel for its length. The speech uses Home Assistant's first
`tts.*` engine. It needs MQTT (for the entities) and Home Assistant (for the speech); a
chime URL plays without Home Assistant. Switched off in Settings › Advanced, the notify
entities are removed from Home Assistant and the topics are ignored, at once; switched on,
they come back.

## 21. Packages and programs a plugin brings

**Python packages.** `"requirements": ["pyserial==3.5"]` in the manifest: a package name
with optional extras and version conditions (no URLs, paths or pip options).

- Approved by the user first. The approval (section 28) lists every package with its
  version ("Install the Python package pyserial==3.5"), and nothing is installed before
  the user allows it. An update that adds a package or changes a version asks again.
- Off until Settings › Advanced › **Plugin packages** is switched on (then restart). A
  package comes from the internet and runs with Sonorium's rights, like the plugin. A
  plugin that needs packages while the switch is off is refused with that sentence, even
  once approved: both have to say yes.
- Installed with pip, wheels only (nothing is built and no setup script runs at install
  time), into `<config>/plugin_packages/<plugin id>/`, once, and again when the
  requirements or Python change. Removed with the plugin.
- That folder is added to the **end** of Python's search path: Sonorium's own packages
  always win, so a plugin can't replace them.

**Programs.** A plugin may ship programs, such as ffmpeg for an audio editor:

```json
"binaries": {"ffmpeg": {
    "linux-x86_64":  {"path": "bin/linux-x86_64/ffmpeg",  "sha256": "..."},
    "linux-aarch64": {"path": "bin/linux-aarch64/ffmpeg", "sha256": "..."},
    "windows-amd64": {"path": "bin/windows-amd64/ffmpeg.exe", "sha256": "..."}}}
```

Each file is checked when the plugin loads: it stays inside the plugin's folder (no
links), and its SHA-256 matches. A mismatch refuses the plugin (it was changed or
damaged). `self.binary("ffmpeg")` is the path for this platform, or None when there is none
for it. Platforms: `linux-x86_64`, `linux-aarch64`, `windows-amd64`, `darwin-arm64`, ...

| Install | Packages | Programs |
|---|---|---|
| Home Assistant app | When switched on: Alpine Linux, so musllinux wheels | Built for musl (or static) |
| Docker | When switched on: Debian, manylinux wheels | Linux builds |
| Windows, Linux, macOS | When switched on: the platform's wheels | The platform's builds |

The config folder is kept across updates on every install, so packages are installed once.

## 22. What the web UI draws for plugins

| Part | Where | From |
|---|---|---|
| Pages | The Plugins section of the left menu | `get_pages()`, `get_page()` (section 9) |
| Connections | A row each in Settings › Connection | `get_connections()` (section 10) |
| Command maps | On the plugin's page | `{"type": "command_map"}` (section 18) |
| Casting | A strip on a channel's card while a phone casts it | the app's outputs |
| Alerts | A bar at the top when something has failed | errors, refused plugins |
| Anything on any screen | The open spots of every screen, row, menu and editor | `get_ui()` (section 24) |

The renderers are one module each in `web/static/js/ext/`, styled in `web/static/css/ext.css`.

## 23. Attributes on Sonorium's objects

A plugin can keep its own values on Sonorium's objects: a light cue on a thunder file, a
strip name on a channel, a light delay on a speaker. It works like extended attributes in a
directory service: the plugin declares a schema, Sonorium stores values only for declared
attributes, checks each one against its declaration, and carries them wherever the object
goes. Sonorium never reads what they mean. Capability `attributes`, plugin API 1.2.

### Declaring

In `manifest.json` (`"attributes"`), from `get_attributes()`, or with
`self.attributes.declare(...)` in `on_load`. Each call replaces the plugin's whole schema.

```json
"attributes": {
  "track":   {"cue": {"type": "enum", "options": ["flash", "rumble", "auto"], "default": "auto",
                      "label": "Light cue", "help": "What the lights do when this file plays"},
              "intensity": {"type": "number", "min": 0, "max": 100, "integer": true}},
  "file":    {"cue": {"type": "enum", "options": ["flash", "rumble", "auto"], "default": "auto"}},
  "speaker": {"light_sync": {"type": "number", "min": 0, "label": "Light sync",
                             "help": "Milliseconds the lights wait for this speaker"}}
}
```

| Object class | What | Stored in |
|---|---|---|
| `theme` | A theme | `metadata.json`, top-level `plugins` |
| `track` | A track of a theme (top level or in a group) | its entry in `metadata.json` `tracks` |
| `file` | A file in a group (`"Group/Name"`) | the same entry: a track's and a file's attributes share it, so declare a name the same way for both |
| `preset` | A theme's preset | the preset in `presets.json` |
| `channel` | A channel (not a personal one) | `state.json`, the channel |
| `speaker` | A speaker | `state.json`, its speaker settings |
| `user` | An account | `users.json`, the user |
| `group` | A theme's group | its entry in `metadata.json` `groups`, `plugins` (a renamed group keeps them) |
| `sequence` | A theme's sequence | the sequence in `presets.json`, `plugins` |
| `step` | A step of a sequence (id `"<sequence id>/<preset id>"`) | the sequence's `step_plugins`, by preset; a step taken out takes its values with it |
| `playlist` | A playlist | `state.json` `playlists`, the playlist |
| `playlist_entry` | An entry of a playlist (id `"<playlist id>/<entry id>"`) | the entry; each entry has an `id` of its own, so it keeps its values when the order changes |
| `speaker_group` | A speaker group | `state.json`, the group |
| `area`, `key`, `connection`, `plugin`, `device` | An area, a key, a connection, a plugin (its row in Settings › Plugins), a plugin's device (section 26) | `state.json` settings `object_plugins`, by kind and id (their records are kept elsewhere) |

Theme files: docs/THEME_FORMAT.md 2.7.

| Type | Limits (all optional unless said) |
|---|---|
| `string` | `max_length`, `pattern` (a regular expression the whole text must match) |
| `number` | `min`, `max`, `integer: true` (whole numbers only) |
| `bool` | |
| `enum` | `options` (required): values, or `{"value", "label"}` |
| `list` | `items` (required: a declaration for each item), `min_items`, `max_items` |
| `object` | `fields` (required: name -> declaration). Only declared fields are kept |

Every attribute may have `default`, `label` and `help`; Sonorium will use label and help when
it draws attribute fields (Plugin Framework 2, phase 1). Names are letters, digits and `_`
(up to 64), starting with a letter. `boolean` and `select` are accepted for `bool` and
`enum`. A malformed attribute is left out with a warning in the log; the others still count.
`GET /api/attributes` lists every installed plugin's schema and the size limit.

### Reading and writing

An object reference is a small dict:

```python
{"kind": "theme",   "id": theme_id}
{"kind": "track",   "theme": theme_id, "id": "Thunder/Close"}
{"kind": "file",    "theme": theme_id, "id": "Thunder/Close"}   # a file in a group only
{"kind": "preset",  "theme": theme_id, "id": "calm"}
{"kind": "channel", "id": channel_id}
{"kind": "speaker", "id": "media_player.living_room"}
{"kind": "user",    "id": user_id}
```

```python
ref = {"kind": "track", "theme": theme_id, "id": "Thunder/Close"}
self.attributes.get(ref)                         # {"cue": "auto"}: defaults filled in
self.attributes.set(ref, {"cue": "flash", "intensity": 90})
self.attributes.set(ref, {"intensity": None})    # None removes one value
self.attributes.clear(ref)                       # removes all of this plugin's values there
self.attributes.schema()                         # what the plugin declared, as kept
```

A plugin reads and writes only under its own id. A value that doesn't fit, a name the
plugin didn't declare for that class, or a change over the size limit raises `ActionError`
(`invalid`, every problem in one message); an unknown object raises `not_found`. Each change
publishes `attribute.changed` (`kind`, `id`, `theme_id` for theme objects, `plugin_id`,
`keys`), so a plugin can follow changes made elsewhere without polling.

Outside programs and the web UI use the HTTP API:

| Route | Permission | What |
|---|---|---|
| `GET /api/attributes` | any of Themes view, Channels view, Speakers view, admin | Every plugin's schema, the classes and the size limit |
| `GET /api/attributes/{kind}?id=..&theme=..` | by kind, below | `{"plugins": {plugin_id: {name: value}}}` with defaults |
| `PUT /api/attributes/{kind}/{plugin_id}?id=..&theme=..` | by kind, below | Body `{name: value or null}`. 400 with every problem; returns the plugin's values |

| Kind | Read | Change |
|---|---|---|
| theme, track, file, preset | Themes: view | Themes: edit |
| channel | Channels: view (and the key's channels) | Channels: change |
| speaker | Speakers: view | Speakers: change settings |
| user | Admin (users), or the user themself | the same |

Speaker attributes can also be changed with the speaker's settings:
`PUT /api/speakers/{id}/settings` with `{"plugins": {"wled": {"light_sync": 300}}}` (null for a
plugin removes its values; other plugins' values stay).

### What is kept, and when it goes

- **Only the schema counts.** A value of a plugin that isn't installed, of an attribute it
  doesn't declare, or that doesn't fit its declaration is discarded. A theme imported into a
  Sonorium without your plugin loses your values on import, without errors, with an info line
  in the log. Values that no longer fit after an update of your schema are left out when
  read, and removed when the theme is next saved or your values on that object next change.
- **Every save keeps the rest**: Theme Editor saves, Reset, presets, moving a file between
  groups, speaker settings, channel changes, theme export and import.
- **Switched off**: the schema and the values stay. Nothing is shown for the plugin; switch
  it on again and everything is there.
- **Uninstalled**: the schema goes and the plugin's values are removed from every object
  Sonorium knows.
- **Size**: 4096 bytes of compact JSON per plugin per object by default. It is adjustable
  (`plugin_data_limit` in the Settings › Advanced values, `PUT /api/settings/playback-values`;
  0 = no limit). A change over it is refused with a message naming the limit; values already
  stored over it are kept, with a warning in the log. Keep big data (hundreds of cue points)
  in a file in the theme folder and store the file's name.

### Sync: when a speaker is heard

Each speaker has a **Sync** setting: how many milliseconds it plays behind Sonorium's mixer
(its own buffer of the stream). Without a setting it is the estimate for its kind (Cast 5000,
DLNA, HEOS and Denon 4000, Sonos and LinkPlay 3000, AirPlay 2000, ESPHome 1500, others 3000).
The Status page's "as heard" and anything timed to the sound use this one number.

- `GET /api/speakers/{id}/settings` and the speaker lists: `speaker.sync_ms` (its Sync now)
  and `speaker.sync_estimate_ms` (the estimate for its kind); `settings.sync` when set.
- `PUT /api/speakers/{id}/settings` with `{"sync": 4200}` (a whole number, 0 or more), or
  `{"sync": null}` to go back to the estimate.

The cue timeline (section 25) gives each speaker's audible time from it.
A plugin that needs its own timing per speaker declares a speaker attribute for it and names
it after the feature, for example `light_sync` labelled "Light sync". Settings › Speakers
has no Sync field yet: it is set through the API until the speaker editor gets one.

## 24. Adding to any screen

A plugin can add fields, buttons, menu items, badges, columns, lines of text and whole pages
to any screen of the web UI. It sends them as data; Sonorium draws them with the same
controls as everything else, so they look and behave like the rest of the screen. Capability
`ui`, plugin API 1.2.

Think of each screen as a form with labelled boxes: some boxes are filled in and locked (the
**fixed parts**: a channel card's name, pickers, volume and Play button), others are left
open for additions (the **open spots**: a badge after the name, an item in the ⋯ menu, a
group in the editor). A plugin only adds, only in the open spots, and only what the spot's
**contract** allows. It can never change, hide, move or restyle a fixed part, and what it
adds never touches another plugin's additions.

`GET /api/ui/targets` lists every target (a screen, a kind of row or card, an editor, the
left menu), its fixed parts, its open spots with their contracts, and the facts its
conditions can test. The tables below are the same list.

### Adding

In `manifest.json` (`"ui"`), from `get_ui()`, or at any time with `self.ui.register()`:

```python
def get_ui(self):
    return [
        {"target": "channel.editor", "spot": "settings", "id": "minutes", "kind": "field",
         "type": "slider", "label": "Stop after", "unit": " min", "bind": {"attribute": "minutes"}},
        {"target": "channel.card", "spot": "badges", "id": "left", "kind": "badge",
         "value": {"live": True}, "when": {"playing": True}},
        {"target": "channel.card", "spot": "menu", "id": "stop30", "kind": "action",
         "label": "Stop in 30 min"},
    ]
```

Every element has its `target`, its `spot`, an `id` (a-z, 0-9, `_` and `-`, up to 40
characters, unique in the plugin) and a `kind`. Sonorium checks them when the plugin is
switched on (or calls `register()`).

### Element kinds

| Kind | What it is | Keys |
|---|---|---|
| `field` | One control with a value | `type`, `label`, `bind`; `help`, `unit`, `placeholder`, `min`, `max`, `step`, `options`, `default`, `compact` |
| `action` | A button, or a menu item | `label`; `confirm` (a question asked first), `help` |
| `badge` | A short badge | `text`, or `value`; `state` (`ok`, `warning`, `error`, `off`), `label` (its tooltip) |
| `column` | A value in a row, with a heading | `label` (the heading), `value`; `text` while there is no value |
| `info` | A line of text | `text`, or `value`; `label` (bold, before it), `style` (`normal`, `muted`, `success`, `warning`, `error`) |
| `group` | A titled set of fields | `label`, `fields` (each a field with its own `id`) |
| `page` | A whole page, opened from the left menu or Settings | `title`, `icon`, `sections`: `[{"title", "elements": [...]}]` of fields, actions, info lines, groups and badges |

Field types: `text`, `number`, `slider` (needs `min` and `max`), `toggle`, `select` (needs
`options`: values, or `{"value", "label"}`), `colour` (`#rrggbb`) and `list` (text or numbers,
typed as `a, b, c`).

### Where a value comes from

A field is bound to one place:

- `{"bind": {"attribute": "minutes"}}`: an attribute the plugin declares (section 23) on the
  object the screen shows (this channel, this speaker). Its type, range, options and default
  come from the declaration (every kind of object keeps attributes, section 23): a `toggle` shows a `bool`, `number` and `slider` a `number`,
  `select` an `enum`, `text` and `colour` a `string`, `list` a list of strings or numbers.
  Editing the field saves the attribute, checked against the declaration.
- `{"bind": {"setting": "units"}}`: one of the plugin's settings (section 3), on its row in
  Settings › Plugins, on its pages and in a screen's top strip.
- `{"bind": {"action": "set_mode"}}`: no stored value. Each change calls
  `ui_action("set_mode", ref, value)`; the field shows `default` until the plugin answers
  with `"value"` or sends a live value.

Badges, columns and info lines show `text`, or a value:

- `{"value": {"attribute": "minutes"}}`: the attribute on the object;
- `{"value": {"live": true}}`: what the plugin sends with
  `self.ui.set_value(element_id, value, ref=None)`. `ref` is the object
  (`{"kind": "speaker", "id": "..."}`; `None` for every object). A value is text, a number,
  true/false, or `{"text", "state"}` for a badge; `None` removes it. The web UI follows
  them on the event stream (`ui.value`), so they change on screen as they are sent.

### Actions

A button, a menu item or an action-bound field calls the plugin:

```python
async def ui_action(self, element_id, ref, value=None):
    if element_id == "stop30":
        ...  # a volume ramp down over 30 minutes, then stop (section 16)
        return {"success": True, "message": "Stops in 30 minutes"}
    return {"success": False, "message": f"Unknown action: {element_id}"}
```

`ref` is the object the screen shows: `{"kind", "id", "theme"}` (theme objects carry their
theme), or `None` on a screen or a page. The answer's `message` is shown as a toast;
`"refresh": true` draws the plugin's additions again; `"value"` sets the field's value.
Actions keep their ability: pressing one needs "Use plugin pages and buttons" (`pg.use`).
Adding to a screen needs no ability of its own.

### Conditions

`"when"` shows an element only for some objects or states. It tests the facts of the object
(the table below), `phone` (the phone layout), and `attr.<name>` (an attribute the plugin
declares, on that object). Each fact is a value, a list of values (any of them), or
`{"not": value}`; all facts of a condition must hold; a list of conditions means any of them:

```json
"when": {"mode": "theme", "playing": true}
"when": [{"section": "sparse"}, {"attr.cue": {"not": "none"}}]
```

### Where things go

- **The plugin's settings** go on its row in Settings › Plugins (spot `plugin` of
  `plugin.row`, bound to settings), where it is switched on and off and uninstalled. Most of
  a plugin's configuration belongs there.
- **Settings about one thing** (a channel, a theme, a track, a speaker) go in that thing's
  editor: one collapsed group per plugin, marked with the plugin badge, after the editor's
  settings. A track, a file, a group, a preset, a speaker and a user have no editor dialog:
  their group is at the end of their ⋯ menu.
- **Menu items** come after the menu's items and before destructive ones (Delete, Remove).
- **Header buttons on a phone** don't take a row of their own: they go in the screen's
  title-bar ⋯ (Themes, Audio, Plugins), or, on a screen without one, in one ⋯ for all
  plugins, so the screen's buttons and the help button keep their row.
- **The ⋯ dot** shows when anything in the menu differs from its default, whichever item
  it is: a value bound to an attribute, or a live value of an action-bound field.
- **The plugin badge** is the grey badge with the plugin's icon and its short name
  (manifest `"short_name"`, up to 16 characters; default the name).
- **On a phone** rows become cards, columns show as "heading value", and a field with
  `"compact": true` drops its label (like Offset).
- **Switched off**, a plugin's additions leave every screen; switched on, they are back.
  Uninstalled, they are gone, and its attribute values go as section 23 says.

### Contracts

Each open spot takes what its contract allows. An element that breaks it is **refused**:
not drawn, logged once, and listed on the plugin's row in Settings › Plugins with the spot
and the rule, for example:

> speaker.row/menu: 'long': the label is 39 characters; the limit here is 20; refused

Nothing is cut short to fit. Too many elements in a spot refuses all of the plugin's
elements in that spot; the rest of the plugin still counts. A fixed part, an unknown target
or spot, a kind or field type the spot doesn't take, a binding it doesn't allow, an
attribute the plugin doesn't declare for that object, an unknown fact: each is refused the
same way. `self.ui.register()` and `self.ui.warnings()` return the warnings.

The contracts are versioned with the plugin API: a later 1.x only adds targets, spots and
kinds, or raises a limit. Every limit is in one place (`plugins/ui_contract.py`), adjustable
there.

| Spot | Element kinds | Per plugin | Label | Text | Field types | Binds to | Fields per element | Where |
|---|---|---|---|---|---|---|---|---|
| `header` | action | 1 | 20 | – | – | – | – | after the screen's buttons; on a phone, in the screen's title-bar ⋯ (or, without one, in one ⋯ for them), so the screen's buttons and help keep their row |
| `top` | info, action, field | 3 | 24 | 120 | toggle, select, number, slider, text | setting, action | 1 | one strip per plugin, after the screen's bars, above its content |
| `badges` | badge | 1 | 24 | 12 | – | – | – | after the item's badges |
| `columns` | column | 1 | 12 | 16 | – | – | – | at the end of the name column, with its heading above |
| `info` | info | 1 | 24 | 80 | – | – | – | one line after the card's lines |
| `menu` | action, field, group | 3 | 20 | – | toggle, select, number | attribute, action | 2 | after the menu's items, before destructive ones |
| `settings` | field, info, action, group | 8 | 24 | 120 | text, number, slider, toggle, select, colour, list | attribute, action | 6 | one collapsed group per plugin, after the editor's settings |
| `plugin` | field, info, action, group | 20 | 24 | 200 | text, number, slider, toggle, select, colour, list | setting, action | 6 | the plugin's row in Settings › Plugins, after its settings |
| `pages` | page | 2 | 24 | – | text, number, slider, toggle, select, colour, list | setting, action | 6 | after the menu's entries |

**Channels**

| Target | Shows | Open spots | Fixed parts |
|---|---|---|---|
| `screen.sessions` | – | `header`, `top` | title, actions, cards |
| `channel.card` | channel (attributes) | `badges`, `info`, `menu` | name, pickers, volume, recent, play, edit |
| `channel.editor` | channel (attributes) | `settings` | name, plays, theme, preset, volume, speakers, footer |

**Playlists**

| Target | Shows | Open spots | Fixed parts |
|---|---|---|---|
| `screen.playlists` | – | `header`, `top` | title, actions, list |
| `playlist.row` | playlist (attributes) | `badges`, `columns`, `menu` | name, entries, order, repeat, play |
| `playlist.editor` | playlist (attributes) | `settings`, `menu` | name, timing, order, entries, footer |
| `playlist.entry` | playlist_entry (attributes) | `menu` | theme, preset |

**Themes**

| Target | Shows | Open spots | Fixed parts |
|---|---|---|---|
| `screen.themes` | – | `header`, `top`, `menu` | title, actions, search, filters, collection |
| `theme.card` | theme (attributes) | `badges`, `menu` | name, description, categories, preview, favorite, edit |
| `theme.editor` | theme (attributes) | `settings`, `menu` | name, icon, categories, description, tracks, footer |
| `theme.track` | track (attributes) | `badges`, `menu`, `settings` (in its ⋯ menu) | name, mode, sliders, mute |
| `theme.file` | file (attributes) | `badges`, `menu`, `settings` (in its ⋯ menu) | name, mode, sliders, mute |
| `theme.group` | group (attributes) | `badges`, `menu`, `settings` (in its ⋯ menu) | name, timing, sliders, mute |
| `theme.preset` | preset (attributes) | `menu`, `settings` (in its ⋯ menu) | preset, load, new |
| `sequence.editor` | sequence (attributes) | `settings`, `menu` | name, timing, order, presets, footer |
| `sequence.step` | step (attributes) | `menu` | preset |
| `step.editor` | step (attributes) | `menu` | tabs, tracks, footer |

**Settings › Connection**

| Target | Shows | Open spots | Fixed parts |
|---|---|---|---|
| `screen.settings-connection` | – | `header`, `top` | title, actions, connections |
| `connection.row` | connection (attributes) | `menu` | name, summary, status |

**Settings › Audio**

| Target | Shows | Open spots | Fixed parts |
|---|---|---|---|
| `screen.settings-audio` | – | `header`, `top`, `menu` | title, actions, settings |

**Settings › Floors & Areas**

| Target | Shows | Open spots | Fixed parts |
|---|---|---|---|
| `screen.settings-spaces` | – | `header`, `top` | title, actions, floors |
| `area.row` | area (attributes) | `badges` | name, floor |

**Settings › Speakers**

| Target | Shows | Open spots | Fixed parts |
|---|---|---|---|
| `screen.settings-speakers` | – | `header`, `top` | title, actions, toolbar, list |
| `speaker.row` | speaker (attributes) | `badges`, `columns`, `menu`, `settings` (in its ⋯ menu) | name, source, address, area, offset, test, use |

**Settings › Speaker Groups**

| Target | Shows | Open spots | Fixed parts |
|---|---|---|---|
| `screen.settings-groups` | – | `header`, `top` | title, actions, list |
| `speaker_group.row` | speaker_group (attributes) | `badges`, `columns`, `menu` | name, includes, speakers |
| `speaker_group.editor` | speaker_group (attributes) | `settings` | name, speakers, footer |

**Settings › Plugins**

| Target | Shows | Open spots | Fixed parts |
|---|---|---|---|
| `screen.settings-plugins` | – | `header`, `top`, `menu` | title, actions, tabs, list |
| `plugin.row` | plugin (attributes) | `badges`, `menu`, `plugin` | name, version, description, author, on, settings |
| `device.row` | device (attributes) | `badges`, `columns`, `menu`, `settings` (in its ⋯ menu) | name, area, actions, use, follows |

**Settings › Logs**

| Target | Shows | Open spots | Fixed parts |
|---|---|---|---|
| `screen.settings-logs` | – | `header`, `top` | title, actions, log |

**Settings › Advanced**

| Target | Shows | Open spots | Fixed parts |
|---|---|---|---|
| `screen.settings-advanced` | – | `header`, `top` | title, actions, features, values |

**Settings › Users & devices**

| Target | Shows | Open spots | Fixed parts |
|---|---|---|---|
| `screen.plugin-users` | – | `header`, `top` | title, actions, list |
| `user.row` | user (attributes) | `badges`, `menu`, `settings` (in its ⋯ menu) | name, role, devices |

**Settings › Keys**

| Target | Shows | Open spots | Fixed parts |
|---|---|---|---|
| `screen.plugin-keys` | – | `header`, `top` | title, actions, list |
| `key.row` | key (attributes) | `menu` | name, abilities |

**Status**

| Target | Shows | Open spots | Fixed parts |
|---|---|---|---|
| `screen.status` | – | `header`, `top` | title, actions, tiles, channels |
| `status.channel` | channel (attributes) | `badges`, `info` | name, tracks |

**The left menu and Settings menu**

| Target | Shows | Open spots | Fixed parts |
|---|---|---|---|
| `sidebar` | – | `pages` | logo, sections, version |
| `settings.nav` | – | `pages` | entries |

**Other clients** (drawn by that client from `GET /api/ui/contributions`, not by the web UI;
`client` in `GET /api/ui/targets`)

| Target | Client | Open spots | Fixed parts |
|---|---|---|---|
| `tray.menu` | the Windows tray app | `menu` (actions, toggles bound to an action) | status, open, pages, quit |

| Object | Facts for `when` |
|---|---|
| channel | `playing` (bool), `mode` (theme / playlist), `theme` (text), `preset` (text), `muted` (bool), `speakers` (number), `attr.<name>` |
| theme | `favorite` (bool), `tracks` (number), `playing` (bool), `attr.<name>` |
| track | `section` (unassigned / continuous / sparse / presence), `muted` (bool), `linked` (bool), `in_group` (bool), `attr.<name>` |
| file | `section` (unassigned / continuous / sparse / presence), `muted` (bool), `linked` (bool), `in_group` (bool), `attr.<name>` |
| group | `section` (continuous / sparse / presence), `muted` (bool), `tracks` (number), `attr.<name>` |
| preset | `default` (bool), `attr.<name>` |
| playlist | `playing` (bool), `order` (in_order / shuffle), `entries` (number), `attr.<name>` |
| playlist_entry | `sequence` (bool), `attr.<name>` |
| sequence | `steps` (number), `end` (reverse / restart / stop), `attr.<name>` |
| step | `advanced` (bool), `attr.<name>` |
| speaker | `online` (bool), `enabled` (bool), `type` (text), `device` (bool), `attr.<name>` |
| speaker_group | `speakers` (number), `attr.<name>` |
| plugin | `enabled` (bool), `builtin` (bool), `attr.<name>` |
| user | `role` (admin / user), `me` (bool), `attr.<name>` |
| key | –, `attr.<name>` |
| connection | `configured` (bool), `attr.<name>` |
| area | `speakers` (number), `attr.<name>` |

For all spots: `help` up to 120 characters, `confirm` up to 80, `unit` up to 8, a select up
to 20 options of up to 24 characters, a list up to 20 items, a text value up to 200
characters, a slider up to 1000 steps, `when` up to 4 conditions of up to 4 facts, and up to
500 elements per plugin. A page has up to 6 sections of up to 12 elements; its title is up to
24 characters.

"Section" facts are the Theme Editor's sections: `continuous` (Background), `sparse`
(Intermittent), `presence` (Ebb & Flow), `unassigned`.

### Example: a sleep timer

A plugin that stops channels after a while. Its time per channel is an attribute, set in the
channel editor; the card shows what is left while it plays; the card's menu has a quick
choice; its default fade is a setting on its row in Settings › Plugins.

```json
{
  "id": "sleep_timer", "name": "Sleep Timer", "short_name": "Sleep", "version": "1.0.0",
  "api_version": "1.2", "requires": ["attributes", "ui"],
  "settings_schema": {"fade": {"type": "number", "label": "Fade out (s)", "min": 0, "max": 600, "default": 60}},
  "attributes": {"channel": {"minutes": {"type": "number", "min": 0, "max": 240, "default": 0,
                                         "label": "Stop after"}}},
  "ui": [
    {"target": "channel.editor", "spot": "settings", "id": "minutes", "kind": "field", "type": "slider",
     "label": "Stop after", "unit": " min", "bind": {"attribute": "minutes"}},
    {"target": "channel.card", "spot": "badges", "id": "left", "kind": "badge", "value": {"live": true},
     "when": {"playing": true, "attr.minutes": {"not": 0}}},
    {"target": "channel.card", "spot": "menu", "id": "stop30", "kind": "action", "label": "Stop in 30 min",
     "when": {"playing": true}},
    {"target": "plugin.row", "spot": "plugin", "id": "fade", "kind": "field", "type": "number",
     "label": "Fade out", "unit": "s", "bind": {"setting": "fade"}}
  ]
}
```

While a channel counts down, the plugin sends what is left:
`self.ui.set_value("left", "25 min", {"kind": "channel", "id": channel_id})`.

### Example: room temperatures

A plugin that reads a temperature sensor per room. Each speaker can name its sensor (an
attribute in the speaker's ⋯ menu); Settings › Speakers shows a Temp column; Status shows
the warmest room; a page in the left menu holds its options.

```python
class RoomClimate(BasePlugin):
    id = "room_climate"
    name = "Room Climate"

    def get_settings_schema(self):
        return {"interval": {"type": "number", "label": "Read every", "min": 1, "max": 60, "default": 5}}

    def get_attributes(self):
        return {"speaker": {"sensor": {"type": "string", "max_length": 80, "default": "",
                                       "label": "Sensor"}}}

    def get_ui(self):
        return [
            {"target": "speaker.row", "spot": "settings", "id": "sensor", "kind": "field",
             "type": "text", "label": "Sensor", "placeholder": "sensor.kitchen",
             "bind": {"attribute": "sensor"}},
            {"target": "speaker.row", "spot": "columns", "id": "temp", "kind": "column",
             "label": "Temp", "value": {"live": True}, "text": "–"},
            {"target": "screen.status", "spot": "top", "id": "warmest", "kind": "info",
             "label": "Warmest", "value": {"live": True}},
            {"target": "sidebar", "spot": "pages", "id": "climate", "kind": "page",
             "title": "Climate", "icon": "cloud", "sections": [
                {"title": "Readings", "elements": [
                    {"id": "every", "kind": "field", "type": "number", "label": "Read every",
                     "unit": "min", "bind": {"setting": "interval"}},
                    {"id": "now", "kind": "action", "label": "Read now"}]}]},
        ]

    async def ui_action(self, element_id, ref, value=None):
        if element_id == "now":
            await self.read_all()          # sends set_value("temp", "21.5°", speaker ref) per speaker
            return {"success": True, "message": "Read"}
        return {"success": False, "message": f"Unknown action: {element_id}"}
```

### HTTP

| Route | What |
|---|---|
| `GET /api/ui/targets` | Every target, its fixed parts, its open spots and their contracts, the facts |
| `GET /api/ui/contributions` | The switched-on plugins' accepted elements, their badges, their live values |
| `POST /api/ui/objects` | `{"refs": [{"kind", "id", "theme"}]}`: the plugin attributes of those objects the caller may see |
| `POST /api/ui/actions/{plugin_id}/{element_id}` | `{"ref", "value"}`: an action, or an action-bound field (`pg.use`) |

Fields bound to attributes save with `PUT /api/attributes/{kind}/{plugin_id}` (section 23),
fields bound to settings with `PUT /api/plugins/{id}/settings` (section 3). `GET /api/plugins`
lists each plugin's refused elements as `ui_warnings`.

A plugin that lists its permissions (section 28) adds to the screens only with `ui` in the
list; one that lists none may.

## 25. Cues: when a sound will be heard

Sonorium tells plugins, ahead of time, which sound will be heard on which speaker and
exactly when. Think of a theatre's stage manager calling "Lights, stand by... go": she
doesn't run the lights, she knows the script and the timing and calls each cue early enough
for the crew to act. Sonorium announces sounds; what a plugin does with them (a light, a
display, a fan, a bass shaker) is up to the plugin. Capability `timeline`, plugin API 1.2.

A network speaker plays seconds behind the mixer (its Sync, section 23). The cue leaves the
mixer with the sound but reaches the plugin at once, so a plugin has those seconds to get
ready: a flash can come a moment before the thunder is heard.

### Following cues

```python
async def on_enable(self):
    self.timeline.subscribe(self.on_cue)                    # every channel
    # self.timeline.subscribe(self.on_cue, channels=[channel_id])

async def on_cue(self, event):
    cue = event.data
    if event.type == "cue.scheduled" and cue["start"] == "intermittent":
        mine = cue["attributes"].get("file", {}).get(self.id) or cue["attributes"].get("track", {}).get(self.id)
        for speaker in cue["speakers"]:
            await self.plan(speaker["area"], speaker["audible_at"], mine)
    elif event.type == "cue.cancelled":
        await self.forget(cue["cue_id"])
```

An async handler runs on the event loop, a plain one in a worker thread. The subscription
ends when the plugin is switched off. `self.timeline.pending(channel=None)` lists the cues
scheduled and not yet ended; `self.timeline.unsubscribe()` ends every subscription.

Cues have their own queue for each subscriber (2048 waiting events, the oldest dropped when
full), apart from the general event bus, and a subscriber gets only the channels it asked
for.

### The events

| Event | When |
|---|---|
| `cue.scheduled` | A sound will be heard |
| `cue.cancelled` | It won't after all (`reason`: `stopped`, `paused`, `theme_changed`, `skipped`, `no_audio`, `not_played`) |
| `cue.ended` | Its last speaker finished it (`reason`: `finished`, or `stopped` when the channel stopped while it was heard) |

`cue.scheduled`:

```json
{"cue_id": "c81", "channel_id": "2c62267b", "name": "Storm", "theme_id": "storm",
 "start": "intermittent", "track": "Thunder/Close", "group": "Thunder", "file": "Thunder/Close.ogg",
 "duration_ms": 7400, "gain": 0.82, "mixer_at": 1791843380.120, "lead_ms": 500, "confirmed": false,
 "speakers": [{"speaker_id": "media_player.living_room", "area": "living_room", "sync_ms": 5000,
               "delay_source": "estimate", "audible_at": 1791843385.120}],
 "attributes": {"track": {"lights": {"cue": "flash"}}, "file": {"lights": {"colour": "#cfe0ff"}},
                "theme": {"panel": {"artwork": "storm.png"}}}}
```

- `mixer_at`: when the sound's first sample leaves the mixer; `audible_at`: when a speaker
  plays it, `mixer_at` plus its Sync. Unix seconds on the server's clock (milliseconds in the
  fraction). A plugin on the server needs nothing else; a device that reads the stream itself
  needs its clock set by NTP.
- `delay_source`: `setting` (the speaker's Sync), `device` (an output device's own latency,
  section 17) or `estimate` (the estimate for its kind). Estimates can be off by a second
  until the user sets the speaker's Sync.
- `attributes`: the plugin attributes (section 23) of the theme, the track and, for a file in
  a group, the file, each by plugin id. Every plugin's values are there: the cue is about the
  sound, not about one plugin.
- `speakers` is empty while the channel plays only to listeners (Listen here, the app) or its
  speakers are muted.

| `start` | What started | Lead |
|---|---|---|
| `intermittent` | An Intermittent track plays | The speaker's Sync, plus the Look-ahead |
| `loop` | A Background track starts a pass (and each loop) | Sync |
| `ebb` | An Ebb & Flow track's file starts a pass | Sync |
| `group` | A Merry-go-round group's next file | Sync |
| `preset`, `step`, `theme` | The channel's preset, sequence step or theme changed; `levels` has each track's new `{volume, on}`, `preset_id` (and `step`) | Sync |
| `sound` | A sound or announcement over the channel (`sound_id`, `label`); it ends with the sound | Sync |

A moment inside a file (a thunder clap 4.2 s in) has no cue of its own: add your offset to the
file's `audible_at`. Store such offsets as a file attribute.

### Look-ahead and confirmation

An Intermittent play is decided a set time before it starts and announced then: the
**Look-ahead** in Settings › Advanced › Plugins (default 0.5 s; `timeline_lookahead` in
`PUT /api/settings/playback-values`, seconds). The random choice is the same, only made
earlier, so a fast local speaker still gives warning. Such a cue has `confirmed: false` and a
`lead_ms`; when the play starts on time nothing more is sent. If it doesn't start (the
channel stopped, the track was muted or set to never, its theme changed), `cue.cancelled`
follows. Tracks in an Intermittent group are announced as they start: the group's turn is
decided only when it comes.

### Outside programs

| Route | What |
|---|---|
| `GET /api/timeline?channels=a,b` | The events and their fields, the Look-ahead, the cues pending |
| `GET /api/timeline/stream?channels=a,b` | Server-Sent Events, framed as `/api/events/stream` (section 4) |

Both take what `/api/events/stream` takes: a key with "Read events", a signed-in user, or the
events token. A personal channel's cues go only to its account's streams, never to plugins.

## 26. Devices of any kind

A plugin can bring a new kind of device (a light, a display, a fan, a button panel) and
Sonorium lists, names, places and assigns it without knowing what it is. Think of a hotel's
front desk: it keeps every guest's name and room, checked in or out, without knowing their
job. Capability `devices`, plugin API 1.2.

Sonorium knows one kind itself: speakers, because only audio goes through the mixer. A
plugin that plays audio adds an output type (section 17) and its devices are speakers, in
Settings › Speakers. Every other kind is a name to Sonorium.

### Declaring a kind

In `manifest.json` (`"device_kinds"`), from `get_device_kinds()`, or with
`self.devices.register_kind(...)` once the plugin is on:

```json
"device_kinds": [
  {"id": "light", "title": "Lights", "icon": "lightbulb", "latency_ms": 20,
   "actions": [{"id": "test", "label": "Flash"}, {"id": "identify", "label": "Identify"}]}
]
```

| Key | Meaning |
|---|---|
| `id` | a-z, 0-9 and `_`, starting with a letter (up to 32). One plugin per kind. `speaker`, `audio`, `output` are refused: those are speakers |
| `title` | The section's title (up to 24 characters) |
| `icon` | An icon name or a small SVG, as for pages |
| `actions` | Up to 6 buttons on each device, `{id, label}` (label up to 24) |
| `latency_ms` | How long a command takes to show (0 to 600000): for timing against cues (section 25) |
| `one_channel` | A device follows one channel at most |

A kind outside these rules is refused and listed on the plugin's row in Settings › Plugins,
with the reason; the plugin's other kinds still count. Every limit is in `core/devices.py`
(`LIMITS`).

```python
async def discover_devices(self, kind):
    return [{"id": "192.168.10.40", "name": "Porch strip", "model": "Strip 2"}]   # "area": a suggestion

async def device_action(self, kind, device, action, params):
    await self.flash(device["device"], params.get("ms", 60))     # device["device"]: your own id
    return {"success": True, "message": "Flashed"}
```

`discover_devices` is asked when the plugin is switched on and on Rescan (15 s limit).

### What Sonorium does for every device

- Remembers it by a stable id, `dev:<kind>:<plugin id>:<16 hex>`, even while it is offline,
  and forgets it on Remove (offline devices only) or when the plugin is uninstalled.
- Shows it on the plugin's row in Settings › Plugins, a section per kind: online or offline,
  its name (the plugin's until the user renames it), its model, its area, the kind's action
  buttons, its Use switch, and in its ⋯ menu Rename, the channels and areas it follows,
  and the plugin's own fields for it.
- Online means it was in the plugin's last listing; the plugin can also say so, with an
  error to show: `self.devices.set_status(device, online=False, error="No answer")`.
- Keeps plugin attributes on it: object class `device` (section 23), for example a
  segment number, with fields in its ⋯ menu (target `device.row`, section 24).

### Following channels

A device follows the channels it is assigned to, and any channel playing on a speaker in one
of its areas. A plugin asks which devices follow a channel:

```python
for light in await self.devices.assigned(channel_id, kind="light"):   # switched on only
    ...   # light["follows_by"]: "channel" or "area"
```

`self.devices.following(device_id)` gives the channels one device follows now.

### Plugins working together

A plugin can list and drive another plugin's devices: `self.devices.list("light")` and
`await self.devices.invoke(device_id, "flash", {"ms": 60})`, which Sonorium passes to the
device's own plugin. So a "storm effects" plugin can flash any light, whoever made the
light plugin. What each action means is agreed between plugins, not by Sonorium, in these
written contracts:

| Kind | Actions | Params |
|---|---|---|
| `light` | `on`, `off`, `colour`, `brightness`, `flash` | `colour`: `"#rrggbb"`; `brightness`: 0 to 100; `flash`: `ms`, `colour` |
| `display` | `show_text`, `show_image`, `clear` | `text`; `url` or `path`; none |
| `input` | none: it sends its codes to a command map (section 18) | |
| `sensor` | `read` | returns `data`: `{"value", "unit"}` |

A plugin may offer more actions than its kind's contract; others should only rely on these.

### The rest of the API

| `self.devices` | What |
|---|---|
| `register_kind(kind)`, `unregister_kinds()` | Kinds while the plugin runs |
| `kinds()` | Every kind registered now |
| `await discover(kind=None)` | List the plugin's own devices again |
| `list(kind=None)`, `get(device_id)` | Devices of kinds registered now |
| `set_status(device, online=None, error=None)` | One of its own devices (core id or its own id) |

Events: `device.added`, `device.removed`, `device.changed`, `device.assigned`,
`device.available`, `device.unavailable` (section 4).

| Route | Permission | What |
|---|---|---|
| `GET /api/plugin-devices/kinds` | Speakers: view | The kinds registered now |
| `GET /api/plugin-devices?kind=&plugin=` | Speakers: view | The devices |
| `POST /api/plugin-devices/discover?kind=` | Speakers: view | Rescan |
| `PUT /api/plugin-devices/{id}` | Speakers: change settings | `{name, area, use, channels, areas}` |
| `DELETE /api/plugin-devices/{id}` | Speakers: change settings | Forget an offline device |
| `POST /api/plugin-devices/{id}/actions/{action}` | Speakers: change settings | `{params}`: one of its actions |
| `GET /api/sessions/{id}/plugin-devices` | Channels: view | The devices following a channel |

## 27. Listening to the audio

A plugin can hear what a channel plays (a level meter on a display, lights pulsing with the
rain) and read a theme's files (finding the loud moments in a thunder file). Both are
read-only: they can never slow or change playback. Capabilities `audio.tap` and
`audio.decode`, plugin API 1.2.

### Taps

```python
def on_block(block):                        # in the tap's own thread
    level = float(np.abs(block.samples).max()) / 32768
    ...

tap = self.audio.tap(channel_id, on_block)                  # the channel's mix
tap = self.audio.tap(channel_id, on_block, track="Rain")    # one track, before mixing
tap.close()                                                 # or self.audio.untap()
```

Each block is a `TapBlock`:

| Field | What |
|---|---|
| `samples` | numpy int16, shape (1, 1024), mono, 44.1 kHz, read-only: the same array for every listener |
| `mixer_sample` | Its first sample on the channel's sample clock (from when the channel started making audio) |
| `mixer_at` | When it left the mixer (Unix seconds, server clock): the clock cues use (section 25) |
| `channel_id`, `track` | Where it comes from (`track` None for the mix) |

- The mix is what goes to the speakers: after fades, volume ramps and sounds over it. A tap
  reads it the way a speaker does, in its own thread. A handler that is slower than the
  audio falls behind alone; the blocks it missed are counted in `tap.dropped`.
- A tap is not a listener: it never keeps a channel playing. It follows its channel when it
  stops and starts again.
- A track tap keeps a copy of each block of that track (a queue of about 1.5 s, the oldest
  dropped), so ask for one only when you need it.
- An `async def` handler runs on the event loop, one block at a time, at most 5 s each.
- Taps close when the plugin is switched off. At most 16 per plugin. Every limit is in
  `core/taps.py` (`LIMITS`).

A tap gives no warning ahead: what it hears is already on its way to the speakers. For
something that must happen before a sound is heard, use cues (section 25).

### Decoding a theme's files

```python
pcm = await self.audio.decode(theme_id, "Thunder/Close")              # numpy float32, -1..1
pcm = await self.audio.decode(theme_id, "Thunder/Close", max_seconds=30)
```

Mono, 44.1 kHz, decoded by Sonorium (no FFmpeg in the plugin), in a thread. Only the theme's
own audio files can be read, by track key; anything else is `ActionError` `not_found`. Up to
30 minutes by default (`decode_max_seconds` in `LIMITS`). Store what you find as a file
attribute (section 23), and add it to the file's `audible_at` when its cue comes.

## 28. Permissions

Each plugin lists what it uses; the user sees that list before the plugin first runs; and
Sonorium's services refuse calls outside it. Like a phone asking "Allow this app to use the
microphone?".

```json
"permissions": ["channels.control", "timeline", "devices.light", "attributes.track", "ui", "network.lan"]
```

| Permission | Lets it | The user sees |
|---|---|---|
| `channels.control` | Play, stop and change channels (`self.actions`), volume ramps, sending channels to its outputs | Control your channels |
| `themes.write` | `self.new_theme()`, `self.open_theme()` | Change your themes |
| `audio.sounds` | Sounds and announcements, text to speech, sound gates | Play sounds and announcements |
| `audio.outputs` | Output types (section 17) | Add speakers |
| `audio.tap` | Taps and decoding (section 27) | Listen to what plays |
| `timeline` | Cues (section 25) | Know when sounds play |
| `devices`, `devices.<kind>` | Device kinds, and driving other plugins' devices of that kind (section 26) | Add devices (: light) |
| `attributes`, `attributes.<class>` | Its own values on objects (section 23) | Keep its own values on ... |
| `ui` | Adding to the screens (section 24) | Add to the screens |
| `http` | Its own routes (section 29) | Answer requests at its own address |
| `ha.entities` | Home Assistant entities (section 30) | Add entities to Home Assistant |
| `homeassistant` | Home Assistant's API (section 20) | Use your Home Assistant connection |
| `mqtt` | Publishing and subscribing (section 19) | Use the MQTT broker |
| `commands` | Handing codes to command maps (section 18) | Take commands from remotes and buttons |
| `packages` | Python packages (section 21) | Install Python packages |
| (from `requirements`) | Each package it installs, added to the list for you | Install the Python package pyserial==3.5 |
| `network.lan`, `network.internet` | Shown to the user; not enforced | Talk to devices on your network; Reach the internet |

Events, settings, pages, connections, the scheduler and webhook triggers need no permission.

- **Nothing runs before the approval.** Think of a parcel held at the door: Sonorium reads
  the label (`manifest.json`) but doesn't open the box. Until the user approves, `plugin.py`
  is not imported, so none of its code runs (not even top-level lines), and none of its
  packages are installed. Settings › Plugins lists it, switched off, with what it asks for.
- **Switching on.** A plugin from outside Sonorium isn't switched on until the user approves
  its list: Settings › Plugins shows "Allow “Lights” to:" with the list, packages and their
  versions included, when it is switched on. After **Allow**, in this order: its packages
  are installed (section 21; the Plugin packages switch still applies), `plugin.py` is
  imported, `on_load` and then `on_enable` run. `GET /api/plugins/{id}/permissions`,
  `PUT /api/plugins/{id}/permissions` (`{"approve": true}`; Admin).
  `PUT /api/plugins/{id}/enable` answers 409 until then.
- **Updates.** An update that asks for more (a permission, a new package, a package at
  another version) stays off and isn't loaded; its row lists what it asks for, and the alert
  bar says so, until the user approves again. Its new packages are installed only after
  that. Asking for less needs nothing.
- **No list.** A plugin without `"permissions"` says nothing about what it uses: the user is
  asked to allow everything a plugin can do. Plugins that were already switched on before
  permissions existed keep running, and packages already installed for an approved plugin
  count as approved.
- **Built-in plugins** need no approval: they ship inside Sonorium, so they are trusted like
  the rest of it, packages included (the Plugin packages switch still applies). A plugin
  counts as built in when its folder holds the exact copy bundled with this Sonorium, file
  for file. `"builtin": true` in a manifest doesn't make a plugin built in, and a newer copy
  of a built-in plugin from the catalog or an upload asks like any other plugin.
- **Refused calls** raise `ActionError` with code `forbidden` (HTTP 403) naming the
  permission to add. A device kind, screen additions, routes or entities it didn't list are
  left out and listed on its row.

**Honest limit.** Plugins are Python running inside Sonorium. The checks are made by
Sonorium's services, so they stop mistakes and show intent; a plugin written to cheat could
go around them. Install plugins you trust.

## 29. A plugin's own HTTP routes

A plugin can answer requests at its own address, `/api/plugins/{id}/http/...`, each route
with the ability a caller needs (the abilities of keys and roles: `ch.view`, `pg.use`, ...,
or `admin.plugins`). Sonorium's sign-in checks the ability before the plugin is called.
Needs the permission `http`.

```json
"http": [
  {"id": "status", "path": "strips/{strip}", "methods": ["GET"], "ability": "ch.view"},
  {"id": "flash", "path": "flash", "methods": ["POST"], "ability": "pg.use"}
]
```

```python
async def http_request(self, route_id, request):
    # request: method, path, params ({"strip": ...}), query, json, body (bytes), caller {kind, user_id, role}
    if route_id == "status":
        return {"status": 200, "json": {"strip": request["params"]["strip"], "on": True}}
    return {"status": 204}
```

| Answer | Sent as |
|---|---|
| `{"status", "json"}` | `application/json` |
| `{"status", "text"}` | `text/plain` |
| `{"status", "body", "content_type"}` | bytes: JSON, text, CSV, PNG, JPEG, GIF, WebP, MP3, OGG, WAV, FLAC or octet-stream |

Never a web page or a script: HTML, SVG, XML and JavaScript are refused (502), so nothing a
plugin sends can run with the signed-in user's rights. Every answer carries `nosniff` and a
sandboxing Content-Security-Policy. A path the plugin doesn't declare, or any path while it
is switched off, is 404. Ids, paths (`a-z0-9_.-` and `{name}` segments), up to 20 routes,
1 MB requests, 4 MB answers and 30 s per call: `plugins/http_routes.py` (`LIMITS`). A route
that breaks the rules is left out and listed on the plugin's row.

For a device or service outside Sonorium that needs no sign-in, use a webhook trigger
(section 7) with its token.

## 30. Home Assistant entities

A plugin can declare entities and Sonorium publishes them under its own device in Home
Assistant (MQTT discovery), so the plugin needs no Home Assistant code. Needs the permission
`ha.entities` and a connected MQTT broker.

```json
"ha_entities": [
  {"id": "lights", "type": "switch", "name": "Storm lights", "icon": "mdi:weather-lightning",
   "bind": {"setting": "enabled"}},
  {"id": "distance", "type": "number", "name": "Storm distance", "min": 0, "max": 5, "step": 0.5,
   "unit": "km", "bind": {"setting": "distance"}},
  {"id": "flash", "type": "button", "name": "Flash now", "bind": {"action": "flash"}},
  {"id": "mode", "type": "select", "name": "Light mode", "options": ["mood", "storm"], "bind": {"action": "mode"}},
  {"id": "online", "type": "sensor", "name": "Strips online", "bind": {"live": true}}
]
```

| Bind | What happens |
|---|---|
| `setting` | One of its settings (section 3): a switch shows a boolean, a select a select, a number a number, a sensor any. A change from Home Assistant is checked against the schema and saved (`on_settings_changed` follows); a change in Sonorium shows in Home Assistant |
| `action` | `ha_action(action, value)` is called (a button: `None`); returning `{"value": ...}` sets the entity's state |
| `live` | Read-only: the plugin sets the state with `await self.ha_set_state("online", 2)` |

Types: `switch`, `select`, `number`, `sensor`, `button`. Entity ids are `a-z0-9_`; the
Home Assistant ids are `<type>.sonorium_plugin_<plugin>_<id>`. The entities appear while the
plugin is on and leave Home Assistant when it is switched off. Up to 20 per plugin; every
limit in `plugins/ha_entities.py` (`LIMITS`). An entity outside the rules is left out and
listed on the plugin's row.

## 31. Automation: when, only if, do

Command maps (section 18) are Sonorium's rules: **when** a code comes in (a remote's button,
an MQTT topic, a webhook, anything a plugin hands over with `self.commands.received`),
**only if** every condition holds, **do** an action. There is no separate rules screen;
Home Assistant, which is a rule engine, can use a plugin's actions through its entities
(section 30). Capability `automation`, plugin API 1.2.

A plugin adds its own pieces as data, next to Sonorium's:

```json
"automation": {
  "actions": [{"id": "flash", "title": "Flash the lights",
               "args": {"ms": {"type": "number", "label": "Length", "min": 20, "max": 2000, "default": 60}}},
              {"id": "dim", "title": "Dim the channel's lights", "channel": true}],
  "conditions": [{"id": "dark", "title": "It's dark outside"},
                 {"id": "weather", "title": "The weather is",
                  "args": {"kind": {"type": "select", "label": "Weather", "options": ["rain", "storm"]}}}]
}
```

```python
async def automation_action(self, action, channel, args, value=None):
    await self.flash(args.get("ms", 60))            # value: what came with the code
    return {"success": True}

async def automation_condition(self, condition, channel, args):
    return self.is_dark()                            # 2 s at most; an error counts as false
```

- In a map their ids are `<plugin id>:<id>`; `GET /api/command-actions` and
  `GET /api/command-conditions` list Sonorium's pieces first, then the plugins' (with their
  `plugin_id`). Argument types: `number`, `string`, `url`, `time` (HH:MM), `select`, `boolean`.
- Sonorium's conditions: The channel is playing, The channel is stopped, The time is between.
- An entry's `"only_if"`: up to 5 conditions, `[{"condition", "channel", "args"}]`; the
  entry runs only when all of them hold. In a map on a plugin's page they are under ⋯ ›
  Only if…, and ⋯ carries a dot while an entry has any.
- A plugin switched off keeps the entries that use its pieces: they don't run, and its
  conditions don't hold, until it is on again.
- What the user mapped runs as the user set it: an input plugin's own permissions (section 28)
  don't limit the actions of its map.
- Up to 20 actions and 20 conditions per plugin, 3 arguments each, 30 s per action: every
  limit is in `plugins/automation.py` (`LIMITS`). A piece outside the rules is left out and
  listed on the plugin's row.

## Not part of the framework yet

These were planned and are left until a real plugin needs them:

- **Sound sources** (audio a plugin supplies, played like a file or as a channel's content).
  They need a track that isn't a file in the theme format, the Theme Editor and the mixer.
- **Effects** that change the audio before the speakers get it: the one piece that could
  stall playback.
- **Sandboxed plugin views**: a plugin's own web page in a locked box. Data contributions
  (section 24) cover the screens.
- **A process per plugin** (a real sandbox): plugins run inside Sonorium (section 11).

