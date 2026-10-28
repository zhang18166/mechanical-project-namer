# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json
import re
import zipfile
from pathlib import Path

import httpx
import pytest

from mechanical_project_namer.ai import (
    AIClient, AIResponseAudit, AIResponseError, NamingMemory, NamingMemoryPackage,
    parse_suggestions,
)
from mechanical_project_namer.ai.models import AISuggestionResult
from mechanical_project_namer.data import Database, Repository
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.ui.ai_suggestions_dialog import AISuggestionsDialog


def project_payload(*, alias: bool = False) -> dict:
    suggestions = []
    for code, meaning in (("ZGQ", "圆管制圈设备"), ("YGQ", "圆管加工设备"), ("ZQJ", "制圈机")):
        item = {"segments": [], "overall_meaning": meaning}
        item["project_code" if alias else "code"] = code
        suggestions.append(item)
    return {"suggestions": suggestions}


def stream_response(payload: dict, *, usage: dict | None = None, request_id: str = "req-test") -> httpx.Response:
    event = {"model": "deepseek-v4-pro", "choices": [{"delta": {"content": json.dumps(payload, ensure_ascii=False)}}]}
    if usage:
        event["usage"] = usage
    body = f"data: {json.dumps(event, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode("utf-8")
    return httpx.Response(200, headers={"content-type": "text/event-stream", "x-request-id": request_id}, content=body)


def make_client(handler, audit=None):
    return AIClient(
        "https://api.deepseek.com", "deepseek-v4-pro", "sk-test-secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        response_audit=audit,
    )


def test_single_segment_code_accepts_empty_segments():
    result = parse_suggestions(json.dumps(project_payload(), ensure_ascii=False), "圆管自动制圈机")
    assert result.suggestions[0].code == "ZGQ"
    assert result.suggestions[0].segments == ()
    assert "建议补充说明" in result.suggestions[0].parse_warning


def test_identified_modules_are_parsed_without_part_numbers():
    payload = project_payload()
    payload["identified_modules"] = [
        {"module_code": "02", "module_name": "送料模块", "example_names": ["送料主动轮", "送料主动轴"]},
        {"module_code": "03", "module_name": "压弧/成型模块", "example_names": ["压弧轮", "压弧滑座", "ZGQ-03-001 压弧轮"]},
    ]
    result = parse_suggestions(json.dumps(payload, ensure_ascii=False), "圆管自动制圈机")
    assert result.module_recommendations == (
        {"module_code": "02", "module_name": "送料模块", "example_names": ["送料主动轮", "送料主动轴"]},
        {"module_code": "03", "module_name": "压弧/成型模块", "example_names": ["压弧轮", "压弧滑座"]},
    )


def test_identified_modules_are_displayed(qtbot):
    parsed = parse_suggestions(json.dumps({
        **project_payload(),
        "identified_modules": [
            {"module_code": "04", "module_name": "锯切模块", "example_names": ["锯片轴", "锯切移动座"]},
        ],
    }, ensure_ascii=False), "圆管自动制圈机")
    dialog = AISuggestionsDialog(parsed)
    qtbot.addWidget(dialog)
    text = dialog.module_summary_label.text()
    assert "04 锯切模块" in text
    assert "锯片轴、锯切移动座" in text


def test_whole_machine_plan_completes_expected_candidate_modules():
    payload = project_payload()
    payload["identified_modules"] = [
        {"module_code": "02", "module_name": "送料模块", "example_names": ["送料轮"]},
        {"module_code": "03", "module_name": "压弧/成型模块", "example_names": ["压弧轮"]},
        {"module_code": "04", "module_name": "锯切模块", "example_names": ["锯片轴"]},
    ]
    result = parse_suggestions(
        json.dumps(payload, ensure_ascii=False),
        "圆管自动压圈打圈机，采用伺服送料、压弧和锯切，我准备画图，怎么给零件命名？",
    )
    by_code = {item["module_code"]: item for item in result.module_recommendations}
    assert set(by_code) >= {"01", "02", "03", "04", "05", "07", "08"}
    assert "送料主动轮" in by_code["02"]["example_names"]
    assert "压弧滑座" in by_code["03"]["example_names"]
    assert all(not re.search(r"-\d{2}-\d{3}", name) for item in by_code.values() for name in item["example_names"])


def test_multisegment_code_still_requires_segments():
    payload = project_payload()
    payload["suggestions"][0]["code"] = "DQ-16"
    with pytest.raises(AIResponseError, match="缺少分段含义"):
        parse_suggestions(json.dumps(payload, ensure_ascii=False), "16毫米打圈机")


def test_local_tolerance_uses_alias_without_second_request(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return stream_response(project_payload(alias=True))

    audit = AIResponseAudit(tmp_path / "audit.jsonl")
    result = make_client(handler, audit).suggest_stream("圆管自动制圈机")
    assert len(calls) == 1
    assert result.repair_used is False
    assert result.suggestions[0].code == "ZGQ"
    assert audit.latest()[0]["parse_result"] == "本地结构容错修复成功"


def test_remote_json_repair_runs_once_and_combines_usage(tmp_path):
    calls = []
    valid = project_payload()

    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return stream_response(
                {"suggestions": "invalid"},
                usage={"prompt_tokens": 10, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 1}},
                request_id="req-original",
            )
        return stream_response(
            valid,
            usage={"prompt_tokens": 5, "completion_tokens": 3, "prompt_tokens_details": {"cached_tokens": 0}},
            request_id="req-repair",
        )

    audit = AIResponseAudit(tmp_path / "audit.jsonl")
    result = make_client(handler, audit).suggest_stream("圆管自动制圈机")
    assert len(calls) == 2
    assert calls[1]["max_tokens"] == 2048
    assert calls[1]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in calls[1]
    assert result.repair_used is True
    assert result.prompt_tokens == 15
    assert result.completion_tokens == 5
    record = audit.latest()[0]
    assert record["request_id"] == "req-original"
    assert record["repair_result"]


