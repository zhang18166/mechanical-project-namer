# SPDX-License-Identifier: GPL-3.0-only
import json,os,subprocess,sys,uuid
from pathlib import Path
import pytest
from portable_server_backups import ServerRecovery,fingerprints
from server_fixture import make_service
from mechanical_project_namer.server_backups import ServerBackups
from mechanical_project_namer.publish_protocol import data,sha
from server_fault_cases import BACKUP_POINTS,RESTORE_POINTS,APPROVE_POINTS,hard_exit_case,approval_exit_case

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('action,point',[('backup',p) for p in BACKUP_POINTS]+[('restore',p) for p in RESTORE_POINTS])
def test_real_hard_exit_new_process_recovery(tmp_path,action,point):
    hard_exit_case(tmp_path,action,point)


@pytest.mark.parametrize('point',APPROVE_POINTS)
def test_approval_hard_exit_is_atomic(tmp_path,point):approval_exit_case(tmp_path,point)


@pytest.mark.parametrize('mutation',['missing','damaged','extra','manifest','owner','database','traversal','ads','case-conflict','schema'])
def test_backup_corruption_rejected(tmp_path,mutation):
    f=make_service(tmp_path/'source');service=ServerBackups();backup=tmp_path/'backup'
    service.create(f['config_path'],backup,task_id=str(uuid.uuid4()))
    doc=json.loads((backup/'manifest.json').read_bytes());payload=backup/'data/storage/versions'/f['task']/'0'
    if mutation=='missing':payload.rename(tmp_path/'removed')
    elif mutation=='damaged':payload.write_bytes(b'DAMAGED')
    elif mutation=='extra':(backup/'data/extra').write_bytes(b'UNEXPECTED')
    elif mutation=='manifest':(backup/'manifest.json').write_bytes(b'{DAMAGED')
    elif mutation=='owner':(backup/'data/storage/.publish-owner.json').write_bytes(data(dict(service=str(uuid.uuid4()))))
    elif mutation=='database':(backup/'data/index.sqlite').write_bytes(b'DAMAGED SQLITE')
    else:
        if mutation=='traversal':doc['files'][0]['path']='../escape'
        elif mutation=='ads':doc['files'][0]['path']='index.sqlite:stream'
        elif mutation=='case-conflict':doc['files'].append(dict(doc['files'][0],path=doc['files'][0]['path'].upper()))
        elif mutation=='schema':doc['index_schema']='999'
        (backup/'manifest.json').write_bytes(data(doc));(backup/'COMPLETE.json').write_bytes(data(dict(format=1,backup_id=doc['backup_id'],manifest_sha=sha(doc))))
    with pytest.raises((ValueError,OSError)):service.verify(backup)
    assert not (tmp_path/'escape').exists()


def test_independent_process_service_lock_blocks_backup(tmp_path):
    f=make_service(tmp_path/'source');ready=tmp_path/'ready.json'
    code="from mechanical_project_namer.library_lock import file_lease;import sys,time;from pathlib import Path\nwith file_lease(sys.argv[1],exclusive=True):\n Path(sys.argv[2]).write_text('ready');time.sleep(8)"
    path=Path(f['config']['lease_root'])/(f['config']['service']+'.lock')
    p=subprocess.Popen([sys.executable,'-c',code,str(path),str(ready)],cwd=ROOT)
    try:
        import time
        deadline=time.monotonic()+5
        while not ready.exists():assert time.monotonic()<deadline;time.sleep(.02)
        from mechanical_project_namer.library_lock import MaintenanceBusy
        with pytest.raises(MaintenanceBusy):ServerBackups().create(f['config_path'],tmp_path/'backup',task_id=str(uuid.uuid4()))
    finally:p.wait(12)


def test_schema2_loader_cannot_open_recovered_schema3(tmp_path):
    f=make_service(tmp_path/'source');service=ServerBackups();backup=tmp_path/'backup';target=tmp_path/'restored'
    service.create(f['config_path'],backup,task_id=str(uuid.uuid4()));service.restore(backup,target,task_id=str(uuid.uuid4()))
    old=tmp_path/'baseline_accounts.py'
    old.write_bytes(subprocess.check_output(['git','show','5561b8c8354cd371cf4d3ad28c5408bea6922d3e:mechanical_project_namer/accounts.py'],cwd=ROOT))
    code="import sys,json,importlib.util;s=importlib.util.spec_from_file_location('mechanical_project_namer._baseline_accounts',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\ntry:m.AccountStore(json.load(open(sys.argv[2])));raise AssertionError('old loader bypassed gate')\nexcept ValueError as e:assert 'unsupported-account-index-schema' in str(e)"
    before=fingerprints(target)
    r=subprocess.run([sys.executable,'-c',code,str(old),str(target/'server.json')],cwd=ROOT,capture_output=True,timeout=20)
    assert r.returncode==0,r.stderr.decode(errors='replace');assert fingerprints(target)==before


