# SPDX-License-Identifier: GPL-3.0-only
import hashlib,json,os,sqlite3,subprocess,sys,threading,uuid
from pathlib import Path
from unittest.mock import patch
import pytest
from mechanical_project_namer.data import Database,Repository
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.project_files import ProjectFileCatalog
from mechanical_project_namer.snapshots import SnapshotService,SnapshotError,Cancelled,role_for
from mechanical_project_namer.snapshot_io import relative,safe_path,digest

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path):
    db=Database(tmp_path/'data.db');db.migrate();repo=Repository(db)
    work=tmp_path/'工程 中文';work.mkdir()
    paths=[work/'子装配A/同名.prt',work/'子装配B/同名.SLDPRT',work/'原名.step',work/'说明.txt']
    for i,p in enumerate(paths):p.parent.mkdir(exist_ok=True);p.write_bytes(('FICTIONAL PLACEHOLDER, NOT VALID CAD '+str(i)).encode())
    project=ProjectService(repo).create('虚构项目','','DEMO',directory=str(work))
    catalog=ProjectFileCatalog(repo);catalog.register_attachments(project.id,[str(p) for p in paths])
    choices=[dict(file_id=r['file_id']) for r in catalog.list_files(project.id)]
    service=SnapshotService(repo,tmp_path/'archive')
    (tmp_path/'.snapshot-test-probe').write_text('test-only')
    return service,repo,project,work,paths,choices


def prepare(env,label='V1',**kw):
    s,r,p,w,files,choices=env;return s.prepare(p.id,w,choices,label,'虚构测试',**kw)


def business(repo):
    with repo.database.connect() as c:return {n:[tuple(r) for r in c.execute('SELECT * FROM '+n)] for n in ['projects','project_modules','drawing_records','number_reservations','part_sequence_counters']}


def test_versions_restore_identity_roles_and_no_number_allocation(env,tmp_path):
    s,repo,p,work,paths,choices=env
    duplicate=work/'子装配B/同名.prt';duplicate.write_bytes(b'FICTIONAL second directory, same full filename')
    catalog=ProjectFileCatalog(repo);catalog.register_attachments(p.id,[str(duplicate)]);paths.append(duplicate)
    choices[:]=[dict(file_id=r['file_id']) for r in catalog.list_files(p.id)]
    before=business(repo);stamps={f:(f.read_bytes(),f.stat().st_mtime_ns) for f in paths}
    t=prepare(env);row=s.run(t);assert row['state']=='complete'
    assert s.run(t)['version_id']==row['version_id'];assert s.prepare(p.id,work,choices,'V1','虚构测试',task_id=t)==t
    assert business(repo)==before
    assert all((f.read_bytes(),f.stat().st_mtime_ns)==v for f,v in stamps.items())
    folder=s.store/'versions'/row['version_id'];manifest=json.loads((folder/'manifest.json').read_text())
    assert manifest['dependency_status']==manifest['preview_status']=='unverified'
    assert not any(str(work) in str(v) for v in manifest.values())
    assert all(f['software']=='未知' for f in manifest['files'])
    assert {f['role'] for f in manifest['files']}=={'source','exchange','attachment'}
    repo.update_project(p.id,name='改名后项目');assert s.history(p.id)[0]['version_id']==row['version_id']
    paths[0].write_bytes(b'FICTIONAL V2');t2=prepare(env,'V2');s.run(t2);assert s.verify(t)
    dest=tmp_path/'恢复 中文';restore=s.restore(t,dest);assert restore['state']=='complete'
    assert (dest/'子装配A/同名.prt').read_bytes()==stamps[paths[0]][0]
    assert (dest/'子装配B/同名.prt').read_bytes()==stamps[duplicate][0]
    assert (dest/'子装配B/同名.SLDPRT').is_file() and (dest/'原名.step').is_file()
    assert json.loads((dest/'.mw-restore.json').read_text())['cad_openability']=='unverified'
    new=SnapshotService(Repository(Database(repo.database.path)),s.store);assert len(new.history(p.id))==2
    assert new.restore(t,dest,restore_id=restore['restore_id'])['state']=='complete'


