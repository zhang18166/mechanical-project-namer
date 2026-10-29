# SPDX-License-Identifier: GPL-3.0-only
"""Cooperative cross-process library leases, also enforced below the UI/Repository."""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import os
import stat
import threading


class MaintenanceBusy(RuntimeError):
    pass


_local = threading.local()


@contextmanager
def file_lease(path, *, exclusive=False):
    """Nonblocking OS lock, shared readers or exclusive maintenance; no stale PID lock."""
    path = Path(path).absolute()
    for part in [*reversed(path.parents),path]:
        try:
            info=part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise MaintenanceBusy('锁资源包含链接或重解析点，拒绝接管。')
    key = os.path.normcase(str(path))
    held = getattr(_local, 'held', None)
    if held is None:
        held = _local.held = {}
    entry = held.get(key)
    if entry:
        if exclusive and not entry['exclusive']:
            raise MaintenanceBusy('当前线程仍有业务连接，不能升级到维护模式。')
        entry['users'] += 1
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open('a+b')
        try:
            release = _os_lock(stream, exclusive)
        except BaseException:
            stream.close()
            raise
        entry = held[key] = dict(exclusive=exclusive, users=1, stream=stream, release=release)
    try:
        yield
    finally:
        entry['users'] -= 1
        if entry['users'] == 0:
            del held[key]
            try:
                entry['release']()
            finally:
                entry['stream'].close()


def _os_lock(stream, exclusive):
        if os.name == 'nt':
            import ctypes
            import msvcrt
            from ctypes import wintypes
            class OVERLAPPED(ctypes.Structure):
                _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                            ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD),
                            ('hEvent', wintypes.HANDLE)]
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            lock = kernel.LockFileEx
            lock.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                             wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(OVERLAPPED)]
            lock.restype = wintypes.BOOL
            unlock = kernel.UnlockFileEx
            unlock.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                               wintypes.DWORD, ctypes.POINTER(OVERLAPPED)]
            unlock.restype = wintypes.BOOL
            handle = msvcrt.get_osfhandle(stream.fileno()); overlap = OVERLAPPED()
            if not lock(handle, 1 | (2 if exclusive else 0), 0, 1, 0, ctypes.byref(overlap)):
                raise MaintenanceBusy('库正在使用或维护中；请关闭所有客户端并等待活动任务结束。')
            release = lambda: unlock(handle, 0, 1, 0, ctypes.byref(overlap))
        else:
            import fcntl
            try:
                fcntl.flock(stream, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except OSError as exc:
                raise MaintenanceBusy('库正在使用或维护中，请稍后重试。') from exc
            release = lambda: fcntl.flock(stream, fcntl.LOCK_UN)
        return release


def library_lease(database_path, *, exclusive=False):
    path = Path(database_path).absolute()
    return file_lease(path.parent / ('.' + path.name + '.library.lock'), exclusive=exclusive)


def library_operation(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        database = getattr(self, 'db', None) or self
        with library_lease(database.path):
            return method(self, *args, **kwargs)
    return guarded
