# SPDX-License-Identifier: GPL-3.0-only
"""Loopback account service and offline maintenance CLI. Never a production deployer."""
import argparse
import getpass
import hashlib
import importlib.abc
import json
import os
import re
import sqlite3
import signal
import sys
import time
import uuid
from http.server import HTTPServer
from pathlib import Path

from .publish_protocol import ProtocolError,data,safe,uid,MAX_JSON
from .publish_server import Handler,TestControl
from .accounts import AccountStore,PERMISSIONS,label
from .library_lock import file_lease
from contextlib import ExitStack


def re_task_create(path):
    return bool(re.fullmatch('/projects/[0-9a-f-]{36}/tasks',path))


def external(path):
    p=safe(path)
    if any((a/'.git').exists() for a in (p,*p.parents)):
        raise ProtocolError('external-private-path-required')
    return p


def read_password(path):
    if path is None:return getpass.getpass('Initial test password: ')
    path=external(path)
    if os.name!='nt':
        s=path.stat()
        if s.st_uid!=os.geteuid() or s.st_mode & 0o077:raise ProtocolError('private-password-file-required')
    b=path.read_bytes()
    if len(b)>1024:raise ProtocolError('password-policy')
    return b.decode('utf-8').rstrip('\r\n')


class AccountHandler(Handler):
    def reply(self,status,obj):
        if status==403 and not getattr(self,'audit_denied',False):
            self.audit_denied=True
            try:
                with self.server.store.db() as c:
                    self.server.store.audit(c,'request-denied',actor=self.server.store.actor)
            except Exception:
                status=503;obj={'error':'temporary-storage-failure'}
        super().reply(status,obj)

    def body(self):
        limit=MAX_JSON if re_task_create(self.path) else 8192
        n=self.length(limit);out=bytearray();deadline=time.monotonic()+5
        while len(out)<n:
            left=deadline-time.monotonic()
            if left<=0:raise ProtocolError('request-timeout')
            self.connection.settimeout(min(5,left))
            b=self.rfile.read1(min(8192,n-len(out)))
            if not b:raise ProtocolError('truncated')
            out.extend(b)
        return json.loads(out)

    def auth(self):
        values=self.headers.get_all('Authorization',[])
        if len(values)!=1 or not values[0].startswith('Bearer '):raise PermissionError()
        self.token=values[0][7:]
        row=self.server.store.authenticate(self.token)
        self.server.store.actor=row['id']
        if row['must_change']:raise PermissionError()
        return self.server.store.person(row)

    def permission(self,person,project,action):
        s=self.server.store
        super().permission(person,project,action)
        if action=='download':
            parts=self.path.strip('/').split('/')
            try:
                row=s.get(parts[1])
                if row['project']!=project:raise PermissionError()
                file=s.bound_manifest(row)['files'][int(parts[3])]
                if int(parts[3])<0:raise PermissionError()
            except (ProtocolError,ValueError,IndexError,KeyError):raise PermissionError() from None
            suffix=Path(file['path']).suffix.lower()
            needed='preview_view' if suffix=='.glb' and file['role']=='preview' else 'pdf_view' if suffix=='.pdf' and file['role']=='drawing' else 'source_download'
            if needed not in s.effective(person['account'],project):raise PermissionError()

    def do_PATCH(self):
        self.dispatch('PATCH')

    def session_user(self):
        values=self.headers.get_all('Authorization',[])
        if len(values)!=1 or not values[0].startswith('Bearer '):raise PermissionError()
        self.token=values[0][7:]
        person=self.server.store.authenticate(self.token)
        self.server.store.actor=person['id']
        return person

    def dispatch(self,method):
        s=self.server.store;s.actor=None;self.audit_denied=False
        try:
            if self.headers.get('Origin') or self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}' or '?' in self.path:
                raise ProtocolError('request-origin')
            parts=self.path.strip('/').split('/')
            if method=='GET' and parts==['health']:
                return self.reply(200,dict(status='ok',service=s.config['service'],protocol=1,accounts=True,**s.recovery_state()))
            if method=='POST' and parts==['auth','login']:
                v=self.body()
                if set(v)!={'username','password'}:raise ProtocolError('login-fields')
                try:result=s.login(v['username'],v['password'])
                except PermissionError:return self.reply(403,{'error':'authentication-failed'})
                return self.reply(200,dict(result,service=s.config['service']))
            if parts and parts[0]=='auth':
                person=self.session_user()
                if method=='GET' and parts==['auth','me']:
                    return self.reply(200,dict(user=s.public_user(person),service=s.config['service']))
                if method=='POST' and parts==['auth','logout']:
                    if self.body()!={}:raise ProtocolError('request-fields')
                    with s.db() as c:
                        c.execute('DELETE FROM sessions WHERE digest=?',(hashlib.sha256(self.token.encode()).hexdigest(),))
                        s.audit(c,'logout',person['id'],actor=person['id'])
                    return self.reply(200,{'logged_out':True})
                if method=='POST' and parts==['auth','password']:
                    if s.recovery_state()['maintenance']:raise PermissionError()
                    v=self.body()
                    if set(v)!={'old_password','new_password'}:raise ProtocolError('password-fields')
                    s.change_password(person,v['old_password'],v['new_password'])
                    return self.reply(200,{'changed':True,'login_required':True})
                return self.reply(404,{'error':'not-found'})
            if parts and parts[0]=='admin':
                person=self.session_user();s.require_admin(person)
                if method!='GET' and s.recovery_state()['maintenance']:raise PermissionError()
                if parts==['admin','users']:
                    if method=='GET':
                        with s.db() as c:users=[s.public_user(r) for r in c.execute('SELECT * FROM users ORDER BY created_at,id')]
                        return self.reply(200,{'users':users})
                    if method=='POST':
                        v=self.body()
                        if set(v)!={'username','role','initial_password'}:raise ProtocolError('user-fields')
                        return self.reply(200,s.create_user(person,v['username'],v['role'],v['initial_password']))
                if len(parts)==3 and parts[1]=='users' and method=='PATCH':
                    s.patch_user(person,parts[2],self.body());return self.reply(200,{'updated':True})
                if len(parts)==4 and parts[1]=='users' and parts[3]=='reset' and method=='POST':
                    v=self.body()
                    if set(v)!={'initial_password'}:raise ProtocolError('password-fields')
                    s.reset_password(person,parts[2],v['initial_password']);return self.reply(200,{'reset':True})
                if parts==['admin','grants'] and method=='POST':
                    v=self.body()
                    if set(v)!={'user','project','permissions'}:raise ProtocolError('permission-fields')
                    s.grant(person,v['user'],v['project'],v['permissions']);return self.reply(200,{'updated':True})
                if parts==['admin','projects'] and method=='POST':
                    if s.recovery_state()['maintenance']:raise PermissionError()
                    return self.reply(200,{'project':s.create_project(person,self.body())})
                if parts==['admin','audit'] and method=='GET':
                    with s.db() as c:rows=[dict(r) for r in c.execute('SELECT * FROM auth_audit ORDER BY id DESC LIMIT 200')]
                    return self.reply(200,{'audit':rows})
                return self.reply(404,{'error':'not-found'})
            if parts==['projects'] and method=='GET':
                who=self.auth();return self.reply(200,{'projects':s.projects(who['account'])})
            if len(parts)==2 and parts[0]=='projects' and method=='PATCH':
                who=self.auth();project=uid(parts[1])
                if s.recovery_state()['maintenance']:raise PermissionError()
                if 'project_manage' not in s.effective(who['account'],project):raise PermissionError()
                v=self.body()
                if not v or set(v)-{'name','description','status'}:raise ProtocolError('project-fields')
                for key,value in v.items():
                    if key=='status':
                        if value not in ('active','archived'):raise ProtocolError('project-status')
                    elif value or key=='name':label(value,120 if key=='name' else 1000)
                with s.db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    for key,value in v.items():c.execute('UPDATE remote_projects SET '+key+'=? WHERE id=?',(value,project))
                    s.audit(c,'project-updated',project,v,who['account']['id'])
                return self.reply(200,{'updated':True})
            return super().dispatch(method)
        except PermissionError:return self.reply(403,{'error':'forbidden'})
        except (ProtocolError,ValueError,KeyError,TypeError,sqlite3.IntegrityError):
            return self.reply(409,{'error':'invalid-or-conflicting-request'})
        except Exception:
            try:return self.reply(503,{'error':'temporary-service-failure'})
            except OSError:return


