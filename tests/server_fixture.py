# SPDX-License-Identifier: GPL-3.0-only
"""Portable service-only fixtures; clearly fictional transport bytes, never CAD."""
import hashlib
from pathlib import Path
import secrets
import uuid
from mechanical_project_namer.accounts import AccountStore
from mechanical_project_namer.publish_protocol import data,sha


def make_service(root):
    root=Path(root);root.mkdir()
    config=dict(service=str(uuid.uuid4()),index=str(root/'index/index.sqlite'),storage=str(root/'storage'),
                projects={},session_seconds=3600,login_window=60,lease_root=str(root.parent/'.server-leases'))
    store=AccountStore(config);initial=secrets.token_urlsafe(24);current=secrets.token_urlsafe(24)
    admin_id=store.bootstrap('fake-admin',initial);first=store.authenticate(store.login('fake-admin',initial)['token'])
    store.change_password(first,initial,current);token=store.login('fake-admin',current)['token'];admin=store.authenticate(token)
    projects=[store.create_project(admin,dict(name='同名虚构项目',description='只用于文件传输',store=str(uuid.uuid4()),local_project=1)) for _ in range(2)]
    member_password=secrets.token_urlsafe(24)
    member=store.create_user(admin,'fake-member','designer',member_password)
    person=store.authenticate(store.login('fake-member',member_password)['token'])
    member_current=secrets.token_urlsafe(24);store.change_password(person,member_password,member_current)
    store.grant(admin,member['id'],projects[0],['metadata_view','source_download','version_publish'])
    task=str(uuid.uuid4());mapping=store.config['projects'][projects[0]]
    files=[];contents=[]
    for name,role in [('中文 (占位).prt','source'),('子目录/交换.step','exchange'),('图纸/查看.pdf','drawing')]:
        content=('FICTIONAL TRANSPORT ONLY - '+name).encode();contents.append(content)
        files.append(dict(path=name,role=role,size=len(content),sha256=hashlib.sha256(content).hexdigest()))
    m=dict(store=mapping['store'],local_project=1,version=str(uuid.uuid4()),label='V1',files=files)
    store.actor=admin_id
    with store.db() as c:
        c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',(task,projects[0],m['version'],sha(m),data(m).decode(),'complete'))
        store.task_created(c,task,projects[0]);store.task_completed(c,task)
    folder=store.root/'versions'/task;folder.mkdir()
    (folder/'manifest.json').write_bytes(data(m))
    for i,content in enumerate(contents):(folder/str(i)).write_bytes(content)
    config_path=root/'server.json';config_path.write_bytes(data(config));config_path.chmod(0o600)
    return dict(root=root,config=config,config_path=config_path,store=store,admin=admin,admin_id=admin_id,
                password=current,token=token,projects=projects,task=task,manifest=m,member=member,member_password=member_current)
