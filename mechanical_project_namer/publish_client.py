# SPDX-License-Identifier: GPL-3.0-only
import hashlib,http.client,json,os,uuid
from pathlib import Path
from .publish_protocol import *

class Client:
    def __init__(self,config):
        self.config=config
        if set(config)!={'port','service','project','token'} or type(config['port']) is not int or not 1<=config['port']<=65535:raise ProtocolError('client-config')
        uid(config['service']);uid(config['project'])
    def request(self,method,route,value=None,file=None,cancel=None):
        c=http.client.HTTPConnection('127.0.0.1',self.config['port'],timeout=10)
        try:
            headers={'Authorization':'Bearer '+self.config['token']}
            if file is not None:
                c.putrequest(method,route);c.putheader('Authorization',headers['Authorization']);c.putheader('Content-Length',str(Path(file).stat().st_size));c.endheaders()
                with safe(file).open('rb') as stream:
                    while b:=stream.read(65536):
                        if cancel and cancel.is_set():raise ProtocolError('client-cancelled')
                        c.send(b)
            else:
                body=data(value) if value is not None else None
                c.request(method,route,body=body,headers=headers)
            r=c.getresponse();body=r.read(MAX_JSON+1)
            if r.status!=200 or len(body)>MAX_JSON:raise ProtocolError('service-rejected-'+str(r.status))
            return json.loads(body)
        finally:c.close()
    def info(self):
        v=self.request('GET','/info')
        if v['service']!=self.config['service'] or v['protocol']!=1:raise ProtocolError('service-identity')
        return v
    def publish(self,snapshots,task,*,cancel=None,progress=None):
        snapshots.verify(task,cancel=cancel);row=snapshots._get(task);local=json.loads(row['request_json'])
        m=manifest(dict(store=snapshots.store_id,local_project=row['project_id'],version=row['version_id'],label=row['label'],files=[dict(path=f['relative_path'],role=f['role'],size=f['size'],sha256=f['sha256']) for f in local['files']]))
        self.info();remote=str(uuid.uuid5(uuid.UUID(self.config['service']),self.config['project']+m['store']+m['version']))
        route='/tasks/'+remote
        state=self.request('POST','/projects/'+self.config['project']+'/tasks',dict(task=remote,manifest=m))
        if state['state']=='complete':
            if not state['healthy']:raise ProtocolError('version-unavailable')
            return state
        for i,f in enumerate(local['files']):
            if i not in state['verified']:
                if progress:progress(f'传输中：{i+1}/{len(local["files"])}')
                self.request('PUT',route+'/files/'+str(i),file=snapshots.store/'versions'/row['version_id']/'payload'/f['relative_path'],cancel=cancel)
        if progress:progress('服务端校验中…')
        try:state=self.request('POST',route+'/commit',{})
        except (OSError,http.client.HTTPException):state=self.request('GET',route)
        if state['state']!='complete' or not state['healthy']:raise ProtocolError('not-committed')
        return state
    def versions(self):return self.request('GET','/projects/'+self.config['project'])['versions']
    def cancel(self,task):return self.request('POST','/tasks/'+uid(task)+'/cancel',{})
    def download(self,task,target,*,cancel=None,progress=None):
        self.info();state=self.request('GET','/tasks/'+uid(task));m=manifest(state['manifest'])
        if state['project']!=self.config['project'] or state['state']!='complete' or not state['healthy']:raise ProtocolError('version-unavailable')
        target=safe(target)
        if target.exists() or not target.parent.is_dir():raise ProtocolError('new-directory-required')
        protected=[Path.home()/'AppData/Local/MechanicalProjectNamer',Path.home()/'AppData/Roaming/MechanicalProjectNamer']
        if any(target.is_relative_to(p) or p.is_relative_to(target) for p in protected):raise ProtocolError('production-output-forbidden')
        for p in target.parents:
            if (p/'.git').exists() or (p/'.mw-store.json').exists() or (p/'.mechanical-workbench-development.json').exists():raise ProtocolError('output-overlap')
        stage=target.parent/('.publish-download-'+uuid.uuid4().hex);stage.mkdir()
        for i,f in enumerate(m['files']):
            if progress:progress(f'下载并校验：{i+1}/{len(m["files"])}')
            out=safe(stage/f['path']);out.parent.mkdir(parents=True,exist_ok=True)
            c=http.client.HTTPConnection('127.0.0.1',self.config['port'],timeout=10)
            try:
                c.request('GET',f'/tasks/{task}/files/{i}',headers={'Authorization':'Bearer '+self.config['token']});r=c.getresponse()
                if r.status!=200 or r.getheader('Content-Length')!=str(f['size']):raise ProtocolError('download-denied')
                h=hashlib.sha256();n=0
                with out.open('xb') as stream:
                    while b:=r.read(65536):
                        if cancel and cancel.is_set():raise ProtocolError('client-cancelled')
                        n+=len(b)
                        if n>f['size']:raise ProtocolError('download-limit')
                        h.update(b);stream.write(b)
                    stream.flush();os.fsync(stream.fileno())
                if n!=f['size'] or h.hexdigest()!=f['sha256']:raise ProtocolError('download-hash')
            finally:c.close()
        os.rename(stage,target);return {'downloaded':len(m['files'])}
