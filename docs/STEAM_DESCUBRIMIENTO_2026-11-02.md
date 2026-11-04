# Steam para descubrir juegos y conseguir material

Revisado el **2 de noviembre de 2026**. Captura de investigación: aproximadamente 15:02 UTC / 16:02 Madrid. Propuesta para NextLoot y PartyLoot; todavía no es un conector activo ni un permiso general para extraer o reutilizar contenido.

## Decisión

Sí merece la pena incorporar Steam a la selección editorial. La función debe recomendar **juegos que encajan con cada canal**, mostrar las señales que respaldan esa recomendación y localizar material oficial utilizable. No debe descargar indiscriminadamente todos los tráileres ni llamar «viral» a cualquier juego con muchos ingresos.

La primera versión viable combina revisión/importación de rankings oficiales con consultas mediante API documentadas. La extracción recurrente de HTML queda pendiente de confirmar una vía autorizada; no se activa por el mero hecho de que una página sea pública.

## Qué aporta cada señal

| Señal | Uso editorial | Limitación |
|---|---|---|
| Más vendidos globales y por categoría | Detectar juegos en los que se está gastando dinero y nuevas entradas relevantes | El ranking mide ingresos, incluidas compras dentro del juego y DLC; no unidades vendidas ni búsquedas |
| Jugadores simultáneos | Comprobar actividad y, con varias capturas comparables, su evolución | Una sola consulta no demuestra crecimiento; próximos lanzamientos pueden no tener jugadores públicos |
| Más deseados y próximos lanzamientos | Preparar descubrimientos antes de salir | Deseo no equivale a venta ni a disponibilidad de una demo |
| Noticias oficiales | Encontrar actualizaciones, estrenos y eventos que justifican una nueva pieza | Hay que verificar la fecha y la afirmación concreta |
| Nuestros resultados en YouTube/Instagram | Evaluar el encaje real de un enfoque para cada audiencia | Comparar ventanas y métricas iguales; pocas publicaciones no demuestran causalidad |

