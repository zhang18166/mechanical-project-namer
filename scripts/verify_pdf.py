# SPDX-License-Identifier: GPL-3.0-only
"""Real visible Windows GUI acceptance; all documents and databases are fictional."""
import argparse, ctypes, gc, hashlib, json, os, socket, sys, time, traceback
from pathlib import Path
from ctypes import wintypes
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--samples',type=Path,help='Manually reviewed COPIES only; no source discovery or copying')
args=parser.parse_args();EVIDENCE=args.output.resolve();REAL=args.samples is not None
if EVIDENCE.exists() or EVIDENCE.is_relative_to(ROOT) or EVIDENCE.parent==EVIDENCE:
    raise ValueError('Choose a new evidence directory outside the repository')
for variable,default in [('LOCALAPPDATA',Path.home()/'AppData/Local'),('APPDATA',Path.home()/'AppData/Roaming')]:
    protected=(Path(os.environ.get(variable,default))/'MechanicalProjectNamer').resolve()
    if EVIDENCE.is_relative_to(protected) or protected.is_relative_to(EVIDENCE):raise ValueError('Production profile forbidden')
EVIDENCE.mkdir(parents=True)
if REAL:
    samples=args.samples.resolve()
    if not samples.is_dir() or samples.is_relative_to(ROOT):raise ValueError('Use an approved external copy directory')
    for variable,default in [('LOCALAPPDATA',Path.home()/'AppData/Local'),('APPDATA',Path.home()/'AppData/Roaming')]:
        protected=(Path(os.environ.get(variable,default))/'MechanicalProjectNamer').resolve()
        if samples.is_relative_to(protected) or protected.is_relative_to(samples):raise ValueError('Production profile forbidden')
else:
    from scripts.generate_pdf_demos import generate
    samples=EVIDENCE/'models';generate(samples)
    with (samples/'超限.pdf').open('wb') as stream:stream.truncate(32*1024*1024+1)
from PyQt6.QtCore import QPoint, QTimer, Qt, qVersion
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtTest import QTest
import mechanical_project_namer.app as module
from mechanical_project_namer.project_files import Source

result={'passed':False,'kind':'real' if REAL else 'generated','pid':os.getpid(),'qt':qVersion(),'samples':[],'cycles':[],'actions':[]};holder={}
old_shell=module.ApplicationShell

def memory(pid):
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(x,ctypes.c_size_t) for x in ['peak','working','qpeakPaged','qPaged','qpeakNonPaged','qNonPaged','pagefile','peakpagefile','private']]
    kernel=ctypes.WinDLL('kernel32');kernel.OpenProcess.restype=wintypes.HANDLE;kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x410,False,int(pid));info=Counters();info.cb=ctypes.sizeof(info)
    if not handle:return None
    try:
        psapi=ctypes.WinDLL('psapi');psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(handle,ctypes.byref(info),info.cb):return None
        return dict(working_bytes=info.working,private_bytes=info.private)
    finally:kernel.CloseHandle(handle)

def wait(condition,seconds=20):
    end=time.monotonic()+seconds
    while not condition():
        if time.monotonic()>end:raise AssertionError('GUI acceptance timed out')
        QTest.qWait(25)

def rendered(window,page=None):
    wait(lambda:window.diagnostics.get('state')=='rendered' and (page is None or window.diagnostics.get('last_page')==page) or '失败' in window.status.text())
    assert window.diagnostics.get('state')=='rendered',window.status.text()
    if page is not None:assert window.diagnostics['last_page']==page

def stamp():
    return {p.name:[hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns] for p in samples.glob('*.pdf')}

def business(repo):
    with repo.database.connect() as c:
        return {name:[tuple(row) for row in c.execute('SELECT * FROM '+name)] for name in ['projects','project_modules','drawing_records','number_reservations','part_sequence_counters']}

def denied(*args,**kwargs):
    result['actions'].append(repr(args));raise AssertionError('PDF attempted an external action')

def factory(*args,**kwargs):
    shell=old_shell(*args,**kwargs);holder['shell']=shell;QTimer.singleShot(400,exercise);return shell

