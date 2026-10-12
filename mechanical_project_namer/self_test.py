# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166
"""Explicit offline packaged-runtime check; never reads production state or credentials."""
import json
import os
from pathlib import Path
import tempfile


def run(report: str) -> int:
    target = Path(report).resolve()
    if target.exists():
        raise ValueError("Self-test report must be a new file")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    checks = []
    try:
        from PyQt6.QtWidgets import QApplication, QLabel
        from PyQt6.QtPrintSupport import QPrinter
        from PyQt6.QtGui import QPainter
        import httpx
        import openpyxl
        import pythoncom
        import win32com.client
        from keyring.backends.Windows import WinVaultKeyring
        from .data import Database, Repository, DataExporter
        from .projects import ProjectService
        from .numbering.workflow import NamingWorkflow
        checks.append("optional_dependency_imports")
        with tempfile.TemporaryDirectory(prefix="mpn-self-test-", dir=target.parent) as tmp:
            base = Path(tmp)
            database = Database(base / "test.db")
            database.migrate()
            repo = Repository(database)
            assert not repo.list_projects()
            checks.append("first_run_empty_database")
            project = ProjectService(repo).create("演示设备", "离线自检", "DEMO")
            workflow = NamingWorkflow(repo, DataExporter(repo, base / "exports"))
            assert workflow.preview_named(project.id, "01", "自制机加工件") == "DEMO-01-001"
            record = workflow.save_named_and_advance(project.id, "01", "自制机加工件", "支撑座")
            assert record is not None
            assert workflow.preview_named(project.id, "01", "自制机加工件") == "DEMO-01-002"
            checks.append("name_and_sequence")
            app = QApplication([])
            label = QLabel("Offline packaging self-test")
            label.resize(300, 80)
            label.show()
            app.processEvents()
            assert not label.grab().isNull()
            checks.append("qt_offscreen_widget")
            printer = QPrinter()
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(base / "test.pdf"))
            painter = QPainter(printer)
            painter.drawText(20, 40, "Offline self-test")
            painter.end()
            assert (base / "test.pdf").stat().st_size > 0
            book = openpyxl.Workbook()
            book.active.append(["DEMO-01-001", "支撑座"])
            book.save(base / "test.xlsx")
            checks.append("pdf_and_excel")
            label.close()
            app.quit()
        result = {"passed": True, "checks": checks, "network_calls": 0,
                  "credential_access": False, "solidworks_access": False,
                  "interactive_gui_verified": False}
        code = 0
    except Exception as error:
        result = {"passed": False, "checks": checks, "error_type": type(error).__name__}
        code = 1
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return code
