# SPDX-License-Identifier: GPL-3.0-only
"""Regression cases use fictional, independent stores only."""
import hashlib
import json
import os
import uuid
import subprocess
import sys
from pathlib import Path

import pytest

from mechanical_project_namer.publish_protocol import data, sha, manifest, MAX_FILE
from mechanical_project_namer.publish_server import Store


def seed_store(tmp_path, state='complete'):
    store = Store({'storage': str(tmp_path/'storage'), 'index': str(tmp_path/'index'/'db.sqlite'),
                   'service': str(uuid.uuid4())})
    task, project = str(uuid.uuid4()), str(uuid.uuid4())
    content = b'FICTIONAL CAD TRANSPORT ONLY'
    m = dict(store=str(uuid.uuid4()), local_project=1, version=str(uuid.uuid4()), label='V1',
             files=[dict(path='中文.prt', role='source', size=len(content), sha256=hashlib.sha256(content).hexdigest())])
    with store.db() as c:
        c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)', (task, project, m['version'], sha(m), data(m).decode(), state))
    for kind in ['tasks', 'versions']:
        folder = store.root/kind/task
        folder.mkdir()
        (folder/'0').write_bytes(content)
        if kind == 'versions':
            (folder/'manifest.json').write_bytes(data(m))
    return store, task


@pytest.mark.parametrize('damage', ['missing-directory', 'content', 'manifest', 'extra'])
def test_complete_requires_formal_directory(tmp_path, damage):
    store, task = seed_store(tmp_path)
    folder = store.root/'versions'/task
    if damage == 'missing-directory':
        os.rename(folder, tmp_path/'simulated-missing-version')
    elif damage == 'content':
        (folder/'0').write_bytes(b'CORRUPT')
    elif damage == 'manifest':
        (folder/'manifest.json').write_bytes(b'{}')
    else:
        (folder/'unexpected.txt').write_bytes(b'EXTRA')
    result = store.status(store.get(task))
    assert result['state'] == 'complete'
    assert result['healthy'] is False
    assert (store.root/'tasks'/task/'0').is_file(), 'staging intentionally intact'
    assert store.get(task)['state'] == 'complete', 'retain history'


def test_formal_commit_recovers_database_registration(tmp_path):
    store, task = seed_store(tmp_path, state='receiving')
    result = store.status(store.get(task))
    assert result['state'] == 'complete' and result['healthy']
    assert store.get(task)['state'] == 'complete'


@pytest.mark.parametrize('field', ['binding', 'version', 'project'])
def test_registration_refuses_wrong_binding(tmp_path,field):
    store,task=seed_store(tmp_path,state='receiving')
    row=store.get(task)
    if field=='project':
        store.config['projects']={row['project']:{'store':str(uuid.uuid4()),'local_project':1}}
    else:
        with store.db() as c:c.execute('UPDATE tasks SET '+field+'=? WHERE id=?',('bad' if field=='binding' else str(uuid.uuid4()),task))
    with pytest.raises(ValueError):store.status(store.get(task))
    assert store.get(task)['state']=='receiving'


ROOT=Path(__file__).resolve().parents[1]


def scenario(tmp_path,name):
    result=subprocess.run([sys.executable,'-m','scripts.verify_publish_hardening','--output',str(tmp_path/'case'),'--scenario',name],cwd=ROOT,capture_output=True,timeout=100)
    assert result.returncode==0,result.stderr.decode(errors='replace')[-5000:]
    report=json.loads((tmp_path/'case/report.json').read_bytes())
    assert report['passed']


@pytest.mark.parametrize('role',['publisher','reader','downloader','outsider','none','wrong'])
@pytest.mark.parametrize('operation',['info','catalog','project','create','query','upload','commit','cancel','download','cross-project','cross-task','cross-file','version-as-task'])
def test_real_authorization_matrix(tmp_path,role,operation):
    scenario(tmp_path,'auth:'+role+':'+operation)


@pytest.mark.parametrize('point',['seal-copy','before-manifest','after-manifest','before-rename','after-rename','db-write','db-commit','after-db'])
def test_real_commit_fault_recovery(tmp_path,point):
    scenario(tmp_path,'fault:'+point)


@pytest.mark.parametrize('mode',['disconnect-upload','exit-upload','drop-file-response'])
def test_real_megabyte_file_retry(tmp_path,mode):
    scenario(tmp_path,'transport:'+mode)


@pytest.mark.parametrize('name',['targets','revoke','cancel'])
def test_real_target_and_lifecycle(tmp_path,name):
    scenario(tmp_path,name)


def test_real_fresh_client_after_hard_exit(tmp_path):
    scenario(tmp_path,'client-exit')


def test_real_gui_target_state_does_not_reuse_success(tmp_path):
    scenario(tmp_path,'gui-targets')


@pytest.mark.parametrize('mode',['receive-space','receive-write','idle','total','bounded-partials','unknown-seal','competition','cancel-commit','download-interrupt','download-existing','download-overlap','download-space'])
def test_real_boundary_matrix(tmp_path,mode):
    scenario(tmp_path,'boundary:'+mode)


from test_library_backups import library


