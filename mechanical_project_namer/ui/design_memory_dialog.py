# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""Small, on-demand editor for confirmed personal/project memory."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QVBoxLayout,
)


class DesignMemoryDialog(QDialog):
    def __init__(self, repository, project_id=None, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.project_id = project_id
        self.setWindowTitle("管理设计问答记忆")
        self.resize(740, 440)
        root = QVBoxLayout(self)
        label = QLabel("个人习惯跨项目使用；项目结论仅用于所选项目。AI建议经过确认才保存。")
        label.setWordWrap(True)
        root.addWidget(label)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["类型", "内容", "来源", "更新时间"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        root.addWidget(self.table, 1)
        footer = QHBoxLayout()
        for text, method in (("编辑", self._edit), ("删除", self._delete)):
            button = QPushButton(text)
            button.clicked.connect(method)
            footer.addWidget(button)
        footer.addStretch()
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        root.addLayout(footer)
        self.reload()

    def reload(self):
        self.rows = [("个人习惯", r) for r in self.repository.list_design_preferences(limit=300)]
        if self.project_id is not None:
            self.rows += [("项目结论", r) for r in self.repository.list_project_memories(self.project_id, limit=1000)]
        self.table.setRowCount(len(self.rows))
        for row, (kind, record) in enumerate(self.rows):
            source = {"auto": f"自动学习 · {record.get('evidence_count', 1)}次", "manual": "手动确认", "explicit": "明确保存指令"}.get(record.get('source'), "项目确认")
            for column, value in enumerate((kind, record['content'], source, record['updated_at'])):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(2)
        self.table.resizeColumnToContents(3)

    def _edit(self):
        index = self.table.currentRow()
        if index < 0:
            return
        kind, record = self.rows[index]
        value, ok = QInputDialog.getMultiLineText(self, "编辑" + kind, "内容（最多2000字）：", record['content'])
        if not ok:
            return
        try:
            if kind == "个人习惯":
                self.repository.save_design_preference(value, preference_id=record['preference_id'])
            else:
                self.repository.edit_project_memory(record['memory_id'], self.project_id, value)
        except Exception as error:
            QMessageBox.warning(self, "无法保存", str(error))
            return
        self.reload()

    def _delete(self):
        index = self.table.currentRow()
        if index < 0:
            return
        kind, record = self.rows[index]
        if QMessageBox.question(self, "删除记忆", "删除所选记忆？原聊天和正式设计数据会保留。",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        if kind == "个人习惯":
            self.repository.delete_design_preference(record['preference_id'])
        else:
            self.repository.delete_project_memory(record['memory_id'], self.project_id)
        self.reload()
