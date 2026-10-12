# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json
import os
import re
import sqlite3
import socket
from datetime import date, datetime
from pathlib import Path

from ..numbering import (
    compose_full_name,
    format_filename,
    is_singleton_part_type,
    part_type_range,
    singleton_exists_message,
    singleton_sequence,
)
from ..projects.models import DrawingRecord, Project
from .database import Database


class DuplicateNumberError(RuntimeError):
    pass


class SingletonNumberExistsError(ValueError):
    """Raised when a per-module singleton part type (总成) already exists.

    The message is exactly the wording confirmed for the 总成 strategy,
    e.g. ``该模块已存在总成 000``.  It subclasses ``ValueError`` so that every
    pre-existing ``except ValueError`` branch keeps working.
    """

    def __init__(self, part_type: str, module_code: str = "", project_code: str = "",
                 existing_full_name: str = "", existing_record_id: int | None = None) -> None:
        self.part_type = part_type
        self.module_code = str(module_code or "").zfill(2) if module_code else ""
        self.project_code = project_code
        self.existing_full_name = existing_full_name
        self.existing_record_id = existing_record_id
        self.part_sequence = singleton_sequence(part_type)
        self.full_number = (
            f"{project_code}-{self.module_code}-{self.part_sequence:03d}"
            if project_code and self.module_code else f"{self.part_sequence:03d}"
        )
        super().__init__(singleton_exists_message(part_type))


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Repository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_project(
        self,
        name: str,
        description: str,
        code: str,
        notes: str = "",
        directory: str = "",
        solidworks_part_directory: str = "",
        start_number: int = 1,
        project_type: str = "formal",
    ) -> Project:
        now = _now()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO projects
                (name, description, code, notes, directory, solidworks_part_directory, start_number, next_number,
                 code_locked, archived, project_type, project_status, created_at, updated_at, last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, 'normal', ?, ?, ?)""",
                (name, description, code, notes, directory, solidworks_part_directory, start_number, start_number,
                 "test" if project_type == "test" else "formal", now, now, now),
            )
            project_id = int(cursor.lastrowid)
            from ..numbering.v12 import MODULES
            for sort_order, (module_code, module_name) in enumerate(MODULES):
                connection.execute(
                    "INSERT INTO project_modules(project_id,module_code,module_name,enabled,sort_order,source,last_part_type,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (project_id, module_code, module_name, int(module_code not in {"06", "90", "99"}), sort_order, "default", "自制机加工件", now, now),
                )
            self._audit(connection, "create", project_id, name, code, "", "normal", {}, {"project_type": project_type})
        return self.get_project(project_id)

    @staticmethod
    def _project_select() -> str:
        return """SELECT p.*,
            (SELECT COUNT(*) FROM drawing_records r WHERE r.project_id=p.id) AS used_count,
            (SELECT COUNT(*) FROM project_modules m WHERE m.project_id=p.id) AS module_count,
            COALESCE((SELECT r.module_code FROM drawing_records r WHERE r.project_id=p.id ORDER BY r.created_at DESC,r.id DESC LIMIT 1),'') AS latest_module,
            COALESCE((SELECT r.filename FROM drawing_records r WHERE r.project_id=p.id ORDER BY r.created_at DESC,r.id DESC LIMIT 1),'') AS latest_number
            FROM projects p"""

    def get_project(self, project_id: int) -> Project:
        with self.database.connect() as connection:
            row = connection.execute(self._project_select() + " WHERE p.id=?", (project_id,)).fetchone()
        if row is None:
            raise LookupError("项目不存在或已不可用。")
        return self._project(row)

    def list_projects(self, query: str = "", include_archived: bool = False,
                      project_status: str | None = None, project_type: str = "all") -> list[Project]:
        like = f"%{query.strip()}%"
        statuses = ("normal", "archived") if include_archived else ("normal",)
        if project_status in {"normal", "archived", "trash"}:
            statuses = (project_status,)
        elif project_status == "all":
            statuses = ("normal", "archived", "trash")
        placeholders = ",".join("?" for _ in statuses)
        sql = self._project_select() + f""" WHERE p.project_status IN ({placeholders})
                   AND (?='all' OR p.project_type=?)
                   AND (p.name LIKE ? OR p.code LIKE ? OR p.notes LIKE ?)
                 ORDER BY CASE p.project_status WHEN 'normal' THEN 0 WHEN 'archived' THEN 1 ELSE 2 END,p.last_used_at DESC"""
        with self.database.connect() as connection:
            rows = connection.execute(sql, (*statuses, project_type, project_type, like, like, like)).fetchall()
        return [self._project(row) for row in rows]

    def update_project(self, project_id: int, **values) -> Project:
        allowed = {"name", "description", "code", "notes", "directory", "solidworks_part_directory", "project_type"}
        changes = {key: value for key, value in values.items() if key in allowed}
        if not changes:
            return self.get_project(project_id)
        changes["updated_at"] = _now()
        assignments = ", ".join(f"{key}=?" for key in changes)
        with self.database.transaction() as connection:
            before = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            connection.execute(
                f"UPDATE projects SET {assignments} WHERE id=?",
                (*changes.values(), project_id),
            )
            after = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if before and after:
                self._audit(connection, "edit", project_id, after["name"], after["code"], after["project_status"], after["project_status"], dict(before), dict(after))
        return self.get_project(project_id)

    def set_archived(self, project_id: int, archived: bool = True) -> None:
        self.set_project_status(project_id, "archived" if archived else "normal")

    def set_project_status(self, project_id: int, status: str) -> Project:
        if status not in {"normal", "archived", "trash"}:
            raise ValueError("项目状态无效。")
        now = _now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if row is None:
                raise LookupError("项目不存在。")
            before = row["project_status"]
            archived_at = now if status == "archived" else None
            deleted_at = now if status == "trash" else None
            connection.execute("UPDATE projects SET project_status=?,archived=?,archived_at=?,deleted_at=?,updated_at=? WHERE id=?", (status, int(status == "archived"), archived_at, deleted_at, now, project_id))
            action = "archive" if status == "archived" else "trash" if status == "trash" else "restore"
            self._audit(connection, action, project_id, row["name"], row["code"], before, status, {}, {})
            if status == "trash":
                connection.execute("INSERT INTO ai_project_preferences(project_id,allow_ai_reference) VALUES (?,0) ON CONFLICT(project_id) DO UPDATE SET allow_ai_reference=0", (project_id,))
            elif before == "trash":
                connection.execute("INSERT INTO ai_project_preferences(project_id,allow_ai_reference) VALUES (?,1) ON CONFLICT(project_id) DO UPDATE SET allow_ai_reference=1", (project_id,))
        return self.get_project(project_id)

    @staticmethod
    def _audit(connection, action: str, project_id: int | None, name: str, code: str,
               before_status: str, after_status: str, before_data: dict, after_data: dict,
               record_count: int = 0) -> None:
        from .. import __version__
        connection.execute(
            """INSERT INTO project_audit_log(action,project_id,project_name,project_code,before_status,after_status,before_data,after_data,record_count,computer_name,app_version,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (action, project_id, name, code, before_status, after_status,
             json.dumps(before_data, ensure_ascii=False, default=str),
             json.dumps(after_data, ensure_ascii=False, default=str), record_count,
             socket.gethostname(), __version__, _now()),
        )

    def audit_project_action(self, project_id: int, action: str) -> None:
        project = self.get_project(project_id)
        with self.database.transaction() as connection:
            self._audit(connection, action, project.id, project.name, project.code,
                        project.project_status, project.project_status, {}, {})

    def list_project_audit(self, project_id: int | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM project_audit_log"
        args: list[object] = []
        if project_id is not None:
            sql += " WHERE project_id=?"
            args.append(project_id)
        sql += " ORDER BY created_at DESC,audit_id DESC LIMIT ?"
        args.append(limit)
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(sql, args).fetchall()]

    def project_properties(self, project_id: int) -> dict:
        project = self.get_project(project_id)
        with self.database.connect() as connection:
            row = connection.execute("""SELECT
                SUM(CASE WHEN status='used' THEN 1 ELSE 0 END) AS formal_count,
                SUM(CASE WHEN status='void' THEN 1 ELSE 0 END) AS void_count
                FROM drawing_records WHERE project_id=?""", (project_id,)).fetchone()
            counts = {
                "ai_plan_count": connection.execute("SELECT COUNT(*) FROM ai_plans WHERE project_id=?", (project_id,)).fetchone()[0],
                "adopted_plan_count": connection.execute("SELECT COUNT(*) FROM ai_plans WHERE project_id=? AND adopted_at IS NOT NULL", (project_id,)).fetchone()[0],
                "project_memory_count": connection.execute("SELECT COUNT(*) FROM project_memories WHERE project_id=?", (project_id,)).fetchone()[0],
                "feedback_count": connection.execute("SELECT COUNT(*) FROM ai_feedback WHERE project_id=?", (project_id,)).fetchone()[0],
                "conversation_count": connection.execute("SELECT COUNT(*) FROM ai_conversations WHERE project_id=?", (project_id,)).fetchone()[0],
            }
        return {"project": project, "formal_count": int(row["formal_count"] or 0),
                "void_count": int(row["void_count"] or 0), **counts}

    def project_numbering_status(self, project_id: int) -> list[dict]:
        result = []
        for module in self.list_modules(project_id, enabled_only=False):
            types = []
            for part_type in ("总成", "自制机加工件", "钣金/焊接件", "采购/标准件"):
                try:
                    next_number = self.get_next_named_number(project_id, module["module_code"], part_type)
                    display = f"{next_number:03d}"
                except SingletonNumberExistsError:
                    display = "已存在 000"
                except ValueError:
                    display = "已用尽"
                types.append({"part_type": part_type, "next_number": display})
            result.append({"module_code": module["module_code"], "module_name": module["module_name"], "types": types})
        return result

    def duplicate_project(self, project_id: int, *, name: str, code: str, directory: str = "",
                          copy_description: bool = True, copy_notes: bool = True,
                          copy_modules: bool = True, copy_memories: bool = False,
                          project_type: str = "formal") -> Project:
        source = self.get_project(project_id)
        now = _now()
        with self.database.transaction() as connection:
            cursor = connection.execute("""INSERT INTO projects(name,description,code,notes,directory,start_number,next_number,code_locked,archived,project_type,project_status,created_at,updated_at,last_used_at)
                VALUES (?,?,?,?,?,1,1,1,0,?,'normal',?,?,?)""",
                (name.strip(), source.description if copy_description else "", code.strip().upper(),
                 source.notes if copy_notes else "", directory.strip(),
                 "test" if project_type == "test" else "formal", now, now, now))
            new_id = int(cursor.lastrowid)
            modules = connection.execute("SELECT * FROM project_modules WHERE project_id=? ORDER BY sort_order,module_code", (project_id,)).fetchall() if copy_modules else []
            if not modules:
                from ..numbering.v12 import MODULES
                modules = [{"module_code": code, "module_name": module_name, "enabled": int(code not in {"06", "90", "99"}), "sort_order": order, "source": "default", "last_part_type": "自制机加工件"} for order, (code, module_name) in enumerate(MODULES)]
            for module in modules:
                connection.execute("INSERT INTO project_modules(project_id,module_code,module_name,enabled,sort_order,source,last_part_type,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (new_id, module["module_code"], module["module_name"], module["enabled"], module["sort_order"], "copy", module["last_part_type"], now, now))
            if copy_memories:
                connection.execute("""INSERT INTO project_memories(project_id,module_code,title,content,source_message_id,confidence,enabled,created_at,updated_at)
                    SELECT ?,module_code,title,content,NULL,confidence,enabled,?,? FROM project_memories WHERE project_id=?""", (new_id, now, now, project_id))
            self._audit(connection, "copy", new_id, name.strip(), code.strip().upper(), "", "normal", {"source_project_id": project_id}, {"copy_modules": copy_modules, "copy_memories": copy_memories})
        return self.get_project(new_id)

    def backup_database(self, directory: Path | None = None, prefix: str = "before_project_delete") -> Path:
        target_dir = Path(directory or (self.database.path.parent / "backups"))
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
        source = self.database.connect()
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        return target

    def permanently_delete_project(self, project_id: int, confirmation: str,
                                   backup_directory: Path | None = None) -> dict:
        project = self.get_project(project_id)
        if project.project_status != "trash":
            raise ValueError("只有回收站中的项目可以永久删除。")
        if confirmation.strip() not in {project.code, project.name}:
            raise ValueError(f"确认内容不匹配，请输入项目代号 {project.code} 或完整项目名称。")
        backup = self.backup_database(backup_directory)
        directory = project.directory
        with self.database.transaction() as connection:
            record_count = connection.execute("SELECT COUNT(*) FROM drawing_records WHERE project_id=?", (project_id,)).fetchone()[0]
            sw_operation_count = connection.execute("SELECT COUNT(*) FROM solidworks_operations WHERE project_id=?", (project_id,)).fetchone()[0]
            connection.execute("DELETE FROM ai_messages WHERE conversation_id IN (SELECT conversation_id FROM ai_conversations WHERE project_id=?)", (project_id,))
            for table in ("ai_conversations", "project_memories", "ai_plans"):
                connection.execute(f"DELETE FROM {table} WHERE project_id=?", (project_id,))
            connection.execute("UPDATE ai_feedback SET source_record_id=NULL WHERE source_record_id IN (SELECT id FROM drawing_records WHERE project_id=?) AND COALESCE(source_project_id,-1)<>?", (project_id, project_id))
            connection.execute("DELETE FROM ai_feedback WHERE project_id=? OR source_project_id=?", (project_id, project_id))
            # This relation intentionally remains NO ACTION; clean project-owned
            # operations explicitly before deleting the parent project.
            connection.execute("DELETE FROM solidworks_operations WHERE project_id=?", (project_id,))
            for table in ("ai_parts", "ai_project_preferences", "part_sequence_counters", "number_reservations", "project_modules", "drawing_records"):
                connection.execute(f"DELETE FROM {table} WHERE project_id=?", (project_id,))
            connection.execute("DELETE FROM projects WHERE id=?", (project_id,))
            self._audit(connection, "permanent_delete", project_id, project.name, project.code,
                        "trash", "deleted", {}, {}, int(record_count))
        return {"project_id": project_id, "record_count": int(record_count), "backup_path": str(backup),
                "solidworks_operation_count": int(sw_operation_count), "project_directory": directory,
                "directory_untouched": not directory or Path(directory).exists(), "physical_files_untouched": True}

    def save_next_record(
        self,
        project_id: int,
        part_name: str,
        purpose: str,
        extra_notes: str = "",
        is_copied: bool = False,
        creation_method: str = "manual",
    ) -> DrawingRecord:
        now = _now()
        record_id: int
        filename = ""
        with self.database.transaction() as connection:
            project = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if project is None:
                raise LookupError("项目不存在或已不可用。")
            if project["project_status"] != "normal":
                raise ValueError("归档或回收站项目不能新增零件记录。")
            number = int(project["next_number"])
            if number > 999:
                raise ValueError("当前项目的三位图号已经用尽。")
            filename = format_filename(project["code"], number)
            duplicate = connection.execute(
                "SELECT 1 FROM drawing_records WHERE project_id=? AND drawing_number=?",
                (project_id, number),
            ).fetchone()
            if duplicate:
                raise DuplicateNumberError(f"图号 {filename} 已经存在，请刷新项目图号或检查历史记录。")
            try:
                cursor = connection.execute(
                    """INSERT INTO drawing_records
                    (project_id, project_name, project_code, drawing_number, filename,
                     part_name, purpose, record_date, created_at, updated_at, status,
                     is_copied, creation_method, project_directory, extra_notes, project_directory_snapshot)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'used', ?, ?, ?, ?, ?)""",
                    (
                        project_id, project["name"], project["code"], number, filename,
                        part_name, purpose, date.today().isoformat(), now, now,
                        int(is_copied), creation_method, project["directory"], extra_notes, project["directory"],
                    ),
                )
            except sqlite3.IntegrityError as error:
                if "UNIQUE constraint failed" in str(error):
                    raise DuplicateNumberError(
                        f"图号 {filename} 已经存在，请刷新项目图号或检查历史记录。"
                    ) from error
                raise
            record_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT OR IGNORE INTO number_reservations(project_id,module_code,part_category,part_sequence,status,original_record_id,reserved_at,reason) VALUES (?,?,?,?,?,?,?,?)",
                (project_id, "00", "legacy", number, "used", record_id, now, "record_created"),
            )
            connection.execute(
                "UPDATE projects SET next_number=?, updated_at=?, last_used_at=? WHERE id=?",
                (number + 1, now, now, project_id),
            )
        return self.get_record(record_id)

    def list_modules(self, project_id: int, enabled_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM project_modules WHERE project_id=?"
        if enabled_only:
            sql += " AND enabled=1"
        sql += " ORDER BY sort_order, module_code"
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(sql, (project_id,)).fetchall()]

    def set_modules(self, project_id: int, modules: list[tuple[str, str]], enabled_codes: set[str] | None = None) -> list[dict]:
        now = _now()
        enabled_codes = enabled_codes if enabled_codes is not None else {code for code, _ in modules}
        with self.database.transaction() as connection:
            for sort_order, (code, name) in enumerate(modules):
                code, name = str(code).zfill(2), str(name or "").strip()
                if not re.fullmatch(r"\d{2}", code) or not name:
                    continue
                connection.execute(
                    "INSERT INTO project_modules(project_id,module_code,module_name,enabled,sort_order,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(project_id,module_code) DO UPDATE SET module_name=excluded.module_name,enabled=excluded.enabled,sort_order=excluded.sort_order,updated_at=excluded.updated_at",
                    (project_id, code, name, int(code in enabled_codes), sort_order, "ai", now, now),
                )
        return self.list_modules(project_id, enabled_only=False)

    def create_module(self, project_id: int, module_code: str, module_name: str, *, enabled: bool = True,
                      before_code: str | None = None, source: str = "user") -> dict:
        code, name = str(module_code).strip().zfill(2), str(module_name).strip()
        if not re.fullmatch(r"\d{2}", code) or not name:
            raise ValueError("模块编号必须是两位数字，模块名称不能为空。")
        now = _now()
        with self.database.transaction() as connection:
            duplicate = connection.execute("SELECT 1 FROM project_modules WHERE project_id=? AND module_code=?", (project_id, code)).fetchone()
            if duplicate:
                raise ValueError(f"模块 {code} 已存在。")
            max_order = connection.execute("SELECT COALESCE(MAX(sort_order), -1) FROM project_modules WHERE project_id=?", (project_id,)).fetchone()[0]
            order = int(max_order) + 1
            if before_code:
                row = connection.execute("SELECT sort_order FROM project_modules WHERE project_id=? AND module_code=?", (project_id, str(before_code).zfill(2))).fetchone()
                if row:
                    order = int(row[0])
                    connection.execute("UPDATE project_modules SET sort_order=sort_order+1 WHERE project_id=? AND sort_order>=?", (project_id, order))
            connection.execute("INSERT INTO project_modules(project_id,module_code,module_name,enabled,sort_order,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (project_id, code, name, int(enabled), order, source, now, now))
        self.audit_project_action(project_id, "modify_module")
        return next(item for item in self.list_modules(project_id, enabled_only=False) if item["module_code"] == code)

    def update_module(self, project_id: int, module_code: str, *, new_code: str | None = None,
                      module_name: str | None = None, enabled: bool | None = None) -> dict:
        old_code = str(module_code).strip().zfill(2)
        code = str(new_code if new_code is not None else old_code).strip().zfill(2)
        if not re.fullmatch(r"\d{2}", code):
            raise ValueError("模块编号必须是两位数字。")
        now = _now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM project_modules WHERE project_id=? AND module_code=?", (project_id, old_code)).fetchone()
            if row is None:
                raise LookupError("模块不存在。")
            used = connection.execute("SELECT COUNT(*) FROM drawing_records WHERE project_id=? AND module_code=?", (project_id, old_code)).fetchone()[0]
            if code != old_code and used:
                raise ValueError("模块已有零件记录，不能静默修改编号；请先执行迁移预览。")
            if code != old_code and connection.execute("SELECT 1 FROM project_modules WHERE project_id=? AND module_code=?", (project_id, code)).fetchone():
                raise ValueError(f"模块 {code} 已存在。")
            connection.execute("UPDATE project_modules SET module_code=?,module_name=?,enabled=COALESCE(?,enabled),updated_at=? WHERE project_id=? AND module_code=?", (code, module_name.strip() if module_name is not None else row["module_name"], None if enabled is None else int(enabled), now, project_id, old_code))
        self.audit_project_action(project_id, "modify_module")
        return next(item for item in self.list_modules(project_id, enabled_only=False) if item["module_code"] == code)

    def set_module_enabled(self, project_id: int, module_code: str, enabled: bool) -> dict:
        return self.update_module(project_id, module_code, enabled=enabled)

    def move_module(self, project_id: int, module_code: str, direction: int) -> list[dict]:
        items = self.list_modules(project_id, enabled_only=False)
        index = next((i for i, item in enumerate(items) if item["module_code"] == str(module_code).zfill(2)), -1)
        target = index + (1 if direction > 0 else -1)
        if index < 0 or target < 0 or target >= len(items):
            return items
        items[index], items[target] = items[target], items[index]
        now = _now()
        with self.database.transaction() as connection:
            for order, item in enumerate(items):
                connection.execute("UPDATE project_modules SET sort_order=?,updated_at=? WHERE project_id=? AND module_code=?", (order, now, project_id, item["module_code"]))
        self.audit_project_action(project_id, "modify_module")
        return self.list_modules(project_id, enabled_only=False)

    def delete_module(self, project_id: int, module_code: str) -> None:
        code = str(module_code).zfill(2)
        with self.database.transaction() as connection:
            used = connection.execute("SELECT COUNT(*) FROM drawing_records WHERE project_id=? AND module_code=?", (project_id, code)).fetchone()[0]
            if used:
                raise ValueError("模块已有零件记录，只能停用或归档，不能删除。")
            connection.execute("DELETE FROM project_modules WHERE project_id=? AND module_code=?", (project_id, code))
        self.audit_project_action(project_id, "modify_module")

    def adopt_modules(self, project_id: int, modules: list[dict], conflict_choices: dict[str, str] | None = None) -> dict:
        """Synchronize only selected AI modules in one transaction; existing modules are never removed."""
        choices = conflict_choices or {}
        added, kept, conflicts = [], [], []
        now = _now()
        with self.database.transaction() as connection:
            for item in modules:
                code = str(item.get("module_code", "")).zfill(2)
                name = str(item.get("module_name", "")).strip()
                if not re.fullmatch(r"\d{2}", code) or not name:
                    continue
                row = connection.execute("SELECT * FROM project_modules WHERE project_id=? AND module_code=?", (project_id, code)).fetchone()
                if row is None:
                    order = connection.execute("SELECT COALESCE(MAX(sort_order), -1)+1 FROM project_modules WHERE project_id=?", (project_id,)).fetchone()[0]
                    connection.execute("INSERT INTO project_modules(project_id,module_code,module_name,enabled,sort_order,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (project_id, code, name, 1, order, "ai", now, now))
                    added.append(code)
                elif row["module_name"] != name:
                    choice = choices.get(code, "keep")
                    conflicts.append({"module_code": code, "current_name": row["module_name"], "ai_name": name, "choice": choice})
                    if choice == "ai":
                        connection.execute("UPDATE project_modules SET module_name=?,enabled=1,source='ai',updated_at=? WHERE project_id=? AND module_code=?", (name, now, project_id, code))
                    elif choice == "manual" and item.get("final_name"):
                        connection.execute("UPDATE project_modules SET module_name=?,enabled=1,source='user',updated_at=? WHERE project_id=? AND module_code=?", (str(item["final_name"]).strip(), now, project_id, code))
                    else:
                        kept.append(code)
                else:
                    kept.append(code)
            project = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if project:
                self._audit(connection, "modify_module", project_id, project["name"], project["code"],
                            project["project_status"], project["project_status"], {},
                            {"added": added, "kept": kept, "conflicts": conflicts})
        return {"added": added, "kept": kept, "conflicts": conflicts}

    def save_ai_plan(self, *, prompt: str, result=None, project_id: int | None = None, status: str = "success",
                     plan_type: str = "project", raw_response_reference: str = "") -> int:
        from dataclasses import asdict, is_dataclass
        payload = asdict(result) if is_dataclass(result) else (result or {})
        now = _now()
        suggestions = payload.get("suggestions", [])
        with self.database.transaction() as connection:
            cursor = connection.execute("""INSERT INTO ai_plans(project_id,prompt,plan_type,requested_model,returned_model,thinking_enabled,reasoning_level,status,project_code_candidates,recommended_modules,missing_information,quality_assessment,response_json,raw_response_reference,input_tokens,output_tokens,cached_tokens,total_duration,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (project_id, prompt, plan_type, payload.get("model", ""), payload.get("actual_model", ""), int(payload.get("thinking_enabled", False)), payload.get("reasoning_level", "high"), status, json.dumps(suggestions, ensure_ascii=False, default=str), json.dumps(payload.get("module_recommendations", []), ensure_ascii=False, default=str), json.dumps(payload.get("missing_information", []), ensure_ascii=False), json.dumps(payload.get("quality_assessment") or {}, ensure_ascii=False), json.dumps(payload, ensure_ascii=False, default=str), raw_response_reference, int(payload.get("prompt_tokens", 0)), int(payload.get("completion_tokens", 0)), int(payload.get("cached_tokens", 0)), float(payload.get("elapsed_seconds", 0)), now))
            return int(cursor.lastrowid)

    def list_ai_plans(self, project_id: int | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM ai_plans" + (" WHERE project_id=?" if project_id is not None else "") + " ORDER BY created_at DESC,plan_id DESC LIMIT ?"
        args = [project_id, limit] if project_id is not None else [limit]
        with self.database.connect() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def search_ai_plans(self, *, query: str = "", project_id: int | None = None,
                        status: str = "all", adopted: bool | None = None,
                        date_from: str = "", date_to: str = "", limit: int = 500) -> list[dict]:
        where, args = ["1=1"], []
        if project_id is not None:
            where.append("project_id=?"); args.append(project_id)
        if query.strip():
            like = f"%{query.strip()}%"; where.append("(prompt LIKE ? OR response_json LIKE ? OR project_code_candidates LIKE ?)"); args.extend([like, like, like])
        if status != "all":
            where.append("status=?"); args.append(status)
        if adopted is not None:
            where.append("adopted_at IS " + ("NOT NULL" if adopted else "NULL"))
        if date_from:
            where.append("created_at>=?"); args.append(date_from)
        if date_to:
            where.append("created_at<?"); args.append(date_to + "T23:59:59.999999")
        args.append(limit)
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(
                f"SELECT * FROM ai_plans WHERE {' AND '.join(where)} ORDER BY created_at DESC,plan_id DESC LIMIT ?", args
            ).fetchall()]

    @staticmethod
    def decode_ai_plan(plan: dict) -> dict:
        """Return only persisted structured data; incomplete legacy rows remain non-adoptable."""
        def load(value, fallback):
            if isinstance(value, (dict, list)):
                return value
            try:
                parsed = json.loads(value or "")
                return parsed
            except (TypeError, ValueError, json.JSONDecodeError):
                return fallback
        response = load(plan.get("response_json"), {})
        suggestions = load(plan.get("project_code_candidates"), response.get("suggestions", []))
        modules = load(plan.get("recommended_modules"), response.get("module_recommendations", []))
        missing = load(plan.get("missing_information"), response.get("missing_information", []))
        quality = load(plan.get("quality_assessment"), response.get("quality_assessment", {}))
        valid_codes = [item for item in suggestions if isinstance(item, dict) and str(item.get("code", "")).strip()]
        valid_modules = [item for item in modules if isinstance(item, dict) and str(item.get("module_code", "")).strip() and str(item.get("module_name", "")).strip()]
        return {"suggestions": valid_codes, "modules": valid_modules, "missing_information": missing,
                "quality_assessment": quality, "response": response,
                "adoptable": bool(valid_codes and valid_modules and plan.get("status") in {"success", "partial"})}

    def get_ai_plan(self, plan_id: int) -> dict:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM ai_plans WHERE plan_id=?", (plan_id,)).fetchone()
        if row is None:
            raise LookupError("AI规划记录不存在。")
        return dict(row)

    def mark_ai_plan_adopted(self, plan_id: int, project_id: int | None = None) -> None:
        with self.database.transaction() as connection:
            connection.execute("UPDATE ai_plans SET adopted_at=?,project_id=COALESCE(?,project_id) WHERE plan_id=?", (_now(), project_id, plan_id))

    def record_ai_plan_adoption(self, plan_id: int, project_id: int | None, mode: str,
                                final_payload: dict, original_payload: dict | None = None,
                                changes: dict | None = None) -> int:
        now = _now()
        with self.database.transaction() as connection:
            plan = connection.execute("SELECT * FROM ai_plans WHERE plan_id=?", (plan_id,)).fetchone()
            if plan is None:
                raise LookupError("AI规划记录不存在。")
            cursor = connection.execute(
                "INSERT INTO ai_plan_adoptions(plan_id,project_id,mode,original_payload,final_payload,changes_json,created_at) VALUES (?,?,?,?,?,?,?)",
                (plan_id, project_id, mode, json.dumps(original_payload or self.decode_ai_plan(dict(plan)), ensure_ascii=False),
                 json.dumps(final_payload, ensure_ascii=False), json.dumps(changes or {}, ensure_ascii=False), now),
            )
            connection.execute("UPDATE ai_plans SET adopted_at=?,project_id=COALESCE(?,project_id),adopted_project_id=?,adoption_mode=?,final_payload=? WHERE plan_id=?", (now, project_id, project_id, mode, json.dumps(final_payload, ensure_ascii=False), plan_id))
            return int(cursor.lastrowid)

    def list_ai_plan_adoptions(self, plan_id: int) -> list[dict]:
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM ai_plan_adoptions WHERE plan_id=? ORDER BY created_at,adoption_id", (plan_id,)).fetchall()]

    def delete_ai_plan(self, plan_id: int) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM ai_plans WHERE plan_id=? AND status NOT IN ('success','partial')", (plan_id,))

    def create_conversation(self, project_id: int | None, title: str, model: str, thinking_enabled: bool, reasoning_level: str) -> int:
        now = _now()
        with self.database.transaction() as connection:
            cursor = connection.execute("INSERT INTO ai_conversations(project_id,title,model,thinking_enabled,reasoning_level,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (project_id, title or "新对话", model, int(thinking_enabled), reasoning_level, now, now))
            return int(cursor.lastrowid)

    def get_conversation(self, conversation_id: int) -> dict:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_conversations WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
        if row is None:
            raise LookupError("AI会话不存在。")
        return dict(row)

    def list_conversations(self, project_id: int | None = None) -> list[dict]:
        sql = "SELECT * FROM ai_conversations" + (" WHERE project_id=? OR project_id IS NULL" if project_id is not None else "") + " ORDER BY updated_at DESC"
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(sql, ((project_id,) if project_id is not None else ())).fetchall()]

    def rename_conversation(self, conversation_id: int, title: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("UPDATE ai_conversations SET title=?,updated_at=? WHERE conversation_id=?", (title.strip() or "新对话", _now(), conversation_id))

    def update_conversation_settings(
        self,
        conversation_id: int,
        *,
        project_id: int | None,
        model: str,
        thinking_enabled: bool,
        reasoning_level: str,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE ai_conversations
                   SET project_id=?,model=?,thinking_enabled=?,reasoning_level=?,updated_at=?
                   WHERE conversation_id=?""",
                (project_id, model, int(thinking_enabled), reasoning_level, _now(), conversation_id),
            )

    def delete_conversation(self, conversation_id: int) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM ai_conversations WHERE conversation_id=?", (conversation_id,))

    def add_ai_message(self, conversation_id: int, role: str, content: str, token_usage: dict | None = None) -> int:
        now = _now()
        with self.database.transaction() as connection:
            cursor = connection.execute("INSERT INTO ai_messages(conversation_id,role,content,token_usage,created_at) VALUES (?,?,?,?,?)", (conversation_id, role, content, json.dumps(token_usage or {}, ensure_ascii=False), now))
            if role == "user":
                row = connection.execute(
                    "SELECT title FROM ai_conversations WHERE conversation_id=?", (conversation_id,)
                ).fetchone()
                if row and row["title"] == "新对话":
                    title = " ".join(content.strip().split())[:24] or "新对话"
                    connection.execute(
                        "UPDATE ai_conversations SET title=?,updated_at=? WHERE conversation_id=?",
                        (title, now, conversation_id),
                    )
                else:
                    connection.execute("UPDATE ai_conversations SET updated_at=? WHERE conversation_id=?", (now, conversation_id))
            else:
                connection.execute("UPDATE ai_conversations SET updated_at=? WHERE conversation_id=?", (now, conversation_id))
            return int(cursor.lastrowid)

    def list_ai_messages(self, conversation_id: int) -> list[dict]:
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM ai_messages WHERE conversation_id=? ORDER BY created_at,message_id", (conversation_id,)).fetchall()]

    def prepare_conversation_context(
        self,
        conversation_id: int,
        *,
        recent_limit: int = 12,
        summary_threshold: int = 20,
        summary_character_limit: int = 4000,
    ) -> dict:
        """Return bounded context and roll older messages into a local summary."""
        conversation = self.get_conversation(conversation_id)
        messages = self.list_ai_messages(conversation_id)
        summary = str(conversation.get("conversation_summary") or "")
        through = int(conversation.get("summary_through_message_id") or 0)
        if len(messages) > summary_threshold:
            older = messages[:-recent_limit]
            additions = [item for item in older if int(item["message_id"]) > through]
            if additions:
                lines = []
                for item in additions:
                    role = {"user": "用户", "assistant": "AI", "system": "系统"}.get(item["role"], item["role"])
                    compact = " ".join(str(item["content"]).split())
                    lines.append(f"{role}：{compact[:500]}")
                summary = (summary + "\n" + "\n".join(lines)).strip()[-summary_character_limit:]
                through = int(older[-1]["message_id"])
                with self.database.transaction() as connection:
                    connection.execute(
                        """UPDATE ai_conversations
                           SET conversation_summary=?,summary_through_message_id=?,updated_at=?
                           WHERE conversation_id=?""",
                        (summary, through, _now(), conversation_id),
                    )
            recent = messages[-recent_limit:]
        else:
            recent = messages
        return {
            "conversation": self.get_conversation(conversation_id),
            "summary": summary,
            "recent_messages": recent,
            "total_message_count": len(messages),
            "summary_loaded": bool(summary),
        }

    def save_project_memory(self, project_id: int, title: str, content: str, module_code: str = "", source_message_id: int | None = None, confidence: int = 100) -> int:
        now = _now()
        with self.database.transaction() as connection:
            cursor = connection.execute("INSERT INTO project_memories(project_id,module_code,title,content,source_message_id,confidence,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (project_id, str(module_code or "").zfill(2) if module_code else "", title.strip(), content.strip(), source_message_id, max(0, min(100, int(confidence))), now, now))
            return int(cursor.lastrowid)

    def list_project_memories(self, project_id: int | None = None, module_code: str = "", query: str = "", limit: int = 10) -> list[dict]:
        where, args = ["enabled=1"], []
        if project_id is not None: where.append("project_id=?"); args.append(project_id)
        if module_code: where.append("(module_code=? OR module_code='')"); args.append(str(module_code).zfill(2))
        if query.strip(): where.append("(title LIKE ? OR content LIKE ?)"); args.extend([f"%{query.strip()}%"] * 2)
        args.append(limit)
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(f"SELECT * FROM project_memories WHERE {' AND '.join(where)} ORDER BY updated_at DESC,memory_id DESC LIMIT ?", args).fetchall()]

    def singleton_record(self, project_id: int, module_code: str, part_type: str) -> dict | None:
        """Return the existing singleton (总成) row for a module, or None."""
        module_code = str(module_code).zfill(2)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id,full_name,part_sequence,status FROM drawing_records "
                "WHERE project_id=? AND module_code=? AND part_type=? ORDER BY id LIMIT 1",
                (project_id, module_code, part_type),
            ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _singleton_exists(connection, project_id: int, module_code: str, part_type: str):
        return connection.execute(
            "SELECT id,full_name FROM drawing_records "
            "WHERE project_id=? AND module_code=? AND part_type=? ORDER BY id LIMIT 1",
            (project_id, str(module_code).zfill(2), part_type),
        ).fetchone()

    def get_next_named_number(self, project_id: int, module_code: str, part_type: str) -> int:
        start, end = part_type_range(part_type)
        module_code = str(module_code).zfill(2)
        if is_singleton_part_type(part_type):
            # 总成 is a per-module singleton: it is always 000 and never uses the
            # monotonic counter, so "1 > 0 序号已用尽" can no longer be produced.
            with self.database.connect() as connection:
                existing = self._singleton_exists(connection, project_id, module_code, part_type)
            if existing is not None:
                project = self.get_project(project_id)
                raise SingletonNumberExistsError(
                    part_type, module_code, project.code,
                    existing_full_name=existing["full_name"], existing_record_id=int(existing["id"]),
                )
            return singleton_sequence(part_type)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT next_sequence FROM part_sequence_counters WHERE project_id=? AND module_code=? AND part_type=?",
                (project_id, module_code, part_type),
            ).fetchone()
            if row is None:
                legacy = connection.execute(
                    "SELECT MAX(part_sequence) AS current FROM drawing_records WHERE project_id=? AND module_code=? AND part_type=?",
                    (project_id, module_code, part_type),
                ).fetchone()
                number = start if legacy["current"] is None else int(legacy["current"]) + 1
            else:
                number = int(row["next_sequence"])
        if number > end:
            raise ValueError(f"模块 {module_code} 的{part_type}序号已用尽。")
        return number

    def save_named_record(self, project_id: int, module_code: str, part_type: str, part_name: str,
                          extra_notes: str = "", is_copied: bool = False,
                          creation_method: str = "manual") -> DrawingRecord:
        module_code = str(module_code or "").strip().zfill(2)
        part_name = str(part_name or "").strip()
        extra_notes = str(extra_notes or "").strip()
        if not module_code or not part_name:
            raise ValueError("模块和零件名称不能为空。")
        start, end = part_type_range(part_type)
        now = _now()
        with self.database.transaction() as connection:
            project = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if project is None:
                raise LookupError("项目不存在或已不可用。")
            if project["project_status"] != "normal":
                raise ValueError("归档或回收站项目不能新增零件记录。")
            module = connection.execute("SELECT 1 FROM project_modules WHERE project_id=? AND module_code=? AND enabled=1", (project_id, module_code)).fetchone()
            if module is None:
                raise ValueError(f"模块 {module_code} 尚未启用。")
            singleton = is_singleton_part_type(part_type)
            if singleton:
                # 总成：固定 000，每模块仅一个，且完全不触碰递增计数器。
                existing = self._singleton_exists(connection, project_id, module_code, part_type)
                if existing is not None:
                    raise SingletonNumberExistsError(
                        part_type, module_code, project["code"],
                        existing_full_name=existing["full_name"], existing_record_id=int(existing["id"]),
                    )
                sequence = singleton_sequence(part_type)
            else:
                counter = connection.execute(
                    "SELECT next_sequence FROM part_sequence_counters WHERE project_id=? AND module_code=? AND part_type=?",
                    (project_id, module_code, part_type),
                ).fetchone()
                if counter is None:
                    row = connection.execute(
                        "SELECT MAX(part_sequence) AS current FROM drawing_records WHERE project_id=? AND module_code=? AND part_type=?",
                        (project_id, module_code, part_type),
                    ).fetchone()
                    sequence = start if row["current"] is None else int(row["current"]) + 1
                else:
                    sequence = int(counter["next_sequence"])
                if sequence > end:
                    raise ValueError(f"模块 {module_code} 的{part_type}序号已用尽。")
            legacy_row = connection.execute("SELECT MAX(drawing_number) AS current FROM drawing_records WHERE project_id=?", (project_id,)).fetchone()
            drawing_number = 1 if legacy_row["current"] is None else int(legacy_row["current"]) + 1
            full_name = compose_full_name(project["code"], module_code, sequence, part_name)
            cursor = connection.execute(
                """INSERT INTO drawing_records
                (project_id,project_name,project_code,drawing_number,filename,part_name,purpose,record_date,created_at,updated_at,status,is_copied,creation_method,project_directory,extra_notes,module_code,part_sequence,part_type,full_name,project_directory_snapshot)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (project_id, project["name"], project["code"], drawing_number, f"{project['code']}-{module_code}-{sequence:03d}", part_name, "", now[:10], now, now, "used", int(is_copied), creation_method, project["directory"], extra_notes, module_code, sequence, part_type, full_name, project["directory"]),
            )
            connection.execute("UPDATE project_modules SET last_part_type=?,updated_at=? WHERE project_id=? AND module_code=?", (part_type, now, project_id, module_code))
            if not singleton:
                connection.execute(
                    """INSERT INTO part_sequence_counters(project_id,module_code,part_type,next_sequence,updated_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(project_id,module_code,part_type) DO UPDATE SET next_sequence=excluded.next_sequence,updated_at=excluded.updated_at""",
                    (project_id, module_code, part_type, sequence + 1, now),
                )
            connection.execute("UPDATE projects SET updated_at=?,last_used_at=? WHERE id=?", (now, now, project_id))
            record_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT OR IGNORE INTO number_reservations(project_id,module_code,part_category,part_sequence,status,original_record_id,reserved_at,reason) VALUES (?,?,?,?,?,?,?,?)",
                (project_id, module_code, part_type, sequence, "used", record_id, now, "record_created"),
            )
        return self.get_record(record_id)

    def record_delete_details(self, record_id: int) -> dict:
        record = self.get_record(record_id)
        path = Path(record.file_path) if record.file_path else None
        exists = bool(path and path.is_file())
        return {"record": record, "file_path": str(path) if path else "", "physical_file_exists": exists,
                "has_physical_file": exists}

    def delete_record(self, record_id: int, *, reason: str = "deleted_error") -> DrawingRecord:
        """Delete only the database record; never remove project or physical files."""
        record = self.get_record(record_id)
        now = _now()
        physical_exists = bool(record.file_path and Path(record.file_path).is_file())
        from .. import __version__
        with self.database.transaction() as connection:
            # Keep the sequence occupied after deletion and detach only sources
            # that exclusively came from this record.
            connection.execute(
                "INSERT INTO number_reservations(project_id,module_code,part_category,part_sequence,status,original_record_id,reserved_at,reason) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id,module_code,part_category,part_sequence) DO UPDATE SET status=excluded.status,reason=excluded.reason",
                (record.project_id, record.module_code, record.part_type, record.part_sequence, "deleted_error", record.id, now, reason),
            )
            connection.execute("DELETE FROM ai_feedback WHERE source_record_id=? AND (source_project_id=? OR source_project_id IS NULL)", (record.id, record.project_id))
            connection.execute("UPDATE ai_feedback SET source_record_id=NULL WHERE source_record_id=?", (record.id,))
            connection.execute("INSERT INTO record_delete_audit(record_id,project_id,full_name,file_path,physical_file_exists,action,created_at,app_version) VALUES (?,?,?,?,?,?,?,?)", (record.id, record.project_id, record.display_name, record.file_path, int(physical_exists), reason, now, __version__))
            connection.execute("DELETE FROM drawing_records WHERE id=?", (record_id,))
            connection.execute("DELETE FROM ai_parts WHERE project_id=? AND module_code=? AND part_number=? AND NOT EXISTS (SELECT 1 FROM drawing_records WHERE project_id=? AND module_code=? AND part_sequence=?)", (record.project_id, record.module_code, record.part_sequence, record.project_id, record.module_code, record.part_sequence))
        return record

    def list_record_delete_audit(self, record_id: int | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM record_delete_audit"; args: list[object] = []
        if record_id is not None:
            sql += " WHERE record_id=?"; args.append(record_id)
        sql += " ORDER BY created_at DESC,audit_id DESC LIMIT ?"; args.append(limit)
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(sql, args).fetchall()]

    def scan_legacy_anomalies(self) -> list[dict]:
        from ..numbering.v12 import normalize_project_code

        issues = []
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM projects ORDER BY id").fetchall()
            records_by_project = {}
            for record in connection.execute("SELECT * FROM drawing_records ORDER BY id"):
                records_by_project.setdefault(record["project_id"], []).append(record)
        for row in rows:
            parsed = normalize_project_code(row["code"])
            if parsed.is_full_part_number:
                issues.append({"kind": "project_code", "project_id": row["id"], "project_name": row["name"], "old_code": row["code"], "suggested_code": parsed.code, "module_code": parsed.module_code, "part_sequence": parsed.part_sequence})
            records = records_by_project.get(row["id"], [])
            for record in records:
                old_name = str(record["part_name"] or "").strip()
                note_name = str(record["purpose"] or record["extra_notes"] or "").strip()
                if re.fullmatch(r"[A-Z0-9]+(?:-[A-Z0-9]+)+", old_name) and note_name and not re.fullmatch(r"[A-Z0-9-]+", note_name):
                    issues.append({"kind": "record_name", "project_id": row["id"], "record_id": record["id"], "old_name": old_name, "suggested_name": note_name, "old_notes": record["extra_notes"] or ""})
        return issues

    def migrate_legacy_anomalies(self, issues: list[dict]) -> dict:
        """Apply only an explicit preview list; callers must show it first."""
        changed = 0
        now = _now()
        with self.database.transaction() as connection:
            for issue in issues:
                if issue.get("kind") == "project_code":
                    project_id = int(issue["project_id"])
                    connection.execute("UPDATE projects SET code=?,updated_at=? WHERE id=?", (issue["suggested_code"], now, project_id))
                    connection.execute("UPDATE drawing_records SET project_code=?,updated_at=? WHERE project_id=?", (issue["suggested_code"], now, project_id))
                    code = str(issue.get("module_code") or "00").zfill(2)
                    connection.execute("UPDATE drawing_records SET module_code=?,part_sequence=CASE WHEN part_sequence=0 THEN drawing_number ELSE part_sequence END WHERE project_id=? AND module_code='00'", (code, project_id))
                    connection.execute(
                        "UPDATE drawing_records SET full_name=project_code || '-' || module_code || '-' || printf('%03d', part_sequence) || CASE WHEN part_name<>'' THEN ' ' || part_name ELSE '' END WHERE project_id=?",
                        (project_id,),
                    )
                    changed += 1
                elif issue.get("kind") == "record_name":
                    record_id = int(issue["record_id"])
                    connection.execute("UPDATE drawing_records SET part_name=?,purpose='',full_name='',updated_at=? WHERE id=?", (issue["suggested_name"], now, record_id))
                    changed += 1
        return {"changed": changed, "issues": len(issues), "updated_at": now}

    def get_record(self, record_id: int) -> DrawingRecord:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM drawing_records WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise LookupError("图号记录不存在。")
        return self._record(row)

    def list_records(
        self,
        project_id: int | None = None,
        query: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int | None = None,
    ) -> list[DrawingRecord]:
        where = ["1=1"]
        parameters: list[object] = []
        if project_id is not None:
            where.append("project_id=?")
            parameters.append(project_id)
        if query.strip():
            like = f"%{query.strip()}%"
            where.append("(filename LIKE ? OR part_name LIKE ? OR purpose LIKE ? OR extra_notes LIKE ?)")
            parameters.extend([like, like, like, like])
        if date_from:
            where.append("record_date>=?")
            parameters.append(date_from)
        if date_to:
            where.append("record_date<=?")
            parameters.append(date_to)
        sql = f"SELECT * FROM drawing_records WHERE {' AND '.join(where)} ORDER BY created_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._record(row) for row in rows]

    def update_record(self, record_id: int, part_name: str, purpose: str, extra_notes: str) -> DrawingRecord:
        part_name = str(part_name or "").strip()
        if not part_name:
            raise ValueError("零件名称不能为空。")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT filename FROM drawing_records WHERE id=?", (record_id,)).fetchone()
            if row is None:
                raise LookupError("图号记录不存在。")
            connection.execute(
                "UPDATE drawing_records SET part_name=?, full_name=?, purpose=?, extra_notes=?, updated_at=? WHERE id=?",
                (part_name, f"{row['filename']} {part_name}".strip(), purpose, extra_notes, _now(), record_id),
            )
        return self.get_record(record_id)

    def set_record_status(self, record_id: int, status: str) -> DrawingRecord:
        if status not in {"used", "void", "reserved"}:
            raise ValueError("记录状态无效。")
        record = self.get_record(record_id)
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE drawing_records SET status=?, updated_at=? WHERE id=?",
                (status, now, record_id),
            )
            reservation_status = "voided" if status == "void" else "used" if status == "used" else "reserved"
            connection.execute(
                "INSERT INTO number_reservations(project_id,module_code,part_category,part_sequence,status,original_record_id,reserved_at,reason) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id,module_code,part_category,part_sequence) DO UPDATE SET status=excluded.status,reason=excluded.reason",
                (record.project_id, record.module_code, record.part_type, record.part_sequence, reservation_status, record.id, now, "record_status_changed"),
            )
        return self.get_record(record_id)

    def mark_copied(self, filename: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE drawing_records SET is_copied=1, updated_at=? WHERE filename=?",
                (_now(), filename),
            )

    @staticmethod
    def _project(row) -> Project:
        return Project(
            id=row["id"], name=row["name"], description=row["description"], code=row["code"],
            notes=row["notes"], directory=row["directory"], start_number=row["start_number"],
            solidworks_part_directory=row["solidworks_part_directory"] or "",
            next_number=row["next_number"], code_locked=bool(row["code_locked"]),
            archived=bool(row["archived"]), created_at=row["created_at"], updated_at=row["updated_at"],
            last_used_at=row["last_used_at"], used_count=row["used_count"],
            project_type=row["project_type"], project_status=row["project_status"],
            archived_at=row["archived_at"] or "", deleted_at=row["deleted_at"] or "",
            module_count=row["module_count"], latest_module=row["latest_module"], latest_number=row["latest_number"],
        )

    @staticmethod
    def _record(row) -> DrawingRecord:
        return DrawingRecord(
            id=row["id"], project_id=row["project_id"], project_name=row["project_name"],
            project_code=row["project_code"], drawing_number=row["drawing_number"],
            filename=row["filename"], part_name=row["part_name"], purpose=row["purpose"],
            record_date=row["record_date"], created_at=row["created_at"], updated_at=row["updated_at"],
            status=row["status"], is_copied=bool(row["is_copied"]),
            creation_method=row["creation_method"], project_directory=row["project_directory"],
            extra_notes=row["extra_notes"], module_code=row["module_code"],
            part_sequence=row["part_sequence"], part_type=row["part_type"], full_name=row["full_name"],
            file_path=row["file_path"] or "", relative_path=row["relative_path"] or "",
            file_extension=row["file_extension"] or "", file_size=int(row["file_size"] or 0),
            file_exists=bool(row["file_exists"]), creation_source=row["creation_source"] or "",
            solidworks_version=row["solidworks_version"] or "",
            solidworks_service_pack=row["solidworks_service_pack"] or "",
            solidworks_template_path=row["solidworks_template_path"] or "",
            solidworks_operation_id=row["solidworks_operation_id"] or "",
        )
