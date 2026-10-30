# SPDX-License-Identifier: GPL-3.0-only
"""Transport-budget/metadata regressions; real Nginx/TLS evidence is separate."""
import json
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import uuid

from mechanical_project_namer.diagnostics import Trace, publish_marker
from mechanical_project_namer.publish_protocol import ProtocolError, data
from mechanical_project_namer.transport import Target
from scripts.https_runtime import diagnostic_config


class HTTPSHardening(unittest.TestCase):
    def target(self):
        target=object.__new__(Target)
        target.host,target.port='127.0.0.1',4443
        target.config=dict(service=str(uuid.uuid4()))
        target.context=Mock()
        return target

    def connection(self,target):
        c=Mock();c._workbench_health=dict(service=target.config['service'],accounts=True,protocol=1)
        return c

    def test_public_retry_only_two_fresh_checks(self):
        t=self.target();c=self.connection(t)
        with patch.object(t,'connection',side_effect=[ConnectionResetError(),c]) as connect:
            value=t.public_ready(budget=1)
        self.assertTrue(value['verified']);self.assertEqual(value['attempts'],2)
        self.assertEqual(connect.call_count,2);c.close.assert_called_once()
        self.assertTrue(all(0<call.kwargs['timeout']<=1 for call in connect.call_args_list))

    def test_repeated_transient_does_not_loop(self):
        t=self.target()
        with patch.object(t,'connection',side_effect=ConnectionRefusedError()) as connect:
            with self.assertRaises(ConnectionRefusedError):t.public_ready(budget=.1)
        self.assertEqual(connect.call_count,2)

    def terminal(self,code):
        t=self.target()
        with patch.object(t,'connection',side_effect=ProtocolError(code)) as connect:
            with self.assertRaisesRegex(ValueError,code):t.public_ready()
        self.assertEqual(connect.call_count,1)

    def test_certificate_error_never_retried(self):self.terminal('tls-certificate-rejected')
    def test_uuid_error_never_retried(self):self.terminal('service-identity')
    def test_invalid_health_never_retried(self):self.terminal('service-verification-failed')

    def test_exhausted_total_budget_does_not_retry(self):
        t=self.target();clock=[0]
        def fail(**kwargs):clock[0]=10;raise TimeoutError()
        with patch('mechanical_project_namer.transport.time.monotonic',side_effect=lambda:clock[0]),patch.object(t,'connection',side_effect=fail) as connect:
            with self.assertRaises(TimeoutError):t.public_ready()
        self.assertEqual(connect.call_count,1)

    def test_public_retry_limits_cannot_increase(self):
        t=self.target()
        for args in (dict(attempts=3),dict(attempts=0),dict(budget=11),dict(budget=0)):
            with self.assertRaisesRegex(ValueError,'readiness-budget'):t.public_ready(**args)

    def test_connection_budget_cannot_increase(self):
        t=self.target()
        for timeout in (0,11):
            with self.assertRaisesRegex(ValueError,'connection-budget'):t.connection(timeout=timeout)

    def test_tcp_tls_share_remaining_budget(self):
        t=self.target();raw=Mock();raw.getsockname.return_value=('127.0.0.1',12345);secure=Mock();clock=[0]
        def tcp(*args,**kwargs):clock[0]=6;return raw
        def tls(*args,**kwargs):clock[0]=10;return secure
        t.context.wrap_socket.side_effect=tls
        with patch('mechanical_project_namer.transport.time.monotonic',side_effect=lambda:clock[0]),patch('mechanical_project_namer.transport.socket.create_connection',side_effect=tcp) as create,patch('mechanical_project_namer.transport.http.client.HTTPSConnection') as connection:
            with self.assertRaises(TimeoutError):t.connection()
            self.assertEqual(create.call_args.kwargs['timeout'],10)
            raw.settimeout.assert_called_with(4)
            connection.return_value.request.assert_not_called()

    def test_uuid_checked_on_same_socket_without_auto_reconnect(self):
        t=self.target();raw=Mock();raw.getsockname.return_value=('127.0.0.1',12345);secure=Mock();c=Mock()
        t.context.wrap_socket.return_value=secure
        health=dict(service=t.config['service'],accounts=True,protocol=1)
        c.getresponse.return_value.status=200
        with patch('mechanical_project_namer.transport.socket.create_connection',return_value=raw),patch('mechanical_project_namer.transport.http.client.HTTPSConnection',return_value=c),patch('mechanical_project_namer.transport.response_bytes',return_value=data(health)):
            result=t.connection()
        self.assertIs(result.sock,secure);self.assertEqual(result.auto_open,0)
        self.assertEqual(result._workbench_health,health)
        self.assertEqual(c.request.call_args.args,('GET','/health'))
        self.assertNotIn('Authorization',c.request.call_args.kwargs['headers'])

    def test_disabled_diagnostics_create_no_file(self):
        t=Trace();t.emit('client-failed',code='timeout');self.assertEqual(t.bytes,0);t.close()

    def test_diagnostic_fields_are_allowlisted(self):
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'events.jsonl';t=Trace(path)
            t.emit('client-failed',trace='a'*32,code='tls-timeout',Authorization='FICTIONAL-SECRET',password='FICTIONAL',path='private-path',body='FICTIONAL',phase='tls')
            t.close();row=json.loads(path.read_bytes())
            self.assertEqual(row['code'],'tls-timeout')
            self.assertFalse({'Authorization','password','path','body'} & row.keys())
            self.assertNotIn('FICTIONAL',path.read_text())

    def test_diagnostics_have_byte_and_row_caps(self):
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'events.jsonl';t=Trace(path,limit=300,rows=1)
            for i in range(20):t.emit('phase',attempt=i)
            t.close();self.assertLessEqual(path.stat().st_size,300)
            self.assertEqual(len(path.read_text().splitlines()),1);self.assertEqual(t.dropped,19)

    def test_diagnostics_never_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as name:
            p=Path(name)/'existing';p.write_bytes(b'preserve')
            with self.assertRaises(FileExistsError):Trace(p)
            self.assertEqual(p.read_bytes(),b'preserve')

    def test_complete_marker_is_exclusive(self):
        with tempfile.TemporaryDirectory() as name:
            p=Path(name)/'ready.json';value=dict(instance=str(uuid.uuid4()),ready=True)
            publish_marker(p,value);self.assertEqual(json.loads(p.read_bytes()),value)
            with self.assertRaises(FileExistsError):publish_marker(p,dict(ready=False))
            self.assertEqual(json.loads(p.read_bytes()),value)
            self.assertEqual([x.name for x in p.parent.iterdir()],['ready.json'])

    def test_proxy_diagnostic_has_no_sensitive_log_fields(self):
        config,headers=diagnostic_config('/private-example')
        for forbidden in ('$request_uri','$http_authorization','$request_body','$ssl_session_id','$remote_user'):
            self.assertNotIn(forbidden,config)
        self.assertIn('syslog:server=unix:',config)
        self.assertIn('X-Workbench-Connection',headers)


