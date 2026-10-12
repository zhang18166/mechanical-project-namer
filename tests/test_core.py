# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import sqlite3

import pytest

from mechanical_project_namer.data import DataExporter, Database, DuplicateNumberError, Repository
from mechanical_project_namer.numbering.workflow import NamingWorkflow
from mechanical_project_namer.projects import ProjectCodeChangeError, ProjectService, ValidationError


@pytest.fixture()
def services(tmp_path):
    database = Database(tmp_path / "data.db")
    database.migrate()
    repository = Repository(database)
    project_service = ProjectService(repository)
    workflow = NamingWorkflow(repository, DataExporter(repository, tmp_path / "exports"))
    return database, repository, project_service, workflow


def test_new_projects_keep_independent_sequences(services):
    _, _, projects, workflow = services
    first = projects.create("演示设备甲", "说明", "DEMO-1")
    second = projects.create("演示设备乙", "说明", "DEMO-2")
    assert workflow.preview(first.id) == "DEMO-1-001"
    assert workflow.preview(second.id) == "DEMO-2-001"


def test_preview_and_copy_do_not_increment(services):
    _, repository, projects, workflow = services
    project = projects.create("演示设备甲", "说明", "DEMO-1")
    assert workflow.preview(project.id) == "DEMO-1-001"
    assert workflow.copy_preview(project.id) == "DEMO-1-001"
    assert repository.get_project(project.id).next_number == 1


def test_successful_save_increments_and_exports_utf8(services, tmp_path):
    _, repository, projects, workflow = services
    project = projects.create("演示设备甲", "说明", "DEMO-1")
    record = workflow.save_and_advance(project.id, "驱动轴", "用于驱动演示设备甲构", "40Cr调质")
    assert record.filename == "DEMO-1-001"
    assert repository.get_project(project.id).next_number == 2
    snapshot = (tmp_path / "exports" / "项目数据快照.json").read_text(encoding="utf-8")
    assert "驱动轴" in snapshot


def test_failed_save_does_not_increment(services):
    database, repository, projects, workflow = services
    project = projects.create("演示设备甲", "说明", "DEMO-1")
    with database.transaction() as connection:
        connection.execute("CREATE TRIGGER stop_insert BEFORE INSERT ON drawing_records BEGIN SELECT RAISE(ABORT, 'stop'); END")
    with pytest.raises(sqlite3.IntegrityError):
        workflow.save_and_advance(project.id, "驱动轴", "用途")
    assert repository.get_project(project.id).next_number == 1


def test_duplicate_number_is_blocked(services):
    database, repository, projects, workflow = services
    project = projects.create("演示设备甲", "说明", "DEMO-1")
    workflow.save_and_advance(project.id, "驱动轴", "用途")
    with database.transaction() as connection:
        connection.execute("UPDATE projects SET next_number=1 WHERE id=?", (project.id,))
    with pytest.raises(DuplicateNumberError, match="已经存在"):
        workflow.save_and_advance(project.id, "手柄轮", "用途")


def test_void_number_is_not_reused(services):
    _, repository, projects, workflow = services
    project = projects.create("演示设备甲", "说明", "DEMO-1")
    first = workflow.save_and_advance(project.id, "驱动轴", "用途")
    repository.set_record_status(first.id, "void")
    second = workflow.save_and_advance(project.id, "手柄轮", "用途")
    assert second.drawing_number == 2


def test_code_change_needs_confirmation(services):
    _, _, projects, _ = services
    project = projects.create("演示设备甲", "说明", "DEMO-1")
    with pytest.raises(ProjectCodeChangeError):
        projects.update(project.id, code="DEMO-3")
    updated = projects.update(project.id, confirm_code_change=True, code="DEMO-3")
    assert updated.code == "DEMO-3"


def test_chinese_notes_are_searchable(services):
    _, repository, projects, workflow = services
    project = projects.create("演示设备甲", "说明", "DEMO-1")
    workflow.save_and_advance(project.id, "导向轮支架", "用于固定导向轮", "注意热处理")
    assert repository.list_records(project.id, query="热处理")[0].part_name == "导向轮支架"


@pytest.mark.parametrize("code", ["", "DQ 16", "DQ/16", "中文代号"])
def test_invalid_codes_are_rejected(services, code):
    _, _, projects, _ = services
    with pytest.raises(ValidationError):
        projects.create("项目", "说明", code)
