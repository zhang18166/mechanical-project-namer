# SPDX-License-Identifier: GPL-3.0-only
import json,subprocess,sys
from pathlib import Path
import pytest
from mechanical_project_namer.publish_protocol import manifest,ProtocolError,path
ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('value',['../x','C:/x','/x','a:stream','CON.txt','x\\y','a/../b','NUL .prt','LPT1 .txt'])
def test_protocol_paths(value):
    with pytest.raises(ProtocolError):path(value)
def test_real_http_process_roundtrip_and_failures(tmp_path):
    result=subprocess.run([sys.executable,str(ROOT/'scripts/verify_publish.py'),'--output',str(tmp_path/'http')],cwd=ROOT,capture_output=True,timeout=45)
    assert result.returncode==0,result.stderr.decode(errors='replace')
    report=json.loads((tmp_path/'http/report.json').read_bytes());assert report['passed'] and report['checks']>=11
