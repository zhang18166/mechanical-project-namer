# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166
"""Inspect MSI and extract its embedded CAB without installing a product."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import subprocess
import warnings
warnings.filterwarnings('ignore',category=DeprecationWarning)
import msilib


def verify(msi: Path, payload: Path, output: Path):
    output.mkdir(parents=True,exist_ok=False)
    db=msilib.OpenDatabase(str(msi.resolve()),msilib.MSIDBOPEN_READONLY)
    def rows(table):
        view=db.OpenView(f'SELECT * FROM `{table}`');view.Execute(None)
        result=[]
        while record:=view.Fetch():
            result.append([record.GetString(i) for i in range(1,record.GetFieldCount()+1)])
        view.Close();return result
    props=dict(rows('Property'))
    assert props['ALLUSERS']=='2' and props['MSIINSTALLPERUSER']=='1'
    assert db.GetSummaryInformation(0).GetProperty(msilib.PID_TEMPLATE).startswith(b'x64;')
    for table in ['CustomAction','Registry','ServiceInstall','ServiceControl','Shortcut','Extension','Class']:
        assert not rows(table),table
    dirs={r[0]:(r[1],r[2].split('|')[-1]) for r in rows('Directory')}
    components={r[0]:r[2] for r in rows('Component')}
    def directory(ident):
        if ident=='INSTALLDIR':return Path('.')
        parent,name=dirs[ident]
        return directory(parent)/name
    mapped={r[0]:directory(components[r[1]])/r[2].split('|')[-1] for r in rows('File')}
    api=ctypes.WinDLL('msi')
    api.MsiOpenDatabaseW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR,ctypes.POINTER(wintypes.UINT)]
    api.MsiDatabaseOpenViewW.argtypes=[wintypes.UINT,wintypes.LPCWSTR,ctypes.POINTER(wintypes.UINT)]
    api.MsiViewExecute.argtypes=[wintypes.UINT,wintypes.UINT]
    api.MsiViewFetch.argtypes=[wintypes.UINT,ctypes.POINTER(wintypes.UINT)]
    api.MsiRecordReadStream.argtypes=[wintypes.UINT,wintypes.UINT,ctypes.c_void_p,ctypes.POINTER(wintypes.DWORD)]
    handles=[]
    def ok(code):
        if code:raise RuntimeError('MSI read error '+str(code))
    try:
        hdb=wintypes.UINT();ok(api.MsiOpenDatabaseW(str(msi.resolve()),None,ctypes.byref(hdb)));handles.append(hdb)
        hv=wintypes.UINT();ok(api.MsiDatabaseOpenViewW(hdb,"SELECT `Data` FROM `_Streams` WHERE `Name`='payload.cab'",ctypes.byref(hv)));handles.append(hv)
        ok(api.MsiViewExecute(hv,0));hr=wintypes.UINT();ok(api.MsiViewFetch(hv,ctypes.byref(hr)));handles.append(hr)
        cabinet=output/'payload.cab'
        with cabinet.open('wb') as f:
            while True:
                buffer=ctypes.create_string_buffer(1024*1024);size=wintypes.DWORD(len(buffer))
                ok(api.MsiRecordReadStream(hr,1,buffer,ctypes.byref(size)))
                if not size.value:break
                f.write(buffer.raw[:size.value])
    finally:
        for handle in reversed(handles):api.MsiCloseHandle(handle)
    extracted=output/'extracted';extracted.mkdir()
    subprocess.run(['expand.exe','-F:*',str(cabinet.resolve()),str(extracted.resolve())],check=True,stdout=subprocess.DEVNULL)
    for fid,relative in mapped.items():
        original=payload/relative;actual=extracted/fid
        assert hashlib.sha256(original.read_bytes()).digest()==hashlib.sha256(actual.read_bytes()).digest(),str(relative)
    assert len(list(extracted.iterdir()))==len(mapped)
    assert len([p for p in payload.rglob('*') if p.is_file()])==len(mapped)
    report={'passed':True,'architecture':'x64','scope':'per-user','files_verified':len(mapped),
            'system_install_performed':False,'custom_actions':0,'registry_rows':0,'services':0,
            'product_code':props['ProductCode'],'uninstall_sequence_present':any(r[0]=='RemoveFiles' for r in rows('InstallExecuteSequence'))}
    assert report['uninstall_sequence_present']
    (output/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('msi',type=Path);p.add_argument('payload',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();verify(a.msi,a.payload,a.output)
