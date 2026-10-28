# SPDX-License-Identifier: GPL-3.0-only
import hashlib,importlib.util,json,os,struct,subprocess,sys
from pathlib import Path
from threading import Event
import pytest
from mechanical_project_namer.cad import (read_cad_isolated,encode_stl,step_preflight,verify_step_assets,PreviewError)
from mechanical_project_namer.preview import read_glb,ResourceServer,MAX_TRIANGLES
# Match app.main's lightweight initialization before pytest-qt creates QApplication.
from PyQt6.QtCore import QCoreApplication,Qt
QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope='session')
def cad_samples(tmp_path_factory):
    folder=tmp_path_factory.mktemp('cad-fixtures')/'中文 😀'
    result=subprocess.run([sys.executable,str(ROOT/'scripts/generate_cad_demos.py'),str(folder)],capture_output=True,timeout=60)
    assert result.returncode==0,result.stderr.decode(errors='replace')
    return folder


@pytest.mark.parametrize('name,encoding',[('ASCII 立方体.stl','ascii'),('Binary 法向异常 😀.stl','binary')])
def test_stl_readonly_unicode_normal_repair_and_valid_glb(cad_samples,tmp_path,name,encoding):
    source=cad_samples/name;before=(hashlib.sha256(source.read_bytes()).hexdigest(),source.stat().st_mtime_ns)
    model=read_cad_isolated(source,Event());assert model.triangles==12
    assert model.metadata['unit']=='unknown' and model.metadata['normal_repairs']==12 and model.metadata['encoding']==encoding
    output=tmp_path/'validated.glb';output.write_bytes(model.data);assert read_glb(output).triangles==12
    assert (hashlib.sha256(source.read_bytes()).hexdigest(),source.stat().st_mtime_ns)==before


@pytest.mark.parametrize('kind',['empty','bad-ascii','truncated-binary','excess-triangles','nonfinite-vertex','degenerate','too-large','missing'])
def test_stl_invalid_and_over_limit_are_rejected(cad_samples,tmp_path,kind):
    path=tmp_path/'异常.stl';data=(cad_samples/'ASCII 立方体.stl').read_bytes()
    if kind=='empty':data=b'solid empty\nendsolid empty'
    if kind=='bad-ascii':data=b'solid broken\nfacet normal q 0 0\nendsolid broken'
    if kind=='truncated-binary':data=(cad_samples/'Binary 法向异常 😀.stl').read_bytes()[:-1]
    if kind=='excess-triangles':data=b'x'*80+struct.pack('<I',MAX_TRIANGLES+1)+b'\0'*((MAX_TRIANGLES+1)*50)
    if kind=='nonfinite-vertex':data=data.replace(b'vertex 20',b'vertex nan',1)
    if kind=='degenerate':data=b'solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 0 0 0\nvertex 0 0 0\nendloop\nendfacet\nendsolid x'
    if kind=='too-large':
        with path.open('wb') as stream:stream.truncate(32*1024*1024+1)
    elif kind!='missing':path.write_bytes(data)
    with pytest.raises((ValueError,OSError)):read_cad_isolated(path,Event())


def test_stl_cancellation_and_degenerate_subset(cad_samples):
    cancel=Event();cancel.set()
    with pytest.raises(PreviewError):read_cad_isolated(cad_samples/'ASCII 立方体.stl',cancel)
    data=(cad_samples/'ASCII 立方体.stl').read_bytes()
    facet=b'facet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 0 0 0\nvertex 0 0 0\nendloop\nendfacet\n'
    model=encode_stl(data.replace(b'endsolid',facet+b'endsolid'),Event())
    assert model.triangles==12 and model.metadata['degenerate_omitted']==1


@pytest.mark.parametrize('name,protocol',[('AP203 单实体.step','AP203'),('AP214 多实体颜色.stp','AP214'),('AP242 装配81件.step','AP242'),('AP242 英寸单位.step','AP242')])
def test_step_protocol_unicode_source_and_unit_metadata(cad_samples,name,protocol):
    f=cad_samples/name;before=(f.read_bytes(),f.stat().st_mtime_ns)
    model=read_cad_isolated(f,Event())
    assert model.format=='step' and model.triangles==0  # preflight is not a rendering success
    assert model.metadata['protocol']==protocol and model.metadata['output_unit']=='millimeter'
    if '英寸' in name:assert 'INCH' in model.metadata['source_units']
    assert (f.read_bytes(),f.stat().st_mtime_ns)==before


@pytest.mark.parametrize('kind',['broken','unsupported','external','cycle','unterminated-string','too-large','too-many-entities'])
def test_step_lexical_and_security_limits(cad_samples,kind):
    data=(cad_samples/'AP203 单实体.step').read_bytes()
    if kind=='broken':data=b'ISO-10303-21;broken'
    if kind=='unsupported':data=data.replace(b'CONFIG_CONTROL_DESIGN',b'UNSUPPORTED_SCHEMA')
    if kind=='external':data=data.replace(b'ENDSEC;\nEND-ISO',b"#99999=EXTERNAL_SOURCE('file://private');\nENDSEC;\nEND-ISO")
    if kind=='cycle':data=data.replace(b'ENDSEC;\nEND-ISO',b"#99999=ORIENTED_EDGE('',*,*,#99999,.T.);\nENDSEC;\nEND-ISO")
    if kind=='unterminated-string':data=data.replace(b'ENDSEC;\nEND-ISO',b"#99999=PRODUCT('unclosed\nENDSEC;\nEND-ISO")
    if kind=='too-large':data+=b' '*(16*1024*1024)
    if kind=='too-many-entities':data=data.replace(b'ENDSEC;\nEND-ISO',(b'#888888=CARTESIAN_POINT((0,0,0));\n'*100001)+b'ENDSEC;\nEND-ISO')
    with pytest.raises((PreviewError,UnicodeError)):step_preflight(data,Event())


