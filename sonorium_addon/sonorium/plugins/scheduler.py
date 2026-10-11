"""
Persistent scheduler for plugins.

A plugin registers jobs; each job belongs to one plugin and has an id the
plugin chooses. Jobs are kept in plugin_framework.json, so they survive
restarts. When a job is due, Sonorium calls the plugin's on_schedule(job)
(or a callback the plugin registered for that job while it runs) and
publishes "schedule.fired".

Kinds (the "spec" of a job):
  once      {"at": "2026-12-24T07:30"}           one time; removed after it ran
  daily     {"time": "07:30"}                    every day
  weekly    {"time": "07:30", "days": ["mon", "fri"]}
  cron      {"cron": "30 7 * * 1-5"}             minute hour day-of-month month day-of-week
  interval  {"seconds": 900, "start": "..."}     every N seconds (at least 10), from start

Times without a time zone are in the scheduler's time zone: the one set in
the framework settings, else the TZ environment variable (the container's),
else the system's. Daylight saving: a time that doesn't exist that day (the
hour skipped in spring) runs at the same clock time after the change; a time
that happens twice (autumn) runs once.

Missed jobs (due while Sonorium was not running): "missed": "skip" (the
default) moves on to the next time; "run_once" runs the job once at start-up
(the event says "missed": true), then moves on.

New kinds (sunrise/sunset is planned) register with register_kind().
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Awaitable, Callable, Optional

from sonorium.obs import logger

UTC = timezone.utc
MIN_INTERVAL_SECONDS = 10
MAX_JOBS_PER_PLUGIN = 200
MISSED_GRACE_SECONDS = 60  # due this recently at start-up: not "missed", just runs
HANDLER_TIMEOUT_SECONDS = 300
MISSED_POLICIES = ("skip", "run_once")
JOB_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

DAY_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
MONTH_NAMES = {name: i + 1 for i, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}


class ScheduleError(ValueError):
    """A job that can't be scheduled (bad kind, spec or id)."""


# --- Time zones ---

def resolve_timezone(name: Optional[str] = None) -> tzinfo:
    """A time zone by IANA name (or the TZ environment variable, or the system's)."""
    name = (name or os.environ.get("TZ") or "").strip()
    if name:
        if name.upper() in ("UTC", "Z", "ETC/UTC"):
            return UTC
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            logger.warning(f"Scheduler: time zone '{name}' is not known here, using the system time zone")
    return datetime.now().astimezone().tzinfo or UTC


def check_timezone(name: str) -> str:
    """The name if it is a known IANA time zone ("" = automatic); ScheduleError if not."""
    name = (name or "").strip()
    if not name or name.upper() == "UTC":
        return name
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
    except Exception:
        raise ScheduleError(f"Unknown time zone '{name}'")
    return name


