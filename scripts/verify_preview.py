# SPDX-License-Identifier: GPL-3.0-only
import os,sys,json,time,hashlib,traceback,ctypes,gc
from pathlib import Path
from ctypes import wintypes
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import argparse
parser=argparse.ArgumentParser(description='Windows real GLB rendering acceptance; generated data only')
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();EVIDENCE=args.output.resolve();run='verification'
if EVIDENCE.exists() or EVIDENCE.is_relative_to(ROOT) or EVIDENCE.parent==EVIDENCE:
 raise ValueError('Choose a new empty directory outside the source tree')
for variable,default in [('LOCALAPPDATA',Path.home()/'AppData/Local'),('APPDATA',Path.home()/'AppData/Roaming')]:
 protected=(Path(os.environ.get(variable,default))/'MechanicalProjectNamer').resolve()
 if EVIDENCE.is_relative_to(protected) or protected.is_relative_to(EVIDENCE):raise ValueError('Cannot write acceptance artifacts in production profile')
EVIDENCE.mkdir(parents=True)
from scripts.generate_preview_demos import generate
generate(EVIDENCE/'models')
from PyQt6.QtCore import QTimer,QPoint,QPointF,Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
import mechanical_project_namer.app as module
from mechanical_project_namer.project_files import Source
from mechanical_project_namer.development import development_root
old_shell=module.ApplicationShell
result={'passed':False,'run':run,'samples':[],'cycles':[]};holder={}
def wait(condition,seconds=22):
 deadline=time.monotonic()+seconds
 while not condition():
  if time.monotonic()>deadline:raise AssertionError('GUI wait timed out')
  QTest.qWait(30)
def js(window,expression):
 answer=[];window.page.runJavaScript(expression,lambda value:answer.append(value));wait(lambda:bool(answer),4);return answer[0]
def diagnostic(w):return js(w,'window.previewDiagnostics ? JSON.parse(JSON.stringify(window.previewDiagnostics())) : null')
def memory(pid):
 class Counters(ctypes.Structure):
  _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(x,ctypes.c_size_t) for x in ['peak','working','qpeakPaged','qPaged','qpeakNonPaged','qNonPaged','pagefile','peakpagefile','private']]
 kernel=ctypes.WinDLL('kernel32');kernel.OpenProcess.restype=wintypes.HANDLE;kernel.CloseHandle.argtypes=[wintypes.HANDLE]
 h=kernel.OpenProcess(0x410,False,int(pid));info=Counters();info.cb=ctypes.sizeof(info)
 if not h:return None
 try:
  psapi=ctypes.WinDLL('psapi');psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
  if not psapi.GetProcessMemoryInfo(h,ctypes.byref(info),info.cb):return None
  return {'working_bytes':info.working,'private_bytes':info.private}
 finally:kernel.CloseHandle(h)
def stamp(paths):return {p.name:[hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns] for p in paths}
def state(repo):
 with repo.database.connect() as c:return {n:[tuple(r) for r in c.execute('SELECT * FROM '+n)] for n in ['projects','drawing_records','number_reservations','part_sequence_counters']}
def factory(*a,**k):
 shell=old_shell(*a,**k);holder['shell']=shell;QTimer.singleShot(400,exercise);return shell
