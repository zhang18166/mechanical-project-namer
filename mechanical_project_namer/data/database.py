# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..paths import database_path


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    notes TEXT NOT NULL DEFAULT '',
    directory TEXT NOT NULL DEFAULT '',
    solidworks_part_directory TEXT NOT NULL DEFAULT '',
    start_number INTEGER NOT NULL DEFAULT 1 CHECK(start_number BETWEEN 1 AND 999),
    next_number INTEGER NOT NULL DEFAULT 1 CHECK(next_number BETWEEN 1 AND 1000),
    code_locked INTEGER NOT NULL DEFAULT 1,
    archived INTEGER NOT NULL DEFAULT 0,
    project_type TEXT NOT NULL DEFAULT 'formal' CHECK(project_type IN ('formal', 'test')),
    project_status TEXT NOT NULL DEFAULT 'normal' CHECK(project_status IN ('normal', 'archived', 'trash')),
    archived_at TEXT,
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    project_id INTEGER,
    project_name TEXT NOT NULL DEFAULT '',
    project_code TEXT NOT NULL DEFAULT '',
    before_status TEXT NOT NULL DEFAULT '',
    after_status TEXT NOT NULL DEFAULT '',
    before_data TEXT NOT NULL DEFAULT '{}',
    after_data TEXT NOT NULL DEFAULT '{}',
    record_count INTEGER NOT NULL DEFAULT 0,
    computer_name TEXT NOT NULL DEFAULT '',
    app_version TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_audit_project ON project_audit_log(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS drawing_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    project_name TEXT NOT NULL,
    project_code TEXT NOT NULL,
    drawing_number INTEGER NOT NULL CHECK(drawing_number BETWEEN 1 AND 999),
    filename TEXT NOT NULL COLLATE NOCASE UNIQUE,
    part_name TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    record_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'used' CHECK(status IN ('used', 'void', 'reserved')),
    is_copied INTEGER NOT NULL DEFAULT 0,
    creation_method TEXT NOT NULL DEFAULT 'manual',
    project_directory TEXT NOT NULL DEFAULT '',
    extra_notes TEXT NOT NULL DEFAULT '',
    module_code TEXT NOT NULL DEFAULT '00',
    part_sequence INTEGER NOT NULL DEFAULT 0,
    part_type TEXT NOT NULL DEFAULT 'legacy',
    full_name TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL DEFAULT '',
    file_extension TEXT NOT NULL DEFAULT '',
    file_size INTEGER NOT NULL DEFAULT 0,
    file_exists INTEGER NOT NULL DEFAULT 0,
    creation_source TEXT NOT NULL DEFAULT '',
    solidworks_version TEXT NOT NULL DEFAULT '',
    solidworks_service_pack TEXT NOT NULL DEFAULT '',
    solidworks_template_path TEXT NOT NULL DEFAULT '',
    solidworks_operation_id TEXT,
    project_directory_snapshot TEXT NOT NULL DEFAULT '',
    UNIQUE(project_id, drawing_number)
);

CREATE INDEX IF NOT EXISTS idx_records_project ON drawing_records(project_id, drawing_number DESC);
CREATE INDEX IF NOT EXISTS idx_records_date ON drawing_records(record_date);
CREATE INDEX IF NOT EXISTS idx_projects_last_used ON projects(archived, last_used_at DESC);
CREATE TABLE IF NOT EXISTS project_modules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    module_code TEXT NOT NULL,
    module_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'default',
    last_part_type TEXT NOT NULL DEFAULT '自制机加工件',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, module_code)
);
CREATE INDEX IF NOT EXISTS idx_project_modules_project ON project_modules(project_id, module_code);

