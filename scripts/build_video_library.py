"""Rebuild the local delivery index from canonical editorial manifests (no upload)."""
from pathlib import Path
import html,json
ROOT=Path(__file__).resolve().parents[1]
def href(path,label):
 return '<a href="'+html.escape(Path(path).as_uri())+'">'+html.escape(label)+'</a>'
def build():
 style='<meta charset="utf-8"><title>Biblioteca de vídeos</title><style>body{background:#141422;color:#eee;font:16px system-ui;max-width:1150px;margin:auto;padding:28px}a{color:#b6a1ff}article{display:inline-block;vertical-align:top;width:44%;padding:2%;margin:1%;background:#222234;border-radius:14px}video{width:100%;max-height:490px}h2{color:#55ddc7}p{line-height:1.6}@media(max-width:700px){article{width:94%}}</style>'
 allcards=[]
 for manifest in sorted((ROOT/'output/gestion').glob('*/piezas.json')):
  pieces=json.loads(manifest.read_text(encoding='utf-8-sig'));cards=[]
  for p in pieces:
   video=ROOT/p['video']
   if not video.is_file():raise FileNotFoundError(video)
   folder=video.parent;links=[href(video,'Vídeo')]
   for f,label in [('YouTube_titulo.txt','Título YouTube'),('YouTube_descripcion.txt','Descripción YouTube'),('Instagram_descripcion.txt','Descripción Instagram')]:
    text=folder/'textos'/f
    if text.is_file():links.append(href(text,label))
   cards.append('<article><h2>'+html.escape(p.get('display_date',p['date'])+' · '+p['brand'])+'</h2><h3>'+html.escape(p['title'])+'</h3><video controls preload="none" src="'+video.as_uri()+'?v='+str(video.stat().st_mtime_ns)+'"></video><p>'+' · '.join(links)+'</p><small>'+html.escape(p.get('publish_gate') or ('Audio corregido: comprobación técnica pasada; escucha pendiente.' if p.get('audio_revision') else 'Preparado; confirmar publicación o programación por separado.'))+'</small></article>')
  page=style+'<h1>'+html.escape(manifest.parent.name)+'</h1><p>'+href(ROOT/'VIDEOS.html','Todos los vídeos')+' · '+href(manifest.parent/'plan_publicaciones.json','Calendario y horas')+'</p>'+''.join(cards)
  (manifest.parent/'REVISAR_SEMANA.html').write_text(page,encoding='utf-8');allcards+=cards
 campaign_cards=[]
 for folder in sorted((ROOT/'output/videos/Campanas').iterdir()):
  video=folder/'video.mp4'
  if not video.is_file():continue
  brief_file=folder/'edicion/brief.json'
  brief=json.loads(brief_file.read_text(encoding='utf-8')) if brief_file.is_file() else {}
  title=brief.get('campaign_name') or folder.name.replace('_',' ')
  links=[href(video,'Vídeo')]
  for name,label in [('YouTube_titulo.txt','Título YouTube'),('YouTube_descripcion.txt','Descripción YouTube'),('Instagram_descripcion.txt','Descripción Instagram'),('TikTok_descripcion.txt','Descripción TikTok')]:
   if (folder/'textos'/name).is_file():links.append(href(folder/'textos'/name,label))
  campaign_cards.append('<article><h2>'+html.escape(title)+'</h2><video controls preload="none" src="'+video.as_uri()+'?v='+str(video.stat().st_mtime_ns)+'"></video><p>'+' · '.join(links)+'</p><small>Comprobar reglas, redes admitidas y revisión auditiva antes de publicar. Este índice no confirma envío ni programación.</small></article>')
 head=style+'<h1>Vídeos para publicar</h1><p>'+href(ROOT/'output/gestion/2026-11-01_08/ITINERARIO.html','Itinerario actualizado')+' · '+href(ROOT/'docs/ORGANIZACION_VIDEOS.md','Cómo está organizado')+'</p><h2>Campañas</h2>'+''.join(campaign_cards)+'<h2>Contenido orgánico</h2>'
 (ROOT/'VIDEOS.html').write_text(head+''.join(allcards),encoding='utf-8')
 return len(allcards)
if __name__=='__main__':print('Piezas indexadas:',build())
