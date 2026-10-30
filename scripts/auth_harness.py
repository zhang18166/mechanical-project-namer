# SPDX-License-Identifier: GPL-3.0-only
"""Real Windows/WSL account-service harness. Private data only, never a deployer."""
import ctypes
import http.client
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
import uuid
from functools import lru_cache
from scripts.publish_harness import Fixture,write
from mechanical_project_namer.account_client import AccountSession
from mechanical_project_namer.publish_protocol import data,digest,ProtocolError

ROOT=Path(__file__).resolve().parents[1]

@lru_cache(maxsize=1)
def linux_root():
    if os.environ.get('MW_ACCOUNT_TEST_ROOT'):return os.environ['MW_ACCOUNT_TEST_ROOT']
    result=subprocess.run(['wsl','-d',os.environ.get('MW_ACCOUNT_TEST_DISTRO','Ubuntu'),'--exec','python3','-c',
                           'from pathlib import Path;print(Path.home()/"mechanical-workbench-phase4a")'],
                          capture_output=True,check=True,timeout=15)
    return result.stdout.decode().strip()


def linux_path(p):
    p=Path(p).absolute()
    if p.drive.upper()!='D:':raise ValueError('Test bridge restricted to the known workspace drive')
    return '/mnt/d/'+p.as_posix()[3:]


def wait_ready(process,path,seconds=15):
    """File creation is not a publication barrier; wait for complete JSON."""
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if process.poll() is not None:raise RuntimeError('account-service-start-failed')
        try:value=json.loads(Path(path).read_bytes())
        except (FileNotFoundError,json.JSONDecodeError):
            time.sleep(.02);continue
        if not isinstance(value,dict) or type(value.get('pid')) is not int or type(value.get('port')) is not int or value.get('accounts') is not True:
            raise RuntimeError('invalid-service-ready')
        return value
    raise RuntimeError('account-service-start-timeout')


