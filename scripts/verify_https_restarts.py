# SPDX-License-Identifier: GPL-3.0-only
"""Serial component-triggered shutdown/restart; no credentials in diagnostics.

The existing supervisor intentionally closes the counterpart. Each next start
uses a new private prefix and the same business data; no in-place hot reload.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import time
import uuid

from scripts.https_harness import fixture, ROOT
from mechanical_project_namer.diagnostics import Trace
from mechanical_project_namer.transport import Target
from mechanical_project_namer.publish_protocol import data


def end_component(service, role, hard):
    pid=service.pid if role=='application' else service.proxy_pid
    marker=str(service.runtime+('/app-ready.json' if role=='application' else '/nginx.conf'))
    code='''import os,sys,signal,pathlib,json,time
pid=int(sys.argv[1]);p=pathlib.Path('/proc')/str(pid)
assert p.stat().st_uid==os.geteuid() and sys.argv[2].encode() in (p/'cmdline').read_bytes()
print(json.dumps(dict(pid=pid,clock_domain='linux-monotonic',signal_monotonic_ns=time.monotonic_ns())),flush=True)
os.kill(pid,signal.SIGKILL if sys.argv[3]=='hard' else signal.SIGTERM)'''
    result=json.loads(service.native(code,str(pid),marker,'hard' if hard else 'normal'))
    service.windows_ready=False
    result['supervisor_exit']=service.process.wait(45)
    service.process=None
    return result


def run(root,role,mode,rounds):
    root=Path(root)
    if not 1<=rounds<=20:raise ValueError('bounded-restart-rounds')
    budget=dict(rounds=rounds,role=role,mode=mode,serial=True,other_large_acceptance=False,
                linux_startup_seconds=15,windows_public_budget_seconds=10,public_attempts=2,
                pre_ready_probe_seconds=.25,post_ready_checks=2,no_credential_or_mutation_retry=True)
    root.parent.mkdir(parents=True,exist_ok=True)
    (root.parent/(root.name+'-budget.json')).write_bytes(data(budget))
    sources=[ROOT/'scripts'/n for n in ('https_runtime.py','https_harness.py','auth_harness.py','verify_https_restarts.py')]
    sources += [ROOT/'mechanical_project_namer'/n for n in ('transport.py','asgi_server.py','diagnostics.py','publish_server.py')]
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    f=fixture(root,diagnostics=True);records=[];unexpected=[];pre_failures=0;retries=0
    try:
        for number in range(rounds):
            scenario=uuid.uuid4().hex;trace=Trace(root/('round-'+str(number)+'.jsonl'))
            old=dict(runtime=f.service.runtime,application=f.service.pid,proxy=f.service.proxy_pid,
                     instance=f.service.ready_info['instance'])
            row=dict(round=number,scenario=scenario,role=role,mode=mode,old=old,
                     windows_clock_domain='windows-monotonic',post_ready=[])
            phase='shutdown';start=time.monotonic()
            try:
                row['shutdown']=end_component(f.service,role,mode=='hard')
                phase='pre-ready'
                before=time.monotonic()
                try:
                    c=Target(f.service.connection).connection(timeout=.25,diagnostic=trace,scenario=scenario);c.close()
                except (OSError,ValueError) as exc:
                    row['pre_ready']=dict(expected_failure=True,type=type(exc).__name__,seconds=time.monotonic()-before)
                    pre_failures+=1
                else:raise AssertionError('Stopped instance unexpectedly verified')
                # A historical ready marker may exist; live-process validation
                # must refuse it rather than declaring the old instance ready.
                check=f.service.base()+['-m','scripts.https_runtime','check','--runtime',old['runtime']]
                import subprocess
                checked=subprocess.run(check,cwd=ROOT,capture_output=True,timeout=12)
                assert checked.returncode!=0
                phase='startup'
                f.service.start()
                assert f.service.windows_ready and f.service.ready_info['instance']!=old['instance']
                retries+=f.service.public_result['attempts']-1
                row['new']=dict(runtime=f.service.runtime,application=f.service.pid,proxy=f.service.proxy_pid,
                                instance=f.service.ready_info['instance'],native=f.service.ready_info,
                                windows=f.service.windows_status)
                phase='post-ready'
                for index in range(2):
                    before=time.monotonic()
                    c=Target(f.service.connection).connection(diagnostic=trace,scenario=scenario)
                    try:assert c._workbench_health['service']==f.service.connection['service']
                    finally:c.close()
                    row['post_ready'].append(dict(verified=True,seconds=time.monotonic()-before))
                row['passed']=True
            except Exception as exc:
                code=str(exc) if re.fullmatch('[a-z0-9-]{1,80}',str(exc)) else 'restart-operation-failed'
                row.update(passed=False,failure_phase=phase,error_type=type(exc).__name__,error_code=code)
                unexpected.append(dict(round=number,phase=phase,error_type=type(exc).__name__,error_code=code))
                # Preserve failure and stop this bounded series; do not remove
                # the sample or silently replace it with a successful round.
            finally:
                row['seconds']=time.monotonic()-start;trace.close()
            records.append(row);(root/'rounds.json').write_bytes(data(records))
            if not row['passed']:break
        assert all(hashlib.sha256(p.read_bytes()).hexdigest()==hashes[str(p)] for p in sources)
        f.protect()
        result=dict(passed=len(records)==rounds and not unexpected,role=role,mode=mode,rounds=len(records),pre_ready_expected_failures=pre_failures,
                    post_ready_checks=sum(len(r['post_ready']) for r in records),post_ready_errors=unexpected,
                    startup_public_retries=retries,original_tls_root_cause='not-established',
                    paired_restart=True,code_unchanged=True,records=records)
        (root/'report.json').write_bytes(data(result))
        assert result['passed'],'Serial restart failure retained in private report'
        print('Serial HTTPS component restarts passed:',role,mode,len(records))
    finally:f.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--role',choices=['application','proxy'],required=True);p.add_argument('--mode',choices=['normal','hard'],required=True)
    p.add_argument('--rounds',type=int,default=20);a=p.parse_args();run(a.output,a.role,a.mode,a.rounds)
