# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from pathlib import Path

import pytest

from mechanical_project_namer.data import Database, Repository
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.solidworks.bridge import SolidWorksBridge, _call_or_value
from mechanical_project_namer.solidworks.compatibility import parse_revision, support_level
from mechanical_project_namer.solidworks.connection import ProcessInfo, require_single_process
from mechanical_project_namer.solidworks.errors import SolidWorksError
from mechanical_project_namer.solidworks.controller import SolidWorksWorkerController
from mechanical_project_namer.solidworks.models import OperationRequest, OperationResult
from mechanical_project_namer.solidworks.service import SolidWorksService
from mechanical_project_namer.solidworks.validation import validate_part_name, validate_target_path
from mechanical_project_namer.solidworks.worker_entry import run_worker
from mechanical_project_namer.settings import AppSettings, load_settings, save_settings
from mechanical_project_namer.ui.main_window import MainWindow
from mechanical_project_namer.ui.mini_window import MiniWindow


class FakeDocument:
    def __init__(self, title="原文档.SLDASM", path="D:/design/original.SLDASM"):
        self.title = title
        self.path = path
        self.saved_paths = []

    def GetTitle(self):
        return self.title

    def GetPathName(self):
        return self.path

    def SaveAs3(self, path, _version, _options):
        Path(path).write_bytes(b"fake-solidworks-part")
        self.path = path
        self.title = Path(path).name
        self.saved_paths.append(path)
        return True


class FakeApplication:
    RevisionNumber = "33.5.0"

    def __init__(self, template):
        self.template = str(template)
        self.original = FakeDocument()
        self.ActiveDoc = self.original
        self.new_document = None
        self.activated = ""

    def GetBuildNumbers(self):
        return "33.5.0.12345"

    def GetUserPreferenceStringValue(self, preference):
        assert preference == 8
        return self.template

    def GetDocumentCount(self):
        return 1 + int(self.new_document is not None)

    def NewDocument(self, template, _paper, _width, _height):
        assert template == self.template
        self.new_document = FakeDocument("Part1", "")
        return self.new_document

    def ActivateDoc3(self, title, _use_preferences, _rebuild, _errors):
        self.activated = title
        self.ActiveDoc = self.new_document
        return self.new_document


@pytest.fixture
def sw_project(tmp_path):
    database = Database(tmp_path / "v140.db")
    database.migrate()
    repository = Repository(database)
    project = ProjectService(repository).create(
        "压圈机", "", "ZGQ", directory=str(tmp_path), solidworks_part_directory=str(tmp_path / "parts")
    )
    (tmp_path / "parts").mkdir()
    return database, repository, project, SolidWorksService(repository, tmp_path / "recovery")


def test_schema_has_solidworks_fields(sw_project):
    database, _, project, _ = sw_project
    assert project.solidworks_part_directory.endswith("parts")
    with database.connect() as connection:
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] >= 5
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='solidworks_operations'").fetchone()
        fields = {row[1] for row in connection.execute("PRAGMA table_info(drawing_records)")}
    assert {"file_path", "file_size", "solidworks_operation_id"} <= fields


@pytest.mark.parametrize(("revision", "year", "sp"), [("33.5.0", 2025, "SP5"), ("34.1.2", 2026, "SP1")])
def test_revision_detection(revision, year, sp):
    assert parse_revision(revision) == (year, sp)
    assert support_level(year) == (True, "official")


def test_unsupported_revision_and_process_counts():
    assert support_level(2024) == (False, "below_minimum")
    with pytest.raises(SolidWorksError, match="未检测"):
        require_single_process([])
    processes = [ProcessInfo(1, "SLDWORKS.exe"), ProcessInfo(2, "SLDWORKS.exe")]
    with pytest.raises(SolidWorksError, match="多个"):
        require_single_process(processes)


@pytest.mark.parametrize("name", ["bad/name", "CON", "尾点.", " 前空格"])
def test_invalid_windows_names_are_rejected(name):
    with pytest.raises(SolidWorksError):
        validate_part_name(name)


def test_duplicate_file_is_never_overwritten(tmp_path):
    target = tmp_path / "ZGQ-03-001 滑座.SLDPRT"
    target.write_bytes(b"existing")
    with pytest.raises(SolidWorksError) as error:
        validate_target_path(str(target))
    assert error.value.code == "file_exists"
    assert target.read_bytes() == b"existing"


def test_fake_bridge_creates_new_part_and_preserves_original(tmp_path):
    template = tmp_path / "Part.prtdot"
    template.write_bytes(b"template")
    target = tmp_path / "ZGQ-03-001 压弧滑座.SLDPRT"
    app = FakeApplication(template)
    bridge = SolidWorksBridge(
        process_provider=lambda: ProcessInfo(1234, "SLDWORKS.exe"), connector=lambda: app
    )
    result = bridge.execute(OperationRequest("op-1", target_path=str(target)))
    assert result.success and result.file_size > 0
    assert result.info.year == 2025 and result.info.service_pack == "SP5"
    assert result.original_document_preserved
    assert app.original.saved_paths == []
    assert app.new_document.saved_paths == [str(target)]
    assert app.activated == target.name


def test_reservation_does_not_advance_until_verified_commit(sw_project, tmp_path):
    _, repository, project, service = sw_project
    reserved = service.reserve(project.id, "03", "自制机加工件", "压弧滑座", project.solidworks_part_directory)
    assert reserved["part_sequence"] == 1
    assert repository.get_next_named_number(project.id, "03", "自制机加工件") == 1
    template = tmp_path / "Part.prtdot"
    template.write_bytes(b"template")
    app = FakeApplication(template)
    result = SolidWorksBridge(lambda: ProcessInfo(8, "SLDWORKS.exe"), lambda: app).execute(
        OperationRequest(reserved["operation_id"], target_path=reserved["target_path"])
    )
    record = service.commit_created_file(reserved["operation_id"], result)
    assert record.file_exists and record.file_size > 0
    assert record.creation_source == "solidworks_one_click"
    assert repository.get_next_named_number(project.id, "03", "自制机加工件") == 2


