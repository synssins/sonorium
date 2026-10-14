"""
What the program that runs Sonorium (its host) can do for it, switched on by
environment variables the host sets. Sonorium never checks which system it
runs on: Docker, the Home Assistant app, a Windows service or a plain
`python -m sonorium.entrypoint` all use the same switches (docs/HOSTING.md).

    SONORIUM_SUPERVISED=1    a restart ends Sonorium with exit code 75
                             (EX_TEMPFAIL) after a clean shutdown; the host
                             starts it again. Without it Sonorium restarts
                             itself in place (os.execv) after the same shutdown.

Restarting (and stopping) always goes the same way: request_restart() asks
the web server to stop, its shutdown stops channels, plugins and sign-in, and
entrypoint.main() then calls finish() to exit or re-execute. A watchdog makes
sure it happens even if the shutdown hangs.
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Callable, Optional

# The exit code that asks the host to start Sonorium again (sysexits.h EX_TEMPFAIL)
RESTART_EXIT_CODE = 75
# How long a clean shutdown may take before Sonorium exits anyway
SHUTDOWN_TIMEOUT_SECONDS = 20.0

_TRUE = {"1", "true", "yes", "on"}

_pending: Optional[str] = None          # "restart" once asked
_stopper: Optional[Callable[[], None]] = None
_watchdog: Optional[threading.Timer] = None
_lock = threading.Lock()


def supervised() -> bool:
    """A host restarts Sonorium when it exits with RESTART_EXIT_CODE."""
    return (os.environ.get("SONORIUM_SUPERVISED") or "").strip().lower() in _TRUE


def set_stopper(stop: Optional[Callable[[], None]]) -> None:
    """The web server's "stop now" (web_server.serve sets it while it runs)."""
    global _stopper
    _stopper = stop


def pending() -> Optional[str]:
    """"restart" once a restart was asked for, else None."""
    return _pending


def restart_argv() -> list[str]:
    """What an in-place restart executes: this Python, starting Sonorium again."""
    return [sys.executable, "-m", "sonorium.entrypoint"]


def _log(message: str) -> None:
    try:
        from sonorium.obs import logger
        logger.info(message)
    except Exception:
        print(message, flush=True)


def request_restart(reason: str = "") -> bool:
    """
    Stop the web server cleanly, then restart (finish()). False if a restart is
    already on its way. Call it from the event loop, after the answer is sent.
    """
    global _pending, _watchdog
    with _lock:
        if _pending is not None:
            return False
        _pending = "restart"
    how = (f"exiting with code {RESTART_EXIT_CODE} so the host starts it again" if supervised()
           else "restarting in place")
    _log(f"Restarting Sonorium{f' ({reason})' if reason else ''}: shutting down, then {how}")
    _watchdog = threading.Timer(SHUTDOWN_TIMEOUT_SECONDS, _shutdown_timed_out)
    _watchdog.daemon = True
    _watchdog.start()
    stop = _stopper
    if stop is None:  # no web server running (tests, or start-up failed): restart straight away
        finish()
        return True
    stop()
    return True


def _shutdown_timed_out() -> None:
    _log(f"Shutting down took longer than {SHUTDOWN_TIMEOUT_SECONDS:.0f} seconds; restarting anyway")
    finish()


def _flush() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass


def finish() -> None:
    """
    After the shutdown (or the watchdog): exit with RESTART_EXIT_CODE when a host
    supervises Sonorium, else execute it again in place. Does nothing if no
    restart was asked for. Never returns otherwise.
    """
    if _pending != "restart":
        return
    _flush()
    if supervised():
        # os._exit: worker threads still running (speaker discovery, casts) can't hold the exit up
        os._exit(RESTART_EXIT_CODE)
    argv = restart_argv()
    os.execv(argv[0], argv)


def _reset_for_tests() -> None:
    global _pending, _stopper, _watchdog
    if _watchdog is not None:
        _watchdog.cancel()
    _pending = None
    _stopper = None
    _watchdog = None
