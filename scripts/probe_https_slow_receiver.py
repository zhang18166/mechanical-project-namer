# SPDX-License-Identifier: GPL-3.0-only
"""Diagnostic, not a passing acceptance test: measure the front-end idle/total boundary."""
import argparse
import json
from pathlib import Path
import socket
import time
from scripts.https_harness import fixture
from mechanical_project_namer.publish_protocol import data


def run(root):
    f=fixture(root,large=True);c=None;rows=[]
    try:
        client=f.admin.publisher(f.pa);v=client.publish(f.a.snap,f.task)
        index=next(i for i,e in enumerate(v['manifest']['files']) if e['size']==8*1024**2)
        c=f.admin.target.connection();c.sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4096)
        c.request('GET',f'/tasks/{v["task"]}/files/{index}',headers={'Authorization':'Bearer '+f.admin.token})
        r=c.getresponse();assert r.status==200
        count=0;started=time.monotonic();closed=False
        while time.monotonic()-started<36:
            try:b=r.read1(512)
            except OSError:closed=True;break
            if not b:closed=True;break
            count+=len(b)
            if not rows or time.monotonic()-started-rows[-1]['seconds']>=5:
                native=json.loads(f.service.native("import pathlib,sys,json,os;p=pathlib.Path('/proc')/sys.argv[1];print(json.dumps(dict(fd=len(list((p/'fd').iterdir())),tcp=(p/'net/tcp').read_text())))",str(f.service.proxy_pid)))
                rows.append(dict(seconds=time.monotonic()-started,received=count,proxy=native))
            time.sleep(.2)
        report=dict(status='PARTIAL',scope='diagnostic-not-an-acceptance-pass',seconds=time.monotonic()-started,
                    received=count,total=8*1024**2,client_eof_observed=closed,samples=rows,
                    limitation='Nginx send_timeout is idle time, not a proven absolute front connection lifetime',
                    trusted_client_and_application_have_total_deadlines=True)
        (f.root/'report.json').write_bytes(data(report));f.protect();print('Slow receiver diagnostic recorded, no absolute front-end deadline PASS claimed')
    finally:
        if c:c.close()
        f.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);run(p.parse_args().output)
