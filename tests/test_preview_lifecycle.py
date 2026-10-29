# SPDX-License-Identifier: GPL-3.0-only
"""Regressions for measured resource lifetimes; no real models or WebEngine startup."""
import gc
import threading
import weakref

from mechanical_project_namer.preview import ResourceServer


def test_server_released_without_cyclic_collection():
    enabled = gc.isenabled()
    gc.disable()
    try:
        server = ResourceServer()
        closed, thread, http = server.closed, server.thread, weakref.ref(server.http)
        ref = weakref.ref(server)
        server.close()
        del server
        assert ref() is None, 'HTTP handler must not strongly capture its owner'
        assert closed.wait(3)
        thread.join(3)
        assert not thread.is_alive()
        assert http() is None
    finally:
        if enabled: gc.enable()


def test_server_shutdown_idempotent_and_routes_revoked():
    server = ResourceServer()
    with server.lock: server.routes['/test'] = ('text/plain', b'not a real model')
    assert server.allowed(server.origin + '/test')
    server.close()
    server.close()
    assert not server.allowed(server.origin + '/test')
    assert server.closed.wait(3)
    assert server.http.socket.fileno() == -1


def test_request_limits_are_per_server():
    first, second = ResourceServer(), ResourceServer()
    try:
        assert first.http.slots is not second.http.slots
        assert first.http.owner() is first and second.http.owner() is second
        for _ in range(8): assert first.http.slots.acquire(blocking=False)
        assert not first.http.slots.acquire(blocking=False)
        assert second.http.slots.acquire(blocking=False)
        second.http.slots.release()
        for _ in range(8): first.http.slots.release()
    finally:
        first.close(); second.close()
        assert first.closed.wait(3) and second.closed.wait(3)


def test_shutdown_does_not_wait_on_gui_thread():
    server = ResourceServer()
    entered, release = threading.Event(), threading.Event()
    shutdown = server.http.shutdown
    def blocked_shutdown():
        entered.set()
        assert release.wait(3)
        shutdown()
    server.http.shutdown = blocked_shutdown
    try:
        server.close()
        assert entered.wait(1)
        assert not server.closed.is_set()
    finally:
        release.set()
        assert server.closed.wait(3)


def test_interceptor_history_is_bounded_and_still_denies(qtbot):
    from PyQt6.QtCore import QUrl
    from mechanical_project_namer.ui.preview_window import OfflineInterceptor
    class Server:
        origin = 'http://127.0.0.1:12345'
        def allowed(self, url): return url == self.origin + '/only'
    class Info:
        def __init__(self, url): self.url, self.blocked = QUrl(url), False
        def requestUrl(self): return self.url
        def block(self, value): self.blocked = value
    interceptor = OfflineInterceptor(Server())
    for _ in range(1000):
        info = Info('https://invalid.example/no-network')
        interceptor.interceptRequest(info)
        assert info.blocked
        info = Info(Server.origin + '/only')
        interceptor.interceptRequest(info)
        assert not info.blocked
    assert len(interceptor.blocked) == len(interceptor.allowed) == 256