def parse_datetime(value, tz: tzinfo) -> datetime:
    """An ISO date-time; without an offset it is in tz. Returned in UTC."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            raise ScheduleError(f"'{value}' is not an ISO date and time (e.g. 2026-12-24T07:30)")
    else:
        raise ScheduleError("A date and time is needed")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(UTC)


def parse_clock(value) -> tuple[int, int]:
    """'HH:MM' -> (hour, minute)."""
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value) if isinstance(value, str) else None
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ScheduleError(f"'{value}' is not a time of day (HH:MM, 24-hour)")
    return int(match.group(1)), int(match.group(2))


# --- Cron ---

def _cron_field(text: str, low: int, high: int, names: dict) -> tuple[set, bool]:
    """The values of one cron field and whether it was '*'."""
    values = set()
    text = text.strip().lower()
    if not text:
        raise ScheduleError("Empty cron field")
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 1:
                raise ScheduleError(f"Bad cron step '{step_text}'")
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = _cron_value(a, names), _cron_value(b, names)
        else:
            start = _cron_value(part, names)
            end = high if step > 1 else start
        if not (low <= start <= high and low <= end <= high) or start > end:
            raise ScheduleError(f"Cron value out of range in '{text}' ({low}-{high})")
        values.update(range(start, end + 1, step))
    return values, text == "*"


def _cron_value(text: str, names: dict) -> int:
    text = text.strip()
    if text in names:
        return names[text]
    if not text.isdigit():
        raise ScheduleError(f"Bad cron value '{text}'")
    return int(text)


@dataclass
class Cron:
    minutes: list
    hours: list
    days: set
    months: set
    weekdays: set  # 0 = Sunday
    any_day: bool
    any_weekday: bool

    @classmethod
    def parse(cls, text: str) -> "Cron":
        if not isinstance(text, str) or len(text.split()) != 5:
            raise ScheduleError("A cron expression has 5 fields: minute hour day-of-month month day-of-week")
        m, h, dom, mon, dow = text.split()
        minutes, _ = _cron_field(m, 0, 59, {})
        hours, _ = _cron_field(h, 0, 23, {})
        days, any_day = _cron_field(dom, 1, 31, {})
        months, _ = _cron_field(mon, 1, 12, MONTH_NAMES)
        weekdays, any_weekday = _cron_field(dow, 0, 7, DAY_NAMES)
        if 7 in weekdays:
            weekdays = (weekdays - {7}) | {0}
        return cls(sorted(minutes), sorted(hours), days, months, weekdays, any_day, any_weekday)

    def day_matches(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        dom_ok = day.day in self.days
        dow_ok = (day.weekday() + 1) % 7 in self.weekdays
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return dow_ok
        if self.any_weekday:
            return dom_ok
        return dom_ok or dow_ok  # both restricted: either one (as cron does)

    def next_after(self, after: datetime, tz: tzinfo) -> Optional[datetime]:
        """The first time after `after` (aware), in UTC; None if there is none within 5 years."""
        local = after.astimezone(tz)
        first_day = local.date()
        for offset in range(0, 366 * 5):
            day = first_day + timedelta(days=offset)
            if not self.day_matches(day):
                continue
            for hour in self.hours:
                if offset == 0 and hour < local.hour:
                    continue
                for minute in self.minutes:
                    if offset == 0 and hour == local.hour and minute < local.minute:
                        continue
                    candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).astimezone(UTC)
                    if candidate > after:
                        return candidate
        return None


# --- Kinds ---

@dataclass
class Kind:
    validate: Callable[[dict, tzinfo], dict]  # spec -> clean spec; ScheduleError if wrong
    next_after: Callable[[dict, datetime, tzinfo], Optional[datetime]]  # None: never again


KINDS: dict[str, Kind] = {}


def register_kind(name: str, validate, next_after) -> None:
    """Add a kind of schedule (e.g. sunrise/sunset)."""
    KINDS[name] = Kind(validate, next_after)


def _once_validate(spec, tz):
    return {"at": parse_datetime(spec.get("at"), tz).isoformat()}


def _once_next(spec, after, tz):
    at = datetime.fromisoformat(spec["at"])
    return at if at > after else None


def _cron_validate(spec, tz):
    Cron.parse(spec.get("cron"))
    return {"cron": " ".join(spec["cron"].split())}


def _cron_next(spec, after, tz):
    return Cron.parse(spec["cron"]).next_after(after, tz)


def _daily_validate(spec, tz):
    hour, minute = parse_clock(spec.get("time"))
    return {"time": f"{hour:02d}:{minute:02d}"}


def _daily_next(spec, after, tz):
    hour, minute = parse_clock(spec["time"])
    return Cron.parse(f"{minute} {hour} * * *").next_after(after, tz)


def _weekly_validate(spec, tz):
    hour, minute = parse_clock(spec.get("time"))
    days = spec.get("days")
    if not isinstance(days, list) or not days:
        raise ScheduleError("'days' must list days: mon, tue, wed, thu, fri, sat, sun")
    clean = []
    for day in days:
        key = str(day).strip().lower()[:3]
        if key not in DAY_NAMES:
            raise ScheduleError(f"Unknown day '{day}'")
        if key not in clean:
            clean.append(key)
    order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    return {"time": f"{hour:02d}:{minute:02d}", "days": sorted(clean, key=order.index)}


def _weekly_next(spec, after, tz):
    hour, minute = parse_clock(spec["time"])
    days = ",".join(str(DAY_NAMES[d]) for d in spec["days"])
    return Cron.parse(f"{minute} {hour} * * {days}").next_after(after, tz)


def _interval_validate(spec, tz):
    seconds = spec.get("seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < MIN_INTERVAL_SECONDS:
        raise ScheduleError(f"'seconds' must be a number of at least {MIN_INTERVAL_SECONDS}")
    clean = {"seconds": seconds}
    if spec.get("start") not in (None, ""):
        clean["start"] = parse_datetime(spec["start"], tz).isoformat()
    return clean


def _interval_next(spec, after, tz):
    seconds = float(spec["seconds"])
    start = datetime.fromisoformat(spec["start"]) if spec.get("start") else None
    if start is None:
        return after + timedelta(seconds=seconds)
    if start > after:
        return start
    periods = int((after - start).total_seconds() // seconds) + 1
    return start + timedelta(seconds=periods * seconds)


register_kind("once", _once_validate, _once_next)
register_kind("daily", _daily_validate, _daily_next)
register_kind("weekly", _weekly_validate, _weekly_next)
register_kind("cron", _cron_validate, _cron_next)
register_kind("interval", _interval_validate, _interval_next)


# --- The scheduler ---

Dispatch = Callable[[dict], Optional[Awaitable[None]]]


class Scheduler:
    """
    store: a FrameworkStore (jobs in its "schedules" section)
    dispatch(job): runs a fired job's handler (the plugin's on_schedule)
    is_enabled(plugin_id): jobs of a disabled plugin don't run (they move on)
    publish(type, data): the event bus's publish
    now(): the time (aware, UTC); for tests
    """

    def __init__(self, store, dispatch: Optional[Dispatch] = None,
                 is_enabled: Callable[[str], bool] = lambda plugin_id: True,
                 publish: Optional[Callable] = None, now: Callable[[], datetime] = None):
        self.store = store
        self.dispatch = dispatch
        self.is_enabled = is_enabled
        self.publish = publish or (lambda *a, **k: None)
        self.now = now or (lambda: datetime.now(UTC))
        self._task: Optional[asyncio.Task] = None
        self._wake: Optional[asyncio.Event] = None
        self._running: set[asyncio.Task] = set()

    # --- Settings ---

    @property
    def timezone_name(self) -> str:
        return (self.store.main.section("settings").get("timezone") or "").strip()

    def set_timezone(self, name: str) -> str:
        """Set the time zone ("" = automatic). Every job's next time is worked out again."""
        name = check_timezone(name)
        self.store.main.section("settings")["timezone"] = name
        now = self.now()
        for job in self._all():
            if job.get("enabled", True):
                self._set_next(job, now)
        self.store.main.save()
        self._poke()
        return name

    def tz(self, job: Optional[dict] = None) -> tzinfo:
        return resolve_timezone((job or {}).get("timezone") or self.timezone_name)

    # --- Jobs ---

    def _jobs(self) -> dict:
        return self.store.main.section("schedules")

    def _all(self) -> list[dict]:
        return [job for jobs in self._jobs().values() for job in jobs.values()]

    def list(self, plugin_id: Optional[str] = None) -> list[dict]:
        jobs = self._all() if plugin_id is None else list(self._jobs().get(plugin_id, {}).values())
        return [dict(job) for job in sorted(jobs, key=lambda j: (j.get("next_run") or "~", j["id"]))]

    def get(self, plugin_id: str, job_id: str) -> Optional[dict]:
        job = self._jobs().get(plugin_id, {}).get(job_id)
        return dict(job) if job else None

    def add(self, plugin_id: str, job_id: str, kind: str, spec: Optional[dict] = None, *, name: str = "",
            data: Optional[dict] = None, missed: str = "skip", enabled: bool = True,
            timezone_name: Optional[str] = None) -> dict:
        """Add or replace a job. Returns it. ScheduleError if anything is wrong."""
        if not isinstance(job_id, str) or not JOB_ID.match(job_id):
            raise ScheduleError("Job id: 1-64 letters, digits, '_', '-' or '.'")
        if kind not in KINDS:
            raise ScheduleError(f"Unknown kind '{kind}' (one of: {', '.join(KINDS)})")
        if missed not in MISSED_POLICIES:
            raise ScheduleError(f"'missed' must be one of {', '.join(MISSED_POLICIES)}")
        if data is not None and not isinstance(data, dict):
            raise ScheduleError("'data' must be an object")
        if timezone_name:
            check_timezone(timezone_name)
        plugin_jobs = self._jobs().setdefault(plugin_id, {})
        if job_id not in plugin_jobs and len(plugin_jobs) >= MAX_JOBS_PER_PLUGIN:
            raise ScheduleError(f"A plugin can have at most {MAX_JOBS_PER_PLUGIN} jobs")
        tz = resolve_timezone(timezone_name or self.timezone_name)
        clean = KINDS[kind].validate(dict(spec or {}), tz)
        now = self.now()
        job = {
            "id": job_id, "plugin_id": plugin_id, "name": str(name or job_id)[:120], "kind": kind,
            "spec": clean, "data": dict(data or {}), "missed": missed, "enabled": bool(enabled),
            "timezone": timezone_name or None, "created": now.isoformat(), "next_run": None,
            "last_run": None, "last_status": None, "last_error": None,
        }
        self._set_next(job, now)
        if job["enabled"] and job["next_run"] is None:
            raise ScheduleError("That schedule never runs (a time in the past?)")
        plugin_jobs[job_id] = job
        self.store.main.save()
        self._poke()
        return dict(job)

    def remove(self, plugin_id: str, job_id: str) -> bool:
        jobs = self._jobs().get(plugin_id, {})
        if jobs.pop(job_id, None) is None:
            return False
        if not jobs:
            self._jobs().pop(plugin_id, None)
        self.store.main.save()
        self._poke()
        return True

    def remove_plugin(self, plugin_id: str) -> int:
        """Remove every job of a plugin (it was uninstalled). Returns how many."""
        jobs = self._jobs().pop(plugin_id, None) or {}
        if jobs:
            self.store.main.save()
        return len(jobs)

    def set_enabled(self, plugin_id: str, job_id: str, enabled: bool) -> Optional[dict]:
        job = self._jobs().get(plugin_id, {}).get(job_id)
        if job is None:
            return None
        job["enabled"] = bool(enabled)
        self._set_next(job, self.now())
        self.store.main.save()
        self._poke()
        return dict(job)

    def _set_next(self, job: dict, after: datetime) -> None:
        if not job.get("enabled", True):
            job["next_run"] = None
            return
        try:
            nxt = KINDS[job["kind"]].next_after(job["spec"], after, self.tz(job))
        except Exception as e:
            logger.warning(f"Scheduler: job {job['plugin_id']}/{job['id']} can't be scheduled: {e}")
            nxt = None
        job["next_run"] = nxt.isoformat() if nxt else None

    # --- Running ---

    async def start(self) -> None:
        """Deal with jobs missed while Sonorium was down, then run due jobs as they come."""
        await self.catch_up()
        if self._task is None or self._task.done():
            self._wake = asyncio.Event()
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def _poke(self) -> None:
        if self._wake is not None:
            try:
                self._wake.set()
            except RuntimeError:
                pass

    async def catch_up(self) -> list[dict]:
        """Jobs whose time passed while Sonorium was down: run once ("run_once") or skip. Returns those run."""
        now = self.now()
        ran = []
        changed = False
        for job in list(self._all()):
            due = job.get("next_run")
            if not job.get("enabled", True) or not due:
                continue
            due_at = datetime.fromisoformat(due)
            if (now - due_at).total_seconds() <= MISSED_GRACE_SECONDS:
                continue
            changed = True
            if job["missed"] == "run_once" and self.is_enabled(job["plugin_id"]):
                logger.info(f"Scheduler: {job['plugin_id']}/{job['id']} was due at {due}, running it now")
                self._fire(job, due_at, now, missed=True)
                ran.append(dict(job))
            else:
                logger.info(f"Scheduler: {job['plugin_id']}/{job['id']} was due at {due} (missed, skipped)")
                job["last_status"] = "missed"
                self._advance(job, now)
        if changed:
            self.store.main.save()
        return ran

    async def tick(self) -> list[dict]:
        """Run every job that is due. Returns the jobs run."""
        now = self.now()
        ran = []
        changed = False
        for job in list(self._all()):
            due = job.get("next_run")
            if not job.get("enabled", True) or not due:
                continue
            due_at = datetime.fromisoformat(due)
            if due_at > now:
                continue
            changed = True
            if not self.is_enabled(job["plugin_id"]):
                job["last_status"] = "skipped"
                job["last_error"] = "plugin disabled"
                self._advance(job, now)
                continue
            self._fire(job, due_at, now, missed=False)
            ran.append(dict(job))
        if changed:
            self.store.main.save()
        return ran

    def _advance(self, job: dict, now: datetime) -> None:
        """Move a job on after it ran (or was skipped); a one-time job is removed."""
        if job["kind"] == "once":
            self._jobs().get(job["plugin_id"], {}).pop(job["id"], None)
            if not self._jobs().get(job["plugin_id"]):
                self._jobs().pop(job["plugin_id"], None)
            return
        self._set_next(job, now)

    def _fire(self, job: dict, due_at: datetime, now: datetime, missed: bool) -> None:
        job["last_run"] = now.isoformat()
        job["last_status"] = "ok"
        job["last_error"] = None
        fired = dict(job, scheduled_for=due_at.isoformat(), missed=missed)
        self._advance(job, now)
        self.publish("schedule.fired", {
            "plugin_id": job["plugin_id"], "job_id": job["id"], "name": job["name"], "data": dict(job["data"]),
            "scheduled_for": due_at.isoformat(), "missed": missed,
        })
        if self.dispatch is None:
            return
        try:
            result = self.dispatch(fired)
        except Exception as e:
            self._failed(job, e)
            return
        if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
            task = asyncio.get_running_loop().create_task(self._await(job, result))
            self._running.add(task)
            task.add_done_callback(self._running.discard)

    async def _await(self, job: dict, awaitable) -> None:
        try:
            await asyncio.wait_for(awaitable, HANDLER_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._failed(job, e)

    def _failed(self, job: dict, error: Exception) -> None:
        message = "timed out" if isinstance(error, asyncio.TimeoutError) else str(error) or type(error).__name__
        logger.warning(f"Scheduler: job {job['plugin_id']}/{job['id']} failed: {message}")
        job["last_status"] = "error"
        job["last_error"] = message[:300]
        try:
            self.store.main.save()
        except Exception:
            pass
        self.publish("error", {"where": "schedule", "message": message, "plugin_id": job["plugin_id"],
                               "job_id": job["id"]})

    async def wait_running(self) -> None:
        """Wait for the handlers started so far (tests)."""
        if self._running:
            await asyncio.gather(*list(self._running), return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Scheduler: {e}")
            delay = 30.0
            upcoming = [datetime.fromisoformat(j["next_run"]) for j in self._all()
                        if j.get("enabled", True) and j.get("next_run")]
            if upcoming:
                delay = max(0.05, min(delay, (min(upcoming) - self.now()).total_seconds()))
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), delay)
            except asyncio.TimeoutError:
                pass
