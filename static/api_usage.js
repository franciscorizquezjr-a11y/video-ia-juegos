/* Provider balances and local usage are deliberately presented as separate data. */
(() => {
    let requestId = 0;
    const units = {requests: 'Peticiones', tokens: 'Tokens informados', characters: 'Caracteres enviados (estimación)'};
    const names = {gemini: 'Gemini', elevenlabs: 'ElevenLabs', groq: 'Groq'};
    const colors = {gemini: '#8b9dff', elevenlabs: '#41e1c2', groq: '#ffab76'};
    const chartUnits = {};
    const fmt = n => n == null ? 'No disponible' : new Intl.NumberFormat('es-ES').format(n);
    const date = value => value ? new Intl.DateTimeFormat('es-ES', {dateStyle:'short',timeStyle:'short',timeZone:'Europe/Madrid'}).format(new Date(value*1000)) : 'Sin fecha informada';
    function el(tag, cls, text) { const node=document.createElement(tag);if(cls)node.className=cls;if(text!=null)node.textContent=text;return node; }
    function line(parent,label,value) {const row=el('div','api-usage-line');row.append(el('span','',label),el('strong','',value));parent.append(row);}
    function progress(parent,used,limit) {
        if(used==null || limit==null || limit<=0)return;
        const pct=Math.min(100,Math.max(0,used/limit*100));
        const track=el('div','api-usage-progress');track.setAttribute('role','progressbar');track.setAttribute('aria-valuemin','0');track.setAttribute('aria-valuemax','100');track.setAttribute('aria-valuenow',String(Math.round(pct)));track.setAttribute('aria-label','Porcentaje consumido');
        const bar=el('div',pct>=100?'danger':pct>=80?'warning':'');bar.style.width=pct+'%';track.append(bar);parent.append(track);
    }
    function chart(parent,provider,unit,timezone) {
        const values=provider.series.map(d=>d[unit]||0);
        const knownTokens=provider.local.requests-provider.local.errors-provider.local.unknown_token_calls;
        if(!provider.local.requests || (unit==='tokens' && knownTokens<=0)){
            parent.append(el('p','api-usage-empty',!provider.local.requests?'Todavía no hay llamadas registradas en este período.':'No hay tokens informados por el proveedor en este período.'));return;
        }
        const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 320 100');svg.setAttribute('role','img');svg.setAttribute('aria-label',`${units[unit]} por día. Zona horaria: ${timezone}`);
        const max=Math.max(1,...values),step=300/values.length;
        values.forEach((v,i)=>{const rect=document.createElementNS(ns,'rect');rect.setAttribute('x',String(10+i*step));rect.setAttribute('y',String(82-v/max*70));rect.setAttribute('width',String(Math.max(1,step-1)));rect.setAttribute('height',String(v/max*70));rect.setAttribute('fill',colors[provider.id]);rect.setAttribute('rx','1');const title=document.createElementNS(ns,'title');title.textContent=`${provider.series[i].date}: ${fmt(v)}`;rect.append(title);svg.append(rect);});
        const top=document.createElementNS(ns,'text');top.setAttribute('x','10');top.setAttribute('y','10');top.textContent=fmt(Math.max(...values));svg.append(top);
        const start=document.createElementNS(ns,'text');start.setAttribute('x','10');start.setAttribute('y','98');start.textContent=provider.series[0].date.slice(5);svg.append(start);
        const end=document.createElementNS(ns,'text');end.setAttribute('x','310');end.setAttribute('y','98');end.setAttribute('text-anchor','end');end.textContent=provider.series.at(-1).date.slice(5);svg.append(end);parent.append(svg);
    }
    function quota(parent,p) {
        const q=p.quota;parent.append(el('h4','','Datos del proveedor'));
        if(!p.configured){parent.append(el('p','api-usage-note','Guarda primero la clave en Ajustes.'));return;}
        if(p.id==='elevenlabs'){
            if(q.status==='ok'){
                line(parent,'Caracteres del ciclo',`${fmt(q.used)} / ${fmt(q.limit)}`);progress(parent,q.used,q.limit);line(parent,'Disponibles en el ciclo',fmt(q.remaining));
                const periods={monthly_period:'Mensual','3_month_period':'Cada 3 meses','6_month_period':'Cada 6 meses',annual_period:'Anual'};
                line(parent,'Renovación',date(q.reset_at));line(parent,'Periodicidad',periods[q.period]||'No informada');line(parent,'Plan',q.tier||'No informado');
                if(q.overage_possible)parent.append(el('p','api-usage-note','El plan permite consumo adicional: agotar el cupo incluido no garantiza que se detengan los cargos.'));
            } else {
                const messages={permission_required:'Falta permiso de lectura. En la clave de ElevenLabs activa Usuario → Acceso y pulsa Actualizar.',unauthorized:'Consulta no autorizada. Revisa la clave y sus permisos.',rate_limited:'El proveedor limita temporalmente las consultas. Reintenta más tarde.',network_error:'No se pudo conectar con ElevenLabs.',expired:'El ciclo guardado ha vencido. Actualiza para consultar el nuevo saldo.',not_refreshed:'Saldo pendiente de consultar.',unavailable:'El proveedor no ha facilitado el saldo.'};
                parent.append(el('p','api-usage-note api-usage-warning',messages[q.status]||'Saldo no disponible.'));
            }
            if(q.at)parent.append(el('p','api-usage-note','Consulta: '+date(q.at)));
        } else if(p.id==='groq'){
            if(!q.models?.length)parent.append(el('p','api-usage-note','Límites pendientes de la próxima llamada real. No se genera texto para consultarlos.'));
            for(const snapshot of q.models||[]){const box=el('div','api-usage-snapshot');box.append(el('strong','',snapshot.model||'Modelo'));const l=snapshot.limits;
                line(box,'Peticiones disponibles / día',l['remaining-requests']==null?'Ventana sin datos vigentes':`${fmt(l['remaining-requests'])} / ${fmt(l['limit-requests'])}`);
                line(box,'Tokens disponibles / minuto',l['remaining-tokens']==null?'Ventana sin datos vigentes':`${fmt(l['remaining-tokens'])} / ${fmt(l['limit-tokens'])}`);
                box.append(el('p','api-usage-note','Última respuesta: '+date(snapshot.at)+' · reinicio de peticiones: '+date(l.reset_requests_at)+' · tokens: '+date(l.reset_tokens_at)));parent.append(box);
            }
            parent.append(el('p','api-usage-note','Son límites de ritmo, no saldo en dinero. Una ventana caducada requiere otra respuesta para conocer el disponible.'));
        } else parent.append(el('p','api-usage-note','La clave por sí sola no ofrece aquí un saldo global. Consulta las cuotas reales por modelo/proyecto en AI Studio. El límite diario de peticiones se renueva a medianoche del Pacífico.'));
        const link=el('a','api-usage-link','Abrir panel oficial ↗');link.href=p.dashboard_url;link.target='_blank';link.rel='noopener noreferrer';parent.append(link);
    }
    function budget(parent,p) {
        const b=p.budget,details=el('details','api-usage-budget');details.append(el('summary','','Presupuesto local opcional'));
        details.append(el('p','api-usage-note','Es un objetivo de esta app que eliges tú. No modifica ni consulta la cuota oficial, ni incluye otras aplicaciones.'));
        if(b.limit!=null){line(details,'Consumido / objetivo',`${fmt(b.used)} / ${fmt(b.limit)}`);progress(details,b.used,b.limit);line(details,'Restante local',fmt(b.remaining));line(details,'Reinicio del objetivo',date(b.reset_at));if(b.unknown_calls)details.append(el('p','api-usage-warning api-usage-note','Hay llamadas sin detalle de tokens; el consumo mostrado está incompleto.'));}
        const form=el('form','api-usage-budget-form');
        const limit=el('input','form-input');limit.type='number';limit.min='1';limit.step='1';limit.max='1000000000000';limit.placeholder='Sin límite local';limit.value=b.limit??'';limit.setAttribute('aria-label','Límite local de '+names[p.id]);
        const unit=el('select','form-input');unit.setAttribute('aria-label','Unidad del presupuesto');
        for(const key of (p.id==='elevenlabs'?['characters','requests']:['requests','tokens'])){const opt=el('option','',units[key]);opt.value=key;unit.append(opt);}unit.value=b.unit;
        const period=el('select','form-input');period.setAttribute('aria-label','Periodicidad del presupuesto');for(const [key,label] of [['daily','Diario'],['monthly','Mensual, desde el día 1']]){const opt=el('option','',label);opt.value=key;period.append(opt);}period.value=b.period;
        const save=el('button','btn btn-secondary','Guardar objetivo');save.type='submit';const feedback=el('p','api-usage-note');feedback.setAttribute('role','status');
        form.append(limit,unit,period,save);form.addEventListener('submit',async event=>{event.preventDefault();save.disabled=true;try{const r=await fetch('/api/usage/budget/'+p.id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({limit:limit.value===''?null:Number(limit.value),unit:unit.value,period:period.value})});if(!r.ok)throw Error('No se pudo guardar el objetivo');await window.loadApiUsage(false,false);}catch(e){feedback.textContent=e.message;}finally{save.disabled=false;}});details.append(form,feedback);parent.append(details);
    }
    function draw(data) {
        const root=document.getElementById('apiUsageProviders');if(!root)return;root.replaceChildren();
        for(const p of data.providers){const card=el('article','api-usage-provider');card.style.setProperty('--provider-color',colors[p.id]);const header=el('div','api-usage-card-header');header.append(el('h3','',names[p.id]),el('span','api-usage-badge',p.configured?'Clave guardada':'Sin clave'));card.append(header);quota(card,p);
            const local=el('div','api-usage-local');local.append(el('h4','','Uso local registrado'));line(local,'Peticiones / errores',`${fmt(p.local.requests)} / ${fmt(p.local.errors)}`);
            if(p.id==='elevenlabs')line(local,'Caracteres enviados',fmt(p.local.characters)+' (estimación)');else {const known=p.local.requests-p.local.errors-p.local.unknown_token_calls;line(local,'Tokens informados',known>0?fmt(p.local.tokens):'Sin datos');if(p.local.unknown_token_calls)local.append(el('p','api-usage-note',`${fmt(p.local.unknown_token_calls)} llamadas sin detalle de tokens.`));}
            const select=el('select','form-input api-usage-unit');select.setAttribute('aria-label','Métrica de la gráfica de '+names[p.id]);for(const key of (p.id==='elevenlabs'?['characters','requests']:['requests','tokens'])){const opt=el('option','',units[key]);opt.value=key;select.append(opt);}select.value=chartUnits[p.id]||(p.id==='elevenlabs'?'characters':'requests');
            const graph=el('div','api-usage-chart');const redraw=()=>{chartUnits[p.id]=select.value;graph.replaceChildren();chart(graph,p,select.value,data.timezone);};select.addEventListener('change',redraw);redraw();local.append(select,graph);card.append(local);budget(card,p);root.append(card);
        }
        const note=document.getElementById('apiUsageScope');if(note)note.textContent=data.scope+' Inicio: '+date(data.started_at)+'. Gráficas: '+data.timezone+'.';
    }
    window.loadApiUsage = async function(force=false,refresh=true) {
        const current=++requestId,button=document.getElementById('apiUsageRefresh'),status=document.getElementById('apiUsageStatus');if(!status)return;
        const days=document.getElementById('apiUsageDays')?.value||'30';if(button)button.disabled=true;status.textContent='Cargando consumo…';
        try{
            let response=await fetch('/api/usage?days='+encodeURIComponent(days));if(!response.ok)throw Error('No se pudo leer el registro local');let data=await response.json();if(current!==requestId)return;draw(data);
            if(refresh && data.providers.some(p=>p.id==='elevenlabs'&&p.configured)){
                status.textContent='Consultando el saldo de ElevenLabs…';response=await fetch('/api/usage/refresh?force='+(force?'true':'false'),{method:'POST'});if(!response.ok)throw Error('No se pudo actualizar el saldo');
                response=await fetch('/api/usage?days='+encodeURIComponent(days));if(!response.ok)throw Error('No se pudo actualizar el panel');data=await response.json();if(current!==requestId)return;draw(data);
            }
            status.textContent='Actualizado '+date(data.updated_at)+' · las consultas de saldo no generan audio ni texto.';
        }catch(e){if(current===requestId)status.textContent=e.message;}finally{if(current===requestId&&button)button.disabled=false;}
    };
    document.addEventListener('DOMContentLoaded',()=>{document.getElementById('apiUsageRefresh')?.addEventListener('click',()=>window.loadApiUsage(true));document.getElementById('apiUsageDays')?.addEventListener('change',()=>window.loadApiUsage(false,false));});
})();
