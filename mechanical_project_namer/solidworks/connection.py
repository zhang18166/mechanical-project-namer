# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from .errors import SolidWorksError


TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_INTEGRITY_LEVEL = 25
TOKEN_ELEVATION = 20


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
    ]


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    process_id: int
    executable: str


def find_solidworks_processes() -> list[ProcessInfo]:
    if not hasattr(ctypes, "windll"):
        return []
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    found: list[ProcessInfo] = []
    try:
        success = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while success:
            if entry.szExeFile.casefold() == "sldworks.exe":
                process_id = int(entry.th32ProcessID)
                found.append(ProcessInfo(process_id, _query_process_path(process_id) or entry.szExeFile))
            success = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def _query_process_path(process_id: int) -> str:
    if not hasattr(ctypes, "windll"):
        return ""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not process:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(process)


def process_file_version(executable: str) -> str:
    try:
        import win32api

        info = win32api.GetFileVersionInfo(executable, "\\")
        major, minor = divmod(info["FileVersionMS"], 65536)
        patch, build = divmod(info["FileVersionLS"], 65536)
        return f"{major}.{minor}.{patch}.{build:04d}"
    except Exception:
        return ""


def require_single_process(processes: list[ProcessInfo] | None = None) -> ProcessInfo:
    items = find_solidworks_processes() if processes is None else processes
    if not items:
        raise SolidWorksError(
            "not_running", "未检测到正在运行的SOLIDWORKS。\n\n请先启动SOLIDWORKS，然后重试。", "process"
        )
    if len(items) > 1:
        raise SolidWorksError(
            "multiple_instances",
            "检测到多个正在运行的SOLIDWORKS实例。请关闭多余实例后重试。",
            "process", details={"process_ids": [item.process_id for item in items]},
        )
    return items[0]


def connect_active_application():
    try:
        import pythoncom
        import win32com.client

        unknown = pythoncom.GetActiveObject("SldWorks.Application")
        dispatch = unknown.QueryInterface(pythoncom.IID_IDispatch)
        return win32com.client.Dispatch(dispatch)
    except Exception as error:
        raise SolidWorksError(
            "com_connection_failed",
            "已检测到SOLIDWORKS进程，但无法建立COM连接。可能存在权限不一致、软件仍在启动、模态对话框或COM注册异常。",
            "connection", details={"exception": type(error).__name__},
        ) from error


def process_integrity_level(process_id: int | None = None) -> str:
    if not hasattr(ctypes, "windll"):
        return "unknown"
    if process_id is None:
        try:
            return "high" if ctypes.windll.shell32.IsUserAnAdmin() else "medium"
        except Exception:
            return "unknown"
    kernel32, advapi32 = ctypes.windll.kernel32, ctypes.windll.advapi32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    process = kernel32.GetCurrentProcess() if process_id is None else kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not process:
        return "unknown"
    token = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
            return "unknown"
        elevation = wintypes.DWORD()
        size = wintypes.DWORD(ctypes.sizeof(elevation))
        if not advapi32.GetTokenInformation(token, TOKEN_ELEVATION, ctypes.byref(elevation), size, ctypes.byref(size)):
            return "unknown"
        return "high" if elevation.value else "medium"
    except Exception:
        return "unknown"
    finally:
        if token:
            kernel32.CloseHandle(token)
        if process_id is not None:
            kernel32.CloseHandle(process)


def bring_process_to_front(process_id: int) -> bool:
    if not hasattr(ctypes, "windll"):
        return False
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == process_id and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(callback, 0)
    return bool(found and user32.SetForegroundWindow(found[0]))
