# SPDX-License-Identifier: GPL-3.0-only
"""Loopback-only experimental service. Standard HTTP parser; serial bounded requests."""
import argparse,hashlib,hmac,json,os,secrets,shutil,sqlite3,time,uuid,sys,importlib.abc
from http.server import HTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from .publish_protocol import *

class Connection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

class Store:
    def __init__(self,config):
        self.config=config;self.root=safe(config['storage']);self.index=safe(config['index'])
        if self.root==self.index or self.root in self.index.parents or self.index.parent==self.root:raise ProtocolError('storage-overlap')
        self.root.mkdir(parents=True,exist_ok=True);self.index.parent.mkdir(parents=True,exist_ok=True)
        marker=self.root/'.publish-owner.json'
        if marker.exists():
            if json.loads(marker.read_bytes())!={'service':config['service']}:raise ProtocolError('unknown-store')
        elif any(self.root.iterdir()):raise ProtocolError('unknown-store')
        else:marker.write_bytes(data({'service':config['service']}))
        for name in ('tasks','versions'):safe(self.root/name).mkdir(exist_ok=True)
        with self.db() as c:c.execute('CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,project TEXT,version TEXT,binding TEXT,manifest TEXT,state TEXT, UNIQUE(project,version))')
    def db(self):
        c=sqlite3.connect(self.index,factory=Connection);c.row_factory=sqlite3.Row;c.execute('PRAGMA synchronous=FULL');return c
    def get(self,t):
        with self.db() as c:r=c.execute('SELECT * FROM tasks WHERE id=?',(uid(t),)).fetchone()
        if not r:raise ProtocolError('not-found')
        return dict(r)
    def folder(self,r):return safe(self.root/('versions' if (self.root/'versions'/r['id']).exists() else 'tasks')/r['id'])
    def verified(self,r):
        m=json.loads(r['manifest']);folder=self.folder(r);out=[]
        for i,f in enumerate(m['files']):
            p=safe(folder/str(i))
            if p.is_file() and digest(p)=={k:f[k] for k in ('size','sha256')}:out.append(i)
        return out
    def status(self,r):
        complete=self.root/'versions'/r['id'];verified=self.verified(r)
        healthy=len(verified)==len(json.loads(r['manifest'])['files'])
        if complete.exists():
            healthy=healthy and {p.name for p in safe(complete).iterdir()}=={str(i) for i in verified}|{'manifest.json'}
            try:healthy=healthy and (safe(complete)/'manifest.json').read_bytes()==r['manifest'].encode()
            except OSError:healthy=False
            if healthy and r['state']!='complete':
                with self.db() as c:c.execute("UPDATE tasks SET state='complete' WHERE id=?",(r['id'],))
                r['state']='complete'
        return dict(task=r['id'],project=r['project'],version=r['version'],state=r['state'],healthy=healthy,verified=verified,manifest=json.loads(r['manifest']),dependencies='unverified',preview_correspondence='unverified')

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.0'
    def setup(self):super().setup();self.connection.settimeout(10)
    def log_message(self,*a):pass
    def reply(self,status,obj):
        b=data(obj);self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def body(self):
        n=self.length(MAX_JSON);b=self.rfile.read(n)
        if len(b)!=n:raise ProtocolError('truncated')
        return json.loads(b)
    def length(self,limit):
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))!=1:raise ProtocolError('body-framing')
        try:n=int(self.headers['Content-Length'])
        except Exception:raise ProtocolError('body-framing') from None
        if not 0<=n<=limit:raise ProtocolError('body-limit')
        return n
    def auth(self):
        token=self.headers.get('Authorization','').removeprefix('Bearer ')
        for name,person in self.server.store.config['identities'].items():
            if hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),person['token_hash']):return person
        raise PermissionError('authentication-required')
    def permission(self,person,project,action):
        if action not in person['grants'].get(project,[]):raise PermissionError('forbidden')
    def do_GET(self):self.dispatch('GET')
    def do_POST(self):self.dispatch('POST')
    def do_PUT(self):self.dispatch('PUT')
    def dispatch(self,method):
        try:
            if self.headers.get('Origin') or self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}' or '?' in self.path:raise ProtocolError('request-origin')
            who=self.auth();s=self.server.store;parts=self.path.strip('/').split('/')
            if method=='GET' and parts==['info']:
                return self.reply(200,dict(service=s.config['service'],protocol=1,max_file=MAX_FILE,max_total=MAX_TOTAL,max_files=MAX_FILES))
            if len(parts)>=2 and parts[0]=='projects':
                project=uid(parts[1]);self.permission(who,project,'read')
                if method=='GET' and len(parts)==2:
                    with s.db() as c:rows=[dict(r) for r in c.execute("SELECT * FROM tasks WHERE project=? AND state='complete'",(project,))]
                    return self.reply(200,{'versions':[s.status(r) for r in rows]})
                if method=='POST' and len(parts)==3 and parts[2]=='tasks':
                    self.permission(who,project,'publish');v=self.body()
                    if set(v)!={'task','manifest'}:raise ProtocolError('request-whitelist')
                    task=uid(v['task']);m=manifest(v['manifest'])
                    if s.config['projects'].get(project)!={'store':m['store'],'local_project':m['local_project']}:raise PermissionError('mapping-not-authorized')
                    with s.db() as c:
                        old=c.execute('SELECT * FROM tasks WHERE id=?',(task,)).fetchone()
                        if old:
                            if old['project']!=project or old['binding']!=sha(m):raise ProtocolError('task-conflict')
                        else:
                            if c.execute('SELECT count(*) FROM tasks').fetchone()[0]>=256:raise ProtocolError('index-limit')
                            if c.execute("SELECT count(*) FROM tasks WHERE state NOT IN ('complete','cancelled')").fetchone()[0]>=32:raise ProtocolError('task-limit')
                            if c.execute("SELECT 1 FROM tasks WHERE project=? AND state NOT IN ('complete','cancelled')",(project,)).fetchone():raise ProtocolError('project-busy')
                            c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',(task,project,m['version'],sha(m),data(m).decode(),'receiving'))
                    return self.reply(200,s.status(s.get(task)))
            if len(parts)>=2 and parts[0]=='tasks':
                r=s.get(parts[1]);self.permission(who,r['project'],'read');state=s.status(r)
                if method=='GET' and len(parts)==2:return self.reply(200,state)
                if len(parts)==4 and parts[2]=='files':
                    i=int(parts[3]);m=json.loads(r['manifest'])
                    if not 0<=i<len(m['files']):raise ProtocolError('file-id')
                    f=m['files'][i]
                    if method=='GET':
                        self.permission(who,r['project'],'download')
                        if r['state']!='complete' or not state['healthy']:raise ProtocolError('version-unavailable')
                        p=safe(s.folder(r)/str(i));self.send_response(200);self.send_header('Content-Length',str(f['size']));self.end_headers()
                        with p.open('rb') as stream:
                            while b:=stream.read(65536):self.wfile.write(b)
                        return
                    if method=='PUT':
                        self.permission(who,r['project'],'publish')
                        if r['state']!='receiving':raise ProtocolError('task-terminal')
                        n=self.length(MAX_FILE)
                        if n!=f['size']:raise ProtocolError('size-mismatch')
                        used=sum(p.stat().st_size for p in s.root.rglob('*') if p.is_file())
                        if used+n>256*1024**2 or shutil.disk_usage(s.root).free<n+32*1024**2:raise ProtocolError('storage-limit')
                        folder=safe(s.root/'tasks'/r['id']);folder.mkdir(exist_ok=True)
                        tmp=folder/(str(i)+'.partial-'+uuid.uuid4().hex);h=hashlib.sha256();remaining=n;deadline=time.monotonic()+30
                        with tmp.open('xb') as stream:
                            while remaining:
                                if time.monotonic()>deadline:raise ProtocolError('transfer-timeout')
                                b=self.rfile.read(min(65536,remaining))
                                if not b:raise ProtocolError('truncated')
                                remaining-=len(b);h.update(b);stream.write(b)
                            stream.flush();os.fsync(stream.fileno())
                        if h.hexdigest()!=f['sha256']:raise ProtocolError('hash-mismatch')
                        target=safe(folder/str(i))
                        if target.exists():
                            if digest(target)!={k:f[k] for k in ('size','sha256')}:raise ProtocolError('existing-file-damaged')
                            # Preserve duplicate attempt as evidence, never replace verified data.
                        else:os.rename(tmp,target)
                        return self.reply(200,{'verified':i})
                if method=='POST' and len(parts)==3:
                    self.permission(who,r['project'],'publish')
                    if self.body()!={}:raise ProtocolError('request-whitelist')
                    if parts[2]=='cancel':
                        if r['state']!='complete':
                            with s.db() as c:c.execute("UPDATE tasks SET state='cancelled' WHERE id=?",(r['id'],))
                        return self.reply(200,s.status(s.get(r['id'])))
                    if parts[2]=='commit':
                        if r['state']=='complete':return self.reply(200,state)
                        if r['state']!='receiving' or not state['healthy']:raise ProtocolError('incomplete')
                        folder=s.folder(r);expected={str(i) for i in state['verified']}
                        # Partial attempts retained separately, never included in a committed version.
                        final=safe(s.root/'tasks'/(r['id']+'-sealed'))
                        if final.exists():
                            if {p.name for p in final.iterdir()}!=expected|{'manifest.json'} or (final/'manifest.json').read_bytes()!=r['manifest'].encode():raise ProtocolError('sealed-recovery-required')
                            if any(digest(final/str(i))!={k:f[k] for k in ('size','sha256')} for i,f in enumerate(json.loads(r['manifest'])['files'])):raise ProtocolError('sealed-damaged')
                        else:
                            size=sum(f['size'] for f in json.loads(r['manifest'])['files'])
                            used=sum(p.stat().st_size for p in s.root.rglob('*') if p.is_file())
                            if used+size+MAX_JSON>256*1024**2 or shutil.disk_usage(s.root).free<size+32*1024**2:raise ProtocolError('storage-limit')
                            final.mkdir()
                            for i in state['verified']:
                                with safe(folder/str(i)).open('rb') as src,(final/str(i)).open('xb') as dst:shutil.copyfileobj(src,dst,65536);dst.flush();os.fsync(dst.fileno())
                            with (final/'manifest.json').open('xb') as out:out.write(r['manifest'].encode());out.flush();os.fsync(out.fileno())
                        if any(digest(final/str(i))!={k:f[k] for k in ('size','sha256')} for i,f in enumerate(json.loads(r['manifest'])['files'])):raise ProtocolError('sealed-hash')
                        if self.server.fault=='before-rename':os._exit(73)
                        os.rename(final,safe(s.root/'versions'/r['id']))
                        if self.server.fault=='after-rename':os._exit(73)
                        with s.db() as c:c.execute("UPDATE tasks SET state='complete' WHERE id=?",(r['id'],))
                        if self.server.fault=='lost-response':self.connection.close();return
                        return self.reply(200,s.status(s.get(r['id'])))
            self.reply(404,{'error':'not-found'})
        except PermissionError:self.reply(403,{'error':'forbidden'})
        except (ProtocolError,ValueError,KeyError,sqlite3.IntegrityError):self.reply(409,{'error':'invalid-or-conflicting-request'})
        except (OSError,sqlite3.Error):
            try:self.reply(503,{'error':'temporary-storage-or-transport-failure'})
            except OSError:pass
        except Exception:
            try:self.reply(400,{'error':'invalid-request'})
            except OSError:pass

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--port',type=int,default=0);p.add_argument('--ready',type=Path,required=True);p.add_argument('--fault',choices=['','before-rename','after-rename','lost-response'],default='');p.add_argument('--deny-root',type=Path,action='append',default=[]);a=p.parse_args()
    forbidden=[Path(os.path.abspath(p)) for p in a.deny_root]
    def audit(event,args):
        if event in ('open','sqlite3.connect','os.scandir','os.listdir') and args and isinstance(args[0],(str,os.PathLike)):
            value=os.fspath(args[0])
            if value.startswith('file:'):
                from urllib.parse import unquote
                value=unquote(value[5:].split('?')[0]).lstrip('/')
            q=Path(os.path.abspath(value))
            if any(q.is_relative_to(p) for p in forbidden):raise PermissionError('client-source-access-denied')
    sys.addaudithook(audit)
    class NoDesktop(importlib.abc.MetaPathFinder):
        def find_spec(self,name,path=None,target=None):
            if name.startswith(('PyQt6','keyring','win32com','OCP')):raise ImportError('desktop-dependency-forbidden')
    sys.meta_path.insert(0,NoDesktop())
    config=json.loads(a.config.read_bytes());store=Store(config)
    from .library_lock import file_lease
    lease=file_lease(store.index.parent/'.publish-server.lock',exclusive=True);lease.__enter__()
    server=HTTPServer(('127.0.0.1',a.port),Handler);server.request_queue_size=4;server.store=store;server.fault=a.fault
    server.handle_error=lambda *args:None
    ready=safe(a.ready)
    if any((p/'.git').exists() for p in ready.parents):raise ProtocolError('ready-must-be-external')
    with ready.open('xb') as out:out.write(data({'port':server.server_port,'service':config['service'],'pid':os.getpid()}))
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();lease.__exit__(None,None,None)
if __name__=='__main__':
    try:main()
    except Exception:
        print('test-service-start-or-storage-failed',file=sys.stderr);raise SystemExit(2)