def main():
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest='action',required=True)
    stop=sub.add_parser('stop');stop.add_argument('--ready',type=Path,required=True)
    init=sub.add_parser('init');init.add_argument('--root',type=Path,required=True);init.add_argument('--config',type=Path,required=True)
    init.add_argument('--index',type=Path);init.add_argument('--storage',type=Path);init.add_argument('--lease-root',type=Path)
    for name in ('bootstrap','serve','backup-index'):
        cmd=sub.add_parser(name);cmd.add_argument('--config',type=Path,required=True)
        if name=='bootstrap':
            cmd.add_argument('--username',required=True);cmd.add_argument('--password-file',type=Path)
        elif name=='serve':
            cmd.add_argument('--ready',type=Path,required=True);cmd.add_argument('--port',type=int,default=0);cmd.add_argument('--deny-root',type=Path,action='append',default=[])
        else:cmd.add_argument('--destination',type=Path,required=True)
    a=p.parse_args()
    if os.name!='nt':
        if os.geteuid()==0:raise ProtocolError('nonprivileged-user-required')
        os.umask(0o077)
    if a.action=='stop':
        if os.name=='nt':raise ProtocolError('use-windows-stop-wrapper')
        ready=external(a.ready);info=json.loads(ready.read_bytes());pid=info['pid']
        if type(pid) is not int or pid<=1:raise ProtocolError('invalid-ready')
        proc=Path('/proc')/str(pid)
        if not proc.exists():return
        argv=(proc/'cmdline').read_bytes().split(b'\0')
        if os.stat(proc).st_uid!=os.geteuid() or b'mechanical_project_namer.account_server' not in argv or os.fsencode(ready) not in argv:
            raise ProtocolError('process-ownership-mismatch')
        os.kill(pid,signal.SIGTERM)
        deadline=time.monotonic()+5
        while proc.exists() and time.monotonic()<deadline:time.sleep(.05)
        if proc.exists():raise ProtocolError('stop-timeout')
        print('account-service-stopped');return
    config_path=external(a.config)
    if a.action=='init':
        root=external(a.root)
        if root.exists() or config_path.exists():raise ProtocolError('new-instance-required')
        root.mkdir(parents=True,mode=0o700)
        config_path.parent.mkdir(parents=True,exist_ok=True)
        config=dict(service=str(uuid.uuid4()),index=str(external(a.index or root/'index/index.sqlite')),
                    storage=str(external(a.storage or root/'storage')),projects={},session_seconds=1800,login_window=60,
                    lease_root=str(external(a.lease_root or root.parent/'.server-leases')))
        with config_path.open('xb') as out:out.write(data(config))
        return
    config=json.loads(config_path.read_bytes());index=external(config['index']);external(config['storage'])
    if os.name!='nt' and a.action=='serve':
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,512*1024**2))
        resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))
        def terminate(*args):raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM,terminate)
    index.parent.mkdir(parents=True,exist_ok=True)
    lease_root=external(config.get('lease_root',config_path.parent.parent/'.server-leases'))
    lease_root.mkdir(parents=True,exist_ok=True)
    with ExitStack() as locks:
        locks.enter_context(file_lease(lease_root/(uid(config['service'])+'.lock'),exclusive=True))
        locks.enter_context(file_lease(index.parent/'.publish-server.lock',exclusive=True))
        store=AccountStore(config)
        if a.action=='bootstrap':
            key=store.bootstrap(a.username,read_password(a.password_file));print(data({'admin_uuid':key}).decode());return
        if a.action=='backup-index':
            dest=external(a.destination)
            if dest.exists():raise ProtocolError('new-backup-required')
            with store.db() as source:
                target=sqlite3.connect(dest)
                try:source.backup(target)
                finally:target.close()
            print('index-backup-complete');return
        if a.port and not 1024<=a.port<=65535:raise ProtocolError('unprivileged-port-required')
        forbidden=[safe(root) for root in a.deny_root]
        def audit(event,args):
            if event in ('open','sqlite3.connect','os.listdir','os.scandir') and args and isinstance(args[0],(str,os.PathLike)):
                q=Path(os.path.abspath(args[0]))
                if any(q.is_relative_to(root) for root in forbidden):raise PermissionError('client-source-denied')
        sys.addaudithook(audit)
        class NoDesktop(importlib.abc.MetaPathFinder):
            def find_spec(self,name,path=None,target=None):
                if name.startswith(('PyQt6','keyring','win32com','OCP')):raise ImportError('desktop-dependency-forbidden')
        sys.meta_path.insert(0,NoDesktop())
        server=HTTPServer(('127.0.0.1',a.port),AccountHandler);server.store=store;server.control=TestControl();server.fault=''
        server.handle_error=lambda *args:None
        ready=external(a.ready)
        with ready.open('xb') as out:out.write(data(dict(port=server.server_port,service=config['service'],pid=os.getpid(),accounts=True,**store.recovery_state())))
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close()


if __name__=='__main__':
    try:main()
    except Exception as exc:
        code=str(exc) if isinstance(exc,ProtocolError) and re.fullmatch('[a-z0-9-]{1,80}',str(exc)) else 'account-service-operation-failed'
        print(code,file=sys.stderr);raise SystemExit(2)
