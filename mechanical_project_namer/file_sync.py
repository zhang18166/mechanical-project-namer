# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""Read-only directory discovery; explicit, transactional registration of files."""
from dataclasses import dataclass, field
from pathlib import Path
import os
import re
import stat
import time

from .numbering import PART_TYPES
from .numbering.simple import SIMPLE_TYPE, module_code as normalize_module

EXTENSIONS = {'.sldprt', '.sldasm', '.slddrw', '.pdf', '.dwg', '.dxf', '.step', '.stp', '.igs', '.iges', '.x_t', '.x_b'}
SKIP_DIRS = {'.git', '.venv', '__pycache__', 'backup', 'backups', 'temp', 'tmp', '缓存', '备份', '回滚'}
READY = {'可登记', '可关联'}


class ExternalFilesNeedSync(ValueError):
    pass


@dataclass(frozen=True)
class FileStamp:
    path: str
    size: int
    mtime_ns: int


@dataclass
class SyncEntry:
    number: str
    module: str
    sequence: int
    name: str
    part_type: str
    files: list[FileStamp]
    status: str = '可登记'
    detail: str = ''
    record_id: int | None = None
    manual: bool = False


@dataclass
class ScanResult:
    project_id: int
    roots: tuple[str, ...]
    entries: list[SyncEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    visited: int = 0


def path_key(path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def category(sequence: int) -> str:
    return next((name for name, _, start, end in PART_TYPES if start <= sequence <= end), '')


def is_reparse(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024))


def project_roots(project, extras=()) -> tuple[str, ...]:
    roots = []
    for text in (project.directory, project.solidworks_part_directory, *extras):
        if not str(text).strip():
            continue
        p = Path(text).expanduser()
        if not p.is_absolute():
            raise ValueError('项目目录必须是绝对路径，请先在项目设置中核对。')
        if p.exists() and any(is_reparse(parent) for parent in (p, *p.parents)):
            raise ValueError('项目目录包含链接或云端占位目录，请选择实际可用的项目文件夹。')
        resolved = p.resolve()
        if resolved.parent == resolved:
            raise ValueError('请设置具体项目文件夹，不能扫描整个磁盘根目录。')
        if path_key(resolved) not in {path_key(r) for r in roots}:
            roots.append(str(resolved))
    return tuple(roots)


def scan_project(repository, project_id: int, *, overrides=None, extras=(), cancel=None, max_entries=12000, seconds=12) -> ScanResult:
    project = repository.get_project(project_id)
    result = ScanResult(project_id, project_roots(project, extras))
    overrides = overrides or {}
    started = time.monotonic()
    pattern = re.compile(r'^' + re.escape(project.code) + r'-([A-Z0-9]{1,12})-(\d{3})(?:([-\s])(.+))?$', re.I)
    with repository.database.connect() as c:
        records = [dict(r) for r in c.execute('SELECT * FROM drawing_records WHERE project_id=?', (project_id,))]
        linked = {path_key(r['path']): dict(r) for r in c.execute('SELECT f.*,r.filename,r.module_code,r.part_sequence,r.part_name,r.part_type FROM record_files f JOIN drawing_records r ON r.id=f.record_id WHERE r.project_id=?', (project_id,))}
        modules = {r['module_code'] for r in c.execute('SELECT module_code FROM project_modules WHERE project_id=? AND enabled=1', (project_id,))}
        reservations = {(r['module_code'], r['part_sequence']) for r in c.execute("SELECT module_code,part_sequence FROM number_reservations WHERE project_id=? AND status<>'released'", (project_id,))}
        operations = {(r['module_code'], r['part_sequence']) for r in c.execute("SELECT module_code,part_sequence FROM solidworks_operations WHERE project_id=? AND status<>'failed'", (project_id,))}
    by_number = {}
    paths_by_record = {}
    for record in records:
        if record['file_path']:
            linked.setdefault(path_key(record['file_path']), {**record, 'record_id': record['id']})
        numbers = {record['filename'].casefold()}
        if record['part_type'] != 'legacy':
            numbers.add(f"{project.code}-{record['module_code']}-{record['part_sequence']:03d}".casefold())
        for number in numbers:
            by_number.setdefault(number, []).append(record)
    for key, link in linked.items():
        paths_by_record.setdefault(link['record_id'], set()).add(key)
    groups = {}
    seen = set()
    seen_dirs = set()
    stack = [Path(r) for r in reversed(result.roots)]
    while stack:
        if (cancel and cancel.is_set()) or result.visited >= max_entries or time.monotonic() - started > seconds:
            result.errors.append('扫描已取消或达到数量/时间限额；请缩小项目目录后重试。')
            break
        directory = stack.pop()
        try:
            if is_reparse(directory):
                result.errors.append(f'目录为链接或云端占位目录，未跟随扫描：{directory}')
                continue
            key = path_key(directory)
            if key in seen_dirs:
                continue
            seen_dirs.add(key)
            with os.scandir(directory) as items:
                for item in items:
                    result.visited += 1
                    if result.visited > max_entries or (cancel and cancel.is_set()) or time.monotonic() - started > seconds:
                        raise TimeoutError('扫描达到数量/时间限额或已取消')
                    p = Path(item.path)
                    if is_reparse(p):
                        result.errors.append(f'未跟随链接或云端占位路径：{p}')
                        continue
                    if item.is_dir(follow_symlinks=False):
                        if item.name.lower() not in SKIP_DIRS:
                            stack.append(p)
                        continue
                    if p.suffix.lower() not in EXTENSIONS or p.name.startswith(('~$', '.~', '$')):
                        continue
                    key = path_key(p)
                    if key in seen:
                        continue
                    seen.add(key)
                    info = p.stat()
                    stamp = FileStamp(str(p.resolve()), info.st_size, info.st_mtime_ns)
                    match = pattern.fullmatch(p.stem)
                    manual = False
                    part_type = ''
                    if key in linked:
                        # A saved association survives edits to device codes or record notes.
                        # Registration never renames the physical file.
                        old = linked[key]
                        module, sequence, name = old['module_code'], old['part_sequence'], old['part_name']
                        part_type = old['part_type']
                    elif key in overrides:
                        override = overrides[key]
                        number, name = override[:2]
                        simple_override = len(override) > 2 and override[2] == SIMPLE_TYPE
                        custom = pattern.fullmatch(number + ('-' if simple_override else ' ') + name)
                        if not custom:
                            raise ValueError('手动登记的图号不属于当前项目或格式不正确。')
                        module, sequence, name = normalize_module(custom.group(1)), int(custom.group(2)), custom.group(4)
                        part_type = SIMPLE_TYPE if simple_override or not module.isdigit() else category(sequence)
                        manual = True
                    elif match:
                        module, sequence, name = normalize_module(match.group(1)), int(match.group(2)), (match.group(4) or '').strip()
                        part_type = SIMPLE_TYPE if match.group(3) == '-' or not module.isdigit() else category(sequence)
                    else:
                        result.entries.append(SyncEntry('', '', -1, p.stem, '', [stamp], '无法识别', '不是当前项目的标准文件名；可选中后手动登记。'))
                        continue
                    number = linked[key]['filename'] if key in linked else f'{project.code}-{module}-{sequence:03d}'
                    item_group = groups.setdefault(number.casefold(), SyncEntry(number, module, sequence, name, part_type if part_type != SIMPLE_TYPE or 1 <= sequence <= 999 else "", []))
                    item_group.files.append(stamp)
                    item_group.manual |= manual
                    if name and item_group.name and name.casefold() != item_group.name.casefold():
                        item_group.status, item_group.detail = '编号冲突', '同图号出现不同零件名称。'
                    if not item_group.name:
                        item_group.name = name
        except (OSError, ValueError) as error:
            result.errors.append(f'{directory}：{error}')
    for entry in groups.values():
        entry.files.sort(key=lambda f: path_key(f.path))
        matches = by_number.get(entry.number.casefold(), [])
        if not entry.name and len(matches) == 1:
            entry.name = matches[0]['part_name']
        suffixes = [Path(f.path).suffix.lower() for f in entry.files]
        if entry.status == '编号冲突':
            pass
        elif not entry.part_type or (entry.module not in modules and not matches) or not entry.name:
            entry.status, entry.detail = '待核对', '编号区间、模块启用状态或零件名称需要核对。'
        elif any(f.size == 0 for f in entry.files):
            entry.status, entry.detail = '待核对', '发现空文件，可能尚未保存完成。'
        elif len(set(suffixes)) != len(suffixes) or ('.sldprt' in suffixes and '.sldasm' in suffixes):
            entry.status, entry.detail = '编号冲突', '同图号存在重复类型文件或同时存在零件和装配模型。'
        elif '.sldasm' in suffixes and entry.sequence != 0 and entry.part_type != SIMPLE_TYPE and not matches:
            entry.status, entry.detail = '待核对', '装配模型不是总成000编号，请核对。'
        else:
            if matches:
                old = matches[0]
                entry.record_id = old['id']
                if len(matches) > 1 or old['part_name'].casefold() != entry.name.casefold() or old['status'] != 'used':
                    entry.status, entry.detail = '编号冲突', '数据库同图号的名称、状态或记录不一致。'
                else:
                    old_paths = set(paths_by_record.get(old['id'], ()))
                    if old['file_path']:
                        old_paths.add(path_key(old['file_path']))
                    new_paths = {path_key(f.path) for f in entry.files}
                    old_same_type = any(Path(old_path).suffix.lower() == Path(new.path).suffix.lower() and old_path != path_key(new.path) for old_path in old_paths for new in entry.files)
                    if old_same_type:
                        entry.status, entry.detail = '待核对', '原关联文件与发现文件路径不同，可能移动或复制；请核对。'
                    elif new_paths <= old_paths:
                        entry.status, entry.detail = '已登记', '已与当前记录关联。'
                    else:
                        entry.status, entry.detail = '可关联', '为已有记录补充关联文件，不重复占号。'
            elif (entry.module, entry.sequence) in reservations | operations:
                entry.status, entry.detail = '编号冲突', '此图号已被保留或正在创建，不能重新登记。'
        result.entries.append(entry)
    for old in records:
        paths = set(paths_by_record.get(old['id'], ()))
        if old['file_path']:
            paths.add(old['file_path'])
        if paths and any(not Path(p).is_file() for p in paths):
            result.entries.append(SyncEntry(old['filename'], old['module_code'], old['part_sequence'], old['part_name'], old['part_type'], [], '文件待核对', '原关联文件缺失或已移动；保留记录和占号。', old['id']))
    return result


def import_entries(repository, preview: ScanResult, selected: list[SyncEntry], *, overrides=None, cancel=None) -> int:
    if preview.errors or not selected:
        raise ValueError('扫描不完整或没有选中可登记项，请重新扫描。')
    project = repository.get_project(preview.project_id)
    if project.project_status != 'normal' or project_roots(project) != preview.roots:
        raise ValueError('项目状态或目录已变化，请重新扫描。')
    fresh = scan_project(repository, project.id, overrides=overrides, cancel=cancel)
    if fresh.errors:
        raise ValueError('目录检查未完成，未登记任何文件。')
    now = __import__('datetime').datetime.now().astimezone().isoformat(timespec='seconds')
    with repository.database.transaction() as c:
        locked_project = c.execute('SELECT project_status,directory,solidworks_part_directory FROM projects WHERE id=?', (project.id,)).fetchone()
        if locked_project['project_status'] != 'normal' or locked_project['directory'] != project.directory or locked_project['solidworks_part_directory'] != project.solidworks_part_directory:
            raise ValueError('项目状态或目录已变化，请重新扫描。')
        for entry in selected:
            if cancel and cancel.is_set():
                raise ValueError('已取消，本次登记已回滚。')
            current = next((e for e in fresh.entries if e.number == entry.number and e.files == entry.files and e.name == entry.name and e.status in READY), None)
            if current is None or entry.status not in READY:
                raise ValueError('扫描后文件或记录已变化，请重新扫描；本次登记已回滚。')
            if not c.execute('SELECT 1 FROM project_modules WHERE project_id=? AND module_code=? AND enabled=1', (project.id, entry.module)).fetchone():
                raise ValueError('模块已停用，请重新扫描。')
            existing = c.execute('SELECT * FROM drawing_records WHERE filename=? COLLATE NOCASE', (entry.number,)).fetchone()
            record_id = entry.record_id
            if record_id:
                if not existing or existing['id'] != record_id or existing['status'] != 'used' or existing['part_name'].casefold() != entry.name.casefold():
                    raise ValueError('记录已变化，请重新扫描。')
            else:
                if existing or c.execute("SELECT 1 FROM number_reservations WHERE project_id=? AND module_code=? AND part_sequence=? AND status<>'released'", (project.id, entry.module, entry.sequence)).fetchone() or c.execute("SELECT 1 FROM solidworks_operations WHERE project_id=? AND module_code=? AND part_sequence=? AND status<>'failed'", (project.id, entry.module, entry.sequence)).fetchone():
                    raise ValueError('图号已被占用，请重新扫描。')
                drawing = c.execute('SELECT COALESCE(MAX(drawing_number),0)+1 FROM drawing_records WHERE project_id=?', (project.id,)).fetchone()[0]
                main = sorted(entry.files, key=lambda f: ({'.sldprt': 0, '.sldasm': 0, '.slddrw': 1}.get(Path(f.path).suffix.lower(), 2), f.path))[0]
                cursor = c.execute('''INSERT INTO drawing_records(project_id,project_name,project_code,drawing_number,filename,part_name,purpose,record_date,created_at,updated_at,status,creation_method,project_directory,module_code,part_sequence,part_type,full_name,file_path,file_extension,file_size,file_exists,creation_source,project_directory_snapshot)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (project.id, project.name, project.code, drawing, entry.number, entry.name, '', now[:10], now, now, 'used', 'external_import', project.directory, entry.module, entry.sequence, entry.part_type, entry.number + ('-' if entry.part_type == SIMPLE_TYPE else ' ') + entry.name, main.path, Path(main.path).suffix, main.size, 1, 'external_import', project.directory))
                record_id = cursor.lastrowid
                c.execute("INSERT INTO number_reservations(project_id,module_code,part_category,part_sequence,status,original_record_id,reserved_at,reason) VALUES (?,?,?,?,?,?,?,?)", (project.id, entry.module, entry.part_type, entry.sequence, 'used', record_id, now, 'external_import'))
            for f in entry.files:
                # Check again after acquiring SQLite's write lock. Nothing writes to these files.
                p = Path(f.path)
                info = p.stat()
                if is_reparse(p) or (info.st_size, info.st_mtime_ns) != (f.size, f.mtime_ns):
                    raise ValueError('文件已变化，请重新扫描。')
                owner = c.execute('SELECT record_id FROM record_files WHERE path=?', (f.path,)).fetchone()
                if owner and owner['record_id'] != record_id:
                    raise ValueError('文件已关联其他记录，本次登记已回滚。')
                c.execute('INSERT INTO record_files(record_id,path,extension,file_size,mtime_ns,created_at) VALUES (?,?,?,?,?,?) ON CONFLICT(path) DO NOTHING', (record_id, f.path, p.suffix.lower(), f.size, f.mtime_ns, now))
            if not existing or not existing['file_path']:
                main = sorted(entry.files, key=lambda f: ({'.sldprt': 0, '.sldasm': 0, '.slddrw': 1}.get(Path(f.path).suffix.lower(), 2), f.path))[0]
                c.execute('UPDATE drawing_records SET file_path=?,file_extension=?,file_size=?,file_exists=1,updated_at=? WHERE id=?', (main.path, Path(main.path).suffix, main.size, now, record_id))
            if entry.sequence != 0:
                c.execute('''INSERT INTO part_sequence_counters(project_id,module_code,part_type,next_sequence,updated_at) VALUES (?,?,?,?,?)
                    ON CONFLICT(project_id,module_code,part_type) DO UPDATE SET next_sequence=MAX(part_sequence_counters.next_sequence,excluded.next_sequence),updated_at=excluded.updated_at''', (project.id, entry.module, entry.part_type, entry.sequence + 1, now))
            c.execute('''INSERT INTO ai_parts(project_id,module_code,part_number,part_name,part_type,purpose,confirmed,created_at)
                SELECT ?,?,?,?,?,?,1,? WHERE NOT EXISTS (SELECT 1 FROM ai_parts WHERE project_id=? AND module_code=? AND part_number=? AND part_name=?)''',
                (project.id, entry.module, entry.sequence, entry.name, entry.part_type, '', now, project.id, entry.module, entry.sequence, entry.name))
    return len(selected)


def ensure_external_numbers_registered(repository, project_id, extras=()):
    result = scan_project(repository, project_id, extras=extras)
    if result.errors:
        raise ExternalFilesNeedSync('项目目录检查未完成，请使用“导入已有文件”核对：' + result.errors[0])
    if any(e.number and e.status not in ('已登记', '可关联', '文件待核对') for e in result.entries):
        raise ExternalFilesNeedSync('发现尚未登记或冲突的项目图号，请先使用“导入已有文件”核对，再创建或复制新编号。')