def test_binding_label_conflict_required_and_explicit_new_versions(env):
    s,r,p,w,files,choices=env;t=prepare(env)
    with pytest.raises(SnapshotError,match='标签'):prepare(env)
    with pytest.raises(SnapshotError,match='变化'):s.prepare(p.id,w,choices,'DIFFERENT',task_id=t)
    with pytest.raises(SnapshotError,match='必需'):prepare(env,'missing',required=['missing.sldprt'])
    t2=prepare(env,'SAME CONTENT NEW LABEL');s.run(t);s.run(t2)
    assert len(s.history(p.id,complete_only=True))==2


@pytest.mark.parametrize('change',['content','mtime','missing','unreadable'])
def test_source_changes_never_complete(env,change):
    s,r,p,w,files,choices=env;t=prepare(env)
    if change=='content':files[0].write_bytes(b'different')
    if change=='mtime':os.utime(files[0],ns=(files[0].stat().st_atime_ns,files[0].stat().st_mtime_ns+10000000))
    if change=='missing':files[0].unlink()
    def deny(self,*a,**k):
        if self==files[0]:raise PermissionError('simulated unreadable')
        return original(self,*a,**k)
    original=Path.open
    with patch.object(Path,'open',deny) if change=='unreadable' else __import__('contextlib').nullcontext():
        with pytest.raises((SnapshotError,OSError)):s.run(t)
    assert not s.history(p.id,complete_only=True)


@pytest.mark.parametrize('fault',['copy','space','cancel','during-copy','db-complete'])
def test_failures_cancel_and_commit_boundary(env,fault,monkeypatch):
    s,r,p,w,files,choices=env;t=prepare(env);event=threading.Event();original=s._update
    if fault=='copy':monkeypatch.setattr('mechanical_project_namer.snapshots.digest',lambda *a,**k:(_ for _ in ()).throw(OSError('copy failed')))
    if fault=='space':monkeypatch.setattr(s,'_space',lambda *a:(_ for _ in ()).throw(SnapshotError('space')))
    if fault=='cancel':event.set()
    if fault=='during-copy':s.checkpoint=lambda point:files[0].write_bytes(b'CHANGED') if point=='file_copied' else None
    if fault=='db-complete':
        def fail(key,*a,**kw):
            if kw.get('state')=='complete':raise sqlite3.OperationalError('db commit failed')
            return original(key,*a,**kw)
        monkeypatch.setattr(s,'_update',fail)
    with pytest.raises((OSError,ValueError,sqlite3.Error)):s.run(t,cancel=event)
    assert not s.history(p.id,complete_only=True)
    if fault=='db-complete':
        monkeypatch.setattr(s,'_update',original);assert s.run(t)['state']=='complete'


def test_cancel_after_commit_finishes_and_cannot_abort(env):
    s,r,p,w,files,choices=env;event=threading.Event();t=prepare(env)
    s.checkpoint=lambda point:event.set() if point=='fs_committed' else None
    assert s.run(t,cancel=event)['state']=='complete'
    with pytest.raises(SnapshotError,match='已提交'):s.abort(t)


@pytest.mark.parametrize('damage',['hash','manifest','missing','extra','db','directory'])
def test_verify_and_restore_refuse_damage(env,tmp_path,damage):
    s,r,p,w,files,choices=env;t=prepare(env);row=s.run(t);folder=s.store/'versions'/row['version_id']
    if damage=='hash':(folder/'payload/原名.step').write_bytes(b'corrupt')
    if damage=='manifest':(folder/'manifest.json').write_text('{}')
    if damage=='missing':(folder/'payload/原名.step').unlink()
    if damage=='extra':(folder/'payload/extra.txt').write_text('x')
    if damage=='directory':(folder/'payload/extra').mkdir()
    if damage=='db':
        with r.database.connect() as c:c.execute('UPDATE local_versions SET manifest_sha=? WHERE task_id=?',('0'*64,t))
    with pytest.raises((SnapshotError,OSError)):s.verify(t)
    with pytest.raises((SnapshotError,OSError)):s.restore(t,tmp_path/'badrestore')
    assert not (tmp_path/'badrestore').exists() and not s.history(p.id,complete_only=True)


@pytest.mark.parametrize('path',['../escape','/absolute','C:/abs','C:relative','a\\b','a:ads','CON.txt','x/NUL','x/COM1.prt','bad.','bad ','a//b','.mw-receipt','x/../b','a\x00b'])
def test_manifest_path_rejection(path):
    with pytest.raises(SnapshotError):relative(path)


