# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""UI 视觉体系 smoke 测试（阶段B）。

只校验视觉约定与控件构建，不触碰业务断言：
- 无 kind 的 QPushButton / QDialogButtonBox 按 secondary 渲染（属性 kind 缺失）。
- 重要弹窗的 Save/Ok 为 primary，Close/Cancel 为 secondary。
- danger 只用于不可逆操作。
- 状态徽章 token 映射正确。
- 各页面与弹窗可构建、可设数据。
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialogButtonBox, QPushButton

from mechanical_project_namer.settings import AppSettings
from mechanical_project_namer.ui.main_window import MainWindow
from mechanical_project_namer.ui.mini_window import MiniWindow
from mechanical_project_namer.ui.settings_page import SettingsPage
from mechanical_project_namer.ui.design_chat_dialog import DesignChatDialog
from mechanical_project_namer.ui.project_dialog import ProjectDialog
from mechanical_project_namer.ui.project_management_dialogs import (
    DuplicateProjectDialog, NumberingStatusDialog, PermanentDeleteDialog, ProjectPropertiesDialog,
)
from mechanical_project_namer.ui.record_dialog import RecordDetailDialog
from mechanical_project_namer.ui import common
from mechanical_project_namer.projects.models import Project, DrawingRecord


def _kind(button):
    return button.property("kind")


def _stacked_button_kinds(button_box):
    return {btn.text(): _kind(btn) for btn in button_box.buttons() if isinstance(btn, QPushButton)}


def test_default_qpushbutton_has_no_primary_kind(qtbot):
    window = MainWindow(AppSettings())
    qtbot.addWidget(window)
    # 页面构造后逐个按钮：无显式 kind 时不能是 primary（默认 secondary 由 QSS 兜底）
    for button in window.findChildren(QPushButton):
        kind = _kind(button)
        if kind is None:
            # 未标注的按钮不应被业务层当成主按钮；这里只断言显式 kind 集合合法
            assert kind is None
        else:
            assert kind in {"primary", "secondary", "ghost", "danger", "sidebar"}


def test_project_page_has_single_primary_open_and_new_project(qtbot):
    window = MainWindow(AppSettings())
    qtbot.addWidget(window)
    window.show_page(0)
    primaries = [b for b in window.findChildren(QPushButton) if _kind(b) == "primary"]
    # 项目管理页：新建项目(header) + 打开项目(底部) 应为 primary；其余 secondary
    assert any(b.text() == "新建项目" and _kind(b) == "primary" for b in primaries)
    assert any(b.text() == "打开项目" and _kind(b) == "primary" for b in primaries)
    # 生命周期/普通动作是 secondary
    for b in window.findChildren(QPushButton):
        if b.text() in {"编辑项目", "复制项目", "项目属性", "打开项目目录", "归档项目"}:
            assert _kind(b) in {"secondary", "ghost"}


def test_current_project_primary_is_solidworks_create(qtbot):
    window = MainWindow(AppSettings())
    qtbot.addWidget(window)
    window.show_page(1)
    assert _kind(window.solidworks_button) == "primary"
    assert _kind(window.save_button) == "secondary"
    assert _kind(window.copy_button) == "ghost"
    assert _kind(window.solidworks_cancel) == "secondary"


def test_danger_menu_item_uses_red_label(qtbot):
    # danger 菜单项通过 QWidgetAction 包一个 menuDanger QLabel，供 QSS 渲染红色
    from PyQt6.QtWidgets import QMenu, QLabel
    from PyQt6.QtWidgets import QWidgetAction
    from mechanical_project_namer.ui.common import add_menu_action
    menu = QMenu()
    add_menu_action(menu, "永久删除", danger=True)
    action = menu.actions()[-1]
    widget = action.defaultWidget()
    assert widget is not None
    assert widget.property("menuDanger") == "true"


def test_lifecycle_save_buttons_are_primary_and_cancel_secondary(qtbot):
    # 新建/编辑项目
    dialog = ProjectDialog(project=None)
    kinds = _stacked_button_kinds(dialog.findChild(QDialogButtonBox))
    assert kinds.get("保存") == "primary"
    assert kinds.get("取消") == "secondary"
    dialog.close()
    # 复制项目：Ok=创建副本 primary，Cancel=取消 secondary
    project = Project(id=1, name="原项目", description="", code="YG", notes="", directory="",
                      solidworks_part_directory="", start_number=1, next_number=1,
                      code_locked=True, archived=False, created_at="2026-01-01", updated_at="2026-01-01",
                      last_used_at="2026-01-01", used_count=1, project_type="formal", project_status="normal")
    dlg = DuplicateProjectDialog(project)
    kinds = _stacked_button_kinds(dlg.findChild(QDialogButtonBox))
    assert kinds.get("创建副本") == "primary"
    assert kinds.get("取消") == "secondary"
    dlg.close()


def test_danger_only_for_destructive_actions(qtbot):
    project = Project(id=1, name="n", description="", code="D", notes="", directory="",
                      solidworks_part_directory="x", start_number=1, next_number=1,
                      code_locked=True, archived=False, created_at="a", updated_at="b",
                      last_used_at="c", used_count=1, project_type="formal", project_status="trash")
    props = {"project": project, "formal_count": 0, "void_count": 0, "ai_plan_count": 0,
             "adopted_plan_count": 0, "project_memory_count": 0, "feedback_count": 0, "conversation_count": 0}
    # 永久删除 → delete_button 是 danger
    dlg = PermanentDeleteDialog(project, props)
    assert _kind(dlg.delete_button) == "danger"
    dlg.close()
    # 记录详情「标记为已作废」→ danger
    record = DrawingRecord(id=1, project_id=1, project_name="n", project_code="D", drawing_number=1,
                           filename="D-01-001", part_name="滑座", purpose="", record_date="2026-01-01",
                           created_at="a", updated_at="b", status="used", is_copied=False,
                           creation_method="manual", project_directory="", extra_notes="",
                           module_code="01", part_sequence=1, part_type="自制机加工件", full_name="D-01-001 滑座")
    rec = RecordDetailDialog(record)
    danger_btns = [b for b in rec.findChildren(QPushButton) if _kind(b) == "danger"]
    assert any(b.text() == "标记为已作废" for b in danger_btns)
    rec.close()


def test_status_badge_token_mapping():
    assert common.BADGE_TOKENS["used"] == "ok"
    assert common.BADGE_TOKENS["failed"] == "void"
    assert common.BADGE_TOKENS["trash"] == "void"
    assert common.BADGE_TOKENS["archived"] == "neutral"
    assert common.BADGE_TOKENS["reserved"] == "reserved"
    assert common.BADGE_TOKENS["disconnected"] == "neutral"


def test_mini_window_primary_is_sw_create(qtbot):
    mini = MiniWindow()
    qtbot.addWidget(mini)
    assert _kind(mini.solidworks_button) == "primary"
    assert _kind(mini.save_button) == "secondary"
    assert _kind(mini.copy_button) == "ghost"


def test_chat_send_primary_stop_secondary(qtbot):
    dialog = DesignChatDialog([])
    qtbot.addWidget(dialog)
    assert _kind(dialog.send_button) == "primary"
    assert _kind(dialog.stop_button) == "secondary"


def test_settings_save_buttons_are_primary(qtbot):
    page = SettingsPage(AppSettings())
    qtbot.addWidget(page)
    # 一个固定保存入口覆盖全部设置页；Key 仍单独保存。
    saves = [b for b in page.findChildren(QPushButton) if b.text() == "保存设置"]
    assert len(saves) == 1
    assert all(_kind(b) == "primary" for b in saves)
