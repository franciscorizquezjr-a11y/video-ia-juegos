# Logo Lab

También puedes seleccionar una imagen ya recortada en **O sube tu imagen ya
recortada** y pulsar **Borrar fondo de mi imagen**. Se aplican los cuatro métodos
a toda la imagen sin consultar Steam ni ejecutar la detección. El endpoint
`POST /api/logo-lab/upload` recibe el archivo multipart `file` (máximo 20 MB y
20 millones de píxeles) y conserva los mismos trabajos SSE, resultados y descargas.

En **Video Producer → Logo Lab**, introduce una URL de Steam o su App ID y pulsa
**Extraer logo**. La prueba muestra la cápsula con el área detectada, un recorte
común y cuatro resultados independientes: limpieza por color, rembg/U2Net, BEN2
y GrabCut. Cambia el fondo de revisión para comprobar los bordes y descarga el
PNG de cada método.

El sistema consulta Steam, busca la cápsula principal (con alternativa de cabecera
indicada en pantalla) y localiza el título con regiones MSER de OpenCV, contraste
y agrupación de componentes similares a letras. No utiliza OCR neuronal, LLM,
API key ni el logo oficial de la biblioteca como sustituto. Si no encuentra grupos
útiles, muestra la cápsula completa y pide ajustar el recorte.

Instala las dependencias con `python -m pip install -r requirements.txt` en el
entorno Python de la aplicación. BEN2 necesita PyTorch y torchvision compatibles;
el paquete BEN2 se instala desde su repositorio oficial y requiere Git. La primera
carga de rembg o BEN2 puede descargar pesos. BEN2 utiliza CUDA cuando el PyTorch
instalado la soporta y está disponible; de lo contrario usa CPU. Los pesos de BEN2
se guardan en `temp/logo_lab_models/ben2`.

La limpieza por color indica «No aplicable» en fondos no uniformes, sin sustituirse
por otro modelo. U2Net y BEN2 reciben el mismo recorte y se cargan una vez por
proceso; BEN2 usa `refine_foreground=False`, como el script de referencia. GrabCut
usa el contexto exterior como fondo, regiones MSER como semillas y cinco
iteraciones. Cada método registra su tiempo y error; los fallos son independientes.

Puedes dibujar, mover o redimensionar el rectángulo sobre la cápsula y editar X, Y,
ancho y alto en píxeles originales. **Recalcular los 4 métodos** utiliza la cápsula
guardada, sin volver a consultar Steam, y crea otra extracción vinculada a la
anterior. El recorte original se conserva con **Restaurar recorte**.

Las pruebas se ejecutan en un hilo con progreso SSE. Al recargar se recupera el
trabajo en curso, el último resultado y los ajustes pendientes del recorte.
Los resultados antiguos de un solo algoritmo siguen siendo visibles. Si el
servidor se reinicia durante una prueba, hay que volver a iniciarla porque los
trabajos viven en memoria; la comparación anterior sigue disponible.

Cada extracción guarda `capsule.png`, `detection.png`, `crop.png`, los archivos
`logo-color.png`, `logo-rembg.png`, `logo-ben2.png` y `logo-grabcut.png` de los
métodos exitosos, y `result.json` en `output/steam_logo_lab/<run_id>/`.
Los metadatos conservan App ID, fuente, detección, recorte en píxeles originales,
el identificador de la prueba anterior y los resultados por algoritmo.
`steam_logo_lab.extract_logo(app_id, progress)` es el punto de reutilización para
el futuro formato de vídeos completos.

Es una prueba experimental: el modelo de eliminación de fondo puede borrar
letras finas o conservar ilustración de la cápsula. La transparencia válida no
garantiza que el recorte contenga exclusivamente el logo; revisa las vistas previas.

API:

- `POST /api/logo-lab/extract` con `steam_input`: inicia un trabajo SSE.
- `POST /api/logo-lab/recalculate` con `run_id` y `crop_bounds`:
  `[izquierda, arriba, derecha, abajo]`, en píxeles enteros originales.
- `GET /api/logo-lab/results/<run_id>`: metadatos guardados, versión de esquema 2.
- `GET /api/logo-lab/results/<run_id>/download-<método>`: PNG del método.
- Los enlaces antiguos `logo.png` y `download` siguen sirviendo extracciones antiguas.

Comprobaciones sin llamadas externas:

```powershell
python -m unittest discover -s tests -p test_steam_logo_lab.py -v
node tests/test_logo_lab_ui.cjs
```
