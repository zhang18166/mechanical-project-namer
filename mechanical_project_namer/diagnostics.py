# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in, capped, field-allowlisted phase events; never HTTP bodies or secrets."""
import json
import logging
import os
from pathlib import Path
import re
import socket
import threading
import time
import uuid

from .publish_protocol import safe
from .safe_fs import remove_file

_PRIVATE_CLEANUP = logging.Logger(__name__+'.private-cleanup')
_PRIVATE_CLEANUP.propagate = False
_PRIVATE_CLEANUP.addHandler(logging.NullHandler())


def trace_id(value):
    return value if isinstance(value, str) and re.fullmatch('[a-f0-9]{32}', value) else ''


def publish_marker(path, value):
    """Publish complete small metadata exclusively, never replace unknown files.

    The temporary metadata inode is linked atomically then removed; this is not
    a model snapshot, backup, or a link to a working design file.
    """
    path = safe(path)
    temporary = path.with_name('.'+path.name+'-'+uuid.uuid4().hex+'.pending')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json.dumps(value, separators=(',', ':')).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Fails if a marker already exists.
    finally:
        remove_file(temporary,logger=_PRIVATE_CLEANUP)


class Trace:
    def __init__(self, path=None, *, limit=1024**2, rows=4096):
        self.start = time.monotonic_ns()
        self.limit, self.rows = min(limit, 1024**2), min(rows, 4096)
        self.bytes = self.count = self.dropped = 0
        self.lock = threading.Lock()
        self.stream = None
        if path is not None:
            path = safe(path)
            if path.is_relative_to(Path(__file__).resolve().parents[1]):
                raise ValueError('external-diagnostic-required')
            if os.name != 'nt':
                stat = path.parent.stat()
                if stat.st_uid != os.geteuid() or stat.st_mode & 0o077:
                    raise ValueError('private-diagnostic-required')
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self.stream = os.fdopen(fd, 'wb', buffering=0)

    def emit(self, event, **values):
        if self.stream is None:
            return
        if not re.fullmatch('[a-z0-9-]{1,64}', event):
            return
        now = time.monotonic_ns()
        row = dict(event=event, pid=os.getpid(), monotonic_ns=now,
                   seconds=(now-self.start)/10**9)
        for key, value in values.items():
            if key == 'trace' and trace_id(value):
                row[key] = value
            elif key == 'connection' and isinstance(value, str) and re.fullmatch(r'[0-9]{1,12}:[0-9]{1,12}:[0-9]{1,6}', value):
                row[key] = value
            elif key in ('phase', 'code') and isinstance(value, str) and re.fullmatch('[a-z0-9-]{1,64}', value):
                row[key] = value
            elif key in ('attempt', 'status', 'worker_pid', 'application_pid', 'proxy_pid',
                         'local_port', 'remote_port', 'received', 'sent', 'request_bytes',
                         'duration_ms', 'ready', 'slot_busy') and type(value) in (int, float, bool) and abs(value) < 2**64:
                row[key] = value
        payload = json.dumps(row, separators=(',', ':'), allow_nan=False).encode() + b'\n'
        with self.lock:
            if self.stream is None:
                return
            if self.count >= self.rows or self.bytes+len(payload) > self.limit:
                self.dropped += 1
                return
            try:
                self.stream.write(payload)
            except OSError:
                self.dropped += 1
                return
            self.bytes += len(payload)
            self.count += 1

    def close(self):
        with self.lock:
            if self.stream:
                self.stream.close()
                self.stream = None


class ProxyTrace:
    """Receive Nginx's allowlisted Unix-datagram access events, not HTTP traffic.

    Socket and capped file are private to the runtime. Default runtime has
    neither this thread nor diagnostic logging. Unknown datagram fields vanish.
    """
    def __init__(self, root):
        self.trace = Trace(Path(root)/'proxy-events.jsonl')
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.path = Path(root)/'diagnostic.sock'
        self.socket.bind(str(self.path))
        self.path.chmod(0o600)
        self.socket.settimeout(.2)
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self.run, name='proxy-diagnostic', daemon=True)
        self.thread.start()

    def run(self):
        while not self.closed.is_set():
            try:
                packet = self.socket.recv(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            self.record(packet)

    def record(self,packet):
        try:
            row = json.loads(packet[packet.index(b'{'):])
            self.trace.emit('proxy-request-end', trace=trace_id(row.get('trace')),
                connection=row.get('connection'), proxy_pid=int(row['proxy_pid']),
                local_port=int(row['local_port']), remote_port=int(row['remote_port']),
                status=int(row['status']), sent=int(row['sent']), request_bytes=int(row['request_bytes']),
                duration_ms=float(row['duration'])*1000)
        except (ValueError, KeyError, TypeError):
            pass

    def close(self):
        self.closed.set()
        self.thread.join(1)
        self.socket.setblocking(False)
        for _ in range(256):
            try:self.record(self.socket.recv(2048))
            except BlockingIOError:break
        self.socket.close()
        self.trace.close()
        # Only our own newly bound socket, never any model/task directory.
        remove_file(self.path,logger=_PRIVATE_CLEANUP)
