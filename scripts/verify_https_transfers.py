# SPDX-License-Identifier: GPL-3.0-only
"""Real streaming/proxy/process faults; every byte count comes from application events."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
from scripts.https_harness import fixture,ROOT
from scripts.publish_harness import remote_manifest
from mechanical_project_namer.publish_protocol import data,digest
from mechanical_project_namer.publish_ledger import PublishLedger


def wait_idle(client,task):
    end=time.monotonic()+12
    while True:
        try:return client.request('GET','/tasks/'+task)
        except Exception:
            if time.monotonic()>end:raise
            time.sleep(.1)


def paths(f,local):
    row=f.a.snap._get(local)
    return f.a.snap.store/'versions'/row['version_id']/'payload'


def task_id(f,client):
    row=f.a.snap._get(f.task);ledger=PublishLedger(f.a.snap)
    return ledger.get(ledger.key(client.config,f.a.snap.store_id,row['version_id']))['task']


def verify_download(f,client,state):
    target=f.root/'download'
    client.download(state['task'],target)
    for item in state['manifest']['files']:
        assert digest(target/item['path'])=={k:item[k] for k in ('size','sha256')}


def restart_after_fault(service):
    """A restart is not a guarantee that WSL's host-side bridge is ready.

    At most one fresh, fully verified public health retry, only after a native
    health check proves the same new instance survived. Never replay a login,
    administrative mutation, upload, or commit; never waive TLS verification.
    """
    try:
        service.start()
        return dict(host_health_attempts=1)
    except TimeoutError:
        if service.process.poll() is not None:raise
        result=subprocess.run(service.base()+['-m','scripts.https_runtime','check','--runtime',service.runtime],
                              cwd=ROOT,capture_output=True,timeout=12)
        assert result.returncode==0
        identity=json.loads(result.stdout)
        assert identity['service']==service.connection['service'] and identity['accounts']
        pid=service.pid;proxy=service.proxy_pid
        service.session()  # One bounded fresh TLS/UUID check; error propagates.
        assert (service.pid,service.proxy_pid)==(pid,proxy)
        return dict(host_health_attempts=2,first_host_handshake_timeout=True,native_health_confirmed=True)


def gui(f):
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtTest import QTest
    from PyQt6.QtCore import Qt
    from mechanical_project_namer.ui.project_files_page import ProjectFilesPage
    import os
    app=QApplication([]);os.environ['MECHANICAL_PROJECT_NAMER_DEV_ROOT']=str(f.a.root/'client')
    page=ProjectFilesPage(f.a.repo);page.resize(1100,800);page.show();QTest.qWait(50)
    QTest.mouseClick(page.account_button,Qt.MouseButton.LeftButton);d=page.account_window
    d.snapshots=f.a.snap;d.local_changed();d.connection.setText(str(f.service.root/'connection.json'))
    def click(button):assert button.isEnabled();QTest.mouseClick(button,Qt.MouseButton.LeftButton)
    def wait(failed=False):
        end=time.monotonic()+60
        while d.worker:
            assert time.monotonic()<end;QTest.qWait(20)
        assert ('操作未完成' in d.status.text())==failed,d.status.text()
    try:
        d.username.setText('fake-admin');d.password.setText(f.service.current);click(d.login_button);wait()
        click(d.refresh_button);wait();d.projects.setCurrentRow(next(i for i,p in enumerate(d.project_rows) if p['id']==f.pa));wait()
        d.local_version.setCurrentIndex(d.local_version.findData(f.a.v1));click(d.publish_button);wait()
        v1=json.loads(d.details.toPlainText());assert v1['healthy']
        f.service.set_control(action='disconnect-upload',file=1,after_bytes=262144,nonce=str(uuid.uuid4()),trace_chunks=True)
        d.local_version.setCurrentIndex(d.local_version.findData(f.task));click(d.publish_button);wait(True)
        d.grab().save(str(f.root/'gui-interrupted.png'))
        client=d.session.publisher(f.pa);remote=task_id(f,client)
        pending=wait_idle(client,remote);assert pending['state']=='receiving' and pending['verified']==[0]
        f.service.set_control(trace_chunks=True)
        click(d.publish_button);wait();state=json.loads(d.details.toPlainText());assert state['task']==remote and state['healthy']
        d.grab().save(str(f.root/'gui-resumed.png'))
        click(d.refresh_button);wait();d.projects.setCurrentRow(next(i for i,p in enumerate(d.project_rows) if p['id']==f.pa));wait()
        d.versions.setCurrentRow(next(i for i,v in enumerate(d.version_rows) if v['task']==remote))
        d.download_target.setText(str(f.root/'gui-download'));click(d.download_button);wait()
        for item in state['manifest']['files']:assert digest(f.root/'gui-download'/item['path'])=={k:item[k] for k in ('size','sha256')}
        versions=client.versions();assert len(versions)==2 and next(v for v in versions if v['task']==v1['task'])==v1
        events=f.service.events();starts=Counter(e['file'] for e in events if e['event']=='upload-start' and e['task']==remote)
        ends=[e for e in events if e['event']=='upload-end' and e['task']==remote and e['file']==1]
        assert dict(starts)=={0:1,1:2,2:1,3:1},starts
        received=ends[0]['received'];assert 0<received<4*1024**2
        click(d.logout_button);wait();f.protect()
        return dict(real_windows_gui=True,interrupted_received=received,file_total=4*1024**2,requests=dict(starts),
                    task=remote,v1_unchanged=True,one_v2=True,download_hashes=True)
    finally:d.close();page.close();QTest.qWait(50)


def fault(f,case):
    s=f.service;client=f.admin.publisher(f.pa)
    v1=client.publish(f.a.snap,f.a.v1)
    control=dict(file=1,after_bytes=262144,nonce=str(uuid.uuid4()),trace_chunks=True)
    if case in ('disconnect','file-ack','app-exit'):
        control['action']={'disconnect':'disconnect-upload','file-ack':'drop-file-response','app-exit':'exit-upload'}[case]
    elif case=='commit-ack':control.update(point='after-db',action='disconnect')
    elif case in ('db','space','seal'):
        control.update(point={'db':'db-commit','space':'receive-space','seal':'seal-copy'}[case],action='fail')
    elif case in ('proxy-exit','client-exit'):control['delay_per_chunk']=.05
    if 'point' in control:
        # Point faults must not carry upload-threshold triggers: the historical
        # test control otherwise consumes their nonce while receiving file 1.
        control.pop('file');control.pop('after_bytes')
    elif case=='file-ack':
        control.pop('after_bytes')  # Fire only after full verification, not mid-upload.
    s.set_control(**control)
    child=None;actual_pid=None;previous_events=[]
    if case in ('proxy-exit','client-exit'):
        instance=str(uuid.uuid4())
        child=subprocess.Popen([sys.executable,'-m','scripts.https_client_worker','--instance',instance,'--slow-upload'],
                               cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        child.stdin.write(data(dict(connection=s.connection,username='fake-admin',password=s.current,project=f.pa,
                                    database=str(f.a.repo.database.path),archive=str(f.a.snap.store),task=f.task))+b'\n')
        child.stdin.close();ready=json.loads(child.stdout.readline());actual_pid=ready['pid'];assert ready['instance']==instance
        end=time.monotonic()+12;observed=None
        while time.monotonic()<end:
            events=s.events()
            candidates=[e for e in events if e['event']=='upload-bytes' and e.get('file')==1 and 0<e['received']<e['size']]
            if candidates:observed=candidates[-1];break
            time.sleep(.03)
        assert observed,'No actual partial application receive'
        if case=='proxy-exit':s.hard_exit('proxy')
        else:
            from scripts.publish_metrics import _api
            kernel,*_= _api();h=kernel.OpenProcess(1,False,actual_pid)
            try:
                assert h;kernel.TerminateProcess.argtypes=[ctypes.c_void_p,ctypes.c_uint];assert kernel.TerminateProcess(h,73)
            finally:kernel.CloseHandle(h)
        child.wait(45);assert child.returncode!=0
    else:
        try:client.publish(f.a.snap,f.task)
        except Exception:pass
        else:
            # Commit acknowledgement loss may already have been resolved by an
            # idempotent query. Other injected faults must visibly interrupt.
            assert case=='commit-ack','Configured fault did not interrupt the transfer'
    remote=task_id(f,client)
    database_boundary=None
    if case in ('db','seal'):
        database_boundary=json.loads(s.native("import json,sqlite3,sys,pathlib;cfg=json.load(open(sys.argv[1]));c=sqlite3.connect(cfg['index']);row=c.execute('SELECT state FROM tasks WHERE id=?',(sys.argv[2],)).fetchone();print(json.dumps(dict(state=row[0],formal=(pathlib.Path(cfg['storage'])/'versions'/sys.argv[2]).exists())))",s.config,remote))
        assert database_boundary['state']=='receiving'
        assert database_boundary['formal']==(case=='db')
    if case=='app-exit':s.process.wait(45);s.process=None
    if case in ('app-exit','proxy-exit'):previous_events=s.events()
    restart=restart_after_fault(s) if case in ('app-exit','proxy-exit') else None
    s.set_control(trace_chunks=True)
    pending=wait_idle(client,remote)
    if case=='db':assert pending['state']=='complete' and pending['healthy']  # Legal verified FS commit recovery.
    if case in ('disconnect','client-exit','proxy-exit','app-exit'):assert 0 in pending['verified']
    if case=='client-exit':
        # A wholly new client process logs in again; no persisted token.
        fresh=subprocess.Popen([sys.executable,'-m','scripts.https_client_worker','--instance',str(uuid.uuid4())],cwd=ROOT,
                               stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        job=dict(connection=s.connection,username='fake-admin',password=s.current,project=f.pa,
                 database=str(f.a.repo.database.path),archive=str(f.a.snap.store),task=f.task)
        stdout,stderr=fresh.communicate(data(job)+b'\n',timeout=60);assert fresh.returncode==0,stderr
    state=client.publish(f.a.snap,f.task);assert state['task']==remote and state['healthy']
    verify_download(f,client,state)
    versions=client.versions();assert len(versions)==2 and next(v for v in versions if v['task']==v1['task'])==v1
    events=previous_events+s.events()
    if case in ('db','space','seal','commit-ack'):
        assert any(e['event']=='fault' and e['point']==control['point'] for e in events),'Fault hook did not fire'
    counts=Counter(e['file'] for e in events if e['event']=='upload-start' and e['task']==remote)
    assert counts[0]==1,'Verified first file was unnecessarily uploaded again'
    if case in ('disconnect','app-exit','proxy-exit','client-exit'):assert counts[1]==2,counts
    if case=='file-ack':assert counts[1]==1,counts
    f.protect()
    return dict(case=case,task=remote,complete=True,one_v2=True,v1_unchanged=True,download_hashes=True,
                actual_client_pid=actual_pid,restart=restart,database_boundary=database_boundary,upload_start_counts=dict(counts),events=events,
                fault_kind='controlled-injection' if case in ('db','space','seal') else 'real-transport-or-process')


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--case',required=True);a=p.parse_args()
    f=fixture(a.output,large=True)
    try:
        result=gui(f) if a.case=='gui' else fault(f,a.case)
        report=dict(passed=True,real_https=True,result=result);(f.root/'report.json').write_bytes(data(report));print('HTTPS transfer acceptance passed:',a.case)
    finally:f.close()

if __name__=='__main__':main()