def test_failed_reservation_releases_number(sw_project):
    _, repository, project, service = sw_project
    first = service.reserve(project.id, "03", "自制机加工件", "失败件", project.solidworks_part_directory)
    service.fail(first["operation_id"], SolidWorksError("save_failed", "保存失败", "file_saved"))
    second = service.reserve(project.id, "03", "自制机加工件", "重试件", project.solidworks_part_directory)
    assert second["part_sequence"] == first["part_sequence"] == 1
    assert repository.get_next_named_number(project.id, "03", "自制机加工件") == 1


def test_ui_exposes_full_mini_and_settings_controls(qtbot):
    settings = AppSettings()
    full = MainWindow(settings)
    mini = MiniWindow()
    qtbot.addWidget(full)
    qtbot.addWidget(mini)
    assert full.solidworks_button.text() == "创建并打开SW零件"
    assert full.solidworks_log.isReadOnly()
    assert mini.solidworks_button.text() == "创建SW零件"
    assert "空白零件" in mini.solidworks_button.toolTip()
    assert full.settings_page.sw_confirm.isChecked()
    assert full.settings_page.sw_timeout.value() == 120


def test_solidworks_settings_roundtrip(tmp_path):
    target = tmp_path / "settings.json"
    settings = AppSettings(
        solidworks_fallback_template="D:/templates/Part.prtdot",
        solidworks_confirm_before_create=False,
        solidworks_overall_timeout=180,
    )
    save_settings(settings, target)
    loaded = load_settings(target)
    assert loaded.solidworks_fallback_template.endswith("Part.prtdot")
    assert loaded.solidworks_confirm_before_create is False
    assert loaded.solidworks_overall_timeout == 180


def test_csv_export_includes_physical_file_columns(sw_project, tmp_path):
    _, repository, project, _ = sw_project
    from mechanical_project_namer.data import DataExporter

    target = tmp_path / "records.csv"
    DataExporter(repository, tmp_path / "exports").export_csv(target, project.id)
    header = target.read_text(encoding="utf-8-sig").splitlines()[0]
    assert "物理文件路径" in header and "文件大小" in header and "创建来源" in header


class FakeWorkerProcess:
    def __init__(self):
        self.killed = False
        self.deleted = False

    def kill(self):
        self.killed = True

    def waitForFinished(self, _timeout):
        return True

    def deleteLater(self):
        self.deleted = True


def test_worker_timeout_and_cancel_only_terminate_helper(qtbot):
    controller = SolidWorksWorkerController()
    timed_out = []
    controller.failed.connect(timed_out.append)
    process = FakeWorkerProcess()
    controller.process = process
    controller._on_timeout()
    controller._on_finished(0, None)
    assert process.killed and timed_out[0].code == "worker_timeout"

    cancelled = []
    controller.failed.connect(cancelled.append)
    process = FakeWorkerProcess()
    controller.process = process
    controller.cancel()
    assert process.killed and cancelled[-1].code == "worker_cancelled"


def test_database_failure_writes_recovery_and_reconciles(sw_project, tmp_path, monkeypatch):
    _, repository, project, service = sw_project
    reserved = service.reserve(project.id, "03", "自制机加工件", "恢复测试件", project.solidworks_part_directory)
    template = tmp_path / "Part.prtdot"
    template.write_bytes(b"template")
    result = SolidWorksBridge(lambda: ProcessInfo(9, "SLDWORKS.exe"), lambda: FakeApplication(template)).execute(
        OperationRequest(reserved["operation_id"], target_path=reserved["target_path"])
    )
    real_commit = service._commit
    monkeypatch.setattr(service, "_commit", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("database unavailable")))
    with pytest.raises(SolidWorksError) as error:
        service.commit_created_file(reserved["operation_id"], result)
    assert error.value.code == "database_commit_failed"
    assert (service.recovery_directory / f"{reserved['operation_id']}.json").is_file()
    monkeypatch.setattr(service, "_commit", real_commit)
    outcomes = service.reconcile_pending()
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome["operation_id"] == reserved["operation_id"]
    assert outcome["success"] is True
    assert outcome["record_id"] == 1
    # 1.6.0 阶段二 A 新增字段：善后删除是否成功。它取决于运行环境（例如宿主的安全删除守卫
    # 可能拒绝删除），因此只要求字段存在，不作为失败条件。
    assert "recovery_file_removed" in outcome
    assert repository.get_next_named_number(project.id, "03", "自制机加工件") == 2


def test_windowed_worker_writes_result_without_stdout(tmp_path, monkeypatch):
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(
        __import__("json").dumps({"operation_id": "windowed", "action": "probe", "result_path": str(result_path)}),
        encoding="utf-8",
    )

    class ProbeBridge:
        def execute(self, request, progress):
            progress("connected", "ok")
            return OperationResult(request.operation_id, True, "completed", "ok")

    monkeypatch.setattr("sys.stdout", None)
    assert run_worker(str(request_path), ProbeBridge()) == 0
    assert __import__("json").loads(result_path.read_text(encoding="utf-8"))["success"] is True


def test_dispatch_objects_are_not_mistaken_for_bound_methods():
    class CallableDispatch:
        _oleobj_ = object()

        def __call__(self):
            raise AssertionError("COM dispatch object must not be invoked as a method")

    dispatch = CallableDispatch()
    assert _call_or_value(type("Owner", (), {"Extension": dispatch})(), "Extension") is dispatch
