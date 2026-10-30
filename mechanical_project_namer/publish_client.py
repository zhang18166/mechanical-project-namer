# SPDX-License-Identifier: GPL-3.0-only
"""Loopback client; a sent request is never equivalent to a healthy commit."""
import hashlib
import http.client
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from .publish_protocol import *
from .transport import Target,response_bytes


def error_code(exc):
    if isinstance(exc, ProtocolError):
        code = str(exc)
        if re.fullmatch('[a-z0-9-]{1,100}', code):
            return code
    return 'transport-interrupted-retry' if isinstance(exc, (OSError,http.client.HTTPException)) else 'local-operation-failed'


class Client:
    def __init__(self, config, *, config_ref=''):
        self.config = dict(config)
        self.config_ref = config_ref
        connection={k:v for k,v in config.items() if k not in ('project','token','account')}
        self.target=Target(connection)
        if self.target.identity:
            uid(config.get('account'))
            self.config['target_identity']=self.target.identity+':'+config['account']
        uid(config['service']); uid(config['project'])
        self.bound_config=dict(self.config)

    def request(self, method, route, value=None, file=None, cancel=None):
        if self.config!=self.bound_config:raise ProtocolError('target-changed-login-required')
        c = self.target.connection()
        try:
            headers = {'Authorization':'Bearer '+self.config['token']}
            if file is not None:
                c.putrequest(method,route)
                c.putheader('Authorization',headers['Authorization'])
                c.putheader('Content-Length',str(safe(file).stat().st_size))
                c.endheaders()
                deadline=time.monotonic()+30
                with safe(file).open('rb') as stream:
                    while b:=stream.read(65536):
                        left=deadline-time.monotonic()
                        if left<=0:raise ProtocolError('transfer-timeout')
                        c.sock.settimeout(min(10,left))
                        if cancel and cancel.is_set():
                            raise ProtocolError('transfer-interrupted-retry')
                        c.send(b)
            else:
                c.request(method,route,body=data(value) if value is not None else None,headers=headers)
            r=c.getresponse(); body=response_bytes(c,r)
            if len(body)>MAX_JSON:
                raise ProtocolError('response-limit')
            if r.status!=200:
                try:
                    code=json.loads(body)['error']
                except Exception:
                    code='service-rejected'
                if not isinstance(code,str) or not re.fullmatch('[a-z0-9-]{1,80}',code):
                    code='service-rejected'
                raise ProtocolError('http-'+str(r.status)+'-'+code)
            return json.loads(body)
        finally:
            c.close()

    def info(self):
        v=self.request('GET','/info')
        if v['service']!=self.config['service'] or v['protocol']!=1:
            raise ProtocolError('service-identity')
        return v

    def publish(self,snapshots,task,*,cancel=None,progress=None,restart_cancelled=False):
        from .publish_ledger import PublishLedger
        snapshots.verify(task,cancel=cancel)
        row=snapshots._get(task); local=json.loads(row['request_json'])
        m=manifest(dict(store=snapshots.store_id,local_project=row['project_id'],version=row['version_id'],label=row['label'],
                        files=[dict(path=f['relative_path'],role=f['role'],size=f['size'],sha256=f['sha256']) for f in local['files']]))
        ledger=PublishLedger(snapshots)
        key,remote=ledger.begin(self.config,m,restart=restart_cancelled,config_ref=self.config_ref)
        route='/tasks/'+remote
        try:
            self.info()
            state=self.request('POST','/projects/'+self.config['project']+'/tasks',dict(task=remote,manifest=m))
            if state['manifest']!=m or state['project']!=self.config['project'] or state['task']!=remote:
                raise ProtocolError('task-binding')
            ledger.update(key,state['state'],confirmation=state)
            if state['state']=='cancelled':
                raise ProtocolError('task-cancelled-explicit-restart-required')
            if state['state']=='complete':
                if not state['healthy']:
                    raise ProtocolError('version-unavailable')
                return state
            for i,f in enumerate(local['files']):
                if cancel and cancel.is_set():
                    raise ProtocolError('transfer-interrupted-retry')
                if progress:
                    progress(f'目标 {self.config["service"]}；已确认 {len(state["verified"])}/{len(local["files"])}；传输文件 {i+1}')
                if i not in state['verified']:
                    ledger.update(key,'transferring')
                    self.request('PUT',route+'/files/'+str(i),file=snapshots.store/'versions'/row['version_id']/'payload'/f['relative_path'],cancel=cancel)
                    state=self.request('GET',route)
                    ledger.update(key,'transferring',confirmation=state)
            if cancel and cancel.is_set():
                raise ProtocolError('transfer-interrupted-retry')
            if progress:
                progress(f'已确认 {len(state["verified"])}/{len(local["files"])}；服务端校验中…')
            ledger.update(key,'verifying')
            try:
                state=self.request('POST',route+'/commit',{})
            except (OSError,http.client.HTTPException):
                state=self.request('GET',route)
            ledger.update(key,state['state'],confirmation=state)
            if state['state']!='complete' or not state['healthy']:
                raise ProtocolError('not-committed')
            return state
        except Exception as exc:
            code=error_code(exc)
            ledger.update(key,'cancelled' if 'task-cancelled' in code else 'failed',code=code)
            raise

    def versions(self):
        self.info()
        return self.request('GET','/projects/'+self.config['project'])['versions']

    def cancel(self,task):
        self.info()
        return self.request('POST','/tasks/'+uid(task)+'/cancel',{})

    def download(self,task,target,*,cancel=None,progress=None):
        self.info()
        state=self.request('GET','/tasks/'+uid(task)); m=manifest(state['manifest'])
        if state['project']!=self.config['project'] or state['state']!='complete' or not state['healthy']:
            raise ProtocolError('version-unavailable')
        target=safe(target)
        if target.exists() or not target.parent.is_dir():
            raise ProtocolError('new-directory-required')
        protected=[Path.home()/'AppData/Local/MechanicalProjectNamer',Path.home()/'AppData/Roaming/MechanicalProjectNamer']
        if any(target.is_relative_to(p) or p.is_relative_to(target) for p in protected):
            raise ProtocolError('production-output-forbidden')
        for p in target.parents:
            if any((p/marker).exists() for marker in ('.git','.mw-store.json','.mechanical-workbench-development.json','.publish-owner.json')):
                raise ProtocolError('output-overlap')
        total=sum(f['size'] for f in m['files'])
        if shutil.disk_usage(target.parent).free < total+32*1024**2:
            raise ProtocolError('download-space')
        stage=safe(target.parent/('.publish-download-'+str(uuid.uuid5(uuid.UUID(self.config['service']),task+str(target)))))
        owner=dict(service=self.config['service'],task=task,fingerprint=sha(m),target=str(target))
        receipt=safe(stage/'owner.json')
        if stage.exists():
            if not receipt.is_file() or json.loads(receipt.read_bytes())!=owner:
                raise ProtocolError('unknown-download')
        else:
            stage.mkdir()
            with receipt.open('xb') as out:
                out.write(data(owner))
        payload=safe(stage/'payload'); payload.mkdir(exist_ok=True)
        allowed={f['path'] for f in m['files']}
        for p in payload.rglob('*'):
            if p.is_file() and p.relative_to(payload).as_posix() not in allowed:
                raise ProtocolError('unknown-download-file')
        for i,f in enumerate(m['files']):
            if progress:
                progress(f'下载并校验：{i+1}/{len(m["files"])}')
            out=safe(payload/f['path']); out.parent.mkdir(parents=True,exist_ok=True)
            if out.exists() and digest(out)=={k:f[k] for k in ('size','sha256')}:
                continue
            c=self.target.connection()
            try:
                c.request('GET',f'/tasks/{task}/files/{i}',headers={'Authorization':'Bearer '+self.config['token']})
                r=c.getresponse()
                if r.status!=200 or r.getheader('Content-Length')!=str(f['size']):
                    raise ProtocolError('download-denied')
                h=hashlib.sha256(); n=0
                deadline=time.monotonic()+30
                with out.open('wb') as stream:
                    while True:
                        left=deadline-time.monotonic()
                        if left<=0:raise ProtocolError('download-timeout')
                        sock=c.sock or getattr(c,'_workbench_socket',None)
                        if sock:sock.settimeout(min(10,left))
                        b=r.read1(65536)
                        if not b:break
                        if cancel and cancel.is_set():
                            raise ProtocolError('download-interrupted-retry')
                        n+=len(b)
                        if n>f['size']:
                            raise ProtocolError('download-limit')
                        h.update(b); stream.write(b)
                    stream.flush(); os.fsync(stream.fileno())
                if n!=f['size'] or h.hexdigest()!=f['sha256']:
                    raise ProtocolError('download-hash')
            finally:
                c.close()
        if {p.relative_to(payload).as_posix() for p in payload.rglob('*') if p.is_file()}!=allowed:
            raise ProtocolError('download-file-set')
        os.rename(payload,target)
        return {'downloaded':len(m['files'])}
