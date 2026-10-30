# SPDX-License-Identifier: GPL-3.0-only
"""Private Linux Nginx/ASGI lifecycle; never installs a service or changes networking."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
from mechanical_project_namer.account_server import external
from mechanical_project_namer.publish_protocol import data, ProtocolError
from mechanical_project_namer.diagnostics import Trace, ProxyTrace, publish_marker


def diagnostic_config(root):
    # No URI, Authorization, request body, username, key or filesystem path is
    # logged. The only client-controlled field is normalized to a 32-hex ID.
    http = f'''map $http_x_workbench_trace $wb_trace {{
        default $request_id;
        "~^[a-f0-9]{{32}}$" $http_x_workbench_trace;
    }}
    log_format wb escape=json '{{"trace":"$wb_trace","connection":"$pid:$connection:$connection_requests","proxy_pid":"$pid","local_port":"$server_port","remote_port":"$remote_port","status":"$status","sent":"$bytes_sent","request_bytes":"$request_length","duration":"$request_time"}}';
    access_log syslog:server=unix:{root}/diagnostic.sock,nohostname,tag=wb wb;'''
    headers = '''proxy_set_header X-Workbench-Trace $wb_trace;
            proxy_set_header X-Workbench-Connection "$pid:$connection:$connection_requests";
            add_header X-Workbench-Trace $wb_trace always;
            add_header X-Workbench-Connection "$pid:$connection:$connection_requests" always;'''
    return http, headers


def quoted(path):
    s=str(external(path))
    if not re.fullmatch(r'/[a-zA-Z0-9_/.-]+',s):
        raise ProtocolError('private-runtime-ascii-path-required')
    return '"'+s+'"'


def prepare(root, config, nginx, port, cert, key, ca, tls_library_dir=None, diagnostics=False):
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
               tls_library_dir=str(external(tls_library_dir)) if tls_library_dir else None,
               diagnostics=bool(diagnostics))
    for p in (root/'owner.json',):p.write_bytes(data(owner));p.chmod(0o600)
    http_diagnostic, trace_headers = diagnostic_config(root) if diagnostics else ('access_log off;',
        'proxy_set_header X-Workbench-Trace "";\n            proxy_set_header X-Workbench-Connection "";')
    text=f'''daemon off;
master_process off;
worker_processes 1;
worker_rlimit_nofile 128;
pid {quoted(root/'nginx.pid')};
error_log /dev/null crit;
events {{ worker_connections 64; }}
http {{
    {http_diagnostic}
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
            {trace_headers}
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


def process_start(pid):
    text = (Path('/proc')/str(pid)/'stat').read_text()
    return int(text[text.rfind(')')+2:].split()[19])


def socket_rows(pid, port):
    """Only this owned process/loopback listener; retain unowned closing tuples.

    A TCP row can outlive its FD. `owned` means the current process actually
    holds that socket inode; FIN_WAIT/TIME_WAIT never implies a live worker.
    """
    proc=Path('/proc')/str(pid)
    if proc.stat().st_uid!=os.geteuid():raise ProtocolError('runtime-process-ownership')
    inodes=set()
    for fd in (proc/'fd').iterdir():
        try:link=os.readlink(fd)
        except FileNotFoundError:continue
        if link.startswith('socket:['):inodes.add(link[8:-1])
    rows=[]
    for line in (proc/'net/tcp').read_text().splitlines()[1:]:
        fields=line.split();local,remote=fields[1:3]
        address,local_port=local.split(':')
        if int(local_port,16)!=port or address!='0100007F':continue
        peer,peer_port=remote.split(':');tx,rx=fields[4].split(':')
        rows.append(dict(local_address='127.0.0.1',local_port=port,
                         remote_address=socket.inet_ntoa(bytes.fromhex(peer)[::-1]),remote_port=int(peer_port,16),
                         state=fields[3],inode=fields[9],owned=fields[9] in inodes,
                         send_queue=int(tx,16),receive_queue=int(rx,16)))
    return rows


def processes(root, owner, ready, *, allow_exited=False):
    if (ready.get('instance') != owner['id'] or ready.get('service') != owner['service']
            or ready.get('port') != owner['port']):
        raise ProtocolError('runtime-ready-identity')
    result=[]
    for role in ('proxy','application'):
        pid=ready[role]
        if type(pid) is not int or pid<=1:raise ProtocolError('runtime-process-ownership')
        proc=Path('/proc')/str(pid)
        if not proc.exists() and allow_exited:continue
        marker=str(root/'nginx.conf') if role=='proxy' else str(root/'app-ready.json')
        if (not proc.exists() or proc.stat().st_uid!=os.geteuid()
                or marker.encode() not in (proc/'cmdline').read_bytes()
                or process_start(pid)!=ready.get('starts',{}).get(role)):
            raise ProtocolError('runtime-process-ownership')
        result.append(pid)
    return result


def stop(root):
    root,v=owned(root);ready=json.loads((root/'ready.json').read_bytes())
    pids=processes(root,v,ready,allow_exited=True)
    marker=root/'stop-request.json'
    if marker.exists():
        if json.loads(marker.read_bytes())!={'instance':v['id']}:raise ProtocolError('runtime-stop-ownership')
    else:publish_marker(marker,dict(instance=v['id']))
    for pid in pids:
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:pass
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
    diagnostic=Trace(root/'startup-events.jsonl') if v.get('diagnostics') else None
    collector=None
    if diagnostic:args+=['--diagnostics',str(root/'application-events.jsonl')]
    def event(stage, **values):
        if diagnostic:diagnostic.emit(stage,trace=v['id'].replace('-',''),**values)
    def terminate(*args):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,terminate)
    app=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    event('runtime-app-created',application_pid=app.pid)
    proxy=None
    try:
        end=time.monotonic()+15
        while not (root/'app-ready.json').exists():
            if app.poll() is not None or time.monotonic()>end:raise ProtocolError('application-start-failed')
            time.sleep(.02)
        marker=json.loads((root/'app-ready.json').read_bytes())
        if marker!=dict(pid=app.pid,service=v['service'],socket=v['socket']):raise ProtocolError('application-ready-identity')
        event('runtime-app-alive',application_pid=app.pid)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as probe:
            probe.settimeout(min(1,max(.001,end-time.monotonic())))
            probe.connect(v['socket'])
        event('runtime-unix-ready',ready=True)
        if diagnostic:collector=ProxyTrace(root)
        # No system paths/default config. All logs/temp/pid use this prefix.
        proxy_env=dict(os.environ)
        if v.get('tls_library_dir'):proxy_env['LD_LIBRARY_PATH']=v['tls_library_dir']
        proxy=subprocess.Popen([v['nginx'],'-e','/dev/null','-p',str(root)+'/', '-c',str(root/'nginx.conf')],
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=proxy_env)
        event('runtime-proxy-created',proxy_pid=proxy.pid)
        while True:
            if proxy.poll() is not None or time.monotonic()>end:raise ProtocolError('proxy-start-failed')
            if any(row['owned'] and row['state']=='0A' for row in socket_rows(proxy.pid,v['port'])):break
            time.sleep(.02)
        event('runtime-proxy-listening',proxy_pid=proxy.pid,local_port=v['port'],ready=True)
        from mechanical_project_namer.transport import Target
        target=Target(dict(url='https://127.0.0.1:'+str(v['port']),service=v['service'],ca=v['ca']))
        if proxy.poll() is not None or time.monotonic()>end:raise ProtocolError('proxy-start-failed')
        target.public_ready(budget=min(10,max(.001,end-time.monotonic())),diagnostic=diagnostic,scenario=v['id'].replace('-',''))
        if app.poll() is not None or proxy.poll() is not None:raise ProtocolError('runtime-child-exited')
        ready=dict(instance=v['id'],application=app.pid,proxy=proxy.pid,supervisor=os.getpid(),service=v['service'],port=v['port'],
                   starts=dict(application=process_start(app.pid),proxy=process_start(proxy.pid)),
                   scope='linux-local-tls',unix_verified=True,linux_https_verified=True,windows_verified=False)
        publish_marker(root/'ready.json',ready)
        event('runtime-linux-ready',ready=True)
        while app.poll() is None and proxy.poll() is None:time.sleep(.1)
        event('runtime-child-ended',code='application-ended' if app.poll() is not None else 'proxy-ended',ready=False)
        stop_marker=root/'stop-request.json'
        if not stop_marker.exists() or json.loads(stop_marker.read_bytes())!=dict(instance=v['id']):
            raise ProtocolError('runtime-child-exited')
    except Exception as exc:
        code=str(exc) if isinstance(exc,ProtocolError) and re.fullmatch('[a-z0-9-]{1,64}',str(exc)) else 'runtime-operation-failed'
        event('runtime-failed',code=code,ready=False)
        raise
    finally:
        for role,proc in (('proxy',proxy),('application',app)):
            if proc:event('runtime-child-stop',phase=role,worker_pid=proc.pid,ready=False)
            if proc and proc.poll() is None:
                proc.terminate()
                try:proc.wait(40)
                except subprocess.TimeoutExpired:raise ProtocolError('runtime-child-stop-timeout')
            if proc:event('runtime-child-stopped',phase=role,worker_pid=proc.pid,ready=False)
        event('runtime-stopped',ready=False)
        if collector:collector.close()
        if diagnostic:diagnostic.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','run','stop','check'])
    p.add_argument('--runtime',type=Path,required=True)
    for name in ('config','nginx','cert','key','ca','control','tls-library-dir'):p.add_argument('--'+name,type=Path)
    p.add_argument('--port',type=int)
    p.add_argument('--diagnostics',action='store_true')
    a=p.parse_args()
    if os.name=='nt' or os.geteuid()==0:raise ProtocolError('nonprivileged-linux-required')
    os.umask(0o077)
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,512*1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    if a.action=='prepare':prepare(a.runtime,a.config,a.nginx,a.port,a.cert,a.key,a.ca,a.tls_library_dir,a.diagnostics)
    elif a.action=='run':run(a.runtime,a.control)
    elif a.action=='stop':stop(a.runtime)
    else:
        root,v=owned(a.runtime)
        ready=json.loads((root/'ready.json').read_bytes())
        processes(root,v,ready)
        from mechanical_project_namer.transport import Target
        result=Target(dict(url='https://127.0.0.1:'+str(v['port']),service=v['service'],ca=v['ca'])).public_ready()
        print(data(dict(result['health'],runtime_instance=v['id'],scope='linux-local-tls',current_verified=True,
                        windows_verified=False,attempts=result['attempts'])).decode())


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:pass
    except Exception:
        print('isolated-https-operation-failed',file=sys.stderr);raise SystemExit(2)
