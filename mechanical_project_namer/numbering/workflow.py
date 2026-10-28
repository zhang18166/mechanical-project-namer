# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import logging
from .simple import SIMPLE_TYPE, module_code as normalize_module, choose_sequence, full_name as simple_full_name

from ..data.exporter import DataExporter
from ..data.repository import Repository
from ..projects.models import DrawingRecord


LOGGER = logging.getLogger(__name__)


class NamingWorkflow:
    def __init__(self, repository: Repository, exporter: DataExporter) -> None:
        self.repository = repository
        self.exporter = exporter

    def preview(self, project_id: int) -> str:
        return self.repository.get_project(project_id).next_filename

    def preview_named(self, project_id: int, module_code: str, part_type: str, sequence_override=None) -> str:
        project = self.repository.get_project(project_id)
        if sequence_override is not None and part_type == SIMPLE_TYPE:
            with self.repository.database.connect() as connection:
                number = choose_sequence(connection, project_id, module_code, sequence_override)
        else:
            number = self.repository.get_next_named_number(project_id, module_code, part_type)
        return f"{project.code}-{normalize_module(module_code)}-{number:03d}"

    def copy_preview(self, project_id: int) -> str:
        return self.preview(project_id)

    def copy_named_preview(self, project_id: int, module_code: str, part_type: str, part_name: str = "", sequence_override=None) -> str:
        from ..file_sync import ensure_external_numbers_registered
        ensure_external_numbers_registered(self.repository, project_id)
        preview = self.preview_named(project_id, module_code, part_type, sequence_override)
        if part_type == SIMPLE_TYPE:
            from ..solidworks.validation import validate_part_name
            validate_part_name(part_name.strip())
            return f"{preview}-{part_name.strip()}"
        return f"{preview} {part_name.strip()}".strip()

    def save_and_advance(
        self,
        project_id: int,
        part_name: str,
        purpose: str,
        extra_notes: str = "",
        is_copied: bool = False,
    ) -> DrawingRecord:
        record = self.repository.save_next_record(
            project_id, part_name.strip(), purpose.strip(), extra_notes.strip(), is_copied=is_copied
        )
        try:
            self.exporter.export_all(project_id)
        except Exception:
            LOGGER.exception("开放数据导出失败，SQLite 正式记录已保留")
        return record

    def save_named_and_advance(
        self, project_id: int, module_code: str, part_type: str, part_name: str,
        extra_notes: str = "", is_copied: bool = False, sequence_override=None,
    ) -> DrawingRecord:
        record = self.repository.save_named_record(
            project_id, module_code, part_type, part_name, extra_notes, is_copied=is_copied, sequence_override=sequence_override,
        )
        try:
            self.exporter.export_all(project_id)
        except Exception:
            LOGGER.exception("开放数据导出失败，SQLite 正式记录已保留")
        return record
