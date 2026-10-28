# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""Lazy project file page. All filesystem scans and index writes run off the UI thread."""
from datetime import datetime
from pathlib import Path
from threading import Event

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSizePolicy,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..project_files import EXTENSIONS, STATE_LABELS, ProjectFileCatalog, Source, local_path, inspect_file
from .common import FlowLayout, open_local_path, show_local_path_error


class CatalogWorker(QThread):
    progress = pyqtSignal(int)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, catalog, project_id, sources=(), *, refresh=False, parent=None):
        super().__init__(parent)
        self.catalog, self.project_id, self.sources = catalog, project_id, sources
        self.refresh = refresh
        self.cancel = Event()

    def run(self):
        try:
            batch = self.catalog.scan(self.project_id, self.sources, refresh=self.refresh,
                                      cancel=self.cancel, progress=self.progress.emit)
            if not batch.complete or self.cancel.is_set():
                self.completed.emit(dict(committed=False, count=0, warnings=batch.warnings))
                return
            count = self.catalog.commit(batch, cancel=self.cancel)
            self.completed.emit(dict(committed=True, count=count, warnings=batch.warnings))
        except Exception as exc:
            self.failed.emit(f'{type(exc).__name__}：{exc}')


class ProjectFilesPage(QWidget):
    def __init__(self, repository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.catalog = ProjectFileCatalog(repository)
        self.worker = None
        self.preview_window = None
        self.pdf_window = None
        self.project_id = None
        self.rows = []
        self._projects = []
        self.setMinimumSize(0, 0)
        layout = QVBoxLayout(self)
        title = QLabel('项目文件 · 只读本地清单')
        title.setStyleSheet('font-size: 20px; font-weight: 600;')
        layout.addWidget(title)
        note = QLabel('仅登记路径和文件信息，不改名、不移动、不创建图号。打开原文件后由系统关联程序处理。')
        note.setWordWrap(True); layout.addWidget(note)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget(); left_layout = QVBoxLayout(left)
        self.project_search = QLineEdit(); self.project_search.setPlaceholderText('搜索项目名称或代号')
        self.projects = QListWidget()
        left_layout.addWidget(self.project_search); left_layout.addWidget(self.projects)
        left.setMaximumWidth(250)
        right = QWidget(); right_layout = QVBoxLayout(right)
        filters = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText('搜索名称、扩展名、项目或零件编号')
        self.extension = QComboBox(); self.extension.addItem('全部类型', '')
        for ext in EXTENSIONS: self.extension.addItem(ext.upper(), ext)
        filters.addWidget(self.search, 1); filters.addWidget(self.extension)
        right_layout.addLayout(filters)
        actions = FlowLayout()
        self.add_files = QPushButton('添加文件'); self.add_directory = QPushButton('添加目录')
        self.rescan = QPushButton('重新扫描'); self.cancel_button = QPushButton('取消扫描')
        self.open_button = QPushButton('打开文件'); self.locate_button = QPushButton('定位文件')
        self.link_button = QPushButton('关联零件')
        self.preview_button = QPushButton('本地预览')
        for button in (self.add_files, self.add_directory, self.rescan, self.cancel_button,
                       self.preview_button, self.open_button, self.locate_button, self.link_button): actions.addWidget(button)
        right_layout.addLayout(actions)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(['文件名称', '类型', '所属项目', '关联零件编号', '原始完整路径', '文件大小', '修改时间', '存在状态'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        for column, width in enumerate((180, 75, 125, 135, 190, 80, 150, 90)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        right_layout.addWidget(self.table, 1)
        self.status = QLabel('请选择项目并添加文件或目录。')
        self.status.setWordWrap(True); right_layout.addWidget(self.status)
        split.addWidget(left); split.addWidget(right); split.setStretchFactor(1, 1)
        split.setSizes([210, 900]); layout.addWidget(split, 1)
        self.project_search.textChanged.connect(self._filter_projects)
        self.projects.currentItemChanged.connect(self._select_project)
        self.search.textChanged.connect(self.refresh)
        self.extension.currentIndexChanged.connect(self.refresh)
        self.add_files.clicked.connect(self._choose_files)
        self.add_directory.clicked.connect(self._choose_directory)
        self.rescan.clicked.connect(lambda: self.start_scan(refresh=True))
        self.cancel_button.clicked.connect(self.cancel_scan)
        self.open_button.clicked.connect(lambda: self.open_selected(False))
        self.locate_button.clicked.connect(lambda: self.open_selected(True))
        self.link_button.clicked.connect(self._associate)
        self.preview_button.clicked.connect(self.preview_selected)
        self.table.doubleClicked.connect(lambda: self.open_selected(False))
        self._set_busy(False)

    def load_projects(self, current_project_id=None):
        if self.worker is not None:
            return
        self._projects = self.repository.list_projects(project_status='all')
        self.project_id = current_project_id
        self._filter_projects()

    def _filter_projects(self):
        key = self.project_search.text().strip().casefold()
        self.projects.blockSignals(True); self.projects.clear()
        all_item = QListWidgetItem('全部项目（仅查看）'); all_item.setData(Qt.ItemDataRole.UserRole, None)
        self.projects.addItem(all_item); selected = all_item
        for project in self._projects:
            if key and key not in f'{project.name} {project.code}'.casefold(): continue
            item = QListWidgetItem(f'{project.name} · {project.code}')
            item.setData(Qt.ItemDataRole.UserRole, project.id); self.projects.addItem(item)
            if project.id == self.project_id: selected = item
        self.projects.setCurrentItem(selected); self.projects.blockSignals(False)
        self._select_project(selected)

    def _select_project(self, current, previous=None):
        self.project_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self._set_busy(self.worker is not None)
        self.refresh()

    def refresh(self, *_args):
        try:
            self.rows = self.catalog.list_files(self.project_id, self.search.text(), self.extension.currentData() or '')
            self.table.setRowCount(len(self.rows))
            for index, row in enumerate(self.rows):
                try:
                    modified = datetime.fromtimestamp(row['mtime_ns']/1e9).astimezone().strftime('%Y-%m-%d %H:%M:%S') if row['mtime_ns'] else '未知'
                except (ValueError, OSError, OverflowError):
                    modified = '时间异常'
                values = (row['name'], row['extension'].upper(), row['project_name'], row['record_number'] or '未关联',
                          row['path'], f"{row['file_size']:,} B", modified, STATE_LABELS[row['state']])
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value)); item.setToolTip(row['error'] if column == 7 else str(value))
                    self.table.setItem(index, column, item)
        except Exception as exc:
            self.status.setText(f'读取文件清单失败：{exc}')

    def _choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '选择本地 CAD 或 PDF 文件', '',
            'CAD 与工程图 (*.SLDPRT *.SLDASM *.SLDDRW *.STEP *.STP *.STL *.GLB *.PDF *.sldprt *.sldasm *.slddrw *.step *.stp *.stl *.glb *.pdf)')
        if paths: self.start_scan([Source(path, 'file') for path in paths])

    def _choose_directory(self):
        path = QFileDialog.getExistingDirectory(self, '选择本地文件目录（只读扫描）')
        if path: self.start_scan([Source(path, 'directory')])

    def start_scan(self, sources=(), *, refresh=False):
        if self.worker is not None or self.project_id is None:
            return
        self.worker = CatalogWorker(self.catalog, self.project_id, sources, refresh=refresh, parent=self)
        self.worker.progress.connect(lambda count: self.status.setText(f'正在后台扫描：已检查 {count} 项；可取消。'))
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(lambda message: self.status.setText('索引失败，本批未提交：' + message))
        self.worker.finished.connect(self._finished)
        self._set_busy(True); self.status.setText('正在后台扫描并校验文件，完成后统一登记…')
        self.worker.start()

    def cancel_scan(self):
        if self.worker is not None:
            self.worker.cancel.set(); self.status.setText('正在取消，等待当前文件操作结束…')

    def _completed(self, result):
        text = f"已完成：检查并登记 {result['count']} 项文件。" if result['committed'] else '扫描取消或未完整结束，本批未提交。'
        if result['warnings']: text += ' 提示：' + '；'.join(result['warnings'][:3])
        self.status.setText(text); self.refresh()

    def _finished(self):
        worker, self.worker = self.worker, None
        self._set_busy(False)
        if worker is not None: worker.deleteLater()

    def _set_busy(self, busy):
        self.projects.setEnabled(not busy); self.project_search.setEnabled(not busy)
        for button in (self.add_files, self.add_directory, self.rescan): button.setEnabled(not busy and self.project_id is not None)
        for button in (self.open_button, self.locate_button, self.link_button): button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def selected_file(self):
        index = self.table.currentRow()
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def preview_selected(self):
        row=self.selected_file()
        if not row:self.status.setText('请先选择一个GLB或PDF文件。');return
        extension=row['extension'].lower()
        if extension not in ('.glb','.pdf'):
            self.status.setText('目前仅支持GLB和PDF内嵌预览；该类型请使用“打开文件”。');return
        try:
            if extension=='.pdf':
                from .pdf_window import PdfDialog
                if self.pdf_window is None:
                    self.pdf_window=PdfDialog(self)
                    self.pdf_window.destroyed.connect(lambda:setattr(self,'pdf_window',None))
                self.pdf_window.show();self.pdf_window.raise_()
                self.pdf_window.load_path(row['path'],row['name'])
                return
            from .preview_window import PreviewDialog
            if self.preview_window is None:
                self.preview_window=PreviewDialog(self)
                self.preview_window.destroyed.connect(lambda:self._preview_destroyed())
            self.preview_window.show();self.preview_window.raise_()
            self.preview_window.load_path(row['path'],row['name'])
        except ImportError:
            self.status.setText('未安装可选预览依赖，请运行 scripts/setup_preview.ps1；基础命名和文件功能仍可用。')
        except Exception as exc:self.status.setText(f'无法启动本地预览：{exc}')

    def _preview_destroyed(self):self.preview_window=None

    @property
    def preview_busy(self):return any(window is not None and window.busy for window in (self.preview_window,self.pdf_window))

    def close_preview(self):
        if self.preview_window is not None:self.preview_window.close()
        if self.pdf_window is not None:self.pdf_window.close()

    def open_selected(self, directory=False, *, opener=None):
        row = self.selected_file()
        if not row: self.status.setText('请先选择一个文件。'); return
        try:
            path = local_path(row['path'])
            if not directory:
                entry = inspect_file(path)
                if entry.state != 'present':
                    self.status.setText(STATE_LABELS[entry.state] + '：' + entry.error); return
            target = path.parent if directory else path
            local_path(target)
            result = open_local_path(target, 'directory' if directory else 'file', opener=opener)
            if result.ok: self.status.setText('已请求系统打开：' + str(target))
            else: show_local_path_error(self, result, '文件目录' if directory else '文件')
            return result
        except Exception as exc:
            self.status.setText(f'无法打开：{exc}')

    def _associate(self):
        row = self.selected_file()
        if not row: self.status.setText('请先选择一个文件。'); return
        records = self.repository.list_records(row['project_id'])
        choices = ['不关联零件'] + [f'{record.filename} · {record.part_name}' for record in records]
        selected, accepted = QInputDialog.getItem(self, '关联已有零件（不占用新编号）', '选择本项目已有记录', choices, 0, False)
        if accepted:
            try:
                index = choices.index(selected)
                self.catalog.associate_record(row['file_id'], records[index-1].id if index else None)
                self.refresh(); self.status.setText('关联已更新，原有图号及计数器不变。')
            except Exception as exc:
                self.status.setText(f'关联失败：{exc}')
