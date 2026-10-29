# SPDX-License-Identifier: GPL-3.0-only
"""Actual Windows snapshot workflow/restart; fictional files and explicit external evidence."""
import argparse,hashlib,importlib.abc,json,os,sys,time,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--reopen',action='store_true');args=parser.parse_args()
E=args.output.resolve()
if E.is_relative_to(ROOT) or E.parent==E:raise ValueError('External evidence required')
for name in ['LOCALAPPDATA','APPDATA']:
 protected=(Path(os.environ.get(name,Path.home()))/'MechanicalProjectNamer').resolve()
 if E.is_relative_to(protected) or protected.is_relative_to(E):raise ValueError('Production paths forbidden')
if args.reopen:
 if not (E/'first.json').exists():raise ValueError('First run required')
elif E.exists():raise ValueError('New evidence folder required')
else:E.mkdir(parents=True)
class NoPreview(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path=None,target=None):
  if name.startswith(('PyQt6.QtWebEngine','PyQt6.QtPdf','OCP')):raise ImportError('Preview/CAD forbidden for snapshot GUI acceptance')
sys.meta_path.insert(0,NoPreview())
from PyQt6.QtCore import Qt,QTimer
from PyQt6.QtTest import QTest
from mechanical_project_namer.project_files import ProjectFileCatalog
import mechanical_project_namer.app as module
holder={};result={'passed':False,'reopen':args.reopen};old_shell=module.ApplicationShell
def wait(predicate,seconds=60):
 end=time.monotonic()+seconds
 while not predicate():
  if time.monotonic()>end:raise AssertionError('Snapshot GUI timed out')
  QTest.qWait(20)
def business(repo):
 with repo.database.connect() as c:return {t:[tuple(r) for r in c.execute('SELECT * FROM '+t)] for t in ['projects','project_modules','drawing_records','number_reservations','part_sequence_counters']}
def factory(*a,**k):
 shell=old_shell(*a,**k);holder['shell']=shell;QTimer.singleShot(500,exercise);return shell
def exercise():
 shell=holder['shell'];dialog=None
 try:
  repo=shell.repository;p=repo.list_projects()[0];work=E/'工作工程';catalog=ProjectFileCatalog(repo)
  if not args.reopen:
   work.mkdir();paths=[work/'子装配A/同名.prt',work/'子装配B/同名.SLDPRT',work/'总成.SLDASM',work/'原名称.step',work/'附件.txt']
   for i,f in enumerate(paths):f.parent.mkdir(exist_ok=True);f.write_bytes(('FICTIONAL PLACEHOLDER ONLY - NOT A VALID CAD MODEL '+str(i)).encode())
   # Bounded large fictional attachment demonstrates background hash/copy responsiveness.
   with paths[-1].open('wb') as stream:
    for _ in range(64):stream.write(b'FICTIONAL\n'*100000)
   catalog.register_attachments(p.id,[str(f) for f in paths]);original={str(f.relative_to(work)):[hashlib.sha256(f.read_bytes()).hexdigest(),f.stat().st_mtime_ns] for f in paths}
  baseline=business(repo);shell.main_window.project_files_button.click();page=shell.project_files_page;page.load_projects(p.id)
  page.snapshot_button.click();dialog=page.snapshot_window;assert dialog is not None;holder['dialog']=dialog
  heartbeat=[];timer=QTimer();timer.timeout.connect(lambda:heartbeat.append(time.monotonic()));timer.start(10)
  def run_click(button):
   dialog.actions[button].click() if button in dialog.actions else dialog.create_button.click()
   wait(lambda:not dialog.busy)
   assert '未成功' not in dialog.status.text(),dialog.status.text()
  if not args.reopen:
   dialog.root.setText(str(work));dialog.label.setText('V1');dialog.note.setText('虚构占位文件，不能用于CAD兼容性验收')
   run_click('create');assert '没有GLB' in dialog.summary.text() and '没有PDF' in dialog.summary.text()
   v1=next(r for r in dialog.rows if r['label']=='V1');paths[0].write_bytes(b'FICTIONAL V2 CHANGED CONTENT')
   after_edit={str(f.relative_to(work)):[hashlib.sha256(f.read_bytes()).hexdigest(),f.stat().st_mtime_ns] for f in paths}
   dialog.label.setText('V2');dialog.note.setText('只修改虚构工作文件，不改旧版本');run_click('create')
   assert len(dialog.rows)==2 and dialog.service.verify(v1['task_id'])
   for i,row in enumerate(dialog.rows):
    if row['label']=='V1':dialog.history.selectRow(i)
   run_click('校验');dialog.target.setText(str(E/'恢复的V1'));run_click('恢复到新目录')
   for rel,(h,_) in original.items():assert hashlib.sha256((E/'恢复的V1'/rel).read_bytes()).hexdigest()==h
   assert all([hashlib.sha256((work/rel).read_bytes()).hexdigest(),(work/rel).stat().st_mtime_ns]==stamp for rel,stamp in after_edit.items())
   result.update(original=original,version_ids=[r['version_id'] for r in dialog.rows],restored_v1=True,source_after_edit=after_edit)
   assert business(repo)==baseline
   # Project rename keeps its stable identity and version history.
   repo.update_project(p.id,name='虚构项目已改名');baseline=business(repo);dialog.refresh();assert len(dialog.rows)==2
  else:
   first=json.loads((E/'first.json').read_text('utf-8'));assert len(dialog.rows)==2
   assert {r['version_id'] for r in dialog.rows}==set(first['version_ids'])
   for i in range(len(dialog.rows)):
    dialog.history.selectRow(i);run_click('校验')
   for rel,(h,_) in first['original'].items():assert hashlib.sha256((E/'恢复的V1'/rel).read_bytes()).hexdigest()==h
   result['restart_history_and_v1']=True
  timer.stop();assert business(repo)==baseline
  result['heartbeat_ticks']=len(heartbeat);result['max_heartbeat_gap_s']=max((b-a for a,b in zip(heartbeat,heartbeat[1:])),default=0)
  assert len(heartbeat)>5 and result['max_heartbeat_gap_s']<1,'GUI event loop blocked'
  result['business_unchanged']=True;result['window_visible']=dialog.isVisible()
  dialog.grab().save(str(E/('restart.png' if args.reopen else 'v1-v2-restored.png')))
  assert not any(n.startswith(('PyQt6.QtWebEngine','PyQt6.QtPdf','OCP')) for n in sys.modules)
  result['no_preview_dependencies']=True;result['passed']=True
 except BaseException:result['error']=traceback.format_exc()
 finally:
  (E/('restart.json' if args.reopen else 'first.json')).write_text(json.dumps(result,ensure_ascii=True,indent=2),'utf-8')
  print(json.dumps({k:v for k,v in result.items() if k in ['passed','error','max_heartbeat_gap_s','heartbeat_ticks']}))
  if dialog:dialog.close()
  shell.quit()
module.ApplicationShell=factory
sys.argv=[str(ROOT/'launcher.py'),'--dev','--dev-root',str(E/'profile')]
code=module.main();raise SystemExit(0 if code==0 and result['passed'] else 1)
