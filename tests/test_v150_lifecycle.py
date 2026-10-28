# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from pathlib import Path
import sqlite3

from mechanical_project_namer.data import Database, Repository
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.ui.ai_plan_history_dialog import AIPlanHistoryDialog


def services(tmp_path):
    db = Database(tmp_path / "v150.db"); db.migrate()
    repo = Repository(db); service = ProjectService(repo)
    return db, repo, service


def test_schema7_and_project_delete_cleans_solidworks_operations(tmp_path):
    db, repo, service = services(tmp_path)
    project = service.create("待删除", "", "DEL")
    with db.transaction() as cx:
        cx.execute("INSERT INTO solidworks_operations(operation_id,project_id,module_code,part_type,part_sequence,part_name,full_name,target_path,status,started_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("op", project.id, "03", "自制机加工件", 1, "滑座", "DEL-03-001 滑座", str(tmp_path / "x.SLDPRT"), "failed", "now", "now"))
    repo.set_project_status(project.id, "trash")
    repo.permanently_delete_project(project.id, "DEL", tmp_path / "backup")
    with db.connect() as cx:
        assert cx.execute("SELECT version FROM schema_meta").fetchone()[0] >= 7
        assert cx.execute("SELECT COUNT(*) FROM solidworks_operations WHERE project_id=?", (project.id,)).fetchone()[0] == 0
        assert cx.execute("PRAGMA foreign_key_check").fetchall() == []


def test_record_delete_ignores_project_directory_and_preserves_file(tmp_path):
    db, repo, service = services(tmp_path)
    project = service.create("设备", "", "DEV", directory=str(tmp_path))
    record = repo.save_named_record(project.id, "03", "自制机加工件", "滑座")
    physical = tmp_path / "滑座.SLDPRT"; physical.write_bytes(b"design")
    with db.transaction() as cx:
        cx.execute("UPDATE drawing_records SET file_path=?,file_exists=1 WHERE id=?", (str(physical), record.id))
    repo.delete_record(record.id)
    assert physical.read_bytes() == b"design"
    assert repo.get_next_named_number(project.id, "03", "自制机加工件") == 2
    assert repo.list_record_delete_audit(record.id)[0]["physical_file_exists"] == 1


def test_void_record_keeps_number_reservation(tmp_path):
    _, repo, service = services(tmp_path)
    project = service.create("设备", "", "DEV")
    record = repo.save_named_record(project.id, "03", "自制机加工件", "滑座")
    repo.set_record_status(record.id, "void")
    assert repo.get_next_named_number(project.id, "03", "自制机加工件") == 2


def test_old_ai_plan_is_not_adoptable_when_structured_data_missing(tmp_path):
    _, repo, service = services(tmp_path)
    project = service.create("设备", "", "DEV")
    plan_id = repo.save_ai_plan(prompt="旧规划", project_id=project.id, result={"response": "not-json"})
    plan = repo.get_ai_plan(plan_id)
    assert repo.decode_ai_plan(plan)["adoptable"] is False


def test_ai_plan_search_and_adoption_audit(tmp_path):
    _, repo, service = services(tmp_path)
    project = service.create("设备", "", "DEV")
    plan_id = repo.save_ai_plan(prompt="压弧设备", project_id=project.id, result={"suggestions": [{"code": "NEW", "project_name": "新设备"}], "module_recommendations": [{"module_code": "03", "module_name": "压弧模块"}]})
    assert repo.search_ai_plans(query="压弧", project_id=project.id)
    adoption = repo.record_ai_plan_adoption(plan_id, project.id, "current_project", {"code": "NEW", "modules": []})
    assert adoption and repo.list_ai_plan_adoptions(plan_id)[0]["mode"] == "current_project"


def test_ai_history_workspace_has_safe_minimum_size(qtbot, tmp_path):
    _, repo, service = services(tmp_path)
    project = service.create("设备", "", "DEV")
    dialog = AIPlanHistoryDialog(repo, project.id); qtbot.addWidget(dialog)
    assert dialog.minimumWidth() >= 1000 and dialog.minimumHeight() >= 650
