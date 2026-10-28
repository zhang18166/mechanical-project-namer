# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json

import pytest

from mechanical_project_namer.ai.response_parser import parse_suggestions
from mechanical_project_namer.data import DataExporter, Database, Repository
from mechanical_project_namer.numbering import compose_full_name
from mechanical_project_namer.numbering.workflow import NamingWorkflow
from mechanical_project_namer.projects import ProjectService, ValidationError


@pytest.fixture()
def v12_services(tmp_path):
    database = Database(tmp_path / "v12.db")
    database.migrate()
    repository = Repository(database)
    service = ProjectService(repository)
    workflow = NamingWorkflow(repository, DataExporter(repository, tmp_path / "exports"))
    return database, repository, service, workflow


def test_full_part_code_is_rejected_with_split(v12_services):
    _, _, service, _ = v12_services
    with pytest.raises(ValidationError, match="项目代号 ZGQ"):
        service.create("圆管设备", "", "ZGQ-02-001")


def test_modules_and_categories_number_independently(v12_services):
    _, repository, service, workflow = v12_services
    project = service.create("圆管设备", "", "ZGQ")
    assert {item["module_code"] for item in repository.list_modules(project.id)} >= {"03", "04"}
    assert workflow.preview_named(project.id, "03", "自制机加工件") == "ZGQ-03-001"
    first = workflow.save_named_and_advance(project.id, "03", "自制机加工件", "压弧滑座")
    assert first.display_name == "ZGQ-03-001 压弧滑座"
    assert workflow.preview_named(project.id, "03", "自制机加工件") == "ZGQ-03-002"
    assert workflow.preview_named(project.id, "04", "自制机加工件") == "ZGQ-04-001"
    assert workflow.preview_named(project.id, "03", "钣金/焊接件") == "ZGQ-03-101"
    assert workflow.preview_named(project.id, "03", "采购/标准件") == "ZGQ-03-201"


def test_copy_preview_does_not_increment_and_delete_does_not_reuse(v12_services):
    _, repository, service, workflow = v12_services
    project = service.create("圆管设备", "", "ZGQ")
    assert workflow.copy_named_preview(project.id, "03", "自制机加工件", "压弧滑座") == "ZGQ-03-001 压弧滑座"
    record = workflow.save_named_and_advance(project.id, "03", "自制机加工件", "压弧滑座")
    repository.delete_record(record.id)
    assert workflow.preview_named(project.id, "03", "自制机加工件") == "ZGQ-03-002"


def test_duplicate_project_candidates_are_normalized_and_merged():
    payload = {"suggestions": [
        {"code": "zgq", "overall_meaning": "短说明"},
        {"code": "ZGQ ", "overall_meaning": "更完整的项目说明"},
        {"code": "YDJ", "overall_meaning": "另一候选"},
    ]}
    result = parse_suggestions(json.dumps(payload, ensure_ascii=False), "圆管设备")
    assert [item.code for item in result.suggestions] == ["ZGQ", "YDJ"]
    assert result.suggestions[0].overall_meaning == "更完整的项目说明"


def test_full_name_composition_keeps_name_separate():
    assert compose_full_name("ZGQ", "03", 5, "压弧滑座") == "ZGQ-03-005 压弧滑座"


def test_legacy_scan_and_explicit_migration(v12_services):
    database, repository, _, _ = v12_services
    project = repository.create_project("旧项目", "", "ZGQ-02-001")
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO drawing_records
               (project_id,project_name,project_code,drawing_number,filename,part_name,purpose,record_date,created_at,updated_at,status,is_copied,creation_method,project_directory,extra_notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (project.id, project.name, project.code, 1, "ZGQ-02-001-002", "ZGQ-02-001-002", "大滑块", "2026-01-01", "2026-01-01", "2026-01-01", "used", 0, "legacy", "", "保留备注"),
        )
    issues = repository.scan_legacy_anomalies()
    assert {item["kind"] for item in issues} == {"project_code", "record_name"}
    result = repository.migrate_legacy_anomalies(issues)
    assert result["changed"] == 2
    assert repository.get_project(project.id).code == "ZGQ"
    assert repository.list_records(project.id)[0].part_name == "大滑块"
