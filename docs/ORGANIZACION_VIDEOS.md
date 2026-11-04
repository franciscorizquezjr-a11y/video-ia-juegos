# Vídeos y exportaciones

Abre `VIDEOS.html` en la raíz.

```
output/
  videos/
    NextLoot/FECHA_pieza/
    PartyLoot/FECHA_pieza/
    Campanas/campaña/
    Por_clasificar/          # nuevas exportaciones de la app
  gestion/                  # calendarios y Excel de publicaciones
  trabajo/                  # recursos de carruseles y composición
```

Dentro de cada pieza: `video.mp4`, `textos/` y `edicion/`. Un mismo vídeo se comparte entre redes; solo se guardan variantes cuando el montaje cambia.

Las voces, guiones y subtítulos se conservan en `edicion/`. Los originales siguen en `input/`, `GamesFootage/` y `PromosFootage/`; son bibliotecas de fuentes, no exportaciones. `temp/` conserva sesiones recuperables. Presets e identidades visuales siguen en `Presets/`.

El Excel está en `output/gestion/Control_publicaciones.xlsx`. Los calendarios están en `output/gestion/<lote>/`. No hay una segunda carpeta de exportación ni accesos antiguos dentro de output.

Las nuevas exportaciones de vídeo de la app van a `output/videos/Por_clasificar/`; al aprobarlas se organizan en la marca correspondiente. Los carruseles y recursos de montaje van a `output/trabajo/`. Las rutas se definen en `project_paths.py` y las URL se obtienen de la ruta real del archivo.

Reconstruir el índice: `.venv/Scripts/python.exe scripts/build_video_library.py`.

Los documentos históricos se conservan en `archive/`. No indican que sus vídeos sigan disponibles ni que un borrador esté publicado.
