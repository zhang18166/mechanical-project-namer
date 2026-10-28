# SPDX-License-Identifier: GPL-3.0-only
"""Bounded STL triangle input and STEP lexical preflight. No native CAD kernel import."""
from dataclasses import dataclass
import hashlib, json, math, re, struct
from pathlib import Path
from .preview import MAX_BYTES,MAX_TRIANGLES,ModelSnapshot,PreviewError
from .project_files import local_path

STEP_ASSETS=Path(__file__).resolve().parents[1]/'cad-web/node_modules/occt-import-js/dist'
STEP_HASHES={'occt-import-js.js':'3fb44ce11d00611f9b3f3c5775d520ebab48930c1f08279b7b1316f05f0d3379',
             'occt-import-js.wasm':'33391fc9d94ea5c869a6718488bf0a9a464222bac9bdc764dfe1690cef281952'}


@dataclass(frozen=True)
class CadSnapshot:
    data:bytes
    triangles:int
    size:int
    format:str
    metadata:dict


def verify_step_assets():
    result={}
    for name,expected in STEP_HASHES.items():
        file=STEP_ASSETS/name
        if not file.is_file():
            raise PreviewError('STEP本地组件缺失或校验失败，请运行 scripts/setup_cad.ps1。')
        data=file.read_bytes()
        if hashlib.sha256(data).hexdigest()!=expected:raise PreviewError('STEP本地组件校验失败。')
        result[name]=data
    return result


def read_source(path,cancel):
    import os,stat
    path=local_path(path)
    with path.open('rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=MAX_BYTES:raise PreviewError('CAD为空或超过32 MiB限制。')
        chunks=[];total=0
        while True:
            if cancel.is_set():raise PreviewError('已取消。')
            piece=stream.read(1024*1024)
            if not piece:break
            chunks.append(piece);total+=len(piece)
            if total>MAX_BYTES:raise PreviewError('CAD超过32 MiB限制。')
        after=os.fstat(stream.fileno())
    current=local_path(path).stat()
    if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino) or (before.st_size,before.st_mtime_ns,before.st_ino)!=(current.st_size,current.st_mtime_ns,current.st_ino):raise PreviewError('读取时文件已变化，请重试。')
    return b''.join(chunks)


