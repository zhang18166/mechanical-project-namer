# SPDX-License-Identifier: GPL-3.0-only
"""Five independent Windows GUI processes: source / backup / offline restore / client / restart.

Only fictional files in a new external opt-in evidence root. Source quarantine is a
single checked rename, never a delete. Raw paths, screenshots and reports stay local.
"""
import argparse
import hashlib
import importlib.abc
import io
import json
import os
from pathlib import Path
import sys
import time
import traceback
from urllib.parse import unquote

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--phase',choices=['source','backup','recover','client','restart'],required=True);args=parser.parse_args()
E=args.output.absolute();phase=args.phase
if E.is_relative_to(ROOT) or E.parent==E:raise ValueError('New external evidence required')
if phase=='source':
    if E.exists():raise ValueError('New evidence folder required')
    E.mkdir(parents=True);(E/'.mw-library-gui-drill').write_text('fictional-only')
elif not (E/'.mw-library-gui-drill').is_file():raise ValueError('Owned drill marker required')

class NoPreview(importlib.abc.MetaPathFinder):
    def find_spec(self,name,path=None,target=None):
        if name.startswith(('PyQt6.QtWebEngine','PyQt6.QtPdf','OCP')):raise ImportError('Backup GUI acceptance must not load preview/CAD components')
sys.meta_path.insert(0,NoPreview())

denied=[]
def install_old_location_fence():
    forbidden=[E/'source-profile',E/'source-offline']
    def guard(value):
        if not isinstance(value,(str,bytes,os.PathLike)):return
        value=os.fsdecode(value)
        if value.startswith('file:'):
            value=unquote(value[5:].split('?')[0]).lstrip('/')
        path=Path(os.path.abspath(value))
        if any(path.is_relative_to(p) for p in forbidden):
            denied.append(str(path));raise AssertionError('Forbidden old-location access: '+str(path))
    for name in ('stat','lstat','scandir','listdir','open'):
        original=getattr(os,name)
        def wrapped(value,*a,__fn=original,**k):guard(value);return __fn(value,*a,**k)
        setattr(os,name,wrapped)
    original=io.open
    def file_open(value,*a,**k):guard(value);return original(value,*a,**k)
    io.open=file_open
    def audit(event,values):
        if event in ('open','sqlite3.connect','os.scandir','os.listdir') and values:guard(values[0])
    sys.addaudithook(audit)

if phase in ('recover','client','restart'):install_old_location_fence()
from PyQt6.QtCore import QTimer,Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from mechanical_project_namer.backup_io import copy_hash, read_metadata, write_metadata, database_fingerprints, readonly_database
from mechanical_project_namer.library_backups import LibraryBackups,DATABASE_RELATIVE,ARCHIVE_RELATIVE,SETTINGS_RELATIVE
from mechanical_project_namer.project_files import ProjectFileCatalog
from mechanical_project_namer.snapshots import SnapshotService, now
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.numbering.simple import SIMPLE_TYPE

result=dict(passed=False,phase=phase);holder={}
def load(name):return json.loads((E/name).read_text('utf-8'))
def save(name,value):return (E/name).write_text(json.dumps(value,ensure_ascii=True,indent=2),'utf-8')
def wait(predicate,seconds=120):
    deadline=time.monotonic()+seconds
    while not predicate():
        if time.monotonic()>deadline:raise AssertionError('GUI timeout')
        QTest.qWait(20)
def tick():
    values=[];timer=QTimer();timer.timeout.connect(lambda:values.append(time.monotonic()));timer.start(10);return timer,values
def finish_ticks(timer,values):
    timer.stop();result['heartbeat_ticks']=len(values)
    result['max_heartbeat_gap_s']=max((b-a for a,b in zip(values,values[1:])),default=0)
    assert len(values)>5 and result['max_heartbeat_gap_s']<2
def unchanged_tables(repo):
    names=['project_modules','drawing_records','number_reservations','part_sequence_counters']
    with repo.database.connect() as c:return {t:[list(r) for r in c.execute('SELECT * FROM '+t)] for t in names}
def tree_stamps(folder):
    return {p.relative_to(folder).as_posix():dict(**copy_hash(p),mtime=p.stat().st_mtime_ns) for p in folder.rglob('*') if p.is_file()}
def click_snapshot(dialog,action):
    if action=='create':dialog.create_button.click()
    else:dialog.actions[action].click()
    wait(lambda:not dialog.busy)
    assert '未成功' not in dialog.status.text(),dialog.status.text()

