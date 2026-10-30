# SPDX-License-Identifier: GPL-3.0-only
"""Actual independent service API acceptance. Windows client talks to Windows or WSL."""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid
from scripts.auth_harness import AuthFixture,rejected,ROOT
from mechanical_project_namer.account_client import AccountSession
from mechanical_project_namer.publish_protocol import data,digest,ProtocolError


def full(f):
    s=f.service;admin=f.admin;writer=f.users['fake-writer']['session'];reader=f.users['fake-reader']['session'];outsider=f.users['fake-outsider']['session']
    rejected(lambda:AccountSession(s.connection).projects(),'login-required')
    rejected(lambda:admin.request('PATCH','/admin/users/'+s.admin_id,dict(enabled=False)),'409')
    rejected(lambda:admin.request('PATCH','/admin/users/'+s.admin_id,dict(role='reader')),'409')
    rejected(lambda:reader.request('POST','/admin/users',dict(username='forged-admin',role='admin',initial_password=secrets.token_urlsafe(24))))
    rejected(lambda:reader.request('PATCH','/admin/users/'+f.users['fake-reader']['id'],dict(role='admin')))
    state=writer.publisher(f.pa).publish(f.a.snap,f.task)
    assert state['healthy'] and state['publisher']['id']==f.users['fake-writer']['id'] and state['published_at']
    assert state['dependencies']==state['preview_correspondence']=='unverified'
    assert [p['id'] for p in reader.projects()]==[f.pa]
    assert [p['id'] for p in outsider.projects()]==[f.pb]
    rejected(lambda:reader.publisher(f.pb).request('GET','/projects/'+f.pb))
    rejected(lambda:outsider.publisher(f.pa).request('GET','/tasks/'+state['task']))
    rejected(lambda:reader.publisher(f.pa).request('POST','/tasks/'+state['task']+'/cancel',{}))
    rejected(lambda:reader.publisher(f.pa).request('POST','/projects/'+f.pa+'/tasks',dict(task=str(uuid.uuid4()),manifest=state['manifest'])))
    for index,entry in enumerate(state['manifest']['files']):
        file_route=f'/tasks/{state["task"]}/files/{index}'
        if entry['path'].lower().endswith(('.glb','.pdf')):
            # Raw client method is JSON-only; use real HTTP to check authorized bytes.
            conn=reader.target.connection()
            try:
                conn.request('GET',file_route,headers={'Authorization':'Bearer '+reader.token})
                response=conn.getresponse();b=response.read()
                assert response.status==200 and hashlib.sha256(b).hexdigest()==entry['sha256']
            finally:conn.close()
        else:rejected(lambda route=file_route:reader.publisher(f.pa).request('GET',route))
        rejected(lambda route=file_route:outsider.publisher(f.pa).request('GET',route))
    writer.publisher(f.pa).download(state['task'],f.root/'download')
    for entry in state['manifest']['files']:
        assert digest(f.root/'download'/entry['path'])=={k:entry[k] for k in ('size','sha256')}
    # Role claims and unauthorized origin mappings cannot override server policy.
    rejected(lambda:reader.request('POST','/admin/grants',dict(user=f.users['fake-reader']['id'],project=f.pb,permissions=['version_publish'],role='admin')))
    rejected(lambda:writer.publisher(f.pb).publish(f.a.snap,f.task))
    # Revoke grants while keeping the same session token.
    same=writer.token;f.grant(f.users['fake-writer']['id'],f.pa,[])
    assert writer.token==same
    rejected(lambda:writer.publisher(f.pa).request('GET','/tasks/'+state['task']))
    f.grant(f.users['fake-writer']['id'],f.pa,['metadata_view','version_publish','source_download'])
    assert writer.publisher(f.pa).versions()[0]['healthy']
    # Role and disable operations revoke existing sessions.
    admin.request('PATCH','/admin/users/'+f.users['fake-writer']['id'],dict(role='reader'))
    rejected(lambda:writer.me())
    disabled=reader.token;admin.request('PATCH','/admin/users/'+f.users['fake-reader']['id'],dict(enabled=False))
    rejected(lambda:reader.me())
    rejected(lambda:AccountSession(s.connection).login('fake-reader',f.users['fake-reader']['password']))
    reset=f.users['fake-download'];old_session=reset['session']
    initial=secrets.token_urlsafe(24);admin.request('POST','/admin/users/'+reset['id']+'/reset',dict(initial_password=initial))
    rejected(lambda:old_session.me())
    old_session.login('fake-download',initial);assert old_session.user['must_change']
    rejected(lambda:old_session.projects())
    changed=secrets.token_urlsafe(24);old_session.change_password(initial,changed);old_session.login('fake-download',changed)
    # Fixed generic login errors and persistent failure throttling.
    bodies=[]
    for name in ('fake-admin','no-such-account'):
        conn=admin.target.connection()
        try:
            conn.request('POST','/auth/login',body=data(dict(username=name,password='NOT A REAL PASSWORD')))
            r=conn.getresponse();bodies.append((r.status,r.read()))
        finally:conn.close()
    assert bodies[0]==bodies[1] and bodies[0][0]==403
    rate_initial=secrets.token_urlsafe(24)
    rate=admin.request('POST','/admin/users',dict(username='fake-rate',role='reader',initial_password=rate_initial))
    for _ in range(5):rejected(lambda:AccountSession(s.connection).login('fake-rate','NOT A REAL PASSWORD'))
    rejected(lambda:AccountSession(s.connection).login('fake-rate',rate_initial))
    # Real SQLite constraint failure through a controlled, private test trigger.
    before=len(admin.request('GET','/admin/users')['users'])
    s.sql("CREATE TRIGGER injected_user_failure BEFORE INSERT ON users BEGIN SELECT RAISE(ABORT,'INJECTED'); END")
    rejected(lambda:admin.request('POST','/admin/users',dict(username='fake-failure',role='reader',initial_password=secrets.token_urlsafe(24))),'409')
    s.sql('DROP TRIGGER injected_user_failure')
    assert len(admin.request('GET','/admin/users')['users'])==before
    # A failed grant insert rolls back the preceding DELETE as well.
    s.sql("CREATE TRIGGER injected_grant_failure BEFORE INSERT ON grants BEGIN SELECT RAISE(ABORT,'INJECTED'); END")
    rejected(lambda:admin.request('POST','/admin/grants',dict(user=reset['id'],project=f.pa,permissions=['metadata_view','preview_view'])),'409')
    s.sql('DROP TRIGGER injected_grant_failure')
    assert old_session.projects()[0]['permissions']==['metadata_view','source_download']
    audit=admin.request('GET','/admin/audit')['audit'];assert audit and all('password' not in json.dumps(r['details']) for r in audit)
    rejected(lambda:outsider.request('GET','/admin/audit'))
    # Persist project/task/user state across a real service restart.
    old_token=admin.token;environment=s.info();s.stop()
    assert s.command('bootstrap','--config',s.config,'--username','fake-again','--password-file',s.data_root+'/bootstrap-password',check=False)!=0
    s.start();admin=AccountSession(s.connection);admin.token=old_token
    assert admin.me()['user']['id']==s.admin_id
    assert len(admin.projects())==2
    assert admin.publisher(f.pa).versions()[0]['task']==state['task']
    # A real expiry test with a newly issued, one-second session.
    s.stop();s.change_config(session_seconds=1);s.start()
    short=AccountSession(s.connection);short.login('fake-admin',s.current);short.me();time.sleep(1.1)
    rejected(lambda:short.me())
    admin=AccountSession(s.connection);admin.token=old_token
    assert admin.me()['user']['role']=='admin'
    # Explicit logout removes the token immediately.
    other=AccountSession(s.connection);other.login('fake-admin',s.current);other.logout();assert other.token is None
    f.protect()
    return dict(independent_process=True,platform=s.platform,environment=environment,project_A=f.pa,project_B=f.pb,
                published_task=state['task'],files=len(state['manifest']['files']),bytes=sum(e['size'] for e in state['manifest']['files']),
                restarted=True,expiry=True,generic_login_errors=True,authorization_revoked=True,sql_rollback=True,source_protected=True)


