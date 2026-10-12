# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QPlainTextEdit,
    QVBoxLayout, QWidget,
)

from .common import make_field_label, status_badge, style_button_box


TYPE_NAMES = {"formal": "正式项目", "test": "测试项目"}
STATUS_NAMES = {"normal": "正常", "archived": "已归档", "trash": "回收站"}


def _value_label(value: str) -> QLabel:
    text = QLabel(str(value))
    text.setWordWrap(True)
    text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return text


def _property_block(title: str, rows: list[tuple[str, QWidget]]) -> tuple[QLabel, QGridLayout]:
    heading = QLabel(title)
    heading.setObjectName("sectionTitle")
    grid = QGridLayout()
    grid.setHorizontalSpacing(16)
    grid.setVerticalSpacing(8)
    for row, (label, widget) in enumerate(rows):
        grid.addWidget(make_field_label(label), row, 0)
        grid.addWidget(widget, row, 1)
    return heading, grid


class ProjectPropertiesDialog(QDialog):
    def __init__(self, properties: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("项目属性")
        self.resize(760, 640)
        project = properties["project"]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        title = QLabel(f"{project.name} · {project.code}")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        # 基本信息
        type_badge = status_badge(project.project_type, TYPE_NAMES.get(project.project_type, project.project_type))
        status_badge_w = status_badge(project.project_status, STATUS_NAMES.get(project.project_status, project.project_status))
        basic_rows = [
            ("项目名称", _value_label(project.name)),
            ("项目代号", _value_label(project.code)),
            ("项目类型", type_badge),
            ("项目状态", status_badge_w),
        ]
        # 文件路径
        path_rows = [
            ("项目目录", _value_label(project.directory or "未设置")),
            ("SOLIDWORKS零件目录", _value_label(project.solidworks_part_directory or "未设置")),
        ]
        # 项目统计
        proj_rows = [
            ("模块数量", _value_label(project.module_count)),
            ("正式零件", _value_label(properties["formal_count"])),
            ("作废记录", _value_label(properties["void_count"])),
            ("创建时间", _value_label(project.created_at)),
            ("最后修改", _value_label(project.updated_at)),
            ("最后使用", _value_label(project.last_used_at)),
        ]
        # AI 统计
        ai_rows = [
            ("AI规划次数", _value_label(properties["ai_plan_count"])),
            ("AI建议采用", _value_label(properties["adopted_plan_count"])),
            ("项目记忆", _value_label(properties["project_memory_count"])),
            ("命名纠偏", _value_label(properties["feedback_count"])),
            ("项目对话", _value_label(properties["conversation_count"])),
        ]

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(14)
        for subtitle, rows in (
            ("基本信息", basic_rows + path_rows),
            ("项目统计", proj_rows),
            ("AI 统计", ai_rows),
        ):
            heading, grid = _property_block(subtitle, rows)
            container_layout.addWidget(heading)
            container_layout.addLayout(grid)

        # 描述 / 备注（长文本独立区块，避免和键值表格抢视觉）
        desc_heading = QLabel("描述 / 备注")
        desc_heading.setObjectName("sectionTitle")
        container_layout.addWidget(desc_heading)
        description = _value_label(project.description or "无")
        notes = _value_label(project.notes or "无")
        container_layout.addWidget(description)
        container_layout.addWidget(notes)

        layout.addWidget(container)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        style_button_box(close, ok_is_primary=False)
        close.rejected.connect(self.reject)
        layout.addWidget(close)


class DuplicateProjectDialog(QDialog):
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("复制项目")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        title = QLabel(f"原项目：{project.name} · {project.code}")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.name = QLineEdit(project.name + " 副本")
        self.code = QLineEdit(); self.code.setPlaceholderText("必须使用新的项目代号")
        self.directory = QLineEdit()
        browse = QPushButton("选择目录"); browse.setProperty("kind", "secondary"); browse.clicked.connect(self._browse)
        directory_row = QHBoxLayout(); directory_row.setContentsMargins(0, 0, 0, 0)
        directory_row.addWidget(self.directory, 1); directory_row.addWidget(browse)
        self.project_type = QComboBox(); self.project_type.addItem("正式项目", "formal"); self.project_type.addItem("测试项目", "test")
        form.addRow(make_field_label("新项目名称 *"), self.name)
        form.addRow(make_field_label("新项目代号 *"), self.code)
        form.addRow(make_field_label("新项目目录"), directory_row)
        form.addRow(make_field_label("项目类型"), self.project_type)
        layout.addLayout(form)
        self.copy_modules = QCheckBox("复制模块结构、名称和顺序"); self.copy_modules.setChecked(True)
        self.copy_description = QCheckBox("复制项目描述"); self.copy_description.setChecked(True)
        self.copy_notes = QCheckBox("复制项目备注"); self.copy_notes.setChecked(True)
        self.copy_memories = QCheckBox("复制项目专属AI记忆")
        for widget in (self.copy_modules, self.copy_description, self.copy_notes, self.copy_memories): layout.addWidget(widget)
        note = QLabel("默认不复制零件记录和编号使用状态；新项目各类别从起始编号开始。")
        note.setObjectName("warningText"); note.setWordWrap(True); layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("创建副本")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        style_button_box(buttons, ok_is_primary=True)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def values(self):
        return {"name": self.name.text(), "code": self.code.text(), "directory": self.directory.text(),
                "copy_modules": self.copy_modules.isChecked(), "copy_description": self.copy_description.isChecked(),
                "copy_notes": self.copy_notes.isChecked(), "copy_memories": self.copy_memories.isChecked(),
                "project_type": self.project_type.currentData()}

    def _browse(self):
        selected = QFileDialog.getExistingDirectory(self, "选择新项目目录", self.directory.text())
        if selected: self.directory.setText(selected)


class NumberingStatusDialog(QDialog):
    def __init__(self, project, status: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("项目编号状态"); self.resize(640, 600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        title = QLabel(f"{project.name} · {project.code}")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        text = QPlainTextEdit(); text.setReadOnly(True)
        lines = []
        for module in status:
            lines.append(f"{module['module_code']} {module['module_name']}")
            lines.extend(f"  {item['part_type']}：下一个 {item['next_number']}" for item in module["types"])
            lines.append("")
        text.setPlainText("\n".join(lines)); layout.addWidget(text)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        style_button_box(close, ok_is_primary=False)
        close.rejected.connect(self.reject); layout.addWidget(close)


class PermanentDeleteDialog(QDialog):
    def __init__(self, project, properties: dict, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("永久删除项目")
        self.setMinimumWidth(680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        title = QLabel("永久删除回收站项目"); title.setObjectName("dialogTitle"); layout.addWidget(title)
        details = QLabel(
            f"项目名称：{project.name}\n项目代号：{project.code}\n模块数量：{project.module_count}\n"
            f"零件记录：{project.used_count}\nAI规划：{properties['ai_plan_count']}\n"
            f"项目记忆：{properties['project_memory_count']}\n命名纠偏：{properties['feedback_count']}\n"
            f"项目目录：{project.directory or '未设置'}"
        ); details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); layout.addWidget(details)
        warning = QLabel("永久删除只删除软件数据库中的该项目资料。不会删除项目目录中的 SolidWorks、STEP、NX、PDF、NC 或其他设计文件。删除前会自动备份 SQLite 数据库。")
        warning.setObjectName("warningText"); warning.setWordWrap(True); layout.addWidget(warning)
        layout.addWidget(make_field_label("请输入项目代号或完整项目名称以确认："))
        self.confirmation = QLineEdit(); layout.addWidget(self.confirmation)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.delete_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.delete_button.setText("永久删除")
        self.delete_button.setEnabled(False)
        self.delete_button.setProperty("kind", "danger")
        self.confirmation.textChanged.connect(lambda text: self.delete_button.setEnabled(text.strip() in {project.code, project.name}))
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_button.setText("取消")
        cancel_button.setProperty("kind", "secondary")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
