# Encuadre y propuestas de edición visual · 31/10/2026

## Implementado en la app

Los individuales nuevos parten de pantalla completa (full_9_16) para NextLoot y PartyLoot. Siguen disponibles los modos anteriores blur_3_4 y blur_1_1. El selector guarda la preferencia por canal en data/production_preferences.json y la carga al volver a abrir la app. Restaurar una sesión conserva el formato de esa sesión sin cambiar el valor preferido para vídeos nuevos. Se conservan los guiones, voces, logos, sesiones y todos los motores anteriores.

El formato global es una elección de encuadre; no convierte automáticamente los vídeos antiguos ya renderizados ni decide por visión artificial dónde está el protagonista. La versión visual 2 del Top 3 tiene decisiones de encuadre por plano guardadas en su receta. Logos limpios y prioridades están guardados en el proyecto; el Top 3 tiene receta reproducible y recursos por marca, y aún no dispone de un botón de producción automática integrado en el asistente web.

Los cambios de la interfaz y backend se cargan al reiniciar la app. El MP4 del Top 3 ya entregado sigue disponible. No se ha interrumpido una instancia activa.

## Propuestas para el Top 3 — pendientes de aplicar

Primera prueba: conservar los tres juegos, duración, voz y mezcla, y mejorar la apertura y las señales visuales. Mostrar de inmediato el plano más sorprendente de Wanderburg, con la fortaleza devorando un pueblo, acompañado de una promesa breve; después entrar en el puesto 3. Para ello habrá que elegir un plano que realmente muestre esa acción. Una alternativa de guion para una revisión posterior es «Your house eats villages. And that's only one of these three games.»; esta frase todavía no está grabada ni incorporada.

Usar una señal breve por juego, durante aproximadamente 0,4–0,7 s: indicar el residente o la decoración en Hokko; mostrar la relación entre ciudad y ballena en Beyond These Stars; señalar el crecimiento o una nueva arma en Wanderburg. Las señales apuntan a elementos reales de ese plano y no inventan mecánicas.

Un indicador discreto de avance con tres posiciones puede reforzar el paso de 3 → 2 → 1. Evitar que compita con el logo, el número y los subtítulos. Cortar cuando termina una acción y dejar respirar la imagen cuando se necesita entenderla; los intervalos de 1–2,5 s son una guía de montaje, no una regla universal de retención.

## Propuestas para individuales — pendientes de aplicar

0–2 s: resultado o acción llamativa. 2–6 s: qué juego es y su premisa. 6–18 s: dos mecánicas con pruebas visuales claras y cortes en la acción. Últimos segundos: una consecuencia o giro real del juego y una pregunta breve. Ajustar a la duración necesaria; no rellenar una plantilla con tomas repetidas.

Combinar un plano de contexto con un detalle para dirigir la atención, respetando la regla de no volver a la entrada de tele/Gameboy. Si se usa una flecha, círculo, pausa breve o texto de apoyo, que explique una sola cosa y desaparezca. El número de capas simultáneas debe permitir seguir el juego en móvil.

Los acentos sonoros pueden apoyar la apertura y uno o dos cambios importantes; mantenerlos por debajo de la narración. No añadir un ruido en cada corte. Las tarjetas de logo limpias y los colores de canal se mantienen en ambos formatos.

## Medición

Estas son propuestas creativas, no mejoras demostradas. Comparar a 24 y 48 horas el porcentaje que se queda, duración media y curva de retención con piezas de duración parecida. En el Top 3 revisar las caídas al pasar de juego. Los 43 visionados de una sola pieza no bastan para atribuir el resultado a un elemento de edición. No dar por equivalente el «27 de retención» a todas las métricas: su etiqueta exacta sigue sin estar confirmada.

YouTube distingue la duración media de los usuarios que se quedaron viendo y los momentos de la curva de retención. Fuente oficial revisada: https://support.google.com/youtube/answer/9313698?hl=es . No es necesario borrar y volver a subir la pieza publicada para esta prueba: aplicar las ideas a la siguiente pieza o revisar un borrador antes de publicarlo.

## Comprobaciones

Se probaron guardado, lectura en una instancia nueva, aislamiento por marca, conservación de otros ajustes, protección de un JSON dañado, restauración de sesiones, orden de cambios rápidos y respuesta tardía al cargar la preferencia. También se mantuvieron los controles de logo y la recuperación del progreso SSE. Los tests usan datos temporales y no cambian las preferencias reales del usuario.