@pytest.mark.parametrize('name,parts',[('AP203 单实体.step',1),('AP214 多实体颜色.stp',2),('AP242 装配81件.step',81),('AP242 英寸单位.step',2)])
def test_reviewed_wasm_worker_units_colors_and_entities(cad_samples,name,parts):
    result=subprocess.run(['node',str(ROOT/'cad-web/verify-worker.cjs'),str(cad_samples/name)],capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr.decode(errors='replace')
    answer=json.loads(result.stdout);assert not answer.get('error'),answer
    assert answer['parts']==parts and answer['triangles']>0 and answer['glb_bytes']>100
    if '颜色' in name:assert answer['materials']>=2
    if '英寸' in name:assert answer['bounds_mm'][3:]==pytest.approx([20,20,30],abs=.01)


@pytest.mark.parametrize('mode',['empty','bad-index','too-many','degenerate'])
def test_worker_rejects_unrenderable_or_over_complex_result(cad_samples,mode):
    result=subprocess.run(['node',str(ROOT/'cad-web/verify-worker.cjs'),str(cad_samples/'AP203 单实体.step'),mode],capture_output=True,timeout=20)
    assert result.returncode==0
    assert json.loads(result.stdout).get('error')


def test_step_assets_optional_integrity_failure(monkeypatch,tmp_path):
    import mechanical_project_namer.cad as cad
    monkeypatch.setattr(cad,'STEP_ASSETS',tmp_path)
    with pytest.raises(PreviewError,match='setup_cad'):verify_step_assets()


def test_step_schema_string_cannot_spoof_header():
    data=b"ISO-10303-21; HEADER; FILE_DESCRIPTION(('FILE_SCHEMA((''CONFIG_CONTROL_DESIGN''))'),'2;1'); FILE_SCHEMA(('UNSUPPORTED')); ENDSEC; DATA; #1=CARTESIAN_POINT('',(0,0,0)); ENDSEC; END-ISO-10303-21;"
    with pytest.raises(PreviewError,match='AP203'):step_preflight(data,Event())


def test_step_disabled_in_production_mode(tmp_path,qtbot):
    from mechanical_project_namer.data import Database,Repository
    from mechanical_project_namer.ui.project_files_page import ProjectFilesPage
    db=Database(tmp_path/'isolated.db');db.migrate();page=ProjectFilesPage(Repository(db));qtbot.addWidget(page)
    page.rows=[dict(extension='.step')];page.table.setRowCount(1);page.table.selectRow(0);page.preview_selected()
    assert '隔离开发模式' in page.status.text() and page.preview_window is None and page.open_button.isEnabled()


def test_step_scoped_routes_csp_and_revocation(cad_samples):
    server=ResourceServer()
    try:
        model=read_cad_isolated(cad_samples/'AP203 单实体.step',Event());url=server.publish(1,model)
        for name in ['input.step','cad-worker.js','occt-import-js.js','occt-import-js.wasm']:assert server.allowed(url.replace('index.html',name))
        for name in ['model.glb','../../private.db','other.step','occt-import-js.wasm?file=secret']:assert not server.allowed(url.replace('index.html',name))
        html=next(data for key,(_,data) in server.routes.items() if key.endswith('index.html')).decode()
        assert "'wasm-unsafe-eval'" in html and "worker-src 'self'" in html and "'unsafe-eval'" not in html
        assert not server.allowed('file:///secret') and not server.allowed('https://invalid.example/model.step')
        server.revoke();assert not server.allowed(url)
    finally:server.close();server.thread.join(2)


def test_preprocessor_worker_cancel_lifecycle(cad_samples,qtbot):
    from mechanical_project_namer.ui.preview_window import ReadWorker
    worker=ReadWorker(12,str(cad_samples/'AP242 装配81件.step'));results=[];failures=[]
    worker.ready.connect(lambda *args:results.append(args));worker.failed.connect(lambda *args:failures.append(args))
    worker.cancel.set();worker.start();qtbot.waitUntil(lambda:not worker.isRunning(),timeout=5000)
    qtbot.waitUntil(lambda:bool(failures),timeout=1000)
    assert not results and failures[0][0]==12
    worker.deleteLater()


def test_cad_does_not_change_business_database(cad_samples,tmp_path):
    from mechanical_project_namer.data import Database,Repository
    from mechanical_project_namer.projects import ProjectService
    from mechanical_project_namer.project_files import ProjectFileCatalog,Source
    db=Database(tmp_path/'isolated.db');db.migrate();repo=Repository(db);project=ProjectService(repo).create('虚构CAD验收','','DEMO')
    catalog=ProjectFileCatalog(repo);path=cad_samples/'AP214 多实体颜色.stp'
    catalog.commit(catalog.scan(project.id,[Source(str(path),'file')]))
    before=(repo.get_project(project.id),repo.list_records(),catalog.list_files(project.id))
    read_cad_isolated(path,Event())
    assert (repo.get_project(project.id),repo.list_records(),catalog.list_files(project.id))==before


def test_optional_cad_and_native_kernel_absent_at_base_start():
    code='''import sys,importlib.abc
class Deny(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path=None,target=None):
  if name.startswith(('OCP','PyQt6.QtPdf','PyQt6.QtWebEngine')):raise ImportError('optional deliberately absent')
sys.meta_path.insert(0,Deny())
import mechanical_project_namer.app
assert not any(x.startswith(('OCP','mechanical_project_namer.cad','PyQt6.QtWebEngine')) for x in sys.modules)
'''
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr.decode(errors='replace')
