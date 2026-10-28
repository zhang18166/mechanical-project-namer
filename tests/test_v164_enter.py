# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""IME commits must not turn the following send key into a newline."""
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QInputMethodEvent, QGuiApplication, QKeyEvent, QFocusEvent
from PyQt6.QtCore import QEvent
from PyQt6.QtTest import QTest, QSignalSpy

from mechanical_project_namer.ui.design_chat_dialog import DesignChatDialog


@pytest.fixture
def dialog(qtbot):
    widget = DesignChatDialog()
    qtbot.addWidget(widget)
    widget.show()
    widget.input.setFocus()
    return widget


def commit(editor, text):
    event = QInputMethodEvent()
    event.setCommitString(text)
    QGuiApplication.sendEvent(editor, event)


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_committed_text_sends_on_first_enter_without_delay(dialog, key):
    spy = QSignalSpy(dialog.send_requested)
    commit(dialog.input, "6210轴承尺寸")
    QTest.keyClick(dialog.input, key)
    assert len(spy) == 1 and spy[0][0] == "6210轴承尺寸"
    assert dialog.input.toPlainText() == "6210轴承尺寸"
    dialog.accept_submission(spy[0][0])
    assert dialog.input.toPlainText() == ""


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_candidate_confirmation_does_not_send_or_add_newline_and_next_enter_sends(dialog, key):
    spy = QSignalSpy(dialog.send_requested)
    QGuiApplication.sendEvent(dialog.input, QInputMethodEvent("chicun", []))
    commit(dialog.input, "尺寸")
    QTest.keyClick(dialog.input, key)  # Candidate confirmation key, including its release.
    assert len(spy) == 0 and dialog.input.toPlainText() == "尺寸"
    QTest.keyClick(dialog.input, key)  # User's next deliberate send; no 250ms wait.
    assert len(spy) == 1 and spy[0][0] == "尺寸"


def test_space_confirmation_does_not_swallow_following_enter(dialog):
    spy = QSignalSpy(dialog.send_requested)
    QGuiApplication.sendEvent(dialog.input, QInputMethodEvent("chicun", []))
    commit(dialog.input, "尺寸")
    QTest.keyRelease(dialog.input, Qt.Key.Key_Space)
    QTest.keyClick(dialog.input, Qt.Key.Key_Return)
    assert len(spy) == 1 and spy[0][0] == "尺寸"


def test_enter_during_preedit_does_not_insert_blank_line(dialog):
    spy = QSignalSpy(dialog.send_requested)
    dialog.input.setPlainText("6210")
    QGuiApplication.sendEvent(dialog.input, QInputMethodEvent("zhoucheng", []))
    QTest.keyClick(dialog.input, Qt.Key.Key_Return)
    assert not spy and dialog.input.toPlainText() == "6210"


def test_holding_confirmation_enter_does_not_submit_a_repeat(dialog):
    spy = QSignalSpy(dialog.send_requested)
    commit(dialog.input, "测试文字")
    repeat = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "\r", True, 1)
    QGuiApplication.sendEvent(dialog.input, repeat)
    assert not spy and dialog.input.toPlainText() == "测试文字"


def test_focus_change_clears_stale_composition_guard(dialog):
    spy = QSignalSpy(dialog.send_requested)
    QGuiApplication.sendEvent(dialog.input, QInputMethodEvent("chicun", []))
    commit(dialog.input, "尺寸")
    QGuiApplication.sendEvent(dialog.input, QFocusEvent(QEvent.Type.FocusOut))
    QGuiApplication.sendEvent(dialog.input, QFocusEvent(QEvent.Type.FocusIn))
    QTest.keyClick(dialog.input, Qt.Key.Key_Return)
    assert len(spy) == 1 and spy[0][0] == "尺寸"
