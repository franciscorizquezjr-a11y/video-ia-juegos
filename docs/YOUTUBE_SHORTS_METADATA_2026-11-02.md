# Títulos, descripciones y publicación de YouTube Shorts

Revisado el 2 de noviembre de 2026. Cambios locales del decisor y propuestas; no se han cambiado las cuentas, los horarios, los vídeos ni las publicaciones.

## Qué adoptamos y qué dejamos como hipótesis

| Consejo recibido | Decisión y evidencia |
|---|---|
| Título corto, palabras importantes al principio | Adoptado. YouTube lo recomienda. El objetivo de 30–65 caracteres es editorial; el límite de plataforma es 100. No existe una regla oficial que dé un peso especial exactamente a las dos primeras palabras. |
| Dejar curiosidad y algún emoji | Curiosidad concreta que el vídeo resuelve; cero o un emoji relevante. Nunca inventar popularidad, número de jugadores, disponibilidad, demo o fecha. YouTube recomienda moderar emojis y mayúsculas. |
| Cuatro o cinco hashtags | Objetivo práctico, no requisito del algoritmo. Usar juego, formato y dos o tres temas realmente respaldados. No inventar un tema para completar el número. Los hashtags obligatorios de campaña prevalecen. |
| #Shorts y #ViralShorts son obligatorios o los mejores | No respaldado por la documentación revisada. #Shorts se conserva como convención; #ViralShorts no se añade por defecto. No tenemos datos de volumen de búsqueda que prueben que sean los dos más populares. |
| Mismos hashtags en título y descripción | No es necesario. Dejamos el título limpio y los hashtags al final de la descripción. YouTube puede mostrar hasta tres de la descripción junto al título. |
| Lista de palabras clave separadas por comas en la descripción | No se añade. YouTube desaconseja/prohíbe usar listas de etiquetas ordinarias o frases repetitivas en descripciones. Incorporar uno o dos términos principales en frases naturales. El campo Etiquetas es distinto y tiene un papel reducido, útil sobre todo para erratas. |
| No publicar nunca directamente, dejar oculto/no listado | Revisar antes de publicar es útil. No hay evidencia de un premio por esperar en no listado; YouTube dice que pasar de no listado a público no perjudica y que importa la respuesta al hacerse público. Se puede usar privado, no listado o programar según la necesidad. |
| Publicar de 1 a 3 AM | No es una regla general. YouTube dice que el horario puede ayudar al arranque pero no se conoce que determine el rendimiento a largo plazo. Mantener la hora actual mientras probamos duración; posteriormente, comparar 01:30 Europe/Madrid con el horario habitual en grupos comparables. No iniciar todas las pruebas a la vez. |
| Ocultar el contador de me gusta y activarlo cuando crezca | Se puede ocultar el número, conservando la interacción. No se ha encontrado evidencia oficial de que aumente la distribución o frene el abandono. No se cambia por defecto ni se automatiza según un umbral inventado; puede evaluarse en una prueba separada. |
| Insertar menos de 0,3 s de imagen para elegir miniatura | No es necesario por defecto ni garantiza subir el CTR. La ayuda actual permite miniaturas personalizadas de Shorts en Studio de ordenador para cuentas verificadas y seleccionar un fotograma en la app de YouTube. Comprobar las opciones disponibles en la cuenta. Si falta la opción, preferir un fotograma real claro que ya exista en el vídeo. Una inserción fugaz puede producir un salto visible; cualquier prueba futura sería voluntaria, sin sustituir el arranque ni cortar la voz. |

## Fuentes oficiales

- [Títulos y miniaturas](https://support.google.com/youtube/answer/12340300?hl=en): títulos fieles, concisos, significado al principio, moderación de emojis; distinguir búsqueda de curiosidad.
- [Descripciones](https://support.google.com/youtube/answer/12948449?hl=en): descripción única, primeras líneas útiles y uno o dos términos principales.
- [Hashtags y sus políticas](https://support.google.com/youtube/answer/6390658?hl=en): relevancia, hasta tres junto al título, no sobreetiquetar ni poner listas de etiquetas ordinarias en descripciones. Más de 60 hashtags provoca que se ignoren todos; no es un objetivo de cantidad.
- [Preguntas sobre rendimiento](https://support.google.com/youtube/answer/141805?hl=en): pasar de no listado a público y efecto del horario.
- [Miniaturas de Shorts, ordenador](https://support.google.com/youtube/answer/72431?hl=en): sección específica para subir archivos propios en cuentas verificadas, recomendación vertical 9:16. Puede haber diferencias con interfaces antiguas o disponibilidad real de cuenta.
- [Elegir fotograma desde móvil](https://support.google.com/youtube/answer/10343433?hl=en): la selección de fotograma se edita en la app de YouTube, no desde Studio.
- [Ajustes de vídeo](https://support.google.com/youtube/answer/57404?hl=en): visibilidad del contador de likes, límites de título y descripción, papel de las etiquetas.
- [Descubrimiento de Shorts](https://support.google.com/youtube/answer/11914225?hl=en): porcentaje que decide ver, duración y porcentaje medio vistos, satisfacción y competencia.

## Interpretar el CTR correctamente

La miniatura puede influir donde el usuario elige un vídeo por su portada: búsqueda, canal y otras superficies con impresiones contabilizadas. El feed de Shorts reproduce directamente; su medida de elección es «se quedaron viendo / saltaron», no el CTR clásico de miniatura. No atribuir un cambio de visitas del feed a la portada basándose solo en el CTR agregado.

Nuestros datos anteriores señalaban una mayoría de tráfico desde el feed y una parte desde búsqueda. Por eso interesa mejorar título y portada, pero también conservar un arranque claro y fluido. No sacrificar el primer segundo por una portada.

## Cómo se usa el decisor nuevo

En la revisión de Promos de la app aparece «Título y descripción · YouTube Shorts».

1. Confirmar el juego, guion y briefing del vídeo.
2. Seleccionar explícitamente Orgánico o Campaña pagada. Para campañas pegar CTA y hashtags obligatorios, sin reescribirlos.
3. Preparar propuestas. Se solicitan tres alternativas: curiosidad concreta, juego/búsqueda primero y enfoque social si está respaldado. Si no hay proveedor disponible se usa texto derivado del guion; se indica que hace falta revisión.
4. Elegir y editar. La puntuación es una heurística editorial, no una predicción de visitas o CTR ni una verificación exhaustiva de hechos. Se bloquean varias exageraciones, cifras no presentes, exceso de longitud, hashtags en título y exceso de emojis; la revisión humana sigue siendo necesaria.
5. Copiar por separado o Guardar textos. Se guarda en la carpeta de entrega de la app, sin subir ni programar en YouTube. Las etiquetas y el CTA de campaña se revisan también al guardar.

El módulo compartido es `youtube_metadata.py`; el criterio persistente está en `data/youtube_metadata_policy.json`. Las propuestas para piezas existentes se dejan en `output/gestion/mejoras_youtube_2026-11-02/` y no reemplazan los textos que quizá ya se hayan usado para programar.

## Orden de las pruebas

Primero estabilizar voz y comparar las duraciones ya acordadas, manteniendo hora y estilo de presentación. Se puede aplicar el criterio editorial de títulos a los siguientes vídeos, pero no atribuir causalidad a la duración si también se cambia mucho el empaquetado. Después, si hay volumen suficiente, probar una sola variable: portada, enfoque de título, horario nocturno o contador visible. Registrar la variante, momento de publicación y métricas a 24 h, 72 h y siete días. Comparar dentro de cada canal y fuente; no re-subir el mismo juego para el ensayo ni romper los 21 días de separación.
