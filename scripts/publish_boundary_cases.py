# SPDX-License-Identifier: GPL-3.0-only
"""Additional fault matrix, separate from the normal resource run."""
import concurrent.futures
import hashlib
import http.client
import json
import shutil
import time
import uuid
from unittest.mock import patch

from scripts.publish_harness import begin,upload_all,remote_manifest
from mechanical_project_namer.publish_protocol import ProtocolError,data
from mechanical_project_namer.publish_ledger import PublishLedger


def boundary(f,mode):
    s=f.service();c=s.start()
    if mode in ('receive-space','receive-write'):
        s.set_control(point=mode,action='fail')
        try:c.publish(f.snap,f.v2)
        except ProtocolError as e:assert '503' in str(e)
        else:raise AssertionError('injected write accepted')
        s.set_control();v=c.publish(f.snap,f.v2);assert v['healthy']
    elif mode in ('idle','total'):
        c,tid,m=begin(s,f)
        s.set_control(idle_timeout=.15 if mode=='idle' else .3,total_timeout=.4)
        conn=http.client.HTTPConnection('127.0.0.1',s.port,timeout=3)
        started=time.monotonic()
        conn.putrequest('PUT',f'/tasks/{tid}/files/0');conn.putheader('Authorization','Bearer '+c.config['token']);conn.putheader('Content-Length',str(m['files'][0]['size']));conn.endheaders()
        try:
            if mode=='idle':
                conn.send(b'A'*20);time.sleep(.3)
            else:
                for _ in range(20):
                    conn.send(b'A'*128);time.sleep(.05)
            response=conn.getresponse();assert response.status==408;response.read()
        except (ConnectionError,http.client.RemoteDisconnected):pass
        finally:conn.close()
        elapsed=time.monotonic()-started;assert elapsed<2
        assert c.request('GET','/tasks/'+tid)['verified']==[]
        s.set_control();upload_all(c,tid,m,f);assert c.request('POST',f'/tasks/{tid}/commit',{})['healthy']
        return dict(mode=mode,seconds=elapsed,events=s.records())
    elif mode=='bounded-partials':
        c,tid,m=begin(s,f);wrong=f.root/'wrong.prt';wrong.write_bytes(b'X'*m['files'][0]['size'])
        for _ in range(12):
            try:c.request('PUT',f'/tasks/{tid}/files/0',file=wrong)
            except ProtocolError:pass
            else:raise AssertionError('wrong hash')
        partials=list((s.root/'storage/tasks'/tid).glob('*.partial'));assert len(partials)==1
        upload_all(c,tid,m,f);assert not list((s.root/'storage/tasks'/tid).glob('*.partial'))
        assert c.request('POST',f'/tasks/{tid}/commit',{})['healthy']
    elif mode=='unknown-seal':
        c,tid,m=begin(s,f);upload_all(c,tid,m,f)
        unknown=s.root/'storage/tasks'/(tid+'-sealed');unknown.mkdir();(unknown/'unknown.txt').write_bytes(b'PRESERVE')
        try:c.request('POST',f'/tasks/{tid}/commit',{})
        except ProtocolError as e:assert 'unknown-task-products' in str(e)
        else:raise AssertionError('unknown adopted')
        assert c.cancel(tid)['state']=='cancelled'
        fresh=str(uuid.uuid4());c.request('POST','/projects/'+c.config['project']+'/tasks',dict(task=fresh,manifest=m))
        upload_all(c,fresh,m,f);assert c.request('POST',f'/tasks/{fresh}/commit',{})['healthy']
        assert (unknown/'unknown.txt').read_bytes()==b'PRESERVE'
    elif mode in ('competition','cancel-commit'):
        if mode=='competition':
            m=remote_manifest(f,f.v2)
            def create(_):
                candidate=dict(m,version=str(uuid.uuid4()))
                try:
                    return c.request('POST','/projects/'+c.config['project']+'/tasks',dict(task=str(uuid.uuid4()),manifest=candidate))['state']
                except ProtocolError:return 'conflict'
            with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(create,range(2)))
            assert sorted(results)==['conflict','receiving']
        else:
            c,tid,m=begin(s,f);upload_all(c,tid,m,f)
            def call(action):
                try:return c.request('POST',f'/tasks/{tid}/{action}',{})
                except ProtocolError:return None
            with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(call,['cancel','commit']))
            state=c.request('GET','/tasks/'+tid)
            assert state['state'] in ('complete','cancelled')
            if state['state']=='complete':
                assert state['healthy'] and len(c.versions())==1
                assert c.cancel(tid)['state']=='complete'
            else:assert len(c.versions())==0
    elif mode.startswith('download'):
        v=c.publish(f.snap,f.v1);target=f.root/'download'
        if mode=='download-interrupt':
            s.set_control(action='disconnect-download',after_bytes=65536)
            try:c.download(v['task'],target)
            except Exception:pass
            else:raise AssertionError('download not interrupted')
            assert not target.exists()
            s.set_control();c.download(v['task'],target)
            assert s.records()[-1]['event']=='download-interrupted'
        elif mode=='download-existing':
            target.mkdir();p=target/'keep.txt';p.write_bytes(b'KEEP')
            try:c.download(v['task'],target)
            except ProtocolError as e:assert str(e)=='new-directory-required'
            else:raise AssertionError('overwritten')
            assert p.read_bytes()==b'KEEP'
        elif mode=='download-overlap':
            for dest in [f.snap.store/'bad',f.root/'client'/'bad',s.root/'storage'/'bad']:
                try:c.download(v['task'],dest)
                except ProtocolError as e:assert str(e)=='output-overlap'
                else:raise AssertionError('overlap accepted')
        else:
            with patch('shutil.disk_usage',return_value=shutil._ntuple_diskusage(1024,1000,24)):
                try:c.download(v['task'],target)
                except ProtocolError as e:assert str(e)=='download-space'
                else:raise AssertionError('space accepted')
            assert not target.exists()
        assert c.request('GET','/tasks/'+v['task'])['healthy']
    else:raise ValueError(mode)
    f.assert_protected()
    return dict(mode=mode,passed=True)
