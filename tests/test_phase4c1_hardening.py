# SPDX-License-Identifier: GPL-3.0-only
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import pytest
import portable_https_hardening

ROOT=Path(__file__).resolve().parents[1]
UNITS=[kind+'.'+name for kind in ('HTTPSHardening','ASGIDiagnostic','PartialCleanup')
       for name in unittest.TestLoader().getTestCaseNames(getattr(portable_https_hardening,kind))]


@pytest.mark.parametrize('case',UNITS)
def test_portable_hardening_regression(tmp_path,case):
    env=dict(os.environ,PYTHONPATH=str(ROOT/'tests')+os.pathsep+str(ROOT),TEMP=str(tmp_path),TMP=str(tmp_path))
    result=subprocess.run([sys.executable,'-m','unittest','portable_https_hardening.'+case],env=env,cwd=ROOT,
                          capture_output=True,timeout=20)
    (tmp_path/'result.log').write_bytes(result.stdout+result.stderr)
    assert result.returncode==0,(result.stdout+result.stderr).decode(errors='replace')


def real(tmp_path,module,arguments,timeout=120):
    result=subprocess.run([sys.executable,'-m',module,'--output',str(tmp_path/'case'),*arguments],cwd=ROOT,
                          capture_output=True,timeout=timeout)
    (tmp_path/'process.log').write_bytes(result.stdout+result.stderr)
    assert result.returncode==0,(result.stdout+result.stderr).decode(errors='replace')
    report=json.loads((tmp_path/'case/report.json').read_bytes())
    assert report['passed']
    return report


@pytest.mark.parametrize('case',['handshake','headers','mixed-headers','upload-idle','upload-trickle',
                                'download-none','download-trickle','keepalive','normal-control'])
def test_real_slow_connection_lifecycle(tmp_path,case):
    # A green measurement case does not promote the general absolute-lifetime
    # policy: download cases explicitly retain a PARTIAL policy conclusion.
    result=real(tmp_path,'scripts.verify_https_connections',['--case',case])
    assert result['measurement_complete'] and result['observer_samples']>0
    assert result['filesystem']==dict(partial=0,proxy_temp=0)


def test_real_tls_cumulative_health_budget(tmp_path):
    real(tmp_path,'scripts.verify_https_budget',[])


@pytest.mark.parametrize('case',['paused-proxy','wrong-uuid','untrusted','diagnostics-off','gate'])
def test_real_bounded_reconnect_without_business_replay(tmp_path,case):
    result=real(tmp_path,'scripts.verify_https_reconnect',['--case',case])
    assert result['no_credentials_or_mutations']


@pytest.mark.parametrize('role',['application','proxy'])
@pytest.mark.parametrize('mode',['normal','hard'])
def test_serial_twenty_restart_rounds(tmp_path,role,mode):
    result=real(tmp_path,'scripts.verify_https_restarts',['--role',role,'--mode',mode,'--rounds','20'],timeout=240)
    assert result['rounds']==20 and result['pre_ready_expected_failures']==20
    assert result['post_ready_checks']==40 and not result['post_ready_errors']
