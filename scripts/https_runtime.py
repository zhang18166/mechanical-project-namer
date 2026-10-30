# SPDX-License-Identifier: GPL-3.0-only
"""Private Linux Nginx/ASGI lifecycle; never installs a service or changes networking."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid
from mechanical_project_namer.account_server import external
from mechanical_project_namer.publish_protocol import data, ProtocolError


def quoted(path):
    s=str(external(path))
    if not re.fullmatch(r'/[a-zA-Z0-9_/.-]+',s):
        raise ProtocolError('private-runtime-ascii-path-required')
    return '"'+s+'"'


def prepare(root, config, nginx, port, cert, key, ca, tls_library_dir=None):
    root=external(root)
    if not 1024<=port<=65535 or root.exists():raise ProtocolError('new-private-runtime-required')
    root.mkdir(mode=0o700,parents=True)
    for part in ('client-temp','proxy-temp','fastcgi-temp','uwsgi-temp','scgi-temp'):(root/part).mkdir(mode=0o700)
    cfg=json.loads(external(config).read_bytes())
    socket=root/'application.sock'
    if len(str(socket).encode())>100:raise ProtocolError('unix-socket-path-limit')
    owner=dict(kind='workbench-private-https-v1',id=str(uuid.uuid4()),config=str(external(config)),
               nginx=str(external(nginx)),port=port,service=cfg['service'],socket=str(socket),
               cert=str(external(cert)),key=str(external(key)),ca=str(external(ca)),
               tls_library_dir=str(external(tls_library_dir)) if tls_library_dir else None)
    for p in (root/'owner.json',):p.write_bytes(data(owner));p.chmod(0o600)
    text=f'''daemon off;
master_process off;
worker_processes 1;
worker_rlimit_nofile 128;
pid {quoted(root/'nginx.pid')};
error_log /dev/null crit;
events {{ worker_connections 64; }}
http {{
    access_log off;
    server_tokens off;
    client_header_timeout 5s;
    client_body_timeout 10s;
    send_timeout 10s;
    keepalive_timeout 2s;
    keepalive_requests 100;
    client_header_buffer_size 1k;
    large_client_header_buffers 2 8k;
    client_max_body_size 16m;
    client_body_buffer_size 16k;
    client_body_temp_path {quoted(root/'client-temp')};
    proxy_temp_path {quoted(root/'proxy-temp')};
    fastcgi_temp_path {quoted(root/'fastcgi-temp')};
    uwsgi_temp_path {quoted(root/'uwsgi-temp')};
    scgi_temp_path {quoted(root/'scgi-temp')};
    limit_conn_zone $binary_remote_addr zone=connections:64k;
    limit_req_zone $binary_remote_addr zone=requests:64k rate=64r/s;
    server {{
        listen 127.0.0.1:{port} ssl;
        server_name 127.0.0.1;
        ssl_certificate {quoted(cert)};
        ssl_certificate_key {quoted(key)};
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_session_tickets off;
        ssl_session_cache off;
        if ($http_host != "127.0.0.1:{port}") {{ return 400; }}
        if ($http_origin != "") {{ return 403; }}
        if ($request_method !~ ^(GET|POST|PUT|PATCH)$) {{ return 405; }}
        limit_conn connections 4;
        limit_conn_status 429;
        limit_req zone=requests burst=32 nodelay;
        limit_req_status 429;
        location ~ ^/(health|auth|admin|projects|tasks|info)(/|$) {{
            proxy_http_version 1.1;
            proxy_request_buffering off;
            proxy_buffering off;
            proxy_max_temp_file_size 0;
            proxy_cache off;
            proxy_next_upstream off;
            proxy_connect_timeout 2s;
            proxy_send_timeout 10s;
            proxy_read_timeout 35s;
            proxy_set_header Host $http_host;
            proxy_set_header Connection "";
            proxy_set_header Forwarded "";
            proxy_set_header X-Forwarded-For "";
            proxy_set_header X-Forwarded-Host "";
            proxy_set_header X-Real-IP "";
            proxy_set_header X-Forwarded-Proto https;
            proxy_pass http://unix:{socket};
            add_header Cache-Control "no-store" always;
            add_header X-Content-Type-Options "nosniff" always;
        }}
        location / {{ return 404; }}
    }}
}}
'''
    (root/'nginx.conf').write_text(text)
    (root/'nginx.conf').chmod(0o600)
    return owner


def owned(root):
    root=external(root)
    s=root.stat()
    if s.st_uid!=os.geteuid() or s.st_mode & 0o077:raise ProtocolError('private-runtime-required')
    v=json.loads((root/'owner.json').read_bytes())
    if v.get('kind')!='workbench-private-https-v1' or v['socket']!=str(root/'application.sock'):
        raise ProtocolError('runtime-ownership')
    return root,v


def stop(root):
    root,v=owned(root);ready=json.loads((root/'ready.json').read_bytes())
    for role in ('proxy','application'):
        pid=ready[role]
        proc=Path('/proc')/str(pid)
        if not proc.exists():continue
        argv=(proc/'cmdline').read_bytes()
        marker=str(root/'nginx.conf') if role=='proxy' else str(root/'app-ready.json')
        if proc.stat().st_uid!=os.geteuid() or marker.encode() not in argv:
            raise ProtocolError('runtime-process-ownership')
        os.kill(pid,signal.SIGTERM)
    end=time.monotonic()+40
    while time.monotonic()<end:
        if not any((Path('/proc')/str(ready[r])).exists() for r in ('proxy','application')):return
        time.sleep(.05)
    raise ProtocolError('runtime-stop-timeout')


def run(root, control=None):
    root,v=owned(root)
    if (root/'ready.json').exists() or (root/'app-ready.json').exists():
        raise ProtocolError('new-runtime-required')
    args=[sys.executable,'-m','mechanical_project_namer.asgi_server','--config',v['config'],
          '--socket',v['socket'],'--host','127.0.0.1:'+str(v['port']),'--ready',str(root/'app-ready.json')]
    if control:args+=['--isolated-test','--test-control',str(external(control))]
    app=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    proxy=None
    try:
        end=time.monotonic()+15
        while not (root/'app-ready.json').exists():
            if app.poll() is not None or time.monotonic()>end:raise ProtocolError('application-start-failed')
            time.sleep(.02)
        # No system paths/default config. All logs/temp/pid use this prefix.
        proxy_env=dict(os.environ)
        if v.get('tls_library_dir'):proxy_env['LD_LIBRARY_PATH']=v['tls_library_dir']
        proxy=subprocess.Popen([v['nginx'],'-e','/dev/null','-p',str(root)+'/', '-c',str(root/'nginx.conf')],
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=proxy_env)
        from mechanical_project_namer.transport import Target
        target=Target(dict(url='https://127.0.0.1:'+str(v['port']),service=v['service'],ca=v['ca']))
        while True:
            if proxy.poll() is not None or time.monotonic()>end:raise ProtocolError('proxy-start-failed')
            try:
                c=target.connection();c.close();break
            except OSError:time.sleep(.05)
        ready=dict(instance=v['id'],application=app.pid,proxy=proxy.pid,supervisor=os.getpid(),service=v['service'],port=v['port'])
        with (root/'ready.json').open('xb') as out:out.write(data(ready))
        def terminate(*args):raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM,terminate)
        while app.poll() is None and proxy.poll() is None:time.sleep(.1)
    finally:
        for proc in (proxy,app):
            if proc and proc.poll() is None:
                proc.terminate()
                try:proc.wait(40)
                except subprocess.TimeoutExpired:raise ProtocolError('runtime-child-stop-timeout')


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','run','stop','check'])
    p.add_argument('--runtime',type=Path,required=True)
    for name in ('config','nginx','cert','key','ca','control','tls-library-dir'):p.add_argument('--'+name,type=Path)
    p.add_argument('--port',type=int)
    a=p.parse_args()
    if os.name=='nt' or os.geteuid()==0:raise ProtocolError('nonprivileged-linux-required')
    os.umask(0o077)
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,512*1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    if a.action=='prepare':prepare(a.runtime,a.config,a.nginx,a.port,a.cert,a.key,a.ca,a.tls_library_dir)
    elif a.action=='run':run(a.runtime,a.control)
    elif a.action=='stop':stop(a.runtime)
    else:
        _,v=owned(a.runtime)
        from mechanical_project_namer.account_client import AccountSession
        print(data(AccountSession(dict(url='https://127.0.0.1:'+str(v['port']),service=v['service'],ca=v['ca'])).verify_service()).decode())


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:pass
    except Exception:
        print('isolated-https-operation-failed',file=sys.stderr);raise SystemExit(2)
