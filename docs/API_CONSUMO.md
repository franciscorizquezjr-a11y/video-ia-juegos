# Consumo y límites de API

Implementado el 18/10/2026. Se abre en **Ajustes → Consumo y límites de API**.

## Qué muestra

- Gráficas de los últimos 7, 30 o 90 días: peticiones, tokens informados o caracteres enviados. Las llamadas fallidas se cuentan como peticiones y errores.
- Consumo local registrado desde la activación del panel. No reconstruye trabajos anteriores ni incluye otras aplicaciones. Cambiar de clave conserva el historial local de esta app.
- Presupuesto local opcional por proveedor: diario o mensual desde el día 1, en peticiones, tokens o caracteres según corresponda. Es un objetivo informativo: no impide generar ni modifica límites del proveedor.
- Las fechas y los presupuestos locales usan Europe/Madrid. Los límites oficiales mantienen la periodicidad informada por su proveedor.

## Datos del proveedor

**ElevenLabs:** consulta de solo lectura de la suscripción: caracteres utilizados, límite, disponibles y fecha de renovación. Se distingue el cupo del ciclo de cualquier consumo adicional permitido por el plan. Los caracteres enviados que registra la app son una estimación de actividad, no créditos facturados: los modelos pueden tener factores de coste diferentes.

Si aparece «Falta permiso de lectura», edita la clave en ElevenLabs y activa **Usuario → Acceso**. Guarda allí los permisos y pulsa **Actualizar** en nuestra app. La clave actual denegó esa consulta por falta de permiso; la generación de voz es un permiso independiente. La consulta se reutiliza durante 120 segundos salvo actualización manual.

**Gemini:** tokens reales devueltos por las respuestas cuando están disponibles. No se inventa un saldo global a partir de la clave. Los límites varían por proyecto y modelo; el límite diario de peticiones se reinicia a medianoche del Pacífico. El enlace a AI Studio permite consultar las cuotas oficiales.

**Groq:** tokens informados y últimos límites recibidos en las cabeceras de una llamada real, por modelo: peticiones por día y tokens por minuto. Una ventana vencida pasa a «sin datos vigentes» hasta recibir otra respuesta; no se supone que vuelve a estar llena. No se hace una generación para obtener estas cabeceras.

## Registro y privacidad

El registro está en `data/api_usage.sqlite3` (incluidos sus archivos SQLite auxiliares). Guarda fechas, proveedor, modelo, estado y cantidades; no guarda claves, guiones, audio ni respuestas completas. Las consultas de saldo se asocian internamente a una huella de la clave para evitar mostrar una cuota antigua tras cambiarla. La base está excluida de Git.

La instrumentación cubre las llamadas de generación existentes de los motores web, análisis, secuenciación, guion, locución y herramientas auxiliares. No cuenta descargas, subida de archivos a Gemini ni llamadas hechas fuera de esta app. Las consultas de saldo no se suman como generaciones. Si el proveedor no devuelve tokens se muestra como dato desconocido, no como consumo confirmado de cero. Los fallos del registro no deben impedir generar vídeos.

## Fuentes oficiales

Consultadas el 18/10/2026:

- [ElevenLabs: consulta de suscripción](https://elevenlabs.io/docs/api-reference/user/subscription/get).
- [Gemini: límites por proyecto y modelo](https://ai.google.dev/gemini-api/docs/rate-limits).
- [Groq: límites y cabeceras de respuesta](https://console.groq.com/docs/rate-limits).
