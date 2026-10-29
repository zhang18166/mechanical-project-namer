# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import uuid
import pytest

from mechanical_project_namer.data import Database, Repository
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.numbering.simple import SIMPLE_TYPE
from mechanical_project_namer.project_files import ProjectFileCatalog
from mechanical_project_namer.snapshots import SnapshotService, now
from mechanical_project_namer.development import MARKER, IDENTITY
from mechanical_project_namer.library_backups import (LibraryBackups, DATABASE_RELATIVE, ARCHIVE_RELATIVE, SETTINGS_RELATIVE)
from mechanical_project_namer.backup_io import (read_metadata, write_metadata, copy_hash, readonly_database,
                                               database_fingerprints, check_entries, relative_path)
from mechanical_project_namer.snapshot_io import SnapshotError, Cancelled
from mechanical_project_namer.library_lock import library_lease, MaintenanceBusy

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def library(tmp_path):
    profile=tmp_path/'源实例 中文';profile.mkdir();write_metadata(profile/MARKER,IDENTITY)
    db=Database(profile/DATABASE_RELATIVE);db.migrate();repo=Repository(db);snap=SnapshotService(repo,profile/ARCHIVE_RELATIVE)
    projects=[];tasks=[]
    for n in range(2):
        work=profile/'workspace'/f'虚构工程{n}';work.mkdir(parents=True)
        project=ProjectService(repo).create(f'虚构项目{n}','','DEMO'+str(n),directory=str(work),modules=[('DJ','虚构模块')]);projects.append(project)
        first=repo.save_named_record(project.id,'DJ',SIMPLE_TYPE,'虚构连接块')
        second=repo.save_named_record(project.id,'DJ',SIMPLE_TYPE,'虚构作废件');repo.set_record_status(second.id,'void')
        with db.transaction() as con:
            con.execute('INSERT INTO number_reservations(project_id,module_code,part_category,part_sequence,status,reserved_at,reason) VALUES (?,?,?,?,?,?,?)',
                        (project.id,'DJ',SIMPLE_TYPE,50,'reserved',now(),'FICTIONAL persistent reservation'))
        files=[work/'甲目录/同名.prt',work/'乙目录/同名.prt',work/'原名称.step']
        for i,f in enumerate(files):f.parent.mkdir(exist_ok=True);f.write_bytes(f'FICTIONAL CAD PLACEHOLDER ONLY {n}-{i}'.encode())
        catalog=ProjectFileCatalog(repo);catalog.register_attachments(project.id,[str(f) for f in files])
        choices=[dict(file_id=r['file_id']) for r in catalog.list_files(project.id)]
        for label in ('V1','V2'):
            if label=='V2':files[0].write_bytes(b'FICTIONAL V2 ONLY')
            task=snap.prepare(project.id,work,choices,label);snap.run(task);tasks.append(task)
    settings=profile/SETTINGS_RELATIVE;settings.parent.mkdir(parents=True)
    write_metadata(settings,dict(current_project_id=projects[0].id,current_module_code='DJ',always_on_top=False,
                                 api_key='FICTIONAL_NOT_A_REAL_SECRET',api_url='https://example.invalid',solidworks_enabled=True))
    (tmp_path/'.library-backup-probe').write_text('test-only')
    return LibraryBackups(),profile,repo,snap,projects,tasks


def stamps(folder):
    return {f.relative_to(folder).as_posix():[hashlib.sha256(f.read_bytes()).hexdigest(),f.stat().st_mtime_ns]
            for f in folder.rglob('*') if f.is_file()}


def table_rows(repo):
    with repo.database.connect() as con:
        return {t:[tuple(r) for r in con.execute('SELECT * FROM '+t)] for t in
                ['project_modules','drawing_records','number_reservations','part_sequence_counters','local_snapshot_events']}


