# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from .memory import MemoryContext
from .models import CodeSuggestion


FORBIDDEN_NAME_WORDS = (
    "最终版", "修改版", "新款", "加厚版", "客户", "不锈钢", "热处理",
)
STRUCTURAL_SUFFIXES = ("座", "台", "板", "架", "轴", "轮", "块", "套", "杆", "总成")


def assess_naming_quality(suggestions: tuple[CodeSuggestion, ...], context: MemoryContext | None,
                          task_type: str = "part") -> dict[str, str]:
    if task_type == "project":
        codes = [item.code for item in suggestions]
        history_codes = {item.get("code", "") for item in context.similar_projects} if context else set()
        historical = "高" if any(code in history_codes for code in codes) else ("中" if history_codes else "暂无历史依据")
        ambiguity = "中" if any(item.missing_information not in ("", "无", "暂无") for item in suggestions) else "低"
        return {
            "规则符合度": "高",
            "历史一致性": historical,
            "功能表达": "高" if all(item.overall_meaning for item in suggestions) else "中",
            "名称简洁度": "高" if all(len(code) <= 12 for code in codes) else "中",
            "存在歧义": ambiguity,
        }
    names = [item.recommended_name.strip() for item in suggestions if item.recommended_name.strip()]
    if not names:
        return {"规则符合度": "低", "历史一致性": "低", "功能表达": "低", "名称简洁度": "低", "存在歧义": "高"}
    forbidden = any(word in name for name in names for word in FORBIDDEN_NAME_WORDS)
    concise = all(2 <= len(name) <= 10 for name in names)
    expressive = any(name.endswith(STRUCTURAL_SUFFIXES) for name in names)
    history_names = set()
    if context:
        history_names.update(item.get("part_name", "") for item in context.current_parts)
        history_names.update(item.get("part_name", "") for item in context.similar_parts)
        history_names.update(item.get("final_name", "") for item in context.feedback)
    historical = "高" if any(name in history_names for name in names) else ("中" if history_names else "暂无历史依据")
    ambiguity = "中" if any(item.missing_information not in ("", "无", "暂无") for item in suggestions) else "低"
    return {
        "规则符合度": "低" if forbidden else "高",
        "历史一致性": historical,
        "功能表达": "高" if expressive else "中",
        "名称简洁度": "高" if concise else "中",
        "存在歧义": ambiguity,
    }
