# SPDX-License-Identifier: GPL-3.0-only
"""Standalone maintenance UI: never opens a default or damaged application database."""
from pathlib import Path
import sys
import uuid
from PyQt6.QtCore import QProcess, Qt, QTimer
from PyQt6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                            QPushButton, QPlainTextEdit, QVBoxLayout)
from ..library_backups import LibraryBackups
from .snapshots_dialog import SnapshotWorker


class BackupsDialog(QDialog):
    def __init__(self, profile='', parent=None, *, service=None):
        super().__init__(parent)
        self.service=service or LibraryBackups(); self.worker=None; self.closing=False; self.retry=None; self.restored=None;self.path_buttons=[]
        self.setWindowTitle('离线归档库 · 备份与恢复'); self.resize(1000,690)
        layout=QVBoxLayout(self)
        banner=QLabel('本地未加密备份 · 私有数据，不上传。完整范围：全部完整归档版本及配套业务数据库，不包含未归档工作文件。')
        banner.setWordWrap(True); layout.addWidget(banner)
        info=QLabel('创建前请从托盘退出所有源客户端，完成或明确终止未完成任务。使用跨进程维护锁及SQLite写入冻结；正在使用则拒绝。验证/恢复不要求旧数据库可用。')
        info.setWordWrap(True);layout.addWidget(info)
        self.profile=self.field(layout,'源隔离实例（仅创建需要）',profile,'选择源实例',True)
        self.destination=self.field(layout,'新备份目录（尚不存在）','','选择备份父目录',False,'备份-')
        self.backup=self.field(layout,'已有备份集（验证/恢复）','','选择备份集',True)
        self.target=self.field(layout,'恢复到新实例目录（尚不存在）','','选择恢复父目录',False,'恢复实例-')
        bar=QHBoxLayout();self.buttons={}
        for text,fn in [('查看备份范围',self.inspect),('创建完整备份',self.create_backup),('验证备份',self.verify_backup),('整体恢复到新目录',self.restore_backup),('重试上次任务',self.retry_last),('载入任务日志',self.load_task)]:
            button=QPushButton(text);button.clicked.connect(fn);bar.addWidget(button);self.buttons[text]=button
        layout.addLayout(bar)
        bar=QHBoxLayout();self.cancel_button=QPushButton('取消当前操作');self.cancel_button.clicked.connect(self.cancel_operation);bar.addWidget(self.cancel_button)
        self.launch_button=QPushButton('启动恢复后的独立实例');self.launch_button.clicked.connect(self.launch);self.launch_button.setEnabled(False);bar.addWidget(self.launch_button);layout.addLayout(bar)
        self.status=QLabel('就绪；只使用隔离数据。不覆盖已有目录，不自动切换正式实例或删除旧备份。')
        self.status.setTextFormat(Qt.TextFormat.PlainText);self.status.setWordWrap(True);layout.addWidget(self.status)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);layout.addWidget(self.details,1)
        self.command=QLineEdit();self.command.setReadOnly(True);self.command.setPlaceholderText('恢复成功后显示新实例启动命令');layout.addWidget(self.command)

    def field(self, layout, label, value, caption, existing, prefix=''):
        layout.addWidget(QLabel(label));bar=QHBoxLayout();edit=QLineEdit(str(value));button=QPushButton(caption)
        def choose():
            path=QFileDialog.getExistingDirectory(self,caption)
            if path:edit.setText(path if existing else str(Path(path)/(prefix+uuid.uuid4().hex[:8])))
        button.clicked.connect(choose);self.path_buttons.append(button);bar.addWidget(edit,1);bar.addWidget(button);layout.addLayout(bar);return edit

    @property
    def busy(self):return self.worker is not None

    def start(self, kind, operation):
        if self.busy:return
        self.worker=SnapshotWorker(operation,self);self.worker.progress.connect(self.status.setText)
        self.worker.completed.connect(lambda result:self.completed(kind,result));self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        for button in [*self.buttons.values(),*self.path_buttons]:button.setEnabled(False)
        for edit in (self.profile,self.destination,self.backup,self.target):edit.setEnabled(False)
        self.launch_button.setEnabled(False);self.status.setText('后台执行中；可取消，已提交结果不会被删除。');self.worker.start()

    def completed(self, kind, result):
        try:self.render_completed(kind,result)
        except Exception as exc:self.failed('结果显示失败，不会自动启动实例：'+str(exc))

    def render_completed(self, kind, result):
        self.status.setText('操作完成（本地未加密、仅隔离实例）。')
        if kind=='inspect':
            self.details.setPlainText(f"完整版本：{result['versions']}；归档文件：{result['archive_files']}；估算{result['estimated_bytes']:,}字节。\n范围：{result['scope']}\n排除："+'；'.join(result['exclusions']))
        elif kind in ('create','verify'):
            self.details.setPlainText(f"备份ID：{result['backup_id']}\n完整版本：{len(result['versions'])}；文件：{result['file_count']}；{result['total_bytes']:,}字节\n字节与数据库关联校验通过；CAD装配依赖/预览对应关系仍未验证。\n排除："+'；'.join(result['exclusions']))
            if kind=='create':self.backup.setText(self.destination.text())
        elif kind=='restore':
            self.restored=result['new_root'];literal=lambda value:"'"+str(value).replace("'","''")+"'"
            self.command.setText('& '+literal(sys.executable)+' -m mechanical_project_namer --dev --dev-root '+literal(self.restored))
            self.command.setToolTip(self.command.text());self.command.setCursorPosition(0)
            self.details.setPlainText(f"恢复到：{self.restored}\n逻辑归档库身份：{result['logical_store_id']}\n项目ID、版本UUID、编号/历史/计数器保留；位置已受控重绑定。\n外部工作文件待重新关联，不读取旧位置。字节级恢复完成，真实CAD打开/装配依赖/预览对应未验证。\n不会自动启动或切换正式实例。")

    def failed(self, message):
        self.status.setText('操作未成功：'+message);self.details.appendPlainText('失败记录：'+message+'\n暂存和任务日志保留；重启可使用同任务ID重试。')
        if self.retry:self.details.appendPlainText('任务ID：'+self.retry[2])

    def finished(self):
        worker,self.worker=self.worker,None;worker.deleteLater()
        for button in [*self.buttons.values(),*self.path_buttons]:button.setEnabled(True)
        for edit in (self.profile,self.destination,self.backup,self.target):edit.setEnabled(True)
        self.launch_button.setEnabled(self.restored is not None)
        if self.closing:QTimer.singleShot(0,self.close)

    def inspect(self):
        profile=self.profile.text();self.start('inspect',lambda cancel,progress:self.service.inspect(profile,cancel=cancel))
    def create_backup(self):self.run_task('create',str(uuid.uuid4()))
    def verify_backup(self):
        path=self.backup.text();self.start('verify',lambda cancel,progress:self.service.verify(path,cancel=cancel,progress=progress))
    def restore_backup(self):self.run_task('restore',str(uuid.uuid4()))
    def run_task(self, kind, task_id):
        source=self.profile.text() if kind=='create' else self.backup.text()
        target=self.destination.text() if kind=='create' else self.target.text()
        self.retry=(kind,(source,target),task_id);self.details.appendPlainText('任务ID：'+task_id)
        fn=self.service.create if kind=='create' else self.service.restore
        self.start(kind,lambda cancel,progress:fn(source,target,task_id=task_id,cancel=cancel,progress=progress))
    def retry_last(self):
        if self.retry:
            kind,(source,target),task_id=self.retry
            if kind=='create':self.profile.setText(source);self.destination.setText(target)
            else:self.backup.setText(source);self.target.setText(target)
            self.run_task(kind,task_id)
    def load_task(self):
        folder=QFileDialog.getExistingDirectory(self,'选择 .mw-library-job-任务ID 目录（私有日志）')
        if not folder:return
        try:
            from ..backup_io import read_metadata
            path=Path(folder)/'owner.json'
            if path.stat().st_size>65536:raise ValueError('任务归属文件过大。')
            owner=read_metadata(path)[0]
            if owner['format']!=1 or owner['kind'] not in ('backup','restore'):raise ValueError('任务格式无效。')
            self.retry=('create' if owner['kind']=='backup' else 'restore',(owner['source'],owner['target']),owner['task_id'])
            self.details.appendPlainText('已载入任务：'+owner['task_id']+'；点击“重试上次任务”，核心仍会核验归属和冻结范围。')
        except Exception as exc:self.failed(str(exc))
    def cancel_operation(self):
        if self.worker:self.worker.cancel.set();self.status.setText('正在请求取消；已提交的结果不会伪报取消或被删除。')
    def launch(self):
        if self.restored and not self.busy:
            ok,_=QProcess.startDetached(sys.executable,['-m','mechanical_project_namer','--dev','--dev-root',self.restored],str(Path(__file__).resolve().parents[2]))
            self.status.setText('新隔离实例已启动。' if ok else '新实例未启动，请使用下方命令并保留错误记录。')
    def closeEvent(self,event):
        if self.busy:self.closing=True;self.cancel_operation();event.ignore()
        else:event.accept()
