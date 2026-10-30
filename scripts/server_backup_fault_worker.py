# SPDX-License-Identifier: GPL-3.0-only
"""Test-only hard-exit worker. Every input/product must belong to an explicit probe."""
import argparse,json,os,sys
from pathlib import Path
from mechanical_project_namer.publish_protocol import data,uid
from mechanical_project_namer.server_backups import ServerBackups,private_path,read_doc


def main():
    p=argparse.ArgumentParser();p.add_argument('--probe',type=Path,required=True)
    p.add_argument('--action',choices=['backup','restore','approve'],required=True);p.add_argument('--point',default='none')
    p.add_argument('--config',type=Path);p.add_argument('--backup',type=Path);p.add_argument('--target',type=Path)
    p.add_argument('--task');p.add_argument('--review',type=Path);p.add_argument('--password-file',type=Path)
    a=p.parse_args();probe=private_path(a.probe);doc=read_doc(probe)[0];root=probe.parent
    if set(doc)!={'kind','root','id'} or doc['kind']!='mw-server-recovery-test' or doc['root']!=str(root):raise ValueError('Explicit private test probe required')
    uid(doc['id'])
    for path in (a.config,a.backup,a.target,a.review,a.password_file):
        if path and not private_path(path).is_relative_to(root):raise ValueError('Fault input outside owned probe')
    if os.name!='nt':os.umask(0o077)
    def checkpoint(point):
        if point==a.point:os._exit(73)
    service=ServerBackups(checkpoint=checkpoint)
    if a.action=='backup':result=service.create(a.config,a.target,task_id=a.task)
    elif a.action=='restore':result=service.restore(a.backup,a.target,task_id=a.task)
    else:
        from mechanical_project_namer.accounts import AccountStore
        from mechanical_project_namer.server_recovery import approve_recovery
        from mechanical_project_namer.account_server import read_password
        store=AccountStore(json.loads(a.config.read_bytes()))
        result=approve_recovery(store,'fake-admin',read_password(a.password_file),read_doc(a.review)[0],checkpoint=checkpoint)
    print(data(dict(complete=True)).decode())


if __name__=='__main__':main()
