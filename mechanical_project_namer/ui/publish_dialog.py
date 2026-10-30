# SPDX-License-Identifier: GPL-3.0-only
import json,uuid
from pathlib import Path
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QFileDialog,QPlainTextEdit
from .snapshots_dialog import SnapshotWorker
from ..publish_client import Client
class PublishDialog(QDialog):
    def __init__(self,snapshots,task,parent=None):
        super().__init__(parent);self.snapshots=snapshots;self.task=task;self.worker=None;self.remote=None;self.closing=False
        self.setWindowTitle('发布到本机隔离测试服务');self.resize(850,480);l=QVBoxLayout(self)
        l.addWidget(QLabel('仅回环测试；不会上传腾讯云/飞牛。配置须由维护脚本预置授权映射，令牌不显示。'))
        self.config=QLineEdit();self.config.setPlaceholderText('仓库外客户端配置JSON路径');l.addWidget(self.config)
        b=QPushButton('选择测试身份配置');b.clicked.connect(self.choose);l.addWidget(b)
        self.target=QLineEdit();self.target.setPlaceholderText('下载到尚不存在的新目录');l.addWidget(self.target)
        bar=QHBoxLayout()
        for text,fn in [('连接/查询版本',self.query),('发布/重试',self.publish),('取消服务任务',self.cancel_remote),('下载已提交版本',self.download)]:
            b=QPushButton(text);b.clicked.connect(fn);bar.addWidget(b)
        l.addLayout(bar);self.status=QLabel('本地完整版本；等待明确授权的测试服务配置。');self.status.setWordWrap(True);l.addWidget(self.status)
        stop=QPushButton('中断本次传输（保留服务端任务以便重试）');stop.clicked.connect(self.stop);l.addWidget(stop)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);l.addWidget(self.details)
    def choose(self):
        f,_=QFileDialog.getOpenFileName(self,'选择仓库外测试身份配置','','JSON (*.json)')
        if f:self.config.setText(f)
    def start(self,operation):
        if self.worker or self.closing:return
        try:client=Client(json.loads(Path(self.config.text()).read_bytes()))
        except Exception:self.status.setText('配置无效；请使用维护脚本生成的仓库外配置。');return
        self.worker=SnapshotWorker(lambda cancel,progress:operation(client,cancel,progress),self)
        self.worker.progress.connect(self.status.setText);self.worker.completed.connect(self.done)
        self.worker.failed.connect(lambda _:self.status.setText('失败/取消；可重试，服务端实际状态需重新查询。'))
        self.worker.finished.connect(self.finished);self.status.setText('等待传输/连接中…');self.worker.start()
    def done(self,result):
        if isinstance(result,dict) and result.get('task'):self.remote=result['task'];self.status.setText('测试服务端已提交' if result['state']=='complete' else result['state'])
        else:self.status.setText('查询/下载校验完成')
        self.details.setPlainText(json.dumps(result,ensure_ascii=False,indent=2))
    def finished(self):
        w,self.worker=self.worker,None;w.deleteLater()
        if self.closing:QTimer.singleShot(0,self.close)
    def query(self):self.start(lambda c,e,p:dict(info=c.info(),binding={'store':self.snapshots.store_id,'local_project':self.snapshots._get(self.task)['project_id'],'remote_project':c.config['project']},versions=c.versions()))
    def publish(self):
        def operation(c,e,p):
            row=self.snapshots._get(self.task)
            self.remote=str(uuid.uuid5(uuid.UUID(c.config['service']),c.config['project']+self.snapshots.store_id+row['version_id']))
            return c.publish(self.snapshots,self.task,cancel=e,progress=p)
        self.start(operation)
    def cancel_remote(self):
        if self.remote:self.start(lambda c,e,p:c.cancel(self.remote))
    def download(self):
        if self.remote:self.start(lambda c,e,p:c.download(self.remote,self.target.text(),cancel=e,progress=p))
    def stop(self):
        if self.worker:self.worker.cancel.set()
    def closeEvent(self,event):
        if self.worker:self.closing=True;self.worker.cancel.set();event.ignore()
        else:event.accept()
