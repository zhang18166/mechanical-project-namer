# SPDX-License-Identifier: GPL-3.0-only
"""Server-owned accounts/index. No desktop, CAD or credential-vault imports."""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError, InvalidHashError
from .publish_protocol import uid, safe, data, ProtocolError
from .publish_server import Store

PERMISSIONS = {'metadata_view','preview_view','pdf_view','source_download','version_publish','project_manage'}
ROLES = {'admin','designer','reader'}  # guest reserved; never anonymous
ACCOUNT_SCHEMA = '3'  # Older loaders must refuse recovery-gated instances.
HASHER = PasswordHasher(time_cost=3,memory_cost=65536,parallelism=4,hash_len=32,salt_len=16,type=Type.ID)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def password(value):
    if not isinstance(value,str) or not 12<=len(value)<=128 or '\0' in value:
        raise ProtocolError('password-policy')
    return value


def label(value,limit=120):
    if not isinstance(value,str) or not 1<=len(value)<=limit or re.search(r'[A-Za-z]:[\\/]|\\\\|^\s*/',value):
        raise ProtocolError('invalid-text')
    return value


def username(value):
    if not isinstance(value,str) or not re.fullmatch('[a-zA-Z][a-zA-Z0-9_.-]{2,31}',value):
        raise ProtocolError('invalid-username')
    return value.lower()


