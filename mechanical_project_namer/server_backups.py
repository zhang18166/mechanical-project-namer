# SPDX-License-Identifier: GPL-3.0-only
"""Private full service sets. No desktop paths, CAD parsers, credentials or network.

Filesystem sealing and SQLite commits are distinct. Journals allow two bounded
attempts; unknown products are never adopted or recursively removed.
"""
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid

from . import __version__
from .accounts import ACCOUNT_SCHEMA,stamp
from .backup_io import (check_entries, copy_hash, database_fingerprints, read_metadata, readonly_database,
                        tree_files, write_metadata)
from .library_lock import file_lease
from .publish_protocol import ProtocolError, data, uid, sha, manifest, MAX_JSON
from .publish_server import Store
from .snapshot_io import safe_path, rename_new, check_cancel, Cancelled

FORMAT = 1
INDEX_SCHEMA = ACCOUNT_SCHEMA
MAX_INDEX = 64 * 1024**2
MAX_SET = 512 * 1024**2
MAX_ENTRIES = 30_000
TABLES = {'tasks','account_meta','users','sessions','login_limits','remote_projects',
          'grants','auth_audit','task_provenance'}


def private_path(value):
    p = safe_path(value)
    if any((a/'.git').exists() for a in (p,*p.parents)):
        raise ProtocolError('private-external-path-required')
    return p


def overlap(a,b):
    return a.is_relative_to(b) or b.is_relative_to(a)


def read_doc(path):
    p=private_path(path)
    if p.stat().st_size>MAX_JSON:raise ProtocolError('metadata-limit')
    return read_metadata(p)


def config_white(config):
    out={'service':uid(config['service']),'session_seconds':config.get('session_seconds',1800),
         'login_window':config.get('login_window',60)}
    if type(out['session_seconds']) is not int or not 1<=out['session_seconds']<=3600:
        raise ProtocolError('invalid-service-config')
    if type(out['login_window']) is not int or not 1<=out['login_window']<=300:
        raise ProtocolError('invalid-service-config')
    return out


def service_paths(config_path):
    p=private_path(config_path);config=read_doc(p)[0];config_white(config)
    index=private_path(config['index']);storage=private_path(config['storage'])
    lease=private_path(config.get('lease_root',p.parent.parent/'.server-leases'))
    if overlap(index,storage) or not index.is_file() or not storage.is_dir():
        raise ProtocolError('invalid-service-paths')
    return p,config,index,storage,lease


def _space(parent,amount):
    if amount>MAX_SET or shutil.disk_usage(parent).free<amount+64*1024**2:
        raise ProtocolError('backup-space-limit')


def _target(value,forbidden):
    target=private_path(value)
    if not target.parent.is_dir() or any(overlap(target,p) for p in forbidden):
        raise ProtocolError('independent-target-required')
    for p in target.parents:
        if any((p/n).exists() for n in ('COMPLETE.json','.server-instance.json','.publish-owner.json','.mw-store.json')):
            raise ProtocolError('target-inside-owned-data')
    return target


def _entries_limit(entries):
    if not isinstance(entries,list) or any(not isinstance(e,dict) or set(e)!={'path','size','sha256'} for e in entries):
        raise ProtocolError('invalid-backup-files')
    check_entries(entries)
    if not 1<=len(entries)<=MAX_ENTRIES or sum(e['size'] for e in entries)>MAX_SET:
        raise ProtocolError('backup-size-limit')
    for e in entries:
        limit=MAX_INDEX if e['path']=='index.sqlite' else 16*1024**2
        if e['size']>limit:raise ProtocolError('backup-file-limit')


