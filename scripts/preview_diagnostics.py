# SPDX-License-Identifier: GPL-3.0-only
"""Windows-only, allocation-stable read-only metrics for preview acceptance scripts."""
import ctypes as C
from ctypes import wintypes as W
import gc
import os


class MemoryCounters(C.Structure):
    _fields_=[('cb',W.DWORD),('faults',W.DWORD)]+[(name,C.c_size_t) for name in
        ['peak','working','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile','private']]


class ProcessEntry(C.Structure):
    _fields_=[('size',W.DWORD),('usage',W.DWORD),('pid',W.DWORD),('heap',C.c_size_t),('module',W.DWORD),
              ('threads',W.DWORD),('parent',W.DWORD),('priority',W.LONG),('flags',W.DWORD),('exe',W.WCHAR*260)]


K=C.WinDLL('kernel32',use_last_error=True);P=C.WinDLL('psapi',use_last_error=True)
K.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];K.OpenProcess.restype=W.HANDLE
K.CloseHandle.argtypes=[W.HANDLE];K.CloseHandle.restype=W.BOOL
K.GetProcessHandleCount.argtypes=[W.HANDLE,C.POINTER(W.DWORD)];K.GetProcessHandleCount.restype=W.BOOL
K.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD];K.CreateToolhelp32Snapshot.restype=W.HANDLE
K.Process32FirstW.argtypes=[W.HANDLE,C.POINTER(ProcessEntry)];K.Process32FirstW.restype=W.BOOL
K.Process32NextW.argtypes=[W.HANDLE,C.POINTER(ProcessEntry)];K.Process32NextW.restype=W.BOOL
P.GetProcessMemoryInfo.argtypes=[W.HANDLE,C.POINTER(MemoryCounters),W.DWORD];P.GetProcessMemoryInfo.restype=W.BOOL


def memory(pid):
    handle=K.OpenProcess(0x410,False,int(pid))
    if not handle:return None
    try:
        info=MemoryCounters();info.cb=C.sizeof(info);handles=W.DWORD()
        if not P.GetProcessMemoryInfo(handle,C.byref(info),info.cb):return None
        K.GetProcessHandleCount(handle,C.byref(handles))
        return dict(working_bytes=info.working,private_bytes=info.private,peak_working_bytes=info.peak,handles=handles.value)
    finally:K.CloseHandle(handle)


def processes():
    handle=K.CreateToolhelp32Snapshot(2,0)
    if handle in (None,C.c_void_p(-1).value):raise OSError('Cannot snapshot process metadata')
    try:
        row=ProcessEntry();row.size=C.sizeof(row);result={}
        ok=K.Process32FirstW(handle,C.byref(row))
        while ok:
            result[row.pid]=dict(pid=row.pid,parent=row.parent,threads=row.threads,name=row.exe)
            ok=K.Process32NextW(handle,C.byref(row))
        return result
    finally:K.CloseHandle(handle)


def process_tree(pid=None):
    pid=pid or os.getpid();table=processes();ids={pid}
    while True:
        new={key for key,row in table.items() if row['parent'] in ids}-ids
        if not new:break
        ids.update(new)
    result=[]
    for key in sorted(ids):
        if key not in table:continue
        values=memory(key)
        if values:result.append({**table[key],**values})
    return result


def object_counts():
    from PyQt6.QtCore import QObject
    from PyQt6 import sip
    counts={};cpp_live=0;cpp_deleted=0
    objects=gc.get_objects()
    for obj in objects:
        cls=type(obj);name=cls.__module__+'.'+cls.__name__
        if cls.__module__.startswith(('mechanical_project_namer','PyQt6')) or cls.__name__ in ('Counters','_FuncPtr','Handler','BoundedServer'):
            counts[name]=counts.get(name,0)+1
        if isinstance(obj,QObject):
            if sip.isdeleted(obj):cpp_deleted+=1
            else:cpp_live+=1
    return dict(tracked_objects=len(objects),types=counts,qt_cpp_live=cpp_live,qt_cpp_deleted=cpp_deleted,gc_counts=gc.get_count())
