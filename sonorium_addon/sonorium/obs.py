"""
Sonorium Logging - Simple logging wrapper.
Replaces fmtr.tools logging with standard Python logging.
"""
import functools
import logging
import sys

from sonorium.paths import paths
from sonorium.version import __version__


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
                    self.info(msg)
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
                    self.info(msg)
                return await func(*args, **kwargs)

            # Return appropriate wrapper based on function type
            import asyncio
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper

        return decorator


def get_logger(name: str, version: str = "") -> InstrumentedLogger:
    """Create an instrumented logger."""
    # Set custom logger class
    logging.setLoggerClass(InstrumentedLogger)

    logger = logging.getLogger(name)
    logger.__class__ = InstrumentedLogger

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter('%(asctime)s.%(msecs)03d %(message)s', datefmt='%H:%M:%S'))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

    return logger


# Create the main logger
logger = get_logger(
    name=paths.name_ns,
    version=__version__,
)
