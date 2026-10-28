# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""Application QSS theme.

「工业蓝灰 · Workbench」— 蓝灰主色 + 近白内容面 + 深青侧栏。

设计规范：docs/ui-visual-design-spec.md
本轮(阶段A)只改本文件 + 当前项目页 + 通用视觉组件（common.py）。

按钮语义约定（重要）：
- 无 kind 的 QPushButton 一律按 secondary 渲染（次按钮）。
- 只有显式  kind="primary"  才是主按钮。
- 其余 kind: secondary / ghost / danger / sidebar。

注意：本程序不做任何 devicePixelRatio 手动乘算，控件尺寸全部用 Qt 逻辑像素，
DPI 适配交给 Qt6 布局与实机缩放，验收靠 100%/125%/150% 截图。
"""

# ============ 4.1 品牌 / 主色 ============
# brand-primary:  #176d86   brand-primary-hover:   #105c73
# brand-pressed:  #0c4b5e   brand-disabled:        #aab8be
# accent:         #0c5f79   focus-ring:            #8fc6d4

# ============ 4.2 中性色（内容面） ============
# surface-window: #eef1f3   surface-panel:  #ffffff
# surface-subtle: #f6f8f9   surface-alt-row:#edf2f4
# border-base:    #d3dce0   border-strong: #b7c4ca   border-divider: #e3e9ec

# ============ 4.3 文字 ============
# text-heading:   #1f3a45   text-body: #2f434d
# text-muted:     #6a7c85   text-faint:#98a6ac   text-on-primary: #ffffff

# ============ 4.4 语义色 ============
# success-bg/#e5f3ea success/#2f9e5f
# warning-bg/#fff4df warning/#c7860a
# danger-bg/#fbeae8  danger/#b3261e  danger-hover/#f6dbd8 danger-text/#a32d25

# ============ 4.5 侧栏 ============
# sidebar-bg #1f3f4d  sidebar-hover #2b5060  selected-bg #ffffff
# selected-text #17323d  sidebar-muted #97afb8  sidebar-text #d9e6eb

# ============ 5. 字号 token 落点 ============
# display-number 24px/700等宽(filePreview)  miniPreview 20px
# page-title 22px/700  dialog-title 18px/700  section-title 15px/600
# button 13px/600  body 13px/400  body-small 12px/400  caption 11px/400

# ============ 6. 间距/圆角 token 落点 ============
# space-1 4px space-2 8px space-3 12px space-4 16px space-5 24px space-6 32px
# radius-sm 3px radius-md 4px radius-lg 8px radius-full 9999px

APP_STYLE = """
* { font-family: "Microsoft YaHei UI"; font-size: 13px; letter-spacing: 0; }

/* ---------- 全局 / 文字 ---------- */
QWidget { color: #2f434d; background: #eef1f3; }
QMainWindow, QWidget#miniWindow { background: #eef1f3; }
QLabel { background: transparent; }
QLabel#pageTitle { font-size: 22px; font-weight: 700; color: #1f3a45; }
QLabel#dialogTitle { font-size: 18px; font-weight: 700; color: #1f3a45; }
QLabel#sectionTitle { font-size: 15px; font-weight: 600; color: #1f3a45; }
QLabel#mutedText { font-size: 12px; color: #6a7c85; }
QLabel#warningText { color: #8a5a00; background: #fff7df; padding: 9px; border-radius: 4px; }

/* ---------- 侧栏 ---------- */
QFrame#sidebar { background: #1f3f4d; border: 0; border-right: 1px solid #16292f; }
QLabel#brand { color: #ffffff; font-size: 20px; font-weight: 700; background: transparent; }
QLabel#sidebarMuted { color: #97afb8; background: transparent; }
QListWidget#navigation { background: transparent; color: #d9e6eb; border: 0; outline: 0; }
QListWidget#navigation::item { height: 44px; padding-left: 12px; border-radius: 4px; }
QListWidget#navigation::item:hover { background: #2b5060; }
QListWidget#navigation::item:selected {
    background: #ffffff; color: #17323d; font-weight: 600;
    border-left: 3px solid #176d86;
}

