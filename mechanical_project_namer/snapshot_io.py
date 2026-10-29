# SPDX-License-Identifier: GPL-3.0-only
"""Bounded, read-only input and exclusive snapshot output. No CAD parser or Qt."""
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import unicodedata

from .project_files import local_path

MAX_FILE = 2 * 1024**3
MAX_TOTAL = 8 * 1024**3
MAX_FILES = 2000
MAX_PATH = 1024
MAX_MANIFEST = 4 * 1024**2
CHUNK = 1024**2
ROLES = ('source', 'exchange', 'preview', 'drawing', 'attachment')


class SnapshotError(ValueError): pass
class Cancelled(SnapshotError): pass


def invalid_component(part):
    stem=part.split('.')[0].upper()
    return (part in ('', '.', '..') or part.endswith(('.', ' ')) or
        re.search(r'[<>:"|?*\x00-\x1f]',part) or stem in ('CON','PRN','AUX','NUL','CONIN$','CONOUT$') or
        re.fullmatch(r'(COM|LPT)[1-9¹²³]',stem) or len(part.encode('utf-16-le'))//2>255)


def check_cancel(cancel):
    if cancel and cancel.is_set(): raise Cancelled('已取消；尚未提交的产物保留为未完成任务。')


def safe_path(value):
    raw = os.fspath(value)
    if '..' in PureWindowsPath(raw).parts or any(':' in p for p in PureWindowsPath(raw).parts[1:]):
        raise SnapshotError('拒绝路径越界或备用数据流。')
    if any(invalid_component(p) for p in PureWindowsPath(raw).parts[1:]):raise SnapshotError('拒绝设备或异常路径名。')
    path = local_path(value)
    if len(str(path).encode('utf-16-le'))//2 > MAX_PATH: raise SnapshotError('完整路径超过1024字符保护上限。')
    return path


def relative(value):
    if not isinstance(value, str) or not value or len(value) > 220 or '\\' in value:
        raise SnapshotError('清单相对路径无效。')
    win = PureWindowsPath(value)
    if win.drive or win.root or value.startswith('/'):
        raise SnapshotError('清单不能包含绝对路径。')
    parts = value.split('/')
    if len(parts)>32:raise SnapshotError('相对路径超过32层。')
    for part in parts:
        if invalid_component(part) or part.lower().startswith('.mw-'):
            raise SnapshotError('清单含越界、设备、保留或异常文件名。')
    return value


def collision_key(value): return unicodedata.normalize('NFC', relative(value)).casefold()


def validate_entries(files):
    if not isinstance(files,list) or not 0 < len(files) <= MAX_FILES:
        raise SnapshotError('文件数量必须为1至2000。')
    keys = set(); total = 0
    for f in files:
        key = collision_key(f['relative_path'])
        if key in keys: raise SnapshotError('Windows大小写或Unicode路径冲突。')
        keys.add(key)
        if f['role'] not in ROLES or type(f['size']) is not int or not 0 <= f['size'] <= MAX_FILE:
            raise SnapshotError('文件角色或2 GiB单文件限制不满足。')
        if not re.fullmatch('[0-9a-f]{64}',f['sha256']): raise SnapshotError('SHA-256字段损坏。')
        total += f['size']
    if total > MAX_TOTAL: raise SnapshotError('总容量超过8 GiB保护上限。')
    for key in keys:
        if any('/'.join(key.split('/')[:i]) in keys for i in range(1,len(key.split('/')))):
            raise SnapshotError('文件与目录路径冲突。')
    return total


def signature(info): return [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns]


def digest(path, *, target=None, cancel=None, progress=None):
    path = safe_path(path); before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE:
        raise SnapshotError('必须为可读普通文件，且不超过2 GiB。')
    h = hashlib.sha256(); count = 0
    with path.open('rb') as source:
        # Windows CPython fstat reports ctime differently from path stat on
        # newly written files. Compare identity/size/mtime across handles;
        # retain the full path signature before/after and in the frozen task.
        if signature(os.fstat(source.fileno()))[:4] != signature(before)[:4]: raise SnapshotError('读取前源文件变化。')
        out = None
        try:
            if target is not None:
                target = safe_path(target); target.parent.mkdir(parents=True,exist_ok=True)
                safe_path(target.parent); out = target.open('xb')
            while True:
                check_cancel(cancel); block = source.read(CHUNK)
                if not block: break
                count += len(block)
                if count > MAX_FILE: raise SnapshotError('文件读取超限。')
                h.update(block)
                if out: out.write(block)
                if progress: progress(count)
            if out: out.flush(); os.fsync(out.fileno())
            if signature(os.fstat(source.fileno()))[:4] != signature(before)[:4]: raise SnapshotError('读取期间源文件变化。')
        finally:
            if out: out.close()
    if signature(safe_path(path).stat()) != signature(before) or count != before.st_size:
        raise SnapshotError('读取期间文件身份、大小或时间变化。')
    return dict(size=count,sha256=h.hexdigest(),signature=signature(before))


def encoded(value):
    return json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')


def write_json(path, value):
    data=encoded(value)
    if len(data)>MAX_MANIFEST: raise SnapshotError('清单超过4 MiB。')
    with safe_path(path).open('xb') as out: out.write(data);out.flush();os.fsync(out.fileno())
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    path=safe_path(path)
    if path.stat().st_size>MAX_MANIFEST:raise SnapshotError('清单超过4 MiB。')
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise SnapshotError('JSON重复键。')
            result[k]=v
        return result
    with path.open('rb') as stream:data=stream.read(MAX_MANIFEST+1)
    if len(data)>MAX_MANIFEST:raise SnapshotError('清单超限。')
    try:return json.loads(data,object_pairs_hook=unique),hashlib.sha256(data).hexdigest()
    except (ValueError,UnicodeError,RecursionError) as exc:raise SnapshotError('JSON清单损坏。') from exc


def verify_tree(folder, files, *, receipt=False, cancel=None):
    validate_entries(files);folder=safe_path(folder); expected={f['relative_path'] for f in files}
    if receipt: expected.add('.mw-restore.json')
    actual=set()
    expected_dirs={str(Path(f).parent).replace('\\','/') for f in expected}
    expected_dirs={p for f in expected_dirs for p in [f,*[str(x).replace('\\','/') for x in Path(f).parents]] if p!='.'}
    for current,dirs,names in os.walk(folder,followlinks=False):
        for name in dirs+names:safe_path(Path(current)/name)
        for name in dirs:
            if (Path(current)/name).relative_to(folder).as_posix() not in expected_dirs:raise SnapshotError('出现清单以外的目录。')
        for name in names:
            actual.add((Path(current)/name).relative_to(folder).as_posix())
            if len(actual)>MAX_FILES+1:raise SnapshotError('归档目录文件数量超限。')
        if len(dirs)>MAX_FILES:raise SnapshotError('目录数量超限。')
        check_cancel(cancel)
    if actual!=expected:raise SnapshotError('归档文件缺失或出现未列入清单的文件。')
    for f in files:
        result=digest(folder/f['relative_path'],cancel=cancel)
        if (result['size'],result['sha256'])!=(f['size'],f['sha256']):raise SnapshotError('文件哈希或大小不匹配。')


def rename_new(source,target):
    source,target=safe_path(source),safe_path(target)
    if target.exists():raise SnapshotError('目标已存在，禁止覆盖或合并。')
    if source.stat().st_dev != target.parent.stat().st_dev:raise SnapshotError('提交必须在同一本地文件系统。')
    if os.name=='nt': os.rename(source,target)  # Windows rename refuses ANY existing destination.
    else:
        import ctypes
        lib=ctypes.CDLL(None,use_errno=True)
        fn=getattr(lib,'renameat2',None)
        if fn is None:raise SnapshotError('此系统缺少排他目录提交能力。')
        fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
        if fn(-100,os.fsencode(source),-100,os.fsencode(target),1):raise OSError(ctypes.get_errno(),'排他目录提交失败')