def test_full_backup_offline_restore_write_restart_and_unchanged_source(library,tmp_path):
    service,profile,repo,snap,projects,tasks=library;original=table_rows(repo)
    source_stamps=stamps(profile);listing=[p.id for p in repo.list_projects()]
    backup=tmp_path/'独立备份集';doc=service.create(profile,backup)
    assert len(doc['versions'])==4 and doc['unencrypted'] and service.verify(backup)==doc
    assert stamps(profile)==source_stamps
    config=read_metadata(backup/'data/config.json')[0]
    assert 'api_key' not in config and 'api_url' not in config and 'solidworks_enabled' not in config
    backup_stamps=stamps(backup)
    offline=tmp_path/'仅测试原库不可访问';profile.rename(offline)
    restored=tmp_path/'恢复实例';receipt=service.restore(backup,restored)
    assert receipt['byte_integrity']=='verified' and receipt['cad_dependencies']==receipt['preview_correspondence']=='unverified'
    new_repo=Repository(Database(restored/DATABASE_RELATIVE));new_snap=SnapshotService(new_repo,restored/ARCHIVE_RELATIVE)
    assert table_rows(new_repo)==original
    assert [p.id for p in new_repo.list_projects()]==listing
    assert set(listing)=={p.id for p in projects}
    for task in tasks:assert new_snap.verify(task)
    extracted=tmp_path/'提取V1';new_snap.restore(tasks[0],extracted)
    assert (extracted/'甲目录/同名.prt').read_bytes()!= (extracted/'乙目录/同名.prt').read_bytes()
    assert (extracted/'原名称.step').is_file()
    catalog=ProjectFileCatalog(new_repo);assert all('待重新关联' in r['error'] for r in catalog.list_files())
    assert catalog.scan(projects[0].id,refresh=True).complete
    new_work=restored/'workspace/新工作文件';new_work.mkdir(parents=True);f=new_work/'新增.prt';f.write_bytes(b'FICTIONAL NEW VERSION')
    catalog.register_attachments(projects[0].id,[str(f)])
    choice=next(r for r in catalog.list_files(projects[0].id) if r['path']==str(f))
    task=new_snap.prepare(projects[0].id,new_work,[dict(file_id=choice['file_id'])],'V3');new_snap.run(task)
    assert stamps(backup)==backup_stamps and stamps(offline)==source_stamps
    restarted=SnapshotService(Repository(Database(restored/DATABASE_RELATIVE)),restored/ARCHIVE_RELATIVE)
    assert len(restarted.history(projects[0].id,complete_only=True))==3
    assert restarted.verify(tasks[0])
    # A recovered, writable instance can itself produce another independent complete set.
    second_backup=tmp_path/'second-generation';assert len(service.create(restored,second_backup)['versions'])==5
    third=tmp_path/'third-location';service.restore(second_backup,third)
    third_repo=Repository(Database(third/DATABASE_RELATIVE));assert table_rows(third_repo)['number_reservations']==original['number_reservations']
    assert SnapshotService(third_repo,third/ARCHIVE_RELATIVE).verify(tasks[0])


def test_wal_committed_rows_and_read_only_backup_verification(library,tmp_path):
    service,profile,repo,snap,projects,tasks=library;db=repo.database.path
    keeper=sqlite3.connect(db)
    try:
        keeper.execute('PRAGMA journal_mode=WAL');keeper.execute('PRAGMA wal_autocheckpoint=0')
        keeper.execute("UPDATE projects SET description='FICTIONAL committed WAL row' WHERE id=?",(projects[0].id,));keeper.commit()
        assert Path(str(db)+'-wal').stat().st_size>0
        backup=tmp_path/'wal-backup';service.create(profile,backup)
        with readonly_database(backup/'data/database.sqlite3') as con:
            assert con.execute('SELECT description FROM projects WHERE id=?',(projects[0].id,)).fetchone()[0]=='FICTIONAL committed WAL row'
        before=stamps(backup);service.verify(backup);assert stamps(backup)==before
        assert not list(backup.rglob('*-wal')) and not list(backup.rglob('*-shm'))
    finally:keeper.close()


