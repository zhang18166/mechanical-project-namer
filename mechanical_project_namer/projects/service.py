# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import re
import sqlite3
from typing import TYPE_CHECKING

from .models import Project
from ..numbering.v12 import normalize_project_code
from ..numbering.v12 import MODULES

if TYPE_CHECKING:
    from ..data.repository import Repository


CODE_PATTERN = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")


class ValidationError(ValueError):
    pass


class ProjectCodeChangeError(ValidationError):
    pass


class ProjectService:
    def __init__(self, repository: "Repository") -> None:
        self.repository = repository

    def create(
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
        name = name.strip()
        code = self.normalize_code(code)
        if not name:
            raise ValidationError("项目名称不能为空。")
        if not 1 <= start_number <= 999:
            raise ValidationError("初始图号必须在 001 到 999 之间。")
        try:
            project = self.repository.create_project(
                name, description.strip(), code, notes.strip(), directory.strip(),
                solidworks_part_directory.strip(), start_number, project_type
            )
            self.repository.set_modules(project.id, list(MODULES), {"00", "01", "02", "03", "04", "05", "07", "08"})
            return project
        except sqlite3.IntegrityError as error:
            raise ValidationError(f"项目代号 {code} 已存在，请使用其他代号。") from error

    def update(self, project_id: int, confirm_code_change: bool = False, **values) -> Project:
        current = self.repository.get_project(project_id)
        if "name" in values and not str(values["name"]).strip():
            raise ValidationError("项目名称不能为空。")
        if "code" in values:
            values["code"] = self.normalize_code(str(values["code"]))
            if values["code"] != current.code and not confirm_code_change:
                raise ProjectCodeChangeError("修改项目代号可能造成历史图号前缀不一致，是否继续？")
        values = {key: str(value).strip() for key, value in values.items()}
        try:
            return self.repository.update_project(project_id, **values)
        except sqlite3.IntegrityError as error:
            raise ValidationError(f"项目代号 {values.get('code', '')} 已存在。") from error

    @staticmethod
    def normalize_code(code: str) -> str:
        parsed = normalize_project_code(code)
        value = parsed.code
        if not value:
            raise ValidationError("项目代号不能为空。")
        if parsed.is_full_part_number:
            raise ValidationError(
                f"当前输入看起来是完整零件编号，不是项目代号。\n"
                f"建议拆分为：项目代号 {parsed.code}，模块编号 {parsed.module_code}，零件序号 {parsed.part_sequence:03d}。"
            )
        if len(value) > 40 or not CODE_PATTERN.fullmatch(value):
            raise ValidationError("项目代号仅支持大写字母、数字和分段连字符，例如 DQ-12-16。")
        return value