def exercise():
 shell=holder['shell']
 try:
  from PyQt6.QtCore import QT_VERSION_STR,PYQT_VERSION_STR
  from PyQt6.QtWebEngineCore import qWebEngineChromiumVersion,qWebEngineVersion
  result.update(pid=os.getpid(),qt=QT_VERSION_STR,pyqt=PYQT_VERSION_STR,webengine=qWebEngineVersion(),chromium=qWebEngineChromiumVersion(),before_memory=memory(os.getpid()))
  shell.main_window.project_files_button.click();page=shell.project_files_page
  samples=EVIDENCE/'models';names=['简单 几何体(红).glb','多零件 装配 😀.glb','受控复杂 环体.glb'];paths=[samples/n for n in names]
  original=stamp(paths);before=state(shell.repository)
  page.start_scan([Source(str(samples),'directory')]);wait(lambda:page.worker is None)
  def select(name):
   page.search.setText(name);assert page.table.rowCount()==1;page.table.selectRow(0);page.preview_button.click()
   w=page.preview_window;wait(lambda:bool(w.diagnostics) or '失败' in w.status.text())
   assert w.diagnostics,w.status.text();return w
  w=select(names[0]);holder['window']=w
  initial=diagnostic(w);target=w.view.focusProxy();center=target.rect().center()
  QTest.mousePress(target,Qt.MouseButton.LeftButton,pos=center)
  QTest.mouseMove(target,center+QPoint(100,65),100);QTest.mouseRelease(target,Qt.MouseButton.LeftButton,pos=center+QPoint(100,65));QTest.qWait(200)
  rotated=diagnostic(w);assert rotated['camera']!=initial['camera'],'rotation did not change camera'
  QTest.mousePress(target,Qt.MouseButton.RightButton,pos=center);QTest.mouseMove(target,center+QPoint(50,30),100);QTest.mouseRelease(target,Qt.MouseButton.RightButton,pos=center+QPoint(50,30));QTest.qWait(200)
  panned=diagnostic(w);assert panned['camera']['center']!=rotated['camera']['center'],'pan did not change center'
  QTest.mouseMove(target,center,60)
  js(w,"window.wheels=[];window.addEventListener('wheel', e=>window.wheels.push({y:e.deltaY,tag:e.target.tagName}),true);true")
  wheel=QWheelEvent(QPointF(center),QPointF(target.mapToGlobal(center)),QPoint(),QPoint(0,240),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
  shell.app.sendEvent(target,wheel);QTest.qWait(500);zoomed=diagnostic(w)
  if zoomed['camera']==panned['camera']:
   result['wheel_debug']=js(w,'window.wheels');result['wheel_target']=str(target.metaObject().className())
  assert zoomed['camera']!=panned['camera'],'zoom did not change camera'
  views={}
  for label in ['正视','俯视','轴测','适配','重置视角']:
   w.buttons[label].click();QTest.qWait(120);views[label]=diagnostic(w)['camera']
  assert views['正视']!=views['俯视'] and views['轴测']==views['重置视角']
  old_size=diagnostic(w);w.resize(920,650);QTest.qWait(200);assert diagnostic(w)['width']!=old_size['width']
  result['interactions']=dict(rotation=True,pan=True,zoom=True,views=views,resize=True)
  # CSP and interceptor remain active; these requests may not escape the component.
  js(w,"window.probe=[]; Promise.all(['https://example.invalid/never','file:///C:/never-preview-canary'].map(u=>fetch(u).then(()=>window.probe.push('escaped')).catch(()=>window.probe.push('blocked')))); true")
  wait(lambda:js(w,'window.probe.length')==2,5);assert js(w,'window.probe')==['blocked','blocked']
  result['external_requests_blocked']=True
  for name,expected in zip(names,[12,48,9216]):
   start=time.monotonic();w=select(name);QTest.qWait(150)
   assert w.diagnostics['triangles']==expected
   record=dict(name=name,bytes=(samples/name).stat().st_size,expected_triangles=expected,render=w.diagnostics,
               wall_ms=(time.monotonic()-start)*1000,main_memory=memory(os.getpid()),renderer_memory=memory(w.page.renderProcessPid()),
               off_the_record=w.profile.isOffTheRecord(),cache_type=w.profile.httpCacheType().name,allowed=len(w.interceptor.allowed))
   result['samples'].append(record);w.grab().save(str(EVIDENCE/(run+'-'+str(expected)+'.png')))
  # Real fast switches, cancellation, invalid file, and stale callbacks.
  w.load_path(str(paths[2]));old_generation=w.generation;w.load_path(str(paths[0]));wait(lambda:bool(w.diagnostics));assert w.diagnostics['triangles']==12
  w._message({'generation':old_generation,'state':'rendered','triangles':999});assert w.diagnostics['triangles']==12
  w.cancel_load();QTest.qWait(80);assert not w.diagnostics and w.snapshot is None
  for name in ['损坏.glb','外部资源.glb','不存在.glb']:
   w.load_path(str(samples/name));wait(lambda:'失败' in w.status.text());assert not w.diagnostics
  large=samples/'超限.glb'
  with large.open('wb') as stream:stream.truncate(32*1024*1024+1)
  w.load_path(str(large));wait(lambda:'失败' in w.status.text());assert not w.diagnostics
  from PyQt6.QtWebEngineCore import QWebEngineSettings
  w.page.settings().setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled,False)
  w.load_path(str(paths[0]));wait(lambda:'失败' in w.status.text());assert not w.diagnostics
  result['webgl_unavailable_controlled']=True
  for index in range(5):
   if page.preview_window is not None:
    old_pid=int(page.preview_window.page.renderProcessPid());page.preview_window.close();wait(lambda:page.preview_window is None)
    wait(lambda:memory(old_pid) is None,5)
   gc.collect();QTest.qWait(200)
   w=select(names[index%3]);holder['window']=w
   result['cycles'].append(dict(index=index,token=w.server.secret,pid=int(w.page.renderProcessPid()),memory=memory(os.getpid()),requests=len(w.server.requests)))
   assert w.profile.isOffTheRecord() and w.profile.httpCacheType().name=='NoCache'
  w.close();wait(lambda:page.preview_window is None);QTest.qWait(800);gc.collect()
  result.update(after_close_memory=memory(os.getpid()),files_unchanged=stamp(paths)==original,business_unchanged=state(shell.repository)==before)
  assert result['files_unchanged'] and result['business_unchanged']
  result['passed']=True
 except BaseException:result['error']=traceback.format_exc()
 finally:
  (EVIDENCE/(run+'-acceptance.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps({k:v for k,v in result.items() if k in ['passed','error','run','pid','qt','webengine','chromium']},ensure_ascii=False))
  if holder.get('window'):
   try:holder['window'].close()
   except RuntimeError:pass
  shell.quit()
module.ApplicationShell=factory
sys.argv=[str(ROOT/'launcher.py'),'--dev','--dev-root',str(EVIDENCE/('live-profile-'+run))]
code=module.main();raise SystemExit(0 if result['passed'] and code==0 else 1)
