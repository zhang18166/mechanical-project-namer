# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import sqlite3
import zipfile
from pathlib import Path

import pytest

from mechanical_project_namer.ai import NamingMemory
from mechanical_project_namer.data import DataExporter, Database, Repository
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.settings import AppSettings
from mechanical_project_namer.ui.main_window import MainWindow


@pytest.fixture
def managed(tmp_path):
    database = Database(tmp_path / "management.db")
    database.migrate()
    repository = Repository(database)
    return database, repository, ProjectService(repository), NamingMemory(database)


def test_project_lifecycle_persists_and_normal_list_excludes_archive_and_trash(managed):
    database, repository, service, _ = managed
    archived = service.create("归档设备", "压弧设备", "ARC")
    trash = service.create("错误设备", "压弧设备", "BAD", project_type="test")
    repository.set_project_status(archived.id, "archived")
    repository.set_project_status(trash.id, "trash")
    reopened = Repository(Database(database.path))
    assert reopened.get_project(archived.id).project_status == "archived"
    assert reopened.get_project(trash.id).project_type == "test"
    assert {item.id for item in reopened.list_projects()} == set()
    assert reopened.list_projects(project_status="trash")[0].id == trash.id


def test_archive_restore_preserves_modules_records_and_numbering(managed):
    _, repository, service, _ = managed
    project = service.create("正式设备", "", "KEEP")
    repository.save_named_record(project.id, "03", "自制机加工件", "压弧滑座")
    repository.set_project_status(project.id, "archived")
    repository.set_project_status(project.id, "normal")
    assert repository.get_next_named_number(project.id, "03", "自制机加工件") == 2
    assert len(repository.list_modules(project.id, enabled_only=False)) == 11
    assert repository.list_records(project.id)[0].part_name == "压弧滑座"


def test_trash_project_cannot_create_record_and_is_excluded_from_ai(managed):
    _, repository, service, memory = managed
    source = service.create("压弧相似设备", "伺服压弧导轨", "SIM")
    current = service.create("当前压弧设备", "伺服压弧导轨", "CUR")
    repository.set_project_status(source.id, "trash")
    with pytest.raises(ValueError, match="回收站"):
        repository.save_named_record(source.id, "03", "自制机加工件", "压弧滑座")
    context = memory.build_context("伺服压弧导轨设备", current.id, "03")
    assert all(item["id"] != source.id for item in context.similar_projects)


def test_archived_ai_reference_switch(managed):
    _, repository, service, memory = managed
    archived = service.create("压弧历史设备", "伺服压弧导轨", "HIS")
    current = service.create("当前设备", "", "NOW")
    repository.set_project_status(archived.id, "archived")
    assert any(item["id"] == archived.id for item in memory.build_context("伺服压弧导轨", current.id).similar_projects)
    memory.set_archived_reference(False)
    assert all(item["id"] != archived.id for item in memory.build_context("伺服压弧导轨", current.id).similar_projects)


def test_duplicate_project_copies_modules_but_not_records_or_numbers(managed):
    _, repository, service, _ = managed
    source = service.create("原设备", "项目描述", "SRC", notes="项目备注")
    repository.create_module(source.id, "10", "检测模块")
    repository.save_named_record(source.id, "03", "自制机加工件", "压弧滑座")
    copied = repository.duplicate_project(source.id, name="新设备", code="NEW", project_type="test")
    assert copied.description == "项目描述" and copied.notes == "项目备注"
    assert copied.project_type == "test"
    assert not repository.list_records(copied.id)
    assert repository.get_next_named_number(copied.id, "03", "自制机加工件") == 1
    assert "10" in {item["module_code"] for item in repository.list_modules(copied.id, False)}


def test_duplicate_code_is_rejected_without_partial_project(managed):
    _, repository, service, _ = managed
    source = service.create("原设备", "", "DUP")
    with pytest.raises(sqlite3.IntegrityError):
        repository.duplicate_project(source.id, name="重复", code="DUP")
    assert len(repository.list_projects(project_status="all")) == 1


def test_project_properties_have_module_record_and_ai_counts(managed):
    _, repository, service, _ = managed
    project = service.create("统计设备", "", "STAT")
    repository.save_named_record(project.id, "03", "自制机加工件", "滑座")
    repository.save_ai_plan(prompt="规划", project_id=project.id)
    repository.save_project_memory(project.id, "结构", "双导轨", "03")
    properties = repository.project_properties(project.id)
    assert properties["project"].module_count == 11
    assert properties["formal_count"] == 1
    assert properties["ai_plan_count"] == 1
    assert properties["project_memory_count"] == 1


def test_export_project_package_has_required_files_and_no_secret(managed, tmp_path):
    _, repository, service, _ = managed
    project = service.create("导出设备", "", "EXP")
    repository.save_named_record(project.id, "03", "自制机加工件", "滑座", "SECRET_API_KEY_SHOULD_NOT_EXIST")
    target = DataExporter(repository, tmp_path / "exports").export_project_package(project.id, tmp_path / "project.zip")
    with zipfile.ZipFile(target) as bundle:
        names = set(bundle.namelist())
        assert {"project.json", "modules.json", "parts.json", "ai_plans.json", "project_memories.json", "naming_examples.jsonl", "README.md", "records.csv"} <= names
        all_data = b"".join(bundle.read(name) for name in names)
        assert b"Authorization" not in all_data and b"sk-" not in all_data


