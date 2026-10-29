# SPDX-License-Identifier: GPL-3.0-only
"""Test-only hard exit helper; external opt-in marker, no preview/CAD processes."""
import argparse
import importlib.abc
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
class NoPreview(importlib.abc.MetaPathFinder):
    def find_spec(self,name,path=None,target=None):
        if name.startswith(('PyQt6.QtWebEngine','PyQt6.QtPdf','OCP')):raise ImportError('Archive maintenance must not load preview/CAD components')
sys.meta_path.insert(0,NoPreview())
from mechanical_project_namer.library_backups import LibraryBackups
parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['backup','restore'],required=True)
parser.add_argument('--source',type=Path,required=True);parser.add_argument('--destination',type=Path,required=True)
parser.add_argument('--task',required=True);parser.add_argument('--point',default='');args=parser.parse_args()
destination=args.destination.absolute()
if destination.is_relative_to(ROOT) or not (destination.parent/'.library-backup-probe').is_file():raise ValueError('External test opt-in marker required')
service=LibraryBackups(checkpoint=lambda p:os._exit(73) if p==args.point else None)
fn=service.create if args.mode=='backup' else service.restore
result=fn(args.source,destination,task_id=args.task)
assert not any(n.startswith(('PyQt6.QtWebEngine','PyQt6.QtPdf','OCP')) for n in sys.modules)
print(json.dumps({'id':result.get('backup_id'),'state':result.get('state','complete')}))
