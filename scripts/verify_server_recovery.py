# SPDX-License-Identifier: GPL-3.0-only
"""Windows native GUI + independent target Linux whole-service recovery drill.

All fixture credentials, models, screenshots and reports stay outside Git.
Only this harness's fresh Linux source is renamed/permission-isolated.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from scripts.auth_harness import AuthFixture,ManagedService,linux_root,linux_path,ROOT,rejected
from scripts.publish_harness import remote_manifest
from mechanical_project_namer.account_client import AccountSession
from mechanical_project_namer.publish_client import Client
from mechanical_project_namer.publish_protocol import data,digest
from mechanical_project_namer.project_files import ProjectFileCatalog
from mechanical_project_namer.ui.project_files_page import ProjectFilesPage

PERMS=['metadata_view','preview_view','pdf_view','source_download','version_publish','project_manage']


def run(root):
    app=QApplication([]);f=AuthFixture(root,'linux',large=True);s=f.service;restored=None;page=None;source_available=True
    native_base=linux_root()+'/drill-'+uuid.uuid4().hex
    s.native("import os,sys;os.mkdir(sys.argv[1],0o700)",native_base)
    backup=native_base+'/backup';target=native_base+'/restored';retired=s.data_root+'-unavailable'
    proof=f.root/'restore-denial-proof.json';backup_proof=f.root/'backup-denial-proof.json'
    source=s.data_root;old_tokens=[f.admin.token,*[v['session'].token for v in f.users.values()]]
    def maintenance(action,*args,deny=(),evidence=proof):
        code="""import sys,os,runpy,json,pathlib
blocked=[pathlib.Path(p) for p in json.loads(sys.argv[1])];proof=pathlib.Path(sys.argv[2]);attempts=[]
def audit(event,args):
 if event in ('open','sqlite3.connect','os.listdir','os.scandir') and args and isinstance(args[0],(str,os.PathLike)):
  p=pathlib.Path(os.path.abspath(args[0]))
  if any(p.is_relative_to(q) for q in blocked):attempts.append(event);raise PermissionError('test-source-inaccessible')
sys.addaudithook(audit);sys.argv=['server_recovery',*sys.argv[3:]]
try:runpy.run_module('mechanical_project_namer.server_recovery',run_name='__main__')
finally:proof.write_text(json.dumps(dict(forbidden_access_attempts=len(attempts))))
"""
        result=subprocess.run(s.base()+['-c',code,json.dumps(list(deny)),linux_path(evidence),action,*map(str,args)],cwd=ROOT,capture_output=True,timeout=90)
        if result.returncode:raise RuntimeError('drill-maintenance-failed: '+result.stderr.decode(errors='replace')[-300:])
        assert json.loads(evidence.read_bytes())['forbidden_access_attempts']==0
        return json.loads(result.stdout)
    def native_fingerprints(path):
        code="""import pathlib,hashlib,json,sys
