# SPDX-License-Identifier: GPL-3.0-only
import os
from pathlib import Path
import subprocess
import sys
import unittest
import pytest
import portable_asgi

CASES=unittest.TestLoader().getTestCaseNames(portable_asgi.ASGICore)

@pytest.mark.parametrize('case',CASES)
def test_portable_asgi_in_isolated_process(tmp_path,case):
    # pytest's no-network guard also blocks asyncio's Windows internal socket
    # pair. Like existing HTTP tests, use a new isolated test process; do not
    # weaken the main suite guard or mock asyncio's internals.
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parent)+os.pathsep+str(Path(__file__).resolve().parents[1]),
             TEMP=str(tmp_path),TMP=str(tmp_path))
    result=subprocess.run([sys.executable,'-m','unittest','portable_asgi.ASGICore.'+case],
                          env=env,capture_output=True,timeout=20)
    (tmp_path/'result.log').write_bytes(result.stdout+result.stderr)
    assert result.returncode==0,(result.stdout+result.stderr).decode(errors='replace')


ROLES=['admin','fake-writer','fake-reader','fake-download','fake-outsider','none']
OPERATIONS=['users','create-user','elevate','audit','foreign-project','foreign-task','publish','cancel','source']

def external_case(tmp_path,module,case):
    result=subprocess.run([sys.executable,'-m',module,'--output',str(tmp_path/'case'),'--case',case],
                          cwd=Path(__file__).resolve().parents[1],capture_output=True,timeout=100)
    (tmp_path/'process.log').write_bytes(result.stdout+result.stderr)
    assert result.returncode==0,(result.stdout+result.stderr).decode(errors='replace')
    import json
    assert json.loads((tmp_path/'case/report.json').read_bytes())['passed']

@pytest.mark.parametrize('role',ROLES)
@pytest.mark.parametrize('operation',OPERATIONS)
def test_real_https_authorization(tmp_path,role,operation):
    external_case(tmp_path,'scripts.verify_https',role+':'+operation)

@pytest.mark.parametrize('case',['wrong-uuid','untrusted','wrong-host','expired','future','replacement',
                               'headers','parser','terminal-race','missing','damaged','extra','manifest',
                               'upload-idle','upload-total','busy-cancel','revoke-before-commit','download-cut','download-slow','lease',
                               'rate-limit','body-limits','header-wait','login-admission','raw-uri'])
def test_real_nginx_tls_security_boundary(tmp_path,case):
    external_case(tmp_path,'scripts.verify_https_security',case)

@pytest.mark.parametrize('case',['disconnect','file-ack','commit-ack','app-exit','proxy-exit','client-exit','db','space','seal'])
def test_real_https_failure_recovery(tmp_path,case):
    external_case(tmp_path,'scripts.verify_https_transfers',case)


def tls_config():
    import uuid
    return dict(url='https://127.0.0.1:4443',service=str(uuid.uuid4()),ca=str(Path(os.environ['MW_HTTPS_TEST_CERTIFICATES'])/'ca.pem'))

def test_target_change_drops_session_before_network():
    from mechanical_project_namer.account_client import AccountSession
    s=AccountSession(tls_config());s.token='FICTIONAL-MEMORY-ONLY';s.user={'id':'fake'}
    s.config['url']='https://127.0.0.1:4444'
    with pytest.raises(ValueError,match='target-changed-login-required'):s.request('GET','/projects')
    assert s.token is None and s.user is None and s.service_state=={}

def test_target_context_only_trusts_selected_ca():
    import ssl
    from mechanical_project_namer.transport import Target
    t=Target(tls_config())
    assert t.context.verify_mode==ssl.CERT_REQUIRED and t.context.check_hostname
    assert t.context.minimum_version==ssl.TLSVersion.TLSv1_2
    assert t.context.cert_store_stats()['x509_ca']==1

@pytest.mark.parametrize('url',['http://127.0.0.1:4443','https://example.com:4443',
                              'https://user:secret@127.0.0.1:4443','https://127.0.0.1:4443/other','https://127.0.0.1:4443/?token=value'])
