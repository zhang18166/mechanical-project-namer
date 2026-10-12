# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from ..data.database import Database
from ..numbering import is_singleton_part_type, singleton_exists_message, singleton_sequence
from ..paths import ai_memory_dir, database_path


@dataclass(frozen=True, slots=True)
class MemoryContext:
    rules: tuple[dict, ...]
    current_project: dict | None
    current_parts: tuple[dict, ...]
    similar_projects: tuple[dict, ...]
    similar_parts: tuple[dict, ...]
    feedback: tuple[dict, ...]
    module_code: str = ""
    part_type: str = ""
    next_part_number: int | None = None

    @property
    def prompt_summary(self) -> str:
        lines = ["固定命名规则："]
        lines.extend(f"- {item['title']}：{item['content']}" for item in self.rules)
        if self.current_project:
            lines.append(f"当前项目：{self.current_project['name']}（{self.current_project['code']}）")
        if self.module_code:
            lines.append(f"当前模块：{self.module_code}（模块编号由本地固定规则确定）")
        if self.part_type:
            lines.append(f"零件类别：{self.part_type}（编号区间由本地固定规则确定）")
        if self.next_part_number is not None:
            lines.append("下一个零件序号由本地程序计算并组合，AI 不得生成或修改序号。")
        if self.current_parts:
            lines.append("当前项目模块已有零件：")
            lines.extend(f"- {item['module_code']}-{item['part_number']:03d} {item['part_name']}" for item in self.current_parts)
        if self.similar_projects:
            lines.append("历史相似项目：")
            lines.extend(f"- {item['name']}（{item['code']}）" for item in self.similar_projects)
        if self.similar_parts:
            lines.append("历史相似零件：")
            lines.extend(f"- {item['project_code']}-{item['module_code']}-{item['part_number']:03d} {item['part_name']}" for item in self.similar_parts)
        if self.feedback:
            lines.append("用户纠偏记录：")
            lines.extend(
                f"- AI“{item['ai_name']}” -> 用户“{item['final_name']}”（{item['action']}）"
                for item in self.feedback
            )
        return "\n".join(lines)