@pytest.mark.parametrize('kind',['snapshot','restore','solidworks','credentials','mismatched-store'])
def test_inconsistent_or_pending_source_is_rejected_without_deletion(library,tmp_path,kind):
    service,profile,repo,snap,projects,tasks=library
    if kind=='snapshot':
        p=projects[0];choices=[dict(file_id=r['file_id']) for r in ProjectFileCatalog(repo).list_files(p.id)]
        pending=snap.prepare(p.id,p.directory,choices,'未完成')
    elif kind=='restore':
        def fail(point):
            if point=='restore_file_copied':raise OSError('FICTIONAL failure')
        snap.checkpoint=fail
        with pytest.raises(OSError):snap.restore(tasks[0],tmp_path/'unfinished-restore')
    elif kind=='solidworks':
        with repo.database.transaction() as con:
            con.execute('INSERT INTO pending_solidworks_creates(operation_id,project_id,module_code,part_category,part_sequence,part_name,created_at) VALUES (?,?,?,?,?,?,?)',
                        ('FICTIONAL',projects[0].id,'DJ',SIMPLE_TYPE,60,'虚构',now()))
    elif kind=='credentials':
        with repo.database.connect() as con:con.execute('ALTER TABLE projects ADD COLUMN api_key TEXT')
    else:
        marker=profile/ARCHIVE_RELATIVE/'.mw-store.json';raw=read_metadata(marker)[0];raw['store_id']=str(uuid.uuid4());write_metadata(marker,raw,replace=True)
    before=stamps(profile)
    with pytest.raises((SnapshotError,sqlite3.Error)):service.create(profile,tmp_path/'reject')
    assert not (tmp_path/'reject').exists() and stamps(profile)==before


def test_terminated_failures_remain_in_database_but_partial_files_are_excluded(library,tmp_path):
    service,profile,repo,snap,projects,tasks=library;p=projects[0]
    choices=[dict(file_id=r['file_id']) for r in ProjectFileCatalog(repo).list_files(p.id)]
    failed=snap.prepare(p.id,p.directory,choices,'明确终止')
    snap.checkpoint=lambda point:(_ for _ in ()).throw(OSError('FICTIONAL failed attempt')) if point=='file_copied' else None
    with pytest.raises(OSError):snap.run(failed)
    snap.checkpoint=lambda point:None;snap.abort(failed)
    backup=tmp_path/'with-history';doc=service.create(profile,backup);assert len(doc['versions'])==4
    with readonly_database(backup/'data/database.sqlite3') as con:
        assert con.execute('SELECT state FROM local_versions WHERE task_id=?',(failed,)).fetchone()[0]=='cancelled'
        assert any('FICTIONAL failed attempt' in r[0] for r in con.execute('SELECT details FROM local_snapshot_events'))
    assert not (backup/'data/archive/staging').exists()


@pytest.mark.parametrize('kind',['copy','space','permission','cancel'])
def test_backup_failures_cancel_keep_source_and_allow_bound_retry(library,tmp_path,monkeypatch,kind):
    service,profile,repo,snap,projects,tasks=library;backup=tmp_path/'attempt';task=str(uuid.uuid4());event=threading.Event();before=stamps(profile)
    if kind=='copy':monkeypatch.setattr('mechanical_project_namer.library_backups.copy_hash',lambda *a,**k:(_ for _ in ()).throw(OSError('copy failed')))
    if kind=='permission':monkeypatch.setattr('mechanical_project_namer.library_backups.copy_hash',lambda *a,**k:(_ for _ in ()).throw(PermissionError('permission denied')))
    if kind=='space':monkeypatch.setattr('mechanical_project_namer.library_backups._space',lambda *a:(_ for _ in ()).throw(SnapshotError('space insufficient')))
    if kind=='cancel':event.set()
    with pytest.raises((SnapshotError,OSError)):service.create(profile,backup,task_id=task,cancel=event)
    assert not backup.exists() and stamps(profile)==before
    monkeypatch.undo();event.clear();assert service.create(profile,backup,task_id=task)['backup_id']==task
    assert service.create(profile,backup,task_id=task)['backup_id']==task
    with pytest.raises(SnapshotError):service.create(profile,tmp_path/'different',task_id=task)


def rewrite_manifest(backup, edit):
    doc=read_metadata(backup/'manifest.json')[0];edit(doc)
    h=write_metadata(backup/'manifest.json',doc,replace=True)
    write_metadata(backup/'COMPLETE.json',dict(format=1,backup_id=doc['backup_id'],manifest_sha256=h,state='complete'),replace=True)


