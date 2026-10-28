# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""阶段二 A（1.6.0）回归测试：总成 000 单例策略 + SolidWorks 失败锁号恢复。

对应 PROJECT_HANDOVER.md 的 P1-1 / P1-3。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from mechanical_project_namer.data import Database, Repository
from mechanical_project_namer.data.database import Database as _Database
from mechanical_project_namer.data.repository import SingletonNumberExistsError
from mechanical_project_namer.numbering import is_singleton_part_type, part_type_range, singleton_sequence
from mechanical_project_namer.numbering.workflow import NamingWorkflow
from mechanical_project_namer.data import DataExporter
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.solidworks.errors import SolidWorksError
from mechanical_project_namer.solidworks.models import OperationResult, SolidWorksInfo
from mechanical_project_namer.solidworks.service import SolidWorksService


# --------------------------------------------------------------------- fixtures


@pytest.fixture()
def services(tmp_path):
    database = Database(tmp_path / "p2a.db")
    database.migrate()
    repository = Repository(database)
    project = ProjectService(repository).create(
        "压圈机", "", "ZGQ", directory=str(tmp_path), solidworks_part_directory=str(tmp_path / "parts")
    )
    (tmp_path / "parts").mkdir()
    service = SolidWorksService(repository, tmp_path / "recovery")
    workflow = NamingWorkflow(repository, DataExporter(repository, tmp_path / "exports"))
    return database, repository, project, service, workflow


def _operations(database, operation_id: str) -> dict:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM solidworks_operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
    return dict(row) if row else {}


def _counters(database, project_id: int, module_code: str, part_type: str):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT next_sequence FROM part_sequence_counters "
            "WHERE project_id=? AND module_code=? AND part_type=?",
            (project_id, module_code, part_type),
        ).fetchone()
    return row["next_sequence"] if row else None


def _age(database, operation_id: str, hours: int) -> None:
    old = (datetime.now().astimezone() - timedelta(hours=hours)).isoformat(timespec="seconds")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE solidworks_operations SET updated_at=? WHERE operation_id=?", (old, operation_id)
        )


def _force_status(database, operation_id: str, status: str) -> None:
    with database.transaction() as connection:
        connection.execute(
            "UPDATE solidworks_operations SET status=? WHERE operation_id=?", (status, operation_id)
        )


# ------------------------------------------------------- 1. 规则层：单例类别定义


def test_singleton_part_type_constants():
    assert is_singleton_part_type("总成")
    assert not is_singleton_part_type("自制机加工件")
    assert singleton_sequence("总成") == 0
    assert part_type_range("总成") == (0, 0)
    assert part_type_range("自制机加工件") == (1, 99)
    assert part_type_range("钣金/焊接件") == (101, 199)
    assert part_type_range("采购/标准件") == (201, 299)
    with pytest.raises(ValueError):
        singleton_sequence("自制机加工件")


# ------------------------------------------------- 2. 总成固定 000、每模块仅一个


def test_singleton_returns_zero_then_refuses_second(services):
    _, repository, project, _, workflow = services
    assert repository.get_next_named_number(project.id, "03", "总成") == 0
    assert workflow.copy_named_preview(project.id, "03", "总成", "压弧总成") == "ZGQ-03-000 压弧总成"

    record = workflow.save_named_and_advance(project.id, "03", "总成", "压弧总成")
    assert record.part_sequence == 0
    assert record.full_name == "ZGQ-03-000 压弧总成"
    assert record.filename == "ZGQ-03-000"

    for call in (lambda: repository.get_next_named_number(project.id, "03", "总成"),
                 lambda: workflow.copy_named_preview(project.id, "03", "总成", "再来一个"),
                 lambda: repository.save_named_record(project.id, "03", "总成", "再来一个")):
        with pytest.raises(SingletonNumberExistsError) as error:
            call()
        # 必须是明确的“已存在”提示，绝不允许出现“序号已用尽”
        assert str(error.value) == "该模块已存在总成 000"
        assert "用尽" not in str(error.value)


def test_singleton_never_writes_or_advances_counter(services):
    _, repository, project, _, workflow = services
    workflow.save_named_and_advance(project.id, "03", "总成", "压弧总成")
    assert _counters(repository.database, project.id, "03", "总成") is None


