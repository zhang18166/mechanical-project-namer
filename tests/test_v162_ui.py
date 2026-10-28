# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""Behavior checks for compact layouts without changing business data."""
from dataclasses import replace

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QMenu, QScrollArea

from mechanical_project_namer.data import Database, Repository
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.settings import AppSettings
from mechanical_project_namer.style import APP_STYLE
from mechanical_project_namer.ui.main_window import MainWindow
from mechanical_project_namer.ui.mini_window import MiniWindow
from mechanical_project_namer.ui.record_dialog import RecordDetailDialog
from mechanical_project_namer.ui.settings_page import SettingsPage
from mechanical_project_namer.ui.shell import ApplicationShell


@pytest.fixture
def project_and_record(tmp_path):
    database = Database(tmp_path / "data.db")
    database.migrate()
    repository = Repository(database)
    project = ProjectService(repository).create("设备", "", "DEV")
    record = repository.save_named_record(project.id, "03", "自制机加工件", "轴")
    return project, record


def test_sw_connection_is_consistent_and_survives_project_refresh(qtbot, project_and_record):
    project, record = project_and_record
    shell = ApplicationShell.__new__(ApplicationShell)
    shell.main_window = MainWindow(AppSettings())
    shell.mini_window = MiniWindow()
    qtbot.addWidget(shell.main_window)
    qtbot.addWidget(shell.mini_window)
    modules = [{"module_code": "03", "module_name": "成型模块", "enabled": 1}]
    shell._set_solidworks_status("已连接 2026")
    for configured in (True, False):
        selected = replace(project, solidworks_part_directory="D:/models" if configured else "")
        shell.main_window.set_current_project(selected, [record], modules, "03")
        shell.mini_window.set_project(selected, [record], modules, "03")
        assert shell.main_window.solidworks_status.text() == shell.mini_window.solidworks_status.text() == "SW：已连接 2026"
        assert shell.main_window.settings_page.sw_status.text() == "已连接 2026"
        assert shell.mini_window.directory_status.text() == ("保存目录：已设置" if configured else "保存目录：未设置")
    shell.mini_window.set_project(None, [], [])
    assert shell.mini_window.directory_status.text() == "保存目录：未设置"


@pytest.mark.parametrize("kind,status", [("settings_probe", "正在检测"), ("create", "正在创建")])
def test_sw_progress_and_busy_do_not_disagree(qtbot, kind, status):
    shell = ApplicationShell.__new__(ApplicationShell)
    shell.main_window = MainWindow(AppSettings())
    shell.mini_window = MiniWindow()
    qtbot.addWidget(shell.main_window)
    qtbot.addWidget(shell.mini_window)
    shell.solidworks_pending_kind = kind
    shell._on_solidworks_progress("worker", "启动辅助进程")
    shell._set_solidworks_busy(True)
    assert shell.main_window.solidworks_status.text() == shell.mini_window.solidworks_status.text() == f"SW：{status}"
    assert shell.main_window.settings_page.sw_status.text() == status
    shell._set_solidworks_busy(False)
    assert shell.main_window.settings_page.sw_status.text() == status


def test_folded_settings_preserve_values_and_single_save_covers_all_tabs(qtbot):
    settings = AppSettings(model_name="my-custom-model", available_models=["another-model"], connect_timeout=19,
                           request_timeout=71, generation_timeout=420, reasoning_level="max",
                           solidworks_fallback_template="D:/templates/custom.prtdot", hotkey="Ctrl+Alt+M")
    page = SettingsPage(settings)
    qtbot.addWidget(page)
    initial = page.values()
    assert initial["model_name"] == "my-custom-model"
    assert "my-custom-model" in page.model_source.text()
    assert not page.ai_advanced_toggle.isChecked()
    spy = QSignalSpy(page.save_requested)
    page.resize(900, 650)
    page.show()
    for tab in range(page.tabs.count()):
        page.tabs.setCurrentIndex(tab)
        page.ai_advanced_toggle.setChecked(True)
        page.ai_advanced_toggle.setChecked(False)
        assert page.values() == initial
        assert page.save_button.isVisible()
        page.save_button.click()
        assert spy[-1][0] == initial
    assert len(spy) == 4
    assert page.values()["connect_timeout"] == 19
    assert page.values()["solidworks_fallback_template"] == settings.solidworks_fallback_template


def test_diagnostics_auto_reveal_and_remain_cancelable(qtbot):
    page = SettingsPage(AppSettings())
    qtbot.addWidget(page)
    page.show()
    spy = QSignalSpy(page.ai_cancel_requested)
    page.set_ai_busy(True, "完整测试")
    assert page.ai_advanced_toggle.isChecked()
    assert page.ai_cancel.isVisible()
    page.ai_cancel.click()
    assert len(spy) == 1
    page.set_ai_busy(False)
    assert not page.ai_cancel.isVisible()
    assert all(button.isEnabled() for button in page.ai_action_buttons)