def test_case_conflict_links_nonempty_and_file_limits(env,tmp_path,monkeypatch):
    from mechanical_project_namer.snapshot_io import validate_entries
    def f(path):return dict(relative_path=path,role='source',size=1,sha256='0'*64)
    for entries in [[f('A.prt'),f('a.PRT')],[f('a'),f('A/b')]]:
        with pytest.raises(SnapshotError):validate_entries(entries)
    with pytest.raises(SnapshotError):validate_entries([f('a')]*2001)
    with pytest.raises(SnapshotError):validate_entries([{**f('a'),'size':2*1024**3+1}])
    s,r,p,w,files,choices=env;t=prepare(env);s.run(t);dest=tmp_path/'exists';dest.mkdir();(dest/'mine').write_text('keep')
    with pytest.raises(SnapshotError):s.restore(t,dest)
    assert (dest/'mine').read_text()=='keep'
    with pytest.raises(SnapshotError):s.restore(t,w/'newchild')
    with pytest.raises(SnapshotError):s.restore(t,ROOT/'forbidden-test-output')
    assert not (ROOT/'forbidden-test-output').exists()
    # Directory junction needs no Windows symlink privilege; only test-owned paths.
    junction=tmp_path/'junction'
    if os.name=='nt':
        import _winapi;_winapi.CreateJunction(str(w),str(junction))
    else:junction.symlink_to(w,target_is_directory=True)
    try:
        with pytest.raises(ValueError):safe_path(junction/files[0].relative_to(w))
    finally:
        if os.name=='nt':junction.rmdir()
        else:junction.unlink()


def test_project_lock_and_unowned_store(env,tmp_path):
    from mechanical_project_namer.snapshots import project_lock
    s,r,p,w,files,choices=env;t=prepare(env)
    with project_lock(s.store/'locks',p.id):
        with pytest.raises(SnapshotError,match='已有任务'):s.run(t)
    unknown=tmp_path/'unknown';unknown.mkdir();(unknown/'user.txt').write_text('keep')
    with pytest.raises(SnapshotError):SnapshotService(r,unknown)
    with pytest.raises(SnapshotError):SnapshotService(r,ROOT/'forbidden-test-store')
    assert not (ROOT/'forbidden-test-store').exists()
    assert (unknown/'user.txt').read_text()=='keep'


SNAP_POINTS=['stage_created','copy_chunk','file_copied','files_verified','manifest_written','sealed','fs_before_commit','fs_committed','db_before_complete','db_complete']
RESTORE_POINTS=['restore_before_db','restore_prepared','restore_stage_created','restore_copy_chunk','restore_file_copied','restore_marker_written','restore_sealed','restore_fs_before_commit','restore_fs_committed','restore_db_before_complete','restore_db_complete']


def probe(env,task,point='',restore_id=None,dest=None):
    s,r,p,w,files,choices=env
    cmd=[sys.executable,str(ROOT/'scripts/snapshot_crash_probe.py'),'--db',str(r.database.path),'--store',str(s.store),'--task',task,'--point',point]
    if restore_id:cmd+=['--restore-id',restore_id,'--destination',str(dest)]
    return subprocess.run(cmd,capture_output=True,timeout=30)


@pytest.mark.parametrize('point',SNAP_POINTS)
def test_hard_process_exit_then_fresh_snapshot_recovery(env,point):
    s,r,p,w,files,choices=env;t=prepare(env)
    result=probe(env,t,point);assert result.returncode==73,result.stderr.decode(errors='replace')
    assert len(s.history(p.id,complete_only=True))==(1 if point=='db_complete' else 0)
    result=probe(env,t);assert result.returncode==0,result.stderr.decode(errors='replace')
    assert len(s.history(p.id,complete_only=True))==1 and s.verify(t)


@pytest.mark.parametrize('point',RESTORE_POINTS)
def test_hard_process_exit_then_fresh_restore_recovery(env,tmp_path,point):
    s,r,p,w,files,choices=env;t=prepare(env);s.run(t);rid=str(uuid.uuid4());dest=tmp_path/'恢复'
    result=probe(env,t,point,rid,dest);assert result.returncode==73,result.stderr.decode(errors='replace')
    result=probe(env,t,'',rid,dest);assert result.returncode==0,result.stderr.decode(errors='replace')
    assert (dest/'原名.step').read_bytes()==files[2].read_bytes()


