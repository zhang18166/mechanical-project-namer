# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json
import sqlite3
from unittest.mock import Mock
from threading import Event, Thread

import pytest
import httpx

from mechanical_project_namer.data import Database, DataExporter, Repository
from mechanical_project_namer.ai.client import AIClient, AICancelled
from mechanical_project_namer.ai.worker import CancellationToken
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.settings import AppSettings
from mechanical_project_namer.ui.main_window import MainWindow
from mechanical_project_namer.ui.shell import ApplicationShell
from scripts import release_manager


def services(tmp_path):
    database = Database(tmp_path / "data.db")
    database.migrate()
    repository = Repository(database)
    project = ProjectService(repository).create("设备", "", "DEV")
    return database, repository, project, DataExporter(repository, tmp_path / "exports")


def test_query_connection_releases_handles_and_rolls_back(tmp_path):
    database, _, _, _ = services(tmp_path)
    with database.connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    with pytest.raises(RuntimeError):
        with database.connect() as connection:
            connection.execute("UPDATE projects SET notes='should rollback'")
            raise RuntimeError("interrupted")
    with database.connect() as connection:
        assert connection.execute("SELECT notes FROM projects").fetchone()[0] == ""


def test_unchanged_export_performs_no_full_query_or_write(tmp_path, monkeypatch):
    _, repository, project, exporter = services(tmp_path)
    exporter.export_all(project.id)
    before = {p.name: p.stat().st_mtime_ns for p in exporter.directory.iterdir()}
    monkeypatch.setattr(repository, "list_records", Mock(side_effect=AssertionError("repeated full scan")))
    exporter.export_all(project.id)
    assert before == {p.name: p.stat().st_mtime_ns for p in exporter.directory.iterdir()}


def test_current_project_change_only_rewrites_current_state(tmp_path, monkeypatch):
    _, repository, project, exporter = services(tmp_path)
    other = ProjectService(repository).create("另一设备", "", "OTHER")
    exporter.export_all(project.id)
    before = (exporter.directory / "项目数据快照.json").read_bytes()
    monkeypatch.setattr(repository, "list_records", Mock(side_effect=AssertionError("full scan on selection")))
    exporter.export_all(other.id)
    assert (exporter.directory / "项目数据快照.json").read_bytes() == before
    assert json.loads((exporter.directory / "当前项目状态.json").read_text(encoding="utf-8"))["current_project_id"] == other.id


def test_export_detects_wal_commit_and_recreates_missing_file(tmp_path):
    database, repository, project, exporter = services(tmp_path)
    # Keep another connection open so the commit remains in WAL.
    with database.connect():
        exporter.export_all(project.id)
        repository.save_named_record(project.id, "03", "自制机加工件", "轴")
        exporter.export_all(project.id)
        snapshot = json.loads((exporter.directory / "项目数据快照.json").read_text(encoding="utf-8"))
        assert len(snapshot["records"]) == 1
        (exporter.directory / "当前项目状态.json").rename(exporter.directory / "state.old")
        exporter.export_all(project.id)
        assert (exporter.directory / "当前项目状态.json").is_file()


