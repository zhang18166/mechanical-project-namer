# SPDX-License-Identifier: GPL-3.0-only
"""Lazy offline snapshot UI. No viewer, CAD kernel or network dependencies."""
from pathlib import Path
import json
from threading import Event
from PyQt6.QtCore import Qt,QThread,QTimer,pyqtSignal
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPlainTextEdit,
    QPushButton,QFileDialog,QTableWidget,QTableWidgetItem,QComboBox,QAbstractItemView,QHeaderView,QSizePolicy)
from ..project_files import ProjectFileCatalog,STATE_LABELS
from ..snapshots import SnapshotService,role_for
from .common import FlowLayout

LABELS={'source':'源文件','exchange':'中性交换','preview':'GLB预览副本','drawing':'PDF查看副本','attachment':'附件'}
STATES={'prepared':'待执行','copying':'复制未完成','sealed':'已封存待提交','committed':'已提交待登记',
        'complete':'已完成','failed':'失败/可重试','cancelled':'已终止'}
INTEGRITY={'unverified':'未校验','verified':'上次字节校验通过','failed':'完整性失败'}


class SnapshotWorker(QThread):
    progress=pyqtSignal(str)
    completed=pyqtSignal(object)
    failed=pyqtSignal(str)
    def __init__(self,operation,parent):
        super().__init__(parent);self.operation=operation;self.cancel=Event()
    def run(self):
        try:self.completed.emit(self.operation(self.cancel,self.progress.emit))
        except Exception as exc:self.failed.emit(f'{type(exc).__name__}：{exc}')


