# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import logging
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMenu, QMessageBox, QSystemTrayIcon, QDialog, QTextEdit, QVBoxLayout

from ..ai import (
    AICancelled, AIClient, AIClientError, AIResponseAudit, AITaskState, AITaskType,
    MemoryPackageError, NamingMemory, NamingMemoryPackage, StreamEvent,
)
from ..ai.worker import AIWorker
from ..ai.design_context import bound_messages, excerpt, explicit_preference, relevance, MAX_MEMORY_CHARS, MAX_QUESTION_CHARS
from ..ai.design_skill import DESIGN_CHAT_SKILL
from .design_memory_dialog import DesignMemoryDialog
from ..ai.runtime import sanitize_log_message
from ..data import DataExporter, Repository
from ..data.repository import SingletonNumberExistsError
from ..numbering.workflow import NamingWorkflow
from ..numbering import MODULES
from ..projects import ProjectService, ValidationError
from ..security import CredentialStore
from ..settings import AppSettings, save_settings
from ..paths import application_log_path, local_data_dir, solidworks_log_path
from ..safe_fs import remove_file
from ..solidworks.controller import SolidWorksWorkerController
from ..solidworks.errors import SolidWorksError
from ..solidworks.models import OperationRequest, OperationResult
from ..solidworks.service import SolidWorksService
from .common import (
    ensure_window_visible, geometry_to_text, open_local_path, restore_visible_geometry, set_on_top,
    show_local_path_error,
)
from .ai_suggestions_dialog import AISuggestionsDialog
from .ai_plan_history_dialog import AIPlanHistoryDialog
from .main_window import MainWindow
from .simple_window import SimpleMainWindow, SimpleProjectDialog, ModuleDialog, SimpleMiniWindow
from ..numbering.simple import SIMPLE_TYPE, module_code as normalize_module
from .mini_window import MiniWindow
from .project_dialog import ProjectDialog
from .record_dialog import RecordDetailDialog
from .file_sync_dialog import FileScanThread, FileSyncDialog, SimpleFileSyncDialog
from ..file_sync import READY, scan_project
from .design_chat_dialog import DesignChatDialog
from .project_management_dialogs import (
    DuplicateProjectDialog, NumberingStatusDialog, PermanentDeleteDialog, ProjectPropertiesDialog,
)


LOGGER = logging.getLogger(__name__)


