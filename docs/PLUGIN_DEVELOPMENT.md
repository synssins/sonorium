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
| `api_version` | The plugin API it was written for, e.g. `"1.1"` (none: `"1.0"`) |
| `requires` | Capabilities it can't work without, e.g. `["audio.sounds", "mqtt"]` |
| `requirements` | Python packages from PyPI, e.g. `["pyserial==3.5"]` |
| `binaries` | Programs it ships, per platform, with their SHA-256 |
| `command_maps` | Declarations, if the class doesn't give them |

## 1a. API versions and capabilities

The plugin API has a version, MAJOR.MINOR (now **1.1**). Adding a service or a field raises
the minor version; a plugin written for 1.0 keeps running on 1.1 and every later 1.x,
because nothing it uses is renamed or removed. The major version goes up only for a change
old plugins would notice; Sonorium then keeps the old facades next to the new ones for as
long as it can.

Sonorium reads `api_version` and `requires` before it loads any of the plugin's code. A
plugin that needs a newer API, another major version, or a capability this install doesn't
offer is refused with a sentence that says why, for example:

> Remote needs plugin API 1.3; this Sonorium has 1.1. Update Sonorium to use it.

A refused plugin is listed in Settings › Plugins with that sentence (`compatible: false`,
`problem` in `GET /api/plugins`), the web UI shows an alert, and none of its code runs.

| Capability | Since | What it is |
|---|---|---|
| `events`, `actions`, `scheduler`, `triggers`, `pages`, `connections` | 1.0 | Sections 4 to 10 |
| `audio.sounds` | 1.1 | Sounds over channels, announcements (section 15) |
| `audio.ramps` | 1.1 | Volume ramps (section 16) |
| `audio.outputs` | 1.1 | Output types (section 17) |
| `audio.tts` | 1.1 | Text to speech providers (section 15) |
| `commands` | 1.1 | Command maps (section 18) |
| `mqtt` | 1.1 | MQTT (section 19) |
| `homeassistant` | 1.1 | Home Assistant's API (section 20) |
| `packages.pip`, `packages.binaries` | 1.1 | Packages and programs (section 21) |

`GET /api/plugin-framework` gives the version, every capability and the ones this install
offers now.

Each 1.1 service is a facade on the plugin: `self.audio`, `self.ramps`, `self.audio_outputs`,
`self.commands`, `self.mqtt`, `self.homeassistant`. Every method that can fail raises
`ActionError` (`code` `invalid`, `not_found`, `conflict` or `unavailable`), as `self.actions`
does.

## 2. Lifecycle

| Method | When |
|---|---|
| `async on_load()` | The plugin was loaded (Sonorium starts, or plugins were reloaded). |
| `async on_enable()` | The user enabled it (also at start-up if it was enabled). Start things here. |
| `async on_disable()` | The user disabled it. Stop what you started. |
| `async on_unload()` | Sonorium is unloading it. |

When a plugin is disabled, Sonorium removes its event subscriptions, MQTT
subscriptions, output types, text to speech providers and sound gates. When it is
uninstalled, its scheduled jobs, trigger tokens, saved connection values, command maps and
Python packages go too. A plugin that fails in `on_enable` stays disabled.

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
| `schedule.fired` | A scheduled job ran | `plugin_id`, `job_id`, `name`, `data`, `scheduled_for`, `missed` |
| `trigger.received` | A webhook trigger was called (and authenticated) | `plugin_id`, `trigger`, `payload`, `method` |
| `error` | Something failed that nobody was waiting for | `where` (`event_handler`, `schedule`, `trigger`, `channel.start`, a plugin method), `message`, `plugin_id` |

| `sound.started` / `sound.ended` | A sound started or ended over a channel (section 15) | `sound_id`, `label`, `origin`; ended: `reason` (`finished`, `replaced`, `cancelled`, `stopped`, `error`), `capped`, `played` |
| `ramp.started` / `ramp.finished` / `ramp.cancelled` | A channel's volume ramp (section 16) | `ramp_id`, `from`, `to`, `seconds`, `curve`, `then`; `level`; cancelled: `reason` |
| `command.received` | An input plugin handed over a code (section 18) | `plugin_id`, `map_id`, `code`, `actions`, `learned` |

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
| `list_speakers()`, `list_speaker_groups()` | Speakers (`id`, `name`, `type`, `online`, `area_id`, `area_name`, `enabled`) and speaker groups |
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
- **Every API route has a permission.** A plugin's own HTTP routes (built-in plugins only,
  see section 14) list theirs; the server refuses an API route that has none.
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
| `"builtin": true` | Shipped with Sonorium |
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

A channel normally goes to speakers that pull its stream over the network. An output type
sends it somewhere else: the sound card of the machine Sonorium runs on, a Bluetooth
speaker, a file. The plugin registers the type; Sonorium asks it for its devices, and for a
sink when a channel is sent to one.

```python
from sonorium.core.audio_outputs import OutputType, Sink


class HostAudio(OutputType):
    id = "host_audio"
    title = "This computer"

    def available(self):
        try:
            import sounddevice
            sounddevice.query_devices()
            return True, ""
        except Exception as e:
            return False, f"No sound card here ({e})"

    async def discover(self):
        import sounddevice
        return [{"id": str(i), "name": d["name"]} for i, d in enumerate(sounddevice.query_devices())
                if d["max_output_channels"] > 0]

    def open(self, device_id, sample_rate, options):
        return HostSink(int(device_id), sample_rate)


class HostSink(Sink):
    latency = 0.1

    def __init__(self, device, rate):
        import sounddevice
        self.stream = sounddevice.OutputStream(device=device, samplerate=rate, channels=1, dtype="int16")
        self.stream.start()

    def write(self, block):            # int16, shape (1, n), mono, at sample_rate
        self.stream.write(block.reshape(-1, 1))     # may block: it runs in its own thread

    def close(self):
        self.stream.stop()
        self.stream.close()


class HostAudioPlugin(BasePlugin):
    id = "host_audio"
    ...
    async def on_enable(self):
        self.audio_outputs.register_type(HostAudio())
```

That is the first example, not part of Sonorium: `sounddevice` isn't in every install (the
plugin brings it with `"requirements": ["sounddevice"]`, section 21). Where it can work:

| Install | Host audio |
|---|---|
| Windows, Linux, macOS (native) | Yes |
| Docker on Linux | With the sound device passed in (`/dev/snd`) or the host's sound server's socket |
| Home Assistant app | Only with `audio: true` in the app's config (false today): the Supervisor's audio |

Sending a channel to a device: `POST /api/sessions/{id}/audio-outputs {"type", "device"}`
(the mix starts if it isn't running, as for Listen here), `DELETE .../{attachment_id}`. A
channel's outputs are remembered and start again when it plays. Each one reads the
channel's stream like a speaker: it keeps the channel from going idle, it hears the
speakers-muted switch, and a slow sink only falls behind itself. A channel still needs a
speaker for Play; one sent only to an output starts when it is sent there.
`sonorium.core.audio_outputs.CaptureOutput` is a test double that keeps what it receives.

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

- Off until Settings › Advanced › **Plugin packages** is switched on (then restart). A
  package comes from the internet and runs with Sonorium's rights, like the plugin. A
  plugin that needs packages while the switch is off is refused with that sentence.
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

The renderers are one module each in `web/static/js/ext/`, styled in `web/static/css/ext.css`.
