# Abrir IA Video Studio y organizar el proyecto

## Abrir la app

Haz doble clic en **IA Video Studio**, en el escritorio o dentro del proyecto. También puedes usar **Abrir app.bat**. Los lanzadores `run_web.bat`, `ejecutar.bat` y `python run_web.py` conducen al mismo arranque.

El acceso directo usa `.venv/Scripts/pythonw.exe`: no necesitas activar el entorno manualmente. El servidor se ejecuta en segundo plano y Chrome abre una pestaña cuando está listo. Si Chrome no está disponible, se usa el navegador predeterminado.

El lanzador busca primero una instancia de esta misma carpeta. Si ya está abierta, la reutiliza; si no, arranca preferentemente en `http://127.0.0.1:8004/`. Si ese puerto está ocupado por otro programa, busca uno libre entre 8000 y 8009. El punto `/api/health` identifica la aplicación y la carpeta sin publicar rutas ni claves.

La apertura normal no instala paquetes, no actualiza Git y no activa recarga automática. Esto evita que un doble clic modifique código o interrumpa renders. Cerrar la pestaña deja el servidor activo para reutilizarlo. Si mueves el proyecto, usa el BAT desde la nueva carpeta y vuelve a crear el acceso directo; los `.lnk` contienen una ruta absoluta.

## Diagnóstico

- `logs/launcher.log`: apertura y errores del lanzador.
- `logs/app_server.log`: salida del servidor iniciado por el acceso directo.
- `logs/launcher_state.json`: última URL y PID; el lanzador verifica el servidor por HTTP, no se fía solo de este archivo.
- Consultar sin arrancar: `.\.venv\Scripts\python.exe run_web.py --status`.
- Arrancar sin pestaña: `.\.venv\Scripts\python.exe run_web.py --no-browser`.

Si falta `.venv`, se muestra un error. Instalar o actualizar dependencias es una operación separada; nunca se hace automáticamente al abrir.

## Carpetas

La estructura vigente está en [ORGANIZACION_VIDEOS.md](ORGANIZACION_VIDEOS.md). Exportaciones únicamente en `output/`, con `videos/`, `gestion/` y `trabajo/`.

## Añadir marcas, idiomas y formatos

Usa una carpeta de preset por marca y los IDs del registro de publicaciones para distinguir red e idioma. Conserva por separado cada montaje realmente distinto; una publicación debe apuntar a la pieza exacta y mantener su estado confirmado.

Para un formato nuevo, trabaja en un módulo independiente y expón opciones compatibles en API e interfaz. Evita añadir scripts de un solo vídeo a `input/` o duplicar todo el motor por canal. Los ejemplos antiguos se agrupan por fecha fuera de la raíz.

## Limpieza

Revisar candidatos sin borrar:

```powershell
.\.venv\Scripts\python.exe scripts/maintenance.py
```

Ejecutar únicamente las reglas comprobadas:

```powershell
.\.venv\Scripts\python.exe scripts/maintenance.py --apply
```

Se exige una salida final válida para limpiar intermedios de una sesión terminada. Se conservan fuentes, voces, planos, guiones, análisis y resultados. La herramienta bloquea la limpieza con FFmpeg activo o si no puede comprobar su estado; rechaza rutas fuera del proyecto y archivos fuera de sus reglas. Cada ejecución genera un informe en `data/maintenance/`.

No vaciar `temp/`, no borrar voces para recuperar espacio y no borrar una ruta antigua de un vídeo final sin revisar sus enlaces. Las reglas actuales no reparan archivos dañados ni modifican el entorno.


