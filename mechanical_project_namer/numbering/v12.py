# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from dataclasses import dataclass
import re

MODULES: tuple[tuple[str, str], ...] = (
    ("00", "整机总装"), ("01", "机架模块"), ("02", "送料模块"),
    ("03", "压弧/成型模块"), ("04", "锯切模块"), ("05", "导料/托料模块"),
    ("06", "气动模块"), ("07", "电气模块"), ("08", "防护模块"),
    ("90", "标准件/通用件"), ("99", "工程图、BOM、资料"),
)
PART_TYPES: tuple[tuple[str, str, int, int], ...] = (
    ("总成", "000", 0, 0), ("自制机加工件", "001-099", 1, 99),
    ("钣金/焊接件", "101-199", 101, 199), ("采购/标准件", "201-299", 201, 299),
)
# 单例类别：每个项目的每个模块只允许存在一个，序号恒定为 0（显示为 000），
# 永不使用 part_sequence_counters 递增计数器。
SINGLETON_PART_TYPES: frozenset[str] = frozenset({"总成"})
SINGLETON_SEQUENCE: int = 0
_FULL_PART_NUMBER = re.compile(r"^(?P<code>[A-Z0-9]+(?:-[A-Z0-9]+)*)-(?P<module>\d{2})-(?P<sequence>\d{3})(?:\s+.*)?$")

@dataclass(frozen=True, slots=True)
class ProjectCodeValidation:
    code: str
    is_full_part_number: bool = False
    module_code: str = ""
    part_sequence: int | None = None

def normalize_project_code(value: str) -> ProjectCodeValidation:
    code = str(value or "").strip().upper().replace("_", "-")
    match = _FULL_PART_NUMBER.fullmatch(code)
    if match:
        return ProjectCodeValidation(match.group("code"), True, match.group("module"), int(match.group("sequence")))
    return ProjectCodeValidation(code)

def compose_full_name(project_code: str, module_code: str, part_sequence: int, part_name: str) -> str:
    return f"{project_code}-{module_code}-{part_sequence:03d} {part_name.strip()}".strip()

def part_type_range(part_type: str) -> tuple[int, int]:
    for name, _label, start, end in PART_TYPES:
        if name == part_type:
            return start, end
    raise ValueError(f"未知零件类别：{part_type}")


def is_singleton_part_type(part_type: str) -> bool:
    """True for part types that allow exactly one record per (project, module)."""
    return part_type in SINGLETON_PART_TYPES


def singleton_sequence(part_type: str) -> int:
    if part_type not in SINGLETON_PART_TYPES:
        raise ValueError(f"{part_type} 不是单例零件类别。")
    return SINGLETON_SEQUENCE


def singleton_exists_message(part_type: str) -> str:
    """User-facing message required by the confirmed 总成 strategy."""
    return f"该模块已存在{part_type} {SINGLETON_SEQUENCE:03d}"
