// SPDX-License-Identifier: GPL-3.0-only
'use strict';
const generation = Number(document.body.dataset.generation);
const started = performance.now();
let embedded = null;
let cadWorker=null,cadDetails=null;
window.startCadPreview=function(){
    if(cadWorker)return;
    cadWorker=new Worker(new URL('cad-worker.js',location.href));
    cadWorker.onerror=()=>fail('STEP工作线程结束，可能超限或文件损坏。');
    cadWorker.onmessage=event=>{
        if(event.data.error){fail(event.data.error);cadWorker.terminate();cadWorker=null;return;}
        const {data,...details}=event.data;cadDetails=details;
        cadWorker.terminate();cadWorker=null;
        embedded.LoadModelFromFileList([new File([data],'display.glb')]);
    };
    cadWorker.postMessage({});
};
function report(value) { console.info('MPN_PREVIEW:' + JSON.stringify({generation, ...value})); }
function fail(message) { document.getElementById('error').textContent=message; report({state:'error', message}); }
window.previewCommand = function(command) {
    if (!embedded || !embedded.GetModel()) return;
    const viewer=embedded.GetViewer();
    const sphere=viewer.GetBoundingSphere(()=>true);
    if (command==='fit') viewer.FitSphereToWindow(sphere,false);
    else {
        const direction = command==='front' ? [0,0,1] : command==='top' ? [0,1,0] : [1,0.8,1];
        const c=sphere.center, r=sphere.radius*3;
        viewer.SetCamera(new OV.Camera(new OV.Coord3D(c.x+direction[0]*r,c.y+direction[1]*r,c.z+direction[2]*r),
            new OV.Coord3D(c.x,c.y,c.z),new OV.Coord3D(0,command==='top'?0:1,command==='top'?-1:0),45));
        viewer.FitSphereToWindow(sphere,false);
    }
    viewer.Render();
};
window.previewDiagnostics = function() {
    if (!embedded) return null;
    const v=embedded.GetViewer(), gl=v.renderer.getContext(), ext=gl.getExtension('WEBGL_debug_renderer_info');
    return {camera:v.GetCamera(), drawCalls:v.renderer.info.render.calls, triangles:v.renderer.info.render.triangles,
        renderer:ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER),
        version:gl.getParameter(gl.VERSION), width:gl.drawingBufferWidth,height:gl.drawingBufferHeight};
};
function dispose() {
    if(cadWorker){cadWorker.terminate();cadWorker=null;}
    if (!embedded) return;
    const v=embedded.GetViewer();v.Clear();v.renderer.dispose();v.renderer.forceContextLoss();embedded=null;
}
window.addEventListener('pagehide',dispose);
window.addEventListener('error',event=>fail('渲染失败：'+event.message));
try {
    embedded=new OV.EmbeddedViewer(document.getElementById('viewer'), {
        backgroundColor:new OV.RGBAColor(237,242,245,255),
        onModelLoaded:()=>{
            // Explicit local headlight replaces a remote environment-map dependency.
            const light=embedded.GetViewer().shadingModel.directionalLight;
            light.color.set(0xffffff);light.intensity=Math.PI;
            previewCommand('iso');
            requestAnimationFrame(()=>requestAnimationFrame(()=>{
                embedded.GetViewer().Render();const d=previewDiagnostics();
                if (!d || d.drawCalls<1 || d.triangles<1) {fail('模型没有产生可见绘制，请检查WebGL。');return;}
                report({state:'rendered',loadMs:performance.now()-started,meshes:embedded.GetModel().MeshCount(),cad:cadDetails,...d});
            }));
        },
        onModelLoadFailed:()=>fail('模型解析失败，请使用有效的无扩展GLB。')
    });
    if(document.body.dataset.format==='step')report({state:'cad-ready'});
    else embedded.LoadModelFromUrlList([new URL('model.glb',location.href).href]);
} catch(error) { fail('WebGL初始化或模型载入失败：'+error.message); }
