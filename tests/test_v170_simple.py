# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from pathlib import Path
import sqlite3

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from mechanical_project_namer.data import Database, Repository, DataExporter
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.numbering.simple import SIMPLE_TYPE
from mechanical_project_namer.numbering.workflow import NamingWorkflow
from mechanical_project_namer.file_sync import scan_project, import_entries, READY, path_key, ExternalFilesNeedSync
from mechanical_project_namer.solidworks.service import SolidWorksService
from mechanical_project_namer.solidworks.models import OperationResult
from mechanical_project_namer.settings import AppSettings
from mechanical_project_namer.ui.simple_window import SimpleMainWindow, SimpleProjectDialog, SimpleMiniWindow
from mechanical_project_namer.ui.record_dialog import RecordDetailDialog


@pytest.fixture
def simple(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    monkeypatch.setenv('APPDATA', str(tmp_path / 'roaming'))
    database = Database(tmp_path / 'data.db'); database.migrate()
    repo = Repository(database)
    folder = tmp_path / 'parts'; folder.mkdir()
    project = ProjectService(repo).create('钢筋弯曲机', '', 'WQJ', directory=str(folder),
        solidworks_part_directory=str(folder), modules=[('DJ', '电机'), ('JJ', '机架')])
    return database, repo, project, folder


def test_full_filename_manual_number_search_and_note_edit(simple, qtbot):
    db, repo, project, folder = simple
    record = repo.save_named_record(project.id, 'dj', SIMPLE_TYPE, '三相电机380', '额外资料', sequence_override=7)
    assert record.full_name == 'WQJ-DJ-007-三相电机380'
    assert record.part_sequence == 7 and record.extra_notes == '额外资料'
    assert repo.list_records(query=record.full_name)[0].id == record.id
    assert repo.list_records(query='DJ')[0].id == record.id
    with db.transaction() as c:
        c.execute("UPDATE drawing_records SET file_extension='.SLDPRT' WHERE id=?", (record.id,))
    assert repo.list_records(query=record.full_name + '.SLDPRT')[0].id == record.id
    dialog = RecordDetailDialog(repo.get_record(record.id)); qtbot.addWidget(dialog)
    dialog.part_name.setText('三相电机220')
    assert dialog.full_name.text() == 'WQJ-DJ-007-三相电机220'
    repo.update_record(record.id, *dialog.values())
    assert repo.get_record(record.id).full_name == 'WQJ-DJ-007-三相电机220'
    assert repo.get_record(record.id).extra_notes == '额外资料'


def test_auto_number_cross_category_reservations_void_and_manual_holes(simple):
    db, repo, project, folder = simple
    old = repo.save_named_record(project.id, 'DJ', '自制机加工件', '旧零件')
    assert old.full_name == 'WQJ-DJ-001 旧零件'
    repo.set_record_status(old.id, 'void')
    with pytest.raises(ValueError, match='已使用'):
        repo.save_named_record(project.id, 'DJ', SIMPLE_TYPE, '重复', sequence_override=1)
    high = repo.save_named_record(project.id, 'DJ', SIMPLE_TYPE, '手动指定', sequence_override=999)
    workflow = NamingWorkflow(repo, DataExporter(repo, folder.parent / 'exports'))
    assert workflow.preview_named(project.id, 'DJ', SIMPLE_TYPE, sequence_override=2) == 'WQJ-DJ-002'
    low = repo.save_named_record(project.id, 'DJ', SIMPLE_TYPE, '空号', sequence_override=2)
    repo.delete_record(low.id)
    with pytest.raises(ValueError, match='已使用'):
        repo.save_named_record(project.id, 'DJ', SIMPLE_TYPE, '不可复用', sequence_override=2)
    with pytest.raises(ValueError, match='用尽'):
        repo.get_next_named_number(project.id, 'DJ', SIMPLE_TYPE)
    assert repo.get_next_named_number(project.id, 'JJ', SIMPLE_TYPE) == 1


@pytest.mark.parametrize('value', [0, 1000, -1, True, 'abc', '1.5'])
def test_invalid_manual_sequence_is_atomic(simple, value):
    db, repo, project, folder = simple
    with pytest.raises(ValueError):
        repo.save_named_record(project.id, 'DJ', SIMPLE_TYPE, '电机', sequence_override=value)
    assert repo.list_records(project.id) == []
    assert repo.get_next_named_number(project.id, 'DJ', SIMPLE_TYPE) == 1


def test_edit_device_module_preserves_history_and_restart(simple):
    db, repo, project, folder = simple
    old = repo.save_named_record(project.id, 'DJ', SIMPLE_TYPE, '电机')
    repo.revise_module(project.id, 'DJ', 'DC', '电动机')
    assert repo.get_record(old.id) == old
    assert [m['module_code'] for m in repo.list_modules(project.id)] == ['DC', 'JJ']
    ProjectService(repo).update(project.id, confirm_code_change=True, code='WQJ2', name='新设备名')
    new = repo.save_named_record(project.id, 'DC', SIMPLE_TYPE, '电机')
    assert new.full_name == 'WQJ2-DC-001-电机'
    assert repo.get_record(old.id) == old
    db.migrate()
    assert {m['module_code'] for m in repo.list_modules(project.id, enabled_only=False)} == {'DJ', 'JJ', 'DC'}
    repo.set_module_enabled(project.id, 'DJ', True)
    assert repo.get_next_named_number(project.id, 'DJ', SIMPLE_TYPE) == 2
    with pytest.raises(ValueError, match='已存在'):
        repo.revise_module(project.id, 'DJ', 'DC', '重复模块')
    assert len(repo.list_modules(project.id)) == 3


def test_new_and_old_filenames_scan_import_without_renaming(simple):
    db, repo, project, folder = simple
    repo.create_module(project.id, '03', '旧模块')
    files = ['WQJ-DJ-006-三相电机380.SLDPRT', 'WQJ-DJ-006-三相电机380.pdf',
             'WQJ-03-028 滑座.SLDPRT', 'WQJ-JJ-100-机架总成.SLDASM']
    for name in files: (folder / name).write_bytes(b'test-only')
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    result = scan_project(repo, project.id)
    assert not result.errors and all(e.status in READY for e in result.entries)
    assert import_entries(repo, result, result.entries) == 3
    assert repo.get_next_named_number(project.id, 'DJ', SIMPLE_TYPE) == 7
    assert repo.get_next_named_number(project.id, '03', SIMPLE_TYPE) == 29
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before
    assert all(e.status == '已登记' for e in scan_project(repo, project.id).entries)


def test_manual_file_registration_can_be_revised_before_import(simple):
    db, repo, project, folder = simple
    actual = folder / '我原来起的名字.SLDPRT'; actual.write_bytes(b'keep-original')
    overrides = {path_key(actual): ('WQJ-DJ-015', '三相电机380', SIMPLE_TYPE)}
    result = scan_project(repo, project.id, overrides=overrides)
    assert result.entries[0].manual and result.entries[0].status == '可登记'
    overrides[path_key(actual)] = ('WQJ-JJ-016', '安装座', SIMPLE_TYPE)
    revised = scan_project(repo, project.id, overrides=overrides)
    import_entries(repo, revised, revised.entries, overrides=overrides)
    record = repo.list_records(project.id)[0]
    assert record.full_name == 'WQJ-JJ-016-安装座' and record.file_path == str(actual)
    assert actual.read_bytes() == b'keep-original' and len(list(folder.iterdir())) == 1
    assert scan_project(repo, project.id).entries[0].status == '已登记'


def test_unregistered_new_style_file_blocks_duplicate_creation(simple):
    db, repo, project, folder = simple
    actual = folder / 'WQJ-DJ-001-已手动创建.SLDPRT'; actual.write_bytes(b'keep-original')
    with pytest.raises(ExternalFilesNeedSync):
        SolidWorksService(repo).reserve(project.id, 'DJ', SIMPLE_TYPE, '不同名称', str(folder), sequence_override=1)
    assert actual.read_bytes() == b'keep-original'
    assert repo.list_records(project.id) == []


def test_sw_notes_persist_through_commit_recovery_and_number_advances_once(simple):
    db, repo, project, folder = simple
    service = SolidWorksService(repo, folder.parent / 'recovery')
    reservation = service.reserve(project.id, 'DJ', SIMPLE_TYPE, '三相电机380', str(folder), extra_notes='用户的详细备注', sequence_override=8)
    target = Path(reservation['target_path']); target.write_bytes(b'test-part')
    result = OperationResult(reservation['operation_id'], True, 'file_saved', 'ok', target_path=str(target),
        file_size=9, document_path=str(target), original_document_preserved=True)
    service._write_recovery(reservation['operation_id'], {'operation_id': reservation['operation_id'], 'result': result.to_dict()})
    service.reconcile_pending()
    record = repo.list_records(project.id)[0]
    assert record.extra_notes == '用户的详细备注' and record.full_name == 'WQJ-DJ-008-三相电机380'
    assert service.commit_created_file(reservation['operation_id'], result).id == record.id
    assert len(repo.list_records(project.id)) == 1 and repo.get_next_named_number(project.id, 'DJ', SIMPLE_TYPE) == 9


def test_total_records_do_not_limit_each_modules_numbering(simple):
    db, repo, project, folder = simple
    old = repo.save_named_record(project.id, 'DJ', SIMPLE_TYPE, '最后一个内部序号')
    with db.transaction() as c: c.execute('UPDATE drawing_records SET drawing_number=999 WHERE id=?', (old.id,))
    new = repo.save_named_record(project.id, 'JJ', SIMPLE_TYPE, '新模块第一号')
    assert new.drawing_number == 1000 and new.part_sequence == 1


def test_schema10_upgrade_preserves_links_ids_reservations_and_rollback_backup(tmp_path):
    # Public, schema-only fixture keeps the migration test runnable in a fresh clone.
    old_schema = (Path(__file__).parent / 'fixtures' / 'schema_v10.sql').read_text(encoding='utf-8')
    database = Database(tmp_path / 'old.db')
    with database.connect() as c:
        c.executescript(old_schema)
        c.execute('INSERT INTO schema_meta VALUES (10)')
    repo = Repository(database)
    project = ProjectService(repo).create('旧设备', '', 'OLD')
    record = repo.save_named_record(project.id, '03', '自制机加工件', '旧记录')
    with database.transaction() as c:
        c.execute("INSERT INTO record_files(record_id,path,extension,file_size,mtime_ns,created_at) VALUES (?,?,?,?,?,?)", (record.id, 'X:/not-a-real-file.SLDPRT', '.sldprt', 1, 1, 'now'))
    database.migrate(); database.migrate()
    assert repo.get_record(record.id) == record
    assert len(repo.list_record_files(record.id)) == 1
    with database.connect() as c:
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert c.execute('SELECT COUNT(*) FROM number_reservations').fetchone()[0] == 1
    from mechanical_project_namer.data.database import SCHEMA_VERSION
    assert list((tmp_path / 'backups/database').glob(f'*pre_schema{SCHEMA_VERSION}*.db'))


def test_simple_ui_enter_single_generate_manual_preview_and_no_notes_loss(simple, qtbot):
    db, repo, project, folder = simple
    window = SimpleMainWindow(AppSettings()); qtbot.addWidget(window); window.show()
    window.set_projects([project], project.id)
    window.set_current_project(project, [], repo.list_modules(project.id), 'DJ', SIMPLE_TYPE, 'WQJ-DJ-001')
    window.part_name.setText('三相电机380'); window.sequence_edit.setText('7')
    assert window.filename_preview.text() == 'WQJ-DJ-007-三相电机380.SLDPRT'
    emitted = []; saves = []
    window.solidworks_requested.connect(lambda *v: emitted.append(v))
    window.save_requested.connect(lambda *v: saves.append(v))
    qtbot.keyClick(window.part_name, Qt.Key.Key_Return)
    assert emitted == [('三相电机380', '')] and saves == []
    window.set_solidworks_busy(True)
    assert not window.module_combo.isEnabled() and not window.more_button.isEnabled()
    qtbot.keyClick(window.part_name, Qt.Key.Key_Return)
    assert len(emitted) == 1
    window.set_solidworks_busy(False)
    assert window.sequence_edit.text() == '007'


def test_simple_project_and_mini_dialogs(simple, qtbot):
    db, repo, project, folder = simple
    dialog = SimpleProjectDialog(); qtbot.addWidget(dialog)
    dialog.directory.setText(str(folder))
    assert dialog.values()['solidworks_part_directory'] == str(folder)
    mini = SimpleMiniWindow(); qtbot.addWidget(mini); mini.show()
    mini.set_project(project, [], repo.list_modules(project.id), 'DJ', SIMPLE_TYPE, 'WQJ-DJ-001')
    mini.part_name.setText('电机')
    assert mini.preview.text() == 'WQJ-DJ-001-电机.SLDPRT'
    emitted = []; mini.solidworks_requested.connect(lambda *v: emitted.append(v))
    qtbot.keyClick(mini.part_name, Qt.Key.Key_Return)
    assert emitted == [('电机', '')]


def test_empty_device_list_can_start_creation_and_edited_folder_takes_effect(simple, qtbot):
    db, repo, project, folder = simple
    window = SimpleMainWindow(AppSettings()); qtbot.addWidget(window)
    window.set_projects([], None)
    assert window.project_combo.currentIndex() == -1
    new_requests = []; window.new_project_requested.connect(lambda: new_requests.append(True))
    window.project_combo.setCurrentIndex(window.project_combo.findData(-1))
    assert new_requests == [True]
    edit = SimpleProjectDialog(project=project); qtbot.addWidget(edit)
    edit.directory.setText(str(folder / 'different'))
    assert edit.values()['solidworks_part_directory'] == str(folder / 'different')


def test_disabled_historical_module_file_does_not_block_new_module(simple):
    db, repo, project, folder = simple
    actual = folder / 'WQJ-DJ-001-电机.SLDPRT'; actual.write_bytes(b'test-only')
    result = scan_project(repo, project.id); import_entries(repo, result, result.entries)
    repo.revise_module(project.id, 'DJ', 'D', '新电机')
    assert scan_project(repo, project.id).entries[0].status == '已登记'
    new = repo.save_named_record(project.id, 'D', SIMPLE_TYPE, '另一电机')
    assert new.full_name == 'WQJ-D-001-另一电机'
    assert actual.read_bytes() == b'test-only'


def test_record_name_edit_is_visible_while_physical_file_keeps_original_name(simple, qtbot):
    db, repo, project, folder = simple
    actual = folder / 'WQJ-JJ-001-垫板.SLDPRT'; actual.write_bytes(b'test-only')
    result = scan_project(repo, project.id); import_entries(repo, result, result.entries)
    original = repo.list_records(project.id)[0]
    edited = repo.update_record(original.id, '垫板修改备注', '', '已保存')
    assert repo.list_records(query='机架')[0].id == edited.id
    window = SimpleMainWindow(AppSettings()); qtbot.addWidget(window)
    window.set_record_rows(window.current_records, [edited])
    assert window.current_records.item(0, 0).text() == 'WQJ-JJ-001-垫板修改备注.SLDPRT'
    assert str(actual) in window.current_records.item(0, 0).toolTip()
    assert actual.read_bytes() == b'test-only' and len(list(folder.iterdir())) == 1
    assert scan_project(repo, project.id).entries[0].status == '已登记'
    assert repo.save_named_record(project.id, 'JJ', SIMPLE_TYPE, '下一个').part_sequence == 2


def test_primary_file_association_survives_device_code_change(simple):
    db, repo, project, folder = simple
    record = repo.save_named_record(project.id, 'DJ', SIMPLE_TYPE, '原电机')
    actual = folder / (record.full_name + '.SLDPRT'); actual.write_bytes(b'test-only')
    with db.transaction() as c:
        c.execute("UPDATE drawing_records SET file_path=?,file_extension='.SLDPRT',file_exists=1 WHERE id=?", (str(actual), record.id))
    ProjectService(repo).update(project.id, confirm_code_change=True, code='WQJ2')
    repo.update_record(record.id, '电机新备注', '', '')
    result = scan_project(repo, project.id)
    assert len(result.entries) == 1 and result.entries[0].status == '已登记'
    assert result.entries[0].record_id == record.id
    assert repo.save_named_record(project.id, 'DJ', SIMPLE_TYPE, '下一件').full_name == 'WQJ2-DJ-002-下一件'
    assert actual.read_bytes() == b'test-only'


def test_manual_override_cannot_steal_an_existing_primary_file(simple):
    db, repo, project, folder = simple
    record = repo.save_named_record(project.id, 'DJ', SIMPLE_TYPE, '原电机')
    actual = folder / (record.full_name + '.SLDPRT'); actual.write_bytes(b'test-only')
    with db.transaction() as c: c.execute('UPDATE drawing_records SET file_path=? WHERE id=?', (str(actual), record.id))
    result = scan_project(repo, project.id, overrides={path_key(actual): ('WQJ-JJ-010', '不能重复登记', SIMPLE_TYPE)})
    assert result.entries[0].status == '已登记' and result.entries[0].record_id == record.id
    assert not any(e.status in READY for e in result.entries)


def test_shell_current_and_all_device_search_and_manual_copy(simple, qtbot, monkeypatch):
    from mechanical_project_namer.ai import NamingMemory
    from mechanical_project_namer.ui.shell import ApplicationShell
    db, repo, project, folder = simple
    other = ProjectService(repo).create('其他设备', '', 'OTHER', modules=[('DJ', '电机')])
    first = repo.save_named_record(project.id, 'DJ', SIMPLE_TYPE, '电机')
    second = repo.save_named_record(other.id, 'DJ', SIMPLE_TYPE, '电机')
    exporter = DataExporter(repo, folder.parent / 'exports')
    shell = ApplicationShell(QApplication.instance(), AppSettings(current_project_id=project.id, current_module_code='DJ'),
        QIcon(), repo, ProjectService(repo), NamingWorkflow(repo, exporter), exporter, NamingMemory(db))
    qtbot.addWidget(shell.main_window); qtbot.addWidget(shell.mini_window)
    shell.main_window.show(); shell.mini_window.hide()
    qtbot.waitUntil(lambda: shell.file_check_thread is None)
    assert shell.main_window.current_records.rowCount() == 1
    shell.main_window.search_scope.setCurrentIndex(1)
    assert shell.main_window.current_records.rowCount() == 2
    shell.main_window.current_search.setText(second.full_name)
    qtbot.waitUntil(lambda: shell.main_window.current_records.rowCount() == 1)
    assert shell.main_window.current_records.item(0, 0).data(Qt.ItemDataRole.UserRole) == second.id
    shell.main_window.part_name.setText('三相电机380'); shell.main_window.sequence_edit.setText('012')
    shell._copy_preview()
    assert QApplication.clipboard().text() == 'WQJ-DJ-012-三相电机380'
    assert repo.get_next_named_number(project.id, 'DJ', SIMPLE_TYPE) == 2
    shell.ai_elapsed_timer.stop(); shell.chat_elapsed_timer.stop(); shell.tray.hide()
    shell.main_window.allow_close = shell.mini_window.allow_close = True
