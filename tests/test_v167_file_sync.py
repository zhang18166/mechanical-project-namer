# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from pathlib import Path
from threading import Event
import hashlib
import sqlite3

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QInputDialog, QMessageBox

from mechanical_project_namer.data import Database, Repository, DataExporter
from mechanical_project_namer.file_sync import (
    READY, ExternalFilesNeedSync, category, ensure_external_numbers_registered,
    import_entries, path_key, scan_project,
)
from mechanical_project_namer.numbering.workflow import NamingWorkflow
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.solidworks.service import SolidWorksService
from mechanical_project_namer.ui.file_sync_dialog import FileSyncDialog
from mechanical_project_namer.ui.record_dialog import RecordDetailDialog


@pytest.fixture
def fixture(tmp_path):
    db = Database(tmp_path / 'sync.db')
    db.migrate()
    repo = Repository(db)
    folder = tmp_path / 'design'
    folder.mkdir()
    project = ProjectService(repo).create('目录同步测试', '', 'SYNC-24', directory=str(folder), solidworks_part_directory=str(folder), project_type='test')
    return db, repo, project, folder


def write(folder, name):
    p = folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(('test-only-' + name).encode('utf-8'))
    return p


def ready(result):
    return [e for e in result.entries if e.status in READY]


def test_model_drawing_pdf_one_record_monotonic_counter_and_repeat_scan(fixture):
    db, repo, project, folder = fixture
    files = [write(folder, 'SYNC-24-03-028 滑座' + ext) for ext in ('.SLDPRT', '.SLDDRW', '.pdf')]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    result = scan_project(repo, project.id)
    assert not result.errors and len(ready(result)) == 1 and len(ready(result)[0].files) == 3
    assert import_entries(repo, result, ready(result)) == 1
    records = repo.list_records(project.id)
    assert len(records) == 1 and records[0].filename == 'SYNC-24-03-028'
    assert records[0].creation_source == 'external_import' and records[0].file_path.endswith('.SLDPRT')
    assert len(repo.list_record_files(records[0].id)) == 3
    from mechanical_project_namer.ai import NamingMemory
    assert NamingMemory(db).build_context('滑座', project.id, '03').current_parts[0]['part_name'] == '滑座'
    assert repo.get_next_named_number(project.id, '03', '自制机加工件') == 29
    assert not ready(scan_project(repo, project.id))
    with pytest.raises(ValueError):
        import_entries(repo, result, ready(result))
    assert len(repo.list_records(project.id)) == 1
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in hashes.items())


def test_standard_name_with_hyphenated_code_nested_directory_and_extension_case(fixture):
    _, repo, project, folder = fixture
    write(folder / '03成型', 'sync-24-03-105 支架.SLDPRT')
    result = scan_project(repo, project.id)
    assert ready(result)[0].part_type == '钣金/焊接件'
    import_entries(repo, result, ready(result))
    assert repo.get_next_named_number(project.id, '03', '钣金/焊接件') == 106


@pytest.mark.parametrize('number,expected', [(0, '总成'), (1, '自制机加工件'), (99, '自制机加工件'), (100, ''), (101, '钣金/焊接件'), (199, '钣金/焊接件'), (200, ''), (201, '采购/标准件'), (299, '采购/标准件'), (300, '')])
def test_number_boundaries(number, expected):
    assert category(number) == expected


def test_import_assembly_zero_without_counter_and_other_modules_independent(fixture):
    db, repo, project, folder = fixture
    write(folder, 'SYNC-24-03-000 成型总成.SLDASM')
    write(folder, 'SYNC-24-02-007 送料座.SLDPRT')
    result = scan_project(repo, project.id)
    import_entries(repo, result, ready(result))
    with db.connect() as c:
        assert not c.execute("SELECT * FROM part_sequence_counters WHERE part_type='总成'").fetchall()
    assert repo.get_next_named_number(project.id, '02', '自制机加工件') == 8
    assert repo.get_next_named_number(project.id, '03', '自制机加工件') == 1


