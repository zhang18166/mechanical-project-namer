# SPDX-License-Identifier: GPL-3.0-only
"""Read-only PDF snapshots and bounded IPC. No Qt/PDF parser imported at startup."""
import json
import os
import stat
import struct
from pathlib import Path
from .project_files import local_path

MAX_FILE = 32 * 1024 * 1024
MAX_FRAME = 40 * 1024 * 1024
MAX_PAGES = 200
MAX_POINTS = 14400
MAX_PIXELS = 4_000_000


class PdfError(ValueError):
    pass


def read_pdf(path, cancel):
    path = local_path(path)
    if path.suffix.lower() != '.pdf':
        raise PdfError('仅支持本地 PDF。')
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_FILE:
            raise PdfError('PDF 为空或超过32 MiB限制。')
        chunks = []
        while True:
            if cancel.is_set():
                raise PdfError('已取消。')
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            if sum(map(len, chunks)) > MAX_FILE:
                raise PdfError('PDF 超过32 MiB限制。')
        after = os.fstat(stream.fileno())
    current = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or (before.st_size, before.st_mtime_ns, before.st_ino) != (current.st_size, current.st_mtime_ns, current.st_ino):
        raise PdfError('读取时文件已变化，请重新选择。')
    data = b''.join(chunks)
    if not data.startswith(b'%PDF-'):
        raise PdfError('不是有效的PDF文件。')
    return data


def packet(data):
    if not isinstance(data, bytes):
        data = json.dumps(data, ensure_ascii=True, allow_nan=False).encode('ascii')
    if len(data) > MAX_FRAME:
        raise PdfError('PDF通信数据超限。')
    return struct.pack('!I', len(data)) + data


def frames(buffer):
    result = []
    while len(buffer) >= 4:
        size = struct.unpack('!I', buffer[:4])[0]
        if size > MAX_FRAME:
            raise PdfError('PDF通信数据超限。')
        if len(buffer) < size + 4:
            break
        result.append(bytes(buffer[4:4+size])); del buffer[:4+size]
    return result


class ProcessBudget:
    """Windows job: 512 MiB per native parser process; kill on owner handle close."""
    def __init__(self, pid):
        self.handle = None
        if os.name != 'nt':
            return
        import ctypes as C
        from ctypes import wintypes as W
        class Basic(C.Structure):
            _fields_ = [('processTime', C.c_int64), ('jobTime', C.c_int64), ('flags', W.DWORD),
                        ('minWorking', C.c_size_t), ('maxWorking', C.c_size_t), ('active', W.DWORD),
                        ('affinity', C.c_size_t), ('priority', W.DWORD), ('scheduling', W.DWORD)]
        class IO(C.Structure):
            _fields_ = [(name, C.c_uint64) for name in ('readOps','writeOps','otherOps','readBytes','writeBytes','otherBytes')]
        class Extended(C.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('processMemory', C.c_size_t), ('jobMemory', C.c_size_t),
                        ('peakProcess', C.c_size_t), ('peakJob', C.c_size_t)]
        kernel = C.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.restype = W.HANDLE
        kernel.SetInformationJobObject.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]
        kernel.OpenProcess.restype = W.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        kernel.CloseHandle.argtypes = [W.HANDLE]
        job = kernel.CreateJobObjectW(None, None)
        config = Extended(); config.basic.flags = 0x2000 | 0x100  # KILL_ON_JOB_CLOSE | PROCESS_MEMORY
        config.processMemory = 512 * 1024 * 1024
        process = kernel.OpenProcess(0x100 | 1, False, int(pid))
        success = job and process and kernel.SetInformationJobObject(job, 9, C.byref(config), C.sizeof(config)) and kernel.AssignProcessToJobObject(job, process)
        if process: kernel.CloseHandle(process)
        if not success:
            if job: kernel.CloseHandle(job)
            raise PdfError('无法设置PDF进程资源限制，已停止预览。')
        self.handle, self.kernel = job, kernel

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle); self.handle = None
