# SPDX-License-Identifier: GPL-3.0-only
"""Bounded GLB validation and capability-scoped loopback resource server. No Qt import."""
import json
import math
import secrets
import stat
import struct
import threading
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .project_files import local_path

MAX_BYTES = 32 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024
MAX_TRIANGLES = 300000
ASSETS = Path(__file__).parent / 'preview_assets'


class PreviewError(ValueError):
    pass


@dataclass(frozen=True)
class ModelSnapshot:
    data: bytes
    triangles: int
    size: int


def read_glb(value, cancel=None):
    """Reject external resources/unsupported extensions before any browser sees bytes."""
    path = local_path(value)
    if path.suffix.lower() != '.glb':
        raise PreviewError('本阶段只支持自包含 GLB，其他类型请使用“打开文件”。')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):raise PreviewError('GLB必须是普通本地文件。')
    if before.st_size > MAX_BYTES:
        raise PreviewError('模型超过32 MiB限制。')
    chunks = []
    with path.open('rb') as stream:
        while True:
            if cancel and cancel.is_set(): raise PreviewError('已取消预览。')
            block = stream.read(1024 * 1024)
            if not block: break
            chunks.append(block)
            if sum(map(len, chunks)) > MAX_BYTES: raise PreviewError('模型超过大小限制。')
    data = b''.join(chunks)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise PreviewError('读取期间模型发生变化，请重新选择。')
    if len(data) < 20: raise PreviewError('GLB文件不完整。')
    magic, version, length = struct.unpack_from('<4sII', data)
    if magic != b'glTF' or version != 2 or length != len(data):
        raise PreviewError('GLB头或长度无效，只支持glTF 2.0。')
    offset, doc, binary = 12, None, None
    while offset < len(data):
        if offset + 8 > len(data): raise PreviewError('GLB块头损坏。')
        count, kind = struct.unpack_from('<II', data, offset); offset += 8
        if count % 4 or offset + count > len(data): raise PreviewError('GLB块长度损坏。')
        if kind == 0x4E4F534A and doc is None and offset == 20:
            if count > MAX_JSON: raise PreviewError('模型JSON超过2 MiB限制。')
            try: doc = json.loads(data[offset:offset+count].decode('utf-8'))
            except (ValueError, UnicodeError, RecursionError) as exc: raise PreviewError('GLB JSON损坏。') from exc
        elif kind == 0x004E4942 and binary is None: binary = data[offset:offset+count]
        else: raise PreviewError('不支持的GLB块布局。')
        offset += count
    if not isinstance(doc, dict) or doc.get('asset', {}).get('version') != '2.0' or binary is None:
        raise PreviewError('缺少glTF 2.0描述或内嵌缓冲区。')
    if doc.get('extensionsRequired') or doc.get('extensionsUsed'):
        raise PreviewError('本阶段不支持扩展/压缩解码，请提供无扩展的自包含GLB。')
    stack, visited = [doc], 0
    while stack:
        if cancel and cancel.is_set(): raise PreviewError('已取消预览。')
        item = stack.pop(); visited += 1
        if visited > 100000: raise PreviewError('模型结构过于复杂。')
        if isinstance(item, dict):
            if 'uri' in item: raise PreviewError('模型引用外部资源或URI；本阶段仅接受内嵌BIN资源。')
            stack.extend(item.values())
        elif isinstance(item, list): stack.extend(item)
        elif isinstance(item, float) and not math.isfinite(item):raise PreviewError('模型包含非有限数值。')
    buffers = doc.get('buffers', [])
    if len(buffers) != 1 or not 0 < buffers[0].get('byteLength', 0) <= len(binary):
        raise PreviewError('内嵌缓冲区长度无效。')
    views = doc.get('bufferViews', [])
    for view in views:
        start, size = view.get('byteOffset', 0), view.get('byteLength', 0)
        if view.get('buffer', 0) != 0 or not isinstance(start, int) or not isinstance(size, int) or start < 0 or size < 0 or start+size > len(binary):
            raise PreviewError('缓冲区范围越界。')
    accessors = doc.get('accessors', [])
    if any(not isinstance(a.get('count'), int) or not 0 <= a['count'] <= 1000000 or 'sparse' in a for a in accessors):
        raise PreviewError('顶点数量超限或不支持稀疏访问器。')
    for accessor in accessors:
        try:
            view=views[accessor['bufferView']]
            component={5120:1,5121:1,5122:2,5123:2,5125:4,5126:4}[accessor['componentType']]
            width={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}[accessor['type']]*component
            stride=view.get('byteStride',width);start=accessor.get('byteOffset',0)
            if not width<=stride<=252 or start<0 or (accessor['count']-1)*stride+start+width>view['byteLength']:
                raise PreviewError('顶点访问器越界。')
        except (KeyError,IndexError,TypeError) as exc:raise PreviewError('访问器格式不支持。') from exc
    if len(doc.get('images', []))>8:raise PreviewError('纹理数量超过8张限制。')
    for image in doc.get('images', []):
        if image.get('mimeType') not in ('image/png', 'image/jpeg') or not 0 <= image.get('bufferView', -1) < len(views):
            raise PreviewError('仅支持内嵌PNG/JPEG纹理。')
        view = views[image['bufferView']]; blob = binary[view.get('byteOffset', 0):][:view['byteLength']]
        if image['mimeType']!='image/png' or len(blob)<24 or blob[:8]!=b'\x89PNG\r\n\x1a\n':
            raise PreviewError('本阶段纹理仅支持内嵌PNG。')
        width,height=struct.unpack_from('>II',blob,16)
        if not 0<width<=2048 or not 0<height<=2048:raise PreviewError('纹理尺寸超过2048限制。')
    triangles = 0;mesh_counts=[];vertices=0
    try:
        for mesh in doc.get('meshes', []):
            mesh_count=0
            for primitive in mesh['primitives']:
                vertices+=accessors[primitive['attributes']['POSITION']]['count']
                if vertices>1000000:raise PreviewError('模型顶点总数超过限制。')
                if primitive.get('mode', 4) != 4: raise PreviewError('仅支持三角网格。')
                a = accessors[primitive.get('indices', primitive['attributes']['POSITION'])]
                mesh_count+=a['count']//3
            mesh_counts.append(mesh_count)
        nodes=doc.get('nodes',[])
        def count_node(index,parents):
            if index in parents or len(parents)>64 or not 0<=index<len(nodes):raise PreviewError('模型节点循环或层级过深。')
            node=nodes[index];total=mesh_counts[node['mesh']] if 'mesh' in node else 0
            for child in node.get('children',[]):
                total+=count_node(child,parents|{index})
                if total>MAX_TRIANGLES:raise PreviewError('实例三角面超过限制。')
            return total
        roots=doc.get('scenes',[{}])[doc.get('scene',0)].get('nodes',[])
        for root in roots:
            triangles+=count_node(root,set())
            if triangles>MAX_TRIANGLES:raise PreviewError('实例三角面超过限制。')
        if not 0 < triangles <= MAX_TRIANGLES: raise PreviewError('模型无三角面或超过30万面限制。')
        if len(doc.get('nodes', [])) > 10000: raise PreviewError('模型节点过多。')
    except (KeyError, IndexError, TypeError) as exc: raise PreviewError('GLB网格结构无效。') from exc
    return ModelSnapshot(data, triangles, len(data))


