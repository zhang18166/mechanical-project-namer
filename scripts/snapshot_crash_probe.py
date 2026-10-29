# SPDX-License-Identifier: GPL-3.0-only
"""Explicit test-only hard exit probe. No GUI/viewer/CAD imports or real default DB."""
import argparse,json,os,sys,importlib.abc
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
class DenyPreview(importlib.abc.MetaPathFinder):
    def find_spec(self,name,path=None,target=None):
        if name.startswith(('PyQt6.QtWebEngine','PyQt6.QtPdf','OCP')):raise ImportError('Preview/CAD dependency forbidden in archive probe')
sys.meta_path.insert(0,DenyPreview())
from mechanical_project_namer.data import Database,Repository
from mechanical_project_namer.snapshots import SnapshotService
parser=argparse.ArgumentParser();parser.add_argument('--db',type=Path,required=True);parser.add_argument('--store',type=Path,required=True)
parser.add_argument('--task',required=True);parser.add_argument('--point',default='');parser.add_argument('--restore-id');parser.add_argument('--destination',type=Path);parser.add_argument('--request',type=Path)
args=parser.parse_args()
# Probe only accepts a test opt-in marker next to an existing external test DB.
if args.db.is_relative_to(ROOT) or not (args.db.parent/'.snapshot-test-probe').is_file():raise ValueError('External test marker required')
def checkpoint(point):
    if point==args.point:os._exit(73)
service=SnapshotService(Repository(Database(args.db)),args.store,checkpoint=checkpoint)
if args.request:
    request=json.loads(args.request.read_text('utf-8'))
    service.prepare(request['project_id'],request['root'],request['selections'],request['label'],task_id=args.task)
if args.restore_id:
    result=service.restore(args.task,args.destination,restore_id=args.restore_id)
else:result=service.run(args.task)
assert not any(n.startswith(('PyQt6.QtWebEngine','PyQt6.QtPdf','OCP')) for n in sys.modules)
print(json.dumps({'state':result['state'],'version_id':result['version_id']}))
