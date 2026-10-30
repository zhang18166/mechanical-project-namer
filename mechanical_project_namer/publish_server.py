# SPDX-License-Identifier: GPL-3.0-only
"""Bounded loopback experiment; stdlib HTTP parser, persistent task state."""
import argparse
import hashlib
import hmac
import importlib.abc
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .publish_protocol import *
from .safe_fs import remove_file

# safe_fs's desktop warning includes the local pathname. The service reports a
# constant code instead, so cleanup failures cannot disclose private paths.
_PRIVATE_CLEANUP = logging.Logger(__name__+'.private-cleanup')
_PRIVATE_CLEANUP.propagate = False
_PRIVATE_CLEANUP.addHandler(logging.NullHandler())


def discard_owned_partial(path):
    """Call only after an ownership receipt and serial task lease are checked."""
    if not remove_file(path, logger=_PRIVATE_CLEANUP):
        raise ProtocolError('partial-cleanup-refused')


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    def __init__(self, config):
        self.config = config
        self.root, self.index = safe(config['storage']), safe(config['index'])
        if self.root == self.index or self.root in self.index.parents:
            raise ProtocolError('storage-overlap')
        self.root.mkdir(parents=True, exist_ok=True)
        self.index.parent.mkdir(parents=True, exist_ok=True)
        marker = safe(self.root / '.publish-owner.json')
        owner = {'service': config['service']}
        if marker.exists():
            if json.loads(marker.read_bytes()) != owner:
                raise ProtocolError('unknown-store')
        elif any(self.root.iterdir()):
            raise ProtocolError('unknown-store')
        else:
            marker.write_bytes(data(owner))
        for name in ('tasks', 'versions'):
            safe(self.root / name).mkdir(exist_ok=True)
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,project TEXT,version TEXT,binding TEXT,manifest TEXT,state TEXT)')
            sql = c.execute("SELECT sql FROM sqlite_master WHERE name='tasks'").fetchone()[0]
            # Service-only index migration: cancelled attempts stay immutable history;
            # a new explicit attempt may publish the same local version.
            if 'UNIQUE(project,version)' in sql.replace(' ', ''):
                c.execute('ALTER TABLE tasks RENAME TO legacy_tasks')
                c.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY,project TEXT,version TEXT,binding TEXT,manifest TEXT,state TEXT)')
                c.execute('INSERT INTO tasks SELECT * FROM legacy_tasks')
                c.execute('DROP TABLE legacy_tasks')
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS active_version ON tasks(project,version) WHERE state!='cancelled'")

    def db(self):
        c = sqlite3.connect(self.index, factory=Connection)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA synchronous=FULL')
        return c

    def task_created(self, connection, task, project):
        """Extension hook inside the task transaction; legacy protocol is unchanged."""

    def task_completed(self, connection, task):
        """Extension hook inside registration, including filesystem recovery."""

    def get(self, task):
        with self.db() as c:
            row = c.execute('SELECT * FROM tasks WHERE id=?', (uid(task),)).fetchone()
        if not row:
            raise ProtocolError('not-found')
        return dict(row)

    def bound_manifest(self, row):
        m = manifest(json.loads(row['manifest']))
        uid(row['id'])
        uid(row['project'])
        if row['binding'] != sha(m) or row['version'] != m['version']:
            raise ProtocolError('binding-damaged')
        if 'projects' in self.config and self.config['projects'].get(row['project']) != {'store': m['store'], 'local_project': m['local_project']}:
            raise ProtocolError('mapping-damaged')
        return m

    def folder(self, row):
        # A complete row must NEVER fall back to receiving data.
        final = safe(self.root / 'versions' / row['id'])
        return final if row['state'] == 'complete' or final.exists() else safe(self.root / 'tasks' / row['id'])

    def verified(self, row, folder=None):
        m = self.bound_manifest(row)
        folder = safe(folder or self.folder(row))
        out = []
        for i, f in enumerate(m['files']):
            try:
                p = safe(folder / str(i))
                if p.is_file() and digest(p) == {k: f[k] for k in ('size', 'sha256')}:
                    out.append(i)
            except (OSError, ProtocolError):
                pass
        return out

    def formal_health(self, row):
        try:
            m = self.bound_manifest(row)
            folder = safe(self.root / 'versions' / row['id'])
            expected = {str(i) for i in range(len(m['files']))} | {'manifest.json'}
            return (folder.is_dir() and {p.name for p in folder.iterdir()} == expected
                    and safe(folder/'manifest.json').read_bytes() == data(m)
                    and len(self.verified(row, folder)) == len(m['files']))
        except (OSError, ValueError, KeyError):
            return False

    def status(self, row):
        m = self.bound_manifest(row)
        final = safe(self.root/'versions'/row['id'])
        healthy = self.formal_health(row)
        # Recover only a bound, fully verified filesystem commit, never a
        # cancelled task, unknown directory, or mismatched metadata.
        if row['state'] == 'receiving' and final.exists() and healthy:
            with self.db() as c:
                c.execute("UPDATE tasks SET state='complete' WHERE id=? AND state='receiving'", (row['id'],))
                self.task_completed(c, row['id'])
            row['state'] = 'complete'
        verified = self.verified(row) if row['state'] == 'receiving' and not final.exists() else (list(range(len(m['files']))) if healthy else [])
        return dict(task=row['id'], project=row['project'], version=row['version'],
                    state=row['state'], healthy=healthy, verified=verified, manifest=m,
                    dependencies='unverified', preview_correspondence='unverified')

    def room(self, size):
        used = sum(safe(p).stat().st_size for p in self.root.rglob('*') if p.is_file())
        if used+size > 256*1024**2 or shutil.disk_usage(self.root).free < size+32*1024**2:
            raise ProtocolError('storage-limit')

    def owner(self, row, kind):
        return dict(service=self.config['service'], task=row['id'], project=row['project'],
                    version=row['version'], binding=row['binding'], kind=kind)

    def owned(self, row, kind, folder):
        receipt = safe(self.root/'tasks'/(row['id']+'.'+kind+'-owner.json'))
        owner = self.owner(row, kind)
        if receipt.exists():
            if json.loads(receipt.read_bytes()) != owner:
                raise ProtocolError('unknown-task-products')
        elif folder.exists():
            # Prior receiving directories can be imported only after complete
            # binding/hash checks, never unknown partial/sealed directories.
            if kind != 'receiving' or any(p.name not in {str(i) for i in self.verified(row, folder)} for p in folder.iterdir()):
                raise ProtocolError('unknown-task-products')
            with receipt.open('xb') as out:
                out.write(data(owner))
        else:
            with receipt.open('xb') as out:
                out.write(data(owner))
        folder.mkdir(exist_ok=True)
        return folder

    def seal(self, row, checkpoint):
        source = safe(self.root/'tasks'/row['id'])
        target = safe(self.root/'tasks'/(row['id']+'-sealed'))
        self.owned(row, 'sealed', target)
        m = self.bound_manifest(row)
        permitted = {'manifest.json', 'manifest.partial'} | {str(i) for i in range(len(m['files']))} | {str(i)+'.copy' for i in range(len(m['files']))}
        if any(p.name not in permitted for p in target.iterdir()):
            raise ProtocolError('unknown-sealed-products')
        for partial in [target/'manifest.partial', *(target/(str(i)+'.copy') for i in range(len(m['files'])))]:
            partial = safe(partial)
            if partial.exists():
                discard_owned_partial(partial)
        self.room(sum(f['size'] for i,f in enumerate(m['files']) if not (target/str(i)).exists())+len(data(m)))
        for i, f in enumerate(m['files']):
            dest, partial = safe(target/str(i)), safe(target/(str(i)+'.copy'))
            expected = {k: f[k] for k in ('size', 'sha256')}
            if dest.exists():
                if digest(dest) != expected:
                    raise ProtocolError('sealed-damaged')
                continue
            if partial.exists():
                discard_owned_partial(partial)  # bound receipt + serial service
            with safe(source/str(i)).open('rb') as src, partial.open('xb') as dst:
                while chunk := src.read(65536):
                    dst.write(chunk)
                    checkpoint('seal-copy')
                dst.flush()
                os.fsync(dst.fileno())
            if digest(partial) != expected:
                raise ProtocolError('sealed-hash')
            os.rename(partial, dest)
        checkpoint('before-manifest')
        mp, mf = safe(target/'manifest.partial'), safe(target/'manifest.json')
        if mf.exists():
            if mf.read_bytes() != data(m):
                raise ProtocolError('sealed-manifest-damaged')
        else:
            if mp.exists():
                discard_owned_partial(mp)
            with mp.open('xb') as out:
                out.write(data(m)); out.flush(); os.fsync(out.fileno())
            os.rename(mp, mf)
        checkpoint('after-manifest')
        return target


