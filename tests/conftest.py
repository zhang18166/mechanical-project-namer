# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""All tests use a disposable profile; real integrations fail closed."""
import os
import tempfile

import pytest


@pytest.fixture(scope="session", autouse=True)
def isolated_profile(tmp_path_factory):
    profile = tmp_path_factory.mktemp("isolated-profile")
    patch = pytest.MonkeyPatch()
    for key, name in (("LOCALAPPDATA", "local"), ("APPDATA", "roaming"), ("TEMP", "temp"), ("TMP", "temp")):
        folder = profile / name
        folder.mkdir(exist_ok=True)
        patch.setenv(key, str(folder))
    patch.delenv("MECHANICAL_PROJECT_NAMER_DEV_ROOT", raising=False)
    patch.setenv("QT_QPA_PLATFORM", "offscreen")
    patch.setattr(tempfile, "tempdir", None)
    yield profile
    patch.undo()


@pytest.fixture(autouse=True)
def no_live_integrations(monkeypatch, isolated_profile):
    import socket
    import pythoncom
    import win32com.client
    from keyring.backends.Windows import WinVaultKeyring
    from mechanical_project_namer.hotkey.manager import WindowsHotkeyBackend

    def denied(*args, **kwargs):
        raise AssertionError("Tests prohibit real network, Windows credentials, hotkeys and COM")

    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(WinVaultKeyring, name, denied)
    monkeypatch.setattr(pythoncom, "GetActiveObject", denied)
    monkeypatch.setattr(win32com.client, "Dispatch", denied)
    monkeypatch.setattr(win32com.client, "GetActiveObject", denied)
    monkeypatch.setattr(WindowsHotkeyBackend, "register", denied)
