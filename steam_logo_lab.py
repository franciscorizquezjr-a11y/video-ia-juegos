"""Steam capsule logo extraction, independent of the video rendering pipeline."""
import io
import json
import math
import re
import threading
import uuid
import time
from collections import Counter
from pathlib import Path
from project_paths import WORK_OUTPUT_DIR
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
RESULTS = WORK_OUTPUT_DIR / "steam_logo_lab"
_rembg_session = None
_rembg_lock = threading.Lock()
_ben_model = None
_ben_device = None
_ben_lock = threading.Lock()
MODEL_CACHE = ROOT / "temp" / "logo_lab_models"
ALGORITHMS = (("color", "Limpieza por color"), ("rembg", "rembg / U2Net"),
              ("ben2", "BEN2"), ("grabcut", "GrabCut"))


def parse_app_id(value):
    value = value.strip()
    if value.isdigit():
        app_id = int(value)
    else:
        parsed = urlparse(value)
        match = re.match(r"^/app/(\d+)(?:/|$)", parsed.path)
        if parsed.scheme != "https" or parsed.hostname != "store.steampowered.com" or not match:
            raise ValueError("Introduce un App ID o una URL https://store.steampowered.com/app/…")
        app_id = int(match.group(1))
    if not 0 < app_id < 10**10:
        raise ValueError("App ID de Steam inválido.")
    return app_id


def pixel_box(box, size, padding=0.02):
    """Legacy normalized [ymin, xmin, ymax, xmax] into PIL bounds."""
    if not isinstance(box, list) or len(box) != 4:
        raise ValueError("La detección no devolvió un recorte válido.")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1000 for v in box):
        raise ValueError("Coordenadas de detección inválidas.")
    y1, x1, y2, x2 = box
    if x2 <= x1 or y2 <= y1:
        raise ValueError("El recorte detectado está vacío.")
    w, h = size
    px, py = (x2 - x1) * padding, (y2 - y1) * padding
    bounds = (max(0, math.floor((x1-px)*w/1000)), max(0, math.floor((y1-py)*h/1000)),
              min(w, math.ceil((x2+px)*w/1000)), min(h, math.ceil((y2+py)*h/1000)))
    if bounds[2]-bounds[0] < 2 or bounds[3]-bounds[1] < 2:
        raise ValueError("El logo detectado es demasiado pequeño.")
    return bounds


def download_image(url):
    # Steam metadata may contain versioned asset paths. Never fetch arbitrary hosts.
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "shared.akamai.steamstatic.com", "cdn.akamai.steamstatic.com",
        "cdn.cloudflare.steamstatic.com", "shared.fastly.steamstatic.com",
        "cdn.fastly.steamstatic.com", "store.akamai.steamstatic.com",
    }:
        raise ValueError("Steam devolvió un servidor de imágenes no permitido.")
    with requests.get(url, timeout=30, stream=True, allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Steam no devolvió una imagen válida.")
        content = bytearray()
        for chunk in response.iter_content(65536):
            content.extend(chunk)
            if len(content) > 20 * 1024 * 1024:
                raise ValueError("La cápsula supera 20 MB.")
    with Image.open(io.BytesIO(content)) as image:
        if image.width * image.height > 20_000_000:
            raise ValueError("La cápsula es demasiado grande.")
        return image.convert("RGB")


def validate_bounds(bounds, size):
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        raise ValueError("El recorte debe contener [izquierda, arriba, derecha, abajo].")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in bounds):
        raise ValueError("El recorte debe usar píxeles enteros de la cápsula original.")
    x1, y1, x2, y2 = bounds
    if not (0 <= x1 < x2 <= size[0] and 0 <= y1 < y2 <= size[1]) or min(x2-x1, y2-y1) < 3:
        raise ValueError("El recorte está fuera de la imagen o es demasiado pequeño.")
    return tuple(bounds)


