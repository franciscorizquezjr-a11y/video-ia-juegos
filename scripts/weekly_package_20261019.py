"""Verify weekly exports, create review page and append only genuinely prepared records."""
from pathlib import Path
import sys,json,subprocess,shutil,os,html,copy
from datetime import date,timedelta
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from weekly_videos_20261019 import PACK,ASSETS,GAMES,BRIEFS,run
CHANNELS=[('NL-EN-YT','NextLoot','YouTube','20:00'),('NL-EN-TT','NextLoot','TikTok','19:00'),('PL-EN-YT','PartyLoot','YouTube','18:00'),('PL-EN-IG','PartyLoot','Instagram','13:00'),('PL-EN-TT','PartyLoot','TikTok','20:30')]

def publication_rows(entries):
    rows=[]
    for e in entries:
        rel=Path(e['file']).relative_to(PACK.relative_to(ROOT)).as_posix()
        text=str(Path(rel).with_suffix('.txt')).replace('\\','/')
        cells=[html.escape(e['planned_at'][:10]),html.escape(e['channel']),html.escape(e['planned_at'][11:16]),html.escape(e['game']),'Preparado; sin programar',f'<a href="{html.escape(rel)}">Vídeo</a> · <a href="{html.escape(text)}">Texto</a>']
        rows.append('<tr>'+''.join('<td>'+cell+'</td>' for cell in cells)+'</tr>')
    return ''.join(rows)

def improve_review_page(page):
    import re
    names={'NL-EN-YT':'Next Loot · YouTube','NL-EN-TT':'Next Loot · TikTok','PL-EN-YT':'Party Loot · YouTube','PL-EN-IG':'Party Loot · Instagram','PL-EN-TT':'Party Loot · TikTok'}
    for key,name in names.items():page=page.replace('>'+key+'</td>','>'+name+'</td>')
    page=page.replace('table{border-collapse','table{min-width:760px;border-collapse')
    if 'id="videos"' not in page:
        match=re.search(r'<div class="grid">.*?</div>(?=</html>)',page,re.S)
        if match:
            grid=match.group(0);page=page[:match.start()]+page[match.end():]
            page=page.replace('<div class="table">','<h2 id="videos">Vídeos</h2>'+grid+'<h2 id="calendario">Calendario propuesto</h2><div class="table">',1)
    page=page.replace('<p><a href="Control_semana_20261019.xlsx">Abrir control semanal en Excel</a></p>','<p><a href="#videos">Ver vídeos</a> · <a href="#calendario">Ver calendario y textos</a> · <a href="Control_semana_20261019.xlsx">Control en Excel</a></p>')
    return page

