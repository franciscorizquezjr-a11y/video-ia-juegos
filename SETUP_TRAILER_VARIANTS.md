# Variantes de Set-up Trailer

En Video Creator → Set-up Trailer, el selector «Variante de montaje» permite elegir:

- **Clásico**: conserva el tráiler dentro del monitor durante todo el vídeo. Es el valor por defecto cuando no existe una elección guardada.
- **Entrar al monitor**: escritorio inicial, acercamiento suave, gameplay y regreso al escritorio. La elección se guarda con la calibración.

La entrada usa las esquinas del monitor que ya están calibradas. No contiene nombres de juegos, rutas a tráileres ni presets de marca fijos. Calcula la lectura a 220 palabras por minuto (mínimo 4 segundos) y después entra gradualmente durante 3 segundos. Ambos tiempos son ajustables; dejar la lectura vacía activa el cálculo automático. El movimiento durante la lectura es muy suave para conservar la legibilidad. Se reserva un regreso rápido y 1,5 segundos de escritorio al final. En clips cortos se reduce cada fase para mantener una sección de gameplay. Conserva la duración original.

El sonido de entrada se genera localmente con FFmpeg: un whoosh suave sincronizado con la llegada al monitor, volumen inicial del 25%, ajustable o desactivable. El audio del tráiler baja suavemente hasta un 25% mientras suena el efecto y recupera su nivel después. Funciona también en vídeos sin audio, sin APIs ni descargas. El modo Clásico no añade este sonido.

API: `intro_seconds` (null = automático, 0.5–30), `transition_seconds` (0.5–8), `entry_sfx_enabled` y `entry_sfx_volume` (0–1).

## Encuadre del gameplay

- **Tráiler completo** (predeterminado): conserva todos los bordes sobre un fondo difuminado. Apto para metraje horizontal o vertical.
- **Llenar vertical**: recorta el centro para ocupar el lienzo. Puede ocultar textos o elementos laterales.

El editor muestra la composición estática; la animación aparece en el vídeo exportado. Los textos o tarjetas que formen parte del fondo se desplazan fuera de plano con la cámara y reaparecen al regresar. La tarjeta se coloca encima o debajo de la pantalla según el espacio disponible. La tarjeta morada es opcional: se puede elegir un preset y editar el texto de cada vídeo. Se genera al exportar sobre un fondo limpio y desaparece durante el gameplay. No añade subtítulos.

## Código y API

- `setup_trailer_motion.py`: tiempos, medición del tráiler y movimiento de cámara.
- `setup_trailer_engine.py`: composición y selección del motor de montaje.
- `/api/setup-trailer/config` y `/api/setup-trailer/render`: campos `variant` (`classic` / `monitor_dive`) y `gameplay_fit` (`contain` / `cover`).
- Tarjeta opcional en la API: `card_enabled`, `card_text` (hasta 1200 caracteres) y `card_preset`.
- La variante de entrada exporta a 30 fps. Requiere FFmpeg y FFprobe; usa NVENC si está disponible y CPU como alternativa.

Comprobaciones: `python -m unittest discover -s tests -p test_setup_trailer_motion.py` y `node tests/test_setup_trailer_motion_ui.cjs`.


## Gancho y revelación opcionales

- `highlight_hook`: destaca en amarillo la primera frase de la tarjeta, sin recortar el párrafo. Desactivado por defecto.
- `opening_mode`: `original` conserva el inicio, `auto` busca actividad visual y `manual` usa `opening_start_seconds`. La búsqueda automática analiza movimiento sostenido y evita planos negros o casi uniformes; no identifica semánticamente la mejor jugada. Analiza hasta los primeros 120 segundos y excluye el tramo final. Reordena también el audio, incluyendo el inicio original al final, para mantener el metraje y la duración.
- `title_reveal_mode`: `immediate`, `after_entry` u `outro`. `title_reveal_delay` añade 0–10 segundos de espera tras la entrada. Si un clip es corto, se adelanta lo necesario para que el nombre llegue a verse.
- La revelación usa un rótulo horizontal sobre el gameplay y vuelve al rótulo en perspectiva sobre el escritorio. No elimina nombres que ya estén integrados en el vídeo original.
- Cada exportación incluye un `.settings.json` con el segundo de apertura elegido y el momento real de revelación. La API devuelve esos valores en `render_details`.

Pruebas adicionales: `python -m unittest discover -s tests -p test_setup_trailer_hooks.py`.


## Entrada «Acción continua → dentro del monitor»

Entrada opcional de la variante de cámara (`entrance_mode: action_match`; `desktop` conserva la anterior). Presenta el metraje durante `action_lead_seconds` (0.3–4 s; 1.2 por defecto), después lo reduce en perspectiva hasta encajar en las esquinas calibradas del monitor. La jugada y el audio avanzan continuamente desde la misma fuente: no hay un adelanto duplicado, congelación ni reinicio al llegar al escritorio. El encuadre inicial respeta la elección de tráiler completo o recorte vertical.