def test_singleton_is_per_module(services):
    _, repository, project, _, workflow = services
    workflow.save_named_and_advance(project.id, "03", "总成", "模块三总成")
    # 同一项目的另一个模块仍可创建自己的总成
    assert repository.get_next_named_number(project.id, "04", "总成") == 0
    other = workflow.save_named_and_advance(project.id, "04", "总成", "模块四总成")
    assert other.full_name == "ZGQ-04-000 模块四总成"


def test_singleton_in_sw_reserve_path(services):
    _, repository, project, service, _ = services
    reserved = service.reserve(project.id, "03", "总成", "压弧总成", project.solidworks_part_directory)
    assert reserved["part_sequence"] == 0
    assert reserved["full_name"] == "ZGQ-03-000 压弧总成"
    assert reserved["target_path"].endswith("ZGQ-03-000 压弧总成.SLDPRT")
    assert _counters(repository.database, project.id, "03", "总成") is None

    # 尚未写库但有活动预留时：明确告知“总成正在创建中”，而不是晦涩的占用错误
    with pytest.raises(SolidWorksError) as pending_error:
        service.reserve(project.id, "03", "总成", "重复总成", project.solidworks_part_directory)
    assert pending_error.value.code == "singleton_reserved"
    assert "正在创建中" in str(pending_error.value)
    assert "用尽" not in str(pending_error.value)


def test_singleton_sw_reserve_refused_after_commit(services, tmp_path):
    _, _, project, service, _ = services
    reserved = service.reserve(project.id, "03", "总成", "压弧总成", project.solidworks_part_directory)
    target = tmp_path / "ZGQ-03-000 压弧总成.SLDPRT"
    target.write_bytes(b"fake-part")
    service.commit_created_file(reserved["operation_id"], OperationResult(
        reserved["operation_id"], True, "file_saved", "ok",
        info=SolidWorksInfo(connected=False, revision="2025"),
        target_path=str(target), template_path="Part.prtdot", file_size=9,
        document_path=str(target), original_document_preserved=True,
    ))
    with pytest.raises(SolidWorksError) as error:
        service.reserve(project.id, "03", "总成", "再来一个", project.solidworks_part_directory)
    assert error.value.code == "singleton_exists"
    assert str(error.value) == "该模块已存在总成 000"


def test_singleton_commit_does_not_touch_counter(services, tmp_path):
    _, repository, project, service, _ = services
    reserved = service.reserve(project.id, "03", "总成", "压弧总成", project.solidworks_part_directory)
    target = tmp_path / "ZGQ-03-000 压弧总成.SLDPRT"
    target.write_bytes(b"fake-part")
    result = OperationResult(
        reserved["operation_id"], True, "file_saved", "ok",
        info=SolidWorksInfo(connected=False, revision="2025"),
        target_path=str(target), template_path="Part.prtdot", file_size=9,
        document_path=str(target), original_document_preserved=True,
    )
    record = service.commit_created_file(reserved["operation_id"], result)
    assert record.part_sequence == 0
    assert _counters(repository.database, project.id, "03", "总成") is None
    # 普通类别完全不受影响
    assert repository.get_next_named_number(project.id, "03", "自制机加工件") == 1


def test_numbering_status_shows_existing_singleton(services):
    _, repository, project, _, workflow = services
    workflow.save_named_and_advance(project.id, "03", "总成", "压弧总成")
    status = {item["module_code"]: item for item in repository.project_numbering_status(project.id)}
    cheng = next(t for t in status["03"]["types"] if t["part_type"] == "总成")
    assert cheng["next_number"] == "已存在 000"
    assert "用尽" not in cheng["next_number"]


# --------------------------------------------------------- 3. 普通编号区间不受影响


