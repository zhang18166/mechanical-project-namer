# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""Compact daily workflow. Existing tools remain reachable from the More menu."""
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QSizePolicy,
)

from .main_window import MainWindow
from .project_dialog import ProjectDialog
from .mini_window import MiniWindow
from ..numbering.simple import SIMPLE_TYPE, full_name


class SimpleMainWindow(MainWindow):
    simple_mode = True
    project_files_requested = pyqtSignal()

    def __init__(self, settings):
        self._simple_project = None
        self._sequence_context = None
        super().__init__(settings)
        self.findChild(QFrame, 'sidebar').hide()
        root = self.centralWidget().layout()
        # Place navigation above content, keeping legacy pages available from menus.
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setContentsMargins(22, 10, 22, 6)
        title = QPushButton('机械命名助手')
        title.setProperty('kind', 'ghost')
        title.clicked.connect(lambda: self.show_page(1))
        bar.addWidget(title)
        self.home_button = QPushButton('返回生成')
        self.home_button.clicked.connect(lambda: self.show_page(1))
        bar.addWidget(self.home_button)
        bar.addStretch()
        self.project_files_button = QPushButton('项目文件')
        self.project_files_button.clicked.connect(self.project_files_requested.emit)
        bar.addWidget(self.project_files_button)
        more = QPushButton('更多')
        self.more_button = more
        menu = QMenu(more)
        for label, callback in (
            ('设备管理', lambda: self.show_page(0)),
            ('模块管理', lambda: self.module_manage_requested.emit('', 'manage')),
            ('全部记录 / 导出', lambda: self.show_page(2)),
            ('AI设计问答', self.design_chat_requested.emit),
            ('AI命名建议', self._show_ai_tools),
            ('AI规划历史', self.plan_history_requested.emit),
            ('快速小窗', self.mini_requested.emit),
            ('仅登记名称（不创建文件）', self._save),
        ):
            menu.addAction(label).triggered.connect(callback)
        more.setMenu(menu)
        bar.addWidget(more)
        settings_button = QPushButton('设置')
        self.settings_button = settings_button
        settings_button.clicked.connect(lambda: self.show_page(3))
        bar.addWidget(settings_button)
        layout.addLayout(bar)
        layout.addWidget(self.content_scroll)
        root.addWidget(body)
        self.pages.currentChanged.connect(lambda i: self.home_button.setVisible(i != 1))
        self.home_button.hide()
        # Put everyday settings first, retain all AI and maintenance settings.
        tabs = self.settings_page.tabs
        tabs.tabBar().moveTab(3, 0)
        tabs.tabBar().moveTab(3, 1)
        tabs.setTabText(0, '常用设置'); tabs.setTabText(1, '文件生成')
        tabs.setTabText(2, 'AI 设置'); tabs.setTabText(3, 'AI 记忆')
        tabs.setCurrentIndex(0)
        # These legacy switches contradict the one-click workflow; preserve their stored values.
        sw_form = self.settings_page.sw_confirm.parentWidget().layout()
        for field in (self.settings_page.sw_confirm, self.settings_page.sw_advance):
            sw_form.setRowVisible(field, False)
        self.statusBar().showMessage('就绪')

    def _build_current_page(self):
        # Reuse the established signals/controllers, with diagnostic controls off the daily page.
        self.advanced_naming = super()._build_current_page()
        page = QWidget()
        self.advanced_naming.setParent(page)
        self.advanced_naming.hide()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 10, 24, 16)
        layout.setSpacing(8)
        title = QLabel('新建文件'); title.setObjectName('pageTitle'); layout.addWidget(title)
        row = QHBoxLayout()
        self.module_combo = QComboBox()
        for combo in (self.project_combo, self.module_combo):
            combo.setMinimumContentsLength(10)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.module_combo.currentIndexChanged.connect(self._module_changed)
        self.sequence_edit = QLineEdit()
        self.sequence_edit.setValidator(QIntValidator(1, 999, self))
        self.sequence_edit.setMaxLength(3)
        self.sequence_edit.setPlaceholderText('001')
        self.sequence_edit.setToolTip('自动给出下一图号，也可以手动输入001至999；已占用号码不能重复使用。')
        self.sequence_edit.textChanged.connect(self._update_filename)
        self.sequence_edit.editingFinished.connect(self._pad_sequence)
        self.auto_number_button = QPushButton('自动')
        self.auto_number_button.clicked.connect(self._reset_sequence)
        for label, widget, stretch in [('设备', self.project_combo, 4), ('模块', self.module_combo, 3)]:
            box = QVBoxLayout(); box.addWidget(QLabel(label)); box.addWidget(widget); row.addLayout(box, stretch)
        numbers = QVBoxLayout(); numbers.addWidget(QLabel('图号'))
        controls = QHBoxLayout(); controls.addWidget(self.sequence_edit); controls.addWidget(self.auto_number_button)
        numbers.addLayout(controls); row.addLayout(numbers, 2); layout.addLayout(row)
        layout.addWidget(QLabel('文字备注'))
        self.part_name.setPlaceholderText('例如：三相电机380（会写入文件名）')
        self.part_name.textChanged.connect(self._update_filename)
        try:
            self.part_name.returnPressed.disconnect()
        except TypeError:
            pass
        self.part_name.returnPressed.connect(lambda: self.solidworks_button.click())
        layout.addWidget(self.part_name)
        preview_box = QFrame(); preview_box.setObjectName('namingPreview')
        preview_layout = QVBoxLayout(preview_box); preview_layout.addWidget(QLabel('文件名预览'))
        self.filename_preview = QLabel('选择设备和模块，填写文字备注')
        self.filename_preview.setObjectName('simpleFilename')
        self.filename_preview.setWordWrap(True)
        self.filename_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        preview_layout.addWidget(self.filename_preview); layout.addWidget(preview_box)
        self.solidworks_directory_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        directory = QHBoxLayout(); directory.addWidget(QLabel('保存到：'))
        directory.addWidget(self.solidworks_directory_label, 1); directory.addWidget(self.solidworks_directory_button)
        layout.addLayout(directory)
        actions = QHBoxLayout()
        self.solidworks_button.setText('生成文件')
        self.copy_button.setText('复制名称')
        self.copy_button.setProperty('kind', 'secondary')
        actions.addWidget(self.solidworks_button); actions.addWidget(self.copy_button)
        actions.addWidget(self.solidworks_cancel)
        self.create_hint = QLabel('创建 SolidWorks 零件并自动记录'); self.create_hint.setObjectName('mutedText')
        actions.addWidget(self.create_hint); actions.addStretch(); actions.addWidget(self.solidworks_status)
        layout.addLayout(actions)
        divider = QFrame(); divider.setFrameShape(QFrame.Shape.HLine); layout.addWidget(divider)
        title = QLabel('记录查询'); title.setObjectName('sectionTitle'); layout.addWidget(title)
        search = QHBoxLayout()
        self.current_search.setPlaceholderText('搜索完整文件名、模块或文字备注')
        search.addWidget(self.current_search, 1)
        self.search_scope = QComboBox(); self.search_scope.addItems(['当前设备', '全部设备'])
        self.search_scope.currentIndexChanged.connect(self._current_record_search)
        search.addWidget(self.search_scope)
        self.file_sync_button.setText('导入已有文件'); search.addWidget(self.file_sync_button)
        layout.addLayout(search)
        self.search_timer = QTimer(self); self.search_timer.setSingleShot(True); self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(self._current_record_search)
        self.current_search.textChanged.connect(lambda: self.search_timer.start())
        self.current_records = QTableWidget(0, 5)
        self.current_records.setObjectName("simpleRecords")
        self.current_records.setProperty('simple_records', True)
        self.current_records.setHorizontalHeaderLabels(['完整名称', '模块', '创建时间', '文件状态', '操作'])
        self.current_records.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.current_records.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.current_records.verticalHeader().hide()
        self.current_records.setWordWrap(False)
        self.current_records.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in [(1, 85), (2, 145), (3, 95), (4, 125)]: self.current_records.setColumnWidth(col, width)
        self.current_records.doubleClicked.connect(lambda i: self.record_action_requested.emit('open_file', self.current_records.item(i.row(), 0).data(Qt.ItemDataRole.UserRole)))
        self.current_records.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.current_records.customContextMenuRequested.connect(lambda p: self._record_context_menu(self.current_records, p))
        layout.addWidget(self.current_records, 1)
        self.file_sync_status.hide()
        return page

    @property
    def selected_sequence(self):
        value = self.sequence_edit.text().strip()
        if not value.isdigit() or not 1 <= int(value) <= 999:
            raise ValueError('请填写001至999之间的图号，或点击“自动”。')
        return int(value)

    def _pad_sequence(self):
        if self.sequence_edit.text().isdigit(): self.sequence_edit.setText(self.sequence_edit.text().zfill(3))

    def _reset_sequence(self):
        self._sequence_context = None
        if hasattr(self, '_automatic_sequence'): self.sequence_edit.setText(self._automatic_sequence)

    def _update_filename(self):
        if not hasattr(self, 'filename_preview'): return
        note = self.part_name.text().strip()
        if self._simple_project and self.module_combo.currentData() and self.sequence_edit.text().isdigit():
            text = full_name(self._simple_project.code, self.module_combo.currentData(), int(self.sequence_edit.text()), note or '文字备注') + '.SLDPRT'
        else: text = '选择设备和模块，填写文字备注'
        self.filename_preview.setText(text)
        self.filename_preview.setToolTip(text)

    def set_projects(self, projects, current_project_id, active_projects=None):
        super().set_projects(projects, current_project_id, active_projects)
        self.project_combo.blockSignals(True)
        self.project_combo.addItem('＋新增设备', -1)
        if current_project_id: self.project_combo.addItem('编辑当前设备…', -2)
        else:
            self.project_combo.setPlaceholderText('选择或新增设备')
            self.project_combo.setCurrentIndex(-1)
        self.project_combo.blockSignals(False)

    def _combo_changed(self, index):
        value = self.project_combo.itemData(index)
        if value == -1:
            self.project_combo.blockSignals(True); self.project_combo.setCurrentIndex(self.project_combo.findData(self.current_project_id)); self.project_combo.blockSignals(False)
            self.new_project_requested.emit()
        elif value == -2:
            self.project_combo.blockSignals(True); self.project_combo.setCurrentIndex(self.project_combo.findData(self.current_project_id)); self.project_combo.blockSignals(False)
            if self.current_project_id: self.edit_project_requested.emit(self.current_project_id)
        elif value is not None: self.project_selected.emit(value)

    def _module_changed(self, index):
        value = self.module_combo.itemData(index)
        if value in ('__new__', '__edit__'):
            current = getattr(self, '_selected_module', '')
            self.module_combo.blockSignals(True); self.module_combo.setCurrentIndex(self.module_combo.findData(current)); self.module_combo.blockSignals(False)
            if value == '__new__': self.module_create_requested.emit()
            elif current: self.module_manage_requested.emit(current, 'edit')
        elif value: self.module_selected.emit(value)

    def _rebuild_module_buttons(self, modules, selected_module):
        self._selected_module = selected_module
        self.module_combo.blockSignals(True); self.module_combo.clear()
        for item in modules:
            if item.get('enabled', 1): self.module_combo.addItem(f"{item['module_code']} · {item['module_name']}", item['module_code'])
        self.module_combo.setCurrentIndex(self.module_combo.findData(selected_module))
        self.module_combo.addItem('＋新增模块', '__new__')
        if selected_module: self.module_combo.addItem('编辑当前模块…', '__edit__')
        else:
            self.module_combo.setPlaceholderText('选择或新增模块')
            self.module_combo.setCurrentIndex(-1)
        self.module_combo.blockSignals(False)

    def set_current_project(self, project, records, modules=None, selected_module='', selected_part_type=SIMPLE_TYPE, preview=''):
        super().set_current_project(project, records, modules, selected_module, selected_part_type, preview)
        context = (project.id if project else None, selected_module, preview)
        self._simple_project = project
        value = preview.rsplit('-', 1)[-1]
        self._automatic_sequence = value if value.isdigit() and 1 <= int(value) <= 999 else ''
        if self._sequence_context != context:
            self.sequence_edit.setText(self._automatic_sequence)
            self._sequence_context = context
        self.solidworks_button.setEnabled(bool(project and selected_module))
        self.module_combo.setEnabled(project is not None)
        self._update_filename()

    def set_record_rows(self, table, records):
        if not table.property('simple_records'): return super().set_record_rows(table, records)
        if records == getattr(table, '_displayed_records', None): return
        table._displayed_records = list(records); table.setRowCount(len(records))
        for row, record in enumerate(records):
            extension = record.file_extension or (Path(record.file_path).suffix if record.file_path else "")
            name = record.display_name + (extension if extension and not record.display_name.lower().endswith(extension.lower()) else "")
            state = '已作废' if record.status == 'void' else ('已关联' if record.file_path else '仅名称记录')
            for col, value in enumerate((name, record.module_code, record.created_at[:16].replace('T', ' '), state)):
                item = QTableWidgetItem(value); item.setData(Qt.ItemDataRole.UserRole, record.id)
                item.setToolTip("记录名称：" + name + ("\n实际文件：" + record.file_path if record.file_path else "")); table.setItem(row, col, item)
            actions = QWidget(); box = QHBoxLayout(actions); box.setContentsMargins(2, 0, 2, 0); box.setSpacing(1)
            for label, action in [('打开', 'open_file'), ('定位', 'open_directory')]:
                button = QPushButton(label); button.setProperty('kind', 'ghost'); button.setProperty('record_action', True)
                button.setEnabled(bool(record.file_path)); button.clicked.connect(lambda checked=False, a=action, rid=record.id: self.record_action_requested.emit(a, rid)); box.addWidget(button)
            table.setCellWidget(row, 4, actions); table.setRowHeight(row, 44)

    def clear_inputs(self):
        self._sequence_context = None
        super().clear_inputs()

    def set_solidworks_busy(self, busy):
        super().set_solidworks_busy(busy)
        self._pad_sequence()
        self.solidworks_button.setText('正在生成…' if busy else '生成文件')
        for widget in (self.project_combo, self.module_combo, self.sequence_edit, self.auto_number_button, self.part_name, self.more_button, self.settings_button, self.solidworks_directory_button, self.copy_button, self.file_sync_button): widget.setEnabled(not busy)

    def _show_ai_tools(self):
        if not hasattr(self, '_ai_dialog'):
            self._ai_dialog = QDialog(self); self._ai_dialog.setWindowTitle('AI命名建议'); self._ai_dialog.resize(800, 560)
            layout = QVBoxLayout(self._ai_dialog); layout.addWidget(self.ai_content)
        self.ai_content.show(); self._ai_dialog.show(); self._ai_dialog.raise_()


