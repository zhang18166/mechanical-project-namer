# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import logging
import os
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon, QStyle

from . import __version__
from .data import DataExporter, Database, Repository
from .ai import NamingMemory
from .hotkey import GlobalHotkey, HotkeyError
from .logging_setup import configure_logging, install_exception_hook
from .paths import resource_path
from .numbering.workflow import NamingWorkflow
from .projects import ProjectService
from .settings import load_settings
from .single_instance import SingleInstance
from .style import APP_STYLE
from .ui import ApplicationShell


LOGGER = logging.getLogger(__name__)


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--solidworks-worker":
        from .solidworks.worker_entry import main as worker_main

        return worker_main(sys.argv[1:])
    configure_logging()
    app = QApplication(sys.argv)
    install_exception_hook()
    # 版本号唯一来源：mechanical_project_namer.__version__（与 pyproject.toml 同步）。
    # 禁止再在此处硬编码版本，否则 version.json / Git tag / __version__ 会失去可追溯性。
    app.setApplicationName(f"机械项目命名助手 V{__version__}")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(APP_STYLE)
    icon = app.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
    app.setWindowIcon(icon)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "无法启动", "当前系统未提供可用的系统托盘。")
        return 1
    instance_name = os.environ.get("MECHANICAL_PROJECT_NAMER_INSTANCE_NAME", "MechanicalProjectNamer.V11.SingleInstance")
    instance = SingleInstance(instance_name)
    if not instance.start_or_notify():
        return 0
    settings = load_settings()
    try:
        database = Database()
        database.migrate()
        repository = Repository(database)
    except Exception as error:
        LOGGER.exception("SQLite 数据库初始化失败")
        QMessageBox.critical(None, "数据库不可用", f"无法初始化本地主数据库：\n{error}")
        instance.release()
        return 2
    exporter = DataExporter(repository)
    workflow = NamingWorkflow(repository, exporter)
    memory = NamingMemory(database)
    shell = ApplicationShell(app, settings, icon, repository, ProjectService(repository), workflow, exporter, memory)
    shell.main_window.settings_page.set_key_status(shell.credential_store.masked_status())
    shell._refresh_memory_summary()
    if os.environ.get("MECHANICAL_PROJECT_NAMER_SMOKE_CHECKS", "") == "1":
        try:
            shell.credential_store.get_api_key()
            exporter.export_all(settings.current_project_id)
            LOGGER.info("发布包自检通过：Windows 凭据、SQLite 与 JSON 导出可用")
        except Exception:
            LOGGER.exception("发布包自检失败")
            shell.quit()
            instance.release()
            return 3
    hotkey = GlobalHotkey(shell.toggle)
    app.installNativeEventFilter(hotkey)
    shell.set_hotkey_register(hotkey.register)
    hotkey_warning = ""
    try:
        hotkey.register(settings.hotkey)
    except HotkeyError as error:
        LOGGER.warning("全局快捷键注册失败：%s", error)
        hotkey_warning = str(error)
    instance.show_requested.connect(shell.show_last)
    app.aboutToQuit.connect(hotkey.unregister)
    app.aboutToQuit.connect(instance.release)
    shell.show_last()
    if hotkey_warning:
        QTimer.singleShot(0, lambda: shell.notify("快捷键不可用", hotkey_warning))
    smoke_ms = os.environ.get("MECHANICAL_PROJECT_NAMER_SMOKE_QUIT_MS", "")
    if smoke_ms.isdigit():
        QTimer.singleShot(max(100, min(int(smoke_ms), 60_000)), app.quit)
    return app.exec()
