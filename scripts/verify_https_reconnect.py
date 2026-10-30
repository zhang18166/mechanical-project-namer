# SPDX-License-Identifier: GPL-3.0-only
"""Real, bounded public-health retry; controlled pause is not a WSL root cause."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid

from scripts.https_harness import fixture, ROOT
from mechanical_project_namer.diagnostics import Trace
from mechanical_project_namer.transport import Target
from mechanical_project_namer.publish_protocol import data


def counts(service):
    return json.loads(service.native("import sqlite3,json,sys;v=json.load(open(sys.argv[1]));c=sqlite3.connect(v['index']);print(json.dumps({t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('auth_audit','users','tasks','remote_projects')}))",service.config))


def run(root,case):
    root=Path(root);f=fixture(root,diagnostics=case!='diagnostics-off');trace=Trace(root/'public-check.jsonl');resumer=None
    before=counts(f.service);config=dict(f.service.connection);started=time.monotonic()
    try:
        if case=='paused-proxy':
            code='''import os,pathlib,sys,signal,time,json
pid=int(sys.argv[1]);marker=sys.argv[2].encode();p=pathlib.Path('/proc')/str(pid)
assert p.stat().st_uid==os.geteuid() and marker in (p/'cmdline').read_bytes()
os.kill(pid,signal.SIGSTOP);print(json.dumps(dict(paused=True,pid=pid)),flush=True)
time.sleep(6)
assert p.stat().st_uid==os.geteuid() and marker in (p/'cmdline').read_bytes()
os.kill(pid,signal.SIGCONT);print(json.dumps(dict(resumed=True,pid=pid)),flush=True)'''
            resumer=subprocess.Popen(f.service.base()+['-c',code,str(f.service.proxy_pid),f.service.runtime],cwd=ROOT,
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            assert json.loads(resumer.stdout.readline())['paused']
        elif case=='wrong-uuid':config['service']=str(uuid.uuid4())
        elif case=='untrusted':
            from scripts.make_tls_fixtures import generate
            config['ca']=str(generate(root/'untrusted')/'ca.pem')
        elif case=='diagnostics-off':
            details=json.loads(f.service.native("import pathlib,json,sys;p=pathlib.Path(sys.argv[1]);print(json.dumps(dict(enabled=json.loads((p/'owner.json').read_bytes())['diagnostics'],logs=[x.name for x in p.glob('*.jsonl')],config=(p/'nginx.conf').read_text())))",f.service.runtime))
            assert details['enabled'] is False and details['logs']==[]
            assert 'access_log off;' in details['config'] and 'syslog:' not in details['config']
        elif case=='gate':
            from scripts.auth_harness import rejected
            state=f.admin.publisher(f.pa).publish(f.a.snap,f.task);before=counts(f.service)
            f.service.sql("UPDATE account_meta SET value='closed' WHERE key='recovery_gate'")
            assert f.admin.verify_service()['maintenance']
            assert f.admin.request('GET','/admin/users')['users']
            rejected(lambda:f.users['fake-reader']['session'].projects())
            rejected(lambda:f.admin.request('POST','/admin/users',dict(username='fake-denied',role='reader',initial_password='FICTIONAL-INITIAL-ONLY')))
            rejected(lambda:f.admin.request('POST','/admin/grants',dict(user=f.admin.user['id'],project=f.pa,permissions=[])))
            rejected(lambda:f.admin.request('POST','/auth/password',dict(old_password='FICTIONAL',new_password='FICTIONAL-ONLY-123')))
            rejected(lambda:f.admin.publisher(f.pa).download(state['task'],root/'must-not-download'),'download-denied')
            assert not (root/'must-not-download').exists()
        else:raise ValueError('unknown-reconnect-case')
        began=time.monotonic()
        if case in ('paused-proxy','diagnostics-off','gate'):
            result=Target(config).public_ready(diagnostic=trace)
            assert result['verified'],result
            if case=='paused-proxy':
                assert result['attempts']==2,result
                elapsed=time.monotonic()-began;assert 5<=elapsed<10.5,elapsed
            if case=='gate':assert result['health']['maintenance']
        else:
            try:Target(config).public_ready(diagnostic=trace)
            except ValueError as exc:
                expected='service-identity' if case=='wrong-uuid' else 'tls-certificate-rejected'
                assert str(exc)==expected
                result=dict(rejected=True,code=expected)
            else:raise AssertionError('Invalid peer accepted')
        trace.close()
        events=[json.loads(line) for line in (root/'public-check.jsonl').read_text().splitlines()]
        attempts=sum(e['event']=='client-tcp-start' for e in events)
        assert attempts==(2 if case=='paused-proxy' else 1)
        after=counts(f.service)
        assert {k:v for k,v in after.items() if k!='auth_audit'}=={k:v for k,v in before.items() if k!='auth_audit'}
        if case!='gate':assert after==before,'Public recovery retried a business or credential operation'
        f.protect()
        report=dict(passed=True,case=case,result=result,attempts=attempts,seconds=time.monotonic()-began,
                    no_credentials_or_mutations=True,original_wsl_root_cause='not-established',
                    fault='controlled-own-proxy-pause' if case=='paused-proxy' else 'none' if case in ('gate','diagnostics-off') else 'invalid-peer')
        (root/'report.json').write_bytes(data(report));print('Real HTTPS bounded reconnect verified:',case)
    finally:
        if resumer:
            # Always let the owned resumer wake our proxy before normal stop.
            resumer.wait(8)
        trace.close();f.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--case',choices=['paused-proxy','wrong-uuid','untrusted','diagnostics-off','gate'],required=True)
    a=p.parse_args();run(a.output,a.case)
