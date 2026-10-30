# SPDX-License-Identifier: GPL-3.0-only
"""Finite, private Windows-client / real Linux-service resource and CLI acceptance."""
import argparse,json,os,statistics,subprocess,sys,time
from pathlib import Path
from scripts.auth_harness import AuthFixture,ROOT
from scripts.publish_metrics import sample
from mechanical_project_namer.account_client import AccountSession
from mechanical_project_namer.publish_protocol import data,digest


def run(root,seconds):
    if seconds<120:raise ValueError('At least 120 seconds for this short-run check')
    f=AuthFixture(root,'linux');rows=[]
    budget=dict(seconds=seconds,warmup_seconds=60,interval_seconds=2,max_private_span_MiB=16,
                max_positive_slope_MiB_per_minute=1,max_fd_span=4,max_thread_span=2,
                no_restart=True,no_explicit_gc=True,fixed_versions=1)
    (f.root/'budget.json').write_bytes(data(budget))
    sources=list((ROOT/'mechanical_project_namer').glob('account*.py'))+[ROOT/'mechanical_project_namer/ui/accounts_dialog.py']
    fingerprints={str(p):digest(p) for p in sources}
    (f.root/'code.json').write_bytes(data(fingerprints))
    def native_sample():
        code="""import json,sys,os
p='/proc/'+sys.argv[1]
s=dict(line.split(':',1) for line in open(p+'/status') if ':' in line)
m=dict(line.split(':',1) for line in open(p+'/smaps_rollup') if ':' in line)
v=dict(pid=int(sys.argv[1]),working_set=int(s['VmRSS'].split()[0])*1024,
 private_bytes=(int(m['Private_Clean'].split()[0])+int(m['Private_Dirty'].split()[0]))*1024,
 threads=int(s['Threads']),handles=len(os.listdir(p+'/fd')))
print(json.dumps(v))"""
        return json.loads(f.service.native(code,str(f.service.pid)))
    try:
        state=f.admin.publisher(f.pa).publish(f.a.snap,f.task)
        f.admin.publisher(f.pa).download(state['task'],f.root/'download')
        for item in state['manifest']['files']:
            assert digest(f.root/'download'/item['path'])=={k:item[k] for k in ('size','sha256')}
        # Private test password file, never stdout/argv. CLI opens HTTP, not SQLite.
        secret=f.root/'cli-password';secret.write_text(f.service.current)
        command=[sys.executable,'scripts/account_admin.py','--connection',str(f.service.root/'connection.json'),
                 '--username','fake-admin','--password-file',str(secret),'users']
        cli=subprocess.run(command,cwd=ROOT,capture_output=True,timeout=20)
        assert cli.returncode==0 and len(json.loads(cli.stdout)['users'])==5
        assert b'password_hash' not in cli.stdout and b'Bearer' not in cli.stdout
        health=subprocess.run([sys.executable,'scripts/check_account_server.py','--connection',str(f.service.root/'connection.json')],cwd=ROOT,capture_output=True,timeout=10)
        assert health.returncode==0 and json.loads(health.stdout)['service']==f.service.connection['service']
        started=time.monotonic();cycles=0
        while time.monotonic()-started<seconds:
            before=time.monotonic()
            assert len(f.admin.projects())==2 and len(f.admin.versions(f.pa))==1
            assert f.admin.publisher(f.pa).publish(f.a.snap,f.task)['healthy']
            member=AccountSession(f.service.connection)
            member.login('fake-reader',f.users['fake-reader']['password']);assert len(member.projects())==1
            member.me();member.logout()
            f.admin.request('GET','/admin/audit')
            rows.append(dict(elapsed=time.monotonic()-started,client=sample(os.getpid()),server=native_sample()))
            with (f.root/'series.jsonl').open('ab') as out:out.write(data(rows[-1])+b'\n')
            cycles+=1
            time.sleep(max(0,min(2-(time.monotonic()-before),seconds-(time.monotonic()-started))))
        warm=[r for r in rows if r['elapsed']>=60];resources={}
        for name in ('client','server'):
            xs=[r['elapsed']/60 for r in warm];ys=[r[name]['private_bytes']/1024**2 for r in warm]
            mx,my=statistics.mean(xs),statistics.mean(ys)
            trend=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/sum((x-mx)**2 for x in xs)
            span=max(ys)-min(ys);fds=max(r[name]['handles'] for r in warm)-min(r[name]['handles'] for r in warm)
            threads=max(r[name]['threads'] for r in warm)-min(r[name]['threads'] for r in warm)
            resources[name]=dict(private_span_MiB=span,slope_MiB_per_minute=trend,handle_span=fds,thread_span=threads,
                                 first=warm[0][name],last=warm[-1][name])
            assert span<=16 and trend<=1 and fds<=4 and threads<=2,resources[name]
        assert all(digest(Path(path))==value for path,value in fingerprints.items())
        f.protect();environment=f.service.info()
        # Actual stop command checks /proc UID + exact service module + unique receipt.
        f.service.command('stop','--ready','/mnt/d/'+f.service.ready.as_posix()[3:])
        f.service.process.wait(15)
        backup=f.service.data_root+'/consistent-index-backup.sqlite'
        f.service.command('backup-index','--config',f.service.config,'--destination',backup)
        checked=f.service.native("import sqlite3,sys,json;c=sqlite3.connect(sys.argv[1]);print(json.dumps(dict(check=c.execute('PRAGMA quick_check').fetchone()[0],users=c.execute('SELECT count(*) FROM users').fetchone()[0])));c.close()",backup)
        assert json.loads(checked)==dict(check='ok',users=5)
        result=dict(passed=True,seconds=time.monotonic()-started,cycles=cycles,errors=0,resources=resources,
                    environment=environment,admin_cli=True,health_cli=True,verified_stop=True,sqlite_backup=True,protected=True,
                    scope='short finite loopback check; not long-term or production certification')
        (f.root/'report.json').write_bytes(data(result));print(data(result).decode())
    finally:f.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--seconds',type=int,default=120);a=p.parse_args();run(a.output,a.seconds)
