# SPDX-License-Identifier: GPL-3.0-only
"""Development-only account/index dialog; sessions live in memory, operations in worker."""
import json
from pathlib import Path
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,
    QPlainTextEdit,QListWidget,QComboBox,QGroupBox,QCheckBox,QFileDialog)
from .snapshots_dialog import SnapshotWorker
from ..account_client import AccountSession
from ..publish_protocol import safe,ProtocolError


class AccountsDialog(QDialog):
    @property
    def busy(self):return self.worker is not None

    def __init__(self,repository,parent=None,*,snapshots=None):
        super().__init__(parent);self.repository=repository;self.snapshots=snapshots
        self.session=None;self.worker=None;self.closing=False;self.project_rows=[];self.version_rows=[]
        self.setWindowTitle('隔离测试账号与远端项目');self.resize(1050,850)
        layout=QVBoxLayout(self)
        note=QLabel('仅本机回环隔离测试；不是腾讯云部署。不保存密码/会话令牌；本地命名与离线文件不依赖登录。');note.setWordWrap(True);layout.addWidget(note)
        row=QHBoxLayout();self.connection=QLineEdit();self.connection.setPlaceholderText('公开测试连接配置JSON（只有port/service）')
        choose=QPushButton('连接配置');choose.clicked.connect(self.choose);row.addWidget(self.connection);row.addWidget(choose);layout.addLayout(row)
        row=QHBoxLayout();self.username=QLineEdit();self.username.setPlaceholderText('虚构用户名')
        self.password=QLineEdit();self.password.setEchoMode(QLineEdit.EchoMode.Password);self.password.setPlaceholderText('密码')
        self.new_password=QLineEdit();self.new_password.setEchoMode(QLineEdit.EchoMode.Password);self.new_password.setPlaceholderText('首次登录新密码')
        row.addWidget(self.username);row.addWidget(self.password);row.addWidget(self.new_password);layout.addLayout(row)
        row=QHBoxLayout();self.login_button=QPushButton('登录');self.change_button=QPushButton('修改初始密码')
        self.logout_button=QPushButton('退出登录');self.refresh_button=QPushButton('刷新授权项目')
        for b,fn in [(self.login_button,self.login),(self.change_button,self.change_password),(self.logout_button,self.logout),(self.refresh_button,self.refresh)]:
            b.clicked.connect(fn);row.addWidget(b)
        layout.addLayout(row)
        self.account_label=QLabel('未登录；本地功能仍可使用。');self.account_label.setWordWrap(True);layout.addWidget(self.account_label)
        row=QHBoxLayout();self.projects=QListWidget();self.projects.setMaximumHeight(130);self.versions=QListWidget();self.versions.setMaximumHeight(130)
        row.addWidget(self.projects);row.addWidget(self.versions);layout.addLayout(row)
        self.projects.currentRowChanged.connect(self.load_versions)
        self.local_project=QComboBox();self.local_version=QComboBox()
        for p in repository.list_projects():self.local_project.addItem(p.name,p.id)
        self.local_project.currentIndexChanged.connect(self.local_changed)
        row=QHBoxLayout();row.addWidget(QLabel('已有本地项目 / 完整版本'));row.addWidget(self.local_project);row.addWidget(self.local_version);layout.addLayout(row)
        row=QHBoxLayout();self.publish_button=QPushButton('发布选中的完整本地版本');self.publish_button.clicked.connect(self.publish)
        self.download_target=QLineEdit();self.download_target.setPlaceholderText('下载到尚不存在的新目录')
        self.download_button=QPushButton('下载选中完整版本');self.download_button.clicked.connect(self.download)
        row.addWidget(self.publish_button);row.addWidget(self.download_target);row.addWidget(self.download_button);layout.addLayout(row)
        self.admin=QGroupBox('管理员维护（虚构账号）');admin_layout=QVBoxLayout(self.admin)
        row=QHBoxLayout();self.users_button=QPushButton('列出账号');self.users_button.clicked.connect(self.list_users)
        self.target_user=QLineEdit();self.target_user.setPlaceholderText('目标用户UUID')
        self.create_name=QLineEdit();self.create_name.setPlaceholderText('新虚构用户名')
        self.role=QComboBox();self.role.addItem('设计成员','designer');self.role.addItem('只读成员','reader');self.role.addItem('管理员','admin')
        row.addWidget(self.users_button);row.addWidget(self.target_user);row.addWidget(self.create_name);row.addWidget(self.role);admin_layout.addLayout(row)
        row=QHBoxLayout();self.initial_password=QLineEdit();self.initial_password.setEchoMode(QLineEdit.EchoMode.Password);self.initial_password.setPlaceholderText('新账号/重置用临时密码（不存储）')
        self.create_button=QPushButton('创建账号');self.create_button.clicked.connect(self.create_user)
        self.disable_button=QPushButton('停用账号');self.disable_button.clicked.connect(self.disable_user)
        self.reset_button=QPushButton('重置初始密码');self.reset_button.clicked.connect(self.reset_user)
        for w in [self.initial_password,self.create_button,self.disable_button,self.reset_button]:row.addWidget(w)
        admin_layout.addLayout(row)
        row=QHBoxLayout();self.permissions={}
        for key,text in [('metadata_view','元数据'),('preview_view','GLB预览副本'),('pdf_view','PDF副本'),('source_download','源文件下载'),('version_publish','发布'),('project_manage','项目管理')]:
            check=QCheckBox(text);self.permissions[key]=check;row.addWidget(check)
        self.grant_button=QPushButton('替换项目授权');self.grant_button.clicked.connect(self.grant);row.addWidget(self.grant_button);admin_layout.addLayout(row)
        row=QHBoxLayout();self.remote_name=QLineEdit();self.remote_name.setPlaceholderText('新远端项目名称（不按同名自动合并）')
        self.project_button=QPushButton('为选中本地项目建立远端映射');self.project_button.clicked.connect(self.create_project)
        self.audit_button=QPushButton('查看授权审计');self.audit_button.clicked.connect(self.audit)
        row.addWidget(self.remote_name);row.addWidget(self.project_button);row.addWidget(self.audit_button);admin_layout.addLayout(row)
        layout.addWidget(self.admin)
        self.status=QLabel('等待测试连接。');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);layout.addWidget(self.details)
        self.controls=[choose,self.connection,self.username,self.password,self.new_password,self.login_button,self.change_button,
                       self.logout_button,self.refresh_button,self.projects,self.versions,self.local_project,self.local_version,
                       self.publish_button,self.download_button,self.download_target,self.admin]
        self.update_identity();self.local_changed()

    def local_changed(self):
        if self.worker:return
        self.local_version.clear()
        if self.snapshots:
            for r in self.snapshots.history(self.local_project.currentData(),complete_only=True):
                self.local_version.addItem(r['label'],r['task_id'])

    def choose(self):
        if self.worker or (self.session and self.session.user):return
        p,_=QFileDialog.getOpenFileName(self,'选择仅含端口和服务UUID的测试配置','','JSON (*.json)')
        if p:self.connection.setText(p)

    def require_session(self):
        if not self.session or not self.session.user:raise ProtocolError('login-required')
        return self.session

    def selected_project(self):
        i=self.projects.currentRow()
        if i<0 or i>=len(self.project_rows):raise ProtocolError('select-project')
        return self.project_rows[i]

    def start(self,operation):
        if self.worker or self.closing:return
        for w in self.controls:w.setEnabled(False)
        self.details.clear();self.status.setText('后台执行中…')
        self.worker=SnapshotWorker(lambda cancel,progress:operation(cancel,progress),self)
        self.worker.completed.connect(self.done);self.worker.failed.connect(self.failed);self.worker.progress.connect(self.status.setText);self.worker.finished.connect(self.finished);self.worker.start()

    def done(self,value):
        kind=value.get('kind') if isinstance(value,dict) else None
        if kind=='projects':
            self.project_rows=value['projects'];self.projects.blockSignals(True);self.projects.clear()
            for p in self.project_rows:self.projects.addItem(p['name']+' | '+p['id']+' | '+','.join(p['permissions']))
            self.projects.blockSignals(False);self.versions.clear();self.version_rows=[]
        elif kind=='versions':
            self.version_rows=value['versions'];self.versions.clear()
            for v in self.version_rows:self.versions.addItem(v['manifest']['label']+(' | 字节健康' if v['healthy'] else ' | 不可用')+' | CAD依赖/预览对应未验证')
        elif kind=='user-created':self.target_user.setText(value['id'])
        if kind!='login' or not (self.session and self.session.user and self.session.user['must_change']):self.password.clear()
        if kind in ('password','user-created','reset'):self.initial_password.clear();self.new_password.clear()
        self.details.setPlainText(json.dumps(value,ensure_ascii=False,indent=2))
        self.status.setText('操作完成；仅隔离测试服务结果。');self.update_identity()

    def failed(self,message):
        # Worker errors may contain local exceptions: never echo them to product UI.
        import re
        code=message.split('：',1)[-1]
        if not re.fullmatch('[a-z0-9-]{1,100}',code):code='operation-failed'
        self.status.setText('操作未完成；检查账号状态、权限或测试服务连接。['+code+']')
        if '403' in code:
            if self.session:self.session.token=None;self.session.user=None
            self.projects.clear();self.versions.clear();self.project_rows=[];self.version_rows=[]
        self.update_identity()

    def finished(self):
        worker,self.worker=self.worker,None;worker.deleteLater()
        for w in self.controls:w.setEnabled(True)
        self.update_identity()
        if self.closing:QTimer.singleShot(0,self.close)

    def update_identity(self):
        user=self.session.user if self.session else None
        if user:
            self.account_label.setText('当前账号 '+user['username']+' / '+user['id']+' / '+user['role']+('；必须修改初始密码' if user['must_change'] else '；权限以服务器当前结果为准'))
            if self.session.service_state.get('maintenance'):
                self.account_label.setText(self.account_label.text()+'；恢复维护中：普通账号关闭，须离线管理员核验放行。')
        else:self.account_label.setText('未登录；命名、本地文件、归档与离线预览仍可使用。')
        self.admin.setVisible(bool(user and user['role']=='admin' and not user['must_change']))
        self.connection.setEnabled(not self.worker and not user)
        self.login_button.setEnabled(not self.worker and not user)
        self.change_button.setEnabled(not self.worker and bool(user))
        ready=not self.worker and bool(user and not user['must_change'])
        for widget in (self.publish_button,self.download_button,self.refresh_button,self.projects,self.versions):widget.setEnabled(ready)
        maintenance=bool(self.session and self.session.service_state.get('maintenance'))
        if maintenance:
            for widget in (self.publish_button,self.download_button,self.change_button):widget.setEnabled(False)
        for widget in (self.create_button,self.disable_button,self.reset_button,self.grant_button,self.project_button):
            widget.setEnabled(bool(ready and not maintenance))

    def login(self):
        try:
            path=safe(self.connection.text())
            if any((p/'.git').exists() for p in path.parents):raise ProtocolError('external-config')
            session=AccountSession(json.loads(path.read_bytes()))
            name,secret=self.username.text(),self.password.text()
        except Exception:self.status.setText('连接配置无效；只允许仓库外测试配置。');return
        def op(cancel,progress):
            result=session.login(name,secret);self.session=session
            return dict(kind='login',**result)
        self.start(op)

    def change_password(self):
        try:session=self.require_session();old,new=self.password.text(),self.new_password.text()
        except Exception:self.status.setText('先登录测试服务。');return
        def op(cancel,progress):
            session.change_password(old,new)
            return dict(kind='password',login_required=True)
        self.start(op)

    def logout(self):
        if not self.session:return
        def op(cancel,progress):
            self.session.logout();return dict(kind='logout')
        self.projects.clear();self.versions.clear();self.project_rows=[];self.version_rows=[];self.start(op)

    def refresh(self):
        try:session=self.require_session()
        except Exception:self.status.setText('先登录测试服务。');return
        def op(cancel,progress):
            session.me();return dict(kind='projects',projects=session.projects())
        self.start(op)

    def load_versions(self,index):
        if self.worker or not 0<=index<len(self.project_rows):return
        project=self.project_rows[index];session=self.require_session()
        self.start(lambda cancel,progress:dict(kind='versions',versions=session.versions(project['id'])))

    def list_users(self):
        session=self.require_session();self.start(lambda c,p:session.request('GET','/admin/users'))

    def create_user(self):
        session=self.require_session();values=dict(username=self.create_name.text(),role=self.role.currentData(),initial_password=self.initial_password.text())
        self.start(lambda c,p:dict(kind='user-created',**session.request('POST','/admin/users',values)))

    def disable_user(self):
        session=self.require_session();key=self.target_user.text()
        self.start(lambda c,p:session.request('PATCH','/admin/users/'+key,dict(enabled=False)))

    def reset_user(self):
        session=self.require_session();key=self.target_user.text();value=self.initial_password.text()
        self.start(lambda c,p:dict(kind='reset',**session.request('POST','/admin/users/'+key+'/reset',dict(initial_password=value))))

    def grant(self):
        try:session=self.require_session();project=self.selected_project()['id']
        except Exception:self.status.setText('先登录并选择已授权项目。');return
        values=dict(user=self.target_user.text(),project=project,permissions=[k for k,v in self.permissions.items() if v.isChecked()])
        self.start(lambda c,p:session.request('POST','/admin/grants',values))

    def create_project(self):
        try:
            session=self.require_session()
            if not self.snapshots:
                from ..snapshots import SnapshotService
                self.snapshots=SnapshotService(self.repository)
            values=dict(name=self.remote_name.text(),description='虚构隔离测试项目',store=self.snapshots.store_id,local_project=self.local_project.currentData())
        except Exception:self.status.setText('选择已有本地项目及测试连接。');return
        self.start(lambda c,p:session.request('POST','/admin/projects',values))

    def audit(self):
        session=self.require_session();self.start(lambda c,p:session.request('GET','/admin/audit'))

    def publish(self):
        try:
            session=self.require_session();project=self.selected_project()
            if not self.snapshots:
                from ..snapshots import SnapshotService
                self.snapshots=SnapshotService(self.repository);self.local_changed()
                self.status.setText('已加载完整本地版本；请选择版本后再次点击发布。');return
            task=self.local_version.currentData()
            if not task:raise ProtocolError('select-complete-local-version')
            row=self.snapshots._get(task)
            if project['store']!=self.snapshots.store_id or project['local_project']!=row['project_id']:raise ProtocolError('mapping-mismatch')
        except Exception:self.status.setText('选择匹配的本地完整版本与远端项目；映射必须由管理员明确建立。');return
        self.start(lambda c,p:session.publisher(project['id']).publish(self.snapshots,task,cancel=c,progress=p))

    def download(self):
        try:
            session=self.require_session();project=self.selected_project()['id'];i=self.versions.currentRow()
            if i<0:raise ProtocolError('select-version')
            task=self.version_rows[i]['task'];target=self.download_target.text()
        except Exception:self.status.setText('选择已完成版本和新的独立下载目录。');return
        self.start(lambda c,p:session.publisher(project).download(task,target,cancel=c,progress=p))

    def closeEvent(self,event):
        if self.worker:self.closing=True;self.worker.cancel.set();event.ignore()
        else:
            if self.session:self.session.token=None;self.session.user=None
            self.password.clear();self.new_password.clear();self.initial_password.clear()
            self.projects.clear();self.versions.clear();self.project_rows=[];self.version_rows=[];self.details.clear()
            self.update_identity()
            event.accept()
