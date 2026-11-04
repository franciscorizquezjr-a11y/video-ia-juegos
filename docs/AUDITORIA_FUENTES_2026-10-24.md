# Fuentes, catálogo y reutilización · revisado 24/10/2026
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
