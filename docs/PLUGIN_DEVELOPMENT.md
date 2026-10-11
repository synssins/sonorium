# Writing a Sonorium plugin

A plugin adds a feature to Sonorium without changing Sonorium: a connector to another home
system, a scheduler, a chat bot, an importer. It is a folder with a Python file. Sonorium
loads it, and through a set of services the plugin can follow what happens (events), act on
channels (the actions API), run at set times (the scheduler), be called from other devices
(webhook triggers), send messages (notifications) and add pages and settings to the web UI
(extension points).

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
| `triggers`, `notifiers`, `pages`, `connections` | Declarations (sections 7-10), if the class doesn't give them. |

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

Import only from `sonorium.plugins.*` (base, actions, events, scheduler). Everything else in
Sonorium is internal and changes without notice.

## 2. Lifecycle

| Method | When |
|---|---|
| `async on_load()` | The plugin was loaded (Sonorium starts, or plugins were reloaded). |
| `async on_enable()` | The user enabled it (also at start-up if it was enabled). Start things here. |
| `async on_disable()` | The user disabled it. Stop what you started. |
| `async on_unload()` | Sonorium is unloading it. |

When a plugin is disabled, Sonorium removes its event subscriptions and notifiers for it.
When it is uninstalled, its scheduled jobs, trigger tokens and saved connection values go
too. A plugin that fails in `on_enable` stays disabled.

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
        "notify": {"type": "boolean", "label": "Send a notification when it plays", "default": False},
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
| `channel.stopped` | A channel stopped | `reason`: `stopped`, `paused`, `idle` (no speaker pulled audio for 90 s) or `ended` (a playlist without repeat finished) |
| `channel.changed` | A channel's settings changed | `changes`: only what changed, any of `theme_id`, `preset_id`, `sequence_id`, `playlist_id`, `volume`, `speakers_muted`, `name`, `speakers` |
| `sequence.step` | A sequence moved to a step (also step 1 when it starts) | `theme_id`, `sequence_id`, `step`, `steps`, `preset_id` |
| `playlist.entry` | A playlist moved to an entry | `playlist_id`, `position`, `entries`, `theme_id` |
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
| `notification` | A message was sent with `notify()` | `title`, `body`, `level`, `data` |
| `error` | Something failed that nobody was waiting for | `where` (`event_handler`, `schedule`, `trigger`, `notifier`, `channel.start`, a plugin method), `message`, `plugin_id` |

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

Each event arrives as `id: <n>`, `event: <type>`, `data: <JSON>`. A comment line is sent
every 15 s to keep the connection open. After a reconnect, the `Last-Event-ID` header (or
`?last_event_id=`) replays the events since then that Sonorium still holds (the last 200).
`?max_events=N` closes the stream after N events.

```sh
curl -N -H "Authorization: Bearer $TOKEN" "http://sonorium.local:8008/api/events/stream?types=channel.*"
```

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

## 8. Notifications

**Sending.** Any plugin can send a message:

```python
self.notify("Door open", "The back door opened while the alarm was on", level="warning")
```

`level` is `info`, `warning` or `error`. The message becomes a `notification` event, and the
user's **notification rules** decide where it goes, the same way as for any other event.

**Delivering.** A notifier plugin delivers messages somewhere (Discord, a phone, e-mail). It
declares its notifiers and delivers:

```python
def get_notifiers(self):
    return [{"id": "main", "title": "Discord #home"}]

async def deliver_notification(self, notifier_id, notification):
    # notification: title, body, level, time, origin, event (the event as a dict, or None)
    await post_to_discord(notification["title"], notification["body"])   # raise if it fails
```

Its notifiers are available as `<plugin_id>.<notifier_id>` while the plugin is enabled.

**Rules** connect events to notifiers:

```
PUT /api/notifications/rules/{rule_id}
{"name": "Problems", "events": ["error", "speaker.unavailable"], "notifiers": ["discord.main"],
 "min_level": "warning", "enabled": true}
```

`events` are patterns as in section 4. `min_level` skips quieter events: `error` events are
`error`, `speaker.unavailable` and `speaker.removed` are `warning`, a `notification` has the
level it was sent with, everything else is `info`. For an event, the message title is a short
summary ("Kitchen started") and the body its description.

