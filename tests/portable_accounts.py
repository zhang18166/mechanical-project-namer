# SPDX-License-Identifier: GPL-3.0-only
"""Portable account API tests, separate from Windows desktop fixtures."""
import json
import secrets
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from mechanical_project_namer.accounts import AccountStore,HASHER,ACCOUNT_SCHEMA


class Accounts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='workbench-auth-')
        root=Path(self.temp.name)
        self.config=dict(service=str(uuid.uuid4()),storage=str(root/'storage'),index=str(root/'index/db.sqlite'),projects={},session_seconds=2,login_window=1)
        self.store=AccountStore(self.config);self.secret=secrets.token_urlsafe(24)
        self.admin_id=self.store.bootstrap('fake-admin',self.secret)
        login=self.store.login('fake-admin',self.secret);person=self.store.authenticate(login['token'])
        self.fresh=secrets.token_urlsafe(24);self.store.change_password(person,self.secret,self.fresh)
        self.token=self.store.login('fake-admin',self.fresh)['token']
        self.admin=self.store.authenticate(self.token)

    def tearDown(self):self.temp.cleanup()

    def test_bootstrap_replay(self):
        with self.assertRaises(ValueError):self.store.bootstrap('other-admin',secrets.token_urlsafe(24))

    def test_last_admin(self):
        for value in [dict(enabled=False),dict(role='reader')]:
            with self.assertRaises(ValueError):self.store.patch_user(self.admin,self.admin_id,value)
        self.assertEqual(self.store.authenticate(self.token)['role'],'admin')

    def test_hash_not_password(self):
        with self.store.db() as c:row=c.execute('SELECT * FROM users').fetchone()
        self.assertNotIn(self.fresh,row['password_hash'])
        self.assertTrue(row['password_hash'].startswith('$argon2id$'))

    def test_bad_and_unknown_login(self):
        for name in ['fake-admin','no-such-user']:
            with self.assertRaises(PermissionError):self.store.login(name,'INVALID NOT A REAL SECRET')

    def test_failed_limit_persists(self):
        for _ in range(5):
            with self.assertRaises(PermissionError):self.store.login('fake-admin','INVALID PASSWORD')
        with self.assertRaises(PermissionError):self.store.login('fake-admin',self.fresh)
        again=AccountStore(self.config)
        with self.assertRaises(PermissionError):again.login('fake-admin',self.fresh)

    def test_first_password_change(self):
        value=secrets.token_urlsafe(24);user=self.store.create_user(self.admin,'fake-reader','reader',value)
        logged=self.store.login('fake-reader',value);person=self.store.authenticate(logged['token'])
        self.assertTrue(person['must_change'])
        self.assertEqual(self.store.effective(person,str(uuid.uuid4())),set())
        self.store.change_password(person,value,secrets.token_urlsafe(24))
        with self.assertRaises(PermissionError):self.store.authenticate(logged['token'])

    def test_reset_and_disable_revoke(self):
        value=secrets.token_urlsafe(24);user=self.store.create_user(self.admin,'fake-writer','designer',value)
        logged=self.store.login('fake-writer',value)
        self.store.reset_password(self.admin,user['id'],secrets.token_urlsafe(24))
        with self.assertRaises(PermissionError):self.store.authenticate(logged['token'])
        self.store.patch_user(self.admin,user['id'],dict(enabled=False))
        with self.assertRaises(PermissionError):self.store.login('fake-writer',value)

    def test_grant_rollback(self):
        value=secrets.token_urlsafe(24);user=self.store.create_user(self.admin,'fake-member','designer',value)
        project=self.store.create_project(self.admin,dict(name='虚构',description='',store=str(uuid.uuid4()),local_project=1))
        def fail(point):
            if point=='grant':raise OSError('INJECTED')
        self.store.checkpoint=fail
        with self.assertRaises(OSError):self.store.grant(self.admin,user['id'],project,['metadata_view'])
        with self.store.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM grants').fetchone()[0],0)

    def test_user_create_rollback(self):
        with self.store.db() as c:before=c.execute('SELECT count(*) FROM users').fetchone()[0]
        self.store.checkpoint=lambda point:(_ for _ in ()).throw(OSError('INJECTED')) if point=='create-user' else None
        with self.assertRaises(OSError):self.store.create_user(self.admin,'fake-new','reader',secrets.token_urlsafe(24))
        with self.store.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM users').fetchone()[0],before)

    def test_nonadmin_cannot_manage(self):
        value=secrets.token_urlsafe(24);user=self.store.create_user(self.admin,'fake-reader','reader',value)
        person=self.store.authenticate(self.store.login('fake-reader',value)['token'])
        for operation in [
            lambda:self.store.create_user(person,'fake-new','admin',value),
            lambda:self.store.patch_user(person,user['id'],dict(role='admin')),
            lambda:self.store.reset_password(person,self.admin_id,value)]:
            with self.assertRaises(PermissionError):operation()

    def test_guest_and_sharing_reserved(self):
        with self.assertRaises(ValueError):self.store.create_user(self.admin,'fake-guest','guest',secrets.token_urlsafe(24))
        project=self.store.create_project(self.admin,dict(name='虚构',description='',store=str(uuid.uuid4()),local_project=1))
        with self.assertRaises(ValueError):self.store.grant(self.admin,self.admin_id,project,['external_share'])

    def test_expired_session(self):
        self.store.clock=lambda:10**12
        with self.assertRaises(PermissionError):self.store.authenticate(self.token)

    def test_legacy_migration_backup_and_repeat(self):
        from mechanical_project_namer.publish_server import Store
        root=Path(self.temp.name)/'legacy';project=str(uuid.uuid4());store=str(uuid.uuid4());task=str(uuid.uuid4())
        cfg=dict(service=str(uuid.uuid4()),storage=str(root/'storage'),index=str(root/'db/index.sqlite'),
                 projects={project:dict(store=store,local_project=7)})
        legacy=Store(cfg)
        with legacy.db() as c:c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',(task,project,str(uuid.uuid4()),'binding','{}','cancelled'))
        migrated=AccountStore(cfg)
        copies=list((root/'db/migration-backups').glob('*.sqlite'));self.assertEqual(len(copies),1)
        backup=sqlite3.connect(copies[0])
        try:
            self.assertEqual(backup.execute('PRAGMA quick_check').fetchone()[0],'ok')
            self.assertEqual(backup.execute('SELECT id FROM tasks').fetchone()[0],task)
            self.assertFalse(backup.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone())
        finally:backup.close()
        AccountStore(cfg)
        self.assertEqual(len(list((root/'db/migration-backups').glob('*.sqlite'))),1)
        with migrated.db() as c:
            row=c.execute('SELECT * FROM remote_projects').fetchone()
            self.assertEqual((row['id'],row['store'],row['local_project']),(project,store,7))
            self.assertEqual(c.execute('SELECT id FROM tasks').fetchone()[0],task)

    def test_migration_failure_rolls_back_and_retries(self):
        from mechanical_project_namer.publish_server import Store
        root=Path(self.temp.name)/'failure'
        cfg=dict(service=str(uuid.uuid4()),storage=str(root/'storage'),index=str(root/'db/index.sqlite'),projects={})
        legacy=Store(cfg)
        def fail(point):
            if point=='migration':raise OSError('INJECTED')
        with self.assertRaises(OSError):AccountStore(cfg,checkpoint=fail)
        with legacy.db() as c:self.assertFalse(c.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone())
        again=AccountStore(cfg)
        with again.db() as c:self.assertEqual(c.execute("SELECT value FROM account_meta WHERE key='schema'").fetchone()[0],ACCOUNT_SCHEMA)

    def test_future_account_schema_refused(self):
        with self.store.db() as c:c.execute("UPDATE account_meta SET value='999' WHERE key='schema'")
        with self.assertRaisesRegex(ValueError,'unsupported-account-index-schema'):AccountStore(self.config)
        with self.store.db() as c:self.assertEqual(c.execute("SELECT value FROM account_meta WHERE key='schema'").fetchone()[0],'999')

    def test_desktop_schema_refused_without_modification(self):
        root=Path(self.temp.name)/'desktop';root.mkdir()
        cfg=dict(service=str(uuid.uuid4()),storage=str(root/'storage'),index=str(root/'data.sqlite'),projects={})
        c=sqlite3.connect(cfg['index']);c.execute('CREATE TABLE schema_meta(version INTEGER)');c.execute('INSERT INTO schema_meta VALUES(13)');c.commit();c.close()
        before=Path(cfg['index']).read_bytes()
        with self.assertRaisesRegex(ValueError,'not-a-service-index'):AccountStore(cfg)
        self.assertEqual(Path(cfg['index']).read_bytes(),before)


if __name__=='__main__':unittest.main()
