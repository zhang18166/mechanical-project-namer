# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QFileDialog, QPushButton, QScrollArea, QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from ..paths import database_path, exports_dir, logs_dir
from ..settings import AppSettings
from .common import FlowLayout


class SettingsPage(QWidget):
    save_requested = pyqtSignal(dict)
    action_requested = pyqtSignal(str, dict)
    memory_action_requested = pyqtSignal(str)
    ai_cancel_requested = pyqtSignal()

    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.settings = settings
        layout = QVBoxLayout(self)
        title = QLabel("设置")
        title.setObjectName("pageTitle")
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch()
        self.save_button = QPushButton("保存设置")
        self.save_button.setProperty("kind", "primary")
        self.save_button.setToolTip("保存 AI、SOLIDWORKS 和窗口设置；API Key 使用单独的保存按钮。")
        self.save_button.clicked.connect(lambda: self.save_requested.emit(self.values()))
        header.addWidget(self.save_button)
        layout.addLayout(header)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._scrollable(self._build_ai_tab(settings), "aiSettingsScroll"), "DeepSeek AI 设置")
        self.tabs.addTab(self._scrollable(self._build_memory_tab(), "memorySettingsScroll"), "AI 命名记忆")
        self.tabs.addTab(self._scrollable(self._build_solidworks_tab(settings), "solidworksSettingsScroll"), "SOLIDWORKS集成")
        self.tabs.addTab(self._scrollable(self._build_general_tab(settings), "generalSettingsScroll"), "窗口与本地数据")
        layout.addWidget(self.tabs, 1)

    @staticmethod
    def _scrollable(content: QWidget, object_name: str) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName(object_name)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _configure_form(form: QFormLayout) -> None:
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setVerticalSpacing(10)

    @staticmethod
    def _folded_section(layout, title: str):
        toggle = QPushButton(f"展开{title}")
        toggle.setProperty("kind", "ghost")
        toggle.setCheckable(True)
        content = QWidget()
        content.setVisible(False)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        def toggled(expanded):
            content.setVisible(expanded)
            toggle.setText(f"{'收起' if expanded else '展开'}{title}")
        toggle.toggled.connect(toggled)
        layout.addWidget(toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(content)
        return toggle, content, content_layout

    def _build_ai_tab(self, settings: AppSettings) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        connection = QGroupBox("DeepSeek AI 设置")
        form = QFormLayout(connection)
        self._configure_form(form)
        self.api_url = QLineEdit(settings.api_url)
        self.api_url.setPlaceholderText("https://api.deepseek.com")
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("只写入 Windows 凭据管理器")
        self.key_status = QLabel("未读取 Key 状态")
        key_buttons = QHBoxLayout()
        save_key = QPushButton("保存 Key")
        save_key.clicked.connect(lambda: self.action_requested.emit("save_key", self.values()))
        delete_key = QPushButton("删除 Key")
        delete_key.setProperty("kind", "danger")
        delete_key.clicked.connect(lambda: self.action_requested.emit("delete_key", self.values()))
        key_buttons.addWidget(save_key)
        key_buttons.addWidget(delete_key)
        key_buttons.addStretch()
        form.addRow("API 地址", self.api_url)
        form.addRow("API Key", self.api_key)
        form.addRow("Key 状态", self.key_status)
        form.addRow("", key_buttons)
        layout.addWidget(connection)
        model_group = QGroupBox("模型与思考模式")
        model_form = QFormLayout(model_group)
        self._configure_form(model_form)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(False)
        self.model_combo.currentIndexChanged.connect(self._clear_manual_model_override)
        self.manual_model = QLineEdit()
        self.manual_model.setPlaceholderText("需要兼容其他模型时填写；优先于模型列表")
        self.manual_model.textChanged.connect(self._update_model_hint)
        self.model_source = QLabel("模型列表：未读取")
        self.model_source.setWordWrap(True)
        model_buttons = QHBoxLayout()
        self.ai_action_buttons: list[QPushButton] = []
        for label, action, kind in (("读取 / 刷新模型", "discover_models", "secondary"), ("测试所选模型", "test_model", "secondary")):
            button = QPushButton(label)
            button.setProperty("kind", kind)
            button.setProperty("idleText", label)
            button.clicked.connect(lambda _checked=False, action=action: self.action_requested.emit(action, self.values()))
            self.ai_action_buttons.append(button)
            model_buttons.addWidget(button)
        model_buttons.addStretch()
        self.thinking = QComboBox()
        self.thinking.addItem("开启", "enabled")
        self.thinking.addItem("关闭", "disabled")
        self.thinking.setCurrentIndex(0 if settings.thinking_mode == "enabled" else 1)
        self.reasoning = QComboBox()
        for label, value in (("低", "low"), ("高", "high"), ("极致", "max")):
            self.reasoning.addItem(label, value)
        self.reasoning.setCurrentIndex(max(0, self.reasoning.findData(settings.reasoning_level)))
        self.json_auto_repair = QCheckBox("JSON失败后自动修复一次")
        self.json_auto_repair.setChecked(settings.json_auto_repair)
        self.json_auto_repair.setToolTip("解析失败时额外请求一次修复，会产生少量 Token 费用。")
        self.thinking.currentIndexChanged.connect(self._update_reasoning_state)
        self._update_reasoning_state()
        model_form.addRow("可用模型", self.model_combo)
        model_form.addRow("模型状态", self.model_source)
        model_form.addRow("", model_buttons)
        model_form.addRow("思考模式", self.thinking)
        model_form.addRow("推理级别", self.reasoning)
        layout.addWidget(model_group)
        self.ai_advanced_toggle, self.ai_advanced_content, advanced_layout = self._folded_section(layout, "高级设置与诊断")
        compatibility = QGroupBox("模型兼容")
        compatibility_form = QFormLayout(compatibility)
        self._configure_form(compatibility_form)
        compatibility_form.addRow("手动模型 ID", self.manual_model)
        compatibility_form.addRow("JSON容错", self.json_auto_repair)
        advanced_layout.addWidget(compatibility)
        diagnostics = QGroupBox("连接诊断")
        diag_layout = QVBoxLayout(diagnostics)
        self.diagnostic_label = QLabel("尚未测试")
        self.diagnostic_label.setWordWrap(True)
        diag_buttons_widget = QWidget()
        diag_buttons = FlowLayout(diag_buttons_widget, spacing=8)
        for label, action in (("检查 Key 与网络", "check_connection"), ("读取账户信息", "balance"), ("完整测试", "full_test")):
            button = QPushButton(label)
            button.setProperty("kind", "secondary")
            button.setProperty("idleText", label)
            button.clicked.connect(lambda _checked=False, action=action: self.action_requested.emit(action, self.values()))
            self.ai_action_buttons.append(button)
            diag_buttons.addWidget(button)
        self.ai_cancel = QPushButton("取消当前 AI 任务")
        self.ai_cancel.setProperty("kind", "secondary")
        self.ai_cancel.setVisible(False)
        self.ai_cancel.clicked.connect(self.ai_cancel_requested)
        diag_buttons.addWidget(self.ai_cancel)
        diag_layout.addWidget(self.diagnostic_label)
        diag_layout.addWidget(diag_buttons_widget)
        advanced_layout.addWidget(diagnostics)
        time_group = QGroupBox("超时")
        time_form = QFormLayout(time_group)
        self._configure_form(time_form)
        self.connect_timeout = QSpinBox(); self.connect_timeout.setRange(1, 120); self.connect_timeout.setValue(settings.connect_timeout); self.connect_timeout.setSuffix(" 秒")
        self.request_timeout = QSpinBox(); self.request_timeout.setRange(1, 300); self.request_timeout.setValue(settings.request_timeout); self.request_timeout.setSuffix(" 秒")
        self.generation_timeout = QSpinBox(); self.generation_timeout.setRange(10, 900); self.generation_timeout.setValue(settings.generation_timeout); self.generation_timeout.setSuffix(" 秒")
        for control in (
            self.api_url, self.api_key, self.model_combo, self.manual_model,
            self.thinking, self.reasoning, self.connect_timeout,
            self.request_timeout, self.generation_timeout,
        ):
            control.setMinimumHeight(34)
        time_form.addRow("连接超时", self.connect_timeout)
        time_form.addRow("普通 API 请求", self.request_timeout)
        time_form.addRow("AI 生成总超时", self.generation_timeout)
        advanced_layout.addWidget(time_group)
        layout.addStretch()
        self.set_model_values(settings.available_models or [], settings.model_list_source, settings.model_list_updated_at, settings.model_name)
        return page

    def _build_memory_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        intro = QLabel("本地命名记忆只保存在 SQLite。固定规则优先级最高，AI 未确认内容不会自动成为高可信记忆。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.reference_archived_projects = QCheckBox("AI 可以参考已归档项目")
        self.reference_archived_projects.setChecked(True)
        self.reference_archived_projects.toggled.connect(lambda: self.memory_action_requested.emit("toggle_archived_reference"))
        layout.addWidget(self.reference_archived_projects)
        self.memory_summary = QTextEdit()
        self.memory_summary.setReadOnly(True)
        self.memory_summary.setPlaceholderText("加载基础规则、历史项目、名称偏好和纠偏记录")
        layout.addWidget(self.memory_summary, 1)
        actions = (
            (("查看命名技能", "view_skill", "secondary"), ("重新生成 SKILL 文件", "regenerate_skill", "secondary"),
             ("查看历史项目记忆", "view_project_memory", "secondary"), ("查看纠偏记录", "view_feedback_memory", "secondary"),
             ("查看 AI 原始响应", "view_raw_response", "secondary")),
            (("重新统计命名习惯", "refresh_memory", "secondary"), ("导出 AI 命名记忆包", "export_memory", "primary"),
             ("导入 AI 命名记忆包", "import_memory", "secondary"), ("编辑名称规则", "edit_rule", "secondary"),
             ("项目参考开关", "toggle_project_reference", "secondary")),
            (("删除纠偏记录", "delete_feedback", "danger"), ("恢复默认规则", "reset_rules", "secondary"),
             ("清空 AI 学习记录", "clear_learning", "danger")),
        )
        for row in actions:
            row_widget = QWidget()
            buttons = FlowLayout(row_widget, spacing=8)
            for label, action, kind in row:
                button = QPushButton(label)
                button.setProperty("memoryAction", True)
                button.setProperty("kind", kind)
                button.clicked.connect(lambda _checked=False, action=action: self.memory_action_requested.emit(action))
                buttons.addWidget(button)
            layout.addWidget(row_widget)
        return page

    def _build_general_tab(self, settings: AppSettings) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        general = QGroupBox("窗口与快捷键")
        form = QFormLayout(general)
        self._configure_form(form)
        self.hotkey = QLineEdit(settings.hotkey)
        self.hotkey.setPlaceholderText("Ctrl+Alt+N")
        self.always_on_top = QCheckBox("Mini 窗格始终置顶")
        self.always_on_top.setChecked(settings.always_on_top)
        form.addRow("全局快捷键", self.hotkey)
        form.addRow("窗口置顶", self.always_on_top)
        layout.addWidget(general)
        paths = QGroupBox("本地数据位置")
        paths_form = QFormLayout(paths)
        self._configure_form(paths_form)
        for label, value in (("SQLite 数据库", database_path()), ("开放数据", exports_dir()), ("日志", logs_dir())):
            item = QLineEdit(str(value)); item.setReadOnly(True); paths_form.addRow(label, item)
        layout.addWidget(paths)
        layout.addStretch()
        return page

    def _build_solidworks_tab(self, settings: AppSettings) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        integration = QGroupBox("SOLIDWORKS集成")
        form = QFormLayout(integration)
        self._configure_form(form)
        self.sw_enabled = QCheckBox("启用SOLIDWORKS集成")
        self.sw_enabled.setChecked(settings.solidworks_enabled)
        self.sw_support = QLabel("SOLIDWORKS 2025-2026（后期绑定 COM）")
        self.sw_status = QLabel("未检测")
        self.sw_status.setWordWrap(True)
        self.sw_default_template = QLineEdit()
        self.sw_default_template.setReadOnly(True)
        self.sw_default_template.setPlaceholderText("连接后自动读取")
        self.sw_fallback_template = QLineEdit(settings.solidworks_fallback_template)
        browse = QPushButton("选择模板")
        browse.setProperty("kind", "secondary")
        browse.clicked.connect(self._browse_sw_template)
        template_row = QHBoxLayout()
        template_row.addWidget(self.sw_fallback_template)
        template_row.addWidget(browse)
        self.sw_confirm = QCheckBox("创建前显示确认")
        self.sw_confirm.setChecked(settings.solidworks_confirm_before_create)
        self.sw_copy = QCheckBox("创建成功后复制完整名称")
        self.sw_copy.setChecked(settings.solidworks_copy_after_success)
        self.sw_switch = QCheckBox("创建成功后切换到SOLIDWORKS")
        self.sw_switch.setChecked(settings.solidworks_switch_after_success)
        self.sw_advance = QCheckBox("创建成功后自动进入下一编号")
        self.sw_advance.setChecked(settings.solidworks_advance_after_success)
        self.sw_timeout = QSpinBox()
        self.sw_timeout.setRange(30, 300)
        self.sw_timeout.setValue(settings.solidworks_overall_timeout)
        self.sw_timeout.setSuffix(" 秒")
        form.addRow("启用", self.sw_enabled)
        form.addRow("支持范围", self.sw_support)
        form.addRow("当前连接", self.sw_status)
        form.addRow("默认零件模板", self.sw_default_template)
        form.addRow("备用零件模板", template_row)
        form.addRow("确认", self.sw_confirm)
        form.addRow("复制", self.sw_copy)
        form.addRow("窗口切换", self.sw_switch)
        form.addRow("编号推进", self.sw_advance)
        form.addRow("整体超时", self.sw_timeout)
        layout.addWidget(integration)
        self.sw_advanced_toggle, self.sw_advanced_content, advanced_layout = self._folded_section(layout, "SOLIDWORKS诊断工具")
        action_widget = QWidget()
        actions = FlowLayout(action_widget, spacing=8)
        self.sw_action_buttons: list[QPushButton] = []
        for label, action in (
            ("测试SOLIDWORKS连接", "sw_test_connection"),
            ("重新读取默认模板", "sw_reload_template"),
            ("测试创建空白零件", "sw_test_create"),
            ("检查编号占用", "sw_reconcile_reservations"),
            ("打开SOLIDWORKS日志", "sw_open_logs"),
        ):
            button = QPushButton(label)
            button.setProperty("kind", "secondary")
            button.clicked.connect(lambda _checked=False, action=action: self.action_requested.emit(action, self.values()))
            self.sw_action_buttons.append(button)
            actions.addWidget(button)
        advanced_layout.addWidget(action_widget)
        layout.addStretch()
        return page

    def values(self) -> dict:
        selected = self.manual_model.text().strip() or self.model_combo.currentData()
        return {
            "hotkey": self.hotkey.text().strip(), "always_on_top": self.always_on_top.isChecked(),
            "api_url": self.api_url.text().strip(), "model_name": selected,
            "api_key": self.api_key.text().strip(), "thinking_mode": self.thinking.currentData(),
            "reasoning_level": self.reasoning.currentData(), "connect_timeout": self.connect_timeout.value(),
            "request_timeout": self.request_timeout.value(), "generation_timeout": self.generation_timeout.value(),
            "json_auto_repair": self.json_auto_repair.isChecked(),
            "solidworks_enabled": self.sw_enabled.isChecked(),
            "solidworks_fallback_template": self.sw_fallback_template.text().strip(),
            "solidworks_confirm_before_create": self.sw_confirm.isChecked(),
            "solidworks_copy_after_success": self.sw_copy.isChecked(),
            "solidworks_switch_after_success": self.sw_switch.isChecked(),
            "solidworks_advance_after_success": self.sw_advance.isChecked(),
            "solidworks_overall_timeout": self.sw_timeout.value(),
        }

    def _browse_sw_template(self) -> None:
        target, _ = QFileDialog.getOpenFileName(self, "选择SOLIDWORKS零件模板", self.sw_fallback_template.text(), "SOLIDWORKS零件模板 (*.prtdot)")
        if target:
            self.sw_fallback_template.setText(target)

    def set_solidworks_status(self, text: str, template_path: str = "") -> None:
        self.sw_status.setText(text)
        if template_path:
            self.sw_default_template.setText(template_path)
            self.sw_status.setToolTip(text)

    def set_solidworks_busy(self, busy: bool) -> None:
        for button in self.sw_action_buttons:
            button.setDisabled(busy)

    def _clear_manual_model_override(self, _index: int) -> None:
        """A deliberate dropdown selection takes precedence over a stale manual ID."""
        if self.manual_model.text().strip() and self.model_combo.currentData():
            self.manual_model.clear()

    def _update_reasoning_state(self, _index: int = -1) -> None:
        self.reasoning.setEnabled(self.thinking.currentData() == "enabled")

    def clear_api_key(self) -> None:
        self.api_key.clear()

    def set_key_status(self, text: str) -> None:
        self.key_status.setText(text)

    def set_diagnostic(self, text: str) -> None:
        self.diagnostic_label.setText(text)

    def set_ai_busy(self, busy: bool, title: str = "") -> None:
        for button in self.ai_action_buttons:
            button.setDisabled(busy)
            button.setText(str(button.property("idleText")))
        self.ai_cancel.setVisible(busy)
        if busy:
            self.ai_advanced_toggle.setChecked(True)
            self.diagnostic_label.setText(f"{title}：正在启动，请稍候……")

    def _update_model_hint(self, _text: str = "") -> None:
        label = getattr(self, "_model_source_text", "未读取模型列表")
        manual = self.manual_model.text().strip()
        if manual:
            label += f" · 当前手动模型：{manual}（高级设置）"
        self.model_source.setText(label)

    def set_model_values(self, models: list[str], source: str, updated_at: str, selected: str = "") -> None:
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model in models:
            self.model_combo.addItem(model, model)
        index = self.model_combo.findData(selected)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)
        elif models:
            preferred = self.model_combo.findData("deepseek-v4-pro")
            self.model_combo.setCurrentIndex(preferred if preferred >= 0 else 0)
        if selected and index < 0:
            self.manual_model.setText(selected)
        self.model_combo.blockSignals(False)
        label = f"{source or '未读取模型列表'}"
        if not models and selected:
            label += " · 上次模型尚未实时验证"
        if updated_at:
            label += f" · {updated_at}"
        self._model_source_text = label
        self._update_model_hint()

    def set_memory_summary(self, text: str) -> None:
        self.memory_summary.setPlainText(text)

    def set_archived_reference(self, allowed: bool) -> None:
        self.reference_archived_projects.blockSignals(True)
        self.reference_archived_projects.setChecked(allowed)
        self.reference_archived_projects.blockSignals(False)
