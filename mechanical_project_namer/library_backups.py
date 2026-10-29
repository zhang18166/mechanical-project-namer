# SPDX-License-Identifier: GPL-3.0-only
"""Complete private offline backup sets and new-location development recovery (format 1)."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid

from . import __version__
from .backup_io import (MAX_FILES, MAX_TOTAL, MAX_FILE, check_entries, copy_hash, database_fingerprints,
                        read_metadata, readonly_database, tree_files, verify_files, write_metadata, quote)
from .data.database import SCHEMA_VERSION
from .development import MARKER, IDENTITY
from .library_lock import file_lease, library_lease, MaintenanceBusy
from .restored_state import DETACHED
from .snapshot_io import SnapshotError, Cancelled, check_cancel, encoded, safe_path, rename_new, validate_entries, relative
from .snapshots import identifier, now, verify_snapshot_folder

DATABASE_RELATIVE = 'local/MechanicalProjectNamer/data/mechanical_project_namer.db'
ARCHIVE_RELATIVE = 'local/MechanicalProjectNamer/snapshots'
SETTINGS_RELATIVE = 'roaming/MechanicalProjectNamer/settings.json'
WHITE_CONFIG = ('current_project_id', 'current_module_code', 'current_part_type', 'last_window_mode', 'always_on_top')
EXCLUSIONS = ['未归档的外部工作文件', '失败/终止任务的暂存文件及已提取的工作副本（记录保留在数据库）',
              'Windows凭据/API Key/密码/令牌/证书', '浏览器数据、缓存、日志、临时文件、其他备份',
              '未列入白名单的设置及数据库外AI记忆派生文件']


def overlap(a, b):
    a, b = Path(os.path.abspath(a)), Path(os.path.abspath(b))
    return a.is_relative_to(b) or b.is_relative_to(a)


def protected_roots():
    roots = [Path(__file__).resolve().parents[1], Path.home()/'AppData/Local/MechanicalProjectNamer',
             Path.home()/'AppData/Roaming/MechanicalProjectNamer']
    for variable in ('LOCALAPPDATA', 'APPDATA'):
        if os.environ.get(variable):
            roots.append(Path(os.environ[variable])/'MechanicalProjectNamer')
    return roots


def profile_paths(profile):
    profile = safe_path(profile)
    if any(overlap(profile, p) for p in protected_roots()):
        raise SnapshotError('源实例必须是独立开发配置，不能接管正式数据或源码。')
    if read_metadata(profile/MARKER)[0] != IDENTITY:
        raise SnapshotError('只支持有正确开发标识的隔离实例。')
    db, archive = safe_path(profile/DATABASE_RELATIVE), safe_path(profile/ARCHIVE_RELATIVE)
    if not db.is_file() or not archive.is_dir():
        raise SnapshotError('隔离数据库或已初始化的归档库不存在。')
    return profile, db, archive


def target_path(target, forbidden=()):
    target = safe_path(target)
    if not target.parent.is_dir() or any(overlap(target, p) for p in [*protected_roots(), *forbidden]):
        raise SnapshotError('目的地必须独立、父目录已存在，不能与原库、源码或正式目录重叠。')
    for parent in target.parents:
        if (parent/'.git').exists() or (parent/MARKER).exists() or (parent/'.mw-store.json').exists() or (parent/'COMPLETE.json').exists():
            raise SnapshotError('拒绝写入其他实例、源码、归档或备份内部。')
    return target


def white_config(path):
    if not path.exists():
        return {}
    raw = read_metadata(path)[0]
    if not isinstance(raw, dict):
        raise SnapshotError('设置格式无效。')
    result = {k:raw[k] for k in WHITE_CONFIG if k in raw}
    for key, value in result.items():
        if key == 'current_project_id':
            valid = value is None or type(value) is int
        elif key == 'always_on_top':
            valid = type(value) is bool
        else:
            valid = isinstance(value, str) and len(value) <= 120 and not any(c in value for c in '/\\:')
        if not valid:
            raise SnapshotError('白名单设置字段无效：' + key)
    return result


def _database_check(con):
    if [r[0] for r in con.execute('PRAGMA integrity_check')] != ['ok']:
        raise SnapshotError('数据库完整性检查失败。')
    if con.execute('PRAGMA foreign_key_check').fetchone():
        raise SnapshotError('数据库外键不一致。')
    versions = [r[0] for r in con.execute('SELECT version FROM schema_meta')]
    if versions != [SCHEMA_VERSION] or SCHEMA_VERSION != 13:
        raise SnapshotError('只支持已验证Schema13，拒绝较新/较旧库，不自动迁移或降级。')
    # Usage counters named input/output_tokens are not authentication credentials.
    secret_names = {'password','passwd','api_key','apikey','access_token','refresh_token','token','secret',
                    'client_secret','private_key','credential','credentials','certificate'}
    objects = list(con.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL"))
    if any(r['type']=='trigger' or r['sql'].lstrip().upper().startswith('CREATE VIRTUAL TABLE') for r in objects):
        raise SnapshotError('包含未验证的触发器/虚拟表，拒绝执行恢复写入。')
    for row in objects:
        if row['type']=='table':
            if any(c['name'].lower() in secret_names for c in con.execute('PRAGMA table_info('+quote(row['name'])+')')):
                raise SnapshotError('发现凭据字段，拒绝备份；必须先设计处理规则，不能删除业务数据。')
    for table,limit in [('projects',10000),('project_files',100000),('project_file_sources',100000),('local_versions',10000),('local_restores',10000),('snapshot_meta',200000)]:
        if con.execute('SELECT count(*) FROM '+quote(table)).fetchone()[0]>limit:
            raise SnapshotError('全库元数据数量超限：'+table)
    if con.execute('SELECT COALESCE(SUM(length(CAST(key AS BLOB))+length(CAST(value AS BLOB))),0) FROM snapshot_meta').fetchone()[0]>16*1024**2:
        raise SnapshotError('归档位置/恢复元数据超过16 MiB。')


def _graph(con, archive, *, bound_store, cancel=None):
    """Validate only the supplied archive tree. Historical source paths are never opened."""
    _database_check(con); archive = safe_path(archive)
    found = con.execute("SELECT value FROM snapshot_meta WHERE key='store_id'").fetchone()
    store_id = identifier(found[0]) if found else None
    if not store_id or read_metadata(archive/'.mw-store.json')[0] != dict(format=1,kind='mechanical-workbench-snapshots',store_id=store_id):
        raise SnapshotError('数据库与归档库身份错配。')
    pending = [r[0] for r in con.execute("SELECT task_id FROM local_versions WHERE state NOT IN ('complete','cancelled') LIMIT 12")]
    pending += [r[0] for r in con.execute("SELECT restore_id FROM local_restores WHERE state NOT IN ('complete','cancelled') LIMIT 12")]
    pending += [r[0] for r in con.execute('SELECT operation_id FROM pending_solidworks_creates LIMIT 12')]
    pending += [r[0] for r in con.execute("SELECT operation_id FROM solidworks_operations WHERE recovery_required=1 OR status NOT IN ('completed','failed') LIMIT 12")]
    if pending:
        raise SnapshotError('发现未终结/待恢复任务，先完成或明确终止：' + ', '.join(pending[:12]))
    expected = {'.mw-store.json'}; complete = []
    for item in con.execute('SELECT * FROM local_versions ORDER BY version_id'):
        row=dict(item)
        check_cancel(cancel)
        identifier(row['task_id']); identifier(row['version_id'])
        if row['store_path'] != str(bound_store):
            raise SnapshotError('版本与实例归档位置不一致；禁止字符串替换绕过。')
        if hashlib.sha256(row['request_json'].encode('ascii')).hexdigest() != row['request_hash']:
            raise SnapshotError('数据库冻结清单损坏。')
        request = json.loads(row['request_json']); validate_entries(request['files'])
        required={relative(p) for p in request['required']}
        if required-{f['relative_path'] for f in request['files']}:
            raise SnapshotError('版本缺少明确声明的必需文件。')
        if any(request[k] != row[k] for k in ('project_id','label','note')):
            raise SnapshotError('版本项目/标签与冻结请求不一致。')
        if row['state'] != 'complete':
            continue
        if row['integrity'] != 'verified' or row['dependency_status']!='unverified' or row['preview_status']!='unverified':
            raise SnapshotError('完整版本状态不满足要求；装配依赖及预览对应必须保持未验证。')
        prefix = 'versions/' + row['version_id']
        doc = verify_snapshot_folder(row, store_id, archive/prefix, cancel=cancel)
        for f in doc['files']:
            linked = con.execute('SELECT project_id FROM project_files WHERE file_id=?',(f['file_id'],)).fetchone()
            if linked is None or linked[0] != row['project_id']:
                raise SnapshotError('项目/文件/版本身份关联失效。')
            if f['record_id'] is not None:
                linked = con.execute('SELECT project_id FROM drawing_records WHERE id=?',(f['record_id'],)).fetchone()
                if linked is None or linked[0] != row['project_id']:
                    raise SnapshotError('版本零件历史映射失效。')
        expected.update({prefix+'/owner.json',prefix+'/manifest.json'})
        expected.update(prefix+'/payload/'+f['relative_path'] for f in doc['files'])
        complete.append(row['version_id'])
        if len(expected)>MAX_FILES-2:raise SnapshotError('全库文件数量超过100000。')
    version_root = safe_path(archive/'versions')
    actual_versions = {p.name for p in version_root.iterdir()} if version_root.exists() else set()
    if actual_versions != set(complete):
        raise SnapshotError('完整版本目录与数据库不一致（缺失或异常额外目录）。')
    if len(expected) > MAX_FILES-2:
        raise SnapshotError('全库文件数量超过100000。')
    return dict(store_id=store_id, versions=complete, files=sorted(expected))


def _space(parent, size):
    if size > MAX_TOTAL or shutil.disk_usage(parent).free < size + 64*1024**2:
        raise SnapshotError('全库容量超限或空间不足（至少另留64 MiB）。')


class _Task:
    """Private durable journal separate from the immutable completed backup/profile."""
    def __init__(self, target, kind, source, task_id):
        self.id = identifier(task_id); self.folder = safe_path(target.parent/('.mw-library-job-'+self.id))
        self.owner = dict(format=1,task_id=self.id,kind=kind,source=str(source),target=str(target))
        if target.exists() and not self.folder.exists():
            raise SnapshotError('目的目录已存在，禁止覆盖或合并。')
        if self.folder.exists():
            if read_metadata(self.folder/'owner.json')[0] != self.owner:
                raise SnapshotError('任务ID已绑定其他来源或目的地。')
        else:
            self.folder.mkdir(); write_metadata(self.folder/'owner.json',self.owner)
        self.path = self.folder/'journal.json'
        self.data = read_metadata(self.path)[0] if self.path.exists() else dict(state='prepared',events=[])
        if 'attempt' in self.data and (len(self.data['attempt'])!=32 or any(c not in '0123456789abcdef' for c in self.data['attempt'])):
            raise SnapshotError('任务暂存标识无效。')

    def record(self, state, **values):
        if len(self.data['events']) >= 10000:
            raise SnapshotError('任务事件数量超限，请保留证据并建立新任务。')
        self.data.update(values,state=state)
        self.data['events'].append(dict(state=state,time=now(),**values))
        write_metadata(self.path,self.data,replace=self.path.exists())

    def attempt(self):
        name = uuid.uuid4().hex
        self.record('copying',attempt=name)
        root = self.folder/name; root.mkdir(); return root


class LibraryBackups:
    def __init__(self, *, checkpoint=None):
        self.checkpoint = checkpoint or (lambda point:None)

    @contextmanager
    def _source(self, profile, *, cancel=None):
        profile, db, archive = profile_paths(profile)
        with library_lease(db, exclusive=True):
            reserve = sqlite3.connect(db,timeout=0)
            try:
                reserve.execute('PRAGMA trusted_schema=OFF')
                try:
                    reserve.execute('BEGIN IMMEDIATE')
                except sqlite3.OperationalError as exc:
                    raise MaintenanceBusy('有SQLite写入尚未结束，不能确认维护独占。') from exc
                with readonly_database(db,immutable=False,cancel=cancel) as reader:
                    yield profile, db, archive, reader
            finally:
                reserve.rollback(); reserve.close()

    def inspect(self, profile, *, cancel=None):
        with self._source(profile,cancel=cancel) as (profile,db,archive,con):
            graph = _graph(con,archive,bound_store=archive,cancel=cancel)
            sizes = [safe_path(archive/f).stat().st_size for f in graph['files']]
            total = sum(sizes) + con.execute('PRAGMA page_count').fetchone()[0]*con.execute('PRAGMA page_size').fetchone()[0]
            if total > MAX_TOTAL or any(s > MAX_FILE for s in sizes):
                raise SnapshotError('全库容量保护上限不满足。')
            return dict(versions=len(graph['versions']),archive_files=len(sizes),estimated_bytes=total,
                        exclusions=EXCLUSIONS,unencrypted=True,scope='全部完整归档版本及配套业务数据库；不含外部工作文件')

    def create(self, profile, destination, *, task_id=None, cancel=None, progress=None):
        profile = Path(os.path.abspath(profile)); target = target_path(destination, [profile])
        task_id = identifier(task_id)
        with file_lease(target.parent/('.mw-library-job-'+task_id+'.lock'),exclusive=True):
            job = _Task(target,'backup',profile,task_id)
            if target.exists():
                doc = self.verify(target,cancel=cancel)
                if doc['backup_id'] != job.id or job.data.get('manifest_sha') != read_metadata(target/'manifest.json')[1]:
                    raise SnapshotError('目的地不是当前任务提交的备份，拒绝接管。')
                job.record('complete'); return doc
            try:
                old = job.folder/job.data.get('attempt','missing')/'backup-set'
                if job.data.get('state') == 'sealed' and old.is_dir():
                    sealed=self.verify(old,cancel=cancel)
                    if sealed['backup_id']!=job.id or read_metadata(old/'manifest.json')[1]!=job.data.get('manifest_sha'):
                        raise SnapshotError('封存备份与任务冻结清单不一致。')
                    check_cancel(cancel)
                    self.checkpoint('backup_before_commit'); rename_new(old,target); self.checkpoint('backup_after_commit')
                    job.record('complete'); return self.verify(target)
                with self._source(profile,cancel=cancel) as (profile,db,archive,reader):
                    graph = _graph(reader,archive,bound_store=archive,cancel=cancel)
                    for root, in reader.execute("SELECT directory FROM projects WHERE directory<>''"):
                        if overlap(target,root):raise SnapshotError('备份不能写入外部工作工程目录。')
                    config = white_config(profile/SETTINGS_RELATIVE)
                    fingerprints = database_fingerprints(reader,cancel=cancel)
                    binding = hashlib.sha256(encoded(dict(database=fingerprints,config=config,store=graph['store_id']))).hexdigest()
                    if job.data.get('binding') not in (None,binding):
                        raise SnapshotError('源范围/数据已变化，必须使用新任务ID。')
                    job.record('frozen',binding=binding); self.checkpoint('backup_frozen')
                    db_bytes=reader.execute('PRAGMA page_count').fetchone()[0]*reader.execute('PRAGMA page_size').fetchone()[0]
                    if db_bytes>MAX_FILE:raise SnapshotError('数据库超过32 GiB单文件上限。')
                    total = db_bytes + sum((archive/f).stat().st_size for f in graph['files'])
                    _space(target.parent,total)
                    stage = job.attempt()/'backup-set'; stage.mkdir(); payload=stage/'data'; payload.mkdir()
                    self.checkpoint('backup_stage_created')
                    destination_db = sqlite3.connect(payload/'database.sqlite3')
                    try:
                        destination_db.execute('PRAGMA synchronous=FULL')
                        def copying(status,remaining,total_pages):
                            check_cancel(cancel)
                            if progress:progress(f'一致性备份数据库：剩余{remaining}页 / {total_pages}页')
                        reader.backup(destination_db,pages=128,progress=copying,sleep=0.01)
                        destination_db.execute('PRAGMA journal_mode=DELETE')
                    finally:
                        destination_db.close()
                    self.checkpoint('backup_database_copied')
                    write_metadata(payload/'config.json',config)
                    for f in graph['files']:
                        if progress:progress('备份：'+f)
                        copy_hash(archive/f,payload/'archive'/f,cancel=cancel,
                                  progress=lambda n:self.checkpoint('backup_copy_chunk'))
                        self.checkpoint('backup_file_copied')
                    with readonly_database(payload/'database.sqlite3',cancel=cancel) as copied:
                        if database_fingerprints(copied,cancel=cancel) != fingerprints:
                            raise SnapshotError('SQLite副本与冻结业务记录不一致。')
                        copied_graph = _graph(copied,payload/'archive',bound_store=archive,cancel=cancel)
                        if copied_graph != graph:raise SnapshotError('数据库与归档副本交叉核对失败。')
                    if database_fingerprints(reader,cancel=cancel) != fingerprints or white_config(profile/SETTINGS_RELATIVE) != config:
                        raise SnapshotError('维护期间源范围发生变化。')
                    # Rehash source archives too; out-of-band disk tampering is not masked by SQLite backup.
                    _graph(reader,archive,bound_store=archive,cancel=cancel)
                    entries = [dict(path=p,**copy_hash(payload/p,cancel=cancel)) for p in sorted(tree_files(payload))]
                    byte_count=check_entries(entries); self.checkpoint('backup_files_verified')
                    doc=dict(format=1,backup_id=job.id,created_at=now(),application=__version__,schema=SCHEMA_VERSION,
                        store_id=graph['store_id'],versions=graph['versions'],files=entries,file_count=len(entries),total_bytes=byte_count,
                        database_tables=fingerprints,origin=dict(profile=str(profile),database=str(db),archive=str(archive)),
                        scope='全部完整归档版本及配套业务数据库；不包括未归档外部工作文件',exclusions=EXCLUSIONS,
                        config_whitelist=list(WHITE_CONFIG),unencrypted=True,validation='bytes-and-database-relations-verified')
                    h=write_metadata(stage/'manifest.json',doc); self.checkpoint('backup_manifest_written')
                    write_metadata(stage/'COMPLETE.json',dict(format=1,backup_id=job.id,manifest_sha256=h,state='complete'))
                    self.verify(stage,cancel=cancel); job.record('sealed',manifest_sha=h); self.checkpoint('backup_sealed')
                    check_cancel(cancel); self.checkpoint('backup_before_commit'); rename_new(stage,target); self.checkpoint('backup_after_commit')
                    job.record('complete'); self.checkpoint('backup_journal_complete'); return doc
            except Exception as exc:
                job.record('committed' if target.exists() else 'cancelled' if isinstance(exc,Cancelled) else 'failed',error=str(exc))
                raise

    def verify(self, backup, *, cancel=None, progress=None):
        backup=safe_path(backup)
        if {p.name for p in backup.iterdir()} != {'data','manifest.json','COMPLETE.json'}:
            raise SnapshotError('备份未完成或目录结构异常。')
        doc,h=read_metadata(backup/'manifest.json'); receipt=read_metadata(backup/'COMPLETE.json')[0]
        required={'format','backup_id','created_at','application','schema','store_id','versions','files','file_count','total_bytes',
                  'database_tables','origin','scope','exclusions','config_whitelist','unencrypted','validation'}
        if not isinstance(doc,dict) or set(doc)!=required:
            raise SnapshotError('备份清单字段不完整或包含未知字段。')
        if not isinstance(doc['created_at'],str) or not 1<=len(doc['created_at'])<=80:
            raise SnapshotError('备份创建时间字段无效。')
        if doc['exclusions']!=EXCLUSIONS or not isinstance(doc['scope'],str) or not 1<=len(doc['scope'])<=1000:
            raise SnapshotError('备份范围/排除项格式不一致。')
        if not isinstance(doc['origin'],dict) or set(doc['origin'])!={'profile','database','archive'} or any(
                not isinstance(p,str) or len(p)>1024 or not Path(p).is_absolute() for p in doc['origin'].values()):
            raise SnapshotError('备份历史来源映射格式无效。')
        if not isinstance(doc['versions'],list) or len(doc['versions'])>10000 or any(identifier(v)!=v for v in doc['versions']):
            raise SnapshotError('备份版本ID集合无效。')
        if type(doc['format']) is not int or type(doc['file_count']) is not int or type(doc['total_bytes']) is not int:
            raise SnapshotError('备份格式或容量字段类型无效。')
        if doc['validation']!='bytes-and-database-relations-verified':raise SnapshotError('备份校验声明无效。')
        if doc.get('format') != 1 or doc.get('schema') != 13 or doc.get('application') != __version__:
            raise SnapshotError('备份格式/应用/Schema不兼容；不自动迁移或降级。')
        if receipt != dict(format=1,backup_id=identifier(doc['backup_id']),manifest_sha256=h,state='complete'):
            raise SnapshotError('备份完成标志或清单校验值不一致。')
        if doc.get('unencrypted') is not True or doc.get('config_whitelist') != list(WHITE_CONFIG):
            raise SnapshotError('备份配置白名单或未加密声明不一致。')
        if check_entries(doc['files']) != doc['total_bytes'] or len(doc['files']) != doc['file_count']:
            raise SnapshotError('备份文件数量或总容量不一致。')
        if progress:progress('逐项验证备份文件与数据库关联…')
        verify_files(backup/'data',doc['files'],cancel=cancel)
        config=read_metadata(backup/'data/config.json')[0]
        if set(config)-set(WHITE_CONFIG) or white_config(backup/'data/config.json') != config:
            raise SnapshotError('备份包含非白名单设置。')
        with readonly_database(backup/'data/database.sqlite3',cancel=cancel) as con:
            graph=_graph(con,backup/'data/archive',bound_store=doc['origin']['archive'],cancel=cancel)
            if graph['store_id'] != doc['store_id'] or graph['versions'] != doc['versions']:
                raise SnapshotError('备份身份/完整版本集合不一致。')
            if database_fingerprints(con,cancel=cancel) != doc['database_tables']:
                raise SnapshotError('备份业务数据库逻辑清单不一致。')
        expected={'database.sqlite3','config.json',*('archive/'+p for p in graph['files'])}
        if {e['path'] for e in doc['files']} != expected:
            raise SnapshotError('备份目录不等于冻结的完整范围。')
        return doc

    def restore(self, backup, destination, *, task_id=None, cancel=None, progress=None):
        backup=safe_path(backup); target=target_path(destination,[backup]); task_id=identifier(task_id)
        with file_lease(target.parent/('.mw-library-job-'+task_id+'.lock'),exclusive=True):
            job=_Task(target,'restore',backup,task_id)
            if target.exists():
                receipt,receipt_hash=read_metadata(target/'.mw-library-restore.json')
                if (receipt.get('task_id') != job.id or receipt.get('new_root')!=str(target) or receipt.get('state')!='complete'
                    or receipt.get('manifest_sha') != job.data.get('manifest_sha') or receipt_hash!=job.data.get('receipt_sha')):
                    raise SnapshotError('已有目录不是该任务的恢复结果，禁止覆盖。')
                if job.data.get('state') != 'complete':
                    self._verify_recovered(target,receipt,cancel=cancel); job.record('complete')
                return receipt
            try:
                doc=self.verify(backup,cancel=cancel,progress=progress); h=read_metadata(backup/'manifest.json')[1]
                if job.data.get('manifest_sha') not in (None,h):raise SnapshotError('备份清单改变，不能沿用恢复任务ID。')
                for root in doc['origin'].values():
                    if overlap(target,root):raise SnapshotError('恢复目的地不能覆盖或位于原实例内部。')
                with readonly_database(backup/'data/database.sqlite3') as con:
                    for root, in con.execute("SELECT directory FROM projects WHERE directory<>''"):
                        if overlap(target,root):raise SnapshotError('恢复目的地不能与原工作工程重叠。')
                old=job.folder/job.data.get('attempt','missing')/'profile'
                if job.data.get('state') == 'sealed' and old.is_dir():
                    receipt,receipt_hash=read_metadata(old/'.mw-library-restore.json')
                    if receipt_hash!=job.data.get('receipt_sha'):raise SnapshotError('封存恢复凭据与任务不一致。')
                    self._verify_recovered(old,receipt,bound_root=target,cancel=cancel)
                    check_cancel(cancel); self.checkpoint('restore_before_commit'); rename_new(old,target); self.checkpoint('restore_after_commit')
                    job.record('complete'); return receipt
                job.record('frozen',manifest_sha=h); self.checkpoint('restore_frozen'); _space(target.parent,doc['total_bytes']*2)
                stage=job.attempt()/'profile'; stage.mkdir(); self.checkpoint('restore_stage_created')
                for e in doc['files']:
                    if e['path']=='database.sqlite3':rel=DATABASE_RELATIVE
                    elif e['path'].startswith('archive/'):rel=ARCHIVE_RELATIVE+'/'+e['path'][8:]
                    else:continue
                    if progress:progress('恢复副本：'+e['path'])
                    if copy_hash(backup/'data'/e['path'],stage/rel,cancel=cancel,
                        progress=lambda n:self.checkpoint('restore_copy_chunk')) != {k:e[k] for k in ('size','sha256')}:
                        raise SnapshotError('恢复期间备份文件发生变化。')
                    self.checkpoint('restore_file_copied')
                self.checkpoint('restore_database_copied')
                allowed=self._rebind(stage,target,doc,job.id,cancel=cancel); self.checkpoint('restore_database_rebound')
                settings=read_metadata(backup/'data/config.json')[0]; settings['solidworks_enabled']=False
                (stage/SETTINGS_RELATIVE).parent.mkdir(parents=True,exist_ok=True)
                write_metadata(stage/SETTINGS_RELATIVE,settings); write_metadata(stage/MARKER,IDENTITY)
                receipt=dict(format=1,task_id=job.id,backup_id=doc['backup_id'],manifest_sha=h,state='complete',
                    logical_store_id=doc['store_id'],new_root=str(target),allowed_changes=allowed,
                    byte_integrity='verified',cad_dependencies='unverified',preview_correspondence='unverified',
                    database_sha256=copy_hash(stage/DATABASE_RELATIVE,cancel=cancel)['sha256'],
                    archive_files=[dict(path=e['path'][8:],size=e['size'],sha256=e['sha256']) for e in doc['files'] if e['path'].startswith('archive/')])
                receipt_sha=write_metadata(stage/'.mw-library-restore.json',receipt); self.checkpoint('restore_receipt_written')
                self._verify_recovered(stage,receipt,bound_root=target,cancel=cancel)
                # Source backup remains byte-identical, checked again before committing the new instance.
                self.verify(backup,cancel=cancel)
                job.record('sealed',receipt_sha=receipt_sha); self.checkpoint('restore_sealed'); check_cancel(cancel)
                self.checkpoint('restore_before_commit'); rename_new(stage,target); self.checkpoint('restore_after_commit')
                job.record('complete'); self.checkpoint('restore_journal_complete'); return receipt
            except Exception as exc:
                job.record('committed' if target.exists() else 'cancelled' if isinstance(exc,Cancelled) else 'failed',error=str(exc))
                raise

    def _rebind(self, stage, target, doc, task_id, *, cancel=None):
        new_store=str(target/ARCHIVE_RELATIVE); db=stage/DATABASE_RELATIVE
        with readonly_database(db,cancel=cancel) as original:
            original_meta=dict(original.execute('SELECT key,value FROM snapshot_meta'))
            project_origins=[dict(r) for r in original.execute('SELECT id,directory,solidworks_part_directory FROM projects')]
            added={'restored_instance':encoded(dict(backup_id=doc['backup_id'],restore_id=task_id)).decode(),
                   'restore_rebinding:'+task_id:encoded(dict(old=doc['origin']['archive'],new=new_store)).decode()}
            added.setdefault('restore_original_project_dirs',original_meta.get('restore_original_project_dirs',encoded(project_origins).decode()))
            for source_id, in original.execute('SELECT source_id FROM project_file_sources'):
                added['detached_source:'+str(source_id)]='1'
            for key, in original.execute("SELECT task_id FROM local_versions WHERE state='cancelled'"):
                added['detached_task:'+key]='1'
            for key, in original.execute('SELECT restore_id FROM local_restores'):
                added['detached_restore:'+key]='1'
            transforms={
                'projects':lambda row:dict(row,directory='',solidworks_part_directory=''),
                'project_files':lambda row:dict(row,state='missing',error=DETACHED+'；历史路径只保留为来源信息。'),
                'local_versions':lambda row:dict(row,store_path=new_store),
                'snapshot_meta':lambda row:dict(row,value=added.get(row['key'],row['value'])),
            }
            expected=database_fingerprints(original,transforms=transforms,cancel=cancel)
        con=sqlite3.connect(db)
        try:
            con.execute('PRAGMA foreign_keys=ON'); con.execute('PRAGMA trusted_schema=OFF'); con.execute('PRAGMA synchronous=FULL')
            if cancel:con.set_progress_handler(lambda:int(cancel.is_set()),10000)
            con.execute('BEGIN IMMEDIATE')
            con.execute('UPDATE local_versions SET store_path=?',(new_store,))
            con.execute("UPDATE projects SET directory='',solidworks_part_directory=''")
            con.execute("UPDATE project_files SET state='missing',error=?",(DETACHED+'；历史路径只保留为来源信息。',))
            for key,value in added.items():
                con.execute('INSERT INTO snapshot_meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
            self.checkpoint('restore_before_database_commit'); check_cancel(cancel); con.commit(); self.checkpoint('restore_after_database_commit')
        except sqlite3.OperationalError:
            check_cancel(cancel)
            raise
        finally:
            con.close()
        with readonly_database(db,cancel=cancel) as rebound:
            actual=database_fingerprints(rebound,skip_meta=set(added)-set(original_meta),cancel=cancel)
            if actual != expected:
                raise SnapshotError('重绑定修改超过允许清单，恢复不能完成。')
        return dict(local_versions=['store_path'],projects=['directory','solidworks_part_directory'],
                    project_files=['state','error'],snapshot_meta_keys=sorted(added),
                    all_other_tables='exact-row-fingerprints-unchanged')

    def _verify_recovered(self, root, receipt, *, bound_root=None, cancel=None):
        root=safe_path(root); bound_root=bound_root or root
        if receipt['new_root'] != str(bound_root) or receipt['state'] != 'complete' or read_metadata(root/MARKER)[0] != IDENTITY:
            raise SnapshotError('恢复实例位置、状态或归属不一致。')
        if copy_hash(root/DATABASE_RELATIVE,cancel=cancel)['sha256'] != receipt['database_sha256']:
            raise SnapshotError('恢复副本数据库提交后发生变化，不能冒充原恢复验证结果。')
        verify_files(root/ARCHIVE_RELATIVE,receipt['archive_files'],cancel=cancel)
        with readonly_database(root/DATABASE_RELATIVE,cancel=cancel) as con:
            graph=_graph(con,root/ARCHIVE_RELATIVE,bound_store=bound_root/ARCHIVE_RELATIVE,cancel=cancel)
            if graph['store_id'] != receipt['logical_store_id']:
                raise SnapshotError('重绑定改变了逻辑归档库身份。')
        return True
