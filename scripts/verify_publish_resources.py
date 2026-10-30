# SPDX-License-Identifier: GPL-3.0-only
"""Finite 30-minute protocol load, no process restarts, explicit GC or global changes."""
import argparse
import ctypes
import json
import os
import statistics
import subprocess
import time
from pathlib import Path
from scripts.publish_harness import Fixture,begin,upload_all,write
from scripts.publish_metrics import sample
from mechanical_project_namer.publish_protocol import data,digest


def slope(rows,role):
    xs=[r['elapsed']/60 for r in rows];ys=[r[role]['private_bytes']/1024**2 for r in rows]
    mx,my=statistics.mean(xs),statistics.mean(ys)
    return sum((x-mx)*(y-my) for x,y in zip(xs,ys))/sum((x-mx)**2 for x in xs)


def run(root,seconds=1800):
    assert seconds>=1800
    f=Fixture(root,large=True);s=f.service()
    # Criteria and code identity are written before any load.
    budget=dict(duration_seconds=seconds,warmup_seconds=300,interval_seconds=10,max_download_write_GiB=4,
                max_private_span_MiB=16,max_private_slope_MiB_per_minute=.25,max_handle_span=16,max_thread_span=2,
                max_established_connections=4,fixed_versions=2,no_process_restart=True,no_explicit_gc=True)
    (f.root/'budget.json').write_bytes(data(budget))
    sources=list(Path('mechanical_project_namer').rglob('publish*.py'))+[Path(p) for p in (
        'mechanical_project_namer/library_lock.py','mechanical_project_namer/snapshots.py',
        'mechanical_project_namer/data/database.py','scripts/publish_metrics.py',
        'scripts/publish_harness.py','scripts/verify_publish_resources.py')]
    (f.root/'code.json').write_bytes(data({'git':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'files':{p.as_posix():digest(p) for p in sources}}))
    records=[];errors=[];cycles=0;download_bytes=0;upload_bytes=0
    try:
        c=s.start()
        v1=c.publish(f.snap,f.v1);pending,tid,m=begin(s,f);upload_all(pending,tid,m,f)
        start=time.monotonic()
        while time.monotonic()-start<seconds:
            cycle_start=time.monotonic()
            try:
                assert c.publish(f.snap,f.v1)['healthy']  # idempotent retry
                assert c.request('POST','/tasks/'+v1['task']+'/commit',{})['healthy']
                assert len(c.versions())==1
                index=cycles%3;entry=m['files'][index]
                row=f.snap._get(f.v2);p=f.snap.store/'versions'/row['version_id']/'payload'/entry['path']
                pending.request('PUT',f'/tasks/{tid}/files/{index}',file=p);upload_bytes+=entry['size']
                state=pending.request('GET','/tasks/'+tid);assert state['verified']==[0,1,2]
                target=f.root/'download';c.download(v1['task'],target)
                for item in v1['manifest']['files']:
                    q=target/item['path'];assert digest(q)=={k:item[k] for k in ('size','sha256')}
                    download_bytes+=item['size'];q.unlink()  # exclusively this verified test download
                for folder in sorted([p for p in target.rglob('*') if p.is_dir()],key=lambda p:len(p.parts),reverse=True):
                    folder.rmdir()
                target.rmdir()
                assert download_bytes<=budget['max_download_write_GiB']*1024**3
            except Exception as exc:
                errors.append(dict(cycle=cycles,type=type(exc).__name__))
                raise
            cycles+=1
            files=[p for p in (s.root/'storage').rglob('*') if p.is_file()]
            record=dict(elapsed=time.monotonic()-start,cycle=cycles,client=sample(os.getpid()),server=sample(s.pid),
                        server_files=len(files),server_bytes=sum(p.stat().st_size for p in files),
                        partial_files=sum('.partial' in p.name or p.name.endswith('.copy') for p in files),errors=len(errors))
            stages=list(f.root.glob('.publish-download-*'))
            record.update(client_stage_directories=len(stages),client_stage_files=sum(p.is_file() for stage in stages for p in stage.rglob('*')),
                layout_types={name:sum(getattr(k,'__name__','')==name for k in ctypes._pointer_type_cache) for name in ('OVERLAPPED','Memory','Thread')})
            records.append(record)
            with (f.root/'series.jsonl').open('ab') as out:out.write(data(record)+b'\n')
            print(data(record).decode(),flush=True)
            remaining=min(10-(time.monotonic()-cycle_start),seconds-(time.monotonic()-start))
            if remaining>0:time.sleep(remaining)
        f.assert_protected();warm=[r for r in records if r['elapsed']>=300]
        code=json.loads((f.root/'code.json').read_bytes())
        assert all(digest(Path(p))==stamp for p,stamp in code['files'].items()),'protocol source changed during load'
        result={}
        for role in ('client','server'):
            span=(max(r[role]['private_bytes'] for r in warm)-min(r[role]['private_bytes'] for r in warm))/1024**2
            trend=slope(warm,role);handles=max(r[role]['handles'] for r in warm)-min(r[role]['handles'] for r in warm)
            threads=max(r[role]['threads'] for r in warm)-min(r[role]['threads'] for r in warm)
            result[role]=dict(private_span_MiB=span,slope_MiB_per_minute=trend,handle_span=handles,thread_span=threads,
                              start=warm[0][role],end=warm[-1][role])
            assert span<=16 and trend<=.25 and handles<=16 and threads<=2,result
        assert all(r['partial_files']==0 and r['client']['tcp_established']<=4 and r['server']['tcp_established']<=4 for r in records)
        assert len({r['server_files'] for r in warm})==1
        assert all(r['client_stage_directories']==r['client_stage_files']==1 and all(count==1 for count in r['layout_types'].values()) for r in records)
        report=dict(passed=True,seconds=time.monotonic()-start,cycles=cycles,errors=errors,resources=result,
                    uploaded_bytes=upload_bytes,downloaded_bytes=download_bytes,client_pid=os.getpid(),server_pid=s.pid,
                    processes_restarted=False,source_protected=True)
        (f.root/'report.json').write_bytes(data(report));print(data(report).decode(),flush=True)
    finally:f.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--seconds',type=int,default=1800);a=p.parse_args()
    run(a.output,a.seconds)
