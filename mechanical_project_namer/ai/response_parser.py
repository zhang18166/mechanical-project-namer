# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json
import re

from ..projects.service import CODE_PATTERN
from .models import AISuggestionResult, CodeSuggestion, SegmentMeaning


class AIResponseError(ValueError):
    """Raised when an AI response is syntactically or structurally unusable."""

    def __init__(self, message: str, *, error_type: str = "schema") -> None:
        super().__init__(message)
        self.error_type = error_type


WHOLE_MACHINE_MODULES = (
    ("01", "机架模块", ("机架", "设备底座")),
    ("02", "送料模块", ("送料主动轮", "送料主动轴")),
    ("03", "压弧/成型模块", ("压弧轮", "压弧滑座")),
    ("04", "锯切模块", ("锯片轴", "锯切移动座")),
    ("05", "导料/托料模块", ("导料支架", "托料架")),
    ("07", "电气模块", ("电控箱", "伺服安装板")),
    ("08", "防护模块", ("锯切防护罩", "传动防护罩")),
)


def _balanced_candidates(text: str) -> list[str]:
    """Return complete top-level JSON object/array candidates from mixed text."""
    candidates: list[str] = []
    for opening, closing in (("{", "}"), ("[", "]")):
        start = None
        depth = 0
        in_string = False
        escaped = False
        for index, char in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opening:
                if depth == 0:
                    start = index
                depth += 1
            elif char == closing and depth:
                depth -= 1
                if depth == 0 and start is not None:
                    candidates.append(text[start:index + 1])
                    start = None
    return sorted(candidates, key=lambda value: text.find(value))


def _clean_json_candidate(candidate: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", candidate.replace("\ufeff", "").replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'"))


def _extract_json(content: str, tolerant: bool = False) -> dict:
    text = str(content or "").lstrip("\ufeff").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    attempts = [text]
    if tolerant:
        attempts.extend(_balanced_candidates(text))
    last_error = None
    for candidate in attempts:
        try:
            data = json.loads(_clean_json_candidate(candidate) if tolerant else candidate)
            if isinstance(data, list):
                data = {"suggestions": data}
            if not isinstance(data, dict):
                raise AIResponseError("AI 返回的数据结构无效。", error_type="schema")
            return data
        except json.JSONDecodeError as error:
            last_error = error
    raise AIResponseError("AI 返回内容不是有效的 JSON，请重新生成。", error_type="syntax") from last_error


def _normalise_candidate_code(value: object) -> str:
    code = str(value or "").strip().upper().replace("_", "-")
    code = re.sub(r"[‐‑‒–—−]", "-", code)
    code = re.sub(r"-+", "-", code).strip("-")
    return code


def _candidate_score(item: CodeSuggestion) -> int:
    return (
        len(item.segments) * 10
        + sum(bool(value) for value in (item.overall_meaning, item.advantage, item.concern, item.reason))
        + len(item.overall_meaning)
        + len(item.reason)
    )