def _cv_regions(image):
    """Letter-like MSER regions. No OCR, neural detector or external service."""
    import cv2
    import numpy as np
    rgb = np.array(image.convert("RGB"))
    height, width = rgb.shape[:2]
    scale = min(1.0, 960 / max(width, height))
    if scale < 1:
        rgb = cv2.resize(rgb, (round(width*scale), round(height*scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    detector = cv2.MSER_create(5, max(8, int(w*h*0.00004)), max(60, int(w*h*0.10)))
    masks, boxes = detector.detectRegions(gray)
    candidates = []
    for points, (x, y, bw, bh) in zip(masks, boxes):
        x, y, bw, bh = map(int, (x, y, bw, bh))
        if not (max(5, h*.025) <= bh <= h*.55 and .08 <= bw/bh <= 3.5 and bw <= w*.50):
            continue
        region = np.zeros((bh, bw), dtype=np.uint8)
        region[points[:, 1]-y, points[:, 0]-x] = 255
        extent = len(points)/(bw*bh)
        if extent < .10:
            continue
        # Contrast against the thin ring around the region, not the full scene.
        patch = gray[max(0, y-2):min(h, y+bh+2), max(0, x-2):min(w, x+bw+2)]
        ring = np.concatenate((patch[:2].ravel(), patch[-2:].ravel(), patch[:, :2].ravel(), patch[:, -2:].ravel()))
        contrast = abs(float(gray[points[:, 1], points[:, 0]].mean()) - float(np.median(ring)))
        if contrast < 7:
            continue
        strokes = cv2.distanceTransform(region, cv2.DIST_L2, 3)
        stroke = float(np.percentile(strokes[strokes > 0], 75))
        candidates.append({"box": (x, y, x+bw, y+bh), "contrast": contrast,
                           "stroke": stroke, "region": region})
    # Remove nested duplicate detections of the same character.
    unique = []
    for item in sorted(candidates, key=lambda r: -r["contrast"]):
        x1, y1, x2, y2 = item["box"]
        duplicate = False
        for other in unique:
            a, b, c, d = other["box"]
            intersection = max(0, min(x2, c)-max(x1, a))*max(0, min(y2, d)-max(y1, b))
            union = (x2-x1)*(y2-y1)+(c-a)*(d-b)-intersection
            if intersection/max(1, union) > .65:
                duplicate = True
                break
        if not duplicate:
            unique.append(item)
        if len(unique) >= 400:
            break
    return unique, scale, (w, h)


def detect_logo(image, name=None):
    """Rank aligned letter groups and return original-image coordinates."""
    import numpy as np
    regions, scale, (w, h) = _cv_regions(image)
    groups = []
    remaining = set(range(len(regions)))
    while remaining:
        group = [remaining.pop()]
        queue = list(group)
        while queue:
            i = queue.pop()
            x1, y1, x2, y2 = regions[i]["box"]
            hi = y2-y1
            for j in list(remaining):
                a, b, c, d = regions[j]["box"]
                hj = d-b
                overlap = max(0, min(y2, d)-max(y1, b))/min(hi, hj)
                gap = max(0, max(x1, a)-min(x2, c))
                stroke_ratio = regions[i]["stroke"]/max(.1, regions[j]["stroke"])
                if .45 <= hi/hj <= 2.2 and overlap >= .55 and gap <= max(hi, hj)*1.6 and .25 <= stroke_ratio <= 4:
                    remaining.remove(j)
                    group.append(j)
                    queue.append(j)
        if len(group) < 2:
            continue
        boxes = [regions[i]["box"] for i in group]
        bounds = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
        bw, bh = bounds[2]-bounds[0], bounds[3]-bounds[1]
        if bw < w*.10 or bw/bh < 1.1:
            continue
        heights = [b[3]-b[1] for b in boxes]
        regularity = 1/(1+float(np.std(heights))/max(1, float(np.mean(heights))))
        contrast = min(1, float(np.mean([regions[i]["contrast"] for i in group]))/80)
        prominence = min(1, float(np.median(heights))/max(1, h*.25))
        score = regularity*.25 + contrast*.20 + min(1, len(group)/8)*.10 + min(1, bw/w)*.10 + prominence*.35
        groups.append({"box": bounds, "score": score, "indices": group})
    if not groups:
        return {"found": False, "method": "opencv-mser", "bounds": [0, 0, *image.size],
                "needs_review": True, "score": 0, "seed_boxes": [],
                "warning": "CV no encontró un grupo de letras fiable. Ajusta el recorte sobre la cápsula."}
    groups.sort(key=lambda g: (-g["score"], g["box"][1], g["box"][0]))
    selected = groups[0]
    x1, y1, x2, y2 = selected["box"]
    indices = list(selected["indices"])
    # Include adjacent, horizontally overlapping title/subtitle lines.
    for group in groups[1:]:
        a, b, c, d = group["box"]
        overlap = max(0, min(x2, c)-max(x1, a))/max(1, min(x2-x1, c-a))
        gap = max(0, max(y1, b)-min(y2, d))
        if overlap >= .5 and gap < (y2-y1)*.9 and group["score"] >= selected["score"]*.65:
            x1, y1, x2, y2 = min(x1, a), min(y1, b), max(x2, c), max(y2, d)
            indices.extend(group["indices"])
    pad_x, pad_y = max(4, (x2-x1)*.025), max(4, (y2-y1)*.12)
    bounds = [max(0, math.floor((x1-pad_x)/scale)), max(0, math.floor((y1-pad_y)/scale)),
              min(image.width, math.ceil((x2+pad_x)/scale)), min(image.height, math.ceil((y2+pad_y)/scale))]
    seeds = [[round(v/scale) for v in regions[i]["box"]] for i in indices]
    coverage = (bounds[2]-bounds[0])*(bounds[3]-bounds[1])/(image.width*image.height)
    needs_review = coverage > .65 or selected["score"] < .60 or (len(groups) > 1 and groups[1]["score"] > selected["score"]*.95)
    return {"found": True, "method": "opencv-mser", "bounds": bounds,
            "needs_review": needs_review, "score": round(selected["score"], 3), "seed_boxes": seeds,
            "warning": "Detección aproximada: revisa el título y ajusta el recorte si es necesario."}


def fetch_main_capsule(app_id, data):
    """Prefer the main capsule, not appdetails' tiny capsule_231x87 thumbnail."""
    base = f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/"
    for filename in ("capsule_616x353_2x.jpg", "capsule_616x353.jpg"):
        try:
            return download_image(base + filename), base + filename, "main"
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
    source = data.get("header_image")
    if not source:
        raise ValueError("El juego no tiene cápsula principal ni cabecera disponible.")
    return download_image(source), source, "header_fallback"


def remove_flat_background(crop):
    """Color-distance matte for flat capsule backgrounds, including letter holes.

    Saliency models can retain background between letters. Use this conservative
    path only when most perimeter pixels agree on one background color.
    """
    rgb = crop.convert("RGB")
    sample = rgb.copy()
    sample.thumbnail((256, 256))
    w, h = sample.size
    edge = [sample.getpixel((x, y)) for x in range(w) for y in (0, h-1)]
    edge += [sample.getpixel((x, y)) for y in range(1, h-1) for x in (0, w-1)]
    if not edge:
        return None
    bucket, _ = Counter(tuple(v//24 for v in p) for p in edge).most_common(1)[0]
    cluster = [p for p in edge if tuple(v//24 for v in p) == bucket]
    background = tuple(sum(p[c] for p in cluster)/len(cluster) for c in range(3))
    distance = lambda p: math.sqrt(sum((p[c]-background[c])**2 for c in range(3)))
    if sum(distance(p) <= 38 for p in edge) / len(edge) < 0.80:
        return None
    # A 45..80 distance ramp removes faint paper/grid texture while retaining ink.
    data_reader = getattr(rgb, "get_flattened_data", rgb.getdata)
    pixels = list(data_reader())
    alpha_values = [round(max(0, min(1, (distance(p)-45)/35))*255) for p in pixels]
    strong_foreground = sum(a > 200 for a in alpha_values) / len(pixels)
    if not 0.015 <= strong_foreground <= 0.80:
        return None
    result = rgb.convert("RGBA")
    # Undo the background color blended into partially transparent edge pixels.
    result.putdata([tuple(round(max(0, min(255, (p[c] - background[c]*(1-a/255))/(a/255))))
                          for c in range(3)) + (a,) if 0 < a < 255 else p + (a,)
                    for p, a in zip(pixels, alpha_values)])
    alpha = Image.new("L", rgb.size)
    alpha.putdata(alpha_values)
    result.putalpha(alpha)
    return result


def remove_rembg_background(crop):
    global _rembg_session
    try:
        from rembg import new_session, remove
    except ImportError as exc:
        raise RuntimeError('Instala rembg con: python -m pip install "rembg[cpu]"') from exc
    with _rembg_lock:
        if _rembg_session is None:
            _rembg_session = new_session("u2net")
        return remove(crop.copy(), session=_rembg_session).convert("RGBA")


def remove_ben2_background(crop):
    global _ben_model, _ben_device
    try:
        from ben2 import BEN_Base
        import torch
    except ImportError as exc:
        raise RuntimeError("BEN2 no está disponible. Instala las dependencias de requirements.txt.") from exc
    with _ben_lock:
        if _ben_model is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            model = BEN_Base.from_pretrained("PramaLLC/BEN2", cache_dir=str(MODEL_CACHE / "ben2"))
            model.to(device).eval()
            _ben_device, _ben_model = device, model
        with torch.inference_mode():
            logo = _ben_model.inference(crop.convert("RGB"), refine_foreground=False)
        logo = logo.convert("RGBA")
        logo.info["device"] = _ben_device
        return logo


def remove_grabcut_background(image, bounds, seed_boxes=None):
    import cv2
    import numpy as np
    x1, y1, x2, y2 = validate_bounds(bounds, image.size)
    pad = max(8, round(max(x2-x1, y2-y1)*.20))
    context_bounds = (max(0, x1-pad), max(0, y1-pad), min(image.width, x2+pad), min(image.height, y2+pad))
    context = image.crop(context_bounds).convert("RGB")
    rgb = np.array(context)
    cx, cy = context_bounds[:2]
    lx1, ly1, lx2, ly2 = x1-cx, y1-cy, x2-cx, y2-cy
    mask = np.full(rgb.shape[:2], cv2.GC_BGD, dtype=np.uint8)
    mask[ly1:ly2, lx1:lx2] = cv2.GC_PR_FGD
    exterior = rgb[mask == cv2.GC_BGD]
    background_distance = None
    if len(exterior):
        background = np.median(exterior.astype(np.float32), axis=0)
        distances = np.linalg.norm(exterior.astype(np.float32)-background, axis=1)
        if float(np.mean(distances < 38)) >= .8:
            background_distance = np.linalg.norm(rgb.astype(np.float32)-background, axis=2)
            # Holes and flat paper/grid texture should not become sure foreground.
            interior = mask[ly1:ly2, lx1:lx2]
            interior[background_distance[ly1:ly2, lx1:lx2] < 38] = cv2.GC_PR_BGD
    # Preserve letter pixels as foreground, never the entire character rectangle.
    regions, scale, _ = _cv_regions(image)
    for region in regions:
        a, b, c, d = [round(v/scale) for v in region["box"]]
        if not (x1 <= a < c <= x2 and y1 <= b < d <= y2):
            continue
        if seed_boxes and not any(max(abs(v-s) for v, s in zip((a, b, c, d), seed)) <= 4/scale for seed in seed_boxes):
            continue
        seed = cv2.resize(region["region"], (c-a, d-b), interpolation=cv2.INTER_NEAREST)
        seed = cv2.erode(seed, np.ones((3, 3), dtype=np.uint8))
        roi = mask[b-cy:d-cy, a-cx:c-cx]
        positive = seed > 0
        if background_distance is not None:
            positive &= background_distance[b-cy:d-cy, a-cx:c-cx] > 45
        roi[positive] = cv2.GC_FGD
    # A full-image/manual crop still needs background samples for initialization.
    mask[:2, :] = mask[-2:, :] = cv2.GC_BGD
    mask[:, :2] = mask[:, -2:] = cv2.GC_BGD
    cv2.setRNGSeed(0)
    cv2.grabCut(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), mask, None,
                np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64), 5, cv2.GC_INIT_WITH_MASK)
    alpha = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    logo = image.crop(bounds).convert("RGBA")
    logo.putalpha(Image.fromarray(alpha[ly1:ly2, lx1:lx2]))
    return logo


def finalize_logo(logo):
    logo = logo.convert("RGBA")
    alpha = logo.getchannel("A")
    bounds = alpha.point(lambda v: 255 if v > 8 else 0).getbbox()
    if not bounds:
        raise ValueError("La eliminación de fondo ha borrado todo el logo. Ajusta el recorte.")
    if alpha.getextrema()[0] == 255:
        raise ValueError("El algoritmo no ha conseguido generar transparencia.")
    logo = logo.crop(bounds)
    # Transparent breathing room makes the output immediately usable as an overlay.
    result = Image.new("RGBA", (logo.width+16, logo.height+16))
    result.alpha_composite(logo, (8, 8))
    return result


def remove_background(crop):
    """Compatibility helper for callers of the original single-result extractor."""
    logo = remove_flat_background(crop)
    method = "flat-color-matte" if logo is not None else "rembg-u2net"
    result = finalize_logo(logo if logo is not None else remove_rembg_background(crop))
    result.info["background_method"] = method
    return result


def compare_algorithms(image, metadata, bounds, detection, progress, parent_run_id=None):
    bounds = validate_bounds(bounds, image.size)
    crop = image.crop(bounds)
    run_id = uuid.uuid4().hex
    folder = RESULTS / run_id
    folder.mkdir(parents=True)
    image.save(folder / "capsule.png")
    crop.save(folder / "crop.png")
    annotated = image.copy()
    ImageDraw.Draw(annotated).rectangle(bounds, outline="#ff6633", width=3)
    annotated.save(folder / "detection.png")
    base = f"/api/logo-lab/results/{run_id}"
    result = {**metadata, "schema_version": 2, "run_id": run_id, "parent_run_id": parent_run_id,
              "detector": detection, "crop_bounds": list(bounds), "capsule_size": list(image.size),
              "original_capsule_url": base+"/capsule.png", "capsule_url": base+"/detection.png",
              "crop_url": base+"/crop.png", "algorithms": [],
              "warning": detection.get("warning", "Revisa las letras y los bordes de cada resultado.")}
    functions = {"color": lambda: remove_flat_background(crop.copy()),
                 "rembg": lambda: remove_rembg_background(crop.copy()),
                 "ben2": lambda: remove_ben2_background(crop.copy()),
                 "grabcut": lambda: remove_grabcut_background(image, bounds, detection.get("seed_boxes"))}
    # Persist the source before model loading so interrupted jobs remain recoverable.
    def persist():
        temporary = folder / "result.json.tmp"
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(folder / "result.json")
    result["complete"] = False
    persist()
    for index, (algorithm, label) in enumerate(ALGORITHMS):
        progress(35+index*15, f"Procesando {label}…" + (" La primera carga puede descargar el modelo." if algorithm in {"ben2", "rembg"} else ""))
        started = time.perf_counter()
        entry = {"id": algorithm, "label": label, "status": "error"}
        try:
            raw = functions[algorithm]()
            if raw is None:
                entry.update(status="not_applicable", error="El fondo no es suficientemente uniforme para la limpieza por color.")
            else:
                device = raw.info.get("device")
                logo = finalize_logo(raw)
                filename = f"logo-{algorithm}.png"
                logo.save(folder / filename)
                entry.update(status="completed", logo_size=list(logo.size),
                             image_url=base+"/"+filename, download_url=base+f"/download-{algorithm}")
                if device:
                    entry["device"] = device
        except (Exception, SystemExit) as exc:
            entry["error"] = str(exc)
        entry["duration_seconds"] = round(time.perf_counter()-started, 3)
        result["algorithms"].append(entry)
        persist()
        progress(50+index*15, f"{label}: " + {"completed": "terminado", "error": "error", "not_applicable": "no aplicable"}[entry["status"]])
    result["complete"] = True
    result["success_count"] = sum(entry["status"] == "completed" for entry in result["algorithms"])
    if not result["success_count"]:
        result["warning"] += " Ningún método produjo un PNG válido. Ajusta el recorte y vuelve a calcular."
    persist()
    return result


def extract_logo(app_id, progress):
    progress(8, "Obteniendo la cápsula principal de Steam…")
    response = requests.get("https://store.steampowered.com/api/appdetails",
                            params={"appids": app_id, "l": "spanish"}, timeout=25)
    response.raise_for_status()
    entry = response.json().get(str(app_id), {})
    if not entry.get("success"):
        raise ValueError("Steam no ha encontrado ese juego.")
    data = entry["data"]
    name = data.get("name", str(app_id))
    image, source, source_kind = fetch_main_capsule(app_id, data)
    progress(25, "Localizando grupos de letras con OpenCV / MSER…")
    detection = detect_logo(image, name)
    return compare_algorithms(image, {"app_id": app_id, "name": name, "source_url": source, "source_kind": source_kind},
                              detection["bounds"], detection, progress)


def load_result(run_id):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise FileNotFoundError("Resultado no encontrado")
    return json.loads((RESULTS / run_id / "result.json").read_text(encoding="utf-8"))


def recalculate_logo(run_id, bounds, progress):
    previous = load_result(run_id)
    with Image.open(RESULTS / run_id / "capsule.png") as source:
        image = source.convert("RGB")
    validate_bounds(bounds, image.size)
    metadata = {key: previous[key] for key in ("app_id", "name", "source_url", "source_kind") if key in previous}
    detection = {"method": "manual", "bounds": list(bounds), "found": True, "needs_review": False,
                 "seed_boxes": [], "warning": "Recorte manual. Revisa las letras y los bordes de cada resultado."}
    return compare_algorithms(image, metadata, bounds, detection, progress, parent_run_id=run_id)


def register_routes(app, create_job):
    from fastapi import HTTPException, UploadFile, File
    from fastapi.responses import FileResponse
    from pydantic import BaseModel, Field, StrictInt

    class LogoRequest(BaseModel):
        steam_input: str = Field(min_length=1, max_length=250)

    class RecalculateRequest(BaseModel):
        run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
        crop_bounds: list[StrictInt] = Field(min_length=4, max_length=4)

    def launch(operation):
        job = create_job("steam_logo")

        def worker():
            try:
                job.status = "running"
                result = operation(job.update)
                job.complete(result, message=f"Comparación terminada: {result['success_count']}/4 PNG generados.")
            except Exception as exc:
                job.fail(str(exc))

        threading.Thread(target=worker, daemon=True).start()
        return {"job_id": job.job_id}

    @app.post("/api/logo-lab/extract")
    def start_logo_extraction(request: LogoRequest):
        try:
            app_id = parse_app_id(request.steam_input)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return launch(lambda progress: extract_logo(app_id, progress))

    @app.post("/api/logo-lab/recalculate")
    def recalculate(request: RecalculateRequest):
        try:
            load_result(request.run_id)
            with Image.open(RESULTS / request.run_id / "capsule.png") as source:
                validate_bounds(request.crop_bounds, source.size)
        except FileNotFoundError as exc:
            raise HTTPException(404, "Extracción original no encontrada") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return launch(lambda progress: recalculate_logo(request.run_id, request.crop_bounds, progress))

    @app.post("/api/logo-lab/upload")
    async def upload_logo(file: UploadFile = File(...)):
        from io import BytesIO
        from PIL import ImageOps
        try:
            data = await file.read(20 * 1024 * 1024 + 1)
            if len(data) > 20 * 1024 * 1024:
                raise HTTPException(413, "La imagen no puede superar 20 MB.")
            with Image.open(BytesIO(data)) as source:
                if source.width * source.height > 20_000_000:
                    raise HTTPException(413, "La imagen no puede superar 20 millones de píxeles.")
                image = ImageOps.exif_transpose(source).convert("RGB")
            bounds = [0, 0, image.width, image.height]
            validate_bounds(bounds, image.size)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, "Sube una imagen válida de al menos 3 × 3 píxeles.") from exc
        finally:
            await file.close()
        metadata = {"name": Path(file.filename or "Imagen subida").name,
                    "source_kind": "upload", "source_url": ""}
        detection = {"method": "manual", "bounds": bounds, "found": True,
                     "needs_review": False, "seed_boxes": [],
                     "warning": "Imagen ya recortada: se ha aplicado únicamente el borrado de fondo."}
        return launch(lambda progress: compare_algorithms(image, metadata, bounds, detection, progress))

    @app.get("/api/logo-lab/results/{run_id}")
    def result_metadata(run_id: str):
        try:
            return load_result(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, "Resultado no encontrado") from exc

    @app.get("/api/logo-lab/results/{run_id}/{asset}")
    def logo_result(run_id: str, asset: str):
        allowed = {"capsule.png", "detection.png", "crop.png", "logo.png", "result.json", "download"}
        allowed.update(f"logo-{key}.png" for key, _ in ALGORITHMS)
        allowed.update(f"download-{key}" for key, _ in ALGORITHMS)
        if not re.fullmatch(r"[a-f0-9]{32}", run_id) or asset not in allowed:
            raise HTTPException(404, "Resultado no encontrado")
        filename = "logo.png" if asset == "download" else f"logo-{asset[9:]}.png" if asset.startswith("download-") else asset
        path = RESULTS / run_id / filename
        if not path.is_file():
            raise HTTPException(404, "Resultado no encontrado")
        return FileResponse(path, filename=f"steam-{filename[:-4]}-{run_id[:8]}.png" if asset.startswith("download") else None)
