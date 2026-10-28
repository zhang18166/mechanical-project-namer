# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QGuiApplication, QInputMethodEvent
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QMainWindow

from mechanical_project_namer.data import Database, Repository
from mechanical_project_namer.settings import AppSettings
from mechanical_project_namer.ui.design_chat_dialog import DesignChatDialog
from mechanical_project_namer.ui.main_window import MainWindow
from mechanical_project_namer.ui.shell import ApplicationShell


MODULES = [
    {"module_code": code, "module_name": name, "enabled": 1}
    for code, name in (
        ("00", "整机总装"), ("01", "机架模块"), ("02", "送料模块"),
        ("03", "压弧/成型模块"), ("04", "锯切模块"), ("05", "导料/托料模块"),
        ("07", "电气模块"), ("08", "防护模块"),
    )
]


@pytest.fixture
def main_window(qtbot):
    window = MainWindow(AppSettings())
    qtbot.addWidget(window)
    window._rebuild_module_buttons(MODULES, "03")
    return window


def _settle(window):
    window.show()
    QTest.qWait(20)
    QGuiApplication.processEvents()


@pytest.mark.parametrize("size", [(1280, 720), (1366, 768), (1920, 1080)])
def test_home_responsive_sizes_have_vertical_only_page_scroll(main_window, size):
    # AI is folded by default; opening it must keep every existing control usable.
    assert not main_window.ai_content.isVisible()
    main_window.ai_toggle.setChecked(True)
    main_window.resize(*size)
    _settle(main_window)
    assert main_window.content_scroll.horizontalScrollBar().maximum() == 0
    if size[1] <= 768:
        assert main_window.content_scroll.verticalScrollBar().maximum() > 0
    for widget in (
        main_window.ai_mode, main_window.ai_description, main_window.ai_button,
        main_window.plan_history_button, main_window.part_name, main_window.extra_notes,
        main_window.solidworks_button, main_window.current_search,
    ):
        assert widget.isVisible() and widget.width() > 0


def test_module_flow_wraps_and_preserves_full_labels(main_window):
    main_window.module_flow.setGeometry(QRect(0, 0, 300, 500))
    rows = {button.y() for button in main_window.module_buttons}
    assert len(rows) >= 3
    assert [button.text() for button in main_window.module_buttons] == [
        f"{item['module_code']} {item['module_name']}" for item in MODULES
    ]
    assert next(button for button in main_window.module_buttons if button.text().startswith("03 ")).isChecked()


def test_module_flow_reflows_after_add_and_rename(main_window):
    main_window.module_flow.setGeometry(QRect(0, 0, 360, 500))
    initial_height = main_window.module_flow.heightForWidth(360)
    changed = MODULES + [{"module_code": "09", "module_name": "超长的新增加工模块", "enabled": 1}]
    main_window._rebuild_module_buttons(changed, "09")
    main_window.module_flow.setGeometry(QRect(0, 0, 360, 500))
    added_height = main_window.module_flow.heightForWidth(360)
    renamed = [dict(item) for item in changed]
    renamed[0]["module_name"] = "整机总装与联合调试模块"
    main_window._rebuild_module_buttons(renamed, "00")
    main_window.module_flow.setGeometry(QRect(0, 0, 360, 500))
    assert added_height >= initial_height
    assert main_window.module_flow.heightForWidth(360) >= added_height
    assert main_window.module_buttons[0].text().endswith("整机总装与联合调试模块")


@pytest.mark.parametrize(
    ("layout_name", "width"),
    [("ai_controls_flow", 300), ("record_toolbar_flow", 300), ("part_fields_flow", 360)],
)
def test_responsive_control_groups_wrap(main_window, layout_name, width):
    flow = getattr(main_window, layout_name)
    flow.setGeometry(QRect(0, 0, width, 500))
    items = [flow.itemAt(index).widget() for index in range(flow.count())]
    assert len({widget.y() for widget in items}) >= 2
    assert all(widget.x() >= 0 and widget.geometry().right() < width for widget in items)


def test_record_table_uses_internal_horizontal_scroll_and_primary_column_order(main_window):
    main_window.resize(1280, 720)
    _settle(main_window)
    assert main_window.current_records.horizontalScrollBar().maximum() == 0
    header = main_window.current_records.horizontalHeader()
    assert [header.logicalIndex(position) for position in range(6)] == [2, 3, 4, 8, 9, 1]
    for column in range(11):
        main_window.current_records.setColumnHidden(column, False)
    QGuiApplication.processEvents()
    assert main_window.current_records.horizontalScrollBar().maximum() > 0
    assert main_window.content_scroll.horizontalScrollBar().maximum() == 0


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
def test_minimum_window_fits_1280_by_720_physical_viewport(main_window, scale):
    assert main_window.minimumWidth() * scale <= 1280
    assert main_window.minimumHeight() * scale <= 720


