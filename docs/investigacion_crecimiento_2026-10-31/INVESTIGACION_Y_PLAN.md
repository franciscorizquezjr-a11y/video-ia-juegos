# Investigación de crecimiento, fuentes y narración

**Revisado: 31 de octubre de 2026 · Europe/Madrid.** Investigación para NextLoot y PartyLoot. Los datos son una captura, no un pronóstico de visitas ni un seguimiento continuo.

## Conclusión y decisiones

La selección debe partir de una escena que se entienda y de una idea que merezca contarse. Después se contrasta la demanda y la actualidad. Un juego con muchos jugadores puede producir un Short peor que otro pequeño si su interés depende de leer una interfaz o entender diez sistemas.

Prioridad para auditar metraje: PartyLoot → Moon Sweepers, R.E.P.O., PEAK y Nomad Drive. NextLoot → Probably Stolen (con condición de legibilidad), ATMOSFAR, No Man's Sky y BELTFED como próximo lanzamiento. Es una preselección editorial: falta revisar los clips concretos y su permiso de uso antes de producir cada pieza.

Los hallazgos se guardan como guía futura. No se regenera el paquete ya preparado ni se altera su calendario de publicaciones. El registro nuevo de fuentes no se ha conectado silenciosamente al lector de feeds de la app; ese lector actualmente solo acepta fuentes YouTube con identificador validado.

## 1. Qué hemos aprendido de nuestros datos

Los informes compartidos por el usuario muestran señales distintas por canal. En NextLoot, Escape Academy 2 tuvo 53,8 % de “se quedaron viendo”, Enshrouded 47,1 %, Timberborn 41,9 %, Valheim 29,4 % y Absolum 15,5 %. En PartyLoot varios de esos mismos juegos tuvieron resultados diferentes. Esto permite formular hipótesis sobre el enfoque, la primera toma y la audiencia; no demostrar que todo un género funciona o falla.

El 83,9 % del tráfico de Shorts procedía del feed y el 10,4 % de búsquedas, en la ventana compartida. La búsqueda es una segunda vía útil, pero esos porcentajes no prueban que las búsquedas impulsen causalmente el feed. El nombre exacto del juego debe estar en metadatos, logo y locución cuando corresponda; el gancho visual debe funcionar sin esperar a una búsqueda.

No mezclar denominadores: en Timberborn, si 16,4 % del tráfico es de búsqueda y “timberborn” representa 31,5 % dentro de esa fuente, ese término supone aproximadamente 5,2 % del total, no 31,5 %. Tampoco confundir “se quedaron viendo” con porcentaje medio visto o con duración media entre vistas interesadas. Los totales acumulados de vídeos publicados en días diferentes no son una comparación de 24 horas.