class ManagedService:
    def __init__(self,root,platform='windows',deny=()):
        self.root=Path(root).absolute();self.root.mkdir(parents=True)
        self.platform=platform;self.process=None;self.deny=list(deny)
        self.data_root=(linux_root()+'/case-'+uuid.uuid4().hex) if platform=='linux' else str(self.root/'service')
        self.config=self.data_root+'/server.json'
        self.initial=secrets.token_urlsafe(24)
        self.current=secrets.token_urlsafe(24)
        self.command('init','--root',self.data_root,'--config',self.config)
        secret_path=self.data_root+'/bootstrap-password'
        self.native_write(secret_path,self.initial.encode(),exclusive=True)
        result=self.command('bootstrap','--config',self.config,'--username','fake-admin','--password-file',secret_path)
        self.admin_id=json.loads(result)['admin_uuid']

    def base(self):
        if self.platform=='linux':
            args=['wsl','-d',os.environ.get('MW_ACCOUNT_TEST_DISTRO','Ubuntu')]
            if os.environ.get('MW_ACCOUNT_TEST_USER'):args+=['-u',os.environ['MW_ACCOUNT_TEST_USER']]
            return args+['--exec','env','PYTHONPATH='+linux_path(ROOT),os.environ.get('MW_ACCOUNT_TEST_PYTHON',linux_root()+'/venv/bin/python')]
        return [sys.executable]

    def command(self,*args,check=True):
        r=subprocess.run(self.base()+['-m','mechanical_project_namer.account_server',*args],cwd=ROOT,capture_output=True,timeout=30)
        if check and r.returncode:raise RuntimeError('maintenance-cli-failed')
        return r.stdout.decode('utf-8').strip() if check else r.returncode

    def native(self,code,*args,input=None,timeout=30):
        if not 0<timeout<=30:raise ValueError('native-operation-budget')
        r=subprocess.run(self.base()+['-c',code,*args],cwd=ROOT,input=input,capture_output=True,timeout=timeout)
        if r.returncode:raise RuntimeError('native-test-operation-failed')
        return r.stdout.decode('utf-8').strip()

    def native_write(self,path,content,exclusive=False):
        code="import sys,os; p=sys.argv[1]; f=os.open(p,os.O_WRONLY|os.O_CREAT|"+('os.O_EXCL' if exclusive else 'os.O_TRUNC')+",0o600); os.write(f,sys.stdin.buffer.read());os.close(f)"
        self.native(code,path,input=content)

    def start(self):
        self.ready=self.root/('ready-'+uuid.uuid4().hex+'.json')
        ready_arg=linux_path(self.ready) if self.platform=='linux' else str(self.ready)
        args=self.base()+['-m','mechanical_project_namer.account_server','serve','--config',self.config,'--ready',ready_arg]
        for p in self.deny:args+=['--deny-root',linux_path(p) if self.platform=='linux' else str(p)]
        self.process=subprocess.Popen(args,cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        v=wait_ready(self.process,self.ready);self.pid=v['pid'];self.connection=dict(port=v['port'],service=v['service'])
        write(self.root/'connection.json',self.connection)
        # WSL localhost forwarding may settle briefly; never use a LAN address.
        end=time.monotonic()+10
        while True:
            try:AccountSession(self.connection).verify_service();break
            except OSError:
                if time.monotonic()>end:raise
                time.sleep(.05)
        return AccountSession(self.connection)

    def stop(self):
        if not self.process:return
        if self.platform=='linux':
            code="import os,sys,signal; p=int(sys.argv[1]); b=open('/proc/'+str(p)+'/cmdline','rb').read(); assert b'mechanical_project_namer.account_server' in b and sys.argv[2].encode() in b; assert os.stat('/proc/'+str(p)).st_uid==os.geteuid();os.kill(p,signal.SIGTERM)"
            if self.process.poll() is None:self.native(code,str(self.pid),linux_path(self.ready))
        elif self.process.poll() is None:
            # The ready PID is the Python worker, not the venv launcher.
            from ctypes import wintypes as W
            k=ctypes.WinDLL('kernel32',use_last_error=True)
            k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenProcess.restype=W.HANDLE
            k.TerminateProcess.argtypes=[W.HANDLE,W.UINT];k.CloseHandle.argtypes=[W.HANDLE]
            h=k.OpenProcess(1,False,self.pid)
            if h:
                try:k.TerminateProcess(h,0)
                finally:k.CloseHandle(h)
        if self.process.poll() is None:self.process.wait(15)

    def admin(self):
        s=AccountSession(self.connection);s.login('fake-admin',self.current);return s

    def info(self):
        code="import os,sys,platform,sqlite3,json; v=dict(platform=platform.platform(),python=sys.version,sqlite=sqlite3.sqlite_version,pid=int(sys.argv[1]));"+("import resource; v.update(uid=os.geteuid(),limits=open('/proc/'+sys.argv[1]+'/limits').read(),status=open('/proc/'+sys.argv[1]+'/status').read());" if self.platform=='linux' else "")+" print(json.dumps(v))"
        return json.loads(self.native(code,str(self.pid)))

    def change_config(self,**values):
        code="import sys,json; p=sys.argv[1];v=json.load(open(p));v.update(json.loads(sys.argv[2]));open(p,'w').write(json.dumps(v))"
        self.native(code,self.config,json.dumps(values))

    def sql(self,statement):
        code="import sys,json,sqlite3;cfg=json.load(open(sys.argv[1])); c=sqlite3.connect(cfg['index']);c.execute(sys.argv[2]);c.commit();c.close()"
        self.native(code,self.config,statement)


class AuthFixture:
    def __init__(self,root,platform='windows',large=False,*,service_class=None):
        self.root=Path(root).absolute()
        if self.root.exists() or self.root.is_relative_to(ROOT):raise ValueError('New external fixture required')
        self.root.mkdir(parents=True)
        self.a=Fixture(self.root/'A',large=large);self.b=Fixture(self.root/'B',large=False)
        # Preview-role bytes are synthetic and never rendered.
        from mechanical_project_namer.project_files import ProjectFileCatalog
        f=self.a.sources[0].parent/'虚构预览.glb';f.write_bytes(b'FICTIONAL PREVIEW TRANSPORT ONLY')
        catalog=ProjectFileCatalog(self.a.repo);catalog.register_attachments(self.a.project.id,[str(f)])
        self.a.sources.append(f);work=self.a.sources[0].parent
        choices=[dict(file_id=r['file_id']) for r in catalog.list_files(self.a.project.id)]
        self.task=self.a.snap.prepare(self.a.project.id,work,choices,'AuthV1');self.a.snap.run(self.task);self.a.before=self.a.protected()
        self.service=(service_class or ManagedService)(self.root/'backend',platform,deny=[self.a.root/'client',self.b.root/'client'])
        self.service.start();self.admin=AccountSession(self.service.connection)
        initial=self.admin.login('fake-admin',self.service.initial)
        assert initial['user']['must_change']
        rejected(lambda:self.admin.request('GET','/admin/users'))
        self.admin.change_password(self.service.initial,self.service.current)
        self.admin.login('fake-admin',self.service.current)
        self.projects=[]
        for model in (self.a,self.b):
            p=self.admin.request('POST','/admin/projects',dict(name='同名虚构项目',description='只用于隔离验收',store=model.snap.store_id,local_project=model.project.id))['project']
            self.projects.append(p)
        self.pa,self.pb=self.projects
        self.grant(self.admin.user['id'],self.pa,['metadata_view','preview_view','pdf_view','source_download','version_publish','project_manage'])
        self.users={}
        for name,role in [('fake-writer','designer'),('fake-reader','reader'),('fake-download','reader'),('fake-outsider','designer')]:
            old=secrets.token_urlsafe(24);new=secrets.token_urlsafe(24)
            user=self.admin.request('POST','/admin/users',dict(username=name,role=role,initial_password=old))
            session=AccountSession(self.service.connection);session.login(name,old);session.change_password(old,new);session.login(name,new)
            self.users[name]=dict(id=user['id'],password=new,session=session)
        self.grant(self.users['fake-writer']['id'],self.pa,['metadata_view','version_publish','source_download','preview_view','pdf_view'])
        self.grant(self.users['fake-reader']['id'],self.pa,['metadata_view','preview_view','pdf_view'])
        self.grant(self.users['fake-download']['id'],self.pa,['metadata_view','source_download'])
        self.grant(self.users['fake-outsider']['id'],self.pb,['metadata_view','version_publish','source_download'])

    def grant(self,user,project,permissions):
        return self.admin.request('POST','/admin/grants',dict(user=user,project=project,permissions=permissions))

    def close(self):self.service.stop();self.a.close();self.b.close()

    def protect(self):self.a.assert_protected();self.b.assert_protected()


def rejected(operation,code='403'):
    try:operation()
    except ProtocolError as exc:
        if code not in str(exc):raise
        return
    raise AssertionError('operation unexpectedly authorized')
