# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166
"""Build a per-user x64 MSI with the CPython 3.12 Windows Installer library.

No custom executable actions, services, associations, scheduled tasks or autorun.
The package can be uninstalled through Windows Installed Apps.
"""
import argparse
import hashlib
from pathlib import Path
import uuid
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
import msilib
from msilib import schema, sequence


def build(payload: Path, output: Path):
    payload = payload.resolve()
    output = output.resolve()
    if output.exists():
        raise ValueError("Refusing to overwrite an existing installer")
    files = sorted(p for p in payload.rglob("*") if p.is_file())
    if not (payload / "MechanicalProjectNamer.exe").is_file():
        raise ValueError("Missing executable")
    if not (payload / "LICENSE").is_file() or not (payload / "licenses").is_dir():
        raise ValueError("License payload missing")
    db = msilib.init_database(str(output), schema, "Mechanical Project Namer (Preview)",
        "{CD37DAB3-5BD3-5C77-B58A-093AF4F3E78D}", "1.6.1", "zhang18166")
    msilib.add_tables(db, sequence)
    msilib.add_data(db, "Property", [
        ("UpgradeCode", "{C01B6E1F-66E6-5EA9-A964-644BB9637C31}"),
        ("ALLUSERS", "2"), ("MSIINSTALLPERUSER", "1"),
        ("ARPNOMODIFY", "1"), ("ARPNOREPAIR", "1"),
        ("ARPURLINFOABOUT", "https://github.com/zhang18166/mechanical-project-namer"),
        ("INSTALLLEVEL", "1")])
    si = db.GetSummaryInformation(20)
    si.SetProperty(msilib.PID_WORDCOUNT, 10)  # compressed + no elevation required
    si.Persist()
    msilib.add_data(db, "LaunchCondition", [("VersionNT64", "Windows x64 is required.")])
    msilib.add_data(db, "Directory", [
        ("TARGETDIR", None, "SourceDir"),
        ("LocalAppDataFolder", "TARGETDIR", "."),
        ("ProgramsDir", "LocalAppDataFolder", "Programs"),
        ("INSTALLDIR", "ProgramsDir", "MPNPRV~1|MechanicalProjectNamer-Preview")])
    msilib.add_data(db, "Feature", [("MainFeature", None, "Application", "Application and licenses", 1, 1, "INSTALLDIR", 0)])
    cabinet = msilib.CAB("payload.cab")
    dirs = {Path('.'): "INSTALLDIR"}
    for i, parent in enumerate(sorted({p.parent.relative_to(payload) for p in files}, key=lambda p: (len(p.parts), str(p)))):
        if parent == Path('.'): continue
        ancestors = list(reversed(parent.parents)) + [parent]
        for sub in ancestors:
            if sub == Path('.') or sub in dirs: continue
            ident = 'D' + hashlib.sha256(sub.as_posix().encode()).hexdigest()[:24]
            dirs[sub] = ident
            msilib.add_data(db, 'Directory', [(ident, dirs[sub.parent], sub.name)])
    for index, path in enumerate(files, 1):
        rel = path.relative_to(payload)
        fid, cid = f'F{index:05}', f'C{index:05}'
        guid = '{' + str(uuid.uuid5(uuid.NAMESPACE_URL, 'https://github.com/zhang18166/mechanical-project-namer/preview/' + rel.as_posix())).upper() + '}'
        msilib.add_data(db, 'Component', [(cid, guid, dirs[rel.parent], 256, None, fid)])
        msilib.add_data(db, 'FeatureComponents', [('MainFeature', cid)])
        seq, _ = cabinet.append(str(path), path.name, fid)
        msilib.add_data(db, 'File', [(fid, cid, path.name, path.stat().st_size, None, None, 512, seq)])
    cabinet.commit(db)
    db.Commit()
    print(f'MSI created: {output.name}; {len(files)} payload files')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('payload', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    build(args.payload, args.output)
