# SPDX-License-Identifier: GPL-3.0-only
"""Windows client -> native Ubuntu Nginx/UDS/Uvicorn, external fake data only."""
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
from scripts.auth_harness import ManagedService,AuthFixture,ROOT,linux_path
from scripts.publish_harness import write
from mechanical_project_namer.account_client import AccountSession
from mechanical_project_namer.publish_protocol import data
from mechanical_project_namer.transport import Target
from mechanical_project_namer.diagnostics import Trace


def environment():
    required=('MW_ACCOUNT_TEST_DISTRO','MW_ACCOUNT_TEST_USER','MW_ACCOUNT_TEST_ROOT',
              'MW_ACCOUNT_TEST_PYTHON','MW_HTTPS_TEST_CERTIFICATES','MW_HTTPS_TEST_NGINX')
    if any(not os.environ.get(k) for k in required):raise RuntimeError('Explicit isolated HTTPS environment required')


class HTTPSService(ManagedService):
    def __init__(self,*args,**kwargs):
        environment()
        super().__init__(*args,**kwargs)
        if self.platform!='linux':raise RuntimeError('Native isolated Linux required')
        self.port=int(self.native("import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()"))

    def start(self):
        self.windows_ready=False
        self.ready_info={}
        diagnostic=Trace(self.root/('connection-'+uuid.uuid4().hex+'.jsonl')) if getattr(self,'diagnostics',False) else None
        started=time.monotonic()
        try:
            result=self._start(diagnostic)
            self.windows_ready=True
            self.windows_status=dict(scope='windows-tls-uuid',verified=True,seconds=time.monotonic()-started,
                                     native_scope=self.ready_info['scope'],attempts=self.public_result['attempts'])
            return result
        except Exception:
            self.windows_status=dict(scope='windows-tls-uuid',verified=False,seconds=time.monotonic()-started,
                                     linux_marker_observed=bool(self.ready_info))
            if diagnostic:diagnostic.emit('windows-readiness-failed',ready=False,code='startup-or-public-health-failed')
            # Do not orphan a healthy native service when host verification
            # fails during fixture construction. Cleanup is not a ready result.
            if self.process and self.process.poll() is None:
                if self.ready_info:self.stop()
                else:self.process.wait(45)  # Native startup/finally already has bounded cleanup.
            raise
        finally:
            write(self.root/('windows-readiness-'+uuid.uuid4().hex+'.json'),self.windows_status)
            if diagnostic:diagnostic.close()

    def _start(self,diagnostic):
        self.runtime=os.environ['MW_ACCOUNT_TEST_ROOT']+'/r-'+uuid.uuid4().hex[:12]
        self.cert_root=self.runtime+'-certs'
        self.native("import pathlib,sys;pathlib.Path(sys.argv[1]).mkdir(mode=0o700)",self.cert_root)
        certificates=Path(os.environ['MW_HTTPS_TEST_CERTIFICATES'])
        for name in ('valid.pem','valid.key','ca.pem'):
            source=(getattr(self,'certificate','valid')+Path(name).suffix) if name.startswith('valid.') else name
            self.native_write(self.cert_root+'/'+name,(certificates/source).read_bytes(),exclusive=True)
        args=['-m','scripts.https_runtime','prepare','--runtime',self.runtime,'--config',self.config,
              '--nginx',os.environ['MW_HTTPS_TEST_NGINX'],'--port',str(self.port),'--cert',self.cert_root+'/valid.pem',
              '--key',self.cert_root+'/valid.key','--ca',self.cert_root+'/ca.pem']
        if os.environ.get('MW_HTTPS_TEST_LIBDIR'):args+=['--tls-library-dir',os.environ['MW_HTTPS_TEST_LIBDIR']]
        if diagnostic:args+=['--diagnostics']
        r=subprocess.run(self.base()+args,cwd=ROOT,capture_output=True,timeout=15)
        if r.returncode:raise RuntimeError('https-runtime-prepare-failed')
        self.control=self.runtime+'/control.json';self.events_path=self.runtime+'/events.jsonl'
        self.set_control()
        self.process=subprocess.Popen(self.base()+['-m','scripts.https_runtime','run','--runtime',self.runtime,'--control',self.control],
                                      cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        end=time.monotonic()+20
        while True:
            if self.process.poll() is not None:
                raise RuntimeError('https-runtime-start-failed')
            v=self.native("import pathlib,sys;p=pathlib.Path(sys.argv[1]);print(p.read_text() if p.exists() else '{}')",self.runtime+'/ready.json',
                          timeout=max(.01,end-time.monotonic()))
            try:
                parsed=json.loads(v)
            except json.JSONDecodeError:
                parsed={}
            if parsed:break
            if time.monotonic()>end:raise RuntimeError('https-ready-timeout')
            time.sleep(.05)
        self.ready_info=json.loads(v);self.pid=self.ready_info['application'];self.proxy_pid=self.ready_info['proxy']
        assert self.ready_info['scope']=='linux-local-tls' and self.ready_info['linux_https_verified']
        assert self.ready_info['unix_verified'] and self.ready_info['windows_verified'] is False
        self.connection=dict(url='https://127.0.0.1:'+str(self.port),service=self.ready_info['service'],ca=str(certificates/'ca.pem'))
        write(self.root/'connection.json',self.connection)
        self.public_result=Target(self.connection).public_ready(diagnostic=diagnostic,scenario=self.ready_info['instance'].replace('-',''))
        return AccountSession(self.connection)

    def session(self):
        session=AccountSession(self.connection);session.verify_service();return session

    def info(self):
        result=super().info()
        result['https']=True;result['application_pid']=self.pid;result['proxy_pid']=self.proxy_pid
        c=AccountSession(self.connection).target.connection()
        try:result['tls_protocol']=c.sock.version();result['cipher']=c.sock.cipher()[0]
        finally:c.close()
        result['proxy_maps']=self.native("import pathlib,sys;print(pathlib.Path('/proc/'+sys.argv[1]+'/maps').read_text())",str(self.proxy_pid))
        return result

    def set_control(self,**values):
        self.native_write(self.control,data(dict(isolated_test=True,events=self.events_path,**values)))

    def events(self):
        result=self.native("import pathlib,sys;p=pathlib.Path(sys.argv[1]);print(p.read_text() if p.exists() else '')",self.events_path)
        return [json.loads(line) for line in result.splitlines()]

    def stop(self):
        self.windows_ready=False
        if not self.process:return
        if self.process.poll() is None:
            r=subprocess.run(self.base()+['-m','scripts.https_runtime','stop','--runtime',self.runtime],cwd=ROOT,capture_output=True,timeout=45)
            if r.returncode:raise RuntimeError('https-owned-stop-failed')
            self.process.wait(10)
        self.process=None

    def hard_exit(self,role):
        self.windows_ready=False
        pid=self.pid if role=='application' else self.proxy_pid
        marker=b'mechanical_project_namer.asgi_server' if role=='application' else self.runtime.encode()
        code="import os,sys,signal;p='/proc/'+sys.argv[1];assert os.stat(p).st_uid==os.geteuid();assert bytes.fromhex(sys.argv[2]) in open(p+'/cmdline','rb').read();os.kill(int(sys.argv[1]),signal.SIGKILL)"
        self.native(code,str(pid),marker.hex())
        self.process.wait(45);self.process=None


class DiagnosticHTTPSService(HTTPSService):
    diagnostics=True


def fixture(root,platform='linux',large=False,*,diagnostics=False):
    return AuthFixture(root,'linux',large,service_class=DiagnosticHTTPSService if diagnostics else HTTPSService)