@pytest.mark.parametrize('damage',['missing','bytes','manifest','extra','extra-directory','database','schema','case','traversal','identity'])
def test_verify_rejects_damage_without_repair_or_migration(library,tmp_path,damage):
    service,profile,repo,snap,projects,tasks=library;backup=tmp_path/'backup';service.create(profile,backup)
    manifest=read_metadata(backup/'manifest.json')[0]
    entry=next(e for e in manifest['files'] if '/payload/' in e['path']);file=backup/'data'/entry['path']
    if damage=='missing':file.unlink()
    if damage=='bytes':file.write_bytes(b'FICTIONAL DAMAGE')
    if damage=='manifest':(backup/'manifest.json').write_text('{}')
    if damage=='extra':(backup/'data/unlisted.txt').write_text('FICTIONAL')
    if damage=='extra-directory':(backup/'data/unlisted').mkdir()
    if damage in ('database','schema','identity'):
        db=backup/'data/database.sqlite3'
        if damage=='database':db.write_bytes(b'not SQLite')
        else:
            with sqlite3.connect(db) as con:
                if damage=='schema':con.execute('UPDATE schema_meta SET version=14')
                else:con.execute("UPDATE snapshot_meta SET value=? WHERE key='store_id'",(str(uuid.uuid4()),))
        rewrite_manifest(backup,lambda doc:[e.update(copy_hash(db)) for e in doc['files'] if e['path']=='database.sqlite3'])
    if damage=='case':rewrite_manifest(backup,lambda doc:doc['files'].append({**entry,'path':entry['path'].upper()}))
    if damage=='traversal':rewrite_manifest(backup,lambda doc:doc['files'][0].update(path='../escape'))
    before=stamps(backup)
    with pytest.raises((SnapshotError,sqlite3.Error,KeyError)):service.verify(backup)
    with pytest.raises((SnapshotError,sqlite3.Error,KeyError)):service.restore(backup,tmp_path/'must-not-complete')
    assert stamps(backup)==before and not (tmp_path/'must-not-complete').exists()


def test_target_overlap_schema_and_source_change_are_rejected(library,tmp_path):
    service,profile,repo,snap,projects,tasks=library
    for target in [profile/'backup',profile/ARCHIVE_RELATIVE/'backup',ROOT/'forbidden-backup']:
        with pytest.raises(SnapshotError):service.create(profile,target)
        assert not target.exists()
    existing=tmp_path/'existing';existing.mkdir();(existing/'keep.txt').write_text('keep')
    with pytest.raises(SnapshotError):service.create(profile,existing)
    assert (existing/'keep.txt').read_text()=='keep'
    with repo.database.connect() as con:con.execute('UPDATE schema_meta SET version=14')
    with pytest.raises(SnapshotError,match='Schema'):service.inspect(profile)
    with sqlite3.connect(repo.database.path) as con:assert con.execute('SELECT version FROM schema_meta').fetchone()[0]==14


def test_cancel_after_commit_is_successful_and_not_deleted(library,tmp_path):
    service,profile,repo,snap,projects,tasks=library;event=threading.Event();backup=tmp_path/'committed'
    service.checkpoint=lambda p:event.set() if p=='backup_after_commit' else None
    assert service.create(profile,backup,cancel=event)['backup_id']
    service.checkpoint=lambda p:event.set() if p=='restore_after_commit' else None;event.clear()
    result=service.restore(backup,tmp_path/'restored',cancel=event);assert result['state']=='complete'


BACKUP_POINTS=['backup_frozen','backup_stage_created','backup_database_copied','backup_copy_chunk','backup_file_copied',
               'backup_files_verified','backup_manifest_written','backup_sealed','backup_before_commit','backup_after_commit','backup_journal_complete']
RESTORE_POINTS=['restore_frozen','restore_stage_created','restore_copy_chunk','restore_file_copied','restore_database_copied',
                'restore_before_database_commit','restore_after_database_commit','restore_database_rebound','restore_receipt_written',
                'restore_sealed','restore_before_commit','restore_after_commit','restore_journal_complete']


def probe(mode,source,target,task,point=''):
    return subprocess.run([sys.executable,str(ROOT/'scripts/backup_crash_probe.py'),'--mode',mode,'--source',str(source),
                           '--destination',str(target),'--task',task,'--point',point],cwd=ROOT,capture_output=True,timeout=45)


@pytest.mark.parametrize('point',BACKUP_POINTS)
def test_backup_hard_exit_and_fresh_process_retry(library,tmp_path,point):
    service,profile,repo,snap,projects,tasks=library;target=tmp_path/'hard-backup';task=str(uuid.uuid4())
    result=probe('backup',profile,target,task,point);assert result.returncode==73,result.stderr.decode(errors='replace')
    result=probe('backup',profile,target,task);assert result.returncode==0,result.stderr.decode(errors='replace')
    assert service.verify(target)['backup_id']==task
    result=probe('backup',profile,target,task);assert result.returncode==0,result.stderr.decode(errors='replace')


