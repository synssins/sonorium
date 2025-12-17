"""
Logging setup for standalone Sonorium.

Uses RotatingFileHandler for automatic log rotation:
- Max 10MB per file
- Keep up to 10 backup files
- Self-cleaning (old files deleted automatically)
"""

import logging
from logging.handlers import RotatingFileHandler
import sys
from pathlib import Path


# Configuration
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10MB
MAX_LOG_FILES = 10  # Keep up to 10 files (current + 9 backups)


def get_log_dir() -> Path:
    """Get the logs directory in the app root."""
    if getattr(sys, 'frozen', False):
        # Running as compiled EXE - exe is in app/windows/, logs is in app/logs/
        log_dir = Path(sys.executable).parent.parent / 'logs'
    else:
        # Running as script - this file is in app/core/sonorium/, logs is in app/logs/
        # app/core/sonorium/obs.py -> parent = sonorium/, parent.parent = core/, parent.parent.parent = app/
        log_dir = Path(__file__).parent.parent.parent / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def cleanup_old_logs(log_dir: Path, keep_count: int = MAX_LOG_FILES):
    """
    Clean up old timestamped log files.

    This handles legacy log files with timestamps in the name
    (from before we switched to rotating logs).
    """
    try:
        # Find all sonorium_*.log files (old format)
        old_logs = sorted(
            log_dir.glob('sonorium_*.log'),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        # Delete all old timestamped logs (we now use rotating sonorium.log)
        for old_log in old_logs:
            try:
                old_log.unlink()
            except Exception:
                pass
    except Exception:
        pass


# Create logger
logger = logging.getLogger('sonorium')
logger.setLevel(logging.DEBUG)

# Console handler
if not logger.handlers:
    # Console handler (INFO level)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # File handler with rotation (DEBUG level - more verbose)
    try:
        log_dir = get_log_dir()

        # Clean up old timestamped logs from previous versions
        cleanup_old_logs(log_dir)

        log_file = log_dir / 'sonorium.log'

        # RotatingFileHandler: rotates when file reaches maxBytes
        # Creates sonorium.log, sonorium.log.1, sonorium.log.2, etc.
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=MAX_LOG_SIZE,
            backupCount=MAX_LOG_FILES - 1,  # -1 because backupCount doesn't include current file
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
        logger.info(f'Log file: {log_file} (max {MAX_LOG_SIZE // 1024 // 1024}MB, {MAX_LOG_FILES} files)')
    except Exception as e:
        logger.warning(f'Could not create log file: {e}')
