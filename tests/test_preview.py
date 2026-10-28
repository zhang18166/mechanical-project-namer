# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import struct
import sys
import threading
import urllib.error
import urllib.request

import pytest
from mechanical_project_namer.preview import PreviewError,ResourceServer,read_glb,MAX_BYTES

spec=importlib.util.spec_from_file_location('demo_generator',Path(__file__).parents[1]/'scripts/generate_preview_demos.py')
demos=importlib.util.module_from_spec(spec);spec.loader.exec_module(demos)
_connect=socket.socket.connect
_create=socket.create_connection


@pytest.fixture
def samples(tmp_path):
    folder=tmp_path/'中文 空格(演示)😀';demos.generate(folder)
    return folder


@pytest.mark.parametrize('name,triangles',[('简单 几何体(红).glb',12),('多零件 装配 😀.glb',48),('受控复杂 环体.glb',9216)])
def test_valid_demo_geometry_unicode_and_source_unchanged(samples,name,triangles):
    path=samples/name;before=(hashlib.sha256(path.read_bytes()).hexdigest(),path.stat().st_mtime_ns)
    model=read_glb(path)
    assert model.triangles==triangles and model.size==path.stat().st_size
    assert before==(hashlib.sha256(path.read_bytes()).hexdigest(),path.stat().st_mtime_ns)


@pytest.mark.parametrize('name',['损坏.glb','外部资源.glb','不存在.glb'])
def test_controlled_invalid_models(samples,name):
    with pytest.raises((PreviewError,OSError)):read_glb(samples/name)


@pytest.mark.parametrize('mode',['version','length','chunk','json','extension','uri','vertices','view','cycle','instances','mode','empty','sparse'])
def test_glb_validation_rejects_unsafe_structures(tmp_path,mode):
    doc,binary,_=demos.build()
    if mode=='extension':doc['extensionsRequired']=['KHR_draco_mesh_compression']
    if mode=='uri':doc['images']=[{'uri':'file:///private/never-read.png'}]
    if mode=='vertices':doc['accessors'][0]['count']=1000001
    if mode=='view':doc['bufferViews'][0]['byteLength']=999999999
    if mode=='cycle':doc['nodes'][0]['children']=[0]
    if mode=='instances':doc['nodes']=[{'mesh':0} for _ in range(10001)];doc['scenes'][0]['nodes']=list(range(10001))
    if mode=='mode':doc['meshes'][0]['primitives'][0]['mode']=1
    if mode=='empty':doc['meshes']=[]
    if mode=='sparse':doc['accessors'][0]['sparse']={}
    data=demos.encode(doc,binary)
    if mode=='version':data=data[:4]+struct.pack('<I',1)+data[8:]
    if mode=='length':data=data[:8]+struct.pack('<I',100)+data[12:]
    if mode=='chunk':data=data[:12]+struct.pack('<I',0x7fffffff)+data[16:]
    if mode=='json':data=data[:20]+b'bad!'+data[24:]
    path=tmp_path/'bad.glb';path.write_bytes(data)
    with pytest.raises(PreviewError):read_glb(path)


def test_oversize_and_cancelled_reads(samples):
    path=samples/'too-large.glb'
    with path.open('wb') as stream:stream.truncate(MAX_BYTES+1)
    with pytest.raises(PreviewError,match='32'):read_glb(path)
    event=threading.Event();event.set()
    with pytest.raises(PreviewError,match='取消'):read_glb(samples/'简单 几何体(红).glb',event)


