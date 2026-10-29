# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import hashlib
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from PyQt6.QtCore import QTimer, Qt

from mechanical_project_namer.data import Database, Repository
from mechanical_project_namer.data.database import SCHEMA_VERSION
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.project_files import (
    EXTENSIONS, CatalogError, FileEntry, ProjectFileCatalog, Source, local_path,
)
from mechanical_project_namer.ui.project_files_page import ProjectFilesPage


@pytest.fixture
def catalog(tmp_path):
    db = Database(tmp_path / 'isolated.db'); db.migrate()
    repo = Repository(db)
    project = ProjectService(repo).create('虚构测试设备甲', '', 'DEMO', modules=[('DJ', '演示模块')])
    return ProjectFileCatalog(repo), project, tmp_path


def import_paths(catalog, project, paths):
    batch = catalog.scan(project.id, [Source(str(p), 'directory' if p.is_dir() else 'file') for p in paths])
    catalog.commit(batch)
    return batch


def business_state(db):
    with db.connect() as con:
        return {table: [tuple(row) for row in con.execute('SELECT * FROM ' + table)] for table in
                ('projects', 'project_modules', 'drawing_records', 'number_reservations',
                 'part_sequence_counters', 'record_files', 'solidworks_operations')}


@pytest.mark.parametrize('extension', EXTENSIONS)
def test_supported_unicode_file_is_indexed_without_mutating_source_or_numbering(catalog, extension):
    service, project, root = catalog
    folder = root / '中文 项目 😀'; folder.mkdir()
    path = folder / ('虚构零件 零号é' + extension.upper()); path.write_bytes(b'fictional CAD placeholder')
    before = business_state(service.database)
    stamp = (path.name, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
    import_paths(service, project, [path])
    row = service.list_files(project.id)[0]
    assert row['project_id'] == project.id and row['record_id'] is None
    assert row['state'] == 'present' and row['path'] == str(path)
    assert row['extension'] == extension and row['file_size'] == path.stat().st_size
    assert (path.name, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) == stamp
    assert business_state(service.database) == before
    assert len(service.repository.list_projects()) == 1


def test_overlapping_roots_duplicate_import_and_restart_keep_one_identity(catalog):
    service, project, root = catalog
    folder = root/'files'; folder.mkdir()
    path = folder/'演示.PDF'; path.write_bytes(b'fake')
    import_paths(service, project, [folder, path, folder])
    first = service.list_files()[0]['file_id']
    import_paths(service, project, [path])
    reopened = ProjectFileCatalog(Repository(Database(service.database.path)))
    reopened.commit(reopened.scan(project.id, refresh=True))
    assert len(reopened.list_files()) == 1 and reopened.list_files()[0]['file_id'] == first
    assert len(reopened.repository.list_projects()) == 1


def test_project_rename_and_code_change_preserve_index_and_search(catalog):
    service, project, root = catalog
    path=root/'模型.step'; path.write_bytes(b'fake')
    import_paths(service, project, [path])
    file_id=service.list_files()[0]['file_id']
    ProjectService(service.repository).update(project.id, confirm_code_change=True, name='新显示名', code='NEW')
    row=service.list_files(query='新显示名')[0]
    assert row['file_id']==file_id and row['project_id']==project.id
    assert len(service.list_files(query='NEW'))==1 and len(service.list_files(query='.STEP'))==1
    assert len(service.list_files(extension='.pdf'))==0


def test_record_association_reuses_existing_identity_and_rejects_cross_project(catalog):
    service, project, root=catalog
    record=service.repository.save_named_record(project.id,'DJ','通用编号','演示件')
    path=root/'随意名称.sldprt'; path.write_bytes(b'fake')
    other=ProjectService(service.repository).create('虚构设备乙','','OTHER')
    other_record=service.repository.save_named_record(other.id,'01','自制机加工件','其他件')
    import_paths(service,project,[path]); file_id=service.list_files(project.id)[0]['file_id']
    before=business_state(service.database)
    service.associate_record(file_id,record.id)
    assert service.list_files(query=record.filename)[0]['record_id']==record.id
    with pytest.raises(CatalogError): service.associate_record(file_id,other_record.id)
    assert service.list_files(project.id)[0]['record_id']==record.id
    service.associate_record(file_id,None)
    assert service.list_files(project.id)[0]['record_id'] is None
    assert business_state(service.database)==before


def test_existing_legacy_path_links_are_reused_without_creating_records(catalog):
    service,project,root=catalog
    path=root/'原有文件.sldprt';path.write_bytes(b'fake')
    record=service.repository.save_named_record(project.id,'DJ','通用编号','演示件')
    with service.database.transaction() as con:
        con.execute('UPDATE drawing_records SET file_path=? WHERE id=?',(str(path),record.id))
    before=business_state(service.database)
    import_paths(service,project,[path])
    row=service.list_files()[0];assert row['record_id']==record.id
    service.associate_record(row['file_id'],None)
    service.commit(service.scan(project.id,refresh=True))
    assert service.list_files()[0]['record_id'] is None
    assert business_state(service.database)==before


def test_missing_and_restored_files_and_new_files_are_rescanned_without_deleting_indexes(catalog):
    service,project,root=catalog
    folder=root/'资料';folder.mkdir();path=folder/'设计.pdf';path.write_bytes(b'fake')
    import_paths(service,project,[folder]);first=service.list_files()[0]['file_id']
    path.unlink()
    service.commit(service.scan(project.id,refresh=True))
    assert service.list_files()[0]['state']=='missing'
    assert service.list_files()[0]['file_id']==first
    path.write_bytes(b'restored');(folder/'新增.glb').write_bytes(b'fake')
    service.commit(service.scan(project.id,refresh=True))
    assert len(service.list_files())==2 and all(r['state']=='present' for r in service.list_files())


def test_sql_failure_rolls_back_sources_and_all_file_rows_and_can_retry(catalog):
    service,project,root=catalog
    paths=[root/'a.pdf',root/'b.pdf']
    for p in paths:p.write_bytes(b'fake')
    batch=service.scan(project.id,[Source(str(p),'file') for p in paths])
    with service.database.transaction() as con:
        con.execute("CREATE TRIGGER reject_second BEFORE INSERT ON project_files WHEN NEW.name='b.pdf' BEGIN SELECT RAISE(ABORT,'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError):service.commit(batch)
    assert service.list_files()==[] and service.sources(project.id)==[]
    with service.database.transaction() as con:con.execute('DROP TRIGGER reject_second')
    service.commit(batch);assert len(service.list_files())==2


def test_changed_file_after_scan_rejects_entire_batch(catalog):
    service,project,root=catalog
    path=root/'changed.stl';path.write_bytes(b'a')
    batch=service.scan(project.id,[Source(str(path),'file')]);path.write_bytes(b'new value')
    with pytest.raises(CatalogError,match='变化'):service.commit(batch)
    assert service.list_files()==[] and service.sources(project.id)==[]


@pytest.mark.parametrize('mode',['cancel','count','time','depth'])
def test_incomplete_scan_cannot_commit_any_indexes(catalog,mode):
    service,project,root=catalog
    folder=root/'files';folder.mkdir();(folder/'a.pdf').write_bytes(b'fake')
    deep=folder/'child';deep.mkdir();(deep/'b.stp').write_bytes(b'fake')
    event=threading.Event()
    if mode=='cancel':event.set()
    options=dict(cancel=event,max_entries=0 if mode=='count' else 100,
                 seconds=-1 if mode=='time' else 15,max_depth=0 if mode=='depth' else 32)
    batch=service.scan(project.id,[Source(str(folder),'directory')],**options)
    assert not batch.complete
    with pytest.raises(CatalogError):service.commit(batch)
    assert service.list_files()==[] and service.sources(project.id)==[]


def test_cancel_during_transaction_rolls_back(catalog,monkeypatch):
    service,project,root=catalog
    paths=[root/'a.pdf',root/'b.pdf']
    for path in paths:path.write_bytes(b'fake')
    batch=service.scan(project.id,[Source(str(path),'file') for path in paths]);event=threading.Event()
    connect=service.database.connect
    def cancellable_connection():
        con=connect();con.create_function('cancel_catalog_batch',0,lambda:event.set() or 1)
        return con
    monkeypatch.setattr(service.database,'connect',cancellable_connection)
    with service.database.transaction() as con:
        con.execute("CREATE TRIGGER cancel_first AFTER INSERT ON project_files WHEN NEW.name='a.pdf' BEGIN SELECT cancel_catalog_batch(); END")
    with pytest.raises(CatalogError):service.commit(batch,cancel=event)
    assert event.is_set() and service.list_files()==[] and service.sources(project.id)==[]


@pytest.mark.parametrize('path',['relative/folder','\\\\server\\share\\folder','\\\\?\\C:\\folder','bad\x00.pdf'])
def test_unsafe_and_network_paths_are_rejected(path):
    with pytest.raises(CatalogError):local_path(path)


def test_mapped_network_drive_is_rejected_before_file_access(tmp_path,monkeypatch):
    monkeypatch.setattr('mechanical_project_namer.project_files._network_drive',lambda anchor:True)
    with pytest.raises(CatalogError,match='网络盘'):local_path(tmp_path/'not-accessed.pdf')


def test_nested_link_is_not_followed_and_permission_error_is_preserved(catalog,monkeypatch):
    service,project,root=catalog
    folder=root/'safe';folder.mkdir();path=folder/'a.pdf';path.write_bytes(b'fake')
    import_paths(service,project,[folder])
    actual=Path.lstat
    def linked(p,*args,**kwargs):
        if p==path:
            from types import SimpleNamespace
            return SimpleNamespace(st_mode=0,st_file_attributes=1024)
        return actual(p,*args,**kwargs)
    monkeypatch.setattr(Path,'lstat',linked)
    service.commit(service.scan(project.id,refresh=True))
    assert service.list_files()[0]['state']=='blocked'
    monkeypatch.setattr(Path,'lstat',actual)
    original_open=Path.open
    def denied(p,*args,**kwargs):
        if p==path:raise PermissionError('test-only access denied')
        return original_open(p,*args,**kwargs)
    monkeypatch.setattr(Path,'open',denied)
    service.commit(service.scan(project.id,refresh=True))
    assert service.list_files()[0]['state']=='unreadable'


def test_schema11_upgrade_is_idempotent_and_preserves_history_and_counters(tmp_path):
    db=Database(tmp_path/'old.db')
    schema=(Path(__file__).parent/'fixtures/schema_v11.sql').read_text(encoding='utf-8')
    with db.connect() as con:con.executescript(schema);con.execute('INSERT INTO schema_meta VALUES(11)')
    repo=Repository(db);project=ProjectService(repo).create('旧项目','','OLD')
    record=repo.save_named_record(project.id,'01','自制机加工件','历史件')
    repo.set_record_status(record.id,'void')
    before=business_state(db)
    db.migrate();db.migrate()
    after=business_state(db)
    assert after['projects']==before['projects'] and after['drawing_records']==before['drawing_records']
    assert after['number_reservations']==before['number_reservations']
    assert after['part_sequence_counters']==before['part_sequence_counters']
    with db.connect() as con:
        assert con.execute('SELECT version FROM schema_meta').fetchone()[0]==SCHEMA_VERSION
        assert con.execute('PRAGMA foreign_key_check').fetchall()==[]
    backups=list((tmp_path/'backups/database').glob(f'*pre_schema{SCHEMA_VERSION}*.db'));assert len(backups)==1
    with sqlite3.connect(backups[0]) as con:
        assert con.execute('SELECT version FROM schema_meta').fetchone()[0]==11
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_file_page_background_scan_filters_opens_and_locates_without_blocking_ui(catalog,qtbot,monkeypatch):
    service,project,root=catalog
    path=root/'中文 演示.glb';path.write_bytes(b'fake')
    page=ProjectFilesPage(service.repository);qtbot.addWidget(page);page.resize(1000,650)
    page.load_projects(project.id);page.show()
    actual=page.catalog.scan
    def slow(*args,**kwargs):
        time.sleep(.15)
        assert threading.current_thread() is not threading.main_thread()
        return actual(*args,**kwargs)
    monkeypatch.setattr(page.catalog,'scan',slow)
    ticks=[];timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:ticks.append(True));timer.start()
    page.start_scan([Source(str(path),'file')]);qtbot.waitUntil(lambda:page.worker is None,timeout=5000);timer.stop()
    assert len(ticks)>=5 and page.table.rowCount()==1
    page.table.selectRow(0);opened=[]
    result=page.open_selected(opener=lambda url:opened.append(url.toLocalFile()) or True)
    assert result.ok and [Path(value) for value in opened]==[path]
    assert page.open_selected(True,opener=lambda url:opened.append(url.toLocalFile()) or True).ok
    assert Path(opened[-1])==path.parent
    page.search.setText('不存在');assert page.table.rowCount()==0
    page.search.clear();page.extension.setCurrentIndex(page.extension.findData('.pdf'));assert page.table.rowCount()==0
    assert page.sizeHint().width()<1800


def test_file_page_cancel_and_failure_leave_ui_reusable(catalog,qtbot,monkeypatch):
    service,project,root=catalog
    path=root/'a.pdf';path.write_bytes(b'fake')
    page=ProjectFilesPage(service.repository);qtbot.addWidget(page);page.load_projects(project.id)
    original=page.catalog.scan
    def wait_cancel(*args,**kwargs):
        assert kwargs['cancel'].wait(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(page.catalog,'scan',wait_cancel)
    page.start_scan([Source(str(path),'file')]);page.cancel_scan()
    qtbot.waitUntil(lambda:page.worker is None,timeout=3000)
    assert service.list_files()==[] and page.add_files.isEnabled()
    monkeypatch.setattr(page.catalog,'scan',lambda *a,**k:(_ for _ in ()).throw(OSError('fictional failure')))
    page.start_scan([Source(str(path),'file')]);qtbot.waitUntil(lambda:page.worker is None,timeout=3000)
    assert '失败' in page.status.text() and page.rescan.isEnabled()


def test_large_directory_runs_in_background_and_budget_protects_atomicity(catalog,qtbot):
    service,project,root=catalog
    folder=root/'大量虚构文件';folder.mkdir()
    for index in range(512): (folder/f'虚构件-{index:04d}.step').write_bytes(b'fake')
    before=business_state(service.database)
    page=ProjectFilesPage(service.repository);qtbot.addWidget(page);page.load_projects(project.id)
    ticks=[];timer=QTimer();timer.setInterval(1);timer.timeout.connect(lambda:ticks.append(True));timer.start()
    page.start_scan([Source(str(folder),'directory')])
    qtbot.waitUntil(lambda:page.worker is None,timeout=15000);timer.stop()
    assert ticks and len(service.list_files())==512
    assert business_state(service.database)==before
    batch=service.scan(project.id,[Source(str(folder),'directory')],max_entries=20)
    assert not batch.complete and batch.visited==21
    with pytest.raises(CatalogError):service.commit(batch)
    assert len(service.list_files())==512


def test_same_name_in_two_directories_and_projects_remains_distinct(catalog):
    service,project,root=catalog
    a=root/'a';b=root/'b';a.mkdir();b.mkdir()
    for folder in (a,b):(folder/'同名.pdf').write_bytes(b'fake')
    import_paths(service,project,[a,b])
    other=ProjectService(service.repository).create('另一虚构项目','','OTHER')
    import_paths(service,other,[a])
    assert len(service.list_files(project.id))==2 and len(service.list_files(other.id))==1
    assert len(service.list_files())==3


def test_record_files_legacy_link_and_record_delete_do_not_delete_source(catalog):
    service,project,root=catalog
    path=root/'任意工程图.pdf';path.write_bytes(b'fake')
    record=service.repository.save_named_record(project.id,'DJ','通用编号','演示件')
    with service.database.transaction() as con:
        con.execute('''INSERT INTO record_files(record_id,path,extension,file_size,mtime_ns,created_at)
            VALUES (?,?,?,?,?,?)''',(record.id,str(path),'.pdf',4,path.stat().st_mtime_ns,'test'))
    import_paths(service,project,[path])
    assert service.list_files()[0]['record_id']==record.id
    service.repository.delete_record(record.id)
    assert path.read_bytes()==b'fake' and service.list_files()[0]['record_id'] is None


def test_missing_file_open_is_reported_without_invoking_associated_program(catalog,qtbot):
    service,project,root=catalog
    path=root/'临时文件.pdf';path.write_bytes(b'fake');import_paths(service,project,[path])
    page=ProjectFilesPage(service.repository);qtbot.addWidget(page);page.load_projects(project.id)
    page.table.selectRow(0);path.unlink();opened=[]
    assert page.open_selected(opener=lambda url:opened.append(url) or True) is None
    assert not opened and '失踪' in page.status.text()
    assert len(service.list_files())==1


def test_bad_timestamp_does_not_hide_other_files_or_crash_page(catalog,qtbot):
    service,project,root=catalog
    paths=[root/'a.pdf',root/'b.pdf']
    for path in paths:path.write_bytes(b'fake')
    import_paths(service,project,paths)
    with service.database.transaction() as con:
        con.execute("UPDATE project_files SET mtime_ns=9223372036854775807*1000000000 WHERE name='a.pdf'")
    page=ProjectFilesPage(service.repository);qtbot.addWidget(page);page.load_projects(project.id)
    assert page.table.rowCount()==2 and page.table.item(0,6).text()=='时间异常'
    assert page.table.item(1,0).text()=='b.pdf'
