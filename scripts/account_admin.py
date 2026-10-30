# SPDX-License-Identifier: GPL-3.0-only
"""Explicit API-only administrator maintenance; never opens service SQLite."""
import argparse,getpass,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from mechanical_project_namer.account_client import AccountSession
from mechanical_project_namer.publish_protocol import data,safe


def secret(path,prompt):
    if path:return safe(path).read_text(encoding='utf-8').rstrip('\r\n')
    return getpass.getpass(prompt)


def main():
    p=argparse.ArgumentParser();p.add_argument('--connection',type=Path,required=True);p.add_argument('--username',required=True)
    p.add_argument('--password-file',type=Path);p.add_argument('--new-login-password-file',type=Path)
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('users');sub.add_parser('audit');sub.add_parser('projects')
    u=sub.add_parser('create-user');u.add_argument('--name',required=True);u.add_argument('--role',choices=['admin','designer','reader'],required=True);u.add_argument('--initial-password-file',type=Path)
    for name in ['disable','reset']:
        c=sub.add_parser(name);c.add_argument('--user',required=True)
        if name=='reset':c.add_argument('--initial-password-file',type=Path)
    c=sub.add_parser('grant');c.add_argument('--user',required=True);c.add_argument('--project',required=True);c.add_argument('--permissions',nargs='*',default=[])
    c=sub.add_parser('create-project');c.add_argument('--name',required=True);c.add_argument('--store',required=True);c.add_argument('--local-project',type=int,required=True)
    a=p.parse_args();session=AccountSession(json.loads(safe(a.connection).read_bytes()))
    try:
        current=secret(a.password_file,'Test account password: ');session.login(a.username,current)
        if session.user['must_change']:
            changed=secret(a.new_login_password_file,'New test login password: ');session.change_password(current,changed);session.login(a.username,changed)
        if a.command=='users':result=session.request('GET','/admin/users')
        elif a.command=='audit':result=session.request('GET','/admin/audit')
        elif a.command=='projects':result=session.projects()
        elif a.command=='create-user':result=session.request('POST','/admin/users',dict(username=a.name,role=a.role,initial_password=secret(a.initial_password_file,'Initial test user password: ')))
        elif a.command=='disable':result=session.request('PATCH','/admin/users/'+a.user,dict(enabled=False))
        elif a.command=='reset':result=session.request('POST','/admin/users/'+a.user+'/reset',dict(initial_password=secret(a.initial_password_file,'New initial test password: ')))
        elif a.command=='grant':result=session.request('POST','/admin/grants',dict(user=a.user,project=a.project,permissions=a.permissions))
        else:result=session.request('POST','/admin/projects',dict(name=a.name,description='虚构维护测试项目',store=a.store,local_project=a.local_project))
        print(data(result).decode())
    finally:
        if session.token:
            try:session.logout()
            except Exception:session.token=None


if __name__=='__main__':
    try:main()
    except Exception:
        print('account-maintenance-operation-failed',file=sys.stderr);raise SystemExit(2)
