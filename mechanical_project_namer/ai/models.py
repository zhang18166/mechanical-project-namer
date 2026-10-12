# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SegmentMeaning:
    segment: str
    meaning: str
    source: str


@dataclass(frozen=True, slots=True)
class CodeSuggestion:
    code: str
    segments: tuple[SegmentMeaning, ...]
    advantage: str
    concern: str
    missing_information: str
    module_code: str = ""
    part_type: str = ""
    recommended_name: str = ""
    reason: str = ""
    overall_meaning: str = ""
    parse_warning: str = ""


@dataclass(frozen=True, slots=True)
class AISuggestionResult:
    suggestions: tuple[CodeSuggestion, ...]
    missing_information: tuple[str, ...]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    model: str = ""
    actual_model: str = ""
    elapsed_seconds: float = 0.0
    first_response_seconds: float = 0.0
    first_text_seconds: float = 0.0
    reasoning_level: str = "high"
    thinking_enabled: bool = True
    reference_counts: dict[str, int] | None = None
    quality_assessment: dict[str, str] | None = None
    parse_seconds: float = 0.0
    model_generation_seconds: float = 0.0
    repair_used: bool = False
    response_record_id: str = ""
    module_recommendations: tuple[dict, ...] = ()
    response_metrics: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ModelInfo:
    model_id: str
    display_name: str
    owned_by: str = ""


@dataclass(frozen=True, slots=True)
class BalanceInfo:
    is_available: bool
    total_balance: str = ""
    granted_balance: str = ""
    topped_up_balance: str = ""
    currency: str = "CNY"
    updated_at: str = ""


@dataclass(frozen=True, slots=True)
class ModelTestResult:
    requested_model: str
    actual_model: str
    reply: str
    first_response_seconds: float
    first_text_seconds: float
    elapsed_seconds: float
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    estimated_cost: str = "暂无法估算"
    reasoning_level: str = "high"
    thinking_enabled: bool = True


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    ok: bool
    stage: str
    message: str
    model_count: int = 0
    elapsed_seconds: float = 0.0
    http_status: int | None = None


@dataclass(frozen=True, slots=True)
class StreamEvent:
    kind: str
    message: str = ""
    text: str = ""
    data: dict[str, Any] | None = None