def test_ordinary_ranges_unaffected(services):
    _, repository, project, _, workflow = services
    assert repository.get_next_named_number(project.id, "03", "自制机加工件") == 1
    assert repository.get_next_named_number(project.id, "03", "钣金/焊接件") == 101
    assert repository.get_next_named_number(project.id, "03", "采购/标准件") == 201
    assert workflow.save_named_and_advance(project.id, "03", "总成", "先建总成").part_sequence == 0
    assert repository.get_next_named_number(project.id, "03", "自制机加工件") == 1
    assert workflow.save_named_and_advance(project.id, "03", "自制机加工件", "一号").part_sequence == 1
    assert workflow.save_named_and_advance(project.id, "03", "自制机加工件", "二号").part_sequence == 2
    assert workflow.save_named_and_advance(project.id, "03", "钣金/焊接件", "钣金一号").part_sequence == 101
    assert workflow.save_named_and_advance(project.id, "03", "采购/标准件", "采购一号").part_sequence == 201


# --------------------------------------------- 4. migrate 幂等 + 脏数据自动修复


def _legacy_corrupted_database(path, *, sequence: int, drawing_number: int) -> Database:
    """复现旧版本 bug：合法总成 0 被改写成 drawing_number，并留下错误计数器。"""
    database = Database(path)
    database.migrate()
    repository = Repository(database)
    project = ProjectService(repository).create("旧库项目", "", "OLD1", directory=str(path))
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO drawing_records
               (project_id,project_name,project_code,drawing_number,filename,part_name,purpose,record_date,
                created_at,updated_at,status,is_copied,creation_method,project_directory,extra_notes,
                module_code,part_sequence,part_type,full_name)
               VALUES (?,?,?,?,?,?,?,?,?,?,'used',0,'solidworks_one_click','','','03',?,'总成',?)""",
            (project.id, project.name, project.code, drawing_number, f"{project.code}-03-000",
             "滚珠丝杠螺母安装座", "", "2026-09-08",
             "2026-09-08T15:14:16+08:00", "2026-09-08T15:14:16+08:00",
             sequence, f"{project.code}-03-000 滚珠丝杠螺母安装座"),
        )
        connection.execute(
            "INSERT INTO part_sequence_counters(project_id,module_code,part_type,next_sequence,updated_at) "
            "VALUES (?,?,?,?,?)",
            (project.id, "03", "总成", drawing_number + 1, "2026-09-08T15:14:16+08:00"),
        )
        connection.execute(
            "INSERT INTO number_reservations(project_id,module_code,part_category,part_sequence,status,"
            "original_record_id,reserved_at,reason) VALUES (?,?,?,?,?,?,?,?)",
            (project.id, "03", "总成", drawing_number, "used", 1, "2026-09-08T15:14:16+08:00", "solidworks_created"),
        )
    return database


def test_migrate_restores_corrupted_singleton_and_is_idempotent(tmp_path):
    database = _legacy_corrupted_database(tmp_path / "legacy.db", sequence=17, drawing_number=17)

    def snapshot():
        with database.connect() as connection:
            records = [dict(r) for r in connection.execute(
                "SELECT id,part_sequence,drawing_number,filename,full_name FROM drawing_records ORDER BY id")]
            counters = [dict(r) for r in connection.execute(
                "SELECT project_id,module_code,part_type,next_sequence FROM part_sequence_counters ORDER BY 1,2,3")]
            reservations = [dict(r) for r in connection.execute(
                "SELECT project_id,module_code,part_category,part_sequence FROM number_reservations ORDER BY 1,2,3,4")]
        return records, counters, reservations

    database.migrate()
    records, counters, reservations = snapshot()
    assert records[0]["part_sequence"] == 0
    assert records[0]["drawing_number"] == 17        # drawing_number 保持不变
    assert records[0]["full_name"] == "OLD1-03-000 滚珠丝杠螺母安装座"
    assert records[0]["filename"] == "OLD1-03-000"
    assert not [item for item in counters if item["part_type"] == "总成"]
    assert all(item["part_sequence"] == 0 for item in reservations if item["part_category"] == "总成")

    first = snapshot()
    database.migrate()
    database.migrate()
    assert snapshot() == first  # 连续执行三次结果完全一致
    with database.connect() as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migrate_never_touches_a_healthy_singleton(tmp_path):
    database = Database(tmp_path / "healthy.db")
    database.migrate()
    repository = Repository(database)
    project = ProjectService(repository).create("新库项目", "", "NEW1", directory=str(tmp_path))
    record = repository.save_named_record(project.id, "03", "总成", "压弧总成")
    assert record.part_sequence == 0

    def snapshot():
        with database.connect() as connection:
            return [dict(r) for r in connection.execute(
                "SELECT id,part_sequence,drawing_number,full_name FROM drawing_records ORDER BY id")]

    before = snapshot()
    for _ in range(3):
        database.migrate()
    assert snapshot() == before
    assert Repository(database).get_record(record.id).part_sequence == 0


def test_migrate_still_backfills_true_legacy_rows(tmp_path):
    """legacy 行的 part_sequence 补值必须保留（只针对 part_type='legacy'）。"""
    database = Database(tmp_path / "legacy_backfill.db")
    database.migrate()
    repository = Repository(database)
    project = ProjectService(repository).create("旧行项目", "", "OLD2", directory=str(tmp_path))
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO drawing_records
               (project_id,project_name,project_code,drawing_number,filename,part_name,purpose,record_date,
                created_at,updated_at,status,is_copied,creation_method,project_directory,extra_notes,
                module_code,part_sequence,part_type,full_name)
               VALUES (?,?,?,?,?,?,?,?,?,?,'used',0,'manual','','','00',0,'legacy','')""",
            (project.id, project.name, project.code, 4, f"{project.code}-004", "老图纸", "",
             "2026-01-01", "2026-01-01T00:00:00+08:00", "2026-01-01T00:00:00+08:00"),
        )
    database.migrate()
    with database.connect() as connection:
        row = connection.execute(
            "SELECT part_sequence,part_type FROM drawing_records WHERE filename=?", (f"{project.code}-004",)
        ).fetchone()
    assert row["part_type"] == "legacy"
    assert row["part_sequence"] == 4