def parse_suggestions(content: str, description: str, usage: dict | None = None,
                      task_type: str = "project", *, tolerant: bool = False) -> AISuggestionResult:
    data = _extract_json(content, tolerant)
    raw_suggestions = data.get("suggestions")
    if not isinstance(raw_suggestions, list) or not 1 <= len(raw_suggestions) <= 3:
        target = "候选零件名称" if task_type == "part" else "候选项目代号"
        raise AIResponseError(f"AI 必须返回 1 至 3 个{target}。")
    suggestions: list[CodeSuggestion] = []
    by_code: dict[str, CodeSuggestion] = {}
    for raw in raw_suggestions:
        if not isinstance(raw, dict):
            raise AIResponseError("候选项目代号的数据结构无效。", error_type="schema")
        code = _normalise_candidate_code(raw.get("code") or (raw.get("project_code") if tolerant else ""))
        if not CODE_PATTERN.fullmatch(code) or len(code) > 40:
            raise AIResponseError(f"AI 返回了无效项目代号：{code or '空值'}。", error_type="schema")
        overall_meaning = str(raw.get("overall_meaning", "")).strip()
        for number in re.findall(r"\d+", code):
            uncertain = "不确定" in overall_meaning or any(
                isinstance(item, dict) and str(item.get("value") or item.get("segment") or "") == number
                and "不确定" in str(item.get("meaning") or "")
                for item in (raw.get("segments") or [])
            )
            if number not in description and not (tolerant and uncertain):
                raise AIResponseError(
                    f"AI 候选 {code} 包含无来源数字“{number}”，已拒绝采用。请补充参数或重新生成。"
                    , error_type="schema")
        raw_segments = raw.get("segments", [])
        if task_type == "part" and (not isinstance(raw_segments, list) or not raw_segments):
            raw_segments = [{"segment": code, "meaning": "当前项目代号", "source": "本地当前项目"}]
        if not isinstance(raw_segments, list):
            if tolerant:
                raw_segments = []
            else:
                raise AIResponseError(f"候选 {code} 缺少分段含义。", error_type="schema")
        is_single_segment = "-" not in code
        parse_warning = ""
        if not raw_segments and not is_single_segment and not tolerant:
            raise AIResponseError(f"候选 {code} 缺少分段含义。", error_type="schema")
        if not raw_segments and (is_single_segment or tolerant):
            parse_warning = "AI没有返回完整的分段解释。可使用，但建议补充说明。"
        segments = tuple(
            SegmentMeaning(
                str(segment.get("segment") or (segment.get("value") if tolerant else "") or "").strip(),
                str(segment.get("meaning", "")).strip() or ("不确定" if tolerant else ""),
                str(segment.get("source", "")).strip() or ("AI未提供依据" if tolerant else ""),
            )
            for segment in raw_segments if isinstance(segment, dict)
        )
        if raw_segments and (not segments or any(not item.segment or not item.meaning or not item.source for item in segments)):
            raise AIResponseError(f"候选 {code} 的分段解释不完整。", error_type="schema")
        suggestion = CodeSuggestion(
            code=code,
            segments=segments,
            advantage=str(raw.get("advantage", "")).strip() or "未说明",
            concern=str(raw.get("concern", "")).strip() or "未说明",
            missing_information=str(raw.get("missing_information", "")).strip() or "无",
            module_code=str(raw.get("module_code", "")).strip(),
            part_type=str(raw.get("part_type", "")).strip(),
            recommended_name=str(raw.get("recommended_name", "")).strip(),
            reason=str(raw.get("reason", "")).strip(),
            overall_meaning=overall_meaning,
            parse_warning=parse_warning,
        )
        if task_type == "part":
            suggestions.append(suggestion)
        else:
            existing = by_code.get(code)
            if existing is None or _candidate_score(suggestion) > _candidate_score(existing):
                by_code[code] = suggestion
    if task_type != "part":
        suggestions = list(by_code.values())
    if not suggestions:
        raise AIResponseError("AI 没有返回有效候选。", error_type="schema")
    raw_missing = data.get("missing_information", [])
    if task_type == "part":
        names = [item.recommended_name for item in suggestions]
        if any(not name for name in names) or len(set(names)) != 3:
            raise AIResponseError("AI 必须返回三个非空且不同的候选零件名称。", error_type="schema")
    missing = tuple(str(item).strip() for item in raw_missing if str(item).strip()) if isinstance(raw_missing, list) else ()
    usage = usage or {}
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    details = usage.get("prompt_tokens_details") or {}
    cached_tokens = int(details.get("cached_tokens", 0) or 0) if isinstance(details, dict) else 0
    module_recommendations = []
    raw_modules = data.get("identified_modules", [])
    if isinstance(raw_modules, list):
        for item in raw_modules[:10]:
            if not isinstance(item, dict):
                continue
            module_code = str(item.get("module_code") or "").strip()
            module_name = str(item.get("module_name") or "").strip()
            examples = item.get("example_names") if isinstance(item.get("example_names"), list) else []
            if module_code and module_name:
                clean_examples = []
                for value in examples[:6]:
                    name = str(value).strip()
                    if name and not re.search(r"(?:^|-)\d{2}-\d{3}(?:\s|$)", name):
                        clean_examples.append(name)
                module_recommendations.append({
                    "module_code": module_code, "module_name": module_name,
                    "example_names": clean_examples,
                })
    whole_machine_plan = task_type == "project" and any(
        phrase in description for phrase in ("怎么给零件命名", "如何给零件命名", "准备画图", "整机")
    )
    if whole_machine_plan:
        by_code = {item["module_code"]: item for item in module_recommendations}
        for module_code, module_name, examples in WHOLE_MACHINE_MODULES:
            if module_code not in by_code:
                item = {"module_code": module_code, "module_name": module_name, "example_names": []}
                module_recommendations.append(item)
                by_code[module_code] = item
            existing = by_code[module_code]["example_names"]
            existing.extend(name for name in examples if name not in existing)
            del existing[6:]
        module_recommendations.sort(key=lambda item: item["module_code"])
    return AISuggestionResult(
        tuple(suggestions), missing, prompt_tokens, completion_tokens, cached_tokens,
        module_recommendations=tuple(module_recommendations),
    )
