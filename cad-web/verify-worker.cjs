// SPDX-License-Identifier: GPL-3.0-only
// Test adapter only. Browser worker logic is unchanged; Node supplies local fixture bytes.
const fs=require('fs'),path=require('path'),vm=require('vm');
const root=path.resolve(__dirname,'..'),dist=path.join(__dirname,'node_modules/occt-import-js/dist');
const mode=process.argv[3]||'real';
global.self=global;self.location={href:'http://fixture.invalid/cad-worker.js'};
global.importScripts=()=>{
 global.occtimportjs=options=>require(path.join(dist,'occt-import-js.js'))({...options,locateFile:name=>path.join(dist,name)}).then(occt=>{
  if(mode==='empty')occt.ReadStepFile=()=>({success:true,meshes:[]});
  if(mode==='degenerate')occt.ReadStepFile=()=>({success:true,meshes:[{attributes:{position:{array:[0,0,0,1,0,0,0,1,0]}},index:{array:[0,0,0]}}]});
  if(mode==='bad-index')occt.ReadStepFile=()=>({success:true,meshes:[{attributes:{position:{array:[0,0,0]}},index:{array:[0,0,9]}}]});
  if(mode==='too-many')occt.ReadStepFile=()=>({success:true,meshes:[{attributes:{position:{array:[0,0,0,1,0,0,0,1,0]}},index:{array:new Array(900003).fill(0)}}]});
  return occt;
 });
};
global.fetch=async()=>({ok:true,arrayBuffer:async()=>{const b=fs.readFileSync(process.argv[2]);return b.buffer.slice(b.byteOffset,b.byteOffset+b.length);}});
self.postMessage=value=>{const {data,...metadata}=value;process.stdout.write(JSON.stringify({...metadata,glb_bytes:data?data.byteLength:0})+'\n');};
vm.runInThisContext(fs.readFileSync(path.join(root,'mechanical_project_namer/preview_assets/cad-worker.js'),'utf8'));
self.onmessage({data:{}}).catch(error=>{console.error(error);process.exitCode=1;});
