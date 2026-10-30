# SPDX-License-Identifier: GPL-3.0-only
"""Portable shared-business/ASGI tests; real proxy/TLS tests are separate."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from mechanical_project_namer.asgi_server import Application, Exchange
from mechanical_project_namer.account_server import AccountService, AccountHandler
from mechanical_project_namer.publish_protocol import data
from mechanical_project_namer.transport import Target
from server_fixture import make_service


async def request(app,path='/health',method='GET',body=None,headers=None):
    result=[]
    raw=[(b'host',b'127.0.0.1:4443'),(b'x-forwarded-proto',b'https')]
    payload=data(body) if body is not None else b''
    if body is not None:raw.append((b'content-length',str(len(payload)).encode()))
    raw+=headers or []
    async def receive():return dict(type='http.request',body=payload,more_body=False)
    async def send(v):result.append(v)
    await app(dict(type='http',method=method,raw_path=path.encode(),headers=raw),receive,send)
    return result[0]['status'],json.loads(b''.join(r.get('body',b'') for r in result))


class ASGICore(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='asgi-business-')
        self.f=make_service(Path(self.temp.name)/'source')
        self.app=Application(self.f['store'],'127.0.0.1:4443')
        self.auth=[(b'authorization',('Bearer '+self.f['token']).encode())]

    def tearDown(self):self.app.close();self.temp.cleanup()

    def call(self,*args,**kwargs):return asyncio.run(request(self.app,*args,**kwargs))

    def test_shared_service_not_old_http_handler(self):
        self.assertTrue(issubclass(Exchange,AccountService))
        self.assertFalse(issubclass(Exchange,AccountHandler))
        self.assertEqual(self.call()[1]['service'],self.f['config']['service'])

    def test_unauthenticated(self):self.assertEqual(self.call('/projects')[0],403)
    def test_origin(self):self.assertEqual(self.call(headers=[(b'origin',b'https://other.invalid')])[0],400)
    def test_duplicate_host(self):self.assertEqual(self.call(headers=[(b'host',b'other')])[0],400)
    def test_forwarded(self):self.assertEqual(self.call(headers=[(b'forwarded',b'for=127.0.0.1')])[0],400)
    def test_forged_address(self):self.assertEqual(self.call(headers=[(b'x-forwarded-for',b'admin')])[0],400)
    def test_wrong_proto(self):self.assertEqual(self.call(headers=[(b'x-forwarded-proto',b'http')])[0],400)
    def test_method(self):self.assertEqual(self.call(method='DELETE')[0],400)

    def test_non_ascii_raw_route_does_not_poison_admission(self):
        async def scenario():
            messages=[]
            async def receive():return dict(type='http.request',body=b'',more_body=False)
            async def send(v):messages.append(v)
            scope=dict(type='http',method='GET',raw_path=b'/projects/\xff',
                       headers=[(b'host',b'127.0.0.1:4443'),(b'x-forwarded-proto',b'https')])
            await self.app(scope,receive,send)
            self.assertEqual(messages[0]['status'],400)
            self.assertFalse(self.app.busy)
            self.assertEqual((await request(self.app))[0],200)
        asyncio.run(scenario())

    def test_projects_and_publisher_preserved(self):
        status,result=self.call('/projects',headers=self.auth)
        self.assertEqual(status,200);self.assertEqual(len(result['projects']),2)
        status,result=self.call('/projects/'+self.f['projects'][0],headers=self.auth)
        self.assertEqual(status,200);self.assertEqual(result['versions'][0]['publisher']['id'],self.f['admin_id'])

    def test_foreign_unknown_task_denied(self):
        self.assertEqual(self.call('/tasks/00000000-0000-0000-0000-000000000000',headers=self.auth)[0],403)

    def test_complete_directory_missing_not_staging(self):
        f=self.f;p=f['store'].root/'versions'/f['task']
        p.rename(f['store'].root/'tasks'/f['task'])
        status,result=self.call('/tasks/'+f['task'],headers=self.auth)
        self.assertEqual(status,200);self.assertFalse(result['healthy'])

    def test_latest_grants_checked(self):
        s=self.f['store'];login=s.login('fake-member',self.f['member_password'])
        auth=[(b'authorization',('Bearer '+login['token']).encode())]
        project=self.f['projects'][0]
        self.assertEqual(self.call('/projects/'+project,headers=auth)[0],200)
        s.grant(self.f['admin'],self.f['member']['id'],project,[])
        self.assertEqual(self.call('/projects/'+project,headers=auth)[0],403)

    def test_maintenance_admin_read_only(self):
        with self.f['store'].db() as c:c.execute("UPDATE account_meta SET value='closed' WHERE key='recovery_gate'")
        self.assertEqual(self.call('/admin/users',headers=self.auth)[0],200)
        self.assertEqual(self.call('/admin/users','POST',dict(username='fake-third',role='reader',initial_password='not-real-password-123'),self.auth)[0],403)

    def test_header_framing_rejected(self):
        self.assertEqual(self.call('/auth/login','POST',dict(username='fake-admin',password='invalid'),[(b'content-length',b'2')])[0],409)

    def test_busy_rejection_no_executor_backlog(self):
        self.app.busy=True
        self.assertEqual(self.call()[0],503)
        self.app.busy=False
        self.assertEqual(self.call()[0],200)

    def test_exchange_construction_failure_cannot_hold_admission(self):
        async def scenario():
            with patch.object(Exchange,'__init__',side_effect=RuntimeError('injected-construction-failure')):
                with self.assertRaises(RuntimeError):await request(self.app)
            self.assertFalse(self.app.busy)
            self.assertEqual((await request(self.app))[0],200)
        asyncio.run(scenario())

    def test_executor_submission_failure_cannot_hold_admission(self):
        async def scenario():
            with patch.object(self.app.executor,'submit',side_effect=RuntimeError('injected-submission-failure')):
                with self.assertRaises(RuntimeError):await request(self.app)
            self.assertFalse(self.app.busy)
            self.assertEqual((await request(self.app))[0],200)
        asyncio.run(scenario())

    def test_audit_capacity_refuses_without_deleting_history(self):
        before=self.f['store'].index.read_bytes()
        with patch('mechanical_project_namer.asgi_server.MAX_INDEX',1):
            status,value=self.call()
            self.assertEqual(status,503)
            self.assertEqual(value['error'],'audit-capacity-maintenance-required')
        self.assertEqual(self.f['store'].index.read_bytes(),before)

    def test_double_cancellation_never_releases_active_worker(self):
        started=threading.Event();release=threading.Event()
        def work(exchange,method):started.set();release.wait(3)
        async def scenario():
            with patch.object(Exchange,'dispatch',work):
                task=asyncio.create_task(request(self.app))
                while not started.is_set():await asyncio.sleep(.001)
                task.cancel();await asyncio.sleep(.01);task.cancel()
                await asyncio.sleep(.01)
                self.assertTrue(self.app.busy)
                self.assertEqual((await request(self.app))[0],503)
                release.set()
                try:await task
                except asyncio.CancelledError:pass
                while self.app.busy:await asyncio.sleep(.001)
        try:asyncio.run(scenario())
        finally:release.set()

    def test_legacy_plaintext_requires_explicit_isolation(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError):Target(dict(port=4443,service=self.f['config']['service']))


if __name__=='__main__':unittest.main()
