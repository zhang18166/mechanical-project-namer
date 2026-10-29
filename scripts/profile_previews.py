# SPDX-License-Identifier: GPL-3.0-only
"""Read-only Windows GUI memory/lifecycle study. Generated data and streaming evidence only."""
import argparse,gc,hashlib,json,os,subprocess,sys,time,weakref
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser()
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--mode',choices=['idle','glb','pdf','stl','step','mixed'],required=True)
parser.add_argument('--cycles',type=int,default=30)
parser.add_argument('--minutes',type=float,default=0)
parser.add_argument('--hold-ms',type=int,default=200)
parser.add_argument('--trace',action='store_true')
parser.add_argument('--collect',action='store_true',help='Diagnostic control only; not the final acceptance default')
args=parser.parse_args();EVIDENCE=args.output.resolve()
if EVIDENCE.exists() or EVIDENCE.is_relative_to(ROOT) or EVIDENCE.parent==EVIDENCE:raise ValueError('New external evidence directory required')
for key,default in [('LOCALAPPDATA',Path.home()/'AppData/Local'),('APPDATA',Path.home()/'AppData/Roaming')]:
 protected=(Path(os.environ.get(key,default))/'MechanicalProjectNamer').resolve()
 if EVIDENCE.is_relative_to(protected) or protected.is_relative_to(EVIDENCE):raise ValueError('Production profile forbidden')
EVIDENCE.mkdir(parents=True)
from scripts.preview_diagnostics import memory,process_tree,object_counts
if args.mode!='idle':
 out=subprocess.run([sys.executable,str(ROOT/'scripts/generate_cad_demos.py'),str(EVIDENCE/'cad')],capture_output=True,timeout=60)
 (EVIDENCE/'generation.txt').write_bytes(out.stdout+out.stderr)
 if out.returncode:raise RuntimeError('Own fixture generation failed')
 from scripts.generate_preview_demos import generate as glb_generate
 from scripts.generate_pdf_demos import generate as pdf_generate
 glb_generate(EVIDENCE/'glb');pdf_generate(EVIDENCE/'pdf')
from PyQt6.QtCore import QTimer,QCoreApplication,QEvent
from PyQt6.QtTest import QTest
from PyQt6 import sip
import mechanical_project_namer.app as module
from mechanical_project_namer.project_files import Source
old_shell=module.ApplicationShell;holder={};tracked=[];summary={'passed':False,'mode':args.mode,'pid':os.getpid(),'counts':{}};trace_before=None
summary['source_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
 for folder in ['mechanical_project_namer','scripts'] for p in (ROOT/folder).rglob('*.py') if '__pycache__' not in p.parts}
stream=(EVIDENCE/'series.jsonl').open('w',encoding='utf-8')

def wait(condition,seconds=50):
 end=time.monotonic()+seconds
 while not condition():
  if time.monotonic()>end:raise AssertionError('GUI wait timed out')
  QTest.qWait(25)

def record(row):
 stream.write(json.dumps(row,ensure_ascii=True)+'\n');stream.flush()
 (EVIDENCE/'progress.json').write_text(json.dumps(row,ensure_ascii=True,indent=2),'utf-8')

def track(window):
 entry={}
 for name in ['window','view','page','profile','server','interceptor']:
  value=window if name=='window' else getattr(window,name,None)
  if value is not None:entry[name]=weakref.ref(value)
 tracked.append(entry)

def lifecycle():
 result={};seen={}
 for entry in tracked:
  for name,ref in entry.items():
   value=ref()
   if value is not None:
    if id(value) in seen.setdefault(name,set()):continue
    seen[name].add(id(value))
    key=name+('_cpp_deleted' if name not in ['server'] and sip.isdeleted(value) else '_alive')
    result[key]=result.get(key,0)+1
 tracked[:]=[entry for entry in tracked if any(ref() is not None for ref in entry.values())]
 return result

def factory(*a,**k):
 shell=old_shell(*a,**k);holder['shell']=shell;QTimer.singleShot(500,exercise);return shell

