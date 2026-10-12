# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import os
import re
from pathlib import Path

from .constants import MAX_FILENAME_CHARS, MAX_PATH_CHARS, PART_EXTENSION
from .errors import SolidWorksError


ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def validate_part_name(value: str) -> str:
    if not value:
        raise SolidWorksError("invalid_filename", "零件名称不能为空。", "validation")
    if value != value.strip():
        raise SolidWorksError("invalid_filename", "零件名称不能包含首尾空格。", "validation")
    if value.endswith("."):
        raise SolidWorksError("invalid_filename", "零件名称不能以句点结尾。", "validation")
    if ILLEGAL_CHARS.search(value):
        raise SolidWorksError("invalid_filename", "零件名称包含Windows不允许的字符。", "validation")
    stem = value.split(".", 1)[0].upper()
    if stem in RESERVED_NAMES:
        raise SolidWorksError("invalid_filename", f"{stem} 是Windows保留名称。", "validation")
    return value


def validate_target_path(target: str, *, require_directory: bool = True) -> Path:
    path = Path(target)
    if path.suffix.upper() != PART_EXTENSION:
        raise SolidWorksError("invalid_filename", f"目标文件必须使用{PART_EXTENSION}扩展名。", "validation")
    validate_part_name(path.stem)
    if len(path.name) > MAX_FILENAME_CHARS:
        raise SolidWorksError("filename_too_long", "SOLIDWORKS零件文件名过长。", "validation")
    if len(str(path.resolve(strict=False))) > MAX_PATH_CHARS:
        raise SolidWorksError("path_too_long", "SOLIDWORKS零件完整路径过长。", "validation")
    if path.exists():
        raise SolidWorksError("file_exists", f"目标SOLIDWORKS零件已经存在：\n{path}", "validation")
    parent = path.parent
    if require_directory and not parent.is_dir():
        raise SolidWorksError("directory_missing", f"目标目录不存在：\n{parent}", "validation")
    if parent.is_dir() and not os.access(parent, os.W_OK):
        raise SolidWorksError("directory_not_writable", f"目标目录不可写：\n{parent}", "validation")
    return path
