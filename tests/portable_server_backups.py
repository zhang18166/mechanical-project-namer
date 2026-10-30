# SPDX-License-Identifier: GPL-3.0-only
"""Portable, real SQLite/filesystem maintenance tests. No Qt/Windows fixtures."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from server_fixture import make_service
from mechanical_project_namer.accounts import AccountStore,ACCOUNT_SCHEMA
from mechanical_project_namer.server_backups import ServerBackups
from mechanical_project_namer.server_recovery import approve_recovery,inspect_recovery
from mechanical_project_namer.library_lock import file_lease,MaintenanceBusy
from mechanical_project_namer.publish_protocol import ProtocolError,data


def fingerprints(root):
    return {p.relative_to(root).as_posix():(hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file() and not p.name.endswith('.lock')}


class ServerRecovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mw-server-recovery-');self.root=Path(self.temp.name)
        self.f=make_service(self.root/'source');self.service=ServerBackups();self.task=str(uuid.uuid4());self.backup=self.root/'backup'
    def tearDown(self):self.temp.cleanup()
    def create(self):return self.service.create(self.f['config_path'],self.backup,task_id=self.task)
    def restore(self):
        self.create();target=self.root/'restored';self.service.restore(self.backup,target,task_id=str(uuid.uuid4()))
        cfg=json.loads((target/'server.json').read_bytes());return target,AccountStore(cfg)
    def review(self,store):
        status=inspect_recovery(store);items=[]
        for row in status['users']:
            item=dict(id=row['id'],enabled=True,role=row['role'],password_action='keep-confirmed',projects={p['id']:[] for p in status['projects']})
            if row['role']!='admin':
                p=self.root/('reset-'+row['id']);p.write_text(secrets.token_urlsafe(24));p.chmod(0o600)
                item.update(password_action='reset',password_file=str(p))
            items.append(item)
        return dict(recovery=status['recovery'],source_offline=True,security_changes_reviewed=True,users=items)

    def test_complete_backup_and_stable_verification(self):
        before=fingerprints(self.f['root']);doc=self.create();saved=fingerprints(self.backup)
        self.assertEqual(self.service.verify(self.backup),doc);self.assertEqual(fingerprints(self.backup),saved)
        self.assertEqual(fingerprints(self.f['root']),before);self.assertEqual(doc['versions'],[self.f['task']])
    def test_idempotent_backup_same_task(self):
        doc=self.create();self.assertEqual(self.create(),doc)
    def test_new_task_cannot_adopt_existing_target(self):
        self.create()
        with self.assertRaises(ValueError):self.service.create(self.f['config_path'],self.backup,task_id=str(uuid.uuid4()))
    def test_pending_task_rejected_without_state_change(self):
        with self.f['store'].db() as c:c.execute("UPDATE tasks SET state='receiving'")
        with self.assertRaisesRegex(ValueError,'unfinished-publication'):self.create()
        self.assertEqual(self.f['store'].get(self.f['task'])['state'],'receiving');self.assertFalse(self.backup.exists())
    def test_wrong_service_uuid(self):
        cfg=self.f['config'];cfg['service']=str(uuid.uuid4());self.f['config_path'].write_bytes(data(cfg))
        with self.assertRaises(ValueError):self.create()
    def test_wrong_storage_owner(self):
        (self.f['store'].root/'.publish-owner.json').write_bytes(data(dict(service=str(uuid.uuid4()))))
        with self.assertRaises(ValueError):self.create()
    def test_missing_formal_directory(self):
        folder=self.f['store'].root/'versions'/self.f['task'];folder.rename(folder.with_name(str(uuid.uuid4())))
        with self.assertRaises(ValueError):self.create()
    def test_damaged_formal_file(self):
        (self.f['store'].root/'versions'/self.f['task']/'0').write_bytes(b'DAMAGED')
        with self.assertRaises(ValueError):self.create()
    def test_extra_formal_file(self):
        (self.f['store'].root/'versions'/self.f['task']/'extra').write_bytes(b'FICTIONAL')
        with self.assertRaises(ValueError):self.create()
    def test_unknown_storage_directory_preserved(self):
        unknown=self.f['store'].root/'unknown';unknown.mkdir()
        with self.assertRaises(ValueError):self.create()
        self.assertTrue(unknown.is_dir())
    def test_hardlinked_payload_rejected(self):
        original=self.f['store'].root/'versions'/self.f['task']/'0';os.link(original,self.root/'hardlink')
        with self.assertRaises(ValueError):self.create()
    def test_backup_cancel_then_retry(self):
        flag=threading.Event();flag.set()
        with self.assertRaises(ValueError):self.service.create(self.f['config_path'],self.backup,task_id=self.task,cancel=flag)
        flag.clear();self.create();self.assertTrue(self.backup.is_dir())
    def test_backup_copy_failure_retry_bounded(self):
        def fail(point):
            if point=='backup-copy':raise OSError('INJECTED COPY ERROR')
        broken=ServerBackups(checkpoint=fail)
        for _ in range(2):
            with self.assertRaises(OSError):broken.create(self.f['config_path'],self.backup,task_id=self.task)
        with self.assertRaisesRegex(ValueError,'attempt-limit'):self.create()
        self.assertEqual(len(list((self.root/('.mw-server-job-'+self.task)).glob('attempt-*'))),2)
    def test_backup_space_failure(self):
        with patch('mechanical_project_namer.server_backups._space',side_effect=OSError('INJECTED ENOSPC')):
            with self.assertRaises(OSError):self.create()
        self.assertFalse(self.backup.exists());self.create()
    def test_backup_permission_failure(self):
        with patch('mechanical_project_namer.server_backups.copy_hash',side_effect=PermissionError('INJECTED DENIED')):
            with self.assertRaises(PermissionError):self.create()
        self.assertFalse(self.backup.exists())
    def test_missing_complete_marker(self):
        self.create();(self.backup/'COMPLETE.json').rename(self.root/'saved-marker')
        with self.assertRaises((ValueError,OSError)):self.service.verify(self.backup)
    def test_restore_revokes_sessions_and_preserves_business(self):
        target,store=self.restore()
        with self.assertRaises(PermissionError):store.authenticate(self.f['token'])
        with self.assertRaises(PermissionError):store.login('fake-member',self.f['member_password'])
        admin=store.authenticate(store.login('fake-admin',self.f['password'])['token'])
        self.assertTrue(store.recovery_state()['maintenance'])
        self.assertEqual(store.effective(admin,self.f['projects'][0]),{'metadata_view','project_manage'})
        self.assertEqual(store.status(store.get(self.f['task']))['publisher']['id'],self.f['admin_id'])
        self.assertEqual(set(store.config['projects']),set(self.f['projects']))
        self.assertNotEqual(store.recovery_state()['instance'],self.f['store'].recovery_state()['instance'])
    def test_restore_does_not_read_source(self):
        self.create();self.f['root'].rename(self.root/'unavailable')
        self.service.restore(self.backup,self.root/'restored',task_id=str(uuid.uuid4()))
        self.assertTrue((self.root/'restored/server.json').is_file())
    def test_restore_does_not_modify_backup(self):
        self.create();before=fingerprints(self.backup)
        self.service.restore(self.backup,self.root/'restored',task_id=str(uuid.uuid4()))
        self.assertEqual(before,fingerprints(self.backup))
    def test_restore_existing_target_refused(self):
        self.create();target=self.root/'occupied';target.mkdir();(target/'keep').write_bytes(b'FICTIONAL')
        with self.assertRaises(ValueError):self.service.restore(self.backup,target,task_id=str(uuid.uuid4()))
        self.assertEqual((target/'keep').read_bytes(),b'FICTIONAL')
    def test_restore_overlap_refused(self):
        self.create()
        with self.assertRaises(ValueError):self.service.restore(self.backup,self.backup/'nested',task_id=str(uuid.uuid4()))
    def test_restore_cancel_then_retry(self):
        self.create();flag=threading.Event();flag.set();task=str(uuid.uuid4());target=self.root/'restored'
        with self.assertRaises(ValueError):self.service.restore(self.backup,target,task_id=task,cancel=flag)
        flag.clear();self.service.restore(self.backup,target,task_id=task)
    def test_restore_space_failure(self):
        self.create()
        with patch('mechanical_project_namer.server_backups._space',side_effect=OSError('INJECTED ENOSPC')):
            with self.assertRaises(OSError):self.service.restore(self.backup,self.root/'restored',task_id=str(uuid.uuid4()))
        self.assertFalse((self.root/'restored').exists())
    def test_review_required_and_admin_auth(self):
        _,store=self.restore();review=self.review(store)
        with self.assertRaises(PermissionError):approve_recovery(store,'fake-admin','NOT A REAL PASSWORD',review)
        review['security_changes_reviewed']=False
        with self.assertRaises(ValueError):approve_recovery(store,'fake-admin',self.f['password'],review)
        self.assertTrue(store.recovery_state()['maintenance'])
    def test_review_covers_all_users_and_projects(self):
        _,store=self.restore();review=self.review(store);review['users'].pop()
        with self.assertRaises(ValueError):approve_recovery(store,'fake-admin',self.f['password'],review)
        review=self.review(store);review['users'][0]['projects']={}
        with self.assertRaises(ValueError):approve_recovery(store,'fake-admin',self.f['password'],review)
    def test_review_last_admin_protected(self):
        _,store=self.restore();review=self.review(store)
        for user in review['users']:
            if user['role']=='admin':user['enabled']=False
        with self.assertRaisesRegex(ValueError,'last-admin'):approve_recovery(store,'fake-admin',self.f['password'],review)
    def test_review_sql_rollback(self):
        _,store=self.restore();review=self.review(store)
        def fail(point):
            if point=='approve-before-commit':raise OSError('INJECTED COMMIT FAILURE')
        with self.assertRaises(OSError):approve_recovery(store,'fake-admin',self.f['password'],review,checkpoint=fail)
        self.assertTrue(store.recovery_state()['maintenance'])
        with store.db() as c:self.assertFalse(c.execute("SELECT 1 FROM auth_audit WHERE action='recovery-approved'").fetchone())
    def test_security_rollback_reconciled_before_open(self):
        self.create();member=self.f['member']['id']
        self.f['store'].patch_user(self.f['admin'],member,dict(enabled=False))
        self.f['store'].grant(self.f['admin'],member,self.f['projects'][0],[])
        target=self.root/'restored';self.service.restore(self.backup,target,task_id=str(uuid.uuid4()))
        store=AccountStore(json.loads((target/'server.json').read_bytes()))
        with self.assertRaises(PermissionError):store.login('fake-member',self.f['member_password'])
        review=self.review(store)
        for row in review['users']:
            if row['id']==member:row['enabled']=False;row['projects']={p:[] for p in self.f['projects']}
        approve_recovery(store,'fake-admin',self.f['password'],review)
        self.assertFalse(store.recovery_state()['maintenance'])
        with self.assertRaises(PermissionError):store.login('fake-member',self.f['member_password'])
        with store.db() as c:
            self.assertFalse(c.execute('SELECT * FROM grants WHERE user_id=?',(member,)).fetchone())
            self.assertTrue(c.execute("SELECT * FROM auth_audit WHERE action='recovery-approved'").fetchone())
    def test_bootstrap_not_reopened_on_restore(self):
        _,store=self.restore()
        with self.assertRaises(ValueError):store.bootstrap('fake-new-admin',secrets.token_urlsafe(24))
    def test_restore_cancel_after_commit_is_completed(self):
        self.create();flag=threading.Event()
        def cancel_after(point):
            if point=='restore-after-commit':flag.set()
        target=self.root/'restored'
        ServerBackups(checkpoint=cancel_after).restore(self.backup,target,task_id=str(uuid.uuid4()),cancel=flag)
        self.assertTrue(target.is_dir())

    def test_schema2_upgrade_backup_and_repeat(self):
        store=self.f['store']
        with store.db() as c:
            c.execute("UPDATE account_meta SET value='2' WHERE key='schema'")
            c.execute("DELETE FROM account_meta WHERE key IN ('service','instance','recovery_gate')")
            before=[tuple(r) for r in c.execute('SELECT * FROM users ORDER BY id')]
        upgraded=AccountStore(self.f['config'])
        backups=list((Path(self.f['config']['index']).parent/'migration-backups').glob('*.sqlite'))
        self.assertEqual(len(backups),1)
        c=sqlite3.connect(backups[0])
        try:self.assertEqual(c.execute("SELECT value FROM account_meta WHERE key='schema'").fetchone()[0],'2')
        finally:c.close()
        AccountStore(self.f['config'])
        self.assertEqual(len(list(backups[0].parent.glob('*.sqlite'))),1)
        with upgraded.db() as c:
            self.assertEqual(c.execute("SELECT value FROM account_meta WHERE key='schema'").fetchone()[0],ACCOUNT_SCHEMA)
            self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM users ORDER BY id')],before)
        self.assertTrue(upgraded.formal_health(upgraded.get(self.f['task'])))

    def test_schema2_failed_upgrade_rollback(self):
        with self.f['store'].db() as c:
            c.execute("UPDATE account_meta SET value='2' WHERE key='schema'")
            c.execute("DELETE FROM account_meta WHERE key IN ('service','instance','recovery_gate')")
        def fail(point):
            if point=='migration':raise OSError('INJECTED MIGRATION FAILURE')
        with self.assertRaises(OSError):AccountStore(self.f['config'],checkpoint=fail)
        with self.f['store'].db() as c:
            self.assertEqual(c.execute("SELECT value FROM account_meta WHERE key='schema'").fetchone()[0],'2')
            self.assertFalse(c.execute("SELECT 1 FROM account_meta WHERE key='recovery_gate'").fetchone())
        self.assertFalse(AccountStore(self.f['config']).recovery_state()['maintenance'])

    def test_schema3_missing_gate_is_not_silently_opened(self):
        with self.f['store'].db() as c:c.execute("DELETE FROM account_meta WHERE key='recovery_gate'")
        with self.assertRaisesRegex(ValueError,'invalid-recovery-metadata'):AccountStore(self.f['config'])

    def test_password_rollback_requires_fresh_reset_before_member_access(self):
        self.create();member=self.f['member']['id'];post_backup=secrets.token_urlsafe(24)
        self.f['store'].reset_password(self.f['admin'],member,post_backup)
        target=self.root/'restored';self.service.restore(self.backup,target,task_id=str(uuid.uuid4()))
        store=AccountStore(json.loads((target/'server.json').read_bytes()))
        with self.assertRaises(PermissionError):store.login('fake-member',self.f['member_password'])
        review=self.review(store);row=next(r for r in review['users'] if r['id']==member)
        initial=Path(row['password_file']).read_text()
        approve_recovery(store,'fake-admin',self.f['password'],review)
        for secret in (self.f['member_password'],post_backup):
            with self.assertRaises(PermissionError):store.login('fake-member',secret)
        login=store.login('fake-member',initial)
        self.assertTrue(login['user']['must_change'])

    def test_enabled_member_cannot_keep_rolled_back_password(self):
        _,store=self.restore();review=self.review(store)
        row=next(r for r in review['users'] if r['role']!='admin')
        row['password_action']='keep-confirmed';row.pop('password_file')
        with self.assertRaisesRegex(ValueError,'enabled-members-require-password-reset'):
            approve_recovery(store,'fake-admin',self.f['password'],review)

    def test_missing_index_does_not_create_new_account_system(self):
        index=Path(self.f['config']['index']);index.rename(self.root/'saved-index.sqlite')
        before=fingerprints(self.f['store'].root)
        with self.assertRaisesRegex(ValueError,'missing-index-recovery-required'):AccountStore(self.f['config'])
        self.assertFalse(index.exists());self.assertEqual(fingerprints(self.f['store'].root),before)

    def test_mismatched_database_and_storage(self):
        other=make_service(self.root/'unrelated')
        cfg=dict(self.f['config'],index=other['config']['index'])
        self.f['config_path'].write_bytes(data(cfg))
        with self.assertRaises(ValueError):self.create()
        self.assertFalse(self.backup.exists())

    def test_committed_wal_pages_are_backed_up(self):
        keeper=sqlite3.connect(self.f['config']['index'])
        try:
            self.assertEqual(keeper.execute('PRAGMA journal_mode=WAL').fetchone()[0],'wal')
            keeper.execute('PRAGMA wal_autocheckpoint=0')
            keeper.execute("INSERT INTO auth_audit(action,details,created_at) VALUES ('COMMITTED-WAL','{}','TEST')");keeper.commit()
            self.assertTrue(Path(self.f['config']['index']+'-wal').is_file())
            self.create()
            saved=fingerprints(self.backup)
            c=sqlite3.connect((self.backup/'data/index.sqlite').as_uri()+'?mode=ro&immutable=1',uri=True)
            try:self.assertEqual(c.execute("SELECT count(*) FROM auth_audit WHERE action='COMMITTED-WAL'").fetchone()[0],1)
            finally:c.close()
            self.service.verify(self.backup)
            self.assertEqual(fingerprints(self.backup),saved)
        finally:keeper.close()

    def test_uncoordinated_sqlite_writer_blocks_backup(self):
        writer=sqlite3.connect(self.f['config']['index'])
        try:
            writer.execute('BEGIN IMMEDIATE');writer.execute("UPDATE tasks SET state='cancelled'")
            with self.assertRaises(sqlite3.OperationalError):self.create()
            self.assertFalse(self.backup.exists())
        finally:writer.rollback();writer.close()
        self.create();self.assertTrue(self.f['store'].formal_health(self.f['store'].get(self.f['task'])))

    def test_initial_admin_requires_offline_reset_at_approval(self):
        temporary=secrets.token_urlsafe(24)
        self.f['store'].reset_password(self.f['admin'],self.f['admin_id'],temporary)
        _,store=self.restore();review=self.review(store)
        with self.assertRaisesRegex(ValueError,'initial-admin-requires-offline-reset'):
            approve_recovery(store,'fake-admin',temporary,review)
        row=next(r for r in review['users'] if r['id']==self.f['admin_id'])
        secret=self.root/'admin-initial';new=secrets.token_urlsafe(24);secret.write_text(new);secret.chmod(0o600)
        row.update(password_action='reset',password_file=str(secret))
        approve_recovery(store,'fake-admin',temporary,review)
        self.assertFalse(store.recovery_state()['maintenance'])
        self.assertTrue(store.login('fake-admin',new)['user']['must_change'])


if __name__=='__main__':unittest.main()
