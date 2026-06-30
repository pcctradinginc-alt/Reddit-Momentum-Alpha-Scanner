"""Centralized logging. Structured-ish, level from env, optional file sink."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_CONFIGURED = False


def setup_logging(level: str | None = None, log_file: str | Path | None = None) -> logging.Logger:
    """Idempotently configure the root 'rmas' logger."""
    global _CONFIGURED
    logger = logging.getLogger("rmas")
    if _CONFIGURED:
        return logger

    level_name = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    logger.propagate = False
    _CONFIGURED = True
    return logger


def get_logger(name: str = "rmas") -> logging.Logger:
    """Get a namespaced child logger (ensures base config exists)."""
    setup_logging()
    return logging.getLogger(name if name.startswith("rmas") else f"rmas.{name}")
