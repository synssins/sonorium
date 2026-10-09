"""
Recent log messages kept in memory for the web UI's Logs page.

Attached as soon as logging is set up, so messages from a startup that fails
part-way are still there to read. No imports from the rest of Sonorium, so
nothing else breaking can stop it working.
"""
import itertools
import logging
import threading
import time
from collections import deque

MAX_ENTRIES = 2000


class LogBuffer(logging.Handler):
    """Keeps the last MAX_ENTRIES log records as plain dicts."""

    def __init__(self, app_logger: str, max_entries: int = MAX_ENTRIES):
        super().__init__(level=logging.DEBUG)
        self._app_logger = app_logger
        self._entries: deque = deque(maxlen=max_entries)
        self._seq = itertools.count(1)
        self._entries_lock = threading.Lock()

    def wanted(self, record: logging.LogRecord) -> bool:
        """Sonorium's own messages at its configured level; other libraries' warnings and errors."""
        name = record.name or ""
        if name == self._app_logger or name.startswith(self._app_logger + "."):
            return record.levelno >= logging.getLogger(self._app_logger).getEffectiveLevel()
        return record.levelno >= logging.WARNING

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not self.wanted(record):
                return
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + logging.Formatter().formatException(record.exc_info)
            entry = {
                "time": time.strftime("%H:%M:%S", time.localtime(record.created)) + f".{int(record.msecs):03d}",
                "level": record.levelname,
                "source": record.name,
                "message": message,
            }
            with self._entries_lock:
                entry["seq"] = next(self._seq)
                self._entries.append(entry)
        except Exception:
            self.handleError(record)

    def entries(self, after: int = 0) -> list[dict]:
        with self._entries_lock:
            return [e for e in self._entries if e["seq"] > after]


_buffer: LogBuffer | None = None


def install(app_logger: str, request_filter: logging.Filter | None = None) -> LogBuffer:
    """Attach the buffer to the root logger once (safe to call again)."""
    global _buffer
    if _buffer is None:
        _buffer = LogBuffer(app_logger)
        if request_filter is not None:
            _buffer.addFilter(request_filter)
        logging.getLogger().addHandler(_buffer)
    return _buffer


def attach(logger_name: str) -> None:
    """Also collect from a logger that doesn't pass messages up to the root (uvicorn's)."""
    if _buffer is not None and _buffer not in logging.getLogger(logger_name).handlers:
        logging.getLogger(logger_name).addHandler(_buffer)


def recent(after: int = 0) -> list[dict]:
    return _buffer.entries(after) if _buffer else []


def as_text(entries: list[dict]) -> str:
    return "".join(f"{e['time']} {e['level']:<8} {e['source']}: {e['message']}\n" for e in entries)