def test_abort_preserves_stage_and_retry_id(env):
    s,r,p,w,files,choices=env;t=prepare(env);s.abort(t);assert s._get(t)['state']=='cancelled'
    assert s.run(t)['state']=='complete'


def test_chunked_read_no_link_copy(env,tmp_path):
    s,r,p,w,files,choices=env;paths=files
    paths[0].write_bytes(b'x'*(3*1024**2+17));t=prepare(env);row=s.run(t)
    target=s.store/'versions'/row['version_id']/'payload'/paths[0].relative_to(w)
    assert target.stat().st_ino!=paths[0].stat().st_ino and target.read_bytes()==paths[0].read_bytes()


@pytest.mark.parametrize('point',['prepare_before_db','prepared'])
def test_hard_exit_during_prepare(env,tmp_path,point):
    s,r,p,w,files,choices=env;t=str(uuid.uuid4());request=tmp_path/'request.json'
    request.write_text(json.dumps(dict(project_id=p.id,root=str(w),selections=choices,label='V1')),'utf-8')
    cmd=[sys.executable,str(ROOT/'scripts/snapshot_crash_probe.py'),'--db',str(r.database.path),'--store',str(s.store),'--task',t,'--request',str(request),'--point',point]
    result=subprocess.run(cmd,capture_output=True,timeout=30);assert result.returncode==73,result.stderr.decode(errors='replace')
    cmd=cmd[:-2];result=subprocess.run(cmd,capture_output=True,timeout=30);assert result.returncode==0,result.stderr.decode(errors='replace')
    assert len(s.history(p.id,complete_only=True))==1


def test_schema12_migration_backup_preserves_legacy_data(tmp_path):
    db=Database(tmp_path/'old.db')
    with db.connect() as c:c.executescript((ROOT/'tests/fixtures/schema_v12.sql').read_text('utf-8'));c.execute('INSERT INTO schema_meta VALUES(12)')
    r=Repository(db);p=ProjectService(r).create('虚构旧版','','LEGACY');record=r.save_named_record(p.id,'01','自制机加工件','虚构历史件');r.set_record_status(record.id,'void');before=business(r)
    db.migrate();db.migrate();assert business(r)==before
    backups=list((tmp_path/'backups/database').glob('*pre_schema13*.db'));assert len(backups)==1
    with sqlite3.connect(backups[0]) as c:
        assert c.execute('SELECT version FROM schema_meta').fetchone()[0]==12
        assert not c.execute("SELECT name FROM sqlite_master WHERE name='local_versions'").fetchall()
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    with db.connect() as c:
        assert c.execute('SELECT version FROM schema_meta').fetchone()[0]==13
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_change_or_cancel_inside_chunk_preserves_old_version(env):
    s,r,p,w,files,choices=env;v1=prepare(env);s.run(v1)
    files[2].write_bytes(b'A'*(3*1024**2));t=prepare(env,'V2')
    def mutate(point):
        if point=='copy_chunk':files[2].write_bytes(b'B'*(3*1024**2))
    s.checkpoint=mutate
    with pytest.raises(SnapshotError):s.run(t)
    assert s.verify(v1) and len(s.history(p.id,complete_only=True))==1
    s.checkpoint=lambda point:None;t=prepare(env,'V3');cancel=threading.Event();s.checkpoint=lambda point:cancel.set() if point=='copy_chunk' else None
    with pytest.raises(Cancelled):s.run(t,cancel=cancel)
    assert s._get(t)['state']=='cancelled' and s.verify(v1)


@pytest.mark.parametrize('where',['label','note','software'])
def test_local_absolute_paths_never_enter_portable_metadata(env,where):
    s,r,p,w,files,choices=env
    if where=='software':choices=[{**f,'software':str(w)} for f in choices]
    with pytest.raises(SnapshotError):s.prepare(p.id,w,choices,str(w) if where=='label' else 'V1',str(w) if where=='note' else '')


