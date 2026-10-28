# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from ..numbering.simple import module_code as normalize_module
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QInputDialog, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from ..ai.models import AISuggestionResult


class AISuggestionsDialog(QDialog):
    def __init__(self, result: AISuggestionResult, parent=None, task_type: str = "project") -> None:
        super().__init__(parent)
        self.task_type = task_type
        self.selected_code: str | None = None
        self.selected_name: str = ""
        self.selected_action: str = ""
        self.selected_suggestion = None
        self.selected_modules: list[str] = []
        self.selected_target: str = "new_project"
        self.regenerate_requested = False
        self.setWindowTitle("AI 零件名称建议" if task_type == "part" else "AI 整机项目规划")
        self.resize(850, 680)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        title = QLabel("AI 零件名称建议" if task_type == "part" else "AI 整机项目规划")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        if result.missing_information:
            missing = QLabel("缺失信息：" + "；".join(result.missing_information))
            missing.setObjectName("warningText")
            missing.setWordWrap(True)
            root.addWidget(missing)
        if result.quality_assessment:
            quality = QLabel("命名质量：" + "  ·  ".join(f"{key}：{value}" for key, value in result.quality_assessment.items()))
            quality.setObjectName("mutedText")
            quality.setWordWrap(True)
            root.addWidget(quality)
        if result.module_recommendations:
            module_lines = []
            for item in result.module_recommendations:
                examples = "、".join(item.get("example_names") or []) or "暂无示例"
                module_lines.append(f"{item['module_code']} {item['module_name']}：{examples}")
            self.module_summary_label = QLabel("识别模块与名称示例（序号由数据库生成）：\n" + "\n".join(module_lines))
            self.module_summary_label.setWordWrap(True)
            self.module_summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            root.addWidget(self.module_summary_label)
            if task_type == "project":
                module_box = QWidget()
                module_layout = QVBoxLayout(module_box)
                module_layout.setContentsMargins(0, 0, 0, 0)
                module_layout.addWidget(QLabel("采用并创建以下模块："))
                self.module_checks: list[tuple[str, QCheckBox]] = []
                for item in result.module_recommendations:
                    code = normalize_module(item.get("module_code", ""))
                    check = QCheckBox(f"{code} {item.get('module_name', '')}")
                    check.setChecked(code not in {"06", "90", "99"})
                    self.module_checks.append((code, check))
                    module_layout.addWidget(check)
                root.addWidget(module_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        self.code_inputs: list[QLineEdit] = []
        for index, suggestion in enumerate(result.suggestions, 1):
            panel = QWidget()
            panel.setObjectName("suggestionPanel")
            layout = QVBoxLayout(panel)
            layout.addWidget(QLabel(f"建议 {index}"))
            code = QLineEdit(suggestion.code)
            code.setObjectName("suggestionCode")
            self.code_inputs.append(code)
            layout.addWidget(code)
            segments = "\n".join(
                f"{segment.segment}：{segment.meaning}（依据：{segment.source}）"
                for segment in suggestion.segments
            )
            if not segments:
                segments = suggestion.overall_meaning or "AI没有返回完整的分段解释。"
            warning = f"\n状态：{suggestion.parse_warning}" if suggestion.parse_warning else ""
            details = QLabel(
                f"{segments}{warning}\n\n模块：{suggestion.module_code or '未指定'}  ·  类别：{suggestion.part_type or '未指定'}\n推荐名称：{suggestion.recommended_name or '项目代号建议'}\n推荐理由：{suggestion.reason or '未说明'}\n优点：{suggestion.advantage}\n可能问题：{suggestion.concern}\n缺失信息：{suggestion.missing_information}"
            )
            details.setWordWrap(True)
            details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(details)
            buttons = QHBoxLayout()
            adopt = QPushButton("采用并创建项目" if task_type == "project" else "采用到输入框")
            adopt.setProperty("kind", "primary")
            adopt.clicked.connect(lambda _checked=False, item=code, suggestion=suggestion: self._adopt(item.text(), suggestion.recommended_name, "adopted", suggestion, "new_project"))
            edit = QPushButton("修改后采用")
            edit.setProperty("kind", "secondary")
            edit.clicked.connect(lambda _checked=False, item=code, suggestion=suggestion: self._edit_adopt(item.text(), suggestion.recommended_name, suggestion))
            reject = QPushButton("拒绝")
            reject.setProperty("kind", "danger")
            reject.clicked.connect(lambda _checked=False, item=code, suggestion=suggestion: self._reject(item.text(), suggestion))
            copy = QPushButton("复制项目代号" if task_type == "project" else "复制零件名称")
            copy.setProperty("kind", "secondary")
            copy.clicked.connect(lambda _checked=False, item=code, suggestion=suggestion: QApplication.clipboard().setText((item.text().strip().upper() if task_type == "project" else suggestion.recommended_name)))
            buttons.addWidget(adopt)
            if task_type == "project":
                adopt_current = QPushButton("采用到当前项目")
                adopt_current.setProperty("kind", "secondary")
                adopt_current.clicked.connect(lambda _checked=False, item=code, suggestion=suggestion: self._adopt(item.text(), suggestion.recommended_name, "adopted", suggestion, "current_project"))
                buttons.addWidget(adopt_current)
            buttons.addWidget(edit)
            buttons.addWidget(reject)
            buttons.addWidget(copy)
            buttons.addStretch()
            layout.addLayout(buttons)
            body_layout.addWidget(panel)
        body_layout.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        footer = QHBoxLayout()
        usage = QLabel(
            f"本次用量：输入 {result.prompt_tokens} · 输出 {result.completion_tokens} · 缓存命中 {result.cached_tokens} Token"
        )
        usage.setObjectName("mutedText")
        footer.addWidget(usage)
        footer.addStretch()
        regenerate = QPushButton("重新生成")
        regenerate.setProperty("kind", "secondary")
        regenerate.clicked.connect(self._regenerate)
        close = QPushButton("关闭")
        close.setProperty("kind", "secondary")
        close.clicked.connect(self.reject)
        footer.addWidget(regenerate)
        footer.addWidget(close)
        root.addLayout(footer)

    def _adopt(self, code: str, name: str = "", action: str = "adopted", suggestion=None, target: str = "new_project") -> None:
        self.selected_code = code.strip().upper()
        self.selected_name = name.strip()
        self.selected_action = action
        self.selected_suggestion = suggestion
        self.selected_target = target
        if self.task_type == "project":
            self.selected_modules = [code for code, checkbox in getattr(self, "module_checks", []) if checkbox.isChecked()]
        self.accept()

    def _edit_adopt(self, code: str, name: str, suggestion=None) -> None:
        value, ok = QInputDialog.getText(self, "修改后采用", "最终名称：", text=name)
        if ok and value.strip():
            self._adopt(code, value, "edited", suggestion)

    def _reject(self, code: str, suggestion=None) -> None:
        value, ok = QInputDialog.getText(self, "拒绝 AI 建议", "拒绝原因（可选）：")
        if ok:
            self.selected_code = code.strip().upper()
            self.selected_name = value.strip()
            self.selected_action = "rejected"
            self.selected_suggestion = suggestion
            self.accept()

    def _regenerate(self) -> None:
        self.regenerate_requested = True
        self.reject()
