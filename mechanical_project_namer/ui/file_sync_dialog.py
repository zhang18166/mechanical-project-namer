# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""On-demand scan/registration preview. Work stops when the dialog closes."""
from threading import Event
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..file_sync import READY, import_entries, path_key, scan_project
from ..numbering.simple import SIMPLE_TYPE


class FileScanThread(QThread):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel = Event()

    def run(self):
        try:
            self.succeeded.emit(self.operation(self.cancel))
        except Exception as error:
            self.failed.emit(str(error))


class FileSyncDialog(QDialog):
    def __init__(self, repository, project_id, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.project_id = project_id
        self.result = None
        self.worker = None
        self.overrides = {}
        self.imported = 0
        self._closing = False
        self.setWindowTitle('同步已有文件')
        self.resize(1100, 640)
        layout = QVBoxLayout(self)
        project = repository.get_project(project_id)
        intro = QLabel(f'{project.name} · {project.code}\n只登记软件记录和占用图号；实际文件不会改名、移动或删除。“修改后名称”仅为规范命名预览。相同图号的模型、工程图、PDF合并为一条记录。')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.status = QLabel('正在准备扫描…')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['选择', '状态', '图号', '零件名称', '原始名称\n（识别到的文件名）', '修改后名称\n（建议，仅预览）', '关联文件 / 核对说明'])
        header = self.table.horizontalHeader()
        for column, width in enumerate((48, 78, 150, 115, 200, 200, 200)):
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeaderItem(4).setToolTip('扫描到的实际文件名，含扩展名；多个关联文件逐行列出。悬停单元格查看完整名称。')
        self.table.horizontalHeaderItem(5).setToolTip('根据已核对的图号和零件名称生成的规范文件名预览，保留原扩展名；登记不会修改实际文件名。')
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        layout.addWidget(self.table, 1)
        footer = QHBoxLayout()
        self.rescan_button = QPushButton('重新扫描')
        self.rescan_button.clicked.connect(self.rescan)
        self.manual_button = QPushButton('为选中文件手动登记…')
        self.manual_button.clicked.connect(self._assign)
        self.import_button = QPushButton('登记勾选项')
        self.import_button.setProperty('kind', 'primary')
        self.import_button.clicked.connect(self._import)
        self.close_button = QPushButton('关闭')
        self.close_button.clicked.connect(self.reject)
        for button in (self.rescan_button, self.manual_button):
            footer.addWidget(button)
        footer.addStretch()
        footer.addWidget(self.import_button)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)
        QTimer.singleShot(0, self.rescan)

    def _start(self, operation, done):
        if self.worker:
            return
        for b in (self.rescan_button, self.manual_button, self.import_button):
            b.setEnabled(False)
        self.worker = FileScanThread(operation, self)
        self.worker.succeeded.connect(done)
        self.worker.failed.connect(lambda error: self.status.setText('未完成：' + error))
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.rescan_button.setEnabled(True)
        self.manual_button.setEnabled(True)
        self.import_button.setEnabled(bool(self.result and not self.result.errors))
        if self._closing:
            super().reject()
        elif self.imported:
            self.accept()

    def rescan(self):
        self.result = None
        self.table.setRowCount(0)
        self.status.setText('正在扫描所选项目目录，可关闭取消…')
        overrides = dict(self.overrides)
        self._start(lambda cancel: scan_project(self.repository, self.project_id, overrides=overrides, cancel=cancel), self._show_result)

    def _show_result(self, result):
        self.result = result
        self.table.setRowCount(len(result.entries))
        for row, e in enumerate(result.entries):
            check = QTableWidgetItem()
            if e.status in READY and not result.errors:
                check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                check.setCheckState(Qt.CheckState.Checked)
            else:
                check.setFlags(Qt.ItemFlag.NoItemFlags)
            self.table.setItem(row, 0, check)
            paths = '\n'.join(f.path for f in e.files)
            originals = '\n'.join(Path(f.path).name for f in e.files) or '未找到文件'
            if e.status in READY | {'已登记'} and e.number and e.name and e.files:
                separator = '-' if e.part_type == SIMPLE_TYPE else ' '
                proposed = '\n'.join(f'{e.number}{separator}{e.name}{Path(f.path).suffix}' for f in e.files)
            elif e.status == '无法识别':
                proposed = '待填写图号和零件名称'
            else:
                proposed = '待核对，暂不生成建议名称'
            for column, value in enumerate((e.status, e.number, e.name, originals, proposed, paths + ('\n' if paths else '') + e.detail), 1):
                item = QTableWidgetItem(value)
                item.setToolTip(value + ('\n仅为规范命名预览，登记不会修改实际文件名。' if column == 5 else ''))
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, max(30, min(96, self.table.fontMetrics().lineSpacing() * max(1, len(e.files)) + 10)))
        ready = sum(e.status in READY for e in result.entries)
        self.status.setText(f'扫描目录：{"；".join(result.roots) or "未设置，请先设置项目目录或SW保存目录"}\n发现{len(result.entries)}项，可登记/关联{ready}项。' + ('\n扫描不完整，禁止登记：' + '\n'.join(result.errors[:5]) if result.errors else ''))

    def _assign(self):
        row = self.table.currentRow()
        if self.result is None or row < 0:
            return
        entry = self.result.entries[row]
        if entry.status != '无法识别' or len(entry.files) != 1:
            QMessageBox.information(self, '手动登记', '请选择一项无法识别的文件；编号冲突需要先核对文件和已有记录。')
            return
        project = self.repository.get_project(self.project_id)
        number, ok = QInputDialog.getText(self, '确认所属图号', f'记录到当前项目，原文件名不变。\n填写完整图号，如{project.code}-03-028：')
        if not ok:
            return
        name, ok = QInputDialog.getText(self, '确认零件名称', '零件名称：', text=entry.name)
        if ok and name.strip():
            self.overrides[path_key(entry.files[0].path)] = (number.strip().upper(), name.strip())
            self.rescan()

    def _import(self):
        if self.result is None or self.result.errors:
            return
        selected = [e for i, e in enumerate(self.result.entries) if e.status in READY and self.table.item(i, 0).checkState() == Qt.CheckState.Checked]
        if not selected:
            return
        if QMessageBox.question(self, '确认登记已有文件', f'登记/关联{len(selected)}项，并保留这些已使用的图号？\n实际文件保持不变。', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.status.setText('正在复核文件并登记…')
        self._start(lambda cancel: import_entries(self.repository, self.result, selected, overrides=self.overrides, cancel=cancel), lambda count: setattr(self, 'imported', count))

    def reject(self):
        if self.worker:
            self._closing = True
            self.worker.cancel.set()
            self.status.setText('正在取消并等待扫描结束…')
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)


