# SPDX-License-Identifier: GPL-3.0-only
"""Loopback health + stable service UUID; consumes public connection JSON only."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mechanical_project_namer.account_client import AccountSession
from mechanical_project_namer.publish_protocol import data,safe

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--connection',type=Path,required=True);a=p.parse_args()
    try:print(data(AccountSession(json.loads(safe(a.connection).read_bytes())).verify_service()).decode())
    except Exception:print('health-or-service-identity-failed',file=sys.stderr);raise SystemExit(2)
