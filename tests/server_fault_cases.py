# SPDX-License-Identifier: GPL-3.0-only
"""Shared real-process cases; pytest/unittest count the same scenarios separately."""
import json,subprocess,sys,uuid
from pathlib import Path
from server_fixture import make_service
from mechanical_project_namer.accounts import AccountStore
from mechanical_project_namer.publish_protocol import data
from mechanical_project_namer.server_backups import ServerBackups
from mechanical_project_namer.server_recovery import inspect_recovery

ROOT=Path(__file__).resolve().parents[1]
BACKUP_POINTS=['backup-frozen','backup-stage','backup-database-copy','backup-database','backup-copy',
               'backup-before-manifest','backup-after-manifest','backup-complete-marker','backup-before-commit','backup-after-commit']
RESTORE_POINTS=['restore-frozen','restore-stage','restore-copy','restore-before-database-commit','restore-after-database-commit',
                'restore-before-config','restore-after-config','restore-before-commit','restore-after-commit']
APPROVE_POINTS=['approve-before-write','approve-before-commit','approve-after-commit']


def worker(args):
    return subprocess.run([sys.executable,'-m','scripts.server_backup_fault_worker',*map(str,args)],cwd=ROOT,capture_output=True,timeout=40)


def probe(root):
    path=root/'probe.json';path.write_bytes(data(dict(kind='mw-server-recovery-test',root=str(root),id=str(uuid.uuid4()))));return path


def hard_exit_case(root,action,point):
    from portable_server_backups import fingerprints
    fixture=make_service(root/'source');service=ServerBackups();backup=root/'backup';task=str(uuid.uuid4())
    target=backup if action=='backup' else root/'restored'
    args=['--probe',probe(root),'--action',action,'--task',task,'--target',target]
    if action=='backup':args+=['--config',fixture['config_path']]
    else:
        service.create(fixture['config_path'],backup,task_id=str(uuid.uuid4()));args+=['--backup',backup]
    before=fingerprints(fixture['root'])
    interrupted=worker([*args,'--point',point]);assert interrupted.returncode==73,interrupted.stderr.decode(errors='replace')
    completed=worker(args);assert completed.returncode==0,completed.stderr.decode(errors='replace')
    if action=='backup':assert service.verify(backup)['backup_id']==task
    else:
        assert service.verify_instance(target)['task']==task
        assert AccountStore(json.loads((target/'server.json').read_bytes())).recovery_state()['maintenance']
    assert fingerprints(fixture['root'])==before


def approval_exit_case(root,point):
    f=make_service(root/'source');service=ServerBackups();backup=root/'backup';target=root/'restored'
    service.create(f['config_path'],backup,task_id=str(uuid.uuid4()));service.restore(backup,target,task_id=str(uuid.uuid4()))
    cfg=target/'server.json';store=AccountStore(json.loads(cfg.read_bytes()));state=inspect_recovery(store)
    review=dict(recovery=state['recovery'],source_offline=True,security_changes_reviewed=True,users=[
        dict(id=u['id'],enabled=u['role']=='admin',role=u['role'],password_action='keep-confirmed',projects={p['id']:[] for p in state['projects']}) for u in state['users']])
    review_path=root/'review.json';review_path.write_bytes(data(review));review_path.chmod(0o600)
    password=root/'password';password.write_text(f['password']);password.chmod(0o600)
    args=['--probe',probe(root),'--action','approve','--config',cfg,'--review',review_path,'--password-file',password]
    result=worker([*args,'--point',point]);assert result.returncode==73,result.stderr.decode(errors='replace')
    # A genuinely new interpreter recovers SQLite and reads the resulting gate.
    code="import json,sys;from mechanical_project_namer.accounts import AccountStore;s=AccountStore(json.load(open(sys.argv[1])));print(json.dumps(s.recovery_state()))"
    reopened=subprocess.run([sys.executable,'-c',code,str(cfg)],cwd=ROOT,capture_output=True,timeout=20)
    assert reopened.returncode==0,reopened.stderr.decode(errors='replace')
    maintenance=json.loads(reopened.stdout)['maintenance']
    assert maintenance==(point!='approve-after-commit')
    retry=worker(args)
    if maintenance:assert retry.returncode==0,retry.stderr.decode(errors='replace')
    else:assert retry.returncode!=0  # Already approved: refuse replay rather than applying again.
    with store.db() as c:assert c.execute("SELECT count(*) FROM auth_audit WHERE action='recovery-approved'").fetchone()[0]==1
