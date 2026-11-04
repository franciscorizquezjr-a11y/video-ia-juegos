"""Build review page, per-network upload folders and a proposed calendar."""
import sys,json,html,os,shutil,subprocess,datetime,re
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(sys.argv[1]).resolve()
PACK=ROOT/"output/gestion/semana_2026-10-26"
BRIEFS=json.loads((PACK/"briefs.json").read_text(encoding="utf-8"))
def dump(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
def write(name,text):
    (PACK/name).write_text(text,encoding="utf-8")
def link(src,dst):
    dst.parent.mkdir(parents=True,exist_ok=True)
    if dst.exists():return
    try:os.link(src,dst)
    except OSError:shutil.copy2(src,dst)
calendar=[];cards=[];measurement=[]
slots={"NextLoot":{"YouTube":"20:00","TikTok":"19:00"},"PartyLoot":{"YouTube":"18:00","TikTok":"20:30","Instagram":"13:00"}}
for b in BRIEFS:
    folder=PACK/b["brand"]/(b["date"]+"_"+b["slug"])
    verification=json.loads((folder/"verification.json").read_text(encoding="utf-8"))
    meta=json.loads((ROOT/"input/semana_2026-10-26"/(b["slug"]+".source.json")).read_text(encoding="utf-8"))
    game=meta["name"];piece=b["brand"]+"_"+b["date"]+"_"+b["slug"]
    (folder/"script.txt").write_text(b["text"]+"\n\n"+("Follow Party Loot for your next game night." if b["brand"]=="PartyLoot" else "Follow Next Loot Games for more discoveries."),encoding="utf-8")
    store="https://store.steampowered.com/app/"+str(meta["steam_appid"])+"/"
    for network,hhmm in slots[b["brand"]].items():
        out=PACK/"Para_subir"/(b["brand"]+"_"+network)/(b["date"]+"_"+b["slug"])
        target=out.with_suffix(".mp4");link(folder/"video.mp4",target)
        cta=("Subscribe to " if network=="YouTube" else "Follow ")+("Next Loot Games for more game discoveries." if b["brand"]=="NextLoot" else "Party Loot for your next game night.")
        caption=b["summary"]+"\n\n"+cta+"\n\n"+b["tags"]+(" #Shorts" if network=="YouTube" else "")
        if network=="TikTok":caption += "\n\nVoice-over generated with AI."
        if network=="YouTube":caption=b["title"]+"\n\n"+caption+"\n\nOfficial game: "+store
        out.with_suffix(".txt").write_text(caption,encoding="utf-8")
        dt=datetime.datetime.fromisoformat(b["date"]+"T"+hhmm).replace(tzinfo=ZoneInfo("Europe/Madrid"))
        row=dict(piece_id=piece,brand=b["brand"],network=network,game=game,planned_at=dt.isoformat(),timezone="Europe/Madrid",status="Preparado; pendiente de programar",file=target.relative_to(ROOT).as_posix(),caption=out.with_suffix(".txt").relative_to(ROOT).as_posix(),privacy_check_required=network=="TikTok",ai_audio_disclosure_required=network=="TikTok",duration=verification["duration"])
        calendar.append(row)
        for window in [24,48,168]:
            measurement.append(dict(piece_id=piece,brand=b["brand"],network=network,planned_at=dt.isoformat(),window_hours=window,total_views=None,engaged_views=None,stayed_percent=None,average_view_seconds=None,average_percentage_viewed=None,search_share_percent=None,subscribers_or_followers=None,likes=None,comments=None,shares=None,saves=None,feed_eligibility=None,public_visibility_confirmed=None,actual_published_at=None))
    rel=folder.relative_to(PACK).as_posix()
    cards.append('<article><h2>'+html.escape(b["brand"]+" · "+b["date"])+'</h2><h3>'+html.escape(b["title"])+'</h3><video controls preload="none" poster="'+rel+'/review_2.jpg" src="'+rel+'/video.mp4"></video><p>'+str(round(verification["duration"],1))+' s · '+html.escape(b["summary"])+'</p><p><a href="'+rel+'/script.txt">Guion</a> · <a href="'+html.escape(store)+'">Ficha oficial</a> · <a href="'+rel+'/video.mp4" download>MP4</a></p></article>')
dump(PACK/"plan_publicaciones.json",calendar);dump(PACK/"medicion_24h_48h_7d.json",measurement)
dump(PACK/"package_manifest.json",dict(created_at=datetime.datetime.now(ZoneInfo("Europe/Madrid")).isoformat(),unique_videos=len(BRIEFS),account_count=5,upload_files=len(calendar),cooldown_days=21,external_publications_performed=0))
rows="".join("<tr><td>"+html.escape(r["planned_at"][:10])+"</td><td>"+html.escape(r["brand"])+"</td><td>"+r["network"]+"</td><td>"+r["planned_at"][11:16]+"</td><td>"+html.escape(r["game"])+"</td><td><a href=\""+html.escape(str(Path(r["caption"]).relative_to("output/gestion/semana_2026-10-26")).replace("\\","/"))+"\">Texto</a></td></tr>" for r in calendar)
write("REVISAR_SEMANA.html",'<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Revisión · 26–30 octubre</title><style>body{font:16px system-ui;background:#11151c;color:#eef2f8;margin:32px auto;max-width:1200px;padding:0 20px}a{color:#63e0c7}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px}article{background:#202633;border-radius:16px;padding:20px}h2{font-size:16px;color:#63e0c7}h3{min-height:3em}video{display:block;width:100%;height:470px;background:black}table{width:100%;border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #424b59;text-align:left}small{color:#bcc8d8}</style><h1>10 vídeos · 5 cuentas · 26–30 octubre</h1><p>Archivos preparados para revisar y programar. No se ha publicado ni programado nada en las plataformas.</p><p>Sin repetir juegos durante 21 días por marca. Horas propuestas, todavía sin evidencia de una hora ganadora. En TikTok confirmar visibilidad pública y comprobar el problema de cero visitas.</p><div class="grid">'+"".join(cards)+'</div><h2>Calendario propuesto · Europe/Madrid (UTC+1)</h2><table><tr><th>Día</th><th>Marca</th><th>Red</th><th>Hora</th><th>Juego</th><th>Copy</th></tr>'+rows+'</table></html>')
write("LEEME.md","""# Paquete 26–30 octubre de 2026
Preparado el 24/10/2026. Abrir REVISAR_SEMANA.html para ver los 10 vídeos.
10 piezas distintas: 5 NextLoot y 5 PartyLoot. En Para_subir hay 25 MP4 y 25 textos: NextLoot YouTube/TikTok, PartyLoot YouTube/TikTok/Instagram. Son versiones de distribución de 10 piezas; no son 25 vídeos diferentes.

## Programación propuesta
No hay publicaciones ni programaciones externas realizadas. plan_publicaciones.json es una propuesta. Días 26–30, Europe/Madrid (UTC+1 desde el cambio de hora del 25/10): NextLoot YouTube 20:00 y TikTok 19:00; PartyLoot YouTube 18:00, Instagram 13:00 y TikTok 20:30. Horarios provisionales para mantener constancia; estos datos no demuestran que sean los mejores.

En TikTok el usuario ya confirma más de 48 h y visibilidad pública; falta comprobar reproducción desde otra cuenta y tramitar la incidencia. Probar primero una pieza nueva. Este lote usa locución sintetizada: los textos de TikTok lo declaran y hay que activar también Contenido generado por IA al subirlo, conforme a su requisito para audio realista generado por IA. La declaración se refiere a la voz. Referencia: https://support.tiktok.com/en/using-tiktok/creating-videos/ai-generated-content . El resto de redes se puede preparar con independencia del diagnóstico de distribución.

## Qué probamos
Narración original en inglés, subtítulos integrados, una idea concreta al inicio, montaje de gameplay y entrada al monitor mucho más breve. Duración real en cada verification.json; no se promete una duración óptima.
Comparar 24 h, 48 h y 7 días desde publicación, por marca y red. No comparar directamente el total histórico de vídeos de distinta antigüedad. Registrar fecha/hora real, retención y búsqueda en medicion_24h_48h_7d.json.
No se repite ninguno de los diez juegos de este lote. La política bloquea 21 días desde publicaciones/programaciones anteriores de la misma marca y permite distribuir la misma pieza entre redes. El filtro del catálogo consulta data/editorial_history.json: hay que mantener ese historial al publicar o programar, porque no existe sincronización automática con las cuentas sociales. Preparado no significa Programado.

## Fuentes y derechos
Fuentes originales y tramos en input/semana_2026-10-26/*.provenance.json y recipe/brief por pieza. No son campañas patrocinadas ni se promete remuneración. Revisar AUDITORIA_FUENTES_2026-10-24.md: permiso editorial del editor y disponibilidad del tráiler no equivalen a ingreso garantizado o a licencia musical.
Se ha eliminado el audio musical del tráiler. El montaje final incorpora la locución del guion.
""")
write("DIAGNOSTICO_TIKTOK_2026-10-24.md","""# Diagnóstico TikTok · 24/10/2026
## Confirmado
El usuario comunica cero visitas en cuatro vídeos de NextLoot y dos de PartyLoot, y no encuentra avisos de cuenta/revisión/no apto. También confirma que llevan más de 48 horas publicados y que son públicos en ambas cuentas. YouTube e Instagram sí tienen distribución. No se ha accedido a la configuración privada de TikTok.

Los cuatro archivos locales comprobados (Absolum, Deep Dish Dungeon, Star Racer y Timberborn) son MP4, 1080×1920, H.264, yuv420p, 30 fps, audio AAC 48 kHz, duraciones 22,07 / 23,47 / 32 / 25,2 s. No hay un error evidente de formato. Esto no prueba que TikTok haya recibido exactamente el mismo archivo ni permite descartar un problema de contenido.

La app no contiene integración de publicación TikTok ni parámetros privacy_level / SELF_ONLY. Genera archivos; la visibilidad efectiva depende de la carga en TikTok u otra herramienta externa.

## Pendiente de comprobar
- Más de 48 h y visibilidad pública: confirmado por el usuario. Falta comprobar la reproducción desde otra cuenta o sesión.
- Abrir uno de los enlaces desde otra cuenta o sesión sin identificar y comprobar que se reproduce.
- TikTok Studio → Comprobación de la cuenta; estadísticas de cada vídeo → avisos de no apto para Para ti. El usuario ya indica que no ve avisos.
- Si llevan más de 48 h, están públicos y reproducibles y continúan exactamente en cero: usar Informar de un problema con enlace, fecha/hora y captura. 48 h es un criterio de diagnóstico práctico; no es un plazo oficial garantizado.

Con la confirmación del usuario, ya procede abrir la incidencia. Texto preparado para copiar (no enviado): «Hola. Los vídeos de mis cuentas Next Loot Games y Party Loot llevan más de 48 horas publicados, son públicos y siguen mostrando exactamente 0 visualizaciones. En TikTok Studio no aparecen avisos de revisión, infracciones ni de no apto para Para ti. ¿Podéis comprobar si existe un problema de procesamiento o distribución? Adjunto enlaces, fechas de publicación y capturas.» Añadir por separado los enlaces de cada cuenta; los cuatro de NextLoot están en el mensaje original. No inventar fecha exacta ni enlaces de PartyLoot.
Para distinguir carga de distribución, publicar manualmente UNA pieza nueva y original del lote desde la app oficial, con visibilidad pública, y medir antes de cargar el resto. No borrar ni resubir masivamente los existentes.
No hay evidencia suficiente para afirmar shadowban ni para culpar a la voz, la IA, el país o el número de cuentas. Tampoco se debe extrapolar el rendimiento de un mismo juego entre marcas: Absolum distribuye en PartyLoot y no en NextLoot.

Este lote nuevo sí contiene voz sintetizada con ElevenLabs. La política oficial exige declarar audio realista generado por IA, con independencia de cuánto metraje del juego tenga la pieza. Los copies de TikTok incorporan la declaración de la voz; activar Contenido generado por IA en las opciones de subida. TikTok indica que activar esta etiqueta no afecta la distribución si el contenido cumple sus normas. Esto no identifica la causa de los ceros anteriores: no se han inspeccionado las publicaciones ni sus etiquetas privadas. Fuente revisada: https://support.tiktok.com/en/using-tiktok/creating-videos/ai-generated-content

## Referencias oficiales
- https://support.tiktok.com/en/safety-hc/account-and-user-safety/why-is-my-account-not-being-recommended
- https://support.tiktok.com/en/safety-hc/account-and-user-safety/content-violations-and-bans
- https://www.tiktok.com/safety/community-controls/
Las páginas de ayuda limitaron el acceso automatizado de esta sesión; no se eludió el bloqueo. La ausencia de avisos es información aportada por el usuario, no una inspección de las cuentas.
""")
write("AUDITORIA_FUENTES_2026-10-24.md","""# Fuentes, catálogo y reutilización · revisado 24/10/2026
## Actualización realizada
366 → 441 entradas, +75 IDs únicos añadidos. Son entradas de vídeos, no 441 videojuegos distintos ni 441 licencias de reutilización. Se consultaron los últimos 15 ítems ofrecidos por cada uno de cinco feeds. No es una descarga exhaustiva de históricos. Copia de seguridad y resultado: data/catalog_refresh/.
Se conservaron los estados y datos anteriores. Pueden existir noticias, Shorts o comentarios entre los ítems: el catálogo sirve para descubrir y requiere selección editorial.

| Canal | Descubrimiento | Uso del material |
|---|---|---|
| Indie Games Hub (@IndieHub01) | RSS público | Descubrimiento; no licencia de vídeo inferida |
| GameTrailers / IGN (@GameTrailers) | RSS público | Descubrimiento; no reutilizar reseñas, voz o marcas del canal |
| Hooded Horse | RSS oficial | Política del editor y recursos de prensa |
| Devolver Digital | RSS oficial | Política del editor y recursos de creadores |
| Team17 | RSS oficial | Kits oficiales por juego; no se ha confirmado una licencia general de todo su catálogo |

Los canales nuevos son los tres editores; el archivo data/trailer_sources.json permite mantener las fuentes y sus límites. Se usan feeds Atom públicos (llamados RSS en la app), no rastreo de páginas completas. Google documenta el feed de vídeo como topic de notificaciones WebSub: https://developers.google.com/youtube/data/guides/push_notifications . Esa documentación no concede derechos de reutilización de footage ni una exención general de sus términos. Para ampliar la automatización, preferir YouTube Data API bajo sus condiciones o WebSub; esto último requiere un callback accesible desde Internet y no se ha activado en la app local.
No se descargaron los vídeos desde YouTube para este lote. Los medios se obtuvieron de la distribución oficial de Steam de cada editor. No se eludieron registros, CAPTCHA, paywalls ni respuestas 403.

## Permisos y límites por editor
Hooded Horse permite monetizar vídeos sobre sus juegos, streams y clips y pide respetar embargos. Ofrece assets y tráilers de prensa:
- https://www.hoodedhorse.com/influencers
- https://drive.google.com/drive/folders/1mGfpObzqZqv1IitplysV-WEffDkBZsdY
Aplicado a Beyond These Stars, Drill Keeper y Empires of the Undergrowth.

Devolver permite crear/monetizar vídeos de sus juegos y ofrece autorización escrita a canales:
- https://canipostandmonetizevideosofdevolvergames.com/
- https://influencers.devolverdigital.com/
Aplicado a BALL x PIT, Mycopunk, Stick It to the Stickman y Cult of the Lamb. El hub devolvió una limitación de acceso en esta revisión; no se evitó.

Team17 publica un hub expresamente para prensa y creadores y enlaces de kits:
- https://www.team17.com/press-and-creator-hub
- https://www.team17.com/hubfs/Press%20Kits/Goblin%20Cleanup%20-%20Press%20Kit.zip
- https://www.team17.com/hubfs/Press%20Kits/Golf%20With%20Your%20Friends%202%20-%20Press%20Kit.zip
Hokko Spaces tiene un enlace PRESS KIT en ese hub. Los enlaces directos no pudieron abrirse con el navegador de investigación. No se inventa que sus archivos contengan una licencia revisada. Base del uso propuesto: cobertura editorial con material oficial distribuido a prensa/creadores; confirmar cualquier condición particular si se transforma en un anuncio pagado o una campaña.
No se afirma permiso general de Team17 para sublicenciar, revender tráilers ni emplear música por separado.

## YouTube y música
https://www.youtube.com/t/terms
Las restricciones cubren acceso automatizado, descarga y reutilización salvo funciones autorizadas, permiso o excepción legal aplicable. Que un vídeo sea público no concede derechos de descarga ni reedición. Los dos agregadores sirven para localizar el juego, luego buscar al titular.
El permiso sobre el juego no garantiza derechos de cada canción del tráiler. Se eliminó el audio de fuente del lote; los archivos finales emplean locución y efectos locales del motor. Aun con permiso, las plataformas evalúan originalidad y monetización por sus propias normas: el ingreso no está garantizado.

## Selección y actualidad
Las novedades de los feeds son señales editoriales de actualidad; no demuestran volumen de búsqueda ni que un juego sea tendencia. No hay Google Trends ni búsquedas privadas de otros canales verificadas. Los títulos incorporan el nombre correcto del juego para facilitar descubrimiento.
Se ha contrastado nombre, características y multijugador con las fichas oficiales guardadas en input/semana_2026-10-26/*.source.json. No se anuncian fechas de salida en la locución: Drill Keeper tiene información de calendario inconsistente entre la ficha y el cierre del tráiler (Q3 2027); se describe como próximo. Golf With Your Friends 2 también se describe como próximo. Cult of the Lamb es un juego existente con co-op local, no un estreno de PC. Golf es competitivo, no co-op; Stickman y Cult son co-op local, no online.

## Opciones para ampliar después
Prioridad a otros editores con kits y política explícita, y RSS oficial o API: publisher indie/co-op, city builders y supervivencia. No añadir en masa agregadores o canales de gameplay ajeno sin derecho de reutilización. Mantener por juego el titular, fuente del vídeo, fecha de revisión, condiciones y necesidad de autorización. Guardar separado interés editorial de licencia de footage.

También se revisaron Landfall y Aggro Crab: https://landfall.se/peak-press-kit , https://landfall.se/content-warning-press-kit y https://aggrocrab.com/Press . Son opciones editoriales interesantes para PartyLoot, pero no se han activado de forma general. La política específica de PEAK (https://landfall.se/peak-fan-policy) prohíbe arte, textos y productos generados por IA basados en su IP y el entrenamiento con ella. Por prudencia, mi interpretación es mantener PEAK fuera de este flujo automatizado de guion/locución hasta aclarar su aplicación a cobertura editorial. No se extrapola esa restricción a todos los juegos de esos estudios. El kit público no sustituye la lectura de condiciones específicas.
""")
write("APRENDIZAJE_STATS_2026-10-24.md","""# Aprendizajes provisionales · 24/10/2026
Muestra pequeña, primeras semanas del canal y ventanas de distinta antigüedad. No permite atribuir causalidad a juego, formato, duración ni hora.

| NextLoot | Duración | Se quedaron | Media vista |
|---|---:|---:|---:|
| Escape Academy 2 | 26 s | 53,8 % | sin dato |
| Enshrouded | 24 s | 47,1 % | sin dato |
| Shoe it All! | 31 s | 44,7 % | 15 s |
| Timberborn | 26 s | 41,9 % | 12 s |
| Deep Dish Dungeon | 24 s | 39,1 % | 12 s |
| Valheim | 23 s | 29,4 % | 11 s |
| Star Racer | 33 s | 21,5 % | sin dato |
| Absolum | 23 s | 15,5 % | sin dato |

Las medias aproximadas sobre la duración conocida son 48 % Shoe, 46 % Timberborn, 50 % Deep Dish y 48 % Valheim. Son cálculos con segundos redondeados; no sustituyen el porcentaje real de Studio. «Se quedaron viendo» mide la decisión inicial, no cuántos terminan el vídeo. No confundir visualizaciones totales con interesadas.

La búsqueda aporta 10,4 % del tráfico de Shorts de NextLoot. Timberborn tiene 16,4 % de búsqueda y Valheim 22,3 %, pero Valheim engancha menos en el feed. Esto apoya conservar nombres de juegos y probar demanda de búsqueda; no demuestra que la búsqueda impulse causalmente el feed. Los porcentajes de términos se refieren al tráfico de búsqueda, no al total del canal.

Todos a las 20:00 salvo Shoe a las 12:00: un único vídeo distinto no demuestra que las 12:00 sean mejores. Mantendremos horas estables y compararemos ventanas equivalentes. En este lote se acorta el cuerpo y la entrada; es una prueba editorial, no una duración óptima demostrada.

PartyLoot cambia la lectura: Absolum ~1,5k, Deep Dish ~1,4k y Enshrouded ~1,2k; Escape ~95 y WheelMates 7. No descartar Absolum para todas las marcas. Valheim: última ficha 845 vistas, 379 interesadas, 43,9 % se quedaron, 12 s media y +1 suscriptor; el listado anterior 711 es otra captura temporal.
Instagram: 188/53/119/106/156/74 vistas según el listado recibido, sin asignación fiable de juego. No inventar esa correspondencia.
Los 5 suscriptores semanales son un resultado inicial, sin atribución por vídeo salvo +1 PartyLoot Valheim.

Audiencia NextLoot: 99,8 % nueva; 25–44 años 64,6 %; móvil 73,4 % del tiempo; EE. UU. 18,5 %, España 9,3 %, Reino Unido 5,8 %. Inglés y texto legible en móvil siguen teniendo sentido. «Sin subtítulos» del informe no mide si el vídeo tiene texto incrustado. Muy pocos habituales es esperable al empezar y no prueba que falle la fidelización.

La siguiente comparación debe recoger: hora real, vistas e interesadas a 24/48 h/7 d, decisión inicial, duración media y porcentaje visto, seguidores/suscriptores por 1.000 vistas, búsquedas y compartidos. Como cambian juego y montaje, el lote no es un experimento causal aislado; las señales servirán para decidir qué repetir tras los 21 días.
""")
assert len(calendar)==25 and len({b["slug"] for b in BRIEFS})==10
assert len(list((PACK/"Para_subir").rglob("*.mp4")))==25
for name in ["refresh_catalog.py","weekly_batch_20261026.py","finalize_batch.py"]:
    src=Path(__file__).parent/name
    if src.exists():shutil.copy2(src,ROOT/"scripts"/name)
print(json.dumps(dict(package=str(PACK),videos=10,upload_files=25,accounts=5,external_publications=0),ensure_ascii=False),flush=True)