def test_invalid_target_no_silent_http_or_remote_fallback(url):
    from mechanical_project_namer.transport import Target
    config=tls_config();config['url']=url
    with pytest.raises(ValueError):Target(config)

def test_origin_and_account_have_distinct_private_ledgers(tmp_path):
    import uuid
    from scripts.publish_harness import Fixture,remote_manifest
    from mechanical_project_namer.publish_client import Client
    from mechanical_project_namer.publish_ledger import PublishLedger
    f=Fixture(tmp_path/'fictional')
    cfg=dict(tls_config(),project=str(uuid.uuid4()),account=str(uuid.uuid4()),token='FICTIONAL-IN-MEMORY')
    a=Client(cfg);b=Client(dict(cfg,url='https://127.0.0.1:4444'));c=Client(dict(cfg,account=str(uuid.uuid4())))
    ledger=PublishLedger(f.snap);m=remote_manifest(f,f.v1)
    rows=[ledger.begin(client.config,m) for client in (a,b,c)]
    assert len({key for key,task in rows})==3 and len({task for key,task in rows})==3
    for key,task in rows:
        stored=ledger.get(key)
        assert 'token' not in stored and 'FICTIONAL-IN-MEMORY' not in json_text(stored)
    f.assert_protected();f.close()

def json_text(value):
    import json
    return json.dumps(value)

def test_json_total_deadline_is_not_reset_by_a_trickle():
    from unittest.mock import Mock,patch
    from mechanical_project_namer.transport import response_bytes
    connection=Mock();response=Mock();response.read1.return_value=b'x'
    with patch('mechanical_project_namer.transport.time.monotonic',side_effect=[0,0,11]):
        with pytest.raises(ValueError,match='response-timeout'):response_bytes(connection,response)
    assert response.read1.call_count==1

def test_json_response_size_is_bounded():
    from unittest.mock import Mock
    from mechanical_project_namer.transport import response_bytes
    response=Mock();response.read1.return_value=b'x'*9
    with pytest.raises(ValueError,match='response-limit'):response_bytes(Mock(),response,limit=8)

def test_legacy_ready_file_creation_is_not_json_publication(tmp_path):
    import json,threading,time
    from unittest.mock import Mock
    from scripts.publish_harness import wait_publish_ready
    p=tmp_path/'ready.json';p.write_bytes(b'');value=dict(pid=123,port=4443,service='FICTIONAL')
    def writer():time.sleep(.05);p.write_text(json.dumps(value))
    t=threading.Thread(target=writer);t.start()
    try:assert wait_publish_ready(Mock(poll=lambda:None),p,seconds=1)==value
    finally:t.join()

def test_legacy_incomplete_ready_wait_has_a_deadline(tmp_path):
    from unittest.mock import Mock
    from scripts.publish_harness import wait_publish_ready
    p=tmp_path/'ready.json';p.write_bytes(b'')
    with pytest.raises(RuntimeError,match='service-start-timeout'):
        wait_publish_ready(Mock(poll=lambda:None),p,seconds=.05)

def test_account_window_separates_history_from_current_confirmation(qtbot):
    from types import SimpleNamespace
    from mechanical_project_namer.ui.accounts_dialog import AccountsDialog
    from mechanical_project_namer.account_client import AccountSession
    d=AccountsDialog(SimpleNamespace(list_projects=lambda:[]));qtbot.addWidget(d)
    d.session=AccountSession(tls_config())
    d.session.user=dict(id='FICTIONAL-USER',username='fake-reader',role='reader',must_change=False)
    d.done(dict(kind='versions',versions=[dict(manifest=dict(label='V1'),healthy=True)]))
    assert '本次操作已确认' in d.confirmation_label.text()
    assert '上次查询' in d.versions.item(0).text()
    d.failed('network-unavailable')
    assert '当前未确认' in d.confirmation_label.text() and '上次确认' in d.confirmation_label.text()
    assert d.versions.count()==1