class SimpleProjectDialog(ProjectDialog):
    def __init__(self, parent=None, project=None):
        super().__init__(parent, project)
        self.setWindowTitle('编辑设备' if project else '新增设备')
        self.name.setPlaceholderText('例如：钢筋弯曲机')
        self.code.setPlaceholderText('例如：WQJ')
        form = self.name.parentWidget().layout().itemAt(1).layout()
        self.layout().itemAt(0).widget().setText('编辑设备' if project else '新增设备')
        form.labelForField(self.name).setText('设备名称 *')
        form.labelForField(self.code).setText('设备简称 *')
        # The main folder is the file destination; an existing separate SW folder stays visible.
        self._extra_rows = [self.description, self.notes, self.project_type]
        if not project or not project.solidworks_part_directory or project.directory == project.solidworks_part_directory:
            self._extra_rows.append(5)
        for widget in self._extra_rows: form.setRowVisible(widget, False)
        form.itemAt(4, QFormLayout.ItemRole.LabelRole).widget().setText('保存目录')
        self._advanced = QPushButton('更多资料')
        self._advanced.setCheckable(True)
        self._advanced.toggled.connect(lambda checked: [form.setRowVisible(w, checked) for w in self._extra_rows])
        self.layout().insertWidget(2, self._advanced)

    def values(self):
        values = super().values()
        if (not self.project and not values['solidworks_part_directory']) or (self.project and self.project.directory == self.project.solidworks_part_directory and not self.solidworks_part_directory.isModified()):
            values['solidworks_part_directory'] = values['directory']
        return values


