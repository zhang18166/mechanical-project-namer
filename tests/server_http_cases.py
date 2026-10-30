# SPDX-License-Identifier: GPL-3.0-only
"""Real child HTTP checks, outside pytest's blocked-network parent process."""
import json,secrets,uuid
from pathlib import Path
from server_fixture import make_service
from scripts.auth_harness import ManagedService
from mechanical_project_namer.account_client import AccountSession
from mechanical_project_namer.publish_protocol import ProtocolError
from mechanical_project_namer.server_backups import ServerBackups


def maintenance_case(root,operation):
    root=Path(root);root.mkdir();f=make_service(root/'source');service=ServerBackups();backup=root/'backup';target=root/'restored'
    service.create(f['config_path'],backup,task_id=str(uuid.uuid4()));service.restore(backup,target,task_id=str(uuid.uuid4()))
    s=ManagedService.__new__(ManagedService);s.root=root/'runtime';s.root.mkdir();s.platform='windows';s.process=None
    s.data_root=str(target);s.config=str(target/'server.json');s.deny=[]
    s.start()
    try:
        account=AccountSession(s.connection);account.login('fake-admin',f['password']);key=f['member']['id']
        routes={
            'create':('POST','/admin/users',dict(username='fake-forbidden',role='admin',initial_password=secrets.token_urlsafe(24))),
            'disable':('PATCH','/admin/users/'+key,dict(enabled=False)),
            'reset':('POST','/admin/users/'+key+'/reset',dict(initial_password=secrets.token_urlsafe(24))),
            'grant':('POST','/admin/grants',dict(user=key,project=f['projects'][0],permissions=['metadata_view'])),
            'password':('POST','/auth/password',dict(old_password=f['password'],new_password=secrets.token_urlsafe(24))),
        }
        assert account.request('GET','/admin/users')['users']
        method,route,body=routes[operation]
        try:account.request(method,route,body)
        except ProtocolError as exc:assert '403' in str(exc)
        else:raise AssertionError('HTTP mutation was allowed before offline recovery approval')
    finally:s.stop()


def duplicate_logical_instance_case(root):
    import os,subprocess,sys,time
    root=Path(root);root.mkdir();f=make_service(root/'source');service=ServerBackups();backup=root/'backup';target=root/'restored'
    service.create(f['config_path'],backup,task_id=str(uuid.uuid4()));service.restore(backup,target,task_id=str(uuid.uuid4()))
    first=ManagedService.__new__(ManagedService);first.root=root/'runtime';first.root.mkdir();first.platform='windows';first.process=None
    first.data_root=str(f['root']);first.config=str(f['config_path']);first.deny=[]
    # ManagedService's Windows stop implementation is deliberately not used on Linux.
    if os.name=='nt':first.start()
    else:
        first.ready=root/'source-ready.json'
        first.process=subprocess.Popen([sys.executable,'-m','mechanical_project_namer.account_server','serve','--config',first.config,'--ready',str(first.ready)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        end=time.monotonic()+10
        while not first.ready.exists():assert first.process.poll() is None and time.monotonic()<end;time.sleep(.02)
    try:
        denied=root/'denied-ready.json'
        result=subprocess.run([sys.executable,'-m','mechanical_project_namer.account_server','serve','--config',str(target/'server.json'),'--ready',str(denied)],capture_output=True,timeout=15)
        assert result.returncode==2 and not denied.exists(),'Two locations with the same logical service must not both start'
    finally:
        if os.name=='nt':first.stop()
        else:first.process.terminate();first.process.wait(10)
