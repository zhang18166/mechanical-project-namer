# SPDX-License-Identifier: GPL-3.0-only
"""Target-bound TLS transport. No redirects, implicit proxy, or HTTP fallback."""
import hashlib
import http.client
import json
import os
import socket
import ssl
import time
import uuid
from urllib.parse import urlsplit

from .publish_protocol import MAX_JSON, ProtocolError, safe, uid
from .diagnostics import trace_id


class Target:
    def __init__(self, config):
        self.config = dict(config)
        uid(config['service'])
        self.context = None
        if set(config) == {'url', 'service', 'ca'}:
            u = urlsplit(config['url'])
            if (u.scheme != 'https' or u.hostname not in ('localhost', '127.0.0.1')
                    or u.username or u.password or u.path not in ('', '/') or u.query or u.fragment
                    or not u.port or not 1024 <= u.port <= 65535):
                raise ProtocolError('https-loopback-target-required')
            self.host, self.port = u.hostname, u.port
            ca = safe(config['ca'])
            if not ca.is_file() or ca.stat().st_size > 1024**2:
                raise ProtocolError('target-ca-required')
            pem = ca.read_bytes()
            self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            self.context.minimum_version = ssl.TLSVersion.TLSv1_2
            self.context.load_verify_locations(cadata=pem.decode('ascii'))
            self.identity = hashlib.sha256((f'https://{self.host}:{self.port}/|'+config['service']).encode()+hashlib.sha256(pem).digest()).hexdigest()
        elif set(config) == {'port', 'service'}:
            # Historical fixtures must explicitly opt into isolated plaintext.
            from .development import is_development
            if not (is_development() or os.environ.get('MW_ALLOW_ISOLATED_HTTP') == '1'):
                raise ProtocolError('explicit-isolated-http-required')
            if type(config['port']) is not int or not 1 <= config['port'] <= 65535:
                raise ProtocolError('connection-config')
            self.host, self.port = '127.0.0.1', config['port']
            self.identity = None  # Retain historical ledger keys only for old fixtures.
        else:
            raise ProtocolError('connection-config')

    def connection(self, *, timeout=10, diagnostic=None, scenario=None):
        if not 0 < timeout <= 10:
            raise ProtocolError('connection-budget')
        if self.context is None:
            return http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        deadline = time.monotonic()+timeout
        correlation = trace_id(scenario) or (uuid.uuid4().hex if diagnostic else '')
        phase, raw = 'tcp', None
        c = http.client.HTTPSConnection(self.host, self.port, timeout=timeout, context=self.context)

        def remaining():
            left = deadline-time.monotonic()
            if left <= 0:
                raise TimeoutError('public-health-timeout')
            return left

        def event(name, **values):
            if diagnostic:
                diagnostic.emit(name, trace=correlation, phase=phase, **values)

        try:
            event('client-tcp-start')
            # Both accepted names denote this loopback address. Avoid DNS or a
            # separate timeout for each address; keep the original TLS name.
            raw = socket.create_connection(('127.0.0.1', self.port), timeout=remaining())
            event('client-tcp-connected', local_port=raw.getsockname()[1], remote_port=self.port)
            phase = 'tls'
            raw.settimeout(remaining())
            c.sock = self.context.wrap_socket(raw, server_hostname=self.host)
            raw = None
            c._workbench_socket = c.sock
            event('client-tls-verified')
            phase = 'health'
            c.sock.settimeout(remaining())
            c.request('GET', '/health', headers={'X-Workbench-Trace': correlation} if correlation else {})
            c.sock.settimeout(remaining())
            r = c.getresponse()
            event('client-health-headers', status=r.status)
            b = response_bytes(c, r, deadline=deadline)
            if r.status != 200 or len(b) > MAX_JSON:
                raise ProtocolError('service-verification-failed')
            v = json.loads(b)
            if v.get('service') != self.config['service'] or v.get('accounts') is not True or v.get('protocol') != 1:
                raise ProtocolError('service-identity')
            # No automatic reconnection between unauthenticated UUID check and
            # credentials: the very same front-end TLS connection must survive.
            if c.sock is None:
                raise ProtocolError('verified-connection-closed')
            c._workbench_socket=c.sock
            c._workbench_health=v
            c.auto_open = 0  # Never reconnect behind a completed UUID check.
            event('client-identity-verified', ready=True)
            return c
        except ssl.SSLCertVerificationError:
            event('client-failed', code='tls-certificate-rejected', ready=False)
            c.close()
            raise ProtocolError('tls-certificate-rejected') from None
        except Exception as exc:
            code = phase+'-timeout' if isinstance(exc, TimeoutError) else 'service-identity' if isinstance(exc, ProtocolError) and str(exc)=='service-identity' else phase+'-failed'
            event('client-failed', code=code, ready=False)
            c.close()
            raise
        finally:
            if raw is not None:
                raw.close()

    def public_ready(self, *, attempts=2, budget=10, diagnostic=None, scenario=None):
        """Only a public GET may retry: <=2 fresh TLS/UUID checks in <=10s.

        Certificate, UUID, malformed health and HTTP failures are terminal.
        This method never performs login, management, file or commit requests.
        """
        if type(attempts) is not int or not 1 <= attempts <= 2 or not 0 < budget <= 10:
            raise ProtocolError('readiness-budget')
        if self.context is None:
            raise ProtocolError('https-readiness-required')
        start = time.monotonic()
        deadline = start+budget
        correlation = trace_id(scenario) or uuid.uuid4().hex
        for attempt in range(1, attempts+1):
            left = deadline-time.monotonic()
            if left <= 0:
                raise TimeoutError('public-health-timeout')
            try:
                c = self.connection(timeout=left/(attempts-attempt+1), diagnostic=diagnostic, scenario=correlation)
                try:
                    result = dict(health=c._workbench_health, attempts=attempt, verified=True,
                                  seconds=time.monotonic()-start)
                finally:
                    c.close()
                if diagnostic:
                    diagnostic.emit('client-public-ready', trace=correlation, attempt=attempt, ready=True)
                return result
            except OSError:
                if diagnostic:
                    diagnostic.emit('client-transient', trace=correlation, attempt=attempt, ready=False)
                if attempt==attempts or time.monotonic() >= deadline:
                    raise
                time.sleep(min(.05, max(0, deadline-time.monotonic())))


def response_bytes(connection,response,limit=MAX_JSON,*,deadline=None):
    """Bound JSON response bytes and total/idle time, including a slow trickle."""
    out=bytearray();deadline=min(deadline,time.monotonic()+10) if deadline is not None else time.monotonic()+10
    while True:
        if response.isclosed() is True:return bytes(out)
        left=deadline-time.monotonic()
        if left<=0:raise ProtocolError('response-timeout')
        sock=connection.sock or getattr(connection,'_workbench_socket',None)
        if sock:sock.settimeout(min(10,left))
        b=response.read1(min(65536,limit+1-len(out)))
        if not b:return bytes(out)
        out.extend(b)
        if len(out)>limit:raise ProtocolError('response-limit')
