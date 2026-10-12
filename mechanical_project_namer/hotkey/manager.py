# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import ctypes
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from typing import Protocol

from PyQt6.QtCore import QAbstractNativeEventFilter


WM_HOTKEY = 0x0312
MODIFIERS = {"ALT": 0x0001, "CTRL": 0x0002, "SHIFT": 0x0004, "WIN": 0x0008}


class HotkeyError(RuntimeError):
    pass


class HotkeyBackend(Protocol):
    def register(self, hotkey_id: int, modifiers: int, key: int) -> bool: ...
    def unregister(self, hotkey_id: int) -> None: ...


@dataclass(frozen=True, slots=True)
class ParsedHotkey:
    modifiers: int
    key: int


def parse_hotkey(value: str) -> ParsedHotkey:
    parts = [part.strip().upper() for part in value.split("+") if part.strip()]
    if len(parts) < 2:
        raise HotkeyError("快捷键必须包含修饰键和字母或数字键。")
    key_name = parts[-1]
    modifier_names = parts[:-1]
    if key_name not in list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"):
        raise HotkeyError("快捷键主键仅支持 A-Z 或 0-9。")
    if any(name not in MODIFIERS for name in modifier_names):
        raise HotkeyError("快捷键包含不支持的修饰键。")
    modifiers = 0
    for name in set(modifier_names):
        modifiers |= MODIFIERS[name]
    return ParsedHotkey(modifiers, ord(key_name))


class WindowsHotkeyBackend:
    def register(self, hotkey_id: int, modifiers: int, key: int) -> bool:
        return bool(ctypes.windll.user32.RegisterHotKey(None, hotkey_id, modifiers, key))

    def unregister(self, hotkey_id: int) -> None:
        ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)


class HotkeyRegistrationManager:
    def __init__(self, backend: HotkeyBackend, hotkey_id: int = 0x4D50) -> None:
        self.backend = backend
        self.current_id = hotkey_id
        self.current_value: str | None = None

    def register(self, value: str) -> None:
        parsed = parse_hotkey(value)
        previous = self.current_value
        if previous is not None:
            self.backend.unregister(self.current_id)
        if self.backend.register(self.current_id, parsed.modifiers, parsed.key):
            self.current_value = value
            return
        if previous is not None:
            old = parse_hotkey(previous)
            self.backend.register(self.current_id, old.modifiers, old.key)
            self.current_value = previous
        raise HotkeyError(f"快捷键 {value} 注册失败，已恢复原快捷键。")

    def unregister(self) -> None:
        if self.current_value is not None:
            self.backend.unregister(self.current_id)
            self.current_value = None


class GlobalHotkey(QAbstractNativeEventFilter):
    def __init__(self, handler: Callable[[], None] | None = None, backend: HotkeyBackend | None = None) -> None:
        super().__init__()
        self.registration = HotkeyRegistrationManager(backend or WindowsHotkeyBackend())
        self.handler = handler

    def register(self, value: str) -> None:
        self.registration.register(value)

    def unregister(self) -> None:
        self.registration.unregister()

    def nativeEventFilter(self, event_type, message):  # type: ignore[override]
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam == self.registration.current_id:
            if self.handler:
                self.handler()
            return True, 0
        return False, 0