@pytest.mark.parametrize('names', [
    ['SYNC-24-03-028 滑座.SLDPRT', 'SYNC-24-03-028 安装座.pdf'],
    ['a/SYNC-24-03-028 滑座.SLDPRT', 'b/SYNC-24-03-028 滑座.SLDPRT'],
    ['SYNC-24-03-000 总成.SLDPRT', 'SYNC-24-03-000 总成.SLDASM'],
])
def test_duplicate_or_differently_named_files_conflict(fixture, names):
    _, repo, project, folder = fixture
    for n in names:
        write(folder, n)
    result = scan_project(repo, project.id)
    assert result.entries[0].status == '编号冲突' and not ready(result)
    assert repo.list_records(project.id) == []


def test_existing_record_can_add_pdf_without_duplicating_number(fixture):
    _, repo, project, folder = fixture
    record = repo.save_named_record(project.id, '03', '自制机加工件', '滑座')
    write(folder, 'SYNC-24-03-001 滑座.SLDPRT')
    result = scan_project(repo, project.id)
    assert ready(result)[0].status == '可关联'
    import_entries(repo, result, ready(result))
    write(folder, 'SYNC-24-03-001.pdf')
    result = scan_project(repo, project.id)
    import_entries(repo, result, ready(result))
    assert len(repo.list_records(project.id)) == 1 and len(repo.list_record_files(record.id)) == 2
    assert repo.get_next_named_number(project.id, '03', '自制机加工件') == 2


def test_deleted_record_number_remains_reserved(fixture, tmp_path):
    _, repo, project, folder = fixture
    record = repo.save_named_record(project.id, '03', '自制机加工件', '滑座')
    repo.delete_record(record.id, reason='错误记录')
    write(folder, 'SYNC-24-03-001 滑座.SLDPRT')
    assert scan_project(repo, project.id).entries[0].status == '编号冲突'


def test_unrecognized_file_can_be_registered_explicitly_without_rename(fixture):
    _, repo, project, folder = fixture
    p = write(folder, '安装板.SLDPRT')
    assert scan_project(repo, project.id).entries[0].status == '无法识别'
    overrides = {path_key(p): ('SYNC-24-03-017', '安装板')}
    result = scan_project(repo, project.id, overrides=overrides)
    import_entries(repo, result, ready(result), overrides=overrides)
    assert p.is_file() and len(list(folder.iterdir())) == 1
    assert scan_project(repo, project.id).entries[0].status == '已登记'
    assert repo.get_next_named_number(project.id, '03', '自制机加工件') == 18


def test_import_refuses_file_changed_after_preview_and_rolls_back_all(fixture):
    _, repo, project, folder = fixture
    write(folder, 'SYNC-24-03-010 滑座.SLDPRT')
    p = write(folder, 'SYNC-24-03-011 支撑座.SLDPRT')
    result = scan_project(repo, project.id)
    p.write_bytes(b'changed')
    with pytest.raises(ValueError, match='变化'):
        import_entries(repo, result, ready(result))
    assert repo.list_records(project.id) == []


def test_import_refuses_project_directory_changed_or_archived(fixture):
    _, repo, project, folder = fixture
    write(folder, 'SYNC-24-03-010 滑座.SLDPRT')
    result = scan_project(repo, project.id)
    repo.set_project_status(project.id, 'archived')
    with pytest.raises(ValueError, match='状态或目录'):
        import_entries(repo, result, ready(result))
    assert repo.list_records(project.id) == []


def test_missing_files_keep_records_numbers_and_new_location_requires_review(fixture):
    _, repo, project, folder = fixture
    p = write(folder, 'SYNC-24-03-028 滑座.SLDPRT')
    result = scan_project(repo, project.id)
    import_entries(repo, result, ready(result))
    sub = folder / 'moved'
    sub.mkdir()
    p.rename(sub / p.name)
    result = scan_project(repo, project.id)
    assert {'待核对', '文件待核对'} <= {e.status for e in result.entries}
    assert not ready(result) and len(repo.list_records(project.id)) == 1
    assert repo.get_next_named_number(project.id, '03', '自制机加工件') == 29


