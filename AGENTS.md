# AGENTS.md

## Contexto y arquitectura
- App web local para crear vídeos sociales verticales de juegos propios y ajenos. El README describe principalmente el pipeline original; contrastarlo con la implementación actual.
- Backend FastAPI en `web_server.py`; frontend HTML/CSS/JS sin compilación en `static/`, con estado global, eventos del DOM y calendario en `schedule.js`. No hay proyecto npm.
- Promo V3 permite ejecutar y revisar cinco pasos: detección de planos → análisis visual con Gemini → guion y locución → decisiones de montaje (EDL) → render FFmpeg. `promo_v3_engine.py` contiene el motor; `audio_aligner.py` gestiona voz, tiempos por palabra y subtítulos ASS; `script_retention.py`, la estructura narrativa.
- Conviven el pipeline original (`main.py`/`pipeline.py`), V1 multivídeo (`run_v1_pipeline.py`) y V2/V3 web. `app.py` es la interfaz Streamlit heredada. No retirar versiones por parecer duplicadas.
- También existen scraping, lotes, benchmarks, gestión de juegos y métricas, carruseles Steam, composición sobre un monitor y extracción de retención mediante OpenCV, sin LLM.
- Gemini, ElevenLabs y FFmpeg se usan desde varios módulos; el aislamiento en un único módulo anunciado en el README ya no describe el proyecto.

## Preparación, ejecución y comprobaciones
- Ejecutar desde la raíz con un intérprete Python dedicado. Instalar con `python -m pip install -r requirements.txt`; FFmpeg y FFprobe deben estar en PATH. Promo V3 y retención requieren `scenedetect`, `opencv-python` y `numpy`, incluidos en el manifiesto. `faster-whisper` es opcional.
- Configurar `.env` siguiendo `.env.example`. `AppConfig.from_env()` exige Gemini y ElevenLabs, incluso en tests de ensamblado sin llamadas externas; Groq se usa en generación textual con alternativas Gemini.
- Arranque normal: acceso directo `IA Video Studio`, `Abrir app.bat` o `python run_web.py`. Usan `scripts/launch_app.py`, esperan `/api/health`, reutilizan esta misma instancia y abren Chrome. Prefieren 8004 y prueban 8000–8009. Sin instalación automática, Git pull ni recarga automática. Para verificaciones, `--no-browser` y `--status`.
- Tests: `python test_script_retention.py`; `python -m unittest discover -s tests -p "test_*.py"` para esquemas, proxies y renders sintéticos; `python -m unittest test_retention_cv` requiere las imágenes de `static/retention_samples`. `test_e2e_retention.py` es una comprobación por consola, sin assertions.
- Tras cambios web, comprobar el flujo afectado, los eventos SSE y la recuperación de sesión; tras cambios de render, verificar resolución, duración y sincronización audiovisual.

## Convenciones y límites
- Los trabajos largos usan hilos y progreso SSE; su registro vive en memoria. Mantener compatibles los contratos entre API, frontend y JSON persistido.
- Las sesiones V3 usan identificadores derivados de rutas y archivos por paso en `temp/v3_cache`; algunos datos se replican en `PromosFootage/<juego>`. Conservar la invalidación de voz, EDL y render cuando cambia el guion.
- Mantener rutas relativas a la raíz, JSON UTF-8, tiempos referidos al vídeo original y compatibilidad Windows. Revisar conjuntamente prompts, parsers y esquemas al cambiar respuestas de IA.
- No sobrescribir `.env`, datos locales, presets, medios ni sesiones recuperables. `temp/` no es enteramente desechable; hay JSON de usuario versionados aunque `.gitignore` ignore nuevos archivos.
- Revisar `git status` antes de editar y preservar los cambios existentes. Mantener las exclusiones de recarga de medios y datos para evitar interrumpir renders.

- **Cambios del 4/11/2026 (leer antes de editar):** `docs/CAMBIOS_2026-11-04.md`. Seguridad local (Host/Origin, claves enmascaradas), `json_store.py` para JSON compartidos, `_safe_session_dir()` para rutas, `fonts.py`, OpenCV <5 y nuevas opciones opcionales (`hook_type` direct/custom + `opening_text`, `speech_speed`, `similarity_boost`, `cut_start_mode`, `audio_normalize`, `audio_ducking`, `output_fps`, `warnings`).
- Estructura y mantenimiento: `docs/ESTRUCTURA_Y_ARRANQUE.md`. No vaciar `temp/`; la herramienta `scripts/maintenance.py` genera un plan por defecto y solo elimina intermedios verificables con `--apply`. Errores de lectura detectados el 18/10/2026: consultar `data/maintenance/unreadable_20261018.json`.