def _graph(c,config,index,storage,*,cancel=None):
    """Read-only cross-check; deliberately never constructs AccountStore/Store."""
    if [r[0] for r in c.execute('PRAGMA integrity_check')]!=['ok'] or c.execute('PRAGMA foreign_key_check').fetchone():
        raise ProtocolError('database-inconsistent')
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if tables!=TABLES or c.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger')").fetchone():
        raise ProtocolError('unsupported-service-index')
    meta=dict(c.execute('SELECT key,value FROM account_meta'))
    if meta.get('schema')!=INDEX_SCHEMA or meta.get('initialized')!='1' or meta.get('service')!=config['service']:
        raise ProtocolError('index-service-mismatch')
    uid(meta['instance'])
    if meta.get('recovery_gate') not in ('open','closed'):raise ProtocolError('invalid-recovery-gate')
    if not c.execute("SELECT 1 FROM users WHERE role='admin' AND enabled=1").fetchone():
        raise ProtocolError('no-enabled-admin')
    for user in c.execute('SELECT * FROM users'):
        uid(user['id'])
        if not user['password_hash'].startswith('$argon2id$'):raise ProtocolError('unsupported-password-hash')
    projects={r['id']:dict(store=uid(r['store']),local_project=r['local_project']) for r in c.execute('SELECT * FROM remote_projects')}
    for key in projects:uid(key)
    checker=Store.__new__(Store);checker.config=dict(service=config['service'],projects=projects);checker.root=storage;checker.index=index
    if read_doc(storage/'.publish-owner.json')[0]!={'service':config['service']}:
        raise ProtocolError('storage-owner-mismatch')
    if {p.name for p in storage.iterdir()}!={'.publish-owner.json','tasks','versions'}:
        raise ProtocolError('unknown-storage-products')
    # Reject redirections/hardlinks even in excluded terminal upload staging.
    for rel in tree_files(storage,cancel=cancel):
        if private_path(storage/rel).stat().st_nlink!=1:raise ProtocolError('hardlink-refused')
    rows=[dict(r) for r in c.execute('SELECT * FROM tasks ORDER BY id')]
    if any(r['state'] not in ('complete','cancelled') for r in rows):
        raise ProtocolError('unfinished-publication-requires-resolution')
    expected=['.publish-owner.json'];complete=[];known_tasks=set()
    for row in rows:
        check_cancel(cancel);uid(row['id']);uid(row['project'])
        m=checker.bound_manifest(row)
        known_tasks|={row['id'],row['id']+'-sealed',row['id']+'.receiving-owner.json',row['id']+'.sealed-owner.json'}
        for kind in ('receiving','sealed'):
            receipt=storage/'tasks'/(row['id']+'.'+kind+'-owner.json')
            if receipt.exists() and read_doc(receipt)[0]!=checker.owner(row,kind):raise ProtocolError('task-owner-mismatch')
        if row['state']=='complete':
            if not checker.formal_health(row):raise ProtocolError('formal-version-unavailable')
            complete.append(row['id']);prefix='versions/'+row['id']
            expected.extend([prefix+'/manifest.json',*[prefix+'/'+str(i) for i in range(len(m['files']))]])
    if {p.name for p in (storage/'versions').iterdir()}!=set(complete):raise ProtocolError('version-index-mismatch')
    if any(p.name not in known_tasks for p in (storage/'tasks').iterdir()):raise ProtocolError('unknown-task-products')
    for row in rows:
        for name in (row['id'],row['id']+'-sealed'):
            folder=storage/'tasks'/name
            if folder.exists():
                kind='sealed' if name.endswith('-sealed') else 'receiving'
                receipt=storage/'tasks'/(row['id']+'.'+kind+'-owner.json')
                if not receipt.is_file():raise ProtocolError('unknown-task-products')
    return dict(files=sorted(expected),versions=complete,source_instance=meta['instance'],fingerprints=database_fingerprints(c,cancel=cancel))


@contextmanager
def _source(config_path,*,cancel=None):
    p,config,index,storage,lease=service_paths(config_path)
    with ExitStack() as stack:
        stack.enter_context(file_lease(lease/(config['service']+'.lock'),exclusive=True))
        stack.enter_context(file_lease(index.parent/'.publish-server.lock',exclusive=True))
        reserve=sqlite3.connect(index,timeout=0)
        try:
            reserve.execute('PRAGMA trusted_schema=OFF');reserve.execute('BEGIN IMMEDIATE')
            with readonly_database(index,immutable=False,cancel=cancel) as reader:
                yield p,config,index,storage,reader
        finally:reserve.rollback();reserve.close()