def encode_stl(data,cancel):
    binary=len(data)>=84 and 84+struct.unpack_from('<I',data,80)[0]*50==len(data)
    if binary:
        count=struct.unpack_from('<I',data,80)[0]
        if not 0<count<=MAX_TRIANGLES:raise PreviewError('STL为空或超过30万三角面。')
        facets=(struct.unpack_from('<12f',data,84+i*50) for i in range(count))
    else:
        try:lines=iter(data.decode('ascii').splitlines())
        except UnicodeError:raise PreviewError('STL编码或二进制长度损坏。')
        def ascii_facets():
            state=0;normal=None;vertices=[];count=0;closed=False
            for raw in lines:
                if len(raw)>4096:raise PreviewError('STL行长度超限。')
                line=raw.strip();parts=line.split()
                if not parts:continue
                if parts[0].lower()=='solid' and state==0:state=1;closed=False
                elif parts[:2]==['facet','normal'] and state==1 and len(parts)==5:
                    normal=list(map(float,parts[2:]));vertices=[];state=2
                elif parts==['outer','loop'] and state==2:state=3
                elif parts[0]=='vertex' and state in (3,4,5) and len(parts)==4:
                    vertices.extend(map(float,parts[1:]));state+=1
                elif parts==['endloop'] and state==6:state=7
                elif parts==['endfacet'] and state==7:
                    count+=1
                    if count>MAX_TRIANGLES:raise PreviewError('STL超过30万三角面。')
                    yield (*normal,*vertices);state=1
                elif parts[0].lower()=='endsolid' and state==1:state=0;closed=True
                else:raise PreviewError('ASCII STL结构损坏。')
            if state!=0 or not closed:raise PreviewError('ASCII STL未完整结束。')
        facets=ascii_facets()
    positions=[];normals=[];repaired=0;degenerate=0
    for facet in facets:
        if cancel.is_set():raise PreviewError('已取消。')
        normal,coords=facet[:3],facet[3:]
        if not all(math.isfinite(x) and abs(x)<=1e9 for x in coords):raise PreviewError('STL顶点坐标非有限数或超限。')
        a,b,c=[coords[i:i+3] for i in (0,3,6)];u=[b[i]-a[i] for i in range(3)];v=[c[i]-a[i] for i in range(3)]
        n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]];length=math.sqrt(sum(x*x for x in n))
        if length<=1e-12:degenerate+=1;continue
        n=[x/length for x in n];old=math.sqrt(sum(x*x for x in normal))
        if not math.isfinite(old) or old<1e-12 or sum(normal[i]*n[i] for i in range(3))/old<.99:repaired+=1
        positions.extend(coords);normals.extend(n*3)
    count=len(positions)//9
    if not count:raise PreviewError('STL没有有效三角面（空模型或全部退化）。')
    pos=struct.pack('<'+'f'*len(positions),*positions);norm=struct.pack('<'+'f'*len(normals),*normals);blob=pos+norm
    doc={'asset':{'version':'2.0'},'scene':0,'scenes':[{'nodes':[0]}],'nodes':[{'mesh':0}],
         'meshes':[{'primitives':[{'attributes':{'POSITION':0,'NORMAL':1},'material':0}]}],
         'materials':[{'doubleSided':True,'pbrMetallicRoughness':{'baseColorFactor':[.2,.45,.8,1],'metallicFactor':0,'roughnessFactor':.8}}],
         'buffers':[{'byteLength':len(blob)}],'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':len(pos)},{'buffer':0,'byteOffset':len(pos),'byteLength':len(norm)}],
         'accessors':[{'bufferView':i,'componentType':5126,'count':count*3,'type':'VEC3'} for i in range(2)]}
    doc['accessors'][0].update(min=[min(positions[i::3]) for i in range(3)],max=[max(positions[i::3]) for i in range(3)])
    header=json.dumps(doc,separators=(',',':')).encode();header+=b' '*((-len(header))%4)
    glb=struct.pack('<III',0x46546c67,2,28+len(header)+len(blob))+struct.pack('<II',len(header),0x4e4f534a)+header+struct.pack('<II',len(blob),0x004e4942)+blob
    return CadSnapshot(glb,count,len(data),'glb',dict(source='STL',unit='unknown',normal_repairs=repaired,degenerate_omitted=degenerate,encoding='binary' if binary else 'ascii'))


