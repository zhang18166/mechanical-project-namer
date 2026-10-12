# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Project:
    id: int
    name: str
    description: str
    code: str
    notes: str
    directory: str
    solidworks_part_directory: str
    start_number: int
    next_number: int
    code_locked: bool
    archived: bool
    created_at: str
    updated_at: str
    last_used_at: str
    used_count: int = 0
    project_type: str = "formal"
    project_status: str = "normal"
    archived_at: str = ""
    deleted_at: str = ""
    module_count: int = 0
    latest_module: str = ""
    latest_number: str = ""

    @property
    def next_filename(self) -> str:
        return f"{self.code}-{self.next_number:03d}" if self.next_number <= 999 else "图号已用尽"


@dataclass(frozen=True, slots=True)
class DrawingRecord:
    id: int
    project_id: int
    project_name: str
    project_code: str
    drawing_number: int
    filename: str
    part_name: str
    purpose: str
    record_date: str
    created_at: str
    updated_at: str
    status: str
    is_copied: bool
    creation_method: str
    project_directory: str
    extra_notes: str
    module_code: str = "00"
    part_sequence: int = 0
    part_type: str = "legacy"
    full_name: str = ""
    file_path: str = ""
    relative_path: str = ""
    file_extension: str = ""
    file_size: int = 0
    file_exists: bool = False
    creation_source: str = ""
    solidworks_version: str = ""
    solidworks_service_pack: str = ""
    solidworks_template_path: str = ""
    solidworks_operation_id: str = ""

    @property
    def part_number(self) -> str:
        return f"{self.module_code}-{self.part_sequence:03d}"

    @property
    def display_name(self) -> str:
        return self.full_name or f"{self.filename} {self.part_name}".strip()