class Disconnect(Exception):
    pass


class TestControl:
    """Only explicitly supplied external test controls; absent in normal service."""
    def __init__(self, path=None):
        self.path = safe(path) if path else None
        self.fired = None

    def config(self):
        if not self.path:
            return {}
        v = json.loads(self.path.read_bytes())
        if v.get('isolated_test') is not True:
            raise ProtocolError('test-control-required')
        return v

    def event(self, event, **values):
        cfg = self.config()
        if not cfg.get('events'):
            return
        p = safe(cfg['events'])
        if p.exists() and p.stat().st_size > 2*1024**2:
            return
        with p.open('ab') as out:
            out.write(data(dict(event=event, monotonic=time.monotonic(), **values))+b'\n')

    def checkpoint(self, point):
        cfg = self.config()
        if cfg.get('point') == point and cfg.get('nonce') != self.fired:
            self.fired = cfg.get('nonce')
            self.event('fault', point=point, action=cfg.get('action'))
            if cfg.get('action') == 'exit':
                os._exit(73)
            if cfg.get('action') == 'fail':
                raise OSError('injected-storage-failure')
            if cfg.get('action') == 'disconnect':
                raise Disconnect()


class PublishService:
    """Shared synchronous business operations; transports supply streams/reply hooks.

    This class neither parses HTTP nor opens/listens on a network socket. Both
    the historical HTTP adapter and ASGI adapter call these same operations.
    """
    protocol_version = 'HTTP/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass

    def reply(self, status, obj):
        b = data(obj)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def length(self, limit):
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
            raise ProtocolError('body-framing')
        try:
            n = int(self.headers['Content-Length'])
        except Exception:
            raise ProtocolError('body-framing') from None
        if not 0 <= n <= limit:
            raise ProtocolError('body-limit')
        return n

    def body(self):
        n = self.length(MAX_JSON)
        b = self.rfile.read(n)
        if len(b) != n:
            raise ProtocolError('truncated')
        return json.loads(b)

    def auth(self):
        values = self.headers.get_all('Authorization', [])
        if len(values) != 1 or not values[0].startswith('Bearer '):
            raise PermissionError()
        token = values[0][7:]
        for person in self.server.store.config['identities'].values():
            if hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), person['token_hash']):
                return person
        raise PermissionError()

    def permission(self, person, project, action):
        if action not in person['grants'].get(project, []):
            raise PermissionError()

    def do_GET(self):
        self.dispatch('GET')

    def do_POST(self):
        self.dispatch('POST')

    def do_PUT(self):
        self.dispatch('PUT')

    def upload(self, row, i, f):
        s, control = self.server.store, self.server.control
        n = self.length(MAX_FILE)
        if n != f['size']:
            raise ProtocolError('size-mismatch')
        folder = s.owned(row, 'receiving', safe(s.root/'tasks'/row['id']))
        target, tmp = safe(folder/str(i)), safe(folder/(str(i)+'.partial'))
        if tmp.exists():
            discard_owned_partial(tmp)  # bounded partial; verified ownership above
        control.checkpoint('receive-space')
        s.room(n)
        h, received = hashlib.sha256(), 0
        cfg = control.config()
        idle = min(10, max(.1, cfg.get('idle_timeout', 10)))
        deadline = time.monotonic()+min(30, max(.1, cfg.get('total_timeout', 30)))
        control.event('upload-start', task=row['id'], file=i, size=n)
        try:
            control.checkpoint('receive-write')
            with tmp.open('xb') as stream:
                while received < n:
                    left = deadline-time.monotonic()
                    if left <= 0:
                        raise ProtocolError('transfer-timeout')
                    self.connection.settimeout(min(idle, left))
                    # read1 returns available bytes; a slow trickle cannot keep
                    # BufferedReader.read(size) alive beyond the total deadline.
                    b = self.rfile.read1(min(65536, n-received))
                    if not b:
                        raise ProtocolError('truncated')
                    received += len(b)
                    h.update(b)
                    stream.write(b)
                    cfg = control.config()
                    if cfg.get('trace_chunks'):
                        control.event('upload-bytes', task=row['id'], file=i, received=received, size=n)
                    if cfg.get('file') == i and received >= cfg.get('after_bytes', MAX_FILE+1) and cfg.get('nonce') != control.fired:
                        control.fired = cfg.get('nonce')
                        if cfg.get('action') == 'disconnect-upload':
                            raise Disconnect()
                        if cfg.get('action') == 'exit-upload':
                            control.event('upload-end', task=row['id'], file=i, received=received, size=n, verified=False)
                            os._exit(73)
                    if cfg.get('delay_per_chunk'):
                        time.sleep(min(.1, max(0, cfg['delay_per_chunk'])))
                stream.flush(); os.fsync(stream.fileno())
            if h.hexdigest() != f['sha256']:
                raise ProtocolError('hash-mismatch')
            if target.exists():
                if digest(target) != {k: f[k] for k in ('size', 'sha256')}:
                    raise ProtocolError('existing-file-damaged')
                discard_owned_partial(tmp)
            else:
                os.rename(tmp, target)
            control.event('file-verified', task=row['id'], file=i, received=received)
            if cfg.get('action') == 'drop-file-response' and cfg.get('file') == i and cfg.get('nonce') != control.fired:
                control.fired = cfg.get('nonce')
                raise Disconnect()
            return self.reply(200, {'verified': i})
        finally:
            control.event('upload-end', task=row['id'], file=i, received=received, size=n)

    def dispatch(self, method):
        try:
            if self.headers.get('Origin') or self.headers.get('Host') != getattr(self.server, 'expected_host', f'127.0.0.1:{self.server.server_port}') or '?' in self.path:
                raise ProtocolError('request-origin')
            who = self.auth()
            s, control = self.server.store, self.server.control
            parts = self.path.strip('/').split('/')
            if method == 'GET' and parts == ['info']:
                return self.reply(200, dict(service=s.config['service'], protocol=1, max_file=MAX_FILE, max_total=MAX_TOTAL, max_files=MAX_FILES))
            if method == 'GET' and parts == ['projects']:
                return self.reply(200, {'projects': [dict(project=key,permissions=who['grants'][key])
                    for key in s.config['projects'] if 'read' in who['grants'].get(key,[])]})
            if len(parts) >= 2 and parts[0] == 'projects':
                project = uid(parts[1])
                self.permission(who, project, 'read')
                if method == 'GET' and len(parts) == 2:
                    with s.db() as c:
                        rows = [dict(r) for r in c.execute("SELECT * FROM tasks WHERE project=? AND state='complete'", (project,))]
                    return self.reply(200, {'versions': [s.status(r) for r in rows]})
                if method == 'POST' and parts[2:] == ['tasks']:
                    self.permission(who, project, 'publish')
                    v = self.body()
                    if set(v) != {'task', 'manifest'}:
                        raise ProtocolError('request-whitelist')
                    task, m = uid(v['task']), manifest(v['manifest'])
                    if s.config['projects'].get(project) != {'store': m['store'], 'local_project': m['local_project']}:
                        raise PermissionError()
                    with s.db() as c:
                        old = c.execute('SELECT * FROM tasks WHERE id=?', (task,)).fetchone()
                        if old:
                            if old['project'] != project or old['binding'] != sha(m):
                                raise ProtocolError('task-conflict')
                        else:
                            if c.execute('SELECT count(*) FROM tasks').fetchone()[0] >= 256:
                                raise ProtocolError('index-limit')
                            if c.execute("SELECT count(*) FROM tasks WHERE state NOT IN ('complete','cancelled')").fetchone()[0] >= 32:
                                raise ProtocolError('task-limit')
                            if c.execute("SELECT 1 FROM tasks WHERE project=? AND state='receiving'", (project,)).fetchone():
                                raise ProtocolError('project-busy')
                            c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)', (task, project, m['version'], sha(m), data(m).decode(), 'receiving'))
                            s.task_created(c, task, project)
                    return self.reply(200, s.status(s.get(task)))
            if len(parts) >= 2 and parts[0] == 'tasks':
                # Unknown/foreign tasks are deliberately indistinguishable to callers.
                try:
                    row = s.get(parts[1])
                except ProtocolError:
                    raise PermissionError() from None
                self.permission(who, row['project'], 'read')
                if method in ('POST', 'PUT'):
                    self.permission(who, row['project'], 'publish')
                if method == 'GET' and len(parts) == 4 and parts[2] == 'files':
                    self.permission(who, row['project'], 'download')
                state = s.status(row)
                if method == 'GET' and len(parts) == 2:
                    return self.reply(200, state)
                if len(parts) == 4 and parts[2] == 'files':
                    i = int(parts[3])
                    m = s.bound_manifest(row)
                    if not 0 <= i < len(m['files']):
                        raise ProtocolError('file-id')
                    f = m['files'][i]
                    if method == 'GET':
                        self.permission(who, row['project'], 'download')
                        if row['state'] != 'complete' or not state['healthy']:
                            raise ProtocolError('version-unavailable')
                        p = safe(s.root/'versions'/row['id']/str(i))
                        self.send_response(200); self.send_header('Content-Length', str(f['size'])); self.end_headers()
                        sent = 0
                        with p.open('rb') as stream:
                            while b := stream.read(65536):
                                self.wfile.write(b)
                                sent += len(b)
                                cfg = control.config()
                                if cfg.get('action') == 'disconnect-download' and sent >= cfg.get('after_bytes', MAX_FILE+1) and cfg.get('nonce') != control.fired:
                                    control.fired = cfg.get('nonce')
                                    control.event('download-interrupted', task=row['id'], file=i, sent=sent)
                                    raise Disconnect()
                        return
                    if method == 'PUT':
                        self.permission(who, row['project'], 'publish')
                        if row['state'] != 'receiving':
                            raise ProtocolError('task-terminal')
                        return self.upload(row, i, f)
                if method == 'POST' and len(parts) == 3:
                    self.permission(who, row['project'], 'publish')
                    if self.body() != {}:
                        raise ProtocolError('request-whitelist')
                    if parts[2] == 'cancel':
                        if row['state'] != 'complete':
                            with s.db() as c:
                                c.execute("UPDATE tasks SET state='cancelled' WHERE id=?", (row['id'],))
                        return self.reply(200, s.status(s.get(row['id'])))
                    if parts[2] == 'commit':
                        if row['state'] == 'complete':
                            if not state['healthy']:
                                raise ProtocolError('version-unavailable')
                            return self.reply(200, state)
                        if row['state'] != 'receiving' or len(state['verified']) != len(state['manifest']['files']):
                            raise ProtocolError('incomplete')
                        final = s.seal(row, control.checkpoint)
                        control.checkpoint('before-rename')
                        if self.server.fault == 'before-rename':
                            os._exit(73)
                        os.rename(final, safe(s.root/'versions'/row['id']))
                        control.checkpoint('after-rename')
                        if self.server.fault == 'after-rename':
                            os._exit(73)
                        control.checkpoint('db-write')
                        with s.db() as c:
                            c.execute("UPDATE tasks SET state='complete' WHERE id=?", (row['id'],))
                            s.task_completed(c, row['id'])
                            control.checkpoint('db-commit')
                        control.checkpoint('after-db')
                        if self.server.fault == 'lost-response':
                            raise Disconnect()
                        return self.reply(200, s.status(s.get(row['id'])))
            self.reply(404, {'error': 'not-found'})
        except Disconnect:
            self.close_connection = True
            self.connection.close()
        except PermissionError:
            self.reply(403, {'error': 'forbidden'})
        except ProtocolError as exc:
            self.reply(409, {'error': str(exc)})
        except (ValueError, KeyError, sqlite3.IntegrityError):
            self.reply(409, {'error': 'invalid-or-conflicting-request'})
        except TimeoutError:
            try:
                self.reply(408, {'error': 'transfer-timeout'})
            except OSError:
                pass
        except (OSError, sqlite3.Error):
            try:
                self.reply(503, {'error': 'temporary-storage-or-transport-failure'})
            except OSError:
                pass
        except Exception:
            try:
                self.reply(400, {'error': 'invalid-request'})
            except OSError:
                pass


