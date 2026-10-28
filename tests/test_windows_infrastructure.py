# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from mechanical_project_namer.hotkey import HotkeyError, HotkeyRegistrationManager, parse_hotkey
from mechanical_project_namer.settings import AppSettings, load_settings, save_settings


class FakeBackend:
    def __init__(self):
        self.registered = []
        self.unregistered = []
        self.fail_key = None

    def register(self, hotkey_id, modifiers, key):
        self.registered.append((hotkey_id, modifiers, key))
        return key != self.fail_key

    def unregister(self, hotkey_id):
        self.unregistered.append(hotkey_id)


def test_hotkey_registration_failure_restores_previous():
    backend = FakeBackend()
    manager = HotkeyRegistrationManager(backend)
    manager.register("Ctrl+Alt+N")
    backend.fail_key = ord("M")
    try:
        manager.register("Ctrl+Alt+M")
    except HotkeyError:
        pass
    assert manager.current_value == "Ctrl+Alt+N"
    assert backend.registered[-1][2] == ord("N")


def test_hotkey_parser_accepts_default_and_rejects_invalid():
    parsed = parse_hotkey("Ctrl+Alt+N")
    assert parsed.key == ord("N")
    try:
        parse_hotkey("Ctrl+F12")
    except HotkeyError:
        pass
    else:
        raise AssertionError("F12 should not be accepted by this version")


def test_window_mode_and_geometry_are_remembered(tmp_path):
    target = tmp_path / "settings.json"
    settings = AppSettings(last_window_mode="mini", main_geometry="main", mini_geometry="mini")
    save_settings(settings, target)
    loaded = load_settings(target)
    assert loaded.last_window_mode == "mini"
    assert loaded.main_geometry == "main"
    assert loaded.mini_geometry == "mini"
    assert "api_key" not in target.read_text(encoding="utf-8").lower()