| Endpoint | |
|---|---|
| `GET /api/notifications` | Notifiers, rules and levels |
| `PUT /api/notifications/rules` `{"rules": [...]}` | Replace all rules |
| `PUT` / `DELETE /api/notifications/rules/{rule_id}` | Add or change, or remove one rule |
| `POST /api/notifications/test` `{"notifier": id}` | Send a test message; answers with the result |

A notifier that fails (or takes over 30 s) is reported as an `error` event with
`where: notifier`; that event is never routed to notifiers again, so a broken notifier can't
start a loop.

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
| `button` | `action`, `label`, `style`: `primary`, `secondary`, `danger`, `confirm` (a question asked first) |
| `status` | `state`: `ok`, `warning`, `error`, `off`, `unknown`; `text` |

A button sends the values of all the page's fields. Sonorium checks them against the fields
before calling `page_action` (and refuses actions the page doesn't show). An item it doesn't
know is left out. A result may set `"refresh": false` to keep the page as it is.

**Icons** are a name (`alarm`, `mdi:alarm-clock`) or a small inline SVG (at most 8 KB, no
scripts, event attributes, links or `foreignObject`; others are dropped).

## 10. Connections

A plugin that talks to another system (SmartThings, Hubitat, openHAB, a Discord bot, Google
Drive) adds a section to Settings > Connections for its address and credentials:

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
  `plugin_framework.json` (jobs, notification rules, plain connection values, the scheduler
  time zone) and `plugin_secrets.json` (token hashes, secret connection values; created
  readable by its owner only).
- **The management endpoints** (making tokens, rules, jobs, connection values) are part of
  Sonorium's API and need an Admin: Home Assistant's sidebar, or a signed-in Admin on the
  direct ports once the Users plugin's Require login is on (docs/AUTH.md). The trigger and
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
- **Catalog:** plugins offered in Settings > Plugins > Catalog are listed in
  [`plugins/catalog.json`](../plugins/catalog.json) with their zip next to it:
  `{"id", "name", "description", "version", "author", "category", "zip_file"}`.
  Raise `version` for every release: Sonorium offers an update when the catalog's version
  differs from the installed one.

## 14. Built-in plugins: sign-in hooks and web UI slots

Built-in plugins (shipped in `sonorium/plugins/builtin/`, like **Users** and
**Integration**) can go further than the schema-rendered pages above: they can recognise
callers, add HTTP routes and draw parts of the web UI. Third-party plugins keep the
schema-rendered pages, triggers, events and actions.

A built-in plugin is copied to the config folder's `plugins/` and replaced only when the
bundled `version` in `manifest.json` is newer: **raise it with every change**.

### Manifest fields

| Field | Meaning |
|---|---|
| `"builtin": true` | Shipped with Sonorium |
| `"enabled_by_default": true` | Switched on the first time Sonorium sees it, never again after the user switches it off |
| `"auth_provider": true` | Guards sign-in (Users). While it's switched on but hasn't started, only streams and public routes answer; switching it off or uninstalling it asks first |

### The sign-in service (`self._host.auth`)

The core's `AuthService` (`sonorium/auth/service.py`). In `on_enable`:

```python
service = self._host.auth
service.add_routes(self.id, create_router(service), PERMISSIONS)       # APIRouter + a permission per route
service.register_ui(self.id, self.plugin_dir / "web", js="x.js", css="x.css", version=self.version)
service.register_authenticator(self.id, MyAuth(), provider=False)
```

and `service.unregister(self.id)` in `on_disable` (the host does it too). While the plugin is
off, its routes answer 404.

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
| `channelCard` | `{badges(session), strip(session)}` | Next to a channel's name, and under its now-playing line |
| `channelEditor` | `{render(session), save(sessionId, session)}` | Fields at the bottom of the channel editor; `save` runs after the channel is saved |

`me` is `GET /api/auth/me`: `kind`, `user`, `perms`, `csrf`, plus the plugins' fields.
Helpers: `Sonorium.modal({id, title, body, footer, cls, onClose})`, `Sonorium.closeModal(id)`,
`Sonorium.showScreen(html)`, `Sonorium.api(method, path, body)`, `Sonorium.esc(text)`,
`Sonorium.toast(message, type)`, `Sonorium.can(perm)`. `fetch` already adds the CSRF token to
changes and reloads the page when the sign-in is lost.

Follow `docs/UI_STYLE.md`; structural screens are drawn as boards and approved first.
