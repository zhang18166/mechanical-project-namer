# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json
import logging
import sqlite3
from ..numbering.simple import SIMPLE_TYPE, module_code as normalize_module, choose_sequence, full_name as simple_full_name
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from ..numbering import (
    compose_full_name,
    is_singleton_part_type,
    part_type_range,
    singleton_exists_message,
    singleton_sequence,
)
from ..paths import local_data_dir
from ..safe_fs import remove_file
from .errors import SolidWorksError
from .models import OperationResult, SolidWorksInfo
from .validation import validate_part_name, validate_target_path


LOGGER = logging.getLogger(__name__)

# 非终态（仍占用号码）的操作状态；'failed' 才是唯一释放号码的终态。
ACTIVE_OPERATION_STATUSES = ("reserved", "connecting", "creating", "saving", "verifying")
TERMINAL_OPERATION_STATUSES = ("completed", "failed")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(value or ""))
    return cleaned or "unknown"


class SolidWorksService:
    """Main-process coordinator. This class owns SQLite; the worker never imports it."""

    def __init__(self, repository, recovery_directory: Path | None = None) -> None:
        self.repository = repository
        self.recovery_directory = Path(recovery_directory or local_data_dir() / "recovery" / "solidworks")
        # 状态写入失败时的落盘目录；与 commit 恢复文件分开，避免 reconcile 误读。
        self.status_recovery_directory = self.recovery_directory / "status"

    def reserve(
        self, project_id: int, module_code: str, part_type: str, part_name: str,
        target_directory: str, operation_id: str | None = None, *, extra_notes: str = "", sequence_override=None,
    ) -> dict:
        from ..file_sync import ensure_external_numbers_registered
        ensure_external_numbers_registered(self.repository, project_id, extras=(target_directory,))
        module_code = normalize_module(module_code)
        validate_part_name(part_name)
        operation_id = operation_id or str(uuid.uuid4())
        start, end = part_type_range(part_type)
        singleton = is_singleton_part_type(part_type)
        now = _now()
        with self.repository.database.transaction() as connection:
            project = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if project is None or project["project_status"] != "normal":
                raise SolidWorksError("project_unavailable", "当前项目不存在、已归档或在回收站。", "reservation")
            module = connection.execute(
                "SELECT 1 FROM project_modules WHERE project_id=? AND module_code=? AND enabled=1",
                (project_id, module_code),
            ).fetchone()
            if module is None:
                raise SolidWorksError("module_unavailable", f"模块 {module_code} 尚未启用。", "reservation")
            if part_type == SIMPLE_TYPE:
                sequence = choose_sequence(connection, project_id, module_code, sequence_override)
            elif singleton:
                # 总成：固定 000，每模块仅一个，不读取也不推进递增计数器。
                # 该分支不再可能抛出 "序号已用尽"。
                existing = connection.execute(
                    "SELECT 1 FROM drawing_records WHERE project_id=? AND module_code=? AND part_type=? LIMIT 1",
                    (project_id, module_code, part_type),
                ).fetchone()
                if existing is not None:
                    raise SolidWorksError(
                        "singleton_exists", singleton_exists_message(part_type), "reservation",
                        details={"module_code": module_code, "part_type": part_type,
                                 "part_sequence": singleton_sequence(part_type)},
                    )
                pending = connection.execute(
                    f"""SELECT 1 FROM solidworks_operations
                        WHERE project_id=? AND module_code=? AND part_type=?
                          AND status IN ({",".join("?" for _ in ACTIVE_OPERATION_STATUSES)}) LIMIT 1""",
                    (project_id, module_code, part_type, *ACTIVE_OPERATION_STATUSES),
                ).fetchone()
                if pending is not None:
                    raise SolidWorksError(
                        "singleton_reserved",
                        f"该模块的总成 {singleton_sequence(part_type):03d} 正在创建中，请稍候。",
                        "reservation",
                        details={"module_code": module_code, "part_type": part_type,
                                 "part_sequence": singleton_sequence(part_type)},
                    )
                sequence = singleton_sequence(part_type)
            else:
                row = connection.execute(
                    "SELECT next_sequence FROM part_sequence_counters WHERE project_id=? AND module_code=? AND part_type=?",
                    (project_id, module_code, part_type),
                ).fetchone()
                if row is None:
                    current = connection.execute(
                        "SELECT MAX(part_sequence) FROM drawing_records WHERE project_id=? AND module_code=? AND part_type=?",
                        (project_id, module_code, part_type),
                    ).fetchone()[0]
                    sequence = start if current is None else int(current) + 1
                else:
                    sequence = int(row["next_sequence"])
                if sequence > end:
                    raise SolidWorksError("number_exhausted", f"模块 {module_code} 的{part_type}序号已用尽。", "reservation")
            full_name = (simple_full_name if part_type == SIMPLE_TYPE else compose_full_name)(project["code"], module_code, sequence, part_name)
            target_path = validate_target_path(str(Path(target_directory) / f"{full_name}.SLDPRT"))
            duplicate = connection.execute(
                "SELECT 1 FROM drawing_records WHERE project_id=? AND module_code=? AND part_type=? AND part_sequence=?",
                (project_id, module_code, part_type, sequence),
            ).fetchone()
            if duplicate:
                raise SolidWorksError("duplicate_number", f"编号 {project['code']}-{module_code}-{sequence:03d} 已存在。", "reservation")
            try:
                connection.execute(
                    """INSERT INTO solidworks_operations
                    (operation_id,project_id,module_code,part_type,part_sequence,part_name,full_name,target_path,
                     status,started_at,updated_at,extra_notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (operation_id, project_id, module_code, part_type, sequence, part_name, full_name,
                     str(target_path), "reserved", now, now, extra_notes),
                )
            except sqlite3.IntegrityError as error:
                raise SolidWorksError("number_reserved", f"编号 {project['code']}-{module_code}-{sequence:03d} 正在被另一任务使用。", "reservation") from error
        return {
            "operation_id": operation_id, "project_id": project_id, "project_name": project["name"],
            "project_code": project["code"], "module_code": module_code, "part_type": part_type,
            "part_sequence": sequence, "part_name": part_name, "full_name": full_name,
            "target_path": str(target_path),
        }

    def update_status(self, operation_id: str, status: str, **values) -> None:
        allowed = {
            "template_path", "detected_version", "detected_build", "service_pack", "process_id",
            "error_stage", "error_code", "error_message", "worker_result", "recovery_required",
        }
        changes = {key: value for key, value in values.items() if key in allowed}
        if isinstance(changes.get("worker_result"), dict):
            changes["worker_result"] = json.dumps(changes["worker_result"], ensure_ascii=False)
        changes.update({"status": status, "updated_at": _now()})
        if status in {"completed", "failed"}:
            changes["completed_at"] = _now()
        assignments = ",".join(f"{key}=?" for key in changes)
        with self.repository.database.transaction() as connection:
            connection.execute(f"UPDATE solidworks_operations SET {assignments} WHERE operation_id=?", (*changes.values(), operation_id))

    def commit_created_file(self, operation_id: str, result: OperationResult, is_copied: bool = False):
        if not result.verified_file:
            raise SolidWorksError("file_not_verified", "物理文件尚未通过验证，不能写入项目记录。", "database")
        try:
            record = self._commit(operation_id, result, is_copied)
        except Exception as error:
            payload = {"operation_id": operation_id, "result": result.to_dict(), "error": str(error), "created_at": _now()}
            recovery_path = self._write_recovery(operation_id, payload)
            status_recovery_path = ""
            try:
                self.update_status(
                    operation_id, "reconciliation_required", recovery_required=1,
                    error_stage="database", error_code="database_commit_failed", error_message=str(error),
                    worker_result=result.to_dict(),
                )
            except Exception as status_error:  # noqa: BLE001 — 必须留痕，不能静默
                LOGGER.exception(
                    "SOLIDWORKS 提交失败后的状态写入同样失败，operation_id=%s", operation_id
                )
                status_recovery_path = str(self._write_status_recovery(
                    operation_id, "reconciliation_required", 1,
                    "database_commit_failed", "database", str(error), status_error,
                ))
            raise SolidWorksError(
                "database_commit_failed",
                f"SOLIDWORKS零件已经创建成功，但项目记录写入失败。文件不会删除，恢复记录：{recovery_path}",
                "database",
                details={
                    "recovery_path": str(recovery_path),
                    "status_recovery_path": status_recovery_path,
                    "cause": str(error),
                },
            ) from error
        # 善后清理：删不掉也不能把"已经成功提交"变成假失败（见 safe_fs 模块说明）。
        recovery = self.recovery_directory / f"{operation_id}.json"
        if recovery.exists():
            remove_file(recovery, what="SOLIDWORKS 恢复文件", logger=LOGGER)
        return record

    def _commit(self, operation_id: str, result: OperationResult, is_copied: bool):
        now = _now()
        with self.repository.database.transaction() as connection:
            operation = connection.execute("SELECT * FROM solidworks_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if operation is None:
                raise LookupError("SOLIDWORKS操作预留不存在。")
            if operation["status"] == "completed":
                row = connection.execute("SELECT id FROM drawing_records WHERE solidworks_operation_id=?", (operation_id,)).fetchone()
                if row:
                    return self.repository.get_record(int(row["id"]))
            project = connection.execute("SELECT * FROM projects WHERE id=?", (operation["project_id"],)).fetchone()
            sequence = int(operation["part_sequence"])
            legacy = connection.execute("SELECT MAX(drawing_number) FROM drawing_records WHERE project_id=?", (project["id"],)).fetchone()[0]
            drawing_number = 1 if legacy is None else int(legacy) + 1
            target = Path(result.target_path)
            sw_root = Path(project["solidworks_part_directory"] or target.parent)
            try:
                relative_path = str(target.relative_to(sw_root))
            except ValueError:
                relative_path = target.name
            cursor = connection.execute(
                """INSERT INTO drawing_records
                (project_id,project_name,project_code,drawing_number,filename,part_name,purpose,record_date,
                 created_at,updated_at,status,is_copied,creation_method,project_directory,extra_notes,module_code,
                 part_sequence,part_type,full_name,file_path,relative_path,file_extension,file_size,file_exists,
                 creation_source,solidworks_version,solidworks_service_pack,solidworks_template_path,solidworks_operation_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (project["id"], project["name"], project["code"], drawing_number,
                 f"{project['code']}-{operation['module_code']}-{sequence:03d}", operation["part_name"], "",
                 now[:10], now, now, "used", int(is_copied), "solidworks_one_click", project["directory"], operation["extra_notes"],
                 operation["module_code"], sequence, operation["part_type"], operation["full_name"],
                 str(target), relative_path, target.suffix.upper(), result.file_size, 1, "solidworks_one_click",
                 str(result.info.year or result.info.revision), result.info.service_pack, result.template_path, operation_id),
            )
            if not is_singleton_part_type(operation["part_type"]):
                connection.execute(
                    """INSERT INTO part_sequence_counters(project_id,module_code,part_type,next_sequence,updated_at)
                    VALUES (?,?,?,?,?) ON CONFLICT(project_id,module_code,part_type)
                    DO UPDATE SET next_sequence=MAX(part_sequence_counters.next_sequence,excluded.next_sequence),updated_at=excluded.updated_at""",
                    (project["id"], operation["module_code"], operation["part_type"], sequence + 1, now),
                )
            connection.execute(
                "INSERT INTO number_reservations(project_id,module_code,part_category,part_sequence,status,original_record_id,reserved_at,reason) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id,module_code,part_category,part_sequence) DO UPDATE SET status='used',original_record_id=excluded.original_record_id,reason=excluded.reason",
                (project["id"], operation["module_code"], operation["part_type"], sequence, "used", int(cursor.lastrowid), now, "solidworks_created"),
            )
            connection.execute("UPDATE projects SET updated_at=?,last_used_at=? WHERE id=?", (now, now, project["id"]))
            connection.execute(
                """UPDATE solidworks_operations SET status='completed',template_path=?,detected_version=?,
                detected_build=?,service_pack=?,process_id=?,worker_result=?,recovery_required=0,
                updated_at=?,completed_at=? WHERE operation_id=?""",
                (result.template_path, str(result.info.year or result.info.revision), result.info.build,
                 result.info.service_pack, result.info.process_id, json.dumps(result.to_dict(), ensure_ascii=False),
                 now, now, operation_id),
            )
            record_id = int(cursor.lastrowid)
        return self.repository.get_record(record_id)

    def fail(self, operation_id: str, error: Exception, physical_file_exists: bool = False,
             snapshot: dict | None = None) -> dict:
        """把操作标记为失败 / 待恢复。**绝不抛异常，也绝不静默**。

        数据库写入失败时改用本地状态恢复文件落盘，保证 operation_id、项目、模块、
        类别、序号、目标文件与异常信息都不会丢失，下次启动仍可 reconcile。
        """
        if physical_file_exists:
            status, recovery = "reconciliation_required", 1
        else:
            status, recovery = "failed", 0
        code = error.code if isinstance(error, SolidWorksError) else "worker_error"
        stage = error.stage if isinstance(error, SolidWorksError) else "worker"
        try:
            self.update_status(operation_id, status, recovery_required=recovery,
                               error_stage=stage, error_code=code, error_message=str(error))
        except Exception as write_error:  # noqa: BLE001 — 必须留痕，不能静默
            LOGGER.exception(
                "SOLIDWORKS 失败状态写入数据库失败，转入本地恢复文件：operation_id=%s status=%s",
                operation_id, status,
            )
            path = self._write_status_recovery(
                operation_id, status, recovery, code, stage, str(error), write_error, snapshot=snapshot,
            )
            return {"operation_id": operation_id, "status": status, "recovery_required": recovery,
                    "persisted": "recovery_file", "recovery_path": str(path)}
        return {"operation_id": operation_id, "status": status, "recovery_required": recovery,
                "persisted": "database"}

    def _write_status_recovery(self, operation_id: str, status: str, recovery_required: int,
                               error_code: str, error_stage: str, error_message: str,
                               write_error: BaseException, snapshot: dict | None = None) -> Path:
        """落盘一份完整的失败现场，供下次启动 reconcile。"""
        operation: dict = {}
        project: dict = {}
        try:
            with self.repository.database.connect() as connection:
                row = connection.execute(
                    "SELECT * FROM solidworks_operations WHERE operation_id=?", (operation_id,)
                ).fetchone()
                if row is not None:
                    operation = dict(row)
                    project_row = connection.execute(
                        "SELECT id,name,code FROM projects WHERE id=?", (operation["project_id"],)
                    ).fetchone()
                    if project_row is not None:
                        project = dict(project_row)
        except Exception as read_error:  # noqa: BLE001 — 只读探测失败仍要继续落盘
            LOGGER.error("读取 SOLIDWORKS 操作现场失败：%s", read_error)
        payload = {
            "kind": "solidworks_status_recovery",
            "operation_id": operation_id,
            "status": status,
            "recovery_required": recovery_required,
            "error_stage": error_stage,
            "error_code": error_code,
            "error_message": error_message,
            "status_write_error": f"{type(write_error).__name__}: {write_error}",
            "created_at": _now(),
            # 关键字段：项目 / 模块 / 类别 / 序号 / 目标文件
            "project_id": operation.get("project_id") or (snapshot or {}).get("project_id"),
            "project_name": project.get("name") or (snapshot or {}).get("project_name", ""),
            "project_code": project.get("code") or (snapshot or {}).get("project_code", ""),
            "module_code": operation.get("module_code") or (snapshot or {}).get("module_code", ""),
            "part_type": operation.get("part_type") or (snapshot or {}).get("part_type", ""),
            "part_sequence": operation.get("part_sequence") if operation else (snapshot or {}).get("part_sequence"),
            "part_name": operation.get("part_name") or (snapshot or {}).get("part_name", ""),
            "full_name": operation.get("full_name") or (snapshot or {}).get("full_name", ""),
            "target_path": operation.get("target_path") or (snapshot or {}).get("target_path", ""),
            "template_path": operation.get("template_path", ""),
            "operation_snapshot": operation,
            "caller_snapshot": snapshot or {},
        }
        path = self._write_status_recovery_file(operation_id, payload)
        LOGGER.error("已写出 SOLIDWORKS 状态恢复文件：%s", path)
        return path

    def _write_status_recovery_file(self, operation_id: str, payload: dict) -> Path:
        self.status_recovery_directory.mkdir(parents=True, exist_ok=True)
        target = self.status_recovery_directory / f"{_safe_name(operation_id)}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(target)
        return target

    def list_status_recovery_files(self) -> list[dict]:
        """列出状态恢复文件（只读，供诊断与 reconcile 使用）。"""
        if not self.status_recovery_directory.exists():
            return []
        items = []
        for path in sorted(self.status_recovery_directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                items.append({"path": str(path), "readable": False, "error": str(error)})
                continue
            payload["path"] = str(path)
            payload["readable"] = True
            items.append(payload)
        return items

    # ------------------------------------------------------------------ 恢复

    def audit_recovery(self, action: str, operation: dict, detail: dict | None = None) -> None:
        """所有恢复动作都写审计日志；审计本身失败只记日志，不影响恢复结果。"""
        project_id = operation.get("project_id")
        try:
            with self.repository.database.transaction() as connection:
                self.repository._audit(
                    connection, action, int(project_id) if project_id else None,
                    operation.get("project_name") or "", operation.get("project_code") or "",
                    operation.get("status") or "", operation.get("new_status") or "",
                    {key: operation.get(key) for key in
                     ("operation_id", "module_code", "part_type", "part_sequence", "full_name", "target_path")},
                    detail or {},
                )
        except Exception:  # noqa: BLE001
            LOGGER.exception("SOLIDWORKS 恢复审计写入失败：action=%s operation=%s", action, operation.get("operation_id"))

    def reconcile_stale_reservations(self, *, grace_minutes: int = 30,
                                     classify_only: bool = False,
                                     release_orphans: bool = False) -> list[dict]:
        """安全恢复被占用的编号。

        判定规则（绝不误释放已经成功落库的编号）：
        - 已存在对应 drawing_records 记录  → 号码已成功使用，只把操作状态修正为 completed；
        - 物理文件存在且大小 > 0           → 走 _commit 补记录；
        - 两者都不满足且超过 grace_minutes → 判定为孤立预留，标记 failed 以释放号码。

        释放策略：
        - ``classify_only=True``  仅诊断，不做任何写入；
        - ``release_orphans=False``（启动时默认）只释放 status='reserved' 的孤立预留
          —— 该状态下工作进程从未启动，物理文件必定不存在，释放绝对安全；
        - ``release_orphans=True``（用户在诊断界面明确确认后）连 status='connecting'
          等更靠后的孤立预留一并释放。
        每个动作都会写入审计日志。
        """
        now = datetime.now().astimezone()
        cutoff = (now - timedelta(minutes=grace_minutes)).isoformat(timespec="seconds")
        outcomes: list[dict] = []
        with self.repository.database.connect() as connection:
            rows = connection.execute(
                f"""SELECT o.*, p.name AS project_name, p.code AS project_code
                    FROM solidworks_operations o LEFT JOIN projects p ON p.id=o.project_id
                    WHERE o.status IN ({",".join("?" for _ in ACTIVE_OPERATION_STATUSES)})
                       OR (o.status='reconciliation_required' AND o.recovery_required=1)
                    ORDER BY o.updated_at""",
                ACTIVE_OPERATION_STATUSES,
            ).fetchall()
            rows = [dict(row) for row in rows]
            linked: dict[str, int] = {}
            for row in rows:
                record = connection.execute(
                    "SELECT id FROM drawing_records WHERE solidworks_operation_id=?",
                    (row["operation_id"],),
                ).fetchone()
                if record is not None:
                    linked[row["operation_id"]] = int(record["id"])
        for row in rows:
            operation_id = row["operation_id"]
            target = Path(row["target_path"]) if row["target_path"] else None
            file_exists = bool(target and target.is_file() and target.stat().st_size > 0)
            common = {"operation_id": operation_id, "project_id": row["project_id"],
                      "project_name": row.get("project_name") or "",
                      "project_code": row.get("project_code") or "",
                      "module_code": row["module_code"], "part_type": row["part_type"],
                      "part_sequence": row["part_sequence"], "full_name": row.get("full_name") or "",
                      "target_path": row["target_path"]}

            if operation_id in linked:
                # 号码已经成功写库：只修状态，绝不释放。
                outcome = {**common, "decision": "already_committed",
                           "record_id": linked[operation_id], "released": False}
                if not classify_only:
                    try:
                        self.update_status(operation_id, "completed", recovery_required=0,
                                           error_code="", error_message="")
                        self.audit_recovery("solidworks_reconcile_repair_status", row, outcome)
                        outcome["applied"] = True
                    except Exception as error:  # noqa: BLE001
                        LOGGER.exception("修正已提交操作状态失败：%s", operation_id)
                        outcome["applied"] = False
                        outcome["error"] = str(error)
                outcomes.append(outcome)
                continue

            if file_exists:
                outcome = {**common, "decision": "recovered_from_file", "released": False}
                if classify_only:
                    outcomes.append(outcome)
                    continue
                try:
                    result = OperationResult(
                        operation_id, True, "file_saved", "从操作表恢复物理文件记录。",
                        info=SolidWorksInfo(
                            connected=False, process_id=row["process_id"],
                            revision=row["detected_version"], build=row["detected_build"],
                            service_pack=row["service_pack"],
                        ),
                        target_path=str(target), template_path=row["template_path"],
                        file_size=target.stat().st_size, document_path=str(target),
                        original_document_preserved=True,
                    )
                    record = self._commit(operation_id, result, False)
                    outcome["record_id"] = record.id
                    outcome["applied"] = True
                    self.audit_recovery("solidworks_reconcile_recover_file", row, {"record_id": record.id})
                except Exception as error:  # noqa: BLE001
                    LOGGER.exception("从物理文件恢复 SOLIDWORKS 记录失败：%s", operation_id)
                    outcome["decision"] = "recover_failed"
                    outcome["error"] = str(error)
                outcomes.append(outcome)
                continue

            # 孤立预留：既没有记录也没有文件。
            stale = (row["updated_at"] or "") <= cutoff
            # status='reserved' 表示辅助进程从未启动，物理文件必定不存在 → 释放绝对安全。
            safe_release = row["status"] == "reserved"
            outcome = {**common, "decision": "orphaned_reservation", "released": False,
                       "stale": stale, "status": row["status"], "updated_at": row["updated_at"],
                       "auto_releasable": safe_release}
            if classify_only:
                outcome["reason"] = "classify_only"
                outcomes.append(outcome)
                continue
            if not stale:
                outcome["reason"] = "within_grace_period"
                outcomes.append(outcome)
                continue
            if not (release_orphans or safe_release):
                outcome["reason"] = "awaiting_user_confirmation"
                outcomes.append(outcome)
                continue
            try:
                self.fail(operation_id, SolidWorksError(
                    "orphaned_reservation_released",
                    "预留长期未完成且无记录、无物理文件，已按孤立预留释放编号。", "recovery",
                ), physical_file_exists=False, snapshot=row)
                outcome["applied"] = True
                outcome["released"] = True
                self.audit_recovery("solidworks_reconcile_release", row, outcome)
            except Exception as error:  # noqa: BLE001
                LOGGER.exception("释放孤立预留失败：%s", operation_id)
                outcome["error"] = str(error)
            outcomes.append(outcome)
        return outcomes

    def reconcile_pending(self) -> list[dict]:
        outcomes: list[dict] = []
        recovery_paths = list(self.recovery_directory.glob("*.json")) if self.recovery_directory.exists() else []
        handled: set[str] = set()
        for path in recovery_paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                handled.add(payload["operation_id"])
                result = OperationResult.from_dict(payload["result"])
                if result.verified_file:
                    record = self._commit(payload["operation_id"], result, False)
                    # 幂等：即使这里删不掉，下次启动会重放本文件，_commit 检出 completed 后
                    # 直接返回已有记录，不会重复插记录、不会二次推进计数器。
                    removed = remove_file(path, what="SOLIDWORKS 恢复文件", logger=LOGGER)
                    outcomes.append({
                        "operation_id": payload["operation_id"], "success": True,
                        "record_id": record.id, "recovery_file_removed": removed,
                    })
                else:
                    outcomes.append({"operation_id": payload["operation_id"], "success": False, "reason": "file_missing"})
            except Exception as error:
                outcomes.append({"operation_id": path.stem, "success": False, "reason": str(error)})
        with self.repository.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM solidworks_operations WHERE recovery_required=1 AND status='reconciliation_required'"
            ).fetchall()
        for row in rows:
            if row["operation_id"] in handled:
                continue
            target = Path(row["target_path"])
            if not target.is_file() or target.stat().st_size <= 0:
                outcomes.append({"operation_id": row["operation_id"], "success": False, "reason": "file_missing"})
                continue
            result = OperationResult(
                row["operation_id"], True, "file_saved", "从操作表恢复物理文件记录。",
                info=SolidWorksInfo(
                    connected=False, process_id=row["process_id"], revision=row["detected_version"],
                    build=row["detected_build"], service_pack=row["service_pack"],
                ),
                target_path=str(target), template_path=row["template_path"], file_size=target.stat().st_size,
                document_path=str(target), original_document_preserved=True,
            )
            try:
                record = self._commit(row["operation_id"], result, False)
                outcomes.append({"operation_id": row["operation_id"], "success": True, "record_id": record.id})
            except Exception as error:
                outcomes.append({"operation_id": row["operation_id"], "success": False, "reason": str(error)})
        return outcomes

    def _write_recovery(self, operation_id: str, payload: dict) -> Path:
        self.recovery_directory.mkdir(parents=True, exist_ok=True)
        target = self.recovery_directory / f"{operation_id}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        return target
