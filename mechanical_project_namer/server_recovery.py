# SPDX-License-Identifier: GPL-3.0-only
"""Offline service maintenance. Recovery approval has no HTTP/default-password path."""
from contextlib import ExitStack
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

from .account_server import read_password
from .accounts import AccountStore, HASHER, ROLES, PERMISSIONS, stamp
from .library_lock import file_lease
from .publish_protocol import ProtocolError, data, sha, uid
from .server_backups import ServerBackups, service_paths, read_doc, private_path


def inspect_recovery(store):
    with store.db() as c:
        users=[store.public_user(r) for r in c.execute('SELECT * FROM users ORDER BY id')]
        grants=[dict(r) for r in c.execute('SELECT * FROM grants ORDER BY user_id,project,permission')]
        projects=[dict(r) for r in c.execute('SELECT id,name,status FROM remote_projects ORDER BY id')]
    return dict(service=store.config['service'],**store.recovery_state(),users=users,projects=projects,grants=grants,
                warning='backup may predate later security changes; all ordinary access remains closed until explicit review')


def approve_recovery(store,name,secret,review,*,checkpoint=None):
    checkpoint=checkpoint or (lambda point:None)
    with store.db() as c:
        actor=c.execute('SELECT * FROM users WHERE username=?',(name.lower(),)).fetchone()
        users={r['id']:dict(r) for r in c.execute('SELECT * FROM users')}
        projects={r[0] for r in c.execute('SELECT id FROM remote_projects')}
    try:valid=HASHER.verify(actor['password_hash'] if actor else store.dummy,secret)
    except Exception:valid=False
    if not actor or not valid or not actor['enabled'] or actor['role']!='admin':
        raise PermissionError('maintenance-authentication-failed')
    if set(review)!={'recovery','source_offline','security_changes_reviewed','users'} or review['source_offline'] is not True or review['security_changes_reviewed'] is not True:
        raise ProtocolError('explicit-security-review-required')
    state=store.recovery_state()
    if not state['maintenance'] or review['recovery']!=state['recovery']:raise ProtocolError('not-pending-recovery')
    if not isinstance(review['users'],list) or len(review['users'])!=len(users):raise ProtocolError('all-users-must-be-reviewed')
    prepared=[];seen=set();sanitized=[]
    for item in review['users']:
        fields={'id','enabled','role','password_action','projects'}
        action=item.get('password_action')
        if set(item)!=(fields|({'password_file'} if action=='reset' else set())):raise ProtocolError('review-user-fields')
        key=uid(item['id'])
        if key not in users or key in seen or type(item['enabled']) is not bool or item['role'] not in ROLES:
            raise ProtocolError('review-user-fields')
        seen.add(key)
        if action not in ('reset','keep-confirmed'):raise ProtocolError('password-review-required')
        if item['enabled'] and item['role']!='admin' and action!='reset':raise ProtocolError('enabled-members-require-password-reset')
        if not isinstance(item['projects'],dict) or set(item['projects'])!=projects:raise ProtocolError('all-project-grants-must-be-reviewed')
        grant_rows=[]
        for project,perms in item['projects'].items():
            if not isinstance(perms,list) or any(not isinstance(p,str) for p in perms) or len(perms)!=len(set(perms)) or set(perms)-PERMISSIONS:
                raise ProtocolError('review-permission-fields')
            if set(perms)-{'metadata_view'} and 'metadata_view' not in perms:raise ProtocolError('metadata-permission-required')
            if item['role']=='reader' and set(perms)&{'version_publish','project_manage'}:raise ProtocolError('reader-write-denied')
            grant_rows.extend((key,project,p) for p in sorted(perms))
        encoded=users[key]['password_hash']
        if action=='reset':
            from .accounts import password
            encoded=HASHER.hash(password(read_password(private_path(item['password_file']))))
        prepared.append((key,item,encoded,grant_rows))
        sanitized.append({k:v for k,v in item.items() if k!='password_file'})
    if not any(item['enabled'] and item['role']=='admin' for _,item,_,_ in prepared):raise ProtocolError('last-admin-protected')
    if actor['must_change'] and next(item for key,item,_,_ in prepared if key==actor['id'])['password_action']!='reset':
        raise ProtocolError('initial-admin-requires-offline-reset')
    fingerprint=sha(dict(recovery=state['recovery'],users=sorted(sanitized,key=lambda row:row['id'])))
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');checkpoint('approve-before-write')
        c.execute('DELETE FROM sessions')
        for key,item,encoded,grants in prepared:
            c.execute('UPDATE users SET enabled=?,role=?,password_hash=?,must_change=?,generation=generation+1 WHERE id=?',
                      (item['enabled'],item['role'],encoded,1 if item['password_action']=='reset' else users[key]['must_change'],key))
            c.execute('DELETE FROM grants WHERE user_id=?',(key,))
            c.executemany('INSERT INTO grants VALUES (?,?,?)',grants)
            store.audit(c,'recovery-user-reviewed',key,dict(enabled=item['enabled'],role=item['role'],password_reset=item['password_action']=='reset',grant_count=len(grants)),actor['id'])
        c.execute("UPDATE account_meta SET value='open' WHERE key='recovery_gate'")
        c.execute("INSERT OR REPLACE INTO account_meta VALUES ('recovery_review',?)",(fingerprint,))
        store.audit(c,'recovery-approved',state['recovery'],dict(review=fingerprint,security_changes_reviewed=True,source_offline_asserted=True),actor['id'])
        checkpoint('approve-before-commit')
    checkpoint('approve-after-commit')
    return dict(approved=True,review=fingerprint,**store.recovery_state())


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='action',required=True)
    create=sub.add_parser('backup');create.add_argument('--config',type=Path,required=True);create.add_argument('--target',type=Path,required=True);create.add_argument('--task',required=True)
    verify=sub.add_parser('verify');verify.add_argument('--backup',type=Path,required=True)
    restore=sub.add_parser('restore');restore.add_argument('--backup',type=Path,required=True);restore.add_argument('--target',type=Path,required=True);restore.add_argument('--task',required=True);restore.add_argument('--lease-root',type=Path)
    for action in ('inspect','approve'):
        command=sub.add_parser(action);command.add_argument('--config',type=Path,required=True)
        if action=='approve':
            command.add_argument('--username',required=True);command.add_argument('--password-file',type=Path);command.add_argument('--review-file',type=Path,required=True)
    args=parser.parse_args()
    if os.name!='nt':
        if os.geteuid()==0:raise ProtocolError('nonprivileged-user-required')
        os.umask(0o077)
    service=ServerBackups()
    if args.action=='backup':
        doc=service.create(args.config,args.target,task_id=args.task)
        result=dict(complete=True,backup=doc['backup_id'],service=doc['service'],versions=len(doc['versions']),unencrypted=True)
    elif args.action=='verify':
        doc=service.verify(args.backup);result=dict(verified=True,backup=doc['backup_id'],versions=len(doc['versions']),unencrypted=True)
    elif args.action=='restore':result=dict(restored=True,maintenance=True,**service.restore(args.backup,args.target,task_id=args.task,lease_root=args.lease_root))
    else:
        path,config,index,storage,lease=service_paths(args.config)
        with ExitStack() as locks:
            locks.enter_context(file_lease(lease/(config['service']+'.lock'),exclusive=True))
            locks.enter_context(file_lease(index.parent/'.publish-server.lock',exclusive=True))
            store=AccountStore(config)
            service.verify_instance(path.parent)
            if args.action=='inspect':result=inspect_recovery(store)
            else:
                review=private_path(args.review_file)
                if os.name!='nt' and (review.stat().st_uid!=os.geteuid() or review.stat().st_mode&0o077):raise ProtocolError('private-review-file-required')
                result=approve_recovery(store,args.username,read_password(args.password_file),read_doc(review)[0])
    print(data(result).decode())


if __name__=='__main__':
    try:main()
    except Exception as exc:
        code=str(exc) if isinstance(exc,ProtocolError) and re.fullmatch('[a-z0-9-]{1,100}',str(exc)) else 'service-maintenance-failed'
        print(code,file=sys.stderr);raise SystemExit(2)
