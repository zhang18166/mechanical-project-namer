# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166
"""Build source-matched Windows preview assets into a fresh external directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT).decode().strip()

def main(inputs: Path,output: Path,tag: str):
    if sys.version_info[:2]!=(3,12):raise RuntimeError('Windows Python 3.12 required')
    if git('status','--porcelain'):raise RuntimeError('Build requires a clean source checkout')
    commit=git('rev-parse','HEAD')
    if git('rev-parse',tag)!=commit:raise RuntimeError('Tag does not match HEAD')
    output=output.resolve();inputs=inputs.resolve();output.mkdir(parents=True,exist_ok=False)
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    def run(args,log):
        with (output/log).open('w',encoding='utf-8') as f:
            subprocess.run(args,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
    run([sys.executable,'-m','pytest','tests','-q','-p','no:cacheprovider','--basetemp',str(output/'pytest')],'test.log')
    run([sys.executable,'-m','PyInstaller','--noconfirm','--distpath',str(output/'dist'),
         '--workpath',str(output/'work'),'packaging/app.spec'],'build.log')
    payload=output/'dist/MechanicalProjectNamer'
    run([sys.executable,'packaging/collect_licenses.py',str(inputs),str(payload)],'licenses.log')
    assets=output/'assets';assets.mkdir()
    src=assets/f'MechanicalProjectNamer-{tag}-source.zip'
    subprocess.run(['git','archive','--format=zip','-o',str(src),commit],cwd=ROOT,check=True)
    shutil.copyfile(src,payload/'source.zip')
    for name in ['LICENSE','THIRD_PARTY_NOTICES.md']:
        shutil.copyfile(ROOT/name,payload/name)
    shutil.copyfile(ROOT/'packaging/INSTALL.md',payload/'INSTALL.md')
    info={'commit':commit,'tag':tag,'version':'1.6.1','architecture':'x64','python':sys.version.split()[0],
          'pyinstaller':'6.12.0','license':'GPL-3.0-only','signed':False}
    (payload/'build-info.json').write_text(json.dumps(info,indent=2),encoding='utf-8')
    run([str(payload/'MechanicalProjectNamer.exe'),'--self-test',str(output/'self-test.json')],'self-test.log')
    assert json.loads((output/'self-test.json').read_text(encoding='utf-8'))['passed']
    sources=json.loads((ROOT/'packaging/third-party-sources.json').read_text())
    bundle=assets/f'MechanicalProjectNamer-{tag}-third-party-sources.zip'
    with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_STORED) as z:
        z.write(ROOT/'packaging/third-party-sources.json','sources.json')
        for item in sources:
            p=inputs/item['file']
            if hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:raise RuntimeError('Source checksum mismatch')
            z.write(p,p.name)
    msi=assets/'MechanicalProjectNamer-1.6.1-preview.1-windows-x64.msi'
    run([sys.executable,'packaging/build_msi.py',str(payload),str(msi)],'msi.log')
    run([sys.executable,'packaging/verify_msi.py',str(msi),str(payload),str(output/'msi-check')],'msi-check.log')
    hashes=''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(assets.iterdir()))
    (assets/'SHA256SUMS.txt').write_text(hashes,encoding='utf-8')
    print('Release assets ready:',assets)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('inputs',type=Path);p.add_argument('output',type=Path);p.add_argument('--tag',required=True)
    a=p.parse_args();main(a.inputs,a.output,a.tag)
