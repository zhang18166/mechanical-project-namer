# SPDX-License-Identifier: GPL-3.0-only
"""Real HTTP in a separate process; all fixtures fictional, secrets remain external."""
import argparse,json,os,subprocess,sys,time,uuid,hashlib,importlib.abc
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.setup_publish_test import provision
from mechanical_project_namer.publish_protocol import data,digest,ProtocolError
from mechanical_project_namer.publish_client import Client
def run(E):
    from mechanical_project_namer.data import Database,Repository
    from mechanical_project_namer.snapshots import SnapshotService
    from mechanical_project_namer.project_files import ProjectFileCatalog
    from mechanical_project_namer.projects import ProjectService
    from mechanical_project_namer.development import MARKER,IDENTITY
    E.mkdir();profile=E/'client';profile.mkdir();(profile/MARKER).write_bytes(data(IDENTITY));db=Database(profile/'db.sqlite');db.migrate();repo=Repository(db)
    snap=SnapshotService(repo,profile/'archive');work=profile/'work';work.mkdir();f=work/'中文 (演示).prt';f.write_bytes(b'FICTIONAL SOURCE ONLY')
    project=ProjectService(repo).create('虚构发布项目','','DEMO',directory=str(work));cat=ProjectFileCatalog(repo);cat.register_attachments(project.id,[str(f)]);choices=[{'file_id':r['file_id']} for r in cat.list_files(project.id)]
    local=snap.prepare(project.id,work,choices,'V1');snap.run(local)
    provision(E/'service',snap.store_id,project.id)
    process=None
    def start(fault=''):
        ready=E/('ready-'+uuid.uuid4().hex+'.json')
        p=subprocess.Popen([sys.executable,'-m','mechanical_project_namer.publish_server','--config',str(E/'service/server.json'),'--ready',str(ready),'--fault',fault,'--deny-root',str(profile)],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline=time.monotonic()+10
        while not ready.exists():
            if p.poll() is not None or time.monotonic()>deadline:raise RuntimeError('service-start-failed')
            time.sleep(.03)
        port=json.loads(ready.read_bytes())['port']
        clients={}
        for name in ['publisher','reader','outsider']:
            path=E/'service'/(name+'.json');cfg=json.loads(path.read_bytes());cfg['port']=port;path.write_bytes(data(cfg));clients[name]=Client(cfg)
        return p,clients
    count=0;failures=[];t=time.monotonic()
    try:
        process,clients=start('lost-response');c=clients['publisher'];state=c.publish(snap,local);assert state['state']=='complete';count+=1
        assert c.publish(snap,local)['task']==state['task'];count+=1
        assert clients['reader'].versions()[0]['healthy'];count+=1
        for name,method,route,value in [('reader','POST','/tasks/'+state['task']+'/cancel',{}),('outsider','GET','/tasks/'+state['task'],None),('reader','GET','/tasks/'+state['task']+'/files/0',None)]:
            try:clients[name].request(method,route,value)
            except ProtocolError:count+=1
            else:raise AssertionError('authorization bypass')
        c.download(state['task'],E/'download-v1');assert digest(E/'download-v1'/f.name)==digest(snap.store/'versions'/state['version']/'payload'/f.name);count+=1
        process.terminate();process.wait(10)
        f.write_bytes(b'FICTIONAL V2');local2=snap.prepare(project.id,work,choices,'V2');snap.run(local2)
        process,clients=start('after-rename')
        try:clients['publisher'].publish(snap,local2)
        except (OSError,Exception):pass
        process.wait(10);assert process.returncode==73;count+=1
        process,clients=start();c=clients['publisher'];state2=c.publish(snap,local2)
        assert len(c.versions())==2 and state2['state']=='complete';count+=1
        c.download(state2['task'],E/'download-v2');assert (E/'download-v2'/f.name).read_bytes()==b'FICTIONAL V2';count+=1
        assert c.versions()[0]['healthy'];count+=1
        # Real interrupted body, wrong hash, frozen-binding conflict and cancellation.
        import http.client
        m=json.loads(json.dumps(state2['manifest']));m['version']=str(uuid.uuid4());m['label']='V3-test';m['files'][0].update(size=1024,sha256=hashlib.sha256(b'A'*1024).hexdigest())
        tid=str(uuid.uuid4());route='/tasks/'+tid
        c.request('POST','/projects/'+c.config['project']+'/tasks',dict(task=tid,manifest=m));count+=1
        changed=json.loads(json.dumps(m));changed['label']='changed'
        for body in [dict(task=tid,manifest=changed),dict(task=str(uuid.uuid4()),manifest=m)]:
            try:c.request('POST','/projects/'+c.config['project']+'/tasks',body)
            except ProtocolError:count+=1
            else:raise AssertionError('conflict not rejected')
        connection=http.client.HTTPConnection('127.0.0.1',c.config['port'],timeout=5)
        connection.putrequest('PUT',route+'/files/0');connection.putheader('Authorization','Bearer '+c.config['token']);connection.putheader('Content-Length','1024');connection.endheaders();connection.send(b'A'*20);connection.close()
        assert c.request('GET',route)['verified']==[];count+=1
        bad=E/'bad-upload.prt';bad.write_bytes(b'B'*1024)
        try:c.request('PUT',route+'/files/0',file=bad)
        except ProtocolError:count+=1
        else:raise AssertionError('bad hash accepted')
        assert c.cancel(tid)['state']=='cancelled';count+=1
        try:c.request('POST',route+'/commit',{})
        except ProtocolError:count+=1
        else:raise AssertionError('cancelled commit accepted')
        assert len(c.versions())==2 and all(v['healthy'] for v in c.versions());count+=1
        invalid=json.loads(json.dumps(m));invalid['store']=str(uuid.uuid4())
        try:c.request('POST','/projects/'+c.config['project']+'/tasks',dict(task=str(uuid.uuid4()),manifest=invalid))
        except ProtocolError:count+=1
        else:raise AssertionError('unbound origin accepted')
        (E/'service/storage/versions'/state2['task']/'0').write_bytes(b'FICTIONAL SERVER DAMAGE')
        assert not c.request('GET','/tasks/'+state2['task'])['healthy'];count+=1
        try:c.download(state2['task'],E/'damaged-download')
        except ProtocolError:count+=1
        else:raise AssertionError('damaged download accepted')
        assert c.request('GET','/tasks/'+state['task'])['healthy'];count+=1
        report=dict(passed=True,checks=count,seconds=time.monotonic()-t,service_separate_process=True,v1_v2_roundtrip=True,hard_exit_after_rename=True,lost_commit_response=True)
        (E/'report.json').write_bytes(data(report));print(data(report).decode());return report
    finally:
        if process and process.poll() is None:process.terminate();process.wait(10)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();E=a.output.absolute()
    if E.exists() or E.is_relative_to(ROOT):raise ValueError('new external evidence required')
    run(E)
