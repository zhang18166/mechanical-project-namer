# SPDX-License-Identifier: GPL-3.0-only
"""Lazy experimental publishing UI; network and hashing stay off the GUI thread."""
import json
from pathlib import Path
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QFileDialog,QPlainTextEdit
from .snapshots_dialog import SnapshotWorker
from ..publish_client import Client, error_code
from ..publish_ledger import PublishLedger
from ..publish_protocol import ProtocolError, safe

ERRORS = {
    'http-403-forbidden':'身份未获此项目或操作授权',
    'http-409-project-busy':'同项目已有未完成任务，请查询或明确终止旧任务',
    'http-409-storage-limit':'测试服务容量或剩余空间不足',
    'http-408-transfer-timeout':'传输等待超时，可查询后重试',
    'service-identity':'服务身份与配置不符，拒绝连接',
    'version-unavailable':'正式版本不可用；保留历史，禁止下载或覆盖',
    'transfer-interrupted-retry':'传输未完成，可重试同一任务',
    'transport-interrupted-retry':'连接中断，可查询状态后重试',
    'task-cancelled-explicit-restart-required':'任务已终止；须明确重新发起，不能复活原任务',
    'new-directory-required':'下载必须选择尚不存在的新目录',
    'download-space':'下载空间不足',
    'output-overlap':'下载目录与受保护实例重叠',
}


