# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import logging
import os
import sys
import json

from PyQt6.QtCore import QTimer, QCoreApplication, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon, QStyle

from . import __version__
from .development import configure_development, development_root, is_development, seed_demo
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
    try:
        arguments, dev_smoke = configure_development(sys.argv[1:])
    except (OSError, ValueError) as error:
        print(f"开发隔离初始化失败：{error}", file=sys.stderr)
        return 4
    sys.argv = [sys.argv[0], *arguments]
    if len(sys.argv) >= 2 and sys.argv[1] == "--solidworks-worker":
        from .solidworks.worker_entry import main as worker_main

        return worker_main(sys.argv[1:])
    # Lightweight Qt setup only; WebEngine is imported on the first preview click.
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
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
    if is_development():
        seed_demo(repository, workflow, settings)
    memory = NamingMemory(database)
    shell = ApplicationShell(app, settings, icon, repository, ProjectService(repository), workflow, exporter, memory)
    if is_development():
        shell.main_window.setWindowTitle(shell.main_window.windowTitle() + " · 隔离开发（虚构数据）")
        for window in (shell.main_window, shell.mini_window):
            window.solidworks_button.setEnabled(False)
            window.solidworks_button.setToolTip("隔离开发模式禁止连接 SOLIDWORKS")
        shell.main_window.settings_page.sw_enabled.setEnabled(False)
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
    hotkey_warning = ""
    if not is_development():
        hotkey = GlobalHotkey(shell.toggle)
        app.installNativeEventFilter(hotkey)
        shell.set_hotkey_register(hotkey.register)
        try:
            hotkey.register(settings.hotkey)
        except HotkeyError as error:
            LOGGER.warning("全局快捷键注册失败：%s", error)
            hotkey_warning = str(error)
        app.aboutToQuit.connect(hotkey.unregister)
    instance.show_requested.connect(shell.show_last)
    app.aboutToQuit.connect(instance.release)
    shell.show_last()
    if dev_smoke:
        def finish_development_smoke():
            from .paths import database_path, settings_dir, exports_dir, logs_dir
            cache = development_root() / "cache"
            screenshot = cache / "development-window.png"
            captured = shell.main_window.grab().save(str(screenshot))
            paths = [database_path(), settings_dir(), exports_dir(), logs_dir(), screenshot]
            report = {
                "isolated_paths": all(path.resolve().is_relative_to(development_root()) for path in paths),
                "instance_name": instance_name, "global_hotkey_registered": False,
                "credentials_disabled": shell.credential_store.backend is None,
                "solidworks_disabled": not settings.solidworks_enabled,
                "window_visible": shell.main_window.isVisible(), "screenshot_saved": captured,
                "projects": len(repository.list_projects()),
                "records": len(repository.list_records()),
            }
            (cache / "development-smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            shell.quit()
        QTimer.singleShot(1500, finish_development_smoke)
    if hotkey_warning:
        QTimer.singleShot(0, lambda: shell.notify("快捷键不可用", hotkey_warning))
    smoke_ms = os.environ.get("MECHANICAL_PROJECT_NAMER_SMOKE_QUIT_MS", "")
    if smoke_ms.isdigit():
        QTimer.singleShot(max(100, min(int(smoke_ms), 60_000)), app.quit)
    return app.exec()
