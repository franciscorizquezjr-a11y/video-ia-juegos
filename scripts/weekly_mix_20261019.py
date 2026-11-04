"""Add the existing social-card format to the weekly plan, keeping narrated originals."""
from pathlib import Path
import json,sys,shutil,subprocess,re,os,html
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from utils import create_text_card
from ffmpeg_wrapper import FFmpegWrapper
from PIL import Image,ImageDraw,ImageFont
PACK=ROOT/'output/gestion/semana_2026-10-19'
SELECTION={
('PartyLoot','01_deep_dish_dungeon'):"Your co-op party needs a chef. Explore a strange dungeon with up to two friends, gather supplies, and cook meals that boost your stats. Then head back into the dark for puzzles and monsters. Who's cooking tonight?",
('PartyLoot','04_escape_academy_2'):"Pick the friend who actually reads the clues. This upcoming escape-room sequel turns the whole campus into a puzzle. Explore hidden passages and solve rooms together, online or in split-screen. Who are you bringing?",
('NextLoot','02_absolum'):"This gorgeous cartoon is a combo machine. Absolum mixes hand-drawn arcade brawling with roguelite runs, branching paths, and magic. Fight solo or team up with a friend on the couch or online. Would you play?",
('NextLoot','05_valheim'):"Your peaceful Viking village is only the beginning. Build a home, sail into storms, and prepare to face giant bosses. Valheim lets you explore alone or with up to nine friends. Builder, sailor, or boss hunter?"
}
def run(cmd):
 r=subprocess.run(cmd,capture_output=True)
 if r.returncode:raise RuntimeError(r.stderr.decode(errors='replace')[-1400:])
 return r