def real_samples(shell):
    from mechanical_project_namer.pdf import MAX_FILE
    result['before_memory']=memory(os.getpid());before=business(shell.repository);original=stamp()
    shell.main_window.project_files_button.click();page=shell.project_files_page
    paths=sorted(samples.glob('*.pdf'))
    assert paths,'No manually approved PDF copies'
    page.start_scan([Source(str(path),'file') for path in paths]);wait(lambda:page.worker is None)
    QDesktopServices.openUrl=denied;socket.socket.connect=denied;socket.create_connection=denied
    for number,path in enumerate(paths,1):
        record=dict(id=f'R{number:02}',name=path.name,bytes=path.stat().st_size,rounds=[])
        for cycle in range(2):
            page.search.setText(path.name);assert page.table.rowCount()==1;page.table.selectRow(0)
            start=time.monotonic();page.preview_button.click();window=page.pdf_window;holder['window']=window
            if path.stat().st_size>MAX_FILE:
                wait(lambda:'失败' in window.status.text());assert '32 MiB' in window.status.text()
                record.update(result='LIMIT_REJECTED',message=window.status.text(),load_ms=(time.monotonic()-start)*1000,pages=None)
            else:
                rendered(window);record.update(result='RENDERED',pages=len(window.sizes),page_sizes=window.sizes)
                record['rounds'].append(dict(first_page_ms=(time.monotonic()-start)*1000,main_memory=memory(os.getpid()),worker_memory=memory(window.process.processId())))
                if cycle==0:window.grab().save(str(EVIDENCE/(record['id']+'-first.png')))
                window.mode.setCurrentIndex(1)
                if len(window.sizes)>1:
                    QTest.mouseClick(window.buttons['下一页'],Qt.MouseButton.LeftButton);rendered(window,1)
                window.go(min(3,len(window.sizes)));rendered(window,window.page.value()-1)
                current=window.page.value()-1
                window.set_zoom(2.0);wait(lambda:current in window.cache)
                if cycle==0:window.grab().save(str(EVIDENCE/(record['id']+'-zoom.png')))
                QTest.mouseClick(window.buttons['适应宽度'],Qt.MouseButton.LeftButton);wait(lambda:current in window.cache)
                assert window.labels[current].width()<=window.scroll.viewport().width()
                QTest.mouseClick(window.buttons['适应窗口'],Qt.MouseButton.LeftButton);wait(lambda:current in window.cache)
                assert window.labels[current].height()<=window.scroll.viewport().height()
                window.mode.setCurrentIndex(0);QTest.qWait(100)
                window.scroll.verticalScrollBar().setValue(window.scroll.verticalScrollBar().maximum())
                wait(lambda:len(window.sizes)-1 in window.cache)
                record.update(zoom=True,paging=True,continuous=True)
            old_pids=[proc.processId() for proc in window.processes]
            window.close();wait(lambda:page.pdf_window is None)
            for pid in old_pids:wait(lambda:memory(pid) is None,5)
            gc.collect();QTest.qWait(150)
            record['rounds'].append(dict(after_close_memory=memory(os.getpid()),worker_pids_exited=True))
        result['samples'].append(record)
    result.update(after_close_memory=memory(os.getpid()),files_unchanged=stamp()==original,business_unchanged=business(shell.repository)==before)
    assert result['files_unchanged'] and result['business_unchanged'] and not result['actions']
    result['passed']=True