CREATE TABLE IF NOT EXISTS ai_plans (
    plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    prompt TEXT NOT NULL DEFAULT '', plan_type TEXT NOT NULL DEFAULT 'project',
    requested_model TEXT NOT NULL DEFAULT '', returned_model TEXT NOT NULL DEFAULT '',
    thinking_enabled INTEGER NOT NULL DEFAULT 0, reasoning_level TEXT NOT NULL DEFAULT 'high',
    status TEXT NOT NULL DEFAULT 'success', project_code_candidates TEXT NOT NULL DEFAULT '[]',
    recommended_modules TEXT NOT NULL DEFAULT '[]', missing_information TEXT NOT NULL DEFAULT '[]',
    quality_assessment TEXT NOT NULL DEFAULT '{}', response_json TEXT NOT NULL DEFAULT '{}',
    raw_response_reference TEXT NOT NULL DEFAULT '', input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0, cached_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost TEXT NOT NULL DEFAULT '', total_duration REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, adopted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_plans_project ON ai_plans(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ai_conversations (
    conversation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    title TEXT NOT NULL DEFAULT '新对话', model TEXT NOT NULL DEFAULT '',
    thinking_enabled INTEGER NOT NULL DEFAULT 0, reasoning_level TEXT NOT NULL DEFAULT 'high',
    conversation_summary TEXT NOT NULL DEFAULT '',
    summary_through_message_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES ai_conversations(conversation_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL, token_usage TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation ON ai_messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS project_memories (
    memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    module_code TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, content TEXT NOT NULL,
    source_message_id INTEGER REFERENCES ai_messages(message_id) ON DELETE SET NULL,
    confidence INTEGER NOT NULL DEFAULT 100, enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_memories_project ON project_memories(project_id, module_code, enabled);

-- Monotonic counters reserve numbers even when a record is deleted.
CREATE TABLE IF NOT EXISTS part_sequence_counters (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    module_code TEXT NOT NULL,
    part_type TEXT NOT NULL,
    next_sequence INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, module_code, part_type)
);

CREATE TABLE IF NOT EXISTS solidworks_operations (
    operation_id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    module_code TEXT NOT NULL,
    part_type TEXT NOT NULL,
    part_sequence INTEGER NOT NULL,
    part_name TEXT NOT NULL,
    full_name TEXT NOT NULL,
    target_path TEXT NOT NULL,
    template_path TEXT NOT NULL DEFAULT '',
    detected_version TEXT NOT NULL DEFAULT '',
    detected_build TEXT NOT NULL DEFAULT '',
    service_pack TEXT NOT NULL DEFAULT '',
    process_id INTEGER,
    status TEXT NOT NULL,
    error_stage TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    worker_result TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    recovery_required INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sw_operations_recovery
    ON solidworks_operations(recovery_required, status, updated_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sw_active_reservation
    ON solidworks_operations(project_id, module_code, part_type, part_sequence)
    WHERE status <> 'failed';

CREATE TABLE IF NOT EXISTS ai_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    is_builtin INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id),
    source_project_id INTEGER REFERENCES projects(id),
    source_record_id INTEGER REFERENCES drawing_records(id),
    module_code TEXT NOT NULL DEFAULT '',
    ai_name TEXT NOT NULL,
    final_name TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL CHECK(action IN ('adopted', 'edited', 'rejected')),
    reject_reason TEXT NOT NULL DEFAULT '',
    input_description TEXT NOT NULL DEFAULT '',
    confidence INTEGER NOT NULL DEFAULT 0,
    use_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_memory_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_project_preferences (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id),
    allow_ai_reference INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_ai_feedback_name ON ai_feedback(ai_name, module_code);
CREATE INDEX IF NOT EXISTS idx_ai_feedback_action ON ai_feedback(action, updated_at DESC);

CREATE TABLE IF NOT EXISTS ai_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id),
    module_code TEXT NOT NULL,
    part_number INTEGER NOT NULL,
    part_name TEXT NOT NULL,
    part_type TEXT NOT NULL DEFAULT '自制机加工件',
    purpose TEXT NOT NULL DEFAULT '',
    confirmed INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, module_code, part_number)
);

CREATE TABLE IF NOT EXISTS ai_imported_project_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    project_code TEXT NOT NULL,
    project_type TEXT NOT NULL DEFAULT '',
    code_explanation TEXT NOT NULL DEFAULT '',
    confirmed INTEGER NOT NULL DEFAULT 1,
    use_count INTEGER NOT NULL DEFAULT 1,
    last_used_at TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL,
    UNIQUE(project_name, project_code)
);

-- Retains numbering and deletion provenance after an erroneous record is removed.
CREATE TABLE IF NOT EXISTS number_reservations (
    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    module_code TEXT NOT NULL,
    part_category TEXT NOT NULL,
    part_sequence INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('used','voided','deleted_error','reserved')),
    original_record_id INTEGER,
    reserved_at TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    UNIQUE(project_id,module_code,part_category,part_sequence)
);
CREATE INDEX IF NOT EXISTS idx_number_reservations_project ON number_reservations(project_id,module_code,part_category);

