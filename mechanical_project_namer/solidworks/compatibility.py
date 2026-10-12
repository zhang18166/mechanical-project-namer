# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import re

from .constants import MIN_SUPPORTED_YEAR, SUPPORTED_YEARS
from .errors import SolidWorksError


def parse_revision(revision: str) -> tuple[int, str]:
    match = re.match(r"\s*(\d+)(?:\.(\d+))?", str(revision or ""))
    if not match:
        raise SolidWorksError("version_unreadable", "无法读取SOLIDWORKS版本。", "version")
    major = int(match.group(1))
    year = major + 1992
    service_pack = f"SP{int(match.group(2))}" if match.group(2) is not None else ""
    return year, service_pack


def support_level(year: int) -> tuple[bool, str]:
    if year in SUPPORTED_YEARS:
        return True, "official"
    if year < MIN_SUPPORTED_YEAR:
        return False, "below_minimum"
    return False, "forward_compatible_untested"


def require_supported(year: int, allow_unsupported: bool = False) -> None:
    supported, level = support_level(year)
    if supported or allow_unsupported:
        return
    if level == "below_minimum":
        message = f"当前检测到SOLIDWORKS {year}，低于正式支持范围（2025及以上）。"
    else:
        message = f"当前检测到SOLIDWORKS {year}，该版本仅做前向兼容设计，尚未实机验证。"
    raise SolidWorksError("unsupported_version", message, "version", details={"year": year})
