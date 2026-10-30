# SPDX-License-Identifier: GPL-3.0-only
"""External-only deterministic transport fixtures. No desktop dependencies on server."""
import hashlib
import json
import os
os.environ.setdefault('MW_ALLOW_ISOLATED_HTTP','1')  # Explicit historical test-only route.
import subprocess
import sys
import time
import uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from mechanical_project_namer.publish_protocol import data,sha,digest
from mechanical_project_namer.publish_client import Client
from scripts.setup_publish_test import provision


def write(path,value):
    # Replace control/config atomically so a running test never reads half JSON.
    temp=path.with_suffix('.new')
    temp.write_bytes(data(value));os.replace(temp,path)


def wait_publish_ready(process,path,seconds=12):
    """Existence precedes the child's JSON write; wait within the same budget."""
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if process.poll() is not None:raise RuntimeError('service-start')
        try:ready=json.loads(Path(path).read_bytes())
        except (FileNotFoundError,json.JSONDecodeError):
            time.sleep(.02);continue
        if not isinstance(ready,dict) or type(ready.get('pid')) is not int or type(ready.get('port')) is not int or not isinstance(ready.get('service'),str):
            raise RuntimeError('invalid-service-ready')
        return ready
    raise RuntimeError('service-start-timeout')


class Fixture:
    def __init__(self,root,*,large=False):
        from mechanical_project_namer.data import Database,Repository
        from mechanical_project_namer.snapshots import SnapshotService
        from mechanical_project_namer.project_files import ProjectFileCatalog
        from mechanical_project_namer.projects import ProjectService
        from mechanical_project_namer.development import MARKER,IDENTITY
        self.root=Path(root).absolute()
        if self.root.exists() or self.root.is_relative_to(ROOT):raise ValueError('new external fixture required')
        self.root.mkdir(parents=True)
        profile=self.root/'client';profile.mkdir();(profile/MARKER).write_bytes(data(IDENTITY))
        self.repo=Repository(Database(profile/'db.sqlite'));self.repo.database.migrate()
        self.snap=SnapshotService(self.repo,profile/'archive')
        work=profile/'work';work.mkdir()
        self.project=ProjectService(self.repo).create('同名虚构项目','','TEST',directory=str(work),modules=[('DJ','虚构模块')])
        from mechanical_project_namer.numbering.simple import SIMPLE_TYPE
        from mechanical_project_namer.snapshots import now
        self.repo.save_named_record(self.project.id,'DJ',SIMPLE_TYPE,'虚构已有件')
        void=self.repo.save_named_record(self.project.id,'DJ',SIMPLE_TYPE,'虚构作废件')
        self.repo.set_record_status(void.id,'void')
        with self.repo.database.transaction() as con:
            con.execute('INSERT INTO number_reservations(project_id,module_code,part_category,part_sequence,status,reserved_at,reason) VALUES (?,?,?,?,?,?,?)',
                        (self.project.id,'DJ',SIMPLE_TYPE,50,'reserved',now(),'FICTIONAL TEST ONLY'))
        sizes=[1024**2,4*1024**2,8*1024**2] if large else [4096,8192,16384]
        self.sources=[]
        for i,size in enumerate(sizes):
            f=work/['中文 (零件).prt','分目录/中性.stp','图纸/查看.pdf'][i];f.parent.mkdir(exist_ok=True)
            block=(b'FICTIONAL TRANSPORT PLACEHOLDER - NOT VALID CAD/PDF '+bytes([65+i]))*2048
            with f.open('wb') as out:
                left=size
                while left:
                    part=block[:left];out.write(part);left-=len(part)
            self.sources.append(f)
        cat=ProjectFileCatalog(self.repo);cat.register_attachments(self.project.id,[str(f) for f in self.sources])
        choices=[{'file_id':r['file_id']} for r in cat.list_files(self.project.id)]
        self.v1=self.snap.prepare(self.project.id,work,choices,'V1');self.snap.run(self.v1)
        # Different version identity; files are frozen already, unchanged during tests.
        self.v2=self.snap.prepare(self.project.id,work,choices,'V2');self.snap.run(self.v2)
        self.before=self.protected()
        self.services=[]

    def protected(self):
        paths=self.sources+[p for p in self.snap.store.rglob('*') if p.is_file() and 'locks' not in p.parts]
        with self.repo.database.connect() as c:
            tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            business={}
            for t in tables:
                if t=='local_snapshot_events':continue  # verify adds its own audit event
                rows=[dict(r) for r in c.execute('SELECT * FROM "'+t+'"')]
                if t=='local_versions':
                    rows=[{k:v for k,v in r.items() if k not in ('updated_at','integrity','error')} for r in rows]
                business[t]=rows
        return dict(files={str(p):[hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns] for p in paths},business=business)

    def assert_protected(self):
        assert self.protected()==self.before,'source/snapshot/business changed'

    def service(self,name='server'):
        s=Service(self.root/name,self.snap.store_id,self.project.id,self.root/'client')
        self.services.append(s);return s

    def close(self):
        for s in self.services:s.stop()


