"""The Logs page's buffer: Sonorium's messages at its level, other libraries' warnings, newest after a seq."""

import importlib.util
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium"


def load():
    spec = importlib.util.spec_from_file_location("logbuffer_under_test", ROOT / "logbuffer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(name, level, msg):
    return logging.LogRecord(name, level, __file__, 1, msg, None, None)


def test_keeps_app_messages_at_its_level_and_library_warnings():
    module = load()
    logging.getLogger("sonorium_t").setLevel(logging.INFO)
    buf = module.LogBuffer("sonorium_t")
    buf.handle(record("sonorium_t", logging.DEBUG, "hidden debug"))
    buf.handle(record("sonorium_t.core", logging.INFO, "app info"))
    buf.handle(record("aiohttp", logging.INFO, "library chatter"))
    buf.handle(record("aiohttp", logging.WARNING, "library warning"))
    assert [e["message"] for e in buf.entries()] == ["app info", "library warning"]


def test_after_returns_only_newer_entries_and_buffer_is_bounded():
    module = load()
    logging.getLogger("sonorium_t").setLevel(logging.INFO)
    buf = module.LogBuffer("sonorium_t", max_entries=3)
    for i in range(5):
        buf.handle(record("sonorium_t", logging.INFO, f"m{i}"))
    entries = buf.entries()
    assert [e["message"] for e in entries] == ["m2", "m3", "m4"]
    assert [e["message"] for e in buf.entries(after=entries[1]["seq"])] == ["m4"]


def test_text_export_includes_level_and_source():
    module = load()
    text = module.as_text([{"time": "12:00:00.000", "level": "ERROR", "source": "sonorium", "message": "boom"}])
    assert text == "12:00:00.000 ERROR    sonorium: boom\n"
