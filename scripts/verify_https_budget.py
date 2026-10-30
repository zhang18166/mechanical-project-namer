# SPDX-License-Identifier: GPL-3.0-only
"""Controlled real TLS peer isolates handshake + header cumulative time.

Uses standard-library TLS and HTTP only as a test peer, never as an upstream
for the application. No passwords/tokens/files are sent to this peer.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import threading
import time
import uuid

from mechanical_project_namer.transport import Target


def probe(root, *, budget=1, tls_delay=.6, health_delay=.6):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    certificates=Path(os.environ['MW_HTTPS_TEST_CERTIFICATES'])
    service=str(uuid.uuid4());methods=[];workers=[]
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificates/'valid.pem',certificates/'valid.key')

    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args):pass
        def do_GET(self):
            methods.append('GET')
            time.sleep(health_delay)
            payload=json.dumps(dict(service=service,accounts=True,protocol=1)).encode()
            try:
                self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers()
                self.wfile.write(payload)
            except OSError:pass
        def do_POST(self):methods.append('POST');self.send_error(403)

    class Server(ThreadingHTTPServer):
        daemon_threads=True
        def finish_request(self,request,address):
            workers.append(threading.current_thread())
            time.sleep(tls_delay)
            try:
                with context.wrap_socket(request,server_side=True) as secure:Handler(secure,address,self)
            except OSError:pass
        def handle_error(self,*args):pass

    server=Server(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,kwargs=dict(poll_interval=.02),daemon=True);thread.start()
    target=Target(dict(url='https://127.0.0.1:'+str(server.server_port),service=service,ca=str(certificates/'ca.pem')))
    start=time.monotonic();outcome='verified';connection=None
    try:
        connection=target.connection() if budget is None else target.connection(timeout=budget)
    except TimeoutError:outcome='timeout'
    finally:
        elapsed=time.monotonic()-start
        if connection:connection.close()
        server.shutdown();server.server_close();thread.join(2)
        for worker in workers:worker.join(tls_delay+health_delay+2)
    report=dict(outcome=outcome,seconds=elapsed,budget=budget,tls_delay=tls_delay,health_delay=health_delay,
                methods=methods,credentials_not_sent='POST' not in methods,real_tls=True)
    (root/'probe.json').write_text(json.dumps(report,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=probe(args.output)
    assert result['outcome']=='timeout' and .8<=result['seconds']<1.5,result
    assert result['credentials_not_sent'] and result['methods']==['GET'],result
    (args.output/'report.json').write_text(json.dumps(dict(passed=True,result=result),indent=2))
    print('Real TLS cumulative health deadline verified')
