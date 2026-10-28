# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""Read-only local CAD catalog. Reuses projects.id and existing drawing identities.

Scanning never writes SQLite or source files. A complete batch is committed in one
transaction; the older file_sync registration/number-allocation flow is untouched.
"""
import os
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

EXTENSIONS = ('.sldprt', '.sldasm', '.slddrw', '.step', '.stp', '.stl', '.glb', '.pdf')
STATE_LABELS = dict(present='可用', missing='文件失踪', unreadable='不可读取', blocked='路径链接已阻止')


class CatalogError(ValueError):
    pass


class ScanCancelled(CatalogError):
    pass


def _network_drive(anchor):
    if os.name != 'nt':
        return False
    import ctypes
    return ctypes.windll.kernel32.GetDriveTypeW(str(anchor)) == 4


def local_path(value) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw.strip() or '\x00' in raw:
        raise CatalogError('文件路径无效。')
    if raw.startswith(('\\\\', '//', '\\\\?\\', '\\\\.\\')):
        raise CatalogError('本阶段只支持本地路径，不扫描网络共享或设备路径。')
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise CatalogError('请选择绝对路径。')
    path = Path(os.path.abspath(path))  # lexical normalization, never resolve links
    if _network_drive(path.anchor):
        raise CatalogError('不扫描映射网络盘，请选择本机磁盘。')
    if path.parent == path:
        raise CatalogError('请选择具体文件夹，不能扫描磁盘根目录。')
    for parent in (*reversed(path.parents), path):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024:
            raise CatalogError('路径含符号链接、目录联接或云端占位文件，已阻止访问。')
    return path


def path_key(value) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(value)))


@dataclass(frozen=True)
class Source:
    path: str
    kind: str


@dataclass(frozen=True)
class FileEntry:
    path: str
    name: str
    extension: str
    file_size: int
    mtime_ns: int
    state: str
    error: str = ''


@dataclass
class ScanBatch:
    project_id: int
    sources: list[Source] = field(default_factory=list)
    entries: dict[str, FileEntry] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    complete: bool = True
    visited: int = 0


def inspect_file(value, previous=None) -> FileEntry:
    path = Path(value)
    size, mtime = (previous['file_size'], previous['mtime_ns']) if previous else (0, 0)
    try:
        path = local_path(path)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise CatalogError('目标不再是普通文件。')
        size, mtime = info.st_size, info.st_mtime_ns
        with path.open('rb'):
            pass  # access check only; never writes or reads document contents
        state, error = 'present', ''
    except FileNotFoundError:
        state, error = 'missing', '未找到原文件；保留索引，请恢复文件后重新扫描。'
    except CatalogError as exc:
        state, error = 'blocked', str(exc)
    except OSError as exc:
        state, error = 'unreadable', f'无法读取文件（{type(exc).__name__}）。'
    return FileEntry(str(path), path.name, path.suffix.lower(), size, mtime, state, error)


class ProjectFileCatalog:
    def __init__(self, repository):
        self.repository = repository
        self.database = repository.database

    def list_files(self, project_id=None, query='', extension=''):
        clauses, values = [], []
        if project_id is not None:
            clauses.append('f.project_id=?'); values.append(project_id)
        if extension:
            clauses.append('f.extension=?'); values.append(extension.lower())
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.database.connect() as con:
            rows = [dict(row) for row in con.execute('''SELECT f.*, p.name AS project_name,
                p.code AS project_code, r.filename AS record_number FROM project_files f
                JOIN projects p ON p.id=f.project_id LEFT JOIN drawing_records r ON r.id=f.record_id'''
                + where + ' ORDER BY f.name COLLATE NOCASE, f.file_id', values)]
        key = query.strip().casefold()
        return [row for row in rows if not key or key in ' '.join(str(row.get(k) or '') for k in
                ('name', 'extension', 'path', 'project_name', 'project_code', 'record_number')).casefold()]

    def sources(self, project_id):
        with self.database.connect() as con:
            return [Source(row['path'], row['kind']) for row in con.execute(
                'SELECT path,kind FROM project_file_sources WHERE project_id=? ORDER BY source_id', (project_id,))]

    def scan(self, project_id, sources=(), *, refresh=False, cancel=None, progress=None,
             max_entries=10000, seconds=15, max_depth=32):
        self.repository.get_project(project_id)
        cancel = cancel or Event()
        batch = ScanBatch(project_id)
        old = {row['path_key']: row for row in self.list_files(project_id)}
        chosen = list(sources) if not refresh else self.sources(project_id) + list(sources)
        if len(chosen) > 128:
            batch.complete = False; batch.warnings.append('扫描来源超过128项，请分批添加。'); return batch
        start = time.monotonic()

        def check():
            if cancel.is_set():
                raise ScanCancelled('扫描已取消，未提交本批索引。')
            if batch.visited > max_entries or time.monotonic() - start > seconds:
                raise CatalogError('已达到扫描数量或时间上限，本批未提交；请选择更小的目录后重试。')

        def add_file(path):
            if path.suffix.lower() in EXTENSIONS:
                key = path_key(path)
                batch.entries[key] = inspect_file(path, old.get(key))

        try:
            seen = set()
            for source in chosen:
                check()
                if source.kind not in {'file', 'directory'}:
                    raise CatalogError('扫描来源类型无效。')
                root = local_path(source.path)
                root_key = path_key(root)
                if root_key in seen:
                    continue
                seen.add(root_key); batch.sources.append(Source(str(root), source.kind))
                if source.kind == 'file':
                    if root.suffix.lower() not in EXTENSIONS:
                        raise CatalogError('不支持此文件类型。')
                    batch.visited += 1; check(); add_file(root)
                    continue
                stack = [(root, 0)]
                while stack:
                    folder, depth = stack.pop(); check()
                    try:
                        local_path(folder)
                        with os.scandir(folder) as iterator:
                            for item in iterator:
                                batch.visited += 1; check()
                                if progress and batch.visited % 32 == 0:
                                    progress(batch.visited)
                                path = Path(item.path)
                                try:
                                    local_path(path)
                                    if item.is_dir(follow_symlinks=False):
                                        if depth >= max_depth:
                                            raise CatalogError('已达到目录层级上限，本批未提交。')
                                        stack.append((path, depth + 1))
                                    else:
                                        add_file(path)
                                except CatalogError as exc:
                                    if '层级上限' in str(exc):
                                        raise
                                    batch.warnings.append(f'{path.name}：{exc}')
                                    if path_key(path) in old:
                                        add_file(path)
                                except OSError as exc:
                                    batch.warnings.append(f'{path.name}：{type(exc).__name__}')
                                    add_file(path)
                    except (OSError, CatalogError) as exc:
                        if isinstance(exc, CatalogError) and '上限' in str(exc):
                            raise
                        batch.warnings.append(f'{folder.name}：{exc}')
            if refresh:
                # Include disappeared/inaccessible paths; absence never deletes an index.
                for key, row in old.items():
                    check()
                    if key not in batch.entries:
                        batch.entries[key] = inspect_file(row['path'], row)
            check()
        except (CatalogError, OSError) as exc:
            batch.complete = False
            batch.warnings.append(str(exc))
        return batch

    def commit(self, batch, *, cancel=None):
        if not batch.complete:
            raise CatalogError('扫描未完整结束，禁止提交：' + '；'.join(batch.warnings[-3:]))
        cancel = cancel or Event()
        def check():
            if cancel.is_set():
                raise ScanCancelled('操作已取消，本批索引已回滚。')
        # Detect files changing between scan and commit, before taking SQLite's writer lock.
        for entry in batch.entries.values():
            check()
            if entry.state == 'present':
                current = inspect_file(entry.path)
                if (current.state, current.file_size, current.mtime_ns) != ('present', entry.file_size, entry.mtime_ns):
                    raise CatalogError('扫描后文件发生变化，请重新扫描；本批未提交。')
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with self.database.transaction() as con:
            if not con.execute('SELECT 1 FROM projects WHERE id=?', (batch.project_id,)).fetchone():
                raise CatalogError('项目已不存在。')
            for source in batch.sources:
                check()
                con.execute('''INSERT INTO project_file_sources(project_id,path,path_key,kind) VALUES (?,?,?,?)
                    ON CONFLICT(project_id,path_key) DO UPDATE SET kind=excluded.kind''',
                    (batch.project_id, source.path, path_key(source.path), source.kind))
            known = {}
            for row in con.execute('SELECT id,file_path FROM drawing_records WHERE project_id=?', (batch.project_id,)):
                if row['file_path']:
                    known.setdefault(path_key(row['file_path']), set()).add(row['id'])
            for row in con.execute('''SELECT f.record_id,f.path FROM record_files f JOIN drawing_records r
                    ON r.id=f.record_id WHERE r.project_id=?''', (batch.project_id,)):
                known.setdefault(path_key(row['path']), set()).add(row['record_id'])
            for key, entry in batch.entries.items():
                check()
                # Reuse exact known legacy links, never infer or allocate a drawing number.
                record_ids = known.get(key, set())
                record_id = next(iter(record_ids)) if len(record_ids) == 1 else None
                con.execute('''INSERT INTO project_files(project_id,record_id,path,path_key,name,extension,
                    file_size,mtime_ns,state,error,checked_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(project_id,path_key) DO UPDATE SET name=excluded.name,extension=excluded.extension,
                    file_size=excluded.file_size,mtime_ns=excluded.mtime_ns,state=excluded.state,
                    error=excluded.error,checked_at=excluded.checked_at,
                    record_id=CASE WHEN project_files.association_manual=1 THEN project_files.record_id
                        ELSE COALESCE(project_files.record_id,excluded.record_id) END''',
                    (batch.project_id, record_id, entry.path, key, entry.name, entry.extension,
                     entry.file_size, entry.mtime_ns, entry.state, entry.error, now))
            check()
        return len(batch.entries)

    def associate_record(self, file_id, record_id):
        with self.database.transaction() as con:
            file = con.execute('SELECT project_id FROM project_files WHERE file_id=?', (file_id,)).fetchone()
            if file is None:
                raise CatalogError('文件索引已不存在。')
            if record_id is not None and not con.execute(
                    'SELECT 1 FROM drawing_records WHERE id=? AND project_id=?', (record_id, file['project_id'])).fetchone():
                raise CatalogError('只能关联本项目已有的零件记录。')
            con.execute('UPDATE project_files SET record_id=?,association_manual=1 WHERE file_id=?', (record_id, file_id))
