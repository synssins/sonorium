"""Plugin framework scheduler: kinds, cron, time zones, persistence, missed jobs, handlers."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from playback_helpers import addon_modules

m = SimpleNamespace()
UTC = timezone.utc


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.plugins.scheduler", "sonorium.plugins.store") as mods:
        m.__dict__.update(mods.__dict__)
        yield


def has_zone(name):
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return True
    except Exception:
        return False


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def make(tmp_path, now, dispatch=None, enabled=lambda pid: True, timezone_name="UTC"):
    store = m.store.FrameworkStore(tmp_path)
    published = []
    clock = Clock(now)
    scheduler = m.scheduler.Scheduler(store, dispatch=dispatch, is_enabled=enabled,
                                      publish=lambda t, d, **k: published.append((t, d)), now=clock)
    if timezone_name is not None:
        store.main.section("settings")["timezone"] = timezone_name
    return SimpleNamespace(s=scheduler, store=store, published=published, clock=clock)


NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)  # a Sunday


# --- Cron and kinds -------------------------------------------------------------

@pytest.mark.parametrize("cron, expected", [
    ("30 7 * * 1-5", datetime(2026, 10, 12, 7, 30, tzinfo=UTC)),       # next weekday morning
    ("*/15 * * * *", datetime(2026, 10, 11, 12, 15, tzinfo=UTC)),
    ("0 0 29 2 *", datetime(2028, 2, 29, 0, 0, tzinfo=UTC)),           # leap day
    ("0 12 * * sun", datetime(2026, 10, 18, 12, 0, tzinfo=UTC)),       # strictly after now
    ("0 9 1 * mon", datetime(2026, 10, 12, 9, 0, tzinfo=UTC)),         # day OR weekday, like cron
    ("5 4 * jan,jul *", datetime(2027, 1, 1, 4, 5, tzinfo=UTC)),
])
def test_cron_next(cron, expected):
    assert m.scheduler.Cron.parse(cron).next_after(NOW, UTC) == expected


@pytest.mark.parametrize("bad", ["", "* * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "*/0 * * * *",
                                 "1-x * * * *", "5-1 * * * *"])
def test_cron_rejects(bad):
    with pytest.raises(m.scheduler.ScheduleError):
        m.scheduler.Cron.parse(bad)


def test_kinds_next_times(tmp_path):
    t = make(tmp_path, NOW)
    s = t.s
    assert s.add("p", "daily", "daily", {"time": "07:30"})["next_run"] == "2026-10-12T07:30:00+00:00"
    weekly = s.add("p", "weekly", "weekly", {"time": "18:00", "days": ["Friday", "mon", "fri"]})
    assert weekly["spec"]["days"] == ["mon", "fri"] and weekly["next_run"] == "2026-10-12T18:00:00+00:00"
    once = s.add("p", "once", "once", {"at": "2026-12-24T07:30"})
    assert once["next_run"] == "2026-12-24T07:30:00+00:00"
    interval = s.add("p", "every", "interval", {"seconds": 900})
    assert interval["next_run"] == "2026-10-11T12:15:00+00:00"
    aligned = s.add("p", "aligned", "interval", {"seconds": 3600, "start": "2026-10-11T09:20:00+00:00"})
    assert aligned["next_run"] == "2026-10-11T12:20:00+00:00"


@pytest.mark.parametrize("kind, spec, fragment", [
    ("hourly", {}, "Unknown kind"),
    ("once", {"at": "2020-01-01T00:00"}, "never runs"),
    ("once", {"at": "soon"}, "ISO"),
    ("daily", {"time": "25:00"}, "time of day"),
    ("weekly", {"time": "07:00", "days": []}, "days"),
    ("weekly", {"time": "07:00", "days": ["someday"]}, "Unknown day"),
    ("interval", {"seconds": 1}, "at least"),
    ("cron", {"cron": "bad"}, "5 fields"),
])
def test_add_rejects(tmp_path, kind, spec, fragment):
    t = make(tmp_path, NOW)
    with pytest.raises(m.scheduler.ScheduleError, match=fragment):
        t.s.add("p", "job", kind, spec)


def test_add_rejects_bad_ids_and_options(tmp_path):
    t = make(tmp_path, NOW)
    with pytest.raises(m.scheduler.ScheduleError):
        t.s.add("p", "bad id!", "daily", {"time": "07:00"})
    with pytest.raises(m.scheduler.ScheduleError):
        t.s.add("p", "j", "daily", {"time": "07:00"}, missed="always")
    with pytest.raises(m.scheduler.ScheduleError):
        t.s.add("p", "j", "daily", {"time": "07:00"}, data=["x"])


# --- Time zones -------------------------------------------------------------------

@pytest.mark.skipif(not has_zone("America/New_York"), reason="no IANA time zone data here")
def test_time_zone_and_daylight_saving(tmp_path):
    t = make(tmp_path, datetime(2026, 3, 7, 13, 0, tzinfo=UTC), timezone_name="America/New_York")  # 08:00 EST
    job = t.s.add("p", "wake", "daily", {"time": "07:30"})
    assert job["next_run"] == "2026-03-08T11:30:00+00:00"  # 07:30 EDT (DST began 02:00 that day)
    t.clock.now = datetime(2026, 3, 8, 5, 0, tzinfo=UTC)  # midnight EST, two hours before the change
    gap = t.s.add("p", "gap", "daily", {"time": "02:30"})  # doesn't exist that day: 03:30 EDT
    assert gap["next_run"] == "2026-03-08T07:30:00+00:00"
    t.s.set_timezone("UTC")
    assert t.s.get("p", "wake")["next_run"] == "2026-03-08T07:30:00+00:00"


def test_unknown_time_zone_is_refused(tmp_path):
    t = make(tmp_path, NOW)
    with pytest.raises(m.scheduler.ScheduleError):
        t.s.set_timezone("Mars/Olympus_Mons")


def test_naive_times_follow_the_tz_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    t = make(tmp_path, NOW, timezone_name=None)
    assert t.s.add("p", "o", "once", {"at": "2026-10-11T13:00"})["next_run"] == "2026-10-11T13:00:00+00:00"


# --- Running ----------------------------------------------------------------------

def test_due_jobs_fire_publish_and_move_on(tmp_path):
    fired = []

    async def dispatch(job):
        fired.append((job["id"], job["scheduled_for"], job["missed"]))

    t = make(tmp_path, NOW, dispatch=dispatch)
    t.s.add("p", "daily", "daily", {"time": "12:30"}, data={"channel": "c1"}, name="Lunch")
    t.s.add("p", "once", "once", {"at": "2026-10-11T12:10"})

    async def main():
        t.clock.now = NOW + timedelta(minutes=20)
        ran = await t.s.tick()
        await t.s.wait_running()
        assert [j["id"] for j in ran] == ["once"]
        t.clock.now = NOW + timedelta(minutes=31)
        await t.s.tick()
        await t.s.wait_running()
    asyncio.run(main())
    assert fired == [("once", "2026-10-11T12:10:00+00:00", False), ("daily", "2026-10-11T12:30:00+00:00", False)]
    assert t.s.get("p", "once") is None  # a one-time job is gone after it ran
    daily = t.s.get("p", "daily")
    assert daily["next_run"] == "2026-10-12T12:30:00+00:00" and daily["last_status"] == "ok"
    events = [d for kind, d in t.published if kind == "schedule.fired"]
    assert events[1] == {"plugin_id": "p", "job_id": "daily", "name": "Lunch", "data": {"channel": "c1"},
                         "scheduled_for": "2026-10-11T12:30:00+00:00", "missed": False}


def test_jobs_survive_a_restart(tmp_path):
    t = make(tmp_path, NOW)
    t.s.add("p", "daily", "daily", {"time": "07:30"})
    saved = json.loads((tmp_path / "plugin_framework.json").read_text())
    assert saved["schedules"]["p"]["daily"]["spec"] == {"time": "07:30"}
    again = make(tmp_path, NOW, timezone_name=None)
    assert [j["id"] for j in again.s.list("p")] == ["daily"]


def test_missed_jobs_skip_or_run_once(tmp_path):
    fired = []
    t = make(tmp_path, NOW, dispatch=lambda job: fired.append((job["id"], job["missed"])))
    t.s.add("p", "skip", "daily", {"time": "13:00"})
    t.s.add("p", "catch", "daily", {"time": "13:00"}, missed="run_once")
    t.s.add("p", "late_once", "once", {"at": "2026-10-11T13:00"})
    # Sonorium was down from 12:00 until two days later at 09:00
    restarted = make(tmp_path, NOW + timedelta(days=2, hours=-3), dispatch=t.s.dispatch, timezone_name=None)
    asyncio.run(restarted.s.catch_up())
    assert fired == [("catch", True)]  # once, not once per missed day
    assert restarted.s.get("p", "skip")["next_run"] == "2026-10-13T13:00:00+00:00"
    assert restarted.s.get("p", "skip")["last_status"] == "missed"
    assert restarted.s.get("p", "catch")["next_run"] == "2026-10-13T13:00:00+00:00"
    assert restarted.s.get("p", "late_once") is None
    missed = [d for kind, d in restarted.published if kind == "schedule.fired"]
    assert missed[0]["missed"] is True


def test_disabled_plugin_jobs_move_on_without_running(tmp_path):
    fired = []
    t = make(tmp_path, NOW, dispatch=lambda job: fired.append(job["id"]), enabled=lambda pid: False)
    t.s.add("p", "j", "interval", {"seconds": 60})
    t.clock.now = NOW + timedelta(seconds=61)
    asyncio.run(t.s.tick())
    assert fired == [] and t.s.get("p", "j")["last_status"] == "skipped"
    assert t.s.get("p", "j")["next_run"] == "2026-10-11T12:02:01+00:00"


def test_a_failing_handler_is_recorded_and_published(tmp_path):
    async def dispatch(job):
        raise RuntimeError("no speaker")

    t = make(tmp_path, NOW, dispatch=dispatch)
    t.s.add("p", "j", "interval", {"seconds": 60})

    async def main():
        t.clock.now = NOW + timedelta(seconds=60)
        await t.s.tick()
        await t.s.wait_running()
    asyncio.run(main())
    job = t.s.get("p", "j")
    assert job["last_status"] == "error" and job["last_error"] == "no speaker"
    assert ("error", {"where": "schedule", "message": "no speaker", "plugin_id": "p", "job_id": "j"}) in t.published


def test_remove_disable_and_plugin_cleanup(tmp_path):
    t = make(tmp_path, NOW)
    t.s.add("p", "a", "daily", {"time": "07:00"})
    t.s.add("p", "b", "daily", {"time": "08:00"})
    t.s.add("q", "c", "daily", {"time": "09:00"})
    assert t.s.set_enabled("p", "a", False)["next_run"] is None
    assert t.s.set_enabled("p", "a", True)["next_run"] is not None
    assert t.s.remove("p", "a") and not t.s.remove("p", "a")
    assert t.s.remove_plugin("p") == 1
    assert [j["id"] for j in t.s.list()] == ["c"]


def test_background_loop_runs_due_jobs(tmp_path):
    fired = []
    real_now = datetime.now(UTC)
    t = make(tmp_path, real_now, dispatch=lambda job: fired.append(job["id"]))
    t.s.now = lambda: datetime.now(UTC)
    t.s.add("p", "soon", "once", {"at": (real_now + timedelta(milliseconds=200)).isoformat()})

    async def main():
        await t.s.start()
        for _ in range(100):
            await asyncio.sleep(0.02)
            if fired:
                break
        await t.s.stop()
    asyncio.run(main())
    assert fired == ["soon"]