def test_backups_temporary_archives_and_other_project_never_auto_imported(fixture):
    _, repo, project, folder = fixture
    write(folder / '备份', 'SYNC-24-03-099 旧件.SLDPRT')
    write(folder, '~$SYNC-24-03-028 滑座.SLDPRT')
    write(folder, 'SYNC-24-03-028 滑座.zip')
    write(folder, 'OTHER-03-002 滑座.SLDPRT')
    result = scan_project(repo, project.id)
    assert len(result.entries) == 1 and result.entries[0].status == '无法识别'
    assert not ready(result)


def test_incomplete_canceled_or_over_limit_scan_cannot_import(fixture):
    _, repo, project, folder = fixture
    write(folder, 'SYNC-24-03-028 滑座.SLDPRT')
    cancel = Event()
    cancel.set()
    assert scan_project(repo, project.id, cancel=cancel).errors
    result = scan_project(repo, project.id, max_entries=0)
    assert result.errors
    with pytest.raises(ValueError):
        import_entries(repo, result, result.entries)


def test_root_reparse_point_never_followed(fixture, monkeypatch):
    from mechanical_project_namer import file_sync
    _, repo, project, folder = fixture
    monkeypatch.setattr(file_sync, 'is_reparse', lambda path: path == folder)
    with pytest.raises(ValueError, match='链接'):
        scan_project(repo, project.id)


def test_number_guard_blocks_manual_save_copy_and_sw_reservation_until_sync(fixture, tmp_path):
    db, repo, project, folder = fixture
    write(folder, 'SYNC-24-03-028 滑座.SLDPRT')
    workflow = NamingWorkflow(repo, DataExporter(repo, tmp_path / 'exports'))
    with pytest.raises(ExternalFilesNeedSync):
        repo.save_named_record(project.id, '03', '自制机加工件', '新零件')
    with pytest.raises(ExternalFilesNeedSync):
        workflow.copy_named_preview(project.id, '03', '自制机加工件', '新零件')
    service = SolidWorksService(repo, tmp_path / 'recovery')
    with pytest.raises(ExternalFilesNeedSync):
        service.reserve(project.id, '03', '自制机加工件', '新零件', str(folder))
    result = scan_project(repo, project.id)
    import_entries(repo, result, ready(result))
    reservation = service.reserve(project.id, '03', '自制机加工件', '新零件', str(folder))
    assert reservation['part_sequence'] == 29


def test_legacy_record_999_limit_failure_rolls_back_import(fixture):
    db, repo, project, folder = fixture
    with db.transaction() as c:
        c.execute("INSERT INTO drawing_records(project_id,project_name,project_code,drawing_number,filename,part_name,record_date,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (project.id, project.name, project.code, 999, 'LEGACY-999', '旧记录', 'now', 'now', 'now'))
    write(folder, 'SYNC-24-03-028 滑座.SLDPRT')
    result = scan_project(repo, project.id)
    with pytest.raises(sqlite3.IntegrityError):
        import_entries(repo, result, ready(result))
    assert len(repo.list_records(project.id)) == 1


def test_preview_closing_does_not_write_and_manual_selection_is_confirmed(fixture, qtbot, monkeypatch):
    _, repo, project, folder = fixture
    write(folder, '安装板.SLDPRT')
    dialog = FileSyncDialog(repo, project.id)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.result is not None and dialog.worker is None)
    assert repo.list_records(project.id) == []
    assert dialog.table.item(0, 4).text() == '安装板.SLDPRT'
    assert dialog.table.item(0, 5).text() == '待填写图号和零件名称'
    dialog.table.selectRow(0)
    values = iter([('SYNC-24-03-028', True), ('安装板', True)])
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: next(values))
    dialog._assign()
    qtbot.waitUntil(lambda: dialog.result is not None and dialog.worker is None)
    assert dialog.table.item(0, 0).checkState() == Qt.CheckState.Checked
    assert dialog.table.item(0, 4).text() == '安装板.SLDPRT'
    assert dialog.table.item(0, 5).text() == 'SYNC-24-03-028 安装板.SLDPRT'
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.No)
    dialog._import()
    assert repo.list_records(project.id) == []
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Yes)
    dialog._import()
    qtbot.waitUntil(lambda: dialog.worker is None)
    assert dialog.imported == 1 and len(repo.list_records(project.id)) == 1
    record = repo.list_records(project.id)[0]
    detail = RecordDetailDialog(record, associated_files=repo.list_record_files(record.id))
    qtbot.addWidget(detail)
    assert '安装板.SLDPRT' in detail.associated_files.toPlainText()
    assert (folder / '安装板.SLDPRT').is_file()
    assert not (folder / 'SYNC-24-03-028 安装板.SLDPRT').exists()


