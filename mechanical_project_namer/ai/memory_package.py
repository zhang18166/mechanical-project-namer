# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json
import os
import shutil
import tempfile
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from ..data.database import Database
from ..paths import ai_memory_dir
from ..safe_fs import remove_file


PACKAGE_FILES = (
    "MECHANICAL_NAMING_SKILL.md",
    "naming_memory.json",
    "naming_examples.jsonl",
    "project_code_memory.json",
    "schema.json",
)

MODULE_RULES = {
    "00": "整机总装", "01": "机架模块", "02": "送料模块", "03": "压弧/成型模块",
    "04": "锯切模块", "05": "导料/托料模块", "06": "气动模块", "07": "电气模块",
    "08": "防护模块", "90": "标准件/通用件", "99": "工程图/BOM/资料",
}

NUMBERING_RULES = {
    "000": "总成", "001-099": "自制机加工件", "101-199": "钣金件、焊接件",
    "201-299": "采购件、标准件",
}


class MemoryPackageError(ValueError):
    pass


class NamingMemoryPackage:
    def __init__(self, database: Database, directory: Path | None = None) -> None:
        self.database = database
        self.directory = Path(directory or ai_memory_dir())
        self.backups_dir = self.directory / "backups"

    @property
    def skill_path(self) -> Path:
        return self.directory / PACKAGE_FILES[0]

    @property
    def response_log_path(self) -> Path:
        return self.directory / "ai_raw_responses.jsonl"

    def generate(self, *, backup: bool = False) -> dict:
        self.directory.mkdir(parents=True, exist_ok=True)
        if backup:
            self._backup_current()
        snapshot = self._snapshot()
        memory = self._memory_json(snapshot)
        projects = self._project_code_json(snapshot)
        examples = self._example_rows(snapshot)
        self._write_text(self.directory / "naming_memory.json", json.dumps(memory, ensure_ascii=False, indent=2) + "\n")
        self._write_text(self.directory / "project_code_memory.json", json.dumps(projects, ensure_ascii=False, indent=2) + "\n")
        self._write_text(
            self.directory / "naming_examples.jsonl",
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in examples),
        )
        self._write_text(self.directory / "schema.json", json.dumps(self._schema(), ensure_ascii=False, indent=2) + "\n")
        self._write_text(self.skill_path, self._skill_markdown(snapshot, memory))
        return {
            "directory": str(self.directory),
            "rule_count": len(snapshot["rules"]),
            "project_count": len(snapshot["projects"]),
            "formal_name_count": len(snapshot["formal_names"]),
            "feedback_count": len(snapshot["feedback"]),
            "rejected_count": sum(item["action"] == "rejected" for item in snapshot["feedback"]),
            "updated_at": memory["updated_at"],
        }

    def export_zip(self, target: Path) -> Path:
        self.generate(backup=True)
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in PACKAGE_FILES:
                archive.write(self.directory / name, name)
        return target

    def inspect_zip(self, source: Path) -> dict:
        payloads = self._read_zip(source)
        memory = self._json_object(payloads["naming_memory.json"], "naming_memory.json")
        projects = self._json_object(payloads["project_code_memory.json"], "project_code_memory.json")
        examples = self._json_lines(payloads["naming_examples.jsonl"])
        schema = self._json_object(payloads["schema.json"], "schema.json")
        if str(schema.get("schema_version")) != "1.0" or str(memory.get("schema_version")) != "1.0":
            raise MemoryPackageError("记忆包 Schema 版本不受支持。")
        rules = memory.get("rules") if isinstance(memory.get("rules"), dict) else {}
        project_items = projects.get("projects") if isinstance(projects.get("projects"), list) else []
        return {
            "source": str(source), "rule_count": len(rules), "example_count": len(examples),
            "project_code_count": len(project_items), "updated_at": str(memory.get("updated_at") or ""),
            "skill_preview": payloads["MECHANICAL_NAMING_SKILL.md"][:1200],
        }

    def import_zip(self, source: Path, mode: str) -> dict:
        if mode not in {"merge", "rules", "preferences"}:
            raise MemoryPackageError("导入模式无效。")
        payloads = self._read_zip(source)
        memory = self._json_object(payloads["naming_memory.json"], "naming_memory.json")
        projects = self._json_object(payloads["project_code_memory.json"], "project_code_memory.json")
        examples = self._json_lines(payloads["naming_examples.jsonl"])
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        imported_rules = imported_examples = imported_projects = 0
        self.generate(backup=True)
        with self.database.transaction() as connection:
            if mode in {"merge", "rules"}:
                rules = memory.get("rules") if isinstance(memory.get("rules"), dict) else {}
                for key, item in rules.items():
                    if not isinstance(item, dict) or not str(item.get("content") or "").strip():
                        continue
                    exists = connection.execute("SELECT 1 FROM ai_rules WHERE rule_key=?", (str(key),)).fetchone()
                    if exists:
                        continue
                    connection.execute(
                        "INSERT INTO ai_rules(rule_key,title,content,priority,is_builtin,enabled,updated_at) VALUES (?,?,?,?,0,1,?)",
                        (str(key), str(item.get("title") or key), str(item["content"]), int(item.get("priority") or 500), now),
                    )
                    imported_rules += 1
            if mode in {"merge", "preferences"}:
                for item in examples:
                    ai_name = str(item.get("ai_suggestion") or "").strip()
                    final_name = str(item.get("final_name") or "").strip()
                    action = {"modified_and_accepted": "edited", "accepted": "adopted", "rejected": "rejected"}.get(str(item.get("action")), "edited")
                    if not ai_name or (action != "rejected" and not final_name):
                        continue
                    exists = connection.execute(
                        "SELECT 1 FROM ai_feedback WHERE project_id IS NULL AND module_code=? AND ai_name=? AND final_name=? AND action=?",
                        (str(item.get("module") or ""), ai_name, final_name, action),
                    ).fetchone()
                    if exists:
                        continue
                    connection.execute(
                        """INSERT INTO ai_feedback(project_id,module_code,ai_name,final_name,action,reject_reason,input_description,
                           confidence,use_count,created_at,updated_at) VALUES (NULL,?,?,?,?,?,?,?,?,?,?)""",
                        (str(item.get("module") or ""), ai_name, final_name, action, str(item.get("reject_reason") or ""),
                         str(item.get("input") or ""), int(item.get("confidence_value") or 80), 1, now, now),
                    )
                    imported_examples += 1
                project_items = projects.get("projects") if isinstance(projects.get("projects"), list) else []
                for item in project_items:
                    name, code = str(item.get("project_name") or "").strip(), str(item.get("project_code") or "").strip().upper()
                    if not name or not code:
                        continue
                    connection.execute(
                        """INSERT INTO ai_imported_project_codes(project_name,project_code,project_type,code_explanation,
                           confirmed,use_count,last_used_at,imported_at) VALUES (?,?,?,?,?,?,?,?)
                           ON CONFLICT(project_name,project_code) DO UPDATE SET
                           project_type=excluded.project_type,code_explanation=excluded.code_explanation,
                           confirmed=MAX(ai_imported_project_codes.confirmed,excluded.confirmed),
                           use_count=MAX(ai_imported_project_codes.use_count,excluded.use_count),
                           last_used_at=MAX(ai_imported_project_codes.last_used_at,excluded.last_used_at)""",
                        (name, code, str(item.get("project_type") or ""), str(item.get("code_explanation") or ""),
                         int(bool(item.get("confirmed", True))), int(item.get("use_count") or 1),
                         str(item.get("last_used_at") or ""), now),
                    )
                    imported_projects += 1
        self.generate()
        return {"rules": imported_rules, "examples": imported_examples, "project_codes": imported_projects}

    def _snapshot(self) -> dict:
        with self.database.connect() as connection:
            rules = [dict(row) for row in connection.execute("SELECT * FROM ai_rules WHERE enabled=1 ORDER BY priority DESC,id").fetchall()]
            projects = [dict(row) for row in connection.execute(
                """SELECT p.id,p.name,p.code,p.description,p.code_locked,p.last_used_at,COUNT(r.id) AS use_count
                   FROM projects p LEFT JOIN drawing_records r ON r.project_id=p.id
                   GROUP BY p.id ORDER BY p.last_used_at DESC"""
            ).fetchall()]
            imported_projects = [dict(row) for row in connection.execute("SELECT * FROM ai_imported_project_codes ORDER BY last_used_at DESC,id DESC").fetchall()]
            feedback = [dict(row) for row in connection.execute(
                """SELECT f.*,p.name AS project_name,p.code AS project_code FROM ai_feedback f
                   LEFT JOIN projects p ON p.id=f.project_id ORDER BY f.updated_at DESC"""
            ).fetchall()]
            formal_names = [dict(row) for row in connection.execute(
                """SELECT p.name AS project_name,p.code AS project_code,a.module_code,a.part_number,a.part_name,a.part_type,a.purpose,a.created_at
                   FROM ai_parts a LEFT JOIN projects p ON p.id=a.project_id WHERE a.confirmed=1
                   UNION ALL
                   SELECT r.project_name,r.project_code,'' AS module_code,r.drawing_number,r.part_name,'' AS part_type,r.purpose,r.created_at
                   FROM drawing_records r WHERE r.status='used' AND r.part_name<>''"""
            ).fetchall()]
        return {"rules": rules, "projects": projects, "imported_projects": imported_projects,
                "feedback": feedback, "formal_names": formal_names}

    def _memory_json(self, snapshot: dict) -> dict:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        rules = {item["rule_key"]: {"title": item["title"], "content": item["content"], "priority": item["priority"]} for item in snapshot["rules"]}
        suffixes = Counter(item["part_name"][-1:] for item in snapshot["formal_names"] if item.get("part_name"))
        part_types = Counter(item.get("part_type") or "未分类" for item in snapshot["formal_names"])
        term_preferences = {
            "导轨往复移动并承载机构": "滑座", "主动传递动力的轴": "主动轴", "固定伺服的块状机加工件": "伺服安装座",
        }
        for item in snapshot["feedback"]:
            if item["action"] in {"edited", "adopted"} and item.get("final_name"):
                term_preferences[item.get("input_description") or item["ai_name"]] = item["final_name"]
        return {
            "schema_version": "1.0", "updated_at": now, "rules": rules,
            "module_rules": MODULE_RULES, "numbering_rules": NUMBERING_RULES,
            "term_preferences": term_preferences,
            "part_type_preferences": dict(part_types),
            "project_code_preferences": {item["name"]: item["code"] for item in snapshot["projects"]},
            "rejected_patterns": {item["ai_name"]: item.get("reject_reason") or "用户拒绝" for item in snapshot["feedback"] if item["action"] == "rejected"},
            "statistics": {
                "fixed_rules": len(snapshot["rules"]), "historical_projects": len(snapshot["projects"]),
                "formal_names": len(snapshot["formal_names"]), "feedback": len(snapshot["feedback"]),
                "rejected": sum(item["action"] == "rejected" for item in snapshot["feedback"]),
                "common_suffixes": suffixes.most_common(10),
            },
        }

    def _example_rows(self, snapshot: dict) -> list[dict]:
        rows = []
        for item in snapshot["feedback"]:
            rows.append({
                "input": item.get("input_description") or "",
                "ai_suggestion": item["ai_name"], "final_name": item.get("final_name") or "",
                "action": {"edited": "modified_and_accepted", "adopted": "accepted", "rejected": "rejected"}[item["action"]],
                "project": item.get("project_name") or "导入记忆", "module": item.get("module_code") or "",
                "confidence": "high" if item.get("confidence", 0) >= 90 else "medium" if item.get("confidence", 0) > 0 else "low",
                "confidence_value": item.get("confidence", 0), "reject_reason": item.get("reject_reason") or "",
                "updated_at": item.get("updated_at") or "",
            })
        return rows

    def _project_code_json(self, snapshot: dict) -> dict:
        projects = [{
            "project_name": item["name"], "project_code": item["code"],
            "project_type": item.get("description") or "", "code_explanation": item.get("description") or "用户未单独填写代号解释",
            "confirmed": bool(item.get("code_locked")), "use_count": int(item.get("use_count") or 0),
            "last_used_at": item.get("last_used_at") or "", "source": "SQLite正式项目",
        } for item in snapshot["projects"]]
        projects.extend({
            "project_name": item["project_name"], "project_code": item["project_code"],
            "project_type": item.get("project_type") or "", "code_explanation": item.get("code_explanation") or "",
            "confirmed": bool(item.get("confirmed")), "use_count": int(item.get("use_count") or 1),
            "last_used_at": item.get("last_used_at") or "", "source": "导入记忆",
        } for item in snapshot["imported_projects"])
        return {"schema_version": "1.0", "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "projects": projects}

    @staticmethod
    def _schema() -> dict:
        return {
            "schema_version": "1.0", "format": "MechanicalProjectNamer AI naming memory package",
            "files": list(PACKAGE_FILES), "authority": "SQLite confirmed user data wins on conflict",
            "import_modes": ["merge", "rules", "preferences"],
            "sensitive_data": "API keys, Authorization headers and Windows credentials are forbidden",
        }

    @staticmethod
    def _skill_markdown(snapshot: dict, memory: dict) -> str:
        rules = "\n".join(f"- {item['title']}：{item['content']}" for item in snapshot["rules"])
        modules = "\n".join(f"- `{code}` {name}" for code, name in MODULE_RULES.items())
        numbering = "\n".join(f"- `{key}` {value}" for key, value in NUMBERING_RULES.items())
        examples = "\n".join(f"- `{item['project_code']}-{item['module_code'] or '00'}-{int(item['part_number']):03d} {item['part_name']}`" for item in snapshot["formal_names"][:30]) or "- 暂无正式案例"
        return f"""# 机械项目命名技能

本文件由机械项目命名助手从 SQLite 正式数据自动生成，可独立交给 Codex、DeepSeek Harness 或其他 AI Agent 使用。冲突时，以用户在 SQLite 中最终确认的结果为准。

## 用户机械零件统一命名规则

正式格式：`项目代号-模块编号-零件序号 零件名称`，例如 `ZGQ-03-005 压弧滑座`。

## 模块编号

{modules}

项目可以根据实际结构增加或删除模块。

## 零件编号

{numbering}

序号必须由本地数据库计算，AI 不得猜测、分配或修改。

## 名称规则

优先采用“位置/功能 + 零件类型”。名称应短、明确、稳定。尺寸、材料、热处理、加工规格、客户名称、最终版、修改版、新款、加厚版和修改2等内容放在备注、BOM、属性或版本记录中，不进入正式名称。

{rules}

## 用户确认案例

{examples}

## 使用优先级

用户手动固定规则 > 用户修改后采用 > 用户直接采用 > 历史正式项目名称 > AI 未确认建议。AI 未被采用的建议不得成为高可信记忆。

## 当前统计

- 固定命名规则：{memory['statistics']['fixed_rules']} 条
- 历史项目：{memory['statistics']['historical_projects']} 个
- 正式命名案例：{memory['statistics']['formal_names']} 个
- 用户纠偏：{memory['statistics']['feedback']} 条
"""

    def _backup_current(self) -> None:
        existing = [self.directory / name for name in PACKAGE_FILES if (self.directory / name).exists()]
        if not existing:
            return
        target = self.backups_dir / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        target.mkdir(parents=True, exist_ok=False)
        for path in existing:
            shutil.copy2(path, target / path.name)

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            remove_file(temporary, what="AI 记忆临时文件")

    @staticmethod
    def _json_object(text: str, name: str) -> dict:
        try:
            value = json.loads(text)
        except ValueError as error:
            raise MemoryPackageError(f"{name} 不是有效 JSON。") from error
        if not isinstance(value, dict):
            raise MemoryPackageError(f"{name} 顶层必须是对象。")
        return value

    @staticmethod
    def _json_lines(text: str) -> list[dict]:
        rows = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError as error:
                raise MemoryPackageError(f"naming_examples.jsonl 第 {number} 行无效。") from error
            if isinstance(item, dict):
                rows.append(item)
        return rows

    @staticmethod
    def _read_zip(source: Path) -> dict[str, str]:
        try:
            with zipfile.ZipFile(source, "r") as archive:
                names = archive.namelist()
                if len(names) != len(set(names)):
                    raise MemoryPackageError("记忆包包含重复文件名。")
                if any(Path(name).name != name or name not in PACKAGE_FILES for name in names):
                    raise MemoryPackageError("记忆包包含不允许的路径或文件。")
                missing = [name for name in PACKAGE_FILES if name not in names]
                if missing:
                    raise MemoryPackageError("记忆包缺少文件：" + "、".join(missing))
                if sum(item.file_size for item in archive.infolist()) > 20_000_000:
                    raise MemoryPackageError("记忆包解压后超过 20 MB。")
                return {name: archive.read(name).decode("utf-8") for name in PACKAGE_FILES}
        except (OSError, zipfile.BadZipFile, UnicodeDecodeError) as error:
            raise MemoryPackageError(f"无法读取记忆包：{error}") from error