def assets(brand,folder_name,text):
 folder=PACK/brand/folder_name;style_path=next((ROOT/'Presets'/brand).glob('*.json'));s=json.loads(style_path.read_text(encoding='utf-8'))
 card=folder/'tarjeta.png'
 create_text_card(text,card,username=s['card_username'],handle=s['card_handle'],width=864,height=490,bg_color=s['card_bg_color'],text_color=s['card_text_color'],border_color=s['card_border_color'],border_width=6,radius=40,avatar_text=s['card_avatar_text'],avatar_bg=s['card_avatar_bg'],avatar_image_path=ROOT/s['card_avatar_path'],font_username_family=s['card_font_username'],font_handle_family=s['card_font_handle'],font_body_family=s['card_font_body'],font_size_body=38,avatar_radius=52,font_size_username=36,font_size_handle=28,highlight_first_sentence=True,highlight_color=s['card_border_color'])
 old=json.loads((folder/'recipe.json').read_text(encoding='utf-8'));game=old['game_name'];game_folder=ROOT/'GamesFootage'/re.sub(r'[<>:"/\\|?*]','',game);logo=game_folder/'game_logo.png'
 overlay=Image.new('RGBA',(1080,1920),(0,0,0,0))
 if logo.is_file():
  im=Image.open(logo).convert('RGBA');im=im.crop(im.getchannel('A').getbbox());im.thumbnail((680,165),Image.Resampling.LANCZOS);overlay.alpha_composite(im,((1080-im.width)//2,1470-im.height//2))
 else:
  font=ImageFont.truetype('C:/Windows/Fonts/segoeuib.ttf',38);draw=ImageDraw.Draw(overlay);draw.text((540,1470),game.upper(),font=font,anchor='mm',fill='white',stroke_width=2,stroke_fill='#101522')
 overlay.save(folder/'tarjeta_logo_overlay.png')
 recipe={**s,'game_name':game,'source_type':'local','local_file_path':str(ROOT/old['trailer_video'].lstrip('/')),'start_sec':0,'end_sec':22,'resolution':'1080p','preset_name':brand,'card_text':text,'overlay_type':'card','background_audio_volume':.5,'narration_enabled':False,'highlight_first_sentence':True,'game_logo_used':logo.is_file(),'card_output_width':864,'card_output_height':490,'card_output_y':220,'video_output_y_offset':90}
 (folder/'tarjeta_recipe.json').write_text(json.dumps(recipe,ensure_ascii=False,indent=2),encoding='utf-8')
 return recipe

def render():
 for (brand,folder_name),text in SELECTION.items():
  folder=PACK/brand/folder_name;previous=json.loads((folder/'tarjeta_recipe.json').read_text(encoding='utf-8')) if (folder/'tarjeta_recipe.json').exists() else None
  recipe=assets(brand,folder_name,text);out=folder/'video_tarjeta.mp4'
  if '--force' not in sys.argv and previous==recipe and out.exists() and (folder/'tarjeta_verification.json').exists():print('REUSE',brand,folder_name,flush=True);continue
  source=Path(recipe['local_file_path']);trim=folder/'tarjeta_source_22s.mp4';base=folder/'tarjeta_base.mp4'
  run(['ffmpeg','-v','error','-y','-i',str(source),'-t','22','-map','0:v:0','-map','0:a:0?','-c','copy',str(trim)])
  print('RENDER CARD',brand,folder_name,flush=True)
  FFmpegWrapper(temp_dir=folder/'render_temp').compose_vertical_with_blurred_bg(trim,folder/'tarjeta.png',base,target_width=1080,target_height=1920,card_y=220,zoom=1,video_y_position=90)
  run(['ffmpeg','-v','error','-y','-i',str(base),'-i',str(folder/'tarjeta_logo_overlay.png'),'-filter_complex','[0:v][1:v]overlay=0:0,format=yuv420p[v]','-map','[v]','-map','0:a:0?','-af','volume=0.5,alimiter=limit=0.89:level=0:latency=1','-c:v','libx264','-preset','veryfast','-crf','18','-c:a','aac','-b:a','192k','-movflags','+faststart',str(out)])
  probe=json.loads(run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(out)]).stdout);v=next(x for x in probe['streams'] if x['codec_type']=='video');duration=float(probe['format']['duration']);assert (v['width'],v['height'])==(1080,1920) and 21.9<=duration<=22.2
  run(['ffmpeg','-v','error','-i',str(out),'-f','null','NUL'])
  data={'file':str(out.relative_to(ROOT)).replace('\\','/'),'format':'social_card','duration':duration,'width':v['width'],'height':v['height'],'audio':any(x['codec_type']=='audio' for x in probe['streams']),'narration_enabled':False,'game_logo_used':recipe['game_logo_used'],'decoded':True,'card_text':text}
  (folder/'tarjeta_verification.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
  frame=folder/'tarjeta_preview.jpg';run(['ffmpeg','-v','error','-y','-ss','2','-i',str(out),'-frames:v','1','-vf','scale=540:960',str(frame)])
  print('DONE CARD',brand,folder_name,round(duration,2),flush=True)

LABELS={'clean_narrated':'Narrado limpio · voz y subtítulos','social_card':'Tarjeta del canal arriba · sin locución'}

def atomic_json(path,data):
 pending=path.with_name(path.name+'.pending');pending.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');pending.replace(path)

def update_review(plan):
 esc=html.escape;cards=[]
 for piece in plan['pieces']:
  rel=Path(piece['file']).relative_to(PACK.relative_to(ROOT)).as_posix();preview=ROOT/Path(piece['file']).parent/'tarjeta_preview.jpg'
  poster=(' poster="'+esc(str(preview.relative_to(PACK)).replace('\\','/'))+'"') if piece['selected_format']=='social_card' else ''
  card='<article><h2>'+esc(piece['brand'])+' · '+esc(piece['game'])+'</h2><p class="format">'+esc(LABELS[piece['selected_format']])+'</p><video controls preload="metadata"'+poster+' src="'+esc(rel)+'"></video><h3>'+esc(piece['youtube_title'])+'</h3><details><summary>Título y descripción de YouTube</summary><pre>'+esc(piece['youtube_description'])+'</pre></details><p><a href="'+esc(rel)+'" download>Descargar versión seleccionada</a></p>'
  for alt in piece.get('alternatives',[]):
   alt_rel=Path(alt['file']).relative_to(PACK.relative_to(ROOT)).as_posix();card+='<details><summary>Comparar con '+esc(alt['label'])+'</summary><p>Alternativa conservada; la carpeta Para_subir contiene la versión seleccionada arriba.</p><video controls preload="none" src="'+esc(alt_rel)+'"></video><a href="'+esc(alt_rel)+'" download>Descargar alternativa</a></details>'
  cards.append(card+'</article>')
 names={'NL-EN-YT':'Next Loot · YouTube','NL-EN-TT':'Next Loot · TikTok','PL-EN-YT':'Party Loot · YouTube','PL-EN-IG':'Party Loot · Instagram','PL-EN-TT':'Party Loot · TikTok'}
 rows=[]
 for e in plan['publications']:
  rel=Path(e['file']).relative_to(PACK.relative_to(ROOT)).as_posix();text=str(Path(rel).with_suffix('.txt')).replace('\\','/')
  vals=[esc(e['planned_at'][:10]),esc(names[e['channel']]),esc(e['planned_at'][11:16]),esc(e['game']),esc(LABELS[e['selected_format']]),'Preparado; sin programar',f'<a href="{esc(rel)}">Vídeo</a> · <a href="{esc(text)}">Texto</a>']
  rows.append('<tr>'+''.join('<td>'+x+'</td>' for x in vals)+'</tr>')
 page='<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Semana 19–23 octubre · Formatos combinados</title><style>body{font:15px system-ui;background:#11151d;color:#e9eef8;margin:30px auto;max-width:1300px;padding:0 20px}h1{font-size:30px}.notice{padding:18px;background:#32291a;border-left:4px solid #ffc16b;border-radius:8px}.format{color:#9fe3d7;font-size:13px}.strategy{color:#c7c0ec;line-height:1.6}table{min-width:950px;border-collapse:collapse;width:100%;margin:24px 0;font-size:13px}td,th{padding:10px;text-align:left;border-bottom:1px solid #394354}.table{overflow:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}article{background:#1c2330;border:1px solid #374155;padding:18px;border-radius:12px;min-width:0}article h2{font-size:18px}video{width:100%;max-height:500px;background:#000;border-radius:8px}h3{font-size:15px}pre{white-space:pre-wrap;font:14px/1.6 system-ui}a{color:#79ddca}summary{cursor:pointer}details p{font-size:12px;color:#aeb7c8}</style><h1>Semana del 19 al 23 de octubre de 2026</h1><p>5 juegos · 2 marcas · 10 vídeos seleccionados · 25 publicaciones previstas</p><p class="strategy"><strong>Dos formatos por marca:</strong> 2 con tarjeta del canal arriba + 3 narrados limpios.<br>Party Loot: tarjeta lunes y jueves. Next Loot: tarjeta martes y viernes. Las otras 4 versiones narradas se conservan para comparar.</p><p class="notice"><strong>Preparados, pendientes de programar en las redes.</strong> Horas de Madrid. La selección ya está aplicada en Para_subir. Comparar una alternativa aquí no cambia los archivos seleccionados ni programa ninguna publicación.</p><p><a href="#videos">Ver vídeos</a> · <a href="#calendario">Calendario y textos por red</a> · <a href="Control_semana_20261019.xlsx">Control en Excel</a></p><h2 id="videos">Vídeos seleccionados</h2><div class="grid">'+''.join(cards)+'</div><h2 id="calendario">Calendario propuesto</h2><div class="table"><table><thead><tr><th>Día</th><th>Cuenta</th><th>Hora Madrid</th><th>Juego</th><th>Formato</th><th>Estado</th><th>Archivos</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div></html>'
 (PACK/'REVISAR_SEMANA.html').write_text(page,encoding='utf-8')


def apply_mix():
 plan_path=PACK/'plan_publicaciones.json';plan=json.loads(plan_path.read_text(encoding='utf-8'))
 for (brand,name) in SELECTION:
  folder=PACK/brand/name;assert (folder/'video_tarjeta.mp4').is_file() and json.loads((folder/'tarjeta_verification.json').read_text(encoding='utf-8'))['decoded']
 backup=ROOT/'archive/development/2026-10-18/weekly_mix_before';backup.mkdir(parents=True,exist_ok=True)
 for src in [plan_path,ROOT/'data/publication_registry.json',PACK/'REVISAR_SEMANA.html',PACK/'LEEME.md',PACK/'Control_semana_20261019.xlsx']:
  dest=backup/src.name
  if not dest.exists():shutil.copy2(src,dest)
 byid={}
 for piece in plan['pieces']:
  current=ROOT/piece['file'];folder=current.parent;brand=folder.parent.name;key=(brand,folder.name);clean=folder/'video.mp4'
  piece['available_formats']={'clean_narrated':str(clean.relative_to(ROOT)).replace('\\','/')}
  if key in SELECTION:
   piece['available_formats']['social_card']=str((folder/'video_tarjeta.mp4').relative_to(ROOT)).replace('\\','/');piece['selected_format']='social_card';piece['file']=piece['available_formats']['social_card'];piece['format']=LABELS['social_card'];piece['alternatives']=[{'id':'clean_narrated','label':'narrado limpio con voz','file':piece['available_formats']['clean_narrated']}];piece['card_text']=SELECTION[key]
  else:
   piece['selected_format']='clean_narrated';piece['file']=piece['available_formats']['clean_narrated'];piece['format']=LABELS['clean_narrated'];piece['alternatives']=[]
  byid[piece['id']]=piece
 for e in plan['publications']:
  piece=byid[e['piece']];e['selected_format']=piece['selected_format'];e['format']=piece['format'];e['source_video']=piece['file'];upload=ROOT/e['file'];source=ROOT/piece['file']
  if not os.path.samefile(upload,source):
   saved=backup/'Para_subir'/upload.parent.name/upload.name;saved.parent.mkdir(parents=True,exist_ok=True)
   if not saved.exists():
    try:os.link(upload,saved)
    except OSError:shutil.copy2(upload,saved)
   pending=upload.with_name(upload.stem+'.mixed.pending.mp4')
   try:os.link(source,pending)
   except OSError:shutil.copy2(source,pending)
   pending.replace(upload)
  text='FECHA PROPUESTA: '+e['planned_at'][:10]+' '+e['planned_at'][11:16]+' Europe/Madrid\nFORMATO: '+piece['format']+'\nESTADO: Preparado, pendiente de programar en la red.\n\n'+('TITULO\n'+e['title']+'\n\n' if e.get('title') else '')+'DESCRIPCION\n'+e['description']+'\n'
  upload.with_suffix('.txt').write_text(text,encoding='utf-8')
 plan['format_strategy']={'clean_narrated':6,'social_card':4,'note':'Dos vídeos de tarjeta y tres narrados por marca. Variedad editorial; los juegos y días difieren, no es un experimento causal de rendimiento.'}
 atomic_json(plan_path,plan)
 atomic_json(PACK/'format_selection.json',{'week':'2026-10-19','pieces':[{'piece':p['id'],'selected_format':p['selected_format'],'available_formats':p['available_formats']} for p in plan['pieces']]})
 registry_path=ROOT/'data/publication_registry.json';registry=json.loads(registry_path.read_text(encoding='utf-8'));pubs={e['id']:e for e in plan['publications']}
 for key,selected in [('pieces',byid),('publications',pubs)]:
  for row in registry[key]:
   if row['id'] in selected:
    fields=['file','format','selected_format','available_formats','alternatives','card_text'] if key=='pieces' else ['selected_format','format','source_video']
    row.update({field:selected[row['id']][field] for field in fields if field in selected[row['id']]})
 atomic_json(registry_path,registry)
 update_review(plan)
 original_checks=PACK/'verification_narrados_originales.json'
 if not original_checks.exists():shutil.copy2(PACK/'verification.json',original_checks)
 original=json.loads(original_checks.read_text(encoding='utf-8'));checks=[]
 for piece in plan['pieces']:
  folder=(ROOT/piece['file']).parent
  info=json.loads((folder/'tarjeta_verification.json').read_text(encoding='utf-8')) if piece['selected_format']=='social_card' else next(x for x in original if Path(x['file'])==Path(piece['file']))
  checks.append({**info,'brand':piece['brand'],'game':piece['game'],'selected_format':piece['selected_format']})
 atomic_json(PACK/'verification.json',checks)
 md='# Semana del 19 al 23 de octubre de 2026 · dos formatos\n\n10 vídeos seleccionados para 25 publicaciones: 4 de tarjeta social (10 publicaciones) y 6 narrados limpios (15 publicaciones). Los 10 narrados originales se conservan; las cuatro alternativas están en la página de revisión.\n\n| Día | Juego | Party Loot | Next Loot Games |\n|---|---|---|---|\n'
 games=['deep_dish_dungeon','absolum','enshrouded','escape_academy_2','valheim']
 for day,slug in enumerate(games,19):
  ps=[p for p in plan['pieces'] if p['id'].endswith('-'+slug)];game=ps[0]['game'];party=next(p for p in ps if p['brand']=='Party Loot');nxt=next(p for p in ps if p['brand']=='Next Loot Games');md+=f'| {day}/10 | {game} | {LABELS[party["selected_format"]]} | {LABELS[nxt["selected_format"]]} |\n'
 md+='\nTarjeta social: avatar, nombre, handle y colores del preset del canal. Texto en inglés con la primera frase destacada, gameplay completo debajo y logo oficial cuando está disponible. Audio original al 50%, sin locución nueva. Las variantes narradas conservan voz, subtítulos y mezcla anterior. Se reutilizan los montajes; no se han consumido APIs en esta revisión.\n\nHorarios propuestos Europe/Madrid: Party Loot Instagram 13:00, YouTube 18:00, TikTok 20:30. Next Loot TikTok 19:00 y YouTube 20:00. Ninguna publicación se ha programado en una red.\n\nPara subir, usa siempre Para_subir: los vídeos y TXT de las cinco cuentas ya reflejan la selección. REVISAR_SEMANA.html permite comparar con las alternativas narradas sin modificar la selección. format_selection.json registra la elección por pieza para futuras revisiones.\n'
 (PACK/'LEEME.md').write_text(md,encoding='utf-8')
 control=ROOT/'CONTROL_PUBLICACIONES.md';s=control.read_text(encoding='utf-8');note='\nRevisión del planteamiento semanal (18/10/2026): 2 tarjetas sociales y 3 narrados por marca. Total 4 tarjetas + 6 narrados seleccionados; 25 publicaciones previstas. Se conservan las cuatro alternativas narradas y los horarios propuestos. La carpeta Para_subir refleja la mezcla. Ninguna publicación programada externamente.\n'
 if 'Revisión del planteamiento semanal (18/10/2026)' not in s:control.write_text(s+note,encoding='utf-8')
 print('Mixed plan applied: 4 social cards, 6 narrated videos; 25 upload copies; originals preserved.',flush=True)

if __name__=='__main__':
 if len(sys.argv)>1 and sys.argv[1]=='assets':
  for (b,f),t in SELECTION.items():assets(b,f,t)
 elif len(sys.argv)>1 and sys.argv[1]=='apply':apply_mix()
 else:render()
