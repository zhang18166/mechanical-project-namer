# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QInputMethodEvent, QKeyEvent
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

import html as _html


class AIChatInputEdit(QPlainTextEdit):
    """Multiline chat editor with IME-safe submit semantics."""
    submit_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ime_composing = False
        self._ime_commit_guard = False
        self._submission_enabled = True

    def set_submission_enabled(self, enabled: bool) -> None:
        self._submission_enabled = enabled

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        was_composing = self._ime_composing
        super().inputMethodEvent(event)
        self._ime_composing = bool(event.preeditString())
        if not self._ime_composing and (was_composing or bool(event.commitString())):
            self._ime_commit_guard = True
            QTimer.singleShot(250, self._clear_ime_commit_guard)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() not in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            super().keyPressEvent(event)
            return
        modifiers = event.modifiers()
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            self.insertPlainText("\n")
            event.accept()
            return
        if self._ime_composing or self._ime_commit_guard:
            super().keyPressEvent(event)
            return
        if modifiers & Qt.KeyboardModifier.AltModifier:
            super().keyPressEvent(event)
            return
        if self._submission_enabled:
            self.submit_requested.emit()
        event.accept()

    def _clear_ime_commit_guard(self) -> None:
        self._ime_commit_guard = False


class DesignChatDialog(QDialog):
    """Independent engineering Q&A surface; messages are persisted by the shell."""
    send_requested = pyqtSignal(str, dict)
    cancel_requested = pyqtSignal()
    new_conversation_requested = pyqtSignal()
    conversation_selected = pyqtSignal(int)
    rename_requested = pyqtSignal(int)
    delete_requested = pyqtSignal(int)
    save_project_memory_requested = pyqtSignal(str)
    save_naming_rule_requested = pyqtSignal(str)

    def __init__(self, conversations=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI设计问答")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(980, 680)
        root = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget(); left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("会话列表"))
        self.conversation_list = QListWidget()
        self.conversation_list.currentItemChanged.connect(self._conversation_changed)
        self.set_conversations(conversations or [])
        left_layout.addWidget(self.conversation_list, 1)
        actions = QHBoxLayout()
        new_button = QPushButton("新建"); new_button.clicked.connect(self.new_conversation_requested); actions.addWidget(new_button)
        rename_button = QPushButton("重命名"); rename_button.clicked.connect(self._request_rename); actions.addWidget(rename_button)
        delete_button = QPushButton("删除"); delete_button.clicked.connect(self._request_delete); actions.addWidget(delete_button)
        left_layout.addLayout(actions)
        splitter.addWidget(left)
        right = QWidget(); layout = QVBoxLayout(right)
        context = QHBoxLayout()
        self.project_combo = QComboBox(); self.project_combo.setPlaceholderText("当前项目")
        self.module_combo = QComboBox(); self.module_combo.setPlaceholderText("当前模块")
        self.model_combo = QComboBox(); self.model_combo.setObjectName("chatModel")
        self.thinking = QCheckBox("深度思考"); self.reasoning = QComboBox(); self.reasoning.addItems(["低", "高", "极致"]); self.reasoning.setCurrentText("高")
        for widget in (self.project_combo, self.module_combo, self.model_combo, self.thinking, self.reasoning): context.addWidget(widget)
        layout.addLayout(context)
        refs = QHBoxLayout()
        self.use_project = QCheckBox("当前项目资料"); self.use_history = QCheckBox("历史项目"); self.use_naming = QCheckBox("命名记忆"); self.use_memory = QCheckBox("项目记忆")
        for check in (self.use_project, self.use_history, self.use_naming, self.use_memory): check.setChecked(True); refs.addWidget(check)
        layout.addLayout(refs)
        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setObjectName("chatTranscript")
        layout.addWidget(self.transcript, 1)
        self.reference_label = QLabel("本轮上下文：尚未发送"); self.reference_label.setObjectName("mutedText"); self.reference_label.setWordWrap(True); layout.addWidget(self.reference_label)
        self.status_label = QLabel("AI状态：空闲    当前模型：—    已用时间：0.0秒")
        self.status_label.setObjectName("mutedText")
        layout.addWidget(self.status_label)
        self.input = AIChatInputEdit(); self.input.setPlaceholderText("描述设计问题、约束条件或要比较的方案…"); self.input.setMaximumHeight(120); self.input.submit_requested.connect(self._send); layout.addWidget(self.input)
        input_hint = QLabel("Enter发送 · Ctrl+Enter换行")
        input_hint.setObjectName("mutedText")
        layout.addWidget(input_hint)
        footer = QHBoxLayout()
        self.send_button = QPushButton("发送"); self.send_button.setProperty("kind", "primary"); self.send_button.clicked.connect(self._send)
        self.stop_button = QPushButton("停止生成"); self.stop_button.setProperty("kind", "secondary"); self.stop_button.setEnabled(False); self.stop_button.clicked.connect(self.cancel_requested)
        footer.addWidget(self.send_button); footer.addWidget(self.stop_button); footer.addStretch(); layout.addLayout(footer)
        splitter.addWidget(right); splitter.setSizes([220, 740]); root.addWidget(splitter)

    def set_conversations(self, conversations, current_id=None):
        self.conversation_list.blockSignals(True)
        self.conversation_list.clear()
        selected_row = -1
        for index, item in enumerate(conversations or []):
            row = QListWidgetItem(item.get("title", "新对话"))
            row.setData(Qt.ItemDataRole.UserRole, item.get("conversation_id"))
            row.setToolTip(item.get("updated_at", ""))
            self.conversation_list.addItem(row)
            if item.get("conversation_id") == current_id:
                selected_row = index
        if selected_row >= 0:
            self.conversation_list.setCurrentRow(selected_row)
        self.conversation_list.blockSignals(False)

    def selected_conversation_id(self):
        item = self.conversation_list.currentItem()
        return int(item.data(Qt.ItemDataRole.UserRole)) if item and item.data(Qt.ItemDataRole.UserRole) is not None else None

    def _conversation_changed(self, current, _previous):
        if current is not None and current.data(Qt.ItemDataRole.UserRole) is not None:
            self.conversation_selected.emit(int(current.data(Qt.ItemDataRole.UserRole)))

    def _request_rename(self):
        conversation_id = self.selected_conversation_id()
        if conversation_id is not None:
            self.rename_requested.emit(conversation_id)

    def _request_delete(self):
        conversation_id = self.selected_conversation_id()
        if conversation_id is not None:
            self.delete_requested.emit(conversation_id)

    def set_projects(self, projects, current_project_id=None):
        self.project_combo.clear()
        for project in projects or []: self.project_combo.addItem(f"{project.name} · {project.code}", project.id)
        index = self.project_combo.findData(current_project_id)
        if index >= 0: self.project_combo.setCurrentIndex(index)

    def set_modules(self, modules, selected_code=""):
        self.module_combo.clear()
        for item in modules or []: self.module_combo.addItem(f"{item['module_code']} {item['module_name']}", item['module_code'])
        index = self.module_combo.findData(selected_code)
        if index >= 0: self.module_combo.setCurrentIndex(index)

    def set_models(self, model_ids, selected=""):
        self.model_combo.clear(); self.model_combo.addItems(model_ids or [])
        index = self.model_combo.findText(selected)
        if index >= 0: self.model_combo.setCurrentIndex(index)

    def append_message(self, role: str, content: str):
        # HTML message card: user / assistant / system(None). Engineering-tool style.
        who = {"user": "你", "assistant": "AI", "system": "系统"}.get(role, "系统")
        body = _html.escape(str(content or "")).replace("\n", "<br/>")
        if role == "user":
            bg = "#e7f1f4"; color = "#17434f"
        elif role == "assistant":
            bg = "#ffffff"; color = "#2f434d"
        else:
            bg = "#f6f8f9"; color = "#6a7c85"
        self.transcript.append(
            f'<table width="100%" cellspacing="0" cellpadding="0" style="margin:6px 0;">'
            f'<tr><td style="background-color:{bg};border-radius:8px;padding:9px 12px;">'
            f'<span style="color:{color};font-weight:600;">{who}：</span>'
            f'<span style="color:{color};">{body}</span>'
            f'</td></tr></table>'
        )
        vbar = self.transcript.verticalScrollBar()
        vbar.setValue(vbar.maximum())

    def set_messages(self, messages):
        self.transcript.clear()
        for item in messages or []:
            if item.get("role") in {"user", "assistant"}:
                self.append_message(item["role"], item.get("content", ""))

    def set_status(self, status: str, model: str = "", elapsed: float = 0.0):
        self.status_label.setText(f"AI状态：{status}    当前模型：{model or '—'}    已用时间：{elapsed:.1f}秒")

    def set_busy(self, busy: bool):
        self.send_button.setDisabled(busy); self.stop_button.setEnabled(busy); self.input.set_submission_enabled(not busy)

    def _send(self):
        if not self.send_button.isEnabled(): return
        content = self.input.toPlainText().strip()
        if not content: return
        self.send_requested.emit(content, {"project_id": self.project_combo.currentData(), "module_code": self.module_combo.currentData() or "", "model": self.model_combo.currentText(), "thinking_enabled": self.thinking.isChecked(), "reasoning_level": self.reasoning.currentText(), "use_project": self.use_project.isChecked(), "use_history": self.use_history.isChecked(), "use_naming": self.use_naming.isChecked(), "use_memory": self.use_memory.isChecked()})

    def accept_submission(self, content: str) -> None:
        self.input.clear()
        self.append_message("user", content)

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.hide()
