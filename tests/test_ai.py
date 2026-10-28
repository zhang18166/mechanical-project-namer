# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json

import pytest

from mechanical_project_namer.ai import AIResponseError, parse_suggestions
from mechanical_project_namer.ai.prompt_builder import build_messages


def response(codes):
    return json.dumps({
        "suggestions": [
            {
                "code": code,
                "segments": [{"segment": part, "meaning": "含义", "source": "用户描述"} for part in code.split("-")],
                "advantage": "简短",
                "concern": "信息有限",
                "missing_information": "系列未提供",
            }
            for code in codes
        ],
        "missing_information": ["系列未提供"],
    }, ensure_ascii=False)


def test_parser_accepts_three_explainable_codes():
    result = parse_suggestions(response(["DQ-16", "DQJ-16", "LWG-DQ-16"]), "16螺纹钢筋打圈机")
    assert len(result.suggestions) == 3
    assert result.suggestions[0].code == "DQ-16"


def test_parser_rejects_fabricated_number_segment():
    with pytest.raises(AIResponseError, match="无来源数字"):
        parse_suggestions(response(["DQ-12-16", "DQJ-16", "LWG-DQ-16"]), "16螺纹钢筋打圈机")


def test_part_parser_allows_shared_project_code():
    payload = json.loads(response(["ZGQ", "ZGQ", "ZGQ"]))
    for item, name in zip(payload["suggestions"], ("压弧滑座", "压弧移动座", "压弧轮座")):
        item["recommended_name"] = name
    result = parse_suggestions(json.dumps(payload, ensure_ascii=False), "ZGQ 压弧模块", task_type="part")
    assert len(result.suggestions) == 3


def test_part_parser_rejects_duplicate_names():
    payload = json.loads(response(["ZGQ", "ZGQ", "ZGQ"]))
    for item in payload["suggestions"]:
        item["recommended_name"] = "压弧滑座"
    with pytest.raises(AIResponseError, match="三个非空且不同"):
        parse_suggestions(json.dumps(payload, ensure_ascii=False), "ZGQ 压弧模块", task_type="part")


def test_part_parser_uses_local_project_segment_when_ai_omits_it():
    payload = json.loads(response(["ZGQ", "ZGQ", "ZGQ"]))
    for item, name in zip(payload["suggestions"], ("压弧滑座", "压弧移动座", "压弧轮座")):
        item["recommended_name"] = name
        item["segments"] = []
    result = parse_suggestions(json.dumps(payload, ensure_ascii=False), "ZGQ 压弧模块", task_type="part")
    assert result.suggestions[0].segments[0].source == "本地当前项目"


def test_prompt_explicitly_forbids_fabricated_numbers():
    messages = build_messages("16螺纹钢筋打圈机")
    assert "每个数字段必须原样出现在用户描述中" in messages[0]["content"]
    assert "16螺纹钢筋打圈机" in messages[1]["content"]