def test_restore_failure_and_cancel_retry(env,tmp_path):
    s,r,p,w,files,choices=env;t=prepare(env);s.run(t);rid=str(uuid.uuid4());dest=tmp_path/'new';cancel=threading.Event()
    s.checkpoint=lambda point:cancel.set() if point=='restore_file_copied' else None
    with pytest.raises(Cancelled):s.restore(t,dest,restore_id=rid,cancel=cancel)
    assert not dest.exists() and s._get(rid,True)['state']!='complete'
    s.checkpoint=lambda point:None;assert s.resume_restore(rid)['state']=='complete'


def test_actual_parallel_requests_are_rejected_not_overwritten(env):
    s,r,p,w,files,choices=env;t1=prepare(env);t2=prepare(env,'V2');entered=threading.Event();release=threading.Event();errors=[]
    def stop(point):
        if point=='stage_created':entered.set();assert release.wait(10)
    s.checkpoint=stop
    def first():
        try:s.run(t1)
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=first);thread.start();assert entered.wait(5)
    try:
        other=SnapshotService(r,s.store)
        with pytest.raises(SnapshotError,match='已有任务'):other.run(t2)
    finally:release.set();thread.join(10)
    assert not thread.is_alive() and not errors
    assert other.run(t2)['state']=='complete' and s.verify(t1)


def test_restore_db_failure_after_filesystem_commit_recovers(env,tmp_path,monkeypatch):
    s,r,p,w,files,choices=env;t=prepare(env);s.run(t);rid=str(uuid.uuid4());dest=tmp_path/'recover-db-failure';original=s._update
    def fail(key,restore=False,**kw):
        if restore and kw.get('state')=='complete':raise sqlite3.OperationalError('commit failure')
        return original(key,restore,**kw)
    monkeypatch.setattr(s,'_update',fail)
    with pytest.raises(sqlite3.Error):s.restore(t,dest,restore_id=rid)
    assert dest.is_dir() and s._get(rid,True)['state']!='complete'
    monkeypatch.setattr(s,'_update',original);assert s.resume_restore(rid)['state']=='complete'


def test_corrupt_frozen_database_request_is_not_silently_used(env):
    s,r,p,w,files,choices=env;t=prepare(env)
    with r.database.connect() as c:c.execute('UPDATE local_versions SET request_json=? WHERE task_id=?',('{}',t))
    with pytest.raises(SnapshotError,match='记录损坏'):s.run(t)


@pytest.mark.parametrize('name,role',[('未知.prt','source'),('装配.SLDASM','source'),('旧名.STEP','exchange'),('副本.stp','exchange'),('模型.GLB','preview'),('工程图.PDF','drawing'),('旧网格.stl','attachment')])
def test_explicit_file_role_inference(name,role):assert role_for(name)==role


def test_dialog_background_create_cancel_close_and_history(env,qtbot,tmp_path):
    from mechanical_project_namer.ui.snapshots_dialog import SnapshotsDialog
    from PyQt6.QtCore import Qt
    from PyQt6 import sip
    s,r,p,w,files,choices=env;dialog=SnapshotsDialog(r,p.id,store=s.store)
    # This test owns close/delete explicitly; pytest-qt must not close the deleted wrapper again.
    try:
        dialog.root.setText(str(w));dialog.label.setText('V1');dialog.show()
        dialog.create_button.click();qtbot.waitUntil(lambda:not dialog.busy,timeout=10000)
        assert len(dialog.rows)==1 and dialog.rows[0]['state']=='complete' and '没有GLB' in dialog.summary.text()
        assert dialog.history.item(0,3).text()=='未验证 / 未验证'
        dialog.history.selectRow(0);dialog.target.setText(str(tmp_path/'ui-recovery'));dialog.restore_selected();qtbot.waitUntil(lambda:not dialog.busy,timeout=10000)
        assert (tmp_path/'ui-recovery/原名.step').is_file() and 'CAD可打开性未验证' in dialog.status.text()
        dialog.label.setText('V2')
        entered=threading.Event()
        def wait_cancel(cancel,progress):
            entered.set()
            assert cancel.wait(5)
            raise Cancelled('test cancel')
        dialog.start(wait_cancel);assert entered.wait(2);dialog.cancel_operation();qtbot.waitUntil(lambda:not dialog.busy,timeout=10000)
        assert len(dialog.rows)==1
    finally:
        dialog.close();qtbot.waitUntil(lambda:sip.isdeleted(dialog),timeout=10000)


