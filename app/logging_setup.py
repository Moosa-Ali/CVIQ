"""Application-wide logging configuration.

Sets up a ``cviq`` logger that writes to both the console and a rotating file
under the data dir (``settings.data_dir / "cviq.log"``). Idempotent: calling
``setup_logging`` more than once does not add duplicate handlers.
"""
import logging
import logging.handlers
from pathlib import Path

from .config import settings

LOGGER_NAME = "cviq"
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_MAX_BYTES = 1_000_000  # ~1 MB
_BACKUP_COUNT = 3


def _log_path() -> Path:
    return settings.data_dir / "cviq.log"


def setup_logging() -> Path:
    """Configure the ``cviq`` logger (console + rotating file). Returns the log path."""
    data_dir = settings.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = _log_path()

    logger = logging.getLogger(LOGGER_NAME)

    # Idempotency guard: if our handlers are already attached, do nothing.
    if any(getattr(h, "_cviq_handler", False) for h in logger.handlers):
        return log_path

    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # avoid duplicate emission via the root logger

    # Also raise the root level so library loggers (e.g. httpx) can be seen at DEBUG.
    logging.getLogger().setLevel(logging.DEBUG)

    formatter = logging.Formatter(_LOG_FORMAT)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    console.setFormatter(formatter)
    console._cviq_handler = True

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler._cviq_handler = True

    logger.addHandler(console)
    logger.addHandler(file_handler)

    return log_path