class ApplicationShell:
    def __init__(self, app: QApplication, settings: AppSettings, icon: QIcon,
                 repository: Repository, project_service: ProjectService,
                 workflow: NamingWorkflow, exporter: DataExporter, memory: NamingMemory) -> None:
        self.app = app
        self.settings = settings
        self.repository = repository
        self.project_service = project_service
        self.workflow = workflow
        self.exporter = exporter
        self.memory = memory
        self.memory_package = NamingMemoryPackage(memory.database, memory.package_directory)
        self.response_audit = AIResponseAudit(self.memory_package.response_log_path)
        self.hotkey_register: Callable[[str], None] | None = None
        self.current_project_id = settings.current_project_id
        self.current_module_code = settings.current_module_code or "03"
        self.current_part_type = settings.current_part_type or "自制机加工件"
        self.preview_copied = False
        self._save_in_progress = False
        self._current_records_cache = []
        self._records_cache_project = None
        self.file_check_thread = None
        self.file_sync_dialog = None
        self.project_files_page = None
        self._last_checked_project = None
        self.credential_store = CredentialStore()
        self.ai_threads: set[QThread] = set()
        self.ai_tasks: dict[AITaskType, AITaskState] = {}
        self.ai_worker = None
        self.ai_thread = None
        self.settings_ai_worker = None
        self.settings_ai_thread = None
        self.chat_ai_worker = None
        self.chat_ai_thread = None
        self.chat_ai_started_at = 0.0
        self.chat_ai_model = ""
        self.ai_started_at = 0.0
        self.ai_metrics: dict = {}
        self.ai_statistics_written = False
        self.latest_balance = None
        self.settings_ai_operation_active = False
        self.design_chat: DesignChatDialog | None = None
        self.chat_conversation_id: int | None = None
        self.solidworks_service = SolidWorksService(repository)
        self.solidworks_controller = SolidWorksWorkerController()
        self.solidworks_pending_kind = ""
        self.solidworks_pending: dict = {}
        self.main_window = SimpleMainWindow(settings)
        self.mini_window = SimpleMiniWindow()
        self.current_part_type = SIMPLE_TYPE
        self.ai_elapsed_timer = QTimer(self.main_window)
        self.ai_elapsed_timer.setInterval(250)
        self.ai_elapsed_timer.timeout.connect(self._update_ai_elapsed)
        self.chat_elapsed_timer = QTimer(self.main_window)
        self.chat_elapsed_timer.setInterval(250)
        self.chat_elapsed_timer.timeout.connect(self._update_chat_ai_elapsed)
        self.main_window.setWindowIcon(icon)
        self.mini_window.setWindowIcon(icon)
        restore_visible_geometry(self.main_window, settings.main_geometry, (1180, 760))
        restore_visible_geometry(self.mini_window, settings.mini_geometry, (430, 610))
        set_on_top(self.mini_window, settings.always_on_top)
        self._connect_signals()
        recovery = self.solidworks_service.reconcile_pending()
        if recovery:
            LOGGER.info("SOLIDWORKS恢复队列处理结果：%s", recovery)
        # 启动自愈：只修状态 / 用已有物理文件补记录 / 释放“辅助进程从未启动”的孤立预留，
        # 绝不释放任何已成功写库的编号。更靠后的孤立预留留给用户显式确认。
        try:
            healed = self.solidworks_service.reconcile_stale_reservations(
                grace_minutes=120, classify_only=False, release_orphans=False
            )
            changed = [item for item in healed if item.get("applied")]
            if changed:
                LOGGER.warning("SOLIDWORKS编号占用启动自愈：%s", changed)
                self.main_window.settings_page.set_solidworks_status(
                    f"启动自愈处理 {len(changed)} 项编号占用"
                )
        except Exception:  # noqa: BLE001
            LOGGER.exception("SOLIDWORKS编号占用启动自愈失败（不影响启动）")
        self.main_window.settings_page.set_archived_reference(self.memory.archived_reference_allowed())
        self.tray = QSystemTrayIcon(icon, app)
        self.tray.setToolTip("机械项目命名助手")
        self.tray.setContextMenu(self._build_tray_menu())
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()
        self.refresh_all()

    def set_hotkey_register(self, callback: Callable[[str], None]) -> None:
        self.hotkey_register = callback

    def refresh_all(self, query: str | None = None, include_archived: bool = False) -> None:
        query = self.main_window.project_search.text() if query is None else query
        status = "all" if include_archived else self.main_window.project_status_filter.currentData()
        project_type = self.main_window.project_type_filter.currentData()
        self._filter_projects(query, status, project_type)
        self.main_window.set_history_projects(self.repository.list_projects(project_status="all"))

    def _filter_projects(self, query: str, status: str, project_type: str) -> None:
        filtered = self.repository.list_projects(query, project_status=status, project_type=project_type)
        active = self.repository.list_projects()
        active_ids = {project.id for project in active}
        if self.current_project_id not in active_ids:
            self.current_project_id = active[0].id if active else None
        self.settings.current_project_id = self.current_project_id
        self.main_window.set_projects(filtered, self.current_project_id, active)
        self.refresh_current()
        self.tray.setContextMenu(self._build_tray_menu())

    def refresh_current(self, record_query: str | None = None, *, refresh_records: bool = True) -> None:
        if getattr(self.main_window, "simple_mode", False):
            self.current_part_type = SIMPLE_TYPE
        project = None
        records = []
        if self.current_project_id is not None:
            try:
                project = self.repository.get_project(self.current_project_id)
                if project.project_status != "normal":
                    raise LookupError("当前项目已归档或移入回收站")
                if refresh_records or self._records_cache_project != project.id:
                    query = self.main_window.current_search.text() if record_query is None else record_query
                    scope_id = None if getattr(self.main_window, "simple_mode", False) and self.main_window.search_scope.currentIndex() == 1 else project.id
                    self._current_records_cache = self.repository.list_records(scope_id, query=query)
                    self._records_cache_project = project.id
                records = self._current_records_cache
            except LookupError:
                self.current_project_id = None
                project = None
        modules = self.repository.list_modules(project.id) if project else []
        if project and modules:
            enabled = {item["module_code"] for item in modules if item.get("enabled")}
            if self.current_module_code not in enabled:
                self.current_module_code = sorted(enabled)[0] if enabled else ("" if getattr(self.main_window, "simple_mode", False) else "03")
            selected = next((item for item in modules if item["module_code"] == self.current_module_code), None)
            if not getattr(self.main_window, "simple_mode", False) and selected and selected.get("last_part_type"):
                self.current_part_type = selected["last_part_type"]
        preview = ""
        if project and self.current_module_code:
            try:
                preview = self.workflow.preview_named(project.id, self.current_module_code, self.current_part_type)
            except SingletonNumberExistsError as error:
                # 总成是单例：已存在时直接显示既有 000，不再回退到 legacy 图号。
                preview = error.full_number
            except (ValueError, LookupError):
                preview = "" if getattr(self.main_window, "simple_mode", False) else project.next_filename
        self.main_window.set_current_project(project, records, modules, self.current_module_code, self.current_part_type, preview)
        self.mini_window.set_project(project, records[:5], modules, self.current_module_code, self.current_part_type, preview)
        try:
            self.exporter.export_all(self.current_project_id)
        except Exception:
            LOGGER.exception("刷新当前项目状态快照失败")
        self._schedule_file_check()

    def _schedule_file_check(self):
        if not getattr(self, 'app', None) or not hasattr(self.main_window, 'file_sync_status') or getattr(self, '_quitting', False):
            return
        if self.current_project_id is None:
            self.main_window.file_sync_status.setText('目录检查：尚未选择项目。')
            return
        project = self.repository.get_project(self.current_project_id)
        key = (project.id, project.directory, project.solidworks_part_directory)
        if key == getattr(self, '_last_checked_project', None):
            return
        if getattr(self, 'file_check_thread', None):
            self.file_check_thread.cancel.set()
            return
        self._last_checked_project = key
        if not (project.directory or project.solidworks_part_directory):
            self.main_window.file_sync_status.setText('目录检查：未设置项目目录或SW保存目录。')
            return
        self.main_window.file_sync_status.setText('目录检查：正在检查已有文件…')
        worker = FileScanThread(lambda cancel: scan_project(self.repository, project.id, cancel=cancel), self.app)
        self.file_check_thread = worker
        def completed(result):
            if self.current_project_id != project.id:
                return
            ready = sum(e.status in READY for e in result.entries)
            pending = sum(e.status not in READY and e.status != '已登记' for e in result.entries)
            text = f'目录检查：可登记/关联{ready}项，待核对{pending}项。'
            if result.errors:
                text = '目录检查未完成，请点“同步已有文件”查看原因。'
            self.main_window.file_sync_status.setText(text)
        def finished():
            self.file_check_thread = None
            worker.deleteLater()
            self._schedule_file_check()
        worker.succeeded.connect(completed)
        worker.failed.connect(lambda error: self.main_window.file_sync_status.setText('目录检查未完成：' + error))
        worker.finished.connect(finished)
        worker.start()

    def _open_file_sync(self):
        if self.current_project_id is None:
            QMessageBox.information(self.main_window, '尚未选择项目', '请先选择项目并设置项目目录或SW保存目录。')
            return
        dialog = SimpleFileSyncDialog(self.repository, self.current_project_id, self.main_window)
        self.file_sync_dialog = dialog
        dialog.exec()
        self.file_sync_dialog = None
        self._last_checked_project = None
        self.refresh_all()
        if dialog.imported:
            self._refresh_memory_summary()
            self.notify('同步完成', f'已登记/关联{dialog.imported}项；请核对当前图号后继续。')

    def show_last(self) -> None:
        self.show_mini() if self.settings.last_window_mode == "mini" else self.show_full()

    def show_full(self) -> None:
        self.mini_window.hide()
        self.settings.last_window_mode = "full"
        self.main_window.showNormal()
        self.main_window.raise_()
        self.main_window.activateWindow()

    def show_mini(self) -> None:
        self.main_window.hide()
        self.settings.last_window_mode = "mini"
        self.mini_window.showNormal()
        self.mini_window.raise_()
        self.mini_window.activateWindow()
        self.mini_window.part_name.setFocus()

    def hide_all(self) -> None:
        self._capture_geometry()
        self.main_window.hide()
        self.mini_window.hide()
        self._save_settings()

    def toggle(self) -> None:
        self.hide_all() if self.main_window.isVisible() or self.mini_window.isVisible() else self.show_last()

    def notify(self, title: str, message: str) -> None:
        self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 3500)

    def quit(self) -> None:
        if getattr(self, "_quitting", False):
            return
        self._quitting = True
        for thread in (getattr(self, 'file_check_thread', None), getattr(getattr(self, 'file_sync_dialog', None), 'worker', None), getattr(getattr(self, 'project_files_page', None), 'worker', None)):
            if thread:
                thread.cancel.set()
        for task in tuple(self.ai_tasks.values()):
            task.cancel()
        if self.solidworks_controller.busy:
            self.solidworks_controller.cancel()
        self.ai_elapsed_timer.stop()
        self.chat_elapsed_timer.stop()
        self._capture_geometry()
        self._save_settings()
        self.main_window.setEnabled(False)
        self.mini_window.setEnabled(False)
        self.main_window.statusBar().showMessage("正在停止后台任务并退出…")
        self._finish_quit()

    def _finish_quit(self) -> None:
        # Keep the Qt event loop alive until workers have released their resources.
        if any(task.thread.isRunning() for task in self.ai_tasks.values()):
            QTimer.singleShot(50, self._finish_quit)
            return
        if any(thread and thread.isRunning() for thread in (getattr(self, 'file_check_thread', None), getattr(getattr(self, 'file_sync_dialog', None), 'worker', None), getattr(getattr(self, 'project_files_page', None), 'worker', None))):
            QTimer.singleShot(50, self._finish_quit)
            return
        self.main_window.allow_close = True
        self.mini_window.allow_close = True
        self.tray.hide()
        self.app.quit()

    def _connect_signals(self) -> None:
        main = self.main_window
        main.hide_requested.connect(self.hide_all)
        main.mini_requested.connect(self.show_mini)
        main.project_selected.connect(self._select_project)
        main.new_project_requested.connect(self._new_project)
        main.edit_project_requested.connect(self._edit_project)
        main.archive_project_requested.connect(self._archive_project)
        main.open_directory_requested.connect(self._open_project_directory)
        main.copy_requested.connect(self._copy_preview)
        main.save_requested.connect(self._save_record)
        main.solidworks_requested.connect(self._request_solidworks_create)
        main.solidworks_directory_requested.connect(self._choose_solidworks_directory)
        main.solidworks_cancel_requested.connect(self.solidworks_controller.cancel)
        main.record_open_requested.connect(self._open_record)
        main.record_action_requested.connect(self._record_action)
        main.project_search_requested.connect(self._filter_projects)
        main.project_action_requested.connect(self._project_action)
        main.record_search_requested.connect(self._search_records)
        main.csv_export_requested.connect(self._export_csv)
        main.record_export_requested.connect(self._export_records)
        main.file_sync_requested.connect(self._open_file_sync)
        main.project_files_requested.connect(self._show_project_files)
        main.settings_page.save_requested.connect(self._save_settings_values)
        main.ai_requested.connect(self._request_ai)
        main.plan_history_requested.connect(self._show_plan_history)
        main.ai_cancel_requested.connect(self._cancel_ai)
        main.module_create_requested.connect(lambda: self._module_action("", "create"))
        main.module_manage_requested.connect(self._module_action)
        main.design_chat_requested.connect(self._open_design_chat)
        mini = self.mini_window
        mini.copy_requested.connect(self._copy_preview)
        mini.save_requested.connect(self._save_record)
        mini.solidworks_requested.connect(self._request_solidworks_create)
        mini.solidworks_directory_requested.connect(self._choose_solidworks_directory)
        mini.recent_action_requested.connect(self._record_action)
        main.module_selected.connect(self._select_module)
        main.part_type_selected.connect(self._select_part_type)
        mini.module_selected.connect(self._select_module)
        mini.part_type_selected.connect(self._select_part_type)
        mini.full_requested.connect(self.show_full)
        mini.hide_requested.connect(self.hide_all)
        main.settings_page.action_requested.connect(self._settings_action)
        main.settings_page.ai_cancel_requested.connect(self._cancel_settings_ai)
        main.settings_page.memory_action_requested.connect(self._memory_action)
        self.solidworks_controller.progress.connect(self._on_solidworks_progress)
        self.solidworks_controller.completed.connect(self._on_solidworks_completed)
        self.solidworks_controller.failed.connect(self._on_solidworks_failed)
        self.solidworks_controller.busy_changed.connect(self._set_solidworks_busy)

    def _show_project_files(self) -> None:
        if self.project_files_page is None:
            from .project_files_page import ProjectFilesPage
            self.project_files_page = ProjectFilesPage(self.repository, self.main_window)
            self.main_window.pages.addWidget(self.project_files_page)
        self.project_files_page.load_projects(self.current_project_id)
        self.main_window.pages.setCurrentWidget(self.project_files_page)

    def _project_action(self, action: str, project_id: int) -> None:
        try:
            project = self.repository.get_project(project_id)
            if action in {"open", "set_current"}:
                if project.project_status == "normal":
                    self.repository.audit_project_action(project_id, "open" if action == "open" else "set_current")
                    self._select_project(project_id)
                    self.main_window.show_page(1)
                else:
                    self._show_project_properties(project_id)
            elif action == "edit":
                self._edit_project(project_id)
            elif action == "properties":
                self._show_project_properties(project_id)
            elif action == "duplicate":
                self._duplicate_project(project_id)
            elif action == "numbering":
                NumberingStatusDialog(project, self.repository.project_numbering_status(project_id), self.main_window).exec()
            elif action == "directory":
                self._open_project_directory(project_id)
            elif action == "export":
                self._export_project(project_id)
            elif action == "archive":
                self._archive_project(project_id)
            elif action == "restore":
                self.repository.set_project_status(project_id, "normal")
                self.refresh_all(); self.notify("项目已恢复", f"{project.name} · {project.code}")
            elif action == "trash":
                self._move_project_to_trash(project_id)
            elif action == "permanent_delete":
                self._permanently_delete_project(project_id)
        except Exception as error:
            LOGGER.exception("项目操作失败：%s", action)
            QMessageBox.critical(self.main_window, "项目操作失败", f"当前操作：{action}\n失败阶段：项目管理事务\n错误摘要：{error}\n数据库修改已回滚。")

    def _show_project_properties(self, project_id: int) -> None:
        ProjectPropertiesDialog(self.repository.project_properties(project_id), self.main_window).exec()

    def _duplicate_project(self, project_id: int) -> None:
        project = self.repository.get_project(project_id)
        dialog = DuplicateProjectDialog(project, self.main_window)
        if not dialog.exec():
            return
        values = dialog.values()
        values["name"] = values["name"].strip()
        values["code"] = self.project_service.normalize_code(values["code"])
        if not values["name"]:
            raise ValidationError("新项目名称不能为空。")
        duplicate = self.repository.duplicate_project(project_id, **values)
        self.refresh_all(); self.notify("项目已复制", f"{duplicate.name} · {duplicate.code}")

    def _export_project(self, project_id: int) -> None:
        project = self.repository.get_project(project_id)
        default = str(Path.home() / f"{project.name}_{project.code}_{__import__('datetime').datetime.now():%Y%m%d}.zip")
        target, _ = QFileDialog.getSaveFileName(self.main_window, "导出项目管理数据", default, "ZIP 文件 (*.zip)")
        if not target:
            return
        exported = self.exporter.export_project_package(project_id, Path(target))
        QMessageBox.information(self.main_window, "项目数据已导出", f"{exported}\n\n本资料包不包含 SolidWorks、STEP、NX、PDF 和 NC 文件。")

    def _switch_from_project(self, project_id: int) -> bool:
        if self.current_project_id != project_id:
            return True
        alternatives = [project for project in self.repository.list_projects() if project.id != project_id]
        if not alternatives:
            QMessageBox.warning(self.main_window, "无法更改当前项目状态", "这是唯一的正常项目。请先创建或恢复另一个项目，再归档或移入回收站。")
            return False
        self.current_project_id = alternatives[0].id
        self.settings.current_project_id = self.current_project_id
        return True

    def _move_project_to_trash(self, project_id: int) -> None:
        project = self.repository.get_project(project_id)
        message = (f"项目名称：{project.name}\n项目代号：{project.code}\n模块数量：{project.module_count}\n零件记录数量：{project.used_count}\n\n"
                   "移入回收站后不会再作为当前项目或 AI 历史参考；数据库资料和真实项目文件暂时不会删除，并可恢复。")
        if QMessageBox.warning(self.main_window, "移入回收站", message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        if not self._switch_from_project(project_id):
            return
        self.repository.set_project_status(project_id, "trash")
        self.refresh_all(); self.notify("项目已移入回收站", f"{project.name} · {project.code}")

    def _permanently_delete_project(self, project_id: int) -> None:
        properties = self.repository.project_properties(project_id)
        project = properties["project"]
        dialog = PermanentDeleteDialog(project, properties, self.main_window)
        if not dialog.exec():
            return
        if QMessageBox.warning(self.main_window, "最终确认", "这是最后一次确认。确定永久删除该项目的数据库资料吗？真实项目目录不会删除。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        result = self.repository.permanently_delete_project(project_id, dialog.confirmation.text())
        self.refresh_all()
        QMessageBox.information(self.main_window, "项目已永久删除", f"已删除数据库记录：{result['record_count']} 条\n删除前备份：{result['backup_path']}\n项目目录未执行删除操作。")

    def _sequence_options(self):
        source = self._active_window()
        if self.current_part_type == SIMPLE_TYPE and hasattr(type(source), 'selected_sequence'):
            return {"sequence_override": source.selected_sequence}
        return {}

    def _simple_module_action(self, module_code, action):
        modules = self.repository.list_modules(self.current_project_id, enabled_only=False)
        item = next((m for m in modules if m['module_code'] == normalize_module(module_code)), None)
        if action == 'manage':
            menu = QMenu(self.main_window)
            for module in modules:
                sub = menu.addMenu(f"{module['module_code']} · {module['module_name']}" + ("（已停用）" if not module['enabled'] else ""))
                for label, key in [('编辑', 'edit'), ('停用' if module['enabled'] else '启用', 'toggle'), ('删除未使用模块', 'delete')]:
                    sub.addAction(label).triggered.connect(lambda checked=False, code=module['module_code'], key=key: self._module_action(code, key))
            menu.addAction('新增模块').triggered.connect(lambda: self._module_action('', 'create'))
            menu.exec(self.main_window.cursor().pos())
            return
        if action == 'edit' and not item:
            return
        dialog = ModuleDialog(self.main_window, item['module_code'] if item else '', item['module_name'] if item else '')
        while dialog.exec():
            try:
                if item:
                    saved = self.repository.revise_module(self.current_project_id, module_code, dialog.code.text(), dialog.name.text())
                else:
                    saved = self.repository.create_module(self.current_project_id, dialog.code.text(), dialog.name.text())
                self.current_module_code = saved['module_code']
                self.settings.current_module_code = self.current_module_code
                self.refresh_all()
                return
            except (ValueError, LookupError) as error:
                QMessageBox.warning(dialog, '无法保存模块', str(error))

    def _module_action(self, module_code: str, action: str) -> None:
        if self.current_project_id is None:
            QMessageBox.information(self.main_window, "尚无项目", "请先选择当前项目。")
            return
        if getattr(self.main_window, "simple_mode", False) and action in {"create", "edit", "manage"}:
            self._simple_module_action(module_code, action)
            return
        if action == "create":
            code, ok = QInputDialog.getText(self.main_window, "新增模块", "模块编号（两位数字）：")
            if not ok:
                return
            name, ok = QInputDialog.getText(self.main_window, "新增模块", "模块名称：")
            if not ok:
                return
            try:
                self.repository.create_module(self.current_project_id, code, name)
                self.refresh_all()
            except Exception as error:
                QMessageBox.warning(self.main_window, "新增模块失败", str(error))
            return
        if action == "edit":
            modules = self.repository.list_modules(self.current_project_id, enabled_only=False)
            item = next((x for x in modules if x["module_code"] == normalize_module(module_code)), None)
            if not item:
                return
            name, ok = QInputDialog.getText(self.main_window, "编辑模块", "模块名称：", text=item["module_name"])
            if ok and name.strip():
                try:
                    self.repository.update_module(self.current_project_id, module_code, module_name=name)
                    self.refresh_all()
                except Exception as error:
                    QMessageBox.warning(self.main_window, "编辑模块失败", str(error))
            return
        if action == "menu":
            menu = QMenu(self.main_window)
            for label, key in (("选择模块", "select"), ("编辑模块", "edit"), ("向前移动", "up"), ("向后移动", "down"), ("停用/启用模块", "toggle"), ("删除模块", "delete")):
                act = menu.addAction(label)
                act.triggered.connect(lambda _=False, key=key, code=module_code: self._module_action(code, key))
            menu.exec(self.main_window.cursor().pos())
            return
        try:
            if action == "select":
                self._select_module(module_code)
            elif action == "up":
                self.repository.move_module(self.current_project_id, module_code, -1); self.refresh_all()
            elif action == "down":
                self.repository.move_module(self.current_project_id, module_code, 1); self.refresh_all()
            elif action == "toggle":
                item = next(x for x in self.repository.list_modules(self.current_project_id, enabled_only=False) if x["module_code"] == normalize_module(module_code))
                self.repository.set_module_enabled(self.current_project_id, module_code, not bool(item["enabled"])); self.refresh_all()
            elif action == "delete":
                if QMessageBox.question(self.main_window, "删除模块", "确定删除未使用模块吗？") == QMessageBox.StandardButton.Yes:
                    self.repository.delete_module(self.current_project_id, module_code); self.refresh_all()
        except Exception as error:
            QMessageBox.warning(self.main_window, "模块操作失败", str(error))

    def _open_design_chat(self) -> None:
        if self.design_chat is None:
            self.design_chat = DesignChatDialog([], self.main_window)
            self.design_chat.setWindowIcon(self.main_window.windowIcon())
            self.design_chat.send_requested.connect(self._send_design_chat)
            self.design_chat.new_conversation_requested.connect(self._new_design_conversation)
            self.design_chat.conversation_selected.connect(self._switch_design_conversation)
            self.design_chat.rename_requested.connect(self._rename_design_conversation)
            self.design_chat.delete_requested.connect(self._delete_design_conversation)
            self.design_chat.cancel_requested.connect(self._cancel_chat_ai)
            self.design_chat.save_project_memory_requested.connect(self._save_chat_project_memory)
            self.design_chat.save_naming_rule_requested.connect(self._save_chat_naming_rule)
            self.design_chat.save_preference_requested.connect(self._save_chat_preference)
            self.design_chat.manage_memory_requested.connect(self._manage_chat_memory)
            self.design_chat.show_skill_requested.connect(self._show_design_skill)
            self.design_chat.project_combo.currentIndexChanged.connect(self._chat_project_changed)
            self.design_chat.auto_learning_changed.connect(self.repository.set_auto_habit_learning)
        self.design_chat.auto_learning.blockSignals(True)
        self.design_chat.auto_learning.setChecked(self.repository.auto_habit_learning_enabled())
        self.design_chat.auto_learning.blockSignals(False)
        conversations = self.repository.list_conversations(self.current_project_id)
        conversation_ids = {item["conversation_id"] for item in conversations}
        if self.chat_conversation_id not in conversation_ids:
            self.chat_conversation_id = conversations[0]["conversation_id"] if conversations else None
        self.design_chat.set_conversations(conversations, self.chat_conversation_id)
        self.design_chat.set_projects(self.repository.list_projects(), self.current_project_id)
        self.design_chat.set_modules(self.repository.list_modules(self.current_project_id) if self.current_project_id else [], self.current_module_code)
        models = self.settings.available_models or ["deepseek-v4-pro", "deepseek-v4-flash"]
        self.design_chat.set_models(models, self.settings.model_name)
        if self.chat_conversation_id is not None:
            self._load_design_conversation(self.chat_conversation_id)
        ensure_window_visible(self.design_chat, self.main_window)
        self.design_chat.showNormal()
        self.design_chat.show()
        self.design_chat.raise_()
        self.design_chat.activateWindow()

    def _show_plan_history(self) -> None:
        dialog = AIPlanHistoryDialog(self.repository, self.current_project_id, self.main_window)
        dialog.adopt_new_project.connect(self._adopt_plan_new_project)
        dialog.adopt_current_project.connect(self._adopt_plan_current_project)
        dialog.adopt_modified.connect(self._adopt_plan_modified)
        dialog.regenerate_requested.connect(self._regenerate_plan)
        dialog.exec()

    def _adopt_plan_new_project(self, plan_id: int, payload: dict) -> None:
        plan = self.repository.get_ai_plan(plan_id)
        code = payload.get("code", "").strip().upper()
        name = payload.get("name", "").strip() or f"{code} 项目"
        if not code:
            QMessageBox.warning(self.main_window, "无法采用", "项目代号不能为空。"); return
        try:
            project = self.project_service.create(name=name, description=plan.get("prompt", ""), code=code)
            selected = [{"module_code": item.get("module_code"), "module_name": item.get("module_name")} for item in payload.get("modules", [])]
            result = self.repository.adopt_modules(project.id, selected)
            self.repository.record_ai_plan_adoption(plan_id, project.id, "new_project", payload,
                                                    self.repository.decode_ai_plan(plan), {"modules": result})
            self.current_project_id = project.id; self.settings.current_project_id = project.id; self.refresh_all(); self.main_window.show_page(1)
            self.notify("AI规划已采用", f"新项目已创建并设为当前项目：{project.name} · {project.code}")
        except Exception as error:
            QMessageBox.warning(self.main_window, "AI规划采用失败", str(error))

    def _adopt_plan_current_project(self, plan_id: int, payload: dict) -> None:
        if self.current_project_id is None: return
        try:
            selected = payload.get("modules", [])
            current = {item["module_code"]: item for item in self.repository.list_modules(self.current_project_id, enabled_only=False)}
            choices = {}
            for item in selected:
                code = normalize_module(item.get("module_code", ""))
                existing = current.get(code)
                if existing and existing.get("module_name") != item.get("module_name"):
                    dialog = QMessageBox(self.main_window)
                    dialog.setWindowTitle("模块冲突")
                    dialog.setText(f"模块 {code} 名称冲突：\n当前：{existing['module_name']}\nAI：{item.get('module_name','')}")
                    keep = dialog.addButton("保留当前名称", QMessageBox.ButtonRole.AcceptRole)
                    adopt = dialog.addButton("采用AI名称", QMessageBox.ButtonRole.ActionRole)
                    manual = dialog.addButton("手动修改", QMessageBox.ButtonRole.ActionRole)
                    dialog.addButton("取消同步", QMessageBox.ButtonRole.RejectRole)
                    dialog.exec(); clicked = dialog.clickedButton()
                    if clicked is adopt: choices[code] = "ai"
                    elif clicked is manual: choices[code] = "manual"
                    elif clicked is keep: choices[code] = "keep"
                    else: return
            preview = self.repository.adopt_modules(self.current_project_id, selected, choices)
            self.repository.record_ai_plan_adoption(plan_id, self.current_project_id, "current_project", payload,
                                                    self.repository.decode_ai_plan(self.repository.get_ai_plan(plan_id)), {"modules": preview})
            self.refresh_all(); self.notify("AI规划模块已同步", f"新增 {len(preview['added'])} 个模块，冲突 {len(preview['conflicts'])} 个")
        except Exception as error:
            QMessageBox.warning(self.main_window, "同步失败", str(error))

    def _adopt_plan_modified(self, plan_id: int, payload: dict) -> None:
        if self.current_project_id is None:
            self._adopt_plan_new_project(plan_id, payload)
        else:
            self._adopt_plan_current_project(plan_id, payload)

    def _regenerate_plan(self, plan_id: int) -> None:
        try:
            plan = self.repository.get_ai_plan(plan_id)
            if plan.get("prompt"):
                self._request_ai(plan["prompt"], "project")
        except LookupError:
            pass

    def _new_design_conversation(self) -> None:
        self.chat_conversation_id = self.repository.create_conversation(self.current_project_id, "新对话", self.settings.model_name, self.settings.thinking_mode == "enabled", self.settings.reasoning_level)
        if self.design_chat:
            self.design_chat.set_conversations(self.repository.list_conversations(self.current_project_id), self.chat_conversation_id)
            self.design_chat.set_messages([])
            self.design_chat.reference_label.setText("本轮上下文：新会话，尚未发送")

    def _switch_design_conversation(self, conversation_id: int) -> None:
        if conversation_id == self.chat_conversation_id:
            return
        self._load_design_conversation(conversation_id)

    def _load_design_conversation(self, conversation_id: int) -> None:
        try:
            conversation = self.repository.get_conversation(conversation_id)
        except LookupError:
            return
        self.chat_conversation_id = conversation_id
        if not self.design_chat:
            return
        self.design_chat.set_messages(self.repository.list_ai_messages(conversation_id))
        project_index = self.design_chat.project_combo.findData(conversation.get("project_id"))
        if project_index >= 0:
            self.design_chat.project_combo.setCurrentIndex(project_index)
        model_index = self.design_chat.model_combo.findText(conversation.get("model") or self.settings.model_name)
        if model_index >= 0:
            self.design_chat.model_combo.setCurrentIndex(model_index)
        self.design_chat.thinking.setChecked(bool(conversation.get("thinking_enabled")))
        reasoning = {"low": "低", "high": "高", "max": "极致"}.get(conversation.get("reasoning_level"), "高")
        self.design_chat.reasoning.setCurrentText(reasoning)

    def _rename_design_conversation(self, conversation_id: int) -> None:
        conversation = self.repository.get_conversation(conversation_id)
        title, accepted = QInputDialog.getText(
            self.design_chat or self.main_window, "重命名会话", "会话标题：", text=conversation["title"]
        )
        if accepted and title.strip():
            self.repository.rename_conversation(conversation_id, title)
            if self.design_chat:
                self.design_chat.set_conversations(self.repository.list_conversations(self.current_project_id), conversation_id)

    def _delete_design_conversation(self, conversation_id: int) -> None:
        conversation = self.repository.get_conversation(conversation_id)
        answer = QMessageBox.question(
            self.design_chat or self.main_window,
            "删除AI会话",
            f"确定删除会话“{conversation['title']}”及其中的临时聊天记录吗？\n\n项目正式数据和长期记忆不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.repository.delete_conversation(conversation_id)
        conversations = self.repository.list_conversations(self.current_project_id)
        self.chat_conversation_id = conversations[0]["conversation_id"] if conversations else None
        if self.design_chat:
            self.design_chat.set_conversations(conversations, self.chat_conversation_id)
            self.design_chat.set_messages(
                self.repository.list_ai_messages(self.chat_conversation_id) if self.chat_conversation_id else []
            )

    def _build_design_chat_messages(self, content: str, options: dict) -> tuple[list[dict], dict]:
        if self.chat_conversation_id is None:
            raise LookupError("当前没有AI会话。")
        if len(content) > MAX_QUESTION_CHARS:
            raise ValueError("本次问题超过12000字，请分段发送；原输入已保留。")
        preference = explicit_preference(content)
        if preference and len(preference) > MAX_MEMORY_CHARS:
            raise ValueError("个人习惯最多2000字，请缩短后发送；原输入已保留。")
        context = self.repository.prepare_conversation_context(self.chat_conversation_id)
        project_id = options.get("project_id") or self.current_project_id
        module_code = options.get("module_code") or self.current_module_code
        system_parts = [DESIGN_CHAT_SKILL]
        stats = {"history_messages": 0, "total_messages": context["total_message_count"],
                 "summary_loaded": context["summary_loaded"], "project_loaded": False,
                 "history_projects": 0, "naming_rules": 0, "project_memories": 0,
                 "preferences": 0, "context_reduced": context["summary_loaded"], "context_chars": 0}

        def reference(title, lines, budget):
            if lines:
                text = "\n".join(lines)
                stats['context_reduced'] |= len(text) > budget
                system_parts.append(title + "：\n" + excerpt(text, budget))

        if options.get("use_preferences", True):
            preferences = self.repository.list_design_preferences(content, limit=8)
            values = [r["content"] for r in preferences]
            if preference and preference not in values:
                values.insert(0, preference)
            reference("用户明确个人习惯（跨项目，本轮要求优先）", ["- " + v for v in values], 1500)
            stats["preferences"] = len(values)
        if options.get("use_project") and project_id is not None:
            project = self.repository.get_project(project_id)
            parts = self.repository.list_records(project_id, limit=40)
            reference("当前项目资料", [f"{project.name}（{project.code}），{project.description}",
                      "当前模块：" + module_code,
                      *[f"- {p.display_name}" for p in parts if p.module_code == module_code and p.status == "used"]], 1500)
            stats["project_loaded"] = True
        if options.get("use_history"):
            projects = self.repository.list_projects(include_archived=self.memory.archived_reference_allowed())
            history = sorted((p for p in projects if p.id != project_id and self.memory.project_reference_allowed(p.id)
                              and relevance(content, p.name + ' ' + p.description) > 0),
                             key=lambda p: relevance(content, p.name + ' ' + p.description), reverse=True)[:5]
            reference("历史相似项目", [f"- {p.name}（{p.code}）" for p in history], 500)
            stats["history_projects"] = len(history)
        if options.get("use_naming"):
            rules = self.memory.rules()
            reference("命名规则", [f"- {r['title']}：{r['content']}" for r in rules], 1800)
            feedback = [r for r in self.memory.feedback() if r['action'] in ('adopted', 'edited')
                        and (not module_code or not r['module_code'] or r['module_code'] == module_code)
                        and relevance(content, r['input_description'] + ' ' + r['final_name']) > 0][:5]
            reference("用户采用的命名纠偏", [f"- {r['ai_name']} → {r['final_name']}" for r in feedback], 600)
            stats["naming_rules"] = len(rules)
        if options.get("use_memory") and project_id is not None:
            references = self.repository.list_project_memories(project_id, module_code, content, limit=6)
            reference("用户确认的项目结论（仅本项目）", [f"- {r['title']}：{r['content']}" for r in references], 2000)
            stats["project_memories"] = len(references)
        messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
        if context["summary"]:
            messages.append({"role": "system", "content": "较早会话摘录（原文仍保存在本地，未载入部分不可猜测）：\n" + context["summary"]})
        messages.extend({"role": r["role"], "content": r["content"]} for r in context["recent_messages"] if r["role"] in {"user", "assistant"})
        messages.append({"role": "user", "content": content})
        messages, reduced = bound_messages(messages)
        stats["history_messages"] = sum(m['role'] in ('user', 'assistant') for m in messages[1:-1])
        stats["context_reduced"] |= reduced
        stats["context_chars"] = sum(len(m['content']) for m in messages)
        return messages, stats

    def _send_design_chat(self, content: str, options: dict) -> None:
        if len(content) > MAX_QUESTION_CHARS:
            QMessageBox.warning(self.design_chat or self.main_window, "输入过长", "本次问题超过12000字，请分段发送；原输入已保留。")
            return
        if self.chat_conversation_id is None:
            self._new_design_conversation()
        conversation_id = self.chat_conversation_id
        try:
            messages, context_stats = self._build_design_chat_messages(content, options)
        except (ValueError, LookupError) as error:
            QMessageBox.warning(self.design_chat or self.main_window, "无法发送", str(error))
            return
        project_id = options.get("project_id") or self.current_project_id
        model = options.get("model") or self.settings.model_name
        reasoning_level = {"低": "low", "高": "high", "极致": "max"}.get(options.get("reasoning_level"), "high")
        self.repository.update_conversation_settings(
            conversation_id, project_id=project_id, model=model,
            thinking_enabled=bool(options.get("thinking_enabled")), reasoning_level=reasoning_level,
        )
        preference = explicit_preference(content)
        message_id = self.repository.add_ai_message(conversation_id, "user", content, design_preference=preference)
        learning = []
        if options.get('auto_learning', True):
            try:
                learning = self.repository.learn_design_habits(message_id)
            except Exception:
                LOGGER.exception('设计习惯自动学习暂未保存')
                learning = ['自动学习暂未保存，聊天仍可继续']
        if learning:
            messages[0]['content'] += '\n\n本轮软件记忆结果（本轮新习惯优先于旧自动习惯）：\n' + '\n'.join(learning)
            messages, reduced = bound_messages(messages)
            context_stats['context_reduced'] |= reduced
            context_stats['context_chars'] = sum(len(m['content']) for m in messages)
            context_stats['history_messages'] = sum(m['role'] in ('user', 'assistant') for m in messages[1:-1])
        if self.design_chat:
            self.design_chat.accept_submission(content)
            self.design_chat.set_conversations(self.repository.list_conversations(self.current_project_id), conversation_id)
            self.design_chat.reference_label.setText(
                f"本轮上下文：当前会话历史 {context_stats['history_messages']} 条消息；"
                f"会话摘要：{'已加载' if context_stats['summary_loaded'] else '未生成'}；"
                f"当前项目资料：{'已加载' if context_stats['project_loaded'] else '未加载'}；"
                f"历史项目：{context_stats['history_projects']} 个；命名记忆：{context_stats['naming_rules']} 条；"
                f"项目记忆：{context_stats['project_memories']} 条；个人习惯：{context_stats['preferences']} 条；"
                f"上下文：{context_stats['context_chars']}/24000字"
                + ("（已压缩，原文保留）" if context_stats['context_reduced'] else "")
                + ("；个人习惯已保存" if preference else "")
                + ('；' + '；'.join(learning) if learning else '')
            )
            self.design_chat.set_busy(True)
            self.design_chat.set_status("正在读取当前会话历史……", model)
        try:
            api_key = self.credential_store.get_api_key()
        except Exception:
            api_key = ""
        if not api_key:
            answer = "当前未配置 DeepSeek API Key。请补充载荷、速度、材料和约束条件后再进行工程校核；本次回答仅作为临时上下文。"
            self.repository.add_ai_message(conversation_id, "assistant", answer)
            if self.design_chat:
                if self.chat_conversation_id == conversation_id:
                    self.design_chat.append_message("assistant", answer)
                self.design_chat.set_busy(False); self.design_chat.set_status("空闲", model)
            return
        def operation(cancel, emit):
            client = AIClient(self.settings.api_url, model, api_key, connect_timeout=self.settings.connect_timeout, request_timeout=self.settings.request_timeout, generation_timeout=self.settings.generation_timeout, thinking_enabled=bool(options.get("thinking_enabled")), reasoning_level=reasoning_level, auto_repair_enabled=self.settings.json_auto_repair, response_audit=self.response_audit)
            return client.engineering_chat_stream(messages, cancel, emit)
        def done(result):
            answer = result.get("content", "")
            self.repository.add_ai_message(conversation_id, "assistant", answer, result.get("usage"))
            if self.design_chat:
                if self.chat_conversation_id == conversation_id:
                    self.design_chat.append_message("assistant", answer)
                self.design_chat.set_busy(False); self.design_chat.set_status("成功", model)
                self.design_chat.set_conversations(self.repository.list_conversations(self.current_project_id), self.chat_conversation_id)
            self._finish_chat_ai(result)
        self._run_chat_ai(operation, done, lambda error: self._on_chat_error(error), model)

    def _on_chat_error(self, error):
        if self.design_chat:
            self.design_chat.set_busy(False); self.design_chat.set_status("失败", self.design_chat.model_combo.currentText())
        self._finish_chat_ai(error, "失败")

    def _chat_memory_target(self):
        if self.design_chat:
            return (self.design_chat.project_combo.currentData(), self.design_chat.module_combo.currentData() or "")
        return self.current_project_id, self.current_module_code

    def _chat_project_changed(self, _index) -> None:
        if not self.design_chat:
            return
        project_id = self.design_chat.project_combo.currentData()
        if project_id is not None:
            selected = self.design_chat.module_combo.currentData() or self.current_module_code
            self.design_chat.set_modules(self.repository.list_modules(project_id), selected)

    def _chat_memory_edit(self, title: str, instruction: str, content: str):
        value, accepted = QInputDialog.getMultiLineText(self.design_chat or self.main_window, title, instruction + "\n内容（最多2000字）：", content)
        if not accepted:
            return None
        value = value.strip()
        if not value or len(value) > MAX_MEMORY_CHARS:
            QMessageBox.warning(self.design_chat or self.main_window, "无法保存", "请输入1至2000字的记忆内容。")
            return None
        return value

    def _save_chat_preference(self, content: str) -> None:
        value = self._chat_memory_edit("记住个人习惯", "只保存跨项目使用的表达和工作方法。项目尺寸、材料和选型请保存为项目结论。", content)
        if value:
            self.repository.save_design_preference(value)
            self.design_chat.reference_label.setText("个人习惯已保存；以后问答可按需加载，可在“记忆 → 管理记忆”中修改或删除。")

    def _save_chat_project_memory(self, content: str) -> None:
        project_id, module = self._chat_memory_target()
        if project_id is None:
            QMessageBox.information(self.design_chat or self.main_window, "尚未选择项目", "请先选择结论所属项目。")
            return
        project = self.repository.get_project(project_id)
        value = self._chat_memory_edit("保存项目结论", f"保存到：{project.name}（{project.code}），模块{module}。请确认采用的结论，标明待验证事项。", content)
        if value:
            self.repository.save_project_memory(project_id, "AI设计问答确认结论", value, module, confidence=100)
            self.design_chat.reference_label.setText(f"项目结论已保存到 {project.code} / {module}，不会用于其他项目。")

    def _save_chat_naming_rule(self, content: str) -> None:
        project_id, module = self._chat_memory_target()
        value = self._chat_memory_edit("保存命名偏好", "请确认希望沿用的命名称谓或规则；图号仍由本地程序分配。", content)
        if value:
            self.memory.record_feedback(project_id, module, value, value, "adopted", "", "AI设计问答明确保存为命名偏好")
            self.design_chat.reference_label.setText("命名偏好已保存；相关命名问答将参考此记录。")

    def _manage_chat_memory(self) -> None:
        project_id, _ = self._chat_memory_target()
        DesignMemoryDialog(self.repository, project_id, self.design_chat or self.main_window).exec()

    def _show_design_skill(self) -> None:
        dialog = QDialog(self.design_chat or self.main_window)
        dialog.setWindowTitle("设计问答流程")
        dialog.resize(680, 460)
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(DESIGN_CHAT_SKILL)
        layout.addWidget(text)
        dialog.exec()

    def _select_project(self, project_id: int) -> None:
        if self.repository.get_project(project_id).project_status != "normal":
            return
        self.current_project_id = project_id
        self.settings.current_project_id = project_id
        self.preview_copied = False
        modules = self.repository.list_modules(project_id)
        enabled = [item["module_code"] for item in modules if item.get("enabled")]
        if self.current_module_code not in enabled:
            self.current_module_code = enabled[0] if enabled else "03"
        selected = next((item for item in modules if item["module_code"] == self.current_module_code), None)
        self.current_part_type = SIMPLE_TYPE if getattr(self.main_window, "simple_mode", False) else (selected.get("last_part_type", self.current_part_type) if selected else self.current_part_type)
        self.refresh_all()

    def _select_module(self, module_code: str) -> None:
        if self.current_project_id is None:
            return
        codes = {item["module_code"] for item in self.repository.list_modules(self.current_project_id)}
        if module_code not in codes:
            return
        self.current_module_code = normalize_module(module_code)
        self.settings.current_module_code = self.current_module_code
        self.refresh_current(refresh_records=False)

    def _select_part_type(self, part_type: str) -> None:
        if not part_type or getattr(self.main_window, "simple_mode", False):
            return
        self.current_part_type = part_type
        self.settings.current_part_type = part_type
        if self.current_project_id is not None:
            with self.repository.database.transaction() as connection:
                connection.execute("UPDATE project_modules SET last_part_type=?,updated_at=datetime('now') WHERE project_id=? AND module_code=?", (part_type, self.current_project_id, self.current_module_code))
        self.refresh_current(refresh_records=False)

    def _new_project(self, prefill_code: str = "", description: str = "") -> None:
        dialog_class = SimpleProjectDialog if getattr(self.main_window, "simple_mode", False) else ProjectDialog
        dialog = dialog_class(self.main_window)
        if prefill_code:
            dialog.code.setText(prefill_code)
        if description:
            dialog.description.setPlainText(description)
        while dialog.exec():
            try:
                options = {"modules": [("DJ", "电机"), ("JJ", "机架"), ("SL", "送料")]} if getattr(self.main_window, "simple_mode", False) else {}
                project = self.project_service.create(**dialog.values(), **options)
                self.current_project_id = project.id
                self.refresh_all()
                self.main_window.show_page(1)
                self.notify("项目已创建", f"{project.name} · {project.code}")
                return
            except ValidationError as error:
                QMessageBox.warning(dialog, "无法创建项目", str(error))

    def _edit_project(self, project_id: int) -> None:
        project = self.repository.get_project(project_id)
        dialog_class = SimpleProjectDialog if getattr(self.main_window, "simple_mode", False) else ProjectDialog
        dialog = dialog_class(self.main_window, project)
        while dialog.exec():
            values = dialog.values()
            values.pop("start_number")
            confirmed = False
            if values["code"].strip().upper() != project.code:
                answer = QMessageBox.warning(
                    dialog, "确认修改项目代号", "修改项目代号可能造成历史图号前缀不一致，是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                confirmed = answer == QMessageBox.StandardButton.Yes
                if not confirmed:
                    continue
            try:
                self.project_service.update(project_id, confirm_code_change=confirmed, **values)
                self.refresh_all()
                return
            except ValidationError as error:
                QMessageBox.warning(dialog, "无法保存项目", str(error))

    def _archive_project(self, project_id: int) -> None:
        project = self.repository.get_project(project_id)
        answer = QMessageBox.question(
            self.main_window, "归档项目",
            f"归档“{project.name}”后：\n\n1. 不再显示在正常项目列表和当前项目下拉框。\n"
            "2. 项目、模块、历史记录和编号状态全部保留。\n3. 项目目录和设计文件不会删除。\n"
            "4. 默认仍可作为 AI 历史参考。\n5. 以后可以随时恢复。\n\n是否继续？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            if not self._switch_from_project(project_id):
                return
            self.repository.set_project_status(project_id, "archived")
            self.refresh_all(); self.notify("项目已归档", f"{project.name} · {project.code}")

    def _open_project_directory(self, project_id: int) -> None:
        project = self.repository.get_project(project_id)
        result = open_local_path(project.directory, "directory")
        if result.ok:
            self.main_window.statusBar().showMessage("已打开项目目录", 2500)
            return
        if result.code == "empty":
            dialog = QMessageBox(self.main_window)
            dialog.setWindowTitle("当前项目未设置项目目录")
            dialog.setText(
                f"当前项目尚未设置项目目录。\n\n项目：\n{project.name} · {project.code}\n\n"
                "请先在“编辑项目”中设置项目目录。"
            )
            edit_button = dialog.addButton("编辑项目", QMessageBox.ButtonRole.AcceptRole)
            choose_button = dialog.addButton("选择目录", QMessageBox.ButtonRole.ActionRole)
            dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            dialog.exec()
            if dialog.clickedButton() is edit_button:
                self._edit_project(project_id)
            elif dialog.clickedButton() is choose_button:
                self._choose_project_directory(project_id)
            return
        if result.code == "missing":
            dialog = QMessageBox(self.main_window)
            dialog.setWindowTitle("项目目录不存在")
            dialog.setText(f"项目目录不存在。\n\n路径：\n{result.path}\n\n请选择后续操作。")
            choose_button = dialog.addButton("重新选择目录", QMessageBox.ButtonRole.ActionRole)
            create_button = dialog.addButton("创建该目录", QMessageBox.ButtonRole.AcceptRole)
            dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            dialog.exec()
            if dialog.clickedButton() is choose_button:
                self._choose_project_directory(project_id)
            elif dialog.clickedButton() is create_button:
                confirm = QMessageBox.question(
                    self.main_window, "确认创建目录", f"确定创建以下项目目录吗？\n\n{result.path}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if confirm == QMessageBox.StandardButton.Yes:
                    try:
                        result.path.mkdir(parents=True, exist_ok=True)
                    except OSError as error:
                        LOGGER.exception("创建项目目录失败：%s", result.path)
                        QMessageBox.warning(self.main_window, "项目目录创建失败", f"路径：\n{result.path}\n\n原因：{error}")
                        return
                    self._open_project_directory(project_id)
            return
        show_local_path_error(self.main_window, result, "项目目录")
        self.main_window.statusBar().showMessage("项目目录打开失败", 3500)

    def _choose_project_directory(self, project_id: int) -> None:
        project = self.repository.get_project(project_id)
        start = project.directory if project.directory and Path(project.directory).exists() else str(Path.home())
        selected = QFileDialog.getExistingDirectory(self.main_window, "选择项目目录", start)
        if not selected:
            return
        self.project_service.update(project_id, directory=selected)
        self.refresh_all()
        self._open_project_directory(project_id)

    def _copy_preview(self) -> None:
        if self.current_project_id is None:
            QMessageBox.information(self._active_window(), "尚无项目", "请先创建或选择项目。")
            return
        source = self._active_window()
        try:
            filename = self.workflow.copy_named_preview(
                self.current_project_id, self.current_module_code, self.current_part_type,
                source.part_name.text().strip(), **self._sequence_options(),
            )
        except SingletonNumberExistsError as error:
            self._notify_singleton_exists(error)
            return
        except (ValueError, SolidWorksError) as error:
            QMessageBox.warning(source, "无法生成编号", str(error))
            return
        QApplication.clipboard().setText(filename)
        self.preview_copied = True
        self.notify("已复制名称", filename)

    def _notify_singleton_exists(self, error: SingletonNumberExistsError) -> None:
        """单例类别（总成）已存在时的统一提示，措辞固定为“该模块已存在总成 000”。"""
        detail = f"\n\n已存在记录：{error.existing_full_name}" if error.existing_full_name else ""
        QMessageBox.information(
            self._active_window(), "总成已存在",
            f"{error}\n\n每个模块只允许一个总成，编号固定为 000。{detail}",
        )
        self.main_window.statusBar().showMessage(str(error), 5000)

    def _save_record(self, part_name: str, purpose: str, extra_notes: str) -> None:
        if self._save_in_progress:
            return
        self._save_in_progress = True
        try:
            self._save_record_impl(part_name, purpose, extra_notes)
        finally:
            self._save_in_progress = False

    def _save_record_impl(self, part_name: str, purpose: str, extra_notes: str) -> None:
        if self.current_project_id is None:
            QMessageBox.information(self._active_window(), "尚无项目", "请先创建或选择项目。")
            return
        if not part_name.strip():
            QMessageBox.warning(self._active_window(), "信息不完整", "请填写零件名称后再保存图号记录。")
            return
        try:
            record = self.workflow.save_named_and_advance(
                self.current_project_id, self.current_module_code, self.current_part_type,
                part_name, extra_notes or purpose, self.preview_copied, **self._sequence_options(),
            )
        except SingletonNumberExistsError as error:
            LOGGER.info("单例编号已存在，拒绝重复创建：%s", error)
            self._notify_singleton_exists(error)
            return
        except Exception as error:
            LOGGER.exception("图号记录保存失败")
            QMessageBox.critical(self._active_window(), "保存失败", f"当前输入尚未清空。\n\n{error}")
            return
        self.preview_copied = False
        try:
            QApplication.clipboard().setText(record.display_name.strip())
            self.repository.mark_copied(record.filename)
            copy_status = "已保存并复制"
        except Exception:
            LOGGER.exception("保存后复制文件名失败")
            copy_status = "已保存，但复制失败"
            retry_box = QMessageBox(self._active_window())
            retry_box.setWindowTitle("复制失败")
            retry_box.setText(f"记录已保存，但未能写入剪贴板：\n{record.display_name.strip()}")
            retry = retry_box.addButton("重试复制", QMessageBox.ButtonRole.AcceptRole)
            retry_box.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
            retry_box.exec()
            if retry_box.clickedButton() is retry:
                try:
                    QApplication.clipboard().setText(record.display_name.strip())
                    self.repository.mark_copied(record.filename)
                    copy_status = "已保存并复制"
                except Exception:
                    LOGGER.exception("重试复制文件名失败")
        try:
            self.memory.save_part(record.project_id, record.module_code, record.part_sequence, record.part_name, record.part_type, "")
        except Exception:
            LOGGER.exception("手动命名写入 AI 记忆失败")
        self.main_window.clear_inputs()
        self.mini_window.clear_inputs()
        self.refresh_all()
        self.notify(copy_status, record.display_name.strip())

    def _request_solidworks_create(self, part_name: str, extra_notes: str = "") -> None:
        if self.solidworks_controller.busy:
            return
        if not self.settings.solidworks_enabled:
            QMessageBox.information(self._active_window(), "SOLIDWORKS集成已关闭", "请先在设置中启用SOLIDWORKS集成。")
            return
        if self.current_project_id is None:
            QMessageBox.information(self._active_window(), "尚无项目", "请先创建或选择项目。")
            return
        part_name = part_name.strip()
        if not part_name:
            QMessageBox.warning(self._active_window(), "信息不完整", "请填写零件名称后再创建。")
            return
        project = self.repository.get_project(self.current_project_id)
        try:
            sequence_options = self._sequence_options()
            if self.current_part_type == SIMPLE_TYPE:
                from ..solidworks.validation import validate_part_name
                validate_part_name(part_name)
                self.workflow.preview_named(project.id, self.current_module_code, self.current_part_type, **sequence_options)
        except (ValueError, LookupError, SolidWorksError) as error:
            QMessageBox.warning(self._active_window(), "无法生成文件", str(error))
            return
        pending_inputs = {"project": project, "part_name": part_name, "extra_notes": extra_notes,
                          "module_code": self.current_module_code, "part_type": self.current_part_type,
                          **sequence_options}
        # 单例类别（总成）在启动 SOLIDWORKS 之前就给出明确提示，避免无谓的探测。
        try:
            self.repository.get_next_named_number(
                self.current_project_id, self.current_module_code, self.current_part_type
            )
        except SingletonNumberExistsError as error:
            self._notify_singleton_exists(error)
            return
        except ValueError:
            pass
        if not project.solidworks_part_directory:
            self.solidworks_pending_kind = "awaiting_directory"
            self.solidworks_pending = pending_inputs
            dialog = QMessageBox(self._active_window())
            dialog.setWindowTitle("未设置SOLIDWORKS目录")
            dialog.setText(
                "当前项目尚未设置SOLIDWORKS零件目录。\n\n"
                f"项目：{project.name} · {project.code}\n当前名称：{part_name}"
            )
            choose = dialog.addButton("立即选择目录", QMessageBox.ButtonRole.AcceptRole)
            use_default = dialog.addButton("使用项目目录\\01_SolidWorks", QMessageBox.ButtonRole.ActionRole)
            edit = dialog.addButton("打开项目路径设置", QMessageBox.ButtonRole.ActionRole)
            cancel = dialog.addButton("取消创建", QMessageBox.ButtonRole.RejectRole)
            use_default.setEnabled(bool(project.directory))
            if not project.directory:
                use_default.setToolTip("请先设置项目根目录")
            dialog.exec()
            clicked = dialog.clickedButton()
            if clicked is choose:
                self._choose_solidworks_directory()
            elif clicked is use_default:
                self._set_default_solidworks_directory(project)
            elif clicked is edit:
                self.solidworks_pending_kind = ""
                self.solidworks_pending = {}
                self._edit_project(project.id)
            else:
                self.solidworks_pending_kind = ""
                self.solidworks_pending = {}
            return
        directory = Path(project.solidworks_part_directory)
        if not directory.exists():
            answer = QMessageBox.question(
                self._active_window(), "目标目录不存在", f"是否创建以下目录？\n\n{directory}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                directory.mkdir(parents=True, exist_ok=False)
            except OSError as error:
                QMessageBox.critical(self._active_window(), "目录创建失败", str(error))
                return
        self.solidworks_pending_kind = "create_probe"
        self.solidworks_pending = pending_inputs
        self._start_solidworks_worker(OperationRequest(
            operation_id=f"probe-{uuid.uuid4()}", action="probe",
            fallback_template=self.settings.solidworks_fallback_template,
        ))

    def _validate_solidworks_directory(self, directory: Path) -> None:
        if not directory.exists() or not directory.is_dir():
            raise OSError("目录不存在。")
        probe = directory / ".mechanical_project_namer_write_test"
        try:
            probe.write_text("ok", encoding="utf-8")
        finally:
            remove_file(probe, what="目录可写性探测文件")

    def _resume_solidworks_create(self) -> None:
        pending = self.solidworks_pending
        if not pending or self.current_project_id is None:
            return
        project = self.repository.get_project(pending["project"].id)
        self.solidworks_pending_kind = "create_probe"
        pending["project"] = project
        self.solidworks_pending = pending
        self._start_solidworks_worker(OperationRequest(
            operation_id=f"probe-{uuid.uuid4()}", action="probe",
            fallback_template=self.settings.solidworks_fallback_template,
        ))

    def _choose_solidworks_directory(self) -> None:
        project_id = self.current_project_id
        if project_id is None:
            return
        project = self.repository.get_project(project_id)
        start = project.solidworks_part_directory or project.directory or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self._active_window(), "选择SOLIDWORKS零件目录", start)
        if not selected:
            return
        try:
            directory = Path(selected)
            self._validate_solidworks_directory(directory)
            self.project_service.update(project_id, solidworks_part_directory=str(directory))
        except Exception as error:
            QMessageBox.warning(self._active_window(), "目录不可用", f"无法使用该目录：\n{error}")
            return
        self.refresh_all()
        if self.solidworks_pending_kind == "awaiting_directory":
            self.notify("SOLIDWORKS目录设置成功", "正在返回创建流程")
            self._resume_solidworks_create()

    def _set_default_solidworks_directory(self, project) -> None:
        if not project.directory:
            return
        directory = Path(project.directory) / "01_SolidWorks"
        if not directory.exists():
            answer = QMessageBox.question(self._active_window(), "确认创建目录", f"目录尚不存在，是否创建？\n\n{directory}")
            if answer != QMessageBox.StandardButton.Yes:
                self.solidworks_pending_kind = ""
                self.solidworks_pending = {}
                return
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                QMessageBox.warning(self._active_window(), "目录创建失败", str(error))
                return
        try:
            self._validate_solidworks_directory(directory)
            self.project_service.update(project.id, solidworks_part_directory=str(directory))
        except Exception as error:
            QMessageBox.warning(self._active_window(), "目录不可用", str(error))
            return
        self.refresh_all()
        if self.solidworks_pending_kind == "awaiting_directory":
            self.notify("SOLIDWORKS目录设置成功", "正在返回创建流程")
            self._resume_solidworks_create()

    def _start_solidworks_worker(self, request: OperationRequest) -> None:
        self._on_solidworks_progress("worker", "正在启动SOLIDWORKS辅助进程")
        try:
            self.solidworks_controller.start(request, self.settings.solidworks_overall_timeout)
        except Exception as error:
            self._on_solidworks_failed(error)

    def _on_solidworks_progress(self, stage: str, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {message}"
        self.main_window.append_solidworks_log(line)
        self._set_solidworks_status({
            "connected": "已连接", "template_resolved": "模板正常", "file_saved": "创建成功",
        }.get(stage, "正在检测" if self.solidworks_pending_kind.endswith("probe") else "正在创建"))
        LOGGER.info("SOLIDWORKS [%s] %s", stage, message)

    def _on_solidworks_completed(self, result: OperationResult) -> None:
        kind = self.solidworks_pending_kind
        if kind == "create_probe":
            pending = self.solidworks_pending
            project = pending["project"]
            module_code = pending.get("module_code", self.current_module_code)
            part_type = pending.get("part_type", self.current_part_type)
            sequence_options = {"sequence_override": pending["sequence_override"]} if "sequence_override" in pending else {}
            try:
                if part_type == SIMPLE_TYPE:
                    # reserve() performs the one authoritative directory scan and number check.
                    preview_name = self.workflow.preview_named(project.id, module_code, part_type, **sequence_options) + '-' + pending["part_name"]
                else:
                    preview_name = self.workflow.copy_named_preview(project.id, module_code, part_type, pending["part_name"])

                target = Path(project.solidworks_part_directory) / f"{preview_name}.SLDPRT"
                if self.settings.solidworks_confirm_before_create and not getattr(self.main_window, "simple_mode", False):
                    text = (
                        "即将创建SOLIDWORKS零件\n\n"
                        f"当前项目：{project.name}\n模块：{self.current_module_code}\n"
                        f"零件类别：{self.current_part_type}\n零件名称：{pending['part_name']}\n"
                        f"最终文件名：{target.name}\n完整路径：{target}\n"
                        f"SOLIDWORKS版本：{result.info.year} {result.info.service_pack}\n"
                        f"零件模板：{result.template_path}"
                    )
                    answer = QMessageBox.question(
                        self._active_window(), "确认创建", text,
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if answer != QMessageBox.StandardButton.Yes:
                        self.solidworks_pending_kind = ""
                        self._set_solidworks_status("已取消")
                        return
                reservation = self.solidworks_service.reserve(
                    project.id, module_code, part_type, pending["part_name"],
                    project.solidworks_part_directory, extra_notes=pending.get("extra_notes", ""), **sequence_options,
                )
                self.solidworks_pending_kind = "create"
                self.solidworks_pending = {**pending, "reservation": reservation}
                self.solidworks_service.update_status(
                    reservation["operation_id"], "connecting", template_path=result.template_path,
                    detected_version=str(result.info.year or ""), detected_build=result.info.build,
                    service_pack=result.info.service_pack, process_id=result.info.process_id,
                )
                self._start_solidworks_worker(OperationRequest(
                    operation_id=reservation["operation_id"], target_path=reservation["target_path"],
                    fallback_template=self.settings.solidworks_fallback_template,
                    bring_to_front=self.settings.solidworks_switch_after_success,
                ))
            except Exception as error:
                self._on_solidworks_failed(error)
            return
        if kind == "create":
            reservation = self.solidworks_pending.get("reservation", {})
            operation_id = reservation.get("operation_id", result.operation_id)
            try:
                record = self.solidworks_service.commit_created_file(operation_id, result, self.preview_copied)
                if self.settings.solidworks_copy_after_success:
                    QApplication.clipboard().setText(record.display_name)
                self.preview_copied = False
                try:
                    self.memory.save_part(record.project_id, record.module_code, record.part_sequence, record.part_name, record.part_type, "")
                except Exception:
                    LOGGER.exception("SOLIDWORKS命名写入AI记忆失败")
                self.main_window.clear_inputs()
                self.mini_window.clear_inputs()
                self.refresh_all()
                self._set_solidworks_status(f"创建成功 {result.info.year}")
                self.notify("SOLIDWORKS零件已创建", f"{record.display_name}.SLDPRT")
            except Exception as error:
                self._on_solidworks_failed(error)
                return
            finally:
                self.solidworks_pending_kind = ""
                self.solidworks_pending = {}
            return
        if kind in {"settings_probe", "test_create_probe"}:
            self.main_window.settings_page.set_solidworks_status(
                f"已连接 SOLIDWORKS {result.info.year} {result.info.service_pack}，构建 {result.info.build}，进程 {result.info.process_id}",
                result.template_path,
            )
            self._set_solidworks_status(f"已连接 {result.info.year}")
            if kind == "settings_probe":
                QMessageBox.information(self.main_window, "SOLIDWORKS连接测试", "连接、版本和默认零件模板检查通过。")
                self.solidworks_pending_kind = ""
                return
            test_dir = local_data_dir() / "sw_test"
            test_dir.mkdir(parents=True, exist_ok=True)
            test_name = datetime.now().strftime("SW_API_TEST_%Y%m%d_%H%M%S.SLDPRT")
            target = test_dir / test_name
            self.solidworks_pending_kind = "test_create"
            self.solidworks_pending = {"target": str(target)}
            self._start_solidworks_worker(OperationRequest(
                operation_id=f"test-{uuid.uuid4()}", target_path=str(target),
                fallback_template=self.settings.solidworks_fallback_template,
            ))
            return
        if kind == "test_create":
            answer = QMessageBox.question(
                self.main_window, "测试创建成功",
                f"已创建并验证测试零件：\n{result.target_path}\n大小：{result.file_size} 字节\n\n是否删除测试文件？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                if not remove_file(Path(result.target_path), what="SOLIDWORKS 测试零件"):
                    QMessageBox.warning(
                        self.main_window, "无法删除测试文件",
                        f"未能删除测试零件，请手动处理：\n{result.target_path}",
                    )
            self.solidworks_pending_kind = ""

    def _on_solidworks_failed(self, error: Exception) -> None:
        if getattr(error, "code", "") == "singleton_reserved":
            QMessageBox.information(self._active_window(), "总成正在创建中", str(error))
            self._set_solidworks_status("未创建：总成正在创建中")
            self.main_window.append_solidworks_log(f"未创建：{error}")
            self.solidworks_pending_kind = ""
            self.solidworks_pending = {}
            return
        if isinstance(error, SingletonNumberExistsError) or getattr(error, "code", "") == "singleton_exists":
            self._notify_singleton_exists(error)
            self._set_solidworks_status("未创建：总成已存在")
            self.main_window.append_solidworks_log(f"未创建：{error}")
            self.solidworks_pending_kind = ""
            self.solidworks_pending = {}
            return
        reservation = self.solidworks_pending.get("reservation", {})
        operation_id = reservation.get("operation_id")
        target = Path(reservation.get("target_path", "")) if reservation.get("target_path") else None
        status_note = "未增加编号"
        if operation_id:
            # fail() 自身不会抛异常：写库失败时自动落盘恢复文件，绝不静默。
            outcome = self.solidworks_service.fail(
                operation_id, error, bool(target and target.is_file()),
                snapshot=reservation or None,
            )
            if outcome.get("persisted") == "recovery_file":
                status_note = f"数据库写入失败，已落盘恢复文件：{outcome.get('recovery_path')}"
                LOGGER.error("SOLIDWORKS失败状态转入本地恢复文件：%s", outcome.get("recovery_path"))
            elif outcome.get("recovery_required"):
                status_note = "保留预留并待恢复"
        self._set_solidworks_status("操作失败")
        self.main_window.append_solidworks_log(f"失败：{error}")
        if status_note.startswith("数据库写入失败"):
            self.main_window.append_solidworks_log(status_note)
        self.main_window.settings_page.sw_status.setToolTip(f"失败：{error}")
        QMessageBox.critical(
            self._active_window(), "SOLIDWORKS操作失败",
            f"原因：{error}\n\n阶段：{getattr(error, 'stage', 'worker')}\n"
            f"错误码：{getattr(error, 'code', 'worker_error')}\n"
            f"目标路径：{target or '无'}\n数据库：{status_note}",
        )
        self.solidworks_pending_kind = ""
        self.solidworks_pending = {}

    def _reconcile_reservations_dialog(self) -> None:
        """诊断并安全恢复被占用的编号（只释放确认失败/孤立的预留）。"""
        try:
            findings = self.solidworks_service.reconcile_stale_reservations(classify_only=True)
            status_files = self.solidworks_service.list_status_recovery_files()
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("编号占用诊断失败")
            QMessageBox.critical(self.main_window, "诊断失败", str(error))
            return
        committed = [item for item in findings if item["decision"] == "already_committed"]
        recoverable = [item for item in findings if item["decision"] == "recovered_from_file"]
        orphaned = [item for item in findings if item["decision"] == "orphaned_reservation"]
        within_grace = [item for item in orphaned if not item.get("stale")]
        releasable = [item for item in orphaned if item.get("stale")]
        failed_recover = [item for item in findings if item["decision"] == "recover_failed"]
        lines = [
            f"已成功写库（只修正状态，不释放）：{len(committed)}",
            f"可用物理文件补记录：{len(recoverable)}",
            f"孤立预留可释放：{len(releasable)}",
            f"孤立预留但仍在保护期内（不释放）：{len(within_grace)}",
            f"待恢复文件补记录失败：{len(failed_recover)}",
            f"本地状态恢复文件：{len(status_files)}",
        ]
        for item in releasable:
            lines.append(
                f"  · 可释放 {item.get('full_name') or item['operation_id']}"
                f"  [{item.get('project_id')}/{item.get('module_code')}/{item.get('part_type')}"
                f" #{item.get('part_sequence')}]  状态={item.get('status')} 最后更新={item.get('updated_at')}"
            )
        for item in committed:
            lines.append(f"  · 已写库记录 id={item.get('record_id')} — 不会释放")
        for item in status_files:
            if not item.get("readable"):
                lines.append(f"  · 恢复文件不可读：{item.get('path')}（{item.get('error')}）")
                continue
            lines.append(
                f"  · 恢复文件 {item.get('operation_id')} {item.get('error_code')}"
                f" 目标={item.get('target_path')}"
            )
        text = "\n".join(lines)
        if not releasable and not recoverable:
            QMessageBox.information(self.main_window, "编号占用检查", f"未发现需要处理的占用。\n\n{text}")
            return
        answer = QMessageBox.question(
            self.main_window, "编号占用检查",
            f"{text}\n\n是否执行恢复？\n（已成功生成并写库的编号绝不会被释放）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            applied = self.solidworks_service.reconcile_stale_reservations(
                classify_only=False, release_orphans=True
            )
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("编号恢复执行失败")
            QMessageBox.critical(self.main_window, "恢复失败", str(error))
            return
        done = [item for item in applied
                if item.get("applied") or item["decision"] == "recover_failed"]
        note = [f"{item['operation_id']}: {item['decision']}"
                + (f" -> 记录 id={item['record_id']}" if item.get("record_id") else "")
                + (f" [{item.get('error')}]" if item.get("error") else "")
                for item in done]
        self.refresh_all()
        QMessageBox.information(
            self.main_window, "编号恢复完成",
            f"已处理 {len(done)} 项。\n\n" + ("\n".join(note) if note else "无需处理。"),
        )

    def _set_solidworks_busy(self, busy: bool) -> None:
        self.main_window.set_solidworks_busy(busy)
        self.mini_window.set_solidworks_busy(busy)
        self.main_window.settings_page.set_solidworks_busy(busy)

    def _set_solidworks_status(self, text: str) -> None:
        self.main_window.set_solidworks_status(text)
        self.mini_window.set_solidworks_status(text)
        self.main_window.settings_page.set_solidworks_status(text)

    def _record_action(self, action: str, record_id: int) -> None:
        try:
            record = self.repository.get_record(record_id)
        except LookupError:
            return
        if action == "edit":
            self._open_record(record_id)
            return
        if action in {"copy_full", "copy_number", "copy_part"}:
            value = {"copy_full": record.display_name, "copy_number": record.part_number,
                     "copy_part": record.part_name}.get(action, record.display_name)
            QApplication.clipboard().setText(value)
            self.notify("已复制", value)
            return
        if action == "open_file":
            if record.file_path:
                result = open_local_path(Path(record.file_path), "file")
                if not result.ok:
                    show_local_path_error(self._active_window(), result)
            return
        if action == "open_directory":
            if record.file_path:
                opened = open_local_path(Path(record.file_path).parent, "directory")
                if opened.ok:
                    return
            self._open_project_directory(record.project_id)
            return
        if action in {"void", "restore"}:
            status = "void" if action == "void" else "used"
            self.repository.set_record_status(record_id, status)
            self.refresh_all()
            self.notify("记录状态已更新", "已作废" if status == "void" else "已恢复使用")
            return
        if action == "delete":
            details = self.repository.record_delete_details(record_id)
            physical = details["physical_file_exists"]
            text = (f"完整名称：{record.display_name}\n项目：{record.project_name} · {record.project_code}\n"
                    f"模块：{record.module_code}\n状态：{'已作废' if record.status == 'void' else '已使用'}\n"
                    f"实际文件路径：{record.file_path or '未关联'}\n物理文件：{'存在' if physical else '不存在'}\n\n")
            dialog = QMessageBox(self.main_window)
            dialog.setWindowTitle("删除错误记录")
            dialog.setText(text + ("该记录关联一个实际设计文件。\n删除软件记录不会删除该设计文件。" if physical else "此操作只删除软件数据库记录。"))
            delete = dialog.addButton("仅删除软件记录，保留文件" if physical else "删除软件记录", QMessageBox.ButtonRole.AcceptRole)
            open_dir = dialog.addButton("打开文件所在目录", QMessageBox.ButtonRole.ActionRole) if physical else None
            void = dialog.addButton("改为作废", QMessageBox.ButtonRole.ActionRole)
            dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            dialog.exec()
            clicked = dialog.clickedButton()
            if clicked is delete:
                self.repository.delete_record(record_id)
                self.refresh_all()
                self.notify("错误记录已删除", f"{record.display_name}，实际设计文件未删除")
            elif physical and clicked is open_dir:
                open_local_path(Path(record.file_path).parent, "directory")
            elif clicked is void:
                self.repository.set_record_status(record_id, "void")
                self.refresh_all()
                self.notify("记录已标记作废", f"编号 {record.part_number} 不会重新使用")

    def _open_record(self, record_id: int) -> None:
        record = self.repository.get_record(record_id)
        dialog = RecordDetailDialog(record, self.main_window, associated_files=self.repository.list_record_files(record_id))
        if not dialog.exec():
            return
        try:
            if dialog.mark_void_requested:
                self.repository.set_record_status(record_id, "void")
            else:
                self.repository.update_record(record_id, *dialog.values())
        except Exception as error:
            QMessageBox.warning(self.main_window, "保存修改失败", str(error))
            return
        try:
            self.exporter.export_all(self.current_project_id)
        except Exception:
            LOGGER.exception("记录修改后的开放数据导出失败")
        self.refresh_all()

    def _search_records(self, query: str, date_from: str, date_to: str, current_only: bool) -> None:
        project_id = self.current_project_id if current_only else self.main_window.history_project.currentData()
        if current_only and getattr(self.main_window, "simple_mode", False) and self.main_window.search_scope.currentIndex() == 1:
            project_id = None
        records = self.repository.list_records(project_id, query, date_from, date_to)
        if current_only:
            self._current_records_cache = records
            self._records_cache_project = project_id
        table = self.main_window.current_records if current_only else self.main_window.history_records
        self.main_window.set_record_rows(table, records)

    def _export_csv(self, current_only: bool) -> None:
        self._export_records("csv", current_only)

    def _export_records(self, file_format: str, current_only: bool) -> None:
        project_id = self.current_project_id if current_only else self.main_window.history_project.currentData()
        if current_only and project_id is None:
            QMessageBox.information(self.main_window, "尚无当前项目", "请先创建或选择项目。")
            return
        choices = ["当前项目全部记录" if current_only else "全部项目记录", "当前搜索和筛选结果", "当前选中的记录", "仅已使用记录", "仅已作废记录"]
        scope, accepted = QInputDialog.getItem(self.main_window, "选择导出范围", "导出范围：", choices, 0, False)
        if not accepted:
            return
        query = ""
        date_from = ""
        date_to = ""
        record_ids = None
        status = ""
        table = self.main_window.current_records if current_only else self.main_window.history_records
        if scope == "当前搜索和筛选结果":
            if current_only:
                query = self.main_window.current_search.text()
            else:
                query = self.main_window.history_search.text()
                date_from = "" if self.main_window.date_from.date() == self.main_window.date_from.minimumDate() else self.main_window.date_from.date().toString("yyyy-MM-dd")
                date_to = "" if self.main_window.date_to.date() == self.main_window.date_to.minimumDate() else self.main_window.date_to.date().toString("yyyy-MM-dd")
        elif scope == "当前选中的记录":
            record_ids = {
                int(table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole))
                for index in table.selectionModel().selectedRows()
                if table.item(index.row(), 0) is not None
            }
            if not record_ids:
                QMessageBox.information(self.main_window, "尚未选择记录", "请先在记录表中选择要导出的记录。")
                return
        elif scope == "仅已使用记录":
            status = "used"
        elif scope == "仅已作废记录":
            status = "void"
        project = self.repository.get_project(project_id) if project_id is not None else None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{project.name}_{project.code}" if project else "全部项目"
        base_name = re.sub(r'[<>:"/\\|?*]', "_", base_name)
        suffix = ".xlsx" if file_format == "xlsx" else f".{file_format}"
        default = str(Path.home() / f"{base_name}_项目文件记录_{timestamp}{suffix}")
        filters = {"xlsx": "Excel 文件 (*.xlsx)", "csv": "CSV 文件 (*.csv)", "pdf": "PDF 文件 (*.pdf)"}
        target_text, _ = QFileDialog.getSaveFileName(self.main_window, "导出项目文件记录", default, filters[file_format])
        if not target_text:
            return
        target = Path(target_text)
        if target.suffix.lower() != suffix:
            target = target.with_suffix(suffix)
        overwrite = False
        if target.exists():
            overwrite = QMessageBox.question(
                self.main_window, "确认覆盖导出文件", f"目标文件已存在：\n\n{target}\n\n是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) == QMessageBox.StandardButton.Yes
            if not overwrite:
                return
        try:
            result = self.exporter.export_records(
                file_format, target, project_id=project_id, query=query, date_from=date_from,
                date_to=date_to, record_ids=record_ids, status=status, overwrite=overwrite,
            )
        except Exception as error:
            LOGGER.exception("项目文件记录导出失败：%s", file_format)
            QMessageBox.critical(self.main_window, "导出失败", f"无法生成导出文件。\n\n目标：\n{target}\n\n原因：{error}")
            return
        dialog = QMessageBox(self.main_window)
        dialog.setWindowTitle("导出成功")
        dialog.setText(
            f"导出成功\n\n格式：\n{result.format}\n\n项目：\n{result.project_name} · {result.project_code}\n\n"
            f"记录数量：\n{result.record_count}\n\n文件：\n{result.path}"
        )
        open_button = dialog.addButton("打开文件", QMessageBox.ButtonRole.AcceptRole)
        directory_button = dialog.addButton("打开所在目录", QMessageBox.ButtonRole.ActionRole)
        copy_button = dialog.addButton("复制路径", QMessageBox.ButtonRole.ActionRole)
        dialog.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        if dialog.clickedButton() is open_button:
            opened = open_local_path(result.path, "file")
            if not opened.ok:
                show_local_path_error(self.main_window, opened, "导出文件")
        elif dialog.clickedButton() is directory_button:
            opened = open_local_path(result.path.parent, "directory")
            if not opened.ok:
                show_local_path_error(self.main_window, opened, "导出目录")
        elif dialog.clickedButton() is copy_button:
            QApplication.clipboard().setText(str(result.path))

    def _save_settings_values(self, values: dict) -> None:
        old_hotkey = self.settings.hotkey
        new_hotkey = values["hotkey"]
        if not new_hotkey:
            QMessageBox.warning(self.main_window, "设置无效", "全局快捷键不能为空。")
            return
        if not values["api_url"] or not values["model_name"]:
            QMessageBox.warning(self.main_window, "设置无效", "API 地址和模型名称不能为空。")
            return
        known_models = set(self.settings.available_models or []) | set(self.settings.verified_manual_models or [])
        if values["model_name"] != self.settings.model_name and values["model_name"] not in known_models:
            QMessageBox.warning(self.main_window, "模型尚未验证", "手动输入的模型 ID 必须先通过“测试所选模型”，才能保存为默认模型。")
            return
        if values["api_key"]:
            try:
                self.credential_store.set_api_key(values["api_key"])
                self.main_window.settings_page.clear_api_key()
            except Exception as error:
                QMessageBox.warning(self.main_window, "API Key 保存失败", str(error))
                return
        if new_hotkey != old_hotkey and self.hotkey_register:
            try:
                self.hotkey_register(new_hotkey)
            except Exception as error:
                QMessageBox.warning(self.main_window, "快捷键注册失败", str(error))
                self.main_window.settings_page.hotkey.setText(old_hotkey)
                return
        self.settings.hotkey = new_hotkey
        self.settings.always_on_top = bool(values["always_on_top"])
        self.settings.api_url = values["api_url"]
        self.settings.model_name = values["model_name"]
        self.settings.thinking_mode = values.get("thinking_mode", self.settings.thinking_mode)
        self.settings.reasoning_level = values.get("reasoning_level", self.settings.reasoning_level)
        self.settings.json_auto_repair = bool(values.get("json_auto_repair", self.settings.json_auto_repair))
        self.settings.connect_timeout = int(values.get("connect_timeout", self.settings.connect_timeout))
        self.settings.request_timeout = int(values.get("request_timeout", self.settings.request_timeout))
        self.settings.generation_timeout = int(values.get("generation_timeout", self.settings.generation_timeout))
        self.settings.solidworks_enabled = bool(values.get("solidworks_enabled", self.settings.solidworks_enabled))
        self.settings.solidworks_fallback_template = str(values.get("solidworks_fallback_template", self.settings.solidworks_fallback_template))
        self.settings.solidworks_confirm_before_create = bool(values.get("solidworks_confirm_before_create", self.settings.solidworks_confirm_before_create))
        self.settings.solidworks_copy_after_success = bool(values.get("solidworks_copy_after_success", self.settings.solidworks_copy_after_success))
        self.settings.solidworks_switch_after_success = bool(values.get("solidworks_switch_after_success", self.settings.solidworks_switch_after_success))
        self.settings.solidworks_advance_after_success = bool(values.get("solidworks_advance_after_success", self.settings.solidworks_advance_after_success))
        self.settings.solidworks_overall_timeout = int(values.get("solidworks_overall_timeout", self.settings.solidworks_overall_timeout))
        set_on_top(self.mini_window, self.settings.always_on_top)
        self.main_window.settings_page.set_key_status(self.credential_store.masked_status())
        self._save_settings()
        self.notify("设置已保存", "窗口、快捷键和 AI 配置已更新。")

    def _settings_action(self, action: str, values: dict) -> None:
        if action in {"sw_test_connection", "sw_reload_template"}:
            if self.solidworks_controller.busy:
                return
            self.solidworks_pending_kind = "settings_probe"
            self.solidworks_pending = {}
            self._start_solidworks_worker(OperationRequest(
                operation_id=f"probe-{uuid.uuid4()}", action="probe",
                fallback_template=values.get("solidworks_fallback_template", ""),
            ))
            return
        if action == "sw_test_create":
            if self.solidworks_controller.busy:
                return
            self.solidworks_pending_kind = "test_create_probe"
            self.solidworks_pending = {}
            self._start_solidworks_worker(OperationRequest(
                operation_id=f"probe-{uuid.uuid4()}", action="probe",
                fallback_template=values.get("solidworks_fallback_template", ""),
            ))
            return
        if action == "sw_reconcile_reservations":
            self._reconcile_reservations_dialog()
            return
        if action == "sw_open_logs":
            result = open_local_path(solidworks_log_path(), "file")
            if result.code == "missing":
                QMessageBox.information(
                    self.main_window, "暂无SOLIDWORKS日志",
                    "当前还没有SOLIDWORKS运行日志。\n\n完成一次连接测试或创建测试零件后会自动生成。",
                )
            elif not result.ok:
                show_local_path_error(self.main_window, result, "SOLIDWORKS日志文件")
            return
        if action == "save_key":
            if not values.get("api_key"):
                QMessageBox.warning(self.main_window, "Key 为空", "请输入 API Key。")
                return
            try:
                self.credential_store.set_api_key(values["api_key"])
                self.main_window.settings_page.clear_api_key()
                self.main_window.settings_page.set_key_status(self.credential_store.masked_status())
                self.notify("API Key 已保存", "Key 已安全写入 Windows 凭据管理器。")
            except Exception as error:
                QMessageBox.warning(self.main_window, "API Key 保存失败", str(error))
            return
        if action == "delete_key":
            try:
                self.credential_store.delete_api_key()
                self.main_window.settings_page.set_key_status("未配置 API Key")
                self.notify("API Key 已删除", "Windows 凭据管理器中的 Key 已删除。")
            except Exception as error:
                QMessageBox.warning(self.main_window, "删除 Key 失败", str(error))
            return
        if action == "discover_models":
            self._run_ai_operation("读取可用模型", self._discover_models, values, self._on_models, self._on_settings_ai_error)
        elif action == "balance":
            self._run_ai_operation("读取账户信息", self._read_balance, values, self._on_balance, self._on_settings_ai_error)
        elif action == "check_connection":
            self._run_ai_operation("检查 Key 与网络", self._check_connection, values, self._on_diagnostic, self._on_settings_ai_error)
        elif action == "test_model":
            answer = QMessageBox.question(self.main_window, "模型测试会产生费用", "模型测试会产生少量 API 费用，是否继续？")
            if answer == QMessageBox.StandardButton.Yes:
                self._run_ai_operation("测试所选模型", self._test_model, values, self._on_model_test, self._on_settings_ai_error)
        elif action == "full_test":
            self._run_ai_operation("完整测试", self._full_test, values, self._on_full_test, self._on_settings_ai_error)

    def _client_from_values(self, values: dict) -> AIClient:
        api_key = values.get("api_key") or self.credential_store.get_api_key()
        return AIClient(
            values.get("api_url", self.settings.api_url), values.get("model_name", self.settings.model_name), api_key or "",
            connect_timeout=values.get("connect_timeout", self.settings.connect_timeout),
            request_timeout=values.get("request_timeout", self.settings.request_timeout),
            generation_timeout=values.get("generation_timeout", self.settings.generation_timeout),
            thinking_enabled=values.get("thinking_mode", self.settings.thinking_mode) == "enabled",
            reasoning_level=values.get("reasoning_level", self.settings.reasoning_level),
            auto_repair_enabled=bool(values.get("json_auto_repair", self.settings.json_auto_repair)),
            response_audit=self.response_audit,
        )

    def _discover_models(self, values, _cancel, emit):
        emit(StreamEvent("config", "正在检查配置"))
        emit(StreamEvent("key", "正在读取 DeepSeek API Key"))
        try:
            return {"models": self._client_from_values(values).discover_models(), "source": "实时模型列表", "warning": ""}
        except AIClientError as error:
            cached = self.settings.available_models or []
            if not cached:
                raise
            emit(StreamEvent("cache", f"实时读取失败，改用缓存模型列表：{error}"))
            from ..ai.models import ModelInfo
            return {"models": [ModelInfo(item, item) for item in cached], "source": "缓存模型列表", "warning": str(error)}

    def _read_balance(self, values, _cancel, emit):
        emit(StreamEvent("config", "正在读取账户信息"))
        return self._client_from_values(values).balance()

    def _check_connection(self, values, _cancel, emit):
        emit(StreamEvent("config", "正在检查 Key 与网络"))
        return self._client_from_values(values).check_key_network()

    def _test_model(self, values, cancel, emit):
        emit(StreamEvent("config", "正在测试所选模型"))
        client = self._client_from_values(values)
        result = client.test_model(cancel, emit)
        balance = None
        try:
            balance = client.balance()
            emit(StreamEvent("balance", "API 余额读取完成"))
        except AIClientError as error:
            emit(StreamEvent("balance_warning", f"余额读取失败，不影响模型测试结果：{error}"))
        return {"test": result, "balance": balance}

    def _full_test(self, values, cancel, emit):
        emit(StreamEvent("config", "完整测试：检查 Key 与网络"))
        client = self._client_from_values(values)
        models = client.discover_models()
        emit(StreamEvent("config", f"模型列表读取成功：{len(models)} 个"))
        balance = client.balance()
        emit(StreamEvent("config", "账户信息读取成功"))
        test = client.test_model(cancel, emit)
        return {"models": models, "balance": balance, "test": test}

    def _on_models(self, result) -> None:
        models = result["models"] if isinstance(result, dict) else result
        source = result.get("source", "实时模型列表") if isinstance(result, dict) else "实时模型列表"
        ids = [model.model_id for model in models]
        from datetime import datetime
        self.settings.available_models = ids
        self.settings.model_list_source = source
        self.settings.model_list_updated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        selected = self.settings.model_name if self.settings.model_name in ids else ("deepseek-v4-pro" if "deepseek-v4-pro" in ids else ids[0])
        if selected != self.settings.model_name:
            self.main_window.settings_page.set_diagnostic("上次使用模型当前不可用，请重新选择。")
        self.settings.model_name = selected
        self.main_window.settings_page.set_model_values(ids, self.settings.model_list_source, self.settings.model_list_updated_at, selected)
        self._save_settings()
        self._finish_settings_ai()
        self.notify("模型列表已更新", f"{source}：{len(ids)} 个模型。")

    def _on_balance(self, balance) -> None:
        self.latest_balance = balance
        text = f"账户状态：{'正常' if balance.is_available else '不可用'}\n现金余额：{balance.total_balance or '未知'}\n赠送余额：{balance.granted_balance or '未知'}\n更新时间：{balance.updated_at}"
        if not balance.is_available:
            text += "\n账户余额不足，AI 生成可能失败。"
        self.main_window.settings_page.set_diagnostic(text)
        self._finish_settings_ai()

    def _on_diagnostic(self, result) -> None:
        self.main_window.settings_page.set_diagnostic(f"{result.message}\n可用模型：{result.model_count} 个\n耗时：{result.elapsed_seconds:.2f} 秒")
        self._finish_settings_ai("成功" if result.ok else "失败")

    def _on_model_test(self, result) -> None:
        outcome = result if isinstance(result, dict) else {"test": result, "balance": None}
        result = outcome["test"]
        if outcome.get("balance") is not None:
            self.latest_balance = outcome["balance"]
        if result.requested_model not in (self.settings.available_models or []):
            verified = list(self.settings.verified_manual_models or [])
            if result.requested_model not in verified:
                verified.append(result.requested_model)
            self.settings.verified_manual_models = verified
            self._save_settings()
        self.main_window.settings_page.set_diagnostic(
            f"模型测试成功\n请求模型：{result.requested_model}\n实际返回模型：{result.actual_model}\n首个响应：{result.first_response_seconds:.2f} 秒\n首个文本：{result.first_text_seconds:.2f} 秒\n总耗时：{result.elapsed_seconds:.2f} 秒\n回复：{result.reply}\nToken：{result.prompt_tokens + result.completion_tokens}，缓存：{result.cached_tokens}"
        )
        self._finish_settings_ai()

    def _on_full_test(self, result) -> None:
        self._on_model_test({"test": result["test"], "balance": result.get("balance")})

    def _on_ai_error(self, error) -> None:
        message = str(error)
        error_title = "AI 请求失败"
        regenerate = False
        if isinstance(error, AIClientError):
            error_title = {
                "json_syntax": "AI返回成功，但JSON语法无效",
                "json_schema": "AI返回JSON可解析，但内容结构不完整",
                "output_truncated": "AI输出因长度限制被截断，无法可靠解析",
            }.get(error.code, "AI 请求失败")
            message = f"阶段：{error.stage}\n原因：{error}\n错误类型：{error.code}"
            if error.status:
                message += f"\nHTTP 状态码：{error.status}"
            suggestions = {
                "no_key": "请在设置中保存 API Key。", "invalid_key": "请删除并重新保存正确的 API Key。",
                "network": "请检查网络、DNS、代理和 API 地址。", "connect_timeout": "请检查网络或适当增加连接超时。",
                "generation_timeout": "模型已接受请求，可增加生成总超时后重试。", "rate_limit": "请求过于频繁，请稍后重试。",
                "model_unavailable": "请刷新模型列表并选择当前账户可用模型。",
                "json_parse": "请重新生成；若持续出现请查看日志。",
                "json_syntax": "可查看原始响应，或重新生成。",
                "json_schema": "可查看原始响应，或重新生成。",
                "output_truncated": "请降低推理级别或重试。",
                "cancelled": "用户已取消，输入内容已保留。",
            }
            message += "\n建议：" + suggestions.get(error.code, "请查看 AI 运行日志后重试。")
        # Compatibility for isolated unit harnesses without the task registry.
        if not hasattr(self, "ai_tasks") and hasattr(self.main_window, "settings_page"):
            self.main_window.settings_page.set_diagnostic(message)
        self._append_ai_log("失败：" + message.replace("\n", "；"))
        if isinstance(error, AIClientError) and error.metrics.get("response_record_id"):
            self._append_ai_log(
                f"查看 AI 原始响应：记录 {error.metrics['response_record_id']} · {self.memory_package.response_log_path}"
            )
        if not isinstance(error, AICancelled):
            QMessageBox.warning(self.main_window, error_title, message)
            if isinstance(error, AIClientError) and error.code in {"json_syntax", "json_schema", "output_truncated", "json_parse"}:
                regenerate = self._show_ai_error_actions(error)
        self._finish_ai_ui(error, "取消" if isinstance(error, AICancelled) else "失败")
        if regenerate:
            QTimer.singleShot(0, lambda: self._request_ai(
                getattr(self, "ai_last_request_description", ""),
                getattr(self, "ai_last_request_type", "project"),
            ))

    def _show_ai_error_actions(self, error: AIClientError) -> bool:
        """Offer local inspection/re-parse and a fresh request after parse failures."""
        if not hasattr(self, "response_audit"):
            return False
        record_id = error.metrics.get("response_record_id")
        record = next((item for item in self.response_audit.latest(50) if item.get("record_id") == record_id), None) if record_id else None
        if not record:
            return False
        dialog = QMessageBox(self.main_window)
        dialog.setWindowTitle("AI 原始响应处理")
        dialog.setText("原始响应已脱敏保存，可选择下一步操作。")
        dialog.setInformativeText(
            f"记录：{record.get('record_id', '暂无数据')}\n模型：{record.get('model', '暂无数据')}\n"
            f"时间：{record.get('returned_at', '暂无数据')}\n完成原因：{(record.get('metadata') or {}).get('finish_reason', '暂无数据')}"
        )
        view_button = dialog.addButton("查看 AI 原始响应", QMessageBox.ButtonRole.ActionRole)
        parse_button = dialog.addButton("重新解析", QMessageBox.ButtonRole.ActionRole)
        regenerate_button = dialog.addButton("重新生成", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is view_button:
            self._view_ai_raw_record(record)
        elif clicked is parse_button:
            self._reparse_ai_record(record)
        return clicked is regenerate_button

    def _view_ai_raw_record(self, record: dict) -> None:
        viewer = QDialog(self.main_window)
        viewer.setWindowTitle("AI 原始响应（已脱敏）")
        viewer.resize(760, 520)
        layout = QVBoxLayout(viewer)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(str(record.get("raw_text") or ""))
        layout.addWidget(text)
        viewer.exec()

    def _reparse_ai_record(self, record: dict) -> None:
        raw = str(record.get("raw_text") or "")
        description = getattr(self, "ai_last_request_description", "")
        task_type = getattr(self, "ai_last_request_type", "project")
        try:
            from ..ai.response_parser import parse_suggestions
            context = self.memory.build_context(description, self.current_project_id)
            validation_text = description
            if task_type == "part" and context.current_project:
                validation_text += " " + context.current_project["code"]
            result = parse_suggestions(raw, validation_text, {}, task_type, tolerant=True)
            QMessageBox.information(self.main_window, "重新解析成功", f"已解析 {len(result.suggestions)} 条候选。")
        except Exception as parse_error:
            QMessageBox.warning(self.main_window, "重新解析失败", str(parse_error))

    def _on_settings_ai_error(self, error) -> None:
        message = str(error)
        if isinstance(error, AIClientError):
            message = f"阶段：{error.stage}\n原因：{error}\n错误类型：{error.code}"
        self.main_window.settings_page.set_diagnostic(message)
        if not isinstance(error, AICancelled):
            QMessageBox.warning(self.main_window, "AI 设置测试失败", message)
        self._finish_settings_ai("取消" if isinstance(error, AICancelled) else "失败")

    def _run_ai_operation(self, title: str, operation: Callable, values: dict, on_success: Callable, on_error: Callable) -> None:
        if AITaskType.MODEL_TEST in self.ai_tasks:
            return
        thread = QThread(self.app)
        worker = AIWorker(lambda cancel, emit: operation(values, cancel, emit))
        task = AITaskState(AITaskType.MODEL_TEST, "settings", worker, thread)
        self.ai_tasks[task.task_type] = task
        self.settings_ai_worker = worker
        self.settings_ai_thread = thread
        self.ai_threads.add(thread)
        self.main_window.settings_page.set_ai_busy(True, title)
        self.main_window.settings_page.set_diagnostic(title)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(lambda result, task_id=task.task_id: on_success(result) if self._task_is_current(AITaskType.MODEL_TEST, task_id) else None)
        worker.failed.connect(lambda error, task_id=task.task_id: on_error(error) if self._task_is_current(AITaskType.MODEL_TEST, task_id) else None)
        worker.status.connect(lambda event, task_id=task.task_id: self._on_settings_ai_status(task_id, event))
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda task_id=task.task_id: self._clear_ai_task(AITaskType.MODEL_TEST, task_id))
        thread.start()

    def _start_ai_ui(self, title: str) -> None:
        import time
        self.ai_started_at = time.perf_counter()
        self.ai_metrics = {
            "model": self.settings.model_name,
            "reasoning_level": self.settings.reasoning_level,
            "thinking_enabled": self.settings.thinking_mode == "enabled",
        }
        self.ai_statistics_written = False
        self.main_window.set_ai_busy(True)
        self.main_window.clear_ai_log()
        self.main_window.set_ai_status(title)
        self.mini_window.set_ai_status(title)
        self.ai_elapsed_timer.start()
        self.main_window.ai_log_toggle.setChecked(True)

    def _finish_ai_ui(self, result=None, status: str = "成功") -> None:
        self._capture_result_metrics(result)
        self._write_ai_statistics(status)
        self.main_window.set_ai_busy(False)
        self.main_window.set_ai_status(status)
        self.mini_window.set_ai_status("空闲")
        self.ai_elapsed_timer.stop()
        task = getattr(self, "ai_tasks", {}).get(AITaskType.AI_PLANNING)
        if task:
            task.status = status
        self.ai_worker = None

    def _clear_ai_thread(self, thread: QThread) -> None:
        self.ai_threads.discard(thread)
        thread.deleteLater()
        self.ai_thread = None

    def _task_is_current(self, task_type: AITaskType, task_id: str) -> bool:
        if getattr(self, "_quitting", False):
            return False
        task = self.ai_tasks.get(task_type)
        return bool(task and task.task_id == task_id)

    def _clear_ai_task(self, task_type: AITaskType, task_id: str) -> None:
        task = self.ai_tasks.get(task_type)
        if not task or task.task_id != task_id:
            return
        self.ai_threads.discard(task.thread)
        task.thread.deleteLater()
        del self.ai_tasks[task_type]
        if task_type == AITaskType.MODEL_TEST:
            self.settings_ai_worker = None
            self.settings_ai_thread = None
        elif task_type == AITaskType.AI_CHAT:
            self.chat_ai_worker = None
            self.chat_ai_thread = None
        elif task_type == AITaskType.AI_PLANNING:
            self.ai_worker = None
            self.ai_thread = None

    def _update_ai_elapsed(self) -> None:
        import time
        self.main_window.set_ai_status(self.main_window.ai_status.text().replace("AI 状态：", ""), time.perf_counter() - self.ai_started_at)

    def _on_ai_status(self, event: StreamEvent) -> None:
        import time
        self.main_window.set_ai_status(event.message or event.kind, time.perf_counter() - self.ai_started_at)
        self.mini_window.set_ai_status(event.message or event.kind)
        if event.data:
            self.ai_metrics.update({key: value for key, value in event.data.items() if value is not None})
        if event.message:
            self._append_ai_log(event.message)

    def _on_settings_ai_status(self, task_id: str, event: StreamEvent) -> None:
        if self._task_is_current(AITaskType.MODEL_TEST, task_id):
            self.main_window.settings_page.set_diagnostic(event.message or event.kind)

    def _finish_settings_ai(self, status: str = "成功") -> None:
        task = self.ai_tasks.get(AITaskType.MODEL_TEST)
        if task:
            task.status = status
        self.main_window.settings_page.set_ai_busy(False)

    def _cancel_settings_ai(self) -> None:
        task = self.ai_tasks.get(AITaskType.MODEL_TEST)
        if task:
            task.cancel()
            self.main_window.settings_page.set_diagnostic("正在取消设置测试……")

    def _append_ai_log(self, message: str) -> None:
        from datetime import datetime
        safe_message = sanitize_log_message(message)
        self.main_window.append_ai_log(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {safe_message}")

    def _capture_result_metrics(self, result) -> None:
        if isinstance(result, AIClientError):
            self.ai_metrics.update(result.metrics)
            return
        if isinstance(result, dict):
            if result.get("balance") is not None:
                self.latest_balance = result["balance"]
            result = result.get("result") or result.get("test")
        if result is None:
            return
        actual_model = getattr(result, "actual_model", None)
        requested_model = getattr(result, "model", None) or getattr(result, "requested_model", None)
        if actual_model or requested_model:
            self.ai_metrics["model"] = actual_model or requested_model
        response_metrics = getattr(result, "response_metrics", None)
        if isinstance(response_metrics, dict):
            self.ai_metrics.update({key: value for key, value in response_metrics.items() if value is not None})
        for key in (
            "reasoning_level", "thinking_enabled", "prompt_tokens",
            "completion_tokens", "cached_tokens", "first_response_seconds", "first_text_seconds",
            "elapsed_seconds", "estimated_cost", "parse_seconds", "model_generation_seconds",
            "finish_reason", "stream_event_count", "content_chunk_count", "reasoning_chunk_count",
            "final_content_length", "stream_assembly_seconds", "server_accept_seconds",
            "reasoning_seconds", "prompt_build_seconds", "local_memory_seconds", "repair_seconds",
            "balance_seconds",
        ):
            value = getattr(result, key, None)
            if value is not None:
                self.ai_metrics[key] = value

    def _write_ai_statistics(self, status: str) -> None:
        if self.ai_statistics_written:
            return
        self.ai_statistics_written = True
        elapsed = self.ai_metrics.get("elapsed_seconds")
        if elapsed is None and self.ai_started_at:
            import time
            elapsed = time.perf_counter() - self.ai_started_at
        prompt = self.ai_metrics.get("prompt_tokens")
        completion = self.ai_metrics.get("completion_tokens")
        cached = self.ai_metrics.get("cached_tokens")
        miss = self.ai_metrics.get("cache_miss_tokens")
        if miss is None and prompt is not None and cached is not None:
            miss = max(0, prompt - cached)
        total = prompt + completion if prompt is not None and completion is not None else None
        hit_rate = (cached / prompt * 100) if prompt and cached is not None else None
        balance = self.latest_balance
        reasoning = self.ai_metrics.get("reasoning_level")
        reasoning_text = {"low": "Low", "high": "High", "max": "Max"}.get(reasoning, reasoning or "暂无数据")
        def number(value):
            return f"{int(value):,}" if value is not None else "暂无数据"
        def seconds(value):
            return f"{float(value):.2f} 秒" if value is not None else "暂无数据"
        estimated = self.ai_metrics.get("estimated_cost")
        if not estimated or estimated in ("暂无法估算", "暂无数据"):
            estimated = "暂无数据"
        balance_text = "暂无数据"
        if balance is not None and balance.total_balance:
            balance_text = f"{balance.currency} {balance.total_balance}"
        block = "\n".join((
            "────────────────────────", "【本次 AI 使用统计】", f"状态：{status}",
            f"模型：{self.ai_metrics.get('model') or '暂无数据'}", f"推理级别：{reasoning_text}",
            f"输入 Token：{number(prompt)}", f"输出 Token：{number(completion)}",
            f"缓存命中 Token：{number(cached)}", f"缓存未命中 Token：{number(miss)}",
            f"总 Token：{number(total)}", f"缓存命中率：{f'{hit_rate:.2f}%' if hit_rate is not None else '暂无数据'}",
            f"预计费用：{estimated}", f"API 剩余余额：{balance_text}",
            f"首个响应耗时：{seconds(self.ai_metrics.get('first_response_seconds'))}",
            f"首个文字耗时：{seconds(self.ai_metrics.get('first_text_seconds'))}",
            f"模型生成耗时：{seconds(self.ai_metrics.get('model_generation_seconds'))}",
            f"解析耗时：{seconds(self.ai_metrics.get('parse_seconds'))}",
            f"完成原因：{self.ai_metrics.get('finish_reason') or '暂无数据'}",
            f"流式事件：{number(self.ai_metrics.get('stream_event_count'))}，正文块：{number(self.ai_metrics.get('content_chunk_count'))}，推理块：{number(self.ai_metrics.get('reasoning_chunk_count'))}",
            f"最终正文长度：{number(self.ai_metrics.get('final_content_length'))}",
            f"流式拼接耗时：{seconds(self.ai_metrics.get('stream_assembly_seconds'))}",
            f"总耗时：{seconds(elapsed)}", "────────────────────────",
        ))
        self.main_window.append_ai_log(sanitize_log_message(block))

    def _cancel_ai(self) -> None:
        task = self.ai_tasks.get(AITaskType.AI_PLANNING)
        if task:
            task.cancel()
            self.main_window.set_ai_status("取消中……")

    def _memory_action(self, action: str) -> None:
        if action == "clear_learning":
            answer = QMessageBox.question(self.main_window, "清空 AI 学习记录", "只清空纠偏学习，不删除正式项目和图号记录，是否继续？")
            if answer == QMessageBox.StandardButton.Yes:
                self.memory.clear_learning()
        elif action == "reset_rules":
            self.memory.reset_rules()
        elif action == "edit_rule":
            rule = next((item for item in self.memory.rules() if item["rule_key"] == "name_style"), None)
            if rule:
                value, ok = QInputDialog.getMultiLineText(self.main_window, "编辑名称规则", "名称规则：", rule["content"])
                if ok and value.strip():
                    self.memory.update_rule("name_style", value.strip())
        elif action == "delete_feedback":
            feedback_id, ok = QInputDialog.getInt(self.main_window, "删除纠偏记录", "纠偏记录 ID：", 1, 1)
            if ok:
                self.memory.delete_feedback(feedback_id)
        elif action == "toggle_project_reference":
            if self.current_project_id is None:
                QMessageBox.information(self.main_window, "尚无项目", "请先选择当前项目。")
            else:
                current = self.memory.project_reference_allowed(self.current_project_id)
                self.memory.set_project_reference(self.current_project_id, not current)
        elif action == "toggle_archived_reference":
            allowed = self.main_window.settings_page.reference_archived_projects.isChecked()
            self.memory.set_archived_reference(allowed)
        elif action in {"view_skill", "view_project_memory", "view_feedback_memory", "view_raw_response"}:
            self.memory_package.generate()
            paths = {
                "view_skill": self.memory_package.skill_path,
                "view_project_memory": self.memory_package.directory / "project_code_memory.json",
                "view_feedback_memory": self.memory_package.directory / "naming_examples.jsonl",
                "view_raw_response": self.memory_package.response_log_path,
            }
            target = paths[action]
            if not target.exists():
                QMessageBox.information(self.main_window, "暂无记录", "当前还没有可查看的记录。")
            else:
                result = open_local_path(target, "file")
                if not result.ok:
                    show_local_path_error(self.main_window, result, "记录文件")
        elif action == "regenerate_skill":
            result = self.memory_package.generate(backup=True)
            QMessageBox.information(self.main_window, "生成完成", f"AI 命名技能已从 SQLite 重新生成：\n{result['directory']}")
        elif action == "export_memory":
            default = str(Path.home() / f"机械命名记忆包_{__import__('datetime').datetime.now():%Y%m%d_%H%M%S}.zip")
            target, _ = QFileDialog.getSaveFileName(self.main_window, "导出 AI 命名记忆包", default, "ZIP 文件 (*.zip)")
            if target:
                exported = self.memory_package.export_zip(Path(target))
                QMessageBox.information(self.main_window, "导出完成", f"AI 命名记忆包已导出：\n{exported}")
        elif action == "import_memory":
            source, _ = QFileDialog.getOpenFileName(self.main_window, "导入 AI 命名记忆包", str(Path.home()), "ZIP 文件 (*.zip)")
            if source:
                try:
                    preview = self.memory_package.inspect_zip(Path(source))
                    QMessageBox.information(
                        self.main_window, "记忆包内容预览",
                        f"更新时间：{preview['updated_at'] or '暂无数据'}\n固定规则：{preview['rule_count']} 条\n"
                        f"命名案例：{preview['example_count']} 条\n项目代号记忆：{preview['project_code_count']} 条\n\n"
                        f"正式项目和图号不会被覆盖或删除。",
                    )
                    label, ok = QInputDialog.getItem(
                        self.main_window, "选择导入方式", "导入方式：",
                        ["合并", "仅导入固定规则", "仅导入命名偏好"], 0, False,
                    )
                    if ok:
                        mode = {"合并": "merge", "仅导入固定规则": "rules", "仅导入命名偏好": "preferences"}[label]
                        result = self.memory_package.import_zip(Path(source), mode)
                        QMessageBox.information(
                            self.main_window, "导入完成",
                            f"新增规则：{result['rules']} 条\n新增命名案例：{result['examples']} 条\n"
                            f"合并项目代号记忆：{result['project_codes']} 条",
                        )
                except MemoryPackageError as error:
                    QMessageBox.warning(self.main_window, "记忆包无效", str(error))
        self._refresh_memory_summary()

    def _refresh_memory_summary(self) -> None:
        package = self.memory_package.generate()
        rules = self.memory.rules()
        feedback = self.memory.feedback()
        preferences = self.memory.preference_summary()
        summary = (
            f"固定命名规则：{package['rule_count']} 条\n历史项目：{package['project_count']} 个\n"
            f"正式零件名称：{package['formal_name_count']} 个\n用户纠偏：{package['feedback_count']} 条\n"
            f"拒绝建议：{package['rejected_count']} 条\n最后更新时间：{package['updated_at']}\n"
            f"记忆目录：{package['directory']}\n\n基础规则\n"
            + "\n".join(f"{item['title']}：{item['content']}" for item in rules)
        )
        summary += f"\n\n名称偏好\n项目代号平均长度：{preferences['average_code_length']}\n带连字符代号：{preferences['hyphen_style_count']} 个\n常用名称后缀：{preferences['common_suffixes']}"
        summary += f"\n\n纠偏记录：{len(feedback)} 条\n"
        summary += "\n".join(f"ID {item['id']} · {item['action']} · 使用 {item['use_count']} 次 · AI：{item['ai_name']} · 最终：{item['final_name']}" for item in feedback[:30])
        if self.current_project_id is not None:
            summary += f"\n\n当前项目允许 AI 参考：{'是' if self.memory.project_reference_allowed(self.current_project_id) else '否'}"
        self.main_window.settings_page.set_memory_summary(summary)

    def _request_ai(self, description: str, task_type: str = "project") -> None:
        description = description.strip()
        if not description:
            QMessageBox.warning(self.main_window, "缺少设备描述", "请先输入设备用途、加工对象或主要参数。")
            return
        try:
            api_key = self.credential_store.get_api_key()
        except Exception as error:
            QMessageBox.warning(self.main_window, "凭据不可用", str(error))
            return
        if not api_key:
            QMessageBox.information(self.main_window, "尚未配置 AI", "请在设置中配置 API 地址、模型名称和 API Key。")
            self.main_window.show_page(3)
            return
        if task_type == "part" and self.current_project_id is None:
            QMessageBox.warning(self.main_window, "尚无当前项目", "零件命名建议需要先选择当前项目。")
            return
        self.ai_last_request_description = description
        self.ai_last_request_type = task_type
        def operation(cancel, emit):
            emit(StreamEvent("task", "AI 命名任务开始"))
            emit(StreamEvent("skill", "正在读取 AI 命名技能"))
            package = self.memory_package.generate()
            emit(StreamEvent("skill", f"AI 命名技能已从 SQLite 刷新：{package['rule_count']} 条固定规则"))
            emit(StreamEvent("rules", "正在读取固定命名规则"))
            context = self.memory.build_context(description, self.current_project_id)
            emit(StreamEvent("rules", f"已加载固定规则：{package['rule_count']} 条"))
            if context.current_project:
                emit(StreamEvent("project", f"当前项目加载完成：{context.current_project['name']}（{context.current_project['code']}）"))
            if task_type == "part":
                emit(StreamEvent("module", f"当前模块：{context.module_code or '未能确定'}"))
                emit(StreamEvent("part_type", f"零件类别：{context.part_type or '未能确定'}"))
                next_text = f"{context.next_part_number:03d}" if context.next_part_number is not None else "暂无"
                emit(StreamEvent("numbering", f"本地下一个可用编号：{next_text}（不交给 AI 决定）"))
            emit(StreamEvent("history_projects", f"检索历史项目完成：找到 {len(context.similar_projects)} 个相关项目"))
            emit(StreamEvent("history_parts", f"检索历史零件完成：找到 {len(context.similar_parts)} 个类似零件"))
            emit(StreamEvent("feedback", f"检索用户纠偏完成：找到 {len(context.feedback)} 条高相关用户纠偏记录"))
            emit(StreamEvent("preferences", "本地命名偏好摘要生成完成"))
            emit(StreamEvent("prompt", "AI 命名上下文构建完成"))
            client = AIClient(
                self.settings.api_url, self.settings.model_name, api_key,
                connect_timeout=self.settings.connect_timeout,
                request_timeout=self.settings.request_timeout,
                generation_timeout=self.settings.generation_timeout,
                thinking_enabled=self.settings.thinking_mode == "enabled",
                reasoning_level=self.settings.reasoning_level,
                auto_repair_enabled=self.settings.json_auto_repair,
                response_audit=self.response_audit,
            )
            emit(StreamEvent("network", "正在连接 DeepSeek"))
            result = client.suggest_stream(description, context, cancel, task_type, emit)
            emit(StreamEvent("parsed", "结构化解析成功"))
            if result.repair_used:
                emit(StreamEvent("parsed", "三级容错已使用一次 JSON 修复请求"))
            if result.response_record_id:
                emit(StreamEvent("audit", f"AI 原始响应已脱敏保存：记录 {result.response_record_id}"))
            balance = None
            try:
                balance = client.balance()
                emit(StreamEvent("balance", "API 余额读取完成"))
            except AIClientError as error:
                emit(StreamEvent("balance_warning", f"余额读取失败，不影响命名结果：{error}"))
            return {"result": result, "balance": balance, "context": context}

        def show_result(outcome) -> None:
            result = outcome["result"]
            context = outcome["context"]
            plan_id = None
            if task_type == "project":
                plan_id = self.repository.save_ai_plan(
                    prompt=description, result=result, project_id=self.current_project_id,
                    status="success", plan_type=task_type,
                    raw_response_reference=result.response_record_id,
                )
            self._append_ai_log("AI 命名任务完成，正在等待用户采用、修改或拒绝")
            dialog = AISuggestionsDialog(result, self.main_window, task_type=task_type)
            dialog.exec()
            if dialog.selected_code:
                suggestion = dialog.selected_suggestion
                original = suggestion.recommended_name if task_type == "part" and suggestion else dialog.selected_code
                final = dialog.selected_name if task_type == "part" else dialog.selected_code
                module_code = context.module_code or (suggestion.module_code if suggestion else "")
                self.memory.record_feedback(
                    self.current_project_id, module_code, original, final,
                    dialog.selected_action or "adopted",
                    dialog.selected_name if dialog.selected_action == "rejected" else "",
                    description,
                )
                self._append_ai_log(f"用户反馈已保存：{dialog.selected_action or 'adopted'}")
                if dialog.selected_action != "rejected" and task_type == "part" and suggestion:
                    # AI adoption only fills the editable name field. Numbering
                    # and persistence remain the explicit Save and Copy action.
                    chosen_name = final or original
                    self.main_window.part_name.setText(chosen_name)
                    self.mini_window.part_name.setText(chosen_name)
                    self.main_window.part_name.setFocus()
                    self.notify("已采用零件名称建议", chosen_name)
                elif dialog.selected_action != "rejected":
                    if task_type == "project" and dialog.selected_target == "current_project" and self.current_project_id:
                        selected = [{"module_code": item.get("module_code"), "module_name": item.get("module_name")} for item in result.module_recommendations if item.get("module_code") in dialog.selected_modules]
                        sync = self.repository.adopt_modules(self.current_project_id, selected)
                        self.refresh_all()
                        self.notify("AI模块已同步", f"新增 {len(sync['added'])} 个模块，冲突 {len(sync['conflicts'])} 个")
                    else:
                        self._create_ai_project(dialog.selected_code, description, dialog.selected_modules)
                    if plan_id is not None:
                        self.repository.mark_ai_plan_adopted(plan_id, self.current_project_id)
                    self.memory.refresh_package()
            regenerate = dialog.regenerate_requested
            self._finish_ai_ui(outcome)
            if regenerate:
                QTimer.singleShot(0, lambda: self._request_ai(description, task_type))

        self._run_custom_ai(operation, show_result, self._on_ai_error)

    def _create_ai_project(self, code: str, description: str, selected_modules: list[str]) -> None:
        """Create a planned project and its selected modules in one action."""
        cleaned = " ".join(description.strip().split())
        name = re.split(r"[，。！？,!?]", cleaned, maxsplit=1)[0].strip()[:80] or f"{code} 项目"
        enabled = set(selected_modules or {item[0] for item in MODULES})
        try:
            project = self.project_service.create(name=name, description=description.strip(), code=code)
            selected = [(item[0], item[1]) for item in MODULES if item[0] in enabled]
            self.repository.adopt_modules(project.id, [{"module_code": item[0], "module_name": item[1]} for item in selected])
            self.current_project_id = project.id
            self.current_module_code = next(iter(sorted(enabled)), "03")
            self.current_part_type = "自制机加工件"
            self.settings.current_project_id = project.id
            self.refresh_all()
            self.main_window.show_page(1)
            self.notify("AI 项目已创建", f"{project.name} · {project.code}")
        except ValidationError as error:
            QMessageBox.warning(self.main_window, "AI 项目未创建", str(error))

    def _run_custom_ai(self, operation: Callable, on_success: Callable, on_error: Callable) -> None:
        if AITaskType.AI_PLANNING in self.ai_tasks:
            return
        self._start_ai_ui("正在准备 AI 请求……")
        thread = QThread(self.app)
        worker = AIWorker(operation)
        task = AITaskState(AITaskType.AI_PLANNING, "planning", worker, thread)
        self.ai_tasks[task.task_type] = task
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(lambda result, task_id=task.task_id: on_success(result) if self._task_is_current(AITaskType.AI_PLANNING, task_id) else None)
        worker.failed.connect(lambda error, task_id=task.task_id: on_error(error) if self._task_is_current(AITaskType.AI_PLANNING, task_id) else None)
        worker.status.connect(lambda event, task_id=task.task_id: self._on_ai_status(event) if self._task_is_current(AITaskType.AI_PLANNING, task_id) else None)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda task_id=task.task_id: self._clear_ai_task(AITaskType.AI_PLANNING, task_id))
        self.ai_worker = worker
        self.ai_thread = thread
        self.ai_threads.add(thread)
        thread.start()

    def _run_chat_ai(self, operation: Callable, on_success: Callable, on_error: Callable, model: str) -> None:
        if AITaskType.AI_CHAT in self.ai_tasks:
            return
        thread = QThread(self.app)
        worker = AIWorker(operation)
        task = AITaskState(AITaskType.AI_CHAT, "design_chat", worker, thread)
        self.ai_tasks[task.task_type] = task
        self.chat_ai_worker = worker
        self.chat_ai_thread = thread
        self.chat_ai_started_at = task.started_at
        self.chat_ai_model = model
        self.ai_threads.add(thread)
        self.chat_elapsed_timer.start()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(lambda result, task_id=task.task_id: on_success(result) if self._task_is_current(AITaskType.AI_CHAT, task_id) else None)
        worker.failed.connect(lambda error, task_id=task.task_id: on_error(error) if self._task_is_current(AITaskType.AI_CHAT, task_id) else None)
        worker.status.connect(lambda event, task_id=task.task_id: self._on_chat_ai_status(task_id, event))
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda task_id=task.task_id: self._clear_ai_task(AITaskType.AI_CHAT, task_id))
        thread.start()

    def _on_chat_ai_status(self, task_id: str, event: StreamEvent) -> None:
        if not self._task_is_current(AITaskType.AI_CHAT, task_id) or not self.design_chat:
            return
        import time
        self.design_chat.set_status(
            event.message or event.kind, self.chat_ai_model,
            time.perf_counter() - self.chat_ai_started_at,
        )

    def _update_chat_ai_elapsed(self) -> None:
        task = self.ai_tasks.get(AITaskType.AI_CHAT)
        if not task or not self.design_chat:
            return
        import time
        current = self.design_chat.status_label.text().split("    当前模型：", 1)[0].replace("AI状态：", "")
        self.design_chat.set_status(current, self.chat_ai_model, time.perf_counter() - task.started_at)

    def _finish_chat_ai(self, _result=None, status: str = "成功") -> None:
        task = self.ai_tasks.get(AITaskType.AI_CHAT)
        if task:
            task.status = status
        self.chat_elapsed_timer.stop()
        if self.design_chat:
            import time
            elapsed = time.perf_counter() - task.started_at if task else 0.0
            self.design_chat.set_busy(False)
            self.design_chat.set_status(status, self.chat_ai_model, elapsed)

    def _cancel_chat_ai(self) -> None:
        task = self.ai_tasks.get(AITaskType.AI_CHAT)
        if task:
            task.cancel()
            if self.design_chat:
                self.design_chat.set_status("取消中……", self.chat_ai_model)

    def _build_tray_menu(self) -> QMenu:
        menu = QMenu()
        for label, callback in (("显示完整窗口", self.show_full), ("显示 Mini 窗格", self.show_mini)):
            action = QAction(label, menu)
            action.triggered.connect(callback)
            menu.addAction(action)
        current_menu = menu.addMenu("当前项目")
        projects = self.repository.list_projects()
        if not projects:
            empty = QAction("尚无项目", current_menu)
            empty.setEnabled(False)
            current_menu.addAction(empty)
        for project in projects:
            action = QAction(f"{project.name} · {project.code}", current_menu)
            action.setCheckable(True)
            action.setChecked(project.id == self.current_project_id)
            action.triggered.connect(lambda _checked=False, project_id=project.id: self._select_project(project_id))
            current_menu.addAction(action)
        project_action = QAction("项目管理", menu)
        project_action.triggered.connect(lambda: (self.show_full(), self.main_window.show_page(0)))
        menu.addAction(project_action)
        settings_action = QAction("设置", menu)
        settings_action.triggered.connect(lambda: (self.show_full(), self.main_window.show_page(3)))
        menu.addAction(settings_action)
        logs_action = QAction("查看日志", menu)
        logs_action.triggered.connect(self._open_logs)
        menu.addAction(logs_action)
        menu.addSeparator()
        exit_action = QAction("退出软件", menu)
        exit_action.triggered.connect(self.quit)
        menu.addAction(exit_action)
        return menu

    def _open_logs(self) -> None:
        result = open_local_path(application_log_path(), "file")
        if not result.ok:
            show_local_path_error(self.main_window, result, "应用日志文件")

    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_last()

    def _capture_geometry(self) -> None:
        self.settings.main_geometry = geometry_to_text(self.main_window)
        self.settings.mini_geometry = geometry_to_text(self.mini_window)

    def _save_settings(self) -> None:
        try:
            save_settings(self.settings)
        except Exception:
            LOGGER.exception("设置保存失败")

    def _active_window(self):
        return self.mini_window if self.mini_window.isVisible() else self.main_window
