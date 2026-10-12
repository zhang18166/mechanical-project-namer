# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166
import json
from pathlib import Path
import subprocess
import sys


def test_offline_runtime_self_test(tmp_path):
    report=tmp_path/'result.json'
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,str(root/'launcher.py'),'--self-test',str(report)],
                          cwd=root,timeout=30,capture_output=True)
    assert result.returncode==0
    data=json.loads(report.read_text(encoding='utf-8'))
    assert data['passed']
    assert data['network_calls']==0
    assert not data['credential_access']
    assert not data['solidworks_access']
    assert 'first_run_empty_database' in data['checks']
    assert 'name_and_sequence' in data['checks']
    assert 'pdf_and_excel' in data['checks']
