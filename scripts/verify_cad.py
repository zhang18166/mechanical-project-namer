# SPDX-License-Identifier: GPL-3.0-only
"""Visible Windows CAD acceptance and timed multi-format stability run. Own fixtures only."""
import argparse,ctypes,gc,hashlib,json,os,sys,time,traceback,subprocess
from pathlib import Path
from ctypes import wintypes
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--minutes',type=float,default=0)
args=parser.parse_args();EVIDENCE=args.output.resolve()
if EVIDENCE.exists() or EVIDENCE.is_relative_to(ROOT) or EVIDENCE.parent==EVIDENCE:raise ValueError('Use a new external output directory')
for variable,default in [('LOCALAPPDATA',Path.home()/'AppData/Local'),('APPDATA',Path.home()/'AppData/Roaming')]:
 protected=(Path(os.environ.get(variable,default))/'MechanicalProjectNamer').resolve()
 if EVIDENCE.is_relative_to(protected) or protected.is_relative_to(EVIDENCE):raise ValueError('Cannot write in production profile')
EVIDENCE.mkdir(parents=True)
from scripts.generate_preview_demos import generate as glb_generate
from scripts.generate_pdf_demos import generate as pdf_generate
samples=EVIDENCE/'cad'
generation=subprocess.run([sys.executable,str(ROOT/'scripts/generate_cad_demos.py'),str(samples)],capture_output=True,timeout=60)
(EVIDENCE/'fixture-generation.txt').write_bytes(generation.stdout+generation.stderr)
if generation.returncode:raise RuntimeError('Synthetic CAD fixture generation failed')
glb_generate(EVIDENCE/'glb');pdf_generate(EVIDENCE/'pdf')
from PyQt6.QtCore import Qt,QTimer,QPoint,QPointF
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
import mechanical_project_namer.app as module
from mechanical_project_namer.project_files import Source
result={'passed':False,'pid':os.getpid(),'samples':[],'cycles':[]};holder={};old_shell=module.ApplicationShell

def memory(pid):
 class Counters(ctypes.Structure):
  _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(x,ctypes.c_size_t) for x in ['peak','working','qpeakPaged','qPaged','qpeakNonPaged','qNonPaged','pagefile','peakpagefile','private']]
 kernel=ctypes.WinDLL('kernel32');kernel.OpenProcess.restype=wintypes.HANDLE;kernel.CloseHandle.argtypes=[wintypes.HANDLE]
 handle=kernel.OpenProcess(0x410,False,int(pid));info=Counters();info.cb=ctypes.sizeof(info)
 if not handle:return None
 try:
  api=ctypes.WinDLL('psapi');api.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
  if not api.GetProcessMemoryInfo(handle,ctypes.byref(info),info.cb):return None
  return dict(working_bytes=info.working,private_bytes=info.private,peak_working_bytes=info.peak)
 finally:kernel.CloseHandle(handle)

def wait(condition,seconds=50):
 end=time.monotonic()+seconds
 while not condition():
  if time.monotonic()>end:raise AssertionError('CAD GUI timed out')
  QTest.qWait(25)

def rendered(window):
 wait(lambda:bool(window.diagnostics) or '失败' in window.status.text())
 assert window.diagnostics,window.status.text()

def js(window,code):
 answer=[];window.page.runJavaScript(code,lambda x:answer.append(x));wait(lambda:bool(answer),5);return answer[0]

def state(repo):
 with repo.database.connect() as c:return {n:[tuple(r) for r in c.execute('SELECT * FROM '+n)] for n in ['projects','project_modules','drawing_records','number_reservations','part_sequence_counters']}

def stamp(paths):return {str(p):[hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns] for p in paths}

def factory(*a,**k):
 shell=old_shell(*a,**k);holder['shell']=shell;QTimer.singleShot(400,exercise);return shell