def finalize():
    entries=[];pieces=[];verification=[]
    for n,(slug,aid) in enumerate(GAMES):
        brief=BRIEFS[slug];game=json.loads((ASSETS/(slug+'.source.json')).read_text(encoding='utf-8'))['name']
        day=str(date(2026,10,19)+timedelta(days=n))
        for brand,preset in [('party','PartyLoot'),('next','NextLoot')]:
            folder=PACK/preset/f'{n+1:02}_{slug}';video=folder/'video.mp4'
            if slug=='valheim' and brand=='next':
                import time
                deadline=time.monotonic()+1200
                while time.monotonic()<deadline:
                    candidate=json.loads((folder/'result.json').read_text(encoding='utf-8'))
                    if candidate['render_details']['source'].endswith('_montage_v2.mp4'):break
                    time.sleep(2)
                else:raise RuntimeError('La revisión final de Valheim sigue pendiente')
            result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
            probe=json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)]).stdout)
            v=next(x for x in probe['streams'] if x['codec_type']=='video');a=next(x for x in probe['streams'] if x['codec_type']=='audio');duration=float(probe['format']['duration'])
            assert (v['width'],v['height'])==(1080,1920)
            assert v['pix_fmt']=='yuv420p' and a['codec_name']=='aac'
            details=result['render_details'];narration=details['narration'];assert narration['enabled'] and narration['layout']=='clean' and not details['card_enabled']
            assert narration['duration']+narration['offset']<=duration-.20
            assert duration-narration['duration']-narration['offset']<.7
            run(['ffmpeg','-v','error','-i',str(video),'-f','null','NUL'])
            audio=subprocess.run(['ffmpeg','-v','info','-i',str(video),'-vn','-af','volumedetect','-f','null','NUL'],capture_output=True)
            import re
            levels={k:float(x) for k,x in re.findall(r'(mean_volume|max_volume): ([\-\d.]+) dB',audio.stderr.decode(errors='replace'))}
            assert levels.get('max_volume',-100)>-25
            info={'brand':preset,'game':game,'file':str(video.relative_to(ROOT)),'duration':round(duration,3),'width':v['width'],'height':v['height'],'fps':v['r_frame_rate'],'audio':a['codec_name'],'game_logo_used':details.get('game_logo_used',False),'levels':levels,'narration_duration':narration['duration'],'tail_seconds':round(duration-narration['duration']-narration['offset'],3),'decoded':True}
            verification.append(info);(folder/'verification.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
            pid=f'W20261019-{preset}-{slug}'
            title=brief[brand+'_title'];name='Party Loot' if brand=='party' else 'Next Loot Games'
            yt_description=brief['summary']+'\n\n'+brief['question']+'\nSubscribe to '+name+(' for your next game night.' if brand=='party' else ' for more game discoveries.')+'\n\nGame and official trailer: https://store.steampowered.com/app/'+str(aid)+'/\n\n'+brief['tags']+' #Shorts'
            pieces.append({'id':pid,'game':game,'language':'en','format':'Narrado limpio, escenario y subtítulos','file':str(video.relative_to(ROOT)).replace('\\','/'),'status':'Preparado','youtube_title':title,'youtube_description':yt_description,'source':f'https://store.steampowered.com/app/{aid}/','batch':'2026-10-19','brand':name})
            for channel,cp,platform,time in CHANNELS:
                if cp!=preset:continue
                desc=yt_description if platform=='YouTube' else brief['question']+'\n\n'+brief['summary']+'\n\nFollow '+('@partylootgames' if platform=='Instagram' else name)+(' for your next game night.' if brand=='party' else ' for more game discoveries.')+'\n\n'+brief['tags']
                dest=PACK/'Para_subir'/f'{preset}_{platform}';dest.mkdir(parents=True,exist_ok=True)
                upload=dest/f'{day}_{slug}.mp4'
                if not upload.exists():
                    try:os.link(video,upload)
                    except OSError:shutil.copy2(video,upload)
                text='FECHA PROPUESTA: '+day+' '+time+' Europe/Madrid\nESTADO: Preparado, pendiente de programar en la red.\n\n'+('TITULO\n'+title+'\n\n' if platform=='YouTube' else '')+'DESCRIPCION\n'+desc+'\n'
                upload.with_suffix('.txt').write_text(text,encoding='utf-8')
                entry={'id':f'PUB-{pid}-{platform}','channel':channel,'piece':pid,'platform':platform,'language':'en','status':'Preparado','date':None,'time':None,'planned_at':day+'T'+time+':00+02:00','timezone':'Europe/Madrid','url':None,'title':title if platform=='YouTube' else None,'description':desc,'complete':'Sí','views':None,'likes':None,'comments':None,'metricsDate':None,'campaignPlatform':None,'campaign':None,'notes':'Fecha y hora propuestas. NO programado en la plataforma: conexión pendiente. Archivo verificado localmente.','file':str(upload.relative_to(ROOT)).replace('\\','/'),'batch':'2026-10-19','game':game}
                entries.append(entry)
    assert len(pieces)==10 and len(entries)==25
    plan={'week_start':'2026-10-19','timezone':'Europe/Madrid','external_scheduling_status':'Pendiente de conexión con las cuentas. No programado.','timing_strategy':'Una hora fija por canal esta semana. Horarios propuestos como prueba editorial, sin datos suficientes para afirmar que son los óptimos.','channels':CHANNELS,'pieces':pieces,'publications':entries}
    (PACK/'plan_publicaciones.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    (PACK/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
    registry_path=ROOT/'data/publication_registry.json';registry=json.loads(registry_path.read_text(encoding='utf-8'))
    backup=ROOT/'archive/development/2026-10-18/weekly_before';backup.mkdir(parents=True,exist_ok=True)
    if not (backup/'publication_registry.json').exists():shutil.copy2(registry_path,backup/'publication_registry.json')
    for key,new in [('pieces',pieces),('publications',entries)]:
        existing={r['id'] for r in registry[key]};registry[key].extend(r for r in new if r['id'] not in existing)
    registry['snapshot_date']='2026-10-18'
    registry_path.write_text(json.dumps(registry,ensure_ascii=False,indent=2),encoding='utf-8')
    esc=html.escape
    rows=publication_rows(entries)
    cards=[]
    for p in pieces:
        file=Path(p['file']);folder=ROOT/file.parent
        cards.append('<article><h2>'+esc(p['brand'])+' · '+esc(p['game'])+'</h2><video controls preload="metadata" src="'+esc(file.relative_to(PACK.relative_to(ROOT)).as_posix())+'"></video><h3>'+esc(p['youtube_title'])+'</h3><details><summary>Texto de YouTube</summary><pre>'+esc(p['youtube_description'])+'</pre></details><a href="'+esc(file.relative_to(PACK.relative_to(ROOT)).as_posix())+'" download>Descargar vídeo</a></article>')
    page='<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Semana 19–23 octubre · Vídeos</title><style>body{font:15px system-ui;background:#11151d;color:#e9eef8;margin:30px auto;max-width:1300px;padding:0 20px}h1{font-size:30px}.notice{padding:18px;background:#32291a;border-left:4px solid #ffc16b;border-radius:8px}table{border-collapse:collapse;width:100%;margin:24px 0;font-size:13px}td,th{padding:10px;text-align:left;border-bottom:1px solid #394354}.table{overflow:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}article{background:#1c2330;border:1px solid #374155;padding:18px;border-radius:12px}article h2{font-size:18px}video{width:100%;max-height:500px;background:#000;border-radius:8px}h3{font-size:15px}pre{white-space:pre-wrap;font:14px/1.6 system-ui}a{color:#79ddca}summary{cursor:pointer}article:has(h2){min-width:0}</style><h1>Vídeos para el 19–23 de octubre de 2026</h1><p>5 juegos · 2 marcas · 10 vídeos Full HD · 25 publicaciones previstas</p><p class="notice"><strong>Preparados, pendientes de programar en las redes.</strong> Este calendario local no publica ni confirma reservas externas. Horas de Madrid. Los textos para cada plataforma están junto a cada vídeo en Para_subir.</p><p><a href="Control_semana_20261019.xlsx">Abrir control semanal en Excel</a></p><div class="table"><table><thead><tr><th>Día</th><th>Canal</th><th>Hora propuesta</th><th>Juego</th><th>Estado</th><th>Archivos</th></tr></thead><tbody>'+rows+'</tbody></table></div><div class="grid">'+''.join(cards)+'</div></html>'
    page=improve_review_page(page)
    (PACK/'REVISAR_SEMANA.html').write_text(page,encoding='utf-8')
    md='# Semana del 19 al 23 de octubre de 2026\n\nDiez vídeos verificados en 1080 × 1920, 30 fps, narración en inglés y subtítulos. Cinco por marca, aptos para las cinco cuentas confirmadas.\n\n**Estado: preparados. No programados en ninguna red.** Falta conectar las cuentas con un publicador. Las horas son propuestas de prueba, no garantías de visitas. Europe/Madrid (UTC+2 durante esta semana).\n\n| Fecha | Juego | Next Loot YT | Next Loot TikTok | Party Loot YT | Party Loot IG | Party Loot TikTok |\n|---|---|---|---|---|---|---|\n'
    for n,(slug,aid) in enumerate(GAMES):md+='| '+str(date(2026,10,19)+timedelta(days=n))+' | '+json.loads((ASSETS/(slug+'.source.json')).read_text(encoding='utf-8'))['name']+' | 20:00 | 19:00 | 18:00 | 13:00 | 20:30 |\n'
    md+='\nAbre REVISAR_SEMANA.html para ver todos los vídeos. En Para_subir hay una carpeta por cuenta con vídeo y texto listo para copiar. Los MP4 repetidos son enlaces físicos cuando el sistema lo permite y comparten espacio.\n\nEl registro data/publication_registry.json incorpora las diez piezas y 25 propuestas sin cambiar publicaciones anteriores. El Excel existente no se sincroniza automáticamente.\n\nEn YouTube la hora de publicación no se conoce como factor del rendimiento a largo plazo, aunque puede influir en las primeras visitas. Fuente: https://support.google.com/youtube/answer/141805?hl=en . TikTok ofrece programación nativa: https://ads.tiktok.com/business/en-US/blog/introducing-video-scheduler-now-you-can-plan-tiktoks-in-advance . No se asume una penalización automática por programar ni se garantiza viralidad.\n'
    (PACK/'LEEME.md').write_text(md,encoding='utf-8')
    control=ROOT/'CONTROL_PUBLICACIONES.md'
    note='\n\nSemana 19–23/10/2026: [revisión de los diez vídeos](archive/produccion_anterior/2026-11-01/output_finales/semana_2026-10-19/REVISAR_SEMANA.html), [plan semanal](archive/produccion_anterior/2026-11-01/output_finales/semana_2026-10-19/LEEME.md) y [control Excel semanal](archive/produccion_anterior/2026-11-01/output_finales/semana_2026-10-19/Control_semana_20261019.xlsx). Veinticinco publicaciones propuestas para las cinco cuentas. Estado: Preparado; ninguna programada en las redes. El registro JSON contiene estas propuestas.\n'
    old=control.read_text(encoding='utf-8')
    if 'Semana 19–23/10/2026:' not in old:control.write_text(old+note,encoding='utf-8')
    if (PACK/'format_selection.json').exists():
        from weekly_mix_20261019 import apply_mix
        apply_mix()
    print('Verified 10 videos; prepared 25 upload packages; prior publication states preserved. No external posts scheduled.',flush=True)
if __name__=='__main__':finalize()
