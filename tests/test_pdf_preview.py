# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import json
import struct
import subprocess
import sys
from pathlib import Path
from threading import Event

import pytest
from mechanical_project_namer.pdf import MAX_FILE, MAX_FRAME, PdfError, frames, packet, read_pdf

spec=importlib.util.spec_from_file_location('pdf_demos',Path(__file__).parents[1]/'scripts/generate_pdf_demos.py')
demos=importlib.util.module_from_spec(spec); spec.loader.exec_module(demos)


@pytest.fixture
def samples(tmp_path):
    folder=tmp_path/'中文(测试) 😀'; demos.generate(folder); return folder


@pytest.fixture
def window(qtbot):
    from mechanical_project_namer.ui.pdf_window import PdfDialog
    dialog=PdfDialog(); dialog.show()
    yield dialog
    dialog.cancel_load(); qtbot.waitUntil(lambda:not dialog.busy,timeout=8000); dialog.close()


def rendered(qtbot,window,page=None):
    qtbot.waitUntil(lambda:window.diagnostics.get('state')=='rendered' and (page is None or window.diagnostics.get('last_page')==page) or '失败' in window.status.text(),timeout=18000)
    assert window.diagnostics.get('state')=='rendered',window.status.text()
    if page is not None: assert window.diagnostics['last_page']==page


def test_pdf_unicode_snapshot_is_readonly(samples):
    f=samples/'单页 工程图(演示).pdf'; before=(hashlib.sha256(f.read_bytes()).hexdigest(),f.stat().st_mtime_ns)
    assert read_pdf(f,Event())==f.read_bytes()
    assert (hashlib.sha256(f.read_bytes()).hexdigest(),f.stat().st_mtime_ns)==before


@pytest.mark.parametrize('kind',['missing','empty','wrong','oversized','cancelled','remote'])
def test_pdf_snapshot_errors(samples,kind):
    path=samples/'单页 工程图(演示).pdf'; cancel=Event()
    if kind=='missing':path=samples/'不存在.pdf'
    if kind=='empty':path=samples/'空文件.pdf'
    if kind=='wrong':path=samples/'not.pdf';path.write_bytes(b'wrong')
    if kind=='oversized':
        path=samples/'too-big.pdf'
        with path.open('wb') as stream:stream.truncate(MAX_FILE+1)
    if kind=='cancelled':cancel.set()
    if kind=='remote':path='https://invalid.example/model.pdf'
    with pytest.raises((ValueError,OSError)):read_pdf(path,cancel)


def test_pdf_ipc_handles_partial_and_multiple_frames():
    raw=packet({'a':1})+packet({'b':2}); buf=bytearray(raw[:3]);assert frames(buf)==[]
    buf.extend(raw[3:]);assert [json.loads(x) for x in frames(buf)]==[{'a':1},{'b':2}] and not buf
    with pytest.raises(PdfError):frames(bytearray(struct.pack('!I',MAX_FRAME+1)))


@pytest.mark.parametrize('name,message',[('损坏.pdf','损坏'),('加密.pdf','加密'),('超多页.pdf','200'),('页面超限.pdf','尺寸')])
def test_native_pdf_errors_are_controlled(samples,window,qtbot,name,message):
    window.load_path(samples/name)
    qtbot.waitUntil(lambda:'失败' in window.status.text(),timeout=16000)
    assert message in window.status.text() and not window.sizes
    qtbot.waitUntil(lambda:not window.busy,timeout=5000)


@pytest.mark.parametrize('name,pages',[('单页 工程图(演示).pdf',1),('多页 装配 😀.pdf',4),('空白页.pdf',1),('链接与脚本.pdf',1)])
def test_real_qtpdf_raster_loads_without_webengine(samples,window,qtbot,name,pages):
    loaded_before={key for key in sys.modules if key.startswith(('PyQt6.QtPdf','PyQt6.QtWebEngine'))}
    window.load_path(samples/name);rendered(qtbot,window)
    assert len(window.sizes)==pages and window.diagnostics['qt']=='6.11.2'
    assert window.diagnostics['pid']==window.process.processId()
    assert window.labels[0].pixmap() is not None
    assert window.processes[window.process].handle  # Windows memory/kill job attached before PDF parsing
    assert {key for key in sys.modules if key.startswith(('PyQt6.QtPdf','PyQt6.QtWebEngine'))}==loaded_before


def test_pdf_paging_scroll_zoom_and_fit(samples,window,qtbot):
    window.load_path(samples/'多页 装配 😀.pdf');rendered(qtbot,window)
    window.mode.setCurrentIndex(1);window.go(3);rendered(qtbot,window,2)
    before=window.labels[2].width();window.set_zoom(window.zoom*1.25)
    qtbot.waitUntil(lambda:2 in window.cache,timeout=10000);assert window.labels[2].width()>before
    window.fit('width');qtbot.waitUntil(lambda:2 in window.cache,timeout=10000)
    assert window.labels[2].width()<=window.scroll.viewport().width()
    window.fit('window');qtbot.waitUntil(lambda:2 in window.cache,timeout=10000)
    assert window.labels[2].height()<=window.scroll.viewport().height()
    window.mode.setCurrentIndex(0);window.go(4);rendered(qtbot,window,3)
    assert window.scroll.verticalScrollBar().value()>0 and len(window.cache)<=3


