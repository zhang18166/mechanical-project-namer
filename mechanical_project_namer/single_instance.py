# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from pathlib import Path

from PyQt6.QtCore import QByteArray, QLockFile, QObject, QStandardPaths, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance(QObject):
    show_requested = pyqtSignal()

    def __init__(self, name: str = "MechanicalProjectNamer.SingleInstance") -> None:
        super().__init__()
        temp_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation))
        self._name = name
        self._lock = QLockFile(str(temp_dir / f"{name}.lock"))
        self._lock.setStaleLockTime(30_000)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._receive)

    def start_or_notify(self) -> bool:
        if not self._lock.tryLock(0):
            client = QLocalSocket(self)
            client.connectToServer(self._name)
            if client.waitForConnected(1_000):
                client.write(QByteArray(b"show"))
                client.waitForBytesWritten(500)
            return False
        QLocalServer.removeServer(self._name)
        if self._server.listen(self._name):
            return True
        self._lock.unlock()
        return False

    def release(self) -> None:
        self._server.close()
        self._lock.unlock()

    def _receive(self) -> None:
        client = self._server.nextPendingConnection()
        if client is not None:
            client.waitForReadyRead(250)
            client.readAll()
            client.disconnectFromServer()
        self.show_requested.emit()