def test_optional_preview_missing_keeps_file_management_and_naming_usable(tmp_path,qtbot,monkeypatch):
    from mechanical_project_namer.data import Database,Repository
    from mechanical_project_namer.projects import ProjectService
    from mechanical_project_namer.project_files import ProjectFileCatalog,Source
    from mechanical_project_namer.ui.project_files_page import ProjectFilesPage
    db=Database(tmp_path/'data.db');db.migrate();repo=Repository(db)
    project=ProjectService(repo).create('虚构预览','','DEMO')
    doc,binary,_=demos.build();path=tmp_path/'演示.glb';path.write_bytes(demos.encode(doc,binary))
    service=ProjectFileCatalog(repo);service.commit(service.scan(project.id,[Source(str(path),'file')]))
    page=ProjectFilesPage(repo);qtbot.addWidget(page);page.load_projects(project.id);page.table.selectRow(0)
    monkeypatch.setitem(sys.modules,'mechanical_project_namer.ui.preview_window',None)
    page.preview_selected()
    assert '未安装' in page.status.text() and page.table.rowCount()==1
    assert repo.get_project(project.id).next_number==1 and len(repo.list_records())==0
    assert page.open_button.isEnabled() and page.locate_button.isEnabled()


def test_unsupported_type_has_explicit_message(tmp_path,qtbot):
    from mechanical_project_namer.data import Database,Repository
    from mechanical_project_namer.ui.project_files_page import ProjectFilesPage
    db=Database(tmp_path/'data.db');db.migrate();page=ProjectFilesPage(Repository(db));qtbot.addWidget(page)
    page.rows=[dict(extension='.pdf')];page.table.setRowCount(1);page.table.selectRow(0)
    page.preview_selected();assert '仅支持GLB' in page.status.text() and page.preview_window is None


def test_loopback_routes_reject_traversal_and_revoke_old_model(samples,monkeypatch):
    server=ResourceServer()
    def connect(sock,address):
        assert address==('127.0.0.1',server.http.server_port)
        return _connect(sock,address)
    monkeypatch.setattr(socket.socket,'connect',connect);monkeypatch.setattr(socket,'create_connection',_create)
    try:
        model=read_glb(samples/'简单 几何体(红).glb');url=server.publish(1,model)
        assert urllib.request.urlopen(url).status==200
        old_model=url.replace('index.html','model.glb')
        assert urllib.request.urlopen(old_model).read()==model.data
        assert server.http.server_address[0]=='127.0.0.1'
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(urllib.request.Request(url,headers={'Host':'untrusted.invalid'}))
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(urllib.request.Request(url,data=b'no uploads',method='POST'))
        for path in ['/','/etc/passwd','/../settings.json','/%2e%2e/private.db','/'+server.secret+'/1/model.glb?file=../x']:
            with pytest.raises(urllib.error.HTTPError):urllib.request.urlopen(server.origin+path)
        for url2 in ['https://invalid.example/x','file:///private','http://localhost:'+str(server.http.server_port)+'/']:
            assert not server.allowed(url2)
        new_url=server.publish(2,model)
        assert server.allowed(new_url) and not server.allowed(url)
        with pytest.raises(urllib.error.HTTPError):urllib.request.urlopen(old_model)
        server.revoke();assert not server.allowed(new_url)
    finally:server.close();server.thread.join(2)


def test_csp_and_assets_are_offline_and_versioned():
    from mechanical_project_namer.preview import ASSETS
    html=(ASSETS/'index.html').read_text(encoding='utf-8')
    assert "connect-src 'self'" in html and "script-src 'self'" in html
    assert 'https://' not in html and 'http://' not in html
    manifest=json.loads((Path(__file__).parents[1]/'third_party/preview/sources.json').read_text())
    assert hashlib.sha256((ASSETS/'o3dv.min.js').read_bytes()).hexdigest()==manifest['viewer_sha256']


def test_base_app_import_does_not_load_or_require_webengine():
    import subprocess
    code='''import sys,importlib.abc
class Deny(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path=None,target=None):
  if name.startswith('PyQt6.QtWebEngine'):raise ImportError('optional preview deliberately absent')
sys.meta_path.insert(0,Deny())
import mechanical_project_namer.app
assert not any(x.startswith('PyQt6.QtWebEngine') for x in sys.modules)
'''
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,timeout=20)
    assert result.returncode==0,result.stderr.decode(errors='replace')
