# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QTextEdit, QVBoxLayout, QComboBox,
)

from ..projects.models import Project
from .common import make_field_label, style_button_box


class ProjectDialog(QDialog):
    def __init__(self, parent=None, project: Project | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("编辑项目" if project else "新建项目")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        heading = QLabel("编辑项目信息" if project else "创建新的机械项目")
        heading.setObjectName("dialogTitle")
        layout.addWidget(heading)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.name = QLineEdit(project.name if project else "")
        self.name.setPlaceholderText("例如：16螺纹钢筋打圈机")
        self.description = QTextEdit(project.description if project else "")
        self.description.setFixedHeight(72)
        self.description.setPlaceholderText("设备用途、加工对象和主要结构")
        self.code = QLineEdit(project.code if project else "")
        self.code.setPlaceholderText("例如：DQ-12-16")
        self.notes = QTextEdit(project.notes if project else "")
        self.notes.setFixedHeight(64)
        self.directory = QLineEdit(project.directory if project else "")
        browse = QPushButton("选择目录")
        browse.setProperty("kind", "secondary")
        browse.clicked.connect(self._browse)
        directory_row = QHBoxLayout()
        directory_row.setContentsMargins(0, 0, 0, 0)
        directory_row.addWidget(self.directory, 1)
        directory_row.addWidget(browse)
        self.solidworks_part_directory = QLineEdit(project.solidworks_part_directory if project else "")
        self.solidworks_part_directory.setPlaceholderText("例如：项目目录\\01_SolidWorks")
        sw_browse = QPushButton("选择目录")
        sw_browse.setProperty("kind", "secondary")
        sw_browse.clicked.connect(self._browse_solidworks)
        sw_directory_row = QHBoxLayout()
        sw_directory_row.setContentsMargins(0, 0, 0, 0)
        sw_directory_row.addWidget(self.solidworks_part_directory, 1)
        sw_directory_row.addWidget(sw_browse)
        self.start_number = QSpinBox()
        self.start_number.setRange(1, 999)
        self.start_number.setValue(project.start_number if project else 1)
        self.start_number.setPrefix("从 ")
        self.start_number.setSuffix(" 开始")
        self.start_number.setEnabled(project is None)
        self.project_type = QComboBox()
        self.project_type.addItem("正式项目", "formal")
        self.project_type.addItem("测试项目", "test")
        if project:
            self.project_type.setCurrentIndex(max(0, self.project_type.findData(project.project_type)))
        form.addRow(make_field_label("项目名称 *"), self.name)
        form.addRow(make_field_label("项目描述"), self.description)
        form.addRow(make_field_label("项目代号 *"), self.code)
        form.addRow(make_field_label("项目备注"), self.notes)
        form.addRow(make_field_label("项目目录"), directory_row)
        form.addRow(make_field_label("SOLIDWORKS零件目录"), sw_directory_row)
        form.addRow(make_field_label("项目类型"), self.project_type)
        layout.addLayout(form)
        if project:
            warning = QLabel("项目代号已锁定。修改时会再次要求确认，历史记录不会被改写。")
            warning.setObjectName("warningText")
            warning.setWordWrap(True)
            layout.addWidget(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        style_button_box(buttons, ok_is_primary=True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, alignment=Qt.AlignmentFlag.AlignRight)

    def values(self) -> dict:
        return {
            "name": self.name.text(),
            "description": self.description.toPlainText(),
            "code": self.code.text(),
            "notes": self.notes.toPlainText(),
            "directory": self.directory.text(),
            "solidworks_part_directory": self.solidworks_part_directory.text(),
            "start_number": self.start_number.value(),
            "project_type": self.project_type.currentData(),
        }

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择项目目录", self.directory.text())
        if selected:
            self.directory.setText(selected)

    def _browse_solidworks(self) -> None:
        initial = self.solidworks_part_directory.text()
        if not initial and self.directory.text():
            initial = str(Path(self.directory.text()) / "01_SolidWorks")
        selected = QFileDialog.getExistingDirectory(self, "选择SOLIDWORKS零件目录", initial)
        if selected:
            self.solidworks_part_directory.setText(selected)