def test_repeated_ai_navigation_click_always_requests_open(main_window):
    _settle(main_window)
    spy = QSignalSpy(main_window.design_chat_requested)
    item = main_window.navigation.item(4)
    point = main_window.navigation.visualItemRect(item).center()
    for _ in range(10):
        QTest.mouseClick(main_window.navigation.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert len(spy) == 10


@pytest.fixture
def chat_dialog(qtbot):
    dialog = DesignChatDialog([])
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.input.setFocus()
    return dialog


def test_chat_enter_sends_and_clears_only_after_accept(chat_dialog):
    received = []
    chat_dialog.send_requested.connect(lambda content, _options: received.append(content))
    chat_dialog.input.setPlainText("第一行\n第二行")
    QTest.keyClick(chat_dialog.input, Qt.Key.Key_Return)
    assert received == ["第一行\n第二行"]
    assert chat_dialog.input.toPlainText() == "第一行\n第二行"
    chat_dialog.accept_submission(received[0])
    assert chat_dialog.input.toPlainText() == ""


@pytest.mark.parametrize("modifier", [Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier])
def test_chat_modified_enter_adds_newline_without_send(chat_dialog, modifier):
    spy = QSignalSpy(chat_dialog.send_requested)
    chat_dialog.input.setPlainText("第一行")
    chat_dialog.input.moveCursor(chat_dialog.input.textCursor().MoveOperation.End)
    QTest.keyClick(chat_dialog.input, Qt.Key.Key_Return, modifier)
    chat_dialog.input.insertPlainText("第二行")
    assert chat_dialog.input.toPlainText() == "第一行\n第二行"
    assert len(spy) == 0


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_chat_empty_enter_does_not_send(chat_dialog, text):
    spy = QSignalSpy(chat_dialog.send_requested)
    chat_dialog.input.setPlainText(text)
    QTest.keyClick(chat_dialog.input, Qt.Key.Key_Return)
    assert len(spy) == 0


def test_chat_enter_is_disabled_while_busy(chat_dialog):
    spy = QSignalSpy(chat_dialog.send_requested)
    chat_dialog.input.setPlainText("不能重复提交")
    chat_dialog.set_busy(True)
    QTest.keyClick(chat_dialog.input, Qt.Key.Key_Return)
    assert len(spy) == 0 and chat_dialog.input.toPlainText() == "不能重复提交"


def test_chat_ime_composition_enter_does_not_send(chat_dialog):
    spy = QSignalSpy(chat_dialog.send_requested)
    preedit = QInputMethodEvent("jixie", [])
    QGuiApplication.sendEvent(chat_dialog.input, preedit)
    QTest.keyClick(chat_dialog.input, Qt.Key.Key_Return)
    assert len(spy) == 0
    commit = QInputMethodEvent()
    commit.setCommitString("机械")
    QGuiApplication.sendEvent(chat_dialog.input, commit)
    QTest.keyClick(chat_dialog.input, Qt.Key.Key_Return)
    assert len(spy) == 0


def test_chat_close_hides_and_preserves_busy_result_and_input(chat_dialog):
    chat_dialog.input.setPlainText("尚未发送的内容")
    chat_dialog.set_busy(True)
    chat_dialog.close()
    assert not chat_dialog.isVisible()
    chat_dialog.append_message("assistant", "后台生成完成")
    chat_dialog.showNormal()
    chat_dialog.show()
    assert "后台生成完成" in chat_dialog.transcript.toPlainText()
    assert chat_dialog.input.toPlainText() == "尚未发送的内容"
    assert not chat_dialog.send_button.isEnabled()


@pytest.fixture
def shell_for_chat(qtbot, tmp_path):
    database = Database(tmp_path / "chat-lifecycle.db")
    database.migrate()
    shell = object.__new__(ApplicationShell)
    shell.repository = Repository(database)
    shell.current_project_id = None
    shell.current_module_code = "03"
    shell.chat_conversation_id = None
    shell.design_chat = None
    shell.settings = SimpleNamespace(
        available_models=["deepseek-v4-flash"], model_name="deepseek-v4-flash"
    )
    shell.main_window = QMainWindow()
    qtbot.addWidget(shell.main_window)
    shell.main_window.resize(900, 650)
    shell.main_window.show()
    return shell


def test_chat_window_is_single_instance_across_ten_close_open_cycles(shell_for_chat, qtbot):
    shell = shell_for_chat
    identities = []
    for _ in range(10):
        shell._open_design_chat()
        identities.append(id(shell.design_chat))
        assert shell.design_chat.isVisible()
        shell.design_chat.close()
        assert not shell.design_chat.isVisible()
    assert len(set(identities)) == 1
    qtbot.addWidget(shell.design_chat)


def test_chat_window_restores_from_minimized_and_offscreen(shell_for_chat, qtbot):
    shell = shell_for_chat
    shell._open_design_chat()
    dialog = shell.design_chat
    dialog.showMinimized()
    shell._open_design_chat()
    assert not dialog.isMinimized()
    dialog.move(QPoint(100000, 100000))
    shell._open_design_chat()
    assert any(screen.availableGeometry().intersects(dialog.frameGeometry()) for screen in QGuiApplication.screens())
    qtbot.addWidget(dialog)