Steam explica que su lista de ventas considera las últimas 24 horas y da más peso a las tres más recientes. También existen listas por etiquetas/categorías. La subida puede responder a una oferta: registrar ese contexto. [Documentación de más vendidos](https://partner.steamgames.com/doc/store/top_sellers?l=english).

## Qué hay en nuestra app

- `scraper.py` descubre vídeos mediante RSS de YouTube; `catalog_policy.py` valida esas fuentes. Su base de vídeos es `data/trailers_db.json`. No acepta una URL de Steam como si fuera un canal de YouTube.
- `scripts/steam_activity.py` ya consulta jugadores actuales por AppID. Es un script de investigación ligado al lote de octubre, no un descubridor reutilizable de ventas.
- `data/discovery_sources_research_20261031.json` contiene fuentes Steam estudiadas, todavía sin integración de rankings en producción.
- `data/editorial_history.json` y el lote preparado permiten evitar repeticiones. Hay que consultar ambos: una pieza preparada tampoco debe producirse otra vez por aparecer en una lista.

Ampliar el catálogo con una entidad de juego identificada por **Steam AppID**, relacionada con sus vídeos oficiales. No insertar un AppID en el campo reservado para un identificador de vídeo de YouTube. Conservar el catálogo y los estados existentes.

## Filtros de los canales

**NextLoot:** descubrimientos con una mecánica o transformación visual clara; construcción, gestión, supervivencia, simulación y otras propuestas originales que puedan explicarse sin leer una interfaz diminuta. No limitar todo el canal a city builders por un único buen resultado.

**PartyLoot:** cooperativo, juegos de grupo, física, situaciones entre amigos y actividades compartidas. Una etiqueta «multijugador» no demuestra que exista cooperativo; comprobar el modo y el número de jugadores en la ficha o briefing. El gancho debe mostrar una interacción entre jugadores, no solo anunciar que se puede jugar acompañado.

Filtros comunes previos a recomendar:

1. Juego base correcto; separar DLC, bandas sonoras, software, paquetes y ediciones duplicadas. La demo se relaciona con su juego base.
2. Evitar contenido incompatible con nuestros canales y no aceptar automáticamente los 100 primeros.
3. Bloqueo de **21 días por marca** desde la publicación; incluir todos los juegos de un Top 3. Reservar también los del calendario preparado. Usar fechas de publicación confirmadas; un estado desconocido necesita revisión.
4. Distinguir compra, acceso anticipado, demo y próximo lanzamiento; no inventar «gratis», fecha o número de jugadores.
5. Comprobar que existe una escena que comunica la idea en móvil. Elegir un juego con demanda no resuelve un gancho confuso.
6. Exigir material con permiso adecuado antes de producir o publicar. La falta de permiso no elimina la señal de interés, pero deja la pieza pendiente.

## Candidatos de esta consulta

Selección editorial, no predicción de visitas. Posiciones observadas en el [ranking global consultado](https://store.steampowered.com/charts/topselling/global?l=english); cambian y deben refrescarse antes de usarlas como afirmación en un vídeo.

| Juego | Posición observada | Encaje propuesto | Siguiente comprobación |
|---|---:|---|---|
| Probably Stolen — AppID 4348910 | 7 | NextLoot: gestión y decisiones en una tienda | Buscar una venta o conflicto legible en pantalla vertical; verificar permisos de metraje |
| Moon Sweepers — AppID 5335510 | 9 | PartyLoot: limpieza y cooperación | Ya está en el paquete preparado; no crear un duplicado |
| Nomad Drive — AppID 2223980 | 35 | NextLoot; PartyLoot solo tras verificar el modo cooperativo | Mostrar mejora de la caravana/supervivencia; confirmar modo, material y derechos |
| R.E.P.O. — AppID 3241660 | 44 | PartyLoot: física, objetos y cooperación | Revisar su press kit oficial y condiciones de uso |
| PEAK — AppID 3527290 | 54 | PartyLoot: ayuda, caídas y escalada | Revisar EULA y política de contenido antes de usar nuestro flujo con IA |

Las mecánicas se contrastaron con las fichas oficiales: [Probably Stolen](https://store.steampowered.com/app/4348910/), [Nomad Drive](https://store.steampowered.com/app/2223980/), [R.E.P.O.](https://store.steampowered.com/app/3241660/) y [PEAK](https://store.steampowered.com/app/3527290/). No se han inspeccionado clips de estos candidatos ni descargado sus kits en esta investigación.

## Tráileres: acceso, descarga y permiso son cosas distintas

Muchos juegos muestran tráileres sin comprar el juego. Eso permite consultarlos, pero no concede automáticamente derechos para copiarlos, remontarlos o utilizarlos en una campaña pagada.

Orden de búsqueda de material:

1. Kit oficial de una campaña, respetando sus reglas, embargo, música y exclusividad.
2. Press kit del estudio/editor con condiciones aplicables al vídeo que queremos hacer.
3. Canal oficial y política de vídeos/creadores del titular, verificando que cubren ese material y nuestra monetización.
4. Gameplay propio cuando lo permita la política del juego. Tener comprado el juego tampoco resuelve por sí solo los derechos de toda su música.

Registrar por recurso: URL oficial, titular, fecha de revisión, documento o permiso, plataformas admitidas, edición/monetización/campañas permitidas, límites, música y embargo. Usar estados separados: localizado, permiso pendiente, permitido con condiciones o descartado. «Está en un press kit» por sí solo no documenta todos los usos posibles.

Ejemplos comprobados: [Semiwork](https://semiwork.se/) enlaza un press kit de R.E.P.O.; todavía hay que inspeccionar su contenido y condiciones. La [FAQ oficial de Content Warning](https://landfall.se/content-warning-faq) permite grabar y emitir el juego con monetización; eso no equivale a autorizar la reutilización de cualquier tráiler ajeno ni una campaña para otra marca.

La [política de vídeo de Valve](https://store.steampowered.com/video_policy) cubre contenido de Valve, no todos los juegos de Steam. Admite monetización mediante programas como el de YouTube, con límites; no es una licencia general de publicidad para terceros.

Caso que requiere atención: la [política de fans de PEAK](https://landfall.se/peak-fan-policy) contiene restricciones sobre contenido generado con IA. No doy por resuelto su alcance respecto a nuestros guiones/voz asistidos: revisar junto a su EULA o aclarar el uso concreto antes de producir. No se aplica esa regla automáticamente a otros juegos del editor.

## Acceso técnico y condiciones

La consulta de jugadores está [documentada por Steamworks](https://partner.steamgames.com/doc/webapi/ISteamUserStats#GetNumberOfCurrentPlayers). Puede servir para enriquecer candidatos conocidos; no descubre por sí sola el ranking de ventas. No he confirmado una API pública documentada para obtener ese ranking.

La API tiene [condiciones propias](https://steamcommunity.com/dev/apiterms): revisar el uso previsto, proteger las claves cuando correspondan y respetar límites. `appdetails` y endpoints internos accesibles en la tienda no deben tratarse como un contrato público estable solo porque respondan.

El [acuerdo de Steam, sección 4.C](https://store.steampowered.com/agreement/?l=english), contiene una prohibición amplia de automatización. Su existencia impide concluir aquí que un robot de HTML esté autorizado. Las API expresamente ofrecidas y sus términos se evalúan por separado. `robots.txt` no sustituye un permiso contractual ni de copyright. No usar sesiones privadas, evadir bloqueos o resolver captchas automáticamente.

Alternativa inicial concreta: importar una selección revisada de rankings y enriquecerla con API documentadas, dejando cualquier fuente automática de rankings desactivada hasta confirmar su autorización. Si después se confirma esa vía, empezar con una captura diaria pequeña, caché, espera ante errores y parada ante 403/429. Esta frecuencia es una propuesta técnica, no un permiso.

## Integración propuesta

Flujo: candidatos Steam → comprobar juego/modos/fechas → historial y calendario → encaje por marca → señal de demanda → buscar metraje oficial → comprobar permiso → cola de producción existente.

Una tarjeta debe explicar: «por qué encaja», señales y fecha, repeticiones, escena propuesta, material disponible y permiso. No mostrar un porcentaje de viralidad inventado. Un dato ausente es desconocido, no cero. No comparar posiciones de regiones distintas como si fueran una subida.

Diseño de datos separado, sin nuevas carpetas de exportación:

- `data/`: juegos por AppID, observaciones fechadas, candidatos y permisos.
- `GamesFootage/` o `PromosFootage/`: originales autorizados, conservando una sola copia.
- `output/videos/`: piezas finales; `output/gestion/`: planes; `output/trabajo/`: intermedios.

No hace falta descargar vídeo durante el descubrimiento. Primero guardar el enlace y la evidencia; descargar solo el recurso seleccionado y autorizado. Mantener YouTube como fuente complementaria de metraje y descubrimiento.

## Piloto recomendado

1. Elegir cinco candidatos por marca, descontando historial y reservas del paquete actual.
2. Registrar por qué se eligieron, fuente/fecha y permiso; descartar los que no tengan una escena clara.
3. Comparar cuatro publicaciones seleccionadas con ayuda de Steam frente a cuatro de selección editorial habitual por marca, sin cambiar a la vez voz, formato y horario.
4. Evaluar a 72 horas y siete días: se quedaron viendo, duración y porcentaje medio visto, suscriptores por mil vistas interesadas y búsquedas. Comparar cada red por separado.
5. Tomarlo como piloto orientativo: ocho publicaciones no garantizan significación ni un formato ganador. Ampliar si la señal resulta útil.

**Estado final:** investigación y propuesta guardadas. No se ha activado un scraper nuevo, descargado tráileres, alterado el calendario ni sustituido vídeos.