def exercise():
 shell=holder['shell']
 try:
  result['before_memory']=memory(os.getpid());business=state(shell.repository)
  shell.main_window.project_files_button.click();page=shell.project_files_page
  names=['ASCII 立方体.stl','Binary 法向异常 😀.stl','AP203 单实体.step','AP214 多实体颜色.stp','AP242 英寸单位.step','AP242 装配81件.step']
  paths=[samples/n for n in names]+[EVIDENCE/'glb/简单 几何体(红).glb',EVIDENCE/'pdf/多页 装配 😀.pdf']
  original=stamp(paths);page.start_scan([Source(str(p),'file') for p in paths]);wait(lambda:page.worker is None)
  def select(path):
   page.search.setText(path.name);assert page.table.rowCount()==1;page.table.selectRow(0);start=time.monotonic();page.preview_button.click()
   window=page.pdf_window if path.suffix=='.pdf' else page.preview_window;holder['window']=window;rendered(window)
   return window,(time.monotonic()-start)*1000
  def close(window,pdf=False):
   ids=[proc.processId() for proc in window.processes] if pdf else [window.page.renderProcessPid()]
   window.close();wait(lambda:(page.pdf_window if pdf else page.preview_window) is None)
   for pid in ids:
    if pid:wait(lambda:memory(pid) is None,8)
   gc.collect();QTest.qWait(100)
  for path in paths[:6]:
   window,elapsed=select(path)
   cad=window.diagnostics.get('cad')
   if path.suffix in ('.step','.stp'):
    js(window,"window.externalProbe=[];Promise.all(['https://invalid.example/blocked','file:///not-authorized'].map(u=>fetch(u).then(()=>false,()=>true))).then(v=>window.externalProbe=v);true")
    wait(lambda:js(window,'window.externalProbe.length')==2,3);assert js(window,'window.externalProbe')==[True,True]
   if path.suffix=='.stl':assert window.diagnostics['triangles']==12 and '单位未知' in window.status.text()
   else:
    assert window.render_budget and cad and cad['parts']==(81 if '81' in path.name else 1 if 'AP203' in path.name else 2)
    if '英寸' in path.name:assert abs(cad['bounds_mm'][3]-20)<.01 and abs(cad['bounds_mm'][5]-30)<.01
    if '颜色' in path.name:assert cad['materials']>=2
   # Actual browser camera responds to mouse rotation and the standard view/fit controls.
   before=js(window,'previewDiagnostics().camera');target=window.view.focusProxy();center=target.rect().center()
   QTest.mouseMove(target,center-QPoint(40,30),50);QTest.mouseMove(target,center,50)
   QTest.mousePress(target,Qt.MouseButton.LeftButton,pos=center);QTest.mouseMove(target,center+QPoint(70,40),80);QTest.mouseRelease(target,Qt.MouseButton.LeftButton,pos=center+QPoint(70,40));QTest.qWait(150)
   wait(lambda:js(window,'previewDiagnostics().camera')!=before,3)
   rotated=js(window,'previewDiagnostics().camera')
   QTest.mouseMove(target,center-QPoint(40,30),50);QTest.mouseMove(target,center,50)
   QTest.mousePress(target,Qt.MouseButton.RightButton,pos=center);QTest.mouseMove(target,center+QPoint(45,25),80);QTest.mouseRelease(target,Qt.MouseButton.RightButton,pos=center+QPoint(45,25))
   wait(lambda:js(window,'previewDiagnostics().camera')['center']!=rotated['center'],3)
   panned=js(window,'previewDiagnostics().camera')
   wheel=QWheelEvent(QPointF(center),QPointF(target.mapToGlobal(center)),QPoint(0,0),QPoint(0,120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
   module.QApplication.sendEvent(target,wheel)
   wait(lambda:js(window,'previewDiagnostics().camera')!=panned,3)
   original_size=js(window,'[previewDiagnostics().width,previewDiagnostics().height]')
   window.resize(900,650);QTest.qWait(200)
   wait(lambda:js(window,'[previewDiagnostics().width,previewDiagnostics().height]')!=original_size,3)
   window.resize(1080,760);QTest.qWait(150)
   for label in ['适配','正视','俯视','轴测']:QTest.mouseClick(window.buttons[label],Qt.MouseButton.LeftButton)
   record=dict(name=path.name,bytes=path.stat().st_size,elapsed_ms=elapsed,render=window.diagnostics,main_memory=memory(os.getpid()),renderer_memory=memory(window.page.renderProcessPid()),blocked=len(window.interceptor.blocked))
   result['samples'].append(record);window.grab().save(str(EVIDENCE/(path.stem+'.png')));close(window)
  window,_=select(paths[0]);window.load_path(str(paths[5]));old=window.generation;window.load_path(str(paths[0]));rendered(window)
  window._message(dict(generation=old,state='rendered',triangles=999));assert window.diagnostics['triangles']==12
  window.cancel_load();assert not window.diagnostics
  for filename in ['空模型.stl','损坏.stl','损坏.step','缺失.step']:
   window.load_path(str(samples/filename));wait(lambda:'失败' in window.status.text());assert not window.diagnostics
  close(window)
  # Timed foreground test, no service/scheduler. Every iteration closes its window and checks PID exit.
  started=time.monotonic();cycle=0;targets=[paths[5],paths[0],paths[6],paths[7]]
  while time.monotonic()-started<args.minutes*60 or cycle<8:
   path=targets[cycle%len(targets)];window,elapsed=select(path);pdf=path.suffix=='.pdf'
   parser_pid=window.process.processId() if pdf else None;renderer_pid=None if pdf else window.page.renderProcessPid()
   row=dict(index=cycle,format=path.suffix,elapsed_ms=elapsed,time_s=time.monotonic()-started,main_memory=memory(os.getpid()),parser_memory=memory(parser_pid) if parser_pid else None,renderer_memory=memory(renderer_pid) if renderer_pid else None)
   if args.minutes:QTest.qWait(12000)
   close(window,pdf);row['after_close_memory']=memory(os.getpid());row['children_exited']=True;result['cycles'].append(row);cycle+=1
   (EVIDENCE/'stability-progress.json').write_text(json.dumps(dict(elapsed_s=time.monotonic()-started,cycles=cycle,last=row),indent=2),'utf-8')
  result.update(stability_seconds=time.monotonic()-started,after_close_memory=memory(os.getpid()),source_files_unchanged=stamp(paths)==original,business_unchanged=state(shell.repository)==business)
  assert result['source_files_unchanged'] and result['business_unchanged'];result['passed']=True
 except BaseException:result['error']=traceback.format_exc()
 finally:
  (EVIDENCE/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
  print(json.dumps({k:v for k,v in result.items() if k in ['passed','error','stability_seconds']},ensure_ascii=True))
  if holder.get('window'):
   try:holder['window'].close()
   except RuntimeError:pass
  shell.quit()

module.ApplicationShell=factory
sys.argv=[str(ROOT/'launcher.py'),'--dev','--dev-root',str(EVIDENCE/'isolated-profile')]
code=module.main();raise SystemExit(0 if code==0 and result['passed'] else 1)
