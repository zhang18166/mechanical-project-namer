# SPDX-License-Identifier: GPL-3.0-only
"""Acceptance child; credentials arrive only over a private stdin pipe."""
import argparse
import http.client
import json
import os
import sys
import time
from mechanical_project_namer.account_client import AccountSession
from mechanical_project_namer.data import Database,Repository
from mechanical_project_namer.snapshots import SnapshotService

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--instance',required=True);p.add_argument('--slow-upload',action='store_true');a=p.parse_args()
    job=json.load(sys.stdin)
    print(json.dumps(dict(pid=os.getpid(),instance=a.instance)),flush=True)
    session=AccountSession(job['connection']);session.login(job['username'],job['password'])
    if a.slow_upload:
        original=http.client.HTTPConnection.send
        def paced(self,b):
            if isinstance(b,bytes) and len(b)>=65536:time.sleep(.05)
            return original(self,b)
        http.client.HTTPConnection.send=paced
    snapshots=SnapshotService(Repository(Database(job['database'])),job['archive'])
    result=session.publisher(job['project']).publish(snapshots,job['task'])
    print(json.dumps(dict(complete=result['state']=='complete',task=result['task'])),flush=True)