class _Job:
    def __init__(self,target,kind,source,task):
        self.id=uid(task);self.folder=private_path(target.parent/('.mw-server-job-'+self.id))
        self.owner=dict(format=FORMAT,task=self.id,kind=kind,source=str(source),target=str(target))
        if self.folder.exists():
            if read_doc(self.folder/'owner.json')[0]!=self.owner:raise ProtocolError('job-binding-conflict')
        else:
            if target.exists():raise ProtocolError('target-exists')
            self.folder.mkdir(mode=0o700);write_metadata(self.folder/'owner.json',self.owner)
        self.journal=self.folder/'journal.json'
        self.state=read_doc(self.journal)[0] if self.journal.exists() else dict(state='prepared',attempts=0,events=[])

    def record(self,state,**values):
        if len(self.state['events'])>=40:raise ProtocolError('job-event-limit')
        self.state.update(values,state=state);self.state['events'].append(dict(state=state,**values))
        write_metadata(self.journal,self.state,replace=self.journal.exists())

    def attempt(self):
        count=self.state['attempts']
        if type(count) is not int or not 0<=count<2:raise ProtocolError('attempt-limit-inspect-retained-products')
        self.record('copying',attempts=count+1)
        folder=self.folder/('attempt-'+str(count+1));folder.mkdir(mode=0o700)
        return folder


