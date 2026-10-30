# SPDX-License-Identifier: GPL-3.0-only
"""Windows process resources; actual ready/worker PIDs, stable ctypes layouts."""
import ctypes
import struct
from ctypes import wintypes as W
from functools import lru_cache


@lru_cache(maxsize=1)
def _api():
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];kernel.OpenProcess.restype=W.HANDLE
    kernel.CloseHandle.argtypes=[W.HANDLE]
    class Memory(ctypes.Structure):
        _fields_=[('cb',W.DWORD),('faults',W.DWORD)]+[(x,ctypes.c_size_t) for x in ('peak_ws','ws','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile','private')]
    get=ctypes.WinDLL('psapi').GetProcessMemoryInfo;get.argtypes=[W.HANDLE,ctypes.POINTER(Memory),W.DWORD]
    count=kernel.GetProcessHandleCount;count.argtypes=[W.HANDLE,ctypes.POINTER(W.DWORD)]
    class Thread(ctypes.Structure):
        _fields_=[('size',W.DWORD),('usage',W.DWORD),('id',W.DWORD),('owner',W.DWORD),('base',W.LONG),('delta',W.LONG),('flags',W.DWORD)]
    create=kernel.CreateToolhelp32Snapshot;create.argtypes=[W.DWORD,W.DWORD];create.restype=W.HANDLE
    first,next_=kernel.Thread32First,kernel.Thread32Next
    for fn in (first,next_):fn.argtypes=[W.HANDLE,ctypes.POINTER(Thread)]
    table=ctypes.WinDLL('iphlpapi').GetExtendedTcpTable
    table.argtypes=[ctypes.c_void_p,ctypes.POINTER(W.DWORD),W.BOOL,W.ULONG,ctypes.c_int,W.ULONG]
    return kernel,Memory,get,count,Thread,create,first,next_,table


def sample(pid):
    kernel,Memory,get,count,Thread,create,first,next_,table=_api()
    handle=kernel.OpenProcess(0x410,False,pid);m=Memory();m.cb=ctypes.sizeof(m);handles=W.DWORD()
    try:
        if not handle or not get(handle,ctypes.byref(m),m.cb) or not count(handle,ctypes.byref(handles)):
            raise OSError('process-sampling')
    finally:
        if handle:kernel.CloseHandle(handle)
    snapshot=create(4,0)
    if not snapshot or snapshot==ctypes.c_void_p(-1).value:raise OSError('thread-sampling')
    entry=Thread();entry.size=ctypes.sizeof(entry);threads=0
    try:
        ok=first(snapshot,ctypes.byref(entry))
        while ok:
            threads+=entry.owner==pid;ok=next_(snapshot,ctypes.byref(entry))
    finally:kernel.CloseHandle(snapshot)
    size=W.DWORD();table(None,ctypes.byref(size),False,2,5,0)
    buf=ctypes.create_string_buffer(size.value)
    if table(buf,ctypes.byref(size),False,2,5,0):raise OSError('connection-sampling')
    n=struct.unpack_from('I',buf.raw)[0];connections=[]
    for i in range(n):
        row=struct.unpack_from('6I',buf.raw,4+i*24)
        if row[5]==pid:connections.append(row[0])
    return dict(pid=pid,working_set=m.ws,private_bytes=m.private,handles=handles.value,threads=threads,
                tcp_count=len(connections),tcp_established=connections.count(5))
