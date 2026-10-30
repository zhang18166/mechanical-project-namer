# SPDX-License-Identifier: GPL-3.0-only
"""Single-worker, private-UDS ASGI transport for the existing account service.

No HTTP parser, forwarding HTTPServer, alternative auth/state machine, or Qt.
One admitted business operation is deliberate: the shared Store actor and
filesystem/SQLite commit boundaries retain their proven serial semantics.
Other requests receive bounded 503, rather than an unbounded executor queue.
"""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import ExitStack
from email.message import Message
import json
import os
from pathlib import Path
import re
import time
import uuid
from types import SimpleNamespace

from .account_server import AccountService, external
from .accounts import AccountStore
from .library_lock import file_lease
from .publish_protocol import ProtocolError, MAX_FILE, data, uid
from .publish_server import TestControl
from .diagnostics import Trace, trace_id, publish_marker

MAX_INDEX = 64*1024**2


class Exchange(AccountService):
    """Transport primitives over bounded ASGI receive/send with backpressure."""
    def __init__(self, server, scope, receive, send, loop):
        self.server, self.scope = server, scope
        self.receive, self.send, self.loop = receive, send, loop
        self.path = scope['raw_path'].decode('ascii')
        if scope.get('query_string'):
            self.path += '?'
        self.headers = Message()
        for k, v in scope['headers']:
            self.headers[k.decode('ascii')] = v.decode('latin1')
        self.rfile = self.wfile = self.connection = self
        self.timeout, self.deadline = 10, time.monotonic() + 30
        self.pending = bytearray()
        self.eof = self.started = self.closed = self.close_connection = False
        self.response_headers = []
        self.received = 0
        self.sent = 0
        self.trace = trace_id(self.headers.get('X-Workbench-Trace'))
        if not self.trace and server.diagnostic:
            self.trace = uuid.uuid4().hex
        self.phase = 'request'

    def call(self, coroutine):
        remaining = min(self.timeout, self.deadline-time.monotonic())
        if self.closed or remaining <= 0:
            coroutine.close()
            if self.server.diagnostic:
                self.server.diagnostic.emit('app-timeout',trace=self.trace,phase=self.phase,
                                            code='closed' if self.closed else 'total-deadline',
                                            received=self.received,sent=self.sent)
            raise TimeoutError('transport-timeout')
        f = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return f.result(remaining)
        except FutureTimeout:
            f.cancel()
            if self.server.diagnostic:
                self.server.diagnostic.emit('app-timeout', trace=self.trace, phase=self.phase,
                                            received=self.received, sent=self.sent)
            raise TimeoutError('transport-timeout') from None

    def settimeout(self, seconds):
        self.timeout = min(10, seconds)

    def close(self):
        self.closed = True

    def read1(self, n):
        self.phase = 'receive'
        if not self.pending and not self.eof:
            v = self.call(self.receive())
            if v['type'] == 'http.disconnect':
                raise ConnectionError('transport-disconnected')
            self.pending.extend(v.get('body', b''))
            self.eof = not v.get('more_body', False)
        b = bytes(self.pending[:n])
        del self.pending[:n]
        self.received += len(b)
        if self.received > MAX_FILE:
            raise ProtocolError('body-limit')
        return b

    def send_response(self, status):
        if self.started:
            raise ConnectionError('response-already-started')
        self.status = status

    def send_header(self, name, value):
        self.response_headers.append((name.lower().encode('ascii'), value.encode('ascii')))

    def end_headers(self):
        self.response_headers += [(b'cache-control', b'no-store'), (b'connection', b'close'),
                                  (b'x-content-type-options', b'nosniff')]
        self.call(self.send(dict(type='http.response.start', status=self.status, headers=self.response_headers)))
        self.started = True

    def write(self, b):
        # Existing service writes chunks <=64KiB. ASGI send awaits transport drain.
        self.phase = 'send'
        self.call(self.send(dict(type='http.response.body', body=b, more_body=True)))
        self.sent += len(b)


