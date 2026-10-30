# SPDX-License-Identifier: GPL-3.0-only
"""At least 30 real minutes on the explicitly selected isolated Linux service.

Fixed accounts/one version; downloads streamed to a hash, not new directories.
No restarts, forced GC or cleanup. Native Linux metrics stay native Linux metrics.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import statistics
import time
from scripts.auth_harness import AuthFixture,ROOT
from mechanical_project_namer.account_client import AccountSession
from mechanical_project_namer.publish_protocol import data,digest


def native_sample(service):
    code="""import json,sys,os,pathlib
p='/proc/'+sys.argv[1]
s=dict(line.split(':',1) for line in open(p+'/status') if ':' in line)
m=dict(line.split(':',1) for line in open(p+'/smaps_rollup') if ':' in line)
root=pathlib.Path(sys.argv[2]);files=[q for q in root.rglob('*') if q.is_file()]
v=dict(pid=int(sys.argv[1]),rss_bytes=int(s['VmRSS'].split()[0])*1024,
 private_bytes=(int(m['Private_Clean'].split()[0])+int(m['Private_Dirty'].split()[0]))*1024,
 threads=int(s['Threads']),fd=len(os.listdir(p+'/fd')),disk_files=len(files),disk_bytes=sum(q.stat().st_size for q in files),
 partial_files=sum(q.name.endswith('.partial') or q.name.endswith('.copy') for q in files))
print(json.dumps(v))"""
    return json.loads(service.native(code,str(service.pid),service.data_root))


def run(root,seconds):
    if seconds<1800:raise ValueError('This acceptance requires at least 1800 seconds')
    f=AuthFixture(root,'linux',large=True);records=[];errors=[]
    budget=dict(duration_seconds=seconds,warmup_seconds=300,interval_seconds=10,max_private_span_MiB=16,
                max_positive_private_slope_MiB_per_minute=.25,max_rss_span_MiB=24,max_fd_span=4,max_threads_span=2,
                max_audit_disk_growth_MiB=8,max_download_GiB=4,fixed_versions=1,no_restart=True,no_forced_gc=True,no_cleanup=True)
    (f.root/'budget.json').write_bytes(data(budget))
    sources=list((ROOT/'mechanical_project_namer').rglob('*.py'))+[ROOT/'requirements-server.lock',ROOT/'scripts/verify_server_resources.py']
    fingerprints={str(p):digest(p) for p in sources}
    (f.root/'code.json').write_bytes(data(fingerprints))
    try:
        version=f.admin.publisher(f.pa).publish(f.a.snap,f.task)
        f.admin.publisher(f.pa).download(version['task'],f.root/'initial-download')
        for item in version['manifest']['files']:
            assert digest(f.root/'initial-download'/item['path'])=={k:item[k] for k in ('size','sha256')}
        index=next(i for i,item in enumerate(version['manifest']['files']) if item['size']==1024**2)
        entry=version['manifest']['files'][index];started=time.monotonic();cycles=0;downloaded=0;pid=f.service.pid
        while time.monotonic()-started<seconds:
            cycle=time.monotonic()
            session=AccountSession(f.service.connection)
            try:
                session.login('fake-admin',f.service.current)
                assert len(session.projects())==2
                assert len(session.versions(f.pa))==1
                assert session.publisher(f.pa).publish(f.a.snap,f.task)['healthy']
                connection=http.client.HTTPConnection('127.0.0.1',f.service.connection['port'],timeout=10)
                h=hashlib.sha256();count=0
                try:
                    connection.request('GET',f'/tasks/{version["task"]}/files/{index}',headers={'Authorization':'Bearer '+session.token})
                    response=connection.getresponse();assert response.status==200
                    while block:=response.read(65536):h.update(block);count+=len(block)
                finally:connection.close()
                assert count==entry['size'] and h.hexdigest()==entry['sha256']
                downloaded+=count;assert downloaded<=budget['max_download_GiB']*1024**3
                session.logout();assert f.service.pid==pid
            except Exception as exc:
                errors.append(dict(cycle=cycles,type=type(exc).__name__))
                (f.root/'errors.json').write_bytes(data(errors));raise
            finally:
                if session.token:session.logout()
            row=dict(elapsed=time.monotonic()-started,cycle=cycles,server=native_sample(f.service))
            records.append(row)
            with (f.root/'series.jsonl').open('ab') as out:out.write(data(row)+b'\n')
            cycles+=1
            time.sleep(max(0,min(10-(time.monotonic()-cycle),seconds-(time.monotonic()-started))))
        elapsed=time.monotonic()-started;warm=[r for r in records if r['elapsed']>=300]
        assert elapsed>=1800 and len(warm)>=100 and not errors
        xs=[r['elapsed']/60 for r in warm];ys=[r['server']['private_bytes']/1024**2 for r in warm]
        mx,my=statistics.mean(xs),statistics.mean(ys)
        trend=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/sum((x-mx)**2 for x in xs)
        def span(key):return max(r['server'][key] for r in warm)-min(r['server'][key] for r in warm)
        result=dict(private_span_MiB=span('private_bytes')/1024**2,private_slope_MiB_per_minute=trend,
                    rss_span_MiB=span('rss_bytes')/1024**2,fd_span=span('fd'),thread_span=span('threads'),
                    disk_growth_MiB=(records[-1]['server']['disk_bytes']-records[0]['server']['disk_bytes'])/1024**2,
                    first=warm[0]['server'],last=warm[-1]['server'])
        assert result['private_span_MiB']<=16 and trend<=.25 and result['rss_span_MiB']<=24,result
        assert result['fd_span']<=4 and result['thread_span']<=2 and result['disk_growth_MiB']<=8,result
        assert all(r['server']['partial_files']==0 for r in records)
        assert len({r['server']['disk_files'] for r in records})==1
        assert all(digest(Path(p))==stamp for p,stamp in fingerprints.items()),'Final source changed during acceptance'
        f.protect()
        report=dict(passed=True,seconds=elapsed,cycles=cycles,errors=errors,resources=result,
                    downloaded_bytes=downloaded,client_pid=os.getpid(),actual_server_pid=pid,environment=f.service.info(),source_protected=True)
        (f.root/'report.json').write_bytes(data(report));print(data(report).decode())
    finally:f.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--seconds',type=int,default=1800);a=p.parse_args();run(a.output,a.seconds)