def test_added_attachments_are_not_executed(env,qtbot):
    from mechanical_project_namer.ui.project_files_page import ProjectFilesPage
    s,r,p,w,files,choices=env;page=ProjectFilesPage(r);qtbot.addWidget(page);page.load_projects(p.id)
    for i,row in enumerate(page.rows):
        if row['extension']=='.prt':page.table.selectRow(i);break
    called=[];page.open_selected(opener=lambda *a:called.append(a))
    assert not called and '不启动关联程序' in page.status.text()


@pytest.mark.parametrize('name',['CON','NUL.txt','COM1','COM¹.prt','bad.','bad ','bad:ads'])
def test_destination_local_paths_reject_device_aliases(tmp_path,name):
    with pytest.raises(SnapshotError):safe_path(tmp_path/name)


def test_disk_and_database_complete_record_mismatch_is_detected(env):
    s,r,p,w,files,choices=env;t=prepare(env);row=s.run(t)
    # Quarantine rename only a test-owned version; never remove history/source.
    folder=s.store/'versions'/row['version_id'];folder.rename(folder.with_name(folder.name+'-test-quarantine'))
    with pytest.raises(OSError):s.verify(t)
    assert s._get(t)['integrity']=='failed' and not s.history(p.id,complete_only=True)


def test_reopened_dialog_exposes_pending_task_resume_and_abort(env,qtbot):
    from mechanical_project_namer.ui.snapshots_dialog import SnapshotsDialog
    s,r,p,w,files,choices=env;t=prepare(env);dialog=SnapshotsDialog(r,p.id,store=s.store);qtbot.addWidget(dialog)
    assert '发现1' in dialog.status.text() and dialog.rows[0]['task_id']==t
    dialog.history.selectRow(0);dialog.actions['恢复归档任务'].click();qtbot.waitUntil(lambda:not dialog.busy,timeout=10000)
    assert s._get(t)['state']=='complete'
    t2=prepare(env,'V2');dialog.refresh();dialog.history.selectRow(0);dialog.actions['终止归档任务'].click();qtbot.waitUntil(lambda:not dialog.busy,timeout=10000)
    assert s._get(t2)['state']=='cancelled' and s.verify(t)


def test_original_filename_casing_preserved_in_manifest(env):
    s,r,p,w,files,choices=env
    if os.name=='nt':
        with r.database.connect() as c:c.execute('UPDATE project_files SET path=? WHERE path=?',(str(files[2]).upper(),str(files[2])))
    t=prepare(env);row=s.run(t);doc=json.loads((s.store/'versions'/row['version_id']/'manifest.json').read_text())
    assert '原名.step' in [f['relative_path'] for f in doc['files']]


def test_retry_retains_failure_and_attempt_provenance(env):
    s,r,p,w,files,choices=env;t=prepare(env)
    def fail(point):
        if point=='file_copied':raise OSError('FICTIONAL copy interruption')
    s.checkpoint=fail
    with pytest.raises(OSError):s.run(t)
    s.checkpoint=lambda point:None;s.run(t)
    assert s._get(t)['error']==''
    events=s.events(t)
    assert any('FICTIONAL copy interruption' in e['details'] for e in events)
    assert sum('stage_name' in e['details'] for e in events)==2
    assert events[0]['state']=='complete'


def test_role_labels_do_not_pretend_glb_or_pdf_exists(env,qtbot):
    from mechanical_project_namer.ui.snapshots_dialog import SnapshotsDialog
    s,r,p,w,files,choices=env
    for c in choices:c['role']='preview' if c==choices[0] else 'drawing'
    t=s.prepare(p.id,w,choices,'V1');s.run(t)
    dialog=SnapshotsDialog(r,p.id,store=s.store);qtbot.addWidget(dialog)
    for i in range(len(dialog.file_rows)):
        combo=dialog.files.cellWidget(i,3);combo.setCurrentIndex(combo.findData('preview' if i==0 else 'drawing'))
    assert '没有GLB' in dialog.summary.text() and '没有PDF' in dialog.summary.text()
    assert '无GLB' in dialog.history.item(0,4).text() and '无PDF' in dialog.history.item(0,4).text()