@pytest.mark.parametrize('point',RESTORE_POINTS)
def test_restore_hard_exit_and_fresh_process_retry(library,tmp_path,point):
    service,profile,repo,snap,projects,tasks=library;backup=tmp_path/'backup';service.create(profile,backup)
    target=tmp_path/'hard-restore';task=str(uuid.uuid4());before=stamps(backup)
    result=probe('restore',backup,target,task,point);assert result.returncode==73,result.stderr.decode(errors='replace')
    result=probe('restore',backup,target,task);assert result.returncode==0,result.stderr.decode(errors='replace')
    new=SnapshotService(Repository(Database(target/DATABASE_RELATIVE)),target/ARCHIVE_RELATIVE)
    assert all(new.verify(t) for t in tasks) and stamps(backup)==before


@pytest.mark.parametrize('mode',['client','writer'])
def test_cross_process_client_or_sql_writer_prevents_maintenance(library,tmp_path,mode):
    service,profile,repo,snap,projects,tasks=library
    if mode=='client':
        code="from mechanical_project_namer.library_lock import library_lease; from pathlib import Path; import sys; lease=library_lease(Path(sys.argv[1])); lease.__enter__(); print('READY',flush=True); sys.stdin.readline(); lease.__exit__(None,None,None)"
    else:
        code="import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('BEGIN IMMEDIATE'); print('READY',flush=True); sys.stdin.readline(); c.rollback(); c.close()"
    proc=subprocess.Popen([sys.executable,'-c',code,str(repo.database.path)],cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert proc.stdout.readline().strip()=='READY'
        with pytest.raises(MaintenanceBusy):service.inspect(profile)
        assert not (tmp_path/'never-created').exists()
    finally:
        proc.communicate('\n',timeout=10)
    assert service.inspect(profile)['versions']==4


def test_database_layer_rejects_write_during_exclusive_maintenance(library):
    service,profile,repo,snap,projects,tasks=library
    with library_lease(repo.database.path,exclusive=True):
        code="from mechanical_project_namer.data import Database; from mechanical_project_namer.library_lock import MaintenanceBusy; from pathlib import Path; import sys\ntry: Database(Path(sys.argv[1])).connect()\nexcept MaintenanceBusy: print('DENIED')\nelse: raise AssertionError('unguarded database access')"
        p=subprocess.run([sys.executable,'-c',code,str(repo.database.path)],cwd=ROOT,capture_output=True,text=True,timeout=10)
        assert p.returncode==0 and 'DENIED' in p.stdout,p.stderr


def test_nested_connection_lifetime_is_not_released_out_of_order(library):
    service,profile,repo,snap,projects,tasks=library
    first=repo.database.connect();second=repo.database.connect();first.close()
    try:
        # Another thread must still see the OS shared lease held by second.
        result=[]
        def attempt():
            try:
                with library_lease(repo.database.path,exclusive=True):result.append('unsafe')
            except MaintenanceBusy:result.append('denied')
        thread=threading.Thread(target=attempt);thread.start();thread.join(5);assert result==['denied']
    finally:second.close()
    assert service.inspect(profile)['versions']==4


@pytest.mark.parametrize('fault',['copy','space','cancel'])
def test_restore_failure_has_no_complete_target_and_can_retry(library,tmp_path,monkeypatch,fault):
    service,profile,repo,snap,projects,tasks=library;backup=tmp_path/'backup';service.create(profile,backup)
    before=stamps(backup);target=tmp_path/'retry';task=str(uuid.uuid4());event=threading.Event()
    if fault=='copy':monkeypatch.setattr('mechanical_project_namer.library_backups.copy_hash',lambda *a,**k:(_ for _ in ()).throw(PermissionError('FICTIONAL denied')))
    if fault=='space':monkeypatch.setattr('mechanical_project_namer.library_backups._space',lambda *a:(_ for _ in ()).throw(SnapshotError('space')))
    if fault=='cancel':event.set()
    with pytest.raises((OSError,SnapshotError)):service.restore(backup,target,task_id=task,cancel=event)
    assert not target.exists() and stamps(backup)==before
    monkeypatch.undo();event.clear();assert service.restore(backup,target,task_id=task)['state']=='complete'


def test_retry_rejects_source_change_and_preserves_failed_task_journal(library,tmp_path):
    service,profile,repo,snap,projects,tasks=library;task=str(uuid.uuid4());target=tmp_path/'backup'
    service.checkpoint=lambda p:(_ for _ in ()).throw(OSError('FICTIONAL failure')) if p=='backup_database_copied' else None
    with pytest.raises(OSError):service.create(profile,target,task_id=task)
    with repo.database.connect() as con:con.execute("UPDATE projects SET description='changed after frozen task'")
    service.checkpoint=lambda p:None
    with pytest.raises(SnapshotError,match='变化'):service.create(profile,target,task_id=task)
    journal=read_metadata(tmp_path/('.mw-library-job-'+task)/'journal.json')[0]
    assert any('FICTIONAL failure' in e.get('error','') for e in journal['events'])
    assert service.create(profile,target)['backup_id']!=task


def test_new_location_and_links_are_strictly_rejected(library,tmp_path):
    service,profile,repo,snap,projects,tasks=library;backup=tmp_path/'backup';service.create(profile,backup)
    exists=tmp_path/'existing';exists.mkdir()
    for target in [exists,profile/'new',backup/'new',ROOT/'never-created']:
        with pytest.raises(SnapshotError):service.restore(backup,target)
    junction=tmp_path/'junction'
    if os.name=='nt':
        import _winapi;_winapi.CreateJunction(str(backup),str(junction))
    else:junction.symlink_to(backup,target_is_directory=True)
    try:
        with pytest.raises(ValueError):service.verify(junction)
        with pytest.raises(ValueError):service.restore(backup,junction/'new')
    finally:
        if os.name=='nt':junction.rmdir()
        else:junction.unlink()


def test_full_library_limits_are_distinct_from_single_version_or_preview_limits():
    entries=[dict(path=f'archive/part{i}',size=2*1024**3,sha256='0'*64) for i in range(9)]
    assert check_entries(entries)==18*1024**3
    for entry in [dict(path='../escape',size=1,sha256='0'*64),dict(path='C:/escape',size=1,sha256='0'*64),
                  dict(path='part:stream',size=1,sha256='0'*64),dict(path='CON.txt',size=1,sha256='0'*64),
                  dict(path='large',size=32*1024**3+1,sha256='0'*64)]:
        with pytest.raises(SnapshotError):check_entries([entry])
    with pytest.raises(SnapshotError):check_entries([dict(path=f'large{i}',size=32*1024**3,sha256='0'*64) for i in range(9)])


def test_backup_dialog_independent_of_damaged_database_and_lifecycle(library,tmp_path,qtbot):
    from mechanical_project_namer.ui.backups_dialog import BackupsDialog
    service,profile,repo,snap,projects,tasks=library;backup=tmp_path/'backup';service.create(profile,backup)
    repo.database.path.write_bytes(b'FICTIONAL DAMAGED DATABASE')
    dialog=BackupsDialog(str(profile));qtbot.addWidget(dialog);dialog.show();dialog.backup.setText(str(backup))
    dialog.buttons['验证备份'].click();qtbot.waitUntil(lambda:not dialog.busy,timeout=15000)
    assert '操作完成' in dialog.status.text() and '完整版本：4' in dialog.details.toPlainText()
    dialog.target.setText(str(tmp_path/'gui-restored'));dialog.buttons['整体恢复到新目录'].click();qtbot.waitUntil(lambda:not dialog.busy,timeout=15000)
    assert '操作完成' in dialog.status.text() and '--dev-root' in dialog.command.text()
    entered=threading.Event()
    def until_cancel(cancel,progress):
        entered.set();assert cancel.wait(5);raise Cancelled('FICTIONAL UI cancel')
    dialog.start('verify',until_cancel);assert entered.wait(2);dialog.cancel_button.click();qtbot.waitUntil(lambda:not dialog.busy,timeout=10000)
    assert '未成功' in dialog.status.text() and 'cancel' in dialog.details.toPlainText()


def test_maintenance_session_does_not_nest_inside_inherited_source_profile(tmp_path,monkeypatch):
    from mechanical_project_namer.backup_entry import default_session_root
    source=tmp_path/'source-profile';monkeypatch.setenv('MECHANICAL_PROJECT_NAMER_DEV_ROOT',str(source))
    monkeypatch.setenv('LOCALAPPDATA',str(source/'local'))
    session=default_session_root()
    assert not session.is_relative_to(source) and session.parent==source.parent


def test_external_hard_link_cannot_pretend_to_be_an_independent_backup(library,tmp_path):
    service,profile,repo,snap,projects,tasks=library;backup=tmp_path/'backup';doc=service.create(profile,backup)
    payload=next(e['path'] for e in doc['files'] if '/payload/' in e['path'])
    alias=tmp_path/'external-alias';os.link(backup/'data'/payload,alias)
    assert alias.stat().st_nlink==2
    with pytest.raises(SnapshotError,match='硬链接'):service.verify(backup)
    assert alias.is_file() and (backup/'data'/payload).is_file()


def test_metadata_scope_is_bounded_before_loading_large_project_sets(library,tmp_path):
    service,profile,repo,snap,projects,tasks=library
    with repo.database.transaction() as con:
        original=dict(con.execute('SELECT * FROM projects LIMIT 1').fetchone());keys=list(original)
        values=[]
        for i in range(10001):
            row=dict(original,id=i+100,code='F'+str(i),name='FICTIONAL capacity row '+str(i),directory='',solidworks_part_directory='')
            values.append(tuple(row[k] for k in keys))
        con.executemany('INSERT INTO projects('+','.join('"'+k+'"' for k in keys)+') VALUES ('+','.join('?' for k in keys)+')',values)
    with pytest.raises(SnapshotError,match='数量超限'):service.create(profile,tmp_path/'reject-over-capacity')
    assert not (tmp_path/'reject-over-capacity').exists()


@pytest.mark.parametrize('field,value',[('exclusions',123),('origin',[]),('created_at',{})])
def test_malformed_metadata_is_rejected_before_gui_result_rendering(library,tmp_path,field,value):
    service,profile,repo,snap,projects,tasks=library;backup=tmp_path/'backup';service.create(profile,backup)
    rewrite_manifest(backup,lambda doc:doc.update({field:value}))
    with pytest.raises(SnapshotError):service.verify(backup)
    with pytest.raises(SnapshotError):service.restore(backup,tmp_path/'not-created')
    assert not (tmp_path/'not-created').exists()


@pytest.mark.parametrize('mode',['backup','restore'])
def test_sealed_payload_cannot_change_its_task_binding(library,tmp_path,mode):
    service,profile,repo,snap,projects,tasks=library;task=str(uuid.uuid4());target=tmp_path/'target'
    if mode=='backup':source=profile;point='backup_sealed'
    else:
        source=tmp_path/'backup';service.create(profile,source);point='restore_sealed'
    result=probe(mode,source,target,task,point);assert result.returncode==73,result.stderr.decode(errors='replace')
    job=tmp_path/('.mw-library-job-'+task);journal=read_metadata(job/'journal.json')[0]
    stage=job/journal['attempt']
    if mode=='backup':rewrite_manifest(stage/'backup-set',lambda doc:doc.update(scope=doc['scope']+' changed'))
    else:
        receipt=stage/'profile/.mw-library-restore.json';doc=read_metadata(receipt)[0];doc['allowed_changes']['extra']='FICTIONAL tamper'
        write_metadata(receipt,doc,replace=True)
    fn=service.create if mode=='backup' else service.restore
    with pytest.raises(SnapshotError,match='封存'):fn(source,target,task_id=task)
    assert not target.exists()


def test_cancel_at_rebinding_database_commit_rolls_back_and_retries(library,tmp_path):
    service,profile,repo,snap,projects,tasks=library;backup=tmp_path/'backup';service.create(profile,backup)
    before=stamps(backup);task=str(uuid.uuid4());target=tmp_path/'restored';event=threading.Event()
    service.checkpoint=lambda p:event.set() if p=='restore_before_database_commit' else None
    with pytest.raises(Cancelled):service.restore(backup,target,task_id=task,cancel=event)
    assert not target.exists() and stamps(backup)==before
    job=tmp_path/('.mw-library-job-'+task);journal=read_metadata(job/'journal.json')[0]
    with readonly_database(job/journal['attempt']/'profile'/DATABASE_RELATIVE) as con:
        assert {r[0] for r in con.execute('SELECT store_path FROM local_versions')}=={str(profile/ARCHIVE_RELATIVE)}
    service.checkpoint=lambda p:None;event.clear()
    assert service.restore(backup,target,task_id=task,cancel=event)['state']=='complete'