class ResourceServer:
    """Exact in-memory routes only. Never accepts a filesystem path from HTTP."""
    def __init__(self):
        self.secret = secrets.token_hex(24)
        self.routes = {}
        self.requests = deque(maxlen=256)
        self.lock = threading.Lock()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                with owner.lock:
                    resource = owner.routes.get(self.path) if self.headers.get('Host') == owner.authority else None
                    owner.requests.append(dict(path=self.path, allowed=resource is not None))
                if resource is None:
                    self.send_error(404); return
                mime, payload = resource
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                try: self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self, *_args): pass
        class BoundedServer(ThreadingHTTPServer):
            daemon_threads=True
            request_queue_size=8
            slots=threading.BoundedSemaphore(8)
            def get_request(self):
                connection,address=super().get_request();connection.settimeout(5)
                return connection,address
            def process_request(self,request,address):
                if not self.slots.acquire(blocking=False):request.close();return
                try:super().process_request(request,address)
                except BaseException:self.slots.release();raise
            def process_request_thread(self,request,address):
                try:super().process_request_thread(request,address)
                finally:self.slots.release()
        self.http = BoundedServer(('127.0.0.1', 0), Handler)
        self.http.daemon_threads = True
        self.authority = f'127.0.0.1:{self.http.server_port}'
        self.origin = 'http://' + self.authority
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def publish(self, generation, model):
        prefix = f'/{self.secret}/{generation}/'
        html = (ASSETS/'index.html').read_text(encoding='utf-8').replace('__GENERATION__', str(generation))
        with self.lock:
            self.routes = {prefix+'index.html': ('text/html; charset=utf-8', html.encode()),
                           prefix+'app.js': ('application/javascript', (ASSETS/'app.js').read_bytes()),
                           prefix+'o3dv.min.js': ('application/javascript', (ASSETS/'o3dv.min.js').read_bytes()),
                           prefix+'model.glb': ('model/gltf-binary', model.data)}
        return self.origin+prefix+'index.html'

    def allowed(self, url):
        with self.lock:
            return url.startswith(self.origin) and url[len(self.origin):] in self.routes

    def revoke(self):
        with self.lock: self.routes = {}

    def close(self):
        self.revoke()
        def stop():
            self.http.shutdown(); self.http.server_close()
        threading.Thread(target=stop, daemon=True).start()