class Handler(PublishService, BaseHTTPRequestHandler):
    """Historical isolated HTTPServer transport, not used by ASGI."""


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--port', type=int, default=0)
    p.add_argument('--ready', type=Path, required=True)
    p.add_argument('--fault', choices=['', 'before-rename', 'after-rename', 'lost-response'], default='')
    p.add_argument('--deny-root', type=Path, action='append', default=[])
    p.add_argument('--test-control', type=Path)
    a = p.parse_args()
    forbidden = [Path(os.path.abspath(q)) for q in a.deny_root]

    def audit(event, args):
        if event in ('open', 'sqlite3.connect', 'os.scandir', 'os.listdir') and args and isinstance(args[0], (str, os.PathLike)):
            value = os.fspath(args[0])
            if value.startswith('file:'):
                from urllib.parse import unquote
                value = unquote(value[5:].split('?')[0]).lstrip('/')
            q = Path(os.path.abspath(value))
            if any(q.is_relative_to(root) for root in forbidden):
                raise PermissionError('client-source-access-denied')

    sys.addaudithook(audit)

    class NoDesktop(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.startswith(('PyQt6', 'keyring', 'win32com', 'OCP')):
                raise ImportError('desktop-dependency-forbidden')

    sys.meta_path.insert(0, NoDesktop())
    config = json.loads(a.config.read_bytes())
    # Legacy fault flags are accepted only with explicit isolation guards.
    if a.fault and not a.deny_root:
        raise ProtocolError('isolated-test-required')
    for path_value in [a.ready, *([a.test_control] if a.test_control else [])]:
        q = safe(path_value)
        if any((parent/'.git').exists() for parent in q.parents):
            raise ProtocolError('external-path-required')
    from .library_lock import file_lease
    index = safe(config['index'])
    index.parent.mkdir(parents=True, exist_ok=True)
    with file_lease(index.parent/'.publish-server.lock', exclusive=True):
        store = Store(config)
        server = HTTPServer(('127.0.0.1', a.port), Handler)
        server.request_queue_size = 4
        server.store, server.fault, server.control = store, a.fault, TestControl(a.test_control)
        server.handle_error = lambda *args: None
        with safe(a.ready).open('xb') as out:
            out.write(data(dict(port=server.server_port, service=config['service'], pid=os.getpid())))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('test-service-start-or-storage-failed', file=sys.stderr)
        raise SystemExit(2)