def test_sidecar_excluded_from_library_backup_and_rebound_after_restore(library,tmp_path):
    from mechanical_project_namer.publish_ledger import PublishLedger
    from mechanical_project_namer.library_backups import DATABASE_RELATIVE,ARCHIVE_RELATIVE
    from mechanical_project_namer.data import Database,Repository
    from mechanical_project_namer.snapshots import SnapshotService
    import shutil
    service,profile,repo,snap,projects,tasks=library
    row=snap._get(tasks[0]);request=json.loads(row['request_json'])
    m=dict(store=snap.store_id,local_project=row['project_id'],version=row['version_id'],label=row['label'],
           files=[dict(path=f['relative_path'],role=f['role'],size=f['size'],sha256=f['sha256']) for f in request['files']])
    config=dict(service=str(uuid.uuid4()),project=str(uuid.uuid4()),token='FICTIONAL_NOT_A_REAL_CREDENTIAL')
    ledger=PublishLedger(snap);key,_=ledger.begin(config,m);ledger.update(key,'failed',code='test-only-not-transferred')
    assert config['token'].encode() not in ledger.path.read_bytes()
    backup=tmp_path/'backup';service.create(profile,backup)
    assert not any('.publish-state' in str(p) for p in backup.rglob('*'))
    destination=tmp_path/'restored';service.restore(backup,destination)
    restored=SnapshotService(Repository(Database(destination/DATABASE_RELATIVE)),destination/ARCHIVE_RELATIVE)
    new=PublishLedger(restored);assert new.get(key) is None
    # Even an unsupported manual sidecar copy cannot authenticate a new instance.
    shutil.copyfile(ledger.path,new.path)
    assert new.get(key) is None
    assert ledger.get(key)['state']=='failed'


def test_service_index_cancelled_version_migration_is_repeatable(tmp_path):
    import sqlite3
    target=tmp_path/'index/db.sqlite';target.parent.mkdir()
    old=str(uuid.uuid4());project=str(uuid.uuid4());version=str(uuid.uuid4())
    with sqlite3.connect(target) as c:
        c.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY,project TEXT,version TEXT,binding TEXT,manifest TEXT,state TEXT, UNIQUE(project,version))')
        c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',(old,project,version,'TEST','{}','cancelled'))
    config=dict(storage=str(tmp_path/'storage'),index=str(target),service=str(uuid.uuid4()))
    store=Store(config);Store(config)
    assert store.get(old)['state']=='cancelled'
    with store.db() as c:
        c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',(str(uuid.uuid4()),project,version,'TEST','{}','receiving'))
    with pytest.raises(sqlite3.IntegrityError):
        with store.db() as c:
            c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',(str(uuid.uuid4()),project,version,'TEST','{}','receiving'))


def test_failed_owned_partial_cleanup_is_preserved_without_path_log(tmp_path,monkeypatch,caplog):
    import mechanical_project_namer.publish_server as server
    store,task=seed_store(tmp_path,state='receiving');row=store.get(task)
    sealed=store.root/'tasks'/(task+'-sealed');store.owned(row,'sealed',sealed)
    partial=sealed/'0.copy';partial.write_bytes(b'PARTIAL KEEP')
    monkeypatch.setattr(server,'remove_file',lambda *args,**kwargs:False)
    with pytest.raises(ValueError,match='partial-cleanup-refused'):
        store.seal(row,lambda point:None)
    assert partial.read_bytes()==b'PARTIAL KEEP'
    assert str(tmp_path) not in caplog.text


@pytest.mark.parametrize('case',['single','total','count','empty','database','case-conflict','unicode-conflict','parent-conflict','boolean-size'])
def test_publish_manifest_limits_and_portable_conflicts(tmp_path,case):
    store,task=seed_store(tmp_path);m=json.loads(store.get(task)['manifest']);first=m['files'][0]
    if case=='single':first['size']=MAX_FILE+1
    elif case=='total':m['files']=[dict(first,path=f'{i}.prt',size=MAX_FILE) for i in range(5)]
    elif case=='count':m['files']=[dict(first,path=f'{i}.prt') for i in range(101)]
    elif case=='empty':m['files']=[]
    elif case=='database':first['path']='business.sqlite'
    elif case=='case-conflict':m['files']=[dict(first,path='PART.prt'),dict(first,path='part.prt')]
    elif case=='unicode-conflict':m['files']=[dict(first,path='é.prt'),dict(first,path='e\u0301.prt')]
    elif case=='parent-conflict':m['files']=[dict(first,path='part.prt'),dict(first,path='part.prt/child.prt')]
    else:first['size']=True
    with pytest.raises(ValueError):manifest(m)


def test_service_room_rejects_injected_disk_full(tmp_path,monkeypatch):
    import shutil
    store,task=seed_store(tmp_path)
    monkeypatch.setattr(shutil,'disk_usage',lambda _:shutil._ntuple_diskusage(100,100,0))
    with pytest.raises(ValueError,match='storage-limit'):store.room(1)


def test_windows_lease_reuses_ctypes_layout(tmp_path):
    import ctypes
    from mechanical_project_namer.library_lock import file_lease
    lock=tmp_path/'own.lock'
    with file_lease(lock):pass
    before={k for k in ctypes._pointer_type_cache if getattr(k,'__name__','')=='OVERLAPPED'}
    for _ in range(200):
        with file_lease(lock):pass
    after={k for k in ctypes._pointer_type_cache if getattr(k,'__name__','')=='OVERLAPPED'}
    assert after==before


def test_windows_sampler_reuses_ctypes_layout():
    import ctypes
    from scripts.publish_metrics import sample
    sample(os.getpid());before=set(ctypes._pointer_type_cache)
    for _ in range(30):sample(os.getpid())
    assert set(ctypes._pointer_type_cache)==before