def test_preview_names_align_grouped_extensions_without_writing(fixture, qtbot):
    _, repo, project, folder = fixture
    files = [write(folder, 'sync-24-03-028 滑座' + ext) for ext in ('.SLDPRT', '.SLDDRW', '.pdf')]
    before = {p.name: p.read_bytes() for p in files}
    dialog = FileSyncDialog(repo, project.id)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.result is not None and dialog.worker is None)
    originals = dialog.table.item(0, 4).text().splitlines()
    targets = dialog.table.item(0, 5).text().splitlines()
    assert len(originals) == len(targets) == 3
    for original, target in zip(originals, targets):
        assert target == 'SYNC-24-03-028 滑座' + Path(original).suffix
    assert '登记不会修改' in dialog.table.item(0, 5).toolTip()
    dialog.reject()
    assert repo.list_records(project.id) == []
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_conflicting_file_names_do_not_show_an_approved_target(fixture, qtbot):
    _, repo, project, folder = fixture
    write(folder, 'SYNC-24-03-028 滑座.SLDPRT')
    write(folder, 'SYNC-24-03-028 另一滑座.pdf')
    dialog = FileSyncDialog(repo, project.id)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.result is not None and dialog.worker is None)
    assert dialog.table.item(0, 1).text() == '编号冲突'
    assert '滑座.SLDPRT' in dialog.table.item(0, 4).text()
    assert dialog.table.item(0, 5).text() == '待核对，暂不生成建议名称'
    assert repo.list_records(project.id) == []


def test_schema9_upgrade_keeps_data_and_creates_file_links_table(fixture):
    db, repo, project, folder = fixture
    record = repo.save_named_record(project.id, '03', '自制机加工件', '旧记录')
    with db.transaction() as c:
        c.execute('DROP TABLE record_files')
        c.execute('UPDATE schema_meta SET version=9')
    db.migrate()
    db.migrate()
    assert repo.get_record(record.id).part_name == '旧记录'
    assert repo.list_record_files(record.id) == []
    from mechanical_project_namer.data.database import SCHEMA_VERSION
    assert list((db.path.parent / 'backups/database').glob(f'*pre_schema{SCHEMA_VERSION}*.db'))


def test_import_never_decrements_counter_or_reuses_same_number_in_different_names(fixture):
    db, repo, project, folder = fixture
    with db.transaction() as c:
        c.execute('INSERT INTO part_sequence_counters VALUES (?,?,?,?,?)', (project.id, '03', '自制机加工件', 50, 'now'))
    write(folder, 'SYNC-24-03-028 滑座.SLDPRT')
    result = scan_project(repo, project.id)
    import_entries(repo, result, ready(result))
    assert repo.get_next_named_number(project.id, '03', '自制机加工件') == 50


def test_new_conflicting_file_added_after_preview_invalidates_whole_group(fixture):
    _, repo, project, folder = fixture
    write(folder, 'SYNC-24-03-028 滑座.SLDPRT')
    result = scan_project(repo, project.id)
    write(folder, 'SYNC-24-03-028 固定座.pdf')
    with pytest.raises(ValueError, match='变化'):
        import_entries(repo, result, ready(result))
    assert repo.list_records(project.id) == []


def test_sql_failure_rolls_back_records_links_and_counters_together(fixture):
    db, repo, project, folder = fixture
    write(folder, 'SYNC-24-03-028 滑座.SLDPRT')
    result = scan_project(repo, project.id)
    with db.transaction() as c:
        c.execute("CREATE TRIGGER reject_link BEFORE INSERT ON record_files BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        import_entries(repo, result, ready(result))
    assert repo.list_records(project.id) == []
    assert repo.get_next_named_number(project.id, '03', '自制机加工件') == 1
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM number_reservations').fetchone()[0] == 0
