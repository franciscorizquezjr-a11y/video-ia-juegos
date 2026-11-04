/* Four algorithms sharing one crop, and the existing jobs/SSE contract. */
(() => {
    const el = id => document.getElementById(id);
    const key = 'steamLogoLabSession';
    const fields = ['logoLabX', 'logoLabY', 'logoLabWidth', 'logoLabHeight'];
    let session = {}, source = null, reconnectTimer = null, submitting = false;
    let activeResult = null, crop = null, capsule = null, drag = null, generation = 0;
    const save = () => { try { localStorage.setItem(key, JSON.stringify(session)); } catch (_) {} };
    const status = message => { el('logoLabStatus').textContent = message; };
    const locked = () => submitting || Boolean(session.job_id);
    function busy(value) {
        for (const id of ['logoLabStart','logoLabSteam','logoLabFile','logoLabUpload','logoLabResetCrop',...fields]) el(id).disabled = value;
        el('logoLabRecalculate').disabled = value || !activeResult?.run_id || !crop;
        el('logoLabProgress').hidden = !value;
    }
    function applyBackground() {
        for (const preview of el('logoLabAlgorithms').querySelectorAll('.logo-lab-preview'))
            preview.className = `logo-lab-preview logo-lab-${el('logoLabPreviewBg').value}`;
    }
    function renderAlgorithms(result) {
        const container = el('logoLabAlgorithms');
        container.replaceChildren();
        const entries = result.algorithms || [{id:'legacy',label:'Resultado anterior',status:'completed',
            image_url:result.logo_url,download_url:result.download_url,logo_size:result.logo_size}];
        for (const entry of entries) {
            const card = document.createElement('article'); card.className = 'card form-card';
            const title = document.createElement('h3'); title.textContent = entry.label; card.appendChild(title);
            const info = document.createElement('p'); info.className = 'logo-lab-algorithm-status';
            if (entry.status === 'completed') {
                const preview = document.createElement('div'); preview.className = 'logo-lab-preview';
                const img = document.createElement('img'); img.src = entry.image_url; img.alt = `Logo con ${entry.label}`;
                preview.appendChild(img); card.appendChild(preview);
                info.textContent = `${(entry.logo_size || []).join(' × ')} px` +
                    (entry.duration_seconds !== undefined ? ` · ${entry.duration_seconds.toFixed(2)} s` : '') +
                    (entry.device ? ` · ${entry.device.toUpperCase()}` : '');
                const link = document.createElement('a'); link.className = 'btn btn-primary';
                link.href = entry.download_url; link.download = ''; link.textContent = 'Descargar PNG';
                card.appendChild(info); card.appendChild(link);
            } else {
                info.classList.add(entry.status === 'not_applicable' ? 'logo-lab-warning' : 'logo-lab-error');
                info.textContent = `${entry.status === 'not_applicable' ? 'No aplicable' : 'Error'}: ${entry.error || 'Sin resultado.'}`;
                if (entry.duration_seconds !== undefined) info.textContent += ` (${entry.duration_seconds.toFixed(2)} s)`;
                card.appendChild(info);
            }
            container.appendChild(card);
        }
        applyBackground();
    }
    function validBounds(bounds, size) {
        return Array.isArray(bounds) && bounds.length === 4 && bounds.every(Number.isInteger) &&
            bounds[0]>=0 && bounds[1]>=0 && bounds[2]<=size[0] && bounds[3]<=size[1] &&
            bounds[2]-bounds[0]>=3 && bounds[3]-bounds[1]>=3;
    }
    function draw() {
        if (!capsule || !crop) return;
        const canvas=el('logoLabCanvas'), ctx=canvas.getContext('2d'); if (!ctx) return;
        ctx.clearRect(0,0,canvas.width,canvas.height);
        ctx.drawImage(capsule,0,0,canvas.width,canvas.height);
        const [x1,y1,x2,y2]=crop;
        ctx.fillStyle='rgba(0,0,0,.48)';
        ctx.fillRect(0,0,canvas.width,y1); ctx.fillRect(0,y2,canvas.width,canvas.height-y2);
        ctx.fillRect(0,y1,x1,y2-y1); ctx.fillRect(x2,y1,canvas.width-x2,y2-y1);
        const ratio=canvas.width/Math.max(1,canvas.getBoundingClientRect().width);
        ctx.strokeStyle='#ff9867'; ctx.lineWidth=2*ratio; ctx.strokeRect(x1,y1,x2-x1,y2-y1);
        ctx.fillStyle='#ff9867';
        for (const [x,y] of [[x1,y1],[x2,y1],[x1,y2],[x2,y2],[(x1+x2)/2,y1],[(x1+x2)/2,y2],[x1,(y1+y2)/2],[x2,(y1+y2)/2]])
            ctx.fillRect(x-4*ratio,y-4*ratio,8*ratio,8*ratio);
    }
    function setCrop(bounds, persist=true) {
        crop=bounds.map(Math.round);
        const [x1,y1,x2,y2]=crop;
        for (const [i,value] of [x1,y1,x2-x1,y2-y1].entries()) el(fields[i]).value=value;
        const changed=JSON.stringify(crop)!==JSON.stringify(activeResult?.crop_bounds);
        el('logoLabCropStatus').textContent=changed ? 'Recorte modificado. Recalcula para actualizar los PNG.' : 'Recorte utilizado en los resultados actuales.';
        if (persist && activeResult) { session.draft_crop={run_id:activeResult.run_id,bounds:crop}; save(); }
        draw(); busy(locked());
    }
    function showResult(result, restoreDraft=false) {
        activeResult=result; capsule=null; drag=null;
        el('logoLabResult').hidden=false; el('logoLabName').textContent=result.name;
        const kind=result.source_kind==='header_fallback' ? 'Cabecera (cápsula principal no disponible)' : 'Cápsula principal';
        const detector=result.detector?.method==='manual' ? 'Recorte manual' : result.detector ? 'Detección CV tradicional' : 'Extracción anterior';
        el('logoLabInfo').textContent=result.source_kind==='upload' ? 'Imagen subida · Borrado de fondo sobre la imagen completa' : `Steam ${result.app_id} · ${kind} · ${detector}`;
        el('logoLabCrop').src=result.crop_url;
        el('logoLabWarning').textContent=result.warning || 'Revisa los bordes y las letras de cada resultado.';
        renderAlgorithms(result); crop=null;
        const size=result.capsule_size;
        const token=++generation;
        if (!size || !result.crop_bounds) { busy(locked()); return; }
        const canvas=el('logoLabCanvas'); canvas.width=size[0]; canvas.height=size[1];
        const draft=restoreDraft && session.draft_crop?.run_id===result.run_id ? session.draft_crop.bounds : null;
        setCrop(validBounds(draft,size) ? draft : result.crop_bounds,false);
        const image=new Image();
        image.onload=()=>{ if (token===generation) { capsule=image; draw(); } };
        image.onerror=()=>{ if (token===generation) el('logoLabCropStatus').textContent='No se pudo cargar la cápsula. Puedes ajustar el recorte mediante los campos.'; };
        image.src=result.original_capsule_url || (result.run_id ? `/api/logo-lab/results/${result.run_id}/capsule.png` : result.capsule_url);
        busy(locked());
    }
    function close() { if (source) source.close(); source=null; clearTimeout(reconnectTimer); }
    function event(data) {
        status(data.message || data.error || 'Procesando…'); el('logoLabProgress').value=data.progress || 0;
        if (data.type==='completed' || data.status==='completed') {
            close(); session.result=data.result; delete session.job_id; delete session.draft_crop;
            save(); showResult(data.result); busy(false);
        } else if (data.type==='error' || data.status==='error') {
            close(); delete session.job_id; save(); busy(false);
        }
    }
    function connect(jobId) {
        close(); busy(true); source=new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/stream`);
        source.onmessage=e=>{ try { event(JSON.parse(e.data)); } catch (_) { status('No se ha podido leer el progreso.'); } };
        source.onerror=()=>{ close(); status('Reconectando con la comparación…'); reconnectTimer=setTimeout(()=>restoreJob(jobId),2000); };
    }
    async function restoreJob(jobId) {
        try {
            const response=await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
            if (response.status===404) {
                delete session.job_id; save(); busy(false);
                status('El servidor se ha reiniciado. Puedes iniciar otra comparación o recalcular la anterior.'); return;
            }
            if (!response.ok) throw new Error('connection');
            const data=await response.json();
            if (['completed','error'].includes(data.status)) event(data); else connect(jobId);
        } catch (_) { status('Esperando conexión con el servidor…'); reconnectTimer=setTimeout(()=>restoreJob(jobId),4000); }
    }
    async function start(url,body) {
        if (locked()) return;
        submitting=true; busy(true); el('logoLabProgress').value=0; status('Iniciando comparación…'); save();
        try {
            const response=await fetch(url,body instanceof FormData ? {method:'POST',body} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
            const data=await response.json();
            if (!response.ok) throw new Error(typeof data.detail==='string' ? data.detail : 'No se ha podido iniciar la comparación.');
            session.job_id=data.job_id; save(); connect(data.job_id);
        } catch (error) { status(error.message); }
        finally { submitting=false; busy(Boolean(session.job_id)); }
    }
    el('logoLabForm').addEventListener('submit',async e=>{
        e.preventDefault(); if (locked()) return;
        session.steam_input=el('logoLabSteam').value.trim();
        await start('/api/logo-lab/extract',{steam_input:session.steam_input});
    });
    el('logoLabRecalculate').addEventListener('click',async()=>{
        if (!activeResult?.run_id || !validBounds(crop,activeResult.capsule_size)) return;
        await start('/api/logo-lab/recalculate',{run_id:activeResult.run_id,crop_bounds:crop});
    });
    el('logoLabUpload').addEventListener('click',async()=>{
        if (locked()) return;
        const file=el('logoLabFile').files?.[0];
        if (!file) { status('Selecciona una imagen ya recortada.'); return; }
        if (file.size>20*1024*1024) { status('La imagen no puede superar 20 MB.'); return; }
        const body=new FormData(); body.append('file',file);
        await start('/api/logo-lab/upload',body);
    });
    el('logoLabResetCrop').addEventListener('click',()=>{ if (!locked() && activeResult?.crop_bounds) setCrop(activeResult.crop_bounds); });
    function updateFromFields() {
        if (locked() || !activeResult?.capsule_size) return;
        const [x,y,width,height]=fields.map(field=>Number(el(field).value)), bounds=[x,y,x+width,y+height];
        if (validBounds(bounds,activeResult.capsule_size)) setCrop(bounds);
        else { el('logoLabCropStatus').textContent='Usa píxeles enteros dentro de la imagen y un tamaño mínimo de 3 × 3.'; el('logoLabRecalculate').disabled=true; }
    }
    for (const id of fields) {
        el(id).addEventListener('input',updateFromFields);
        el(id).addEventListener('change',updateFromFields);
    }
    const canvas=el('logoLabCanvas');
    function point(e) {
        const rect=canvas.getBoundingClientRect();
        return [Math.max(0,Math.min(canvas.width,Math.round((e.clientX-rect.left)*canvas.width/rect.width))),
                Math.max(0,Math.min(canvas.height,Math.round((e.clientY-rect.top)*canvas.height/rect.height)))];
    }
    canvas.addEventListener('pointerdown',e=>{
        if (locked() || !capsule || !crop || (e.button!==undefined && e.button!==0)) return;
        const [x,y]=point(e), [x1,y1,x2,y2]=crop;
        const tolerance=10*canvas.width/Math.max(1,canvas.getBoundingClientRect().width);
        const near=y>=y1-tolerance && y<=y2+tolerance && x>=x1-tolerance && x<=x2+tolerance;
        const edges={left:near && Math.abs(x-x1)<tolerance,right:near && Math.abs(x-x2)<tolerance,
                     top:near && Math.abs(y-y1)<tolerance,bottom:near && Math.abs(y-y2)<tolerance};
        const mode=Object.values(edges).some(Boolean) ? 'resize' : x>=x1 && x<=x2 && y>=y1 && y<=y2 ? 'move' : 'draw';
        drag={start:[x,y],bounds:crop.slice(),edges,mode,pointer:e.pointerId};
        canvas.setPointerCapture(e.pointerId); e.preventDefault();
    });
    canvas.addEventListener('pointermove',e=>{
        if (!drag || drag.pointer!==e.pointerId || locked()) return;
        const [x,y]=point(e), [sx,sy]=drag.start; let [x1,y1,x2,y2]=drag.bounds;
        if (drag.mode==='move') {
            const dx=Math.max(-x1,Math.min(canvas.width-x2,x-sx)), dy=Math.max(-y1,Math.min(canvas.height-y2,y-sy));
            x1+=dx; x2+=dx; y1+=dy; y2+=dy;
        } else if (drag.mode==='draw') {
            x1=Math.min(sx,x); x2=Math.max(sx,x); y1=Math.min(sy,y); y2=Math.max(sy,y);
        } else {
            if (drag.edges.left) x1=Math.min(x,x2-3); if (drag.edges.right) x2=Math.max(x,x1+3);
            if (drag.edges.top) y1=Math.min(y,y2-3); if (drag.edges.bottom) y2=Math.max(y,y1+3);
        }
        if (validBounds([x1,y1,x2,y2],activeResult.capsule_size)) setCrop([x1,y1,x2,y2]);
    });
    for (const name of ['pointerup','pointercancel','lostpointercapture']) canvas.addEventListener(name,()=>{drag=null;});
    el('logoLabPreviewBg').addEventListener('change',applyBackground);
    window.addEventListener('resize',draw);
    try { session=JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch (_) {}
    el('logoLabSteam').value=session.steam_input || '';
    if (session.result) { showResult(session.result,true); status('Última comparación recuperada.'); }
    if (session.job_id) { busy(true); restoreJob(session.job_id); } else busy(false);
    window.addEventListener('beforeunload',close);
})();
