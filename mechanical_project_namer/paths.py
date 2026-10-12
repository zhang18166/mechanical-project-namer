# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import os
import sys
from pathlib import Path


APP_DIR_NAME = "MechanicalProjectNamer"


def local_data_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / APP_DIR_NAME


def settings_dir() -> Path:
    root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return root / APP_DIR_NAME


def database_path() -> Path:
    return local_data_dir() / "data" / "mechanical_project_namer.db"


def exports_dir() -> Path:
    return local_data_dir() / "exports"


def logs_dir() -> Path:
    return local_data_dir() / "logs"


def application_log_path() -> Path:
    return logs_dir() / "mechanical_project_namer.log"


def solidworks_log_path() -> Path:
    return logs_dir() / "solidworks.log"


def ai_memory_dir() -> Path:
    return local_data_dir() / "ai_memory"


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative
