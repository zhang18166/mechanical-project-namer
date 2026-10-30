# SPDX-License-Identifier: GPL-3.0-only
"""Real certificate/parser/concurrency boundary checks at the Nginx TLS listener."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
from scripts.https_harness import fixture,ROOT
from scripts.publish_harness import remote_manifest
from scripts.auth_harness import rejected
from mechanical_project_namer.account_client import AccountSession
from mechanical_project_namer.publish_protocol import data


def tls(f,case):
    s=f.service;cfg=dict(s.connection);proxy=None;runtime=None
    before=int(s.native("import sqlite3,json,sys;c=sqlite3.connect(json.load(open(sys.argv[1]))['index']);print(c.execute('select count(*) from auth_audit').fetchone()[0])",s.config))
    try:
        if case=='wrong-uuid':cfg['service']=str(uuid.uuid4())
        elif case=='untrusted':
            from scripts.make_tls_fixtures import generate
            other=generate(f.root/'untrusted-ca');cfg['ca']=str(other/'ca.pem')
        elif case=='replacement':
            s.stop();s.certificate='replacement';s.start()
            account=AccountSession(s.connection);account.login('fake-admin',s.current);assert len(account.projects())==2
            return dict(tls=True,legitimate_replacement=True,protocol=s.info()['tls_protocol'])
        else:
            port=int(s.native("import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()"))
            runtime=os.environ['MW_ACCOUNT_TEST_ROOT']+'/n-'+uuid.uuid4().hex[:12]
            certs=Path(os.environ['MW_HTTPS_TEST_CERTIFICATES'])
            for suffix in ('.pem','.key'):s.native_write(s.cert_root+'/'+case+suffix,(certs/(case+suffix)).read_bytes(),exclusive=True)
            args=['-m','scripts.https_runtime','prepare','--runtime',runtime,'--config',s.config,
                  '--nginx',os.environ['MW_HTTPS_TEST_NGINX'],'--port',str(port),'--cert',s.cert_root+'/'+case+'.pem',
                  '--key',s.cert_root+'/'+case+'.key','--ca',s.cert_root+'/ca.pem']
            subprocess.run(s.base()+args,cwd=ROOT,check=True,capture_output=True,timeout=15)
            # Deliberately invalid *server* certificate fixture. The real client
            # verification remains enabled; no CA/private-key material is in Git.
            prefix=s.base()[:-1]  # retain controlled WSL/env invocation, replace Python
            envarg=['LD_LIBRARY_PATH='+os.environ['MW_HTTPS_TEST_LIBDIR']]
            proxy=subprocess.Popen(prefix+envarg+[os.environ['MW_HTTPS_TEST_NGINX'],'-e','/dev/null','-p',runtime+'/', '-c',runtime+'/nginx.conf'],
                                   cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            end=time.monotonic()+5
            while not s.native("import pathlib,sys;print(int(pathlib.Path(sys.argv[1]).exists()))",runtime+'/nginx.pid').strip()=='1':
                assert time.monotonic()<end;time.sleep(.03)
            cfg['url']='https://127.0.0.1:'+str(port)
        try:AccountSession(cfg).login('fake-admin',s.current)
        except ValueError as exc:
            code=str(exc);assert code==('service-identity' if case=='wrong-uuid' else 'tls-certificate-rejected'),code
        else:raise AssertionError('Invalid peer accepted')
        after=int(s.native("import sqlite3,json,sys;c=sqlite3.connect(json.load(open(sys.argv[1]))['index']);print(c.execute('select count(*) from auth_audit').fetchone()[0])",s.config))
        assert after==before,'Credentials reached the business service'
        return dict(rejected=True,code=code,credentials_not_sent=True,no_http_fallback=True)
    finally:
        if proxy:
            s.native("import os,sys,pathlib,signal;p=int(pathlib.Path(sys.argv[1]+'/nginx.pid').read_text());assert os.stat('/proc/'+str(p)).st_uid==os.geteuid();assert sys.argv[1].encode() in pathlib.Path('/proc/'+str(p)+'/cmdline').read_bytes();os.kill(p,signal.SIGTERM)",runtime)
            proxy.wait(10)


def boundary(f,case):
    s=f.service;session=f.admin;client=session.publisher(f.pa)
    if case=='raw-uri':
        c=session.target.connection()
        try:
            c.sock.sendall(b'GET /projects/\xff HTTP/1.1\r\nHost: 127.0.0.1:'+str(s.port).encode()+b'\r\nConnection: close\r\n\r\n')
            r=http.client.HTTPResponse(c.sock);r.begin();r.read(8192);bad_status=r.status
        finally:c.close()
        c=http.client.HTTPSConnection('127.0.0.1',s.port,context=session.target.context,timeout=3)
        try:c.request('GET','/health');r=c.getresponse();r.read(8192);health=r.status
        finally:c.close()
        result=dict(invalid_route_status=bad_status,next_health_status=health)
        (f.root/'raw-uri-observation.json').write_bytes(data(result))
        assert bad_status==400 and health==200,result
        return result
    if case=='rate-limit':
        from collections import Counter
        c=session.target.connection();statuses=Counter();started=time.monotonic()
        try:
            for _ in range(160):
                if c.sock is None:
                    c.close()
                    # This fixed-size rate probe sends public health only. A
                    # closed connection gets an explicit fresh verified TLS
                    # channel, never implicit reconnection of a credentialled
                    # business operation after its earlier UUID check.
                    c=http.client.HTTPSConnection('127.0.0.1',s.port,context=session.target.context,timeout=3)
                    c.connect();c.auto_open=0
                c.request('GET','/health');r=c.getresponse();body=r.read(8192);statuses[r.status]+=1
                if r.status==200:assert json.loads(body)['service']==s.connection['service']
        finally:c.close()
        assert statuses[429]>0 and statuses[200]>0,statuses
        assert not set(statuses)-{200,429,503},statuses
        time.sleep(1)
        assert session.projects()
        return dict(statuses=dict(statuses),seconds=time.monotonic()-started,business_available_after_refill=True)
    if case=='body-limits':
        c=session.target.connection()
        try:
            c.request('POST','/auth/login',body=data(dict(username='x'*9000,password='FICTIONAL')))
            r=c.getresponse();r.read();assert r.status==409
        finally:c.close()
        c=session.target.connection()
        try:
            c.putrequest('PUT','/tasks/'+str(uuid.uuid4())+'/files/0')
            c.putheader('Content-Length',str(16*1024**2+1));c.endheaders()
            r=c.getresponse();r.read(8192);assert r.status==413
        finally:c.close()
        assert client.versions()==[]
        return dict(json_8KiB_refused=True,header_only_over_16MiB_refused=True,no_complete_versions=True)
    if case=='header-wait':
        c=http.client.HTTPSConnection('127.0.0.1',s.port,context=session.target.context,timeout=8)
        try:
            c.connect();started=time.monotonic()
            c.sock.sendall(('GET /health HTTP/1.1\r\nHost: 127.0.0.1:'+str(s.port)+'\r\n').encode())
            response=http.client.HTTPResponse(c.sock)
            try:response.begin();response.read(8192);assert response.status==408
            except http.client.RemoteDisconnected:pass
            elapsed=time.monotonic()-started;assert 4<=elapsed<8,elapsed
        finally:c.close()
        return dict(header_wait_expired=True,seconds=elapsed)
    if case=='login-admission':
        connections=[session.target.connection() for _ in range(2)]
        import threading
        barrier=threading.Barrier(2)
        def login(c):
            try:
                barrier.wait(3)
                c.request('POST','/auth/login',body=data(dict(username='fake-admin',password=s.current)))
                r=c.getresponse();return r.status,json.loads(r.read(8192))
            finally:c.close()
        with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(login,connections))
        statuses=[status for status,v in responses];assert sorted(statuses)==[200,503],statuses
        for status,v in responses:
            if status==200:
                account=AccountSession(s.connection);account.token=v['token'];account.user=v['user'];account.logout()
            else:assert v==dict(error='service-busy-retry')
        assert session.projects()
        return dict(statuses=statuses,one_password_worker=True,no_queued_login=True,tokens_not_recorded=True)
    if case=='lease':
        # A second ASGI entry and a maintenance backup both contend for the
        # same existing logical/index leases; neither may open a second writer.
        ready=s.runtime+'/forbidden-second-ready.json'
        command=s.base()+['-m','mechanical_project_namer.asgi_server','--config',s.config,
                         '--socket',s.runtime+'/second.sock','--host','127.0.0.1:'+str(s.port),'--ready',ready]
        r=subprocess.run(command,cwd=ROOT,capture_output=True,timeout=15)
        assert r.returncode!=0 and b'isolated-asgi-service-failed' in r.stderr
        r=subprocess.run(s.base()+['-m','mechanical_project_namer.server_recovery','backup','--config',s.config,
                         '--target',s.runtime+'/must-not-backup-live','--task',str(uuid.uuid4())],cwd=ROOT,capture_output=True,timeout=15)
        assert r.returncode!=0
        assert session.projects()
        return dict(second_asgi_refused=True,live_backup_refused=True,first_service_healthy=True)
    if case in ('upload-idle','upload-total','busy-cancel','revoke-before-commit'):
        from scripts.verify_https_transfers import wait_idle,paths
        actor=f.users['fake-writer']['session'];writer=actor.publisher(f.pa)
        m=remote_manifest(f.a,f.task);task=str(uuid.uuid4());route='/tasks/'+task
        writer.request('POST','/projects/'+f.pa+'/tasks',dict(task=task,manifest=m))
        writer.request('PUT',route+'/files/0',file=paths(f,f.task)/m['files'][0]['path'])
        s.set_control(idle_timeout=.4 if case=='upload-total' else .2,total_timeout=1,trace_chunks=True)
        entry=m['files'][1];c=actor.target.connection();started=time.monotonic()
        c.putrequest('PUT',route+'/files/1');c.putheader('Authorization','Bearer '+actor.token)
        c.putheader('Content-Length',str(entry['size']));c.endheaders()
        try:
            c.send(b'F'*65536)
            end=time.monotonic()+3
            while not any(e['event']=='upload-bytes' and e['task']==task for e in s.events()):
                assert time.monotonic()<end;time.sleep(.02)
            if case=='upload-total':
                for _ in range(20):
                    try:c.send(b'F'*4096)
                    except OSError:break
                    time.sleep(.08)
            elif case in ('busy-cancel','revoke-before-commit'):
                rejected(lambda:session.request('POST',route+'/cancel',{}),'service-verification-failed')
                # A rejected busy request must not release the active operation.
                assert session.target.config==s.connection
            else:time.sleep(.3)
            if case.startswith('upload-'):
                try:
                    r=c.getresponse();r.read(8192);assert r.status!=200
                except OSError:
                    pass  # TLS close on timeout is checked against application bytes/state below.
        finally:c.close()
        pending=wait_idle(writer,task);assert pending['state']=='receiving' and pending['verified']==[0]
        if case=='revoke-before-commit':
            f.grant(f.users['fake-writer']['id'],f.pa,[])
            rejected(lambda:writer.request('POST',route+'/commit',{}))
            assert client.request('GET',route)['state']=='receiving'
            client.request('POST',route+'/cancel',{})
        else:assert writer.request('POST',route+'/cancel',{})['state']=='cancelled'
        actual=[e['received'] for e in s.events() if e['event']=='upload-end' and e['task']==task]
        assert actual and 0<actual[-1]<entry['size']
        return dict(actual_received=actual[-1],total=entry['size'],elapsed=time.monotonic()-started,
                    bounded_busy=True,latest_commit_permission=True,no_complete_version=True)
    if case in ('download-cut','download-slow'):
        from unittest.mock import patch
        from mechanical_project_namer.publish_client import Client
        state=client.publish(f.a.snap,f.task)
        if case=='download-cut':
            s.set_control(action='disconnect-download',after_bytes=262144,nonce=str(uuid.uuid4()))
            rejected(lambda:client.download(state['task'],f.root/'partial-must-not-be-success'),'download-hash')
            assert not (f.root/'partial-must-not-be-success').exists()
            return dict(incomplete_not_committed=True)
        # Slow actual TLS consumption in the test process only, not production.
        original=http.client.HTTPResponse.read1
        def paced(response,n=-1):
            if response.getheader('Content-Length')==str(8*1024**2):time.sleep(.3)
            return original(response,n)
        started=time.monotonic()
        with patch.object(http.client.HTTPResponse,'read1',paced):
            rejected(lambda:client.download(state['task'],f.root/'slow-must-not-be-success'),'download-timeout')
        elapsed=time.monotonic()-started
        assert 25<elapsed<40 and not (f.root/'slow-must-not-be-success').exists(),elapsed
        return dict(client_total_deadline=True,elapsed=elapsed,incomplete_not_committed=True)
    if case=='parser':
        status=[]
        for fields in ('Content-Length: 1\r\nContent-Length: 2','Content-Length: 2\r\nTransfer-Encoding: chunked',
                       'Content-Length: invalid','Transfer-Encoding: gzip'):
            c=session.target.connection()
            try:
                c.sock.sendall(('POST /auth/login HTTP/1.1\r\nHost: 127.0.0.1:'+str(s.port)+'\r\n'+fields+'\r\nConnection: close\r\n\r\n{}').encode())
                response=http.client.HTTPResponse(c.sock);response.begin();response.read(8192)
                assert response.status in (400,411,501);status.append(response.status)
            finally:c.close()
        assert client.versions()==[]
        return dict(parser_rejections=status,no_complete_version=True)
    if case=='headers':
        status=[]
        for method,headers,expected in [('GET',{'Host':'wrong.invalid'},400),('GET',{'Origin':'https://other.invalid'},403),
                                        ('DELETE',{},405),('GET',{'X-Large':'x'*20000},400)]:
            c=session.target.connection()
            try:
                c.request(method,'/projects',headers=dict(Authorization='Bearer '+session.token,**headers));r=c.getresponse();r.read(65536)
                assert r.status==expected,(method,r.status);status.append(r.status)
            finally:c.close()
        reader=f.users['fake-reader']['session']
        state=client.publish(f.a.snap,f.task)
        c=reader.target.connection()
        try:
            c.request('GET',f'/tasks/{state["task"]}/files/0',headers={'Authorization':'Bearer '+reader.token,
                          'X-Forwarded-For':'admin','Forwarded':'for=127.0.0.1;proto=https','X-Forwarded-Proto':'https','X-Real-IP':'admin'})
            r=c.getresponse();b=r.read();assert r.status==403 and b==data({'error':'forbidden'})
        finally:c.close()
        return dict(status=status,spoofed_forwarded_stripped=True,source_denied=True)
    if case=='terminal-race':
        m=remote_manifest(f.a,f.task);task=str(uuid.uuid4());route='/tasks/'+task
        client.request('POST','/projects/'+f.pa+'/tasks',dict(task=task,manifest=m))
        payload=f.a.snap.store/'versions'/f.a.snap._get(f.task)['version_id']/'payload'
        for i,e in enumerate(m['files']):client.request('PUT',route+'/files/'+str(i),file=payload/e['path'])
        def operation(name):
            try:return name,client.request('POST',route+'/'+name,{})['state']
            except Exception as exc:return name,str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(operation,['commit','cancel']))
        state=client.request('GET',route)
        assert state['state'] in ('complete','cancelled')
        if state['state']=='complete':assert state['healthy']
        assert client.request('POST',route+'/cancel',{})['state']==state['state']
        return dict(results=results,terminal=state['state'],no_deleted_history=True)
    if case in ('missing','damaged','extra','manifest'):
        state=client.publish(f.a.snap,f.task);folder=s.data_root+'/storage/versions/'+state['task']
        code={'missing':"import os,sys;os.rename(sys.argv[1],sys.argv[1]+'-missing')",
              'damaged':"import pathlib,sys;(pathlib.Path(sys.argv[1])/'0').write_bytes(b'DAMAGED')",
              'extra':"import pathlib,sys;(pathlib.Path(sys.argv[1])/'unexpected').write_bytes(b'EXTRA')",
              'manifest':"import pathlib,sys;(pathlib.Path(sys.argv[1])/'manifest.json').write_bytes(b'BROKEN')"}[case]
        s.native(code,folder)
        assert not client.versions()[0]['healthy'];rejected(lambda:client.publish(f.a.snap,f.task),'version-unavailable')
        rejected(lambda:client.download(state['task'],f.root/'must-not-exist'),'version-unavailable')
        assert not (f.root/'must-not-exist').exists()
        return dict(unhealthy=True,download_rejected=True,history_retained=True)
    raise ValueError('Unknown security scenario')


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--case',required=True);a=p.parse_args()
    f=fixture(a.output,large=a.case in ('upload-idle','upload-total','busy-cancel','revoke-before-commit','download-cut','download-slow'))
    try:
        result=tls(f,a.case) if a.case in ('wrong-uuid','untrusted','wrong-host','expired','future','replacement') else boundary(f,a.case)
        f.protect();(f.root/'report.json').write_bytes(data(dict(passed=True,case=a.case,result=result)))
        print('HTTPS security acceptance passed:',a.case)
    finally:f.close()

if __name__=='__main__':main()
