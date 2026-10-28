// SPDX-License-Identifier: GPL-3.0-only
'use strict';
// The worker receives no filesystem handles or Python bridge. OCCT uses WASM/MEMFS.
importScripts('occt-import-js.js');
self.onmessage=async function(event) {
    try {
        const occt=await occtimportjs({locateFile:name=>{
            if(name!=='occt-import-js.wasm')throw Error('Unapproved resource');
            return new URL(name,self.location.href).href;
        }});
        const response=await fetch(new URL('input.step',self.location.href));
        if(!response.ok)throw Error('STEP snapshot missing');
        const bytes=await response.arrayBuffer();
        if(bytes.byteLength>16*1024*1024)throw Error('STEP input too large');
        const parsed=occt.ReadStepFile(new Uint8Array(bytes),{linearUnit:'millimeter',linearDeflectionType:'absolute_value',linearDeflection:0.15,angularDeflection:0.35});
        if(!parsed.success || !parsed.meshes.length || parsed.meshes.length>1000)throw Error('STEP has no supported mesh or too many bodies');
        const doc={asset:{version:'2.0'},scene:0,scenes:[{nodes:[]}],nodes:[],meshes:[],materials:[],buffers:[],bufferViews:[],accessors:[]};
        const chunks=[];let offset=0,triangles=0,vertices=0;let bounds=[Infinity,Infinity,Infinity,-Infinity,-Infinity,-Infinity];const meshTriangles=[];
        function accessor(array,type,componentType) {
            const raw=new Uint8Array(array.buffer);const view=doc.bufferViews.length;
            doc.bufferViews.push({buffer:0,byteOffset:offset,byteLength:raw.byteLength});chunks.push(raw);offset+=raw.byteLength;
            doc.accessors.push({bufferView:view,componentType,count:array.length/(type==='VEC3'?3:1),type});return doc.accessors.length-1;
        }
        for(const mesh of parsed.meshes) {
            const pos=mesh.attributes.position.array,idx=mesh.index.array;
            if(pos.length%3 || idx.length%3 || !pos.length || !idx.length)throw Error('Invalid mesh layout');
            vertices+=pos.length/3;triangles+=idx.length/3;
            if(vertices>1000000 || triangles>300000)throw Error('STEP tessellation exceeds 1M vertices / 300K triangles');
            if(pos.some(x=>!Number.isFinite(x)||Math.abs(x)>1e9) || idx.some(x=>!Number.isInteger(x)||x<0||x>=pos.length/3))throw Error('Invalid coordinates / indices');
            for(let i=0;i<pos.length;i++) {bounds[i%3]=Math.min(bounds[i%3],pos[i]);bounds[3+i%3]=Math.max(bounds[3+i%3],pos[i]);}
            meshTriangles.push(idx.length/3);
            const floatPositions=new Float32Array(pos);
            for(let i=0;i<idx.length;i+=3) {
                const a=idx[i]*3,b=idx[i+1]*3,c=idx[i+2]*3;
                const ux=floatPositions[b]-floatPositions[a],uy=floatPositions[b+1]-floatPositions[a+1],uz=floatPositions[b+2]-floatPositions[a+2];
                const vx=floatPositions[c]-floatPositions[a],vy=floatPositions[c+1]-floatPositions[a+1],vz=floatPositions[c+2]-floatPositions[a+2];
                if((uy*vz-uz*vy)**2+(uz*vx-ux*vz)**2+(ux*vy-uy*vx)**2<=1e-24)throw Error('Degenerate or precision-collapsed STEP triangle');
            }
            const position=accessor(floatPositions,'VEC3',5126);
            doc.accessors[position].min=[0,1,2].map(axis=>{let v=Infinity;for(let j=axis;j<pos.length;j+=3)v=Math.min(v,pos[j]);return v;});
            doc.accessors[position].max=[0,1,2].map(axis=>{let v=-Infinity;for(let j=axis;j<pos.length;j+=3)v=Math.max(v,pos[j]);return v;});
            const color=mesh.color||[0.65,0.68,0.72];
            if(color.length!==3 || color.some(x=>!Number.isFinite(x)||x<0||x>1))throw Error('Invalid color');
            const groups=new Map();
            // Keep face-level colors as material groups, including the mesh's default color.
            const assigned=new Uint8Array(idx.length/3);
            for(const face of mesh.brep_faces||[]) {
                if(!face.color)continue;
                if(!Number.isInteger(face.first)||!Number.isInteger(face.last)||face.first<0||face.last>=assigned.length||face.last<face.first)throw Error('Invalid face range');
                if(face.color.some(x=>!Number.isFinite(x)||x<0||x>1))throw Error('Invalid face color');
                const key=JSON.stringify(face.color);if(!groups.has(key))groups.set(key,[]);
                const group=groups.get(key);
                for(let t=face.first;t<=face.last;t++){if(assigned[t])throw Error('Overlapping face range');assigned[t]=1;group.push(...idx.slice(3*t,3*t+3));}
            }
            const remaining=[];for(let t=0;t<assigned.length;t++)if(!assigned[t])remaining.push(...idx.slice(t*3,t*3+3));
            if(remaining.length)groups.set(JSON.stringify(color),[...(groups.get(JSON.stringify(color))||[]),...remaining]);
            if(groups.size>128)throw Error('Too many face materials');
            const primitives=[];
            for(const [key,indices] of groups) {
                if(doc.materials.length>=2048)throw Error('STEP material/primitive limit');
                const material=doc.materials.length;doc.materials.push({doubleSided:true,pbrMetallicRoughness:{baseColorFactor:[...JSON.parse(key),1],metallicFactor:0,roughnessFactor:.7}});
                primitives.push({attributes:{POSITION:position},indices:accessor(new Uint32Array(indices),'SCALAR',5125),material});
            }
            doc.meshes.push({name:String(mesh.name||'Body').slice(0,256),primitives});
        }
        let nodes=0,parts=0,instanceTriangles=0,primitiveInstances=0;
        function visit(node,depth) {
            if(depth>64 || ++nodes>10000)throw Error('STEP assembly hierarchy limit');
            const index=doc.nodes.length,entry={name:String(node.name||'Assembly').slice(0,256),children:[]};doc.nodes.push(entry);
            for(const mesh of node.meshes||[]) {
                if(!Number.isInteger(mesh)||mesh<0||mesh>=doc.meshes.length)throw Error('Invalid entity reference');
                entry.children.push(doc.nodes.length);doc.nodes.push({mesh,name:doc.meshes[mesh].name});parts++;
                instanceTriangles+=meshTriangles[mesh];if(instanceTriangles>300000)throw Error('STEP instances exceed 300K triangles');
                primitiveInstances+=doc.meshes[mesh].primitives.length;if(primitiveInstances>4096)throw Error('STEP draw primitive limit');
            }
            for(const child of node.children||[])entry.children.push(visit(child,depth+1));
            return index;
        }
        const root=visit(parsed.root,0);doc.nodes[root].scale=[.001,.001,.001];doc.scenes[0].nodes=[root];
        if(!parts || parts>1000 || offset>32*1024*1024)throw Error('STEP output exceeds limits');
        doc.buffers=[{byteLength:offset}];
        let json=new TextEncoder().encode(JSON.stringify(doc));const pad=(4-json.length%4)%4;
        if(json.length>2*1024*1024 || 28+json.length+pad+offset>32*1024*1024)throw Error('STEP display snapshot size limit');
        const output=new ArrayBuffer(28+json.length+pad+offset),view=new DataView(output),data=new Uint8Array(output);
        view.setUint32(0,0x46546c67,true);view.setUint32(4,2,true);view.setUint32(8,output.byteLength,true);view.setUint32(12,json.length+pad,true);view.setUint32(16,0x4e4f534a,true);
        data.set(json,20);data.fill(32,20+json.length,20+json.length+pad);let cursor=20+json.length+pad;
        view.setUint32(cursor,offset,true);view.setUint32(cursor+4,0x004e4942,true);cursor+=8;
        for(const chunk of chunks){data.set(chunk,cursor);cursor+=chunk.length;}
        self.postMessage({data:output,triangles:instanceTriangles,unique_triangles:triangles,vertices,parts,meshes:parsed.meshes.length,materials:doc.materials.length,bounds_mm:bounds,unit:'millimeter'},[output]);
    } catch(error) { self.postMessage({error:'STEP解析失败或超限：'+error.message}); }
};
