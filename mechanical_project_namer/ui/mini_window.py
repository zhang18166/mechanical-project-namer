# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QMenu,
    QTextEdit, QVBoxLayout, QWidget,
)

from ..projects.models import DrawingRecord, Project
from ..numbering import MODULES, PART_TYPES
from .. import __version__
from .common import STATUS_NAMES


class MiniWindow(QWidget):
    copy_requested = pyqtSignal()
    save_requested = pyqtSignal(str, str, str)
    solidworks_requested = pyqtSignal(str, str)
    solidworks_directory_requested = pyqtSignal()
    full_requested = pyqtSignal()
    hide_requested = pyqtSignal()
    module_selected = pyqtSignal(str)
    part_type_selected = pyqtSignal(str)
    recent_action_requested = pyqtSignal(str, int)

    def __init__(self) -> None:
        super().__init__()
        self.allow_close = False
        self.setWindowTitle(f"机械项目命名助手 V{__version__} · Mini")
        self.setMinimumWidth(390)
        self.setMaximumWidth(520)
        self.setObjectName("miniWindow")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        header = QHBoxLayout()
        label = QLabel("快速图号")
        label.setObjectName("miniTitle")
        header.addWidget(label)
        header.addStretch()
        full = QPushButton("切换完整窗口")
        full.setProperty("kind", "ghost")
        full.clicked.connect(self.full_requested)
        header.addWidget(full)
        layout.addLayout(header)
        self.project_name = QLabel("尚未选择项目")
        self.project_name.setObjectName("miniProject")
        self.project_code = QLabel("—")
        self.project_code.setObjectName("mutedText")
        layout.addWidget(self.project_name)
        layout.addWidget(self.project_code)
        module_row = QHBoxLayout()
        module_label = QLabel("模块")
        module_label.setProperty("fieldLabel", "true")
        module_row.addWidget(module_label)
        self.module_combo = QComboBox()
        self.module_combo.currentIndexChanged.connect(self._module_changed)
        module_row.addWidget(self.module_combo, 1)
        layout.addLayout(module_row)
        type_row = QHBoxLayout()
        type_label = QLabel("类别")
        type_label.setProperty("fieldLabel", "true")
        type_row.addWidget(type_label)
        self.part_type_combo = QComboBox()
        for name, label, _start, _end in PART_TYPES:
            self.part_type_combo.addItem(f"{name} {label}", name)
        self.part_type_combo.currentIndexChanged.connect(self._type_changed)
        type_row.addWidget(self.part_type_combo, 1)
        layout.addLayout(type_row)
        self.ai_status = QLabel("AI：空闲")
        self.ai_status.setObjectName("mutedText")
        layout.addWidget(self.ai_status)
        preview_frame = QFrame()
        preview_frame.setObjectName("previewBand")
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.addWidget(QLabel("当前文件名"))
        self.preview = QLabel("请先创建或选择项目")
        self.preview.setObjectName("miniPreview")
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        preview_layout.addWidget(self.preview)
        layout.addWidget(preview_frame)
        self.part_name = QLineEdit()
        self.part_name.setPlaceholderText("零件名称")
        self.part_name.returnPressed.connect(self._save)
        self.purpose = QTextEdit()
        self.purpose.setPlaceholderText("用途备注")
        self.purpose.setVisible(False)
        self.purpose.setFixedHeight(66)
        self.purpose.installEventFilter(self)
        self.extra_notes = QLineEdit()
        self.extra_notes.setPlaceholderText("额外备注（可选）")
        self.extra_notes.returnPressed.connect(self._save)
        layout.addWidget(self.part_name)
        layout.addWidget(self.purpose)
        layout.addWidget(self.extra_notes)
        buttons = QHBoxLayout()
        self.copy_button = QPushButton("仅复制")
        self.copy_button.setProperty("kind", "ghost")
        self.copy_button.clicked.connect(self.copy_requested)
        self.save_button = QPushButton("保存并复制")
        self.save_button.setProperty("kind", "secondary")
        self.save_button.clicked.connect(self._save)
        self.solidworks_button = QPushButton("创建SW零件")
        self.solidworks_button.setProperty("kind", "primary")
        self.solidworks_button.setToolTip("使用当前名称创建并打开SOLIDWORKS空白零件")
        self.solidworks_button.clicked.connect(lambda: self.solidworks_requested.emit(self.part_name.text(), self.extra_notes.text()))
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.save_button, 1)
        buttons.addWidget(self.solidworks_button, 1)
        layout.addLayout(buttons)
        status_row = QHBoxLayout()
        self.solidworks_status = QLabel("SW：未检测"); status_row.addWidget(self.solidworks_status, 1)
        self.solidworks_directory_button = QPushButton("设置")
        self.solidworks_directory_button.setProperty("kind", "ghost")
        self.solidworks_directory_button.clicked.connect(self.solidworks_directory_requested)
        status_row.addWidget(self.solidworks_directory_button); layout.addLayout(status_row)
        self.recent_title = QLabel("最近记录（双击复制完整名称）")
        self.recent_title.setObjectName("mutedText")
        layout.addWidget(self.recent_title)
        self.recent = QListWidget()
        self.recent.setMinimumHeight(90)
        self.recent.setToolTip("双击整行复制完整名称；右键可复制不同字段")
        self.recent.itemDoubleClicked.connect(self._recent_double_click)
        self.recent.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.recent.customContextMenuRequested.connect(self._recent_context_menu)
        layout.addWidget(self.recent, 1)
        self.shortcuts = [
            QShortcut(QKeySequence("Ctrl+C"), self, activated=self.copy_requested),
            QShortcut(QKeySequence("Escape"), self, activated=self.hide_requested),
            QShortcut(QKeySequence("Return"), self, activated=self._save),
        ]

    def set_project(self, project: Project | None, records: list[DrawingRecord], modules: list[dict] | None = None,
                    selected_module: str = "", selected_part_type: str = "自制机加工件", preview: str = "") -> None:
        enabled_codes = {item["module_code"] for item in (modules or []) if item.get("enabled", 1)}
        enabled = project is not None and bool(selected_module) and selected_module in enabled_codes
        self.copy_button.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.solidworks_button.setEnabled(enabled)
        if records[:5] != getattr(self, "_recent_records", None):
            self._recent_records = list(records[:5])
            self.recent.clear()
            for record in records[:5]:
                status = STATUS_NAMES.get(record.status, record.status)
                item = QListWidgetItem(f"{record.filename:<16} {record.part_name or '未填写零件名称'}  ·  {status}")
                item.setData(Qt.ItemDataRole.UserRole, record.id)
                self.recent.addItem(item)
        self.module_combo.blockSignals(True)
        self.module_combo.clear()
        for module in modules or []:
            self.module_combo.addItem(f"{module['module_code']} {module['module_name']}", module['module_code'])
        self.module_combo.blockSignals(False)
        if project is None:
            self.project_name.setText("尚未选择项目")
            self.project_code.setText("—")
            self.preview.setText("请先创建或选择项目")
            return
        self.project_name.setText(project.name)
        self.project_code.setText(f"项目代号  {project.code}")
        configured = bool(project.solidworks_part_directory)
        self.solidworks_status.setText("SW：已设置" if configured else "SW：未设置")
        self.solidworks_directory_button.setText("修改" if configured else "设置")
        self.preview.setText(preview or project.next_filename)
        self.module_combo.blockSignals(True)
        index = self.module_combo.findData(selected_module)
        if index >= 0:
            self.module_combo.setCurrentIndex(index)
        self.module_combo.blockSignals(False)
        self.part_type_combo.blockSignals(True)
        index = self.part_type_combo.findData(selected_part_type)
        if index >= 0:
            self.part_type_combo.setCurrentIndex(index)
        self.part_type_combo.blockSignals(False)

    def clear_inputs(self) -> None:
        self.part_name.clear()
        self.purpose.clear()
        self.extra_notes.clear()
        self.part_name.setFocus()

    def set_ai_status(self, text: str) -> None:
        self.ai_status.setText(f"AI：{text}")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.allow_close:
            event.accept()
        else:
            event.ignore()
            self.hide_requested.emit()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if watched is self.purpose and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers():
                self._save()
                return True
        return super().eventFilter(watched, event)

    def _save(self) -> None:
        if self.save_button.isEnabled():
            self.save_requested.emit(
                self.part_name.text(), self.extra_notes.text(), ""
            )

    def set_solidworks_busy(self, busy: bool) -> None:
        self.solidworks_button.setDisabled(busy)
        if busy:
            self.solidworks_status.setText("SW：正在创建")

    def set_solidworks_status(self, text: str) -> None:
        self.solidworks_status.setText(f"SW：{text}")

    def _module_changed(self, _index: int) -> None:
        self.module_selected.emit(str(self.module_combo.currentData() or ""))

    def _type_changed(self, _index: int) -> None:
        self.part_type_selected.emit(str(self.part_type_combo.currentData() or ""))

    def _recent_double_click(self, item: QListWidgetItem) -> None:
        record_id = item.data(Qt.ItemDataRole.UserRole)
        if record_id:
            self.recent_action_requested.emit("copy_full", int(record_id))

    def _recent_context_menu(self, position) -> None:
        item = self.recent.itemAt(position)
        if item is None:
            return
        record_id = int(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self.recent)
        for label, action_name in (("复制完整名称", "copy_full"), ("复制编号", "copy_number"),
                                   ("复制零件名称", "copy_part")):
            action = menu.addAction(label)
            action.triggered.connect(lambda _checked=False, name=action_name, rid=record_id: self.recent_action_requested.emit(name, rid))
        menu.addSeparator()
        action = menu.addAction("查看详情")
        action.triggered.connect(lambda _checked=False, rid=record_id: self.recent_action_requested.emit("edit", rid))
        menu.addSeparator()
        action = menu.addAction("打开完整窗口")
        action.triggered.connect(lambda: self.recent_action_requested.emit("full", record_id))
        menu.exec(self.recent.viewport().mapToGlobal(position))
