# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import time
from collections.abc import Callable
from threading import Event, Lock

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from .client import AICancelled, AIClientError
from .models import StreamEvent


class CancellationToken(Event):
    """Wake blocked HTTP reads by closing the registered response on cancel."""

    def __init__(self):
        super().__init__()
        self._callbacks = set()
        self._callback_lock = Lock()

    def add_callback(self, callback):
        with self._callback_lock:
            cancelled = self.is_set()
            if not cancelled:
                self._callbacks.add(callback)
        if cancelled:
            callback()

    def remove_callback(self, callback):
        with self._callback_lock:
            self._callbacks.discard(callback)

    def set(self):
        super().set()
        with self._callback_lock:
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                # The worker owns final cleanup and error reporting.
                pass


class AIWorker(QObject):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(object)
    status = pyqtSignal(object)
    finished = pyqtSignal()

    def __init__(self, operation: Callable[[Event, Callable[[StreamEvent], None]], object]) -> None:
        super().__init__()
        self.operation = operation
        self.cancel_event = CancellationToken()
        self.started_at = 0.0

    @pyqtSlot()
    def run(self) -> None:
        self.started_at = time.perf_counter()
        self.status.emit(StreamEvent("started", "正在准备 AI 请求……"))
        try:
            result = self.operation(self.cancel_event, self._emit)
            self.succeeded.emit(result)
        except AICancelled as error:
            self.failed.emit(error)
        except Exception as error:
            self.failed.emit(error)
        finally:
            self.finished.emit()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.status.emit(StreamEvent("cancel_requested", "正在取消请求……"))

    def _emit(self, event: StreamEvent) -> None:
        self.status.emit(event)