YouTube explica que considera elección de visualización, permanencia y satisfacción, con personalización y competencia entre contenidos. No promete un formato ganador ni exige una frecuencia mínima. [Fuente oficial](https://support.google.com/youtube/answer/11914225?co=YOUTUBE._YTVideoType%3Dshorts&hl=en).

## 2. Cómo cambiar el texto y la voz

### Escribir una situación, no un catálogo de características

Cada pieza necesita: una promesa concreta, dos o tres ideas que la desarrollen, una escena que las pruebe y un cierre conectado con lo visto. Evitar varias frases seguidas con la misma longitud, listas de verbos sin jerarquía, adjetivos vacíos y repetir siempre “Would you play?” o “Who would you bring?”.

Como punto de partida editorial, trabajar con unas 55–75 palabras para un vídeo de un juego y 80–105 para un Top 3. Son guías: recortar una idea redundante antes de acelerar una locución para que quepa. No alargar una escena para alcanzar una duración arbitraria. El ritmo real se decide al escuchar la voz habitual, no solo contando palabras.

### Pausas breves con una función

Probar 1–3 pausas deliberadas por cada 30 s: alrededor de 0,18–0,35 s para separar una idea y 0,25–0,40 s antes de un contraste o una resolución. Estos intervalos son nuestra propuesta inicial, no una regla científica ni una garantía de retención. El gameplay puede continuar durante la pausa; no hace falta una pantalla quieta ni un efecto sonoro en cada silencio.

Conservar la voz AHc7z8dzjrGlVbbQ8enm y Multilingual v2. ElevenLabs documenta pausas SSML para ese modelo y avisa de que su abuso puede producir artefactos. La sintaxis y el soporte varían entre modelos. [Documentación](https://elevenlabs.io/docs/help-center/product/core-capabilities/text-to-speech/how-can-i-add-pauses).

### Hallazgos técnicos comprobados en nuestra app

En `audio_aligner.py`, el limpiador elimina todas las etiquetas entre `<...>` y sustituye comas y dos puntos por puntos y coma. La síntesis añade velocidad al cuerpo y genera una introducción acelerada en una segunda petición, después la empalma con el resto. No afirmo que esto explique por sí solo la percepción de artificialidad, pero sí son transformaciones reales que pueden interferir con la intención escrita.

Prueba local, sin consumir créditos: `Your friends, one mission: clean the Moon. <break time="0.25s" /> Who gets the first vehicle?` se convierte en `Your friends; one mission; clean the Moon.  Who gets the first vehicle?`. Por tanto, añadir una etiqueta de pausa ahora mismo NO basta: el motor la quita antes de enviarla.

Trabajo de implementación pendiente: un modo de locución natural que conserve puntuación y pausas admitidas, prefiera una toma continua sin aceleración automática del gancho, mantenga alineación exacta de subtítulos y no reescriba un CTA literal de campaña mediante contracciones automáticas. Separar texto hablado/markup, texto de subtítulos y texto obligatorio. Verificar los cambios con una locución de prueba y escucha real; un pico de audio correcto no demuestra buena interpretación. El campo `viral_score` del análisis de escenas también es una valoración heurística, no una probabilidad medida de éxito en YouTube.

### Cierre con gancho y resolución

Primero cumplir lo prometido al inicio. Después elegir UNA salida: pregunta específica, elección entre dos papeles del grupo o enlace natural con la premisa inicial. No cerrar con tres órdenes seguidas: comentar, suscribirse y visitar la tienda. La llamada genérica al canal puede ir en la descripción.

En campañas manda el briefing. Si exige un cierre literal, ese cierre sigue siendo lo último. La pregunta puede aparecer antes, si encaja y está permitida; nunca reemplaza el CTA. Ejemplo Miner: pregunta sobre el papel en la tienda → pausa breve → `Wishlist now - Demo available`. No poner una llamada al canal después.

## 3. Ejemplos de intención de guion

Son borradores, no locuciones generadas ni escenas verificadas fotograma a fotograma. Las anotaciones de pausa no se mandan al limpiador actual como si ya funcionasen.

**PartyLoot · Moon Sweepers**

“The Moon needs cleaning. [pausa breve] And you brought a toothbrush. Moon Sweepers turns lunar dust into a co-op job for up to sixteen players: collect it, sell it, and work toward better tools and cleaning vehicles. [pausa] Who gets the first vehicle... and who's still brushing?”

La premisa, el cooperativo y los vehículos constan en su [ficha oficial](https://store.steampowered.com/app/5335510/). Mostrar la herramienta inicial y un progreso visible; no prometer que dieciséis jugadores estén presentes en un clip si no se ven.

**NextLoot · Probably Stolen**

“That customer could cost you the whole shop. [pausa] Probably Stolen puts you behind a counter on a cyberpunk space station, where trading goods and choosing who to deal with are part of staying afloat. The rent keeps rising. [pausa] Would you take the risky sale... or protect what you've built?”

La economía y las lealtades proceden de la [ficha del juego](https://store.steampowered.com/app/4348910/). La captura tiene mucho texto: necesita una interacción legible y un encuadre concreto, no un paseo rápido por todos los menús.

**PartyLoot · R.E.P.O.** Gancho propuesto: “The monster isn't the only thing that can ruin this job.” La escena debe demostrar la fragilidad del traslado y la física antes de cerrar con una elección sobre quién transporta el objeto. Confirmar el objeto concreto en el metraje; no inventar una anécdota personal de una partida que no hemos jugado. [Ficha](https://store.steampowered.com/app/3241660/).

**Top 3**: usar una razón temática real para esperar al puesto uno, variar la formulación y resolverla. Una lista editorial no convierte “el mejor” en consenso objetivo. “Lo está petando” requiere una señal reciente identificada; no basta el tamaño del fondo de una campaña.

## 4. Fuentes: dónde descubrir y dónde obtener material

| Fuente | Uso que le daría | Acceso y límite importante |
|---|---|---|
| [YouTube Studio · Tendencias](https://support.google.com/youtube/answer/11962757?hl=en) | Consultas, contenido destacado de canales similares y posibles huecos de Shorts | Revisar en cada canal. Algunas secciones dependen de país, idioma o datos; no he accedido a sus datos privados. |
| [Google Trends](https://support.google.com/trends/answer/4365533?hl=en) | Comparar juegos en búsqueda de YouTube, 7/30/90 días, US/UK/ES | Escala normalizada 0–100, no volumen absoluto. Elegir el tema del videojuego cuando exista; “PEAK” como palabra genérica puede confundir. |
| [Steam · rankings](https://store.steampowered.com/charts) | Separar ventas, jugadores y próximos lanzamientos | Ingresos no equivalen a jugadores ni a interés de Shorts; no elegir Counter-Strike solo por liderar la tabla. |
| [Steam · API de jugadores](https://partner.steamgames.com/doc/webapi/ISteamUserStats#GetNumberOfCurrentPlayers) | Capturas pequeñas y comparables del uso actual | Se ha probado en esta investigación. Una captura no mide crecimiento. |
| [Steam · API de noticias](https://partner.steamgames.com/doc/webapi/ISteamNews) | Encontrar motivo editorial: estreno, demo, cambio jugable o evento | Usar anuncios oficiales; distinguir un parche técnico de una mecánica nueva. |
| [Steam · más deseados](https://store.steampowered.com/search/?filter=popularwishlist) y [Next Fest](https://store.steampowered.com/sale/nextfest) | Próximos juegos y demos | Verificar disponibilidad y fechas; no inferirlas de un puesto en una lista. |
| [Hooded Horse](https://www.hoodedhorse.com/influencers) | Fuente prioritaria NextLoot: estrategia, automatización y construcción | Tiene kits y permiso explícito para monetizar vídeos de sus juegos; respetar embargos. |
| [Team17](https://www.team17.com/press-and-creator-hub) | Cooperativo, party y propuestas con recursos de prensa | Revisar condiciones del kit concreto. |
| [Devolver](https://influencers.devolverdigital.com/) | Mecánicas raras y acción con una idea fácil de contar | Usar recursos del titular; su política de monetización no concede derechos de terceros sobre música. |
| [tinyBuild](https://www.tinybuild.com/creator) | Gestión, simuladores y rarezas; programa que contempla creadores de varios tamaños | La [política](https://www.tinybuild.com/influencer-policy) exige aportación original y aviso de copyright. No es permiso para republicar sus vídeos tal cual. |
| [Raw Fury](https://rawfury.com/lets-play/) | Indies de misterio, mecánica y gestión | Permite vídeos comentados monetizados; pide enlace al juego y distingue derechos musicales. |
| [Future Friends](https://press.futurefriendsgames.com/) | Excelente catálogo de premisas visuales, cozy y experimentales | Portal de kits descargables; revisar el título concreto. |
| [Fireshine](https://fireshinegames.co.uk/) | Crafting, supervivencia y cooperativos; noticias de actualizaciones | No he confirmado una licencia global de reutilización de todos sus tráileres. |
| [Ghost Ship Publishing](https://ghostship.dk/publishing/) | Cooperativos y roguelikes con kits por juego | No confundir Deep Rock Galactic: Survivor, solo, con otros títulos cooperativos del grupo. |
| [Coffee Stain](https://coffeestain.com/) | Física, automatización, supervivencia y novedades del catálogo | Confirmar desarrollador, material y permisos por juego. |
| [Landfall · FAQ de Content Warning](https://landfall.se/content-warning-faq) | Física y situaciones de grupo | La autorización de ese juego no se extrapola a todos; una política de merchandising de PEAK no resuelve por sí sola cada uso audiovisual. |
| [Wholesome Games](https://wholesomegames.com/) | Cozy y mecánicas con gancho; enlaces a kits y creadores | Detectar juegos en el showcase y acudir al estudio para el metraje. |
| [Day of the Devs](https://www.dayofthedevs.org/) | Propuestas singulares antes de que estén en todas las listas | Selección editorial; no licencia de su retransmisión completa. |
| [itch.io](https://itch.io/docs/general/faq) | Prototipos, destacados y devlogs con una mecánica potente | Prioridad secundaria: comprobar estabilidad, origen del material y permiso del creador. |
| [Steam 250](https://steam250.com/hidden_gems) | Hallar juegos valorados que aún no tenemos | Calidad y popularidad no son lo mismo; consulta manual. |
| [SteamDB](https://steamdb.info/faq/) | Contraste manual puntual | Su FAQ prohíbe scraping/crawling. Para automatizar, obtener datos de Steam directamente. |
| [TikTok Creative Center](https://ads.tiktok.com/creative/creativeCenter/trends) | Estudiar lenguaje visual y temas, no copiar vídeos | Se ha visto la entrada/login; no se han obtenido rankings privados. No explica la incidencia de nuestras cuentas. |

También guardaría referencias editoriales como [Real Civil Engineer](https://www.youtube.com/@RealCivilEngineerGaming), [Splattercatgaming](https://www.youtube.com/@Splattercatgaming), [Retromation](https://www.youtube.com/@Retromation) y [The Co-Op Bros](https://www.youtube.com/watch?v=ERMhLrWKQlw). Sirven para descubrir enfoques, nunca como biblioteca de metraje. No se han verificado aquí sus diez últimos vídeos ni sus tasas actuales: varias páginas de canal solo devolvieron una carcasa de YouTube. No se les atribuye un rendimiento que no se ha medido.

Para ampliar la automatización, usar identificadores oficiales y APIs o mecanismos documentados como [WebSub de YouTube](https://developers.google.com/youtube/data/guides/push_notifications), con sus [políticas de desarrollo](https://developers.google.com/youtube/terms/developer-policies). Que exista un feed no concede derechos de descarga o edición del vídeo.

## 5. Lo que se ha consultado de verdad

Se consultaron 23 fichas/capturas de juegos mediante las fuentes públicas de Steam, sus jugadores actuales y anuncios oficiales; se inspeccionaron capturas oficiales de 12 candidatos. Se renovaron además los cinco feeds ya configurados. No se generó ninguna locución nueva para esta investigación, no se descargó material de otros creadores, no se inició una suscripción y no se publicaron vídeos.

El endpoint de reseñas devolvió respuestas no utilizables como JSON en los 23 intentos. Está registrado como dato desconocido: no se ha sustituido por ceros ni se ha inventado una velocidad de reseñas. La ficha de Light No Fire no permite obtener jugadores de un juego aún no publicado; también queda como no disponible. No hay series históricas propias suficientes para afirmar crecimiento de 7 días. Los JSON conservan errores y la hora de captura.

Las capturas oficiales ayudan a valorar legibilidad, pero no prueban cómo se mueve el tráiler ni reemplazan revisar la toma final a 9:16. En Probably Stolen, Factorio y Slay the Spire 2 hay especial riesgo de que demasiado texto o una vista general esconda el interés. En Moon Sweepers, PEAK o un vehículo de Nomad Drive la acción central puede resultar más inmediata, siempre que se escoja una toma que lo demuestre.

## 6. Preselección actual

Jugadores simultáneos observados en la API durante esta consulta, no visitas, ventas, tendencia de búsquedas ni previsión. Las prioridades A/B/C son juicio editorial inicial, no probabilidades ni medición calibrada de viralidad. Incluyo tres referencias ya usadas para no olvidar el bloqueo por historial.

| Juego | Canal principal | Jugadores en la captura | Prioridad | Lectura editorial |
|---|---|---:|---|---|
| [Probably Stolen - Cyberpunk Shopkeeper Sim](https://store.steampowered.com/app/4348910/) | NextLoot | 82.601 | A, condicionado a plano legible | Alta demanda de estreno; tienda con riesgo claro. Las capturas muestran interfaz densa: no montar el Short como un menú entero. |
| [No Man's Sky](https://store.steampowered.com/app/275850/) | NextLoot | 19.804 | A/B | Novedad oficial reciente y escala visual; elegir una mecánica nueva concreta en lugar de resumir todo el juego. |
| [ATMOSFAR](https://store.steampowered.com/app/1798230/) | NextLoot | 1.890 | A/B, oportunidad visual | Lanzamiento reciente, islas flotantes y vehículos; demanda menor, pero buen potencial de comprensión visual. |
| [BELTFED](https://www.hoodedhorse.com/games/beltfed) | NextLoot | No disponible / próximo | A/B, próximo lanzamiento | Factory-defense concept and strong official trailer reach; do not treat lifetime views/hour as current growth. |
| [Slay the Spire 2](https://store.steampowered.com/app/2868840/) | NextLoot | 54.443 | B | Demanda alta; cartas y texto pequeño complican el recorte móvil. También tiene cooperativo, que debe explicarse con un ángulo específico si se usa en PartyLoot. |
| [Factorio](https://store.steampowered.com/app/427520/) | NextLoot | 24.748 | B | Interés establecido y noticia oficial reciente. Mostrar una cadena o un atasco, no un mapa indescifrable. |
| [Cairn](https://store.steampowered.com/app/1588550/) | NextLoot | 1.847 | B, experimento visual | Menor demanda, pero una decisión de escalada se entiende rápido. No es cooperativo. |
| [Enshrouded](https://store.steampowered.com/app/1203620/) | NextLoot | 84.744 | Referencia propia; bloqueado por historial | Demanda alta y encaje claro; el historial y las reservas mandan. |
| [Timberborn](https://store.steampowered.com/app/1062090/) | NextLoot | 5.542 | Referencia propia; respetar espera | Ya funcionó en el canal. No se repite automáticamente porque aparezca una señal nueva. |
| [Light No Fire](https://store.steampowered.com/app/2719590/) | NextLoot | No disponible / próximo | Vigilancia de próximos lanzamientos | Aún sin fecha anunciada en la ficha; no presentar disponibilidad, demo o jugadores activos que no estén confirmados. |
| [Moon Sweepers](https://store.steampowered.com/app/5335510/) | PartyLoot | 74.642 | A | Premisa inmediata: limpiar la Luna empezando con un cepillo; cooperativo, progreso visual y estreno reciente. |
| [R.E.P.O.](https://store.steampowered.com/app/3241660/) | PartyLoot | 66.252 | A | Física, botín y cooperación; actualización reciente y escena que puede tener causa y consecuencia. |
| [PEAK](https://store.steampowered.com/app/3527290/) | PartyLoot | 17.556 | A | Altura, caída y cooperación se leen sin explicar un menú; demanda actual sustancial. |
| [Nomad Drive](https://store.steampowered.com/app/2223980/) | PartyLoot | 4.207 | A/B, revisar metraje | Estreno reciente y supervivencia sobre ruedas; comprobar cuál es el momento visual distintivo frente a RV There Yet. |
| [Project Zomboid](https://store.steampowered.com/app/108600/) | PartyLoot | 56.944 | B | Demanda alta; situaciones de grupo y decisiones claras. Verificar rama/versión si se habla de novedades. |
| [HELLDIVERS™ 2](https://store.steampowered.com/app/553850/) | PartyLoot | 42.999 | B | Demanda y acción; competencia alta. Necesita un fallo de coordinación legible, no una sucesión genérica de explosiones. |
| [Sons Of The Forest](https://store.steampowered.com/app/1326470/) | PartyLoot | 20.805 | B | Supervivencia compartida y demanda; sin actualización reciente fuerte en la muestra oficial recibida. |
| [The Outlast Trials](https://store.steampowered.com/app/1304930/) | PartyLoot | 17.672 | B | Cooperativo y evento reciente; seleccionar escena apta para el canal y contexto del juego. |
| [RV There Yet?](https://store.steampowered.com/app/3949040/) | PartyLoot | 8.039 | B | Viaje y coordinación claros; no confundir su éxito histórico con un nuevo pico viral hoy. |
| [Escape the Backrooms](https://store.steampowered.com/app/1943950/) | PartyLoot | 5.941 | B | Cooperación y objetivo de escapar legibles; revisar escena nueva, no tratar cualquier parche como gran actualización. |
| [Scam With Your Friends](https://store.steampowered.com/app/4954910/) | PartyLoot | 4.668 | B, revisar enfoque | Estreno cooperativo con situación de oficina; verificar escena, tono y aportación real antes de seleccionarlo. |
| [Lethal Company](https://store.steampowered.com/app/1966720/) | PartyLoot | 4.501 | B/C | Buen concepto, pero la última noticia devuelta no es reciente; buscar una razón concreta para recuperarlo. |
| [Content Warning](https://store.steampowered.com/app/2881650/) | PartyLoot | 964 | C, nicho/experimento | Física y grabación social claras; menor demanda actual que R.E.P.O. en esta captura. |
| [Valheim](https://store.steampowered.com/app/892970/) | PartyLoot | 87.454 | Referencia propia; bloqueado por historial | Demanda muy alta, pero ya publicado en ambas marcas en octubre. |

BELTFED aporta una señal adicional fuera de Steam: su tráiler oficial tenía 694.423 vistas en el feed consultado, con unas 534 horas desde publicación. Su [página del editor](https://www.hoodedhorse.com/games/beltfed) lo presenta para 2027. Esa cifra no es una velocidad actual de visitas ni demuestra tráfico orgánico: un vídeo del editor puede tener promoción. Sirve como señal para investigar una premisa que encaja, no como promesa de que nuestro Short tendrá esas vistas.

## 7. Auditoría del catálogo

Antes de la actualización había 441 entradas y 384 cadenas de nombre distintas; no 441 juegos verificados. La actualización de los cinco feeds pasó de 441 a 494 entradas: 53 nuevas, sin retirar registros ni cambiar los estados de trabajo. Se hicieron dos correcciones verificadas de nombre: el lema del tráiler de Tenjutsu se estaba tomando por su título; una frase publicitaria de Look Outside también.

El detalle final, incluidas las cadenas distintas tras correcciones, las fechas de métricas y los canales, está en AUDITORIA_CATALOGO.json. La mayoría de registros procede de dos agregadores; gran parte del catálogo antiguo carece de fecha de métricas. Por eso no debe ordenarse toda la biblioteca por “viralidad” como si cada fila tuviera una medición reciente equivalente.

El extractor de nombres no debe identificar un juego por la frase más llamativa del título. Prioridad: AppID/ficha del titular y alias verificado. No unir automáticamente una secuela, demo, DLC o actualización al juego base sin conservar su tipo. Un registro de vídeo, una entidad de juego y un recurso con permiso son tres cosas diferentes.

## 8. Método para elegir las próximas piezas

### Filtros que se aplican antes de puntuar

1. Encaje del canal. NextLoot: descubrimiento y mecánicas distinguibles. PartyLoot: experiencia con amigos, sea cooperativa o party/multijugador, con modo verificado; no basta una etiqueta ambigua.
2. Historial y reservas: 21 días por marca. La misma pieza puede distribuirse entre redes. Un borrador reservado no se marca como publicado; se comprueba por separado para evitar preparar duplicados.
3. Material concreto utilizable, logo limpio y embargo compatible. Fuentes de descubrimiento y fuentes de metraje quedan separadas.
4. Al menos una acción de la pieza que se entienda rápido en móvil. Rechazar una apertura que dependa de leer un menú entero o de explicar todo un sistema.
5. Capacidad de cumplir el briefing si hay campaña: formato dedicado, voz admitida, duración, gameplay mínimo, material nuevo y CTA exacto. Una promo no entra en un Top 3.

### Puntuación propuesta, todavía sin calibrar

Encaje 30 %, claridad visual 25 %, demanda 20 %, actualidad 15 % y facilidad de producción/material 10 %. Guardar cada dato con fuente y fecha; lo desconocido no se transforma en una cifra inventada. No sumar dos veces la misma evidencia: jugadores y ventas de Steam son señales distintas, pero pertenecen a la misma familia; conviene contrastar también la demanda en YouTube.

El campo “viral_score” generado por un modelo sobre una escena no sustituye esta evidencia. Tampoco afirmar “crece un 200 %” sin dos mediciones comparables. La valoración visual debe indicar si procede de una captura, de un tráiler completo o del montaje final.

### Lectura de vídeos de referencia

Si obtenemos metadatos válidos, comparar un vídeo con otros recientes del mismo canal y formato, en ventanas de edad parecidas. Distinguir vistas totales, media desde publicación y crecimiento entre dos observaciones. No mezclar un tráiler de 22 días con un Short de 6 horas ni considerar las vistas por hora desde publicación como velocidad de la última hora. La muestra pública disponible no revela retención, fuente pagada o conversión a seguidores de los demás.

### Mezcla de un lote de ocho

Probar cuatro piezas con demanda establecida y un enfoque concreto, dos estrenos/actualizaciones y dos hallazgos experimentales. Mantener aproximadamente un Top 3 temático por marca y semana; el resto puede variar entre un juego, problema/resolución, roles del grupo, una decisión de riesgo o una transformación visual. Es una propuesta inicial, no una fórmula de distribución. No añadir un juego a una lista solo porque falte un puesto.

Una pieza orgánica diaria sigue siendo la base, con el hueco de campaña ya reservado y condicionado a su briefing. No se cambia de golpe volumen, duración, voz, texto y horarios para después atribuir el resultado a una sola variable.

## 9. Próximo orden de trabajo

1. Aplicar la guía de guion a la siguiente pieza: una escena, una promesa, un desarrollo corto y un cierre que responda a lo visto.
2. Preparar el modo natural del motor de voz y una prueba privada con el mismo texto antes de usar SSML en producción. Escucharla; revisar que las pausas no se imprimen en subtítulos ni desalinean el montaje.
3. Auditar metraje/licencia de Moon Sweepers y R.E.P.O. para PartyLoot; Probably Stolen y ATMOSFAR para NextLoot. BELTFED queda como posibilidad de contenido de próximo lanzamiento, no como disponible hoy.
4. Añadir después los portales de prensa verificados al descubrimiento, con adaptadores apropiados. El registro investigado no está encendido en un scraper que solo entiende YouTube.
5. Antes de cerrar el siguiente lote, revisar tendencias del canal y comparar capturas de demanda/actualidad. Mantener un pequeño espacio editorial flexible para una oportunidad real, sin publicar dos piezas del mismo juego por el mismo canal.
6. Registrar vistas interesadas, elección de visualización, duración, finalización y seguidores a 24/48 h. Comparar las mejoras por canal; una semana y una muestra pequeña producen hipótesis, no certezas.

## Archivos y límites de esta entrega

- snapshot.json y JSON por juego: captura y errores conservados.
- PRESELECCION_JUEGOS.json: criterios, bloqueo por historial, reservas y noticias.
- feed_examples.json: metadatos públicos observados; no autorización audiovisual.
- FUENTES_REVISADAS.json: registro con método de acceso y estado de integración.
- CRITERIOS_GUION_Y_VOZ.json: guía adoptada; soporte del motor pendiente.
- AUDITORIA_CATALOGO.json: recuentos reales y correcciones.

Las páginas dinámicas de Google Trends, Studio y TikTok no se han hecho pasar por datos privados consultados. No se han analizado completos los vídeos de referencia. No se envían mensajes a editores ni se solicita acceso a programas en esta investigación. La revisión está fechada: señales y permisos deben volver a comprobarse al producir.
