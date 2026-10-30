# SPDX-License-Identifier: GPL-3.0-only
"""Finite rogue-client lifecycle evidence at the real Nginx HTTPS listener.

The test peer deliberately ignores official transfer deadlines. TLS and HTTP
parsing still use the standard library. No packet capture or host changes.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import time
import uuid

from scripts.https_harness import fixture, ROOT
from scripts.publish_harness import remote_manifest
from scripts.verify_https_transfers import paths
from mechanical_project_namer.diagnostics import Trace
from mechanical_project_namer.transport import Target
from mechanical_project_namer.publish_protocol import data


class Observer:
    def __init__(self,service,scenario):
        self.service,self.scenario=service,scenario
        self.process=subprocess.Popen(service.base()+['-m','scripts.https_connection_observer'],cwd=ROOT,
                                      stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.process.stdin.write(data(dict(runtime=service.runtime,scenario=scenario,seconds=100))+b'\n')
        self.process.stdin.close();self.process.stdin=None
        self.ready=json.loads(self.process.stdout.readline())
        assert self.ready['scenario']==scenario

    def finish(self,root):
        self.service.native_write(self.service.runtime+'/observer-stop-'+self.scenario,self.scenario.encode(),exclusive=True)
        stdout,stderr=self.process.communicate(timeout=10)
        assert self.process.returncode==0,'Native connection observer failed'
        result=json.loads(stdout)
        assert not result['capped'] and result['samples']>0,result
        text=self.service.native("import pathlib,sys;print(pathlib.Path(sys.argv[1]).read_text())",
                                 self.service.runtime+'/connections-'+self.scenario+'.jsonl')
        (root/'connections.jsonl').write_text(text)
        return [json.loads(line) for line in text.splitlines()]


class Peer:
    def __init__(self,session,trace,*,tls=True,receive_buffer=4096):
        self.session,self.trace=session,trace
        self.started=time.monotonic();self.raw=socket.socket()
        self.raw.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,receive_buffer)
        self.raw.settimeout(2);self.raw.connect(('127.0.0.1',session.f.service.port))
        self.windows_endpoints=dict(source=self.raw.getsockname(),destination=self.raw.getpeername())
        # Before TLS/other probes, establish a unique controlled Linux tuple.
        end=time.monotonic()+2
        while True:
            rows=session.rows()
            candidates=[r for r in rows if r['owned'] and r['state']=='01' and r['remote_port'] not in session.pinned]
            if len(candidates)==1:break
            assert time.monotonic()<end,'Could not uniquely correlate the controlled connection'
            time.sleep(.02)
        self.native=candidates[0];self.remote_port=self.native['remote_port'];session.pinned.add(self.remote_port)
        self.socket=self.raw
        self.connection=None
        if tls:
            self.socket=session.f.admin.target.context.wrap_socket(self.raw,server_hostname='127.0.0.1')
            self.connection=http.client.HTTPSConnection('127.0.0.1',session.f.service.port,context=session.f.admin.target.context,timeout=2)
            self.connection.sock=self.socket;self.connection.auto_open=0
            self.connection.request('GET','/health',headers={'X-Workbench-Trace':uuid.uuid4().hex})
            response=self.connection.getresponse();health=json.loads(response.read(8192))
            assert response.status==200 and health['service']==session.f.service.connection['service']
            assert health['accounts'] and health['protocol']==1
            self.health_connection=response.getheader('X-Workbench-Connection')
            assert self.health_connection,'Diagnostic correlation headers missing'
        session.peers.append(self)

    def send_request(self,method,route,*,size=None,auth=False,close=True):
        c=self.connection;c.putrequest(method,route)
        c.putheader('X-Workbench-Trace',self.trace)
        if auth:c.putheader('Authorization','Bearer '+self.session.f.admin.token)
        if size is not None:c.putheader('Content-Length',str(size))
        if close:c.putheader('Connection','close')
        c.endheaders()

    def response(self):
        # Keep the socket reference until lifecycle observation finishes;
        # HTTPConnection.getresponse would auto-close on Connection: close.
        response=http.client.HTTPResponse(self.socket);response.begin()
        return response

    def owned(self):
        return [r for r in self.session.rows() if r['remote_port']==self.remote_port and r['owned']]

    def close(self):
        if self.connection:self.connection.close()
        self.socket.close()


class Scenario:
    def __init__(self,root,case):
        self.root=Path(root);self.case=case;self.scenario=uuid.uuid4().hex
        self.f=fixture(root,large=True,diagnostics=True)
        self.start=time.monotonic();self.peers=[];self.pinned=set();self.probes=[]
        self.trace=Trace(self.root/'client-events.jsonl')
        self.observer=None

    def rows(self):
        code="import json,sys;from scripts.https_runtime import socket_rows;print(json.dumps(socket_rows(int(sys.argv[1]),int(sys.argv[2]))))"
        return json.loads(self.f.service.native(code,str(self.f.service.proxy_pid),str(self.f.service.port)))

    def normal(self,busy=False):
        started=time.monotonic();scenario=uuid.uuid4().hex
        try:
            c=Target(self.f.service.connection).connection(timeout=2,diagnostic=self.trace,scenario=scenario)
            c.close();result=dict(status=200,seconds=time.monotonic()-started,scenario=scenario)
        except ValueError as exc:
            if not busy or str(exc)!='service-verification-failed':raise
            # Require an actual 503, not merely any ValueError or TLS failure.
            rows=[json.loads(x) for x in (self.root/'client-events.jsonl').read_text().splitlines()]
            assert any(r.get('trace')==scenario and r.get('status')==503 for r in rows)
            result=dict(status=503,seconds=time.monotonic()-started,scenario=scenario)
        assert result['seconds']<2.5,result
        self.probes.append(result);return result

    def release(self,peer,seconds=8):
        end=time.monotonic()+seconds
        while peer.owned():
            if time.monotonic()>end:return False
            time.sleep(.1)
        time.sleep(.12)  # Permit the independent 100ms observer to sample release.
        return True

    def terminal(self,peer,seconds=3):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            peer.socket.settimeout(min(.5,end-time.monotonic()))
            try:
                part=peer.socket.recv(65536)
                if not part:return dict(observed=True,kind='tls-or-tcp-eof',seconds=time.monotonic()-self.start)
            except socket.timeout:continue
            except OSError as exc:return dict(observed=True,kind=type(exc).__name__,seconds=time.monotonic()-self.start)
        return dict(observed=False)

    def artifacts(self):
        result={}
        for name in ('application-events.jsonl','proxy-events.jsonl','startup-events.jsonl'):
            text=self.f.service.native("import pathlib,sys;p=pathlib.Path(sys.argv[1]);print(p.read_text() if p.exists() else '')",self.f.service.runtime+'/'+name)
            (self.root/name).write_text(text)
            result[name]=[json.loads(line) for line in text.splitlines()]
        return result

    def close(self):
        for p in self.peers:p.close()
        self.trace.close();self.f.close()


def header_case(s,mixed=False):
    peers=[Peer(s,s.scenario)]
    if mixed:peers.append(Peer(s,uuid.uuid4().hex))
    for peer in peers:
        peer.socket.sendall(('GET /health HTTP/1.1\r\nHost: 127.0.0.1:'+str(s.f.service.port)+'\r\nX-Wait: ').encode())
    start=time.monotonic();sent=0
    while time.monotonic()-start<6:
        for peer in peers:
            try:peer.socket.sendall(b'a');sent+=1
            except OSError:pass
        if mixed or sent==5:s.normal()
        time.sleep(.4 if mixed else .2)
    statuses=[]
    for peer in peers:
        peer.socket.settimeout(2)
        try:r=peer.response();statuses.append(r.status);r.read(8192)
        except OSError:statuses.append('closed')
        assert s.release(peer,3)
    assert all(code in (408,'closed') for code in statuses),statuses
    return dict(protection='PASS',statuses=statuses,continuous_header_bytes=sent,max_rogue_connections=len(peers))


def handshake_case(s):
    peer=Peer(s,s.scenario,tls=False)
    peer.socket.sendall(b'\x16\x03\x01')  # Incomplete TLS record, not a plaintext HTTP request.
    s.normal();peer.socket.settimeout(8)
    try:closed=peer.socket.recv(32)==b''
    except OSError:closed=True
    assert closed and s.release(peer,2)
    return dict(protection='PASS',handshake_not_completed=True,closed=True)


def upload_case(s,trickle=False):
    f=s.f;client=f.admin.publisher(f.pa);m=remote_manifest(f.a,f.task);task=str(uuid.uuid4())
    client.request('POST','/projects/'+f.pa+'/tasks',dict(task=task,manifest=m))
    client.request('PUT','/tasks/'+task+'/files/0',file=paths(f,f.task)/m['files'][0]['path'])
    peer=Peer(s,s.scenario);entry=m['files'][1]
    start=time.monotonic()
    s.trace.emit('client-upload-start',trace=s.scenario)
    peer.send_request('PUT','/tasks/'+task+'/files/1',size=entry['size'],auth=True)
    peer.socket.sendall(b'F'*65536);sent=65536;last_probe=0;released=False
    while time.monotonic()-start<(65 if trickle else 11):
        elapsed=time.monotonic()-start
        if trickle:
            try:peer.socket.sendall(b'F'*4096);sent+=4096
            except OSError:break
        if elapsed-last_probe>3:
            s.normal(busy=elapsed<32);last_probe=elapsed
            if not peer.owned():released=True;break
        time.sleep(.2)
    peer.socket.settimeout(2)
    try:r=peer.response();status=r.status;r.read(8192)
    except OSError:status='closed'
    assert s.release(peer,8),'Proxy did not release the rejected upload within the measured bound'
    s.normal()
    state=client.request('GET','/tasks/'+task)
    assert state['state']=='receiving' and state['verified']==[0]
    client.request('POST','/tasks/'+task+'/cancel',{})
    return dict(protection='PASS',status=status,client_sent=sent,file_total=entry['size'],
                seconds=time.monotonic()-start,no_complete_version=True,
                request_started_windows_seconds=start-s.start,measurement_origin='request-header-send',
                continuous_body=trickle,proxy_release_seen_while_sending=released)


def download_case(s,trickle=False):
    f=s.f;state=f.admin.publisher(f.pa).publish(f.a.snap,f.task)
    index=next(i for i,e in enumerate(state['manifest']['files']) if e['size']==8*1024**2)
    peer=Peer(s,s.scenario);request_started=time.monotonic()
    s.trace.emit('client-download-start',trace=s.scenario)
    peer.send_request('GET',f'/tasks/{state["task"]}/files/{index}',auth=True)
    response=peer.response();assert response.status==200
    count=0;start=time.monotonic();released_at=None;after_release=0;last_probe=-10
    while time.monotonic()-start<40:
        elapsed=time.monotonic()-start
        if elapsed-last_probe>=4:
            s.normal(busy=elapsed<32);last_probe=elapsed
            if not peer.owned() and released_at is None:
                released_at=dict(windows_seconds=time.monotonic()-s.start,body_wait_seconds=elapsed,client_bytes=count,
                    ssl_pending=peer.socket.pending(),kernel=[r for r in s.rows() if r['remote_port']==peer.remote_port])
        if trickle:
            peer.socket.settimeout(.5)
            try:part=response.read1(1024)
            except socket.timeout:part=None
            if part==b'':break
            if part:
                count+=len(part)
                if released_at is not None:after_release+=len(part)
        time.sleep(.1)
    # A happens-before checkpoint, not subtraction of Windows/WSL clocks.
    released_before_drain=not peer.owned()
    assert released_before_drain,'Server still owned the connection before client drain'
    # Only after independently observing server FD release, enlarge this test
    # receiver's window to drain queued data; this is not server protection.
    peer.socket.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024**2)
    drain_start=time.monotonic();terminal=None
    s.trace.emit('client-drain-start',trace=s.scenario,received=count)
    while time.monotonic()-drain_start<15:
        peer.socket.settimeout(.5)
        try:part=response.read1(65536)
        except socket.timeout:continue
        except OSError:break
        if not part:break
        count+=len(part)
        if released_at is not None:after_release+=len(part)
    drain_end=time.monotonic();s.trace.emit('client-drain-end',trace=s.scenario,received=count)
    terminal=s.terminal(peer,3)
    released=s.release(peer,3);s.normal()
    assert terminal['observed'] and released
    unchanged=f.admin.publisher(f.pa).request('GET','/tasks/'+state['task'])==state
    assert unchanged
    return dict(protection='PARTIAL',measured_release=released,terminal=terminal,client_bytes=count,
                total=8*1024**2,rogue_ignores_official_deadline=True,trickle=trickle,
                proxy_released_before_fast_drain=released_before_drain,
                client_bytes_after_release_checkpoint=after_release,release_checkpoint=released_at,
                receive_window_increased_after_release=True,version_unchanged=unchanged,
                request_started_windows_seconds=request_started-s.start,body_wait_started_windows_seconds=start-s.start,
                drain_started_windows_seconds=drain_start-s.start,drain_ended_windows_seconds=drain_end-s.start,
                limitation='Measured lifecycle is not a general absolute front-end lifetime guarantee')


def keepalive_case(s):
    peer=Peer(s,s.scenario);time.sleep(3)
    idle=s.terminal(peer,2);assert idle['observed'] and s.release(peer,2)
    another=Peer(s,uuid.uuid4().hex);count=1;last_connection=None
    for _ in range(99):
        another.connection.request('GET','/health',headers={'X-Workbench-Trace':uuid.uuid4().hex})
        r=another.connection.getresponse();r.read(8192);assert r.status==200
        last_connection=r.getheader('X-Workbench-Connection');count+=1
        time.sleep(.025)
    assert count==100 and another.connection.sock is None
    assert last_connection.split(':')[-1]=='100'
    assert s.release(another,3)
    return dict(protection='PASS',idle_closed=idle,reused_requests=count,request_cap_closed=True,
                not_an_active_request_total_deadline=True)


def normal_control(s):
    f=s.f;client=f.admin.publisher(f.pa);v1=client.publish(f.a.snap,f.a.v1)
    m=remote_manifest(f.a,f.task);task=str(uuid.uuid4())
    client.request('POST','/projects/'+f.pa+'/tasks',dict(task=task,manifest=m));durations=[]
    for index,entry in enumerate(m['files']):
        peer=Peer(s,uuid.uuid4().hex,receive_buffer=65536)
        peer.send_request('PUT',f'/tasks/{task}/files/{index}',size=entry['size'],auth=True)
        start=time.monotonic()
        with (paths(f,f.task)/entry['path']).open('rb') as source:
            while chunk:=source.read(65536):peer.socket.sendall(chunk);time.sleep(len(chunk)/1024**2)
        r=peer.response();body=r.read(2*1024**2);assert r.status==200
        json.loads(body);durations.append(time.monotonic()-start);peer.close()
    state=client.request('POST','/tasks/'+task+'/commit',{});assert state['healthy']
    index=next(i for i,e in enumerate(m['files']) if e['size']==8*1024**2);entry=m['files'][index]
    peer=Peer(s,uuid.uuid4().hex,receive_buffer=65536)
    peer.send_request('GET',f'/tasks/{task}/files/{index}',auth=True);r=peer.response();assert r.status==200
    count=0;h=hashlib.sha256();start=time.monotonic()
    while chunk:=r.read1(65536):h.update(chunk);count+=len(chunk);time.sleep(len(chunk)/1024**2)
    elapsed=time.monotonic()-start
    assert count==entry['size'] and h.hexdigest()==entry['sha256']
    versions=client.versions();assert len(versions)==2 and next(v for v in versions if v['task']==v1['task'])==v1
    return dict(protection='PASS',supported_test_rate_bytes_per_second=1024**2,upload_seconds=durations,
                download_seconds=elapsed,download_bytes=count,download_hash=True,v1_unchanged=True)


CASES={'handshake':handshake_case,'headers':header_case,'mixed-headers':lambda s:header_case(s,True),
       'upload-idle':upload_case,'upload-trickle':lambda s:upload_case(s,True),
       'download-none':download_case,'download-trickle':lambda s:download_case(s,True),
       'keepalive':keepalive_case,'normal-control':normal_control}


def run(root,case):
    root=Path(root)
    sources=list((ROOT/'mechanical_project_namer').rglob('*.py'))
    sources += [ROOT/'scripts'/n for n in ('https_runtime.py','https_harness.py','auth_harness.py',
                'publish_harness.py','verify_https_connections.py','https_connection_observer.py')]
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    budget=dict(case=case,max_rogue_connections=2,max_normal_connections=1,observer_seconds=100,
                observer_bytes=4*1024**2,normal_response_seconds=2.5,
                busy_response_503_allowed_during_single_active_business=True,
                supported_slow_control_bytes_per_second=1024**2,no_host_network_changes=True,
                handshake_or_headers_release_seconds=8,upload_idle_seconds=10,upload_total_seconds=30,
                proxy_stream_idle_seconds=10,measured_download_release_before_drain_seconds=40,
                upload_with_lingering_observation_seconds=73,client_drain_seconds=18)
    root.parent.mkdir(parents=True,exist_ok=True);(root.parent/(root.name+'-budget.json')).write_bytes(data(budget))
    s=Scenario(root,case);result=None;error=None;rows=[]
    try:
        # Setup fixtures/tasks before observing the deliberately slow connection.
        s.observer=Observer(s.f.service,s.scenario)
        result=CASES[case](s);s.f.protect()
    except Exception as exc:
        error=dict(type=type(exc).__name__,code='scenario-assertion-or-transport-failed')
    finally:
        try:
            if s.observer:rows=s.observer.finish(root)
            artifacts=s.artifacts()
            identities=[dict(trace=p.trace,windows=p.windows_endpoints,linux=p.native) for p in s.peers]
            lifecycles=[]
            for peer in s.peers:
                owned=[(i,r) for i,r in enumerate(rows) if any(c['owned'] and c['remote_port']==peer.remote_port for c in r['connections'])]
                matching=[e for e in artifacts['application-events.jsonl'] if e.get('trace')==peer.trace]
                start=next((r for r in matching if r['event']=='app-request-start'),None)
                end=next((r for r in matching if r['event']=='app-worker-end'),None)
                lifecycle=dict(trace=peer.trace,linux_remote_port=peer.remote_port,application_events=matching,
                               worker_slot_released=bool(end and end['slot_busy'] is False))
                if start and end:lifecycle['application_seconds']=(end['monotonic_ns']-start['monotonic_ns'])/10**9
                if owned:
                    first,last=owned[0],owned[-1]
                    lifecycle.update(proxy_first_owned_seconds=first[1]['seconds'],proxy_last_owned_seconds=last[1]['seconds'],
                                     proxy_released_observed=last[0]<len(rows)-1,
                                     closing_samples=[dict(seconds=r['seconds'],connections=[c for c in r['connections'] if c['remote_port']==peer.remote_port]) for r in rows[last[0]+1:]])
                lifecycles.append(lifecycle)
            filesystem=json.loads(s.f.service.native("import json,pathlib,sys;v=json.load(open(sys.argv[1]));paths=[p for root in (pathlib.Path(v['storage']),pathlib.Path(sys.argv[2])) for p in root.rglob('*') if p.is_file()];print(json.dumps(dict(partial=sum(p.name.endswith(('.partial','.copy')) for p in paths),proxy_temp=sum('client-temp' in p.parts or 'proxy-temp' in p.parts for p in paths))))",s.f.service.config,s.f.service.runtime))
            if filesystem['partial'] or filesystem['proxy_temp']:
                error=error or dict(type='ResidualFiles',code='unexpected-transfer-residual')
            unchanged=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value for name,value in hashes.items())
            if not unchanged:error=error or dict(type='SourceChanged',code='source-changed-during-case')
            report=dict(measurement_complete=error is None,passed=error is None,case=case,scenario=s.scenario,
                        result=result,error=error,normal_probes=s.probes,connection_mappings=identities,
                        clock_policy='Within-domain monotonic intervals; scenario/tuple correlation across Windows and Linux, never cross-clock subtraction',
                        observer_samples=len(rows),application_pid=s.f.service.pid,proxy_pid=s.f.service.proxy_pid,
                        client_pid=os.getpid(),observer_pid=s.observer.ready['observer_pid'],lifecycles=lifecycles,
                        filesystem=filesystem,source_hashes=hashes,code_unchanged=unchanged)
            (root/'report.json').write_bytes(data(report))
        finally:s.close()
    assert error is None,'Connection scenario failed; inspect private bounded evidence'
    print('Real HTTPS connection lifecycle recorded:',case,result['protection'])


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--case',choices=sorted(CASES),required=True)
    a=p.parse_args();run(a.output,a.case)
