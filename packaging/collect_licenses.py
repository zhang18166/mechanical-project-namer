# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166
"""Collect original notices without executing downloaded source distributions."""
import argparse
import importlib.metadata as metadata
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile


def main(inputs: Path, payload: Path):
    dest = payload / 'licenses'
    dest.mkdir(parents=True, exist_ok=False)
    def put(rel, content):
        rel = PurePosixPath(str(rel).replace('\\', '/'))
        if rel.is_absolute() or '..' in rel.parts: raise ValueError('Unsafe path')
        target = dest / str(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    notice = re.compile(r'^(licen[cs]e|copying|notice|copyright)(?:[._-]|$)', re.I)
    inventory = []
    for d in sorted(metadata.distributions(), key=lambda d:d.metadata['Name'].lower()):
        name, version = d.metadata['Name'], d.version
        count = 0
        for f in d.files or []:
            if notice.match(f.name) and f.suffix.lower() not in ('.py','.pyc'):
                path = Path(d.locate_file(f))
                if path.is_file():
                    put(f'{name}-{version}/{f}', path.read_bytes())
                    count += 1
        inventory.append({'name':name,'version':version,'notice_files':count,
                          'license':d.metadata.get('License-Expression') or d.metadata.get('License')})
    for archive in sorted(inputs.glob('*.tar.*')):
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                rel = PurePosixPath(member.name)
                if not member.isfile(): continue
                if (notice.match(rel.name) or rel.name == 'qt_attribution.json'
                    or 'LICENSES' in rel.parts) and member.size < 2_000_000:
                    put(rel, tf.extractfile(member).read())
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    put('Python/LICENSE.txt', python_license.read_bytes())
    runtime_licenses = Path(__file__).with_name('runtime-licenses')
    for p in runtime_licenses.rglob('*'):
        if p.is_file():put('runtime/' + p.relative_to(runtime_licenses).as_posix(),p.read_bytes())
    required=['pyqt6-6.8.1/LICENSE','openpyxl-3.1.5/LICENCE.rst',
        'qtbase-everywhere-src-6.8.2/LICENSES/LGPL-3.0-only.txt',
        'qtbase-everywhere-src-6.8.2/LICENSES/GPL-3.0-only.txt',
        'runtime/OpenSSL-LICENSE.txt','runtime/libffi-LICENSE.txt']
    for name in required:
        if not (dest/name).is_file():raise ValueError('Missing required license: '+name)
    (dest/'environment-inventory.json').write_text(json.dumps(inventory,indent=2),encoding='utf-8')
    print('License files:',len([p for p in dest.rglob('*') if p.is_file()]))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('inputs',type=Path);p.add_argument('payload',type=Path)
    a=p.parse_args();main(a.inputs,a.payload)