def test_audit_redacts_api_keys(tmp_path):
    audit = AIResponseAudit(tmp_path / "audit.jsonl")
    audit.append(
        model="deepseek-v4-pro", request_id="req", raw_text="Authorization: Bearer sk-super-secret",
        parse_result="失败", failed_fields=["x"], repair_result="sk-another-secret",
    )
    text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "super-secret" not in text
    assert "another-secret" not in text
    assert "已隐藏" in text


@pytest.fixture()
def source_memory(tmp_path):
    database = Database(tmp_path / "source.db")
    database.migrate()
    repository = Repository(database)
    project = ProjectService(repository).create("圆管自动制圈机", "伺服压弧设备", "ZGQ")
    memory = NamingMemory(database)
    memory.record_feedback(
        project.id, "03", "压弧移动座", "压弧滑座", "edited", "",
        "安装在导轨上并承载压弧轮，由丝杆推动",
    )
    memory.save_part(project.id, "03", 1, "压弧滑座", "自制机加工件", "导轨往复承载")
    package = NamingMemoryPackage(database, tmp_path / "ai_memory")
    return database, repository, project, memory, package


def test_custom_database_refreshes_memory_beside_database(tmp_path):
    database = Database(tmp_path / "isolated" / "source.db")
    database.migrate()
    memory = NamingMemory(database)
    memory.record_feedback(None, "03", "压弧移动座", "压弧滑座", "edited", input_description="导轨承载")
    assert memory.package_directory == database.path.parent / "ai_memory"
    assert (memory.package_directory / "naming_memory.json").exists()


def test_explicit_memory_directory_is_respected(tmp_path):
    database = Database(tmp_path / "source.db")
    database.migrate()
    target = tmp_path / "portable_memory"
    memory = NamingMemory(database, target)
    memory.refresh_package()
    assert memory.package_directory == target
    assert (target / "MECHANICAL_NAMING_SKILL.md").exists()


def test_memory_package_generates_five_portable_files(source_memory, tmp_path):
    _, _, _, _, package = source_memory
    summary = package.generate()
    assert summary["rule_count"] == 11
    assert summary["formal_name_count"] == 1
    for name in ("MECHANICAL_NAMING_SKILL.md", "naming_memory.json", "naming_examples.jsonl", "project_code_memory.json", "schema.json"):
        assert (package.directory / name).exists()
    skill = package.skill_path.read_text(encoding="utf-8")
    assert "ZGQ-03-005 压弧滑座" in skill
    assert "用户手动固定规则 > 用户修改后采用" in skill
    memory = json.loads((package.directory / "naming_memory.json").read_text(encoding="utf-8"))
    assert memory["term_preferences"]["安装在导轨上并承载压弧轮，由丝杆推动"] == "压弧滑座"


def test_export_and_import_preserve_formal_projects(source_memory, tmp_path):
    _, _, _, _, source_package = source_memory
    archive = source_package.export_zip(tmp_path / "memory.zip")
    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {
            "MECHANICAL_NAMING_SKILL.md", "naming_memory.json", "naming_examples.jsonl",
            "project_code_memory.json", "schema.json",
        }

    target_database = Database(tmp_path / "target.db")
    target_database.migrate()
    target_repository = Repository(target_database)
    formal = ProjectService(target_repository).create("正式项目", "不可丢失", "ZSXM")
    target_repository.save_next_record(formal.id, "正式零件", "保留")
    target_package = NamingMemoryPackage(target_database, tmp_path / "target_memory")
    preview = target_package.inspect_zip(archive)
    assert preview["example_count"] == 1
    imported = target_package.import_zip(archive, "merge")
    assert imported["examples"] == 1
    assert target_repository.get_project(formal.id).name == "正式项目"
    assert len(target_repository.list_records(formal.id)) == 1
    context = NamingMemory(target_database).build_context("导轨承载压弧轮", formal.id, "03")
    assert any(item["final_name"] == "压弧滑座" for item in context.feedback)


def test_context_reference_limits_are_bounded(source_memory):
    database, repository, current, memory, _ = source_memory
    service = ProjectService(repository)
    for index in range(12):
        project = service.create(f"压弧相似项目{index}", "导轨压弧滑动", f"XY{index}")
        memory.save_part(project.id, "03", 1, f"压弧滑座{index}", "自制机加工件", "导轨承载压弧轮")
    for index in range(7):
        memory.record_feedback(current.id, "03", f"压弧移动座{index}", f"压弧滑座{index}", "edited", "", "导轨承载压弧轮")
    context = memory.build_context("导轨承载压弧轮的压弧移动结构", current.id, "03")
    assert len(context.similar_projects) <= 5
    assert len(context.similar_parts) <= 10
    assert len(context.feedback) <= 5