def test_header_menu_reveals_all_fields_without_losing_identity_or_selection(qtbot, project_and_record, monkeypatch):
    _, record = project_and_record
    window = MainWindow(AppSettings())
    qtbot.addWidget(window)
    table = window.history_records
    window.set_record_rows(table, [record])
    table.selectRow(0)
    def operate(menu, _position):
        actions = {action.text(): action for action in menu.actions()}
        assert not actions["完整名称"].isEnabled()
        actions["额外备注"].setChecked(True)
        actions["创建时间"].setChecked(True)
    monkeypatch.setattr(QMenu, "exec", operate)
    window._record_columns_menu(table, QPoint(10, 10))
    assert not table.isColumnHidden(7)
    assert not table.isColumnHidden(10)
    assert table.item(0, 0).data(Qt.ItemDataRole.UserRole) == record.id
    assert table.selectionModel().selectedRows()[0].row() == 0
    spy = QSignalSpy(window.record_open_requested)
    window._open_record(table)
    assert spy[0][0] == record.id
    assert table.columnCount() == 11


def test_history_enter_applies_filter_and_double_click_copies_name(qtbot, project_and_record):
    _, record = project_and_record
    window = MainWindow(AppSettings())
    qtbot.addWidget(window)
    window.show_page(2)
    window.show()
    spy = QSignalSpy(window.record_search_requested)
    window.history_search.setText("轴")
    QTest.keyClick(window.history_search, Qt.Key.Key_Return)
    assert spy[-1][0] == "轴" and spy[-1][3] is False
    window.set_record_rows(window.history_records, [record])
    window._record_double_click(window.history_records, window.history_records.model().index(0, 2))
    assert window.history_records.rowCount() == 1
    assert window.history_records.item(0, 2).text() == record.display_name
    assert window.history_records.item(0, 0).data(Qt.ItemDataRole.UserRole) == record.id


@pytest.mark.parametrize("exists", [True, False])
def test_file_check_is_explicit_and_does_not_change_record(qtbot, tmp_path, project_and_record, exists):
    _, record = project_and_record
    path = tmp_path / "轴.SLDPRT"
    if exists:
        path.write_bytes(b"design-data")
    linked = replace(record, file_path=str(path))
    dialog = RecordDetailDialog(linked)
    qtbot.addWidget(dialog)
    assert dialog.file_state.text() == "未检查"
    before = dialog.values()
    dialog.check_file_button.click()
    assert (dialog.file_state.text() == "文件存在") is exists
    assert dialog.values() == before and dialog.record == linked
    if exists:
        assert path.read_bytes() == b"design-data"
    dialog.resize(640, 480)
    dialog.show()
    assert dialog.height() <= 480
    assert dialog.findChild(QScrollArea).horizontalScrollBar().maximum() == 0


def test_unlinked_record_is_not_reported_as_deleted(qtbot, project_and_record):
    _, record = project_and_record
    dialog = RecordDetailDialog(record)
    qtbot.addWidget(dialog)
    assert dialog.file_state.text() == "未关联实际文件"
    assert not dialog.check_file_button.isEnabled()


def test_home_recent_rows_fit_first_screen_and_module_actions_remain(qtbot, project_and_record):
    project, record = project_and_record
    window = MainWindow(AppSettings())
    qtbot.addWidget(window)
    window.setStyleSheet(APP_STYLE)
    modules = [{"module_code": f"{index:02d}", "module_name": "测试模块", "enabled": 1} for index in range(11)]
    window.set_current_project(project, [record], modules, "03")
    window.resize(1280, 720)
    window.show()
    qtbot.wait(20)
    top = window.current_records.mapTo(window.content_scroll.viewport(), QPoint(0, 0)).y()
    assert top + window.current_records.horizontalHeader().height() + 35 < window.content_scroll.viewport().height()
    create_spy = QSignalSpy(window.module_create_requested)
    manage_spy = QSignalSpy(window.module_manage_requested)
    menu = next(menu for menu in window.current_page.findChildren(QMenu) if any(a.text() == "新增模块" for a in menu.actions()))
    menu.actions()[0].trigger()
    menu.actions()[1].trigger()
    assert len(create_spy) == 1 and manage_spy[0] == ["", "manage"]


def test_mini_fits_small_viewport_with_long_project_name(qtbot, project_and_record):
    project, record = project_and_record
    mini = MiniWindow()
    qtbot.addWidget(mini)
    mini.setStyleSheet(APP_STYLE)
    project = replace(project, name="非常长的机械设备项目名称" * 5)
    mini.set_project(project, [record], [{"module_code": "03", "module_name": "成型模块", "enabled": 1}], "03")
    mini.resize(430, 480)
    mini.show()
    qtbot.wait(20)
    assert mini.width() == 430 and mini.height() <= 480
    assert mini.save_button.isVisible() and mini.solidworks_button.isVisible()
    assert mini.project_name.toolTip() == project.name
    assert mini.recent.height() >= 60