def test_permanent_delete_requires_trash_and_correct_confirmation(managed, tmp_path):
    _, repository, service, _ = managed
    project = service.create("删除设备", "", "DEL")
    with pytest.raises(ValueError, match="回收站"):
        repository.permanently_delete_project(project.id, "DEL", tmp_path)
    repository.set_project_status(project.id, "trash")
    with pytest.raises(ValueError, match="不匹配"):
        repository.permanently_delete_project(project.id, "WRONG", tmp_path)
    assert repository.get_project(project.id).code == "DEL"


def test_permanent_delete_backs_up_cleans_project_data_and_keeps_rules(managed, tmp_path):
    _, repository, service, memory = managed
    project = service.create("测试删除", "", "TDL", project_type="test")
    other = service.create("正式保留", "", "SAFE")
    repository.save_named_record(project.id, "03", "自制机加工件", "测试滑座")
    repository.save_ai_plan(prompt="测试规划", project_id=project.id)
    repository.save_project_memory(project.id, "测试记忆", "内容")
    memory.record_feedback(project.id, "03", "板1", "测试滑座", "edited")
    rule_count = len(memory.rules())
    repository.set_project_status(project.id, "trash")
    result = repository.permanently_delete_project(project.id, "TDL", tmp_path)
    assert Path(result["backup_path"]).exists()
    with pytest.raises(LookupError): repository.get_project(project.id)
    assert repository.get_project(other.id).code == "SAFE"
    assert len(memory.rules()) == rule_count
    with repository.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_feedback WHERE project_id=?", (project.id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM ai_plans WHERE project_id=?", (project.id,)).fetchone()[0] == 0


def test_permanent_delete_transaction_rolls_back_on_failure(managed, tmp_path):
    database, repository, service, _ = managed
    project = service.create("回滚设备", "", "RBK")
    repository.save_named_record(project.id, "03", "自制机加工件", "滑座")
    repository.set_project_status(project.id, "trash")
    with database.connect() as connection:
        connection.execute("CREATE TRIGGER prevent_project_delete BEFORE DELETE ON projects BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repository.permanently_delete_project(project.id, "RBK", tmp_path)
    assert repository.get_project(project.id).project_status == "trash"
    assert len(repository.list_records(project.id)) == 1


def test_permanent_delete_never_deletes_real_project_directory(managed, tmp_path):
    _, repository, service, _ = managed
    design_dir = tmp_path / "solidworks_project"; design_dir.mkdir(); design_file = design_dir / "assembly.SLDASM"; design_file.write_text("design", encoding="utf-8")
    project = service.create("目录设备", "", "DIR", directory=str(design_dir))
    repository.set_project_status(project.id, "trash")
    result = repository.permanently_delete_project(project.id, "DIR", tmp_path / "backup")
    assert result["directory_untouched"] is True
    assert design_file.read_text(encoding="utf-8") == "design"


def test_audit_log_keeps_minimal_delete_record(managed, tmp_path):
    _, repository, service, _ = managed
    project = service.create("审计设备", "", "AUD")
    repository.set_project_status(project.id, "archived")
    repository.set_project_status(project.id, "trash")
    repository.permanently_delete_project(project.id, "AUD", tmp_path)
    actions = repository.list_project_audit(project.id)
    assert {item["action"] for item in actions} >= {"create", "archive", "trash", "permanent_delete"}
    deleted = next(item for item in actions if item["action"] == "permanent_delete")
    assert deleted["project_code"] == "AUD" and deleted["after_status"] == "deleted"


def test_project_management_table_removes_global_next_number_and_has_dynamic_menus(qtbot):
    window = MainWindow(AppSettings()); qtbot.addWidget(window)
    headers = [window.project_table.horizontalHeaderItem(i).text() for i in range(window.project_table.columnCount())]
    assert "下一个图号" not in headers
    assert {"模块数量", "零件记录数量", "最近使用模块", "最近创建编号"} <= set(headers)
    assert "永久删除" not in {label for label, _ in window.project_menu_actions("normal")}
    assert "永久删除" in {label for label, _ in window.project_menu_actions("trash")}
    assert "恢复项目" in {label for label, _ in window.project_menu_actions("archived")}


def test_double_click_normal_project_emits_open_action(qtbot, managed):
    _, repository, service, _ = managed
    project = service.create("双击设备", "", "DBL")
    window = MainWindow(AppSettings()); qtbot.addWidget(window); window.set_projects([project], project.id, [project]); window.project_table.selectRow(0)
    received = []; window.project_action_requested.connect(lambda action, project_id: received.append((action, project_id)))
    window._open_selected_project()
    assert received == [("open", project.id)]
