# SPDX-License-Identifier: GPL-3.0-only
"""Private QtPdf raster worker. Only memory input, no links/forms/JS/network API."""
import base64
import json
import math
import os
import struct
import sys
from .pdf import MAX_FILE, MAX_FRAME, MAX_PAGES, MAX_POINTS, MAX_PIXELS, PdfError, packet


def receive():
    header = sys.stdin.buffer.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise PdfError('通信中断。')
    size = struct.unpack('!I', header)[0]
    if size > MAX_FRAME:
        raise PdfError('通信超限。')
    data = sys.stdin.buffer.read(size)
    if len(data) != size:
        raise PdfError('通信中断。')
    return data


def send(value):
    sys.stdout.buffer.write(packet(value)); sys.stdout.buffer.flush()


def main():
    def deny_actions(event, args):
        if event in ('socket.connect','socket.getaddrinfo','subprocess.Popen','os.system','os.startfile'):
            raise PdfError('PDF进程禁止网络和系统动作。')
    sys.addaudithook(deny_actions)
    from PyQt6.QtCore import QBuffer, QIODevice, QSize, qVersion
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtPdf import QPdfDocument
    app = QGuiApplication(['pdf-raster-worker', '-platform', 'offscreen'])
    data = receive()
    if data is None or not 0 < len(data) <= MAX_FILE or not data.startswith(b'%PDF-'):
        raise PdfError('PDF为空、损坏或超限。')
    source = QBuffer(); source.setData(data); source.open(QIODevice.OpenModeFlag.ReadOnly)
    document = QPdfDocument(None); document.load(source); app.processEvents()
    if document.error() == QPdfDocument.Error.IncorrectPassword:
        raise PdfError('PDF已加密，本阶段不接收密码或解密。')
    if document.status() != QPdfDocument.Status.Ready:
        raise PdfError('PDF损坏、不可解析或采用不支持的加密方式。')
    if not 0 < document.pageCount() <= MAX_PAGES:
        raise PdfError('PDF无页面或超过200页限制。')
    sizes = []
    for page in range(document.pageCount()):
        size = document.pagePointSize(page)
        if not all(math.isfinite(v) and 0 < v <= MAX_POINTS for v in (size.width(), size.height())):
            raise PdfError('PDF页面尺寸异常或超过14400点限制。')
        sizes.append([size.width(), size.height()])
    send(dict(type='ready', sizes=sizes, pid=os.getpid(), qt=qVersion()))
    while (raw := receive()) is not None:
        request = json.loads(raw)
        page, width, height = (request.get(k) for k in ('page','width','height'))
        if any(type(v) is not int for v in (page, width, height)) or not 0 <= page < len(sizes) or not 0 < width <= 4096 or not 0 < height <= 4096 or width * height > MAX_PIXELS:
            raise PdfError('页面渲染请求超限。')
        image = document.render(page, QSize(width, height))
        if image.isNull():
            raise PdfError('PDF页面渲染失败。')
        output = QBuffer(); output.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(output, 'PNG'):
            raise PdfError('PDF页面图像转换失败。')
        send(dict(type='page', page=page, width=width, height=height, png=base64.b64encode(bytes(output.data())).decode('ascii')))
    document.close(); source.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        send(dict(type='error', message=str(exc)))
        sys.exit(1)
