# SPDX-License-Identifier: GPL-3.0-only
"""Independent real Nginx HTTPS API cases, not framework mock requests."""
import argparse
from pathlib import Path
import time
from scripts.https_harness import fixture
from scripts.verify_accounts import full,auth_case
from mechanical_project_namer.publish_protocol import data

def run(root,case):
    f=fixture(root,'linux',large=case=='full');started=time.monotonic()
    try:
        result=full(f) if case=='full' else auth_case(f,*case.split(':'))
        report=dict(passed=True,real_https=True,seconds=time.monotonic()-started,result=result)
        (f.root/'report.json').write_bytes(data(report));print(data(report).decode())
    finally:f.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--case',default='full')
    a=p.parse_args();run(a.output,a.case)
