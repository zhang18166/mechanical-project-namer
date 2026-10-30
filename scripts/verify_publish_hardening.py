# SPDX-License-Identifier: GPL-3.0-only
"""Independent HTTP scenarios; every output is private and outside Git."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import time
import uuid
from scripts.publish_harness import Fixture,begin,upload_all,remote_manifest,write,ROOT
from mechanical_project_namer.publish_protocol import data,ProtocolError,digest
from mechanical_project_namer.publish_ledger import PublishLedger
from mechanical_project_namer.publish_client import Client


def auth_case(f,role,operation):
    s=f.service();c=s.start()
    ca,ta,ma=begin(s,f,f.v1)
    # upload_all uses payload V2, identical bytes by construction.
    upload_all(ca,ta,ma,f);ca.request('POST',f'/tasks/{ta}/commit',{})
    cfg=json.loads(s.config.read_bytes());original=s.client('outsider')
    b=Client(dict(original.config,project=s.other))  # Explicit new project/target binding.
    mb=remote_manifest(f,f.v1);mb['local_project']=2;tb=str(uuid.uuid4())
    b.request('POST',f'/projects/{s.other}/tasks',dict(task=tb,manifest=mb));upload_all(b,tb,mb,f);b.request('POST',f'/tasks/{tb}/commit',{})
    tid=ta;m=ma
    if operation in ('upload','commit','cancel'):
        c,tid,m=begin(s,f)
        if operation=='commit':upload_all(c,tid,m,f)
    raw=operation in ('download','cross-file')
    routes={
        'info':('GET','/info',None),
        'catalog':('GET','/projects',None),
        'project':('GET','/projects/'+c.config['project'],None),
        'create':('POST','/projects/'+c.config['project']+'/tasks',dict(task=str(uuid.uuid4()),manifest=remote_manifest(f,f.v2))),
        'query':('GET','/tasks/'+tid,None),
        'upload':('PUT',f'/tasks/{tid}/files/0',b'X'*m['files'][0]['size']),
        'commit':('POST',f'/tasks/{tid}/commit',{}),
        'cancel':('POST',f'/tasks/{tid}/cancel',{}),
        'download':('GET',f'/tasks/{ta}/files/0',None),
        'cross-project':('GET','/projects/'+s.other,None),
        'cross-task':('GET','/tasks/'+tb,None),
        'cross-file':('GET',f'/tasks/{tb}/files/0',None),
        'version-as-task':('GET','/tasks/'+ma['version'],None),
    }
    method,route,value=routes[operation]
    person=s.client(role if role not in ('none','wrong') else 'publisher')
    headers={}
    if role!='none':headers['Authorization']='Bearer '+('WRONG' if role=='wrong' else person.config['token'])
    body=value if isinstance(value,bytes) else data(value) if value is not None else None
    before={str(p.relative_to(s.root/'storage')):digest(p) for p in (s.root/'storage').rglob('*') if p.is_file()}
    with sqlite3.connect(cfg['index']) as database:
        before_rows=database.execute('SELECT * FROM tasks ORDER BY id').fetchall()
    con=http.client.HTTPConnection('127.0.0.1',s.port,timeout=10)
    try:
        con.request(method,route,body,headers);r=con.getresponse();result=r.read();status=r.status
    finally:con.close()
    authenticated=role not in ('none','wrong')
    if operation in ('info','catalog'):allowed=authenticated
    elif operation.startswith('cross-'):allowed=role=='outsider'
    elif operation=='version-as-task':allowed=False
    elif operation in ('create','upload','commit','cancel'):allowed=role=='publisher'
    elif operation=='download':allowed=role in ('publisher','downloader')
    else:allowed=role in ('publisher','reader','downloader')
    # Wrong file content intentionally elicits 409 only for an authorized uploader.
    expected=409 if allowed and operation=='upload' else 200 if allowed else 403
    assert status==expected,(role,operation,status,result)
    if operation=='catalog' and allowed:
        expected_project=s.other if role=='outsider' else c.config['project']
        assert [p['project'] for p in json.loads(result)['projects']]==[expected_project]
    if not allowed:
        assert result==b'{"error":"forbidden"}'
        after={str(p.relative_to(s.root/'storage')):digest(p) for p in (s.root/'storage').rglob('*') if p.is_file()}
        assert before==after,'denied request changed stored bytes'
        with sqlite3.connect(cfg['index']) as database:
            assert before_rows==database.execute('SELECT * FROM tasks ORDER BY id').fetchall()
    f.assert_protected()
    return dict(role=role,operation=operation,status=status,expected=expected,denied_bytes_unchanged=not allowed)


def fault_case(f,point):
    s=f.service();c=s.start();c,tid,m=begin(s,f);upload_all(c,tid,m,f)
    action='disconnect' if point=='after-db' else 'fail' if point in ('db-write','db-commit') else 'exit'
    s.set_control(point=point,action=action)
    try:c.request('POST',f'/tasks/{tid}/commit',{})
    except Exception:pass
    else:raise AssertionError('fault did not execute')
    if action=='exit':
        s.process.wait(12);assert s.process.returncode==73
        s.set_control();c=s.start()
    else:s.set_control()
    state=c.request('GET','/tasks/'+tid)
    if state['state']!='complete':state=c.request('POST',f'/tasks/{tid}/commit',{})
    assert state['state']=='complete' and state['healthy']
    assert len(c.versions())==1
    assert c.request('POST',f'/tasks/{tid}/commit',{})['task']==tid
    f.assert_protected()
    return dict(point=point,action=action,recovered=True,task=tid)


def transport_case(f,mode):
    s=f.service();c=s.start()
    state=c.publish(f.snap,f.v1)
    controls=dict(file=1,after_bytes=262144,action=mode)
    if mode=='drop-file-response':controls['after_bytes']=100000000
    s.set_control(**controls)
    try:c.publish(f.snap,f.v2)
    except Exception:pass
    else:raise AssertionError('injection did not interrupt')
    if mode=='exit-upload':
        s.process.wait(12);assert s.process.returncode==73;s.set_control();c=s.start()
    else:s.set_control()
    result=c.publish(f.snap,f.v2)
    events=[r for r in s.records() if r.get('task')==result['task']]
    starts=[sum(r['event']=='upload-start' and r['file']==i for r in events) for i in range(3)]
    assert starts[0]==1 and starts[2]==1
    if mode=='drop-file-response':assert starts==[1,1,1]
    else:
        assert starts==[1,2,1]
        cut=next(r for r in events if r['event']=='upload-end' and r['file']==1)
        assert 0<cut['received']<cut['size']
    assert len(c.versions())==2 and c.request('GET','/tasks/'+state['task'])['healthy']
    c.download(result['task'],f.root/'download')
    for entry in result['manifest']['files']:
        assert digest(f.root/'download'/entry['path'])=={k:entry[k] for k in ('size','sha256')}
    f.assert_protected()
    return dict(mode=mode,requests=starts,task=result['task'],events=events)


def targets_case(f):
    a,b=f.service('A'),f.service('B');ca=a.start();cb=b.start()
    state=ca.publish(f.snap,f.v1);ledger=PublishLedger(f.snap);version=state['version']
    keya=ledger.key(ca.config,f.snap.store_id,version);keyb=ledger.key(cb.config,f.snap.store_id,version)
    assert ledger.get(keya)['state']=='complete' and ledger.get(keyb) is None
    # Fresh process reads sidecar, no credential stored there.
    code="from mechanical_project_namer.publish_ledger import PublishLedger;from mechanical_project_namer.data import Database,Repository;from mechanical_project_namer.snapshots import SnapshotService;import sys; s=SnapshotService(Repository(Database(sys.argv[1])),sys.argv[2]); l=PublishLedger(s); assert l.get(sys.argv[3])['state']=='complete'; assert l.get(sys.argv[4]) is None"
    subprocess.run([sys.executable,'-c',code,str(f.snap.db.path),str(f.snap.store),keya,keyb],cwd=ROOT,check=True,timeout=15)
    port=a.port;a.stop();b.stop();cb=b.start(port)
    # Use valid B credentials but retain expected service A identity. Old A
    # credentials correctly receive 403 before the UUID check can run.
    mismatch=dict(cb.config,service=ca.config['service'],port=port)
    from mechanical_project_namer.publish_client import Client
    try:Client(mismatch).info()
    except ProtocolError as e:assert str(e)=='service-identity'
    else:raise AssertionError('identity mismatch accepted')
    assert ledger.get(keya)['confirmed_at']>0 and ledger.get(keyb) is None
    f.assert_protected()
    return dict(A=ca.config['service'],B=cb.config['service'],fresh_process=True,port_reuse_rejected=True)


def revoked_case(f):
    s=f.service();c=s.start();v=c.publish(f.snap,f.v1);cfg=json.loads(s.config.read_bytes());cfg['identities']['publisher']['grants']={};write(s.config,cfg);s.stop();c=s.start()
    try:c.request('GET','/tasks/'+v['task'])
    except ProtocolError as e:assert '403' in str(e)
    else:raise AssertionError('revoked ACL accepted')
    cfg['identities']['publisher']['token_hash']=hashlib.sha256(b'REPLACED TEST TOKEN').hexdigest();write(s.config,cfg);s.stop();c=s.start()
    try:c.info()
    except ProtocolError as e:assert '403' in str(e)
    else:raise AssertionError('revoked token accepted')
    return dict(permission_revoked=True,token_replaced=True)


def cancel_case(f):
    s=f.service();c=s.start()
    from threading import Event
    e=Event();e.set()
    try:c.publish(f.snap,f.v2,cancel=e)
    except Exception:pass
    # Create once without pre-cancelled local verifier.
    s.set_control(action='disconnect-upload',file=1,after_bytes=1)
    try:c.publish(f.snap,f.v2)
    except Exception:pass
    else:raise AssertionError('cancel fixture did not interrupt')
    ledger=PublishLedger(f.snap);row=f.snap._get(f.v2);key=ledger.key(c.config,f.snap.store_id,row['version_id']);old=ledger.get(key)
    cancelled=c.cancel(old['task']);assert cancelled['state']=='cancelled';ledger.update(key,'cancelled',confirmation=cancelled)
    s.set_control()
    try:c.publish(f.snap,f.v2)
    except ProtocolError as exc:assert 'task-cancelled' in str(exc)
    else:raise AssertionError('cancelled revived')
    result=c.publish(f.snap,f.v2,restart_cancelled=True)
    assert result['task']!=old['task'] and result['healthy'] and len(c.versions())==1
    assert c.request('GET','/tasks/'+old['task'])['state']=='cancelled'
    with ledger.db() as con:
        recorded={r[0] for r in con.execute('SELECT task FROM events WHERE target=?',(key,))}
    assert old['task'] in recorded and result['task'] in recorded
    return dict(new_task=result['task'],old_task=old['task'],old_cancelled=True)


def client_worker(root,task,config,pid_path):
    from mechanical_project_namer.data import Database,Repository
    from mechanical_project_namer.snapshots import SnapshotService
    from PyQt6.QtWidgets import QApplication,QPushButton
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from mechanical_project_namer.ui.publish_dialog import PublishDialog
    profile=root/'client';snap=SnapshotService(Repository(Database(profile/'db.sqlite')),profile/'archive')
    app=QApplication([]);dialog=PublishDialog(snap,task);dialog.config.setText(str(config));dialog.show();QTest.qWait(50)
    pid_path.write_bytes(data({'pid':os.getpid()}));dialog.publish()
    end=time.monotonic()+90
    while dialog.worker:
        assert time.monotonic()<end
        QTest.qWait(20)
    assert '已提交' in dialog.status.text(),dialog.status.text()
    dialog.grab().save(str(pid_path.with_suffix('.png')))
    dialog.close();QTest.qWait(30)


def client_exit_case(f):
    s=f.service();c=s.start();v1=c.publish(f.snap,f.v1)
    s.set_control(trace_chunks=True,delay_per_chunk=.02)
    pid_path=f.root/'killed-client.json'
    args=[sys.executable,'-m','scripts.verify_publish_hardening','--worker-root',str(f.root),'--task',f.v2,'--config',str(s.root/'publisher.json'),'--pid',str(pid_path)]
    child=subprocess.Popen(args,cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        event=s.wait_event(lambda r:r['event']=='upload-bytes' and r['file']==1 and r['received']>=262144)
        assert event['received']<event['size']
        pid=json.loads(pid_path.read_bytes())['pid']
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=ctypes.c_void_p;kernel.OpenProcess.argtypes=[ctypes.c_uint,ctypes.c_int,ctypes.c_uint]
        kernel.TerminateProcess.argtypes=[ctypes.c_void_p,ctypes.c_uint];kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.OpenProcess(1,False,pid)
        assert handle and kernel.TerminateProcess(handle,74)
        kernel.CloseHandle(handle);child.wait(12)
        s.wait_event(lambda r:r['event']=='upload-end' and r['file']==1)
    finally:
        if child.poll() is None:child.terminate();child.wait(12)
    s.set_control()
    args[-1]=str(f.root/'resumed-client.json')
    subprocess.run(args,cwd=ROOT,check=True,timeout=90,stdout=subprocess.DEVNULL)
    states=c.versions();assert len(states)==2 and all(v['healthy'] for v in states)
    v2=next(v for v in states if v['version']==f.snap._get(f.v2)['version_id'])
    events=[r for r in s.records() if r.get('task')==v2['task']]
    counts=[sum(r['event']=='upload-start' and r['file']==i for r in events) for i in range(3)]
    assert counts==[1,2,1],counts
    assert c.request('GET','/tasks/'+v1['task'])['healthy']
    f.assert_protected()
    return dict(client_pid=pid,hard_exit=True,fresh_gui_process=True,requests=counts,cut_received=event['received'],file_size=event['size'])


def gui_case(f):
    from PyQt6.QtWidgets import QApplication,QPushButton
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from mechanical_project_namer.ui.publish_dialog import PublishDialog
    app=QApplication.instance() or QApplication([])
    s=f.service();c=s.start();d=PublishDialog(f.snap,f.v1);d.config.setText(str(s.root/'publisher.json'));d.show();QTest.qWait(100)
    def wait():
        end=time.monotonic()+60
        while d.worker:
            assert time.monotonic()<end,'GUI stalled'
            QTest.qWait(20)
    def click(text):
        button=next(b for b in d.findChildren(QPushButton) if b.text()==text)
        QTest.mouseClick(button,Qt.MouseButton.LeftButton)
    try:
        click('发布/重试');wait();assert '已提交' in d.status.text()
        v1=d.remote
        d.task=f.v2;d.config_changed();s.set_control(action='disconnect-upload',file=1,after_bytes=262144)
        click('发布/重试');wait();assert '未完成' in d.status.text()
        d.grab().save(str(f.root/'gui-interrupted.png'))
        s.set_control();click('发布/重试');wait();assert '已提交' in d.status.text()
        v2=d.remote;events=[r for r in s.records() if r.get('task')==v2]
        starts=[sum(r['event']=='upload-start' and r['file']==i for r in events) for i in range(3)]
        assert starts==[1,2,1],starts
        cut=next(r for r in events if r['event']=='upload-end' and r['file']==1)
        assert 0<cut['received']<cut['size']
        click('连接/查询版本');wait();d.target.setText(str(f.root/'gui-download'));click('下载已提交版本');wait()
        for entry in remote_manifest(f,f.v2)['files']:
            assert digest(f.root/'gui-download'/entry['path'])=={k:entry[k] for k in ('size','sha256')}
        assert len(c.versions())==2 and c.request('GET','/tasks/'+v1)['healthy']
        d.grab().save(str(f.root/'gui-complete.png'))
        f.assert_protected()
        return dict(real_gui=True,requests=starts,interrupted_received=cut['received'],file_size=cut['size'],v1=v1,v2=v2,events=events)
    finally:
        d.close();QTest.qWait(50)


def gui_targets_case(f):
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtTest import QTest
    from mechanical_project_namer.ui.publish_dialog import PublishDialog
    app=QApplication.instance() or QApplication([])
    a,b=f.service('A'),f.service('B');ca=a.start();cb=b.start()
    d=PublishDialog(f.snap,f.v1);d.config.setText(str(a.root/'publisher.json'));d.show();QTest.qWait(50)
    def wait():
        end=time.monotonic()+30
        while d.worker:
            assert time.monotonic()<end
            QTest.qWait(20)
    try:
        d.publish();assert not d.choose_button.isEnabled();wait();assert d.choose_button.isEnabled() and '已提交' in d.status.text()
        d.config.setText(str(b.root/'publisher.json'))
        d.grab().save(str(f.root/'gui-switched-target.png'))
        assert '已提交' not in d.status.text(),'A success leaked into B current state'
        assert ca.config['service'] not in d.details.toPlainText()
        d.query();wait();assert not cb.versions()
        assert '已提交' not in d.status.text()
        d.config.setText(str(a.root/'publisher.json'))
        assert 'complete' in d.history.text() and '已提交' not in d.status.text()
        a.stop();d.query();wait();assert '失败' in d.status.text()
        assert 'complete' in d.history.text(),'retain historical confirmation'
        assert '本次' in d.identity.text() and 'last_server_confirmation' in d.details.toPlainText()
        f.assert_protected()
        return dict(real_gui=True,B_not_published=True,no_stale_success=True,offline_history_labeled=True)
    finally:d.close();QTest.qWait(30)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path);p.add_argument('--scenario')
    p.add_argument('--worker-root',type=Path);p.add_argument('--task');p.add_argument('--config',type=Path);p.add_argument('--pid',type=Path)
    a=p.parse_args();scenario=a.scenario
    if a.worker_root:return client_worker(a.worker_root,a.task,a.config,a.pid)
    f=Fixture(a.output,large=scenario.startswith(('transport:','gui','client-exit','boundary:download-interrupt')))
    try:
        start=time.monotonic()
        if scenario.startswith('auth:'):result=auth_case(f,*scenario.split(':')[1:])
        elif scenario.startswith('fault:'):result=fault_case(f,scenario.split(':')[1])
        elif scenario.startswith('transport:'):result=transport_case(f,scenario.split(':')[1])
        elif scenario=='targets':result=targets_case(f)
        elif scenario=='revoke':result=revoked_case(f)
        elif scenario=='cancel':result=cancel_case(f)
        elif scenario=='client-exit':result=client_exit_case(f)
        elif scenario=='gui':result=gui_case(f)
        elif scenario=='gui-targets':result=gui_targets_case(f)
        elif scenario.startswith('boundary:'):
            from scripts.publish_boundary_cases import boundary
            result=boundary(f,scenario.split(':')[1])
        else:raise ValueError('unknown scenario')
        report=dict(passed=True,scenario=scenario,seconds=time.monotonic()-start,result=result)
        (f.root/'report.json').write_bytes(data(report));print(data(report).decode())
    finally:f.close()

if __name__=='__main__':main()
