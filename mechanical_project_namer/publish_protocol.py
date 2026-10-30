# SPDX-License-Identifier: GPL-3.0-only
"""Pure standard-library protocol. Never imports desktop/CAD modules."""
import hashlib,json,os,re,stat,unicodedata,uuid
from pathlib import Path,PureWindowsPath
MAX_FILE=16*1024**2
MAX_TOTAL=64*1024**2
MAX_FILES=100
MAX_JSON=2*1024**2
class ProtocolError(ValueError):pass
def uid(v):
    try:
        if str(uuid.UUID(v))!=v:raise ValueError()
        return v
    except Exception:raise ProtocolError('invalid-id') from None
def data(v):return json.dumps(v,ensure_ascii=True,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(v):return hashlib.sha256(data(v)).hexdigest()
def path(v):
    if not isinstance(v,str) or not 0<len(v)<=220 or '\\' in v:raise ProtocolError('invalid-path')
    w=PureWindowsPath(v);parts=v.split('/')
    if w.drive or w.root or len(parts)>32:raise ProtocolError('invalid-path')
    for p in parts:
        if p in ('','.','..') or p.endswith((' ','.')) or p.lower().startswith('.mw-') or re.search(r'[<>:"|?*\x00-\x1f]',p) or re.fullmatch(r'(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])',p.split('.')[0].rstrip(' .'),re.I):raise ProtocolError('invalid-path')
    return v
def manifest(m):
    if not isinstance(m,dict) or set(m)!={'store','local_project','version','label','files'}:raise ProtocolError('metadata-whitelist')
    uid(m['store']);uid(m['version'])
    if type(m['local_project']) is not int or m['local_project']<1:raise ProtocolError('project-id')
    if not isinstance(m['label'],str) or not 1<=len(m['label'])<=80 or re.search(r'[\\/:]',m['label']):raise ProtocolError('label')
    if not isinstance(m['files'],list) or not 1<=len(m['files'])<=MAX_FILES:raise ProtocolError('file-count')
    keys=set();total=0
    for f in m['files']:
        if set(f)!={'path','role','size','sha256'}:raise ProtocolError('file-whitelist')
        k=unicodedata.normalize('NFC',path(f['path'])).casefold()
        if PureWindowsPath(f['path']).suffix.lower() not in {'.sldprt','.sldasm','.slddrw','.prt','.step','.stp','.glb','.pdf','.stl','.txt','.csv'}:
            raise ProtocolError('file-type-not-publishable')
        if k in keys:raise ProtocolError('path-conflict')
        keys.add(k)
        if f['role'] not in ('source','exchange','preview','drawing','attachment') or type(f['size']) is not int or not 0<=f['size']<=MAX_FILE or not isinstance(f['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',f['sha256']):raise ProtocolError('file-limit')
        total+=f['size']
    if total>MAX_TOTAL or any('/'.join(k.split('/')[:i]) in keys for k in keys for i in range(1,len(k.split('/')))):raise ProtocolError('version-limit')
    return m
def safe(p):
    p=Path(os.path.abspath(p))
    for q in [*reversed(p.parents),p]:
        try:s=q.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&0x400:raise ProtocolError('redirected-path')
    return p
def digest(p):
    p=safe(p);h=hashlib.sha256();n=0
    with p.open('rb') as f:
        while b:=f.read(1024**2):
            n+=len(b)
            if n>MAX_FILE:raise ProtocolError('file-limit')
            h.update(b)
    return dict(size=n,sha256=h.hexdigest())
