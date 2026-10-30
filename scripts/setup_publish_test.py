# SPDX-License-Identifier: GPL-3.0-only
"""Explicit local test provisioning, never a public registration route."""
import argparse,hashlib,json,secrets,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from mechanical_project_namer.publish_protocol import data,safe,uid
def provision(root,store,project):
    root=safe(root)
    if root.exists() or root.is_relative_to(ROOT):raise ValueError('New external test root required')
    if any((p/'.git').exists() for p in root.parents):raise ValueError('Credentials must stay outside all repositories')
    root.mkdir(parents=True);remote=str(uuid.uuid4());service=str(uuid.uuid4());identities={}
    for name,grants in [('publisher',['read','publish','download']),('reader',['read']),('outsider',[])]:
        token=secrets.token_urlsafe(32);identities[name]=dict(token_hash=hashlib.sha256(token.encode()).hexdigest(),grants={remote:grants})
        (root/(name+'.json')).write_bytes(data(dict(port=1,service=service,project=remote,token=token)))
    (root/'server.json').write_bytes(data(dict(service=service,index=str(root/'index'/'index.db'),storage=str(root/'storage'),projects={remote:dict(store=uid(store),local_project=project)},identities=identities)))
    return remote
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--store',required=True);p.add_argument('--project',type=int,required=True);a=p.parse_args();provision(a.root,a.store,a.project)
