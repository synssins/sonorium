"""
Alerts: Sonorium's own serious problems, shown as a small bar at the top of
the web UI until someone dismisses it (GET /api/alerts).

Problems are written to the log at their level (the Logs page); only the
ones where something has really gone wrong are also reported here: a plugin
that failed or couldn't be loaded, a scheduled job or trigger handler that
failed, a channel that couldn't start. Each alert links to its log entry
(log_seq: the Logs page's entry number).

These are not notifications: notifications are what outside things need
attention for (plugins/notifications.py).

No imports from the rest of Sonorium, so it works whatever else failed.
"""
from __future__ import annotations

import itertools
import logging
import threading
import time
from collections import deque
from typing import Optional

MAX_ALERTS = 50
REPEAT_SECONDS = 300   # the same message again within this long updates the alert instead of adding one

_alerts: deque = deque(maxlen=MAX_ALERTS)
_ids = itertools.count(1)
_lock = threading.Lock()
_log = logging.getLogger("sonorium")


def _last_log_seq() -> Optional[int]:
    try:
        from sonorium import logbuffer
        entries = logbuffer.recent()
        return entries[-1]["seq"] if entries else None
    except Exception:
        return None


def report(message: str, where: str = "", log: bool = True) -> dict:
    """Log the problem as an error and show it in the alert bar. Returns the alert."""
    message = str(message)[:500]
    if log:
        _log.error(message)
    seq = _last_log_seq()
    now = time.time()
    with _lock:
        for alert in _alerts:
            if alert["message"] == message and not alert["dismissed"] and now - alert["last"] < REPEAT_SECONDS:
                alert.update(last=now, count=alert["count"] + 1, log_seq=seq or alert["log_seq"])
                return dict(alert)
        alert = {"id": next(_ids), "message": message, "where": where, "time": now, "last": now, "count": 1,
                 "log_seq": seq, "dismissed": False}
        _alerts.append(alert)
        return dict(alert)


def active() -> list[dict]:
    with _lock:
        return [dict(a) for a in _alerts if not a["dismissed"]]


def dismiss(alert_id: int) -> bool:
    with _lock:
        for alert in _alerts:
            if alert["id"] == alert_id and not alert["dismissed"]:
                alert["dismissed"] = True
                return True
    return False


def dismiss_all() -> int:
    with _lock:
        count = 0
        for alert in _alerts:
            if not alert["dismissed"]:
                alert["dismissed"] = True
                count += 1
        return count