class AccountStore(Store):
    def __init__(self,config,*,clock=time.time,checkpoint=None):
        self.clock=clock
        self.checkpoint=checkpoint or (lambda point:None)
        self.actor=None
        index=safe(config['index'])
        for p in [index,safe(config['storage'])]:
            if any((a/'.git').exists() for a in (p,*p.parents)):
                raise ProtocolError('external-service-data-required')
        if not index.exists() and (safe(config['storage'])/'.publish-owner.json').exists():
            raise ProtocolError('missing-index-recovery-required')
        if index.exists():
            # Opening an existing index in rw (never create) lets SQLite recover
            # a hot rollback journal after a killed writer before schema reads.
            old=sqlite3.connect(index.as_uri()+'?mode=rw',uri=True)
            try:
                tables={r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'schema_meta' in tables or (tables and 'tasks' not in tables):
                    raise ProtocolError('not-a-service-index')
                if 'account_meta' in tables:
                    row=old.execute("SELECT value FROM account_meta WHERE key='schema'").fetchone()
                    if not row or row[0] not in ('2',ACCOUNT_SCHEMA):raise ProtocolError('unsupported-account-index-schema')
                    meta=dict(old.execute('SELECT key,value FROM account_meta'))
                    if meta.get('service',config['service'])!=uid(config['service']):raise ProtocolError('index-service-mismatch')
                    if row[0]==ACCOUNT_SCHEMA:
                        if not {'service','instance','recovery_gate'}<=meta.keys() or meta['recovery_gate'] not in ('open','closed'):
                            raise ProtocolError('invalid-recovery-metadata')
                        uid(meta['instance'])
                        if meta['recovery_gate']=='closed':uid(meta.get('recovery_id'))
                        if meta['recovery_gate']=='open' and 'recovery_id' in meta and 'recovery_review' not in meta:
                            raise ProtocolError('unreviewed-recovery-state')
                else:meta={}
                migrate=meta.get('schema')!=ACCOUNT_SCHEMA
                if migrate:
                    folder=index.parent/'migration-backups';folder.mkdir(exist_ok=True)
                    target=safe(folder/('before-accounts-'+uuid.uuid4().hex+'.sqlite'))
                    backup=sqlite3.connect(target)
                    try:old.backup(backup)
                    finally:backup.close()
            finally:old.close()
        super().__init__(config)
        self.dummy=HASHER.hash(secrets.token_urlsafe(32))
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            statements=[
                'CREATE TABLE IF NOT EXISTS account_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)',
                "CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','designer','reader','guest')),enabled INTEGER NOT NULL,must_change INTEGER NOT NULL,password_hash TEXT NOT NULL,generation INTEGER NOT NULL,created_at TEXT NOT NULL)",
                'CREATE TABLE IF NOT EXISTS sessions(digest TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),generation INTEGER NOT NULL,expires REAL NOT NULL)',
                'CREATE TABLE IF NOT EXISTS login_limits(subject TEXT PRIMARY KEY,failures INTEGER NOT NULL,until REAL NOT NULL)',
                "CREATE TABLE IF NOT EXISTS remote_projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('active','archived')),store TEXT NOT NULL,local_project INTEGER NOT NULL,created_at TEXT NOT NULL)",
                'CREATE TABLE IF NOT EXISTS grants(user_id TEXT NOT NULL REFERENCES users(id),project TEXT NOT NULL REFERENCES remote_projects(id),permission TEXT NOT NULL,PRIMARY KEY(user_id,project,permission))',
                'CREATE TABLE IF NOT EXISTS auth_audit(id INTEGER PRIMARY KEY,actor TEXT,action TEXT NOT NULL,target TEXT,details TEXT NOT NULL,created_at TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS task_provenance(task TEXT PRIMARY KEY REFERENCES tasks(id),publisher TEXT REFERENCES users(id),accepted_at TEXT,published_at TEXT)']
            for sql in statements:c.execute(sql)
            c.execute("INSERT OR IGNORE INTO account_meta VALUES ('schema',?)",(ACCOUNT_SCHEMA,))
            c.execute("UPDATE account_meta SET value=? WHERE key='schema'",(ACCOUNT_SCHEMA,))
            c.execute("INSERT OR IGNORE INTO account_meta VALUES ('service',?)",(uid(config['service']),))
            c.execute("INSERT OR IGNORE INTO account_meta VALUES ('instance',?)",(str(uuid.uuid4()),))
            c.execute("INSERT OR IGNORE INTO account_meta VALUES ('recovery_gate','open')")
            for key,mapping in config.get('projects',{}).items():
                c.execute("INSERT OR IGNORE INTO remote_projects VALUES (?,?,?,'active',?,?,?)",
                          (uid(key),'旧索引项目','迁移来源；原发布者/时间未知',uid(mapping['store']),mapping['local_project'],stamp()))
            self.checkpoint('migration')
        self.refresh_projects()

    def recovery_state(self):
        with self.db() as c:meta=dict(c.execute('SELECT key,value FROM account_meta'))
        if meta.get('recovery_gate') not in ('open','closed'):raise ProtocolError('invalid-recovery-gate')
        return dict(instance=uid(meta['instance']),maintenance=meta['recovery_gate']=='closed',recovery=meta.get('recovery_id'))

    def db(self):
        c=super().db()
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA trusted_schema=OFF')
        return c

    def refresh_projects(self):
        with self.db() as c:
            self.config['projects']={r['id']:dict(store=r['store'],local_project=r['local_project']) for r in c.execute('SELECT * FROM remote_projects')}

    def audit(self,c,action,target=None,details=None,actor=None):
        c.execute('INSERT INTO auth_audit(actor,action,target,details,created_at) VALUES (?,?,?,?,?)',
                  (actor if actor is not None else self.actor,action,target,data(details or {}).decode(),stamp()))
        self.checkpoint('audit')

    def bootstrap(self,name,initial):
        name=username(name);encoded=HASHER.hash(password(initial))
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM account_meta WHERE key='initialized'").fetchone() or c.execute('SELECT 1 FROM users').fetchone():
                raise ProtocolError('already-initialized')
            key=str(uuid.uuid4())
            c.execute('INSERT INTO users VALUES (?,?,?,1,1,?,0,?)',(key,name,'admin',encoded,stamp()))
            c.execute("INSERT INTO account_meta VALUES ('initialized','1')")
            self.audit(c,'bootstrap',key,actor=key)
            self.checkpoint('bootstrap')
        return key

    def public_user(self,row):
        return {k:row[k] for k in ('id','username','role','enabled','must_change','created_at')}

    def login(self,name,secret):
        try:name=username(name)
        except ProtocolError:name='invalid-input'
        if not isinstance(secret,str) or len(secret)>128:secret=''
        subject=hashlib.sha256(name.encode()).hexdigest()
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
            gate=c.execute("SELECT value FROM account_meta WHERE key='recovery_gate'").fetchone()[0]
            limit=c.execute('SELECT * FROM login_limits WHERE subject=?',(subject,)).fetchone()
            now=self.clock()
            blocked=bool(limit and limit['failures']>=5 and limit['until']>now)
            try:valid=HASHER.verify(row['password_hash'] if row else self.dummy,secret)
            except (VerificationError,InvalidHashError):valid=False
            if not row or not valid or not row['enabled'] or row['role']=='guest' or blocked or (gate!='open' and row['role']!='admin'):
                count=limit['failures']+1 if limit and limit['until']>now else 1
                if limit or row or c.execute('SELECT count(*) FROM login_limits').fetchone()[0]<4096:
                    c.execute('INSERT OR REPLACE INTO login_limits VALUES (?,?,?)',(subject,count,now+min(300,max(1,int(self.config.get('login_window',60)))) if not limit or limit['until']<=now else limit['until']))
                self.audit(c,'login-failed',actor=None)
                result=None
            else:
                if HASHER.check_needs_rehash(row['password_hash']):
                    c.execute('UPDATE users SET password_hash=? WHERE id=?',(HASHER.hash(secret),row['id']))
                c.execute('DELETE FROM login_limits WHERE subject=?',(subject,))
                # Expired session cleanup is metadata only; never filesystem/history cleanup.
                c.execute('DELETE FROM sessions WHERE expires<=?',(now,))
                if c.execute('SELECT count(*) FROM sessions WHERE user_id=?',(row['id'],)).fetchone()[0]>=16:
                    self.audit(c,'login-session-limit',row['id'],actor=row['id']);result=None
                else:
                    token=secrets.token_urlsafe(32)
                    ttl=min(3600,max(1,int(self.config.get('session_seconds',1800))))
                    c.execute('INSERT INTO sessions VALUES (?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),row['id'],row['generation'],now+ttl))
                    self.audit(c,'login',row['id'],actor=row['id'])
                    result=dict(token=token,expires_at=now+ttl,user=self.public_user(row))
        if result is None:raise PermissionError('authentication-failed')
        return result

    def authenticate(self,token):
        if not isinstance(token,str) or not re.fullmatch('[a-zA-Z0-9_-]{20,128}',token):raise PermissionError()
        with self.db() as c:
            row=c.execute('SELECT u.*,s.expires,s.generation AS session_generation FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.digest=?',
                          (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row or row['expires']<=self.clock() or not row['enabled'] or row['generation']!=row['session_generation'] or row['role']=='guest':
            raise PermissionError()
        if self.recovery_state()['maintenance'] and row['role']!='admin':raise PermissionError()
        return dict(row)

    def require_admin(self,person):
        if person['role']!='admin' or person['must_change']:raise PermissionError()

    def effective(self,person,project):
        if person['must_change']:return set()
        if self.recovery_state()['maintenance']:
            if person['role']!='admin':return set()
            with self.db() as c:
                return {'metadata_view','project_manage'} if c.execute('SELECT 1 FROM remote_projects WHERE id=?',(project,)).fetchone() else set()
        with self.db() as c:
            row=c.execute('SELECT status FROM remote_projects WHERE id=?',(project,)).fetchone()
            perms={r[0] for r in c.execute('SELECT permission FROM grants WHERE user_id=? AND project=?',(person['id'],project))}
        if not row:return set()
        if person['role']=='admin':perms|={'metadata_view','project_manage'}
        if person['role']=='reader':perms-={'version_publish','project_manage'}
        if row['status']!='active':perms.discard('version_publish')
        return perms & PERMISSIONS

    def person(self,row):
        self.actor=row['id']
        self.refresh_projects()
        grants={}
        for project in self.config['projects']:
            p=self.effective(row,project);legacy=[]
            if 'metadata_view' in p:legacy.append('read')
            if 'version_publish' in p:legacy.append('publish')
            if p & {'source_download','preview_view','pdf_view'}:legacy.append('download')
            if legacy:grants[project]=legacy
        return dict(account=row,grants=grants)

    def create_user(self,person,name,role,initial):
        self.require_admin(person);name=username(name)
        if role not in ROLES:raise ProtocolError('reserved-or-invalid-role')
        encoded=HASHER.hash(password(initial));key=str(uuid.uuid4())
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT count(*) FROM users').fetchone()[0]>=256:raise ProtocolError('account-limit')
            c.execute('INSERT INTO users VALUES (?,?,?,1,1,?,0,?)',(key,name,role,encoded,stamp()))
            self.audit(c,'user-created',key,dict(role=role),person['id'])
            self.checkpoint('create-user')
        return dict(id=key,username=name,role=role,enabled=True,must_change=True)

    def change_password(self,person,old,new):
        password(new)
        try:valid=HASHER.verify(person['password_hash'],old)
        except (VerificationError,InvalidHashError,TypeError):valid=False
        if not valid or old==new:raise PermissionError()
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('UPDATE users SET password_hash=?,must_change=0,generation=generation+1 WHERE id=?',(HASHER.hash(new),person['id']))
            c.execute('DELETE FROM sessions WHERE user_id=?',(person['id'],))
            self.audit(c,'password-changed',person['id'],actor=person['id'])

    def patch_user(self,person,key,values):
        self.require_admin(person);uid(key)
        if not values or set(values)-{'enabled','role'}:raise ProtocolError('user-fields')
        if 'enabled' in values and type(values['enabled']) is not bool:raise ProtocolError('user-fields')
        if 'role' in values and values['role'] not in ROLES:raise ProtocolError('reserved-or-invalid-role')
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT * FROM users WHERE id=?',(key,)).fetchone()
            if not old:raise ProtocolError('unknown-resource')
            enabled=values.get('enabled',bool(old['enabled']));role=values.get('role',old['role'])
            if old['role']=='admin' and old['enabled'] and (not enabled or role!='admin'):
                if c.execute("SELECT count(*) FROM users WHERE enabled=1 AND role='admin'").fetchone()[0]<=1:raise ProtocolError('last-admin-protected')
            c.execute('UPDATE users SET enabled=?,role=?,generation=generation+1 WHERE id=?',(enabled,role,key))
            c.execute('DELETE FROM sessions WHERE user_id=?',(key,))
            self.audit(c,'user-updated',key,values,person['id'])
            self.checkpoint('patch-user')

    def reset_password(self,person,key,initial):
        self.require_admin(person);uid(key);encoded=HASHER.hash(password(initial))
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM users WHERE id=?',(key,)).fetchone():raise ProtocolError('unknown-resource')
            c.execute('UPDATE users SET password_hash=?,must_change=1,generation=generation+1 WHERE id=?',(encoded,key))
            c.execute('DELETE FROM sessions WHERE user_id=?',(key,))
            self.audit(c,'password-reset',key,actor=person['id'])

    def create_project(self,person,values):
        self.require_admin(person)
        if set(values)!={'name','description','store','local_project'}:raise ProtocolError('project-fields')
        name=label(values['name']);description=values['description']
        if not isinstance(description,str):raise ProtocolError('project-fields')
        if description:label(description,1000)
        if type(values['local_project']) is not int or values['local_project']<1:raise ProtocolError('project-fields')
        key=str(uuid.uuid4())
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT count(*) FROM remote_projects').fetchone()[0]>=256:raise ProtocolError('project-limit')
            c.execute('INSERT INTO remote_projects VALUES (?,?,?, ?,?,?,?)',(key,name,description,'active',uid(values['store']),values['local_project'],stamp()))
            self.audit(c,'project-created',key,actor=person['id'])
        self.refresh_projects()
        return key

    def grant(self,person,user_id,project,permissions):
        self.require_admin(person);uid(user_id);uid(project)
        if not isinstance(permissions,list) or len(permissions)!=len(set(permissions)) or set(permissions)-PERMISSIONS:
            raise ProtocolError('permission-fields')
        perms=set(permissions)
        if perms-{'metadata_view'} and 'metadata_view' not in perms:raise ProtocolError('metadata-permission-required')
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            user=c.execute('SELECT role FROM users WHERE id=?',(user_id,)).fetchone()
            if not user or not c.execute('SELECT 1 FROM remote_projects WHERE id=?',(project,)).fetchone():raise ProtocolError('unknown-resource')
            if user['role']=='reader' and perms & {'version_publish','project_manage'}:raise ProtocolError('reader-write-denied')
            c.execute('DELETE FROM grants WHERE user_id=? AND project=?',(user_id,project))
            c.executemany('INSERT INTO grants VALUES (?,?,?)',[(user_id,project,p) for p in sorted(perms)])
            self.audit(c,'grants-replaced',user_id,dict(project=project,permissions=sorted(perms)),person['id'])
            self.checkpoint('grant')

    def projects(self,person):
        with self.db() as c:rows=[dict(r) for r in c.execute('SELECT * FROM remote_projects ORDER BY created_at,id')]
        return [dict(row,permissions=sorted(self.effective(person,row['id']))) for row in rows if 'metadata_view' in self.effective(person,row['id'])]

    def task_created(self,c,task,project):
        if not self.actor:raise PermissionError()
        c.execute('INSERT INTO task_provenance VALUES (?,?,?,NULL)',(task,self.actor,stamp()))
        self.audit(c,'publish-task-created',task,dict(project=project))

    def task_completed(self,c,task):
        old=c.execute('SELECT * FROM task_provenance WHERE task=?',(task,)).fetchone()
        if old and not old['published_at']:
            c.execute('UPDATE task_provenance SET published_at=? WHERE task=?',(stamp(),task))
            self.audit(c,'version-committed',task,actor=old['publisher'])

    def status(self,row):
        result=super().status(row)
        with self.db() as c:
            p=c.execute('SELECT t.*,u.username FROM task_provenance t LEFT JOIN users u ON t.publisher=u.id WHERE t.task=?',(row['id'],)).fetchone()
        result.update(publisher=dict(id=p['publisher'],username=p['username']) if p else None,
                      published_at=p['published_at'] if p else None,provenance='recorded' if p else 'legacy-unrecorded')
        return result