def exercise():
    shell=holder['shell'];window=None
    try:
        if REAL:return real_samples(shell)
        result['before_memory']=memory(os.getpid())
        before=business(shell.repository);original=stamp()
        shell.main_window.project_files_button.click();page=shell.project_files_page
        page.start_scan([Source(str(samples),'directory')]);wait(lambda:page.worker is None)
        links=(samples/'链接与脚本.pdf').read_bytes();assert b'/JavaScript' in links and b'/URI' in links
        # This process has no permitted network or URL opener. Native QtPdf only rasterizes memory.
        QDesktopServices.openUrl=denied;socket.socket.connect=denied;socket.create_connection=denied

        def select(name):
            page.search.setText(name);assert page.table.rowCount()==1
            page.table.selectRow(0);start=time.monotonic();page.preview_button.click()
            w=page.pdf_window;holder['window']=w;rendered(w)
            result['samples'].append(dict(name=name,bytes=(samples/name).stat().st_size,pages=len(w.sizes),
                first_page_ms=(time.monotonic()-start)*1000,pid=w.process.processId(),worker_memory=memory(w.process.processId()),main_memory=memory(os.getpid())))
            return w

        window=select('单页 工程图(演示).pdf');assert window.labels[0].pixmap() is not None
        assert not any(key.startswith(('PyQt6.QtPdf','PyQt6.QtWebEngine')) for key in sys.modules)
        window.grab().save(str(EVIDENCE/'single.png'))
        window=select('多页 装配 😀.pdf');window.mode.setCurrentIndex(1)
        QTest.mouseClick(window.buttons['下一页'],Qt.MouseButton.LeftButton);rendered(window,1)
        window.grab().save(str(EVIDENCE/'page2.png'))
        width=window.labels[1].width();QTest.mouseClick(window.buttons['放大'],Qt.MouseButton.LeftButton)
        wait(lambda:1 in window.cache);assert window.labels[1].width()>width
        QTest.mouseClick(window.buttons['缩小'],Qt.MouseButton.LeftButton);wait(lambda:1 in window.cache)
        QTest.mouseClick(window.buttons['适应宽度'],Qt.MouseButton.LeftButton);wait(lambda:1 in window.cache)
        assert window.labels[1].width()<=window.scroll.viewport().width()
        QTest.mouseClick(window.buttons['适应窗口'],Qt.MouseButton.LeftButton);wait(lambda:1 in window.cache)
        assert window.labels[1].height()<=window.scroll.viewport().height()
        window.mode.setCurrentIndex(0);QTest.qWait(150);window.scroll.verticalScrollBar().setValue(window.scroll.verticalScrollBar().maximum())
        wait(lambda:window.page.value()>=3);wait(lambda:3 in window.cache)
        window.grab().save(str(EVIDENCE/'continuous.png'))
        result['paging_zoom_scroll_fit']=True
        window.resize(850,650);QTest.qWait(300);wait(lambda:bool(window.cache))
        window=select('空白页.pdf');assert len(window.sizes)==1
        window=select('链接与脚本.pdf');label=window.labels[0]
        QTest.mouseClick(label,Qt.MouseButton.LeftButton,pos=QPoint(int(200*window.zoom),int(120*window.zoom)));QTest.qWait(200)
        assert not result['actions'];result['active_content_inert']=True
        window.load_path(samples/'多页 装配 😀.pdf');old_generation=window.generation
        window.load_path(samples/'单页 工程图(演示).pdf');rendered(window)
        window.snapshot_ready(old_generation,b'%PDF-stale');assert len(window.sizes)==1
        window.cancel_load();assert not window.diagnostics and not window.labels
        for name in ['损坏.pdf','加密.pdf','空文件.pdf','不存在.pdf','超限.pdf','超多页.pdf','页面超限.pdf']:
            window.load_path(samples/name);wait(lambda:'失败' in window.status.text());assert not window.sizes
        for cycle in range(5):
            if page.pdf_window is not None:
                old_pids=[proc.processId() for proc in page.pdf_window.processes]
                page.pdf_window.close();wait(lambda:page.pdf_window is None)
                for pid in old_pids:wait(lambda:memory(pid) is None,5)
            gc.collect();QTest.qWait(100)
            window=select('多页 装配 😀.pdf');window.mode.setCurrentIndex(1);window.go(4);rendered(window,3)
            result['cycles'].append(dict(index=cycle,pid=window.process.processId(),main_memory=memory(os.getpid()),worker_memory=memory(window.process.processId())))
        final_pid=window.process.processId();window.close();wait(lambda:page.pdf_window is None);wait(lambda:memory(final_pid) is None,5)
        gc.collect();QTest.qWait(500)
        result.update(after_close_memory=memory(os.getpid()),files_unchanged=stamp()==original,business_unchanged=business(shell.repository)==before)
        assert result['files_unchanged'] and result['business_unchanged'] and not result['actions']
        result['passed']=True
    except BaseException:result['error']=traceback.format_exc()
    finally:
        (EVIDENCE/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k in ['passed','error','pid','qt']},ensure_ascii=True))
        if holder.get('window'):
            try:holder['window'].close()
            except RuntimeError:pass
        shell.quit()

module.ApplicationShell=factory
sys.argv=[str(ROOT/'launcher.py'),'--dev','--dev-root',str(EVIDENCE/'isolated-profile')]
code=module.main();raise SystemExit(0 if result['passed'] and code==0 else 1)