class Service:
    def __init__(self,root,store,project,deny):
        self.root=root;provision(root,store,project)
        self.config=root/'server.json';self.control=root/'control.json';self.events=root/'events.jsonl'
        self.process=None;self.deny=deny;self.pid=None;self.port=None
        self.set_control()
        cfg=json.loads(self.config.read_bytes())
        self.other=str(uuid.uuid4());cfg['projects'][self.other]={'store':store,'local_project':2}
        # Four roles; outsider is authorized only for the other project.
        cfg['identities']['outsider']['grants']={self.other:['read','publish','download']}
        token=__import__('secrets').token_urlsafe(32)
        cfg['identities']['downloader']={'token_hash':hashlib.sha256(token.encode()).hexdigest(),'grants':{next(iter(cfg['projects'])):['read','download']}}
        write(root/'downloader.json',dict(port=1,service=cfg['service'],project=next(iter(cfg['projects'])),token=token))
        write(self.config,cfg)

    def set_control(self,**kwargs):
        write(self.control,dict(isolated_test=True,events=str(self.events),nonce=str(uuid.uuid4()),**kwargs))

    def start(self,port=0):
        self.ready=self.root/('ready-'+uuid.uuid4().hex+'.json')
        self.process=subprocess.Popen([sys.executable,'-m','mechanical_project_namer.publish_server','--config',str(self.config),'--ready',str(self.ready),'--port',str(port),'--test-control',str(self.control),'--deny-root',str(self.deny)],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        ready=wait_publish_ready(self.process,self.ready);self.pid=ready['pid'];self.port=ready['port']
        for name in ['publisher','reader','outsider','downloader']:
            path=self.root/(name+'.json');cfg=json.loads(path.read_bytes());cfg['port']=self.port;write(path,cfg)
        return self.client()

    def client(self,name='publisher'):
        cfg=json.loads((self.root/(name+'.json')).read_bytes())
        return Client(cfg,config_ref=str(self.root/(name+'.json')))

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate();self.process.wait(12)

    def records(self):
        if not self.events.exists():return []
        return [json.loads(line) for line in self.events.read_bytes().splitlines() if line]

    def wait_event(self,predicate,timeout=20):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            for row in reversed(self.records()):
                if predicate(row):return row
            time.sleep(.02)
        raise AssertionError('test-event-timeout')


def remote_manifest(fixture,task):
    row=fixture.snap._get(task);frozen=json.loads(row['request_json'])
    return dict(store=fixture.snap.store_id,local_project=row['project_id'],version=row['version_id'],label=row['label'],files=[dict(path=f['relative_path'],role=f['role'],size=f['size'],sha256=f['sha256']) for f in frozen['files']])


def begin(s,fixture,local=None):
    c=s.client();m=remote_manifest(fixture,local or fixture.v2);tid=str(uuid.uuid4())
    c.request('POST','/projects/'+c.config['project']+'/tasks',dict(task=tid,manifest=m))
    return c,tid,m


def upload_all(c,tid,m,fixture):
    row=fixture.snap._get(fixture.v2)
    for i,f in enumerate(m['files']):
        c.request('PUT',f'/tasks/{tid}/files/{i}',file=fixture.snap.store/'versions'/row['version_id']/'payload'/f['path'])
