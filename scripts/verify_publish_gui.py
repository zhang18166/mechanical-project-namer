# SPDX-License-Identifier: GPL-3.0-only
import argparse,json,subprocess,sys,time,uuid,hashlib,ctypes,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
def memory(pid):
 from ctypes import wintypes
 class C(ctypes.Structure):
  _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(x,ctypes.c_size_t) for x in ['peak_ws','ws','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile','private']]
 kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.OpenProcess.restype=wintypes.HANDLE;kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
 kernel.CloseHandle.argtypes=[wintypes.HANDLE]
 get=ctypes.WinDLL('psapi').GetProcessMemoryInfo;get.argtypes=[wintypes.HANDLE,ctypes.POINTER(C),wintypes.DWORD]
 handle=kernel.OpenProcess(0x410,False,pid);v=C();v.cb=ctypes.sizeof(v)
 try:
  if not handle or not get(handle,ctypes.byref(v),v.cb):raise RuntimeError('memory-sampling')
  return dict(working_set=v.ws,private_bytes=v.private)
 finally:
  if handle:kernel.CloseHandle(handle)
from mechanical_project_namer.publish_protocol import data
from mechanical_project_namer.publish_client import Client
from scripts.setup_publish_test import provision
from mechanical_project_namer.data import Database,Repository
from mechanical_project_namer.snapshots import SnapshotService
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from mechanical_project_namer.ui.publish_dialog import PublishDialog
p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);a=p.parse_args();E=a.evidence.absolute()
if E.is_relative_to(ROOT) or not (E/'report.json').is_file():raise ValueError('Existing fictional HTTP acceptance fixture required')
repo=Repository(Database(E/'client/db.sqlite'));snap=SnapshotService(repo,E/'client/archive')
rows=snap.history(repo.list_projects()[0].id);v1=next(r for r in rows if r['label']=='V1');v2=next(r for r in rows if r['label']=='V2')
provision(E/'gui-service',snap.store_id,v1['project_id']);process=None;service_pid=None
def start(fault=''):
 global process,service_pid
 ready=E/('gui-ready-'+uuid.uuid4().hex+'.json')
 process=subprocess.Popen([sys.executable,'-m','mechanical_project_namer.publish_server','--config',str(E/'gui-service/server.json'),'--ready',str(ready),'--fault',fault,'--deny-root',str(E/'client')],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 end=time.monotonic()+10
 while not ready.exists():
  if time.monotonic()>end:raise RuntimeError('service-start')
  QTest.qWait(20)
 info=json.loads(ready.read_bytes());port=info['port'];service_pid=info['pid']
 for name in ['publisher','reader','outsider']:
  f=E/'gui-service'/(name+'.json');cfg=json.loads(f.read_bytes());cfg['port']=port;f.write_bytes(data(cfg))
def wait(d):
 end=time.monotonic()+30
 while d.worker:
  if time.monotonic()>end:raise RuntimeError('GUI timeout')
  QTest.qWait(20)
 if '失败' in d.status.text():raise AssertionError(d.status.text())
app=QApplication([]);d=PublishDialog(snap,v1['task_id']);d.config.setText(str(E/'gui-service/publisher.json'));d.show();QTest.qWait(100)
result={'passed':False,'client_before':memory(os.getpid())};t=time.monotonic()
try:
 start();result['first_server_before']=memory(service_pid);d.publish();wait(d);assert d.remote;d.query();wait(d);d.target.setText(str(E/'gui-download-v1'));d.download();wait(d)
 d.grab().save(str(E/'gui-v1.png'));process.terminate();process.wait(10)
 start('after-rename');d.task=v2['task_id'];d.publish()
 end=time.monotonic()+30
 while d.worker and time.monotonic()<end:QTest.qWait(20)
 assert '失败' in d.status.text();process.wait(10);assert process.returncode==73
 start();d.publish();wait(d);cfg=json.loads((E/'gui-service/publisher.json').read_bytes());assert len(Client(cfg).versions())==2
 d.target.setText(str(E/'gui-download-v2'));d.download();wait(d);d.grab().save(str(E/'gui-v2.png'))
 assert (E/'gui-download-v1/中文 (演示).prt').read_bytes()==b'FICTIONAL SOURCE ONLY'
 assert (E/'gui-download-v2/中文 (演示).prt').read_bytes()==b'FICTIONAL V2'
 result.update(passed=True,visible=d.isVisible(),v1_v2=True,service_hard_restart=True,seconds=time.monotonic()-t,client_after=memory(os.getpid()),restarted_server_after=memory(service_pid))
finally:
 d.close()
 if process and process.poll() is None:process.terminate();process.wait(10)
 (E/'gui-report.json').write_bytes(data(result));print(data(result).decode())
