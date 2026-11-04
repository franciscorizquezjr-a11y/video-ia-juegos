const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../static/logo_lab.js'), 'utf8');
const result = {run_id:'a'.repeat(32), name:'Portal 2', app_id:620, capsule_size:[1000,500],
    crop_bounds:[100,100,900,400], crop_url:'/crop', original_capsule_url:'/capsule',
    warning:'Review', detector:{method:'opencv-mser'}, algorithms:[
        {id:'color',label:'Color',status:'completed',image_url:'/color',download_url:'/download-color',logo_size:[300,100],duration_seconds:.1},
        {id:'rembg',label:'rembg',status:'completed',image_url:'/rembg',download_url:'/download-rembg',logo_size:[300,100],duration_seconds:1},
        {id:'ben2',label:'BEN2',status:'error',error:'Missing model',duration_seconds:2},
        {id:'grabcut',label:'GrabCut',status:'not_applicable',error:'Test',duration_seconds:.3}]};
class Element {
    constructor(tag='div') { this.tag=tag; this.value=''; this.hidden=false; this.events={}; this.children=[]; this.className=''; this.classList={add:name=>this.className+=' '+name}; }
    addEventListener(name,fn) { this.events[name]=fn; }
    appendChild(child) { this.children.push(child); }
    replaceChildren(...children) { this.children=children; }
    querySelectorAll(selector) {
        const matches=[];
        for (const child of this.children) {
            if (selector[0]==='.' && child.className.split(' ').includes(selector.slice(1))) matches.push(child);
            matches.push(...child.querySelectorAll(selector));
        }
        return matches;
    }
    getBoundingClientRect() { return {left:0,top:0,width:500,height:250}; }
    getContext() { return {clearRect(){},drawImage(){},fillRect(){},strokeRect(){}}; }
    setPointerCapture() {}
}
function harness(saved={}, fetch=async()=>({ok:true,json:async()=>({job_id:'test'})})) {
    const elements=new Map(), streams=[], timers=[], requests=[];
    let stored=JSON.stringify(saved);
    class Stream {
        constructor(url) { this.url=url; streams.push(this); }
        close() { this.closed=true; }
        emit(data) { this.onmessage({data:JSON.stringify(data)}); }
    }
    class FakeImage { set src(url) { this.url=url; this.onload(); } }
    const document={createElement:tag=>new Element(tag),getElementById(id) {
        if (!elements.has(id)) { const element=new Element(); if (id==='logoLabPreviewBg') element.value='checker'; elements.set(id,element); }
        return elements.get(id);
    }};
    vm.runInNewContext(script,{document,FormData,Image:FakeImage,fetch:async(...args)=>{requests.push(args);return fetch(...args);},EventSource:Stream,
        localStorage:{getItem:()=>stored,setItem:(k,v)=>stored=v},setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},window:{addEventListener(){}}});
    return {el:id=>elements.get(id),streams,timers,requests,saved:()=>JSON.parse(stored)};
}
function pointer(canvas,name,x,y) { canvas.events[name]({clientX:x,clientY:y,pointerId:1,button:0,preventDefault(){}}); }
const tick=()=>new Promise(setImmediate);
(async()=>{
    const upload=harness();
    await upload.el('logoLabUpload').events.click();
    assert.match(upload.el('logoLabStatus').textContent,/Selecciona/);
    upload.el('logoLabFile').files=[new Blob(['test'],{type:'image/png'})];
    await upload.el('logoLabUpload').events.click();
    assert.equal(upload.requests[0][0],'/api/logo-lab/upload');
    assert.ok(upload.requests[0][1].body instanceof FormData);
    assert.equal(upload.requests[0][1].headers,undefined);
    assert.equal(upload.el('logoLabFile').disabled,true);
    upload.streams[0].emit({type:'completed',result:{...result,app_id:undefined,source_kind:'upload'},message:'Done'});
    assert.match(upload.el('logoLabInfo').textContent,/Imagen subida/);
    const ui=harness(); ui.el('logoLabSteam').value='620';
    await ui.el('logoLabForm').events.submit({preventDefault(){}});
    assert.equal(ui.streams[0].url,'/api/jobs/test/stream');
    assert.equal(ui.el('logoLabStart').disabled,true);
    ui.streams[0].emit({type:'progress',progress:65,message:'Comparing'});
    assert.equal(ui.el('logoLabProgress').value,65);
    ui.streams[0].emit({type:'completed',result,message:'Done'});
    assert.equal(ui.el('logoLabStart').disabled,false);
    const cards=ui.el('logoLabAlgorithms').children;
    assert.equal(cards.length,4);
    assert.equal(cards[0].children.find(n=>n.tag==='a').href,'/download-color');
    assert.match(cards[2].children[1].textContent,/Missing model/);
    assert.match(cards[3].children[1].textContent,/No aplicable/);
    assert.equal(ui.saved().job_id,undefined);
    const restored=harness(ui.saved());
    assert.equal(restored.el('logoLabResult').hidden,false);
    assert.equal(restored.el('logoLabSteam').value,'620');
    restored.el('logoLabPreviewBg').value='dark'; restored.el('logoLabPreviewBg').events.change();
    assert.equal(restored.el('logoLabAlgorithms').querySelectorAll('.logo-lab-preview')[0].className,'logo-lab-preview logo-lab-dark');
    // Pointer coordinates use the displayed image scale, rather than CSS pixels.
    const canvas=restored.el('logoLabCanvas');
    pointer(canvas,'pointerdown',250,125); pointer(canvas,'pointermove',275,135); pointer(canvas,'pointerup',275,135);
    assert.deepEqual(restored.saved().draft_crop.bounds,[150,120,950,420]);
    const draftRestored=harness(restored.saved()); assert.equal(draftRestored.el('logoLabX').value,150);
    restored.el('logoLabResetCrop').events.click();
    pointer(canvas,'pointerdown',450,125); pointer(canvas,'pointermove',425,125); pointer(canvas,'pointerup',425,125);
    assert.deepEqual(restored.saved().draft_crop.bounds,[100,100,850,400]);
    pointer(canvas,'pointerdown',10,10); pointer(canvas,'pointermove',150,100); pointer(canvas,'pointerup',150,100);
    assert.deepEqual(restored.saved().draft_crop.bounds,[20,20,300,200]);
    restored.el('logoLabX').value=25; restored.el('logoLabX').events.input();
    assert.equal(restored.saved().draft_crop.bounds[0],25);
    restored.el('logoLabWidth').value=10000; restored.el('logoLabWidth').events.change();
    assert.equal(restored.el('logoLabRecalculate').disabled,true);
    restored.el('logoLabResetCrop').events.click();
    await restored.el('logoLabRecalculate').events.click();
    assert.equal(restored.requests[0][0],'/api/logo-lab/recalculate');
    assert.deepEqual(JSON.parse(restored.requests[0][1].body),{run_id:result.run_id,crop_bounds:result.crop_bounds});
    assert.equal(restored.el('logoLabRecalculate').disabled,true);
    const running=harness({job_id:'existing',steam_input:'620',result},async()=>({ok:true,json:async()=>({status:'running'})}));
    await tick(); assert.equal(running.streams[0].url,'/api/jobs/existing/stream');
    running.streams[0].emit({type:'error',message:'No logo'});
    assert.equal(running.saved().job_id,undefined); assert.equal(running.el('logoLabStart').disabled,false);
    assert.equal(running.el('logoLabAlgorithms').children.length,4);
    const restarted=harness({job_id:'old',result},async()=>({status:404})); await tick();
    assert.equal(restarted.saved().job_id,undefined); assert.equal(restarted.el('logoLabRecalculate').disabled,false);
    const reconnect=harness({job_id:'existing'},async()=>({ok:true,json:async()=>({status:'running'})})); await tick();
    reconnect.streams[0].onerror(); assert.equal(reconnect.timers.length,1); assert.equal(reconnect.streams[0].closed,true);
    const legacy=harness({result:{...result,algorithms:undefined,logo_url:'/old',download_url:'/old-download',logo_size:[200,100]}});
    assert.equal(legacy.el('logoLabAlgorithms').children.length,1);
    assert.equal(legacy.el('logoLabAlgorithms').children[0].children.find(n=>n.tag==='a').href,'/old-download');
    console.log('Logo Lab UI: algorithms, crop drawing/moving/resizing, numeric edits, recalculation, legacy results, SSE and recovery passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