class ASGIDiagnostic(unittest.TestCase):
    def setUp(self):
        from server_fixture import make_service
        from mechanical_project_namer.asgi_server import Application
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name);self.service=make_service(root/'source')
        self.path=root/'application.jsonl';self.trace=Trace(self.path)
        self.app=Application(self.service['store'],'127.0.0.1:4443',diagnostic=self.trace)

    def tearDown(self):self.app.close();self.trace.close();self.temp.cleanup()

    def test_worker_release_is_logged_without_authorization_or_route(self):
        from portable_asgi import request
        secret=self.service['token']
        status,_=asyncio.run(request(self.app,headers=[(b'authorization',('Bearer '+secret).encode()),(b'x-workbench-trace',b'a'*32)]))
        self.assertEqual(status,200);self.assertFalse(self.app.busy)
        text=self.path.read_text();events=[json.loads(x) for x in text.splitlines()]
        ended=next(x for x in events if x['event']=='app-worker-end')
        self.assertEqual(ended['trace'],'a'*32);self.assertIs(ended['slot_busy'],False)
        self.assertNotIn(secret,text);self.assertNotIn('Authorization',text)
        self.assertTrue(all('path' not in x for x in events))

    def test_busy_diagnostic_never_releases_active_slot(self):
        from portable_asgi import request
        self.app.busy=True
        status,_=asyncio.run(request(self.app,headers=[(b'x-workbench-trace',b'b'*32)]))
        self.assertEqual(status,503);self.assertTrue(self.app.busy)
        row=json.loads(self.path.read_text())
        self.assertEqual(row['event'],'app-rejected');self.assertEqual(row['code'],'service-busy-retry')
        self.assertIs(row['slot_busy'],True)


class PartialCleanup(unittest.TestCase):
    def setUp(self):
        from server_fixture import make_service
        from mechanical_project_namer.publish_server import PublishService, TestControl
        from mechanical_project_namer.publish_protocol import sha
        self.temp=tempfile.TemporaryDirectory();self.f=make_service(Path(self.temp.name)/'source')
        self.store=self.f['store'];self.task=str(uuid.uuid4());m=dict(self.f['manifest'],version=str(uuid.uuid4()))
        self.store.actor=self.f['admin_id']
        with self.store.db() as c:
            c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',(self.task,self.f['projects'][0],m['version'],sha(m),data(m).decode(),'receiving'))
            self.store.task_created(c,self.task,self.f['projects'][0])
        self.row=self.store.get(self.task);self.entry=m['files'][0]
        self.partial=self.store.root/'tasks'/self.task/'0.partial'
        self.operation=object.__new__(PublishService)
        self.operation.server=SimpleNamespace(store=self.store,control=TestControl())
        self.operation.length=Mock(return_value=self.entry['size'])
        self.operation.connection=Mock();self.operation.rfile=Mock()

    def tearDown(self):self.temp.cleanup()

    def test_timeout_reclaims_only_unverified_attempt(self):
        self.operation.rfile.read1.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.operation.upload(self.row,0,self.entry)
        self.assertFalse(self.partial.exists())
        self.assertEqual(self.store.get(self.task)['state'],'receiving')
        self.assertTrue((self.store.root/'versions'/self.f['task']/'0').is_file())
        self.assertFalse((self.partial.parent/'0').exists())

    def test_changed_partial_identity_is_preserved(self):
        active=[False];original=Path.lstat
        def fail(*args):active[0]=True;raise TimeoutError()
        def changed(path):
            value=original(path)
            if active[0] and path==self.partial:
                return SimpleNamespace(st_dev=value.st_dev,st_ino=value.st_ino+1,
                                       st_mode=value.st_mode,st_size=value.st_size)
            return value
        self.operation.rfile.read1.side_effect=fail
        with patch.object(Path,'lstat',changed):
            with self.assertRaisesRegex(ValueError,'partial-cleanup-ownership'):
                self.operation.upload(self.row,0,self.entry)
        self.assertTrue(self.partial.exists())


if __name__=='__main__':unittest.main()