r=pathlib.Path(sys.argv[1]);print(json.dumps({p.relative_to(r).as_posix():[hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns] for p in r.rglob('*') if p.is_file() and not p.name.endswith('.lock')}))"""
        return json.loads(s.native(code,path))
    def private_write(name,value):
        path=native_base+'/'+name;s.native_write(path,value if isinstance(value,bytes) else data(value),exclusive=True);return path
    def wait(dialog):
        end=time.monotonic()+90
        while dialog.worker:
            assert time.monotonic()<end,'Native account UI timeout';QTest.qWait(20)
        assert '操作未完成' not in dialog.status.text(),dialog.status.text()
    def click(button,dialog):assert button.isEnabled();QTest.mouseClick(button,Qt.MouseButton.LeftButton);wait(dialog)
    def login(dialog,name,password):
        dialog.username.setText(name);dialog.password.setText(password);click(dialog.login_button,dialog)
    def project(dialog,key):
        click(dialog.refresh_button,dialog);i=next(i for i,p in enumerate(dialog.project_rows) if p['id']==key)
        dialog.projects.setCurrentRow(i);wait(dialog)
    try:
        # Prepare V3 before protection starts; no original file or number is changed.
        cat=ProjectFileCatalog(f.a.repo);choices=[dict(file_id=r['file_id']) for r in cat.list_files(f.a.project.id)]
        v3=f.a.snap.prepare(f.a.project.id,f.a.sources[0].parent,choices,'恢复后V3');f.a.snap.run(v3);f.a.before=f.a.protected()
        f.grant(f.admin.user['id'],f.pb,PERMS)
        disabled_initial=secrets.token_urlsafe(24)
        disabled=f.admin.request('POST','/admin/users',dict(username='fake-disabled',role='reader',initial_password=disabled_initial))
        f.grant(disabled['id'],f.pa,['metadata_view']);f.admin.request('PATCH','/admin/users/'+disabled['id'],dict(enabled=False))
        metadata_initial=secrets.token_urlsafe(24);metadata_current=secrets.token_urlsafe(24)
        metadata=f.admin.request('POST','/admin/users',dict(username='fake-metadata',role='reader',initial_password=metadata_initial))
        m=AccountSession(s.connection);m.login('fake-metadata',metadata_initial);m.change_password(metadata_initial,metadata_current);m.login('fake-metadata',metadata_current)
        f.grant(metadata['id'],f.pa,['metadata_view']);old_tokens.append(m.token)
        # Real partial HTTP upload: completed first file, truncated second file.
        first=f.admin.publisher(f.pa).publish(f.a.snap,f.a.v1)
        writer=f.users['fake-writer']['session'];client=writer.publisher(f.pa);manifest=remote_manifest(f.a,f.task)
        task=str(uuid.uuid4());client.request('POST','/projects/'+f.pa+'/tasks',dict(task=task,manifest=manifest))
        local=f.a.snap._get(f.task);payload=f.a.snap.store/'versions'/local['version_id']/'payload'
        client.request('PUT',f'/tasks/{task}/files/0',file=payload/manifest['files'][0]['path'])
        route=f'/tasks/{task}/files/1';amount=262144;entry=manifest['files'][1]
        connection=http.client.HTTPConnection('127.0.0.1',s.connection['port'],timeout=10)
        connection.putrequest('PUT',route);connection.putheader('Authorization','Bearer '+writer.token);connection.putheader('Content-Length',str(entry['size']));connection.endheaders()
        with (payload/entry['path']).open('rb') as stream:connection.send(stream.read(amount))
        # Observe the server's actual partial file BEFORE severing the transport;
        # Windows/WSL forwarding can otherwise drop queued bytes on half-close.
        received=int(s.native("import pathlib,sys,time;p=pathlib.Path(sys.argv[1]);end=time.monotonic()+5\nwhile (not p.exists() or p.stat().st_size<int(sys.argv[2])) and time.monotonic()<end:time.sleep(.01)\nprint(p.stat().st_size if p.exists() else 0)",source+'/storage/tasks/'+task+'/1.partial',str(amount)))
        assert 0<received<entry['size'] and received==amount
        connection.sock.shutdown(__import__('socket').SHUT_WR)
        try:
            response=connection.getresponse();response.read();assert response.status!=200
        except http.client.RemoteDisconnected:
            pass  # WSL forwarding may close a half-closed connection without an HTTP error body.
        finally:connection.close()
        pending=client.request('GET','/tasks/'+task);assert pending['verified']==[0] and 0<amount<entry['size']
        confirmed=[]
        for i,item in enumerate(manifest['files']):
            if i in pending['verified']:continue
            client.request('PUT',f'/tasks/{task}/files/{i}',file=payload/item['path']);confirmed.append(i)
        second=client.request('POST','/tasks/'+task+'/commit',{});assert second['healthy'] and confirmed==list(range(1,len(manifest['files'])))
        other=f.admin.publisher(f.pb).publish(f.b.snap,f.b.v1)
        # A cancelled record stays in the backup; no automatic completion.
        cancelled=str(uuid.uuid4());cancel_manifest=dict(manifest,version=str(uuid.uuid4()))
        client.request('POST','/projects/'+f.pa+'/tasks',dict(task=cancelled,manifest=cancel_manifest))
        assert client.request('POST','/tasks/'+cancelled+'/cancel',{})['state']=='cancelled'
        # Include an initial-password reset and active sessions at the recovery point.
        reset_before=secrets.token_urlsafe(24)
        f.admin.request('POST','/admin/users/'+f.users['fake-download']['id']+'/reset',dict(initial_password=reset_before))
        s.stop();before_backup=native_fingerprints(source)
        created=maintenance('backup','--config',s.config,'--target',backup,'--task',str(uuid.uuid4()),evidence=backup_proof)
        assert created['versions']==3 and created['unencrypted']
        verified=maintenance('verify','--backup',backup,evidence=f.root/'verify-proof.json');assert verified['verified']
        assert native_fingerprints(source)==before_backup
        backup_before=native_fingerprints(backup)
        # Security events deliberately AFTER the backup, absent from the old point.
        s.start();f.admin=s.admin()
        f.admin.request('PATCH','/admin/users/'+f.users['fake-outsider']['id'],dict(enabled=False))
        f.grant(f.users['fake-reader']['id'],f.pa,[])
        reset_after=secrets.token_urlsafe(24)
        f.admin.request('POST','/admin/users/'+f.users['fake-download']['id']+'/reset',dict(initial_password=reset_after))
        s.stop();source_after_changes=native_fingerprints(source)
        s.native("import os,sys;os.rename(sys.argv[1],sys.argv[2]);os.chmod(sys.argv[2],0)",source,retired);source_available=False
        forbidden=[source,retired,linux_path(f.a.root/'client'),linux_path(f.b.root/'client')]
        receipt=maintenance('restore','--backup',backup,'--target',target,'--task',str(uuid.uuid4()),'--lease-root',linux_root()+'/.server-leases',deny=forbidden)
        assert receipt['maintenance'] and receipt['service']==s.connection['service']
        # Reuse only process lifecycle helpers, never source config/data.
        restored=ManagedService.__new__(ManagedService);restored.root=f.root/'replacement';restored.root.mkdir();restored.platform='linux';restored.process=None
        restored.data_root=target;restored.config=target+'/server.json';restored.deny=[Path(str(f.a.root/'client')),Path(str(f.b.root/'client'))]
        restored.initial='';restored.current=s.current
        restored.start()
        for old in old_tokens:
            stale=AccountSession(restored.connection);stale.token=old;rejected(lambda:stale.me())
        for name,value in f.users.items():rejected(lambda n=name,v=value:AccountSession(restored.connection).login(n,v['password']))
        os.environ['MECHANICAL_PROJECT_NAMER_DEV_ROOT']=str(f.a.root/'client')
        page=ProjectFilesPage(f.a.repo);page.resize(1100,800);page.show();QTest.qWait(100)
        QTest.mouseClick(page.account_button,Qt.MouseButton.LeftButton);d=page.account_window;d.snapshots=f.a.snap;d.local_changed()
        d.connection.setText(str(restored.root/'connection.json'));login(d,'fake-admin',s.current)
        assert '恢复维护中' in d.account_label.text();project(d,f.pa);assert len(d.version_rows)==2
        assert next(v for v in d.version_rows if v['task']==second['task'])['publisher']['id']==f.users['fake-writer']['id']
        d.grab().save(str(f.root/'gui-maintenance.png'))
        click(d.logout_button,d);restored.stop()
        state=maintenance('inspect','--config',restored.config,deny=forbidden,evidence=f.root/'inspect-proof.json')
        reset_passwords={};rows=[]
        for user in state['users']:
            name=user['username'];enabled=bool(user['enabled']) and name!='fake-outsider'
            grants={p['id']:[] for p in state['projects']}
            if name=='fake-admin':grants={p:PERMS for p in (f.pa,f.pb)}
            elif name=='fake-writer':grants[f.pa]=[p for p in PERMS if p!='project_manage']
            elif name=='fake-download':grants[f.pa]=['metadata_view','source_download']
            elif name=='fake-metadata':grants[f.pa]=['metadata_view']
            row=dict(id=user['id'],enabled=enabled,role=user['role'],password_action='keep-confirmed',projects=grants)
            if name!='fake-admin':
                initial=secrets.token_urlsafe(24);password_file=private_write('reset-'+user['id'],initial.encode())
                reset_passwords[name]=initial;row.update(password_action='reset',password_file=password_file)
            rows.append(row)
        review=dict(recovery=state['recovery'],source_offline=True,security_changes_reviewed=True,users=rows)
        review_file=private_write('review.json',review);admin_file=private_write('admin-password',s.current.encode())
        assert restored.command('bootstrap','--config',restored.config,'--username','fake-replay','--password-file',admin_file,check=False)!=0
        approved=maintenance('approve','--config',restored.config,'--username','fake-admin','--password-file',admin_file,'--review-file',review_file,deny=forbidden,evidence=f.root/'approve-proof.json')
        assert approved['approved'] and not approved['maintenance']
        restored.start();d.connection.setText(str(restored.root/'connection.json'));login(d,'fake-admin',s.current)
        downloaded=0
        for remote,expected in ((f.pa,[first,second]),(f.pb,[other])):
            project(d,remote);assert len(d.version_rows)==len(expected)
            for version in expected:
                i=next(i for i,v in enumerate(d.version_rows) if v['task']==version['task']);d.versions.setCurrentRow(i)
                destination=f.root/('download-'+version['task']);d.download_target.setText(str(destination));click(d.download_button,d)
                for item in version['manifest']['files']:
                    assert digest(destination/item['path'])=={k:item[k] for k in ('size','sha256')};downloaded+=1
        d.grab().save(str(f.root/'gui-restored-download.png'));click(d.logout_button,d)
        login(d,'fake-writer',reset_passwords['fake-writer']);assert d.session.user['must_change']
        writer_fresh=secrets.token_urlsafe(24);d.new_password.setText(writer_fresh);click(d.change_button,d)
        login(d,'fake-writer',writer_fresh);project(d,f.pa);d.local_version.setCurrentIndex(d.local_version.findData(v3))
        click(d.publish_button,d);new=json.loads(d.details.toPlainText());assert new['healthy'] and new['publisher']['id']==f.users['fake-writer']['id']
        d.grab().save(str(f.root/'gui-restored-v3.png'));click(d.logout_button,d)
        # Known disabled, revoked and metadata-only states do not silently revive.
        for name in ('fake-disabled','fake-outsider'):rejected(lambda n=name:AccountSession(restored.connection).login(n,reset_passwords[n]))
        for name,project_count in (('fake-reader',0),('fake-metadata',1),('fake-download',1)):
            account=AccountSession(restored.connection);initial=reset_passwords[name];changed=secrets.token_urlsafe(24)
            account.login(name,initial);account.change_password(initial,changed);account.login(name,changed)
            assert len(account.projects())==project_count
            rejected(lambda a=account:a.publisher(f.pa).request('POST',f'/tasks/{second["task"]}/commit',{}))
            if name=='fake-metadata':rejected(lambda:account.publisher(f.pa).request('GET',f'/tasks/{second["task"]}/files/0'))
            account.logout()
        rejected(lambda:AccountSession(restored.connection).login('fake-download',reset_after))
        old_root_versions=s.native("import sys,pathlib;print('isolated') if not pathlib.Path(sys.argv[1]).exists() else sys.exit(3)",source)
        assert old_root_versions=='isolated'
        restored.stop();restored.start();admin=AccountSession(restored.connection);admin.login('fake-admin',s.current)
        assert len(admin.versions(f.pa))==3 and len(admin.versions(f.pb))==1
        assert next(v for v in admin.versions(f.pa) if v['task']==new['task'])['healthy'];admin.logout()
        assert native_fingerprints(backup)==backup_before
        s.native('import os,sys;os.chmod(sys.argv[1],0o700)',retired);source_available=True
        assert native_fingerprints(retired)==source_after_changes
        f.protect()
        report=dict(passed=True,real_windows_gui=True,linux_recovery=True,versions_before=3,versions_after=4,
                    downloaded_files=downloaded,old_sessions_revoked=True,security_rollback_reviewed=True,source_unchanged=True,backup_unchanged=True,
                    restore_forbidden_access_attempts=json.loads(proof.read_bytes())['forbidden_access_attempts'],
                    partial_upload=dict(sent_bytes=amount,received_bytes=received,total_bytes=entry['size'],first_file_reused=True,resumed_indices=confirmed,
                                        per_file_requests=[1,2,1,1]),
                    application='1.7.1',desktop_schema=13,index_schema=3,environment=restored.info())
        (f.root/'report.json').write_bytes(data(report));print(data(report).decode())
    finally:
        if page:page.close_preview();page.close();QTest.qWait(40)
        if restored:restored.stop()
        if not source_available:s.native('import os,sys;os.chmod(sys.argv[1],0o700)',retired)
        f.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.output)
