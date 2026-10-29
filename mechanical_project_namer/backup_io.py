# SPDX-License-Identifier: GPL-3.0-only
"""Private directory backup format: bounded metadata, streaming files, read-only SQLite."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import stat
import unicodedata
import uuid

from .snapshot_io import SnapshotError, Cancelled, safe_path, invalid_component, signature, check_cancel, encoded

MAX_FILES = 100_000
MAX_TOTAL = 256 * 1024**3
MAX_FILE = 32 * 1024**3
MAX_METADATA = 64 * 1024**2
CHUNK = 1024**2


def relative_path(value):
    if not isinstance(value, str) or not value or len(value) > 512 or '\\' in value:
        raise SnapshotError('备份相对路径无效或超过512字符。')
    win = PureWindowsPath(value)
    parts = value.split('/')
    if win.drive or win.root or len(parts) > 48 or any(invalid_component(p) for p in parts):
        raise SnapshotError('备份路径含绝对路径、越界、设备或异常名称。')
    return value


def check_entries(entries):
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_FILES:
        raise SnapshotError('全库文件数限制为1至100000。')
    keys = set(); total = 0
    for entry in entries:
        key = unicodedata.normalize('NFC', relative_path(entry['path'])).casefold()
        if key in keys:
            raise SnapshotError('备份Windows大小写/Unicode路径冲突。')
        keys.add(key)
        if type(entry['size']) is not int or not 0 <= entry['size'] <= MAX_FILE:
            raise SnapshotError('全库单文件超过32 GiB或大小无效。')
        if not re.fullmatch('[0-9a-f]{64}', entry['sha256']):
            raise SnapshotError('备份SHA-256字段无效。')
        total += entry['size']
    if total > MAX_TOTAL:
        raise SnapshotError('全库总容量超过256 GiB。')
    if any('/'.join(k.split('/')[:i]) in keys for k in keys for i in range(1, len(k.split('/')))):
        raise SnapshotError('备份文件与目录路径冲突。')
    return total


def copy_hash(source, target=None, *, cancel=None, progress=None):
    source = safe_path(source); before = source.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE:
        raise SnapshotError('备份只接受普通文件，单文件不超过32 GiB。')
    if before.st_nlink != 1:
        raise SnapshotError('备份文件必须是独立副本，拒绝硬链接。')
    count = 0; h = hashlib.sha256(); output = None
    with source.open('rb') as stream:
        if signature(os.fstat(stream.fileno()))[:4] != signature(before)[:4]:
            raise SnapshotError('备份读取前文件身份变化。')
        try:
            if target is not None:
                target = safe_path(target); target.parent.mkdir(parents=True, exist_ok=True)
                safe_path(target.parent); output = target.open('xb')
            while True:
                check_cancel(cancel); block = stream.read(CHUNK)
                if not block:
                    break
                count += len(block)
                if count > MAX_FILE:
                    raise SnapshotError('全库文件读取超限。')
                h.update(block)
                if output:
                    output.write(block)
                if progress:
                    progress(count)
            if output:
                output.flush(); os.fsync(output.fileno())
            if signature(os.fstat(stream.fileno()))[:4] != signature(before)[:4]:
                raise SnapshotError('备份读取期间文件变化。')
        finally:
            if output:
                output.close()
    if signature(safe_path(source).stat()) != signature(before) or count != before.st_size:
        raise SnapshotError('备份读取期间文件内容、身份或时间变化。')
    return dict(size=count, sha256=h.hexdigest())


def write_metadata(path, value, *, replace=False):
    data = encoded(value)
    if len(data) > MAX_METADATA:
        raise SnapshotError('备份元数据超过64 MiB。')
    path = safe_path(path)
    actual = path.with_name('.mw-json-' + uuid.uuid4().hex) if replace else path
    with actual.open('xb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    if replace:
        os.replace(actual, path)  # only the already-owned task journal
    return hashlib.sha256(data).hexdigest()


def read_metadata(path):
    path = safe_path(path)
    if path.stat().st_nlink != 1:
        raise SnapshotError('备份元数据不能使用硬链接。')
    if path.stat().st_size > MAX_METADATA:
        raise SnapshotError('备份元数据超过64 MiB。')
    with path.open('rb') as stream:
        data = stream.read(MAX_METADATA + 1)
    if len(data) > MAX_METADATA:
        raise SnapshotError('备份元数据读取超限。')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SnapshotError('备份JSON含重复键。')
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=unique), hashlib.sha256(data).hexdigest()
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SnapshotError('备份JSON损坏。') from exc


def tree_files(root, *, cancel=None):
    root = safe_path(root); result = set(); directories = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        check_cancel(cancel)
        directories += len(dirs)
        if directories > MAX_FILES * 48:
            raise SnapshotError('全库目录数量超限。')
        for name in dirs + names:
            safe_path(Path(current) / name)
        for name in names:
            result.add(relative_path((Path(current) / name).relative_to(root).as_posix()))
            if len(result) > MAX_FILES:
                raise SnapshotError('全库文件数量超限。')
    return result


def verify_files(root, entries, *, cancel=None):
    check_entries(entries)
    expected = {e['path'] for e in entries}
    if tree_files(root, cancel=cancel) != expected:
        raise SnapshotError('备份缺失文件或出现异常额外文件。')
    expected_dirs = {str(p).replace('\\', '/') for e in entries for p in Path(e['path']).parents if str(p) != '.'}
    for current, dirs, _ in os.walk(root, followlinks=False):
        for name in dirs:
            if (Path(current) / name).relative_to(root).as_posix() not in expected_dirs:
                raise SnapshotError('备份出现异常额外目录。')
    for entry in entries:
        if copy_hash(Path(root) / entry['path'], cancel=cancel) != {k:entry[k] for k in ('size','sha256')}:
            raise SnapshotError('备份文件大小或SHA-256不一致：' + entry['path'])


@contextmanager
def readonly_database(path, *, immutable=True, cancel=None):
    path = safe_path(path)
    if not path.is_file():
        raise SnapshotError('数据库不存在；不会创建空库代替。')
    uri = path.as_uri() + '?mode=ro' + ('&immutable=1' if immutable else '')
    con = sqlite3.connect(uri, uri=True, timeout=0)
    try:
        con.row_factory = sqlite3.Row
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 16 * 1024**2)
        con.execute('PRAGMA query_only=ON'); con.execute('PRAGMA trusted_schema=OFF')
        if cancel:
            con.set_progress_handler(lambda: int(cancel.is_set()), 10000)
        yield con
    except sqlite3.OperationalError:
        check_cancel(cancel)
        raise
    finally:
        con.close()


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def database_fingerprints(con, *, transforms=None, skip_meta=(), cancel=None):
    """Stream rows, including SQLite sequences; no full-database read into Python memory."""
    result = {}; transforms = transforms or {}
    for table, in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        h = hashlib.sha256(); count = 0
        order = 'key' if table == 'snapshot_meta' else 'rowid'
        for row in con.execute(f'SELECT * FROM {quote(table)} ORDER BY {quote(order)}'):
            check_cancel(cancel)
            value = dict(row)
            if table == 'snapshot_meta' and value['key'] in skip_meta:
                continue
            if table in transforms:
                value = transforms[table](value)
            for key, item in value.items():
                if isinstance(item, bytes):
                    value[key] = {'sqlite_blob_hex': item.hex()}
            h.update(encoded(value)); h.update(b'\n'); count += 1
            if count > 2_000_000:
                raise SnapshotError('单表超过200万行保护上限。')
        result[table] = dict(rows=count, sha256=h.hexdigest())
    h = hashlib.sha256()
    for row in con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"):
        h.update(encoded(tuple(row))); h.update(b'\n')
    result['__schema__'] = h.hexdigest()
    return result