def test_real_hot_journal_old_reader_failure_then_rw_recovery(tmp_path):
    f=make_service(tmp_path/'source');index=Path(f['config']['index'])
    with f['store'].db() as c:before=c.execute('SELECT count(*) FROM auth_audit').fetchone()[0]
    writer="import sqlite3,sys,os;c=sqlite3.connect(sys.argv[1]);c.execute('PRAGMA synchronous=FULL');c.execute('PRAGMA cache_size=1');c.execute('BEGIN IMMEDIATE');c.execute(\"UPDATE tasks SET state='cancelled'\");\nfor i in range(80):c.execute(\"INSERT INTO auth_audit(action,details,created_at) VALUES ('UNCOMMITTED',?,'TEST')\",('X'*8192,))\nos._exit(73)"
    p=subprocess.run([sys.executable,'-c',writer,str(index)],capture_output=True,timeout=15);assert p.returncode==73
    assert Path(str(index)+'-journal').is_file()
    old=tmp_path/'baseline_accounts.py';old.write_bytes(subprocess.check_output(['git','show','5561b8c8354cd371cf4d3ad28c5408bea6922d3e:mechanical_project_namer/accounts.py'],cwd=ROOT))
    code="import sys,json,importlib.util,sqlite3;s=importlib.util.spec_from_file_location('mechanical_project_namer._baseline_accounts',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\ntry:m.AccountStore(json.load(open(sys.argv[2])));raise AssertionError('hot journal not reproduced')\nexcept sqlite3.OperationalError as e:assert 'readonly' in str(e)"
    old_result=subprocess.run([sys.executable,'-c',code,str(old),str(f['config_path'])],cwd=ROOT,capture_output=True,timeout=20)
    assert old_result.returncode==0,old_result.stderr.decode(errors='replace')
    from mechanical_project_namer.accounts import AccountStore
    recovered=AccountStore(f['config'])
    assert recovered.get(f['task'])['state']=='complete'
    with recovered.db() as c:assert c.execute('SELECT count(*) FROM auth_audit').fetchone()[0]==before
    assert recovered.formal_health(recovered.get(f['task']))


@pytest.mark.parametrize('operation',['create','disable','reset','grant','password'])
def test_pending_recovery_blocks_http_admin_mutations(tmp_path,operation):
    code="import sys;sys.path.insert(0,'tests');from server_http_cases import maintenance_case;maintenance_case(sys.argv[1],sys.argv[2])"
    result=subprocess.run([sys.executable,'-c',code,str(tmp_path/'case'),operation],cwd=ROOT,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr.decode(errors='replace')[-2000:]


def test_same_logical_service_cannot_start_at_two_locations(tmp_path):
    code="import sys;sys.path.insert(0,'tests');from server_http_cases import duplicate_logical_instance_case;duplicate_logical_instance_case(sys.argv[1])"
    result=subprocess.run([sys.executable,'-c',code,str(tmp_path/'case')],cwd=ROOT,capture_output=True,timeout=40)
    assert result.returncode==0,result.stderr.decode(errors='replace')[-2000:]


def test_fixture_waits_for_complete_ready_json(tmp_path):
    import threading,time
    from scripts.auth_harness import wait_ready
    class Alive:
        def poll(self):return None
    path=tmp_path/'ready.json';created=threading.Event()
    expected=dict(pid=123,port=12345,accounts=True,service=str(uuid.uuid4()))
    def publish():
        with path.open('xb') as out:
            created.set();time.sleep(.1);out.write(data(expected))
    thread=threading.Thread(target=publish);thread.start();created.wait(2)
    try:assert wait_ready(Alive(),path,seconds=2)==expected
    finally:thread.join()


def test_fixture_partial_ready_has_bounded_timeout(tmp_path):
    from scripts.auth_harness import wait_ready
    class Alive:
        def poll(self):return None
    path=tmp_path/'ready.json';path.write_bytes(b'{')
    with pytest.raises(RuntimeError,match='start-timeout'):wait_ready(Alive(),path,seconds=.05)
