# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTextEdit, QVBoxLayout, QHBoxLayout, QScrollArea, QWidget,
)

from ..projects.models import DrawingRecord
from .common import make_field_label, STATUS_NAMES, status_badge, style_button_box


class RecordDetailDialog(QDialog):
    def __init__(self, record: DrawingRecord, parent=None, associated_files=()) -> None:
        super().__init__(parent)
        self.record = record
        self.mark_void_requested = False
        self.setWindowTitle("图号记录详情")
        self.setMinimumWidth(580)
        self.setMinimumHeight(440)
        self.resize(660, 640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        title = QLabel(record.filename)
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        fields = QWidget()
        form = QFormLayout(fields)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(12)
        form.addRow(make_field_label("项目"), QLabel(f"{record.project_name}  ·  {record.project_code}"))
        explanation = QLabel("本次修改仅更新软件记录，不会自动修改实际文件名称。")
        explanation.setWordWrap(True)
        form.addRow(make_field_label("说明"), explanation)
        form.addRow(make_field_label("日期"), QLabel(record.record_date))
        status_label = status_badge(record.status)
        status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        form.addRow(make_field_label("状态"), status_label)
        self.file_path = QLineEdit(record.file_path or "未关联实际文件")
        self.file_path.setReadOnly(True)
        self.file_path.setToolTip(record.file_path or "该记录尚未关联实际设计文件。")
        form.addRow(make_field_label("文件路径"), self.file_path)
        if associated_files:
            self.associated_files = QTextEdit('\n'.join(f['path'] for f in associated_files))
            self.associated_files.setReadOnly(True)
            self.associated_files.setMaximumHeight(96)
            form.addRow(make_field_label('关联文件'), self.associated_files)
        self.file_state = QLabel("未检查" if record.file_path else "未关联实际文件")
        self.file_state.setWordWrap(True)
        self.check_file_button = QPushButton("检查文件")
        self.check_file_button.setProperty("kind", "secondary")
        self.check_file_button.setEnabled(bool(record.file_path))
        self.check_file_button.clicked.connect(self._check_file)
        file_row = QHBoxLayout()
        file_row.addWidget(self.file_state, 1)
        file_row.addWidget(self.check_file_button)
        form.addRow(make_field_label("文件状态"), file_row)
        self.part_name = QLineEdit(record.part_name)
        self.full_name = QLineEdit(record.display_name)
        self.full_name.setToolTip("可直接粘贴完整名称；保存时始终保留当前图号。")
        self._full_name_edited = False
        self.full_name.textEdited.connect(lambda _value: setattr(self, "_full_name_edited", True))
        self.part_name.textChanged.connect(self._sync_full_name)
        self.purpose = QTextEdit(record.purpose)
        self.purpose.setFixedHeight(72)
        self.extra_notes = QTextEdit(record.extra_notes)
        self.extra_notes.setFixedHeight(72)
        form.addRow(make_field_label("零件名称"), self.part_name)
        form.addRow(make_field_label("图号"), QLabel(record.filename))
        form.addRow(make_field_label("完整名称"), self.full_name)
        form.addRow(make_field_label("用途备注"), self.purpose)
        form.addRow(make_field_label("额外备注"), self.extra_notes)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(fields)
        layout.addWidget(scroll, 1)
        copy_button = QPushButton("复制文件名")
        copy_button.setProperty("kind", "secondary")
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.full_name.text().strip()))
        layout.addWidget(copy_button)
        if record.status != "void":
            void_button = QPushButton("标记为已作废")
            void_button.setProperty("kind", "danger")
            void_button.clicked.connect(self._mark_void)
            layout.addWidget(void_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存修改")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        style_button_box(buttons, ok_is_primary=True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _check_file(self) -> None:
        try:
            exists = Path(self.record.file_path).is_file()
        except OSError:
            self.file_state.setText("暂不可访问，请检查权限或同步状态")
            return
        self.file_state.setText("文件存在" if exists else "路径找不到，请检查文件是否移动或尚未同步")

    def values(self) -> tuple[str, str, str]:
        value = self.full_name.text().strip()
        prefix = self.record.filename
        if value.startswith(prefix):
            parsed = value[len(prefix):].strip()
            if self.record.part_type == "通用编号" and parsed.startswith("-"):
                parsed = parsed[1:]
            if parsed:
                self.part_name.setText(parsed)
        elif self._full_name_edited and value != self.record.display_name:
            answer = QMessageBox.question(self, "图号不一致", "完整名称中的图号与当前记录不同。是否保留当前图号，仅使用其余文字作为零件名称？")
            if answer != QMessageBox.StandardButton.Yes:
                raise ValueError("已取消保存：完整名称中的图号与当前记录不一致。")
            self.part_name.setText(value)
        return self.part_name.text().strip(), self.purpose.toPlainText(), self.extra_notes.toPlainText()

    def _sync_full_name(self, value: str) -> None:
        if not self._full_name_edited:
            separator = "-" if self.record.part_type == "通用编号" else " "
            self.full_name.setText(f"{self.record.filename}{separator}{value.strip()}".strip())

    def _mark_void(self) -> None:
        answer = QMessageBox.question(
            self, "确认作废", f"确定将 {self.record.filename} 标记为已作废吗？\n该图号以后不会重新使用。"
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.mark_void_requested = True
            self.accept()