def exercise_client():
    shell=holder['shell'];dialog=None;timer,heartbeats=tick()
    try:
        repo=shell.repository;catalog=ProjectFileCatalog(repo)
        if phase=='source':
            p0=repo.list_projects()[0];p1=ProjectService(repo).create('第二虚构设备','','TWO',directory=str(E/'source-profile/workspace/第二工程'),modules=[('DJ','演练模块')])
            project_ids=[p0.id,p1.id]; originals={}; version_ids=[]
            for p in [p0,p1]:
                work=Path(p.directory);work.mkdir(parents=True,exist_ok=True)
                repo.save_named_record(p.id,'DJ',SIMPLE_TYPE,'虚构连接块')
                void=repo.save_named_record(p.id,'DJ',SIMPLE_TYPE,'虚构作废件');repo.set_record_status(void.id,'void')
                with repo.database.transaction() as c:
                    c.execute('INSERT INTO number_reservations(project_id,module_code,part_category,part_sequence,status,reserved_at,reason) VALUES (?,?,?,?,?,?,?)',
                              (p.id,'DJ',SIMPLE_TYPE,50,'reserved',now(),'FICTIONAL offline drill reservation'))
                files=[work/'甲目录/同名.prt',work/'乙目录/同名.prt',work/'原名称.step']
                for i,f in enumerate(files):f.parent.mkdir(exist_ok=True);f.write_bytes(f'FICTIONAL CAD PLACEHOLDER ONLY {p.id}-{i}'.encode())
                if p.id==p0.id:
                    large=work/'虚构附件.bin'
                    with large.open('wb') as out:
                        for _ in range(64):out.write(b'FICTIONAL\n'*100000)
                    files.append(large)
                catalog.register_attachments(p.id,[str(f) for f in files]);originals[str(p.id)]={f.relative_to(work).as_posix():copy_hash(f) for f in files}
                shell.main_window.project_files_button.click();page=shell.project_files_page;page.load_projects(p.id);page.snapshot_button.click();dialog=page.snapshot_window
                dialog.root.setText(str(work));dialog.label.setText('V1');dialog.note.setText('虚构占位，非CAD兼容性验证');click_snapshot(dialog,'create')
                files[0].write_bytes(b'FICTIONAL V2 CHANGE');dialog.label.setText('V2');click_snapshot(dialog,'create')
                assert len(dialog.rows)==2;version_ids.extend(r['version_id'] for r in dialog.rows)
                dialog.grab().save(str(E/f'source-project-{p.id}.png'));dialog.close();wait(lambda:page.snapshot_window is None);dialog=None
            result.update(project_ids=project_ids,version_ids=version_ids,original_v1=originals,business=unchanged_tables(repo))
        else:
            expected=load('source.json');project_ids=expected['project_ids'];assert {p.id for p in repo.list_projects()}==set(project_ids)
            assert unchanged_tables(repo)==expected['business']
            version_ids=[]
            for project_id in project_ids:
                shell.main_window.project_files_button.click();page=shell.project_files_page;page.load_projects(project_id)
                if phase=='client':assert all('待重新关联' in r['error'] for r in page.rows)
                page.snapshot_button.click();dialog=page.snapshot_window
                assert len(dialog.rows)==(3 if phase=='restart' and project_id==project_ids[0] else 2)
                version_ids.extend(r['version_id'] for r in dialog.rows)
                for i in range(len(dialog.rows)):
                    dialog.history.selectRow(i);click_snapshot(dialog,'校验')
                if phase=='client' and project_id==project_ids[0]:
                    for i,row in enumerate(dialog.rows):
                        if row['label']=='V1':dialog.history.selectRow(i)
                    extracted=E/'提取旧V1';dialog.target.setText(str(extracted));click_snapshot(dialog,'恢复到新目录')
                    for rel,stamp in expected['original_v1'][str(project_id)].items():assert copy_hash(extracted/rel)==stamp
                    new_work=E/'restored-profile/workspace/明确新工作目录';new_work.mkdir(parents=True)
                    f=new_work/'新增虚构件.prt';f.write_bytes(b'FICTIONAL NEW RESTORED INSTANCE VERSION')
                    catalog.register_attachments(project_id,[str(f)]);row=next(r for r in catalog.list_files(project_id) if r['path']==str(f))
                    dialog.load_files(row['file_id']);dialog.root.setText(str(new_work));dialog.label.setText('V3');click_snapshot(dialog,'create')
                    assert len(dialog.rows)==3
                dialog.grab().save(str(E/f'{phase}-project-{project_id}.png'));dialog.close();wait(lambda:page.snapshot_window is None);dialog=None
            assert set(expected['version_ids']).issubset(version_ids)
            assert unchanged_tables(repo)==expected['business'];result['business_preserved']=True
            if phase=='restart':
                for rel,stamp in expected['original_v1'][str(project_ids[0])].items():assert copy_hash(E/'提取旧V1'/rel)==stamp
            result['original_ids_preserved']=True
        finish_ticks(timer,heartbeats);assert not denied;result['old_location_attempts']=len(denied)
        assert not any(n.startswith(('PyQt6.QtWebEngine','PyQt6.QtPdf','OCP')) for n in sys.modules)
        result['window_visible']=shell.main_window.isVisible();result['no_preview_dependencies']=True;result['passed']=True
    except BaseException:result['error']=traceback.format_exc()
    finally:
        if dialog:dialog.close();wait(lambda:dialog.worker is None)
        save(phase+'.json',result);shell.quit()

