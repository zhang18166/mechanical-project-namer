# SPDX-License-Identifier: GPL-3.0-only
"""User-prefix extraction + hash-locked venv. No dpkg install/apt/system service."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
from mechanical_project_namer.account_server import external

ROOT=Path(__file__).resolve().parents[1]

def artifact_hash(path):
    if path.stat().st_size>20*1024**2:raise ValueError('dependency-artifact-size-limit')
    h=hashlib.sha256()
    with path.open('rb') as stream:
        while b:=stream.read(65536):h.update(b)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--archives',type=Path,required=True);p.add_argument('--verify-only',action='store_true')
    a=p.parse_args()
    if os.name=='nt' or os.geteuid()==0:raise ValueError('nonprivileged-linux-required')
    os.umask(0o077);root=external(a.root);archives=external(a.archives)
    if not a.verify_only:
        root.mkdir(mode=0o700,parents=True,exist_ok=False);archives.mkdir(mode=0o700,parents=True,exist_ok=True)
    sources=json.loads((ROOT/'third_party/https/sources.json').read_bytes())
    for name in ('nginx','openssl'):
        v=sources[name];archive=external(archives/v['local_archive_name'])
        if not archive.exists() and not a.verify_only:
            h=hashlib.sha256();total=0;deadline=time.monotonic()+120
            with urllib.request.urlopen(v['url'],timeout=20) as src,archive.open('xb') as out:
                while b:=src.read(65536):
                    total+=len(b)
                    if total>20*1024**2 or time.monotonic()>deadline:raise ValueError('dependency-artifact-limit')
                    h.update(b);out.write(b)
            if h.hexdigest()!=v['sha256']:raise ValueError('dependency-integrity')
        if artifact_hash(archive)!=v['sha256']:raise ValueError('dependency-integrity')
        if not a.verify_only:subprocess.run(['dpkg-deb','-x',str(archive),str(root/v['extract_directory'])],check=True)
    if not a.verify_only:
        subprocess.run([sys.executable,'-m','venv',str(root/'venv')],check=True)
        subprocess.run([str(root/'venv/bin/python'),'-m','pip','install','--require-hashes','--only-binary=:all:',
                        '--index-url','https://pypi.org/simple','-r',str(ROOT/'requirements-https.lock')],check=True)
    env=dict(os.environ,LD_LIBRARY_PATH=str(root/'openssl/usr/lib/x86_64-linux-gnu'))
    subprocess.run([str(root/'nginx/usr/sbin/nginx'),'-V'],check=True,env=env)
    subprocess.run([str(root/'venv/bin/python'),'-m','pip','check'],check=True)
    print('private-https-dependencies-verified')

if __name__=='__main__':main()
