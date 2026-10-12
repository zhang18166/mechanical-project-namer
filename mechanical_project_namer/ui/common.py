# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from PyQt6.QtCore import QByteArray, Qt, QRect, QSize, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialogButtonBox, QHeaderView, QLabel, QMenu, QMessageBox,
    QPushButton, QTableWidget, QLayout, QSizePolicy, QWidgetAction,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalPathResult:
    ok: bool
    code: str
    path: Path | None = None
    expected_type: str = "any"
    error: str = ""


def open_local_path(
    path: str | os.PathLike[str] | None,
    expected_type: Literal["any", "file", "directory"] = "any",
    *,
    opener: Callable[[QUrl], bool] | None = None,
) -> LocalPathResult:
    """Validate and open a local path without leaking Qt exceptions to the UI."""
    if expected_type not in {"any", "file", "directory"}:
        raise ValueError(f"Unsupported local path type: {expected_type}")
    if path is None:
        return LocalPathResult(False, "empty", expected_type=expected_type)
    try:
        raw = os.fspath(path)
    except TypeError as error:
        return LocalPathResult(False, "invalid_type", expected_type=expected_type, error=str(error))
    if not isinstance(raw, str):
        return LocalPathResult(False, "invalid_type", expected_type=expected_type)
    cleaned = os.path.expandvars(raw.strip())
    if not cleaned:
        return LocalPathResult(False, "empty", expected_type=expected_type)
    target = Path(cleaned).expanduser()
    if not target.exists():
        return LocalPathResult(False, "missing", target, expected_type)
    if expected_type == "file" and not target.is_file():
        return LocalPathResult(False, "wrong_type", target, expected_type)
    if expected_type == "directory" and not target.is_dir():
        return LocalPathResult(False, "wrong_type", target, expected_type)
    qt_opener = opener or QDesktopServices.openUrl
    try:
        opened = bool(qt_opener(QUrl.fromLocalFile(str(target))))
    except Exception as error:
        LOGGER.exception("打开本地路径时发生异常：%s", target)
        return LocalPathResult(False, "open_error", target, expected_type, str(error))
    if not opened:
        LOGGER.error("系统拒绝打开本地路径：%s", target)
        return LocalPathResult(False, "open_failed", target, expected_type)
    return LocalPathResult(True, "opened", target, expected_type)


def show_local_path_error(parent, result: LocalPathResult, target_name: str = "目标路径") -> None:
    if result.code == "empty":
        QMessageBox.information(parent, f"未设置{target_name}", f"当前尚未设置{target_name}。")
        return
    path_text = str(result.path or "")
    if result.code == "missing":
        QMessageBox.warning(parent, f"{target_name}不存在", f"{target_name}不存在。\n\n路径：\n{path_text}")
        return
    if result.code == "wrong_type":
        expected = "文件" if result.expected_type == "file" else "目录"
        QMessageBox.warning(parent, "路径类型不正确", f"目标不是预期的{expected}。\n\n路径：\n{path_text}")
        return
    if result.code == "invalid_type":
        QMessageBox.warning(parent, "路径格式不正确", f"{target_name}的路径格式无效。")
        return
    QMessageBox.warning(
        parent,
        "无法打开目标路径",
        f"无法打开目标路径。\n\n路径：\n{path_text}\n\n可能原因：\n"
        "1. 路径不存在\n2. 当前账户无权限\n3. Windows文件关联异常\n4. 目标文件被删除",
    )


class FlowLayout(QLayout):
    """A wrapping layout that gives spare row width to expanding controls."""
    def __init__(self, parent=None, margin=0, spacing=8):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect, test_only):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        lines = []
        line, line_width = [], 0
        for item in self._items:
            hint = item.sizeHint()
            needed = hint.width() + (self.spacing() if line else 0)
            if line and line_width + needed > max(0, area.width()):
                lines.append(line)
                line, line_width = [], 0
                needed = hint.width()
            line.append((item, hint))
            line_width += needed
        if line:
            lines.append(line)

        y = area.y()
        for line in lines:
            natural_width = sum(hint.width() for _item, hint in line)
            natural_width += self.spacing() * max(0, len(line) - 1)
            expanding = [
                item for item, _hint in line
                if item.widget() is not None
                and item.widget().sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
            ]
            spare = max(0, area.width() - natural_width)
            extra, remainder = divmod(spare, len(expanding)) if expanding else (0, 0)
            x = area.x()
            line_height = max((hint.height() for _item, hint in line), default=0)
            for item, hint in line:
                width = hint.width()
                if item in expanding:
                    width += extra + (1 if remainder > 0 else 0)
                    remainder = max(0, remainder - 1)
                if len(line) == 1:
                    width = min(width, max(0, area.width()))
                if not test_only:
                    item.setGeometry(QRect(x, y, width, hint.height()))
                x += width + self.spacing()
            y += line_height + self.spacing()
        if lines:
            y -= self.spacing()
        return y - rect.y() + margins.bottom()


STATUS_NAMES = {"used": "已使用", "void": "已作废", "reserved": "已预留"}