class PublishDialog(QDialog):
    def __init__(self,snapshots,task,parent=None):
        super().__init__(parent)
        self.snapshots,self.task=snapshots,task
        self.worker=None;self.remote=None;self.closing=False;self.pending_cancel=False
        self.ledger=PublishLedger(snapshots);self.active_key=None
        self.setWindowTitle('发布到本机隔离测试服务');self.resize(920,560)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel('仅127.0.0.1隔离测试；不连接腾讯云/飞牛。中断传输保留任务；终止任务不可复活。'))
        self.config=QLineEdit();self.config.setPlaceholderText('仓库外测试身份配置JSON路径')
        self.config.textChanged.connect(self.config_changed);layout.addWidget(self.config)
        choose=QPushButton('选择测试身份配置');choose.clicked.connect(self.choose);layout.addWidget(choose)
        self.identity=QLabel('尚未连接；无当前服务健康确认');self.identity.setWordWrap(True);layout.addWidget(self.identity)
        self.history=QLabel('无本目标历史确认');self.history.setWordWrap(True);layout.addWidget(self.history)
        self.target=QLineEdit();self.target.setPlaceholderText('下载到尚不存在的新目录');layout.addWidget(self.target)
        bar=QHBoxLayout()
        for text,fn in [('连接/查询版本',self.query),('发布/重试',self.publish),('终止服务任务',self.cancel_remote),('下载已提交版本',self.download)]:
            button=QPushButton(text);button.clicked.connect(fn);bar.addWidget(button)
        layout.addLayout(bar)
        restart=QPushButton('为已终止版本明确重新发起新任务');restart.clicked.connect(lambda:self.publish(restart=True));layout.addWidget(restart)
        self.status=QLabel('本地完整版本；等待测试配置。');self.status.setWordWrap(True);layout.addWidget(self.status)
        stop=QPushButton('中断本次传输（保留同一任务，以便文件级重试）');stop.clicked.connect(self.stop);layout.addWidget(stop)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);layout.addWidget(self.details)

    def client(self):
        path=safe(self.config.text())
        if any((p/'.git').exists() for p in path.parents):
            raise ProtocolError('external-config-required')
        return Client(json.loads(path.read_bytes()),config_ref=str(path))

    def config_changed(self):
        self.remote=None
        self.identity.setText('尚未进行当前连接验证')
        try:
            client=self.client();row=self.snapshots._get(self.task)
            self.active_key=self.ledger.key(client.config,self.snapshots.store_id,row['version_id'])
            old=self.ledger.get(self.active_key)
            if old:
                self.remote=old['task']
                self.history.setText(f'上次记录：{old["state"]}；上次服务确认时间戳 {old["confirmed_at"]}（不是当前健康确认）')
            else:
                self.history.setText('本目标尚无发布记录；其他服务成功不代表本服务已发布')
        except Exception:
            self.history.setText('配置尚未就绪；不读取正式凭据')

    def choose(self):
        path,_=QFileDialog.getOpenFileName(self,'选择仓库外测试身份配置','','JSON (*.json)')
        if path:self.config.setText(path)

    def start(self,operation):
        if self.worker or self.closing:return
        try:
            client=self.client()
        except Exception:
            self.status.setText('配置无效；仅使用维护脚本生成的仓库外测试配置。');return
        self.identity.setText(f'目标服务 {client.config["service"]} / 项目 {client.config["project"]}；当前连接待验证')
        self.worker=SnapshotWorker(lambda cancel,progress:operation(client,cancel,progress),self)
        self.config.setEnabled(False);self.target.setEnabled(False)
        self.worker.progress.connect(self.status.setText)
        self.worker.completed.connect(self.done)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.status.setText('等待传输/连接中…');self.worker.start()

    def failed(self,message):
        candidate=message.split('：',1)[-1]
        code=error_code(ProtocolError(candidate))
        if code=='local-operation-failed':code='transport-interrupted-retry'
        self.status.setText('失败/未完成：'+ERRORS.get(code,'操作未完成，请核对权限或查询后重试')+' ['+code+']')
        self.identity.setText('本次连接/完整性验证未成功；历史确认不代表当前健康')

    def done(self,result):
        if isinstance(result,dict) and result.get('task'):
            self.remote=result['task']
            healthy=result.get('healthy',False)
            self.status.setText('测试服务端已提交且当前校验健康' if result['state']=='complete' and healthy else ('正式版本不可用' if result['state']=='complete' else result['state']))
            self.identity.setText('当前服务响应已核验；字节完整性与CAD依赖/预览对应分别判断')
        else:
            self.status.setText('查询/下载校验完成')
            self.identity.setText('本次连接验证成功；版本健康以本次查询结果为准')
        self.details.setPlainText(json.dumps(result,ensure_ascii=False,indent=2))
        if self.active_key:
            old=self.ledger.get(self.active_key)
            if old:self.history.setText(f'上次记录：{old["state"]}；服务确认时间戳 {old["confirmed_at"]}')

    def finished(self):
        worker,self.worker=self.worker,None
        worker.deleteLater()
        self.config.setEnabled(True);self.target.setEnabled(True)
        if self.closing:QTimer.singleShot(0,self.close)
        elif self.pending_cancel:
            self.pending_cancel=False;QTimer.singleShot(0,self.cancel_remote)

    def query(self):
        def operation(client,cancel,progress):
            info=client.info();row=self.snapshots._get(self.task)
            key=self.ledger.key(client.config,self.snapshots.store_id,row['version_id']);old=self.ledger.get(key)
            if old:
                state=client.request('GET','/tasks/'+old['task'])
                if state['project']!=client.config['project'] or state['version']!=row['version_id']:
                    raise ProtocolError('task-binding')
                self.ledger.update(key,'unavailable' if state['state']=='complete' and not state['healthy'] else state['state'],confirmation=state)
                return state
            return dict(info=info,binding={'store':self.snapshots.store_id,'local_project':row['project_id'],'remote_project':client.config['project']},versions=client.versions(),local_state='not-published-to-this-target')
        self.start(operation)

    def publish(self,checked=False,*,restart=False):
        self.start(lambda client,cancel,progress:client.publish(self.snapshots,self.task,cancel=cancel,progress=progress,restart_cancelled=restart))

    def cancel_remote(self):
        if self.worker:
            self.pending_cancel=True;self.stop();return
        def operation(client,cancel,progress):
            row=self.snapshots._get(self.task)
            key=self.ledger.key(client.config,self.snapshots.store_id,row['version_id']);old=self.ledger.get(key)
            if not old:raise ProtocolError('no-task')
            state=client.cancel(old['task']);self.ledger.update(key,state['state'],confirmation=state,code='user-terminated')
            return state
        self.start(operation)

    def download(self):
        target=self.target.text()
        def operation(client,cancel,progress):
            row=self.snapshots._get(self.task);key=self.ledger.key(client.config,self.snapshots.store_id,row['version_id'])
            old=self.ledger.get(key)
            if not old:raise ProtocolError('no-task')
            return client.download(old['task'],target,cancel=cancel,progress=progress)
        self.start(operation)

    def stop(self):
        if self.worker:
            self.worker.cancel.set()
            self.status.setText('正在中断本次传输；保留已确认文件，等待连接结束后核对服务状态。')

    def closeEvent(self,event):
        if self.worker:
            self.closing=True;self.worker.cancel.set();event.ignore()
        else:event.accept()