class ServerBackups:
    def __init__(self,*,checkpoint=None):self.checkpoint=checkpoint or (lambda point:None)

    def create(self,config_path,destination,*,task_id,cancel=None):
        config_path,config,index,storage,_=service_paths(config_path)
        target=_target(destination,[config_path.parent,index.parent,storage])
        with file_lease(target.parent/('.mw-server-job-'+uid(task_id)+'.lock'),exclusive=True):
            job=_Job(target,'backup',config_path,task_id)
            if target.exists():
                doc=self.verify(target)
                if doc['backup_id']!=job.id or sha(doc)!=job.state.get('manifest_sha'):raise ProtocolError('unknown-final-backup')
                job.record('complete');return doc
            if job.state.get('manifest_sha'):
                stage=job.folder/('attempt-'+str(job.state['attempts']))/'set'
                doc=self.verify(stage,cancel=cancel)
                if doc['backup_id']!=job.id or sha(doc)!=job.state['manifest_sha']:raise ProtocolError('sealed-backup-mismatch')
                check_cancel(cancel);self.checkpoint('backup-before-commit');rename_new(stage,target)
                self.checkpoint('backup-after-commit');job.record('complete');return doc
            try:
                with _source(config_path,cancel=cancel) as (_,config,index,storage,reader):
                    graph=_graph(reader,config,index,storage,cancel=cancel);white=config_white(config)
                    binding=sha(dict(graph=graph,config=white))
                    if job.state.get('binding') not in (None,binding):raise ProtocolError('source-changed-new-task-required')
                    job.record('frozen',binding=binding);self.checkpoint('backup-frozen')
                    db_bytes=reader.execute('PRAGMA page_count').fetchone()[0]*reader.execute('PRAGMA page_size').fetchone()[0]
                    amount=db_bytes+sum((storage/rel).stat().st_size for rel in graph['files'])
                    if db_bytes>MAX_INDEX:raise ProtocolError('index-size-limit')
                    _space(target.parent,amount);check_cancel(cancel)
                    stage=job.attempt()/'set';stage.mkdir(mode=0o700);payload=stage/'data';payload.mkdir()
                    self.checkpoint('backup-stage')
                    database=sqlite3.connect(payload/'index.sqlite')
                    try:
                        reader.backup(database,pages=16,progress=lambda *args:(check_cancel(cancel),self.checkpoint('backup-database-copy')))
                    finally:database.close()
                    self.checkpoint('backup-database')
                    entries=[dict(path='index.sqlite',**copy_hash(payload/'index.sqlite',cancel=cancel))]
                    write_metadata(payload/'config.json',white)
                    entries.append(dict(path='config.json',**copy_hash(payload/'config.json',cancel=cancel)))
                    for rel in graph['files']:
                        dest=payload/'storage'/rel
                        value=copy_hash(storage/rel,dest,cancel=cancel,progress=lambda count:self.checkpoint('backup-copy'))
                        entries.append(dict(path='storage/'+rel,**value))
                    (payload/'storage/tasks').mkdir(exist_ok=True)
                    (payload/'storage/versions').mkdir(exist_ok=True)
                    # Copy only formal versions, never terminal receiving/sealed duplicates.
                    _entries_limit(entries)
                    doc=dict(format=FORMAT,application=__version__,index_schema=INDEX_SCHEMA,backup_id=job.id,
                             service=white['service'],source_instance=graph['source_instance'],versions=graph['versions'],
                             database_fingerprints=graph['fingerprints'],files=entries,unencrypted=True,
                             excluded=['plaintext-passwords','raw-tokens','logs','client-work-files','terminal-upload-staging'])
                    self.checkpoint('backup-before-manifest');write_metadata(stage/'manifest.json',doc)
                    self.checkpoint('backup-after-manifest');write_metadata(stage/'COMPLETE.json',dict(format=FORMAT,backup_id=job.id,manifest_sha=sha(doc)))
                    self.checkpoint('backup-complete-marker')
                    # Verify from a database copy; never run migration on source or set.
                    self.verify(stage,cancel=cancel);job.record('sealed',manifest_sha=sha(doc))
                    if _graph(reader,config,index,storage,cancel=cancel)!=graph:raise ProtocolError('source-changed-during-backup')
                    check_cancel(cancel);self.checkpoint('backup-before-commit');rename_new(stage,target)
                    self.checkpoint('backup-after-commit');job.record('complete');return doc
            except Exception as exc:
                job.record('cancelled' if isinstance(exc,Cancelled) else 'failed',error=type(exc).__name__)
                raise

    def verify(self,backup,*,cancel=None):
        root=private_path(backup);doc,raw_sha=read_doc(root/'manifest.json')
        fields={'format','application','index_schema','backup_id','service','source_instance','versions','database_fingerprints','files','unencrypted','excluded'}
        if set(doc)!=fields or doc['format']!=FORMAT or doc['application']!=__version__ or doc['index_schema']!=INDEX_SCHEMA or doc['unencrypted'] is not True:
            raise ProtocolError('unsupported-backup-format')
        uid(doc['backup_id']);uid(doc['service']);uid(doc['source_instance']);_entries_limit(doc['files'])
        if read_doc(root/'COMPLETE.json')[0]!=dict(format=FORMAT,backup_id=doc['backup_id'],manifest_sha=raw_sha):
            raise ProtocolError('incomplete-backup')
        if {p.name for p in root.iterdir()}!={'manifest.json','COMPLETE.json','data'}:raise ProtocolError('unexpected-backup-products')
        # These two empty structural directories are deliberately permitted.
        entries=doc['files'];expected={e['path'] for e in entries}
        if tree_files(root/'data',cancel=cancel)!=expected:raise ProtocolError('backup-file-set-mismatch')
        dirs={str(p).replace('\\','/') for e in entries for p in Path(e['path']).parents if str(p)!='.'}|{'storage','storage/tasks','storage/versions'}
        for current,children,_ in os.walk(root/'data',followlinks=False):
            for name in children:
                rel=(Path(current)/name).relative_to(root/'data').as_posix()
                if rel not in dirs:raise ProtocolError('unexpected-backup-directory')
        for entry in entries:
            if copy_hash(root/'data'/entry['path'],cancel=cancel)!={k:entry[k] for k in ('size','sha256')}:raise ProtocolError('backup-hash-mismatch')
        white=read_doc(root/'data/config.json')[0]
        if config_white(white)!=white or white['service']!=doc['service']:raise ProtocolError('backup-config-mismatch')
        # All DB reads execute on this disposable, private copy, not the backup.
        with tempfile.TemporaryDirectory(prefix='mw-server-check-') as temporary:
            copied=Path(temporary)/'index.sqlite';copy_hash(root/'data/index.sqlite',copied,cancel=cancel)
            with readonly_database(copied,cancel=cancel) as reader:
                graph=_graph(reader,white,copied,root/'data/storage',cancel=cancel)
            if graph['versions']!=doc['versions'] or graph['source_instance']!=doc['source_instance'] or graph['fingerprints']!=doc['database_fingerprints']:
                raise ProtocolError('backup-database-file-mismatch')
            actual={'index.sqlite','config.json'}|{'storage/'+rel for rel in graph['files']}
            if expected!=actual:raise ProtocolError('backup-graph-mismatch')
        return doc

    def restore(self,backup,destination,*,task_id,lease_root=None,cancel=None):
        backup=private_path(backup);target=_target(destination,[backup])
        with file_lease(target.parent/('.mw-server-job-'+uid(task_id)+'.lock'),exclusive=True):
            job=_Job(target,'restore',backup,task_id);doc=self.verify(backup,cancel=None if target.exists() else cancel)
            binding=sha(doc)
            if job.state.get('binding') not in (None,binding):raise ProtocolError('backup-changed-new-task-required')
            if target.exists():
                receipt=read_doc(target/'.server-instance.json')[0]
                if receipt.get('task')!=job.id or receipt.get('backup')!=doc['backup_id']:raise ProtocolError('unknown-restored-target')
                self.verify_instance(target)
                job.record('complete');return receipt
            if job.state.get('sealed'):
                stage=job.folder/('attempt-'+str(job.state['attempts']))/'instance'
                receipt=self.verify_instance(stage,config_root=target)
                if receipt['task']!=job.id:raise ProtocolError('restore-job-mismatch')
                check_cancel(cancel);self.checkpoint('restore-before-commit');rename_new(stage,target)
                self.checkpoint('restore-after-commit');job.record('complete');return receipt
            try:
                job.record('frozen',binding=binding);self.checkpoint('restore-frozen')
                _space(target.parent,sum(e['size'] for e in doc['files']));check_cancel(cancel)
                stage=job.attempt()/'instance';stage.mkdir(mode=0o700)
                self.checkpoint('restore-stage')
                for entry in doc['files']:
                    if entry['path']=='config.json':continue
                    rel='index/index.sqlite' if entry['path']=='index.sqlite' else entry['path']
                    value=copy_hash(backup/'data'/entry['path'],stage/rel,cancel=cancel,progress=lambda n:self.checkpoint('restore-copy'))
                    if value!={k:entry[k] for k in ('size','sha256')}:raise ProtocolError('restore-copy-hash')
                (stage/'storage/tasks').mkdir(exist_ok=True);(stage/'storage/versions').mkdir(exist_ok=True)
                white=read_doc(backup/'data/config.json')[0]
                runtime_leases=private_path(lease_root or target.parent/'.server-leases')
                config=dict(white,index=str(target/'index/index.sqlite'),storage=str(target/'storage'),projects={},lease_root=str(runtime_leases))
                instance=str(uuid.uuid4());recovery=str(uuid.uuid4())
                c=sqlite3.connect(stage/'index/index.sqlite')
                try:
                    c.execute('PRAGMA trusted_schema=OFF');c.execute('BEGIN IMMEDIATE')
                    c.execute('DELETE FROM sessions')
                    for key,value in dict(instance=instance,recovery_gate='closed',recovery_id=recovery).items():
                        c.execute('INSERT OR REPLACE INTO account_meta VALUES (?,?)',(key,value))
                    c.execute("INSERT INTO auth_audit(actor,action,target,details,created_at) VALUES (NULL,'service-restored',?,?,?)",
                              (recovery,data(dict(backup=doc['backup_id'],old_sessions_revoked=True,security_rollback_unreviewed=True)).decode(),stamp()))
                    self.checkpoint('restore-before-database-commit');c.commit();self.checkpoint('restore-after-database-commit')
                finally:c.close()
                self.checkpoint('restore-before-config');write_metadata(stage/'server.json',config)
                receipt=dict(format=FORMAT,task=job.id,backup=doc['backup_id'],service=doc['service'],instance=instance,recovery=recovery)
                write_metadata(stage/'.server-instance.json',receipt);self.checkpoint('restore-after-config')
                self.verify_instance(stage,config_root=target)
                job.record('sealed',sealed=True);check_cancel(cancel)
                self.checkpoint('restore-before-commit');rename_new(stage,target)
                self.checkpoint('restore-after-commit');job.record('complete');return receipt
            except Exception as exc:
                job.record('cancelled' if isinstance(exc,Cancelled) else 'failed',error=type(exc).__name__);raise

    def verify_instance(self,root,*,config_root=None):
        root=private_path(root);location=private_path(config_root or root)
        receipt=read_doc(root/'.server-instance.json')[0];config=read_doc(root/'server.json')[0]
        if receipt.get('format')!=FORMAT or config['service']!=receipt['service'] or config['index']!=str(location/'index/index.sqlite') or config['storage']!=str(location/'storage'):
            raise ProtocolError('restored-path-binding-mismatch')
        for key in ('task','backup','service','instance','recovery'):uid(receipt[key])
        with readonly_database(root/'index/index.sqlite') as c:
            meta=dict(c.execute('SELECT key,value FROM account_meta'))
            if meta.get('instance')!=receipt['instance'] or meta.get('recovery_id')!=receipt['recovery']:
                raise ProtocolError('restored-instance-mismatch')
            _graph(c,config,root/'index/index.sqlite',root/'storage')
        return receipt