def exercise_maintenance(window,app):
    timer,heartbeats=tick()
    try:
        def action(label):
            window.buttons[label].click();wait(lambda:not window.busy)
            assert '未成功' not in window.status.text(),window.status.text()
        window.backup.setText(str(E/'backup-set'))
        if phase=='backup':
            window.profile.setText(str(E/'source-profile'));action('查看备份范围')
            assert '完整版本：4' in window.details.toPlainText()
            window.destination.setText(str(E/'backup-set'));window.buttons['创建完整备份'].click()
            cancelled=[False]
            def request_cancel(message):
                if message.startswith('备份：') and not cancelled[0]:
                    cancelled[0]=True;window.cancel_button.click()
            window.worker.progress.connect(request_cancel);wait(lambda:not window.busy)
            assert cancelled[0] and '未成功' in window.status.text() and not (E/'backup-set').exists()
            result['cancelled_without_complete_set']=True;window.grab().save(str(E/'backup-cancel.png'))
            action('重试上次任务');action('验证备份');result['retry_and_verify']=True
            save('source-closed-stamps.json',tree_stamps(E/'source-profile'));save('backup-stamps.json',tree_stamps(E/'backup-set'))
            source=E/'source-profile';offline=E/'source-offline'
            # Checked, single-directory rename of this drill's own data only.
            assert source.resolve().parent==E.resolve() and offline.resolve().parent==E.resolve() and not offline.exists()
            os.rename(source,offline);result['source_quarantined']=True
        else:
            # No source DB was opened by this new process; the profile field deliberately points at the missing source.
            window.profile.setText(str(E/'source-profile'));action('验证备份')
            window.target.setText(str(E/'restored-profile'));action('整体恢复到新目录')
            assert '--dev-root' in window.command.text() and '待重新关联' in window.details.toPlainText()
            result['restored_only_from_backup']=True;result['command']=window.command.text()
        window.grab().save(str(E/(phase+'-window.png')));finish_ticks(timer,heartbeats)
        assert not denied;result['old_location_attempts']=len(denied);result['window_visible']=window.isVisible();result['passed']=True
    except BaseException:result['error']=traceback.format_exc()
    finally:
        save(phase+'.json',result);window.close();app.quit()

if phase in ('source','client','restart'):
    import mechanical_project_namer.app as module
    old=module.ApplicationShell
    def factory(*a,**k):
        shell=old(*a,**k);holder['shell']=shell;QTimer.singleShot(500,exercise_client);return shell
    module.ApplicationShell=factory
    sys.argv=[str(ROOT/'launcher.py'),'--dev','--dev-root',str(E/('source-profile' if phase=='source' else 'restored-profile'))]
    code=module.main()
else:
    from mechanical_project_namer.development import configure_development
    configure_development(['--dev','--dev-root',str(E/('maintenance-'+phase))])
    from mechanical_project_namer.ui.backups_dialog import BackupsDialog
    from mechanical_project_namer.style import APP_STYLE
    app=QApplication([sys.argv[0]]);app.setStyleSheet(APP_STYLE);window=BackupsDialog();window.show()
    QTimer.singleShot(300,lambda:exercise_maintenance(window,app));code=app.exec()
print(json.dumps({k:v for k,v in result.items() if k in ('phase','passed','error','old_location_attempts','max_heartbeat_gap_s')}))
raise SystemExit(0 if code==0 and result['passed'] else 1)
