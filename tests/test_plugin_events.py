"""
Plugin framework event bus: delivery, isolation of slow and failing
subscribers, threads, debouncing, and the events published from the real
code paths (session manager, playback runner, speaker registry, theme files).
"""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from playback_helpers import addon_modules

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.plugins.events", "sonorium.core.session_manager", "sonorium.core.state",
                       "sonorium.core.playback_runner", "sonorium.core.sequences", "sonorium.core.playlists",
                       "sonorium.core.theme_presets", "sonorium.ha.registry") as mods:
        m.__dict__.update(mods.__dict__)
        import test_sequences_playlists as runner_tests
        runner_tests.m.__dict__.update(mods.__dict__)
        yield


@pytest.fixture
def bus():
    new = m.events.EventBus()
    old = m.events.set_bus(new)
    yield new
    m.events.set_bus(old)


def types_of(bus):
    return [e.type for e in bus.history()]


# --- The bus itself -------------------------------------------------------------

def test_patterns_and_known_types():
    e = m.events
    assert e.matches("*", "channel.started")
    assert e.matches("channel.*", "channel.started") and not e.matches("channel.*", "theme.changed")
    assert e.matches("plugin.demo.*", "plugin.demo.played")
    assert e.is_known_type("channel.started") and e.is_known_type("plugin.demo.played")
    assert not e.is_known_type("channel.exploded") and not e.is_known_type("plugin.demo")
    assert e.plugin_event_type("demo", "played") == "plugin.demo.played"
    with pytest.raises(ValueError):
        e.plugin_event_type("demo", "Bad Name")
    for bad in ("", "a..b", "channel.*.x", "*.started", 5):
        with pytest.raises(ValueError):
            e.check_pattern(bad)


def test_every_catalogue_event_documents_its_fields():
    for name, entry in m.events.CATALOGUE.items():
        assert entry["description"] and isinstance(entry["fields"], dict) and entry["summary"], name


def test_async_and_plain_handlers_get_matching_events(bus):
    got_async, got_plain = [], []

    async def on_async(event):
        got_async.append(event.type)

    def on_plain(event):
        got_plain.append((event.type, threading.current_thread() is threading.main_thread()))

    async def main():
        bus.subscribe("channel.*", on_async)
        bus.subscribe(["theme.changed"], on_plain)
        bus.publish("channel.started", {"channel_id": "a"})
        bus.publish("theme.changed", {"folder": "x"})
        bus.publish("speaker.added", {"speaker_id": "s"})
        await bus.drain()
    asyncio.run(main())
    assert got_async == ["channel.started"]
    assert got_plain == [("theme.changed", False)]  # a plain handler runs off the event loop


def test_unknown_event_is_not_published(bus):
    assert bus.publish("channel.exploded", {}) is None
    assert bus.history() == []


def test_slow_subscriber_never_blocks_the_publisher_or_others(bus):
    fast = []

    async def slow(event):
        await asyncio.sleep(0.5)

    async def quick(event):
        fast.append(event.id)

    async def main():
        bus.subscribe("*", slow)
        bus.subscribe("*", quick)
        started = time.perf_counter()
        for _ in range(5):
            bus.publish("channel.started", {"channel_id": "a"})
        took = time.perf_counter() - started
        await asyncio.sleep(0.05)
        return took
    took = asyncio.run(main())
    assert took < 0.05
    assert len(fast) == 5


def test_failing_and_timed_out_handlers_publish_error_events(bus):
    errors = []

    async def broken(event):
        raise RuntimeError("boom")

    async def stuck(event):
        await asyncio.sleep(10)

    async def on_error(event):
        errors.append(event.data)

    async def main():
        bus.subscribe("channel.started", broken, owner="bad_plugin")
        bus.subscribe("channel.started", stuck, owner="stuck_plugin", timeout=0.05)
        bus.subscribe("error", on_error)
        bus.publish("channel.started", {"channel_id": "a"})
        await asyncio.sleep(0.2)
        await bus.drain()
    asyncio.run(main())
    by_plugin = {e["plugin_id"]: e for e in errors}
    assert "boom" in by_plugin["bad_plugin"]["message"]
    assert "timed out" in by_plugin["stuck_plugin"]["message"]
    assert all(e["where"] == "event_handler" for e in errors)


def test_full_queue_drops_the_oldest(bus):
    seen = []
    gate = asyncio.Event

    async def main():
        release = gate()

        async def handler(event):
            await release.wait()
            seen.append(event.data["n"])
        sub = bus.subscribe("channel.changed", handler, queue_size=3)
        for n in range(10):
            bus.publish("channel.changed", {"n": n})
        await asyncio.sleep(0.01)
        release.set()
        await bus.drain()
        return sub
    sub = asyncio.run(main())
    assert seen == [7, 8, 9]  # the queue holds 3: the oldest went
    assert sub.dropped == 7


def test_publish_from_another_thread_reaches_the_loop(bus):
    got = []

    async def handler(event):
        got.append((event.type, threading.current_thread() is threading.main_thread()))

    async def main():
        bus.subscribe("speaker.*", handler)
        thread = threading.Thread(target=lambda: bus.publish("speaker.added", {"speaker_id": "x"}))
        thread.start()
        thread.join()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if got:
                break
    asyncio.run(main())
    assert got == [("speaker.added", True)]


def test_unsubscribe_owner_and_close(bus):
    got = []

    async def handler(event):
        got.append(event.type)

    async def main():
        bus.subscribe("*", handler, owner="p1")
        sub = bus.subscribe("*", handler, owner="p2")
        assert bus.unsubscribe_owner("p1") == 1
        sub.close()
        bus.publish("channel.started", {})
        await bus.drain()
    asyncio.run(main())
    assert got == []