class NamingMemory:
    def __init__(self, database: Database, package_directory: Path | None = None) -> None:
        self.database = database
        if package_directory is not None:
            self.package_directory = Path(package_directory)
        elif database.path.resolve() == database_path().resolve():
            self.package_directory = ai_memory_dir()
        else:
            self.package_directory = database.path.parent / "ai_memory"

    def rules(self) -> list[dict]:
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM ai_rules WHERE enabled=1 ORDER BY priority DESC, id"
            ).fetchall()]

    @staticmethod
    def infer_module_code(description: str) -> str:
        mappings = (
            ("00", ("整机", "总装")), ("01", ("机架", "床身")), ("02", ("送料", "进料")),
            ("03", ("压弧", "成型", "制圈")), ("04", ("锯切", "切割")),
            ("05", ("导料", "托料", "出料")), ("06", ("气动", "气缸")),
            ("07", ("电气", "电控")), ("08", ("防护", "护罩")),
            ("90", ("标准件", "通用件")), ("99", ("工程图", "BOM", "资料")),
        )
        explicit = re.search(r"(?:模块|所属模块)?\s*(\d{2})(?:\s|号|模块)", description)
        if explicit and any(explicit.group(1) == code for code, _ in mappings):
            return explicit.group(1)
        return next((code for code, words in mappings if any(word in description for word in words)), "")

    @staticmethod
    def infer_part_type(description: str) -> str:
        for part_type, words in (
            ("总成", ("总成", "装配体")), ("钣金件", ("钣金", "折弯板")),
            ("焊接件", ("焊接", "焊件")), ("采购件", ("采购件", "外购")),
            ("标准件", ("标准件", "螺栓", "轴承")), ("自制机加工件", ("机加工", "自制")),
        ):
            if any(word in description for word in words):
                return part_type
        return "自制机加工件"

    @staticmethod
    def _similarity(query: str, *values: str) -> int:
        query = re.sub(r"\s+", "", query)
        text = re.sub(r"\s+", "", " ".join(value or "" for value in values))
        if not query or not text:
            return 0
        score = 20 if text in query or query in text else 0
        query_pairs = {query[index:index + 2] for index in range(max(0, len(query) - 1))}
        text_pairs = {text[index:index + 2] for index in range(max(0, len(text) - 1))}
        return score + len(query_pairs & text_pairs)

    def build_context(self, description: str, project_id: int | None = None, module_code: str = "") -> MemoryContext:
        module_code = module_code or self.infer_module_code(description)
        part_type = self.infer_part_type(description)
        with self.database.connect() as connection:
            project = None
            current_parts: list[dict] = []
            if project_id is not None:
                row = connection.execute("SELECT id,name,code,description FROM projects WHERE id=?", (project_id,)).fetchone()
                project = dict(row) if row else None
                if project:
                    part_sql = "SELECT module_code,part_number,part_name,part_type,purpose FROM ai_parts WHERE project_id=?"
                    params: list[object] = [project_id]
                    if module_code:
                        part_sql += " AND module_code=?"
                        params.append(module_code)
                    current_parts = [dict(row) for row in connection.execute(part_sql, params).fetchall()]
            archived_setting = connection.execute("SELECT value FROM ai_memory_settings WHERE key='reference_archived_projects'").fetchone()
            include_archived = archived_setting is None or str(archived_setting["value"]).lower() not in {"0", "false", "no"}
            project_rows = [dict(row) for row in connection.execute(
                "SELECT p.id,p.name,p.code,p.description FROM projects p LEFT JOIN ai_project_preferences ap ON ap.project_id=p.id WHERE (p.project_status='normal' OR (p.project_status='archived' AND ?)) AND COALESCE(ap.allow_ai_reference,1)=1 ORDER BY p.last_used_at DESC LIMIT 100",
                (int(include_archived),),
            ).fetchall()]
            imported_rows = [dict(row) for row in connection.execute(
                "SELECT -id AS id,project_name AS name,project_code AS code,project_type AS description FROM ai_imported_project_codes WHERE confirmed=1 ORDER BY last_used_at DESC,id DESC LIMIT 100"
            ).fetchall()]
            project_rows.extend(imported_rows)
            similar_projects = sorted(
                (item for item in project_rows if item["id"] != project_id and self._similarity(description, item["name"], item["code"], item["description"]) >= 2),
                key=lambda item: self._similarity(description, item["name"], item["code"], item["description"]), reverse=True,
            )[:5]
            part_rows = [dict(row) for row in connection.execute(
                """SELECT a.project_id,p.code AS project_code,a.module_code,a.part_number,a.part_name,a.purpose
                   FROM ai_parts a LEFT JOIN projects p ON p.id=a.project_id WHERE a.confirmed=1 AND p.project_status<>'trash'
                   UNION ALL
                   SELECT r.project_id,r.project_code,'' AS module_code,r.drawing_number AS part_number,r.part_name,r.purpose
                   FROM drawing_records r JOIN projects p2 ON p2.id=r.project_id WHERE r.status='used' AND r.part_name<>'' AND p2.project_status<>'trash'
                   ORDER BY part_number DESC LIMIT 500"""
            ).fetchall()]
            similar_parts = sorted(
                (item for item in part_rows if item["project_id"] != project_id and (not module_code or not item["module_code"] or item["module_code"] == module_code) and self._similarity(description, item["part_name"], item["purpose"]) >= 1),
                key=lambda item: self._similarity(description, item["part_name"], item["purpose"]), reverse=True,
            )[:10]
            feedback_rows = [dict(row) for row in connection.execute(
                "SELECT ai_name,final_name,action,module_code,reject_reason,input_description,confidence,use_count FROM ai_feedback WHERE action IN ('adopted','edited') ORDER BY updated_at DESC LIMIT 100"
            ).fetchall()]
            feedback = sorted(
                (item for item in feedback_rows if (not module_code or not item["module_code"] or item["module_code"] == module_code) and self._similarity(description, item["input_description"], item["ai_name"], item["final_name"]) >= 1),
                key=lambda item: (self._similarity(description, item["input_description"], item["ai_name"], item["final_name"]), item["confidence"], item["use_count"]), reverse=True,
            )[:5]
        rules = self.rules()
        preferences = self.preference_summary()
        rules.append({
            "title": "用户命名习惯统计",
            "content": f"历史项目 {preferences['project_count']} 个；项目代号平均长度 {preferences['average_code_length']}；带连字符代号 {preferences['hyphen_style_count']} 个；常用零件名称后缀 {preferences['common_suffixes']}",
        })
        next_number = self.allocate_part_number(project_id, module_code, part_type) if project_id is not None and module_code else None
        return MemoryContext(
            tuple(rules), project, tuple(current_parts), tuple(similar_projects), tuple(similar_parts),
            tuple(feedback), module_code, part_type, next_number,
        )

    def allocate_part_number(self, project_id: int | None, module_code: str, part_type: str = "自制机加工件") -> int:
        if is_singleton_part_type(part_type):
            # 总成：每模块仅一个，序号恒定为 0，不参与递增计数。
            with self.database.connect() as connection:
                existing = connection.execute(
                    "SELECT 1 FROM ai_parts WHERE project_id IS ? AND module_code=? AND part_number=?",
                    (project_id, module_code, singleton_sequence(part_type)),
                ).fetchone()
            if existing is not None:
                raise ValueError(singleton_exists_message(part_type))
            return singleton_sequence(part_type)
        ranges = {"自制机加工件": (1, 99), "钣金件": (101, 199), "焊接件": (101, 199), "采购件": (201, 299), "标准件": (201, 299)}
        start, end = ranges.get(part_type, (1, 99))
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT MAX(part_number) AS current FROM ai_parts WHERE project_id IS ? AND module_code=? AND part_number BETWEEN ? AND ?",
                (project_id, module_code, start, end),
            ).fetchone()
            next_number = start if row["current"] is None else int(row["current"]) + 1
            if next_number > end:
                raise ValueError(f"模块 {module_code} 的{part_type}序号已用尽。")
        return next_number

    def save_part(self, project_id: int | None, module_code: str, part_number: int, part_name: str, part_type: str, purpose: str = "") -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO ai_parts(project_id,module_code,part_number,part_name,part_type,purpose,confirmed,created_at) VALUES (?,?,?,?,?,?,1,?)",
                (project_id, module_code, part_number, part_name, part_type, purpose, now),
            )
        self.refresh_package()

    def record_feedback(self, project_id: int | None, module_code: str, ai_name: str, final_name: str = "", action: str = "rejected", reject_reason: str = "", input_description: str = "") -> int:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        confidence = {"edited": 100, "adopted": 90, "rejected": -80}.get(action, 0)
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM ai_feedback WHERE project_id IS ? AND module_code=? AND ai_name=? AND final_name=? AND action=?",
                (project_id, module_code, ai_name, final_name, action),
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE ai_feedback SET use_count=use_count+1,reject_reason=?,input_description=CASE WHEN ?<>'' THEN ? ELSE input_description END,updated_at=? WHERE id=?",
                    (reject_reason, input_description, input_description, now, existing["id"]),
                )
                feedback_id = int(existing["id"])
            else:
                cursor = connection.execute(
                    "INSERT INTO ai_feedback(project_id,source_project_id,module_code,ai_name,final_name,action,reject_reason,input_description,confidence,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (project_id, project_id, module_code, ai_name, final_name, action, reject_reason, input_description, confidence, now, now),
                )
                feedback_id = int(cursor.lastrowid)
        self.refresh_package()
        return feedback_id

    def preferred_correction(self, ai_name: str, module_code: str = "") -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT final_name FROM ai_feedback WHERE ai_name=? AND final_name<>'' AND action IN ('edited','adopted') AND (?='' OR module_code=?) ORDER BY confidence DESC,use_count DESC,updated_at DESC LIMIT 1",
                (ai_name, module_code, module_code),
            ).fetchone()
        return str(row["final_name"]) if row else None

    def feedback(self, limit: int = 100) -> list[dict]:
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM ai_feedback ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()]

    def clear_learning(self) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM ai_feedback")
        self.refresh_package()

    def delete_feedback(self, feedback_id: int) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM ai_feedback WHERE id=?", (feedback_id,))
        self.refresh_package()

    def set_project_reference(self, project_id: int, allowed: bool) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO ai_project_preferences(project_id,allow_ai_reference) VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET allow_ai_reference=excluded.allow_ai_reference",
                (project_id, int(allowed)),
            )

    def set_archived_reference(self, allowed: bool) -> None:
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO ai_memory_settings(key,value) VALUES ('reference_archived_projects',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if allowed else "0",))

    def archived_reference_allowed(self) -> bool:
        with self.database.connect() as connection:
            row = connection.execute("SELECT value FROM ai_memory_settings WHERE key='reference_archived_projects'").fetchone()
        return row is None or str(row["value"]).lower() not in {"0", "false", "no"}

    def project_reference_allowed(self, project_id: int) -> bool:
        with self.database.connect() as connection:
            row = connection.execute("SELECT allow_ai_reference FROM ai_project_preferences WHERE project_id=?", (project_id,)).fetchone()
        return True if row is None else bool(row["allow_ai_reference"])

    def preference_summary(self) -> dict:
        with self.database.connect() as connection:
            projects = connection.execute("SELECT code FROM projects WHERE archived=0 ORDER BY created_at").fetchall()
            names = [row["part_name"] for row in connection.execute("SELECT part_name FROM drawing_records WHERE status='used' AND part_name<>''").fetchall()]
            edited = [row["final_name"] for row in connection.execute("SELECT final_name FROM ai_feedback WHERE action IN ('edited','adopted') AND final_name<>''").fetchall()]
        codes = [row["code"] for row in projects]
        suffix_counts: dict[str, int] = {}
        for name in names + edited:
            suffix = name[-1:] if name else ""
            if suffix:
                suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
        return {
            "project_count": len(codes),
            "average_code_length": round(sum(map(len, codes)) / len(codes), 1) if codes else 0,
            "hyphen_style_count": sum("-" in code for code in codes),
            "common_suffixes": sorted(suffix_counts.items(), key=lambda item: (-item[1], item[0]))[:8],
        }

    def update_rule(self, rule_key: str, content: str) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.database.transaction() as connection:
            connection.execute("UPDATE ai_rules SET content=?, updated_at=? WHERE rule_key=? AND is_builtin=1", (content, now, rule_key))
        self.refresh_package()

    def reset_rules(self) -> None:
        from ..data.database import DEFAULT_AI_RULES

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.database.transaction() as connection:
            for rule_key, _title, content, _priority in DEFAULT_AI_RULES:
                connection.execute("UPDATE ai_rules SET content=?,updated_at=? WHERE rule_key=?", (content, now, rule_key))
        self.refresh_package()

    def refresh_package(self, *, backup: bool = False) -> dict:
        from .memory_package import NamingMemoryPackage

        return NamingMemoryPackage(self.database, self.package_directory).generate(backup=backup)