Después de ese comienzo se calcula el tiempo completo de lectura de la tarjeta y se hace el acercamiento al gameplay. Los tiempos, el sonido de entrada y la revelación del nombre usan un único reloj de montaje, incluyendo el adelanto inicial. La duración total sigue siendo la del tráiler. Las fases se reducen en clips cortos.

La geometría y continuidad se reutilizan con otros juegos y otros monitores. Elegir el instante anterior a una jugada requiere revisar el tráiler: la búsqueda automática por movimiento no reconoce impactos semánticamente. Se puede usar la apertura original o elegir su segundo manualmente. El formato no garantiza que cualquier instante tenga una acción interesante.

Prueba de continuidad con dos proporciones de imagen, monitor inclinado y audio marcado: `python -m unittest discover -s tests -p test_setup_trailer_action_match.py`.


## Logo del juego

`game_logo_enabled` (por defecto true) utiliza un logo disponible para el juego exacto; si no existe, conserva el título en texto. Reutiliza logos subidos en sesiones del mismo juego, archivos de su carpeta y logos oficiales de Steam con coincidencia de nombre. Nunca usa un logo global de otra sesión sin verificar el juego. La subida se guarda por juego; es preferible usar PNG transparente.

La revelación y el cierre de la variante de cámara muestran el logo grande, con proporción y transparencia conservadas, y retiran la tarjeta en el cierre para evitar superposiciones. La lectura inicial permanece. El modo clásico usa el logo sobre el monitor cuando hay tarjeta. `game_logo_scale` (0.5–1.5) ajusta la revelación grande. Si se oculta el título, tampoco se muestra su logo.

API: GET `/api/setup-trailer/game-logo?game_name=…` comprueba disponibilidad; POST `/api/setup-trailer/game-logo/upload` recibe `game_name` y `file` como multipart. La resolución de logos usa archivos locales primero; la búsqueda oficial en Steam requiere conexión. No genera un logo ficticio cuando falta el oficial.


## Formato completo: escenario + voz + subtítulos

En Set-up Trailer, selecciona **Formato completo · escenario + voz + subtítulos** en Variante de montaje. Funciona con cualquier escenario, juego y preset del canal. Mantiene la tarjeta, la entrada al monitor, el regreso al escenario y el logo cuando están activados.

Escribe el guion en el idioma del canal; si está vacío, usa el texto de la tarjeta. ElevenLabs genera la narración con la voz y el modelo configurados en la app. Los subtítulos se sincronizan con sus tiempos de palabras y resaltan la palabra activa con el color del preset. Se colocan bajo el gameplay para separar tarjeta, logo y subtítulos.

El deslizador de audio del tráiler controla el fondo de la narración (12 % por defecto, aproximadamente -18,4 dB respecto al original). Afecta a la mezcla original completa, incluidos sus efectos; no separa instrumentos ni voces del tráiler. La voz se refuerza para destacar sobre el fondo, con limitador en la mezcla. El modo habitual conserva su audio anterior.

El formato es opcional. El guion y el volumen se guardan en la configuración del editor; el audio se reutiliza para el mismo texto, voz y modelo. No se guardan claves en los archivos de receta. Si la voz es demasiado larga, la app pide acortar el guion, evitando cortarla al final. También admite tráilers sin audio.

Prueba de Party Loot: `output/gestion/PartyLoot/en/WheelMates/youtube_narrado.mp4`. Es una alternativa al montaje sin voz; no se ha publicado ni programado.

Referencia del servicio de sincronización: https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps/


## Narrado limpio: sin tarjeta y con cierre narrado

Selecciona **Narrado limpio · sin tarjeta + CTA final**. Es una opción adicional, compatible con cualquier escenario y preset. El backend excluye la tarjeta y su texto incluso si el borrador anterior los tenía activados. El logo del juego sigue siendo opcional e independiente.

El guion contiene el cuerpo de la narración. La frase de cierre se edita en **Llamada a la acción final**, en el mismo idioma que el guion. `{channel}` se sustituye por el nombre del preset elegido. La frase se añade al guion antes de generar la voz y los subtítulos; si el guion ya termina exactamente con ella, no se duplica.

La duración final sigue la locución: 0,2 s de arranque y 0,3 s de cierre, redondeados al fotograma siguiente. Se recorta el tráiler, no la voz. Si el audio no cabe en el fragmento elegido, se pide un guion más corto o un tráiler más largo. El acercamiento y el regreso al escenario se recalculan para esa duración; el regreso acompaña la llamada a la acción final. No se acelera la locución ni se añade una pausa larga al final.

Al elegir esta variante, la presentación del escenario dura 2,5 s por defecto antes del acercamiento. Se pueden ajustar los tiempos de cámara. El audio de fondo conserva el volumen elegido (12 % por defecto).

Prueba de Party Loot: `output/gestion/PartyLoot/en/WheelMates/youtube_narrado_limpio.mp4`. No publicado ni programado.