def test_switch_cancel_close_and_reopen(samples,window,qtbot):
    window.load_path(samples/'多页 装配 😀.pdf');rendered(qtbot,window)
    old=window.process
    window.load_path(samples/'损坏.pdf');window.load_path(samples/'单页 工程图(演示).pdf')
    rendered(qtbot,window);assert len(window.sizes)==1
    qtbot.waitUntil(lambda:old not in window.processes,timeout=5000)
    generation=window.generation;window.cancel_load()
    window.snapshot_ready(generation,b'%PDF-stale');assert not window.sizes and window.process is None
    qtbot.waitUntil(lambda:not window.busy,timeout=5000)
    window.load_path(samples/'空白页.pdf');rendered(qtbot,window)


def test_native_parser_timeout_keeps_ui_responsive(samples,window,qtbot,monkeypatch):
    from PyQt6.QtCore import QProcess,QTimer
    original=QProcess.setArguments
    monkeypatch.setattr(QProcess,'setArguments',lambda proc,args:original(proc,['-c','import time;time.sleep(30)']))
    ticks=[];timer=QTimer();timer.timeout.connect(lambda:ticks.append(1));timer.start(10)
    window.load_path(samples/'单页 工程图(演示).pdf')
    qtbot.waitUntil(lambda:window.process is not None and window.process.processId()>0,timeout=5000)
    window.timeout.start(100)
    qtbot.waitUntil(lambda:'超过15秒' in window.status.text(),timeout=3000)
    qtbot.waitUntil(lambda:not window.busy,timeout=3000)
    timer.stop();assert len(ticks)>=3


def test_pdf_budget_failure_fails_closed(samples,window,qtbot,monkeypatch):
    import mechanical_project_namer.ui.pdf_window as module
    def denied(pid):raise PdfError('测试资源限制不可用')
    monkeypatch.setattr(module,'ProcessBudget',denied)
    window.load_path(samples/'单页 工程图(演示).pdf')
    qtbot.waitUntil(lambda:'资源限制不可用' in window.status.text(),timeout=6000)
    assert not window.sizes


def test_worker_rejects_unbounded_render_request(samples):
    raw=packet((samples/'单页 工程图(演示).pdf').read_bytes())+packet(dict(page=0,width=100000,height=100000))
    result=subprocess.run([sys.executable,'-m','mechanical_project_namer.pdf_worker'],input=raw,capture_output=True,timeout=15)
    responses=[json.loads(f) for f in frames(bytearray(result.stdout))]
    assert responses[0]['type']=='ready' and responses[1]['type']=='error' and result.returncode==1


def test_windows_job_enforces_native_memory_limit():
    from mechanical_project_namer.pdf import ProcessBudget
    code="""import sys
sys.stdin.buffer.read(1)
try:
 data=bytearray(600*1024*1024)
except MemoryError:
 print('allocation-refused',flush=True)
else:
 raise AssertionError('job failed to enforce its budget')
"""
    process=subprocess.Popen([sys._base_executable,'-I','-S','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    budget=None
    try:
        budget=ProcessBudget(process.pid)
        output,error=process.communicate(b'x',timeout=8)
        assert process.returncode==0 and b'allocation-refused' in output,error.decode(errors='replace')
    finally:
        if budget:budget.close()
        if process.poll() is None:process.kill();process.wait(5)


def test_pdf_preview_routing_reuses_project_identity(samples,qtbot,tmp_path):
    from mechanical_project_namer.data import Database,Repository
    from mechanical_project_namer.projects import ProjectService
    from mechanical_project_namer.project_files import ProjectFileCatalog,Source
    from mechanical_project_namer.ui.project_files_page import ProjectFilesPage
    db=Database(tmp_path/'isolated.db');db.migrate();repo=Repository(db)
    project=ProjectService(repo).create('虚构PDF设备','','DEMO')
    catalog=ProjectFileCatalog(repo);catalog.commit(catalog.scan(project.id,[Source(str(samples/'单页 工程图(演示).pdf'),'file')]))
    page=ProjectFilesPage(repo);qtbot.addWidget(page);page.load_projects(project.id);page.table.selectRow(0);page.preview_selected()
    window=page.pdf_window
    try:
        assert page.preview_window is None;rendered(qtbot,window)
        assert repo.get_project(project.id).next_number==1 and not repo.list_records()
        assert len(repo.list_projects(project_status='all'))==1
    finally:
        window.cancel_load();qtbot.waitUntil(lambda:not window.busy,timeout=5000)


def test_pdf_missing_dependency_and_base_import_are_lazy(samples,qtbot,tmp_path,monkeypatch):
    from mechanical_project_namer.data import Database,Repository
    from mechanical_project_namer.ui.project_files_page import ProjectFilesPage
    import mechanical_project_namer.ui.pdf_window as module
    original=module.importlib.util.find_spec
    monkeypatch.setattr(module.importlib.util,'find_spec',lambda name:None if name=='PyQt6.QtPdf' else original(name))
    db=Database(tmp_path/'isolated.db');db.migrate();page=ProjectFilesPage(Repository(db));qtbot.addWidget(page)
    page.rows=[dict(extension='.pdf')];page.table.setRowCount(1);page.table.selectRow(0);page.preview_selected()
    assert '可选预览依赖' in page.status.text() and page.pdf_window is None and page.open_button.isEnabled()
    code="""import sys,importlib.abc
class Deny(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path=None,target=None):
  if name.startswith(('PyQt6.QtPdf','PyQt6.QtWebEngine')):raise ImportError('optional deliberately absent')
sys.meta_path.insert(0,Deny())
import mechanical_project_namer.app
assert not any(x.startswith(('PyQt6.QtPdf','PyQt6.QtWebEngine')) for x in sys.modules)
"""
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr.decode(errors='replace')
