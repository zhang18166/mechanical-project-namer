# SPDX-License-Identifier: GPL-3.0-only
"""Optional WebEngine window. Imported only when the user requests preview."""
import json
import os
from threading import Event
from urllib.parse import urlsplit
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor
from PyQt6.QtWebEngineWidgets import QWebEngineView
from ..paths import local_data_dir
from ..preview import read_glb, ResourceServer, PreviewError, ASSETS


class OfflineInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, server, parent=None):
        super().__init__(parent); self.server=server; self.blocked=[]; self.allowed=[]

    def interceptRequest(self, info):
        url=info.requestUrl().toString()
        # blob is needed only for in-memory embedded textures, never filesystem data.
        safe=self.server.allowed(url) or url.startswith('blob:'+self.server.origin+'/')
        if safe:self.allowed.append(url)
        else:self.blocked.append(url);info.block(True)


class LockedPage(QWebEnginePage):
    message=pyqtSignal(object)
    def __init__(self, profile, server, parent):
        super().__init__(profile,parent);self.server=server

    def acceptNavigationRequest(self,url,kind,is_main):
        return url.toString()=='about:blank' or self.server.allowed(url.toString())

    def createWindow(self,_kind):return None

    def javaScriptConsoleMessage(self,level,message,line,source):
        if message.startswith('MPN_PREVIEW:'):
            try:self.message.emit(json.loads(message[len('MPN_PREVIEW:'):]))
            except (ValueError,TypeError):pass


class ReadWorker(QThread):
    ready=pyqtSignal(int,object)
    failed=pyqtSignal(int,str)
    def __init__(self,generation,path):
        super().__init__();self.generation=generation;self.path=path;self.cancel=Event()
    def run(self):
        try:
            if Path(self.path).suffix.lower()=='.glb':model=read_glb(self.path,self.cancel)
            else:
                from ..cad import read_cad_isolated
                model=read_cad_isolated(self.path,self.cancel)
            self.ready.emit(self.generation,model)
        except Exception as exc:self.failed.emit(self.generation,f'{type(exc).__name__}：{exc}')


