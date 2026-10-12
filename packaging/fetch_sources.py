# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166
"""Download pinned upstream source archives without installing or executing them."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

parser=argparse.ArgumentParser()
parser.add_argument('destination',type=Path)
args=parser.parse_args()
args.destination.mkdir(parents=True,exist_ok=True)
manifest=json.loads(Path(__file__).with_name('third-party-sources.json').read_text())
for item in manifest:
    filename=Path(item['file'])
    if filename.name!=item['file']:raise ValueError('Invalid archive filename')
    target=args.destination/filename
    if not target.exists():
        with urllib.request.urlopen(item['url'],timeout=60) as response,target.open('wb') as out:
            while chunk:=response.read(1024*1024):out.write(chunk)
    if hashlib.sha256(target.read_bytes()).hexdigest()!=item['sha256']:
        raise ValueError('Source archive checksum mismatch: '+filename.name)
    print(filename.name)
