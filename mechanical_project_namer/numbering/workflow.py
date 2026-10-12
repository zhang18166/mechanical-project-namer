# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import logging

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

    def preview_named(self, project_id: int, module_code: str, part_type: str) -> str:
        project = self.repository.get_project(project_id)
        number = self.repository.get_next_named_number(project_id, module_code, part_type)
        return f"{project.code}-{str(module_code).zfill(2)}-{number:03d}"

    def copy_preview(self, project_id: int) -> str:
        return self.preview(project_id)

    def copy_named_preview(self, project_id: int, module_code: str, part_type: str, part_name: str = "") -> str:
        preview = self.preview_named(project_id, module_code, part_type)
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
        extra_notes: str = "", is_copied: bool = False,
    ) -> DrawingRecord:
        record = self.repository.save_named_record(
            project_id, module_code, part_type, part_name, extra_notes, is_copied=is_copied,
        )
        try:
            self.exporter.export_all(project_id)
        except Exception:
            LOGGER.exception("开放数据导出失败，SQLite 正式记录已保留")
        return record