def auth_case(f,role,operation):
    admin=f.admin
    if role=='admin':session=admin
    elif role=='none':session=AccountSession(f.service.connection);session.token='INVALID-TEST-SESSION-TOKEN'
    else:session=f.users[role]['session']
    state=admin.publisher(f.pa).publish(f.a.snap,f.task)
    endpoints={
        'users':('GET','/admin/users',None),
        'create-user':('POST','/admin/users',dict(username='fake-third',role='admin',initial_password=secrets.token_urlsafe(24))),
        'elevate':('PATCH','/admin/users/'+f.users['fake-reader']['id'],dict(role='admin')),
        'audit':('GET','/admin/audit',None),
        'foreign-project':('GET','/projects/'+f.pb,None),
        'foreign-task':('GET','/tasks/'+str(uuid.uuid4()),None),
        'publish':('POST','/projects/'+f.pa+'/tasks',dict(task=str(uuid.uuid4()),manifest=dict(state['manifest'],version=str(uuid.uuid4())))),
        'cancel':('POST','/tasks/'+state['task']+'/cancel',{}),
        'source':('GET','/tasks/'+state['task']+'/files/'+str(next(i for i,e in enumerate(state['manifest']['files']) if e['path'].endswith('.prt'))),None),
    }
    method,path,body=endpoints[operation]
    allowed=role=='admin' or role=='fake-writer' and operation in ('publish','cancel','source') or role=='fake-download' and operation=='source' or role=='fake-outsider' and operation=='foreign-project'
    if operation=='foreign-task':allowed=False
    if allowed and operation=='source':
        con=session.target.connection()
        try:
            con.request(method,path,headers={'Authorization':'Bearer '+session.token});r=con.getresponse();body=r.read();assert r.status==200
        finally:con.close()
    elif allowed:session.request(method,path,body)
    else:rejected(lambda:session.request(method,path,body))
    f.protect();return dict(role=role,operation=operation,allowed=allowed)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--platform',choices=['windows','linux'],default='windows')
    p.add_argument('--case',default='full');a=p.parse_args();t=time.monotonic();f=None
    try:
        f=AuthFixture(a.output,a.platform,large=a.case=='full')
        result=full(f) if a.case=='full' else auth_case(f,*a.case.split(':'))
        report=dict(passed=True,seconds=time.monotonic()-t,result=result);(f.root/'report.json').write_bytes(data(report));print(data(report).decode())
    finally:
        if f:f.close()


if __name__=='__main__':main()
