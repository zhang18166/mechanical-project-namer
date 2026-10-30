# SPDX-License-Identifier: GPL-3.0-only
"""Target-bound TLS transport. No redirects, implicit proxy, or HTTP fallback."""
import hashlib
import http.client
import json
import os
import ssl
import time
from urllib.parse import urlsplit

from .publish_protocol import MAX_JSON, ProtocolError, safe, uid


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

    def connection(self):
        if self.context is None:
            return http.client.HTTPConnection(self.host, self.port, timeout=10)
        c = http.client.HTTPSConnection(self.host, self.port, timeout=10, context=self.context)
        try:
            c.connect()  # Verified handshake before any HTTP secrets/content.
            c.request('GET', '/health')
            r = c.getresponse()
            b = response_bytes(c,r)
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
            return c
        except ssl.SSLCertVerificationError:
            c.close()
            raise ProtocolError('tls-certificate-rejected') from None
        except Exception:
            c.close()
            raise


def response_bytes(connection,response,limit=MAX_JSON):
    """Bound JSON response bytes and total/idle time, including a slow trickle."""
    out=bytearray();deadline=time.monotonic()+10
    while True:
        left=deadline-time.monotonic()
        if left<=0:raise ProtocolError('response-timeout')
        sock=connection.sock or getattr(connection,'_workbench_socket',None)
        if sock:sock.settimeout(min(10,left))
        b=response.read1(min(65536,limit+1-len(out)))
        if not b:return bytes(out)
        out.extend(b)
        if len(out)>limit:raise ProtocolError('response-limit')