class Application:
    def __init__(self, store, expected_host, control=None, diagnostic=None):
        self.server = SimpleNamespace(store=store, expected_host=expected_host, server_port=0,
                                      control=control or TestControl(), fault='', diagnostic=diagnostic)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='business')
        # Start the only worker before accepting requests. A thread-start failure
        # aborts startup, rather than leaving a queued write without a returned
        # Future during request admission.
        self.executor.submit(lambda: None).result(timeout=5)
        self.busy = False

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return
        headers = Message()
        for k, v in scope['headers']:
            headers[k.decode('ascii')] = v.decode('latin1')
        # The only listener is a private Unix socket. Uvicorn proxy_headers=False;
        # identities never derive from a forwarded address supplied by a client.
        valid = (scope['method'] in ('GET', 'POST', 'PUT', 'PATCH')
                 and isinstance(scope.get('raw_path'),bytes) and scope['raw_path'].isascii()
                 and headers.get_all('Host') == [self.server.expected_host]
                 and not headers.get_all('Origin') and not scope.get('query_string')
                 and not any(headers.get_all(k) for k in ('Forwarded', 'X-Forwarded-For', 'X-Real-IP'))
                 and headers.get_all('X-Forwarded-Proto') == ['https'])
        index=self.server.store.index
        full=sum(p.stat().st_size for p in (index,Path(str(index)+'-wal')) if p.exists())>=MAX_INDEX
        if not valid or self.busy or full:
            code='request-origin' if not valid else 'audit-capacity-maintenance-required' if full else 'service-busy-retry'
            if self.server.diagnostic:
                self.server.diagnostic.emit('app-rejected', trace=trace_id(headers.get('X-Workbench-Trace')),
                                            code=code, slot_busy=self.busy)
            b = data({'error': code})
            await send(dict(type='http.response.start', status=503 if valid else 400,
                            headers=[(b'content-length', str(len(b)).encode()), (b'connection', b'close'), (b'cache-control', b'no-store')]))
            await send(dict(type='http.response.body', body=b))
            return
        exchange = Exchange(self.server, scope, receive, send, asyncio.get_running_loop())
        self.busy = True  # No await between check/set: bounded admission, zero queue.
        if self.server.diagnostic:
            self.server.diagnostic.emit('app-request-start', trace=exchange.trace, slot_busy=True,
                                        connection=headers.get('X-Workbench-Connection'))
        try:
            future = asyncio.get_running_loop().run_in_executor(self.executor, exchange.dispatch, scope['method'])
        except BaseException:
            self.busy = False  # No worker was submitted; no lease may remain stuck.
            raise
        # Only actual worker completion releases admission, even if the ASGI
        # request task is cancelled more than once during shutdown/disconnect.
        def completed(done):
            self.busy = False
            if self.server.diagnostic:
                failed = done.cancelled() or done.exception() is not None
                self.server.diagnostic.emit('app-worker-end', trace=exchange.trace, slot_busy=False,
                    received=exchange.received, sent=exchange.sent, status=getattr(exchange, 'status', 0),
                    code='operation-error' if failed else 'completed')
        future.add_done_callback(completed)
        try:
            await asyncio.shield(future)
            if exchange.closed or not exchange.started:
                raise ConnectionError('transport-disconnected')
            await send(dict(type='http.response.body', body=b'', more_body=False))
        finally:
            # ASGI cancellation is not thread cancellation. Never admit another
            # operation until the actual worker has stopped touching task files.
            exchange.closed = True
            if self.server.diagnostic:
                self.server.diagnostic.emit('app-response-end', trace=exchange.trace, slot_busy=self.busy)
            await asyncio.shield(future)

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--socket', type=Path, required=True)
    p.add_argument('--host', required=True)
    p.add_argument('--ready', type=Path, required=True)
    p.add_argument('--test-control', type=Path)
    p.add_argument('--isolated-test', action='store_true')
    p.add_argument('--diagnostics', type=Path)
    a = p.parse_args()
    if os.name == 'nt' or os.geteuid() == 0:
        raise ProtocolError('nonprivileged-linux-required')
    if not re.fullmatch(r'(localhost|127\.0\.0\.1):[1-9][0-9]{3,4}', a.host):
        raise ProtocolError('loopback-host-required')
    os.umask(0o077)
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (512*1024**2, 512*1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    config = json.loads(external(a.config).read_bytes())
    socket = external(a.socket)
    parent = socket.parent.stat()
    if parent.st_uid != os.geteuid() or parent.st_mode & 0o077 or socket.exists():
        raise ProtocolError('private-new-socket-required')
    if a.test_control and not a.isolated_test:
        raise ProtocolError('isolated-test-required')
    index = external(config['index'])
    lease_root = external(config['lease_root'])
    lease_root.mkdir(parents=True, exist_ok=True)
    with ExitStack() as leases:
        leases.enter_context(file_lease(lease_root/(uid(config['service'])+'.lock'), exclusive=True))
        leases.enter_context(file_lease(index.parent/'.publish-server.lock', exclusive=True))
        diagnostic = Trace(a.diagnostics) if a.diagnostics else None
        app = Application(AccountStore(config), a.host, TestControl(external(a.test_control) if a.test_control else None), diagnostic)
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(app, uds=str(socket), workers=1, loop='asyncio', http='h11',
                 ws='none', lifespan='off', proxy_headers=False, forwarded_allow_ips='', access_log=False,
                 log_config={'version': 1, 'handlers': {'null': {'class': 'logging.NullHandler'}},
                             'loggers': {'uvicorn': {'handlers': ['null'], 'propagate': False}}},
                 limit_concurrency=8, backlog=8, timeout_keep_alive=2, timeout_graceful_shutdown=35,
                 h11_max_incomplete_event_size=16384, server_header=False, date_header=False))

        async def serve():
            async def ready():
                while not server.started:
                    await asyncio.sleep(.02)
                socket.chmod(0o600)
                publish_marker(external(a.ready),dict(pid=os.getpid(), service=config['service'], socket=str(socket)))
                if diagnostic:
                    diagnostic.emit('app-listener-ready', ready=True)
            watcher = asyncio.create_task(ready())
            try:
                await server.serve()
            finally:
                watcher.cancel()
        try:
            asyncio.run(serve())
        finally:
            app.close()
            if diagnostic:
                diagnostic.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('isolated-asgi-service-failed', file=__import__('sys').stderr)
        raise SystemExit(2)
