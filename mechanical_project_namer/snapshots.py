# SPDX-License-Identifier: GPL-3.0-only
"""Offline immutable version sets. SQLite and filesystem commit are separate/recoverable."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import re
import uuid

from .paths import local_data_dir
from .library_lock import library_lease, library_operation
from .snapshot_io import (SnapshotError,Cancelled,ROLES,MAX_TOTAL,MAX_FILES,encoded,safe_path,
                          relative,validate_entries,digest,read_json,write_json,verify_tree,rename_new,check_cancel)


def now():return datetime.now(timezone.utc).isoformat(timespec='microseconds')
def identifier(value=None):
    try:return str(uuid.UUID(value)) if value is not None else str(uuid.uuid4())
    except (ValueError,TypeError,AttributeError) as exc:raise SnapshotError('任务或版本ID无效。') from exc


def portable_text(value):
    if not isinstance(value,str) or re.search(r'[A-Za-z]:[\\/]|\\\\|(?:^|\s)/[^\s]',value):raise SnapshotError('公开清单文字不能包含本地绝对路径。')
    return value


def role_for(path):
    ext=Path(path).suffix.lower()
    if ext in ('.sldprt','.sldasm','.slddrw','.prt'):return 'source'
    return {'.step':'exchange','.stp':'exchange','.glb':'preview','.pdf':'drawing'}.get(ext,'attachment')


def snapshot_manifest(row):
    request=json.loads(row['request_json'])
    return dict(format_version=1,task_id=row['task_id'],version_id=row['version_id'],project_id=row['project_id'],
        label=row['label'],note=row['note'],created_at=row['created_at'],dependency_status='unverified',
        preview_status='unverified',required=request['required'],files=[{k:f[k] for k in
        ('file_id','record_id','relative_path','role','software','size','sha256')} for f in request['files']])


def verify_snapshot_folder(row,store_id,folder,*,cancel=None):
    folder=safe_path(folder)
    if {p.name for p in folder.iterdir()}!={'owner.json','manifest.json','payload'}:raise SnapshotError('归档目录结构不一致。')
    if read_json(folder/'owner.json')[0]!={'store_id':store_id,'task_id':row['task_id']}:raise SnapshotError('归档归属记录不一致。')
    doc,h=read_json(folder/'manifest.json')
    if not row['manifest_sha'] or h!=row['manifest_sha'] or doc!=snapshot_manifest(row):raise SnapshotError('清单与数据库冻结记录不一致。')
    verify_tree(folder/'payload',doc['files'],cancel=cancel)
    return doc


@contextmanager
def project_lock(folder,project_id):
    path=safe_path(folder/f'{project_id}.lock')
    with path.open('a+b') as stream:
        stream.seek(0,os.SEEK_END)
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:raise SnapshotError('同项目已有任务运行，请稍后重试。') from exc
        try:yield
        finally:
            if os.name=='nt':
                stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


class SnapshotService:
    def __init__(self,repository,store=None,*,checkpoint=None):
        self.repository=repository;self.db=repository.database
        with library_lease(self.db.path):
            self._initialize(store,checkpoint)

    def _initialize(self,store,checkpoint):
        self.store=safe_path(store or local_data_dir()/'snapshots')
        if self.store.is_relative_to(Path(__file__).resolve().parents[1]):raise SnapshotError('归档库不能放在源码工作树内。')
        self.checkpoint=checkpoint or (lambda point:None)
        with self.db.transaction() as c:
            c.execute('INSERT OR IGNORE INTO snapshot_meta VALUES (?,?)',('store_id',identifier()))
            self.store_id=c.execute('SELECT value FROM snapshot_meta WHERE key=?',('store_id',)).fetchone()[0]
        marker=self.store/'.mw-store.json'
        self.store.mkdir(parents=True,exist_ok=True);safe_path(self.store)
        expected={'format':1,'kind':'mechanical-workbench-snapshots','store_id':self.store_id}
        if marker.exists():
            if read_json(marker)[0]!=expected:raise SnapshotError('归档库不属于当前数据库。')
        elif any(self.store.iterdir()):raise SnapshotError('拒绝接管非空未知目录。')
        else:write_json(marker,expected)
        for name in ('staging','versions','locks'):
            safe_path(self.store/name).mkdir(exist_ok=True)

    def _get(self,task_id,restore=False):
        key=identifier(task_id);table='local_restores' if restore else 'local_versions';column='restore_id' if restore else 'task_id'
        with self.db.connect() as c:row=c.execute(f'SELECT * FROM {table} WHERE {column}=?',(key,)).fetchone()
        if row is None:raise SnapshotError('任务不存在。')
        result=dict(row)
        if not restore and result['store_path']!=str(self.store):raise SnapshotError('归档库路径与任务记录不一致。')
        if not restore:
            if hashlib.sha256(result['request_json'].encode('ascii')).hexdigest()!=result['request_hash']:raise SnapshotError('冻结任务记录损坏。')
            request=json.loads(result['request_json'])
            if any(request[k]!=result[k] for k in ('project_id','label','note')):raise SnapshotError('任务身份或标签记录不一致。')
            validate_entries(request['files'])
        return result

    def _update(self,key,restore=False,**values):
        table='local_restores' if restore else 'local_versions';column='restore_id' if restore else 'task_id'
        values['updated_at']=now()
        with self.db.connect() as c:
            c.execute('PRAGMA synchronous=FULL');c.execute('BEGIN IMMEDIATE')
            c.execute(f'UPDATE {table} SET '+','.join(k+'=?' for k in values)+f' WHERE {column}=?',(*values.values(),key))
            c.execute('INSERT INTO local_snapshot_events(task_id,kind,state,details,created_at) VALUES (?,?,?,?,?)',
                (key,'restore' if restore else 'snapshot',values.get('state','integrity'),encoded(values).decode(),now()))

    def events(self,key,*,restore=False):
        self._get(key,restore)
        with self.db.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM local_snapshot_events WHERE task_id=? AND kind=? ORDER BY event_id DESC LIMIT 500',
                (key,'restore' if restore else 'snapshot'))]

    def history(self,project_id,*,complete_only=False):
        with self.db.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM local_versions WHERE project_id=?'+
                (" AND state='complete' AND integrity='verified'" if complete_only else '')+' ORDER BY created_at DESC',(project_id,))]

    def restores(self,project_id):
        with self.db.connect() as c:
            return [dict(r) for r in c.execute('SELECT r.* FROM local_restores r JOIN local_versions v USING(version_id) WHERE v.project_id=? ORDER BY r.created_at DESC',(project_id,))]

    @library_operation
    def prepare(self,project_id,root,selections,label,note='',*,required=(),task_id=None,cancel=None,progress=None):
        self.repository.get_project(project_id);root=safe_path(root)
        root=safe_path(os.path.realpath(root,strict=True))
        if not root.is_dir() or root.is_relative_to(self.store) or self.store.is_relative_to(root):raise SnapshotError('项目根与归档库必须分开。')
        label=portable_text(label.strip());note=portable_text(note.strip())
        if not 1<=len(label)<=80 or len(note)>4000:raise SnapshotError('版本标签为1至80字，说明最多4000字。')
        if not 0<len(selections)<=MAX_FILES:raise SnapshotError('请选择1至2000个文件。')
        with self.db.connect() as c:known={r['file_id']:dict(r) for r in c.execute('SELECT * FROM project_files WHERE project_id=?',(project_id,))}
        files=[];total=0
        for choice in selections:
            check_cancel(cancel)
            if choice['file_id'] not in known:raise SnapshotError('只能归档当前项目已有文件记录。')
            row=known[choice['file_id']]
            from .restored_state import DETACHED
            if row['error'].startswith(DETACHED):raise SnapshotError('工作文件待重新关联，请明确选择新的工作文件创建新版本。')
            path=safe_path(row['path'])
            # Reject links first, then recover actual Windows filename casing.
            path=safe_path(os.path.realpath(path,strict=True))
            try:rel=relative(path.relative_to(root).as_posix())
            except ValueError as exc:raise SnapshotError('选中文件不在指定项目根目录下。') from exc
            if progress:progress('冻结清单：'+rel)
            result=digest(path,cancel=cancel)
            total+=result['size']
            if total>MAX_TOTAL:raise SnapshotError('总容量超过8 GiB。')
            files.append(dict(file_id=row['file_id'],record_id=row['record_id'],relative_path=rel,
                role=choice.get('role',role_for(path)),software=portable_text(str(choice.get('software','未知')))[:120],
                size=result['size'],sha256=result['sha256'],signature=result['signature'],source_path=str(path)))
        files.sort(key=lambda f:f['relative_path']);validate_entries(files)
        required=sorted(set(relative(r) for r in required))
        if set(required)-{f['relative_path'] for f in files}:raise SnapshotError('缺少已声明的必需文件，不能创建完整文件集。')
        request=dict(project_id=project_id,root=str(root),label=label,note=note,files=files,required=required)
        binding=hashlib.sha256(encoded(request)).hexdigest();task=identifier(task_id)
        self.checkpoint('prepare_before_db')
        with self.db.connect() as c:
            c.execute('PRAGMA synchronous=FULL');c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT request_hash FROM local_versions WHERE task_id=?',(task,)).fetchone()
            if old:
                if old[0]!=binding:raise SnapshotError('清单或说明已变化，不能沿用原任务ID。')
                return task
            try:c.execute('''INSERT INTO local_versions(task_id,version_id,project_id,label,note,created_at,request_hash,
                request_json,store_path,state,updated_at) VALUES (?,?,?,?,?,?,?,?,?,'prepared',?)''',
                (task,identifier(),project_id,label,note,now(),binding,encoded(request).decode(),str(self.store),now()))
            except sqlite3.IntegrityError as exc:raise SnapshotError('该项目版本标签已存在，请建立新标签。') from exc
            c.execute('INSERT INTO local_snapshot_events(task_id,kind,state,details,created_at) VALUES (?,\'snapshot\',\'prepared\',?,?)',
                (task,encoded({'request_hash':binding}).decode(),now()))
        self.checkpoint('prepared');return task

    def _manifest(self,row):
        return snapshot_manifest(row)

    def _verify_at(self,row,folder,*,cancel=None):
        return verify_snapshot_folder(row,self.store_id,folder,cancel=cancel)

    def _sources(self,row,cancel=None):
        for f in json.loads(row['request_json'])['files']:
            current=digest(f['source_path'],cancel=cancel)
            if any(current[k]!=f[k] for k in ('size','sha256','signature')):raise SnapshotError('冻结后源文件发生变化，任务未提交。')

    def _stage(self,row):
        name=uuid.uuid4().hex;stage=safe_path(self.store/'staging'/name)
        self._update(row['task_id'],state='copying',stage_name=name,error='')
        stage.mkdir();write_json(stage/'owner.json',{'store_id':self.store_id,'task_id':row['task_id']})
        (stage/'payload').mkdir();self.checkpoint('stage_created');return stage

    def _space(self,folder,size):
        if shutil.disk_usage(folder).free<size+16*1024**2:raise SnapshotError('可用空间不足（另保留16 MiB余量）。')

    @library_operation
    def run(self,task_id,*,cancel=None,progress=None):
        row=self._get(task_id)
        from .restored_state import reject_detached_task
        reject_detached_task(self.db,task_id)
        with project_lock(self.store/'locks',row['project_id']):
            row=self._get(task_id);dest=safe_path(self.store/'versions'/identifier(row['version_id']))
            if row['state']=='complete':
                self.verify(task_id,cancel=cancel);return self._get(task_id)
            try:
                if dest.exists():
                    self._verify_at(row,dest)  # beyond commit: do not pretend cancellation or touch sources
                elif row['state']=='complete':raise SnapshotError('数据库为完成，但归档目录失踪。')
                else:
                    check_cancel(cancel);request=json.loads(row['request_json']);self._space(self.store,sum(f['size'] for f in request['files']))
                    if row['state']=='sealed':
                        if not re.fullmatch('[0-9a-f]{32}',row['stage_name']):raise SnapshotError('暂存目录标识异常。')
                        stage=safe_path(self.store/'staging'/row['stage_name'])
                        if read_json(stage/'owner.json')[0]!={'store_id':self.store_id,'task_id':row['task_id']}:raise SnapshotError('暂存归属不符。')
                        self._verify_at(row,stage,cancel=cancel)
                    else:
                        stage=self._stage(row)
                        for f in request['files']:
                            if progress:progress('复制：'+f['relative_path'])
                            result=digest(f['source_path'],target=stage/'payload'/f['relative_path'],cancel=cancel,
                                progress=lambda count:self.checkpoint('copy_chunk'))
                            if any(result[k]!=f[k] for k in ('size','sha256','signature')):raise SnapshotError('复制时源文件变化。')
                            self.checkpoint('file_copied')
                        self._sources(row,cancel);verify_tree(stage/'payload',request['files'],cancel=cancel);self.checkpoint('files_verified')
                        manifest_sha=write_json(stage/'manifest.json',self._manifest(row));self.checkpoint('manifest_written')
                        self._update(task_id,state='sealed',manifest_sha=manifest_sha);row=self._get(task_id);self.checkpoint('sealed')
                    self._sources(row,cancel);check_cancel(cancel);self.checkpoint('fs_before_commit');rename_new(stage,dest);self.checkpoint('fs_committed')
                self._update(task_id,state='committed',error='');self.checkpoint('db_before_complete')
                self._update(task_id,state='complete',integrity='verified',error='');self.checkpoint('db_complete')
                return self._get(task_id)
            except Exception as exc:
                try:self._update(task_id,state=('committed' if dest.exists() else 'cancelled' if isinstance(exc,Cancelled) else 'failed'),error=str(exc))
                except Exception:pass  # original failure and durable prepare/rename evidence remain
                raise

    @library_operation
    def verify(self,task_id,*,cancel=None):
        row=self._get(task_id)
        if row['state']!='complete':raise SnapshotError('任务尚未完成，请先恢复任务。')
        try:self._verify_at(row,safe_path(self.store/'versions'/identifier(row['version_id'])),cancel=cancel)
        except Cancelled:raise
        except Exception as exc:
            self._update(task_id,integrity='failed',error=str(exc));raise
        self._update(task_id,integrity='verified',error='');return True

    def _working_root(self,row):
        from .restored_state import restored_instance
        root=json.loads(row['request_json'])['root']
        # Historical source locations are not probed in a restored instance.
        return Path(os.path.abspath(root)) if restored_instance(self.db) else safe_path(root)

    @library_operation
    def restore(self,task_id,destination,*,restore_id=None,cancel=None,progress=None):
        row=self._get(task_id);destination=safe_path(destination);restore_id=identifier(restore_id)
        if destination.is_relative_to(Path(__file__).resolve().parents[1]):raise SnapshotError('恢复输出不能放在源码工作树内。')
        with project_lock(self.store/'locks',row['project_id']):
            root=self._working_root(row)
            if destination.is_relative_to(root) or root.is_relative_to(destination) or destination.is_relative_to(self.store) or self.store.is_relative_to(destination):raise SnapshotError('恢复目录必须独立于工作工程及归档库。')
            with self.db.connect() as c:existing=c.execute('SELECT * FROM local_restores WHERE restore_id=?',(restore_id,)).fetchone()
            if existing:
                if existing['version_id']!=row['version_id'] or existing['destination']!=str(destination):raise SnapshotError('恢复任务绑定已变化。')
            else:
                root=self._working_root(row)
                if destination.exists() or not destination.parent.is_dir():raise SnapshotError('请选择父目录已存在的新目录，禁止覆盖或合并。')
                if destination.is_relative_to(root) or root.is_relative_to(destination) or destination.is_relative_to(self.store) or self.store.is_relative_to(destination):raise SnapshotError('恢复目录必须独立于工作工程及归档库。')
                self.verify(task_id,cancel=cancel)
                self.checkpoint('restore_before_db')
                with self.db.connect() as c:
                    c.execute('PRAGMA synchronous=FULL');c.execute('INSERT INTO local_restores(restore_id,version_id,destination,state,manifest_sha,created_at,updated_at) VALUES (?,?,?,\'prepared\',?,?,?)',
                        (restore_id,row['version_id'],str(destination),row['manifest_sha'],now(),now()))
                    c.execute('INSERT INTO local_snapshot_events(task_id,kind,state,details,created_at) VALUES (?,\'restore\',\'prepared\',?,?)',
                        (restore_id,encoded({'version_id':row['version_id'],'destination':str(destination)}).decode(),now()))
                self.checkpoint('restore_prepared')
            return self._run_restore(row,restore_id,cancel,progress)

    def _run_restore(self,row,restore_id,cancel,progress):
        job=self._get(restore_id,True);dest=safe_path(job['destination']);doc=self._manifest(row)
        if job['manifest_sha']!=row['manifest_sha']:raise SnapshotError('恢复任务与版本清单不一致。')
        receipt={'restore_id':restore_id,'version_id':row['version_id'],'manifest_sha':job['manifest_sha'],'byte_integrity':'verified','cad_openability':'unverified'}
        try:
            if dest.exists():
                if read_json(dest/'.mw-restore.json')[0]!=receipt:raise SnapshotError('已存在目录不属于该恢复任务，拒绝接管。')
                verify_tree(dest,doc['files'],receipt=True)
            elif job['state']=='complete':raise SnapshotError('已恢复目录失踪。')
            else:
                self.verify(row['task_id'],cancel=cancel);check_cancel(cancel)
                self._space(dest.parent,sum(f['size'] for f in doc['files']))
                name='.mw-restore-'+uuid.uuid4().hex;stage=safe_path(dest.parent/name)
                self._update(restore_id,True,state='copying',stage_name=name,error='');stage.mkdir()
                write_json(stage/'owner.json',receipt);(stage/'payload').mkdir();self.checkpoint('restore_stage_created')
                source=self.store/'versions'/identifier(row['version_id'])/'payload'
                for f in doc['files']:
                    if progress:progress('恢复：'+f['relative_path'])
                    result=digest(source/f['relative_path'],target=stage/'payload'/f['relative_path'],cancel=cancel,
                        progress=lambda count:self.checkpoint('restore_copy_chunk'))
                    if (result['size'],result['sha256'])!=(f['size'],f['sha256']):raise SnapshotError('恢复期间归档文件变化。')
                    self.checkpoint('restore_file_copied')
                verify_tree(stage/'payload',doc['files'],cancel=cancel)
                write_json(stage/'payload'/'.mw-restore.json',receipt);self.checkpoint('restore_marker_written')
                self._update(restore_id,True,state='sealed');self.checkpoint('restore_sealed')
                check_cancel(cancel);self.checkpoint('restore_fs_before_commit');rename_new(stage/'payload',dest);self.checkpoint('restore_fs_committed')
            self._update(restore_id,True,state='committed');self.checkpoint('restore_db_before_complete')
            verify_tree(dest,doc['files'],receipt=True)
            self._update(restore_id,True,state='complete',error='');self.checkpoint('restore_db_complete')
            return self._get(restore_id,True)
        except Exception as exc:
            try:self._update(restore_id,True,state='cancelled' if isinstance(exc,Cancelled) else 'failed',error=str(exc))
            except Exception:pass
            raise

    @library_operation
    def resume_restore(self,restore_id,*,cancel=None,progress=None):
        from .restored_state import reject_detached_task
        reject_detached_task(self.db,restore_id,restore=True)
        job=self._get(restore_id,True)
        with self.db.connect() as c:task=c.execute('SELECT task_id FROM local_versions WHERE version_id=?',(job['version_id'],)).fetchone()[0]
        return self.restore(task,job['destination'],restore_id=restore_id,cancel=cancel,progress=progress)

    @library_operation
    def abort(self,key,*,restore=False):
        from .restored_state import reject_detached_task
        reject_detached_task(self.db,key,restore=restore)
        row=self._get(key,restore)
        if restore:
            with self.db.connect() as c:task=c.execute('SELECT task_id FROM local_versions WHERE version_id=?',(row['version_id'],)).fetchone()[0]
            version=self._get(task)
        else:version=row
        with project_lock(self.store/'locks',version['project_id']):
            dest=safe_path(row['destination']) if restore else self.store/'versions'/identifier(row['version_id'])
            if row['state']=='complete' or dest.exists():raise SnapshotError('文件已提交；请校验/恢复登记，不能伪报取消或删除。')
            self._update(key,restore,state='cancelled',error='用户终止；保留暂存证据，不自动删除历史或工作文件。')