class SnapshotsDialog(QDialog):
    def __init__(self,repository,project_id,parent=None,selected_id=None,*,store=None):
        super().__init__(parent);self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.repository=repository;self.project_id=project_id;self.service=SnapshotService(repository,store)
        self.worker=None;self.closing=False;self.rows=[];self.jobs=[];self.file_rows=[]
        self.setWindowTitle('本地版本与恢复');self.resize(1120,850)
        layout=QVBoxLayout(self)
        self.title=QLabel();self.title.setTextFormat(Qt.TextFormat.PlainText);self.title.setWordWrap(True);layout.addWidget(self.title)
        note=QLabel('仅归档选中的文件；创建前请停止编辑。逐文件复制不是整套CAD同一时刻快照。装配依赖及预览对应关系均未验证；.prt来源默认未知。')
        note.setWordWrap(True);layout.addWidget(note)
        bar=QHBoxLayout();self.root=QLineEdit();self.root.setPlaceholderText('选定项目根目录（保留其下相对目录）')
        choose=QPushButton('选择项目根');choose.clicked.connect(self.choose_root);bar.addWidget(self.root,1);bar.addWidget(choose);layout.addLayout(bar)
        attach=QPushButton('添加明确选择的附件 / 原生文件');attach.clicked.connect(self.attach);layout.addWidget(attach)
        self.files=QTableWidget(0,5);self.files.setHorizontalHeaderLabels(['选择 / 文件','大小','状态','文件角色','软件来源（可标注）'])
        self.files.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch);self.files.setMaximumHeight(270)
        self.files.verticalHeader().setDefaultSectionSize(40)
        for column,width in [(1,110),(2,120),(3,150),(4,180)]:self.files.setColumnWidth(column,width)
        layout.addWidget(self.files)
        self.summary=QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary);self.files.itemChanged.connect(self.update_summary)
        bar=QHBoxLayout();self.label=QLineEdit();self.label.setPlaceholderText('版本标签，例如 V1')
        self.note=QLineEdit();self.note.setPlaceholderText('修改说明');bar.addWidget(self.label);bar.addWidget(self.note,1);layout.addLayout(bar)
        self.required=QLineEdit();self.required.setPlaceholderText('额外声明的必需相对路径，用 ; 分隔；未选中/缺失时拒绝完整文件集')
        layout.addWidget(self.required)
        bar=QHBoxLayout();self.create_button=QPushButton('创建本地版本');self.cancel_button=QPushButton('取消当前操作')
        self.create_button.clicked.connect(self.create_version);self.cancel_button.clicked.connect(self.cancel_operation)
        bar.addWidget(self.create_button);bar.addWidget(self.cancel_button);layout.addLayout(bar)
        layout.addWidget(QLabel('版本历史（包含未完成任务；仅“已完成 + 字节校验通过”列为完整文件集）'))
        self.history=QTableWidget(0,5);self.history.setHorizontalHeaderLabels(['版本标签','归档状态','字节完整性','装配依赖 / 预览对应','说明 / 失败原因'])
        self.history.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.history.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.history.horizontalHeader().setSectionResizeMode(4,QHeaderView.ResizeMode.Stretch)
        for column,width in [(0,120),(1,145),(2,180),(3,180)]:self.history.setColumnWidth(column,width)
        layout.addWidget(self.history,1)
        bar=FlowLayout();self.actions={}
        for text,fn in [('刷新版本历史',self.refresh),('查看任务记录',self.show_events),('校验',self.verify_selected),('恢复到新目录',self.restore_selected),('恢复归档任务',self.resume_selected),('终止归档任务',self.abort_selected)]:
            button=QPushButton(text);button.clicked.connect(fn);bar.addWidget(button);self.actions[text]=button
        layout.addLayout(bar)
        self.target=QLineEdit();self.target.setPlaceholderText('恢复到全新的独立目录，父目录必须已存在（不覆盖非空或空的已有目录）')
        bar=QHBoxLayout();bar.addWidget(self.target,1);browse=QPushButton('选择恢复父目录');browse.clicked.connect(self.choose_target);bar.addWidget(browse);layout.addLayout(bar)
        self.restores=QComboBox();self.restores.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.restores.setMinimumContentsLength(20);self.restores.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);layout.addWidget(self.restores)
        bar=QHBoxLayout();resume=QPushButton('恢复未完成的恢复任务');resume.clicked.connect(self.resume_restore);bar.addWidget(resume)
        abort=QPushButton('终止恢复任务');abort.clicked.connect(self.abort_restore);bar.addWidget(abort);layout.addLayout(bar)
        details=QPushButton('查看恢复任务记录');details.clicked.connect(lambda:self.show_events(True));bar.addWidget(details)
        self.status=QLabel('本地归档，不联网。重启后未完成任务会列在历史中，可恢复或终止；不会自动删除暂存或历史。')
        self.status.setTextFormat(Qt.TextFormat.PlainText);self.status.setWordWrap(True);layout.addWidget(self.status)
        self.load_files(selected_id);self.refresh()
        pending=sum(r['state']!='complete' for r in self.rows+self.jobs)
        if pending:self.status.setText(f'发现{pending}个未完成/终止/失败任务；选择相应任务后可恢复或终止。不会自动重写文件或删除暂存。')

    @property
    def busy(self):return self.worker is not None

    def choose_root(self):
        if self.busy:return
        path=QFileDialog.getExistingDirectory(self,'选定项目根')
        if path:self.root.setText(path)

    def choose_target(self):
        if self.busy:return
        path=QFileDialog.getExistingDirectory(self,'选择恢复父目录，程序将创建全新子目录')
        if path:self.target.setText(str(Path(path)/('恢复-'+__import__('uuid').uuid4().hex[:8])))

    def attach(self):
        if self.busy:return
        paths,_=QFileDialog.getOpenFileNames(self,'明确选择附件（不解析、不执行、不推断.prt软件来源）')
        if paths:self.start(lambda cancel,progress:ProjectFileCatalog(self.repository).register_attachments(self.project_id,paths),reload_files=True)

    def load_files(self,selected_id=None):
        self.file_rows=ProjectFileCatalog(self.repository).list_files(self.project_id)
        self.files.blockSignals(True);self.files.setRowCount(len(self.file_rows))
        for i,row in enumerate(self.file_rows):
            item=QTableWidgetItem(row['name']);item.setToolTip(row['path']);item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if selected_id is None or selected_id==row['file_id'] else Qt.CheckState.Unchecked)
            self.files.setItem(i,0,item)
            for col,value in [(1,str(row['file_size'])),(2,STATE_LABELS[row['state']])]:
                item=QTableWidgetItem(value);item.setFlags(Qt.ItemFlag.ItemIsEnabled);self.files.setItem(i,col,item)
            role=QComboBox()
            for key,label in LABELS.items():role.addItem(label,key)
            role.setCurrentIndex(list(LABELS).index(role_for(row['path'])));role.currentIndexChanged.connect(self.update_summary);self.files.setCellWidget(i,3,role)
            self.files.setItem(i,4,QTableWidgetItem('未知'))
        self.files.blockSignals(False)
        project=self.repository.get_project(self.project_id)
        if not self.root.text() and project.directory:self.root.setText(project.directory)
        self.update_summary()

    def choices(self):
        return [dict(file_id=r['file_id'],role=self.files.cellWidget(i,3).currentData(),software=self.files.item(i,4).text())
            for i,r in enumerate(self.file_rows) if self.files.item(i,0).checkState()==Qt.CheckState.Checked]

    def update_summary(self,*_):
        indexes=[i for i in range(len(self.file_rows)) if self.files.item(i,0).checkState()==Qt.CheckState.Checked]
        roles={self.files.cellWidget(i,3).currentData() for i in indexes};missing=sum(self.file_rows[i]['state']!='present' for i in indexes)
        self.summary.setText(f"索引选中{len(indexes)}项 / {sum(self.file_rows[i]['file_size'] for i in indexes):,}字节；不可用{missing}项（创建时重新检查）。"+
            ('没有GLB预览副本。' if 'preview' not in roles else '')+('没有PDF查看副本。' if 'drawing' not in roles else '')+'装配依赖未验证；预览对应未验证。')

    def refresh(self,*_):
        self.title.setText('项目：'+self.repository.get_project(self.project_id).name+' · 稳定ID '+str(self.project_id))
        self.rows=self.service.history(self.project_id);self.history.setRowCount(len(self.rows))
        for i,r in enumerate(self.rows):
            roles={f['role'] for f in json.loads(r['request_json'])['files']}
            extra=(' 无GLB预览。' if 'preview' not in roles else '')+(' 无PDF副本。' if 'drawing' not in roles else '')
            for col,text in enumerate((r['label'],STATES.get(r['state'],r['state']),INTEGRITY.get(r['integrity'],r['integrity']),'未验证 / 未验证',(r['error'] or r['note'])+extra)):
                item=QTableWidgetItem(text);item.setToolTip(text);self.history.setItem(i,col,item)
        self.jobs=self.service.restores(self.project_id);self.restores.clear()
        for r in self.jobs:self.restores.addItem(f"{r['restore_id'][:8]} · {STATES.get(r['state'],r['state'])} · {r['destination']} · {r['error']}",r['restore_id'])

    def start(self,operation,*,reload_files=False):
        if self.busy:return
        self.worker=SnapshotWorker(operation,self);self.worker.progress.connect(self.status.setText)
        self.worker.completed.connect(lambda result:self.status.setText('操作完成（仅本地）。'+('字节级恢复已完成；CAD可打开性未验证。' if isinstance(result,dict) and 'restore_id' in result else '')))
        self.worker.failed.connect(lambda message:self.status.setText('操作未成功：'+message))
        self.worker.finished.connect(lambda:self.finished(reload_files));self.create_button.setEnabled(False);self.files.setEnabled(False);self.worker.start()

    def finished(self,reload_files):
        worker,self.worker=self.worker,None;worker.deleteLater();self.create_button.setEnabled(True);self.files.setEnabled(True)
        if reload_files:self.load_files()
        self.refresh()
        if self.closing:QTimer.singleShot(0,self.close)

    def create_version(self):
        if self.busy:return
        root,choices,label,note,required=self.root.text(),self.choices(),self.label.text(),self.note.text(),[r.strip() for r in self.required.text().split(';') if r.strip()]
        def operation(cancel,progress):
            task=self.service.prepare(self.project_id,root,choices,label,note,required=required,cancel=cancel,progress=progress)
            return self.service.run(task,cancel=cancel,progress=progress)
        self.start(operation)

    def selected(self):
        i=self.history.currentRow()
        if 0<=i<len(self.rows):return self.rows[i]
        self.status.setText('请先选择一个历史版本。')

    def verify_selected(self):
        row=self.selected()
        if row:self.start(lambda cancel,progress:self.service.verify(row['task_id'],cancel=cancel))
    def restore_selected(self):
        row=self.selected();target=self.target.text()
        if row:self.start(lambda cancel,progress:self.service.restore(row['task_id'],target,cancel=cancel,progress=progress))
    def resume_selected(self):
        row=self.selected()
        if row:self.start(lambda cancel,progress:self.service.run(row['task_id'],cancel=cancel,progress=progress))
    def abort_selected(self):
        row=self.selected()
        if row:self.start(lambda cancel,progress:self.service.abort(row['task_id']))
    def resume_restore(self):
        key=self.restores.currentData()
        if key:self.start(lambda cancel,progress:self.service.resume_restore(key,cancel=cancel,progress=progress))
    def abort_restore(self):
        key=self.restores.currentData()
        if key:self.start(lambda cancel,progress:self.service.abort(key,restore=True))
    def show_events(self,restore=False):
        if self.busy:return
        row=None if restore else self.selected();key=self.restores.currentData() if restore else row['task_id'] if row else None
        if not key:return
        window=QDialog(self);window.setWindowTitle('本地任务记录（最近500项；完整事件仍在本地数据库）');window.resize(850,500)
        layout=QVBoxLayout(window);text=QPlainTextEdit();text.setReadOnly(True)
        text.setPlainText('\n'.join(f"{r['created_at']} {STATES.get(r['state'],r['state'])} {json.dumps(json.loads(r['details']),ensure_ascii=False)}" for r in self.service.events(key,restore=restore)))
        layout.addWidget(text);window.exec()
    def cancel_operation(self):
        if self.worker:self.worker.cancel.set();self.status.setText('正在请求取消；已提交的版本不会被删除或伪报取消。')
    def closeEvent(self,event):
        if self.busy:self.closing=True;self.cancel_operation();self.hide();event.ignore()
        else:event.accept()