class PreviewDialog(QDialog):
    def __init__(self,parent=None):
        if not (ASSETS/'o3dv.min.js').is_file():raise PreviewError('本地查看器资源缺失，请运行 scripts/setup_preview.ps1。')
        flags=os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS','')
        if 'QTWEBENGINE_DISABLE_SANDBOX' in os.environ or any(x in flags for x in ('--no-sandbox','--disable-web-security','--ignore-certificate-errors','--single-process')):
            raise PreviewError('检测到不安全的WebEngine环境参数；拒绝启动预览。')
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('本地三维预览 · GLB / STL / STEP');self.resize(1080,760)
        self.generation=0;self.workers=[];self.closing=False;self.disposed=False
        self.diagnostics={};self.snapshot=None
        self.pending=None
        self.render_budget=None;self.render_budget_pid=None
        self.server=ResourceServer()
        layout=QVBoxLayout(self)
        self.name=QLabel();self.name.setTextFormat(Qt.TextFormat.PlainText);self.name.setWordWrap(True);layout.addWidget(self.name)
        toolbar=QHBoxLayout();self.buttons={}
        for label,command in [('适配','fit'),('重置视角','iso'),('正视','front'),('俯视','top'),('轴测','iso')]:
            b=QPushButton(label);b.clicked.connect(lambda checked=False,c=command:self.command(c));toolbar.addWidget(b);self.buttons[label]=b
        cancel=QPushButton('取消');cancel.clicked.connect(self.cancel_load);toolbar.addWidget(cancel)
        layout.addLayout(toolbar)
        self.status=QLabel('请选择GLB');self.status.setTextFormat(Qt.TextFormat.PlainText);self.status.setWordWrap(True);layout.addWidget(self.status)
        self.view=QWebEngineView(self);self.view.setAcceptDrops(False);layout.addWidget(self.view,1)
        self.profile=QWebEngineProfile(self)
        cache=local_data_dir()/'cache'/'preview'/self.server.secret
        self.profile.setCachePath(str(cache/'cache'));self.profile.setPersistentStoragePath(str(cache/'storage'))
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self.interceptor=OfflineInterceptor(self.server,self.profile);self.profile.setUrlRequestInterceptor(self.interceptor)
        self.profile.downloadRequested.connect(lambda request:request.cancel())
        self.page=LockedPage(self.profile,self.server,self.view);self.view.setPage(self.page)
        self.page.permissionRequested.connect(lambda permission:permission.deny())
        settings=self.page.settings()
        for key in ('LocalContentCanAccessFileUrls','LocalContentCanAccessRemoteUrls','JavascriptCanOpenWindows','JavascriptCanAccessClipboard','HyperlinkAuditingEnabled','DnsPrefetchEnabled','PluginsEnabled','PdfViewerEnabled','AllowRunningInsecureContent'):
            settings.setAttribute(getattr(QWebEngineSettings.WebAttribute,key),False)
        self.page.message.connect(self._message)
        self.page.renderProcessTerminated.connect(lambda *_:self._error(self.generation,'渲染进程结束，请关闭后重开。'))
        self.timeout=QTimer(self);self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(lambda:self._error(self.generation,'预览超时，请简化模型后重试或检查WebGL。'))
        hint=QLabel('仅供查看，不是加工尺寸依据。左键旋转 · 滚轮缩放 · 右键平移。仅从本地读取，不上传。')
        hint.setWordWrap(True);layout.addWidget(hint)

    @property
    def busy(self):return bool(self.workers)  # include queued finished signals before QObject teardown

    def load_path(self,path,name=None):
        self.cancel_load();self.closing=False
        self.name.setText(f'{name or path}\n来源：{path}')
        self.status.setText('正在隔离检查模型及依赖…')
        self.pending=(self.generation,path)
        self.timeout.start(45000 if Path(path).suffix.lower() in ('.step','.stp') else 20000);self._start_pending()

    def _start_pending(self):
        if self.workers or self.pending is None or self.closing:return
        generation,path=self.pending;self.pending=None
        worker=ReadWorker(generation,path);self.workers.append(worker)
        worker.ready.connect(self._ready);worker.failed.connect(self._error)
        worker.finished.connect(lambda:self._finished(worker))
        worker.start()

    def cancel_load(self):
        self.generation+=1;self.timeout.stop();self.pending=None
        for worker in self.workers:worker.cancel.set()
        self.snapshot=None;self.diagnostics={};self.server.revoke()
        self.view.stop();self.view.setUrl(QUrl('about:blank'))
        self.status.setText('已取消，模型资源已清除。')

    def _ready(self,generation,snapshot):
        if generation!=self.generation or self.closing:return
        self.snapshot=snapshot
        self.status.setText('检查通过，正在本地渲染…')
        try:self.view.setUrl(QUrl(self.server.publish(generation,snapshot)))
        except Exception as exc:self._error(generation,str(exc))

    def _error(self,generation,message):
        if generation!=self.generation or self.closing:return
        self.cancel_load();self.status.setText('预览失败：'+message)

    def _message(self,data):
        if data.get('generation')!=self.generation or self.closing:return
        if data.get('state')=='error':self._error(self.generation,data.get('message','加载失败'));return
        if data.get('state')=='cad-ready' and getattr(self.snapshot,'format','')=='step':
            try:
                from ..pdf import ProcessBudget
                pid=int(self.page.renderProcessPid())
                if pid!=self.render_budget_pid:
                    if self.render_budget:self.render_budget.close()
                    self.render_budget=ProcessBudget(pid,1024);self.render_budget_pid=pid
                self.timeout.start(30000);self.page.runJavaScript('window.startCadPreview()')
            except Exception as exc:self._error(self.generation,'无法建立STEP资源隔离：'+str(exc))
            return
        if data.get('state')=='rendered' and data.get('triangles',0)>0:
            self.timeout.stop();self.diagnostics=data
            self.status.setText(f"模型已显示 · {data['triangles']} 个绘制三角面 · 首帧 {data['loadMs']:.0f} ms")
            metadata=getattr(self.snapshot,'metadata',{})
            if metadata.get('source')=='STL':self.status.setText(self.status.text()+f" · STL单位未知；重算法向{metadata['normal_repairs']}面，略去退化{metadata['degenerate_omitted']}面")
            elif metadata.get('source')=='STEP':self.status.setText(self.status.text()+f" · {metadata['protocol']} · {data.get('cad',{}).get('parts',0)}实体 · 标准化毫米；三角网格不等于精确CAD测量")

    def command(self,command):
        if command in ('fit','iso','front','top') and self.diagnostics:
            self.page.runJavaScript('window.previewCommand('+json.dumps(command)+')')

    def _finished(self,worker):
        self.workers.remove(worker);worker.deleteLater()
        if self.closing and not self.busy:QTimer.singleShot(0,self.close)
        else:self._start_pending()

    def closeEvent(self,event):
        if not self.closing:self.closing=True;self.cancel_load()
        if self.busy:event.ignore();self.hide();return
        if not self.disposed:
            self.disposed=True;self.server.close();self.view.stop()
            self.page.deleteLater();self.profile.deleteLater()
            if self.render_budget:self.render_budget.close();self.render_budget=None
        event.accept()