CREATE TABLE IF NOT EXISTS record_delete_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER NOT NULL,
    project_id INTEGER,
    full_name TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    physical_file_exists INTEGER NOT NULL DEFAULT 0,
    action TEXT NOT NULL,
    created_at TEXT NOT NULL,
    app_version TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ai_plan_adoptions (
    adoption_id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES ai_plans(plan_id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    mode TEXT NOT NULL,
    original_payload TEXT NOT NULL DEFAULT '{}',
    final_payload TEXT NOT NULL DEFAULT '{}',
    changes_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_solidworks_creates (
    operation_id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    module_code TEXT NOT NULL,
    part_category TEXT NOT NULL,
    part_sequence INTEGER NOT NULL,
    part_name TEXT NOT NULL,
    extra_notes TEXT NOT NULL DEFAULT '',
    final_name TEXT NOT NULL DEFAULT '',
    original_action TEXT NOT NULL DEFAULT 'create_part',
    created_at TEXT NOT NULL
);
"""


DEFAULT_AI_RULES = (
    ("format", "正式命名格式", "项目代号-模块编号-零件序号 零件名称", 1000),
    ("modules", "默认模块规则", "00 整机总装；01 机架模块；02 送料模块；03 压弧/成型模块；04 锯切模块；05 导料/托料模块；06 气动模块；07 电气模块；08 防护模块；90 标准件/通用件；99 工程图、BOM、资料", 1000),
    ("numbering", "零件序号规则", "000 总成；001-099 自制机加工件；101-199 钣金件、焊接件；201-299 采购件、标准件。序号必须由本地程序生成。", 1000),
    ("name_style", "零件名称规则", "优先使用位置/功能 + 零件类型，名称短、明确、稳定，避免板1、轴1、零件3。", 1000),
    ("forbidden", "正式名称禁止项", "正式名称原则上不要加入尺寸、直径、材料、热处理、加工规格、客户名称、最终版、修改版、新款、加厚版等。", 1000),
    ("power_relation", "动力关系", "只有明确主动传递动力时使用主动轴、主动轮或驱动轴；明确被动跟随时使用从动轴、从动轮；没有依据不得使用主动或从动。", 900),
    ("guide_motion", "导轨移动零件", "安装在导轨上、可往复移动且承载其他机构的零件，优先考虑滑座、滑台、移动座，不要笼统命名为板、底板或安装板。", 900),
    ("mounting_parts", "安装类零件", "板状安装件使用XX安装板；块状或座体使用XX安装座、XX固定座；支撑结构使用XX支架。", 900),
    ("shaft_types", "轴类区分", "根据实际功能区分主动轴、从动轴、传动轴、支撑轴、轮轴、销轴、丝杆、转轴，不要把所有圆柱形零件统一叫轴。", 900),
    ("wheel_types", "轮类区分", "根据作用区分送料轮、主动轮、从动轮、压紧轮、导向轮、压弧轮、支撑轮。", 900),
    ("historical_consistency", "名称一致性", "同类结构优先沿用用户已经正式确认的历史名称；不要为了所谓优化频繁替换稳定称谓。", 900),
)


class ClosingConnection(sqlite3.Connection):
    """Commit/rollback and release SQLite handles when a context ends."""

    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or database_path())

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def migrate(self) -> None:
        self._backup_before_upgrade()
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(SCHEMA)
            row = connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
            if row is None:
                connection.execute("INSERT INTO schema_meta(version) VALUES (1)")
            connection.execute("UPDATE schema_meta SET version=MAX(version, 7)")
            now = __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds")
            for rule_key, title, content, priority in DEFAULT_AI_RULES:
                connection.execute(
                    "INSERT OR IGNORE INTO ai_rules(rule_key,title,content,priority,is_builtin,enabled,updated_at) VALUES (?,?,?,?,1,1,?)",
                    (rule_key, title, content, priority, now),
                )
            plan_columns = {row["name"] for row in connection.execute("PRAGMA table_info(ai_plans)").fetchall()}
            for name, definition in (("adopted_project_id", "INTEGER REFERENCES projects(id) ON DELETE SET NULL"), ("adoption_mode", "TEXT NOT NULL DEFAULT ''"), ("final_payload", "TEXT NOT NULL DEFAULT '{}'")):
                if name not in plan_columns:
                    connection.execute(f"ALTER TABLE ai_plans ADD COLUMN {name} {definition}")
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(ai_feedback)").fetchall()}
            if "use_count" not in columns:
                connection.execute("ALTER TABLE ai_feedback ADD COLUMN use_count INTEGER NOT NULL DEFAULT 1")
            if "input_description" not in columns:
                connection.execute("ALTER TABLE ai_feedback ADD COLUMN input_description TEXT NOT NULL DEFAULT ''")
            if "source_project_id" not in columns:
                connection.execute("ALTER TABLE ai_feedback ADD COLUMN source_project_id INTEGER REFERENCES projects(id)")
            if "source_record_id" not in columns:
                connection.execute("ALTER TABLE ai_feedback ADD COLUMN source_record_id INTEGER REFERENCES drawing_records(id)")
            conversation_columns = {row["name"] for row in connection.execute("PRAGMA table_info(ai_conversations)").fetchall()}
            if "conversation_summary" not in conversation_columns:
                connection.execute("ALTER TABLE ai_conversations ADD COLUMN conversation_summary TEXT NOT NULL DEFAULT ''")
            if "summary_through_message_id" not in conversation_columns:
                connection.execute("ALTER TABLE ai_conversations ADD COLUMN summary_through_message_id INTEGER NOT NULL DEFAULT 0")
            connection.execute("UPDATE ai_feedback SET source_project_id=project_id WHERE source_project_id IS NULL AND project_id IS NOT NULL")
            record_columns = {row["name"] for row in connection.execute("PRAGMA table_info(drawing_records)").fetchall()}
            for name, definition in (("module_code", "TEXT NOT NULL DEFAULT '00'"), ("part_sequence", "INTEGER NOT NULL DEFAULT 0"), ("part_type", "TEXT NOT NULL DEFAULT 'legacy'"), ("full_name", "TEXT NOT NULL DEFAULT ''")):
                if name not in record_columns:
                    connection.execute(f"ALTER TABLE drawing_records ADD COLUMN {name} {definition}")
            module_columns = {row["name"] for row in connection.execute("PRAGMA table_info(project_modules)").fetchall()}
            for name, definition in (("sort_order", "INTEGER NOT NULL DEFAULT 0"), ("source", "TEXT NOT NULL DEFAULT 'default'")):
                if name not in module_columns:
                    connection.execute(f"ALTER TABLE project_modules ADD COLUMN {name} {definition}")
            project_columns = {row["name"] for row in connection.execute("PRAGMA table_info(projects)").fetchall()}
            for name, definition in (
                ("project_type", "TEXT NOT NULL DEFAULT 'formal'"),
                ("project_status", "TEXT NOT NULL DEFAULT 'normal'"),
                ("archived_at", "TEXT"),
                ("deleted_at", "TEXT"),
                ("solidworks_part_directory", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in project_columns:
                    connection.execute(f"ALTER TABLE projects ADD COLUMN {name} {definition}")
            record_columns = {row["name"] for row in connection.execute("PRAGMA table_info(drawing_records)").fetchall()}
            for name, definition in (
                ("file_path", "TEXT NOT NULL DEFAULT ''"),
                ("relative_path", "TEXT NOT NULL DEFAULT ''"),
                ("file_extension", "TEXT NOT NULL DEFAULT ''"),
                ("file_size", "INTEGER NOT NULL DEFAULT 0"),
                ("file_exists", "INTEGER NOT NULL DEFAULT 0"),
                ("creation_source", "TEXT NOT NULL DEFAULT ''"),
                ("solidworks_version", "TEXT NOT NULL DEFAULT ''"),
                ("solidworks_service_pack", "TEXT NOT NULL DEFAULT ''"),
                ("solidworks_template_path", "TEXT NOT NULL DEFAULT ''"),
                ("solidworks_operation_id", "TEXT"),
                ("project_directory_snapshot", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in record_columns:
                    connection.execute(f"ALTER TABLE drawing_records ADD COLUMN {name} {definition}")
            connection.execute("UPDATE drawing_records SET project_directory_snapshot=project_directory WHERE project_directory_snapshot='' AND project_directory<>''")
            connection.execute("UPDATE projects SET project_status=CASE WHEN archived=1 THEN 'archived' ELSE 'normal' END WHERE project_status IS NULL OR project_status='' OR (archived=1 AND project_status='normal')")
            connection.execute("UPDATE drawing_records SET full_name=CASE WHEN part_name<>'' THEN filename || ' ' || part_name ELSE filename END WHERE full_name='' ")
            # Legacy back-fill only.  '总成' legitimately uses part_sequence=0 and a
            # legitimate 0 must never be rewritten into its drawing_number, so the
            # statement is restricted to rows that are still marked as legacy.
            connection.execute(
                "UPDATE drawing_records SET part_sequence=drawing_number "
                "WHERE part_sequence=0 AND part_type='legacy'"
            )
            connection.execute("UPDATE drawing_records SET module_code='00', part_type='legacy' WHERE module_code='' OR module_code IS NULL")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_records_v12_number ON drawing_records(project_id, module_code, part_type, part_sequence)")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_records_v12_number ON drawing_records(project_id, module_code, part_type, part_sequence) WHERE part_type <> 'legacy'")
            self._repair_singleton_numbering(connection)
            module_defaults = (("00", "整机总装"), ("01", "机架模块"), ("02", "送料模块"), ("03", "压弧/成型模块"), ("04", "锯切模块"), ("05", "导料/托料模块"), ("06", "气动模块"), ("07", "电气模块"), ("08", "防护模块"), ("90", "标准件/通用件"), ("99", "工程图、BOM、资料"))
            project_ids = [row["id"] for row in connection.execute("SELECT id FROM projects").fetchall()]
            for project_id in project_ids:
                for sort_order, (module_code, module_name) in enumerate(module_defaults):
                    connection.execute(
                        "INSERT OR IGNORE INTO project_modules(project_id,module_code,module_name,enabled,sort_order,source,last_part_type,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (project_id, module_code, module_name, int(module_code not in {"06", "90", "99"}), sort_order, "default", "自制机加工件", now, now),
                    )
                connection.execute("UPDATE project_modules SET sort_order=CAST(module_code AS INTEGER) WHERE project_id=? AND sort_order=0 AND module_code<>'00'", (project_id,))
            # Initialize monotonic counters from existing named records.  The
            # counter is deliberately never decremented by delete/void flows.
            # 总成 (singleton, fixed 000) never uses a counter at all.
            for row in connection.execute(
                """SELECT project_id,module_code,part_type,MAX(part_sequence) AS highest
                   FROM drawing_records WHERE part_type NOT IN ('legacy','总成')
                   GROUP BY project_id,module_code,part_type"""
            ).fetchall():
                starts = {"自制机加工件": 1, "钣金/焊接件": 101, "采购/标准件": 201}
                next_sequence = int(row["highest"] or starts.get(row["part_type"], 1)) + 1
                connection.execute(
                    """INSERT INTO part_sequence_counters(project_id,module_code,part_type,next_sequence,updated_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(project_id,module_code,part_type) DO UPDATE SET
                       next_sequence=MAX(part_sequence_counters.next_sequence, excluded.next_sequence), updated_at=excluded.updated_at""",
                    (row["project_id"], row["module_code"], row["part_type"], next_sequence, now),
                )

    @staticmethod
    def _repair_singleton_numbering(connection) -> None:
        """Idempotently restore the '总成 = 000, one per module' invariant.

        Earlier versions ran ``UPDATE ... SET part_sequence=drawing_number WHERE
        part_sequence=0`` on every start, which rewrote a legitimate 总成 ``0``
        into its legacy ``drawing_number`` and left the stale counter behind.
        Every statement below is guarded so that a second run is a no-op and so
        that no unique index can ever be violated.
        """
        # 1. Restore the sequence of the earliest 总成 row per module when its own
        #    filename/full_name already proves it is 000.
        connection.execute(
            """UPDATE drawing_records SET part_sequence=0
               WHERE part_type='总成' AND part_sequence<>0
                 AND id=(SELECT d.id FROM drawing_records d
                         WHERE d.project_id=drawing_records.project_id
                           AND d.module_code=drawing_records.module_code
                           AND d.part_type='总成'
                         ORDER BY d.id LIMIT 1)
                 AND (filename LIKE '%-000' OR full_name LIKE '%-000 %' OR full_name LIKE '%-000')
                 AND NOT EXISTS (SELECT 1 FROM drawing_records d2
                                 WHERE d2.project_id=drawing_records.project_id
                                   AND d2.module_code=drawing_records.module_code
                                   AND d2.part_type='总成' AND d2.part_sequence=0)"""
        )
        # 2. 总成 must not keep a monotonic counter row.
        connection.execute("DELETE FROM part_sequence_counters WHERE part_type='总成'")
        # 3. Align stale reservation rows with the corrected sequence.
        connection.execute(
            """UPDATE number_reservations SET part_sequence=0
               WHERE part_category='总成' AND part_sequence<>0
                 AND reservation_id=(SELECT r.reservation_id FROM number_reservations r
                                     WHERE r.project_id=number_reservations.project_id
                                       AND r.module_code=number_reservations.module_code
                                       AND r.part_category='总成'
                                     ORDER BY r.reservation_id LIMIT 1)
                 AND NOT EXISTS (SELECT 1 FROM number_reservations r2
                                 WHERE r2.project_id=number_reservations.project_id
                                   AND r2.module_code=number_reservations.module_code
                                   AND r2.part_category='总成' AND r2.part_sequence=0)"""
        )
        # 4. Same for still-active SOLIDWORKS reservations, which carry a partial
        #    unique index on (project, module, type, sequence).
        connection.execute(
            """UPDATE solidworks_operations SET part_sequence=0
               WHERE part_type='总成' AND part_sequence<>0 AND status<>'failed'
                 AND operation_id=(SELECT o.operation_id FROM solidworks_operations o
                                   WHERE o.project_id=solidworks_operations.project_id
                                     AND o.module_code=solidworks_operations.module_code
                                     AND o.part_type='总成' AND o.status<>'failed'
                                   ORDER BY o.updated_at, o.operation_id LIMIT 1)
                 AND NOT EXISTS (SELECT 1 FROM solidworks_operations o2
                                 WHERE o2.project_id=solidworks_operations.project_id
                                   AND o2.module_code=solidworks_operations.module_code
                                   AND o2.part_type='总成' AND o2.part_sequence=0
                                   AND o2.status<>'failed')"""
        )

    def _backup_before_upgrade(self) -> None:
        if not self.path.exists():
            return
        try:
            with sqlite3.connect(self.path) as connection:
                row = connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
                version = int(row[0]) if row else 0
            if version >= 7:
                return
        except sqlite3.DatabaseError:
            return
        target_dir = self.path.parent / "backups" / "database"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{self.path.stem}_pre_schema7_{__import__('datetime').datetime.now():%Y%m%d_%H%M%S_%f}.db"
        source = sqlite3.connect(self.path); destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close(); source.close()

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