def test_export_retries_after_write_failure(tmp_path, monkeypatch):
    _, _, project, exporter = services(tmp_path)
    original = exporter._atomic_json
    monkeypatch.setattr(exporter, "_atomic_json", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        exporter.export_all(project.id)
    monkeypatch.setattr(exporter, "_atomic_json", original)
    exporter.export_all(project.id)
    assert len(list(exporter.directory.glob("*.json"))) == 3


def test_compact_home_keeps_ai_controls_and_module_actions(qtbot):
    window = MainWindow(AppSettings())
    qtbot.addWidget(window)
    window.resize(1280, 720)
    window.show()
    qtbot.wait(30)
    assert not window.ai_content.isVisible()
    assert window.part_name.isVisible() and window.solidworks_button.isVisible()
    window.ai_toggle.click()
    assert window.ai_mode.isVisible() and window.plan_history_button.isVisible()
    window.set_ai_busy(True)
    assert window.ai_cancel.isVisible()
    window.ai_toggle.setChecked(False)
    window.set_ai_busy(True)
    assert window.ai_content.isVisible()
    modules = [{"module_code": "03", "module_name": "成型", "enabled": True}]
    window._rebuild_module_buttons(modules, "03")
    button = window.module_buttons[0]
    received = []
    window.module_selected.connect(received.append)
    window._rebuild_module_buttons(modules, "03")
    assert window.module_buttons[0] is button
    button.click()
    assert received == ["03"]


def test_save_guard_is_released_after_unexpected_failure():
    shell = ApplicationShell.__new__(ApplicationShell)
    shell._save_in_progress = False
    shell._save_record_impl = Mock(side_effect=RuntimeError("refresh failed"))
    with pytest.raises(RuntimeError):
        shell._save_record("轴", "", "")
    assert shell._save_in_progress is False


def test_backup_captures_uncheckpointed_wal(tmp_path, monkeypatch):
    monkeypatch.setattr(release_manager, "ROOT", tmp_path)
    database, repository, project, _ = services(tmp_path)
    with database.connect():
        repository.save_named_record(project.id, "03", "总成", "总成")
        backup = release_manager.backup_database(database.path)
        with sqlite3.connect(backup) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("SELECT part_sequence FROM drawing_records").fetchone()[0] == 0


def test_failed_upgrade_preserves_all_three_packages(tmp_path, monkeypatch):
    release = tmp_path / "release"
    for name in ("current", "previous", "staging"):
        folder = release / name
        folder.mkdir(parents=True)
        (folder / "机械项目命名助手.exe").write_bytes(name.encode())
    monkeypatch.setattr(release_manager, "ROOT", tmp_path)
    monkeypatch.setattr(release_manager, "RELEASE", release)
    monkeypatch.setattr(release_manager, "validate_package", lambda _: {})
    def broken_log(event, **details):
        if event == "upgrade_success":
            raise OSError("cannot log upgrade")
    monkeypatch.setattr(release_manager, "log_event", broken_log)
    with pytest.raises(OSError):
        release_manager.upgrade(release / "staging")
    for name in ("current", "previous", "staging"):
        assert (release / name / "机械项目命名助手.exe").read_bytes() == name.encode()


def test_upgrade_rejects_bad_package_before_moving_current(tmp_path, monkeypatch):
    release = tmp_path / "release"
    for name in ("current", "staging"):
        folder = release / name
        folder.mkdir(parents=True)
        (folder / "机械项目命名助手.exe").write_bytes(name.encode())
    (release / "staging" / "version.json").write_text(json.dumps({"exe_sha256": "bad"}))
    monkeypatch.setattr(release_manager, "ROOT", tmp_path)
    monkeypatch.setattr(release_manager, "RELEASE", release)
    with pytest.raises(RuntimeError, match="校验值"):
        release_manager.upgrade(release / "staging")
    assert (release / "current" / "机械项目命名助手.exe").read_bytes() == b"current"


def test_cancel_releases_blocked_response_without_external_network():
    class BlockingStream(httpx.SyncByteStream):
        def __init__(self):
            self.reading = Event()
            self.closed = Event()

        def __iter__(self):
            self.reading.set()
            self.closed.wait(3)
            if self.closed.is_set():
                raise httpx.ReadError("response closed")
            yield b"data: [DONE]\n\n"

        def close(self):
            self.closed.set()

    stream = BlockingStream()
    client = AIClient("https://example.invalid", "model", "test-key",
                      http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))))
    token = CancellationToken()
    errors = []
    def run():
        try:
            client.engineering_chat_stream([{"role": "user", "content": "test"}], token)
        except Exception as error:
            errors.append(error)
    thread = Thread(target=run)
    thread.start()
    assert stream.reading.wait(1)
    token.set()
    thread.join(1)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], AICancelled)
    assert not token._callbacks
    client.http_client.close()


def test_shutdown_waits_for_worker_before_quitting(qtbot, monkeypatch):
    from types import SimpleNamespace

    shell = ApplicationShell.__new__(ApplicationShell)
    thread = Mock()
    thread.isRunning.side_effect = [True, False]
    shell.ai_tasks = {"task": SimpleNamespace(thread=thread)}
    shell.main_window = SimpleNamespace(allow_close=False)
    shell.mini_window = SimpleNamespace(allow_close=False)
    shell.tray = Mock()
    shell.app = Mock()
    shell._finish_quit()
    shell.app.quit.assert_not_called()
    qtbot.waitUntil(lambda: shell.app.quit.call_count == 1)
    assert shell.main_window.allow_close and shell.mini_window.allow_close


def test_package_identity_handles_windows_archive_paths_and_rejects_mismatch(tmp_path, monkeypatch):
    from PyInstaller.archive import readers

    (tmp_path / "机械项目命名助手.exe").write_bytes(b"exe")
    identity = {"version": "1.6.1", "git_commit": "abc", "git_dirty": False}
    metadata = {**identity, "exe_sha256": release_manager.sha256(tmp_path / "机械项目命名助手.exe"), "git_tags": ["v1.6.1"]}
    (tmp_path / "version.json").write_text(json.dumps(metadata))
    class Archive:
        toc = {"assets\\build_info.json": None}
        def __init__(self, _):
            pass
        def extract(self, name):
            assert name == "assets\\build_info.json"
            return json.dumps(identity).encode()
    monkeypatch.setattr(readers, "CArchiveReader", Archive)
    monkeypatch.setattr(release_manager, "git_output", lambda *args: "abc")
    assert release_manager.validate_package(tmp_path)["version"] == "1.6.1"
    identity["git_commit"] = "different"
    with pytest.raises(RuntimeError, match="git_commit"):
        release_manager.validate_package(tmp_path)