class SimpleFileSyncDialog(FileSyncDialog):
    """Same registration safeguards, with a concise before/record view."""
    def __init__(self, repository, project_id, parent=None):
        super().__init__(repository, project_id, parent)
        self.setWindowTitle('导入已有文件')
        self.layout().itemAt(0).widget().setText('把手动创建的文件登记到记录中，并保留图号。原文件保持原名和原位置。')
        self.table.setColumnHidden(2, True)
        self.table.setColumnHidden(3, True)
        for col, title in [(4, '原始文件名'), (5, '登记名称（不改原文件）'), (6, '位置 / 核对说明')]:
            self.table.horizontalHeaderItem(col).setText(title)
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.manual_button.setText('填写 / 修改登记名称')
        self.table.doubleClicked.connect(lambda _: self._assign())

    def _show_result(self, result):
        super()._show_result(result)
        ready = sum(e.status in READY for e in result.entries)
        self.status.setText(f'发现 {len(result.entries)} 项 · 可登记 {ready} 项' + (' · 扫描未完成，请核对目录' if result.errors else ''))
        self.status.setToolTip('\n'.join((*result.roots, *result.errors)))

    def _assign(self):
        from PyQt6.QtWidgets import QComboBox, QDialogButtonBox, QFormLayout, QLineEdit, QSpinBox
        row = self.table.currentRow()
        if self.result is None or row < 0:
            return
        entry = self.result.entries[row]
        if not entry.files or entry.record_id or (entry.status != '无法识别' and not entry.manual):
            QMessageBox.information(self, '登记名称', '请选择尚未识别或手动填写过的文件。已登记记录可在记录列表中编辑。')
            return
        project = self.repository.get_project(self.project_id)
        dialog = QDialog(self); dialog.setWindowTitle('登记已有文件'); dialog.resize(560, 280)
        layout = QVBoxLayout(dialog)
        original = QLabel('原文件：' + '\n'.join(Path(f.path).name for f in entry.files)); original.setWordWrap(True); layout.addWidget(original)
        form = QFormLayout(); module = QComboBox()
        for item in self.repository.list_modules(self.project_id): module.addItem(f"{item['module_code']} · {item['module_name']}", item['module_code'])
        if entry.module: module.setCurrentIndex(max(0, module.findData(entry.module)))
        sequence = QSpinBox(); sequence.setRange(1, 999)
        def automatic():
            try: sequence.setValue(self.repository.get_next_named_number(project.id, module.currentData(), SIMPLE_TYPE))
            except ValueError: sequence.setValue(999)
        module.currentIndexChanged.connect(automatic); automatic()
        if entry.sequence > 0: sequence.setValue(entry.sequence)
        note = QLineEdit(entry.name if entry.manual else Path(entry.files[0].path).stem)
        note.setPlaceholderText('文字备注，例如三相电机380')
        form.addRow('模块', module); form.addRow('图号', sequence); form.addRow('文字备注', note); layout.addLayout(form)
        preview = QLabel(); preview.setWordWrap(True); layout.addWidget(preview)
        def refresh(): preview.setText(f"登记名称：{project.code}-{module.currentData() or '模块'}-{sequence.value():03d}-{note.text().strip()}")
        module.currentIndexChanged.connect(refresh); sequence.valueChanged.connect(refresh); note.textChanged.connect(refresh); refresh()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('确认名称'); buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        while dialog.exec():
            from ..solidworks.validation import validate_part_name
            try:
                validate_part_name(note.text().strip())
                if not module.currentData(): raise ValueError('请先在首页新增模块。')
            except (ValueError, RuntimeError) as error:
                QMessageBox.warning(dialog, '请核对名称', str(error)); continue
            for file in entry.files:
                self.overrides[path_key(file.path)] = (f'{project.code}-{module.currentData()}-{sequence.value():03d}', note.text().strip(), SIMPLE_TYPE)
            self.rescan()
            break
