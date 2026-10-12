# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QPlainTextEdit,
    QSplitter, QVBoxLayout, QWidget,
)

from .common import status_badge


class AIPlanHistoryDialog(QDialog):
    adopt_new_project = pyqtSignal(int, dict)
    adopt_current_project = pyqtSignal(int, dict)
    adopt_modified = pyqtSignal(int, dict)
    regenerate_requested = pyqtSignal(int)
    delete_failed_requested = pyqtSignal(int)

    def __init__(self, repository, project_id: int | None = None, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.project_id = project_id
        self.plans: list[dict] = []
        self.current_plan: dict | None = None
        self.setWindowTitle("AI规划历史工作区")
        self.setMinimumSize(1000, 650)
        self.resize(1180, 760)
        root = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("搜索描述、项目代号或模型")
        self.status = QComboBox(); self.status.addItem("全部状态", "all"); self.status.addItem("成功", "success"); self.status.addItem("失败", "failed"); self.status.addItem("部分成功", "partial")
        self.adopted = QComboBox(); self.adopted.addItem("全部采用状态", "all"); self.adopted.addItem("已采用", "yes"); self.adopted.addItem("未采用", "no")
        filters.addWidget(self.search, 1); filters.addWidget(self.status); filters.addWidget(self.adopted)
        root.addLayout(filters)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget(); self.list.currentItemChanged.connect(self._select_item)
        self.list.setObjectName("planListCard")
        splitter.addWidget(self.list)
        detail = QWidget(); detail_layout = QVBoxLayout(detail)
        detail_card = QFrame()
        detail_card.setObjectName("planDetailCard")
        detail_card.setMinimumWidth(520)
        card_layout = QVBoxLayout(detail_card)
        card_layout.setContentsMargins(14, 13, 14, 14)
        self.detail_title = QLabel("AI 规划详情"); self.detail_title.setObjectName("sectionTitle")
        card_layout.addWidget(self.detail_title)
        self.status_badge_label = QLabel(""); self.status_badge_label.setObjectName("mutedText")
        card_layout.addWidget(self.status_badge_label)
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True); self.detail.setMinimumWidth(520); card_layout.addWidget(self.detail, 1)
        detail_layout.addWidget(detail_card)
        self.code = QLineEdit(); self.name = QLineEdit()
        form = QFormLayout(); form.setVerticalSpacing(10)
        field_label1 = QLabel("采用项目代号"); field_label1.setProperty("fieldLabel", "true")
        field_label2 = QLabel("采用项目名称"); field_label2.setProperty("fieldLabel", "true")
        form.addRow(field_label1, self.code); form.addRow(field_label2, self.name); detail_layout.addLayout(form)
        self.modules_box = QVBoxLayout(); detail_layout.addLayout(self.modules_box)
        actions = QHBoxLayout()
        self.new_button = QPushButton("采用并创建新项目"); self.new_button.setProperty("kind", "primary")
        self.current_button = QPushButton("采用到当前项目"); self.current_button.setProperty("kind", "secondary")
        self.modified_button = QPushButton("修改后采用"); self.modified_button.setProperty("kind", "secondary")
        self.copy_button = QPushButton("复制建议"); self.copy_button.setProperty("kind", "ghost")
        self.regenerate_button = QPushButton("使用原提示词重新生成"); self.regenerate_button.setProperty("kind", "secondary")
        self.delete_button = QPushButton("删除失败记录"); self.delete_button.setProperty("kind", "danger")
        close = QPushButton("关闭"); close.setProperty("kind", "secondary")
        for button in (self.new_button, self.current_button, self.modified_button, self.copy_button, self.regenerate_button, self.delete_button, close): actions.addWidget(button)
        actions.addStretch()
        detail_layout.addLayout(actions); splitter.addWidget(detail); splitter.setSizes([360, 760]); root.addWidget(splitter, 1)
        self.search.textChanged.connect(self.load)
        self.status.currentIndexChanged.connect(self.load); self.adopted.currentIndexChanged.connect(self.load)
        self.new_button.clicked.connect(lambda: self._emit(self.adopt_new_project))
        self.current_button.clicked.connect(lambda: self._emit(self.adopt_current_project))
        self.modified_button.clicked.connect(lambda: self._emit(self.adopt_modified))
        self.copy_button.clicked.connect(self._copy)
        self.regenerate_button.clicked.connect(lambda: self.regenerate_requested.emit(self.current_plan["plan_id"]) if self.current_plan else None)
        self.delete_button.clicked.connect(self._delete_failed)
        close.clicked.connect(self.reject)
        self.load()

    def load(self) -> None:
        adopted_value = self.adopted.currentData()
        adopted = None if adopted_value == "all" else adopted_value == "yes"
        self.plans = self.repository.search_ai_plans(query=self.search.text(), project_id=self.project_id,
                                                     status=self.status.currentData(), adopted=adopted)
        self.list.blockSignals(True); self.list.clear()
        for plan in self.plans:
            summary = " ".join(str(plan.get("prompt", "")).split())[:42] or "（无提示词）"
            state = "失败" if plan.get("status") == "failed" else "已采用" if plan.get("adopted_at") else "未采用"
            item = QListWidgetItem(f"{plan.get('created_at','')} · {summary}\n{plan.get('requested_model') or '未知模型'} · {state}")
            item.setData(Qt.ItemDataRole.UserRole, plan["plan_id"]); self.list.addItem(item)
        self.list.blockSignals(False)
        if self.plans: self.list.setCurrentRow(0)
        else: self._show_plan(None)

    def _select_item(self, current, _previous) -> None:
        plan_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self._show_plan(next((p for p in self.plans if p["plan_id"] == plan_id), None))

    def _show_plan(self, plan: dict | None) -> None:
        self.current_plan = plan
        while self.modules_box.count():
            item = self.modules_box.takeAt(0)
            if item and item.widget(): item.widget().deleteLater()
        if not plan:
            self.detail.setPlainText("暂无 AI规划记录。"); self.code.clear(); self.name.clear()
            self.status_badge_label.setText("")
            self._set_actions(False); return
        decoded = self.repository.decode_ai_plan(plan); plan["_decoded"] = decoded
        status_code = plan.get("status", "")
        status_text = {"success": "成功", "failed": "失败", "partial": "部分成功"}.get(status_code, status_code)
        adopted = "已采用" if plan.get("adopted_at") else "未采用"
        self.status_badge_label.setText(f"状态：{status_text}  ·  {adopted}")
        self.detail_title.setText("AI 规划详情")
        lines = [f"原始用户描述：{plan.get('prompt','')}", f"规划类型：{plan.get('plan_type','')}", f"状态：{plan.get('status','')}", f"模型：{plan.get('requested_model') or '未知'}", f"思考模式：{plan.get('reasoning_level','')}", f"Token：{plan.get('input_tokens',0)} / {plan.get('output_tokens',0)}", f"耗时：{plan.get('total_duration',0)} 秒", f"缺失信息：{json.dumps(decoded['missing_information'], ensure_ascii=False)}", f"命名质量：{json.dumps(decoded['quality_assessment'], ensure_ascii=False)}"]
        self.detail.setPlainText("\n".join(lines))
        suggestions = decoded["suggestions"]
        first = suggestions[0] if suggestions else {}
        self.code.setText(str(first.get("code", ""))); self.name.setText(str(first.get("project_name", first.get("name", ""))))
        for module in decoded["modules"]:
            row = QWidget(); row_layout = QHBoxLayout(row); row_layout.setContentsMargins(0, 0, 0, 0)
            check = QCheckBox(str(module.get("module_code", "")).zfill(2)); check.setChecked(True); check.setProperty("module", module)
            name_edit = QLineEdit(str(module.get("module_name", ""))); name_edit.setProperty("module_code", str(module.get("module_code", "")).zfill(2)); check.setProperty("name_edit", name_edit)
            row_layout.addWidget(check); row_layout.addWidget(name_edit, 1); self.modules_box.addWidget(row)
        adoptable = bool(decoded["adoptable"] and plan.get("status") != "failed")
        self._set_actions(adoptable)
        self.delete_button.setEnabled(plan.get("status") == "failed")

    def _set_actions(self, enabled: bool) -> None:
        for button in (self.new_button, self.current_button, self.modified_button, self.copy_button): button.setEnabled(enabled)
        self.regenerate_button.setEnabled(bool(self.current_plan))

    def current_payload(self) -> dict:
        modules = []
        for index in range(self.modules_box.count()):
            row = self.modules_box.itemAt(index).widget()
            if not row: continue
            check = row.layout().itemAt(0).widget(); name_edit = row.layout().itemAt(1).widget()
            if check and check.isChecked():
                module = dict(check.property("module") or {}); module["module_name"] = name_edit.text().strip(); module["final_name"] = module["module_name"]; modules.append(module)
        return {"code": self.code.text().strip(), "name": self.name.text().strip(), "modules": modules}

    def _emit(self, signal) -> None:
        if self.current_plan: signal.emit(self.current_plan["plan_id"], self.current_payload())

    def _copy(self) -> None:
        if self.current_plan:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(self.detail.toPlainText())

    def _delete_failed(self) -> None:
        if not self.current_plan: return
        answer = QMessageBox.question(self, "删除失败记录", "仅删除失败的 AI规划记录，不影响正式项目。")
        if answer == QMessageBox.StandardButton.Yes:
            self.repository.delete_ai_plan(self.current_plan["plan_id"]); self.delete_failed_requested.emit(self.current_plan["plan_id"]); self.load()