/* ---------- 卡片 / 面板 ---------- */
QFrame#aiBand {
    background: #f6f8f9; border: 1px solid #d3dce0; border-left: 4px solid #176d86;
    border-radius: 8px;
}
QFrame#aiRuntime { background: #f6f8f9; border: 1px solid #d3dce0; border-radius: 6px; }
QFrame#namingPanel { background: #ffffff; border: 1px solid #d6e0e4; border-radius: 8px; }
QFrame#recordsCard { background: #ffffff; border: 1px solid #d3dce0; border-radius: 8px; }
QFrame#toolbarCard { background: #ffffff; border: 1px solid #d3dce0; border-radius: 8px; }
QFrame#listCard, QFrame#tableCard { background: #ffffff; border: 1px solid #d3dce0; border-radius: 8px; }
QFrame#previewBand { background: #eef4f6; border: 1px solid #d3e0e4; border-radius: 8px; }
QLabel#filePreview { font-family: "Consolas","Cascadia Mono",monospace; font-size: 24px; font-weight: 700; color: #0c5f79; padding: 8px 0; }
QLabel#miniTitle { font-size: 17px; font-weight: 700; color: #1f3a45; }
QLabel#miniProject { font-size: 15px; font-weight: 600; }
QLabel#miniPreview { font-family: "Consolas","Cascadia Mono",monospace; font-size: 20px; font-weight: 700; color: #0c5f79; }
QTextEdit#aiLog { background: #1f3038; color: #d6edf2; font-family: "Consolas"; }

/* ---------- 输入控件 ---------- */
QLineEdit, QTextEdit, QSpinBox, QComboBox, QDateEdit {
    background: #ffffff; border: 1px solid #b9c8cf; border-radius: 4px;
    padding: 7px 9px; min-height: 20px; selection-background-color: #277b94;
}
QScrollArea#aiSettingsScroll, QScrollArea#generalSettingsScroll,
QScrollArea#solidworksSettingsScroll { border: 0; background: transparent; }
/* focus 只加过渡色外圈，不改变 border 宽度，避免控件抖动 */
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus, QDateEdit:focus {
    border: 1px solid #8fc6d4; background: #fbfeff;
}
QGroupBox {
    background: #ffffff; border: 1px solid #d4dfe3; border-radius: 8px;
    margin-top: 14px; padding: 16px; font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }

/* ---------- 按钮体系 ----------
   无 kind == secondary（次按钮）。主按钮必须显式 kind="primary"。 */
