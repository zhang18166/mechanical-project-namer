# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .paths import settings_dir
from .safe_fs import remove_file


@dataclass(slots=True)
class AppSettings:
    hotkey: str = "Ctrl+Alt+N"
    always_on_top: bool = False
    last_window_mode: str = "full"
    main_geometry: str = ""
    mini_geometry: str = ""
    current_project_id: int | None = None
    current_module_code: str = "03"
    current_part_type: str = "自制机加工件"
    api_url: str = "https://api.deepseek.com"
    model_name: str = "deepseek-v4-pro"
    available_models: list[str] | None = None
    verified_manual_models: list[str] | None = None
    model_list_source: str = "未读取"
    model_list_updated_at: str = ""
    thinking_mode: str = "enabled"
    reasoning_level: str = "high"
    json_auto_repair: bool = True
    connect_timeout: int = 10
    request_timeout: int = 30
    generation_timeout: int = 180
    solidworks_enabled: bool = True
    solidworks_fallback_template: str = ""
    solidworks_confirm_before_create: bool = True
    solidworks_copy_after_success: bool = True
    solidworks_switch_after_success: bool = True
    solidworks_advance_after_success: bool = True
    solidworks_overall_timeout: int = 120


def settings_path() -> Path:
    return settings_dir() / "settings.json"


def load_settings(path: Path | None = None) -> AppSettings:
    target = path or settings_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        allowed = {item.name for item in fields(AppSettings)}
        values = {key: value for key, value in raw.items() if key in allowed}
        if values.get("available_models") is not None and not isinstance(values["available_models"], list):
            values["available_models"] = None
        return AppSettings(**values)
    except (OSError, ValueError, TypeError):
        return AppSettings()


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(asdict(settings), stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        remove_file(temporary, what="设置临时文件")
