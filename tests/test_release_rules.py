# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json
import sqlite3

import pytest
from PyQt6.QtCore import Qt

from mechanical_project_namer.data import DataExporter, Database, Repository
from mechanical_project_namer.numbering.workflow import NamingWorkflow
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.security import CredentialStore
from mechanical_project_namer.ui.mini_window import MiniWindow


def make_services(tmp_path, exporter_type=DataExporter):
    database = Database(tmp_path / "data.db")
    database.migrate()
    repository = Repository(database)
    projects = ProjectService(repository)
    exporter = exporter_type(repository, tmp_path / "exports")
    return database, repository, projects, NamingWorkflow(repository, exporter), exporter


def test_sqlite_uses_wal_mode(tmp_path):
    database, *_ = make_services(tmp_path)
    with database.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_custom_initial_number_is_used(tmp_path):
    _, _, projects, workflow, _ = make_services(tmp_path)
    project = projects.create("测试设备", "", "CS-20", start_number=20)
    assert workflow.preview(project.id) == "CS-20-020"


def test_drawing_999_is_last_available_number(tmp_path):
    _, repository, projects, workflow, _ = make_services(tmp_path)
    project = projects.create("极限测试", "", "LIMIT", start_number=999)
    record = workflow.save_and_advance(project.id, "最后一件", "极限测试")
    assert record.filename == "LIMIT-999"
    assert repository.get_project(project.id).next_filename == "图号已用尽"
    with pytest.raises(ValueError, match="用尽"):
        workflow.save_and_advance(project.id, "不可保存", "")


def test_archiving_keeps_project_and_records(tmp_path):
    _, repository, projects, workflow, _ = make_services(tmp_path)
    project = projects.create("归档设备", "", "GD-1")
    workflow.save_and_advance(project.id, "轴", "")
    repository.set_archived(project.id)
    assert repository.list_projects() == []
    assert repository.get_project(project.id).archived is True
    assert len(repository.list_records(project.id)) == 1


class BrokenExporter(DataExporter):
    def export_all(self, current_project_id=None):
        raise OSError("disk unavailable")


def test_json_export_failure_does_not_rollback_sqlite(tmp_path):
    _, repository, projects, workflow, _ = make_services(tmp_path, BrokenExporter)
    project = projects.create("导出故障设备", "", "DC-1")
    record = workflow.save_and_advance(project.id, "驱动轴", "")
    assert repository.get_record(record.id).filename == "DC-1-001"
    assert repository.get_project(project.id).next_number == 2


def test_csv_export_is_excel_friendly_utf8(tmp_path):
    _, _, projects, workflow, exporter = make_services(tmp_path)
    project = projects.create("中文项目", "", "CN-1")
    workflow.save_and_advance(project.id, "导向轮", "固定导向轮", "材料45钢")
    target = tmp_path / "records.csv"
    assert exporter.export_csv(target, project.id) == 1
    raw = target.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert "导向轮" in raw.decode("utf-8-sig")


class FakeCredentialBackend:
    def __init__(self):
        self.value = None

    def get_password(self, service, account):
        return self.value

    def set_password(self, service, account, value):
        self.value = value


def test_api_key_roundtrip_only_uses_credential_backend():
    backend = FakeCredentialBackend()
    store = CredentialStore(backend)
    assert store.get_api_key() is None
    store.set_api_key(" secret-key ")
    assert store.get_api_key() == "secret-key"


def test_mini_enter_emits_save_without_changing_preview(qtbot):
    window = MiniWindow()
    qtbot.addWidget(window)
    received = []
    window.save_button.setEnabled(True)
    window.preview.setText("CS-1-001")
    window.part_name.setText("手柄轮")
    window.save_requested.connect(lambda *values: received.append(values))
    window.show()
    qtbot.waitExposed(window)
    window.activateWindow()
    window.part_name.setFocus()
    qtbot.keyClick(window.part_name, Qt.Key.Key_Return)
    assert received == [("手柄轮", "", "")]
    assert window.preview.text() == "CS-1-001"
