# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDateEdit, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QHeaderView, QMainWindow, QPushButton, QStackedWidget, QSizePolicy,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QMenu, QScrollArea,
)

from ..projects.models import DrawingRecord, Project
from ..numbering import MODULES, PART_TYPES
from ..settings import AppSettings
from .. import __version__
from .common import STATUS_NAMES, BADGE_TOKENS, add_menu_action, configure_table, FlowLayout, status_badge
from .settings_page import SettingsPage


class ResponsiveStackedWidget(QStackedWidget):
    """Expose current-page height without imposing its horizontal minimum."""

    def sizeHint(self) -> QSize:
        page = self.currentWidget()
        return QSize(0, page.sizeHint().height()) if page is not None else QSize()

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()


class MainWindow(QMainWindow):
    hide_requested = pyqtSignal()
    mini_requested = pyqtSignal()
    project_selected = pyqtSignal(int)
    module_selected = pyqtSignal(str)
    part_type_selected = pyqtSignal(str)
    new_project_requested = pyqtSignal()
    edit_project_requested = pyqtSignal(int)
    archive_project_requested = pyqtSignal(int)
    open_directory_requested = pyqtSignal(int)
    copy_requested = pyqtSignal()
    save_requested = pyqtSignal(str, str, str)
    solidworks_requested = pyqtSignal(str, str)
    solidworks_directory_requested = pyqtSignal()
    solidworks_cancel_requested = pyqtSignal()
    record_open_requested = pyqtSignal(int)
    record_action_requested = pyqtSignal(str, int)
    project_search_requested = pyqtSignal(str, str, str)
    project_action_requested = pyqtSignal(str, int)
    record_search_requested = pyqtSignal(str, str, str, bool)
    csv_export_requested = pyqtSignal(bool)
    record_export_requested = pyqtSignal(str, bool)
    ai_requested = pyqtSignal(str, str)
    ai_cancel_requested = pyqtSignal()
    module_manage_requested = pyqtSignal(str, str)
    module_create_requested = pyqtSignal()
    design_chat_requested = pyqtSignal()
    plan_history_requested = pyqtSignal()

    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.allow_close = False
        self.current_project_id: int | None = None
        self.setWindowTitle(f"机械项目命名助手 V{__version__}")
        self.setMinimumSize(620, 440)
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(170)
        sidebar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(18, 22, 18, 18)
        brand = QLabel("机械项目\n命名助手")
        brand.setObjectName("brand")
        side_layout.addWidget(brand)
        sub = QLabel("图号与项目记录")
        sub.setObjectName("sidebarMuted")
        side_layout.addWidget(sub)
        side_layout.addSpacing(22)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        for text in ("项目管理", "当前项目", "历史记录", "设置", "AI设计问答"):
            QListWidgetItem(text, self.navigation)
        self.navigation.setCurrentRow(1)
        self.navigation.currentRowChanged.connect(self._navigate)
        self.navigation.itemClicked.connect(self._navigation_clicked)
        side_layout.addWidget(self.navigation, 1)
        mini = QPushButton("打开 Mini 窗格")
        mini.setProperty("kind", "sidebar")
        mini.clicked.connect(self.mini_requested)
        side_layout.addWidget(mini)
        root_layout.addWidget(sidebar)
        self.pages = ResponsiveStackedWidget()
        self.project_page = self._build_project_page()
        self.current_page = self._build_current_page()
        self.history_page = self._build_history_page()
        self.settings_page = SettingsPage(settings)
        for page in (self.project_page, self.current_page, self.history_page, self.settings_page):
            self.pages.addWidget(page)
        self.pages.setCurrentIndex(1)
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.content_scroll.setWidget(self.pages)
        root_layout.addWidget(self.content_scroll, 1)
        self.setCentralWidget(root)

    def _build_project_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("项目管理")
        title.setObjectName("pageTitle")
        title_box.addWidget(title)
        subtitle = QLabel("项目代号确认后默认锁定；归档会保留全部历史数据。")
        subtitle.setObjectName("mutedText")
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        new_button = QPushButton("新建项目")
        new_button.setProperty("kind", "primary")
        new_button.clicked.connect(self.new_project_requested)
        header.addWidget(new_button)
        layout.addLayout(header)
        toolbar_card = QFrame()
        toolbar_card.setObjectName("toolbarCard")
        toolbar_layout = QVBoxLayout(toolbar_card)
        toolbar_layout.setContentsMargins(16, 13, 16, 13)
        filters = QHBoxLayout()
        self.project_search = QLineEdit()
        self.project_search.setPlaceholderText("搜索项目名称、代号或备注")
        self.project_status_filter = QComboBox()
        self.project_status_filter.addItem("正常项目", "normal")
        self.project_status_filter.addItem("已归档", "archived")
        self.project_status_filter.addItem("回收站", "trash")
        self.project_type_filter = QComboBox()
        self.project_type_filter.addItem("全部类型", "all")
        self.project_type_filter.addItem("正式项目", "formal")
        self.project_type_filter.addItem("测试项目", "test")
        self.project_status_filter.currentIndexChanged.connect(self._project_search)
        self.project_type_filter.currentIndexChanged.connect(self._project_search)
        search_button = QPushButton("搜索")
        search_button.setProperty("kind", "secondary")
        search_button.clicked.connect(self._project_search)
        self.project_search.returnPressed.connect(self._project_search)
        filters.addWidget(self.project_search, 1)
        filters.addWidget(self.project_status_filter)
        filters.addWidget(self.project_type_filter)
        filters.addWidget(search_button)
        toolbar_layout.addLayout(filters)
        layout.addWidget(toolbar_card)
        list_card = QFrame()
        list_card.setObjectName("listCard")
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(16, 13, 16, 16)
        self.project_table = QTableWidget(0, 9)
        self.project_table.setHorizontalHeaderLabels(["项目名称", "项目代号", "项目类型", "项目状态", "模块数量", "零件记录数量", "最近使用模块", "最近创建编号", "项目备注"])
        configure_table(self.project_table)
        self.project_table.doubleClicked.connect(self._open_selected_project)
        self.project_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.project_table.customContextMenuRequested.connect(self._project_context_menu)
        list_layout.addWidget(self.project_table, 1)
        actions = QHBoxLayout()
        for text, callback, kind in (
            ("打开项目", self._open_selected_project, "primary"),
            ("编辑项目", self._edit_selected_project, "secondary"),
            ("复制项目", lambda: self._emit_project_action("duplicate"), "secondary"),
            ("项目属性", lambda: self._emit_project_action("properties"), "secondary"),
            ("打开项目目录", self._open_selected_directory, "secondary"),
            ("归档项目", lambda: self._emit_project_action("archive"), "secondary"),
        ):
            button = QPushButton(text)
            button.setProperty("kind", kind)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        list_layout.addLayout(actions)
        layout.addWidget(list_card)
        return page

    def _build_current_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("当前项目")
        title.setObjectName("pageTitle")
        title_box.addWidget(title)
        summary = QLabel("当前项目的模块、类别与编号预览")
        summary.setObjectName("mutedText")
        title_box.addWidget(summary)
        header.addLayout(title_box)
        header.addStretch()
        new_button = QPushButton("新建项目")
        new_button.setProperty("kind", "secondary")
        new_button.clicked.connect(self.new_project_requested)
        header.addWidget(new_button)
        mini = QPushButton("Mini 窗格")
        mini.setProperty("kind", "secondary")
        mini.clicked.connect(self.mini_requested)
        header.addWidget(mini)
        layout.addLayout(header)
        ai_band = QFrame()
        ai_band.setObjectName("aiBand")
        ai_layout = QVBoxLayout(ai_band)
        ai_layout.setContentsMargins(16, 13, 16, 13)
        self.ai_toggle = QPushButton("展开 AI 规划建议")
        self.ai_toggle.setProperty("kind", "ghost")
        self.ai_toggle.setCheckable(True)
        ai_layout.addWidget(self.ai_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.ai_content = QWidget()
        ai_content_layout = QVBoxLayout(self.ai_content)
        ai_content_layout.setContentsMargins(0, 0, 0, 0)
        self.ai_content.setVisible(False)
        self.ai_toggle.toggled.connect(self._toggle_ai_panel)
        description = QLabel("输入设备描述，获取三个可解释的项目代号候选。AI 不会修改当前项目。")
        description.setWordWrap(True)
        ai_content_layout.addWidget(description)
        ai_controls = QWidget()
        self.ai_controls_flow = FlowLayout(ai_controls, spacing=8)
        self.ai_description = QLineEdit()
        self.ai_description.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.ai_description.setPlaceholderText("例如：16毫米螺纹钢十二轮打圈机")
        self.ai_mode = QComboBox()
        self.ai_mode.addItem("项目代号建议", "project")
        self.ai_mode.addItem("零件命名建议", "part")
        self.ai_controls_flow.addWidget(self.ai_mode)
        self.ai_controls_flow.addWidget(self.ai_description)
        self.ai_button = QPushButton("生成建议")
        self.ai_button.setProperty("kind", "secondary")
        self.ai_button.clicked.connect(lambda: self.ai_requested.emit(self.ai_description.text(), self.ai_mode.currentData()))
        self.ai_controls_flow.addWidget(self.ai_button)
        self.plan_history_button = QPushButton("AI规划历史")
        self.plan_history_button.setProperty("kind", "ghost")
        self.plan_history_button.clicked.connect(self.plan_history_requested)
        self.ai_controls_flow.addWidget(self.plan_history_button)
        ai_content_layout.addWidget(ai_controls)
        runtime = QFrame()
        runtime.setObjectName("aiRuntime")
        runtime_layout = QVBoxLayout(runtime)
        runtime_header_widget = QWidget()
        runtime_header = FlowLayout(runtime_header_widget, spacing=8)
        self.ai_status = QLabel("AI 状态：空闲")
        self.ai_elapsed = QLabel("已用时间：0.0 秒")
        self.ai_cancel = QPushButton("取消生成")
        self.ai_cancel.setProperty("kind", "secondary")
        self.ai_cancel.setVisible(False)
        self.ai_cancel.clicked.connect(self.ai_cancel_requested)
        self.ai_log_toggle = QPushButton("展开 AI 运行日志")
        self.ai_log_toggle.setProperty("kind", "ghost")
        self.ai_log_toggle.setCheckable(True)
        self.ai_log_toggle.toggled.connect(lambda checked: self.ai_log.setVisible(checked))
        runtime_header.addWidget(self.ai_status)
        runtime_header.addWidget(self.ai_elapsed)
        runtime_header.addWidget(self.ai_cancel)
        runtime_header.addWidget(self.ai_log_toggle)
        runtime_layout.addWidget(runtime_header_widget)
        self.ai_log = QTextEdit()
        self.ai_log.setObjectName("aiLog")
        self.ai_log.setReadOnly(True)
        self.ai_log.document().setMaximumBlockCount(500)
        self.ai_log.setMaximumHeight(145)
        self.ai_log.setVisible(False)
        runtime_layout.addWidget(self.ai_log)
        ai_content_layout.addWidget(runtime)
        ai_layout.addWidget(self.ai_content)
        project_row_widget = QWidget()
        project_row = FlowLayout(project_row_widget, spacing=8)
        project_row.addWidget(QLabel("当前项目"))
        self.project_combo = QComboBox()
        self.project_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.project_combo.currentIndexChanged.connect(self._combo_changed)
        project_row.addWidget(self.project_combo)
        layout.addWidget(project_row_widget)
        naming = QFrame()
        naming.setObjectName("namingPanel")
        naming_layout = QVBoxLayout(naming)
        naming_layout.setContentsMargins(16, 13, 16, 14)
        self.project_summary = QLabel("请先新建项目")
        self.project_summary.setObjectName("mutedText")
        naming_layout.addWidget(self.project_summary)
        module_label = QLabel("模块")
        module_label.setObjectName("mutedText")
        naming_layout.addWidget(module_label)
        self.module_buttons: list[QPushButton] = []
        self.module_flow_widget = QWidget()
        self.module_flow = FlowLayout(self.module_flow_widget, spacing=8)
        naming_layout.addWidget(self.module_flow_widget)
        module_actions = QHBoxLayout()
        add_module = QPushButton("+ 新增模块")
        add_module.setProperty("kind", "secondary")
        add_module.clicked.connect(self.module_create_requested)
        manage_module = QPushButton("管理模块")
        manage_module.setProperty("kind", "secondary")
        manage_module.clicked.connect(lambda: self.module_manage_requested.emit("", "manage"))
        module_actions.addWidget(add_module)
        module_actions.addWidget(manage_module)
        module_actions.addStretch()
        naming_layout.addLayout(module_actions)
        type_row_widget = QWidget()
        type_row = FlowLayout(type_row_widget, spacing=8)
        type_row.addWidget(QLabel("零件类别"))
        self.part_type = QComboBox()
        for name, label, _start, _end in PART_TYPES:
            self.part_type.addItem(f"{name} {label}", name)
        self.part_type.currentIndexChanged.connect(lambda _index: self.part_type_selected.emit(self.part_type.currentData()))
        type_row.addWidget(self.part_type)
        type_row.addWidget(QLabel("当前编号"))
        naming_layout.addWidget(type_row_widget)
        self.preview = QLabel("—")
        self.preview.setObjectName("filePreview")
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        naming_layout.addWidget(self.preview)
        fields_widget = QWidget()
        self.part_fields_flow = FlowLayout(fields_widget, spacing=12)
        self.part_name = QLineEdit()
        self.part_name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.part_name.setPlaceholderText("零件名称")
        self.purpose = QLineEdit()
        self.purpose.setPlaceholderText("用途备注")
        self.purpose.setVisible(False)
        self.extra_notes = QLineEdit()
        self.extra_notes.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.extra_notes.setPlaceholderText("额外备注（可选）")
        part_field = QWidget()
        part_field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        part_field_layout = QHBoxLayout(part_field)
        part_field_layout.setContentsMargins(0, 0, 0, 0)
        part_field_layout.addWidget(QLabel("零件名称"))
        part_field_layout.addWidget(self.part_name, 1)
        notes_field = QWidget()
        notes_field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        notes_field_layout = QHBoxLayout(notes_field)
        notes_field_layout.setContentsMargins(0, 0, 0, 0)
        notes_field_layout.addWidget(QLabel("额外备注"))
        notes_field_layout.addWidget(self.extra_notes, 1)
        self.part_fields_flow.addWidget(part_field)
        self.part_fields_flow.addWidget(notes_field)
        naming_layout.addWidget(fields_widget)
        button_row_widget = QWidget()
        self.naming_actions_flow = FlowLayout(button_row_widget, spacing=8)
        self.copy_button = QPushButton("仅复制")
        self.copy_button.setProperty("kind", "ghost")
        self.copy_button.clicked.connect(self.copy_requested)
        self.save_button = QPushButton("保存并复制")
        self.save_button.setProperty("kind", "secondary")
        self.save_button.clicked.connect(self._save)
        self.solidworks_button = QPushButton("创建并打开SW零件")
        self.solidworks_button.setProperty("kind", "primary")
        self.solidworks_button.clicked.connect(lambda: self.solidworks_requested.emit(self.part_name.text(), self.extra_notes.text()))
        self.solidworks_cancel = QPushButton("取消创建")
        self.solidworks_cancel.setProperty("kind", "secondary")
        self.solidworks_cancel.setVisible(False)
        self.solidworks_cancel.clicked.connect(self.solidworks_cancel_requested)
        self.solidworks_status = QLabel("SW：未检测")
        self.naming_actions_flow.addWidget(self.copy_button)
        self.naming_actions_flow.addWidget(self.save_button)
        self.naming_actions_flow.addWidget(self.solidworks_button)
        self.naming_actions_flow.addWidget(self.solidworks_cancel)
        self.naming_actions_flow.addWidget(self.solidworks_status)
        naming_layout.addWidget(button_row_widget)
        sw_path_row = QHBoxLayout()
        sw_path_row.addWidget(QLabel("SW保存目录："))
        self.solidworks_directory_label = QLabel("未设置")
        self.solidworks_directory_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.solidworks_directory_label.setToolTip("SOLIDWORKS零件实际保存目录")
        sw_path_row.addWidget(self.solidworks_directory_label, 1)
        self.solidworks_directory_button = QPushButton("立即设置")
        self.solidworks_directory_button.setProperty("kind", "ghost")
        self.solidworks_directory_button.clicked.connect(self.solidworks_directory_requested)
        sw_path_row.addWidget(self.solidworks_directory_button)
        naming_layout.addLayout(sw_path_row)
        self.solidworks_log_toggle = QPushButton("展开 SOLIDWORKS 运行日志")
        self.solidworks_log_toggle.setProperty("kind", "ghost")
        self.solidworks_log_toggle.setCheckable(True)
        self.solidworks_log = QTextEdit()
        self.solidworks_log.setReadOnly(True)
        self.solidworks_log.document().setMaximumBlockCount(500)
        self.solidworks_log.setMaximumHeight(130)
        self.solidworks_log.setVisible(False)
        self.solidworks_log_toggle.toggled.connect(self.solidworks_log.setVisible)
        naming_layout.addWidget(self.solidworks_log_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        naming_layout.addWidget(self.solidworks_log)
        layout.addWidget(naming)
        layout.addWidget(ai_band)
        records = QFrame()
        records.setObjectName("recordsCard")
        records_layout = QVBoxLayout(records)
        records_layout.setContentsMargins(16, 14, 16, 16)
        records_layout.addWidget(QLabel("当前项目文件记录"))
        record_header_widget = QWidget()
        self.record_toolbar_flow = FlowLayout(record_header_widget, spacing=8)
        self.current_search = QLineEdit()
        self.current_search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.current_search.setPlaceholderText("搜索文件名、零件或备注")
        self.current_search.returnPressed.connect(self._current_record_search)
        self.record_toolbar_flow.addWidget(self.current_search)
        current_search_button = QPushButton("搜索")
        current_search_button.setProperty("kind", "secondary")
        current_search_button.clicked.connect(self._current_record_search)
        self.record_toolbar_flow.addWidget(current_search_button)
        directory_button = QPushButton("打开项目目录")
        directory_button.setProperty("kind", "secondary")
        directory_button.clicked.connect(self._open_current_directory)
        self.record_toolbar_flow.addWidget(directory_button)
        export_button = QPushButton("导出记录 ▼")
        export_button.setProperty("kind", "secondary")
        export_menu = QMenu(export_button)
        for label, file_format in (("导出 PDF", "pdf"), ("导出 Excel", "xlsx"), ("导出 CSV", "csv")):
            action = export_menu.addAction(label)
            action.triggered.connect(lambda _checked=False, fmt=file_format: self.record_export_requested.emit(fmt, True))
        export_button.setMenu(export_menu)
        self.record_toolbar_flow.addWidget(export_button)
        records_layout.addWidget(record_header_widget)
        self.current_records = self._record_table(primary_first=True)
        records_layout.addWidget(self.current_records)
        layout.addWidget(records)
        layout.addStretch()
        return page

    def _build_history_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        title = QLabel("历史记录")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        toolbar_card = QFrame()
        toolbar_card.setObjectName("toolbarCard")
        toolbar_layout = QVBoxLayout(toolbar_card)
        toolbar_layout.setContentsMargins(16, 13, 16, 13)
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("搜索所有项目的文件名、零件名称或备注")
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        self.date_from.setSpecialValueText("起始日期")
        self.date_from.setMinimumDate(self.date_from.minimumDate())
        self.date_from.setDate(self.date_from.minimumDate())
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        self.date_to.setSpecialValueText("结束日期")
        self.date_to.setDate(self.date_to.minimumDate())
        self.history_project = QComboBox()
        self.history_project.setEditable(True)
        self.history_project.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.history_project.setPlaceholderText("搜索项目代号、名称或备注")
        self.history_project.currentIndexChanged.connect(lambda: self._history_search())
        search = QPushButton("搜索")
        search.setProperty("kind", "secondary")
        search.clicked.connect(self._history_search)
        export = QPushButton("导出记录 ▼")
        export.setProperty("kind", "secondary")
        export_menu = QMenu(export)
        for label, file_format in (("导出 PDF", "pdf"), ("导出 Excel", "xlsx"), ("导出 CSV", "csv")):
            action = export_menu.addAction(label)
            action.triggered.connect(lambda _checked=False, fmt=file_format: self.record_export_requested.emit(fmt, False))
        export.setMenu(export_menu)
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("项目"))
        search_row.addWidget(self.history_project, 1)
        search_row.addWidget(self.history_search, 1)
        search_row.addWidget(search)
        toolbar_layout.addLayout(search_row)
        date_row = QHBoxLayout()
        date_row.addWidget(QLabel("起始日期"))
        date_row.addWidget(self.date_from)
        date_row.addWidget(QLabel("结束日期"))
        date_row.addWidget(self.date_to)
        date_row.addStretch()
        clear_button = QPushButton("清除筛选")
        clear_button.setProperty("kind", "ghost")
        clear_button.clicked.connect(self._clear_history_filters)
        date_row.addWidget(clear_button)
        date_row.addWidget(export)
        toolbar_layout.addLayout(date_row)
        layout.addWidget(toolbar_card)
        records_card = QFrame()
        records_card.setObjectName("recordsCard")
        records_layout = QVBoxLayout(records_card)
        records_layout.setContentsMargins(16, 13, 16, 16)
        self.history_records = self._record_table()
        records_layout.addWidget(self.history_records)
        layout.addWidget(records_card)
        return page

    def _record_table(self, primary_first: bool = False) -> QTableWidget:
        table = QTableWidget(0, 11)
        table.setHorizontalHeaderLabels(["序号", "日期", "完整名称", "项目代号", "模块", "零件序号", "零件名称", "额外备注", "零件类别", "状态", "创建时间"])
        configure_table(table)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        if primary_first:
            header = table.horizontalHeader()
            header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            for target_position, logical_index in enumerate((0, 1, 2, 4, 6, 9, 10)):
                header.moveSection(header.visualIndex(logical_index), target_position)
            widths = (62, 92, 270, 110, 150, 82, 160, 110, 100, 180, 130)
            for logical_index, width in enumerate(widths):
                table.setColumnWidth(logical_index, width)
        table.doubleClicked.connect(lambda index, source=table: self._record_double_click(source, index))
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(lambda position, source=table: self._record_context_menu(source, position))
        return table

    def _record_double_click(self, table: QTableWidget, index) -> None:
        row = index.row()
        item = table.item(row, index.column())
        if item is None:
            return
        record_id = int(table.item(row, 0).data(Qt.ItemDataRole.UserRole))
        if index.column() in {2, 6}:
            QApplication.clipboard().setText(item.text())
            self.statusBar().showMessage(f"已复制：{item.text()}", 2200)
        else:
            self.record_open_requested.emit(record_id)

    def _record_context_menu(self, table: QTableWidget, position) -> None:
        row = table.rowAt(position.y())
        if row < 0 or table.item(row, 0) is None:
            return
        record_id = int(table.item(row, 0).data(Qt.ItemDataRole.UserRole))
        menu = QMenu(table)
        # 分组：复制 / 编辑 / 状态管理 / 危险操作（放最后）
        for label, action_name in (("复制完整名称", "copy_full"), ("复制零件编号", "copy_number"),
                                   ("复制零件名称", "copy_part")):
            action = menu.addAction(label)
            action.triggered.connect(lambda _checked=False, name=action_name, rid=record_id: self.record_action_requested.emit(name, rid))
        menu.addSeparator()
        for label, action_name in (("编辑记录 / 查看详情", "edit"), ("打开项目目录", "open_directory")):
            action = menu.addAction(label)
            action.triggered.connect(lambda _checked=False, name=action_name, rid=record_id: self.record_action_requested.emit(name, rid))
        menu.addSeparator()
        for label, action_name in (("标记作废", "void"), ("恢复使用", "restore")):
            action = menu.addAction(label)
            action.triggered.connect(lambda _checked=False, name=action_name, rid=record_id: self.record_action_requested.emit(name, rid))
        # 危险操作（不可逆）：红色 + 最后
        add_menu_action(menu, "删除错误记录", slot=lambda: self.record_action_requested.emit("delete", record_id),
                        danger=True, separator_before=True)
        menu.exec(table.viewport().mapToGlobal(position))

    def set_projects(self, projects: list[Project], current_project_id: int | None,
                     active_projects: list[Project] | None = None) -> None:
        self.project_table.setRowCount(len(projects))
        name_counts = {}
        for project in projects:
            name_counts[project.name] = name_counts.get(project.name, 0) + 1
        for row, project in enumerate(projects):
            display_name = project.name + ("（可能重复，请检查）" if name_counts.get(project.name, 0) > 1 else "")
            values = [display_name, project.code,
                      "测试项目" if project.project_type == "test" else "正式项目",
                      {"normal": "正常", "archived": "已归档", "trash": "回收站"}.get(project.project_status, project.project_status),
                      str(project.module_count), str(project.used_count), project.latest_module or "—",
                      project.latest_number or "—", project.notes]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, project.id)
                item.setData(Qt.ItemDataRole.UserRole + 1, project.project_status)
                item.setToolTip(value)
                self.project_table.setItem(row, column, item)
            # 项目类型 / 项目状态 → 视觉徽章（数据保留在 col0 UserRole，逻辑不变）
            if project.project_type == "test":
                self.project_table.setCellWidget(row, 2, status_badge("test"))
            if project.project_status in BADGE_TOKENS:
                badge = status_badge(project.project_status,
                                     {"normal": "正常", "archived": "已归档", "trash": "回收站"}.get(project.project_status, project.project_status))
                self.project_table.setCellWidget(row, 3, badge)
        active = active_projects if active_projects is not None else [project for project in projects if project.project_status == "normal"]
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for project in active:
            self.project_combo.addItem(f"{project.name}  ·  {project.code}", project.id)
        index = self.project_combo.findData(current_project_id)
        self.project_combo.setCurrentIndex(index if index >= 0 else (0 if active else -1))
        self.project_combo.blockSignals(False)

    def set_current_project(self, project: Project | None, records: list[DrawingRecord], modules: list[dict] | None = None,
                            selected_module: str = "", selected_part_type: str = "自制机加工件", preview: str = "") -> None:
        self.current_project_id = project.id if project else None
        enabled = project is not None
        self.copy_button.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        if project:
            self.preview.setText(preview or project.next_filename)
            self.project_summary.setText(f"{project.name}  ·  项目代号 {project.code}  ·  已使用 {project.used_count} 个图号")
            directory = project.solidworks_part_directory or "未设置"
            self.solidworks_directory_label.setText(directory)
            self.solidworks_directory_label.setToolTip(directory)
            self.solidworks_directory_button.setText("修改目录" if project.solidworks_part_directory else "立即设置")
            enabled_codes = {item["module_code"] for item in (modules or []) if item.get("enabled", 1)}
            self._rebuild_module_buttons(modules or [], selected_module)
            index = self.part_type.findData(selected_part_type)
            if index >= 0:
                self.part_type.blockSignals(True)
                self.part_type.setCurrentIndex(index)
                self.part_type.blockSignals(False)
        else:
            self.preview.setText("请先创建或选择项目")
            self.project_summary.setText("尚无当前项目")
            self._rebuild_module_buttons([], "")
            self.solidworks_directory_label.setText("未设置")
            self.solidworks_directory_label.setToolTip("未设置")
            self.solidworks_directory_button.setText("立即设置")
        self.set_record_rows(self.current_records, records)

    def _rebuild_module_buttons(self, modules: list[dict], selected_module: str) -> None:
        signature = tuple((str(item.get("module_code", "")).zfill(2), str(item.get("module_name", "")), bool(item.get("enabled", 1))) for item in modules)
        if signature == getattr(self, "_module_signature", None):
            for button, (code, _name, _enabled) in zip(self.module_buttons, signature):
                button.setChecked(code == selected_module)
            return
        self._module_signature = signature
        while self.module_flow.count():
            item = self.module_flow.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self.module_buttons = []
        for item in modules:
            code = str(item.get("module_code", "")).zfill(2)
            name = str(item.get("module_name", ""))
            button = QPushButton(f"{code} {name}")
            button.setCheckable(True)
            button.setProperty("kind", "secondary")
            button.setToolTip(f"{code} {name}")
            button.setEnabled(bool(item.get("enabled", 1)))
            button.setChecked(code == selected_module)
            button.clicked.connect(lambda _checked=False, code=code: self.module_selected.emit(code))
            button.mouseDoubleClickEvent = lambda event, code=code: self.module_manage_requested.emit(code, "edit")
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(lambda _pos, code=code: self.module_manage_requested.emit(code, "menu"))
            self.module_flow.addWidget(button)
            self.module_buttons.append(button)
        self.module_flow.invalidate()
        self.module_flow_widget.updateGeometry()

    @staticmethod
    def set_record_rows(table: QTableWidget, records: list[DrawingRecord]) -> None:
        if records == getattr(table, "_displayed_records", None):
            return
        table._displayed_records = list(records)
        table.setRowCount(len(records))
        for row, record in enumerate(records):
            values = [str(record.drawing_number), record.record_date, record.display_name, record.project_code,
                      record.module_code, f"{record.part_sequence:03d}", record.part_name, record.extra_notes,
                      record.part_type, STATUS_NAMES.get(record.status, record.status), record.created_at.replace("T", " ")]
            for column, value in enumerate(values):
                if column == 9:  # status column -> visual badge (data stays in UserRole at col 0)
                    continue
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, record.id)
                table.setItem(row, column, item)
            table.setCellWidget(row, 9, status_badge(record.status))

    def clear_inputs(self) -> None:
        self.part_name.clear()
        self.purpose.clear()
        self.extra_notes.clear()
        self.part_name.setFocus()

    def set_ai_busy(self, busy: bool) -> None:
        if busy:
            self.ai_toggle.setChecked(True)
        self.ai_button.setDisabled(busy)
        self.ai_button.setText("正在生成…" if busy else "生成建议")
        self.ai_cancel.setVisible(busy)
        if not busy:
            self.ai_elapsed.setText("已用时间：0.0 秒")

    def _toggle_ai_panel(self, expanded: bool) -> None:
        self.ai_content.setVisible(expanded)
        self.ai_toggle.setText("收起 AI 规划建议" if expanded else "展开 AI 规划建议")
        if hasattr(self, "pages"):
            self.pages.updateGeometry()

    def set_ai_status(self, text: str, elapsed: float | None = None) -> None:
        self.ai_status.setText(f"AI 状态：{text}")
        if elapsed is not None:
            self.ai_elapsed.setText(f"已用时间：{elapsed:.1f} 秒")

    def append_ai_log(self, text: str) -> None:
        self.ai_log.append(text)

    def clear_ai_log(self) -> None:
        self.ai_log.clear()

    def show_page(self, index: int) -> None:
        self.navigation.setCurrentRow(index)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.allow_close:
            event.accept()
        else:
            event.ignore()
            self.hide_requested.emit()

    def _navigate(self, index: int) -> None:
        if index == 4:
            return
        self.pages.setCurrentIndex(index)
        self.pages.updateGeometry()
        if index == 0:
            self._project_search()
        elif index == 2:
            self._history_search()

    def _navigation_clicked(self, item: QListWidgetItem) -> None:
        if self.navigation.row(item) == 4:
            self.design_chat_requested.emit()

    def _selected_project_id(self) -> int | None:
        row = self.project_table.currentRow()
        item = self.project_table.item(row, 0) if row >= 0 else None
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _combo_changed(self, index: int) -> None:
        project_id = self.project_combo.itemData(index)
        if project_id is not None:
            self.project_selected.emit(int(project_id))

    def _open_selected_project(self, *_args) -> None:
        project_id = self._selected_project_id()
        if project_id is not None:
            self._emit_project_action("open")

    def _edit_selected_project(self) -> None:
        project_id = self._selected_project_id()
        if project_id is not None:
            self.edit_project_requested.emit(project_id)

    def _archive_selected_project(self) -> None:
        project_id = self._selected_project_id()
        if project_id is not None:
            self.archive_project_requested.emit(project_id)

    def _open_selected_directory(self) -> None:
        project_id = self._selected_project_id()
        if project_id is not None:
            self.open_directory_requested.emit(project_id)

    def _project_search(self) -> None:
        self.project_search_requested.emit(self.project_search.text(), self.project_status_filter.currentData(), self.project_type_filter.currentData())

    def _emit_project_action(self, action: str) -> None:
        project_id = self._selected_project_id()
        if project_id is not None:
            self.project_action_requested.emit(action, project_id)

    def _project_context_menu(self, position) -> None:
        row = self.project_table.rowAt(position.y())
        item = self.project_table.item(row, 0) if row >= 0 else None
        if item is None:
            return
        project_id = int(item.data(Qt.ItemDataRole.UserRole))
        status = str(item.data(Qt.ItemDataRole.UserRole + 1) or "normal")
        menu = QMenu(self.project_table)
        # 危险操作「永久删除」单独放最后并标红；其余归类并加分隔线
        safe_actions = []
        for label, action_name in self.project_menu_actions(status):
            if action_name == "permanent_delete":
                add_menu_action(
                    menu, label,
                    slot=lambda pid=project_id: self.project_action_requested.emit("permanent_delete", pid),
                    danger=True, separator_before=True,
                )
            else:
                safe_actions.append((label, action_name))
        if safe_actions:
            for label, action_name in safe_actions:
                action = menu.addAction(label)
                action.triggered.connect(lambda _checked=False, name=action_name, pid=project_id: self.project_action_requested.emit(name, pid))
        menu.exec(self.project_table.viewport().mapToGlobal(position))

    @staticmethod
    def project_menu_actions(status: str):
        menus = {
            "normal": (("打开项目", "open"), ("设为当前项目", "set_current"), ("编辑项目", "edit"),
                       ("项目属性", "properties"), ("复制项目", "duplicate"), ("查看编号状态", "numbering"),
                       ("打开项目目录", "directory"), ("导出项目", "export"), ("归档项目", "archive"), ("移入回收站", "trash")),
            "archived": (("查看项目", "open"), ("恢复项目", "restore"), ("复制为新项目", "duplicate"),
                         ("项目属性", "properties"), ("查看编号状态", "numbering"), ("打开项目目录", "directory"),
                         ("导出项目", "export"), ("移入回收站", "trash")),
            "trash": (("查看项目属性", "properties"), ("恢复项目", "restore"), ("导出项目", "export"), ("永久删除", "permanent_delete")),
        }
        return menus.get(status, ())

    def _current_record_search(self) -> None:
        self.record_search_requested.emit(self.current_search.text(), "", "", True)

    def _open_current_directory(self) -> None:
        if self.current_project_id is not None:
            self.open_directory_requested.emit(self.current_project_id)

    def _history_search(self) -> None:
        date_from = "" if self.date_from.date() == self.date_from.minimumDate() else self.date_from.date().toString("yyyy-MM-dd")
        date_to = "" if self.date_to.date() == self.date_to.minimumDate() else self.date_to.date().toString("yyyy-MM-dd")
        self.record_search_requested.emit(self.history_search.text(), date_from, date_to, False)

    def _clear_history_filters(self) -> None:
        self.history_project.setCurrentIndex(0)
        self.history_project.lineEdit().clear()
        self.history_search.clear()
        self.date_from.setDate(self.date_from.minimumDate())
        self.date_to.setDate(self.date_to.minimumDate())
        self._history_search()

    def set_history_projects(self, projects: list[Project]) -> None:
        current = self.history_project.currentData()
        self.history_project.blockSignals(True)
        self.history_project.clear()
        self.history_project.addItem("全部项目", None)
        for project in projects:
            notes = project.notes.strip() or "无备注"
            self.history_project.addItem(f"{project.code}｜{project.name}｜备注：{notes}", project.id)
        if current is not None:
            index = self.history_project.findData(current)
            if index >= 0:
                self.history_project.setCurrentIndex(index)
        self.history_project.blockSignals(False)

    def _open_record(self, table: QTableWidget) -> None:
        row = table.currentRow()
        item = table.item(row, 0) if row >= 0 else None
        if item:
            self.record_open_requested.emit(int(item.data(Qt.ItemDataRole.UserRole)))

    def _save(self) -> None:
        if self.save_button.isEnabled():
            self.save_requested.emit(self.part_name.text(), self.extra_notes.text(), "")

    def set_solidworks_busy(self, busy: bool) -> None:
        self.solidworks_button.setDisabled(busy)
        self.solidworks_cancel.setVisible(busy)
        if busy:
            self.solidworks_status.setText("SW：正在创建")

    def set_solidworks_status(self, text: str) -> None:
        self.solidworks_status.setText(f"SW：{text}")

    def append_solidworks_log(self, text: str) -> None:
        self.solidworks_log.append(text)
