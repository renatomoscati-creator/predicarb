import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


def configure_logging(
    logs_dir: Path,
    log_level: str = "INFO",
    log_file_name: str = "polymarket_bot.log",
) -> None:
    """
    Configure application logging.

    - Logs to stdout and a rotating file in `logs_dir`.
    - Timestamps are emitted in UTC; consumers should set `UTC` on formatters.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file_path = logs_dir / log_file_name

    # Root logger
    logger = logging.getLogger()
    logger.setLevel(log_level.upper())

    # Clear existing handlers to avoid duplicate logs when re-configuring
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)sZ %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # File handler with rotation
    file_handler = RotatingFileHandler(
        filename=str(log_file_path),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
    )
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name)