def step_preflight(data,cancel):
    if not data.lstrip().startswith(b'ISO-10303-21;') or not data.rstrip().endswith(b'END-ISO-10303-21;'):raise PreviewError('STEP头尾损坏或格式不支持。')
    # Bounded lexical checks only; all geometric interpretation belongs to OCCT/WASM.
    if len(data)>16*1024*1024:raise PreviewError('STEP超过16 MiB限制。')
    text=data.decode('ascii',errors='strict')
    if len(text.splitlines())>300000 or max(map(len,text.splitlines()),default=0)>1024*1024:raise PreviewError('STEP行数/行长度超限。')
    # Mask strings/comments before checking entity types/references; never resolve external paths.
    masked=bytearray(data);i=0
    while i<len(data):
        if cancel.is_set():raise PreviewError('已取消。')
        start=i
        if data[i:i+2]==b'/*':
            end=data.find(b'*/',i+2)
            if end<0:raise PreviewError('STEP注释未结束。')
            i=end+2;masked[start:i]=b' '*(i-start)
        elif data[i]==39:
            i+=1
            while True:
                end=data.find(b"'",i)
                if end<0:raise PreviewError('STEP字符串未结束。')
                i=end+1
                if i<len(data) and data[i]==39:i+=1;continue
                break
            masked[start:i]=b' '*(i-start)
        else:i+=1
    masked=masked.decode('ascii')
    keyword=re.search(r'\bFILE_SCHEMA\s*\(',masked[:65536],re.I)
    if not keyword:raise PreviewError('STEP缺少FILE_SCHEMA。')
    end=masked.find(';',keyword.start())
    schema=re.fullmatch(r'FILE_SCHEMA\s*\(\s*\((.*?)\)\s*\)\s*;',text[keyword.start():end+1],re.S|re.I)
    if not schema:raise PreviewError('STEP的FILE_SCHEMA结构异常。')
    value=schema.group(1).upper()
    protocol='AP203' if 'CONFIG_CONTROL_DESIGN' in value else 'AP214' if 'AUTOMOTIVE_DESIGN' in value else 'AP242' if 'AP242' in value else None
    if protocol is None:raise PreviewError('仅验证了STEP AP203/AP214/AP242。')
    if re.search(r'\b(?:REFERENCE|ANCHOR)\s*;',masked,re.I):raise PreviewError('STEP外部引用/锚点段暂不支持。')
    edges={};entities=0;references=0
    for record in masked.split(';'):
        if len(record)>1024*1024:raise PreviewError('STEP记录长度超限。')
        match=re.fullmatch(r'\s*#(\d+)\s*=\s*(.*)',record,re.S)
        if not match:continue
        if cancel.is_set():raise PreviewError('已取消。')
        entities+=1;body=match.group(2);refs=re.findall(r'#(\d+)',body);references+=len(refs)
        if entities>100000 or references>500000:raise PreviewError('STEP实体/引用复杂度超限。')
        if re.search(r'\b\w*(?:EXTERNAL|DOCUMENT_FILE)\w*\s*\(',body):raise PreviewError('STEP含外部依赖，本阶段拒绝加载。')
        if re.match(r'\s*ORIENTED_EDGE\b',body):edges[match.group(1)]=refs[-1] if refs else ''
    for start in edges:
        seen=set();node=start
        while node in edges:
            if node in seen or len(seen)>64:raise PreviewError('STEP方向边循环或深度超限。')
            seen.add(node);node=edges[node]
    if not entities:raise PreviewError('STEP没有有效实体。')
    units=sorted(set(re.findall(r"SI_UNIT\s*\(\s*(\.\w+\.|\$)\s*,\s*\.METRE\.\s*\)",text,re.I)))
    if re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'INCH'",text,re.I):units.append('INCH')
    return dict(source='STEP',protocol=protocol,entities=entities,source_units=units,output_unit='millimeter',experimental=True)


def read_cad(path,cancel):
    extension=Path(path).suffix.lower();data=read_source(path,cancel)
    if extension=='.stl':return encode_stl(data,cancel)
    if extension not in ('.step','.stp'):raise PreviewError('不支持此CAD格式。')
    metadata=step_preflight(data,cancel);verify_step_assets()
    return CadSnapshot(data,0,len(data),'step',metadata)


def read_cad_isolated(path,cancel):
    """Only metadata/file reads in the client; finite native-free child handles parsing."""
    import subprocess,sys,time,base64,importlib.util
    from .pdf import packet,frames,ProcessBudget
    data=read_source(path,cancel);extension=Path(path).suffix.lower()
    root=str(Path(__file__).resolve().parents[1])
    site=str(Path(next(iter(importlib.util.find_spec('PyQt6').submodule_search_locations))).parent)
    bootstrap=f'import sys,runpy;sys.path[:0]={[root,site]!r};runpy.run_module("mechanical_project_namer.cad_worker",run_name="__main__")'
    process=subprocess.Popen([sys._base_executable,'-I','-S','-c',bootstrap],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    budget=None
    try:
        budget=ProcessBudget(process.pid,1024);payload=packet({'extension':extension})+packet(data);first=True;deadline=time.monotonic()+15
        while True:
            if cancel.is_set():raise PreviewError('已取消。')
            if time.monotonic()>deadline:raise PreviewError('CAD预处理超过15秒，已终止。')
            try:
                output,_=process.communicate(payload if first else None,timeout=.1);break
            except subprocess.TimeoutExpired:first=False
        messages=frames(bytearray(output))
        if len(messages)!=1:raise PreviewError('CAD预处理进程异常退出或内存超限。')
        result=json.loads(messages[0])
        if result.get('error'):raise PreviewError(result['error'])
        if result['pid']!=process.pid:raise PreviewError('CAD预处理进程身份不一致。')
        snapshot=CadSnapshot(base64.b64decode(result['data'],validate=True),result['triangles'],len(data),result['format'],result['metadata'])
        return snapshot
    finally:
        if budget:budget.close()
        if process.poll() is None:process.kill()
        process.wait(timeout=5)
