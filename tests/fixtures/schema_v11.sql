-- Schema 11 DDL only; fictional tests.
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
    drawing_number INTEGER NOT NULL CHECK(drawing_number >= 1 AND (part_type = '通用编号' OR drawing_number <= 999)),
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
CREATE TABLE IF NOT EXISTS record_files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER NOT NULL REFERENCES drawing_records(id) ON DELETE CASCADE,
    path TEXT NOT NULL COLLATE NOCASE UNIQUE,
    extension TEXT NOT NULL, file_size INTEGER NOT NULL DEFAULT 0,
    mtime_ns INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_record_files_record ON record_files(record_id);
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

CREATE TABLE IF NOT EXISTS design_preferences (
    preference_id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL UNIQUE,
    source_message_id INTEGER REFERENCES ai_messages(message_id) ON DELETE SET NULL,
    habit_key TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'manual',
    evidence_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

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
