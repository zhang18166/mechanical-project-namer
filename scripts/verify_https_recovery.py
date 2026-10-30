# SPDX-License-Identifier: GPL-3.0-only
"""Full 4B stopped-source recovery and native Windows GUI through HTTPS."""
import argparse
from pathlib import Path
from scripts import verify_server_recovery
from scripts.https_harness import HTTPSService,fixture

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    verify_server_recovery.AuthFixture=fixture
    verify_server_recovery.ManagedService=HTTPSService
    verify_server_recovery.run(p.parse_args().output)