def exercise():
 global trace_before
 shell=holder['shell'];window=None
 try:
  if args.trace:
   import tracemalloc;tracemalloc.start(8)
  summary['initial']={'processes':process_tree(),'objects':object_counts()}
  with shell.repository.database.connect() as c:business={n:[tuple(r) for r in c.execute('SELECT * FROM '+n)] for n in ['projects','project_modules','drawing_records','number_reservations','part_sequence_counters']}
  paths={'glb':EVIDENCE/'glb/受控复杂 环体.glb','pdf':EVIDENCE/'pdf/多页 装配 😀.pdf','stl':EVIDENCE/'cad/Binary 法向异常 😀.stl','step':EVIDENCE/'cad/AP242 装配81件.step'}
  before={str(f):[hashlib.sha256(f.read_bytes()).hexdigest(),f.stat().st_mtime_ns] for f in paths.values()} if args.mode!='idle' else {}
  if args.mode!='idle':
   shell.main_window.project_files_button.click();page=shell.project_files_page
   page.start_scan([Source(str(f),'file') for f in paths.values()]);wait(lambda:page.worker is None)
  start=time.monotonic();cycle=0
  while cycle<args.cycles or time.monotonic()-start<args.minutes*60:
   kind=['glb','pdf','stl','step'][cycle%4] if args.mode=='mixed' else args.mode
   row={'cycle':cycle,'format':kind,'elapsed_s':time.monotonic()-start}
   if kind=='idle':
    QTest.qWait(max(100,args.hold_ms));row['load_ms']=None
   else:
    file=paths[kind];page.search.setText(file.name);assert page.table.rowCount()==1;page.table.selectRow(0)
    loading=time.monotonic();page.preview_button.click();window=page.pdf_window if kind=='pdf' else page.preview_window
    holder['window']=window;track(window)
    during=[];sampler=QTimer();sampler.timeout.connect(lambda:during.append(process_tree()));sampler.start(100)
    wait(lambda:(window.diagnostics.get('state')=='rendered' if kind=='pdf' else bool(window.diagnostics)) or '失败' in window.status.text());sampler.stop()
    assert window.diagnostics,window.status.text();row['load_ms']=(time.monotonic()-loading)*1000
    row['during_load']=during;row['loaded']=process_tree();row['triangles']=window.diagnostics.get('triangles');row['render']=window.diagnostics.copy()
    QTest.qWait(max(0,args.hold_ms))
    ids=[proc.processId() for proc in window.processes] if kind=='pdf' else [window.page.renderProcessPid()]
    window.close();wait(lambda:(page.pdf_window if kind=='pdf' else page.preview_window) is None)
    for pid in ids:
     if pid:wait(lambda:memory(pid) is None,8)
    row['children_exited']=True
    window=None;holder.pop('window',None)
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);QTest.qWait(550)
   if args.collect:gc.collect()
   row['closed']=process_tree();row['lifecycle']=lifecycle();row['objects']=object_counts()
   if args.trace:
    import tracemalloc
    row['python_traced']=tracemalloc.get_traced_memory()
    if cycle==9:trace_before=tracemalloc.take_snapshot()
   record(row);summary['counts'][kind]=summary['counts'].get(kind,0)+1;cycle+=1
  summary.update(elapsed_s=time.monotonic()-start,cycles=cycle,final={'processes':process_tree(),'objects':object_counts(),'lifecycle':lifecycle()})
  if args.trace and trace_before:
   import tracemalloc
   diff=tracemalloc.take_snapshot().compare_to(trace_before,'lineno')
   (EVIDENCE/'python-allocation-diff.json').write_text(json.dumps([dict(location=str(s.traceback),size_diff=s.size_diff,count_diff=s.count_diff) for s in diff[:40]],indent=2),'utf-8')
  with shell.repository.database.connect() as c:after={n:[tuple(r) for r in c.execute('SELECT * FROM '+n)] for n in business}
  assert after==business
  assert all([hashlib.sha256(Path(f).read_bytes()).hexdigest(),Path(f).stat().st_mtime_ns]==stamp for f,stamp in before.items())
  summary['files_and_business_unchanged']=True;summary['passed']=True
 except BaseException:
  import traceback;summary['error']=traceback.format_exc()
 finally:
  (EVIDENCE/'summary.json').write_text(json.dumps(summary,ensure_ascii=True,indent=2),'utf-8');stream.close()
  print(json.dumps({k:v for k,v in summary.items() if k in ['passed','error','mode','cycles','elapsed_s']}))
  if holder.get('window'):
   try:holder['window'].close()
   except RuntimeError:pass
  shell.quit()

module.ApplicationShell=factory
sys.argv=[str(ROOT/'launcher.py'),'--dev','--dev-root',str(EVIDENCE/'profile')]
code=module.main();raise SystemExit(0 if code==0 and summary['passed'] else 1)
