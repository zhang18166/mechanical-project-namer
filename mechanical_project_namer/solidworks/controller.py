# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json
import logging
import sys
import tempfile
import uuid
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal

from ..logging_setup import solidworks_logger
from ..safe_fs import remove_file
from .errors import SolidWorksError
from .models import OperationRequest, OperationResult


LOGGER = logging.getLogger(__name__)


class SolidWorksWorkerController(QObject):
    progress = pyqtSignal(str, str)
    completed = pyqtSignal(object)
    failed = pyqtSignal(object)
    busy_changed = pyqtSignal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.process: QProcess | None = None
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self._on_timeout)
        self.request_path: Path | None = None
        self.result_path: Path | None = None
        self._buffer = b""
        self._timed_out = False

    @property
    def busy(self) -> bool:
        return self.process is not None

    def start(self, request: OperationRequest, timeout_seconds: int) -> None:
        if self.busy:
            raise SolidWorksError("already_running", "已有SOLIDWORKS创建任务正在运行。", "worker")
        runtime = Path(tempfile.gettempdir()) / "MechanicalProjectNamer" / "solidworks_worker"
        runtime.mkdir(parents=True, exist_ok=True)
        token = request.operation_id or str(uuid.uuid4())
        self.request_path = runtime / f"{token}.request.json"
        self.result_path = runtime / f"{token}.result.json"
        request.result_path = str(self.result_path)
        self.request_path.write_text(json.dumps(request.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        process = QProcess(self)
        self.process = process
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        environment.insert("PYTHONIOENCODING", "utf-8")
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_output)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_process_error)
        if getattr(sys, "frozen", False):
            program, arguments = sys.executable, ["--solidworks-worker", str(self.request_path)]
        else:
            program = sys.executable
            arguments = ["-m", "mechanical_project_namer.solidworks.worker_entry", "--solidworks-worker", str(self.request_path)]
        self._buffer = b""
        self._timed_out = False
        solidworks_logger().info("[%s] [worker] 正在启动SOLIDWORKS辅助进程", request.operation_id)
        self.busy_changed.emit(True)
        process.start(program, arguments)
        self.timeout.start(max(1, int(timeout_seconds)) * 1000)

    def cancel(self) -> None:
        if not self.process:
            return
        self.timeout.stop()
        self.process.kill()
        self.process.waitForFinished(3000)
        self._finish_error(SolidWorksError("worker_cancelled", "用户已取消SOLIDWORKS任务。", "worker"))

    def _read_output(self) -> None:
        if not self.process:
            return
        self._buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self._buffer:
            raw, self._buffer = self._buffer.split(b"\n", 1)
            try:
                event = json.loads(raw.decode("utf-8", errors="replace"))
            except (ValueError, UnicodeError):
                LOGGER.warning("忽略非JSON的SOLIDWORKS Worker输出：%r", raw[:240])
                continue
            if event.get("type") == "progress":
                stage = str(event.get("stage", "worker"))
                message = str(event.get("message", ""))
                operation_id = str(event.get("operation_id", ""))
                solidworks_logger().info("[%s] [%s] %s", operation_id, stage, message)
                self.progress.emit(stage, message)

    def _on_timeout(self) -> None:
        if not self.process:
            return
        self._timed_out = True
        self.process.kill()

    def _on_finished(self, _exit_code: int, _exit_status) -> None:
        self.timeout.stop()
        if self._timed_out:
            self._finish_error(SolidWorksError("worker_timeout", "SOLIDWORKS Worker超过整体超时，已安全终止辅助进程。", "worker"))
            return
        try:
            if not self.result_path or not self.result_path.is_file():
                raise SolidWorksError("worker_no_result", "SOLIDWORKS Worker未返回结果文件。", "worker")
            result = OperationResult.from_dict(json.loads(self.result_path.read_text(encoding="utf-8")))
            if result.success:
                solidworks_logger().info("[%s] [%s] %s", result.operation_id, result.stage, result.message)
                self._cleanup()
                self.completed.emit(result)
            else:
                self._finish_error(SolidWorksError(result.error_code or "worker_failed", result.message, result.stage))
        except Exception as error:
            self._finish_error(error if isinstance(error, SolidWorksError) else SolidWorksError("worker_result_invalid", str(error), "worker"))

    def _on_process_error(self, _error) -> None:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            return
        if not self._timed_out:
            self._finish_error(SolidWorksError("worker_start_failed", "无法启动SOLIDWORKS辅助进程。", "worker"))

    def _finish_error(self, error: Exception) -> None:
        solidworks_logger().error("SOLIDWORKS任务失败：%s", error)
        self._cleanup()
        self.failed.emit(error)

    def _cleanup(self) -> None:
        self.timeout.stop()
        process, self.process = self.process, None
        if process:
            process.deleteLater()
        for path in (self.request_path, self.result_path):
            if path:
                remove_file(path, what="SOLIDWORKS worker 临时文件", logger=LOGGER)
        self.request_path = None
        self.result_path = None
        self.busy_changed.emit(False)