class SimpleMiniWindow(MiniWindow):
    def __init__(self):
        super().__init__()
        self.part_type_combo.hide()
        for label in self.findChildren(QLabel):
            if label.text() == '类别': label.hide()
        self.ai_status.hide()
        self.save_button.hide()
        self.extra_notes.hide()
        self.part_name.setPlaceholderText('文字备注，例如三相电机380')
        self.copy_button.setText('复制名称')
        self.solidworks_button.setText('生成文件')
        try:
            self.part_name.returnPressed.disconnect()
        except TypeError:
            pass
        self.part_name.returnPressed.connect(self.solidworks_button.click)
        self.shortcuts[-1].setEnabled(False)
        self._number_preview = ''
        self.part_name.textChanged.connect(self._update_filename)
        self.preview.setWordWrap(True)

    def set_project(self, *args, **kwargs):
        super().set_project(*args, **kwargs)
        self._number_preview = kwargs.get('preview', args[5] if len(args) > 5 else '')
        self._update_filename()

    def _update_filename(self):
        self.preview.setText(f"{self._number_preview}-{self.part_name.text().strip() or '文字备注'}.SLDPRT" if self._number_preview else '请选择设备和模块')

    def set_solidworks_busy(self, busy):
        super().set_solidworks_busy(busy)
        for widget in (self.module_combo, self.part_name, self.copy_button, self.solidworks_directory_button): widget.setEnabled(not busy)


class ModuleDialog(QDialog):
    def __init__(self, parent=None, code='', name=''):
        super().__init__(parent); self.setWindowTitle('编辑模块' if code else '新增模块')
        layout = QVBoxLayout(self); form = QFormLayout()
        self.code = QLineEdit(code); self.code.setPlaceholderText('例如 DJ')
        self.name = QLineEdit(name); self.name.setPlaceholderText('例如 电机')
        form.addRow('模块代号', self.code); form.addRow('模块名称', self.name); layout.addLayout(form)
        hint = QLabel('更改已使用的代号会建立新模块；旧记录和文件保留。'); hint.setWordWrap(True); layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存'); buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
