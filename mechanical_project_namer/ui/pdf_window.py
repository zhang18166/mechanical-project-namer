# SPDX-License-Identifier: GPL-3.0-only
"""PDF-only window: bounded QtPdf child process, visible-page raster cache, no actions."""
import base64
import importlib.util
import json
import math
import sys
from pathlib import Path
from threading import Event

from PyQt6.QtCore import QProcess, QProcessEnvironment, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton,
                            QScrollArea, QSpinBox, QVBoxLayout, QWidget)
from ..pdf import MAX_FRAME, MAX_PIXELS, PdfError, ProcessBudget, frames, packet, read_pdf


class PdfReadWorker(QThread):
    completed = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(self, generation, path):
        super().__init__(); self.generation = generation; self.path = path; self.cancel = Event()

    def run(self):
        try: self.completed.emit(self.generation, read_pdf(self.path, self.cancel))
        except Exception as exc: self.failed.emit(self.generation, str(exc))


class PdfDialog(QDialog):
    def __init__(self, parent=None):
        if getattr(sys,'frozen',False):
            raise RuntimeError('PDF预览尚未打包，请使用源码开发版运行。')
        if importlib.util.find_spec('PyQt6.QtPdf') is None:
            raise ImportError('QtPdf missing')
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('本地 PDF 工程图预览'); self.resize(1080, 800)
        self.generation = 0; self.workers = []; self.processes = {}; self.process = None
        self.pending = None; self.closing = False; self.buffer = bytearray()
        self.sizes = []; self.labels = []; self.cache = []; self.revision = 0; self.inflight = None
        self.diagnostics = {}; self.zoom_mode = 'window'; self.zoom = 1.0
        layout = QVBoxLayout(self)
        self.name = QLabel(); self.name.setTextFormat(Qt.TextFormat.PlainText); self.name.setWordWrap(True); layout.addWidget(self.name)
        bar = QHBoxLayout(); self.buttons = {}
        for label, callback in [('上一页',lambda:self.go(self.page.value()-1)), ('下一页',lambda:self.go(self.page.value()+1)),
                                ('缩小',lambda:self.set_zoom(self.zoom/1.25)), ('放大',lambda:self.set_zoom(self.zoom*1.25)),
                                ('适应宽度',lambda:self.fit('width')), ('适应窗口',lambda:self.fit('window')),
                                ('取消',self.cancel_load)]:
            button = QPushButton(label); button.clicked.connect(callback); bar.addWidget(button); self.buttons[label] = button
        self.page = QSpinBox(); self.page.setRange(1,1); self.page.valueChanged.connect(self.go); bar.insertWidget(1,self.page)
        self.count = QLabel('/ 0'); bar.insertWidget(2,self.count)
        self.mode = QComboBox(); self.mode.addItems(['连续滚动','单页']); self.mode.currentIndexChanged.connect(self.relayout); bar.addWidget(self.mode)
        layout.addLayout(bar)
        self.status = QLabel('请选择 PDF。'); self.status.setTextFormat(Qt.TextFormat.PlainText); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.scroll = QScrollArea(); self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter); self.scroll.setAcceptDrops(False)
        self.paper = QWidget(); self.papers = QVBoxLayout(self.paper); self.papers.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.scroll.setWidget(self.paper); layout.addWidget(self.scroll,1)
        self.scroll.verticalScrollBar().valueChanged.connect(self.visible_changed)
        self.scroll.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        hint = QLabel('只读离线查看；不执行脚本、链接、表单或附件。仅供查看，不是加工尺寸依据。')
        hint.setWordWrap(True); layout.addWidget(hint)
        self.timeout = QTimer(self); self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(lambda:self.fail(self.generation,'PDF处理超过15秒，已终止；可重新选择文件。'))
        self.resize_delay = QTimer(self); self.resize_delay.setSingleShot(True); self.resize_delay.timeout.connect(self.relayout)

    @property
    def busy(self): return bool(self.workers or self.processes)

    def load_path(self, path, name=None):
        self.cancel_load(); self.closing = False
        self.name.setText(f'{name or path}\n来源：{path}')
        self.status.setText('正在后台读取PDF只读快照…')
        self.pending = (self.generation,path); self.timeout.start(15000); self.start_pending()

    def start_pending(self):
        if self.workers or self.pending is None or self.closing: return
        generation,path = self.pending; self.pending = None
        worker = PdfReadWorker(generation,path); self.workers.append(worker)
        worker.completed.connect(self.snapshot_ready); worker.failed.connect(self.fail)
        worker.finished.connect(lambda:self.reader_finished(worker)); worker.start()

    def reader_finished(self, worker):
        self.workers.remove(worker); worker.deleteLater()
        if self.closing and not self.busy: self.close()
        else: self.start_pending()

    def snapshot_ready(self, generation, data):
        if generation != self.generation or self.closing: return
        process = QProcess(self); self.process = process; self.processes[process] = None
        environment = QProcessEnvironment.systemEnvironment(); environment.insert('QT_QPA_PLATFORM','offscreen')
        environment.insert('PYTHONIOENCODING','utf-8'); process.setProcessEnvironment(environment)
        # Windows venv python.exe is a redirector: QProcess.pid may otherwise be a
        # launcher instead of the parser. Launch CPython directly, with only this
        # worktree's package and Qt installation added to isolated stdlib paths.
        root=str(Path(__file__).resolve().parents[2])
        qt_site=str(Path(next(iter(importlib.util.find_spec('PyQt6').submodule_search_locations))).parent)
        bootstrap=f'import sys,runpy;sys.path[:0]={[root,qt_site]!r};runpy.run_module("mechanical_project_namer.pdf_worker",run_name="__main__")'
        process.setProgram(getattr(sys,'_base_executable',sys.executable))
        process.setArguments(['-I','-S','-c',bootstrap]);process.setWorkingDirectory(root)
        process.started.connect(lambda:self.started(generation,process,data))
        process.readyReadStandardOutput.connect(lambda:self.receive(generation,process))
        process.readyReadStandardError.connect(lambda:process.readAllStandardError())
        process.errorOccurred.connect(lambda error:self.process_error(generation,process,error))
        process.finished.connect(lambda *_:self.process_finished(generation,process))
        self.status.setText('正在隔离进程中解析PDF…'); process.start()

    def started(self, generation, process, data):
        if generation != self.generation: process.kill(); return
        try:
            self.processes[process] = ProcessBudget(process.processId())
            process.write(packet(data))
        except Exception as exc: self.fail(generation,str(exc))

    def process_error(self, generation, process, error):
        if generation == self.generation: self.fail(generation,'PDF子进程无法启动或异常退出。')
        if error == QProcess.ProcessError.FailedToStart: self.process_finished(generation,process)

    def process_finished(self, generation, process):
        budget = self.processes.pop(process,None)
        if budget: budget.close()
        if process == self.process:
            self.process = None
            if generation == self.generation: self.fail(generation,'PDF子进程已结束，可能损坏、超时或超过内存限制。')
        process.deleteLater()
        if self.closing and not self.busy: self.close()

    def receive(self, generation, process):
        raw = bytes(process.readAllStandardOutput())
        if generation != self.generation or self.closing: return
        self.buffer.extend(raw)
        try:
            if len(self.buffer) > MAX_FRAME+4: raise PdfError('PDF响应超限。')
            for payload in frames(self.buffer):
                result = json.loads(payload)
                if result['type'] == 'error': self.fail(generation,result['message']); return
                if result['type'] == 'ready':
                    if result['pid'] != process.processId(): raise PdfError('PDF进程身份不一致，已拒绝预览。')
                    self.sizes = result['sizes']; self.diagnostics = result
                    self.page.blockSignals(True); self.page.setRange(1,len(self.sizes)); self.page.setValue(1); self.page.blockSignals(False)
                    self.count.setText('/ '+str(len(self.sizes))); self.relayout()
                elif result['type'] == 'page':
                    request,self.inflight = self.inflight,None
                    if request is None: raise PdfError('意外的PDF页面响应。')
                    page,width,height,revision = request
                    if (result['page'],result['width'],result['height']) != (page,width,height): raise PdfError('PDF页面响应不匹配。')
                    if revision == self.revision:
                        pixmap = QPixmap(); encoded = base64.b64decode(result['png'],validate=True)
                        if not pixmap.loadFromData(encoded,'PNG') or pixmap.size() != self.labels[page].size(): raise PdfError('PDF页面图像无效。')
                        self.labels[page].setPixmap(pixmap); self.cache.append(page)
                        while len(self.cache)>3:
                            old=self.cache.pop(0); self.labels[old].clear(); self.labels[old].setText(f'第{old+1}页 · 等待显示')
                        self.diagnostics.update(state='rendered',last_page=page,width=width,height=height)
                        effective=width/self.sizes[page][0]
                        limit='（已达页面像素上限）' if effective<self.zoom-.01 else ''
                        self.status.setText(f'已显示第{page+1}页 / 共{len(self.sizes)}页 · 缩放{effective:.0%}{limit} · 只读离线')
                    self.timeout.stop(); self.render_visible()
        except Exception as exc: self.fail(generation,'PDF预览失败：'+str(exc))

    def visible_pages(self):
        if not self.labels: return []
        if self.mode.currentIndex()==1: return [self.page.value()-1]
        top=self.scroll.verticalScrollBar().value(); bottom=top+self.scroll.viewport().height()
        return [i for i,label in enumerate(self.labels) if label.y() < bottom and label.y()+label.height()>top][:3]

    def visible_changed(self, *_):
        visible = self.visible_pages()
        if visible and self.mode.currentIndex()==0:
            self.page.blockSignals(True); self.page.setValue(visible[0]+1); self.page.blockSignals(False)
        self.render_visible()

    def render_visible(self):
        if self.process is None or self.inflight or not self.sizes: return
        for page in self.visible_pages():
            if page in self.cache: continue
            label = self.labels[page]; width,height = label.width(),label.height()
            self.inflight=(page,width,height,self.revision)
            self.timeout.start(15000); self.process.write(packet(dict(page=page,width=width,height=height)))
            return

    def relayout(self, *_):
        if not self.sizes: return
        self.revision += 1; self.cache.clear()
        current=self.page.value()-1
        if self.zoom_mode != 'custom':
            width,height=self.sizes[current]; available=self.scroll.viewport().size()
            self.zoom=(available.width()-32)/width
            if self.zoom_mode=='window': self.zoom=min(self.zoom,(available.height()-32)/height)
            self.zoom=max(.01,min(3.0,self.zoom))
        if not self.labels:
            for i in range(len(self.sizes)):
                label=QLabel(); label.setAlignment(Qt.AlignmentFlag.AlignCenter); label.setStyleSheet('background:white;color:#556;')
                label.setTextFormat(Qt.TextFormat.PlainText); label.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
                label.setAcceptDrops(False); self.papers.addWidget(label); self.labels.append(label)
        for i,((width,height),label) in enumerate(zip(self.sizes,self.labels)):
            scale=min(self.zoom,4096/max(width,height),math.sqrt(MAX_PIXELS/(width*height)))
            label.clear(); label.setText(f'第{i+1}页 · 等待显示'); label.setFixedSize(max(1,int(width*scale)),max(1,int(height*scale)))
            label.setVisible(self.mode.currentIndex()==0 or i==current)
        self.papers.activate(); self.paper.adjustSize()
        self.scroll.verticalScrollBar().setValue(self.labels[current].y() if self.mode.currentIndex()==0 else 0)
        QTimer.singleShot(0,self.render_visible)

    def go(self, number):
        if not self.sizes: return
        number=max(1,min(len(self.sizes),number)); self.page.blockSignals(True); self.page.setValue(number); self.page.blockSignals(False)
        if self.mode.currentIndex()==1: self.relayout()
        else: self.scroll.verticalScrollBar().setValue(self.labels[number-1].y()); self.render_visible()

    def fit(self, mode): self.zoom_mode=mode; self.relayout()
    def set_zoom(self, value): self.zoom_mode='custom'; self.zoom=max(.1,min(3.0,value)); self.relayout()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'resize_delay'): self.resize_delay.start(120)

    def clear_pages(self):
        self.sizes=[]; self.cache.clear(); self.diagnostics={}; self.count.setText('/ 0')
        for label in self.labels: self.papers.removeWidget(label); label.clear(); label.deleteLater()
        self.labels=[]; self.paper.adjustSize()

    def cancel_load(self):
        self.generation+=1; self.pending=None; self.timeout.stop(); self.resize_delay.stop()
        for worker in self.workers: worker.cancel.set()
        self.process=None
        for process in list(self.processes): process.kill()
        self.buffer.clear(); self.inflight=None; self.clear_pages(); self.status.setText('已取消；可以重新选择PDF。')

    def fail(self,generation,message):
        if generation != self.generation: return
        self.cancel_load(); self.status.setText('PDF预览失败：'+message)

    def closeEvent(self,event):
        self.closing=True; self.cancel_load()
        if self.busy: event.ignore(); self.hide()
        else: event.accept()
