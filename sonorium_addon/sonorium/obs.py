"""
Sonorium's log: one logger ("sonorium") writing to stdout, which the add-on
log and `docker logs` show, and to the buffer behind the web UI's Logs page.
The level comes from SONORIUM_LOG_LEVEL.
"""
import functools
import logging
import os
import re
import sys



class InstrumentedLogger(logging.Logger):
    """Logger with instrument decorator for method tracing."""

    def instrument(self, message_template: str = ""):
        """
        Decorator that logs entry to a function/method.

        Args:
            message_template: Format string that can reference {self}, {args}, etc.
        """
        def decorator(func):
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                # Format the message template with available context
                try:
                    # Try to format with self if it's a method
                    if args and hasattr(args[0], '__class__'):
                        msg = message_template.format(self=args[0], **kwargs)
                    else:
                        msg = message_template.format(**kwargs)
                except (KeyError, AttributeError, IndexError):
                    msg = message_template

                if msg:
                    self.debug(msg)
                return func(*args, **kwargs)

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                try:
                    if args and hasattr(args[0], '__class__'):
                        msg = message_template.format(self=args[0], **kwargs)
                    else:
                        msg = message_template.format(**kwargs)
                except (KeyError, AttributeError, IndexError):
                    msg = message_template

                if msg:
                    self.debug(msg)
                return await func(*args, **kwargs)

            # Return appropriate wrapper based on function type
            import asyncio
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper

        return decorator


class LevelPrefixFormatter(logging.Formatter):
    """Prefix warnings and errors with their level so they stand out in the add-on log."""

    def format(self, record):
        message = super().format(record)
        if record.levelno >= logging.WARNING:
            time_part, _, text = message.partition(" ")
            return f"{time_part} {record.levelname}: {text}"
        return message


class RequestLineFilter(logging.Filter):
    """
    HTTP request lines ("GET /api/sessions") arrive at info level from the API
    layer, one per UI poll. Show them only at debug level.
    """
    REQUEST = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) \S")

    def __init__(self, logger: logging.Logger):
        super().__init__()
        self._logger = logger

    def filter(self, record):
        if record.levelno == logging.INFO and self.REQUEST.match(str(record.msg)):
            return self._logger.isEnabledFor(logging.DEBUG)
        return True


def log_level_from_env() -> int:
    """Log level from SONORIUM_LOG_LEVEL (set from the add-on's log_level option); INFO by default."""
    name = os.environ.get("SONORIUM_LOG_LEVEL", "info").strip().upper()
    if name == "TRACE":
        name = "DEBUG"
    return logging.getLevelName(name) if isinstance(logging.getLevelName(name), int) else logging.INFO


def get_logger(name: str) -> InstrumentedLogger:
    """The named logger, set up on first use (handler, filter, level, Logs page buffer)."""
    # Set custom logger class
    logging.setLoggerClass(InstrumentedLogger)

    logger = logging.getLogger(name)
    logger.__class__ = InstrumentedLogger

    if not logger.handlers:
        # Force unbuffered stdout for Docker/container environments
        sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(LevelPrefixFormatter('%(asctime)s.%(msecs)03d %(message)s', datefmt='%H:%M:%S'))
        handler.addFilter(RequestLineFilter(logger))
        logger.addHandler(handler)
        logger.setLevel(log_level_from_env())

        # Recent messages for the web UI's Logs page, from the very start
        from sonorium import logbuffer
        logbuffer.install(name, RequestLineFilter(logger))

    return logger


logger = get_logger("sonorium")
