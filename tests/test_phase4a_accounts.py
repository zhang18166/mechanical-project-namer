# SPDX-License-Identifier: GPL-3.0-only
import json,os,subprocess,sys,threading,uuid
from pathlib import Path
import pytest
from portable_accounts import Accounts
ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('role',['admin','fake-writer','fake-reader','fake-download','fake-outsider','none'])
@pytest.mark.parametrize('operation',['users','create-user','elevate','audit','foreign-project','foreign-task','publish','cancel','source'])
def test_account_http_authorization(tmp_path,role,operation):
    r=subprocess.run([sys.executable,'-m','scripts.verify_accounts','--output',str(tmp_path/'auth'),'--case',role+':'+operation],cwd=ROOT,capture_output=True,timeout=90)
    assert r.returncode==0,r.stderr.decode(errors='replace')[-4000:]
    assert json.loads((tmp_path/'auth/report.json').read_bytes())['passed']


def test_full_account_http_restart_and_rollback(tmp_path):
    r=subprocess.run([sys.executable,'-m','scripts.verify_accounts','--output',str(tmp_path/'flow')],cwd=ROOT,capture_output=True,timeout=90)
    assert r.returncode==0,r.stderr.decode(errors='replace')[-4000:]
    report=json.loads((tmp_path/'flow/report.json').read_bytes())
    assert report['passed'] and report['result']['sql_rollback'] and report['result']['restarted']


def test_account_client_optional_server_dependency_absent(tmp_path):
    code="""
import importlib.abc,sys
class Deny(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.startswith('argon2'):raise ImportError('server-only dependency absent')
sys.meta_path.insert(0,Deny())
from mechanical_project_namer.account_client import AccountSession
from mechanical_project_namer.ui.project_files_page import ProjectFilesPage
from mechanical_project_namer.numbering.simple import SIMPLE_TYPE
assert SIMPLE_TYPE
"""
    r=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,timeout=15)
    assert r.returncode==0,r.stderr.decode(errors='replace')


def test_local_development_entry_without_server(tmp_path):
    env=dict(os.environ,QT_QPA_PLATFORM='windows')
    r=subprocess.run([sys.executable,'-m','mechanical_project_namer','--dev','--dev-root',str(tmp_path/'offline'),'--dev-smoke'],cwd=ROOT,env=env,capture_output=True,timeout=30)
    assert r.returncode==0,r.stderr.decode(errors='replace')[-3000:]
    report=json.loads((tmp_path/'offline/cache/development-smoke.json').read_bytes())
    assert report['isolated_paths'] and report['credentials_disabled'] and report['window_visible']


def test_changed_service_identity_receives_no_session_token():
    code="import sys;sys.path.insert(0,'tests');from test_phase4a_accounts import _identity_check;_identity_check()"
    r=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,timeout=15)
    assert r.returncode==0,r.stderr.decode(errors='replace')


def _identity_check():
    from http.server import BaseHTTPRequestHandler,HTTPServer
    from mechanical_project_namer.account_client import AccountSession
    from mechanical_project_namer.publish_protocol import ProtocolError,data
    received=[]
    class OtherService(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            received.append((self.path,self.headers.get('Authorization')))
            body=data(dict(service=str(uuid.uuid4()),accounts=True))
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=HTTPServer(('127.0.0.1',0),OtherService)
    thread=threading.Thread(target=server.serve_forever);thread.start()
    try:
        s=AccountSession(dict(port=server.server_port,service=str(uuid.uuid4())))
        s.token='FICTITIOUS_SESSION_FOR_IDENTITY_TEST'
        with pytest.raises(ProtocolError,match='service-identity'):s.me()
        assert received==[('/health',None)]
    finally:server.shutdown();thread.join();server.server_close()
