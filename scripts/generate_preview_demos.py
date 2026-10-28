# SPDX-License-Identifier: GPL-3.0-only
"""Generate original, valid GLB 2.0 fixtures. No production model or extra library."""
import argparse
import json
import math
from pathlib import Path
import struct


def encode(doc,binary):
    doc['asset']={'version':'2.0','generator':'MechanicalWorkbench fictional fixtures'}
    doc['buffers']=[{'byteLength':len(binary)}]
    text=json.dumps(doc,separators=(',',':')).encode();text+=b' '*((-len(text))%4)
    binary+=b'\0'*((-len(binary))%4)
    return struct.pack('<4sII',b'glTF',2,28+len(text)+len(binary))+struct.pack('<II',len(text),0x4E4F534A)+text+struct.pack('<II',len(binary),0x004E4942)+binary


def build(complex_model=False,assembly=False):
    if complex_model:
        positions=[];indices=[];u,v=96,48
        for i in range(u):
            for j in range(v):
                a,b=i*2*math.pi/u,j*2*math.pi/v
                positions.extend([(2+.65*math.cos(b))*math.cos(a),.65*math.sin(b),(2+.65*math.cos(b))*math.sin(a)])
        for i in range(u):
            for j in range(v):
                a=i*v+j;b=((i+1)%u)*v+j;c=((i+1)%u)*v+(j+1)%v;d=i*v+(j+1)%v
                indices.extend([a,b,c,a,c,d])
    else:
        positions=[-1,-1,-1,1,-1,-1,1,1,-1,-1,1,-1,-1,-1,1,1,-1,1,1,1,1,-1,1,1]
        indices=[0,2,1,0,3,2,4,5,6,4,6,7,0,1,5,0,5,4,3,7,6,3,6,2,0,4,7,0,7,3,1,2,6,1,6,5]
    vertices=struct.pack('<'+'f'*len(positions),*positions);faces=struct.pack('<'+'I'*len(indices),*indices)
    minimum=[min(positions[i::3]) for i in range(3)];maximum=[max(positions[i::3]) for i in range(3)]
    count=4 if assembly else 1
    doc={'scene':0,'scenes':[{'nodes':list(range(count))}],
         'nodes':[{'mesh':i,'translation':[0 if i==0 else (i-2)*2.8,0 if i==0 else 2.8,0], 'scale':[2,.35,1.5] if assembly and i==0 else [1,1,1]} for i in range(count)],
         'materials':[{'pbrMetallicRoughness':{'baseColorFactor':c,'metallicFactor':0,'roughnessFactor':.65},'doubleSided':True} for c in [[.8,.2,.13,1],[.1,.45,.85,1],[.9,.6,.12,1],[.18,.7,.35,1]][:count]],
         'meshes':[{'primitives':[{'attributes':{'POSITION':0},'indices':1,'material':i}]} for i in range(count)],
         'accessors':[{'bufferView':0,'componentType':5126,'count':len(positions)//3,'type':'VEC3','min':minimum,'max':maximum},
                      {'bufferView':1,'componentType':5125,'count':len(indices),'type':'SCALAR'}],
         'bufferViews':[{'buffer':0,'byteLength':len(vertices),'target':34962},
                        {'buffer':0,'byteOffset':len(vertices),'byteLength':len(faces),'target':34963}]}
    return doc,vertices+faces,len(indices)//3*count


def generate(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=False)
    manifest=[]
    for name,complex_model,assembly in [('简单 几何体(红).glb',False,False),('多零件 装配 😀.glb',False,True),('受控复杂 环体.glb',True,False)]:
        doc,binary,triangles=build(complex_model,assembly);data=encode(doc,binary);(folder/name).write_bytes(data)
        manifest.append(dict(name=name,bytes=len(data),triangles=triangles))
    doc,binary,_=build();doc['buffers']=[{'byteLength':len(binary)}]
    # encode sets buffers; inject an external image instead for an unambiguous rejected sample.
    doc['images']=[{'uri':'https://invalid.example/never-request.png'}]
    (folder/'外部资源.glb').write_bytes(encode(doc,binary))
    (folder/'损坏.glb').write_bytes(b'not-a-GLB')
    (folder/'samples.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path)
    args=parser.parse_args();print(json.dumps(generate(args.output),ensure_ascii=False))