def test_publish_later_coalesces_quick_repeats(bus):
    async def main():
        for _ in range(5):
            bus.publish_later("k", 0.05, "theme.changed", lambda: {"folder": "x"})
        await asyncio.sleep(0.15)
    asyncio.run(main())
    assert types_of(bus) == ["theme.changed"]


def test_origin_comes_from_the_context(bus):
    token = m.events.current_origin.set("plugin:demo")
    try:
        event = bus.publish("channel.started", {})
    finally:
        m.events.current_origin.reset(token)
    assert event.origin == "plugin:demo"
    assert bus.publish("channel.started", {}).origin == "sonorium"


def test_history_filters(bus):
    first = bus.publish("channel.started", {})
    bus.publish("theme.changed", {})
    assert [e.type for e in bus.history(first.id)] == ["theme.changed"]
    assert [e.type for e in bus.history(0, ["channel.*"])] == ["channel.started"]


# --- Published from the real code paths -------------------------------------------

def test_channel_lifecycle_events(bus, tmp_path):
    from test_sequences_playlists import build, run
    t = build(tmp_path)
    assert types_of(bus) == ["channel.created"]
    run(t.manager.play(t.session.id))
    started = bus.history(0, ["channel.started"])[-1]
    assert started.data["channel_id"] == t.session.id and started.data["theme_id"] == "storm"
    assert started.data["speakers"] == ["media_player.x"]

    t.manager.update(t.session.id, theme_id="tavern")
    changed = bus.history(0, ["channel.changed"])[-1].data["changes"]
    assert changed["theme_id"] == "tavern" and changed["preset_id"] == "quiet"
    run(t.manager.set_volume(t.session.id, 12))
    assert bus.history(0, ["channel.changed"])[-1].data["changes"] == {"volume": 12}
    t.manager.set_speakers_muted(t.session.id, True)
    assert bus.history(0, ["channel.changed"])[-1].data["changes"] == {"speakers_muted": True}

    run(t.manager.stop(t.session.id))
    stopped = bus.history(0, ["channel.stopped"])
    assert [e.data["reason"] for e in stopped] == ["stopped"]
    run(t.manager.stop(t.session.id))  # already stopped: no second event
    assert len(bus.history(0, ["channel.stopped"])) == 1

    t.manager.delete(t.session.id)
    assert types_of(bus)[-1] == "channel.deleted"


def test_update_without_changes_publishes_nothing(bus, tmp_path):
    from test_sequences_playlists import build
    t = build(tmp_path)
    t.manager.update(t.session.id, volume=t.session.volume)
    assert bus.history(0, ["channel.changed"]) == []


def test_sequence_and_playlist_events(bus, tmp_path):
    from test_sequences_playlists import build, run, at
    t = build(tmp_path)
    run(t.manager.play(t.session.id))
    t.manager.start_sequence(t.session.id, "up_down")
    at(t, 13)
    steps = [(e.data["step"], e.data["preset_id"]) for e in bus.history(0, ["sequence.step"])]
    assert steps[:2] == [(1, "a"), (2, "b")]
    assert bus.history(0, ["channel.changed"])[0].data["changes"]["sequence_id"] == "up_down"

    t.store.playlists["pl"] = {"name": "Mix", "order": "in_order", "repeat": True, "play_min": 60, "play_max": 60,
                               "entries": [{"theme_id": "tavern"}, {"theme_id": "forest"}]}
    t.manager.set_playlist(t.session.id, "pl")
    entries = bus.history(0, ["playlist.entry"])
    assert entries[-1].data["playlist_id"] == "pl" and entries[-1].data["theme_id"] == "tavern"
    assert entries[-1].data["position"] == 1 and entries[-1].data["entries"] == 2


def test_play_failure_publishes_an_error(bus, tmp_path):
    from test_sequences_playlists import build, run
    t = build(tmp_path)
    t.session.theme_id = None
    assert run(t.manager.play(t.session.id)) is False
    error = bus.history(0, ["error"])[-1]
    assert error.data["where"] == "channel.start" and error.data["channel_id"] == t.session.id


def test_speaker_events_from_the_registry(bus):
    registry = m.registry.HARegistry("http://ha.invalid/api", "token")
    speakers = [{"entity_id": "net:cast:a", "name": "Kitchen", "type": "cast", "online": True}]
    registry.set_extra_speaker_source(lambda: list(speakers))
    registry._rebuild()
    assert bus.history() == []  # the first list at start-up is not "added"
    speakers.append({"entity_id": "net:cast:b", "name": "Desk", "type": "cast", "online": True})
    registry._rebuild()
    speakers[0] = dict(speakers[0], online=False)
    registry._rebuild()
    del speakers[1]
    registry._rebuild()
    assert [(e.type, e.data["speaker_id"]) for e in bus.history()] == [
        ("speaker.added", "net:cast:b"), ("speaker.unavailable", "net:cast:a"), ("speaker.removed", "net:cast:b")]


def test_theme_file_writes_publish_theme_changed(bus, tmp_path):
    folder = tmp_path / "rain"
    folder.mkdir()
    m.theme_presets.write_json_atomic(folder / "metadata.json", {"id": "rain-id", "name": "Rain"})
    staging = tmp_path / ".sonorium-new-abc"
    staging.mkdir()
    m.theme_presets.write_json_atomic(staging / "metadata.json", {"id": "x"})  # not a theme yet: no event
    events = bus.history(0, ["theme.changed"])
    assert [e.data for e in events] == [{"theme_id": "rain-id", "folder": "rain"}]
