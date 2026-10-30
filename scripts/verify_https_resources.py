# SPDX-License-Identifier: GPL-3.0-only
"""Fixed-code 30 minute HTTPS load, native Windows and Linux process sampling."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import time
from scripts.https_harness import fixture,ROOT
from scripts.publish_metrics import sample
from mechanical_project_namer.account_client import AccountSession
from mechanical_project_namer.publish_protocol import data,digest


def native(service):
    code='''import json,pathlib,sys,os,re
out={}
for role,pid in [('application',sys.argv[1]),('proxy',sys.argv[2])]:
 p=pathlib.Path('/proc')/pid
 s=dict(line.split(':',1) for line in (p/'status').read_text().splitlines() if ':' in line)
 m=dict(line.split(':',1) for line in (p/'smaps_rollup').read_text().splitlines() if ':' in line)
 links=[os.readlink(x) for x in (p/'fd').iterdir()]
 inodes={v[8:-1] for v in links if v.startswith('socket:[')}
 tcp=[line.split() for line in (p/'net/tcp').read_text().splitlines()[1:]]
 out[role]=dict(pid=int(pid),rss_bytes=int(s['VmRSS'].split()[0])*1024,
  private_bytes=(int(m['Private_Clean'].split()[0])+int(m['Private_Dirty'].split()[0]))*1024,
  fd=len(links),threads=int(s['Threads']),sockets=len(inodes),tcp=sum(row[9] in inodes for row in tcp))
files=[p for root in sys.argv[3:] for p in pathlib.Path(root).rglob('*') if p.is_file()]
out['disk']=dict(files=len(files),bytes=sum(p.stat().st_size for p in files),
 partial=sum(p.name.endswith(('.partial','.copy')) for p in files),
 temp=sum('client-temp' in p.parts or 'proxy-temp' in p.parts for p in files))
print(json.dumps(out))'''
    return json.loads(service.native(code,str(service.pid),str(service.proxy_pid),service.data_root,service.runtime))


def client_disk(root):
    """Only this finite fictional client/evidence tree; never production paths."""
    files=[p for p in root.rglob('*') if p.is_file()]
    return dict(files=len(files),bytes=sum(p.stat().st_size for p in files),
                partial=sum(p.name.endswith(('.partial','.copy','.tmp')) for p in files),
                log_bytes=sum(p.stat().st_size for p in files if p.suffix in ('.log','.jsonl')),
                ledger_bytes=sum(p.stat().st_size for p in files if '.publish-state' in p.parts),
                database_bytes=sum(p.stat().st_size for p in files if p.suffix=='.sqlite'))


def run(root,seconds):
    if seconds<1800:raise ValueError('At least 1800 uninterrupted seconds required')
    root=Path(root)
    budget=dict(seconds=seconds,warmup=300,interval=10,private_span_MiB=16,positive_private_slope_MiB_per_min=.25,
                windows_ws_span_MiB=32,linux_rss_span_MiB=24,fd_handle_span=6,thread_span=2,connection_span=6,
                disk_growth_MiB=8,client_disk_growth_MiB=8,temp_files=0,download_GiB=4,fixed_versions=1,no_restart=True,no_gc=True,no_cleanup=True)
    (root.parent/(root.name+'-budget.json')).write_bytes(data(budget))
    source=list((ROOT/'mechanical_project_namer').rglob('*.py'))+list(ROOT.glob('requirements-*.lock'))
    source+=[ROOT/'scripts'/n for n in ('https_runtime.py','https_harness.py','verify_https_resources.py','publish_metrics.py','auth_harness.py','publish_harness.py')]
    hashes={str(p):digest(p) for p in source}
    f=fixture(root,large=True);records=[];errors=[];rejects=0;downloaded=0
    (root/'budget.json').write_bytes(data(budget));(root/'code.json').write_bytes(data(hashes))
    try:
        state=f.admin.publisher(f.pa).publish(f.a.snap,f.task)
        f.admin.publisher(f.pa).download(state['task'],root/'initial-download')
        index=next(i for i,e in enumerate(state['manifest']['files']) if e['size']==1024**2)
        item=state['manifest']['files'][index];pids=(os.getpid(),f.service.pid,f.service.proxy_pid)
        start=time.monotonic();cycles=0
        while time.monotonic()-start<seconds:
            cycle=time.monotonic();s=AccountSession(f.service.connection)
            try:
                s.login('fake-admin',f.service.current)
                assert len(s.projects())==2 and len(s.versions(f.pa))==1
                assert s.publisher(f.pa).publish(f.a.snap,f.task)['healthy']
                c=s.target.connection();h=hashlib.sha256();count=0
                try:
                    c.request('GET',f'/tasks/{state["task"]}/files/{index}',headers={'Authorization':'Bearer '+s.token})
                    r=c.getresponse();assert r.status==200
                    while b:=r.read1(65536):h.update(b);count+=len(b)
                finally:c.close()
                assert count==item['size'] and h.hexdigest()==item['sha256'];downloaded+=count
                s.logout()
                # Expected rejection, counted independently of successful load.
                c=s.target.connection()
                try:
                    c.request('GET','/projects');r=c.getresponse();assert r.status==403;r.read();rejects+=1
                finally:c.close()
                assert (os.getpid(),f.service.pid,f.service.proxy_pid)==pids
                row=dict(seconds=time.monotonic()-start,cycle=cycles,windows=sample(os.getpid()),linux=native(f.service),client_disk=client_disk(root))
                records.append(row)
                with (root/'series.jsonl').open('ab') as out:out.write(data(row)+b'\n')
            except Exception as exc:
                errors.append(dict(cycle=cycles,type=type(exc).__name__));(root/'errors.json').write_bytes(data(errors));raise
            cycles+=1
            time.sleep(max(0,min(10-(time.monotonic()-cycle),seconds-(time.monotonic()-start))))
        elapsed=time.monotonic()-start;warm=[r for r in records if r['seconds']>=300];assert len(warm)>=100 and elapsed>=1800
        resources={}
        for role in ('windows','application','proxy'):
            rows=[r['windows'] if role=='windows' else r['linux'][role] for r in warm]
            def span(key):return max(r[key] for r in rows)-min(r[key] for r in rows)
            xs=[r['seconds']/60 for r in warm];ys=[r['private_bytes']/1024**2 for r in rows];mx,my=statistics.mean(xs),statistics.mean(ys)
            trend=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/sum((x-mx)**2 for x in xs)
            result=dict(private_span_MiB=span('private_bytes')/1024**2,private_slope_MiB_min=trend,
                        working_set_or_rss_span_MiB=span('working_set' if role=='windows' else 'rss_bytes')/1024**2,
                        handles_or_fd_span=span('handles' if role=='windows' else 'fd'),thread_span=span('threads'),
                        connection_span=span('tcp_count' if role=='windows' else 'tcp'),first=rows[0],last=rows[-1])
            resources[role]=result
            assert result['private_span_MiB']<=16 and trend<=.25,result
            assert result['working_set_or_rss_span_MiB']<=(32 if role=='windows' else 24),result
            assert result['handles_or_fd_span']<=6 and result['thread_span']<=2 and result['connection_span']<=6,result
        growth=(records[-1]['linux']['disk']['bytes']-records[0]['linux']['disk']['bytes'])/1024**2
        client_growth=(records[-1]['client_disk']['bytes']-records[0]['client_disk']['bytes'])/1024**2
        assert client_growth<=budget['client_disk_growth_MiB']
        assert growth<=8 and downloaded<=4*1024**3
        assert all(r['linux']['disk']['partial']==r['linux']['disk']['temp']==0 for r in records)
        assert all(r['client_disk']['partial']==0 for r in records)
        assert max(r['client_disk']['files'] for r in records)-min(r['client_disk']['files'] for r in records)<=2
        assert max(r['linux']['disk']['files'] for r in records)-min(r['linux']['disk']['files'] for r in records)<=2
        assert all(digest(Path(p))==v for p,v in hashes.items()),'Source changed during final load'
        f.protect()
        report=dict(passed=True,seconds=elapsed,cycles=cycles,normal_errors=errors,expected_rejections=rejects,
                    resources=resources,disk_growth_MiB=growth,client_disk_growth_MiB=client_growth,
                    client_disk_first=records[0]['client_disk'],client_disk_last=records[-1]['client_disk'],
                    download_bytes=downloaded,pids=pids,source_hashes_unchanged=True,
                    fixed_dataset_protected=True,environment=f.service.info())
        (root/'report.json').write_bytes(data(report));print('Final HTTPS resource acceptance passed')
    finally:f.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--seconds',type=int,default=1800)
    a=p.parse_args();run(a.output,a.seconds)