# -------------------------------------------- 5. P1-3：失败写库失败必须留痕


def test_fail_writes_status_recovery_file_when_database_write_fails(services, monkeypatch):
    _, _, project, service, _ = services
    reserved = service.reserve(project.id, "03", "自制机加工件", "滑座", project.solidworks_part_directory)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(service, "update_status", broken)
    outcome = service.fail(reserved["operation_id"], SolidWorksError("worker_error", "模拟保存失败", "saving"),
                           physical_file_exists=False)

    assert outcome["persisted"] == "recovery_file"
    path = service.status_recovery_directory / f"{reserved['operation_id']}.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["operation_id"] == reserved["operation_id"]
    assert payload["project_id"] == project.id
    assert payload["project_code"] == project.code
    assert payload["module_code"] == "03"
    assert payload["part_type"] == "自制机加工件"
    assert payload["part_sequence"] == 1
    assert payload["target_path"] == reserved["target_path"]
    assert "模拟保存失败" in payload["error_message"]
    assert payload["status_write_error"]
    assert service.list_status_recovery_files()


def test_commit_failure_keeps_both_recovery_files(services, monkeypatch, tmp_path):
    _, _, project, service, _ = services
    reserved = service.reserve(project.id, "03", "自制机加工件", "滑座", project.solidworks_part_directory)
    target = tmp_path / "ZGQ-03-001 滑座.SLDPRT"
    target.write_bytes(b"fake-part")
    result = OperationResult(
        reserved["operation_id"], True, "file_saved", "ok",
        info=SolidWorksInfo(connected=False, revision="2025"),
        target_path=str(target), template_path="Part.prtdot", file_size=9,
        document_path=str(target), original_document_preserved=True,
    )

    def broken_write(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(service, "_commit", broken_write)      # 事务本身失败
    monkeypatch.setattr(service, "update_status", broken_write)  # 连状态也写不进去
    with pytest.raises(SolidWorksError) as error:
        service.commit_created_file(reserved["operation_id"], result)
    assert error.value.code == "database_commit_failed"
    assert error.value.details.get("recovery_path")
    assert error.value.details.get("status_recovery_path")
    assert (service.recovery_directory / f"{reserved['operation_id']}.json").is_file()
    status_path = service.status_recovery_directory / f"{reserved['operation_id']}.json"
    assert status_path.is_file()
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["operation_id"] == reserved["operation_id"]
    assert payload["error_code"] == "database_commit_failed"
    assert payload["part_sequence"] == 1
    assert payload["status_write_error"]


# -------------------------------------------- 6. P1-3：编号不再永久锁死


def test_orphaned_reserved_release_but_never_touch_committed(services, tmp_path):
    _, repository, project, service, _ = services
    reserved = service.reserve(project.id, "05", "自制机加工件", "孤立预留", project.solidworks_part_directory)

    # 保护期内不释放
    early = service.reconcile_stale_reservations(grace_minutes=30, classify_only=False)
    item = next(i for i in early if i["operation_id"] == reserved["operation_id"])
    assert item["decision"] == "orphaned_reservation" and not item["released"]

    _age(repository.database, reserved["operation_id"], hours=2)
    applied = service.reconcile_stale_reservations(grace_minutes=30, classify_only=False)
    item = next(i for i in applied if i["operation_id"] == reserved["operation_id"])
    assert item["released"] is True
    assert _operations(repository.database, reserved["operation_id"])["status"] == "failed"

    # 释放后同一号码可再次预留
    again = service.reserve(project.id, "05", "自制机加工件", "释放后重用", project.solidworks_part_directory)
    assert again["part_sequence"] == 1


def test_classify_only_never_writes(services):
    _, repository, project, service, _ = services
    reserved = service.reserve(project.id, "05", "自制机加工件", "只读诊断", project.solidworks_part_directory)
    _age(repository.database, reserved["operation_id"], hours=5)
    before = _operations(repository.database, reserved["operation_id"])
    service.reconcile_stale_reservations(classify_only=True)
    assert _operations(repository.database, reserved["operation_id"]) == before


def test_connecting_reservation_needs_user_confirmation(services):
    _, repository, project, service, _ = services
    reserved = service.reserve(project.id, "07", "自制机加工件", "靠后状态", project.solidworks_part_directory)
    _force_status(repository.database, reserved["operation_id"], "connecting")
    _age(repository.database, reserved["operation_id"], hours=3)

    auto = service.reconcile_stale_reservations(grace_minutes=30, classify_only=False, release_orphans=False)
    item = next(i for i in auto if i["operation_id"] == reserved["operation_id"])
    assert not item["released"] and item["reason"] == "awaiting_user_confirmation"

    confirmed = service.reconcile_stale_reservations(grace_minutes=30, classify_only=False, release_orphans=True)
    item = next(i for i in confirmed if i["operation_id"] == reserved["operation_id"])
    assert item["released"] is True


def test_committed_number_is_never_released(services, tmp_path):
    _, repository, project, service, _ = services
    reserved = service.reserve(project.id, "08", "自制机加工件", "已入库", project.solidworks_part_directory)
    target = tmp_path / "ZGQ-08-001 已入库.SLDPRT"
    target.write_bytes(b"fake-part")
    result = OperationResult(
        reserved["operation_id"], True, "file_saved", "ok",
        info=SolidWorksInfo(connected=False, revision="2025"),
        target_path=str(target), template_path="Part.prtdot", file_size=9,
        document_path=str(target), original_document_preserved=True,
    )
    record = service.commit_created_file(reserved["operation_id"], result)
    _force_status(repository.database, reserved["operation_id"], "connecting")
    _age(repository.database, reserved["operation_id"], hours=6)

    outcomes = service.reconcile_stale_reservations(grace_minutes=30, classify_only=False, release_orphans=True)
    item = next(i for i in outcomes if i["operation_id"] == reserved["operation_id"])
    assert item["decision"] == "already_committed" and not item["released"]
    assert _operations(repository.database, reserved["operation_id"])["status"] == "completed"
    assert repository.get_record(record.id).full_name == "ZGQ-08-001 已入库"
    assert repository.get_next_named_number(project.id, "08", "自制机加工件") == 2


def test_recovery_actions_are_audited(services, tmp_path):
    database, repository, project, service, _ = services
    reserved = service.reserve(project.id, "05", "自制机加工件", "审计用", project.solidworks_part_directory)
    _age(database, reserved["operation_id"], hours=2)
    service.reconcile_stale_reservations(grace_minutes=30, classify_only=False)
    audits = repository.list_project_audit(project.id, limit=50)
    assert any(item["action"] == "solidworks_reconcile_release" for item in audits)
    release_audit = next(item for item in audits if item["action"] == "solidworks_reconcile_release")
    assert reserved["operation_id"] in release_audit["before_data"]


def test_recovery_file_supersedes_stale_status(tmp_path):
    """提交恢复文件仍可被 reconcile_pending 消费（回归 phase-1 行为）。"""
    database = Database(tmp_path / "recover.db")
    database.migrate()
    repository = Repository(database)
    project = ProjectService(repository).create("恢复项目", "", "REC1", directory=str(tmp_path))
    (tmp_path / "parts").mkdir()
    service = SolidWorksService(repository, tmp_path / "recovery")
    reserved = service.reserve(project.id, "03", "自制机加工件", "恢复零件", str(tmp_path / "parts"))
    target = tmp_path / "parts" / "REC1-03-001 恢复零件.SLDPRT"
    target.write_bytes(b"fake-part")
    result = OperationResult(
        reserved["operation_id"], True, "file_saved", "ok",
        info=SolidWorksInfo(connected=False, revision="2025"),
        target_path=str(target), template_path="Part.prtdot", file_size=9,
        document_path=str(target), original_document_preserved=True,
    )
    service._write_recovery(reserved["operation_id"], {
        "operation_id": reserved["operation_id"], "result": result.to_dict(), "error": "test", "created_at": "now",
    })
    outcomes = service.reconcile_pending()
    assert any(item.get("success") for item in outcomes), outcomes
    assert repository.list_records(project_id=project.id)[0].full_name == "REC1-03-001 恢复零件"


# ------------------------------------------------- 7. 版本号单一来源可追溯


def test_version_is_single_source_of_truth():
    import re
    from pathlib import Path

    from mechanical_project_namer import __version__

    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE).group(1)
    assert declared == __version__
    app_source = (root / "mechanical_project_namer" / "app.py").read_text(encoding="utf-8")
    assert "setApplicationVersion(__version__)" in app_source
    assert not re.search(r'setApplicationName\("[^"]*V\d+\.\d+\.\d+', app_source), \
        "app.py 不应再硬编码版本号"