# 归一化的状态徽章 token 映射：已使用/成功/正常/已连接 -> ok；已预留/等待/未设置 -> reserved；
# 已作废/失败/回收站 -> void；已归档/未连接/模板异常 -> neutral / warning。
BADGE_TOKENS = {
    "used": "ok", "success": "ok", "normal": "ok", "connected": "ok",
    "reserved": "reserved", "pending": "reserved", "waiting": "reserved", "unset": "reserved",
    "void": "void", "failed": "void", "trash": "void",
    "archived": "neutral", "disconnected": "neutral", "empty": "neutral",
    "warning": "reserved",
}

BADGE_LABELS = {
    "normal": "正常", "archived": "已归档", "trash": "回收站",
    "formal": "正式项目", "test": "测试项目",
    "success": "成功", "partial": "部分成功", "failed": "失败",
    "connected": "已连接", "disconnected": "未连接",
    "used": "已使用", "reserved": "已预留", "void": "已作废",
}


def status_badge(code: str, label: str | None = None) -> QLabel:
    """Render a colored pill badge. `code` selects the token; `label` overrides text."""
    text = label or BADGE_LABELS.get(code, STATUS_NAMES.get(code, code))
    token = BADGE_TOKENS.get(code, "neutral")
    label_widget = QLabel(str(text))
    label_widget.setProperty("statusBadge", token)
    label_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label_widget


def empty_state(text: str, sub: str = "") -> QLabel:
    """Simple empty-state label pair (nothing is rendered when both empty)."""
    label = QLabel(text)
    label.setObjectName("emptyState")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setWordWrap(True)
    if sub:
        label.setToolTip(sub)
    return label


def make_field_label(text: str) -> QLabel:
    """QFormLayout field label styled consistently (weight 500, muted)."""
    label = QLabel(text)
    label.setProperty("fieldLabel", "true")
    return label


def configure_table(table: QTableWidget) -> None:
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setHighlightSections(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)


def open_directory(parent, directory: str) -> LocalPathResult:
    result = open_local_path(directory, "directory")
    if not result.ok:
        show_local_path_error(parent, result, "项目目录")
    return result


def restore_visible_geometry(window, encoded: str, default_size: tuple[int, int]) -> None:
    restored = False
    if encoded:
        restored = window.restoreGeometry(QByteArray.fromBase64(encoded.encode("ascii")))
    if not restored:
        window.resize(*default_size)
    ensure_window_visible(window)


def ensure_window_visible(window, reference=None) -> None:
    """Move a fully off-screen top-level window onto the reference screen."""
    frame = window.frameGeometry()
    screens = QGuiApplication.screens()
    if any(screen.availableGeometry().intersects(frame) for screen in screens):
        return
    reference_center = reference.frameGeometry().center() if reference is not None else None
    screen = QGuiApplication.screenAt(reference_center) if reference_center is not None else None
    screen = screen or QGuiApplication.primaryScreen()
    if screen is None:
        return
    frame.moveCenter(screen.availableGeometry().center())
    window.move(frame.topLeft())


def geometry_to_text(window) -> str:
    return bytes(window.saveGeometry().toBase64()).decode("ascii")


def set_on_top(window, enabled: bool) -> None:
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
    if window.isVisible():
        window.show()


def set_button_kind(button: QPushButton, kind: str = "secondary") -> QPushButton:
    """Tag a QPushButton with a visual kind (primary/secondary/ghost/danger)."""
    button.setProperty("kind", kind)
    button.style().unpolish(button)
    button.style().polish(button)
    return button


def style_button_box(button_box: QDialogButtonBox, ok_is_primary: bool = True) -> None:
    """Assign primary/secondary roles to a QDialogButtonBox's standard buttons."""
    ok = button_box.button(QDialogButtonBox.StandardButton.Ok)
    save = button_box.button(QDialogButtonBox.StandardButton.Save)
    cancel = button_box.button(QDialogButtonBox.StandardButton.Cancel)
    close = button_box.button(QDialogButtonBox.StandardButton.Close)
    apply_ = button_box.button(QDialogButtonBox.StandardButton.Apply)
    yes = button_box.button(QDialogButtonBox.StandardButton.Yes)
    no = button_box.button(QDialogButtonBox.StandardButton.No)
    primary_kind = "primary" if ok_is_primary else "secondary"
    for button in (ok, save, yes):
        if button is not None:
            set_button_kind(button, primary_kind)
    for button in (cancel, close, no):
        if button is not None:
            set_button_kind(button, "secondary")
    if apply_ is not None:
        set_button_kind(apply_, "secondary")


def add_menu_action(menu: QMenu, text: str, *, slot=None, danger: bool = False,
                    separator_before: bool = False) -> None:
    """Add a QMenu action, optionally in a danger-styled QWidgetAction.

    Danger items are wrapped so QSS `QLabel[menuDanger=true]` renders red text.
    """
    if separator_before:
        menu.addSeparator()
    if danger:
        item_label = QLabel(text)
        item_label.setProperty("menuDanger", "true")
        action = QWidgetAction(menu)
        action.setDefaultWidget(item_label)
        menu.addAction(action)
    else:
        action = menu.addAction(text)
    if slot is not None:
        action.triggered.connect(slot)
    return action