QPushButton {
    min-height: 34px; padding: 0 15px; border: 1px solid #c3d0d5; border-radius: 4px;
    background: #e7edf0; color: #264653; font-weight: 600;
}
QPushButton:hover { background: #d5e2e7; }
QPushButton:pressed { background: #c6d6dc; }
QPushButton:disabled { background: #eef1f2; color: #a9b4b9; border: 1px solid #dee5e8; }

QPushButton[kind="primary"] { background: #176d86; color: #ffffff; border: 1px solid #176d86; }
QPushButton[kind="primary"]:hover { background: #105c73; }
QPushButton[kind="primary"]:pressed { background: #0c4b5e; }
QPushButton[kind="primary"]:disabled { background: #aab8be; color: #ecf0f2; border: 1px solid #aab8be; }

QPushButton[kind="secondary"] { background: #e7edf0; color: #264653; border: 1px solid #c3d0d5; }
QPushButton[kind="secondary"]:hover { background: #d5e2e7; }
QPushButton[kind="secondary"]:pressed { background: #c6d6dc; }
/* 可选/选中态：模块胶囊被选中时用主色底白字 */
QPushButton[kind="secondary"]:checked { background: #176d86; color: #ffffff; border: 1px solid #176d86; }

QPushButton[kind="ghost"] { background: transparent; color: #176d86; border: 1px solid transparent; padding: 0 5px; }
QPushButton[kind="ghost"]:hover { background: #eef4f6; }

QPushButton[kind="danger"] { background: #fbeae8; color: #a32d25; border: 1px solid #edcfcb; }
QPushButton[kind="danger"]:hover { background: #f6dbd8; }

QPushButton[kind="sidebar"] { background: #dbe7ea; color: #163f50; border: 1px solid #dbe7ea; }
QPushButton[compact="true"] { min-height: 28px; padding: 0 9px; }
QWidget#miniWindow QLineEdit, QWidget#miniWindow QComboBox {
    min-height: 18px; padding: 4px 8px;
}
QWidget#miniWindow QPushButton { min-height: 28px; padding: 0 9px; }

/* ---------- 表格 / 列表 ---------- */
QTableWidget, QListWidget {
    background: #ffffff; border: 1px solid #d3dee2; border-radius: 4px;
    alternate-background-color: #edf2f4; gridline-color: #e4ebee;
}
QTableWidget::item { padding: 8px; }
QTableWidget::item:selected, QListWidget::item:selected { background: #d9ebf0; color: #173e50; }
QHeaderView::section {
    background: #f6f8f9; color: #344b57; padding: 8px; border: 0;
    border-right: 1px solid #d5dfe3; border-bottom: 1px solid #cbd8dd; font-weight: 600;
}

/* ---------- 滚动 / 分割 / 菜单 / 提示 ---------- */
QSplitter::handle { background: #dce5e8; height: 4px; }
QScrollBar:vertical { background: #eef1f3; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #c3d0d6; border-radius: 6px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #aab8be; }
QScrollBar:horizontal { background: #eef1f3; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #c3d0d6; border-radius: 6px; min-width: 28px; }
QScrollBar::handle:horizontal:hover { background: #aab8be; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QMenu {
    background: #ffffff; border: 1px solid #bdcbd1; border-radius: 6px;
    padding: 6px; min-width: 200px;
}
QMenu::item { padding: 8px 30px 8px 12px; border-radius: 4px; font-size: 13px; }
QMenu::item:selected { background: #dcecf1; color: #173e50; }
QMenu::item:disabled { color: #98a6ac; }
QMenu::separator { height: 1px; background: #e3e9ec; margin: 5px 8px; }
/* 危险菜单项：包裹在 QWidgetAction 的 QLabel 内，统一红色 */
QLabel[menuDanger="true"] {
    color: #a32d25; font-size: 13px; padding: 8px 30px 8px 12px;
    border-radius: 4px; background: transparent;
}
QLabel[menuDanger="true"]:hover { background: #fbeae8; }
QToolTip { background: #213b49; color: white; border: 0; padding: 5px; }

/* ---------- 选项卡（设置页） ---------- */
QTabWidget::pane { border: 1px solid #d3dce0; border-radius: 8px; background: #ffffff; top: -1px; }
QTabBar::tab {
    background: #e7edf0; color: #264653; padding: 8px 16px;
    border: 1px solid #c3d0d5; border-bottom: 0; border-radius: 6px 6px 0 0; margin-right: 3px;
}
QTabBar::tab:selected { background: #ffffff; color: #176d86; font-weight: 600; }
QTabBar::tab:hover { background: #dbe6ea; }

/* ---------- 消息框 / 对话框 ---------- */
QMessageBox { background: #ffffff; }
QDialog { background: #eef1f3; }
QDialog QLabel { background: transparent; }
QDialogButtonBox { background: transparent; }

/* ---------- 表单标签 ---------- */
QFormLayout QLabel[fieldLabel="true"] { color: #465a63; font-weight: 500; background: transparent; }

/* ---------- 聊天消息卡（AI 设计问答） ---------- */
QFrame#chatUser { background: #e7f1f4; border: 1px solid #cfe0e6; border-radius: 8px; }
QFrame#chatAssistant { background: #ffffff; border: 1px solid #d3dee2; border-radius: 8px; }
QFrame#chatSystem { background: #f6f8f9; border: 1px dashed #c3d0d5; border-radius: 8px; }

/* ---------- 建议卡 / 历史区卡 ---------- */
QFrame#suggestionPanel, QFrame#suggestionCard {
    background: #ffffff; border: 1px solid #d3dee2; border-radius: 8px;
}
QFrame#planDetailCard, QFrame#planListCard {
    background: #ffffff; border: 1px solid #d3dee2; border-radius: 8px;
}

/* ---------- 空状态 ---------- */
QLabel#emptyState { color: #98a6ac; font-size: 12px; }

/* ---------- 状态徽章 ---------- */
QLabel[statusBadge="ok"]      { background: #e5f3ea; color: #2f9e5f; border-radius: 9999px; padding: 1px 10px; font-size: 11px; }
QLabel[statusBadge="reserved"]{ background: #fff4df; color: #c7860a; border-radius: 9999px; padding: 1px 10px; font-size: 11px; }
QLabel[statusBadge="void"]    { background: #fbeae8; color: #a32d25; border-radius: 9999px; padding: 1px 10px; font-size: 11px; }
QLabel[statusBadge="neutral"] { background: #eef1f2; color: #6a7c85; border-radius: 9999px; padding: 1px 10px; font-size: 11px; }
"""

APP_STYLE += """
QFrame#namingPreview { background: #e7f1f4; border: 1px solid #cbdde3; border-radius: 8px; }
QFrame#namingPreview QLabel { background: transparent; border: none; }
QLabel#simpleFilename { color: #145b70; font-size: 23px; font-weight: 600; padding: 6px 0; }
"""

APP_STYLE += """
QTableWidget#simpleRecords::item { padding: 4px; }
QPushButton[record_action="true"] { min-height: 24px; padding: 0 3px; }
QPushButton[record_action="true"]:disabled { color: #a9b4b9; background: transparent; border: none; }
"""