def test_release_manager_reads_version_and_guards_dirty_tree():
    from scripts.release_manager import project_version, worktree_dirty

    assert project_version()
    assert isinstance(worktree_dirty(), bool)


# ------------------------------------------------- 8. 清理失败不得中断主流程（safe_fs）


def _block_recovery_unlink(monkeypatch):
    """只拦截 recovery 目录下的 unlink（模拟环境“安全删除”守卫抛 SystemExit）。"""
    from pathlib import Path as _Path

    real_unlink = _Path.unlink

    def _blocked(self, *args, **kwargs):
        if "recovery" in str(self).lower():
            raise SystemExit(1)
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(_Path, "unlink", _blocked)


def test_remove_file_returns_false_when_delete_is_blocked(tmp_path, monkeypatch):
    from mechanical_project_namer.safe_fs import remove_file

    target = tmp_path / "recovery" / "x.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    _block_recovery_unlink(monkeypatch)
    assert remove_file(target, what="测试恢复文件") is False
    assert target.exists(), "删不掉时必须保留文件，等待下次幂等重放"


def test_remove_file_treats_missing_file_as_success(tmp_path):
    from mechanical_project_namer.safe_fs import remove_file

    assert remove_file(tmp_path / "not-there.json") is True


def test_remove_file_still_propagates_keyboard_interrupt(tmp_path, monkeypatch):
    from pathlib import Path as _Path

    from mechanical_project_namer.safe_fs import remove_file

    target = tmp_path / "x.bin"
    target.write_bytes(b"x")

    def _interrupt(self, *args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(_Path, "unlink", _interrupt)
    with pytest.raises(KeyboardInterrupt):
        remove_file(target)


def test_reconcile_pending_survives_blocked_cleanup_and_stays_idempotent(tmp_path, monkeypatch):
    """恢复文件删不掉时：编号必须照常入库，且重放不会二次推进计数器。"""
    database = Database(tmp_path / "blocked.db")
    database.migrate()
    repository = Repository(database)
    project = ProjectService(repository).create("阻塞项目", "", "BLK1", directory=str(tmp_path))
    (tmp_path / "parts").mkdir()
    service = SolidWorksService(repository, tmp_path / "recovery")
    reserved = service.reserve(project.id, "03", "自制机加工件", "阻塞零件", str(tmp_path / "parts"))
    target = tmp_path / "parts" / "BLK1-03-001 阻塞零件.SLDPRT"
    target.write_bytes(b"fake-part")
    result = OperationResult(
        reserved["operation_id"], True, "file_saved", "ok",
        info=SolidWorksInfo(connected=False, revision="2025"),
        target_path=str(target), template_path="Part.prtdot", file_size=9,
        document_path=str(target), original_document_preserved=True,
    )
    service._write_recovery(reserved["operation_id"], {
        "operation_id": reserved["operation_id"], "result": result.to_dict(),
        "error": "test", "created_at": "now",
    })

    _block_recovery_unlink(monkeypatch)
    outcomes = service.reconcile_pending()
    handled = [item for item in outcomes if item.get("success")]
    assert handled, outcomes
    assert handled[0]["recovery_file_removed"] is False

    records = repository.list_records(project_id=project.id)
    assert len(records) == 1 and records[0].full_name == "BLK1-03-001 阻塞零件"
    assert repository.get_next_named_number(project.id, "03", "自制机加工件") == 2

    # 放宽拦截后重放：_commit 检出 completed → 返回既有记录，不重复建号。
    # 注意：这里不 assert 恢复文件是否被删除——真实环境里 unlink 仍可能被宿主的
    # 安全删除守卫拒绝，而"删不掉"恰恰是这条测试要覆盖的场景，不应作为失败条件。
    monkeypatch.undo()
    service.reconcile_pending()
    records_after = repository.list_records(project_id=project.id)
    assert len(records_after) == 1, "重放不得产生第二条记录"
    assert repository.get_next_named_number(project.id, "03", "自制机加工件") == 2, "不得二次推进计数器"
    assert _operations(database, reserved["operation_id"])["status"] == "completed"


def test_commit_created_file_survives_blocked_cleanup(tmp_path, monkeypatch):
    """已成功提交的零件不能因为善后删除失败而报成失败（假失败）。"""
    database = Database(tmp_path / "commit.db")
    database.migrate()
    repository = Repository(database)
    project = ProjectService(repository).create("提交项目", "", "CMT1", directory=str(tmp_path))
    (tmp_path / "parts").mkdir()
    service = SolidWorksService(repository, tmp_path / "recovery")
    reserved = service.reserve(project.id, "03", "自制机加工件", "善后零件", str(tmp_path / "parts"))
    target = tmp_path / "parts" / "CMT1-03-001 善后零件.SLDPRT"
    target.write_bytes(b"fake-part")
    result = OperationResult(
        reserved["operation_id"], True, "file_saved", "ok",
        info=SolidWorksInfo(connected=False, revision="2025"),
        target_path=str(target), template_path="Part.prtdot", file_size=9,
        document_path=str(target), original_document_preserved=True,
    )
    (service.recovery_directory).mkdir(parents=True, exist_ok=True)
    service._write_recovery(reserved["operation_id"], {
        "operation_id": reserved["operation_id"], "result": result.to_dict(),
        "error": "stale", "created_at": "now",
    })

    _block_recovery_unlink(monkeypatch)
    record = service.commit_created_file(reserved["operation_id"], result)  # 不得抛异常
    assert record.full_name == "CMT1-03-001 善后零件"
    assert repository.get_next_named_number(project.id, "03", "自制机加工件") == 2


def test_production_code_has_no_bare_unlink(monkeypatch):
    """守卫：生产代码里不应再出现未被 safe_fs 包裹的清理类删除。"""
    import re
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1] / "mechanical_project_namer"
    offenders = []
    for source in root.rglob("*.py"):
        if source.name == "safe_fs.py":
            continue
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"\.unlink\(|os\.unlink\(|rmtree\(", line):
                offenders.append(f"{source.relative_to(root)}:{number}")
    assert not offenders, f"请改用 safe_fs.remove_file：{offenders}"
