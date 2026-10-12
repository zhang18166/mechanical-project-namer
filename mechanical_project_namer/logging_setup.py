# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import logging
import sys
from logging.handlers import RotatingFileHandler

from .paths import logs_dir


def configure_logging() -> None:
    directory = logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / "mechanical_project_namer.log",
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)


def solidworks_logger() -> logging.Logger:
    """Return the dedicated UTF-8 rolling logger for SOLIDWORKS operations."""
    directory = logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("mechanical_project_namer.solidworks.operations")
    target = str((directory / "solidworks.log").resolve())
    if not any(getattr(handler, "baseFilename", "") == target for handler in logger.handlers):
        handler = RotatingFileHandler(
            target,
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def install_exception_hook() -> None:
    previous = sys.excepthook

    def handle(exc_type, exc_value, traceback) -> None:
        logging.getLogger(__name__).critical("未处理异常", exc_info=(exc_type, exc_value, traceback))
        previous(exc_type, exc_value, traceback)

    sys.excepthook = handle
