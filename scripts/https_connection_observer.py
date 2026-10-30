# SPDX-License-Identifier: GPL-3.0-only
"""Bounded ordinary-user /proc observer for one private HTTPS test instance.

No packet capture, socket destruction, credentials, or system configuration.
Linux timestamps share one monotonic domain. Windows keeps its own timeline.
"""
import json
import os
from pathlib import Path
import sys
import time

from scripts.https_runtime import owned, processes, socket_rows
from mechanical_project_namer.diagnostics import trace_id


def state(pid):
    proc=Path('/proc')/str(pid)
    try:
        if proc.stat().st_uid!=os.geteuid():raise ValueError('observer-process-owner')
        values=dict(line.split(':',1) for line in (proc/'status').read_text().splitlines() if ':' in line)
        return dict(pid=pid,alive=True,fd=len(list((proc/'fd').iterdir())),threads=int(values['Threads']))
    except (FileNotFoundError,ProcessLookupError):
        return dict(pid=pid,alive=False,fd=0,threads=0)


def observe(job):
    scenario=trace_id(job['scenario'])
    seconds=job['seconds']
    if not scenario or not 1<=seconds<=100:raise ValueError('observer-budget')
    root,owner=owned(Path(job['runtime']))
    ready=json.loads((root/'ready.json').read_bytes());processes(root,owner,ready)
    output=root/('connections-'+scenario+'.jsonl')
    stop=root/('observer-stop-'+scenario)
    start=time.monotonic();known=set();samples=0;size=0;capped=False
    descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'wb',buffering=0) as stream:
        print(json.dumps(dict(observer_pid=os.getpid(),scenario=scenario,clock_domain='linux-monotonic')),flush=True)
        while time.monotonic()-start<seconds:
            if stop.exists():
                if stop.read_text()!=scenario:raise ValueError('observer-stop-owner')
                break
            app,proxy=state(ready['application']),state(ready['proxy'])
            rows=socket_rows(proxy['pid'] if proxy['alive'] else os.getpid(),owner['port'])
            # Do not repeatedly dump old TIME_WAIT rows from fixture setup.
            # Once observed as owned, retain that exact tuple after FD release.
            for row in rows:
                if row['owned'] and row['state']!='0A':
                    known.add((row['remote_address'],row['remote_port']))
            rows=[r for r in rows if (r['remote_address'],r['remote_port']) in known]
            value=dict(event='socket-sample',scenario=scenario,clock_domain='linux-monotonic',
                       seconds=time.monotonic()-start,monotonic_ns=time.monotonic_ns(),
                       application=app,proxy=proxy,connections=rows)
            payload=json.dumps(value,separators=(',',':')).encode()+b'\n'
            if size+len(payload)>4*1024**2:
                capped=True;break
            stream.write(payload);size+=len(payload);samples+=1
            time.sleep(.1)
    print(json.dumps(dict(scenario=scenario,samples=samples,bytes=size,capped=capped,
                          seconds=time.monotonic()-start)),flush=True)


if __name__=='__main__':
    try:observe(json.loads(sys.stdin.buffer.readline(8192)))
    except Exception:
        print('isolated-connection-observer-failed',file=sys.stderr);raise SystemExit(2)
