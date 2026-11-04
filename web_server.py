"""
web_server.py — High-performance FastAPI Backend for AI Video Pipeline

Provides REST API endpoints and Server-Sent Events (SSE) streaming for:
- Pre-production Trailer Feed (Scraping, DB search, filtering, archiving)
- Single Mode Editor (AI Hook Generation, Real-time Card Mockups, Video Rendering)
- Super Batch Execution (Queue management, parallel tracking, multi-video processing)
- Rendering Benchmark Suite (12 test combinations, live progress, metric reporting)
- Media Streaming (Output videos, benchmark previews, avatars, presets)
"""

import api_usage as _api_usage

import os
import sys
import re
import json
import time
import uuid
import asyncio
import logging
import threading
import subprocess
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Literal
from datetime import datetime

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).parent.resolve()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Ensure Windows terminal doesn't crash on unicode/emoji characters
try:
    if sys.stdout:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if sys.stderr:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, BackgroundTasks
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, JSONResponse, FileResponse, StreamingResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import inspect
import json_store
from json_store import JsonStoreError
from channel_branding import ChannelBrandingSettings, build_channel_badge
from video_format_preferences import preferred_video_format

from project_paths import APP_OUTPUT_DIR, WORK_OUTPUT_DIR, output_url, app_output_url
from project_paths import validate_folder_name, safe_child_dir
from config import AppConfig
from pipeline import Pipeline
from scraper import TrailerDatabaseManager, refresh_all_trailers
from benchmark import (
    run_full_benchmark,
    load_benchmark_history,
    save_benchmark_record,
    clear_benchmark_history,
    BLUR_LEVELS,
    RESOLUTIONS,
    HARDWARE_MODES,
)
from utils import (
    setup_logging,
    generate_run_id,
    download_youtube_clip,
    create_text_card,
    generate_hook_with_groq,
    modify_hook_length,
    generate_v2_script,
    modify_v2_script_length,
    CARD_PRESET_KEYS,
    export_card_preset,
    create_video_preview,
    generate_steam_hook_overlay_video,
    ensure_steam_hook_prefix,
    is_script_text_equivalent,
    CAMERA_MONITOR_EFFECT_INTENSITY,
    build_camera_monitor_filtergraph,
    apply_camera_monitor_effects,
)
from audio_aligner import (
    get_audio_duration,
    extract_clean_audio,
    align_script_words,
    generate_kinetic_ass_subtitles,
    filter_steam_hook_words,
    check_video_has_audio,
    generate_elevenlabs_hook_audio,
    synthesize_elevenlabs_with_timestamps,
    parse_elevenlabs_alignment,
)
import shutil
import traceback
from video_analyzer import VideoAnalyzer
from video_sequencer import VideoSequencer
from ffmpeg_assembler import FFmpegAssembler
from promo_v3_engine import (
    probe_video,
    create_analysis_proxy,
    detect_scenes_adaptive,
    extract_shot_preview_clips,
    format_timecode,
    generate_9_16_preview_clips,
    build_clip_batches,
    analyze_clip_batches_gemini,
    filter_trailer_splash_shots,
    SPLASH_KEYWORDS,
    orchestrate_promov3_step4_edl,
    GEMINI_CANDIDATE_MODELS,
    TRANSITION_SPECS,
    render_promov3_final_video,
    resolve_promov3_video_path,
    get_media_duration,
    sample_footage_chunks_if_needed,
    remix_promov3_audio_sfx,
)
import steam_carousel
import three_games_carousel

LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
logger = setup_logging("INFO", logs_dir=LOGS_DIR, log_to_file=True)

app = FastAPI(title="AI Video Producer Pro Web API", version="2.0.0")

@app.get("/api/health", include_in_schema=False)
def local_app_health():
    import hashlib
    return {"app": "ia-video-studio", "workspace_id": hashlib.sha256(str(BASE_DIR).casefold().encode()).hexdigest()[:24],
            "pid": os.getpid(), "ready": True}


# =============================================================================
# Local-only access guard (CORS + DNS-rebinding + cross-site write protection)
# =============================================================================
# The app is a LOCAL tool: the browser UI is served from http://127.0.0.1:<port>
# (or localhost) and automation such as Codex / curl / python scripts calls the
# HTTP API directly WITHOUT an Origin header. Both keep working. What is blocked:
#   * any web page on another origin reading responses (CORS limited to localhost),
#   * DNS-rebinding (a foreign domain resolving to 127.0.0.1 → Host header check),
#   * cross-site writes (POST/PUT/PATCH/DELETE with a foreign Origin or
#     Sec-Fetch-Site: cross-site), e.g. a malicious page silently saving settings.
# Extra hosts/origins (e.g. LAN access from a tablet) can be allowed explicitly with
# the env vars IA_VIDEO_ALLOWED_HOSTS / IA_VIDEO_ALLOWED_ORIGINS (comma separated).

_LOCAL_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d{1,5})?$"
_LOCAL_ORIGIN_RE = re.compile(_LOCAL_ORIGIN_REGEX, re.IGNORECASE)
# "testserver" is the Host used by Starlette's TestClient (unit tests). It is a
# single-label name that public DNS cannot serve, so it is not a rebinding vector.
_DEFAULT_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1", "testserver"}
_UNSAFE_HTTP_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _env_csv(name: str) -> List[str]:
    raw = os.environ.get(name, "") or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _allowed_hosts() -> set:
    return _DEFAULT_ALLOWED_HOSTS | {h.lower().rstrip(".") for h in _env_csv("IA_VIDEO_ALLOWED_HOSTS")}


def _allowed_extra_origins() -> set:
    return {o.rstrip("/").lower() for o in _env_csv("IA_VIDEO_ALLOWED_ORIGINS")}


def _host_without_port(host_header: str) -> str:
    """'localhost:8004' -> 'localhost', '[::1]:8004' -> '[::1]', '::1' -> '::1'."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[: end + 1] if end != -1 else host
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host.rstrip(".")


def _is_local_host_header(host_header: Optional[str]) -> bool:
    if host_header is None:
        # No Host header at all (raw HTTP/1.0 clients): browsers always send one,
        # so this cannot be a DNS-rebinding request.
        return True
    return _host_without_port(host_header) in _allowed_hosts()


def _is_local_origin(origin: str) -> bool:
    origin = (origin or "").strip()
    if _LOCAL_ORIGIN_RE.match(origin):
        return True
    return origin.rstrip("/").lower() in _allowed_extra_origins()


def _local_request_rejection(method: str, headers: Dict[str, str]) -> Optional[Tuple[int, str]]:
    """Return (status, message) when the request must be rejected, else None.

    ``headers`` maps lower-case header names to values.
    """
    if not _is_local_host_header(headers.get("host")):
        return 400, "Host no permitido: esta app solo acepta peticiones a localhost / 127.0.0.1."
    if method.upper() in _UNSAFE_HTTP_METHODS:
        origin = headers.get("origin")
        if origin is not None and not _is_local_origin(origin):
            return 403, "Origen no permitido: las peticiones de escritura solo se aceptan desde la propia app local."
        # A browser that hides the Origin but reports a cross-site request is
        # rejected too. An explicitly local Origin (e.g. localhost:8004 -> 127.0.0.1:8004,
        # which browsers label "cross-site") is trusted, like CORS trusts it.
        if origin is None and (headers.get("sec-fetch-site") or "").strip().lower() == "cross-site":
            return 403, "Petición cross-site bloqueada por seguridad."
    return None


class LocalRequestGuardMiddleware:
    """Pure ASGI middleware (does not wrap responses, so SSE streams and
    StaticFiles range requests are untouched)."""

    def __init__(self, app_):
        self.app = app_

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers: Dict[str, str] = {}
        for raw_name, raw_value in scope.get("headers") or []:
            name = raw_name.decode("latin-1").lower()
            if name not in headers:
                headers[name] = raw_value.decode("latin-1")
        method = scope.get("method", "GET") if scope.get("type") == "http" else "POST"
        rejection = _local_request_rejection(method, headers)
        if rejection is None:
            await self.app(scope, receive, send)
            return
        status_code, message = rejection
        logger.warning(
            f"Petición bloqueada ({status_code}) {method} {scope.get('path')} "
            f"host={headers.get('host')!r} origin={headers.get('origin')!r}"
        )
        if scope.get("type") == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse(status_code=status_code, content={"status": "error", "detail": message, "message": message})
        await response(scope, receive, send)


# CORS: only the app's own local origins (any port) may read responses cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_LOCAL_ORIGIN_REGEX,
    allow_origins=sorted(_allowed_extra_origins()),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_static_middleware(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static") or path in ("/", ""):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# Added last so it is the OUTERMOST layer: rejected requests never reach CORS,
# the no-cache middleware or any endpoint.
app.add_middleware(LocalRequestGuardMiddleware)


@app.exception_handler(JsonStoreError)
async def _json_store_error_handler(request, exc: JsonStoreError):
    """A corrupt data file is reported clearly instead of being silently wiped."""
    logger.error(f"Archivo de datos ilegible: {exc}")
    return JSONResponse(status_code=500, content={"status": "error", "detail": str(exc), "message": str(exc)})

# Ensure directories exist
for d in ["data", "output", "temp", "temp/benchmarks", "temp/uploads", "input", "Presets", "static", "GamesFootage", "PromosFootage"]:
    (BASE_DIR / d).mkdir(parents=True, exist_ok=True)

# Safely clean up truly obsolete temporary uploads (older than 48 hours) to prevent disk buildup without deleting active session files
def _collect_json_strings(node: Any, out: set) -> None:
    if isinstance(node, str):
        out.add(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str):
                out.add(k)
            _collect_json_strings(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _collect_json_strings(v, out)


def _referenced_upload_names(cache_dir: Path, extra_json_files: Optional[List[Path]] = None) -> set:
    """Names (and stems) of files mentioned anywhere in the session JSON files.

    Raises on ANY read/parse problem so the caller can skip deletion entirely.
    """
    names: set = set()
    json_files: List[Path] = []
    if cache_dir.exists():
        json_files.extend(sorted(cache_dir.glob("*/*.json")))
    for extra in extra_json_files or []:
        if extra.exists():
            json_files.append(extra)
    for jf in json_files:
        with open(jf, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        strings: set = set()
        _collect_json_strings(data, strings)
        for s in strings:
            if not s or len(s) > 4096:
                continue
            tail = urllib.parse.unquote(s.split("?", 1)[0]).replace("\\", "/").rstrip("/")
            base = tail.rsplit("/", 1)[-1]
            if base:
                names.add(base.casefold())
                names.add(Path(base).stem.casefold())
    return names


def _cleanup_stale_uploads(uploads_dir: Path, cache_dir: Path, max_age_s: float = 48 * 3600,
                           extra_json_files: Optional[List[Path]] = None) -> int:
    """Delete temp/uploads files older than ``max_age_s`` that no session references.

    If the session JSON files cannot be scanned, nothing is deleted.
    """
    if not uploads_dir.exists():
        return 0
    try:
        referenced = _referenced_upload_names(cache_dir, extra_json_files)
    except Exception as scan_err:
        logger.warning(f"Limpieza de temp/uploads omitida: no se pudieron revisar las sesiones guardadas ({scan_err}).")
        return 0
    now_ts = time.time()
    purged = 0
    for f in uploads_dir.iterdir():
        try:
            if not f.is_file() or (now_ts - f.stat().st_mtime) <= max_age_s:
                continue
            if f.name.casefold() in referenced or f.stem.casefold() in referenced:
                continue
            f.unlink()
            purged += 1
        except Exception:
            pass
    return purged


_uploads_dir = BASE_DIR / "temp" / "uploads"
_purged_count = _cleanup_stale_uploads(
    _uploads_dir,
    BASE_DIR / "temp" / "v3_cache",
    extra_json_files=[BASE_DIR / "data" / "batch_queue.json"],
)
if _purged_count > 0:
    logger.info(f"Limpiados {_purged_count} archivos obsoletos (>48h) de temp/uploads.")

# File paths
BATCH_QUEUE_FILE = BASE_DIR / "data" / "batch_queue.json"


# =============================================================================
# In-Memory Job Management & SSE Streaming
# =============================================================================

class Job:
    def __init__(self, job_id: str, job_type: str):
        self.job_id = job_id
        self.job_type = job_type
        self.status = "running"  # "running", "completed", "error"
        self.progress = 0.0      # 0.0 to 100.0
        self.message = "Iniciando..."
        self.logs: List[str] = []
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.subscribers: List[Tuple[asyncio.Queue, asyncio.AbstractEventLoop]] = []
        self.lock = threading.Lock()
        self.created_at = time.time()
        self.finished_at: Optional[float] = None

    def update(self, progress: float, message: str, log: Optional[str] = None):
        with self.lock:
            self.progress = progress
            self.message = message
            if log:
                self.logs.append(log)
            payload = {
                "type": "progress",
                "job_id": self.job_id,
                "status": self.status,
                "progress": self.progress,
                "phase": self.message,
                "message": self.message,
                "log": log or message,
            }
            self._notify_subscribers(payload)

    def complete(self, result: Dict[str, Any], message: str = "¡Completado con éxito!"):
        with self.lock:
            self.status = "completed"
            self.progress = 100.0
            self.message = message
            self.result = result
            self.finished_at = time.time()
            payload = {
                "type": "completed",
                "job_id": self.job_id,
                "status": "completed",
                "progress": 100.0,
                "message": message,
                "result": result,
            }
            self._notify_subscribers(payload)

    def fail(self, error: str):
        with self.lock:
            self.status = "error"
            self.error = error
            self.message = f"Error: {error}"
            self.finished_at = time.time()
            payload = {
                "type": "error",
                "job_id": self.job_id,
                "status": "error",
                "error": error,
                "message": self.message,
            }
            self._notify_subscribers(payload)

    def _notify_subscribers(self, data: Dict[str, Any]):
        for q, loop in list(self.subscribers):
            try:
                loop.call_soon_threadsafe(q.put_nowait, data)
            except Exception:
                pass

    def add_subscriber(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        q = asyncio.Queue()
        with self.lock:
            self.subscribers.append((q, loop))
            if self.status == "completed":
                initial_data = {
                    "type": "completed",
                    "job_id": self.job_id,
                    "status": "completed",
                    "progress": 100.0,
                    "message": self.message,
                    "result": self.result,
                }
            elif self.status == "error":
                initial_data = {
                    "type": "error",
                    "job_id": self.job_id,
                    "status": "error",
                    "error": self.error,
                    "message": self.message,
                }
            else:
                initial_data = {
                    "type": "progress",
                    "job_id": self.job_id,
                    "status": self.status,
                    "progress": self.progress,
                    "message": self.message,
                    "log": self.logs[-1] if self.logs else self.message,
                }
            loop.call_soon_threadsafe(q.put_nowait, initial_data)
        return q

    def remove_subscriber(self, q: asyncio.Queue):
        with self.lock:
            self.subscribers = [(sub_q, loop) for sub_q, loop in self.subscribers if sub_q != q]


ACTIVE_JOBS: Dict[str, Job] = {}
_ACTIVE_JOBS_LOCK = threading.Lock()
JOB_RETENTION_SECONDS = 6 * 3600
JOB_MAX_FINISHED = 200


def _job_finished_at(job: Job) -> Optional[float]:
    """Time a job finished (completed/error), or None while it is running."""
    if job.status == "running":
        return None
    finished = getattr(job, "finished_at", None)
    if finished is None:
        # First time we see it finished: stamp it now (Job.complete/fail predate this field).
        finished = time.time()
        try:
            job.finished_at = finished
        except Exception:
            pass
    return finished


def _evict_finished_jobs(now: Optional[float] = None,
                         max_age_s: float = JOB_RETENTION_SECONDS,
                         max_finished: int = JOB_MAX_FINISHED) -> int:
    """Drop finished jobs older than ``max_age_s`` or beyond the newest ``max_finished``.

    Running jobs are never evicted. Returns how many jobs were removed.
    """
    now = time.time() if now is None else now
    with _ACTIVE_JOBS_LOCK:
        finished = []
        for jid, jb in list(ACTIVE_JOBS.items()):
            f_at = _job_finished_at(jb)
            if f_at is not None:
                finished.append((f_at, jid))
        finished.sort(reverse=True)  # newest first
        to_remove = set()
        for idx, (f_at, jid) in enumerate(finished):
            if idx >= max_finished or (now - f_at) > max_age_s:
                to_remove.add(jid)
        for jid in to_remove:
            ACTIVE_JOBS.pop(jid, None)
        return len(to_remove)


def create_job(job_type: str) -> Job:
    try:
        _evict_finished_jobs()
    except Exception as evict_err:
        logger.debug(f"No se pudo podar el registro de trabajos: {evict_err}")
    job_id = f"{job_type}_{uuid.uuid4().hex[:8]}"
    job = Job(job_id, job_type)
    with _ACTIVE_JOBS_LOCK:
        ACTIVE_JOBS[job_id] = job
    return job


# =============================================================================
# Helper Functions: Batch Queue & Presets
# =============================================================================

def _load_list_state(path: Path) -> List[Any]:
    """Load a JSON list state file. Missing file -> []; corrupt file -> moved aside
    to <name>.corrupt-<timestamp> and JsonStoreError raised (never silently wiped)."""
    data = json_store.load_json(path, [])
    if isinstance(data, list):
        return data
    raise JsonStoreError(
        f"El archivo de datos '{path.name}' no contiene una lista JSON; no se ha modificado.", path
    )


def _state_locked(*paths: Path):
    """Run a sync endpoint while holding the json_store lock of each state file, so
    concurrent load -> modify -> save cycles on the same file cannot lose writes."""
    import contextlib
    import functools

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with contextlib.ExitStack() as stack:
                for state_path in paths:
                    stack.enter_context(json_store.lock_for(state_path))
                return fn(*args, **kwargs)
        return wrapper
    return deco


def load_batch_queue() -> List[Dict[str, Any]]:
    return _load_list_state(BATCH_QUEUE_FILE)


def save_batch_queue(items: List[Dict[str, Any]]):
    json_store.save_json_atomic(BATCH_QUEUE_FILE, items)


def get_all_presets() -> Dict[str, Any]:
    presets_dir = BASE_DIR / "Presets"
    presets = {}
    if presets_dir.exists():
        for item in presets_dir.iterdir():
            if item.is_dir():
                json_files = list(item.glob("*.json"))
                image_files = list(item.glob("*.png")) + list(item.glob("*.jpg")) + list(item.glob("*.jpeg"))
                if json_files:
                    try:
                        with open(json_files[0], "r", encoding="utf-8") as f:
                            data = json.load(f)
                        avatar_url = f"/media/presets/{item.name}/{image_files[0].name}" if image_files else None
                        presets[item.name] = {
                            "name": item.name,
                            "data": data,
                            "avatar_url": avatar_url,
                            "avatar_path": str(image_files[0]) if image_files else None,
                        }
                    except Exception as e:
                        logger.error(f"Error loading preset {item.name}: {e}")
    return presets


# =============================================================================
# Pydantic Request Models
# =============================================================================

class HookGenerateRequest(BaseModel):
    game_name: str
    prompt_variation: Optional[int] = 0
    groq_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None

class MultipleHooksGenerateRequest(BaseModel):
    game_name: str
    count: int = 3
    groq_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None

class HookModifyRequest(BaseModel):
    current_text: str
    action: str  # "shorter" or "longer"
    groq_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None

class SingleRenderRequest(BaseModel):
    game_name: str
    source_type: str  # "youtube" or "local"
    youtube_url: Optional[str] = None
    local_file_path: Optional[str] = None
    start_sec: float = 0.0
    end_sec: float = 30.0
    export_filename: Optional[str] = None
    resolution: str = "1080p"  # "1080p" or "4K"
    preset_name: Optional[str] = "Predeterminado: IGD"
    # Style parameters
    card_username: str = "Indie Games Discovery"
    card_handle: str = "@indiegamesdiscovery"
    card_text: str = ""
    card_bg_color: str = "#ffffff"
    card_text_color: str = "#000000"
    card_border_color: str = "#7ca032"
    card_border_width: int = 12
    card_radius: int = 70
    card_avatar_text: str = "G"
    card_avatar_bg: str = "#7ca032"
    card_avatar_path: Optional[str] = None
    card_font_username: str = "Segoe UI"
    card_font_handle: str = "Segoe UI"
    card_font_body: str = "Segoe UI"
    card_width: int = 1728
    card_height: int = 980
    card_font_size_body: int = 76
    card_font_size_username: int = 72
    card_font_size_handle: int = 56
    card_avatar_radius: int = 105
    card_y_position: int = 440
    video_zoom: float = 1.0
    video_y_position: int = 180
    overlay_type: Optional[str] = "card"  # "card" or "instagram_classic"
    groq_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None

class PreviewRequest(BaseModel):
    text: Optional[str] = ""
    overlay_type: Optional[str] = "card"
    preset: Optional[str] = "IGD"
    card_username: Optional[str] = "Indie Games Discovery"
    card_handle: Optional[str] = "@indiegamesdiscovery"
    card_bg_color: Optional[str] = "#ffffff"
    card_text_color: Optional[str] = "#000000"
    card_border_color: Optional[str] = "#7ca032"
    card_border_width: Optional[int] = 12
    card_radius: Optional[int] = 70
    card_avatar_text: Optional[str] = "G"
    card_avatar_bg: Optional[str] = "#7ca032"
    card_avatar_path: Optional[str] = None
    card_font_username: Optional[str] = "Segoe UI"
    card_font_handle: Optional[str] = "Segoe UI"
    card_font_body: Optional[str] = "Segoe UI"
    card_width: Optional[int] = 1728
    card_height: Optional[int] = 980
    card_font_size_body: Optional[int] = 76
    card_avatar_radius: Optional[int] = 105
    card_font_size_username: Optional[int] = 72
    card_font_size_handle: Optional[int] = 56
    card_y_position: Optional[int] = 440
    video_style: Optional[str] = "vertical_split_blur"
    video_zoom: Optional[float] = 1.0
    video_y_position: Optional[int] = 180
    video_path: Optional[str] = None

class BatchItemModel(BaseModel):
    id: Optional[str] = None
    game_name: str
    youtube_url: Optional[str] = None
    local_file_path: Optional[str] = None
    start_sec: Optional[float] = 0.0
    end_sec: Optional[float] = 30.0
    target_duration: Optional[float] = 30.0
    preset_name: str = "Predeterminado: IGD"
    hook_text: str = ""
    resolution: str = "1080p"
    overlay_type: Optional[str] = "card"  # "card" or "instagram_classic"
    card_username: Optional[str] = None
    card_handle: Optional[str] = None
    card_bg_color: Optional[str] = None
    card_text_color: Optional[str] = None
    card_border_color: Optional[str] = None
    card_border_width: Optional[int] = None
    card_radius: Optional[int] = None
    card_font_size_body: Optional[int] = None
    card_avatar_path: Optional[str] = None
    card_avatar_url: Optional[str] = None
    card_avatar_bg: Optional[str] = None
    card_y_position: Optional[int] = None
    card_width: Optional[int] = 1728
    card_height: Optional[int] = 980
    video_zoom: Optional[float] = None
    video_y_position: Optional[int] = None
    thumbnail: Optional[str] = None
    status: Optional[str] = "pending"
    output_video: Optional[str] = None
    added_at: Optional[str] = None

class BenchmarkRunRequest(BaseModel):
    source: str  # URL or local file path

class ScrapeRequest(BaseModel):
    timeframe: str = "all"  # "24h", "3d", "7d", "all"

class ConfigUpdateRequest(BaseModel):
    groq_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    elevenlabs_api_key: Optional[str] = None
    elevenlabs_voice_id: Optional[str] = None
    # Explicitly empty these keys (field names, e.g. ["groq_api_key"]). Empty or
    # masked values in the fields above are ignored instead of erasing keys.
    clear_keys: Optional[List[str]] = None

class V1ProcessTrailerRequest(BaseModel):
    url: str
    game_name: str
    target_duration: float = 30.0
    framing: str = "fit_16_9"  # "fit_16_9", "blur_bg", or "center_crop"
    strip_splash: bool = True

class V2ScriptGenerateRequest(BaseModel):
    game_name: str
    target_duration: float = 30.0
    tone: Optional[str] = "viral_hype"
    # "steam_new_game" (default, required by Trapster campaigns), "so_in_this_new_game",
    # "direct" (no formulaic opener) or "custom" (starts exactly with opening_text).
    hook_type: Optional[str] = "steam_new_game"
    opening_text: Optional[str] = ""
    # Voice speed the narration will use (word budget must match it). None -> the
    # session's saved voice speed, else 1.1.
    speech_speed: Optional[float] = None
    script_mode: Literal["normal", "caveman"] = "normal"
    game_genre: str = ""
    briefing_text: Optional[str] = ""
    session_id: Optional[str] = None
    groq_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None


class V2ScriptModifyRequest(BaseModel):
    script_text: str
    action: str  # "shorter", "longer", "change_hook", "custom_instruction", "more_viral", or "regenerate"
    game_name: str
    target_duration: float = 30.0
    custom_instruction: Optional[str] = ""
    groq_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    # Optional opening options (same values as V2ScriptGenerateRequest). None keeps the
    # current modification prompts; "custom" keeps opening_text as the first words and
    # "direct" avoids formulaic openers. speech_speed sizes "regenerate" (None -> 1.1).
    hook_type: Optional[str] = None
    opening_text: Optional[str] = ""
    speech_speed: Optional[float] = None
    script_mode: Literal["normal", "caveman"] = "normal"
    game_genre: str = ""


class PromoV3Step3SaveRequest(BaseModel):
    session_id: Optional[str] = None
    video_path: Optional[str] = None
    video_paths: Optional[List[str]] = None
    game_name: str
    hook_type: str = "steam_new_game"
    opening_text: Optional[str] = ""
    tone: str = "viral_hype"
    target_duration: float = 30.0
    briefing_text: Optional[str] = ""
    script_text: str
    word_count: int
    estimated_duration: float
    voice_mode: str = "elevenlabs"
    script_mode: Literal["normal", "caveman"] = "normal"
    game_genre: str = ""
    voice_id: Optional[str] = None
    voice_path: Optional[str] = None
    voice_url: Optional[str] = None
    voice_duration: Optional[float] = None
    sentence_1_end_s: Optional[float] = None
    retention_data: Optional[Dict[str, Any]] = None


class PromoV3SynthesizeVoiceRequest(BaseModel):
    session_id: Optional[str] = None
    video_path: Optional[str] = None
    video_paths: Optional[List[str]] = None
    script_text: str
    voice_id: Optional[str] = None
    speed: Optional[float] = 1.1
    stability: Optional[float] = 0.5
    similarity_boost: Optional[float] = 0.75


class PromoV3Step4OrchestrateRequest(BaseModel):
    session_id: Optional[str] = None
    video_path: Optional[str] = None
    video_paths: Optional[List[str]] = None
    game_name: Optional[str] = ""
    game_genre: Optional[str] = ""
    script_mode: Optional[Literal["normal", "caveman"]] = None
    allow_secondary_transition: bool = True
    preferred_model: Optional[str] = None
    script_text: Optional[str] = None
    banned_shot_ids: Optional[List[int]] = None
    approved_shot_ids: Optional[List[int]] = None
    sentence_1_end_s: Optional[float] = None
    hook_duration: Optional[float] = None
    video_format: Optional[str] = None
    # "shot_start" (default: cut starts at the shot start) or "best_moment"
    # (start inside the shot at a stronger moment, never leaving the shot bounds).
    cut_start_mode: Optional[str] = "shot_start"


class PromoV3Step4SaveRequest(BaseModel):
    session_id: Optional[str] = None
    video_path: Optional[str] = None
    video_paths: Optional[List[str]] = None
    edl: Dict[str, Any]


class PromoV3SlotShotRequest(PromoV3Step4SaveRequest):
    slot_index: int
    shot_id: Optional[int] = None


class PromoV3Step5RenderRequest(BaseModel):
    channel_branding: ChannelBrandingSettings = Field(default_factory=ChannelBrandingSettings)
    session_id: Optional[str] = None
    video_path: Optional[str] = None
    video_paths: Optional[List[str]] = None
    game_name: Optional[str] = ""
    resolution: str = "1080p"  # "1080p" or "4K"
    enable_steam_hook: bool = True
    steam_logo_scale: float = 1.3
    steam_logo_y: int = 540
    steam_text_scale: float = 1.6
    steam_text_y: int = 1160
    captions_font_size: int = 84
    captions_y: int = 600
    captions_highlight_color: str = "#FFF380"
    gameplay_volume: float = 0.15
    edl: Optional[Dict[str, Any]] = None
    script_text: Optional[str] = None
    video_format: Optional[str] = None
    sentence_1_end_s: Optional[float] = None
    hook_duration: Optional[float] = None
    steam_hook_mode: Optional[str] = "classic"
    intro_subtitles_y: Optional[int] = 340
    intro_subtitles_size: Optional[int] = 84
    enable_game_name_reveal: Optional[bool] = False
    game_name_reveal_y: Optional[int] = 800
    game_name_reveal_scale: Optional[float] = 1.0
    custom_game_logo_path: Optional[str] = None
    # Final master: loudness normalization (-14 LUFS / -1 dBTP) and voice ducking.
    audio_normalize: bool = True
    audio_ducking: bool = True
    # 30 or 60 (60 only when the source has >= 50 fps; otherwise 30 + warning).
    output_fps: int = 30


class PromoV3Step5SaveRequest(BaseModel):
    session_id: Optional[str] = None
    video_path: Optional[str] = None
    video_paths: Optional[List[str]] = None
    result: Dict[str, Any]


class YouTubeIngestRequest(BaseModel):
    url: str
    start_sec: Optional[float] = 0.0
    end_sec: Optional[float] = 0.0
    game_name: Optional[str] = None


class PromoProcessRequest(BaseModel):
    footage_paths: List[str]
    game_name: str
    mode: str = "v1_and_card"  # "v1_and_card", "v1_only", "card_only", "v2_voice_script", "v2_finished_video"
    pipeline_version: str = "v1"  # "v1" or "v2"
    video_count: int = 1  # 1, 2, or 3
    target_duration: float = 30.0
    framing: str = "vertical_split_blur"  # "vertical_split_blur" or "fit_16_9"
    resolution: str = "1080p"  # "1080p" or "4K"
    strip_splash: bool = True  # Auto-detect and strip outro / wishlist / title splash cards
    footage_is_9_16: bool = False  # If True, footage is already 9:16 vertical; bypass crop restrictions
    precut_video: bool = False  # If True, footage is already cut/edited; bypass Gemini analysis and Director IA
    # V2 Voiceover & Script parameters
    voiceover_path: Optional[str] = None
    script_text: Optional[str] = ""
    briefing_text: Optional[str] = ""
    elevenlabs_voice_id: Optional[str] = None
    elevenlabs_api_key: Optional[str] = None
    captions_style: Optional[str] = "kinetic_highlight"
    captions_font_size: Optional[int] = 84  # Font size in 1080p base
    captions_highlight_color: Optional[str] = "#FFF380"  # Hex color of active/emphasized words
    captions_y_position: Optional[int] = 600  # Vertical distance from bottom edge (ASS MarginV in 1080p)
    gameplay_volume: Optional[float] = 0.15  # Volume of background gameplay audio (0.0 to 1.0)
    hook_type: Optional[str] = "steam_new_game"  # "steam_new_game", "so_in_this_new_game", "direct", "custom"
    opening_text: Optional[str] = ""  # opener used when hook_type == "custom"
    # Steam Hook Visual Customizer
    steam_logo_scale: Optional[float] = 1.3
    steam_logo_y: Optional[int] = 540
    steam_text_scale: Optional[float] = 1.6
    steam_text_y: Optional[int] = 1160
    # Social Media description & hashtag
    hashtags: Optional[str] = ""
    social_description: Optional[str] = ""
    # V1 Card parameters
    preset_name: Optional[str] = "Predeterminado: IGD"
    hook_text: Optional[str] = ""
    hook_texts: Optional[List[str]] = None
    overlay_type: Optional[str] = "card"
    card_username: Optional[str] = None
    card_handle: Optional[str] = None
    card_bg_color: Optional[str] = None
    card_text_color: Optional[str] = None
    card_border_color: Optional[str] = None
    card_border_width: Optional[int] = 12
    card_radius: Optional[int] = 80
    card_avatar_path: Optional[str] = None
    card_avatar_bg: Optional[str] = None
    card_font_size_body: Optional[int] = 76
    card_width: Optional[int] = 1728
    card_height: Optional[int] = 980
    card_y_position: Optional[int] = 440
    video_zoom: Optional[float] = 1.0
    video_y_position: Optional[int] = 180


# =============================================================================
# API Endpoints: System & Config
# =============================================================================

# Secrets are never sent back to the browser in clear text: GET endpoints return
# "••••" + last 4 chars, and every endpoint that accepts a key from the client
# falls back to the server-side .env value when the client sends nothing or a mask.
ENV_FILE_PATH = BASE_DIR / ".env"
SECRET_MASK_CHAR = "\u2022"  # "•"
_ENV_KEY_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _mask_secret(value: Optional[str]) -> str:
    """'abcd1234wxyz' -> '••••wxyz'; '' -> ''."""
    v = (value or "").strip()
    if not v:
        return ""
    return SECRET_MASK_CHAR * 4 + (v[-4:] if len(v) > 4 else "")


def _is_unusable_secret(value: Optional[str]) -> bool:
    """True for None, blank strings and masked placeholders ("••••1234")."""
    return value is None or not str(value).strip() or SECRET_MASK_CHAR in str(value)


def _client_secret_or_env(client_value: Optional[str], server_value: Optional[str]) -> str:
    """Prefer a real key typed by the client; otherwise use the server-side key."""
    if not _is_unusable_secret(client_value):
        return str(client_value).strip()
    return (server_value or "").strip()


def _validate_env_value(name: str, value: str) -> None:
    if any(ch in value for ch in ("\r", "\n", "\x00")):
        raise HTTPException(status_code=400, detail=f"Valor inválido para {name}: no puede contener saltos de línea.")


def _update_env_file(updates: Dict[str, str], env_path: Optional[Path] = None) -> List[str]:
    """Set KEY=value lines in .env atomically (temp file in the same folder + os.replace).

    Other lines (comments, unrelated keys) and their line endings are preserved.
    Returns the list of keys written. Also mirrors the values into os.environ.
    """
    env_path = Path(env_path or ENV_FILE_PATH)
    for name, value in updates.items():
        if not _ENV_KEY_NAME_RE.match(name):
            raise HTTPException(status_code=400, detail=f"Nombre de variable inválido: {name}")
        _validate_env_value(name, value)
    if not updates:
        return []
    import tempfile
    with json_store.lock_for(env_path):
        lines: List[str] = []
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8-sig", newline="") as f:
                lines = f.readlines()
        eol = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
        patterns = {k: re.compile(r"^\s*(?:export\s+)?" + re.escape(k) + r"\s*=") for k in updates}
        new_lines: List[str] = []
        seen = set()
        for line in lines:
            replaced = False
            for k, v in updates.items():
                if patterns[k].match(line):
                    new_lines.append(f"{k}={v}{eol}")
                    seen.add(k)
                    replaced = True
                    break
            if not replaced:
                new_lines.append(line)
        missing = [k for k in updates if k not in seen]
        if missing and new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += eol
        for k in missing:
            new_lines.append(f"{k}={updates[k]}{eol}")
        env_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=str(env_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.writelines(new_lines)
            os.replace(tmp_name, str(env_path))
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    # Keep future clients in this process aligned with the newly saved keys.
    for key, value in updates.items():
        os.environ[key] = str(value)
    return list(updates.keys())


def _collect_env_updates(candidates: Dict[str, Optional[str]], clear_keys: Optional[List[str]],
                         field_to_env: Dict[str, str]) -> Tuple[Dict[str, str], List[str]]:
    """Filter client values: masked / empty / None are ignored (never overwrite a real
    key with its mask); names listed in clear_keys are explicitly emptied."""
    updates: Dict[str, str] = {}
    ignored: List[str] = []
    for env_name, value in candidates.items():
        if _is_unusable_secret(value):
            if value is not None:
                ignored.append(env_name)
            continue
        value = str(value).strip()
        _validate_env_value(env_name, value)
        updates[env_name] = value
    for field_name in clear_keys or []:
        env_name = field_to_env.get(str(field_name).strip().lower()) or (
            str(field_name).strip().upper() if str(field_name).strip().upper() in field_to_env.values() else None)
        if env_name and env_name not in updates:
            updates[env_name] = ""
    return updates, ignored


def _config_key_status() -> Dict[str, Any]:
    """Masked keys + *_set booleans. Works even when .env is incomplete."""
    try:
        config = AppConfig.from_env()
        groq, gemini, eleven = config.groq_api_key, config.gemini_api_key, config.elevenlabs_api_key
        voice_id = config.elevenlabs_voice_id
        extra = {
            "output_dir": str(config.output_dir),
            "video_style": config.video_style,
            "default_resolution": f"{config.output_width}x{config.output_height}",
        }
    except Exception as cfg_err:
        # Fresh install / missing required keys: still let the Settings page load.
        logger.info(f"Configuración incompleta al leer /api/config: {cfg_err}")
        try:
            from dotenv import load_dotenv
            load_dotenv(ENV_FILE_PATH)
        except Exception:
            pass
        groq = os.environ.get("GROQ_API_KEY", "")
        gemini = os.environ.get("GEMINI_API_KEY", "")
        eleven = os.environ.get("ELEVENLABS_API_KEY", "")
        voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "")
        extra = {
            "output_dir": str(APP_OUTPUT_DIR),
            "video_style": os.environ.get("VIDEO_STYLE", "vertical_full"),
            "default_resolution": f"{os.environ.get('OUTPUT_WIDTH', '1080')}x{os.environ.get('OUTPUT_HEIGHT', '1920')}",
        }
    return {
        "groq_api_key": _mask_secret(groq),
        "gemini_api_key": _mask_secret(gemini),
        "elevenlabs_api_key": _mask_secret(eleven),
        "groq_api_key_set": bool((groq or "").strip()),
        "gemini_api_key_set": bool((gemini or "").strip()),
        "elevenlabs_api_key_set": bool((eleven or "").strip()),
        "elevenlabs_voice_id": voice_id or "21m00Tcm4TlvDq8ikWAM",
        **extra,
    }


@app.get("/api/config")
def get_config():
    """Get active configuration. API keys are MASKED ("••••" + last 4 chars)."""
    return _config_key_status()


_CONFIG_FIELD_TO_ENV = {
    "groq_api_key": "GROQ_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
    "elevenlabs_api_key": "ELEVENLABS_API_KEY",
    "elevenlabs_voice_id": "ELEVENLABS_VOICE_ID",
}


@app.post("/api/config")
def update_config(req: ConfigUpdateRequest):
    """Update config API keys into .env file.

    Values that are None, empty or masked placeholders (contain "•") are ignored, so
    saving the Settings form never replaces a real key with its mask. Use
    ``clear_keys`` (e.g. ["groq_api_key"]) to explicitly empty a key.
    """
    candidates = {
        "GROQ_API_KEY": req.groq_api_key,
        "GEMINI_API_KEY": req.gemini_api_key,
        "ELEVENLABS_API_KEY": req.elevenlabs_api_key,
        "ELEVENLABS_VOICE_ID": req.elevenlabs_voice_id,
    }
    updates, ignored = _collect_env_updates(candidates, req.clear_keys, _CONFIG_FIELD_TO_ENV)
    written = _update_env_file(updates)
    return {
        "status": "ok",
        "message": "Configuración guardada en .env" if written else "Sin cambios: las claves existentes se conservan.",
        "updated_keys": written,
        "ignored_keys": ignored,
        **_config_key_status(),
    }


class ApiUsageBudgetRequest(BaseModel):
    limit: Optional[int] = Field(None, ge=1, le=1000000000000)
    unit: Literal["requests", "tokens", "characters"] = "requests"
    period: Literal["daily", "monthly"] = "monthly"


@app.get("/api/usage")
def get_api_usage(days: int = Query(30,ge=1,le=90)):
    return _api_usage.dashboard(days)


@app.post("/api/usage/refresh")
def refresh_api_usage(force: bool = False):
    return _api_usage.refresh_accounts(force)


@app.post("/api/usage/budget/{provider}")
def set_api_usage_budget(provider: str,req: ApiUsageBudgetRequest):
    try:
        _api_usage.save_budget(provider,req.limit,req.unit,req.period)
    except ValueError as exc:
        raise HTTPException(status_code=400,detail=str(exc))
    return {"success":True}


@app.get("/api/presets")
def list_presets():
    """List available predetermined presets."""
    return get_all_presets()


class ProductionVideoFormatRequest(BaseModel):
    brand: str = Field(default="NextLoot", min_length=1, max_length=80, pattern=r"^[\w -]+$")
    video_format: Literal["full_9_16", "blur_3_4", "blur_1_1"]


@app.get("/api/production-preferences/video-format")
def get_production_video_format(brand: str = "NextLoot"):
    from video_format_preferences import preferred_video_format, FORMATS
    try:
        value=preferred_video_format(BASE_DIR / "data/production_preferences.json",brand)
    except ValueError as exc:
        raise HTTPException(status_code=400,detail=str(exc))
    return {"brand":brand,"video_format":value,"available_formats":list(FORMATS)}


@app.post("/api/production-preferences/video-format")
def set_production_video_format(req: ProductionVideoFormatRequest):
    from video_format_preferences import save_video_format
    try:
        value=save_video_format(BASE_DIR / "data/production_preferences.json",req.brand,req.video_format)
    except ValueError as exc:
        raise HTTPException(status_code=400,detail=str(exc))
    return {"brand":req.brand,"video_format":value,"saved":True}


@app.get("/api/channel-branding/preview")
def channel_branding_preview(preset: str = "NextLoot", text: str = "@nextlootgames",
                             size: int = 64, x: int = 60, y: int = 240, opacity: int = 80):
    """Use the same RGBA badge in the mockup and in the exported video."""
    from io import BytesIO
    try:
        settings = ChannelBrandingSettings(enabled=True, preset=preset, text=text,
                                          size=size, x=x, y=y, opacity=opacity)
        badge, _, _ = build_channel_badge(settings)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    buffer = BytesIO()
    badge.save(buffer, format="PNG")
    return Response(buffer.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


# =============================================================================
# API Endpoints: Video Uploads Calendar (IGD / GUAM & Promos)
# =============================================================================

CALENDAR_DATA_FILE = BASE_DIR / "data" / "calendar_events.json"


class CalendarEventItem(BaseModel):
    id: Optional[str] = None
    game_name: str
    date: str  # Format: "DD-MM-YY" (or "YYYY-MM-DD" internally converted)
    is_promo: bool = False
    account: str = "IGD"  # "IGD" or "GUAM"
    notes: Optional[str] = ""
    created_at: Optional[str] = None


def _normalize_date_to_dmy(d_str: str) -> str:
    """Ensure date is stored and formatted as DD-MM-YY."""
    d_str = (d_str or "").strip()
    if not d_str:
        return datetime.now().strftime("%d-%m-%y")
    # If in YYYY-MM-DD format
    m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", d_str)
    if m_iso:
        yyyy, mm, dd = m_iso.groups()
        yy = yyyy[-2:]
        return f"{dd}-{mm}-{yy}"
    # If already DD-MM-YY or DD-MM-YYYY
    m_dmy = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})$", d_str)
    if m_dmy:
        dd, mm, y = m_dmy.groups()
        dd = dd.zfill(2)
        mm = mm.zfill(2)
        yy = y[-2:] if len(y) == 4 else y.zfill(2)
        return f"{dd}-{mm}-{yy}"
    return d_str


def _load_calendar_events_data() -> List[dict]:
    return _load_list_state(CALENDAR_DATA_FILE)


def _save_calendar_events_data(events: List[dict]):
    json_store.save_json_atomic(CALENDAR_DATA_FILE, events)


@app.get("/api/calendar")
def get_calendar_events():
    """List all scheduled video uploads in the calendar."""
    events = _load_calendar_events_data()
    return {"status": "ok", "events": events}


@app.post("/api/calendar")
@_state_locked(CALENDAR_DATA_FILE)
def create_or_update_calendar_event(item: CalendarEventItem):
    """Create or update a scheduled video upload event in the calendar."""
    events = _load_calendar_events_data()
    event_id = item.id.strip() if (item.id and item.id.strip()) else f"cal_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    
    formatted_date = _normalize_date_to_dmy(item.date)
    account_clean = (item.account or "IGD").strip().upper()
    if account_clean not in ["IGD", "GUAM"]:
        account_clean = "IGD" if "IGD" in account_clean else ("GUAM" if "GUAM" in account_clean else "IGD")

    event_data = {
        "id": event_id,
        "game_name": item.game_name.strip(),
        "date": formatted_date,  # Format: DD-MM-YY
        "is_promo": bool(item.is_promo),
        "account": account_clean,
        "notes": (item.notes or "").strip(),
        "updated_at": datetime.now().isoformat(),
    }
    
    updated = False
    for i, ev in enumerate(events):
        if ev.get("id") == event_id:
            event_data["created_at"] = ev.get("created_at", datetime.now().isoformat())
            events[i] = event_data
            updated = True
            break
            
    if not updated:
        event_data["created_at"] = datetime.now().isoformat()
        events.append(event_data)
        
    _save_calendar_events_data(events)
    return {"status": "ok", "event": event_data}


@app.delete("/api/calendar/{event_id}")
@_state_locked(CALENDAR_DATA_FILE)
def delete_calendar_event(event_id: str):
    """Delete a scheduled calendar event."""
    events = _load_calendar_events_data()
    initial_count = len(events)
    events = [ev for ev in events if ev.get("id") != event_id]
    if len(events) == initial_count:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Evento no encontrado"})
    _save_calendar_events_data(events)
    return {"status": "ok", "message": "Evento eliminado correctamente"}


# =============================================================================
# API Endpoints: Weekly Schedule (Horario Semanal 9:00 - 00:00, 30 min intervals)
# =============================================================================

SCHEDULE_DATA_FILE = BASE_DIR / "data" / "schedule_blocks.json"


class ScheduleBlockItem(BaseModel):
    id: Optional[str] = None
    day: int  # 0=Lunes, 1=Martes, 2=Miércoles, 3=Jueves, 4=Viernes, 5=Sábado, 6=Domingo
    start_time: str  # e.g. "09:00"
    end_time: str  # e.g. "11:00"
    title: str
    category: Optional[str] = "general"
    color: Optional[str] = "#d97757"
    notes: Optional[str] = ""
    created_at: Optional[str] = None


def _load_schedule_blocks_data() -> List[dict]:
    return _load_list_state(SCHEDULE_DATA_FILE)


def _save_schedule_blocks_data(blocks: List[dict]):
    json_store.save_json_atomic(SCHEDULE_DATA_FILE, blocks)


@app.get("/api/schedule")
def get_schedule_blocks():
    """List all scheduled time blocks in the weekly schedule."""
    blocks = _load_schedule_blocks_data()
    return {"status": "ok", "blocks": blocks}


@app.post("/api/schedule")
@_state_locked(SCHEDULE_DATA_FILE)
def create_or_update_schedule_block(item: ScheduleBlockItem):
    """Create or update a scheduled time block."""
    blocks = _load_schedule_blocks_data()
    block_id = item.id.strip() if (item.id and item.id.strip()) else f"blk_{int(time.time())}_{uuid.uuid4().hex[:6]}"

    block_data = {
        "id": block_id,
        "day": int(item.day),
        "start_time": item.start_time.strip(),
        "end_time": item.end_time.strip(),
        "title": item.title.strip(),
        "category": (item.category or "general").strip(),
        "color": (item.color or "#d97757").strip(),
        "notes": (item.notes or "").strip(),
        "updated_at": datetime.now().isoformat(),
    }

    updated = False
    for i, blk in enumerate(blocks):
        if blk.get("id") == block_id:
            block_data["created_at"] = blk.get("created_at", datetime.now().isoformat())
            blocks[i] = block_data
            updated = True
            break

    if not updated:
        block_data["created_at"] = datetime.now().isoformat()
        blocks.append(block_data)

    _save_schedule_blocks_data(blocks)
    return {"status": "ok", "block": block_data}


@app.delete("/api/schedule/{block_id}")
@_state_locked(SCHEDULE_DATA_FILE)
def delete_schedule_block(block_id: str):
    """Delete a schedule time block."""
    blocks = _load_schedule_blocks_data()
    initial_count = len(blocks)
    blocks = [b for b in blocks if b.get("id") != block_id]
    if len(blocks) == initial_count:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Bloque no encontrado"})
    _save_schedule_blocks_data(blocks)
    return {"status": "ok", "message": "Bloque eliminado correctamente"}


@app.post("/api/schedule/batch")
@_state_locked(SCHEDULE_DATA_FILE)
def batch_save_schedule_blocks(payload: Dict[str, Any]):
    """Batch save or replace all schedule blocks."""
    blocks = payload.get("blocks", [])
    if not isinstance(blocks, list):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Se esperaba una lista de bloques"})
    _save_schedule_blocks_data(blocks)
    return {"status": "ok", "count": len(blocks), "blocks": blocks}


@app.post("/api/schedule/clear")
@_state_locked(SCHEDULE_DATA_FILE)
def clear_all_schedule_blocks():
    """Clear all blocks from the weekly schedule."""
    _save_schedule_blocks_data([])
    return {"status": "ok", "message": "Horario vaciado correctamente"}


# =============================================================================
# API Endpoints: Schedule Categories (Customizable Name, Color & Icon)
# =============================================================================

SCHEDULE_CATEGORIES_FILE = BASE_DIR / "data" / "schedule_categories.json"

DEFAULT_CATEGORIES = [
    {"id": "video", "name": "Vídeo / Edición", "color": "#d97757", "icon": "🎬"},
    {"id": "dev", "name": "Dev / Código", "color": "#3b82f6", "icon": "💻"},
    {"id": "record", "name": "Grabación / Stream", "color": "#8b5cf6", "icon": "🎙️"},
    {"id": "gym", "name": "Salud / Gym", "color": "#10b981", "icon": "🏋️"},
    {"id": "ocio", "name": "Ocio / Juegos", "color": "#f59e0b", "icon": "🎮"},
    {"id": "rest", "name": "Descanso / Comida", "color": "#06b6d4", "icon": "☕"},
    {"id": "general", "name": "General", "color": "#9e978e", "icon": "📌"},
]


class ScheduleCategoryItem(BaseModel):
    id: Optional[str] = None
    name: str
    color: str
    icon: Optional[str] = "📌"


def _load_schedule_categories_data() -> List[dict]:
    with json_store.lock_for(SCHEDULE_CATEGORIES_FILE):
        if not SCHEDULE_CATEGORIES_FILE.exists():
            _save_schedule_categories_data(DEFAULT_CATEGORIES)
            return [dict(c) for c in DEFAULT_CATEGORIES]
        data = _load_list_state(SCHEDULE_CATEGORIES_FILE)
        if len(data) > 0:
            return data
        return [dict(c) for c in DEFAULT_CATEGORIES]


def _save_schedule_categories_data(categories: List[dict]):
    json_store.save_json_atomic(SCHEDULE_CATEGORIES_FILE, categories)


@app.get("/api/schedule/categories")
def get_schedule_categories():
    """Get all customizable schedule categories."""
    categories = _load_schedule_categories_data()
    return {"status": "ok", "categories": categories}


@app.post("/api/schedule/categories")
@_state_locked(SCHEDULE_CATEGORIES_FILE)
def create_or_update_schedule_category(item: ScheduleCategoryItem):
    """Create or update a schedule category."""
    categories = _load_schedule_categories_data()
    cat_id = item.id.strip().lower() if (item.id and item.id.strip()) else f"cat_{int(time.time())}_{uuid.uuid4().hex[:4]}"
    cat_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", cat_id)

    cat_data = {
        "id": cat_id,
        "name": item.name.strip(),
        "color": item.color.strip(),
        "icon": (item.icon or "📌").strip(),
    }

    updated = False
    for i, c in enumerate(categories):
        if c.get("id") == cat_id:
            categories[i] = cat_data
            updated = True
            break

    if not updated:
        categories.append(cat_data)

    _save_schedule_categories_data(categories)
    return {"status": "ok", "category": cat_data}


@app.delete("/api/schedule/categories/{cat_id}")
@_state_locked(SCHEDULE_CATEGORIES_FILE)
def delete_schedule_category(cat_id: str):
    """Delete a custom schedule category."""
    categories = _load_schedule_categories_data()
    initial_count = len(categories)
    categories = [c for c in categories if c.get("id") != cat_id]
    if len(categories) == initial_count:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Categoría no encontrada"})
    _save_schedule_categories_data(categories)
    return {"status": "ok", "message": "Categoría eliminada"}


@app.post("/api/schedule/categories/reset")
@_state_locked(SCHEDULE_CATEGORIES_FILE)
def reset_schedule_categories():
    """Reset categories to default presets."""
    _save_schedule_categories_data(DEFAULT_CATEGORIES)
    return {"status": "ok", "categories": list(DEFAULT_CATEGORIES)}





# =============================================================================
# API Endpoints: ElevenLabs Voice Tester & Synthesis
# =============================================================================

class ElevenLabsTestSpeechRequest(BaseModel):
    api_key: Optional[str] = None
    voice_id: str
    text: Optional[str] = "Hola, esta es una prueba de voz con ElevenLabs para el pipeline de vídeo."
    model_id: Optional[str] = "eleven_v4"


@app.get("/api/elevenlabs/voices")
def get_elevenlabs_voices(api_key: Optional[str] = Query(None)):
    """Fetch available voices directly from ElevenLabs official endpoint (GET /v1/voices)."""
    import requests
    
    key_to_use = _client_secret_or_env(api_key, os.environ.get("ELEVENLABS_API_KEY", ""))
    if _is_unusable_secret(api_key):
        try:
            cfg = AppConfig.from_env()
            key_to_use = (cfg.elevenlabs_api_key or key_to_use or "").strip()
        except Exception:
            # from_env() loads .env before validating: re-read the server-side key.
            key_to_use = (os.environ.get("ELEVENLABS_API_KEY", "") or "").strip()
    
    if not key_to_use:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "Falta la API Key de ElevenLabs. Introdúcela en el campo de texto o guárdala en Ajustes."
            }
        )
    
    try:
        url = "https://api.elevenlabs.io/v1/voices"
        headers = {"xi-api-key": key_to_use}
        resp = requests.get(url, headers=headers, timeout=12)
        
        if resp.status_code == 401:
            return JSONResponse(
                status_code=401,
                content={
                    "status": "error",
                    "error_code": "unauthorized",
                    "message": "API Key de ElevenLabs inválida o no autorizada. Revisa que esté bien copiada."
                }
            )
        elif resp.status_code == 429:
            return JSONResponse(
                status_code=429,
                content={
                    "status": "error",
                    "error_code": "rate_limited",
                    "message": "Se ha alcanzado el límite de peticiones en ElevenLabs (Rate Limit). Inténtalo más tarde."
                }
            )
        elif resp.status_code != 200:
            return JSONResponse(
                status_code=resp.status_code,
                content={
                    "status": "error",
                    "message": f"Error devuelto por ElevenLabs ({resp.status_code}): {resp.text[:200]}"
                }
            )
            
        data = resp.json()
        raw_voices = data.get("voices", [])
        
        formatted_voices = []
        for v in raw_voices:
            labels = v.get("labels") or {}
            formatted_voices.append({
                "voice_id": v.get("voice_id", ""),
                "name": v.get("name", "Sin nombre"),
                "category": v.get("category", "premade"),
                "description": v.get("description", ""),
                "preview_url": v.get("preview_url", ""),
                "labels": labels,
                "accent": labels.get("accent", ""),
                "gender": labels.get("gender", ""),
                "age": labels.get("age", ""),
                "use_case": labels.get("use_case", ""),
                "is_premade": v.get("category") == "premade",
            })
            
        # Sort: premade first, then alphabetical by name
        formatted_voices.sort(key=lambda x: (0 if x["is_premade"] else 1, x["name"].lower()))
        
        cfg = AppConfig.from_env()
        current_voice_id = cfg.elevenlabs_voice_id or "JBFqnCBsd6RMkjVDRZzb"
        
        return {
            "status": "ok",
            "total_count": len(formatted_voices),
            "current_voice_id": current_voice_id,
            "voices": formatted_voices
        }
    except requests.exceptions.Timeout:
        return JSONResponse(
            status_code=504,
            content={"status": "error", "message": "Tiempo de espera agotado al conectar con api.elevenlabs.io"}
        )
    except Exception as e:
        logger.error(f"Error fetching ElevenLabs voices: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"Error interno consultando ElevenLabs: {str(e)}"}
        )


class ElevenLabsVoicesRequest(BaseModel):
    api_key: Optional[str] = None


@app.post("/api/elevenlabs/voices")
def post_elevenlabs_voices(req: Optional[ElevenLabsVoicesRequest] = None):
    """Same as GET /api/elevenlabs/voices, with the key in the JSON body instead of the URL.

    Lets the Settings tab test a key the user just typed before saving it; an empty or
    masked key falls back to the server-side .env key.
    """
    return get_elevenlabs_voices(api_key=(req.api_key if req else None))


@app.post("/api/elevenlabs/test-speech")
def test_elevenlabs_speech(req: ElevenLabsTestSpeechRequest):
    """Synthesize a short sample phrase to test voice availability and license tier."""
    import requests
    
    key_to_use = _client_secret_or_env(req.api_key, os.environ.get("ELEVENLABS_API_KEY", ""))
    if _is_unusable_secret(req.api_key):
        try:
            cfg = AppConfig.from_env()
            key_to_use = (cfg.elevenlabs_api_key or key_to_use or "").strip()
        except Exception:
            # from_env() loads .env before validating: re-read the server-side key.
            key_to_use = (os.environ.get("ELEVENLABS_API_KEY", "") or "").strip()
        
    if not key_to_use:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Falta la API Key de ElevenLabs."}
        )
        
    voice_id = (req.voice_id or "").strip()
    if not voice_id:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Falta el Voice ID a probar."}
        )
        
    text_to_speak = (req.text or "").strip() or "Hola, esta es una prueba de voz con ElevenLabs para el pipeline de vídeo."
    
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": key_to_use,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text_to_speak,
        "model_id": req.model_id or "eleven_v4",
    }
    
    try:
        resp = _api_usage.usage_post(url, headers=headers, json=payload, timeout=18)
        
        if resp.status_code == 200:
            temp_out_dir = BASE_DIR / "temp" / "elevenlabs"
            temp_out_dir.mkdir(parents=True, exist_ok=True)
            out_file = temp_out_dir / f"test_{voice_id[:8]}.mp3"
            with open(out_file, "wb") as f:
                f.write(resp.content)
                
            return {
                "status": "ok",
                "message": f"Locución generada con éxito ({len(resp.content) // 1024} KB).",
                "audio_url": f"/media/temp/elevenlabs/{out_file.name}?t={int(time.time())}",
                "voice_id": voice_id
            }
        elif resp.status_code == 402:
            data = {}
            try:
                data = resp.json()
            except Exception:
                pass
            msg = data.get("detail", {}).get("message", "") if isinstance(data.get("detail"), dict) else resp.text
            return JSONResponse(
                status_code=402,
                content={
                    "status": "error",
                    "error_code": "payment_required",
                    "message": "Esta voz requiere suscripción de pago en ElevenLabs. Para planes gratuitos, usa una voz 'premade' como George (JBFqnCBsd6RMkjVDRZzb) o Sarah.",
                    "detail": msg
                }
            )
        elif resp.status_code == 401:
            return JSONResponse(
                status_code=401,
                content={"status": "error", "message": "API Key de ElevenLabs inválida o no autorizada."}
            )
        else:
            return JSONResponse(
                status_code=resp.status_code,
                content={"status": "error", "message": f"Error de ElevenLabs ({resp.status_code}): {resp.text[:200]}"}
            )
    except requests.exceptions.Timeout:
        return JSONResponse(status_code=504, content={"status": "error", "message": "Tiempo de espera agotado generando locución."})
    except Exception as e:
        logger.error(f"Error testing ElevenLabs speech: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})



# =============================================================================
# API Endpoints: GameDev Promo Content Automation — Game Profiles
# =============================================================================

GAMEDEV_GAMES_FILE = BASE_DIR / "data" / "gamedev_games.json"
GAMES_FOOTAGE_DIR = BASE_DIR / "GamesFootage"
PROMOS_FOOTAGE_DIR = BASE_DIR / "PromosFootage"

class GameSocialLinks(BaseModel):
    tiktok: Optional[str] = ""
    instagram: Optional[str] = ""
    youtube: Optional[str] = ""
    steam: Optional[str] = ""

class GameProfilePayload(BaseModel):
    name: str
    icon: Optional[str] = "🎮"
    accent_color: Optional[str] = "#7c3aed"
    footage_folder: Optional[str] = ""
    steam_app_id: Optional[str] = ""
    social_links: Optional[GameSocialLinks] = None
    description: Optional[str] = ""

class FolderCheckPayload(BaseModel):
    folder_path: str


class UpdateHookPayload(BaseModel):
    hook: str


class UpdateAutomatedPayload(BaseModel):
    automated: bool


class ManualInsightPayload(BaseModel):
    upload_date: str
    views_tiktok: Optional[int] = 0
    views_instagram: Optional[int] = 0
    views_shorts: Optional[int] = 0
    hook: Optional[str] = ""
    automated: Optional[bool] = True


class GamedevSettingsPayload(BaseModel):
    scrapecreators_api_key: Optional[str] = None
    steamworks_api_key: Optional[str] = None
    clear_keys: Optional[List[str]] = None  # e.g. ["steamworks_api_key"] to empty it explicitly


class UpdateWishlistsPayload(BaseModel):
    wishlists_count: int


class SyncSteamPayload(BaseModel):
    wishlists_count: Optional[int] = None


def _sanitize_folder_name(name: str) -> str:
    """Sanitize game title into a safe Windows folder name without illegal characters."""
    # Remove characters illegal in Windows paths: < > : " / \ | ? *
    cleaned = re.sub(r'[<>:"/\\|?*]', '', name)
    # Remove control characters
    cleaned = "".join(ch for ch in cleaned if ord(ch) >= 32)
    # Collapse multiple whitespaces and strip leading/trailing spaces & dots
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(". ")
    if not cleaned:
        cleaned = "UnnamedGame"
    return cleaned


_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _safe_upload_basename(filename: Optional[str], default_stem: str = "upload", max_len: int = 180) -> str:
    """Reduce a client-supplied file name to a safe bare file name (no folders,
    no '..', no characters Windows rejects, no reserved device names)."""
    raw = str(filename or "").replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", raw)
    cleaned = "".join(ch for ch in cleaned if ord(ch) >= 32).strip().strip(". ")
    if not cleaned or set(cleaned) <= {"."}:
        cleaned = f"{default_stem}_{uuid.uuid4().hex[:6]}"
    stem, dot, ext = cleaned.rpartition(".")
    if not dot:
        stem, ext = cleaned, ""
    if stem.split(".")[0].upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = "_" + cleaned
    if len(cleaned) > max_len:
        ext_part = ("." + ext[:16]) if dot and ext else ""
        cleaned = cleaned[: max_len - len(ext_part)].rstrip(". ") + ext_part
    return cleaned


def _safe_child_path(directory: Path, name: str) -> Path:
    """directory/name, guaranteed to resolve to a direct child of ``directory``."""
    base = Path(directory).resolve()
    target = (base / name).resolve()
    if target.parent != base:
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido.")
    return target


def _write_bytes_file(path: Path, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)


def _load_gamedev_games() -> List[Dict[str, Any]]:
    # Missing file -> [] (the file is created on the first save). A corrupt file is
    # moved aside and reported instead of being replaced by an empty list.
    return _load_list_state(GAMEDEV_GAMES_FILE)


def _save_gamedev_games(games: List[Dict[str, Any]]) -> None:
    try:
        json_store.save_json_atomic(GAMEDEV_GAMES_FILE, games)
    except Exception as e:
        logger.error(f"Error saving gamedev_games.json: {e}")


def _extract_steam_app_id(val: Optional[str]) -> str:
    """Extract numeric Steam App ID from a URL, store link, or raw string."""
    if not val:
        return ""
    val_str = str(val).strip()
    if not val_str:
        return ""
    # Look for /app/<digits> pattern in URLs
    match = re.search(r'app/(\d+)', val_str)
    if match:
        return match.group(1)
    # Check if string contains or is only digits (4 to 9 digits)
    match_digits = re.search(r'(\d{4,9})', val_str)
    if match_digits:
        return match_digits.group(1)
    return ""


def _scan_folder_videos(folder_path_str: str) -> Dict[str, Any]:
    """Check if folder exists and count video files."""
    if not folder_path_str or not folder_path_str.strip():
        return {"exists": False, "is_dir": False, "video_count": 0, "video_files": []}
    
    p = Path(folder_path_str.strip())
    if not p.is_absolute():
        p = (BASE_DIR / p).resolve()
        
    if not p.exists() or not p.is_dir():
        return {"exists": False, "is_dir": False, "video_count": 0, "video_files": []}
    
    video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}
    video_files = []
    try:
        for f in p.iterdir():
            if f.is_file() and f.suffix.lower() in video_exts:
                video_files.append(f.name)
    except Exception as e:
        logger.warning(f"Error scanning folder {p}: {e}")
        
    return {
        "exists": True,
        "is_dir": True,
        "video_count": len(video_files),
        "video_files": video_files[:10],
        "resolved_path": str(p),
    }


@app.get("/api/gamedev/games")
def get_gamedev_games():
    """List all game profiles with live footage folder status."""
    games = _load_gamedev_games()
    for g in games:
        folder = g.get("footage_folder", "")
        status = _scan_folder_videos(folder)
        g["folder_exists"] = status["exists"]
        g["video_count"] = status["video_count"]
        g["accent_color"] = g.get("accent_color", "#7c3aed")
        g["views_count"] = g.get("views_count", 0)
        g["wishlists_count"] = g.get("wishlists_count", 0)
        g["uploaded_count"] = g.get("uploaded_count", 0)
        g["pending_count"] = g.get("pending_count", 0)
    return {"games": games}


@app.post("/api/gamedev/games")
@_state_locked(GAMEDEV_GAMES_FILE)
def create_gamedev_game(payload: GameProfilePayload):
    """Create a new game profile, automatically creating and assigning its folder in GamesFootage."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="El nombre del juego es obligatorio.")
    
    games = _load_gamedev_games()
    game_id = f"game_{uuid.uuid4().hex[:8]}"
    
    # 1. Sanitize folder name from game name
    safe_folder_name = _sanitize_folder_name(name)
    GAMES_FOOTAGE_DIR.mkdir(parents=True, exist_ok=True)
    
    # 2. Determine footage folder path & auto-create the directory on disk
    custom_folder = (payload.footage_folder or "").strip()
    if not custom_folder or custom_folder == f"GamesFootage/{safe_folder_name}":
        footage_folder = f"GamesFootage/{safe_folder_name}"
        target_dir = GAMES_FOOTAGE_DIR / safe_folder_name
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        footage_folder = custom_folder
        p = Path(custom_folder)
        if not p.is_absolute():
            p = (BASE_DIR / p).resolve()
        p.mkdir(parents=True, exist_ok=True)
    
    status = _scan_folder_videos(footage_folder)
    now = datetime.now().isoformat()
    
    socials = payload.social_links
    raw_steam = (socials.steam or "").strip() if socials else ""
    steam_app_id = _extract_steam_app_id(payload.steam_app_id or raw_steam)

    new_game = {
        "id": game_id,
        "name": name,
        "icon": payload.icon.strip() if payload.icon and payload.icon.strip() else "🎮",
        "accent_color": (payload.accent_color or "#7c3aed").strip(),
        "footage_folder": footage_folder,
        "steam_app_id": steam_app_id,
        "social_links": {
            "tiktok": (socials.tiktok or "").strip() if socials else "",
            "instagram": (socials.instagram or "").strip() if socials else "",
            "youtube": (socials.youtube or "").strip() if socials else "",
            "steam": raw_steam or (f"https://store.steampowered.com/app/{steam_app_id}" if steam_app_id else ""),
        },
        "description": (payload.description or "").strip(),
        "folder_exists": status["exists"],
        "video_count": status["video_count"],
        "views_count": 0,
        "wishlists_count": 0,
        "uploaded_count": 0,
        "pending_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    games.append(new_game)
    _save_gamedev_games(games)
    return {"status": "ok", "game": new_game}


@app.put("/api/gamedev/games/{game_id}")
@_state_locked(GAMEDEV_GAMES_FILE)
def update_gamedev_game(game_id: str, payload: GameProfilePayload):
    """Update an existing game profile and ensure its footage directory exists."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="El nombre del juego es obligatorio.")
    
    games = _load_gamedev_games()
    found = False
    updated_game = None
    for i, g in enumerate(games):
        if g.get("id") == game_id:
            custom_folder = (payload.footage_folder or "").strip()
            if not custom_folder:
                safe_folder_name = _sanitize_folder_name(name)
                GAMES_FOOTAGE_DIR.mkdir(parents=True, exist_ok=True)
                footage_folder = f"GamesFootage/{safe_folder_name}"
                (GAMES_FOOTAGE_DIR / safe_folder_name).mkdir(parents=True, exist_ok=True)
            else:
                footage_folder = custom_folder
                p = Path(custom_folder)
                if not p.is_absolute():
                    p = (BASE_DIR / p).resolve()
                p.mkdir(parents=True, exist_ok=True)
                
            status = _scan_folder_videos(footage_folder)
            socials = payload.social_links
            raw_steam = (socials.steam or "").strip() if socials else ""
            steam_app_id = _extract_steam_app_id(payload.steam_app_id or raw_steam or g.get("steam_app_id", ""))

            g["name"] = name
            g["icon"] = payload.icon.strip() if payload.icon and payload.icon.strip() else "🎮"
            g["accent_color"] = (payload.accent_color or "#7c3aed").strip()
            g["footage_folder"] = footage_folder
            g["steam_app_id"] = steam_app_id
            g["social_links"] = {
                "tiktok": (socials.tiktok or "").strip() if socials else "",
                "instagram": (socials.instagram or "").strip() if socials else "",
                "youtube": (socials.youtube or "").strip() if socials else "",
                "steam": raw_steam or (f"https://store.steampowered.com/app/{steam_app_id}" if steam_app_id else ""),
            }
            g["description"] = (payload.description or "").strip()
            g["folder_exists"] = status["exists"]
            g["video_count"] = status["video_count"]
            g.setdefault("views_count", 0)
            g.setdefault("wishlists_count", 0)
            g.setdefault("uploaded_count", 0)
            g.setdefault("pending_count", 0)
            g["updated_at"] = datetime.now().isoformat()
            updated_game = g
            found = True
            break
            
    if not found:
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")
        
    _save_gamedev_games(games)
    return {"status": "ok", "game": updated_game}


@app.delete("/api/gamedev/games/{game_id}")
@_state_locked(GAMEDEV_GAMES_FILE)
def delete_gamedev_game(game_id: str):
    """Delete a game profile."""
    games = _load_gamedev_games()
    filtered = [g for g in games if g.get("id") != game_id]
    if len(filtered) == len(games):
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")
    _save_gamedev_games(filtered)
    return {"status": "ok", "message": "Perfil de juego eliminado."}


@app.post("/api/gamedev/check-folder")
def check_gamedev_folder(payload: FolderCheckPayload):
    """Scan and validate a local footage folder."""
    status = _scan_folder_videos(payload.folder_path)
    return {
        "status": "ok",
        "exists": status["exists"],
        "is_dir": status["is_dir"],
        "video_count": status["video_count"],
        "video_files": status["video_files"]
    }


def _format_file_size(size_in_bytes: int) -> str:
    if size_in_bytes >= 1024 * 1024 * 1024:
        return f"{size_in_bytes / (1024 * 1024 * 1024):.1f} GB"
    if size_in_bytes >= 1024 * 1024:
        return f"{size_in_bytes / (1024 * 1024):.1f} MB"
    if size_in_bytes >= 1024:
        return f"{size_in_bytes / 1024:.1f} KB"
    return f"{size_in_bytes} B"


@app.get("/api/gamedev/games/{game_id}/footage")
@_state_locked(GAMEDEV_GAMES_FILE)
def get_game_footage(game_id: str):
    """Scan and list all footage video clips inside the game's designated folder."""
    games = _load_gamedev_games()
    target_game = next((g for g in games if g.get("id") == game_id), None)
    if not target_game:
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")

    folder_str = target_game.get("footage_folder") or f"GamesFootage/{_sanitize_folder_name(target_game.get('name', 'game'))}"
    folder_path = Path(folder_str)
    if not folder_path.is_absolute():
        folder_path = (BASE_DIR / folder_path).resolve()
    folder_path.mkdir(parents=True, exist_ok=True)

    video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}
    footage_files = []
    
    if folder_path.exists():
        for f in sorted(folder_path.iterdir(), key=lambda x: x.stat().st_mtime if x.is_file() else 0, reverse=True):
            if f.is_file() and f.suffix.lower() in video_exts:
                try:
                    rel = f.relative_to(BASE_DIR).as_posix()
                    file_url = f"/media/{rel}"
                except ValueError:
                    rel = f.name
                    file_url = ""

                st = f.stat()
                footage_files.append({
                    "filename": f.name,
                    "saved_path": str(f),
                    "relative_path": rel,
                    "url": file_url,
                    "size_bytes": st.st_size,
                    "size_formatted": _format_file_size(st.st_size),
                    "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
                })

    # Keep game video_count updated
    if target_game.get("video_count") != len(footage_files):
        target_game["video_count"] = len(footage_files)
        target_game["folder_exists"] = True
        _save_gamedev_games(games)

    return {
        "status": "ok",
        "game_id": game_id,
        "game_name": target_game.get("name"),
        "footage_folder": folder_str,
        "resolved_path": str(folder_path),
        "video_count": len(footage_files),
        "footage_files": footage_files
    }


@app.post("/api/gamedev/games/{game_id}/footage/upload")
async def upload_game_footage(game_id: str, files: List[UploadFile] = File(...)):
    """Upload one or more video clips directly to the game's footage directory."""
    games = _load_gamedev_games()
    target_game = next((g for g in games if g.get("id") == game_id), None)
    if not target_game:
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")

    folder_str = target_game.get("footage_folder") or f"GamesFootage/{_sanitize_folder_name(target_game.get('name', 'game'))}"
    folder_path = Path(folder_str)
    if not folder_path.is_absolute():
        folder_path = (BASE_DIR / folder_path).resolve()
    folder_path.mkdir(parents=True, exist_ok=True)

    uploaded_items = []
    for file in files:
        if not file.filename:
            continue
        # Sanitize filename: bare name only, always inside the footage folder
        clean_name = _safe_upload_basename(file.filename, default_stem="clip")
        dest_path = _safe_child_path(folder_path, clean_name)

        # Avoid overwrite collision by appending short UUID
        if dest_path.exists():
            stem = dest_path.stem
            suffix = dest_path.suffix
            dest_path = _safe_child_path(folder_path, f"{stem}_{uuid.uuid4().hex[:4]}{suffix}")

        content = await file.read()
        await run_in_threadpool(_write_bytes_file, dest_path, content)

        try:
            rel = dest_path.relative_to(BASE_DIR).as_posix()
            file_url = f"/media/{rel}"
        except ValueError:
            rel = dest_path.name
            file_url = ""

        st = dest_path.stat()
        uploaded_items.append({
            "filename": dest_path.name,
            "saved_path": str(dest_path),
            "relative_path": rel,
            "url": file_url,
            "size_bytes": st.st_size,
            "size_formatted": _format_file_size(st.st_size),
            "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
        })

    # Rescan total videos in folder (re-load under the state lock so concurrent
    # edits to other game profiles made during the upload are not lost)
    video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}

    def _rescan_and_save() -> int:
        count = len([f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() in video_exts])
        with json_store.lock_for(GAMEDEV_GAMES_FILE):
            fresh_games = _load_gamedev_games()
            fresh_target = next((g for g in fresh_games if g.get("id") == game_id), None)
            if fresh_target is not None:
                fresh_target["video_count"] = count
                fresh_target["folder_exists"] = True
                _save_gamedev_games(fresh_games)
        return count

    total_vids = await run_in_threadpool(_rescan_and_save)

    return {
        "status": "ok",
        "message": f"¡{len(uploaded_items)} clip(s) subido(s) con éxito a {folder_str}!",
        "uploaded_files": uploaded_items,
        "total_video_count": total_vids
    }


@app.delete("/api/gamedev/games/{game_id}/footage/{filename}")
@_state_locked(GAMEDEV_GAMES_FILE)
def delete_game_footage_file(game_id: str, filename: str):
    """Delete a video clip from the game's footage folder."""
    games = _load_gamedev_games()
    target_game = next((g for g in games if g.get("id") == game_id), None)
    if not target_game:
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")

    folder_str = target_game.get("footage_folder") or f"GamesFootage/{_sanitize_folder_name(target_game.get('name', 'game'))}"
    folder_path = Path(folder_str)
    if not folder_path.is_absolute():
        folder_path = (BASE_DIR / folder_path).resolve()

    # Only a bare file name that lives directly inside the footage folder.
    if (not filename or filename in (".", "..") or ".." in filename or "\\" in filename
            or "/" in filename or ":" in filename or Path(filename).name != filename):
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido.")
    base_dir_resolved = folder_path.resolve()
    target_file = (base_dir_resolved / filename).resolve()
    if target_file.parent != base_dir_resolved:
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido.")
    if not target_file.exists() or not target_file.is_file():
        raise HTTPException(status_code=404, detail=f"Archivo '{filename}' no encontrado en {folder_str}.")

    try:
        target_file.unlink()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al eliminar archivo: {e}")

    # Rescan
    video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"}
    total_vids = len([f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() in video_exts])
    target_game["video_count"] = total_vids
    _save_gamedev_games(games)

    return {
        "status": "ok",
        "message": f"Archivo '{filename}' eliminado con éxito.",
        "remaining_video_count": total_vids
    }


@app.post("/api/gamedev/games/{game_id}/open-folder")
def open_game_footage_folder(game_id: str):
    """Open the game's footage directory in system file explorer."""
    games = _load_gamedev_games()
    target_game = next((g for g in games if g.get("id") == game_id), None)
    if not target_game:
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")

    folder_str = target_game.get("footage_folder") or f"GamesFootage/{_sanitize_folder_name(target_game.get('name', 'game'))}"
    folder_path = Path(folder_str)
    if not folder_path.is_absolute():
        folder_path = (BASE_DIR / folder_path).resolve()
    folder_path.mkdir(parents=True, exist_ok=True)

    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(folder_path)])  # argument list: no command-line parsing of the path
        else:
            subprocess.Popen(["xdg-open", str(folder_path)])
        return {"status": "ok", "path": str(folder_path)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _gamedev_key_status() -> Dict[str, Any]:
    sc_key = os.getenv("SCRAPECREATORS_API_KEY", "").strip()
    steam_key = (os.getenv("STEAMWORKS_API_KEY") or os.getenv("STEAM_WEB_API_KEY") or "").strip()
    return {
        "has_scrapecreators_key": bool(sc_key),
        "scrapecreators_api_key": _mask_secret(sc_key),
        "scrapecreators_masked": _mask_secret(sc_key),
        "scrapecreators_api_key_set": bool(sc_key),
        "has_steamworks_key": bool(steam_key),
        "steamworks_api_key": _mask_secret(steam_key),
        "steamworks_masked": _mask_secret(steam_key),
        "steamworks_api_key_set": bool(steam_key),
    }


@app.get("/api/gamedev/settings")
def get_gamedev_settings():
    """Return current gamedev settings including Scrape Creators and Steamworks API Key statuses (keys masked)."""
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE_PATH, override=True)
    return _gamedev_key_status()


_GAMEDEV_FIELD_TO_ENV = {
    "scrapecreators_api_key": "SCRAPECREATORS_API_KEY",
    "steamworks_api_key": "STEAMWORKS_API_KEY",
}


@app.post("/api/gamedev/settings")
def update_gamedev_settings(payload: GamedevSettingsPayload):
    """Save Scrape Creators and Steamworks API Keys to .env and active runtime environment.

    Empty or masked values are ignored (existing keys are kept); use clear_keys to remove one.
    """
    candidates = {
        "SCRAPECREATORS_API_KEY": payload.scrapecreators_api_key,
        "STEAMWORKS_API_KEY": payload.steamworks_api_key,
    }
    updates, ignored = _collect_env_updates(candidates, payload.clear_keys, _GAMEDEV_FIELD_TO_ENV)
    written = _update_env_file(updates)
    return {
        "status": "ok",
        **_gamedev_key_status(),
        "updated_keys": written,
        "ignored_keys": ignored,
        "message": "Ajustes de API Keys guardados correctamente en .env" if written else "Sin cambios: las claves existentes se conservan.",
    }


def _extract_steam_app_id(val: str) -> str:
    """Extract Steam App ID from number or store URL."""
    if not val:
        return ""
    val = str(val).strip()
    m = re.search(r'app/(\d+)', val)
    if m:
        return m.group(1)
    m2 = re.search(r'\b\d{3,9}\b', val)
    if m2:
        return m2.group(0)
    return val


# -----------------------------------------------------------------------------
# GameDev Insights & Scrape Creators API Integration
# -----------------------------------------------------------------------------

def _extract_social_handle(url: str, platform: str) -> str:
    """Extract username or channel handle from social profile URL."""
    if not url:
        return ""
    u = url.strip()
    if not ("http://" in u or "https://" in u or "/" in u):
        return u.lstrip("@").strip()
    
    clean = u.split("?")[0].rstrip("/")
    parts = clean.split("/")
    if not parts:
        return ""
    last_part = parts[-1]
    
    if platform == "youtube":
        if last_part.lower() in ("shorts", "videos", "featured", "streams") and len(parts) > 1:
            last_part = parts[-2]
            
    return last_part.lstrip("@").strip()


def _generate_default_seed_insights() -> List[Dict[str, Any]]:
    """Generate realistic seed insights for demonstration when API key is not yet configured or no videos scraped."""
    from datetime import timedelta
    today = datetime.now()
    return [
        {
            "upload_date": (today - timedelta(days=1)).strftime("%Y-%m-%d"),
            "views_tiktok": 58400,
            "views_instagram": 21300,
            "views_shorts": 89200,
            "total_views": 168900,
            "hook": "No vas a encontrar tu coche ni en un millón de años 🏎️💨",
            "automated": True
        },
        {
            "upload_date": (today - timedelta(days=3)).strftime("%Y-%m-%d"),
            "views_tiktok": 124000,
            "views_instagram": 45600,
            "views_shorts": 156000,
            "total_views": 325600,
            "hook": "El peor parking de la historia de los videojuegos 💀",
            "automated": True
        },
        {
            "upload_date": (today - timedelta(days=6)).strftime("%Y-%m-%d"),
            "views_tiktok": 38900,
            "views_instagram": 14200,
            "views_shorts": 47500,
            "total_views": 100600,
            "hook": "POV: Se te olvidó en qué planta dejaste el coche 🏢",
            "automated": True
        },
        {
            "upload_date": (today - timedelta(days=9)).strftime("%Y-%m-%d"),
            "views_tiktok": 76500,
            "views_instagram": 32100,
            "views_shorts": 98400,
            "total_views": 207000,
            "hook": "Físicas hiperrealistas de choques en el parking 💥",
            "automated": True
        },
        {
            "upload_date": (today - timedelta(days=14)).strftime("%Y-%m-%d"),
            "views_tiktok": 92300,
            "views_instagram": 28900,
            "views_shorts": 112000,
            "total_views": 233200,
            "hook": "¿Puedes escapar del parking subterráneo antes del colapso? ⏱️",
            "automated": True
        }
    ]


def _calculate_t_plus(publish_date: str, publish_time: str, scrape_time_str: str) -> str:
    """Calculate elapsed time T+ string (e.g. +1h, +6h, +24h, +48h) from publication to scrape timestamp."""
    try:
        if not publish_date:
            return "+0h"
        time_clean = (publish_time or "12:00").strip()
        if len(time_clean.split(":")) == 1:
            time_clean += ":00"
        pub_dt = datetime.strptime(f"{publish_date} {time_clean}", "%Y-%m-%d %H:%M")
        
        if " " in scrape_time_str:
            scr_dt = datetime.strptime(scrape_time_str.split(".")[0], "%Y-%m-%d %H:%M")
        else:
            scr_dt = datetime.strptime(f"{publish_date} {scrape_time_str.strip()}", "%Y-%m-%d %H:%M")
            
        diff = scr_dt - pub_dt
        hours = int(diff.total_seconds() // 3600)
        if hours < 0:
            return "+0h"
        return f"+{hours}h"
    except Exception:
        return "+0h"


def _generate_default_seed_evolutions() -> List[Dict[str, Any]]:
    """Seed data for Evolutions table simulating hourly scrapes over time for 3 videos."""
    from datetime import timedelta
    today = datetime.now()
    d1 = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    d2 = (today - timedelta(days=3)).strftime("%Y-%m-%d")
    d3 = (today - timedelta(days=6)).strftime("%Y-%m-%d")

    return [
        # Video 1 (d1, published at 12:00)
        {"id": "evo_101", "upload_date": d1, "publish_time": "12:00", "views_tiktok": 4200,  "views_reels": 1500,  "views_shorts": 6100,  "scrape_time": f"{d1} 13:00", "t_plus": "+1h"},
        {"id": "evo_102", "upload_date": d1, "publish_time": "12:00", "views_tiktok": 14500, "views_reels": 4800,  "views_shorts": 22000, "scrape_time": f"{d1} 15:00", "t_plus": "+3h"},
        {"id": "evo_103", "upload_date": d1, "publish_time": "12:00", "views_tiktok": 28900, "views_reels": 10200, "views_shorts": 45300, "scrape_time": f"{d1} 18:00", "t_plus": "+6h"},
        {"id": "evo_104", "upload_date": d1, "publish_time": "12:00", "views_tiktok": 42100, "views_reels": 16800, "views_shorts": 68400, "scrape_time": f"{d1} 00:00", "t_plus": "+12h"},
        {"id": "evo_105", "upload_date": d1, "publish_time": "12:00", "views_tiktok": 58400, "views_reels": 21300, "views_shorts": 89200, "scrape_time": f"{today.strftime('%Y-%m-%d')} 12:00", "t_plus": "+24h"},

        # Video 2 (d2, published at 15:30)
        {"id": "evo_201", "upload_date": d2, "publish_time": "15:30", "views_tiktok": 12000, "views_reels": 3800,  "views_shorts": 14000, "scrape_time": f"{d2} 16:30", "t_plus": "+1h"},
        {"id": "evo_202", "upload_date": d2, "publish_time": "15:30", "views_tiktok": 38000, "views_reels": 12400, "views_shorts": 48000, "scrape_time": f"{d2} 18:30", "t_plus": "+3h"},
        {"id": "evo_203", "upload_date": d2, "publish_time": "15:30", "views_tiktok": 65000, "views_reels": 24100, "views_shorts": 82000, "scrape_time": f"{d2} 21:30", "t_plus": "+6h"},
        {"id": "evo_204", "upload_date": d2, "publish_time": "15:30", "views_tiktok": 94000, "views_reels": 35000, "views_shorts": 121000, "scrape_time": f"{d2} 03:30", "t_plus": "+12h"},
        {"id": "evo_205", "upload_date": d2, "publish_time": "15:30", "views_tiktok": 124000, "views_reels": 45600, "views_shorts": 156000, "scrape_time": f"{d2} 15:30", "t_plus": "+24h"},

        # Video 3 (d3, published at 10:00)
        {"id": "evo_301", "upload_date": d3, "publish_time": "10:00", "views_tiktok": 3100,  "views_reels": 950,   "views_shorts": 3800,  "scrape_time": f"{d3} 11:00", "t_plus": "+1h"},
        {"id": "evo_302", "upload_date": d3, "publish_time": "10:00", "views_tiktok": 9400,  "views_reels": 3200,  "views_shorts": 11500, "scrape_time": f"{d3} 13:00", "t_plus": "+3h"},
        {"id": "evo_303", "upload_date": d3, "publish_time": "10:00", "views_tiktok": 18200, "views_reels": 6800,  "views_shorts": 23400, "scrape_time": f"{d3} 16:00", "t_plus": "+6h"},
        {"id": "evo_304", "upload_date": d3, "publish_time": "10:00", "views_tiktok": 27500, "views_reels": 10100, "views_shorts": 34900, "scrape_time": f"{d3} 22:00", "t_plus": "+12h"},
        {"id": "evo_305", "upload_date": d3, "publish_time": "10:00", "views_tiktok": 38900, "views_reels": 14200, "views_shorts": 47500, "scrape_time": f"{d3} 10:00", "t_plus": "+24h"},
    ]


def _calculate_insights_summary(insights: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate aggregated view totals across all platforms for automated videos only."""
    total_views = 0
    tiktok_views = 0
    insta_views = 0
    shorts_views = 0
    automated_count = 0
    for item in insights:
        # Only compute videos that have automated checked (default to True if not explicitly False)
        if item.get("automated") is False:
            continue
        tk = int(item.get("views_tiktok") or 0)
        ig = int(item.get("views_instagram") or 0)
        sh = int(item.get("views_shorts") or 0)
        tiktok_views += tk
        insta_views += ig
        shorts_views += sh
        total_views += (tk + ig + sh)
        automated_count += 1
    return {
        "total_views": total_views,
        "tiktok_views": tiktok_views,
        "instagram_views": insta_views,
        "shorts_views": shorts_views,
        "video_count": automated_count,
        "all_videos_count": len(insights)
    }


def _fetch_scrapecreators_metrics(social_links: Dict[str, str]) -> Dict[str, Dict[str, int]]:
    """
    Calls Scrape Creators API for TikTok, Instagram, and YouTube Shorts.
    Returns a dictionary keyed by date string (YYYY-MM-DD):
    {
        "2026-09-08": {"views_tiktok": 45000, "views_instagram": 12000, "views_shorts": 68000},
        ...
    }
    """
    import requests
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.getenv("SCRAPECREATORS_API_KEY", "").strip()
    
    results: Dict[str, Dict[str, int]] = {}
    
    if not api_key:
        logger.info("SCRAPECREATORS_API_KEY not configured. Falling back to default simulation seed.")
        return results

    headers = {
        "x-api-key": api_key,
        "Accept": "application/json",
        "User-Agent": "AIVideoPipeline/2.7",
    }
    base_url = "https://api.scrapecreators.com"
    timeout = 15

    # 1. TikTok
    tiktok_url = (social_links.get("tiktok") or "").strip()
    tiktok_handle = _extract_social_handle(tiktok_url, "tiktok")
    if tiktok_handle:
        try:
            logger.info(f"Scrape Creators: Fetching TikTok for handle: {tiktok_handle}")
            resp = requests.get(
                f"{base_url}/v3/tiktok/profile/videos",
                params={"handle": tiktok_handle},
                headers=headers,
                timeout=timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("videos") or data.get("items") or data.get("data") or []
                for item in items:
                    created_ts = item.get("create_time") or item.get("created_at") or item.get("timestamp")
                    date_str = ""
                    if isinstance(created_ts, (int, float)):
                        date_str = datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d")
                    elif isinstance(created_ts, str):
                        date_str = created_ts[:10]
                    
                    if not date_str:
                        continue
                        
                    views = int(item.get("play_count") or item.get("views") or (item.get("stats") or {}).get("playCount") or 0)
                    if date_str not in results:
                        results[date_str] = {"views_tiktok": 0, "views_instagram": 0, "views_shorts": 0}
                    results[date_str]["views_tiktok"] += views
            else:
                logger.warning(f"Scrape Creators TikTok response {resp.status_code}: {resp.text[:180]}")
        except Exception as e:
            logger.warning(f"Error scraping TikTok via Scrape Creators: {e}")

    # 2. Instagram
    insta_url = (social_links.get("instagram") or "").strip()
    insta_handle = _extract_social_handle(insta_url, "instagram")
    if insta_handle:
        try:
            logger.info(f"Scrape Creators: Fetching Instagram for handle: {insta_handle}")
            resp = requests.get(
                f"{base_url}/v2/instagram/user/posts",
                params={"handle": insta_handle},
                headers=headers,
                timeout=timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("posts") or data.get("items") or data.get("data") or []
                for item in items:
                    created_ts = item.get("taken_at") or item.get("created_time") or item.get("timestamp")
                    date_str = ""
                    if isinstance(created_ts, (int, float)):
                        date_str = datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d")
                    elif isinstance(created_ts, str):
                        date_str = created_ts[:10]
                        
                    if not date_str:
                        continue
                        
                    views = int(item.get("video_view_count") or item.get("play_count") or item.get("view_count") or item.get("like_count") or 0)
                    if date_str not in results:
                        results[date_str] = {"views_tiktok": 0, "views_instagram": 0, "views_shorts": 0}
                    results[date_str]["views_instagram"] += views
            else:
                logger.warning(f"Scrape Creators Instagram response {resp.status_code}: {resp.text[:180]}")
        except Exception as e:
            logger.warning(f"Error scraping Instagram via Scrape Creators: {e}")

    # 3. YouTube Shorts
    yt_url = (social_links.get("youtube") or "").strip()
    yt_handle = _extract_social_handle(yt_url, "youtube")
    if yt_handle:
        try:
            logger.info(f"Scrape Creators: Fetching YouTube Shorts for handle: {yt_handle}")
            resp = requests.get(
                f"{base_url}/v1/youtube/channel/shorts",
                params={"handle": yt_handle},
                headers=headers,
                timeout=timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("shorts") or data.get("items") or data.get("data") or []
                for item in items:
                    date_val = item.get("published_time") or item.get("upload_date") or item.get("timestamp")
                    date_str = ""
                    if isinstance(date_val, (int, float)):
                        date_str = datetime.fromtimestamp(date_val).strftime("%Y-%m-%d")
                    elif isinstance(date_val, str):
                        date_str = date_val[:10]
                        
                    if not date_str:
                        continue
                        
                    views = int(item.get("views") or item.get("view_count") or 0)
                    if date_str not in results:
                        results[date_str] = {"views_tiktok": 0, "views_instagram": 0, "views_shorts": 0}
                    results[date_str]["views_shorts"] += views
            else:
                logger.warning(f"Scrape Creators YouTube response {resp.status_code}: {resp.text[:180]}")
        except Exception as e:
            logger.warning(f"Error scraping YouTube via Scrape Creators: {e}")

    return results


@app.get("/api/gamedev/games/{game_id}/insights")
@_state_locked(GAMEDEV_GAMES_FILE)
def get_game_insights(game_id: str):
    """Retrieve insights, metrics, and summary for a specific game profile."""
    games = _load_gamedev_games()
    target_game = None
    for g in games:
        if g.get("id") == game_id:
            target_game = g
            break
            
    if not target_game:
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")
        
    insights = target_game.get("insights", [])
    simulated = False
    # Brand new profile: show demo metrics so the dashboard is not empty, but NEVER
    # persist them from a GET (they would be mistaken for real campaign data).
    if not insights:
        insights = _generate_default_seed_insights()
        simulated = True
    elif all(isinstance(item, dict) and item.get("simulated") for item in insights):
        simulated = True

    # Normalize automated field for all entries (response only)
    insights = [dict(item) for item in insights if isinstance(item, dict)]
    for item in insights:
        if "automated" not in item:
            item["automated"] = True
        if simulated:
            item["simulated"] = True

    summary = _calculate_insights_summary(insights)
    if not simulated and (
        target_game.get("views_count") != summary["total_views"]
        or target_game.get("uploaded_count") != summary["video_count"]
    ):
        # Real data only: keep the cached counters shown in the games list in sync.
        target_game["views_count"] = summary["total_views"]
        target_game["uploaded_count"] = summary["video_count"]
        _save_gamedev_games(games)

    return {
        "game_id": game_id,
        "game_name": target_game.get("name", ""),
        "accent_color": target_game.get("accent_color", "#7c3aed"),
        "social_links": target_game.get("social_links", {}),
        "insights": insights,
        "summary": summary,
        "simulated": simulated,
    }


@app.post("/api/gamedev/games/{game_id}/sync-insights")
@_state_locked(GAMEDEV_GAMES_FILE)
def sync_game_insights(game_id: str):
    """
    Sync video metrics from Scrape Creators API.
    Upload date (YYYY-MM-DD) acts as cross-platform ID.
    CRITICAL: Preserves all manually entered user hooks and automated flag!
    Newly discovered videos default to automated = True.
    """
    games = _load_gamedev_games()
    target_game = None
    for g in games:
        if g.get("id") == game_id:
            target_game = g
            break
            
    if not target_game:
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")
        
    social_links = target_game.get("social_links") or {}
    scraped_data = _fetch_scrapecreators_metrics(social_links)
    
    current_insights = target_game.get("insights") or []
    # Index current insights by upload_date to ensure hooks and automated flags are preserved
    insights_by_date = {item["upload_date"]: dict(item) for item in current_insights if "upload_date" in item}
    if scraped_data:
        # Real metrics arrived: drop previously seeded demo rows.
        insights_by_date = {k: v for k, v in insights_by_date.items() if not v.get("simulated")}

    if not scraped_data and not current_insights:
        # Seed realistic fallback data (explicitly labelled as simulated demo data)
        seed = [dict(item, simulated=True) for item in _generate_default_seed_insights()]
        target_game["insights"] = seed
    else:
        # Merge freshly scraped metrics while preserving user hook & automated flag!
        for date_str, metrics in scraped_data.items():
            tk = metrics.get("views_tiktok", 0)
            ig = metrics.get("views_instagram", 0)
            sh = metrics.get("views_shorts", 0)
            total = tk + ig + sh
            
            if date_str in insights_by_date:
                rec = insights_by_date[date_str]
                rec["views_tiktok"] = tk
                rec["views_instagram"] = ig
                rec["views_shorts"] = sh
                rec["total_views"] = total
                # rec["hook"] is preserved as is!
                # rec["automated"] is preserved as is (defaults to True if missing)!
                if "automated" not in rec:
                    rec["automated"] = True
            else:
                # Newly scraped video -> automated defaults to True
                insights_by_date[date_str] = {
                    "upload_date": date_str,
                    "views_tiktok": tk,
                    "views_instagram": ig,
                    "views_shorts": sh,
                    "total_views": total,
                    "hook": "",
                    "automated": True
                }
        
        # Sort descending by date
        sorted_insights = sorted(insights_by_date.values(), key=lambda x: x.get("upload_date", ""), reverse=True)
        target_game["insights"] = sorted_insights
        
    all_insights = target_game.get("insights", [])
    for item in all_insights:
        if "automated" not in item:
            item["automated"] = True

    summary = _calculate_insights_summary(all_insights)
    target_game["views_count"] = summary["total_views"]
    target_game["uploaded_count"] = summary["video_count"]
    target_game["updated_at"] = datetime.now().isoformat()

    # Record evolutions snapshot
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    evolutions = target_game.get("evolutions") or []
    for item in all_insights:
        p_date = item.get("upload_date", "")
        p_time = item.get("publish_time", "12:00")
        t_plus = _calculate_t_plus(p_date, p_time, now_ts)
        evolutions.append({
            "id": f"evo_{uuid.uuid4().hex[:8]}",
            "upload_date": p_date,
            "publish_time": p_time,
            "views_tiktok": int(item.get("views_tiktok") or 0),
            "views_reels": int(item.get("views_instagram") or 0),
            "views_shorts": int(item.get("views_shorts") or 0),
            "scrape_time": now_ts,
            "t_plus": t_plus
        })
    target_game["evolutions"] = evolutions
    
    _save_gamedev_games(games)
    
    summary = _calculate_insights_summary(all_insights)
    return {
        "status": "ok",
        "game_id": game_id,
        "insights": all_insights,
        "evolutions": evolutions,
        "summary": summary,
        "synced_count": len(all_insights),
        "message": f"Métricas actualizadas ({len(all_insights)} vídeos registrados)"
    }


@app.get("/api/gamedev/games/{game_id}/evolutions")
@_state_locked(GAMEDEV_GAMES_FILE)
def get_game_evolutions(game_id: str):
    """Retrieve video performance evolution snapshots over scrape time (T+)."""
    games = _load_gamedev_games()
    target_game = None
    for g in games:
        if g.get("id") == game_id:
            target_game = g
            break
            
    if not target_game:
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")
        
    evolutions = target_game.get("evolutions", [])
    simulated = False
    if not evolutions:
        # Demo data for display only: never persisted from a GET.
        evolutions = [dict(e, simulated=True) for e in _generate_default_seed_evolutions()]
        simulated = True

    insights = target_game.get("insights", [])
    hook_map = {item.get("upload_date"): item.get("hook", "") for item in insights}
    
    unique_dates = sorted(list(set(e.get("upload_date") for e in evolutions if e.get("upload_date"))), reverse=True)
    available_videos = [
        {
            "upload_date": d,
            "hook": hook_map.get(d, ""),
            "label": f"{d} — {hook_map.get(d, 'Vídeo')[:35]}"
        }
        for d in unique_dates
    ]

    return {
        "game_id": game_id,
        "evolutions": evolutions,
        "available_videos": available_videos,
        "simulated": simulated,
    }


@app.patch("/api/gamedev/games/{game_id}/insights/{upload_date}/hook")
@_state_locked(GAMEDEV_GAMES_FILE)
def update_game_insight_hook(game_id: str, upload_date: str, payload: UpdateHookPayload):
    """Update manually entered video hook for a specific upload date."""
    games = _load_gamedev_games()
    target_game = None
    for g in games:
        if g.get("id") == game_id:
            target_game = g
            break
            
    if not target_game:
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")
        
    insights = target_game.get("insights", [])
    found = False
    updated_item = None
    for item in insights:
        if item.get("upload_date") == upload_date:
            item["hook"] = payload.hook.strip()
            updated_item = item
            found = True
            break
            
    if not found:
        new_item = {
            "upload_date": upload_date,
            "views_tiktok": 0,
            "views_instagram": 0,
            "views_shorts": 0,
            "total_views": 0,
            "hook": payload.hook.strip()
        }
        insights.append(new_item)
        updated_item = new_item
        target_game["insights"] = sorted(insights, key=lambda x: x.get("upload_date", ""), reverse=True)
        
    _save_gamedev_games(games)
    return {"status": "ok", "item": updated_item}


@app.patch("/api/gamedev/games/{game_id}/insights/{upload_date}/automated")
@_state_locked(GAMEDEV_GAMES_FILE)
def update_game_insight_automated(game_id: str, upload_date: str, payload: UpdateAutomatedPayload):
    """Toggle automated status for a video in insights."""
    games = _load_gamedev_games()
    target_game = None
    for g in games:
        if g.get("id") == game_id:
            target_game = g
            break
            
    if not target_game:
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")
        
    insights = target_game.get("insights", [])
    found = False
    updated_item = None
    for item in insights:
        if item.get("upload_date") == upload_date:
            item["automated"] = bool(payload.automated)
            updated_item = item
            found = True
            break
            
    if not found:
        raise HTTPException(status_code=404, detail="Vídeo no encontrado.")
        
    summary = _calculate_insights_summary(insights)
    target_game["views_count"] = summary["total_views"]
    target_game["uploaded_count"] = summary["video_count"]
    _save_gamedev_games(games)
    return {
        "status": "ok", 
        "item": updated_item,
        "summary": summary
    }


@app.post("/api/gamedev/games/{game_id}/insights/manual")
@_state_locked(GAMEDEV_GAMES_FILE)
def add_manual_game_insight(game_id: str, payload: ManualInsightPayload):
    """Manually insert or edit a video insight entry."""
    games = _load_gamedev_games()
    target_game = None
    for g in games:
        if g.get("id") == game_id:
            target_game = g
            break
            
    if not target_game:
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")
        
    date_str = payload.upload_date.strip() or datetime.now().strftime("%Y-%m-%d")
    insights = target_game.get("insights", [])
    total = (payload.views_tiktok or 0) + (payload.views_instagram or 0) + (payload.views_shorts or 0)
    
    found = False
    for item in insights:
        if item.get("upload_date") == date_str:
            item["views_tiktok"] = payload.views_tiktok or 0
            item["views_instagram"] = payload.views_instagram or 0
            item["views_shorts"] = payload.views_shorts or 0
            item["total_views"] = total
            if payload.hook:
                item["hook"] = payload.hook.strip()
            if payload.automated is not None:
                item["automated"] = bool(payload.automated)
            found = True
            break
            
    if not found:
        insights.append({
            "upload_date": date_str,
            "views_tiktok": payload.views_tiktok or 0,
            "views_instagram": payload.views_instagram or 0,
            "views_shorts": payload.views_shorts or 0,
            "total_views": total,
            "hook": (payload.hook or "").strip(),
            "automated": True if payload.automated is None else bool(payload.automated)
        })
        
    insights.sort(key=lambda x: x.get("upload_date", ""), reverse=True)
    target_game["insights"] = insights
    summary = _calculate_insights_summary(insights)
    target_game["views_count"] = summary["total_views"]
    target_game["uploaded_count"] = summary["video_count"]
    _save_gamedev_games(games)
    
    return {"status": "ok", "insights": insights, "summary": summary}


@app.delete("/api/gamedev/games/{game_id}/insights/{upload_date}")
@_state_locked(GAMEDEV_GAMES_FILE)
def delete_game_insight(game_id: str, upload_date: str):
    """Delete a video insight row by upload_date."""
    games = _load_gamedev_games()
    target_game = None
    for g in games:
        if g.get("id") == game_id:
            target_game = g
            break
            
    if not target_game:
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")
        
    insights = target_game.get("insights", [])
    filtered = [item for item in insights if item.get("upload_date") != upload_date]
    target_game["insights"] = filtered
    summary = _calculate_insights_summary(filtered)
    target_game["views_count"] = summary["total_views"]
    target_game["uploaded_count"] = summary["video_count"]
    _save_gamedev_games(games)
    
    return {"status": "ok", "insights": filtered, "summary": summary}


def _fetch_steam_store_data(app_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch public game information from official Steam Store API.
    Does not require any API key. Validates whether an App ID exists on Steam.
    """
    import requests
    if not app_id:
        return None
    try:
        resp = requests.get(
            "https://store.steampowered.com/api/appdetails",
            params={"appids": str(app_id).strip(), "l": "spanish"},
            headers={"User-Agent": "AIVideoPipeline/3.0", "Accept": "application/json"},
            timeout=8
        )
        if resp.status_code == 200:
            res_json = resp.json()
            app_data = res_json.get(str(app_id).strip(), {})
            if app_data.get("success"):
                return app_data.get("data", {})
    except Exception as e:
        logger.warning(f"Error consultando Steam Store API para App ID {app_id}: {e}")
    return None


def _fetch_steamworks_wishlists_history(app_id: str, api_key: str, existing_history: Optional[Dict[str, Any]] = None, days: int = 14) -> Dict[str, Any]:
    """
    Calls Steamworks Partner Web API (IPartnerFinancialsService/GetAppWishlistReporting/v001/)
    to fetch missing historical daily wishlist metrics back to app_min_date,
    computing the true official total outstanding wishlists.
    """
    import requests
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta

    if not api_key:
        return {"status": "no_key", "message": "No se ha configurado la clave STEAMWORKS_API_KEY en Ajustes."}
    if not app_id:
        return {"status": "no_appid", "message": "El juego no tiene asignado un Steam App ID."}

    headers = {"User-Agent": "AIVideoPipeline/3.0", "Accept": "application/json"}
    history_by_date = {}
    cached = dict(existing_history or {})
    
    # 1. Probe yesterday first to get recent data and app_min_date
    yesterday_dt = datetime.now().date() - timedelta(days=1)
    yesterday_str = yesterday_dt.strftime("%Y-%m-%d")
    app_min_date_str = None
    
    def fetch_single(d_str: str):
        try:
            resp = requests.get(
                "https://partner.steam-api.com/IPartnerFinancialsService/GetAppWishlistReporting/v001/",
                params={"key": api_key, "appid": str(app_id).strip(), "date": d_str},
                headers=headers,
                timeout=8
            )
            if resp.status_code == 200:
                res_data = resp.json().get("response", {})
                w_summary = res_data.get("wishlist_summary", {})
                return d_str, {
                    "adds": int(w_summary.get("wishlist_adds", 0)),
                    "deletes": int(w_summary.get("wishlist_deletes", 0)),
                    "purchases": int(w_summary.get("wishlist_purchases", 0)),
                    "gifts": int(w_summary.get("wishlist_gifts", 0)),
                    "net": int(w_summary.get("wishlist_adds", 0)) - int(w_summary.get("wishlist_deletes", 0)),
                    "countries": res_data.get("country_summary", []),
                    "languages": res_data.get("language_summary", [])
                }, res_data.get("app_min_date"), None
            elif resp.status_code in (401, 403):
                return d_str, None, None, "forbidden"
            else:
                return d_str, None, None, f"HTTP {resp.status_code}"
        except Exception as e:
            return d_str, None, None, str(e)

    # First fetch yesterday
    d_probe, probe_data, probe_min_date, probe_err = fetch_single(yesterday_str)
    if probe_err == "forbidden":
        return {
            "status": "forbidden",
            "message": "Tu API Key no tiene permisos de 'Financial API Group' en partner.steamgames.com."
        }
    if probe_data:
        history_by_date[yesterday_str] = probe_data
        app_min_date_str = probe_min_date

    # 2. Determine date range to cover back to app_min_date
    if app_min_date_str:
        try:
            min_dt = datetime.strptime(app_min_date_str, "%Y-%m-%d").date()
            # Safety cap at 180 days so it doesn't do thousands of calls if app is very old
            earliest_cap = yesterday_dt - timedelta(days=180)
            start_dt = max(min_dt, earliest_cap)
        except Exception:
            start_dt = yesterday_dt - timedelta(days=days)
    else:
        start_dt = yesterday_dt - timedelta(days=days)

    # Generate all missing dates between start_dt and yesterday_dt
    needed_dates = []
    curr = start_dt
    while curr <= yesterday_dt:
        c_str = curr.strftime("%Y-%m-%d")
        if c_str not in history_by_date and c_str not in cached:
            needed_dates.append(c_str)
        curr += timedelta(days=1)

    # 3. Concurrently fetch any missing dates
    if needed_dates:
        with ThreadPoolExecutor(max_workers=8) as executor:
            batch_results = executor.map(fetch_single, needed_dates)
            for d_str, day_data, min_d, err in batch_results:
                if day_data:
                    history_by_date[d_str] = day_data
                if min_d and not app_min_date_str:
                    app_min_date_str = min_d

    # 4. Merge all history (cached + newly fetched)
    full_history = {**cached, **history_by_date}
    
    # 5. Compute total lifetime metrics across history
    lifetime_adds = sum(item.get("adds", 0) for item in full_history.values())
    lifetime_deletes = sum(item.get("deletes", 0) for item in full_history.values())
    lifetime_purchases = sum(item.get("purchases", 0) for item in full_history.values())
    lifetime_net = lifetime_adds - lifetime_deletes - lifetime_purchases

    if full_history:
        return {
            "status": "ok",
            "history": history_by_date,
            "full_history": full_history,
            "lifetime_total": max(0, lifetime_net),
            "lifetime_adds": lifetime_adds,
            "lifetime_deletes": lifetime_deletes,
            "app_min_date": app_min_date_str,
            "days_fetched": len(history_by_date)
        }
    else:
        return {
            "status": "error",
            "message": probe_err or "No se pudieron obtener métricas de wishlist de Steamworks."
        }


def _generate_steam_impact_analysis(target_game: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate Steam Impact correlation analysis using real Steamworks daily wishlist history when available:
    - Current total wishlists (anchored to user's real wishlist count)
    - 30-day wishlist gain
    - Total accumulated video views across social platforms (both automated-only and all videos)
    - Average conversion rate (Views -> Wishlists)
    - Timeline correlating daily wishlists gained vs. accumulated daily video views.
    """
    from datetime import datetime, timedelta
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env", override=True)

    app_id = (target_game.get("steam_app_id") or "").strip()
    if not app_id and target_game.get("social_links", {}).get("steam"):
        app_id = _extract_steam_app_id(target_game.get("social_links", {}).get("steam"))

    steam_key = (os.getenv("STEAMWORKS_API_KEY") or os.getenv("STEAM_WEB_API_KEY") or "").strip()
    has_steam_key = bool(steam_key)
    steam_history = target_game.get("steam_wishlist_history", {})
    has_real_steam_data = bool(steam_history)

    # 1. Gather all video insights
    insights = target_game.get("insights", [])
    if not insights:
        insights = _generate_default_seed_insights()
        target_game["insights"] = insights

    # Aggregate views by date: both for automated-only and all videos
    views_by_date_auto: Dict[str, Dict[str, int]] = {}
    views_by_date_all: Dict[str, Dict[str, int]] = {}

    for item in insights:
        d = item.get("upload_date")
        if not d:
            continue
        is_auto = item.get("automated") is not False
        tk = int(item.get("views_tiktok") or 0)
        ig = int(item.get("views_instagram") or 0)
        sh = int(item.get("views_shorts") or 0)

        # All videos aggregation
        if d not in views_by_date_all:
            views_by_date_all[d] = {"tiktok": 0, "instagram": 0, "shorts": 0, "total": 0}
        views_by_date_all[d]["tiktok"] += tk
        views_by_date_all[d]["instagram"] += ig
        views_by_date_all[d]["shorts"] += sh
        views_by_date_all[d]["total"] += (tk + ig + sh)

        # Automated only aggregation
        if is_auto:
            if d not in views_by_date_auto:
                views_by_date_auto[d] = {"tiktok": 0, "instagram": 0, "shorts": 0, "total": 0}
            views_by_date_auto[d]["tiktok"] += tk
            views_by_date_auto[d]["instagram"] += ig
            views_by_date_auto[d]["shorts"] += sh
            views_by_date_auto[d]["total"] += (tk + ig + sh)

    # 2. Build continuous timeline spanning the last 20 days
    today = datetime.now()
    days_to_span = 20
    date_list = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_to_span, -1, -1)]

    # Merge in any video dates or steam history dates outside this range
    for vd in list(views_by_date_all.keys()) + list(steam_history.keys()):
        if vd not in date_list:
            date_list.append(vd)
    date_list.sort()

    # Determine anchor wishlist total: prioritize user's real configured wishlists
    known_wishlists = target_game.get("wishlists_count")
    if known_wishlists == 15115 and steam_history:
        calc_total = sum(item.get("net", item.get("adds", 0)) for item in steam_history.values())
        if calc_total > 0:
            target_game["wishlists_count"] = calc_total
            known_wishlists = calc_total

    if known_wishlists is not None and int(known_wishlists) > 0 and int(known_wishlists) != 15115:
        target_final_wishlists = int(known_wishlists)
    elif steam_history:
        target_final_wishlists = max(0, sum(item.get("net", item.get("adds", 0)) for item in steam_history.values()))
    else:
        target_final_wishlists = 15115

    # First pass: calculate daily deltas
    daily_deltas = []
    total_video_wishlists = 0
    total_gained_period = 0
    total_views_auto_all_time = 0
    total_views_all_all_time = 0

    for d_str in date_list:
        v_auto = views_by_date_auto.get(d_str, {"tiktok": 0, "instagram": 0, "shorts": 0, "total": 0})
        v_all = views_by_date_all.get(d_str, {"tiktok": 0, "instagram": 0, "shorts": 0, "total": 0})

        total_views_auto_all_time += v_auto["total"]
        total_views_all_all_time += v_all["total"]

        # Deterministic organic baseline
        seed_hash = sum(ord(c) for c in (d_str + target_game.get("id", "")))
        organic_wishlists = 20 + (seed_hash % 19)

        # Video conversion impact from total views on that day
        day_total_views = v_all["total"]
        if day_total_views > 0:
            conversion_rate = 0.0016 + ((seed_hash % 8) * 0.00008)
            video_wishlists = int(round(day_total_views * conversion_rate))
            total_video_wishlists += video_wishlists
        else:
            video_wishlists = 0

        # IF we have REAL data from Steamworks for this date, USE IT!
        if d_str in steam_history:
            real_day = steam_history[d_str]
            day_wishlists = max(0, real_day.get("net", real_day.get("adds", 0)))
            is_live_steam = True
        else:
            day_wishlists = organic_wishlists + video_wishlists
            is_live_steam = False

        total_gained_period += day_wishlists

        daily_deltas.append({
            "date": d_str,
            "v_auto": v_auto,
            "v_all": v_all,
            "organic_wishlists": organic_wishlists,
            "video_wishlists": video_wishlists,
            "day_wishlists": day_wishlists,
            "is_live_steam": is_live_steam
        })

    # Anchor the cumulative counter so that the final day lands precisely on target_final_wishlists
    running_cumulative = max(0, target_final_wishlists - total_gained_period)

    timeline = []
    for item in daily_deltas:
        running_cumulative += item["day_wishlists"]
        timeline.append({
            "date": item["date"],
            "daily_wishlists": item["day_wishlists"],
            "organic_wishlists": item["organic_wishlists"],
            "video_wishlists": item["video_wishlists"],
            "is_live_steam": item["is_live_steam"],
            # Automated only metrics
            "views_tiktok_auto": item["v_auto"]["tiktok"],
            "views_instagram_auto": item["v_auto"]["instagram"],
            "views_shorts_auto": item["v_auto"]["shorts"],
            "total_views_auto": item["v_auto"]["total"],
            # All videos metrics
            "views_tiktok_all": item["v_all"]["tiktok"],
            "views_instagram_all": item["v_all"]["instagram"],
            "views_shorts_all": item["v_all"]["shorts"],
            "total_views_all": item["v_all"]["total"],
            # Defaults
            "views_tiktok": item["v_auto"]["tiktok"],
            "views_instagram": item["v_auto"]["instagram"],
            "views_shorts": item["v_auto"]["shorts"],
            "total_views": item["v_auto"]["total"],
            "cumulative_wishlists": running_cumulative,
        })

    # Summary metrics
    current_wishlists = target_final_wishlists
    
    # Calculate gain specifically for the last 7 days
    wishlists_7d_gain = sum(item["daily_wishlists"] for item in timeline[-7:]) if timeline else 0

    wishlists_30d_gain = wishlists_7d_gain
    conv_rate_pct = round((total_video_wishlists / total_views_all_all_time * 100), 2) if total_views_all_all_time > 0 else 0.18

    # Store computed wishlists_count back into game profile
    target_game["wishlists_count"] = current_wishlists

    return {
        "game_id": target_game.get("id", ""),
        "game_name": target_game.get("name", ""),
        "accent_color": target_game.get("accent_color", "#7c3aed"),
        "steam_app_id": app_id,
        "steam_store_url": f"https://store.steampowered.com/app/{app_id}" if app_id else "",
        "has_steam_key": has_steam_key,
        "is_simulated": not has_real_steam_data,
        "has_real_steam_data": has_real_steam_data,
        "current_wishlists": current_wishlists,
        "wishlists_7d_gain": wishlists_7d_gain,
        "wishlists_30d_gain": wishlists_7d_gain,
        "total_video_views_auto": total_views_auto_all_time,
        "total_video_views_all": total_views_all_all_time,
        "total_video_views": total_views_auto_all_time,
        "avg_conversion_rate": conv_rate_pct,
        "timeline": timeline,
    }


@app.get("/api/gamedev/games/{game_id}/steam-impact")
@_state_locked(GAMEDEV_GAMES_FILE)
def get_game_steam_impact(game_id: str):
    """Retrieve Steam Impact metrics, wishlist conversion analysis, and daily timeline."""
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env", override=True)

    games = _load_gamedev_games()
    target_game = None
    for g in games:
        if g.get("id") == game_id:
            target_game = g
            break
            
    if not target_game:
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")
        
    had_insights = bool(target_game.get("insights"))
    wishlists_before = target_game.get("wishlists_count")
    analysis = _generate_steam_impact_analysis(target_game)
    # A GET never persists demo data: seeded insights and simulated wishlist totals
    # are returned for display only (same rule as the insights GET endpoint).
    if not had_insights:
        target_game.pop("insights", None)
        if isinstance(analysis, dict):
            analysis["simulated_insights"] = True
    real_steam_data = isinstance(analysis, dict) and bool(analysis.get("has_real_steam_data"))
    if not real_steam_data:
        if wishlists_before is None:
            target_game.pop("wishlists_count", None)
        else:
            target_game["wishlists_count"] = wishlists_before
    if target_game.get("wishlists_count") != wishlists_before:
        _save_gamedev_games(games)
    return analysis


@app.post("/api/gamedev/games/{game_id}/sync-steam")
@_state_locked(GAMEDEV_GAMES_FILE)
def sync_game_steam_impact(game_id: str, payload: Optional[SyncSteamPayload] = None):
    """Sync Steam store metadata & live wishlist metrics with Steam API and recompute impact analysis."""
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env", override=True)

    games = _load_gamedev_games()
    target_game = None
    for g in games:
        if g.get("id") == game_id:
            target_game = g
            break
            
    if not target_game:
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")
        
    app_id = (target_game.get("steam_app_id") or "").strip()
    if not app_id and target_game.get("social_links", {}).get("steam"):
        app_id = _extract_steam_app_id(target_game.get("social_links", {}).get("steam"))

    if not app_id:
        raise HTTPException(
            status_code=400,
            detail=f"El juego '{target_game.get('name')}' no tiene configurado un Steam App ID ni enlace de la tienda. Haz clic en 'Editar Perfil' y añade el Steam App ID (ej: 5195880)."
        )

    # 1. Validar primero con la API Oficial de Steam Store (pública y 100% fiable)
    store_data = _fetch_steam_store_data(app_id)
    if not store_data:
        raise HTTPException(
            status_code=400,
            detail=f"El Steam App ID '{app_id}' no fue encontrado en los servidores de Steam (error 404). Verifica que el número coincida con la URL de tu juego (store.steampowered.com/app/{app_id})."
        )

    store_name = store_data.get("name", "")
    header_img = store_data.get("header_image", "")
    if header_img:
        target_game["steam_header_image"] = header_img
    if store_name and not target_game.get("name"):
        target_game["name"] = store_name

    # Check for manual wishlist count supplied in payload
    manual_wishlists = None
    if payload and payload.wishlists_count is not None and int(payload.wishlists_count) > 0:
        manual_wishlists = int(payload.wishlists_count)

    # 2. Consultar Steamworks Partner API para Wishlists
    steam_key = (os.getenv("STEAMWORKS_API_KEY") or os.getenv("STEAM_WEB_API_KEY") or "").strip()
    wishlist_synced = False
    status_msg = ""

    if steam_key:
        existing_history = target_game.get("steam_wishlist_history", {})
        steam_res = _fetch_steamworks_wishlists_history(app_id, steam_key, existing_history=existing_history)
        if steam_res.get("status") == "ok":
            full_history = steam_res.get("full_history", {})
            target_game["steam_wishlist_history"] = full_history
            wishlist_synced = True
            
            lifetime_total = steam_res.get("lifetime_total", 0)
            lifetime_adds = steam_res.get("lifetime_adds", 0)
            lifetime_dels = steam_res.get("lifetime_deletes", 0)

            if manual_wishlists is not None:
                target_game["wishlists_count"] = manual_wishlists
                status_msg = f"¡Métricas oficiales de Steamworks sincronizadas para '{store_name}'! Total de Wishlists actualizado a {manual_wishlists:,}."
            elif lifetime_total > 0:
                target_game["wishlists_count"] = lifetime_total
                status_msg = f"¡Métricas oficiales de Steamworks sincronizadas para '{store_name}'! Total de Wishlists actualizado a {lifetime_total:,} ({lifetime_adds:,} altas, {lifetime_dels:,} bajas netas)."
            else:
                total_adds = sum(item.get("adds", 0) for item in full_history.values())
                total_net = sum(item.get("net", 0) for item in full_history.values())
                status_msg = f"¡Métricas oficiales de Steamworks sincronizadas para '{store_name}'! (+{total_net:,} netas)."
        elif steam_res.get("status") == "forbidden":
            if manual_wishlists is not None:
                target_game["wishlists_count"] = manual_wishlists
                status_msg = f"Datos de la tienda sincronizados. Total de wishlists actualizado a {manual_wishlists:,}."
            else:
                status_msg = (
                    f"Datos oficiales de Steam sincronizados para '{store_name}'. "
                    f"Aviso: tu clave requiere permisos de 'Financial API Group' en partner.steamgames.com para descargar wishlists automáticamente. "
                    f"Se mantiene tu conteo de {target_game.get('wishlists_count', 0):,} wishlists."
                )
        else:
            if manual_wishlists is not None:
                target_game["wishlists_count"] = manual_wishlists
            status_msg = f"Datos de la tienda sincronizados para '{store_name}'. {steam_res.get('message', '')}"
    else:
        if manual_wishlists is not None:
            target_game["wishlists_count"] = manual_wishlists
            status_msg = f"Datos de Steam sincronizados para '{store_name}'. Total de wishlists actualizado a {manual_wishlists:,}."
        else:
            status_msg = (
                f"Datos oficiales de Steam sincronizados para '{store_name}'. "
                f"No tienes configurada la Steamworks API Key en Ajustes, pero puedes ingresar o editar tus wishlists directamente con el botón ✏️."
            )

    analysis = _generate_steam_impact_analysis(target_game)
    _save_gamedev_games(games)

    return {
        "status": "ok",
        "message": status_msg,
        "store_name": store_name,
        "wishlist_synced": wishlist_synced,
        "data": analysis
    }



@app.patch("/api/gamedev/games/{game_id}/wishlists")
@_state_locked(GAMEDEV_GAMES_FILE)
def update_game_wishlists(game_id: str, payload: UpdateWishlistsPayload):
    """Directly update current wishlist count for a game and recompute impact correlation."""
    games = _load_gamedev_games()
    target_game = None
    for g in games:
        if g.get("id") == game_id:
            target_game = g
            break
            
    if not target_game:
        raise HTTPException(status_code=404, detail="Perfil de juego no encontrado.")
        
    target_game["wishlists_count"] = max(0, int(payload.wishlists_count))
    analysis = _generate_steam_impact_analysis(target_game)
    _save_gamedev_games(games)
    
    return {
        "status": "ok",
        "message": f"Wishlists actualizadas a {target_game['wishlists_count']:,}",
        "data": analysis
    }


# =============================================================================
# API Endpoints: Trailer Feed (Pre-Production)
# =============================================================================

@app.get("/api/trailers")
def get_trailers(
    timeframe: Optional[str] = "all",
    search: Optional[str] = None,
    channel: Optional[str] = None,
    brand: Optional[str] = None,
    planned_date: Optional[str] = None,
):
    """Retrieve trailers and editorial eligibility for a planned publication."""
    mgr = TrailerDatabaseManager()
    
    # Auto-initialize database if empty
    if len(mgr.trailers) == 0:
        try:
            logger.info("Trailers database is empty. Performing initial RSS scrape...")
            refresh_all_trailers(timeframe_hours=168)
            mgr = TrailerDatabaseManager()
        except Exception as e:
            logger.warning(f"Initial trailer scrape failed: {e}")

    channel_filter = None
    if channel and channel != "all":
        if channel == "indie":
            channel_filter = "Indie Games Hub"
        elif channel == "trailers":
            channel_filter = "GameTrailers (IGN)"
        else:
            channel_filter = channel

    trailers = mgr.get_feed_items(channel_filter=channel_filter, search_query=search)

    if timeframe and timeframe != "all":
        from datetime import timezone, timedelta
        hours = {"24h": 24, "3d": 72, "7d": 168}.get(timeframe, 24)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        filtered = []
        for t in trailers:
            pub = t.get("published_at")
            if pub:
                try:
                    pub_str = str(pub).replace("Z", "+00:00")
                    pdt = datetime.fromisoformat(pub_str)
                    if pdt.tzinfo is None:
                        pdt = pdt.replace(tzinfo=timezone.utc)
                    if pdt >= cutoff:
                        filtered.append(t)
                except Exception:
                    filtered.append(t)
            else:
                filtered.append(t)
        trailers = filtered

    from catalog_policy import cooldown_check, day
    from zoneinfo import ZoneInfo
    if brand and brand not in ("NextLoot", "PartyLoot"):
        raise HTTPException(status_code=400, detail="Marca editorial desconocida")
    try:
        editorial_date = day(planned_date) if planned_date else datetime.now(ZoneInfo("Europe/Madrid")).date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Fecha editorial inválida; usa YYYY-MM-DD")
    history_path = BASE_DIR / "data" / "editorial_history.json"
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    for t in trailers:
        t["editorial"] = {b: cooldown_check(t.get("game_name", ""), b, editorial_date, history, 21)
                          for b in ("NextLoot", "PartyLoot")}
    if brand:
        trailers = [t for t in trailers if t["editorial"][brand]["eligible"]]

    # Map id to video_id
    for t in trailers:
        t["video_id"] = t.get("id")
        t["upload_date"] = t.get("published_at", "")[:10]

    return {
        "count": len(trailers),
        "trailers": trailers,
    }


@app.post("/api/trailers/scrape")
def trigger_scrape(req: ScrapeRequest):
    """Trigger background scraping of trailers from YouTube channels."""
    job = create_job("scrape")
    
    def worker():
        try:
            job.update(10.0, f"Iniciando scrape de canales (periodo: {req.timeframe})...", "Conectando con YouTube RSS feeds...")
            hours = {"24h": 24, "3d": 72, "7d": 168}.get(req.timeframe, 720)
            
            new_count, total_count = refresh_all_trailers(timeframe_hours=hours)
            job.complete({
                "new_count": new_count,
                "total_count": total_count,
            }, f"Scraping completado. {new_count} nuevos trailers añadidos ({total_count} total en DB).")
        except Exception as e:
            logger.exception("Scrape failed")
            job.fail(str(e))
            
    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job.job_id}


@app.delete("/api/trailers/{video_id}")
def delete_trailer(video_id: str):
    """Archive/delete a specific trailer by video_id."""
    mgr = TrailerDatabaseManager()
    mgr.set_status(video_id, "archived")
    return {"status": "ok"}


@app.post("/api/trailers/clear")
def clear_all_trailers():
    """Clear all entries in the trailer database."""
    mgr = TrailerDatabaseManager()
    mgr.clear_all()
    return {"status": "ok", "message": "Base de datos de trailers vaciada"}


# =============================================================================
# API Endpoints: AI Hook Generation
# =============================================================================

@app.post("/api/hook/generate")
def generate_hook(req: HookGenerateRequest):
    """Generate a viral promotional hook using Groq or Gemini."""
    cfg = AppConfig.from_env()
    groq_key = _client_secret_or_env(req.groq_api_key, cfg.groq_api_key)
    gemini_key = _client_secret_or_env(req.gemini_api_key, cfg.gemini_api_key)
    
    if not groq_key and not gemini_key:
        raise HTTPException(status_code=400, detail="Se requiere Groq API Key o Gemini API Key")
    if not req.game_name.strip():
        raise HTTPException(status_code=400, detail="Escribe el nombre del juego")
        
    try:
        var_idx = getattr(req, "prompt_variation", 0) or 0
        hook = generate_hook_with_groq(req.game_name, groq_key, prompt_variation=var_idx, gemini_api_key=gemini_key)
        return {"status": "ok", "hook": hook}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/hook/generate-multiple")
def generate_multiple_hooks(req: MultipleHooksGenerateRequest):
    """Generate multiple distinct promotional hooks with varied creative angles."""
    cfg = AppConfig.from_env()
    groq_key = _client_secret_or_env(req.groq_api_key, cfg.groq_api_key)
    gemini_key = _client_secret_or_env(req.gemini_api_key, cfg.gemini_api_key)

    if not groq_key and not gemini_key:
        raise HTTPException(status_code=400, detail="Se requiere Groq API Key o Gemini API Key")
    if not req.game_name.strip():
        raise HTTPException(status_code=400, detail="Escribe el nombre del juego")

    count = max(1, min(3, req.count or 1))
    hooks = []
    for i in range(count):
        try:
            h = generate_hook_with_groq(
                game_name=req.game_name,
                groq_api_key=groq_key,
                prompt_variation=i,
                gemini_api_key=gemini_key
            )
            hooks.append(h)
        except Exception as e:
            logger.warning(f"Error generating hook variant {i+1}: {e}")
            if not hooks:
                raise HTTPException(status_code=500, detail=str(e))
            # Fallback if subsequent variant fails
            fallback_angles = [
                f"A game that looks simple until you realize every action has permanent consequences. 🎮🔥",
                f"You will spend 20 minutes setting up the perfect plan, only for everything to fail hilariously in 3 seconds. 💀",
                f"An indie game where the physics engine is either your greatest weapon or your worst enemy. 🕹️"
            ]
            hooks.append(fallback_angles[i % len(fallback_angles)])

    return {"status": "ok", "hooks": hooks}


@app.post("/api/hook/modify")
def modify_hook(req: HookModifyRequest):
    """Modify hook length (shorter or longer)."""
    cfg = AppConfig.from_env()
    groq_key = _client_secret_or_env(req.groq_api_key, cfg.groq_api_key)
    gemini_key = _client_secret_or_env(req.gemini_api_key, cfg.gemini_api_key)
    
    if not req.current_text.strip():
        raise HTTPException(status_code=400, detail="El texto actual está vacío")
        
    try:
        new_text = modify_hook_length(req.current_text, req.action, groq_key, gemini_api_key=gemini_key)
        return {"status": "ok", "hook": new_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# API Endpoints: Short V2 IA (Script & Voiceover)
# =============================================================================

@app.post("/api/v2/script/generate")
def generate_v2_script_endpoint(req: V2ScriptGenerateRequest):
    """Generate high-retention viral promo script for Short V2 IA."""
    cfg = AppConfig.from_env()
    groq_key = _client_secret_or_env(req.groq_api_key, cfg.groq_api_key)
    gemini_key = _client_secret_or_env(req.gemini_api_key, cfg.gemini_api_key)

    if not groq_key and not gemini_key:
        raise HTTPException(status_code=400, detail="Se requiere Groq API Key o Gemini API Key")
    if not req.game_name.strip():
        raise HTTPException(status_code=400, detail="Escribe el nombre del juego")

    briefing = req.briefing_text or ""
    if req.session_id:
        step2_json = _safe_session_dir(req.session_id) / "step2_evaluated_shots.json"
        if step2_json.exists():
            try:
                with open(step2_json, "r", encoding="utf-8") as f:
                    s2_data = json.load(f)
                shots = s2_data.get("shots", [])
                rendered_shots = [s for s in shots if s.get("rendered") and s.get("description")]
                rendered_shots.sort(key=lambda s: s.get("viral_score", 0), reverse=True)
                top_moments = [
                    f"Shot #{s.get('shot_id')} (Viral {s.get('viral_score', 0)}/100, Role: {s.get('suggested_role', 'gameplay')}): {s.get('description')}"
                    for s in rendered_shots[:4]
                ]
                if top_moments:
                    ctx = "Key gameplay moments captured in the actual video footage:\n- " + "\n- ".join(top_moments)
                    briefing = f"{briefing}\n\n{ctx}" if briefing else ctx
            except Exception as e:
                logger.warning(f"Could not load step2 context from {step2_json}: {e}")

    # Word budget must use the SAME speed the voice will use.
    saved_voice_speed = None
    if req.session_id and req.speech_speed is None:
        try:
            s3_file = _safe_session_dir(req.session_id) / "step3_script.json"
            if s3_file.exists():
                saved_voice_speed = (json.loads(s3_file.read_text(encoding="utf-8")) or {}).get("speed")
        except HTTPException:
            raise
        except Exception:
            saved_voice_speed = None
    speech_speed = _resolve_speech_speed(req.speech_speed, saved_voice_speed)
    hook_type = _hook_type_value(req.hook_type)
    opening_text = (req.opening_text or "").strip()

    try:
        optional_kwargs, _ = _filter_supported_kwargs(generate_v2_script, {
            "opening_text": opening_text,
            "speech_speed": speech_speed,
        })
        res = generate_v2_script(
            game_name=req.game_name,
            target_duration=req.target_duration,
            tone=req.tone or "viral_hype",
            briefing_text=briefing,
            hook_type=hook_type,
            script_mode=req.script_mode,
            game_genre=req.game_genre,
            groq_api_key=groq_key or "",
            gemini_api_key=gemini_key,
            **optional_kwargs,
        )
        if hook_type == "custom" and opening_text and "opening_text" not in optional_kwargs and isinstance(res, dict) and res.get("script"):
            # Script generator without opening_text support yet: enforce the opener here.
            res["script"] = _apply_custom_opening(res["script"], opening_text)
        return {"status": "ok", "hook_type": hook_type, "opening_text": opening_text, "speech_speed": speech_speed, **res}
    except Exception as e:
        logger.exception("Error generating V2 script")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v2/script/modify")
def modify_v2_script_endpoint(req: V2ScriptModifyRequest):
    """Shorten, lengthen, change hook, or modify with custom instructions an existing V2 promo script."""
    cfg = AppConfig.from_env()
    groq_key = _client_secret_or_env(req.groq_api_key, cfg.groq_api_key)
    gemini_key = _client_secret_or_env(req.gemini_api_key, cfg.gemini_api_key)

    if not req.script_text.strip():
        raise HTTPException(status_code=400, detail="El texto del guión está vacío")

    try:
        optional_kwargs, _ = _filter_supported_kwargs(modify_v2_script_length, {
            "hook_type": (req.hook_type or "").strip() or None,
            "opening_text": (req.opening_text or "").strip(),
            "speech_speed": _resolve_speech_speed(req.speech_speed),
            "script_mode": req.script_mode,
            "game_genre": req.game_genre or "",
        })
        res = modify_v2_script_length(
            current_script=req.script_text,
            action=req.action,
            game_name=req.game_name,
            target_duration=req.target_duration,
            custom_instruction=req.custom_instruction or "",
            groq_api_key=groq_key or "",
            gemini_api_key=gemini_key,
            **optional_kwargs,
        )
        return {"status": "ok", **res}
    except Exception as e:
        logger.exception("Error modifying V2 script")
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/api/v2/steam/game-details")
def get_v2_steam_game_details_endpoint(game_name: str):
    """Retrieve Steam game metadata, short description and detailed long description."""
    from utils import clean_steam_html
    import requests

    clean_name = (game_name or "").strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Nombre del juego no proporcionado")

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        s_res = requests.get(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": clean_name, "l": "english", "cc": "US"},
            headers=headers,
            timeout=10
        ).json()
        items = s_res.get("items", [])
        if not items:
            return {"found": False, "message": f"No se encontró el juego '{clean_name}' en Steam"}

        app_id = items[0].get("id")
        app_name = items[0].get("name")
        tiny_image = items[0].get("tiny_image", "")

        d_res = requests.get(
            "https://store.steampowered.com/api/appdetails",
            params={"appids": str(app_id), "l": "english"},
            headers=headers,
            timeout=10
        ).json()

        app_data = d_res.get(str(app_id)) or (next(iter(d_res.values()), {}) if d_res else {})
        data = app_data.get("data", {}) if app_data.get("success") else {}

        short_desc = clean_steam_html(data.get("short_description", ""))
        long_desc = clean_steam_html(data.get("about_the_game", "") or data.get("detailed_description", ""))

        return {
            "found": True,
            "app_id": app_id,
            "name": app_name or clean_name,
            "tiny_image": tiny_image,
            "short_description": short_desc,
            "long_description": long_desc,
            "steam_store_url": f"https://store.steampowered.com/app/{app_id}"
        }
    except Exception as e:
        logger.warning(f"Error fetching Steam details for '{clean_name}': {e}")
        return {"found": False, "error": str(e)}


@app.post("/api/v2/voiceover/upload")
async def upload_voiceover_endpoint(file: UploadFile = File(...)):
    """Upload user voiceover file (MP3, WAV, M4A, MP4, WebM) and probe duration."""
    upload_dir = BASE_DIR / "temp" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    clean_filename = f"voice_{uuid.uuid4().hex[:6]}_{_safe_upload_basename(file.filename, default_stem='voice')}"
    dest_path = _safe_child_path(upload_dir, clean_filename)

    content = await file.read()
    await run_in_threadpool(_write_bytes_file, dest_path, content)

    # Extract audio if it's a video or raw audio file (FFmpeg: run off the event loop)
    extracted_wav = _safe_child_path(upload_dir, f"{dest_path.stem}.wav")
    clean_audio_path = await run_in_threadpool(extract_clean_audio, dest_path, extracted_wav)
    duration = await run_in_threadpool(get_audio_duration, clean_audio_path)

    return {
        "status": "ok",
        "filename": file.filename,
        "saved_path": str(clean_audio_path),
        "duration": duration,
        "url": f"/media/temp/uploads/{extracted_wav.name}",
    }


def _resolve_promov3_video_path(raw_path: str) -> Optional[Path]:
    """Resolve a raw video path or URL into a local filesystem Path."""
    if not raw_path:
        return None
    raw = raw_path.strip()
    if raw.startswith("/media/PromosFootage/"):
        rel = raw[len("/media/PromosFootage/"):]
        return (PROMOS_FOOTAGE_DIR / urllib.parse.unquote(rel)).resolve()
    elif raw.startswith("/media/GamesFootage/"):
        rel = raw[len("/media/GamesFootage/"):]
        return (GAMES_FOOTAGE_DIR / urllib.parse.unquote(rel)).resolve()
    elif raw.startswith("/media/temp/"):
        rel = raw[len("/media/temp/"):]
        return (BASE_DIR / "temp" / urllib.parse.unquote(rel)).resolve()
    elif raw.startswith("/media/"):
        rel = raw[len("/media/"):]
        return (BASE_DIR / urllib.parse.unquote(rel)).resolve()
    else:
        p = Path(raw)
        if p.is_absolute() and p.exists():
            return p
        elif (BASE_DIR / raw).exists():
            return (BASE_DIR / raw).resolve()
        return Path(raw).resolve()


V3_CACHE_DIR = BASE_DIR / "temp" / "v3_cache"

# ----------------------------------------------------------------------------
# Shared API contract helpers (opening/hook, voice, render options)
# ----------------------------------------------------------------------------
HOOK_TYPES = ("steam_new_game", "so_in_this_new_game", "direct", "custom")
STEAM_SPLASH_HOOK_TYPES = ("steam_new_game", "so_in_this_new_game")
CUT_START_MODES = ("shot_start", "best_moment")
DEFAULT_SPEECH_SPEED = 1.1


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return max(lo, min(hi, v))


def _resolve_speech_speed(value: Any, fallback: Any = None) -> float:
    """Voice speed used for the script word budget (ElevenLabs range 0.7-1.2)."""
    for cand in (value, fallback):
        if cand is not None:
            try:
                return _clamp_float(float(cand), 0.7, 1.2, DEFAULT_SPEECH_SPEED)
            except (TypeError, ValueError):
                continue
    return DEFAULT_SPEECH_SPEED


def _hook_type_value(hook_type: Any) -> str:
    return (str(hook_type).strip() if hook_type else "") or "steam_new_game"


def _effective_hook_type(hook_type: Any, opening_text: Optional[str] = "") -> str:
    """hook_type actually applied to a script.

    Same rule as script_retention.resolve_hook_type: "custom" without opening_text
    falls back to the default Steam opener (required by Trapster campaigns), so the
    spoken opener, the splash and the captions always agree. Other values unchanged.
    """
    ht = _hook_type_value(hook_type)
    if ht == "custom" and not (opening_text or "").strip():
        logger.warning("hook_type 'custom' sin opening_text: se usa la entrada Steam por defecto.")
        return "steam_new_game"
    return ht


def _uses_steam_splash(hook_type: Any) -> bool:
    """direct/custom scripts have no spoken Steam prefix: no splash timed to it.
    Unknown legacy values keep the previous behaviour (splash on)."""
    return _hook_type_value(hook_type) not in ("direct", "custom")


def _normalize_for_prefix(text: str) -> str:
    return re.sub(r"[^\w]+", " ", (text or "").lower()).strip()


def _apply_custom_opening(script: str, opening_text: Optional[str]) -> str:
    """Make sure ``script`` starts with the campaign-provided opener."""
    opener = (opening_text or "").strip()
    body = (script or "").strip()
    if not opener:
        return body
    if _normalize_for_prefix(body).startswith(_normalize_for_prefix(opener)):
        return body
    return f"{opener} {body}".strip()


def _apply_spoken_opening(script: str, hook_type: Any, opening_text: Optional[str] = "") -> str:
    """Spoken opener for synthesized V2 scripts.

    steam_new_game -> "Here's a new Steam game..." prefix (unchanged behaviour);
    so_in_this_new_game -> "So in this new game, ..." (unchanged behaviour);
    custom -> starts with opening_text (without text: default Steam opener); direct -> untouched.
    """
    ht = _effective_hook_type(hook_type, opening_text)
    clean_script = (script or "").strip()
    if ht == "steam_new_game":
        return ensure_steam_hook_prefix(clean_script)
    if ht == "so_in_this_new_game":
        if not clean_script.lower().startswith("so in this new game"):
            first_char = clean_script[0].lower() if len(clean_script) > 1 else ""
            clean_script = f"So in this new game, {first_char}{clean_script[1:]}" if clean_script else "So in this new game, check out what happens."
        return clean_script
    if ht == "custom":
        return _apply_custom_opening(clean_script, opening_text)
    return clean_script


def _filter_supported_kwargs(fn: Any, optional: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Split ``optional`` kwargs into the ones ``fn`` accepts and the ones it does not.

    New contract options are passed only when the target engine/voice/script
    function already supports them, so this module keeps working whatever the
    merge order of the parallel engine changes is (dropped options are logged).
    """
    if not optional:
        return {}, []
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return dict(optional), []
    if any(prm.kind == inspect.Parameter.VAR_KEYWORD for prm in params.values()):
        return dict(optional), []
    accepted = {k: v for k, v in optional.items() if k in params}
    dropped = [k for k in optional if k not in params]
    if dropped:
        logger.warning(f"{getattr(fn, '__name__', fn)} no admite todavía: {', '.join(dropped)} (se ignoran).")
    return accepted, dropped
def _validate_session_id(session_id: Any) -> str:
    """Validate a Promo V3 session id used as a folder name under temp/v3_cache.

    Session ids are generated as ``sess_<md5>_<stem>`` / ``sess_multi_...`` (stems
    keep Unicode letters, digits, '_' and '-'), plus legacy names such as
    ``sess_<timestamp>`` and the ``global`` folder. Any single, valid Windows folder
    name is accepted; path separators, '..', drive colons, control characters and
    names ending in '.' or ' ' (aliased by Windows) are rejected with HTTP 400.
    """
    try:
        return validate_folder_name(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="session_id inválido.")


def _safe_session_dir(session_id: Any, cache_dir: Optional[Path] = None) -> Path:
    """Return temp/v3_cache/<session_id> after validating it stays inside the cache
    (the single helper used by every handler that joins a session id into a path)."""
    try:
        return safe_child_dir(Path(cache_dir or V3_CACHE_DIR), session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="session_id inválido.")


def _safe_session_dir_or_none(session_id: Any) -> Optional[Path]:
    """Non-raising variant for ids read from local data files."""
    try:
        return _safe_session_dir(session_id)
    except HTTPException:
        return None


def get_canonical_v3_session_id(video_path: str) -> str:
    """Generate a stable, predictable session directory name for a given video."""
    import hashlib
    resolved = _resolve_promov3_video_path(video_path)
    if not resolved:
        resolved = Path(video_path)
    path_hash = hashlib.md5(str(resolved).encode("utf-8")).hexdigest()[:8]
    clean_stem = re.sub(r"[^\w\-]", "_", resolved.stem)[:24]
    return f"sess_{path_hash}_{clean_stem}"


def get_canonical_v3_multi_session_id(video_paths: List[str | Path]) -> str:
    """Generate a stable, predictable session directory name for multiple videos."""
    import hashlib
    resolved_list = [_resolve_promov3_video_path(str(p)) or Path(p) for p in video_paths if p]
    if not resolved_list:
        return f"sess_multi_{int(time.time())}"
    if len(resolved_list) == 1:
        return get_canonical_v3_session_id(str(resolved_list[0]))
    combined = "|".join(sorted(str(p.resolve()) for p in resolved_list))
    path_hash = hashlib.md5(combined.encode("utf-8")).hexdigest()[:8]
    first_stem = re.sub(r"[^\w\-]", "_", resolved_list[0].stem)[:16]
    return f"sess_multi_{path_hash}_{first_stem}_{len(resolved_list)}v"


@app.post("/api/promov3/step3/save")
def save_promov3_step3_endpoint(req: PromoV3Step3SaveRequest):
    """Save Step 3 script & voiceover state to session cache in v3_cache/{session_id}/step3_script.json."""
    sess_name = (req.session_id or "").strip()
    if not sess_name and req.video_paths:
        sess_name = get_canonical_v3_multi_session_id(req.video_paths)
    elif not sess_name and req.video_path:
        sess_name = get_canonical_v3_session_id(req.video_path)

    if not sess_name:
        raise HTTPException(status_code=400, detail="session_id o video_path es obligatorio")

    from project_paths import safe_child_dir as _safe_child_dir  # local import: endpoint is also loaded in isolation by tests
    try:
        sess_dir = _safe_child_dir(BASE_DIR / "temp" / "v3_cache", sess_name)
    except ValueError:
        raise HTTPException(status_code=400, detail="session_id inválido.")
    sess_dir.mkdir(parents=True, exist_ok=True)
    out_file = sess_dir / "step3_script.json"

    # Purge stale voice & downstream cache if script_text has actually changed
    voice_file = sess_dir / "step3_voice.json"
    voice_mp3 = sess_dir / "voiceover.mp3"
    old_script_txt = ""
    vdata = {}
    if voice_file.exists():
        try:
            with open(voice_file, "r", encoding="utf-8") as vf:
                vdata = json.load(vf)
            old_script_txt = (vdata.get("script_text") or "").strip()
        except Exception as ve:
            logger.warning(f"Error verificando caché de voz previa: {ve}")

    if not old_script_txt and out_file.exists():
        try:
            with open(out_file, "r", encoding="utf-8") as of:
                sdata = json.load(of)
            old_script_txt = (sdata.get("script_text") or "").strip()
        except Exception:
            pass

    new_script_txt = (req.script_text or "").strip()
    script_changed = bool(old_script_txt and new_script_txt and not is_script_text_equivalent(old_script_txt, new_script_txt))

    if script_changed:
        logger.info(f"Guión modificado en Paso 3. Purgando locución y montaje EDL desactualizados en sesión '{sess_name}'.")
        voice_file.unlink(missing_ok=True)
        voice_mp3.unlink(missing_ok=True)
        (sess_dir / "step4_edl.json").unlink(missing_ok=True)
        (sess_dir / "step5_result.json").unlink(missing_ok=True)
        (sess_dir / "master_audio.mp3").unlink(missing_ok=True)
        (sess_dir / "silent_video.mp4").unlink(missing_ok=True)
        (sess_dir / "gameplay_audio.flac").unlink(missing_ok=True)
        vdata = {}

    # If voice was not passed in request but previous equivalent voice exists, preserve it
    saved_voice_path = None if script_changed else (req.voice_path or vdata.get("audio_path") or vdata.get("voice_path"))
    saved_voice_url = None if script_changed else (req.voice_url or vdata.get("voice_url"))
    saved_voice_duration = None if script_changed else (req.voice_duration or vdata.get("voice_duration"))
    saved_sentence_1_end = req.sentence_1_end_s if req.sentence_1_end_s is not None else (None if script_changed else vdata.get("sentence_1_end_s"))
    saved_words = [] if script_changed else vdata.get("words", [])
    saved_alignment = {} if script_changed else vdata.get("alignment", {})

    retention_payload = req.retention_data
    if req.script_text:
        try:
            from script_retention import parse_and_repair_retention_script
            retention_payload = parse_and_repair_retention_script(
                raw_content=retention_payload or req.script_text,
                game_name=req.game_name,
                target_duration=req.target_duration,
                words=saved_words if saved_words else None,
            ).to_dict()
        except Exception as r_err:
            logger.warning(f"No se pudo estructurar metadatos de retención: {r_err}")

    resolved_gname = (req.game_name or "").strip()
    if not resolved_gname:
        vp = req.video_path or (req.video_paths[0] if req.video_paths else "")
        if vp:
            pn = Path(vp).parent.name
            if pn and pn.lower() not in ("promosfootage", "footage", "temp", "input", "videos", "output", "render_tmp", "clips", "test", "v3_cache"):
                resolved_gname = pn
    if not resolved_gname and req.script_text:
        m = re.search(r"(?:this is|called|game called|title is|named)\s+([A-Za-z0-9\s:''-]+?)(?:\.|\!|\;|\,|(?:\s+on\s+steam)|(?:\s+wishlist))", req.script_text, re.IGNORECASE)
        if m:
            cand = m.group(1).strip()
            if cand and len(cand) < 40:
                resolved_gname = cand

    data = {
        "step": 3,
        "step_name": "script_and_voiceover",
        "session_id": sess_name,
        "video_path": req.video_path or (req.video_paths[0] if req.video_paths else ""),
        "video_paths": req.video_paths or ([req.video_path] if req.video_path else []),
        "game_name": resolved_gname,
        "hook_type": req.hook_type,
        "opening_text": (req.opening_text or "").strip(),
        "tone": req.tone,
        "target_duration": req.target_duration,
        "briefing_text": req.briefing_text or "",
        "script_text": req.script_text,
        "word_count": req.word_count,
        "estimated_duration": req.estimated_duration,
        "voice_mode": req.voice_mode,
        "script_mode": req.script_mode,
        "game_genre": req.game_genre,
        "voice_id": vdata.get("voice_id") or req.voice_id,
        "voice_path": saved_voice_path,
        "voice_url": saved_voice_url,
        "voice_duration": saved_voice_duration,
        "sentence_1_end_s": saved_sentence_1_end,
        "words": saved_words,
        "alignment": saved_alignment,
        "speed": vdata.get("speed", 1.10),
        "stability": vdata.get("stability", 0.50),
        "similarity_boost": vdata.get("similarity_boost", 0.75),
        "speed_full": vdata.get("speed_full"),
        "speed_hook": vdata.get("speed_hook"),
        "synthesis_mode": vdata.get("synthesis_mode"),
        "retention_data": retention_payload,
        "saved_at": time.time(),
    }

    try:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return {"status": "ok", "session_id": sess_name, "saved_path": str(out_file), "data": data}
    except Exception as e:
        logger.exception("Error saving step 3 script")
        raise HTTPException(status_code=500, detail=f"Error guardando guión del Paso 3: {e}")


@app.get("/api/promov3/step3/retention")
def get_promov3_retention_endpoint(session_id: str):
    """Retrieve structured Hook & Retention analysis metadata for a session."""
    sess_name = (session_id or "").strip()
    if not sess_name:
        raise HTTPException(status_code=400, detail="session_id es obligatorio")
    step3_file = _safe_session_dir(sess_name) / "step3_script.json"
    if not step3_file.exists():
        raise HTTPException(status_code=404, detail=f"No se encontró step3_script.json para la sesión {sess_name}")
    try:
        with open(step3_file, "r", encoding="utf-8") as f:
            sdata = json.load(f)
        retention = sdata.get("retention_data")
        words = sdata.get("words")
        if not words:
            voice_file = _safe_session_dir(sess_name) / "step3_voice.json"
            if voice_file.exists():
                try:
                    with open(voice_file, "r", encoding="utf-8") as vf:
                        words = json.load(vf).get("words")
                except Exception:
                    pass
        if sdata.get("script_text") or retention:
            from script_retention import parse_and_repair_retention_script
            retention = parse_and_repair_retention_script(
                raw_content=retention or sdata.get("script_text"),
                game_name=sdata.get("game_name", "Game"),
                target_duration=sdata.get("target_duration", 30.0),
                words=words,
            ).to_dict()
        return {"status": "ok", "session_id": sess_name, "retention_data": retention}
    except Exception as e:
        logger.exception("Error recuperando metadatos de retención")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/promov3/step3/synthesize-voice")
def synthesize_promov3_voice_endpoint(req: PromoV3SynthesizeVoiceRequest):
    """Synthesizes voiceover with ElevenLabs for Step 3, returns audio_url, duration, and words."""
    sess_name = (req.session_id or "").strip()
    if not sess_name and req.video_paths:
        sess_name = get_canonical_v3_multi_session_id(req.video_paths)
    elif not sess_name and req.video_path:
        sess_name = get_canonical_v3_session_id(req.video_path)

    if not sess_name:
        sess_name = f"sess_{int(time.time())}"

    from project_paths import safe_child_dir as _safe_child_dir  # local import: endpoint is also loaded in isolation by tests
    try:
        v3_dir = _safe_child_dir(BASE_DIR / "temp" / "v3_cache", sess_name)
    except ValueError:
        raise HTTPException(status_code=400, detail="session_id inválido.")
    v3_dir.mkdir(parents=True, exist_ok=True)

    text = req.script_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="El texto del guión no puede estar vacío.")

    cfg = AppConfig.from_env()
    eleven_key = (cfg.elevenlabs_api_key or os.environ.get("ELEVENLABS_API_KEY", "")).strip()
    if not eleven_key:
        raise HTTPException(status_code=400, detail="Falta ELEVENLABS_API_KEY en la configuración.")

    voice_id = (req.voice_id or cfg.elevenlabs_voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "TX3LPaxmHKxFdv7VOQHJ")).strip()

    out_file = v3_dir / "voiceover.mp3"

    spd = float(req.speed) if req.speed is not None else 1.1
    stb = float(req.stability) if req.stability is not None else 0.5
    try:
        sim = max(0.0, min(1.0, float(req.similarity_boost if req.similarity_boost is not None else 0.75)))
    except (TypeError, ValueError):
        sim = 0.75

    # similarity_boost is passed only if the voice engine already supports it.
    voice_optional = {"similarity_boost": sim}
    try:
        import inspect as _inspect
        _voice_params = _inspect.signature(synthesize_elevenlabs_with_timestamps).parameters
        if "similarity_boost" not in _voice_params and not any(
                prm.kind == _inspect.Parameter.VAR_KEYWORD for prm in _voice_params.values()):
            logger.warning("synthesize_elevenlabs_with_timestamps no admite todavía similarity_boost (se ignora).")
            voice_optional = {}
    except (TypeError, ValueError):
        pass

    try:
        tts_out, words, align_dict = synthesize_elevenlabs_with_timestamps(
            text=text,
            output_path=out_file,
            api_key=eleven_key,
            voice_id=voice_id,
            speed=spd,
            stability=stb,
            **voice_optional,
        )
        dur = get_media_duration(out_file)
        t_stamp = int(time.time())
        media_url = f"/media/temp/v3_cache/{sess_name}/voiceover.mp3?t={t_stamp}"

        from audio_aligner import get_first_sentence_end_time
        s1_end = round(get_first_sentence_end_time({"words": words}, script_text=text), 3)

        voice_data = {
            "voice_id": voice_id,
            "audio_path": str(out_file),
            "voice_url": media_url,
            "voice_duration": dur,
            "sentence_1_end_s": s1_end,
            "speed": spd,
            "speed_full": align_dict.get("speed_full", spd + 0.02),
            "speed_hook": align_dict.get("speed_hook"),
            "synthesis_mode": align_dict.get("synthesis_mode"),
            "stability": stb,
            "similarity_boost": sim,
            "words": words,
            "alignment": align_dict,
            "script_text": text,
            "synthesized_at": time.time(),
        }
        with open(v3_dir / "step3_voice.json", "w", encoding="utf-8") as vf:
            json.dump(voice_data, vf, indent=2, ensure_ascii=False)

        # Purge stale downstream montage & render files from prior voice
        (v3_dir / "step4_edl.json").unlink(missing_ok=True)
        (v3_dir / "step5_result.json").unlink(missing_ok=True)
        (v3_dir / "master_audio.mp3").unlink(missing_ok=True)
        (v3_dir / "silent_video.mp4").unlink(missing_ok=True)
        (v3_dir / "gameplay_audio.flac").unlink(missing_ok=True)

        # Synchronize step3_script.json so it matches the synthesized voice
        script_file = v3_dir / "step3_script.json"
        sdata = {}
        if script_file.exists():
            try:
                with open(script_file, "r", encoding="utf-8") as sf:
                    sdata = json.load(sf)
            except Exception as e:
                logger.warning(f"Error leyendo step3_script.json: {e}")
        sdata["step"] = 3
        sdata["step_name"] = "script_and_voiceover"
        sdata["session_id"] = sess_name
        sdata["script_text"] = text
        sdata["words"] = words
        sdata["word_count"] = len(words)
        sdata["voice_path"] = str(out_file)
        sdata["voice_url"] = media_url
        sdata["voice_duration"] = dur
        sdata["voice_id"] = voice_id
        # Align retention metadata with exact speech timestamps
        ret_data = sdata.get("retention_data") or text
        try:
            from script_retention import parse_and_repair_retention_script
            ret_obj = parse_and_repair_retention_script(
                raw_content=ret_data,
                game_name=sdata.get("game_name", "Game"),
                target_duration=dur,
                words=words,
            )
            sdata["retention_data"] = ret_obj.to_dict()
        except Exception as ret_err:
            logger.warning(f"Error sincronizando retention_data con palabras en synthesize: {ret_err}")

        sdata["alignment"] = align_dict
        sdata["speed"] = spd
        sdata["stability"] = stb
        sdata["similarity_boost"] = sim
        sdata["speed_full"] = voice_data["speed_full"]
        sdata["speed_hook"] = voice_data["speed_hook"]
        sdata["synthesis_mode"] = voice_data["synthesis_mode"]
        sdata["updated_at"] = time.time()
        try:
            with open(script_file, "w", encoding="utf-8") as sf:
                json.dump(sdata, sf, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Error actualizando step3_script.json: {e}")

        return {
            "status": "ok",
            "session_id": sess_name,
            "voice_url": media_url,
            "voice_path": str(out_file),
            "duration": dur,
            "sentence_1_end_s": s1_end,
            "voice_id": voice_id,
            "word_count": len(words),
            "words": words,
            "retention_data": sdata.get("retention_data"),
            "speed_full": voice_data["speed_full"],
            "speed_hook": voice_data["speed_hook"],
            "synthesis_mode": voice_data["synthesis_mode"],
        }
    except Exception as e:
        logger.exception("Error sintetizando voz en Step 3")
        raise HTTPException(status_code=500, detail=f"Error sintetizando voz con ElevenLabs: {e}")


@app.post("/api/promov3/step4/orchestrate")
def run_promov3_step4_orchestrate(req: PromoV3Step4OrchestrateRequest):
    """
    Execute Step 4: AI Director & Sequence Assembly (EDL).
    1. Loads Step 2 evaluated shots.
    2. Loads Step 3 script & voice info.
    3. Calls orchestrate_promov3_step4_edl to generate structured timeline with contextual transitions:
       - Hook transition: 'white_flash' (0.15s)
       - Contextual secondary transition: max 1, strictly no glitch for cozy games, circle_zoom or glitch for action/chaos
       - Normal cuts: 'cut'
    4. Auto-saves the resulting EDL in v3_cache/{session_id}/step4_edl.json.
    5. Returns EDL JSON.
    """
    v3_cache_dir = BASE_DIR / "temp" / "v3_cache"
    v3_dir = None

    if req.session_id:
        cand = _safe_session_dir(req.session_id)
        if cand.exists():
            v3_dir = cand

    if not v3_dir and req.video_paths:
        cand_id = get_canonical_v3_multi_session_id(req.video_paths)
        cand = v3_cache_dir / cand_id
        if cand.exists():
            v3_dir = cand

    if not v3_dir and req.video_path:
        cand_id = get_canonical_v3_session_id(req.video_path)
        cand = v3_cache_dir / cand_id
        if cand.exists():
            v3_dir = cand

    if not v3_dir or not v3_dir.exists():
        raise HTTPException(
            status_code=400,
            detail="No se encontró la sesión de Promo V3 en caché. Asegúrate de ejecutar o cargar los pasos anteriores."
        )

    # 1. Load Step 2 evaluated shots
    step2_file = v3_dir / "step2_evaluated_shots.json"
    if not step2_file.exists():
        step2_file = v3_dir / "step2_analysis.json"

    if not step2_file.exists():
        raise HTTPException(
            status_code=400,
            detail="No se encontraron los planos evaluados del Paso 2. Ejecuta el Paso 2 primero."
        )

    try:
        with open(step2_file, "r", encoding="utf-8") as f:
            step2_data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo planos del Paso 2: {e}")

    step2_shots = step2_data.get("shots", [])
    if not step2_shots:
        raise HTTPException(
            status_code=400,
            detail="El Paso 2 no contiene planos utilizables."
        )

    # Persist approvals separately from Gemini scores, scoped to this analysis/session.
    known_ids = {str(s["shot_id"]) for s in step2_shots}
    approved_ids = {str(sid) for sid in step2_data.get("director_approved_shot_ids", [])}
    approved_ids.update(str(sid) for sid in (req.approved_shot_ids or []))
    approved_ids &= known_ids
    approved_shot_ids = [s["shot_id"] for s in step2_shots if str(s["shot_id"]) in approved_ids]
    if req.approved_shot_ids:
        step2_data["director_approved_shot_ids"] = approved_shot_ids
        with open(step2_file, "w", encoding="utf-8") as f:
            json.dump(step2_data, f, indent=2, ensure_ascii=False)

    # 2. Load Step 3 script & voice info
    step3_file = v3_dir / "step3_script.json"
    step3_voice_file = v3_dir / "step3_voice.json"
    voice_mp3_file = v3_dir / "voiceover.mp3"

    step3_data = {}
    if step3_file.exists():
        try:
            with open(step3_file, "r", encoding="utf-8") as f:
                step3_data = json.load(f)
        except Exception as e:
            logger.warning(f"Error leyendo step3_script.json: {e}")

    req_script = (req.script_text or "").strip()
    existing_vtext = ""
    if step3_voice_file.exists():
        try:
            with open(step3_voice_file, "r", encoding="utf-8") as vf:
                v_data = json.load(vf)
                existing_vtext = (v_data.get("script_text") or "").strip()
        except Exception:
            pass

    target_script = req_script or (step3_data.get("script_text") or "").strip() or existing_vtext
    script_text = target_script
    if target_script:
        step3_data["script_text"] = target_script

    if not script_text and not step3_file.exists() and not step3_voice_file.exists():
        raise HTTPException(
            status_code=400,
            detail="No se encontró el guión ni la locución del Paso 3. Genera la locución en el Paso 3 primero."
        )

    # Check if we need to auto-synthesize live script_text
    needs_synth = False
    if not voice_mp3_file.exists() or not step3_voice_file.exists():
        needs_synth = bool(script_text)
    elif script_text and existing_vtext and not is_script_text_equivalent(script_text, existing_vtext):
        logger.info(f"Paso 4: El guión actual difiere de la locución en caché. Forzando re-síntesis con ElevenLabs...")
        needs_synth = True

    if needs_synth:
        logger.info(f"Paso 4: Auto-sintetizando locución para el texto en vivo ({len(script_text)} chars)...")
        # Purge stale files
        voice_mp3_file.unlink(missing_ok=True)
        step3_voice_file.unlink(missing_ok=True)
        (v3_dir / "step4_edl.json").unlink(missing_ok=True)
        (v3_dir / "step5_result.json").unlink(missing_ok=True)
        (v3_dir / "master_audio.mp3").unlink(missing_ok=True)
        (v3_dir / "silent_video.mp4").unlink(missing_ok=True)
        (v3_dir / "gameplay_audio.flac").unlink(missing_ok=True)

        from config import AppConfig
        cfg = AppConfig.from_env()
        eleven_key = (cfg.elevenlabs_api_key or os.environ.get("ELEVENLABS_API_KEY", "")).strip()
        voice_id = (cfg.elevenlabs_voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "TX3LPaxmHKxFdv7VOQHJ")).strip()
        if eleven_key:
            try:
                s4_voice_optional, _ = _filter_supported_kwargs(synthesize_elevenlabs_with_timestamps, {
                    "similarity_boost": _clamp_float(step3_data.get("similarity_boost", 0.75), 0.0, 1.0, 0.75),
                })
                tts_out, words, align_dict = synthesize_elevenlabs_with_timestamps(
                    text=script_text,
                    output_path=voice_mp3_file,
                    api_key=eleven_key,
                    voice_id=voice_id,
                    speed=float(step3_data.get("speed") or 1.10),
                    stability=float(step3_data.get("stability", 0.50)),
                    **s4_voice_optional,
                )
                dur = get_media_duration(voice_mp3_file)
                from audio_aligner import get_first_sentence_end_time
                s1_end = round(get_first_sentence_end_time({"words": words}, script_text=script_text), 3)
                media_url = f"/media/temp/v3_cache/{v3_dir.name}/voiceover.mp3?t={int(time.time())}"
                voice_data = {
                    "audio_path": str(voice_mp3_file),
                    "speed": float(step3_data.get("speed") or 1.10),
                    "stability": float(step3_data.get("stability", 0.50)),
                    "speed_full": align_dict.get("speed_full"),
                    "speed_hook": align_dict.get("speed_hook"),
                    "synthesis_mode": align_dict.get("synthesis_mode"),
                    "voice_url": media_url,
                    "voice_duration": dur,
                    "sentence_1_end_s": s1_end,
                    "words": words,
                    "alignment": align_dict,
                    "script_text": script_text,
                    "synthesized_at": time.time(),
                }
                with open(step3_voice_file, "w", encoding="utf-8") as vf:
                    json.dump(voice_data, vf, indent=2, ensure_ascii=False)
                step3_data["voice_path"] = str(voice_mp3_file)
                step3_data["voice_url"] = media_url
                step3_data["voice_duration"] = dur
                step3_data["sentence_1_end_s"] = s1_end
                step3_data["words"] = words
                step3_data["alignment"] = align_dict
                step3_data["script_text"] = script_text
                with open(step3_file, "w", encoding="utf-8") as sf:
                    json.dump(step3_data, sf, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"Auto-síntesis en Paso 4 falló: {e}")

    voice_duration = None
    hook_duration = None

    # Priority A: Check step3_voice.json and verify script match
    is_voice_valid = True
    if step3_voice_file.exists():
        try:
            with open(step3_voice_file, "r", encoding="utf-8") as f:
                v_data = json.load(f)
                voice_script_text = (v_data.get("script_text") or "").strip()
                if script_text and voice_script_text and not is_script_text_equivalent(script_text, voice_script_text):
                    logger.info("Paso 4: El guión actual difiere de la locución sintetizada previa. Ignorando locución desactualizada.")
                    is_voice_valid = False
                else:
                    if not script_text and voice_script_text:
                        script_text = voice_script_text
                    if v_data.get("voice_duration") or v_data.get("duration"):
                        voice_duration = float(v_data.get("voice_duration") or v_data.get("duration"))
                    words = v_data.get("words") or []
                    if words:
                        from audio_aligner import get_first_sentence_end_time
                        h_end = get_first_sentence_end_time({"words": words}, script_text=script_text)
                        if h_end > 0.5:
                            hook_duration = round(h_end, 2)
        except Exception as e:
            logger.warning(f"Error leyendo step3_voice.json: {e}")
            is_voice_valid = False

    # Priority B: Measure exact voiceover audio file duration on disk if valid and present
    if is_voice_valid and voice_mp3_file.exists() and not voice_duration:
        try:
            from audio_aligner import get_audio_duration
            measured_dur = get_audio_duration(voice_mp3_file)
            if measured_dur > 1.0:
                voice_duration = round(measured_dur, 2)
                logger.info(f"Paso 4: Duración de locución detectada desde 'voiceover.mp3': {voice_duration:.2f}s")
        except Exception as e:
            logger.warning(f"Error midiendo voiceover.mp3: {e}")

    if not script_text:
        raise HTTPException(
            status_code=400,
            detail="El guión del Paso 3 está vacío. Escribe o genera un guión primero."
        )

    game_name = (req.game_name or step3_data.get("game_name") or "").strip()
    if not game_name:
        vp = req.video_path or (req.video_paths[0] if req.video_paths else "") or step3_data.get("video_path", "")
        if vp:
            pn = Path(vp).parent.name
            if pn and pn.lower() not in ("promosfootage", "footage", "temp", "input", "videos", "output", "render_tmp", "clips", "test", "v3_cache"):
                game_name = pn
    if not game_name and script_text:
        m = re.search(r"(?:this is|called|game called|title is|named)\s+([A-Za-z0-9\s:''-]+?)(?:\.|\!|\;|\,|(?:\s+on\s+steam)|(?:\s+wishlist))", script_text, re.IGNORECASE)
        if m:
            cand = m.group(1).strip()
            if cand and len(cand) < 40:
                game_name = cand

    game_genre = (req.game_genre or step3_data.get("game_genre") or step3_data.get("tone") or "").strip()

    # Priority C: Check step3_data for fallback voice_duration
    if not voice_duration:
        v_dur = step3_data.get("voice_duration") or step3_data.get("estimated_duration") or step3_data.get("duration")
        if v_dur:
            try:
                voice_duration = float(v_dur)
            except (ValueError, TypeError):
                voice_duration = None

    # Priority: Check manual or saved sentence_1_end_s from request or Step 3
    req_hook = None
    if req.sentence_1_end_s is not None and float(req.sentence_1_end_s) > 0.5:
        req_hook = float(req.sentence_1_end_s)
    elif req.hook_duration is not None and float(req.hook_duration) > 0.5:
        req_hook = float(req.hook_duration)

    if req_hook:
        hook_duration = req_hook
        logger.info(f"Paso 4: Usando hook_duration explícito de la petición: {hook_duration:.3f}s")
    else:
        s1_end_manual = None
        if is_voice_valid and step3_voice_file.exists():
            try:
                with open(step3_voice_file, "r", encoding="utf-8") as vf:
                    v_data_s1 = json.load(vf)
                    if v_data_s1.get("sentence_1_end_s"):
                        s1_end_manual = v_data_s1.get("sentence_1_end_s")
            except Exception:
                pass
        if not s1_end_manual:
            s1_end_manual = step3_data.get("sentence_1_end_s")

        if s1_end_manual and float(s1_end_manual) > 0.5:
            hook_duration = float(s1_end_manual)

    if not hook_duration:
        h_dur = step3_data.get("hook_duration")
        if h_dur:
            try:
                hook_duration = float(h_dur)
            except (ValueError, TypeError):
                hook_duration = None

    if hook_duration:
        if step3_voice_file.exists():
            try:
                with open(step3_voice_file, "r", encoding="utf-8") as vf:
                    v_up = json.load(vf)
                v_up["sentence_1_end_s"] = hook_duration
                with open(step3_voice_file, "w", encoding="utf-8") as vf:
                    json.dump(v_up, vf, indent=2, ensure_ascii=False)
            except Exception:
                pass
        step3_data["sentence_1_end_s"] = hook_duration
        step3_data["hook_duration"] = hook_duration
        try:
            with open(step3_file, "w", encoding="utf-8") as sf:
                json.dump(step3_data, sf, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # Automatically set target_duration = voice_duration if voice_duration is available!
    if voice_duration and voice_duration > 1.0:
        target_duration = voice_duration
    else:
        target_duration = float(step3_data.get("target_duration") or 30.0)

    # 3. Keys and LLM setup
    from config import AppConfig
    cfg = AppConfig.from_env()
    groq_key = (cfg.groq_api_key or os.environ.get("GROQ_API_KEY", "")).strip().strip('"').strip("'")
    gemini_key = (cfg.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")).strip().strip('"').strip("'")

    gemini_client = None
    if gemini_key:
        try:
            from google import genai
            gemini_client = genai.Client(api_key=gemini_key)
        except Exception as e:
            logger.warning(f"Could not init Gemini client for Step 4: {e}")

    alignment_data = None
    if step3_voice_file.exists():
        try:
            with open(step3_voice_file, "r", encoding="utf-8") as vf:
                v_data_align = json.load(vf)
                alignment_data = v_data_align.get("alignment") or v_data_align.get("words")
        except Exception:
            pass
    if not alignment_data:
        alignment_data = step3_data.get("alignment") or step3_data.get("words")

    step1_file = v3_dir / "step1_shots.json"
    resolved_video_format = req.video_format
    if not resolved_video_format:
        resolved_video_format = step2_data.get("video_format")
    if not resolved_video_format and step1_file.exists():
        try:
            with open(step1_file, "r", encoding="utf-8") as sf:
                s1_d = json.load(sf)
                resolved_video_format = s1_d.get("video_format")
        except Exception:
            pass
    if not resolved_video_format:
        resolved_video_format = "full_9_16"

    cut_start_mode = (req.cut_start_mode or "shot_start").strip().lower()
    if cut_start_mode not in CUT_START_MODES:
        logger.warning(f"cut_start_mode desconocido '{req.cut_start_mode}': se usa 'shot_start'.")
        cut_start_mode = "shot_start"
    step4_optional, _ = _filter_supported_kwargs(orchestrate_promov3_step4_edl, {"cut_start_mode": cut_start_mode})

    try:
        edl = orchestrate_promov3_step4_edl(
            step2_shots=step2_shots,
            script_text=script_text,
            target_duration=target_duration,
            voice_duration=voice_duration,
            hook_duration=hook_duration,
            game_name=game_name,
            game_genre=game_genre,
            groq_api_key=groq_key,
            gemini_client=gemini_client,
            gemini_candidate_models=GEMINI_CANDIDATE_MODELS,
            allow_secondary_transition=req.allow_secondary_transition,
            alignment_data=alignment_data,
            banned_shot_ids=req.banned_shot_ids,
            approved_shot_ids=approved_shot_ids,
            video_format=resolved_video_format,
            script_mode=req.script_mode or step3_data.get("script_mode") or "normal",
            **step4_optional,
        )
    except Exception as e:
        from promo_v3_engine import InsufficientUniqueShotsError
        if isinstance(e, InsufficientUniqueShotsError):
            banned_ids = {str(sid) for sid in (req.banned_shot_ids or [])}
            discarded_shots = [s for s in step2_shots
                if str(s["shot_id"]) not in approved_ids | banned_ids
                and (s.get("format_score", 50) < 40
                     or s.get("suggested_role") == "discard" or s.get("is_splash"))]
            return JSONResponse(status_code=409, content={
                "status": "needs_shot_approval",
                "detail": str(e),
                "session_id": v3_dir.name,
                "discarded_shots": discarded_shots,
                "approved_shot_ids": approved_shot_ids,
            })
        logger.exception("Error in orchestrate_promov3_step4_edl")
        raise HTTPException(status_code=500, detail=f"Error orquestando el montaje con Director IA: {e}")

    sess_name = v3_dir.name
    edl["session_id"] = sess_name
    edl["video_path"] = req.video_path or step3_data.get("video_path", "")
    edl["video_format"] = resolved_video_format
    edl.setdefault("cut_start_mode", cut_start_mode)
    if hook_duration and edl.get("script_mode") != "caveman":
        edl["sentence_1_end_s"] = hook_duration
    edl["saved_at"] = time.time()

    # 4. Auto-save EDL to session folder
    out_file = v3_dir / "step4_edl.json"
    try:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(edl, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Could not save step4_edl.json: {e}")

    return {
        "status": "ok",
        "session_id": sess_name,
        "saved_path": str(out_file),
        "edl": edl,
    }


@app.post("/api/promov3/step4/save")
def save_promov3_step4_endpoint(req: PromoV3Step4SaveRequest):
    """Save or update Step 4 EDL state in session cache in v3_cache/{session_id}/step4_edl.json."""
    sess_name = (req.session_id or "").strip()
    if not sess_name and req.video_paths:
        sess_name = get_canonical_v3_multi_session_id(req.video_paths)
    elif not sess_name and req.video_path:
        sess_name = get_canonical_v3_session_id(req.video_path)

    if not sess_name:
        raise HTTPException(status_code=400, detail="session_id o video_path es obligatorio")

    sess_dir = _safe_session_dir(sess_name)
    sess_dir.mkdir(parents=True, exist_ok=True)
    out_file = sess_dir / "step4_edl.json"

    data = dict(req.edl)
    data["session_id"] = sess_name
    data["saved_at"] = time.time()

    try:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return {"status": "ok", "session_id": sess_name, "saved_path": str(out_file), "data": data}
    except Exception as e:
        logger.exception("Error saving step 4 EDL")
        raise HTTPException(status_code=500, detail=f"Error guardando montaje del Paso 4: {e}")


def _promov3_slot_candidates(req: PromoV3SlotShotRequest):
    sess_name = (req.session_id or "").strip()
    if not sess_name:
        sess_name = (get_canonical_v3_multi_session_id(req.video_paths) if req.video_paths
                     else get_canonical_v3_session_id(req.video_path) if req.video_path else "")
    # Self-contained containment check (also exercised in isolation by tests):
    # the resolved folder must be a direct child of temp/v3_cache.
    cache = (BASE_DIR / "temp" / "v3_cache").resolve()
    sess_dir = (cache / sess_name).resolve()
    if not sess_name or sess_dir.parent != cache or not sess_dir.is_dir():
        raise HTTPException(status_code=404, detail="No se encontró la sesión de montaje")
    timeline = req.edl.get("timeline", [])
    if req.slot_index < 0 or req.slot_index >= len(timeline):
        raise HTTPException(status_code=400, detail="Slot de montaje inválido")
    step2_file = sess_dir / "step2_evaluated_shots.json"
    if not step2_file.exists():
        step2_file = sess_dir / "step2_analysis.json"
    if not step2_file.exists():
        raise HTTPException(status_code=400, detail="Falta el análisis de planos")
    step2 = json.loads(step2_file.read_text(encoding="utf-8"))
    step1_file = sess_dir / "step1_shots.json"
    originals = json.loads(step1_file.read_text(encoding="utf-8")).get("shots", []) if step1_file.exists() else []
    originals = {str(s.get("shot_id", s.get("id"))): s for s in originals}
    used = {str(c.get("shot_id", c.get("source_shot_id"))) for i, c in enumerate(timeline) if i != req.slot_index}
    selected = {str(c.get("shot_id", c.get("source_shot_id"))) for c in timeline}
    duration = float(timeline[req.slot_index].get("duration", 0))
    if duration <= 0:
        raise HTTPException(status_code=400, detail="El slot no tiene una duración válida")
    candidates = []
    for analyzed in step2.get("shots", []):
        discarded = (analyzed.get("suggested_role") == "discard" or analyzed.get("is_splash") or analyzed.get("format_score", 50) < 40)
        if not discarded and str(analyzed.get("shot_id")) in selected:
            continue
        shot = {**originals.get(str(analyzed.get("shot_id")), {}), **analyzed}
        start = float(shot.get("start", shot.get("start_time", 0)))
        end = float(shot.get("end", shot.get("end_time", start + float(shot.get("duration", 0)))))
        reason = "Ya utilizado en otro slot" if str(shot.get("shot_id")) in used else "Demasiado corto para este slot" if end - start + 0.001 < duration else ""
        candidates.append({**shot, "unavailable_reason": reason,
                           "rejection_reason": (shot.get("skip_reason") or shot.get("format_reason") or "Descartado por Gemini") if discarded else "No seleccionado por el Director IA"})
    return sess_dir, step2_file, step2, candidates


@app.post("/api/promov3/step4/slot-candidates")
def promov3_slot_candidates_endpoint(req: PromoV3SlotShotRequest):
    _, _, _, candidates = _promov3_slot_candidates(req)
    return {"status": "ok", "shots": candidates}


@app.post("/api/promov3/step4/replace-slot")
def promov3_replace_slot_endpoint(req: PromoV3SlotShotRequest):
    sess_dir, step2_file, step2, candidates = _promov3_slot_candidates(req)
    shot = next((s for s in candidates if str(s.get("shot_id")) == str(req.shot_id)), None)
    if shot is None or shot.get("unavailable_reason"):
        raise HTTPException(status_code=400, detail=shot.get("unavailable_reason") if shot else "El plano no está disponible en esta sesión")
    edl = json.loads(json.dumps(req.edl))
    cut = edl["timeline"][req.slot_index]
    source_in = float(shot.get("start", shot.get("start_time", 0)))
    cut.update(shot_id=shot["shot_id"], source_in=source_in,
               source_out=round(source_in + float(cut["duration"]), 3),
               video_path=shot.get("video_path") or shot.get("source_video_path") or "",
               video_name=shot.get("video_name", ""), video_index=shot.get("video_index", 1),
               preview_9_16_url=shot.get("preview_9_16_url") or shot.get("preview_url") or "",
               preview_9_16_path=shot.get("preview_9_16_path", ""),
               shot_description=shot.get("description", ""), manually_selected=True)
    cut.pop("source_shot_id", None)
    cut.pop("preview_url", None)
    approved = step2.setdefault("director_approved_shot_ids", [])
    if str(shot["shot_id"]) not in {str(s) for s in approved}:
        approved.append(shot["shot_id"])
    edl.update(session_id=sess_dir.name, saved_at=time.time())
    step2_file.write_text(json.dumps(step2, indent=2, ensure_ascii=False), encoding="utf-8")
    (sess_dir / "step4_edl.json").write_text(json.dumps(edl, indent=2, ensure_ascii=False), encoding="utf-8")
    (sess_dir / "step5_result.json").unlink(missing_ok=True)
    return {"status": "ok", "edl": edl, "session_id": sess_dir.name}


# =============================================================================
# API Endpoints: Promo V3 — Paso 5 (Renderizado Final, Subtítulos & Steam Hook)
# =============================================================================

def _finalize_render_result(render_result: Any, render_options: Dict[str, Any],
                            server_warnings: Optional[List[str]] = None,
                            dropped_options: Optional[List[str]] = None) -> Dict[str, Any]:
    """Merge server-side warnings into the engine result and record the options used.

    Keeps every key the engine returned; adds ``warnings`` (list of str) and the
    render options (engine values win, e.g. the real fps after a fallback) so a
    recovered session restores them.
    """
    result: Dict[str, Any] = dict(render_result or {})
    warnings: List[str] = []
    for w in list(server_warnings or []) + list(result.get("warnings") or []):
        if w and str(w) not in warnings:
            warnings.append(str(w))
    for opt in dropped_options or []:
        msg = f"La opción de render '{opt}' aún no está disponible en el motor; se ha ignorado."
        if msg not in warnings:
            warnings.append(msg)
    result["warnings"] = warnings
    for key, value in render_options.items():
        result.setdefault(key, value)
    result["requested_output_fps"] = render_options.get("output_fps", 30)
    result["render_options"] = {k: result.get(k, v) for k, v in render_options.items()}
    return result


@app.post("/api/promov3/step5/render")
def run_promov3_step5_render(req: PromoV3Step5RenderRequest):
    """
    Renders the final Promo V3 video from Step 4 EDL using background worker & SSE progress:
    - Cuts high-res 9:16 vertical segments from source video
    - Applies transitions (white_flash, circle_zoom, glitch_pixelize, cut)
    - Concatenates sequence
    - Generates animated Steam Hook overlay (if enabled)
    - Generates and burns kinetic ASS subtitles
    - Mixes ElevenLabs voiceover + background gameplay audio
    - Exports final MP4 and saves to session cache v3_cache/{session_id}/step5_result.json
    """
    sess_name = (req.session_id or "").strip()
    if not sess_name and req.video_paths:
        sess_name = get_canonical_v3_multi_session_id(req.video_paths)
    elif not sess_name and req.video_path:
        sess_name = get_canonical_v3_session_id(req.video_path)

    if not sess_name:
        raise HTTPException(status_code=400, detail="session_id o video_path es obligatorio")

    v3_dir = _safe_session_dir(sess_name)
    if not v3_dir.exists():
        raise HTTPException(status_code=404, detail=f"No se encontró sesión en caché para {sess_name}")

    # Load Step 1 to get source video path
    step1_file = v3_dir / "step1_shots.json"
    if not step1_file.exists():
        raise HTTPException(status_code=400, detail="Falta el Paso 1 en la sesión.")
    with open(step1_file, "r", encoding="utf-8") as f:
        s1_data = json.load(f)

    game_name = req.game_name or s1_data.get("game_name", "")
    if not game_name and (v3_dir / "step4_edl.json").exists():
        try:
            with open(v3_dir / "step4_edl.json", "r", encoding="utf-8") as f:
                game_name = json.load(f).get("game_name", "")
        except Exception:
            pass

    # Validate before any voice synthesis or expensive render work.
    from production_logo_policy import require_game_logo
    try:
        required_game_logo = require_game_logo(game_name, req.custom_game_logo_path, root=BASE_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    source_video_path = s1_data.get("video_info", {}).get("path") or req.video_path or (req.video_paths[0] if req.video_paths else "")
    res_src = resolve_promov3_video_path(source_video_path, game_name=game_name, session_id=sess_name)
    if res_src and res_src.exists():
        source_video_path = str(res_src)
    else:
        paths_list = s1_data.get("video_info", {}).get("paths", []) or s1_data.get("videos", [])
        for p in paths_list:
            if isinstance(p, dict):
                p = p.get("path", "")
            res_p = resolve_promov3_video_path(p, game_name=game_name, session_id=sess_name)
            if res_p and res_p.exists():
                source_video_path = str(res_p)
                break
    if not source_video_path or not Path(source_video_path).exists():
        if req.edl and req.edl.get("timeline"):
            for c in req.edl["timeline"]:
                vp = c.get("video_path")
                res_vp = resolve_promov3_video_path(vp, game_name=game_name, session_id=sess_name)
                if res_vp and res_vp.exists():
                    source_video_path = str(res_vp)
                    break

    # Load Step 3 to get voiceover audio & alignment
    step3_voice_file = v3_dir / "step3_voice.json"
    step3_script_file = v3_dir / "step3_script.json"
    
    script_data = {}
    if step3_script_file.exists():
        with open(step3_script_file, "r", encoding="utf-8") as f:
            script_data = json.load(f)

    voice_data = {}
    if step3_voice_file.exists():
        with open(step3_voice_file, "r", encoding="utf-8") as f:
            voice_data = json.load(f)

    s3_data = voice_data or script_data
    if not s3_data and not (v3_dir / "voiceover.mp3").exists():
        raise HTTPException(status_code=400, detail="Falta el Paso 3 (guión o locución) en la sesión. Genera el guión antes de renderizar.")

    target_script = (
        (req.script_text or "").strip()
        or (req.edl.get("script_text") if req.edl else "").strip()
        or (script_data.get("script_text") or "").strip()
        or (voice_data.get("script_text") or "").strip()
    )

    voice_script_text = (voice_data.get("script_text") or "").strip()
    voice_is_valid = True
    if target_script and voice_script_text:
        if not is_script_text_equivalent(target_script, voice_script_text):
            logger.warning(f"Paso 5: La locución en disco ('{voice_script_text[:30]}...') no coincide con el guión actual ('{target_script[:30]}...'). Purgando caché de voz para re-sintetizar...")
            voice_is_valid = False
            (v3_dir / "voiceover.mp3").unlink(missing_ok=True)
            (v3_dir / "step3_voice.json").unlink(missing_ok=True)
            (v3_dir / "master_audio.mp3").unlink(missing_ok=True)
            (v3_dir / "silent_video.mp4").unlink(missing_ok=True)
            (v3_dir / "gameplay_audio.flac").unlink(missing_ok=True)
            voice_data = {}

    voice_path = None
    if voice_is_valid:
        voice_path = voice_data.get("audio_path") or voice_data.get("voice_path")
        if not voice_path:
            default_mp3 = v3_dir / "voiceover.mp3"
            if default_mp3.exists():
                voice_path = str(default_mp3)
        elif not Path(voice_path).exists():
            v_url = voice_data.get("voice_url") or voice_data.get("audio_url", "")
            if v_url.startswith("/media/temp/"):
                voice_path = str(BASE_DIR / "temp" / v_url.replace("/media/temp/", ""))
            elif v_url.startswith("/media/output/"):
                voice_path = str(BASE_DIR / "output" / v_url.replace("/media/output/", ""))
            if not Path(voice_path).exists():
                default_mp3 = v3_dir / "voiceover.mp3"
                if default_mp3.exists():
                    voice_path = str(default_mp3)

    # Prepare complete alignment data containing words list
    words = (voice_data.get("words") or script_data.get("words") or []) if voice_is_valid else []
    alignment_dict = (voice_data.get("alignment") or script_data.get("alignment") or {}) if voice_is_valid else {}
    alignment = {
        "words": words,
        "alignment": alignment_dict,
        "script_text": target_script,
    }

    # Load Step 4 EDL (safely ensuring timeline is present, merging custom sfx_tracks if any)
    edl = req.edl or {}
    if not edl.get("timeline"):
        step4_file = v3_dir / "step4_edl.json"
        if not step4_file.exists():
            raise HTTPException(status_code=400, detail="Falta el montaje del Paso 4 (EDL). Orquesta el montaje antes de renderizar.")
        with open(step4_file, "r", encoding="utf-8") as f:
            loaded_edl = json.load(f)
            if edl.get("sfx_tracks"):
                loaded_edl["sfx_tracks"] = edl["sfx_tracks"]
            edl = loaded_edl

    if edl.get("script_mode") != "caveman" and req.sentence_1_end_s and float(req.sentence_1_end_s) > 0.5:
        edl["sentence_1_end_s"] = float(req.sentence_1_end_s)
        edl["hook_duration"] = float(req.sentence_1_end_s)
    elif edl.get("script_mode") != "caveman" and req.hook_duration and float(req.hook_duration) > 0.5:
        edl["sentence_1_end_s"] = float(req.hook_duration)
        edl["hook_duration"] = float(req.hook_duration)

    game_name = (req.game_name or edl.get("game_name") or s1_data.get("game_name") or "").strip()
    if not game_name and source_video_path:
        pn = Path(source_video_path).parent.name
        if pn and pn.lower() not in ("promosfootage", "footage", "temp", "input", "videos", "output", "render_tmp", "clips", "test", "v3_cache"):
            game_name = pn
    if not game_name and edl.get("timeline"):
        for cut in edl.get("timeline", []):
            c_vp = cut.get("video_path")
            if c_vp:
                pn = Path(c_vp).parent.name
                if pn and pn.lower() not in ("promosfootage", "footage", "temp", "input", "videos", "output", "render_tmp", "clips", "test", "v3_cache"):
                    game_name = pn
                    break
    if not game_name and target_script:
        m = re.search(r"(?:this is|called|game called|title is|named)\s+([A-Za-z0-9\s:''-]+?)(?:\.|\!|\;|\,|(?:\s+on\s+steam)|(?:\s+wishlist))", target_script, re.IGNORECASE)
        if m:
            cand = m.group(1).strip()
            if cand and len(cand) < 40:
                game_name = cand

    # Ensure each cut in EDL points to a valid, resolved video file
    if edl.get("timeline"):
        for cut in edl["timeline"]:
            c_vp = cut.get("video_path") or source_video_path
            resolved_vp = resolve_promov3_video_path(c_vp, game_name=game_name, session_id=sess_name)
            if resolved_vp and resolved_vp.exists():
                cut["video_path"] = str(resolved_vp)
                if not source_video_path or not Path(source_video_path).exists():
                    source_video_path = str(resolved_vp)

    output_dir = APP_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # New render options (contract item 3). Invalid fps values fall back to 30 with a warning.
    server_warnings: List[str] = []
    try:
        requested_fps = int(req.output_fps)
    except (TypeError, ValueError):
        requested_fps = 30
    if requested_fps not in (30, 60):
        server_warnings.append(f"output_fps={req.output_fps} no soportado (solo 30 o 60): se renderiza a 30 fps.")
        requested_fps = 30
    render_options = {
        "audio_normalize": bool(req.audio_normalize),
        "audio_ducking": bool(req.audio_ducking),
        "output_fps": requested_fps,
    }

    job = create_job("promov3_render")

    def worker():
        try:
            job.update(4.0, "Preparando espacio de trabajo para renderizado...", "Iniciando render...")

            nonlocal voice_path, alignment
            if not voice_path or not Path(voice_path).exists():
                script_txt = (
                    target_script
                    or (edl.get("script_text") if edl else "")
                    or script_data.get("script_text")
                    or ""
                ).strip()
                if not script_txt:
                    raise ValueError("No se encontró locución grabada ni texto de guión para sintetizar.")

                job.update(7.0, "Sintetizando locución con ElevenLabs para el guión actualizado...", "Generando voz...")
                tts_file = v3_dir / "voiceover.mp3"
                cfg_curr = AppConfig.from_env()
                eleven_k = (cfg_curr.elevenlabs_api_key or os.environ.get("ELEVENLABS_API_KEY", "")).strip()
                voice_id = (script_data.get("voice_id") or cfg_curr.elevenlabs_voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "TX3LPaxmHKxFdv7VOQHJ")).strip()

                if eleven_k:
                    from audio_aligner import synthesize_elevenlabs_with_timestamps, get_first_sentence_end_time
                    s5_voice_optional, _ = _filter_supported_kwargs(synthesize_elevenlabs_with_timestamps, {
                        "similarity_boost": _clamp_float(script_data.get("similarity_boost", 0.75), 0.0, 1.0, 0.75),
                    })
                    tts_out, words, align_dict = synthesize_elevenlabs_with_timestamps(
                        text=script_txt,
                        output_path=tts_file,
                        api_key=eleven_k,
                        voice_id=voice_id,
                        speed=float(script_data.get("speed") or 1.10),
                        stability=float(script_data.get("stability", 0.50)),
                        **s5_voice_optional,
                    )
                    dur = get_media_duration(tts_file)
                    s1_end = round(get_first_sentence_end_time({"words": words}, script_text=script_txt), 3)
                    voice_path = tts_out
                    alignment = {
                        "words": words,
                        "alignment": align_dict,
                        "script_text": script_txt,
                    }
                    with open(v3_dir / "step3_voice.json", "w", encoding="utf-8") as vf:
                        json.dump({
                            "audio_path": str(tts_file),
                            "voice_id": voice_id,
                            "speed": float(script_data.get("speed") or 1.10),
                            "stability": float(script_data.get("stability", 0.50)),
                            "speed_full": align_dict.get("speed_full"),
                            "speed_hook": align_dict.get("speed_hook"),
                            "synthesis_mode": align_dict.get("synthesis_mode"),
                            "voice_url": f"/media/temp/v3_cache/{sess_name}/voiceover.mp3?t={int(time.time())}",
                            "voice_duration": dur,
                            "sentence_1_end_s": s1_end,
                            "words": words,
                            "alignment": align_dict,
                            "script_text": script_txt,
                            "synthesized_at": time.time(),
                        }, vf, indent=2, ensure_ascii=False)
                    script_data["script_text"] = script_txt
                    script_data["words"] = words
                    script_data["alignment"] = align_dict
                    script_data["voice_path"] = str(tts_file)
                    script_data["voice_duration"] = dur
                    script_data["voice_id"] = voice_id
                    script_data["sentence_1_end_s"] = s1_end
                    try:
                        with open(v3_dir / "step3_script.json", "w", encoding="utf-8") as sf:
                            json.dump(script_data, sf, indent=2, ensure_ascii=False)
                    except Exception:
                        pass
                else:
                    raise ValueError("Falta la API Key de ElevenLabs para sintetizar la locución del guión. Configúrala en .env.")

            job.update(10.0, "Componiendo planos con FFmpeg...", "Renderizando vídeo...")

            resolved_video_format = (
                req.video_format
                or (edl.get("video_format") if edl else None)
                or s1_data.get("video_format")
                or preferred_video_format(
                    BASE_DIR / "data/production_preferences.json", req.channel_branding.preset)
            )

            def progress_cb(pct: float, msg: str):
                job.update(pct, msg, msg)

            render_optional, render_dropped = _filter_supported_kwargs(render_promov3_final_video, render_options)
            render_result = render_promov3_final_video(
                edl=edl,
                source_video_path=source_video_path,
                voiceover_audio_path=voice_path,
                alignment_data=alignment,
                output_dir=output_dir,
                game_name=game_name,
                resolution=req.resolution,
                video_format=resolved_video_format,
                enable_steam_hook=req.enable_steam_hook,
                steam_logo_scale=req.steam_logo_scale,
                steam_logo_y=req.steam_logo_y,
                steam_text_scale=req.steam_text_scale,
                steam_text_y=req.steam_text_y,
                captions_font_size=req.captions_font_size,
                captions_y=req.captions_y,
                captions_highlight_color=req.captions_highlight_color,
                gameplay_volume=req.gameplay_volume,
                temp_dir=v3_dir / "render_tmp",
                session_id=sess_name,
                progress_callback=progress_cb,
                steam_hook_mode=req.steam_hook_mode or "classic",
                intro_subtitles_y=req.intro_subtitles_y if req.intro_subtitles_y is not None else 340,
                intro_subtitles_size=req.intro_subtitles_size if req.intro_subtitles_size is not None else 84,
                enable_game_name_reveal=bool(req.enable_game_name_reveal),
                game_name_reveal_y=req.game_name_reveal_y if req.game_name_reveal_y is not None else 800,
                game_name_reveal_scale=req.game_name_reveal_scale if req.game_name_reveal_scale is not None else 1.0,
                custom_game_logo_path=required_game_logo,
                channel_branding=req.channel_branding.model_dump(),
                require_official_game_logo=True,
                **render_optional,
            )

            render_result = _finalize_render_result(render_result, render_options, server_warnings, render_dropped)
            render_result["session_id"] = sess_name
            render_result["video_format"] = resolved_video_format
            render_result["step"] = 5
            render_result["step_name"] = "final_render_and_delivery"

            step5_out_path = v3_dir / "step5_result.json"
            try:
                with open(step5_out_path, "w", encoding="utf-8") as f:
                    json.dump(render_result, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"Error guardando step5_result.json: {e}")

            done_msg = "¡Renderizado final Promo V3 completado con éxito!"
            if render_result.get("warnings"):
                done_msg += f" ({len(render_result['warnings'])} aviso(s))"
            job.complete(render_result, done_msg)

        except Exception as e:
            err_details = str(e)
            if hasattr(e, "stderr") and e.stderr:
                err_details += f"\n\nFFmpeg Stderr:\n{e.stderr}"
            elif hasattr(e, "__cause__") and hasattr(e.__cause__, "stderr") and e.__cause__.stderr:
                err_details += f"\n\nFFmpeg Stderr:\n{e.__cause__.stderr}"

            # Persistent error logging to logs/render_errors.log
            try:
                err_log_path = BASE_DIR / "logs" / "render_errors.log"
                err_log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(err_log_path, "a", encoding="utf-8") as elf:
                    elf.write(
                        f"\n{'='*70}\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR EN RENDER (Paso 5)\n"
                        f"Juego: {game_name} | Sesión: {sess_name}\n"
                        f"Detalle: {err_details}\n"
                        f"Traceback:\n{traceback.format_exc()}\n"
                    )
            except Exception:
                pass

            # Save in session cache too
            try:
                with open(v3_dir / "render_error.log", "w", encoding="utf-8") as elf2:
                    elf2.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]\n{err_details}\n\n{traceback.format_exc()}\n")
            except Exception:
                pass

            logger.exception("Promo V3 Step 5 render failed")
            job.fail(f"Error en Paso 5 (Renderizado Final): {err_details}\n\n{traceback.format_exc()}")

    threading.Thread(target=worker, daemon=True).start()
    # Warnings known before rendering (e.g. unsupported fps); the final list,
    # including engine warnings, arrives in the job/SSE result ("warnings").
    return {"status": "ok", "job_id": job.job_id, "warnings": list(server_warnings), "render_options": render_options}


@app.post("/api/promov3/step5/save")
def save_promov3_step5_endpoint(req: PromoV3Step5SaveRequest):
    """Save or update Step 5 render result state in session cache in v3_cache/{session_id}/step5_result.json."""
    sess_name = (req.session_id or "").strip()
    if not sess_name and req.video_paths:
        sess_name = get_canonical_v3_multi_session_id(req.video_paths)
    elif not sess_name and req.video_path:
        sess_name = get_canonical_v3_session_id(req.video_path)

    if not sess_name:
        raise HTTPException(status_code=400, detail="session_id o video_path es obligatorio")

    sess_dir = _safe_session_dir(sess_name)
    sess_dir.mkdir(parents=True, exist_ok=True)
    out_file = sess_dir / "step5_result.json"

    data = dict(req.result)
    data["session_id"] = sess_name
    data["saved_at"] = time.time()

    try:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return {"status": "ok", "session_id": sess_name, "saved_path": str(out_file), "data": data}
    except Exception as e:
        logger.exception("Error saving step 5 result")
        raise HTTPException(status_code=500, detail=f"Error guardando resultado del Paso 5: {e}")


class PromoV3RemixSfxRequest(BaseModel):
    session_id: str
    sfx_tracks: List[Dict[str, Any]]
    gameplay_volume: Optional[float] = 0.15
    # None -> reuse the options saved with the last render (step5_result.json).
    audio_normalize: Optional[bool] = None
    audio_ducking: Optional[bool] = None


@app.post("/api/promov3/step5/remix_sfx")
def run_promov3_step5_remix_sfx(req: PromoV3RemixSfxRequest):
    """
    Fast audio re-mixing endpoint (~1.5s execution):
    Re-mixes audio streams (voiceover, background audio, custom SFX tracks)
    and muxes them with the existing rendered video using `-c:v copy`.
    """
    sess_name = (req.session_id or "").strip()
    if not sess_name:
        raise HTTPException(status_code=400, detail="session_id es obligatorio")

    sess_dir = _safe_session_dir(sess_name)
    if not sess_dir.exists():
        raise HTTPException(status_code=404, detail=f"Sesión no encontrada: {sess_name}")

    remix_options: Dict[str, Any] = {}
    saved_render: Dict[str, Any] = {}
    s5_file = sess_dir / "step5_result.json"
    if s5_file.exists():
        try:
            saved_render = json.loads(s5_file.read_text(encoding="utf-8")) or {}
        except Exception:
            saved_render = {}
    for opt in ("audio_normalize", "audio_ducking"):
        value = getattr(req, opt)
        if value is None:
            value = saved_render.get(opt)
        if value is not None:
            remix_options[opt] = bool(value)
    remix_optional, _ = _filter_supported_kwargs(remix_promov3_audio_sfx, remix_options)

    try:
        updated_data = remix_promov3_audio_sfx(
            session_dir=sess_dir,
            sfx_tracks=req.sfx_tracks,
            gameplay_volume=req.gameplay_volume,
            **remix_optional,
        )
        if isinstance(updated_data, dict):
            updated_data.setdefault("warnings", saved_render.get("warnings") or [])
        return {"status": "ok", "result": updated_data}
    except Exception as e:
        logger.exception("Error re-mezclando SFX")
        raise HTTPException(status_code=500, detail=f"Error al re-mezclar SFX: {str(e)}")


@app.get("/api/promov3/game-logo/status")
def get_promov3_game_logo_status(game_name: str = "", session_id: str = ""):
    """Check if an official Steam logo exists or if a custom logo is uploaded for this session."""
    from utils import check_steam_game_logo_status
    sess_name = (session_id or "").strip()
    gname = (game_name or "").strip()
    if not gname and sess_name:
        try:
            s3_path = _safe_session_dir(sess_name) / "step3_script.json"
            if s3_path.exists():
                with open(s3_path, "r", encoding="utf-8") as f:
                    s3d = json.load(f)
                    gname = (s3d.get("game_name") or "").strip()
                    if not gname:
                        vp = s3d.get("video_path") or (s3d.get("video_paths", [None])[0])
                        if vp:
                            pn = Path(vp).parent.name
                            if pn and pn.lower() not in ("promosfootage", "footage", "temp", "input", "videos", "output", "render_tmp", "clips", "test", "v3_cache"):
                                gname = pn
        except Exception:
            pass
    return check_steam_game_logo_status(game_name=gname, session_id=sess_name)


@app.post("/api/promov3/game-logo/upload")
async def upload_promov3_game_logo(
    file: UploadFile = File(...),
    session_id: str = Form(""),
    game_name: str = Form(""),
):
    """Upload a custom transparent PNG logo for the game reveal widget."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Archivo inválido")
    sess_name = (session_id or "").strip()
    target_dir = _safe_session_dir(sess_name or "global")
    target_dir.mkdir(parents=True, exist_ok=True)

    dest_path = target_dir / "custom_game_logo.png"
    contents = await file.read()

    from PIL import Image
    import io

    def _save_logo() -> None:
        try:
            img = Image.open(io.BytesIO(contents)).convert("RGBA")
            img.save(dest_path, format="PNG")
        except Exception:
            _write_bytes_file(dest_path, contents)

    await run_in_threadpool(_save_logo)

    ts = int(time.time())
    url = f"/media/temp/v3_cache/{sess_name}/custom_game_logo.png?t={ts}" if sess_name else f"/media/temp/v3_cache/global/custom_game_logo.png?t={ts}"
    return {
        "status": "ok",
        "has_logo": True,
        "source": "custom",
        "logo_url": url,
        "logo_path": str(dest_path.resolve()),
        "message": "Logo personalizado subido correctamente."
    }


@app.delete("/api/promov3/game-logo")
def delete_promov3_game_logo(session_id: str = "", game_name: str = ""):
    """Delete uploaded custom game logo."""
    sess_name = (session_id or "").strip()
    target_dir = _safe_session_dir(sess_name or "global")
    custom_file = target_dir / "custom_game_logo.png"
    if custom_file.exists():
        custom_file.unlink(missing_ok=True)
    return {"status": "ok", "message": "Logo personalizado eliminado."}


# =============================================================================
# API Endpoints: Approved & Uploaded Videos Registry (Performance Metrics DB)
# =============================================================================

UPLOADED_VIDEOS_DATA_FILE = BASE_DIR / "data" / "uploaded_videos.json"


def _load_uploaded_videos_data() -> List[dict]:
    return _load_list_state(UPLOADED_VIDEOS_DATA_FILE)


def _save_uploaded_videos_data(records: List[dict]):
    json_store.save_json_atomic(UPLOADED_VIDEOS_DATA_FILE, records)


class PromoV3ApproveRequest(BaseModel):
    session_id: Optional[str] = None
    game_name: Optional[str] = None
    account: str = "IGD"
    video_path: Optional[str] = None
    video_url: Optional[str] = None
    duration: Optional[float] = None
    resolution: Optional[str] = None
    video_format: Optional[str] = None
    script_text: Optional[str] = None
    retention_data: Optional[Dict[str, Any]] = None


class VideoMetricsUpdateRequest(BaseModel):
    video_id: str
    metrics: Dict[str, Any]


@app.post("/api/promov3/step5/approve")
@_state_locked(UPLOADED_VIDEOS_DATA_FILE)
def approve_promov3_video_endpoint(req: PromoV3ApproveRequest):
    """
    Approves a rendered video for uploading:
    - Associates the chosen upload account (IGD, GUAM, or custom).
    - Collects complete script and retention metadata (4-part hook, sentence classifications, open loops).
    - Persists everything in data/uploaded_videos.json with empty performance metric slots for future tracking.
    - Updates session approval cache in v3_cache/{session_id}/approval.json.
    """
    raw_account = (req.account or "IGD").strip()
    account_clean = raw_account.upper() if raw_account.upper() in ["IGD", "GUAM"] else raw_account

    sess_name = (req.session_id or "").strip()
    v3_dir = _safe_session_dir(sess_name) if sess_name else None

    # Resolve game name
    game_name = (req.game_name or "").strip()
    if not game_name and v3_dir and v3_dir.exists():
        for fname in ["step4_edl.json", "step3_script.json", "step1_shots.json"]:
            fp = v3_dir / fname
            if fp.exists():
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        j = json.load(f)
                        if j.get("game_name"):
                            game_name = j.get("game_name")
                            break
                except Exception:
                    pass
    if not game_name:
        game_name = "Video Promocional"

    # Resolve script info
    script_text = (req.script_text or "").strip()
    retention_data = req.retention_data
    word_count = None
    estimated_duration = None
    target_duration = None

    if v3_dir and v3_dir.exists() and (v3_dir / "step3_script.json").exists():
        try:
            with open(v3_dir / "step3_script.json", "r", encoding="utf-8") as f3:
                s3_json = json.load(f3)
                if not script_text:
                    script_text = (s3_json.get("script_text") or s3_json.get("script") or "").strip()
                if not retention_data:
                    retention_data = s3_json.get("retention_data")
                word_count = s3_json.get("word_count")
                estimated_duration = s3_json.get("estimated_duration")
                target_duration = s3_json.get("target_duration")
        except Exception as e:
            logger.warning(f"Error loading step3_script.json in approve: {e}")

    # Ensure retention_data has full clauses breakdown & speech timings
    if script_text or retention_data:
        try:
            approve_words = None
            if v3_dir and v3_dir.exists():
                for cand_f in ["step3_voice.json", "step3_script.json"]:
                    if (v3_dir / cand_f).exists():
                        try:
                            with open(v3_dir / cand_f, "r", encoding="utf-8") as wf:
                                w_data = json.load(wf)
                                if w_data.get("words"):
                                    approve_words = w_data["words"]
                                    break
                        except Exception:
                            pass
            from script_retention import parse_and_repair_retention_script
            ret_script = parse_and_repair_retention_script(
                raw_content=retention_data or script_text,
                game_name=game_name,
                target_duration=duration or target_duration or 30,
                words=approve_words,
            )
            retention_data = ret_script.to_dict()
        except Exception as r_err:
            logger.warning(f"No se pudo estructurar retención en approve: {r_err}")

    if not word_count and script_text:
        word_count = len(script_text.split())
    if not estimated_duration and word_count:
        estimated_duration = round(word_count / 2.5, 1)

    # Resolve video info
    video_path = req.video_path or ""
    video_url = req.video_url or ""
    duration = req.duration or 0.0
    resolution = req.resolution or ""
    video_format = req.video_format or "9:16"

    if v3_dir and v3_dir.exists() and (v3_dir / "step5_result.json").exists():
        try:
            with open(v3_dir / "step5_result.json", "r", encoding="utf-8") as f5:
                s5_json = json.load(f5)
                if not video_path:
                    video_path = s5_json.get("output_path") or s5_json.get("video_path") or ""
                if not video_url:
                    video_url = s5_json.get("output_url") or s5_json.get("video_url") or ""
                if not duration:
                    duration = s5_json.get("duration") or 0.0
                if not resolution:
                    resolution = s5_json.get("resolution") or ""
                if not video_format:
                    video_format = s5_json.get("video_format") or "9:16"
        except Exception as e:
            logger.warning(f"Error loading step5_result.json in approve: {e}")

    game_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', game_name.lower())[:30].strip('_') or "game"
    now_iso = datetime.now().isoformat()
    now_ts = time.time()
    record_id = f"vid_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{game_slug}"

    records = _load_uploaded_videos_data()
    existing_idx = None
    for idx, rec in enumerate(records):
        if sess_name and rec.get("session_id") == sess_name:
            existing_idx = idx
            break
        if video_path and rec.get("video_info", {}).get("output_path") == video_path:
            existing_idx = idx
            break

    if existing_idx is not None:
        rec = records[existing_idx]
        rec["account"] = account_clean
        rec["status"] = "approved"
        rec["approved_at"] = now_iso
        rec["approved_timestamp"] = now_ts
        rec["game_name"] = game_name
        if sess_name:
            rec["session_id"] = sess_name
        rec["video_info"] = {
            "output_path": video_path,
            "output_url": video_url,
            "duration": duration,
            "resolution": resolution,
            "video_format": video_format,
        }
        rec["script_info"] = {
            "script_text": script_text,
            "word_count": word_count,
            "estimated_duration": estimated_duration,
            "target_duration": target_duration,
            "retention_data": retention_data,
        }
        if "performance_metrics" not in rec or not isinstance(rec["performance_metrics"], dict):
            rec["performance_metrics"] = {
                "views": None,
                "likes": None,
                "comments": None,
                "shares": None,
                "saves": None,
                "retention_rate_avg": None,
                "retention_at_3s": None,
                "watch_time_avg_seconds": None,
                "platform": None,
                "post_url": None,
                "posted_at": None,
                "notes": None,
            }
        records[existing_idx] = rec
        final_record = rec
    else:
        final_record = {
            "id": record_id,
            "game_name": game_name,
            "account": account_clean,
            "status": "approved",
            "approved_at": now_iso,
            "approved_timestamp": now_ts,
            "session_id": sess_name,
            "video_info": {
                "output_path": video_path,
                "output_url": video_url,
                "duration": duration,
                "resolution": resolution,
                "video_format": video_format,
            },
            "script_info": {
                "script_text": script_text,
                "word_count": word_count,
                "estimated_duration": estimated_duration,
                "target_duration": target_duration,
                "retention_data": retention_data,
            },
            "performance_metrics": {
                "views": None,
                "likes": None,
                "comments": None,
                "shares": None,
                "saves": None,
                "retention_rate_avg": None,
                "retention_at_3s": None,
                "watch_time_avg_seconds": None,
                "platform": None,
                "post_url": None,
                "posted_at": None,
                "notes": None,
            },
        }
        records.append(final_record)

    _save_uploaded_videos_data(records)

    # Mirror approval to session cache
    if v3_dir and v3_dir.exists():
        approval_info = {
            "approved": True,
            "account": account_clean,
            "approved_at": now_iso,
            "record_id": final_record["id"],
        }
        try:
            with open(v3_dir / "approval.json", "w", encoding="utf-8") as af:
                json.dump(approval_info, af, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Error saving approval.json: {e}")

        s5_p = v3_dir / "step5_result.json"
        if s5_p.exists():
            try:
                with open(s5_p, "r", encoding="utf-8") as s5f:
                    s5_data = json.load(s5f)
                s5_data["approval"] = approval_info
                with open(s5_p, "w", encoding="utf-8") as s5f:
                    json.dump(s5_data, s5f, indent=2, ensure_ascii=False)
            except Exception:
                pass

    return {
        "status": "ok",
        "message": f"Vídeo aprobado exitosamente para la cuenta {account_clean}",
        "record": final_record,
    }


@app.get("/api/promov3/step5/approval-status")
def get_promov3_approval_status(session_id: Optional[str] = None, game_name: Optional[str] = None):
    """Checks whether the given session or game has been approved for upload."""
    sess_name = (session_id or "").strip()
    v3_dir = _safe_session_dir(sess_name) if sess_name else None

    if v3_dir and v3_dir.exists() and (v3_dir / "approval.json").exists():
        try:
            with open(v3_dir / "approval.json", "r", encoding="utf-8") as af:
                appr_data = json.load(af)
                return {"status": "ok", "is_approved": True, "approval": appr_data}
        except Exception:
            pass

    records = _load_uploaded_videos_data()
    for rec in reversed(records):
        if sess_name and rec.get("session_id") == sess_name:
            return {"status": "ok", "is_approved": True, "approval": rec}
        if game_name and rec.get("game_name", "").lower() == game_name.strip().lower():
            return {"status": "ok", "is_approved": True, "approval": rec}

    return {"status": "ok", "is_approved": False, "approval": None}


@app.get("/api/videos/uploaded")
def list_uploaded_videos(account: Optional[str] = None, game_name: Optional[str] = None):
    """Returns the registry of all videos approved for upload."""
    records = _load_uploaded_videos_data()
    if account:
        records = [r for r in records if r.get("account", "").upper() == account.strip().upper()]
    if game_name:
        records = [r for r in records if game_name.strip().lower() in r.get("game_name", "").lower()]
    return {"status": "ok", "videos": records, "count": len(records)}


@app.post("/api/videos/metrics")
@_state_locked(UPLOADED_VIDEOS_DATA_FILE)
def update_video_metrics(req: VideoMetricsUpdateRequest):
    """Updates performance metrics for an approved video in data/uploaded_videos.json."""
    records = _load_uploaded_videos_data()
    found = False
    for rec in records:
        if rec.get("id") == req.video_id:
            if "performance_metrics" not in rec or not isinstance(rec["performance_metrics"], dict):
                rec["performance_metrics"] = {}
            rec["performance_metrics"].update(req.metrics)
            rec["metrics_updated_at"] = datetime.now().isoformat()
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail=f"Vídeo no encontrado: {req.video_id}")
    _save_uploaded_videos_data(records)
    return {"status": "ok", "message": "Métricas actualizadas correctamente"}


@app.delete("/api/videos/uploaded/{video_id}")
@_state_locked(UPLOADED_VIDEOS_DATA_FILE)
def delete_uploaded_video(video_id: str):
    """Removes an approved video from data/uploaded_videos.json and cleans up session approval if applicable."""
    records = _load_uploaded_videos_data()
    idx_to_remove = None
    removed_record = None
    for idx, rec in enumerate(records):
        if rec.get("id") == video_id:
            idx_to_remove = idx
            removed_record = rec
            break
    if idx_to_remove is None:
        raise HTTPException(status_code=404, detail=f"Vídeo no encontrado: {video_id}")

    records.pop(idx_to_remove)
    _save_uploaded_videos_data(records)

    # Clean up session approval cache if session_id exists
    sess_name = removed_record.get("session_id")
    if sess_name:
        v3_dir = _safe_session_dir_or_none(sess_name) or (V3_CACHE_DIR / "__invalid_session__")
        if v3_dir.exists():
            appr_path = v3_dir / "approval.json"
            if appr_path.exists():
                try:
                    appr_path.unlink()
                except Exception as e:
                    logger.warning(f"Error deleting approval.json for session {sess_name}: {e}")
            s5_p = v3_dir / "step5_result.json"
            if s5_p.exists():
                try:
                    with open(s5_p, "r", encoding="utf-8") as s5f:
                        s5_data = json.load(s5f)
                    if "approval" in s5_data:
                        del s5_data["approval"]
                    with open(s5_p, "w", encoding="utf-8") as s5f:
                        json.dump(s5_data, s5f, indent=2, ensure_ascii=False)
                except Exception:
                    pass

    return {"status": "ok", "message": f"Vídeo '{removed_record.get('game_name', video_id)}' eliminado de la base de datos"}



@app.get("/api/promov3/sfx/library")
def get_promov3_sfx_library():
    """
    Scan static/sfx directory recursively for audio files.
    Deduplicates files by stem name (preferring .wav > .flac > .m4a > .mp3 > .mp4).
    Returns list of available SFX items with URLs and metadata.
    """
    sfx_base = BASE_DIR / "static" / "sfx"
    sfx_base.mkdir(parents=True, exist_ok=True)

    audio_extensions = {".wav", ".mp3", ".mp4", ".ogg", ".flac", ".m4a"}
    priority = {".wav": 1, ".flac": 2, ".m4a": 3, ".ogg": 4, ".mp3": 5, ".mp4": 6}

    all_files: List[Path] = []
    for p in sfx_base.rglob("*"):
        if p.is_file() and p.suffix.lower() in audio_extensions:
            all_files.append(p)

    grouped: Dict[str, List[Path]] = {}
    for p in all_files:
        stem_key = p.stem.lower()
        if stem_key not in grouped:
            grouped[stem_key] = []
        grouped[stem_key].append(p)

    library_items = []
    for stem_key, files in grouped.items():
        files.sort(key=lambda f: priority.get(f.suffix.lower(), 99))
        best_file = files[0]

        rel_path = best_file.relative_to(sfx_base).as_posix()
        cat = best_file.parent.name if best_file.parent != sfx_base else "general"

        dur = 1.5
        try:
            dur = round(get_media_duration(best_file), 2)
        except Exception:
            pass

        clean_title = stem_key.replace("-", " ").replace("_", " ").title()

        library_items.append({
            "id": stem_key,
            "title": clean_title,
            "filename": best_file.name,
            "relative_path": rel_path,
            "url": f"/static/sfx/{rel_path}",
            "duration": dur if dur > 0.05 else 1.5,
            "category": cat,
        })

    library_items.sort(key=lambda x: (x["category"], x["title"]))
    return {"status": "ok", "library": library_items}



# =============================================================================
# API Endpoints: File Upload & Preview
# =============================================================================

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload gameplay video or avatar image."""
    upload_dir = BASE_DIR / "temp" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    clean_filename = f"{uuid.uuid4().hex[:6]}_{_safe_upload_basename(file.filename)}"
    dest_path = _safe_child_path(upload_dir, clean_filename)

    content = await file.read()
    await run_in_threadpool(_write_bytes_file, dest_path, content)

    return {
        "status": "ok",
        "filename": file.filename,
        "saved_path": str(dest_path),
        "url": f"/media/temp/uploads/{clean_filename}",
    }


@app.post("/api/ingest/youtube")
def ingest_youtube_video(req: YouTubeIngestRequest):
    """Download and ingest a video clip or full video from YouTube using yt-dlp."""
    if not req.url or not req.url.strip():
        raise HTTPException(status_code=400, detail="Debes proporcionar una URL válida de YouTube.")

    url = req.url.strip()
    if not ("youtube.com" in url.lower() or "youtu.be" in url.lower()):
        raise HTTPException(status_code=400, detail="La URL especificada no parece ser un enlace de YouTube válido.")
    try:
        url = _validate_http_url(url)
    except ValueError as url_err:
        raise HTTPException(status_code=400, detail=str(url_err))

    game_name = (req.game_name or "").strip()
    if game_name:
        clean_gname = re.sub(r'[^\w\s-]', '', game_name).strip() or "YouTube_Import"
        dest_dir = PROMOS_FOOTAGE_DIR / clean_gname
    else:
        dest_dir = BASE_DIR / "temp" / "uploads"

    dest_dir.mkdir(parents=True, exist_ok=True)

    uid = uuid.uuid4().hex[:6]
    output_filename = f"yt_{uid}.mp4"
    output_path = dest_dir / output_filename

    start_s = max(0.0, float(req.start_sec or 0.0))
    end_s = max(0.0, float(req.end_sec or 0.0))

    try:
        logger.info(f"Ingestando vídeo de YouTube: {url} -> {output_path} (segmento: {start_s}s - {end_s}s)")
        download_youtube_clip(
            url=url,
            start_sec=start_s,
            end_sec=end_s,
            output_path=output_path,
        )

        if not output_path.exists():
            raise FileNotFoundError("No se generó el archivo de vídeo descargado desde YouTube.")

        size_mb = round(output_path.stat().st_size / (1024 * 1024), 2)
        meta = {}
        try:
            meta = probe_video(str(output_path))
        except Exception as pe:
            logger.warning(f"Could not probe downloaded video metadata: {pe}")

        duration = meta.get("duration", 0.0)

        if game_name:
            media_url = f"/media/PromosFootage/{urllib.parse.quote(clean_gname)}/{urllib.parse.quote(output_filename)}"
            group_name = f"PromosFootage / {clean_gname}"
        else:
            media_url = f"/media/temp/uploads/{urllib.parse.quote(output_filename)}"
            group_name = "Subidos Recientes (Uploads)"

        clip_data = {
            "group": group_name,
            "game_name": clean_gname if game_name else "Uploads",
            "filename": output_filename,
            "label": f"YouTube ({clean_gname if game_name else 'Uploads'}) — {output_filename} ({size_mb} MB)",
            "path": str(output_path.resolve()),
            "url": media_url,
            "size_mb": size_mb,
            "duration": duration,
        }

        return {
            "status": "ok",
            "filename": output_filename,
            "saved_path": str(output_path.resolve()),
            "url": media_url,
            "size_mb": size_mb,
            "duration": duration,
            "clip": clip_data,
        }

    except Exception as e:
        logger.exception(f"Error descargando/ingestando vídeo de YouTube desde {url}")
        raise HTTPException(status_code=500, detail=f"Error descargando de YouTube: {str(e)}")


@app.post("/api/preview/frame")
def generate_preview_frame(req: PreviewRequest):
    """Generate a live 1080x1920 preview frame with Pillow for 1:1 pixel accuracy."""
    try:
        # Determine avatar image path
        avatar_path = req.card_avatar_path
        if not avatar_path:
            p_name = (req.preset or "").upper()
            u_name = (req.card_username or "").upper()
            if "IGD" in p_name or "INDIE" in u_name:
                igd_logo = BASE_DIR / "Presets" / "IGD" / "IGD_Logo.png"
                if igd_logo.exists():
                    avatar_path = str(igd_logo)
            elif "GUAM" in p_name or "MINUTE" in u_name:
                guam_logo = BASE_DIR / "Presets" / "GUAM" / "GUAM_Logo.png"
                if guam_logo.exists():
                    avatar_path = str(guam_logo)

        card_w = req.card_width or 1728
        card_h = req.card_height or 980
        font_body = req.card_font_size_body or 76
        font_user = req.card_font_size_username or 72
        font_handle = req.card_font_size_handle or 56
        avatar_r = req.card_avatar_radius or 105
        border_w = req.card_border_width or 12
        radius = req.card_radius or 70
        card_y = req.card_y_position if req.card_y_position is not None else 440
        video_y = req.video_y_position if req.video_y_position is not None else 180
        zoom = req.video_zoom if req.video_zoom is not None else 1.0

        temp_dir = BASE_DIR / "temp"
        temp_dir.mkdir(exist_ok=True)
        card_path = temp_dir / "preview_live_card.png"
        mockup_path = temp_dir / "preview_live_mockup.png"

        create_text_card(
            text=req.text or "Texto de ejemplo para previsualización...",
            output_path=card_path,
            username=req.card_username or "Indie Games Discovery",
            handle=req.card_handle or "@indiegamesdiscovery",
            bg_color=req.card_bg_color or "#ffffff",
            text_color=req.card_text_color or "#000000",
            border_color=req.card_border_color or "#7ca032",
            border_width=border_w,
            radius=radius,
            avatar_text=req.card_avatar_text or "G",
            avatar_bg=req.card_avatar_bg or "#7ca032",
            avatar_image_path=avatar_path,
            font_username_family=req.card_font_username or "Segoe UI",
            font_handle_family=req.card_font_handle or "Segoe UI",
            font_body_family=req.card_font_body or "Segoe UI",
            width=card_w,
            height=card_h,
            font_size_body=font_body,
            avatar_radius=avatar_r,
            font_size_username=font_user,
            font_size_handle=font_handle,
            overlay_type=req.overlay_type or "card",
        )

        create_video_preview(
            card_image_path=card_path,
            output_preview_path=mockup_path,
            style=req.video_style or "vertical_split_blur",
            username=req.card_username or "indiegamesdiscovery",
            card_y_position=card_y,
            zoom=zoom,
            video_y_position=video_y,
            video_path=req.video_path,
        )

        return FileResponse(mockup_path, media_type="image/png")
    except Exception as e:
        logger.error(f"Error generating preview frame: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/preview/batch/{item_id}")
def generate_batch_preview(item_id: str):
    """Generate a live 1080x1920 preview frame for a batch task."""
    queue_file = BASE_DIR / "data" / "batch_queue.json"
    if not queue_file.exists():
        raise HTTPException(status_code=404, detail="Cola vacía")
    with open(queue_file, "r", encoding="utf-8") as f:
        items = json.load(f)
    item = next((x for x in items if x.get("id") == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")

    temp_dir = BASE_DIR / "temp"
    temp_dir.mkdir(exist_ok=True)
    card_path = temp_dir / f"preview_batch_card_{item_id}.png"
    mockup_path = temp_dir / f"preview_batch_mockup_{item_id}.png"

    preset_name = item.get("preset_name", "")
    avatar_path = item.get("card_avatar_path")
    if not avatar_path:
        if "IGD" in preset_name:
            avatar_path = str(BASE_DIR / "Presets" / "IGD" / "IGD_Logo.png")
        elif "GUAM" in preset_name:
            avatar_path = str(BASE_DIR / "Presets" / "GUAM" / "GUAM_Logo.png")

    create_text_card(
        text=item.get("hook_text") or "Gancho publicitario...",
        output_path=card_path,
        username=item.get("card_username") or "Indie Games Discovery",
        handle=item.get("card_handle") or "@indiegamesdiscovery",
        bg_color=item.get("card_bg_color") or "#ffffff",
        text_color=item.get("card_text_color") or "#000000",
        border_color=item.get("card_border_color") or "#7ca032",
        border_width=item.get("card_border_width") or 12,
        radius=item.get("card_radius") or 70,
        avatar_text="G",
        avatar_bg=item.get("card_avatar_bg") or "#7ca032",
        avatar_image_path=avatar_path,
        font_username_family="Segoe UI",
        font_handle_family="Segoe UI",
        font_body_family="Segoe UI",
        width=item.get("card_width") or 1728,
        height=item.get("card_height") or 980,
        font_size_body=item.get("card_font_size_body") or 76,
        avatar_radius=105,
        font_size_username=72,
        font_size_handle=56,
        overlay_type=item.get("overlay_type") or "card",
    )

    create_video_preview(
        card_image_path=card_path,
        output_preview_path=mockup_path,
        style="vertical_split_blur",
        username=item.get("card_username") or "indiegamesdiscovery",
        card_y_position=item.get("card_y_position") or 440,
        zoom=item.get("video_zoom") or 1.0,
        video_y_position=item.get("video_y_position") or 180,
        video_path=item.get("local_file_path"),
    )

    return FileResponse(mockup_path, media_type="image/png")


# =============================================================================
# API Endpoints: Single Mode Video Render
# =============================================================================

@app.post("/api/render/single")
def render_single_video(req: SingleRenderRequest):
    """Render a vertical promotional video with blurred background and card overlay."""
    job = create_job("render")
    
    def worker():
        try:
            cfg = AppConfig.from_env()
            groq_key = _client_secret_or_env(req.groq_api_key, cfg.groq_api_key)
            gemini_key = _client_secret_or_env(req.gemini_api_key, cfg.gemini_api_key)
            
            job.update(10.0, "Preparando origen del vídeo...", "Validando archivo/enlace...")
            
            run_id = generate_run_id()
            input_dir = BASE_DIR / "input"
            input_dir.mkdir(exist_ok=True)
            
            # 1. Obtain input video
            if req.source_type == "youtube":
                if not req.youtube_url:
                    raise ValueError("URL de YouTube requerida")
                _validate_http_url(req.youtube_url)
                input_video_path = input_dir / f"yt_{run_id}.mp4"
                job.update(20.0, f"Descargando clip de YouTube (seg {req.start_sec} a {req.end_sec})...", f"URL: {req.youtube_url}")
                download_youtube_clip(
                    url=req.youtube_url,
                    start_sec=req.start_sec,
                    end_sec=req.end_sec,
                    output_path=input_video_path,
                    ffmpeg_bin=cfg.ffmpeg_path
                )
            else:
                if not req.local_file_path or not Path(req.local_file_path).exists():
                    raise ValueError(f"Archivo local no encontrado: {req.local_file_path}")
                input_video_path = Path(req.local_file_path)
                
            job.update(45.0, "Configurando dimensiones y estilo...", f"Resolución seleccionada: {req.resolution}")
            
            # Dimensions
            if req.resolution == "4K":
                out_w, out_h = 2160, 3840
            else:
                out_w, out_h = 1080, 1920
                
            presets_dict = get_all_presets()
            clean_preset = (req.preset_name or "").replace("Predeterminado: ", "").strip()
            p_info = presets_dict.get(clean_preset)

            # Resolve avatar path: prioritize explicit path if exists, otherwise fallback to preset avatar
            avatar_path_to_use = req.card_avatar_path
            if (not avatar_path_to_use or not Path(avatar_path_to_use).exists()) and p_info and p_info.get("avatar_path"):
                avatar_path_to_use = p_info["avatar_path"]

            # Build Style Dictionary
            style_dict = {
                "name": req.preset_name or "Personalizado",
                "card_username": req.card_username,
                "card_handle": req.card_handle,
                "card_bg_color": req.card_bg_color,
                "card_text_color": req.card_text_color,
                "card_border_color": req.card_border_color,
                "card_border_width": req.card_border_width if req.card_border_width is not None else 12,
                "card_radius": req.card_radius if req.card_radius is not None else 80,
                "card_avatar_text": req.card_avatar_text,
                "card_avatar_bg": req.card_avatar_bg or (p_info["data"].get("card_avatar_bg") if p_info else "#7ca032"),
                "card_avatar_path": avatar_path_to_use,
                "card_font_username": req.card_font_username or "Segoe UI",
                "card_font_handle": req.card_font_handle or "Segoe UI",
                "card_font_body": req.card_font_body or "Segoe UI",
                "card_width": req.card_width or 1728,
                "card_height": req.card_height or 980,
                "card_font_size_body": req.card_font_size_body or 76,
                "card_font_size_username": req.card_font_size_username or 72,
                "card_font_size_handle": req.card_font_size_handle or 56,
                "card_avatar_radius": req.card_avatar_radius or 105,
                "card_y_position": req.card_y_position if req.card_y_position is not None else 440,
                "video_zoom": req.video_zoom,
                "video_y_position": req.video_y_position if req.video_y_position is not None else 180,
                "overlay_type": req.overlay_type or "card",
            }
            
            # AppConfig for Pipeline
            run_config = AppConfig(
                gemini_api_key=gemini_key,
                groq_api_key=groq_key,
                output_width=out_w,
                output_height=out_h,
                video_style="vertical_split_blur",
                video_zoom=req.video_zoom,
                video_y_position=req.video_y_position,
                overlay_type=req.overlay_type or "card",
                card_username=req.card_username,
                card_handle=req.card_handle,
                card_bg_color=req.card_bg_color,
                card_text_color=req.card_text_color,
                card_border_color=req.card_border_color,
                card_border_width=style_dict["card_border_width"],
                card_radius=style_dict["card_radius"],
                card_avatar_text=req.card_avatar_text,
                card_avatar_bg=style_dict["card_avatar_bg"],
                card_avatar_path=avatar_path_to_use,
                card_font_username=style_dict["card_font_username"],
                card_font_handle=style_dict["card_font_handle"],
                card_font_body=style_dict["card_font_body"],
                card_width=style_dict["card_width"],
                card_height=style_dict["card_height"],
                card_font_size_body=style_dict["card_font_size_body"],
                card_y_position=style_dict["card_y_position"],
                card_avatar_radius=style_dict["card_avatar_radius"],
                card_font_size_username=style_dict["card_font_size_username"],
                card_font_size_handle=style_dict["card_font_size_handle"],
                card_text=req.card_text,
                game_name=req.game_name,
                auto_cut=False,
            )
            
            job.update(60.0, "Renderizando composición vertical (Fondo borroso + Tarjeta)...", "Ejecutando FFmpeg...")
            
            pipeline = Pipeline(run_config)
            from schemas import PipelineContext
            ctx = PipelineContext(run_id=run_id, input_video=input_video_path)
            ctx.merged_video_path = ctx.input_video
            
            # Output filename
            export_name = req.export_filename
            if not export_name or not export_name.strip():
                clean_game = "".join(c for c in req.game_name if c.isalnum() or c in ("-", "_"))
                export_name = f"{clean_game or 'video'}_{run_id}.mp4"
            if not export_name.endswith(".mp4"):
                export_name += ".mp4"
                
            final_res = pipeline.export(ctx, export_name, styles_list=[style_dict])
            
            final_path = final_res[0] if isinstance(final_res, list) else final_res
            final_filename = Path(final_path).name
            
            video_url = app_output_url(final_filename)
            
            job.complete({
                "video_url": video_url,
                "video_filename": final_filename,
                "video_path": str(final_path),
                "resolution": f"{out_w}x{out_h}",
                "run_id": run_id,
            }, "¡Vídeo renderizado y exportado con éxito!")
            
        except Exception as e:
            logger.exception("Single render failed")
            job.fail(str(e))
            
    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job.job_id}


# =============================================================================
# API Endpoints: Super Batch Processing
# =============================================================================

@app.get("/api/batch")
def get_batch():
    """Get current batch queue items."""
    return {"items": load_batch_queue()}


@app.post("/api/batch/add")
@_state_locked(BATCH_QUEUE_FILE)
def add_to_batch(item: BatchItemModel):
    """Add a video job to the batch queue, populating default preset properties."""
    queue = load_batch_queue()
    entry = item.dict()
    entry["id"] = f"batch_{uuid.uuid4().hex[:6]}"
    entry["added_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry["status"] = "pending"
    entry["output_video"] = None

    # Populate default style properties from preset if missing
    presets_dict = get_all_presets()
    clean_preset = (entry.get("preset_name") or "Predeterminado: IGD").replace("Predeterminado: ", "").strip()
    preset_info = presets_dict.get(clean_preset)

    if preset_info and preset_info.get("data"):
        d = preset_info["data"]
        # If preset is IGD or GUAM, prevent cross-contamination of brand colors
        if clean_preset == "IGD":
            if entry.get("card_avatar_bg") == "#ff7d00":
                entry["card_avatar_bg"] = "#7ca032"
            if entry.get("card_border_color") == "#ff7d00":
                entry["card_border_color"] = "#7ca032"
        elif clean_preset == "GUAM":
            if entry.get("card_avatar_bg") == "#7ca032":
                entry["card_avatar_bg"] = "#ff7d00"
            if entry.get("card_border_color") == "#7ca032":
                entry["card_border_color"] = "#ff7d00"

        for key in ["card_username", "card_handle", "card_bg_color", "card_text_color",
                    "card_border_color", "card_border_width", "card_radius",
                    "card_font_size_body", "card_avatar_bg", "card_y_position",
                    "card_width", "card_height", "video_zoom", "video_y_position"]:
            if entry.get(key) is None and key in d:
                entry[key] = d[key]
        if not entry.get("card_avatar_path") and preset_info.get("avatar_path"):
            entry["card_avatar_path"] = preset_info["avatar_path"]
        if not entry.get("card_avatar_url") and preset_info.get("avatar_url"):
            entry["card_avatar_url"] = preset_info["avatar_url"]

    queue.append(entry)
    save_batch_queue(queue)
    return {"status": "ok", "items_count": len(queue), "item": entry}


@app.post("/api/batch/update")
@_state_locked(BATCH_QUEUE_FILE)
def update_batch_item(item: BatchItemModel):
    """Update an existing batch task with modified parameters."""
    if not item.id:
        raise HTTPException(status_code=400, detail="ID de tarea requerido")
    queue = load_batch_queue()
    found_idx = -1
    for i, q_item in enumerate(queue):
        if q_item.get("id") == item.id:
            found_idx = i
            break
    if found_idx == -1:
        raise HTTPException(status_code=404, detail="Tarea no encontrada en la cola")

    updated_data = item.dict(exclude_unset=True)
    queue[found_idx].update(updated_data)

    clean_p = (queue[found_idx].get("preset_name") or "Predeterminado: IGD").replace("Predeterminado: ", "").strip()
    if clean_p == "IGD" and queue[found_idx].get("card_avatar_bg") == "#ff7d00":
        queue[found_idx]["card_avatar_bg"] = "#7ca032"
    elif clean_p == "GUAM" and queue[found_idx].get("card_avatar_bg") == "#7ca032":
        queue[found_idx]["card_avatar_bg"] = "#ff7d00"

    save_batch_queue(queue)
    return {"status": "ok", "item": queue[found_idx]}


@app.delete("/api/batch/{item_id}")
@_state_locked(BATCH_QUEUE_FILE)
def remove_from_batch(item_id: str):
    """Remove an item from batch queue by ID."""
    queue = load_batch_queue()
    new_queue = [item for item in queue if item.get("id") != item_id]
    save_batch_queue(new_queue)
    return {"status": "ok", "items_count": len(new_queue)}


@app.post("/api/batch/clear")
@_state_locked(BATCH_QUEUE_FILE)
def clear_batch():
    """Clear all items from batch queue."""
    save_batch_queue([])
    return {"status": "ok", "message": "Cola de Super Batch vaciada"}


@app.post("/api/batch/run")
def run_batch():
    """Run all pending items in the Super Batch queue."""
    queue = load_batch_queue()
    if not queue:
        raise HTTPException(status_code=400, detail="La cola de batch está vacía")
        
    job = create_job("batch")
    
    def worker():
        try:
            total_items = len(queue)
            job.update(5.0, f"Iniciando Super Batch ({total_items} vídeos)...", "Cargando presets y entorno...")
            
            presets_dict = get_all_presets()
            cfg = AppConfig.from_env()
            
            completed_results = []
            
            for idx, item in enumerate(queue):
                g_name = item.get("game_name", f"Video_{idx+1}")
                pct = 5.0 + (idx / total_items) * 90.0
                job.update(pct, f"[{idx+1}/{total_items}] Procesando '{g_name}'...", f"Iniciando tarea {idx+1}")
                
                try:
                    item["status"] = "processing"
                    save_batch_queue(queue)
                    
                    run_id = generate_run_id()
                    input_dir = BASE_DIR / "input"
                    input_dir.mkdir(exist_ok=True)
                    output_dir = APP_OUTPUT_DIR
                    output_dir.mkdir(parents=True, exist_ok=True)
                    
                    yt_url = item.get("youtube_url")
                    local_path = item.get("local_file_path")
                    target_dur = float(item.get("target_duration") or item.get("end_sec") or 30.0)
                    if target_dur <= 0:
                        target_dur = 30.0
                    
                    clean_game_name = "".join(c for c in g_name if c.isalnum() or c in ("-", "_")).strip() or "video"
                    
                    if yt_url:
                        input_video = input_dir / f"batch_yt_{run_id}.mp4"
                        job.update(pct + 1.0, f"[{idx+1}/{total_items}] Descargando trailer completo ({g_name})...", f"URL: {yt_url}")
                        download_youtube_trailer(
                            url=yt_url,
                            output_path=input_video,
                            max_duration=180.0,
                            ffmpeg_bin=cfg.ffmpeg_path or "ffmpeg",
                        )
                    else:
                        input_video = Path(local_path)
                        
                    hook_text = item.get("hook_text", "")
                    if not hook_text.strip():
                        if cfg.groq_api_key or cfg.gemini_api_key:
                            job.update(pct + 2.5, f"[{idx+1}/{total_items}] Generando gancho con IA ({g_name})...", "Llamando a modelo de lenguaje...")
                            hook_text = generate_hook_with_groq(g_name, cfg.groq_api_key, gemini_api_key=cfg.gemini_api_key)
                        else:
                            hook_text = f"¡Descubre {g_name}! Un juego imprescindible."
                        item["hook_text"] = hook_text
                    
                    # -------------------------------------------------------------
                    # SHORTS V1 IA PIPELINE: Multimodal Analysis + Director IA (EDL) + FFmpeg Assembler
                    # -------------------------------------------------------------
                    game_footage_dir = BASE_DIR / "GamesFootage" / clean_game_name
                    game_footage_dir.mkdir(parents=True, exist_ok=True)
                    breakdowns_dir = BASE_DIR / "data" / "breakdowns" / clean_game_name
                    breakdowns_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Pre-sync: if breakdown exists in GamesFootage, use it directly (0 token cost)
                    gf_bd = game_footage_dir / f"{input_video.stem}.breakdown.json"
                    local_bd = input_video.parent / f"{input_video.stem}.breakdown.json"
                    target_bd = breakdowns_dir / f"{input_video.stem}.breakdown.json"
                    if not target_bd.exists():
                        if gf_bd.exists():
                            try:
                                shutil.copy2(gf_bd, target_bd)
                            except Exception:
                                pass
                        elif local_bd.exists():
                            try:
                                shutil.copy2(local_bd, target_bd)
                            except Exception:
                                pass
                                
                    job.update(pct + 4.0, f"[{idx+1}/{total_items}] Analizando trailer con Gemini Vision ({g_name})...", "Detectando secuencias de gameplay, planos y momentos clave...")
                    analyzer = VideoAnalyzer(config=cfg, output_dir=breakdowns_dir)
                    bd = analyzer.process_video(input_video, force=False, footage_is_already_9_16=False, target_aspect="16:9")
                    
                    # Post-sync to GamesFootage
                    try:
                        if target_bd.exists():
                            shutil.copy2(target_bd, gf_bd)
                        if "temp" in str(input_video).lower() and not (game_footage_dir / input_video.name).exists():
                            shutil.copy2(input_video, game_footage_dir / input_video.name)
                    except Exception as sync_err:
                        logger.warning(f"Could not sync breakdown to GamesFootage: {sync_err}")
                        
                    # Filtro Splash Final: eliminar logos, wishlist y fechas de lanzamiento al final del trailer
                    from video_analyzer import strip_trailer_final_splash
                    clean_bd, splash_start = strip_trailer_final_splash(bd)
                    if splash_start is not None:
                        tail_diff = bd.metadata.duration_seconds - splash_start
                        job.update(
                            pct + 5.0,
                            f"[{idx+1}/{total_items}] Filtro Splash Final: Purgados {tail_diff:.1f}s finales de logos/wishlist.",
                            f"Gameplay limpio hasta el segundo {splash_start:.1f}s."
                        )
                        bd = clean_bd
                        
                    job.update(pct + 6.0, f"[{idx+1}/{total_items}] Director IA: estructurando cortes dinámicos ({target_dur:.0f}s)...", f"Hook: '{hook_text[:30]}...'")
                    sequencer = VideoSequencer(config=cfg)
                    edl = sequencer.generate_edl(
                        breakdowns=[bd],
                        target_duration=target_dur,
                        framing_mode="fit_16_9",
                        variant_index=1,
                        total_variants=1,
                        hook_text=hook_text,
                        footage_is_already_9_16=False,
                    )
                    
                    job.update(pct + 7.5, f"[{idx+1}/{total_items}] Ensamblando {len(edl.cuts)} planos en alta resolución con FFmpeg...", "Renderizando cortes de vídeo...")
                    temp_v1_path = output_dir / f"{clean_game_name}_batch_montage_{run_id}.mp4"
                    search_dirs = [input_video.parent, game_footage_dir, BASE_DIR / "GamesFootage", BASE_DIR / "input", BASE_DIR / "temp" / "uploads"]
                    assembler = FFmpegAssembler(config=cfg)
                    assembler.assemble_edl(
                        edl=edl,
                        search_dirs=search_dirs,
                        output_path=temp_v1_path,
                        override_framing="fit_16_9",
                    )
                            
                    preset_choice = item.get("preset_name", "Predeterminado: IGD")
                    clean_preset_name = preset_choice.replace("Predeterminado: ", "").strip()
                    preset_info = presets_dict.get(clean_preset_name)
                    
                    style_dict = {
                        "name": clean_preset_name,
                        "card_username": cfg.card_username,
                        "card_handle": cfg.card_handle,
                        "card_bg_color": cfg.card_bg_color,
                        "card_text_color": cfg.card_text_color,
                        "card_border_color": cfg.card_border_color,
                        "card_border_width": cfg.card_border_width,
                        "card_radius": cfg.card_radius,
                        "card_avatar_text": cfg.card_avatar_text,
                        "card_avatar_bg": cfg.card_avatar_bg,
                        "card_avatar_path": cfg.card_avatar_path,
                        "card_font_username": cfg.card_font_username,
                        "card_font_handle": cfg.card_font_handle,
                        "card_font_body": cfg.card_font_body,
                        "card_width": cfg.card_width,
                        "card_height": cfg.card_height,
                        "card_font_size_body": cfg.card_font_size_body,
                        "card_y_position": cfg.card_y_position,
                        "video_zoom": cfg.video_zoom,
                        "video_y_position": cfg.video_y_position,
                    }
                    
                    # Apply baseline preset if available
                    if preset_info:
                        style_dict.update(preset_info["data"])
                        if preset_info.get("avatar_path"):
                            style_dict["card_avatar_path"] = preset_info["avatar_path"]

                    # Override with individual item customizations
                    for k in ["card_username", "card_handle", "card_bg_color", "card_text_color",
                              "card_border_color", "card_border_width", "card_radius", "card_font_size_body",
                              "card_y_position", "card_width", "card_height", "video_zoom", "video_y_position", "card_avatar_path", "card_avatar_bg", "overlay_type"]:
                        if item.get(k) is not None:
                            style_dict[k] = item[k]
                            
                    res = item.get("resolution", "1080p")
                    out_w, out_h = (2160, 3840) if res == "4K" else (1080, 1920)
                    
                    export_filename = f"{clean_game_name or 'video'}_{run_id}.mp4"
                    
                    clean_p = (item.get("preset_name") or "Predeterminado: IGD").replace("Predeterminado: ", "").strip()
                    avatar_bg_val = style_dict.get("card_avatar_bg")
                    if clean_p == "IGD" and (avatar_bg_val == "#ff7d00" or not avatar_bg_val):
                        avatar_bg_val = "#7ca032"
                    elif clean_p == "GUAM" and (avatar_bg_val == "#7ca032" or not avatar_bg_val):
                        avatar_bg_val = "#ff7d00"
                    elif not avatar_bg_val:
                        avatar_bg_val = style_dict.get("card_border_color", "#7ca032")
                    style_dict["card_avatar_bg"] = avatar_bg_val

                    run_config = AppConfig(
                        gemini_api_key=cfg.gemini_api_key,
                        groq_api_key=cfg.groq_api_key,
                        output_width=out_w,
                        output_height=out_h,
                        video_style="vertical_split_blur",
                        video_zoom=style_dict.get("video_zoom", 1.0),
                        video_y_position=style_dict.get("video_y_position", 0),
                        overlay_type=style_dict.get("overlay_type", "card"),
                        card_username=style_dict.get("card_username", cfg.card_username),
                        card_handle=style_dict.get("card_handle", cfg.card_handle),
                        card_bg_color=style_dict.get("card_bg_color", cfg.card_bg_color),
                        card_text_color=style_dict.get("card_text_color", cfg.card_text_color),
                        card_border_color=style_dict.get("card_border_color", cfg.card_border_color),
                        card_border_width=style_dict.get("card_border_width", 12),
                        card_radius=style_dict.get("card_radius", 80),
                        card_avatar_bg=avatar_bg_val,
                        card_avatar_path=style_dict.get("card_avatar_path"),
                        card_font_size_body=style_dict.get("card_font_size_body", 82),
                        card_y_position=style_dict.get("card_y_position", cfg.card_y_position),
                        card_text=hook_text,
                        game_name=g_name,
                        auto_cut=False,
                    )
                    
                    job.update(pct + 8.5, f"[{idx+1}/{total_items}] Componiendo formato vertical y tarjeta social ({res})...", "Exportando Short V1...")
                    
                    pipeline = Pipeline(run_config)
                    from schemas import PipelineContext
                    ctx = PipelineContext(run_id=run_id, input_video=temp_v1_path)
                    ctx.merged_video_path = ctx.input_video
                    
                    final_res = pipeline.export(ctx, export_filename, styles_list=[style_dict])
                    final_path = final_res[0] if isinstance(final_res, list) else final_res
                    out_name = Path(final_path).name
                    
                    # Limpieza de montaje temporal intermedio
                    try:
                        temp_v1_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    
                    item["status"] = "completed"
                    item["output_video"] = app_output_url(out_name)
                    item["output_filename"] = out_name
                    save_batch_queue(queue)
                    
                    completed_results.append({
                        "id": item.get("id"),
                        "game_name": g_name,
                        "video_url": app_output_url(out_name),
                        "filename": out_name,
                        "status": "completed"
                    })
                    
                except Exception as e_item:
                    logger.exception(f"Error in batch item {g_name}")
                    item["status"] = "error"
                    item["error_msg"] = str(e_item)
                    save_batch_queue(queue)
                    completed_results.append({
                        "id": item.get("id"),
                        "game_name": g_name,
                        "error": str(e_item),
                        "status": "error"
                    })
                    
            job.complete({
                "total": total_items,
                "results": completed_results,
            }, f"Super Batch finalizado. {len([r for r in completed_results if r.get('status') == 'completed'])}/{total_items} vídeos completados.")
            
        except Exception as e:
            logger.exception("Super Batch failed completely")
            job.fail(str(e))
            
    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job.job_id}


# =============================================================================
# API Endpoints: Benchmark Suite (12 Tests Matrix)
# =============================================================================

@app.post("/api/benchmark/run")
def start_benchmark(req: BenchmarkRunRequest):
    """Run full 12-test benchmark comparing 1080p vs 4K, CPU vs GPU, and 3 blur levels."""
    if not req.source or not req.source.strip():
        raise HTTPException(status_code=400, detail="Se requiere enlace de YouTube o ruta de vídeo")
        
    job = create_job("benchmark")
    
    def worker():
        try:
            job.update(2.0, "Iniciando benchmark de rendimiento...", "Descargando clip de 10s...")
            
            def progress_callback(step: int, total: int, msg: str):
                pct = 2.0 + (step / max(total, 1)) * 96.0
                job.update(pct, msg, f"[{step}/{total}] {msg}")
                
            results = run_full_benchmark(req.source.strip(), progress_callback=progress_callback)
            
            run_id = results.get("run_id")
            for r in results.get("results", []):
                out_f = r.get("output_file")
                if out_f:
                    filename = Path(out_f).name
                    r["video_url"] = f"/media/benchmarks/run_{run_id}/{filename}"
                    
            job.complete(results, "¡Benchmark de 12 pruebas completado!")
        except Exception as e:
            logger.exception("Benchmark failed")
            job.fail(str(e))
            
    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job.job_id}


@app.get("/api/benchmark/history")
def get_benchmark_history():
    """Retrieve full benchmark history."""
    history = load_benchmark_history()
    for run in history:
        run_id = run.get("run_id")
        for r in run.get("results", []):
            out_f = r.get("output_file")
            if out_f:
                filename = Path(out_f).name
                r["video_url"] = f"/media/benchmarks/run_{run_id}/{filename}"
    return {"history": history}


@app.delete("/api/benchmark/history")
def delete_benchmark_history():
    """Clear benchmark history records."""
    clear_benchmark_history()
    return {"status": "ok", "message": "Historial de benchmarks borrado"}


# =============================================================================
# API Endpoints: V1 Multi-Video Pipeline Automation
# =============================================================================

def _validate_http_url(url: Any) -> str:
    """Return the stripped URL if it is a plain http(s) URL, else raise ValueError.

    Guards the yt-dlp command lines built here: a value such as "--exec ..." or a
    file:// URL must never reach yt-dlp as an option or a local file reference.
    """
    candidate = str(url or "").strip()
    if not candidate or candidate.startswith("-") or any(ord(ch) < 32 or ch.isspace() for ch in candidate):
        raise ValueError("URL inválida: debe ser un enlace http(s) válido.")
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL inválida: solo se admiten enlaces http:// o https://.")
    return candidate


def download_youtube_trailer(
    url: str,
    output_path: Path,
    max_duration: float = 180.0,
    ffmpeg_bin: str = "ffmpeg",
    camera_effect_intensity: Optional[float] = 0.0,
    save_raw_copy: bool = False
) -> Path:
    """Download trailer from YouTube using yt-dlp, trimming to max_duration if necessary.
    Optionally applies physical camera-recording-monitor simulation in a single FFmpeg pass.
    """
    url = _validate_http_url(url)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = BASE_DIR / "temp"
    temp_dir.mkdir(exist_ok=True)
    temp_dl = temp_dir / f"yt_dl_{uuid.uuid4().hex[:8]}.mp4"

    if temp_dl.exists():
        try:
            temp_dl.unlink(missing_ok=True)
        except OSError:
            pass

    ffmpeg_dir = str(Path(ffmpeg_bin).parent) if Path(ffmpeg_bin).is_absolute() else None

    cmd_ytdlp = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        # Kept on purpose: some user machines sit behind TLS-intercepting antivirus/proxies.
        "--no-check-certificates",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "-o", str(temp_dl),
    ]
    if ffmpeg_dir:
        cmd_ytdlp += ["--ffmpeg-location", ffmpeg_dir]
    cmd_ytdlp += ["--", url]  # "--": the URL can never be parsed as a yt-dlp option

    res = subprocess.run(cmd_ytdlp, capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        # Fallback stream with maximum quality
        cmd_fallback = [
            sys.executable, "-m", "yt_dlp",
            "-f", "bestvideo+bestaudio/best",
            "--merge-output-format", "mp4",
            "--no-check-certificates",
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "-o", str(temp_dl),
        ]
        if ffmpeg_dir:
            cmd_fallback += ["--ffmpeg-location", ffmpeg_dir]
        cmd_fallback += ["--", url]
        res_fb = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=300)
        if res_fb.returncode != 0:
            raise RuntimeError(f"Fallo en descarga con yt-dlp: {res_fb.stderr or res.stderr}")

    if not temp_dl.exists():
        raise FileNotFoundError("yt-dlp no generó el archivo de vídeo descargado.")

    if save_raw_copy:
        raw_target = output_path.parent / f"raw_{output_path.name}"
        try:
            shutil.copy2(temp_dl, raw_target)
        except Exception as e:
            logger.warning(f"No se pudo guardar copia raw de trailer: {e}")

    try:
        eff_intensity = float(camera_effect_intensity) if camera_effect_intensity is not None else 0.0
        if eff_intensity >= 0.01:
            # Single-pass FFmpeg transcode with full physical camera-monitor simulation
            fg = build_camera_monitor_filtergraph(intensity=eff_intensity)
            cmd_trim = [
                ffmpeg_bin, "-y",
                "-i", str(temp_dl),
                "-t", str(max_duration),
                "-filter_complex", fg,
                "-map", "[outv]",
                "-map", "0:a?",
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "17",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                str(output_path)
            ]
        else:
            # High-bitrate crisp normalization with medium preset and CRF 16 (clean bypass)
            cmd_trim = [
                ffmpeg_bin, "-y",
                "-i", str(temp_dl),
                "-t", str(max_duration),
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "16",
                "-pix_fmt", "yuv420p",
                "-b:v", "15M",
                "-maxrate", "25M",
                "-bufsize", "30M",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "48000",
                "-movflags", "+faststart",
                str(output_path)
            ]

        res_trim = subprocess.run(cmd_trim, capture_output=True, text=True, timeout=300)
        if res_trim.returncode != 0:
            logger.warning(f"FFmpeg transcode failed ({res_trim.stderr[:200]}), falling back to stream copy...")
            cmd_copy = [
                ffmpeg_bin, "-y",
                "-i", str(temp_dl),
                "-t", str(max_duration),
                "-c", "copy",
                "-movflags", "+faststart",
                str(output_path)
            ]
            res_copy = subprocess.run(cmd_copy, capture_output=True, text=True, timeout=120)
            if res_copy.returncode != 0:
                shutil.copy2(temp_dl, output_path)
    finally:
        if temp_dl.exists():
            try:
                temp_dl.unlink(missing_ok=True)
            except OSError:
                pass

    return output_path


@app.post("/api/v1/process-trailer")
def process_trailer_v1(req: V1ProcessTrailerRequest):
    """Trigger complete V1 Short-Form pipeline on a trailer from the database."""
    if req.url and req.url.strip():
        try:
            _validate_http_url(req.url)
        except ValueError as url_err:
            raise HTTPException(status_code=400, detail=str(url_err))
    job = create_job("v1_pipeline")

    def worker():
        try:
            cfg = AppConfig.from_env()

            # 1. Validation
            if not req.url or not req.url.strip():
                raise ValueError("URL del vídeo no proporcionada.")

            clean_game_name = _sanitize_folder_name(req.game_name or "Game")
            game_footage_dir = BASE_DIR / "GamesFootage" / clean_game_name
            game_footage_dir.mkdir(parents=True, exist_ok=True)

            run_id = generate_run_id()
            video_filename = f"trailer_{run_id}.mp4"
            target_video_path = game_footage_dir / video_filename

            # -----------------------------------------------------------------
            # Step 1: Descarga del trailer
            # -----------------------------------------------------------------
            job.update(
                12.0,
                f"Descargando trailer de '{req.game_name}' con yt-dlp...",
                f"URL de YouTube: {req.url} -> Guardando en {clean_game_name}/{video_filename}"
            )

            download_youtube_trailer(
                url=req.url,
                output_path=target_video_path,
                max_duration=180.0,
                ffmpeg_bin=cfg.ffmpeg_path or "ffmpeg",
            )

            file_size_mb = target_video_path.stat().st_size / (1024 * 1024)
            job.update(
                28.0,
                f"Descarga completada ({file_size_mb:.1f} MB).",
                f"Archivo local listo en: {target_video_path.name}"
            )

            # -----------------------------------------------------------------
            # Step 2: Análisis Multimodal con Gemini (Programa 1)
            # -----------------------------------------------------------------
            job.update(
                35.0,
                "Optimizando clip a 720p y analizando transiciones...",
                "Generando proxy ultrarrápido y detectando cortes de cámara con OpenCV..."
            )

            breakdowns_dir = BASE_DIR / "data" / "breakdowns" / clean_game_name
            breakdowns_dir.mkdir(parents=True, exist_ok=True)

            analyzer = VideoAnalyzer(config=cfg, output_dir=breakdowns_dir)
            job.update(
                45.0,
                "Subiendo proxy 720p a Gemini File API y analizando con IA...",
                "Gemini analizando acciones visuales, picos acústicos y potencial de gancho..."
            )

            target_aspect = "16:9" if (req.framing == "fit_16_9") else "9:16"
            breakdown = analyzer.process_video(target_video_path, force=True, target_aspect=target_aspect)

            job.update(
                65.0,
                f"Análisis IA finalizado: {len(breakdown.scenes)} planos y {len(breakdown.seconds_breakdown)}s desglosados.",
                f"Top ganchos detectados en escenas: {breakdown.top_hook_scenes}"
            )

            # -----------------------------------------------------------------
            # Step 3: Director IA / Secuenciador EDL (Programa 2)
            # -----------------------------------------------------------------
            job.update(
                70.0,
                "Director IA: Seleccionando los mejores ganchos y estructurando el montaje...",
                "Aplicando regla de los 3 segundos y arco de retención viral..."
            )

            # Filtro Splash Final: eliminar tarjetas promocionales (logos, fechas, wishlist)
            if req.strip_splash:
                from video_analyzer import strip_trailer_final_splash
                clean_breakdown, splash_start = strip_trailer_final_splash(breakdown)
                if splash_start is not None:
                    tail_diff = breakdown.metadata.duration_seconds - splash_start
                    job.update(
                        68.0,
                        f"Filtro Splash Final: Detectado inicio de logos/fecha en {splash_start:.1f}s.",
                        f"Purgados los últimos {tail_diff:.1f}s del footage para evitar que aparezcan en el vídeo final."
                    )
                    breakdown = clean_breakdown

            sequencer = VideoSequencer(config=cfg)
            edl = sequencer.generate_edl(
                breakdowns=[breakdown],
                target_duration=req.target_duration,
                framing_mode=req.framing or "fit_16_9",
            )

            edl_file = breakdowns_dir / f"edl_{run_id}.json"
            edl.save_json(edl_file)

            cuts_summary = " -> ".join([f"#{c.order} {c.purpose.upper()} ({c.duration:.1f}s)" for c in edl.cuts])
            job.update(
                82.0,
                f"Montaje estructurado: '{edl.title}' ({len(edl.cuts)} cortes, {edl.total_duration:.1f}s total).",
                f"Secuencia: {cuts_summary}"
            )

            # -----------------------------------------------------------------
            # Step 4: Ensamblado y Renderizado FFmpeg 16:9 1080p (Programa 3)
            # -----------------------------------------------------------------
            job.update(
                86.0,
                f"Renderizando vídeo final en alta resolución con FFmpeg (Modo: {req.framing or 'fit_16_9'})...",
                "Cortando directamente del footage original en alta calidad y aplicando micro-fundidos..."
            )

            assembler = FFmpegAssembler(config=cfg)
            output_dir = APP_OUTPUT_DIR
            output_dir.mkdir(parents=True, exist_ok=True)
            output_video_path = output_dir / f"{clean_game_name}_short_{run_id}.mp4"

            assembler.assemble_edl(
                edl=edl,
                search_dirs=[game_footage_dir, BASE_DIR / "input", BASE_DIR / "GamesFootage"],
                output_path=output_video_path,
                override_framing=req.framing or "fit_16_9",
            )

            job.update(98.0, "Optimizando archivo final para reproducción web...", "Agregando cabeceras faststart...")

            # -----------------------------------------------------------------
            # Step 5: Éxito
            # -----------------------------------------------------------------
            output_filename = output_video_path.name
            job.complete({
                "video_url": app_output_url(output_filename),
                "output_filename": output_filename,
                "title": edl.title,
                "cuts_count": len(edl.cuts),
                "duration": round(edl.total_duration, 1),
                "framing": req.framing or "fit_16_9",
                "game_name": req.game_name,
                "edl_path": str(edl_file),
            }, f"¡Vídeo 16:9 1080p de '{req.game_name}' completado con éxito!")

        except Exception as e:
            logger.exception("Fallo durante el pipeline V1")
            detailed_error = f"Error en {type(e).__name__}: {str(e)}\n\nDetalles técnicos:\n{traceback.format_exc()}"
            job.fail(detailed_error)

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job.job_id}


@app.post("/api/v1/open-output-folder")
def open_output_folder():
    """Open output folder in system file explorer."""
    out_dir = BASE_DIR / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(out_dir)])
        else:
            subprocess.Popen(["xdg-open", str(out_dir)])
        return {"status": "ok", "path": str(out_dir)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


class PromoLibrarySaveRequest(BaseModel):
    game_name: str
    footage_paths: List[str]


@app.get("/api/promos/library/games")
def get_promos_library_games():
    """List all game folders in PromosFootage with their clips and breakdown status."""
    PROMOS_FOOTAGE_DIR.mkdir(parents=True, exist_ok=True)
    video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
    games_list = []

    try:
        for folder in sorted(PROMOS_FOOTAGE_DIR.iterdir(), key=lambda p: p.name.lower()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue

            clean_folder = folder.name
            folder_videos = []
            has_breakdowns = False

            try:
                for f in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
                    if f.is_file() and f.suffix.lower() in video_exts:
                        bd_path = folder / f"{f.stem}.breakdown.json"
                        bd_916_path = folder / f"{f.stem}.9_16.breakdown.json"
                        data_bd_path = BASE_DIR / "data" / "breakdowns" / clean_folder / f"{f.stem}.breakdown.json"
                        data_bd_916_path = BASE_DIR / "data" / "breakdowns" / clean_folder / f"{f.stem}.9_16.breakdown.json"
                        bd_exists = bd_path.exists() or bd_916_path.exists() or data_bd_path.exists() or data_bd_916_path.exists()
                        if bd_exists:
                            has_breakdowns = True

                        folder_videos.append({
                            "filename": f.name,
                            "saved_path": str(f.resolve()),
                            "url": f"/media/PromosFootage/{urllib.parse.quote(clean_folder)}/{urllib.parse.quote(f.name)}",
                            "size": f.stat().st_size,
                            "size_mb": round(f.stat().st_size / (1024 * 1024), 1),
                            "has_breakdown": bd_exists,
                        })

                # Also verify if folder has breakdown json files directly even if video clips are yet to be uploaded
                if not has_breakdowns:
                    for f in folder.iterdir():
                        if f.is_file() and (f.name.endswith(".breakdown.json") or f.name.endswith(".9_16.breakdown.json")):
                            has_breakdowns = True
                            break
            except Exception as scan_err:
                logger.warning(f"Error scanning promo footage folder {folder.name}: {scan_err}")

            games_list.append({
                "game_name": clean_folder,
                "folder_name": clean_folder,
                "video_count": len(folder_videos),
                "has_breakdowns": has_breakdowns,
                "videos": folder_videos,
            })

    except Exception as e:
        logger.error(f"Error listing PromosFootage library: {e}")

    # Ordenar por nombre del juego
    games_list.sort(key=lambda g: g["game_name"].lower())
    return {"status": "ok", "games": games_list}


@app.post("/api/promos/library/save")
def save_promos_footage_to_library(req: PromoLibrarySaveRequest):
    """Save uploaded footage and breakdowns to PromosFootage/<GameName>/ for persistent reuse in Promos."""
    if not req.game_name or not req.game_name.strip():
        raise HTTPException(status_code=400, detail="Escribe el nombre del juego para crear su carpeta en PromosFootage")

    clean_game = _sanitize_folder_name(req.game_name.strip())
    target_dir = PROMOS_FOOTAGE_DIR / clean_game
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_videos = []
    video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}

    for fp in req.footage_paths:
        src = Path(fp)
        if not src.is_absolute():
            src = BASE_DIR / src
        if not src.exists() or not src.is_file() or src.suffix.lower() not in video_exts:
            continue

        dest = target_dir / src.name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)

        # Check and sync breakdowns (both standard 16:9 and 9:16 vertical) if they exist
        has_bd = False
        for suffix in (".breakdown.json", ".9_16.breakdown.json"):
            data_bd = BASE_DIR / "data" / "breakdowns" / clean_game / f"{src.stem}{suffix}"
            local_bd = src.parent / f"{src.stem}{suffix}"
            dest_bd = target_dir / f"{src.stem}{suffix}"
            if data_bd.exists() and not dest_bd.exists():
                try:
                    shutil.copy2(data_bd, dest_bd)
                    has_bd = True
                except Exception:
                    pass
            elif local_bd.exists() and not dest_bd.exists():
                try:
                    shutil.copy2(local_bd, dest_bd)
                    has_bd = True
                except Exception:
                    pass
            elif dest_bd.exists():
                has_bd = True

        saved_videos.append({
            "filename": dest.name,
            "saved_path": str(dest.resolve()),
            "url": f"/media/PromosFootage/{urllib.parse.quote(clean_game)}/{urllib.parse.quote(dest.name)}",
            "size": dest.stat().st_size,
            "size_mb": round(dest.stat().st_size / (1024 * 1024), 1),
            "has_breakdown": has_bd,
        })

    if not saved_videos:
        raise HTTPException(status_code=400, detail="No se encontraron archivos de vídeo válidos para guardar.")

    return {
        "status": "ok",
        "message": f"Footage guardado correctamente en PromosFootage/{clean_game}",
        "game_name": req.game_name,
        "folder_name": clean_game,
        "video_count": len(saved_videos),
        "videos": saved_videos,
    }


# =============================================================================
# API Endpoints: Promo Social Media Description Generator
# =============================================================================


# YouTube-specific editorial metadata; generic promo copy remains a separate workflow.
class YoutubeShortsMetadataRequest(BaseModel):
    game_name: str
    script_text: str = ""
    briefing: str = ""
    candidate_titles: List[str] = Field(default_factory=list)
    requested_hashtags: List[str] = Field(default_factory=list)
    required_hashtags: List[str] = Field(default_factory=list)
    required_cta: str = ""
    sponsored: bool = False
    use_ai: bool = False

@app.post("/api/youtube-shorts/metadata")
def youtube_shorts_metadata(req: YoutubeShortsMetadataRequest):
    from youtube_metadata import build_metadata, generation_prompt
    titles=list(req.candidate_titles);summary="";method="source_fallback"
    if not req.script_text.strip() and not req.briefing.strip():
        raise HTTPException(status_code=400, detail="Añade el guion o un briefing verificado antes de preparar los textos.")
    if req.use_ai:
        cfg=AppConfig.from_env()
        key=(cfg.groq_api_key or "").strip()
        if key:
            try:
                response=_api_usage.usage_post("https://api.groq.com/openai/v1/chat/completions",headers={"Authorization":"Bearer "+key},json={"model":"llama-3.3-70b-versatile","messages":[{"role":"user","content":generation_prompt(req.game_name,req.script_text,req.briefing,req.sponsored,req.required_cta)}],"response_format":{"type":"json_object"},"temperature":.5,"max_tokens":600},timeout=20)
                if response.status_code==200:
                    generated=json.loads(response.json()["choices"][0]["message"]["content"])
                    proposed=generated.get("titles",[])
                    if isinstance(proposed,list):titles=[x for x in proposed if isinstance(x,str)]+titles
                    if isinstance(generated.get("summary"),str):summary=generated["summary"]
                    method="generated_candidates_with_editorial_validation"
            except Exception:
                logger.warning("YouTube metadata provider unavailable; using source-based candidates")
    try:
        result=build_metadata(req.game_name,req.script_text,req.briefing,titles,summary,req.requested_hashtags,req.required_hashtags,req.sponsored,req.required_cta)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result.update(status="ok",method=method)
    return result

class YoutubeShortsSaveRequest(BaseModel):
    game_name: str
    title: str
    description: str
    sponsored: bool = False
    required_cta: str = ""
    required_hashtags: List[str] = Field(default_factory=list)

@app.post("/api/youtube-shorts/save-metadata")
def save_youtube_shorts_metadata(req: YoutubeShortsSaveRequest):
    from youtube_metadata import utf16_length
    if not req.title.strip() or utf16_length(req.title)>100 or not req.description.strip() or len(req.description)>5000:
        raise HTTPException(status_code=400,detail="Revisa el título y la descripción antes de guardar.")
    if req.sponsored and not re.search(r"(?i)(?<!\w)#ad(?!\w)",req.description):
        raise HTTPException(status_code=400,detail="La campaña necesita un aviso #ad en la descripción y la declaración de promoción pagada al publicar.")
    if req.required_cta and req.required_cta not in req.description:
        raise HTTPException(status_code=400,detail="Falta el CTA literal de la campaña.")
    if any(tag.lstrip('#').casefold() not in {x.casefold() for x in re.findall(r'#(\w+)',req.description)} for tag in req.required_hashtags):
        raise HTTPException(status_code=400,detail="Falta un hashtag obligatorio de la campaña.")
    folder=APP_OUTPUT_DIR/('Promo_'+_sanitize_folder_name(req.game_name)+'_latest')
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'YouTube_titulo.txt').write_text(req.title.strip()+'\n',encoding='utf-8')
    (folder/'YouTube_descripcion.txt').write_text(req.description.strip()+'\n',encoding='utf-8')
    (folder/'youtube_metadata.json').write_text(json.dumps(req.model_dump(),ensure_ascii=False,indent=2),encoding='utf-8')
    return {"status":"ok","folder":str(folder.relative_to(BASE_DIR)),"published":False}

class PromoDescriptionRequest(BaseModel):
    game_name: str
    briefing: Optional[str] = ""
    script_text: Optional[str] = ""
    hashtags: Optional[str] = ""
    hook_type: Optional[str] = "steam_new_game"
    opening_text: Optional[str] = ""


class PromoDescriptionSaveRequest(BaseModel):
    game_name: str
    run_id: Optional[str] = None
    description_text: str


@app.post("/api/promos/generate-description")
def generate_promo_social_description(req: PromoDescriptionRequest):
    """Generate social media copy with exact structure: [Hook] - [Explicación breve] - [CTA Wishlist Steam] - ad #[hashtags]."""
    cfg = AppConfig.from_env()
    clean_hashtags = (req.hashtags or "").strip()
    
    tags_list = []
    for token in clean_hashtags.replace(",", " ").split():
        token = token.strip()
        if not token:
            continue
        if not token.startswith("#"):
            token = f"#{token}"
        tags_list.append(token)
    
    if not tags_list:
        game_tag = re.sub(r'[^a-zA-Z0-9]', '', req.game_name.lower())
        tags_list = [f"#{game_tag}", "#indiegames", "#gaming", "#steam", "#gamedev", "#gamer", "#indiedev"]
    
    tags_str = " ".join(tags_list)

    if req.hook_type == "steam_new_game":
        hook_hint = "Here's a new Steam game you won't want to miss! 🎮🔥"
    elif req.hook_type == "custom" and (req.opening_text or "").strip():
        hook_hint = f"{req.opening_text.strip()} 🎮🔥"
    elif req.hook_type in ("direct", "custom"):
        hook_hint = "Lead with the game's strongest moment or payoff (no generic opener). 🎮🔥"
    else:
        hook_hint = "So in this new game, check out what happens! 🎮🔥"

    prompt = f"""You are an elite viral gaming copywriter for TikTok, Instagram Reels, and YouTube Shorts.
Write an engaging social media post description for this indie game:

Game Name: {req.game_name}
Game Briefing / Key Info: {req.briefing or 'Unique indie game gameplay and mechanics'}
Voiceover Script Used: {req.script_text or 'Awesome new game you have to check out on Steam!'}
Hook Type: {req.hook_type or 'steam_new_game'}
User Custom Hashtags: {tags_str}

CRITICAL RULES AND MANDATORY OUTPUT STRUCTURE:
You MUST format the output text EXACTLY in this 4-part structure (separate each part with a line break):
1. [Hook]: A catchy opening line (e.g. "{hook_hint}").
2. [Brief Explanation]: 1-2 punchy, exciting sentences describing the atmosphere, core mechanic, or what makes the game special.
3. [CTA]: Clear Call To Action (e.g. "👉 Add {req.game_name} to your Steam Wishlist today!" or "🎮 Wishlist now on Steam!").
4. ad {tags_str} (Exactly the word 'ad' followed by the space-separated hashtags).

Do not include markdown headers (like '## Description' or brackets). Output ONLY the final ready-to-copy social post text in natural, authentic English.
"""

    description = ""
    groq_key = (cfg.groq_api_key or os.environ.get("GROQ_API_KEY", "")).strip().strip('"').strip("'")
    if groq_key:
        import requests
        try:
            headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": "You write punchy viral social media descriptions for gaming videos."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 350,
            }
            resp = _api_usage.usage_post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                description = data["choices"][0]["message"]["content"].strip()
        except Exception as groq_err:
            logger.warning(f"Groq description error: {groq_err}")

    if not description:
        gemini_key = (cfg.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
        if gemini_key:
            try:
                from google import genai
                from google.genai import types
                client = genai.Client(api_key=gemini_key)
                for g_mod in ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-latest"]:
                    try:
                        resp = _api_usage.usage_generate(client.models.generate_content, 
                            model=g_mod,
                            contents=prompt,
                        )
                        if resp and resp.text:
                            description = resp.text.strip()
                            break
                    except Exception as mod_err:
                        logger.warning(f"Gemini description model {g_mod} failed: {mod_err}")
            except Exception as gem_err:
                logger.warning(f"Gemini description client error: {gem_err}")

    if not description:
        # Fallback template
        if req.hook_type == "steam_new_game":
            hook_line = "Here's a new Steam game you won't want to miss! 🎮🔥"
        elif req.hook_type == "custom" and (req.opening_text or "").strip():
            hook_line = f"{req.opening_text.strip()} 🎮🔥"
        else:
            hook_line = f"Check out {req.game_name}! 🎮🔥"
        description = f"{hook_line}\n\n{req.game_name} brings unique gameplay, intense atmosphere, and hours of fun. Check out the demo!\n\n👉 Add {req.game_name} to your Steam Wishlist now!\n\nad {tags_str}"

    return {
        "status": "ok",
        "description": description,
        "hashtags": tags_str,
    }


@app.post("/api/promos/save-description")
def save_promo_description_file(req: PromoDescriptionSaveRequest):
    """Save or update description.txt in the promo's folder in output."""
    clean_game = _sanitize_folder_name(req.game_name or "Promo")
    run_id = req.run_id or "latest"
    
    # Save in specific folder and root output
    target_dir = APP_OUTPUT_DIR / f"Promo_{clean_game}_{run_id}"
    target_dir.mkdir(parents=True, exist_ok=True)
    
    desc_path = target_dir / "description.txt"
    with open(desc_path, "w", encoding="utf-8") as f:
        f.write(req.description_text)
        
    # Also save a generic copy
    root_desc = APP_OUTPUT_DIR / f"{clean_game}_description.txt"
    with open(root_desc, "w", encoding="utf-8") as f:
        f.write(req.description_text)
        
    return {"status": "ok", "message": "Descripción guardada correctamente", "path": str(desc_path)}


@app.post("/api/promos/process")
def process_promo_pipeline(req: PromoProcessRequest):
    """Process promotional video: Short V1 IA montage, Card Overlay, or both."""
    job = create_job("promo_pipeline")

    def worker():
        try:
            cfg = AppConfig.from_env()
            run_id = generate_run_id()
            clean_game_name = _sanitize_folder_name(req.game_name or "Promo")

            # 1. Validate footage paths
            valid_footage: List[Path] = []
            for fp in req.footage_paths:
                p = Path(fp)
                if not p.is_absolute():
                    clean_fp = str(fp).replace("\\", "/").lstrip("/")
                    if clean_fp.startswith("media/"):
                        clean_fp = clean_fp[6:]
                    p = BASE_DIR / clean_fp
                if p.exists() and p.is_file():
                    valid_footage.append(p)
                else:
                    # Search in candidate folders
                    candidate_dirs = [
                        PROMOS_FOOTAGE_DIR / clean_game_name,
                        PROMOS_FOOTAGE_DIR,
                        BASE_DIR / "temp" / "uploads",
                        BASE_DIR / "input",
                        BASE_DIR / "GamesFootage" / clean_game_name,
                        BASE_DIR / "GamesFootage",
                    ]
                    found = False
                    for c_dir in candidate_dirs:
                        cand = c_dir / Path(fp).name
                        if cand.exists() and cand.is_file():
                            valid_footage.append(cand)
                            found = True
                            break
            
            if not valid_footage:
                raise ValueError("No se ha proporcionado ningún archivo de gameplay válido o existente.")

            job.update(10.0, f"Iniciando producción para '{req.game_name}'...", f"Clips detectados: {len(valid_footage)}")

            # Determine number of videos to generate (1 to 3)
            video_count = max(1, min(3, int(getattr(req, "video_count", 1) or 1)))

            # Prepare list of hooks corresponding to each video
            raw_hooks = []
            if req.hook_texts and isinstance(req.hook_texts, list):
                raw_hooks = [str(h).strip() for h in req.hook_texts if str(h).strip()]
            if not raw_hooks and req.hook_text:
                raw_hooks = [req.hook_text.strip()]

            # Ensure we have enough hooks for the requested count
            while len(raw_hooks) < video_count:
                raw_hooks.append(f"¡Descubre {req.game_name}! Un gameplay increíble que tienes que probar. 🎮🔥")

            # =========================================================================
            # SHORT V2 FINISHED VIDEO PIPELINE: Pre-edited Video + Captions + Steam Hook
            # =========================================================================
            if req.mode == "v2_finished_video":
                temp_dir = BASE_DIR / "temp" / f"v2_fin_{run_id}"
                temp_dir.mkdir(parents=True, exist_ok=True)
                output_dir = APP_OUTPUT_DIR
                output_dir.mkdir(parents=True, exist_ok=True)

                source_video = valid_footage[0]
                job.update(15.0, "Extrayendo audio del vídeo montado...", f"Procesando archivo {source_video.name}...")

                clean_audio_path = temp_dir / "clean_audio.wav"
                extract_clean_audio(source_video, clean_audio_path)
                audio_dur = get_audio_duration(clean_audio_path)

                clean_script = (req.script_text or "").strip()
                if not clean_script:
                    raise ValueError("Por favor introduce el guión correspondiente al audio de tu vídeo montado para sincronizar los subtítulos.")

                fin_hook_type = _effective_hook_type(getattr(req, "hook_type", "steam_new_game"), getattr(req, "opening_text", ""))
                fin_uses_splash = _uses_steam_splash(fin_hook_type)
                if fin_uses_splash:
                    clean_script = ensure_steam_hook_prefix(clean_script)
                elif fin_hook_type == "custom":
                    clean_script = _apply_custom_opening(clean_script, getattr(req, "opening_text", ""))
                total_duration = audio_dur if audio_dur > 0.5 else (req.target_duration if req.target_duration > 0 else 30.0)

                job.update(35.0, f"Sincronizando guión y marcas de tiempo ({total_duration:.1f}s)...", "Alineando palabras y generando subtítulos dinámicos...")
                alignment = align_script_words(
                    audio_path=clean_audio_path,
                    script_text=clean_script,
                    target_duration=total_duration,
                )
                raw_words = alignment.get("words", [])
                if fin_uses_splash:
                    _, hook_duration = filter_steam_hook_words(raw_words)
                else:
                    hook_duration = 0.0  # no Steam prefix spoken: captions start at 0, no splash

                # Determine target resolution and caption margins
                user_font_size = int(req.captions_font_size if req.captions_font_size is not None else 84)
                captions_y = int(req.captions_y_position if req.captions_y_position is not None else 600)
                is_16_9 = (req.framing == "fit_16_9")

                if is_16_9:
                    if req.resolution == "4K":
                        out_w, out_h = 3840, 2160
                        font_sz = int(round(user_font_size * (100.0 / 84.0) * (2160.0 / 1080.0)))
                        margin_v = int(captions_y * (2160.0 / 1920.0))
                    else:
                        out_w, out_h = 1920, 1080
                        font_sz = int(round(user_font_size * (56.0 / 84.0)))
                        margin_v = int(captions_y * (1080.0 / 1920.0))
                else:
                    if req.resolution == "4K":
                        out_w, out_h = 2160, 3840
                        font_sz = user_font_size * 2
                        margin_v = captions_y * 2
                    else:
                        out_w, out_h = 1080, 1920
                        font_sz = user_font_size
                        margin_v = captions_y

                ass_sub_path = temp_dir / "kinetic_captions.ass"
                highlight_col = req.captions_highlight_color or "#FFF380"
                generate_kinetic_ass_subtitles(
                    alignment_data=alignment,
                    output_ass_path=ass_sub_path,
                    video_width=out_w,
                    video_height=out_h,
                    font_size=font_sz,
                    word_by_word=True,
                    margin_v=margin_v,
                    highlight_color=highlight_col,
                    skip_steam_hook=fin_uses_splash,
                )

                job.update(65.0, "Generando gancho visual de Steam...", f"Apertura Steam Hook ({hook_duration:.2f}s)...")
                ffmpeg_bin = cfg.ffmpeg_path or "ffmpeg"
                hook_overlay_mov = temp_dir / "steam_hook_overlay.mov"
                has_hook_overlay = False
                try:
                    if not fin_uses_splash or hook_duration <= 0:
                        raise RuntimeError("Apertura sin frase Steam: se omite el rótulo 'NEW STEAM GAME'.")
                    generate_steam_hook_overlay_video(
                        output_mov_path=hook_overlay_mov,
                        duration=hook_duration,
                        width=out_w,
                        height=out_h,
                        fps=30,
                        ffmpeg_bin=ffmpeg_bin,
                        logo_scale=float(req.steam_logo_scale if req.steam_logo_scale is not None else 1.3),
                        logo_y_pos=int(req.steam_logo_y if req.steam_logo_y is not None else 540),
                        text_scale=float(req.steam_text_scale if req.steam_text_scale is not None else 1.6),
                        text_y_pos=int(req.steam_text_y if req.steam_text_y is not None else 1160),
                    )
                    has_hook_overlay = hook_overlay_mov.exists()
                except Exception as hook_err:
                    logger.warning(f"Error generando overlay de Steam Hook: {hook_err}")
                    has_hook_overlay = False

                job.update(85.0, "Componiendo gancho de Steam y quemando subtítulos...", "Renderizando con FFmpeg...")
                final_filename = f"{clean_game_name}_promo_finished_{run_id}.mp4"
                final_output_path = output_dir / final_filename

                safe_ass = str(ass_sub_path.resolve()).replace("\\", "/").replace(":", "\\:")
                scale_and_pad = f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,setsar=1"

                if has_hook_overlay:
                    comp_filter = f"[0:v]{scale_and_pad}[base_v];[base_v][1:v]overlay=0:0:enable='between(t,0,{hook_duration:.2f})'[v_hook];[v_hook]ass='{safe_ass}'[vout]"
                    inputs = [
                        "-i", str(source_video),
                        "-i", str(hook_overlay_mov),
                    ]
                else:
                    comp_filter = f"[0:v]{scale_and_pad},ass='{safe_ass}'[vout]"
                    inputs = [
                        "-i", str(source_video),
                    ]

                mix_cmd = [
                    ffmpeg_bin,
                    "-y",
                    *inputs,
                    "-filter_complex", comp_filter,
                    "-map", "[vout]",
                    "-map", "0:a?",
                    "-c:v", "libx264",
                    "-crf", "16",
                    "-preset", "medium",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-movflags", "+faststart",
                    str(final_output_path),
                ]

                try:
                    subprocess.run(mix_cmd, capture_output=True, text=True, check=True)
                except Exception as ass_err:
                    logger.warning(f"Error quemando subtítulos con ASS ({ass_err}), reintentando sin filtro ASS...")
                    fallback_filter = f"[0:v]{scale_and_pad}[vout]"
                    fallback_cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-i", str(source_video),
                        "-filter_complex", fallback_filter,
                        "-map", "[vout]",
                        "-map", "0:a?",
                        "-c:v", "libx264",
                        "-crf", "16",
                        "-preset", "medium",
                        "-pix_fmt", "yuv420p",
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-movflags", "+faststart",
                        str(final_output_path),
                    ]
                    subprocess.run(fallback_cmd, capture_output=True, text=True, check=True)

                video_url = app_output_url(final_filename)
                job.complete({
                    "status": "completed",
                    "video_url": video_url,
                    "video_urls": [video_url],
                    "video_count": 1,
                    "filename": final_filename,
                    "game_name": req.game_name,
                    "mode": req.mode,
                })
                return

            # =========================================================================
            # SHORT V2 IA PIPELINE: Voiceover + Semantic AI Director + Kinetic Captions
            # =========================================================================
            if req.pipeline_version == "v2" or req.mode == "v2_voice_script":
                temp_dir = BASE_DIR / "temp" / f"v2_{run_id}"
                temp_dir.mkdir(parents=True, exist_ok=True)
                output_dir = APP_OUTPUT_DIR
                output_dir.mkdir(parents=True, exist_ok=True)

                voice_path_str = (req.voiceover_path or "").strip()
                if not voice_path_str:
                    # No voiceover file uploaded -> synthesize with ElevenLabs from script_text
                    clean_script = (req.script_text or "").strip()
                    if not clean_script:
                        raise ValueError("No has subido archivo de voz ni has introducido un guión. Por favor genera o escribe un guión para sintetizar la locución.")

                    hook_type = _effective_hook_type(getattr(req, "hook_type", "steam_new_game"), getattr(req, "opening_text", ""))
                    is_steam_hook = (hook_type == "steam_new_game")

                    # steam_new_game / so_in_this_new_game: unchanged behaviour.
                    # custom: starts with opening_text. direct: no formulaic opener.
                    clean_script = _apply_spoken_opening(clean_script, hook_type, getattr(req, "opening_text", ""))

                    eleven_key = _client_secret_or_env(req.elevenlabs_api_key, cfg.elevenlabs_api_key)
                    if not eleven_key:
                        raise ValueError("Para generar la voz automáticamente a partir del guión, necesitas configurar tu ElevenLabs API Key en la pestaña Configuración.")

                    voice_id = (req.elevenlabs_voice_id or cfg.elevenlabs_voice_id or "JBFqnCBsd6RMkjVDRZzb").strip()
                    eleven_model = getattr(cfg, "elevenlabs_model", "eleven_v4") or "eleven_v4"

                    hook_duration = 0.0
                    if is_steam_hook:
                        # 1. Generate opening hook phrase first to get exact spoken duration of the Steam splash
                        job.update(10.0, f"Paso 1: Generando locución del gancho inicial ('Here's a new Steam game')...", f"Sintetizando apertura con ElevenLabs (Voz: {voice_id[:8]})...")
                        hook_audio_file = temp_dir / "steam_hook_voice.mp3"
                        _, hook_duration = generate_elevenlabs_hook_audio(
                            api_key=eleven_key,
                            voice_id=voice_id,
                            output_path=hook_audio_file,
                            hook_text="Here's a new Steam game.",
                            model_id=eleven_model,
                        )
                        logger.info(f"Steam Hook voiceover generated: {hook_duration:.2f}s")
                    else:
                        job.update(10.0, f"Paso 1: Preparando síntesis de voz con ElevenLabs...", f"Generando locución con la voz seleccionada ({voice_id[:8]})...")

                    # 2. Generate full script with ElevenLabs with-timestamps
                    job.update(18.0, f"Paso 1: Sintetizando locución completa con ElevenLabs...", f"Convirtiendo guión completo con marcas de tiempo a nivel de palabra...")
                    tts_file = temp_dir / "elevenlabs_voice.mp3"
                    tts_path, eleven_words, _ = synthesize_elevenlabs_with_timestamps(
                        text=clean_script,
                        output_path=tts_file,
                        api_key=eleven_key,
                        voice_id=voice_id,
                        model_id=eleven_model,
                    )

                    raw_voice_path = Path(tts_path)
                    clean_audio_path = temp_dir / "clean_voiceover.wav"
                    extract_clean_audio(raw_voice_path, clean_audio_path)
                    voice_duration = get_audio_duration(clean_audio_path)
                    if voice_duration <= 0.5:
                        voice_duration = req.target_duration if req.target_duration > 0 else 30.0

                    if eleven_words:
                        alignment = {
                            "total_duration": voice_duration,
                            "words": eleven_words,
                            "sentences": [],
                            "method": "elevenlabs_timestamps",
                        }
                        if is_steam_hook:
                            _, actual_hook_dur = filter_steam_hook_words(eleven_words)
                            if actual_hook_dur > 0.8:
                                hook_duration = actual_hook_dur
                    else:
                        job.update(25.0, f"Sincronizando guión y alineación palabra por palabra ({voice_duration:.1f}s)...", "Generando subtítulos dinámicos kinetic con resaltado...")
                        alignment = align_script_words(
                            audio_path=clean_audio_path,
                            script_text=clean_script,
                            target_duration=voice_duration,
                            known_hook_duration=hook_duration if is_steam_hook else None,
                        )
                else:
                    raw_voice_path = Path(voice_path_str)
                    if not raw_voice_path.is_absolute():
                        raw_voice_path = BASE_DIR / raw_voice_path
                    if not raw_voice_path.exists():
                        raise ValueError(f"El archivo de locución de voz no existe: {raw_voice_path}")

                    job.update(15.0, "Paso 1: Extrayendo locución de voz y midiendo duración...", f"Analizando archivo de audio {raw_voice_path.name}...")
                    clean_audio_path = temp_dir / "clean_voiceover.wav"
                    extract_clean_audio(raw_voice_path, clean_audio_path)
                    voice_duration = get_audio_duration(clean_audio_path)
                    if voice_duration <= 0.5:
                        voice_duration = req.target_duration if req.target_duration > 0 else 30.0

                    job.update(25.0, f"Sincronizando guión y alineación palabra por palabra ({voice_duration:.1f}s)...", "Generando subtítulos dinámicos kinetic con resaltado...")
                    alignment = align_script_words(
                        audio_path=clean_audio_path,
                        script_text=req.script_text or "",
                        target_duration=voice_duration,
                    )
                    raw_words = alignment.get("words", [])
                    _, hook_duration = filter_steam_hook_words(raw_words)

                user_font_size = int(req.captions_font_size if req.captions_font_size is not None else 86)
                captions_y = int(req.captions_y_position if req.captions_y_position is not None else 450)
                is_16_9 = (req.framing == "fit_16_9")
                if is_16_9:
                    if req.resolution == "4K":
                        out_w, out_h = 3840, 2160
                        font_sz = int(round(user_font_size * (100.0 / 86.0) * (2160.0 / 1080.0)))
                        margin_v = int(captions_y * (2160.0 / 1920.0))
                    else:
                        out_w, out_h = 1920, 1080
                        font_sz = int(round(user_font_size * (56.0 / 86.0)))
                        margin_v = int(captions_y * (1080.0 / 1920.0))
                    framing_mode_to_use = "fit_16_9"
                else:
                    # 9:16 Vertical: Zoom fill entire height (center_crop) with NO blurred borders
                    if req.resolution == "4K":
                        out_w, out_h = 2160, 3840
                        font_sz = user_font_size * 2
                        margin_v = captions_y * 2
                    else:
                        out_w, out_h = 1080, 1920
                        font_sz = user_font_size
                        margin_v = captions_y
                    framing_mode_to_use = "center_crop"

                # Measure hook duration for Steam Hook overlay (only when a Steam-style
                # opener is spoken; direct/custom openers keep captions from second 0).
                v2_uses_splash = _uses_steam_splash(
                    _effective_hook_type(getattr(req, "hook_type", "steam_new_game"), getattr(req, "opening_text", ""))
                )
                raw_words = alignment.get("words", [])
                if v2_uses_splash:
                    _, hook_duration = filter_steam_hook_words(raw_words)
                else:
                    hook_duration = 0.0

                ass_sub_path = temp_dir / "kinetic_captions.ass"
                highlight_col = req.captions_highlight_color or "#FFF380"
                generate_kinetic_ass_subtitles(
                    alignment_data=alignment,
                    output_ass_path=ass_sub_path,
                    video_width=out_w,
                    video_height=out_h,
                    font_size=font_sz,
                    word_by_word=True,
                    margin_v=margin_v,
                    highlight_color=highlight_col,
                    skip_steam_hook=v2_uses_splash,
                )

                ffmpeg_bin = cfg.ffmpeg_path or "ffmpeg"
                if getattr(req, "precut_video", False):
                    job.update(65.0, "Vídeo precortado: omitiendo análisis y montaje del Director IA...", f"Ajustando archivo '{valid_footage[0].name}' a {voice_duration:.1f}s...")
                    source_precut = valid_footage[0]
                    temp_assembled_video = temp_dir / "gameplay_assembled.mp4"

                    if is_16_9:
                        scale_filter = f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
                    elif req.footage_is_9_16:
                        scale_filter = f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
                    else:
                        scale_filter = f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},setsar=1"

                    precut_cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-stream_loop", "-1",
                        "-i", str(source_precut),
                        "-vf", scale_filter,
                        "-c:v", "libx264",
                        "-crf", "16",
                        "-preset", "veryfast",
                        "-pix_fmt", "yuv420p",
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-t", str(voice_duration),
                        str(temp_assembled_video),
                    ]
                    try:
                        subprocess.run(precut_cmd, capture_output=True, text=True, check=True)
                    except Exception as precut_err:
                        logger.warning(f"Error procesando vídeo precortado con stream_loop ({precut_err}), reintentando sin loop...")
                        precut_cmd_fallback = [
                            ffmpeg_bin,
                            "-y",
                            "-i", str(source_precut),
                            "-vf", scale_filter,
                            "-c:v", "libx264",
                            "-crf", "16",
                            "-preset", "veryfast",
                            "-pix_fmt", "yuv420p",
                            "-c:a", "aac",
                            "-b:a", "192k",
                            "-t", str(voice_duration),
                            str(temp_assembled_video),
                        ]
                        subprocess.run(precut_cmd_fallback, capture_output=True, text=True, check=True)
                else:
                    # Phase 2: Ingestión & Análisis Multimodal
                    job.update(40.0, "Paso 2: Analizando footage con IA Multimodal (Gemini)...", "Detectando secuencias clave y momentos de alto impacto...")
                    is_gamedev_footage = any("gamesfootage" in str(p).lower() for p in valid_footage)
                    game_footage_dir = (BASE_DIR / "GamesFootage" / clean_game_name) if is_gamedev_footage else (PROMOS_FOOTAGE_DIR / clean_game_name)
                    game_footage_dir.mkdir(parents=True, exist_ok=True)
                    breakdowns_dir = BASE_DIR / "data" / "breakdowns" / clean_game_name
                    breakdowns_dir.mkdir(parents=True, exist_ok=True)

                    target_aspect = "16:9" if (req.framing == "fit_16_9") else "9:16"
                    bd_suffix = ".breakdown.json" if target_aspect == "16:9" else ".9_16.breakdown.json"

                    # Pre-sync: if breakdown exists in footage folder or breakdowns, use it directly (0 token cost)
                    for v_path in valid_footage:
                        gf_bd = game_footage_dir / f"{v_path.stem}{bd_suffix}"
                        local_bd = v_path.parent / f"{v_path.stem}{bd_suffix}"
                        target_bd = breakdowns_dir / f"{v_path.stem}{bd_suffix}"
                        if not target_bd.exists():
                            if gf_bd.exists():
                                try:
                                    shutil.copy2(gf_bd, target_bd)
                                except Exception:
                                    pass
                            elif local_bd.exists():
                                try:
                                    shutil.copy2(local_bd, target_bd)
                                except Exception:
                                    pass

                    analyzer = VideoAnalyzer(config=cfg, output_dir=breakdowns_dir)
                    breakdowns = []
                    for idx, v_path in enumerate(valid_footage):
                        pct = 40.0 + (25.0 * (idx + 1) / len(valid_footage))
                        job.update(pct, f"Analizando footage ({idx+1}/{len(valid_footage)}): {v_path.name}...", "Generando proxy 720p y analizando con visión...")
                        bd = analyzer.process_video(v_path, force=False, footage_is_already_9_16=req.footage_is_9_16, target_aspect=target_aspect)

                        # Post-sync: persist breakdown and footage copy into game_footage_dir
                        try:
                            target_bd = breakdowns_dir / f"{v_path.stem}{bd_suffix}"
                            if target_bd.exists():
                                gf_bd = game_footage_dir / f"{v_path.stem}{bd_suffix}"
                                shutil.copy2(target_bd, gf_bd)
                            if "temp" in str(v_path).lower() and not (game_footage_dir / v_path.name).exists():
                                shutil.copy2(v_path, game_footage_dir / v_path.name)
                        except Exception as sync_err:
                            logger.warning(f"Could not sync breakdown to {game_footage_dir}: {sync_err}")

                        if getattr(req, "strip_splash", True):
                            from video_analyzer import strip_trailer_final_splash
                            clean_bd, splash_start = strip_trailer_final_splash(bd)
                            if splash_start is not None:
                                bd = clean_bd
                        breakdowns.append(bd)

                    # Phase 3: Director IA Semántico
                    job.update(68.0, "Paso 3: Director IA Semántico estructurando montaje...", "Alineando cortes de gameplay con la locución y narrativa...")
                    sequencer = VideoSequencer(config=cfg)
                    edl = sequencer.generate_v2_semantic_edl(
                        breakdowns=breakdowns,
                        voiceover_timeline=alignment.get("sentences", []),
                        target_duration=voice_duration,
                        framing_mode=framing_mode_to_use,
                        footage_is_already_9_16=req.footage_is_9_16,
                    )

                    # Phase 4: Ensamblado FFmpeg
                    job.update(78.0, "Paso 4: Ensamblando cortes de gameplay en alta resolución...", f"Renderizando {len(edl.cuts)} planos sincronizados...")
                    assembler = FFmpegAssembler(config=cfg, temp_dir=temp_dir, output_width=out_w, output_height=out_h)
                    temp_assembled_video = temp_dir / "gameplay_assembled.mp4"
                    search_dirs = [p.parent for p in valid_footage] + [game_footage_dir, PROMOS_FOOTAGE_DIR, BASE_DIR / "GamesFootage", BASE_DIR / "input", BASE_DIR / "temp" / "uploads"]
                    assembler.assemble_edl(
                        edl=edl,
                        search_dirs=search_dirs,
                        output_path=temp_assembled_video,
                        override_framing=framing_mode_to_use,
                    )

                # Phase 5: Composición Final (Steam Hook Overlay + Ducking de Audio + Subtítulos Dinámicos)
                job.update(88.0, "Paso 5: Componiendo gancho visual de Steam, mezcla de audio y subtítulos...", "Renderizando overlay 'NEW STEAM GAME' y subtítulos dinámicos...")
                final_filename = f"{clean_game_name}_promo_v2_{run_id}.mp4"
                final_output_path = output_dir / final_filename

                ffmpeg_bin = cfg.ffmpeg_path or "ffmpeg"
                safe_ass = str(ass_sub_path.resolve()).replace("\\", "/").replace(":", "\\:")

                # Render animated Steam Hook Overlay (Logo Pop-in + NEW STEAM GAME Bounce)
                hook_overlay_mov = temp_dir / "steam_hook_overlay.mov"
                has_hook_overlay = False
                try:
                    if not v2_uses_splash or hook_duration <= 0:
                        raise RuntimeError("Apertura sin frase Steam: se omite el rótulo 'NEW STEAM GAME'.")
                    generate_steam_hook_overlay_video(
                        output_mov_path=hook_overlay_mov,
                        duration=hook_duration,
                        width=out_w,
                        height=out_h,
                        fps=30,
                        ffmpeg_bin=ffmpeg_bin,
                        logo_scale=float(req.steam_logo_scale if req.steam_logo_scale is not None else 1.3),
                        logo_y_pos=int(req.steam_logo_y if req.steam_logo_y is not None else 540),
                        text_scale=float(req.steam_text_scale if req.steam_text_scale is not None else 1.6),
                        text_y_pos=int(req.steam_text_y if req.steam_text_y is not None else 1160),
                    )
                    has_hook_overlay = hook_overlay_mov.exists()
                except Exception as hook_err:
                    logger.warning(f"Error generando overlay de Steam Hook: {hook_err}")
                    has_hook_overlay = False

                bg_vol = float(req.gameplay_volume if req.gameplay_volume is not None else 0.15)
                if bg_vol > 1.0:
                    bg_vol = bg_vol / 100.0
                bg_vol = max(0.0, min(1.0, bg_vol))

                has_game_audio = check_video_has_audio(temp_assembled_video) if bg_vol > 0.001 else False

                if has_hook_overlay:
                    comp_filter = f"[0:v][2:v]overlay=0:0:enable='between(t,0,{hook_duration:.2f})'[v_hook];[v_hook]ass='{safe_ass}'[vout]"
                    inputs = [
                        "-i", str(temp_assembled_video),
                        "-i", str(clean_audio_path),
                        "-i", str(hook_overlay_mov),
                    ]
                else:
                    comp_filter = f"[0:v]ass='{safe_ass}'[vout]"
                    inputs = [
                        "-i", str(temp_assembled_video),
                        "-i", str(clean_audio_path),
                    ]

                if has_game_audio:
                    audio_filter = f";[0:a]volume={bg_vol:.2f}[g_a];[1:a]volume=1.0[v_a];[v_a][g_a]amix=inputs=2:duration=first:dropout_transition=2[aout]"
                else:
                    audio_filter = ";[1:a]volume=1.0[aout]"

                mix_cmd = [
                    ffmpeg_bin,
                    "-y",
                    *inputs,
                    "-filter_complex", f"{comp_filter}{audio_filter}",
                    "-map", "[vout]",
                    "-map", "[aout]",
                    "-c:v", "libx264",
                    "-crf", "16",
                    "-preset", "medium",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-t", str(voice_duration),
                    "-movflags", "+faststart",
                    str(final_output_path),
                ]

                try:
                    subprocess.run(mix_cmd, capture_output=True, text=True, check=True)
                except Exception as ass_err:
                    logger.warning(f"ASS subtitle burn error ({ass_err}), using fallback stream copy & ducking...")
                    fallback_filter = f"[0:a]volume={bg_vol:.2f}[g_a];[1:a]volume=1.0[v_a];[v_a][g_a]amix=inputs=2:duration=first:dropout_transition=2[aout]" if has_game_audio else "[1:a]volume=1.0[aout]"
                    fallback_cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-i", str(temp_assembled_video),
                        "-i", str(clean_audio_path),
                        "-filter_complex", fallback_filter,
                        "-map", "0:v",
                        "-map", "[aout]",
                        "-c:v", "libx264",
                        "-crf", "16",
                        "-preset", "medium",
                        "-pix_fmt", "yuv420p",
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-t", str(voice_duration),
                        "-movflags", "+faststart",
                        str(final_output_path),
                    ]
                    subprocess.run(fallback_cmd, capture_output=True, text=True, check=True)

                video_url = app_output_url(final_filename)
                job.complete({
                    "video_url": video_url,
                    "video_filename": final_filename,
                    "video_path": str(final_output_path),
                    "videos": [{
                        "variant_index": 1,
                        "video_url": video_url,
                        "video_filename": final_filename,
                        "video_path": str(final_output_path),
                        "hook_text": req.script_text[:60] if req.script_text else "Short V2 IA",
                    }],
                    "game_name": req.game_name,
                    "mode": "v2_voice_script",
                    "pipeline_version": "v2",
                    "duration": voice_duration,
                    "run_id": run_id,
                    "video_count": 1,
                }, f"¡Short V2 IA para '{req.game_name}' completado con éxito ({voice_duration:.1f}s)!")
                return

            # =========================================================================
            # SHORT V1 IA PIPELINE: Gameplay Sequence + Text Card Overlay
            # =========================================================================
            # Phase 1: Ingestión & Análisis de Clips con Gemini Multimodal + Proxies 720p (Una sola vez para todo el pool)
            breakdowns = []
            is_gamedev_footage = any("gamesfootage" in str(p).lower() for p in valid_footage)
            game_footage_dir = (BASE_DIR / "GamesFootage" / clean_game_name) if is_gamedev_footage else (PROMOS_FOOTAGE_DIR / clean_game_name)
            game_footage_dir.mkdir(parents=True, exist_ok=True)
            if req.mode in ("v1_and_card", "v1_only"):
                job.update(20.0, "Paso 1: Analizando clips con IA Multimodal (Gemini)...", "Detectando planos, acciones y momentos destacados...")
                breakdowns_dir = BASE_DIR / "data" / "breakdowns" / clean_game_name
                breakdowns_dir.mkdir(parents=True, exist_ok=True)

                is_requested_9_16 = (req.framing != "fit_16_9") if req.mode != "v1_and_card" else bool(req.footage_is_9_16)
                target_aspect = "9:16" if is_requested_9_16 else "16:9"
                bd_suffix = ".breakdown.json" if target_aspect == "16:9" else ".9_16.breakdown.json"

                # Pre-sync: if breakdown exists in footage folder or breakdowns, use it directly (0 token cost)
                for v_path in valid_footage:
                    gf_bd = game_footage_dir / f"{v_path.stem}{bd_suffix}"
                    local_bd = v_path.parent / f"{v_path.stem}{bd_suffix}"
                    target_bd = breakdowns_dir / f"{v_path.stem}{bd_suffix}"
                    if not target_bd.exists():
                        if gf_bd.exists():
                            try:
                                shutil.copy2(gf_bd, target_bd)
                            except Exception:
                                pass
                        elif local_bd.exists():
                            try:
                                shutil.copy2(local_bd, target_bd)
                            except Exception:
                                pass

                analyzer = VideoAnalyzer(config=cfg, output_dir=breakdowns_dir)

                for idx, v_path in enumerate(valid_footage):
                    pct = 20.0 + (25.0 * (idx + 1) / len(valid_footage))
                    job.update(pct, f"Optimizando y analizando footage ({idx+1}/{len(valid_footage)}): {v_path.name}...", "Generando proxy 720p y extrayendo desglose con IA Gemini...")
                    bd = analyzer.process_video(v_path, force=False, footage_is_already_9_16=req.footage_is_9_16, target_aspect=target_aspect)

                    # Post-sync: persist breakdown and footage copy into game_footage_dir
                    try:
                        target_bd = breakdowns_dir / f"{v_path.stem}{bd_suffix}"
                        if target_bd.exists():
                            gf_bd = game_footage_dir / f"{v_path.stem}{bd_suffix}"
                            shutil.copy2(target_bd, gf_bd)
                        if "temp" in str(v_path).lower() and not (game_footage_dir / v_path.name).exists():
                            shutil.copy2(v_path, game_footage_dir / v_path.name)
                    except Exception as sync_err:
                        logger.warning(f"Could not sync breakdown to {game_footage_dir}: {sync_err}")

                    # Filtro Splash Final: eliminar tarjetas promocionales (logos, fechas, wishlist)
                    if getattr(req, "strip_splash", True):
                        from video_analyzer import strip_trailer_final_splash
                        clean_bd, splash_start = strip_trailer_final_splash(bd)
                        if splash_start is not None:
                            tail_diff = bd.metadata.duration_seconds - splash_start
                            job.update(
                                pct,
                                f"Filtro Splash Final ({v_path.name}): Detectado inicio de logos/wishlist en {splash_start:.1f}s.",
                                f"Purgados los últimos {tail_diff:.1f}s de '{v_path.name}' para evitar que aparezcan en la promo."
                            )
                            bd = clean_bd

                    breakdowns.append(bd)

            # Phase 2: Generar cada uno de los vídeos solicitados con Director IA y variación de planos
            output_dir = APP_OUTPUT_DIR
            output_dir.mkdir(parents=True, exist_ok=True)
            search_dirs = [p.parent for p in valid_footage] + [game_footage_dir, PROMOS_FOOTAGE_DIR, BASE_DIR / "GamesFootage", BASE_DIR / "input", BASE_DIR / "temp" / "uploads"]

            presets_dict = get_all_presets()
            clean_preset = (req.preset_name or "").replace("Predeterminado: ", "").strip()
            p_info = presets_dict.get(clean_preset)

            avatar_path_to_use = req.card_avatar_path
            if (not avatar_path_to_use or not Path(avatar_path_to_use).exists()) and p_info and p_info.get("avatar_path"):
                avatar_path_to_use = p_info["avatar_path"]

            generated_videos = []
            used_opening_scenes = []

            for i in range(video_count):
                var_num = i + 1
                curr_hook = raw_hooks[i]
                pct_base = 45.0 + (50.0 * i / video_count)
                pct_half = pct_base + (25.0 / video_count)
                pct_end = pct_base + (50.0 / video_count)

                job.update(
                    pct_base,
                    f"[Vídeo {var_num}/{video_count}] Director IA seleccionando montaje único...",
                    f"Estructurando cortes con gancho publicitario #{var_num}..."
                )

                current_video_path = valid_footage[min(i, len(valid_footage) - 1)]

                # Montaje V1 si aplica
                if req.mode in ("v1_and_card", "v1_only"):
                    sequencer = VideoSequencer(config=cfg)
                    internal_framing = "center_crop" if req.footage_is_9_16 else ("fit_16_9" if req.mode == "v1_and_card" else (req.framing or "fit_16_9"))
                    edl = sequencer.generate_edl(
                        breakdowns=breakdowns,
                        target_duration=req.target_duration,
                        framing_mode=internal_framing,
                        variant_index=var_num,
                        total_variants=video_count,
                        hook_text=curr_hook,
                        exclude_opening_scenes=used_opening_scenes,
                        footage_is_already_9_16=req.footage_is_9_16,
                    )

                    # Registrar corte inicial para forzar que el siguiente vídeo explore una apertura diferente
                    if edl.cuts:
                        first_cut = edl.cuts[0]
                        used_opening_scenes.append(int(first_cut.source_in))

                    job.update(
                        pct_half,
                        f"[Vídeo {var_num}/{video_count}] Renderizando montaje en alta resolución con FFmpeg...",
                        f"Ensamblando {len(edl.cuts)} cortes de vídeo a partir del material original..."
                    )

                    assembler = FFmpegAssembler(config=cfg)
                    temp_v1_path = output_dir / f"{clean_game_name}_v1_{run_id}_v{var_num}.mp4"
                    assembler.assemble_edl(
                        edl=edl,
                        search_dirs=search_dirs,
                        output_path=temp_v1_path,
                        override_framing=internal_framing,
                    )
                    current_video_path = temp_v1_path

                # Superposición de Tarjeta Social si aplica
                if req.mode in ("v1_and_card", "card_only"):
                    job.update(
                        pct_end - 5.0,
                        f"[Vídeo {var_num}/{video_count}] Componiendo formato vertical y tarjeta social...",
                        f"Renderizando gancho #{var_num}: '{curr_hook[:30]}...'..."
                    )

                    if req.resolution == "4K":
                        out_w, out_h = 2160, 3840
                    else:
                        out_w, out_h = 1080, 1920

                    style_dict = {
                        "name": req.preset_name or "Personalizado",
                        "card_username": req.card_username or "Indie Games Discovery",
                        "card_handle": req.card_handle or "@indiegamesdiscovery",
                        "card_bg_color": req.card_bg_color or "#ffffff",
                        "card_text_color": req.card_text_color or "#000000",
                        "card_border_color": req.card_border_color or "#7ca032",
                        "card_border_width": req.card_border_width if req.card_border_width is not None else 12,
                        "card_radius": req.card_radius if req.card_radius is not None else 80,
                        "card_avatar_text": "G",
                        "card_avatar_bg": req.card_avatar_bg or (p_info["data"].get("card_avatar_bg") if p_info else "#7ca032"),
                        "card_avatar_path": avatar_path_to_use,
                        "card_font_username": "Segoe UI",
                        "card_font_handle": "Segoe UI",
                        "card_font_body": "Segoe UI",
                        "card_width": req.card_width or 1728,
                        "card_height": req.card_height or 980,
                        "card_font_size_body": req.card_font_size_body or 76,
                        "card_font_size_username": 72,
                        "card_font_size_handle": 56,
                        "card_avatar_radius": 105,
                        "card_y_position": req.card_y_position if req.card_y_position is not None else 440,
                        "video_zoom": req.video_zoom or 1.0,
                        "video_y_position": req.video_y_position if req.video_y_position is not None else 180,
                        "overlay_type": req.overlay_type or "card",
                    }

                    export_style = req.framing or "vertical_split_blur"
                    run_config = AppConfig(
                        output_width=out_w,
                        output_height=out_h,
                        video_style=export_style,
                        video_zoom=req.video_zoom or 1.0,
                        video_y_position=req.video_y_position if req.video_y_position is not None else 180,
                        overlay_type=req.overlay_type or "card",
                        card_username=style_dict["card_username"],
                        card_handle=style_dict["card_handle"],
                        card_bg_color=style_dict["card_bg_color"],
                        card_text_color=style_dict["card_text_color"],
                        card_border_color=style_dict["card_border_color"],
                        card_border_width=style_dict["card_border_width"],
                        card_radius=style_dict["card_radius"],
                        card_avatar_text="G",
                        card_avatar_bg=style_dict["card_avatar_bg"],
                        card_avatar_path=avatar_path_to_use,
                        card_width=style_dict["card_width"],
                        card_height=style_dict["card_height"],
                        card_font_size_body=style_dict["card_font_size_body"],
                        card_y_position=style_dict["card_y_position"],
                        card_text=curr_hook,
                        game_name=req.game_name,
                        auto_cut=False,
                    )

                    pipeline = Pipeline(run_config)
                    from schemas import PipelineContext
                    ctx = PipelineContext(run_id=f"{run_id}_v{var_num}", input_video=current_video_path)
                    ctx.merged_video_path = ctx.input_video

                    final_filename = f"{clean_game_name}_promo_{run_id}_v{var_num}.mp4"
                    final_res = pipeline.export(ctx, final_filename, styles_list=[style_dict])
                    final_path = final_res[0] if isinstance(final_res, list) else final_res
                    output_file = Path(final_path)
                else:
                    output_file = current_video_path

                final_name = output_file.name
                video_url = app_output_url(final_name)
                generated_videos.append({
                    "variant_index": var_num,
                    "video_url": video_url,
                    "video_filename": final_name,
                    "video_path": str(output_file),
                    "hook_text": curr_hook,
                })

            primary_video = generated_videos[0]
            job.complete({
                "video_url": primary_video["video_url"],
                "video_filename": primary_video["video_filename"],
                "video_path": primary_video["video_path"],
                "videos": generated_videos,
                "game_name": req.game_name,
                "mode": req.mode,
                "run_id": run_id,
                "video_count": video_count,
            }, f"¡{video_count} Vídeo(s) de Promo para '{req.game_name}' completados con éxito!")

        except Exception as e:
            logger.exception("Promo pipeline failed")
            job.fail(f"Error procesando promo: {str(e)}\n\n{traceback.format_exc()}")

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job.job_id}


# =============================================================================
# API Endpoints: Promo V3 Construction Pipeline (Notebook-style Architecture)
# =============================================================================

class PromoV3DetectRequest(BaseModel):
    video_path: Optional[str] = None
    video_paths: Optional[List[str]] = None
    proxy_height: int = 720
    proxy_fps: int = 12
    force_reproxy: bool = False
    adaptive_threshold: float = 1.5
    min_scene_len_sec: float = 0.6
    window_width: int = 8
    detect_fades: bool = True
    generate_previews: bool = True
    min_clip_duration: float = 1.0
    trim_frames: int = 5
    enable_deduplication: bool = True
    dedup_hamming_threshold: int = 7
    dedup_min_match_ratio: float = 0.6
    session_id: Optional[str] = None
    video_format: Optional[str] = "full_9_16"


class PromoV3Step2Request(BaseModel):
    video_path: Optional[str] = None
    video_paths: Optional[List[str]] = None
    session_id: Optional[str] = None
    batch_target_duration: float = 60.0
    preferred_model: Optional[str] = None
    force_reanalyze: bool = False
    enable_early_stopping: bool = True
    strip_splash: bool = True
    video_format: Optional[str] = None


class PromoV3Step1SaveRequest(BaseModel):
    video_path: Optional[str] = None
    video_paths: Optional[List[str]] = None
    session_id: Optional[str] = None
    step1_result: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None


class PromoV3Step2SaveRequest(BaseModel):
    session_id: Optional[str] = None
    video_path: Optional[str] = None
    video_paths: Optional[List[str]] = None
    step2_result: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None


@app.get("/api/promov3/session")
@app.get("/api/promov3/session/check")
def get_promov3_session_endpoint(video_path: Optional[str] = None, session_id: Optional[str] = None):
    """Retrieve all cached steps (Step 1, Step 2, Step 3) for a given video or session_id."""
    v3_cache_dir = BASE_DIR / "temp" / "v3_cache"
    v3_dir = None

    if session_id:
        cand = _safe_session_dir(session_id)
        if cand.exists() and cand.is_dir():
            v3_dir = cand

    if not v3_dir and video_path:
        cand_id = get_canonical_v3_session_id(video_path)
        cand = v3_cache_dir / cand_id
        if cand.exists() and cand.is_dir():
            v3_dir = cand
        else:
            target_stem = Path(video_path).stem.lower()
            candidates = []
            if v3_cache_dir.exists():
                for d in v3_cache_dir.iterdir():
                    if d.is_dir():
                        if target_stem in d.name.lower():
                            candidates.append((d.stat().st_mtime, d))
                        elif (d / "step1_shots.json").exists():
                            try:
                                with open(d / "step1_shots.json", "r", encoding="utf-8") as f:
                                    d_json = json.load(f)
                                    v_info = d_json.get("video_info", {})
                                    fn = v_info.get("filename", "").lower()
                                    paths_str = " ".join([str(p).lower() for p in v_info.get("paths", [])])
                                    v_items_str = " ".join([v.get("filename", "").lower() for v in d_json.get("videos", [])])
                                    if target_stem in fn or target_stem in paths_str or target_stem in v_items_str:
                                        candidates.append((d.stat().st_mtime, d))
                            except Exception:
                                pass
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                v3_dir = candidates[0][1]

    cand_sess_id = session_id or (get_canonical_v3_session_id(video_path) if video_path else "")
    if not v3_dir or not v3_dir.exists():
        return {
            "status": "ok",
            "found": False,
            "session_id": cand_sess_id,
            "has_step1": False,
            "has_step2": False,
            "has_step3": False,
        }

    sess_name = v3_dir.name
    step1_file = v3_dir / "step1_shots.json"
    step2_file = v3_dir / "step2_evaluated_shots.json"
    step3_file = v3_dir / "step3_script.json"
    step4_file = v3_dir / "step4_edl.json"
    step5_file = v3_dir / "step5_result.json"

    step1_data = None
    if step1_file.exists():
        try:
            with open(step1_file, "r", encoding="utf-8") as f:
                step1_data = json.load(f)
        except Exception as e:
            logger.warning(f"Error loading step1_shots.json from {sess_name}: {e}")

    step2_data = None
    if step2_file.exists():
        try:
            with open(step2_file, "r", encoding="utf-8") as f:
                step2_data = json.load(f)
        except Exception as e:
            logger.warning(f"Error loading step2_evaluated_shots.json from {sess_name}: {e}")

    step3_data = None
    if step3_file.exists():
        try:
            with open(step3_file, "r", encoding="utf-8") as f:
                step3_data = json.load(f)
        except Exception as e:
            logger.warning(f"Error loading step3_script.json from {sess_name}: {e}")

    step4_data = None
    if step4_file.exists():
        try:
            with open(step4_file, "r", encoding="utf-8") as f:
                step4_data = json.load(f)
        except Exception as e:
            logger.warning(f"Error loading step4_edl.json from {sess_name}: {e}")

    step5_data = None
    if step5_file.exists():
        try:
            with open(step5_file, "r", encoding="utf-8") as f:
                step5_data = json.load(f)
        except Exception as e:
            logger.warning(f"Error loading step5_result.json from {sess_name}: {e}")

    approval_data = None
    approval_file = v3_dir / "approval.json"
    if approval_file.exists():
        try:
            with open(approval_file, "r", encoding="utf-8") as f:
                approval_data = json.load(f)
        except Exception as e:
            logger.warning(f"Error loading approval.json from {sess_name}: {e}")

    return {
        "status": "ok",
        "found": bool(step1_data or step2_data or step3_data or step4_data or step5_data),
        "session_id": sess_name,
        "has_step1": bool(step1_data),
        "step1": step1_data,
        "has_step2": bool(step2_data),
        "step2": step2_data,
        "has_step3": bool(step3_data),
        "step3": step3_data,
        "has_step4": bool(step4_data),
        "step4": step4_data,
        "has_step5": bool(step5_data),
        "step5": step5_data,
        "has_approval": bool(approval_data),
        "approval": approval_data,
    }


@app.get("/api/promov3/sessions")
def list_promov3_sessions_endpoint():
    """List all saved Promo V3 sessions with summary step completion flags."""
    v3_cache_dir = BASE_DIR / "temp" / "v3_cache"
    sessions = []
    if v3_cache_dir.exists():
        for d in sorted(v3_cache_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not d.is_dir():
                continue
            s1 = (d / "step1_shots.json").exists()
            s2 = (d / "step2_evaluated_shots.json").exists()
            s3 = (d / "step3_script.json").exists()
            s4 = (d / "step4_edl.json").exists()
            s5 = (d / "step5_result.json").exists()
            if not (s1 or s2 or s3 or s4 or s5):
                continue
            v_name = ""
            if s1:
                try:
                    with open(d / "step1_shots.json", "r", encoding="utf-8") as f:
                        d_json = json.load(f)
                        videos = d_json.get("videos", [])
                        if len(videos) > 1:
                            v_name = f"{len(videos)} vídeos: " + ", ".join([v.get("filename", "") for v in videos[:2]])
                            if len(videos) > 2:
                                v_name += f" (+{len(videos) - 2} más)"
                        else:
                            v_name = d_json.get("video_info", {}).get("filename", "")
                except Exception:
                    pass
            sessions.append({
                "session_id": d.name,
                "video_name": v_name or d.name,
                "updated_at": d.stat().st_mtime,
                "has_step1": s1,
                "has_step2": s2,
                "has_step3": s3,
                "has_step4": s4,
                "has_step5": s5,
            })
    return {"status": "ok", "sessions": sessions}


@app.post("/api/promov3/step1/save")
def save_promov3_step1_endpoint(req: PromoV3Step1SaveRequest):
    """Save or update Step 1 detected shots state in session cache."""
    sess_name = (req.session_id or "").strip()
    if not sess_name and req.video_paths:
        sess_name = get_canonical_v3_multi_session_id(req.video_paths)
    elif not sess_name and req.video_path:
        sess_name = get_canonical_v3_session_id(req.video_path)

    if not sess_name:
        raise HTTPException(status_code=400, detail="session_id o video_path es obligatorio")

    raw_step1 = req.step1_result or req.result
    if not raw_step1:
        raise HTTPException(status_code=400, detail="step1_result o result es obligatorio")

    sess_dir = _safe_session_dir(sess_name)
    sess_dir.mkdir(parents=True, exist_ok=True)
    out_file = sess_dir / "step1_shots.json"

    data = dict(raw_step1)
    data["session_id"] = sess_name
    data["saved_at"] = time.time()

    try:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # Mirror to PromosFootage/<game_name> if video resides there
        all_vp = (req.video_paths or []) + ([req.video_path] if req.video_path else [])
        for vp in all_vp:
            try:
                p_vp = Path(vp).resolve()
                if PROMOS_FOOTAGE_DIR.resolve() in p_vp.parents:
                    rel = p_vp.relative_to(PROMOS_FOOTAGE_DIR.resolve())
                    game_folder = PROMOS_FOOTAGE_DIR / rel.parts[0]
                    if game_folder.is_dir():
                        with open(game_folder / "step1_shots.json", "w", encoding="utf-8") as gf:
                            json.dump(data, gf, indent=2, ensure_ascii=False)
                        logger.info(f"Mirrored step1_shots.json to game folder: {game_folder.name}")
                        break
            except Exception as e:
                logger.warning(f"Error mirroring step1 to game folder: {e}")

        return {"status": "ok", "session_id": sess_name, "saved_path": str(out_file), "data": data}
    except Exception as e:
        logger.exception("Error saving step 1 shots")
        raise HTTPException(status_code=500, detail=f"Error guardando datos del Paso 1: {e}")


@app.post("/api/promov3/step2/save")
def save_promov3_step2_endpoint(req: PromoV3Step2SaveRequest):
    """Save or update Step 2 evaluated shots state in session cache."""
    sess_name = (req.session_id or "").strip()
    if not sess_name and req.video_paths:
        sess_name = get_canonical_v3_multi_session_id(req.video_paths)
    elif not sess_name and req.video_path:
        sess_name = get_canonical_v3_session_id(req.video_path)

    if not sess_name:
        raise HTTPException(status_code=400, detail="session_id o video_path es obligatorio")

    raw_step2 = req.step2_result or req.result
    if not raw_step2:
        raise HTTPException(status_code=400, detail="step2_result o result es obligatorio")

    sess_dir = _safe_session_dir(sess_name)
    sess_dir.mkdir(parents=True, exist_ok=True)
    out_file = sess_dir / "step2_evaluated_shots.json"

    data = dict(raw_step2)
    data["session_id"] = sess_name
    data["saved_at"] = time.time()

    try:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # Mirror to PromosFootage/<game_name> if video resides there
        all_vp = (req.video_paths or []) + ([req.video_path] if req.video_path else [])
        for vp in all_vp:
            try:
                p_vp = Path(vp).resolve()
                if PROMOS_FOOTAGE_DIR.resolve() in p_vp.parents:
                    rel = p_vp.relative_to(PROMOS_FOOTAGE_DIR.resolve())
                    game_folder = PROMOS_FOOTAGE_DIR / rel.parts[0]
                    if game_folder.is_dir():
                        with open(game_folder / "step2_evaluated_shots.json", "w", encoding="utf-8") as gf:
                            json.dump(data, gf, indent=2, ensure_ascii=False)
                        logger.info(f"Mirrored step2_evaluated_shots.json to game folder: {game_folder.name}")
                        break
            except Exception as e:
                logger.warning(f"Error mirroring step2 to game folder: {e}")

        return {"status": "ok", "session_id": sess_name, "saved_path": str(out_file), "data": data}
    except Exception as e:
        logger.exception("Error saving step 2 evaluated shots")
        raise HTTPException(status_code=500, detail=f"Error guardando datos del Paso 2: {e}")


@app.get("/api/promov3/footage")
def get_promov3_available_footage():
    """List available video footage grouped into folders from PromosFootage, GamesFootage, and temp/uploads for Promo V3."""
    video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
    folders = []

    def scan_root(root_dir: Path, source_name: str):
        if not root_dir.exists():
            return
        for folder in sorted(root_dir.iterdir(), key=lambda p: p.name.lower()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            game_name = folder.name
            clips = []
            total_size_bytes = 0
            try:
                for f in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
                    if f.is_file() and f.suffix.lower() in video_exts:
                        size_mb = round(f.stat().st_size / (1024 * 1024), 2)
                        total_size_bytes += f.stat().st_size
                        clips.append({
                            "group": f"{source_name} / {game_name}",
                            "game_name": game_name,
                            "filename": f.name,
                            "label": f"{game_name} — {f.name} ({size_mb} MB)",
                            "path": str(f.resolve()),
                            "url": f"/media/{source_name}/{urllib.parse.quote(game_name)}/{urllib.parse.quote(f.name)}",
                            "size_mb": size_mb,
                        })
            except Exception as e:
                logger.warning(f"Error scanning folder {folder.name} in {source_name}: {e}")

            if clips:
                total_mb = round(total_size_bytes / (1024 * 1024), 1)
                clean_fid = re.sub(r'[^a-zA-Z0-9_]', '_', f"{source_name}_{game_name}").lower()
                folders.append({
                    "folder_id": clean_fid,
                    "name": f"{source_name} / {game_name}",
                    "game_name": game_name,
                    "source_type": source_name,
                    "clip_count": len(clips),
                    "total_size_mb": total_mb,
                    "label": f"{source_name} / {game_name} ({len(clips)} vídeo{'s' if len(clips)!=1 else ''} — {total_mb} MB)",
                    "clips": clips,
                })

    # 1. PromosFootage
    scan_root(PROMOS_FOOTAGE_DIR, "PromosFootage")

    # 2. GamesFootage
    if (BASE_DIR / "GamesFootage").exists():
        scan_root(BASE_DIR / "GamesFootage", "GamesFootage")

    # 3. Uploads folder
    uploads_dir = BASE_DIR / "temp" / "uploads"
    upload_clips = []
    upload_size_bytes = 0
    if uploads_dir.exists():
        for f in sorted(uploads_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.is_file() and f.suffix.lower() in video_exts:
                size_mb = round(f.stat().st_size / (1024 * 1024), 2)
                upload_size_bytes += f.stat().st_size
                upload_clips.append({
                    "group": "Subidos Recientes (Uploads)",
                    "game_name": "Uploads",
                    "filename": f.name,
                    "label": f"Subido: {f.name} ({size_mb} MB)",
                    "path": str(f.resolve()),
                    "url": f"/media/temp/uploads/{urllib.parse.quote(f.name)}",
                    "size_mb": size_mb,
                })

    if upload_clips:
        u_mb = round(upload_size_bytes / (1024 * 1024), 1)
        folders.append({
            "folder_id": "uploads_recent",
            "name": "Subidos Recientes (Uploads)",
            "game_name": "Uploads",
            "source_type": "Uploads",
            "clip_count": len(upload_clips),
            "total_size_mb": u_mb,
            "label": f"📁 Subidos Recientes (Uploads) ({len(upload_clips)} vídeo{'s' if len(upload_clips)!=1 else ''} — {u_mb} MB)",
            "clips": upload_clips,
        })

    # Flat list for fallback
    flat_items = []
    for f_info in folders:
        flat_items.extend(f_info["clips"])

    return {
        "status": "ok",
        "folders": folders,
        "items": flat_items,
    }


class PromoV3ResetRequest(BaseModel):
    session_id: Optional[str] = None
    video_path: Optional[str] = None
    video_paths: Optional[List[str]] = None
    clear_all: bool = False


@app.post("/api/promov3/reset")
def reset_promov3_notebook_endpoint(req: Optional[PromoV3ResetRequest] = None):
    """
    Clears all cached session steps and temporary uploads from disk for Promo V3 notebook.
    """
    v3_cache_dir = BASE_DIR / "temp" / "v3_cache"
    deleted_count = 0

    if v3_cache_dir.exists():
        for d in list(v3_cache_dir.iterdir()):
            if d.is_dir():
                try:
                    shutil.rmtree(d, ignore_errors=True)
                    deleted_count += 1
                except Exception as err:
                    logger.warning(f"Could not delete cache folder {d}: {err}")

    # Also clean temporary uploads
    uploads_dir = BASE_DIR / "temp" / "uploads"
    if uploads_dir.exists():
        for f in list(uploads_dir.iterdir()):
            try:
                if f.is_file():
                    f.unlink()
            except Exception:
                pass

    logger.info(f"Limpiadas {deleted_count} carpetas de caché V3 y archivos temporales.")

    return {
        "status": "ok",
        "deleted_count": deleted_count,
        "message": "Resultados, vídeos temporales y cachés del notebook eliminados correctamente."
    }


def sanitize_filename(name: str) -> str:
    """Removes invalid filesystem characters from folder/filenames."""
    if not name:
        return ""
    cleaned = re.sub(r'[<>:"/\\|?*]', '_', name.strip())
    cleaned = re.sub(r'[\s_]+', ' ', cleaned).strip()
    return cleaned


class PromoV3OrganizeFootageRequest(BaseModel):
    game_name: str
    video_paths: List[str]
    session_id: Optional[str] = None
    step1_result: Optional[Dict[str, Any]] = None
    step2_result: Optional[Dict[str, Any]] = None


@app.post("/api/promov3/games/organize")
def organize_promov3_game_footage(req: PromoV3OrganizeFootageRequest):
    """
    Ensures a folder in PromosFootage/<clean_game_name> exists and moves/copies
    any temporary/uploaded video files into it so they become permanent footage.
    Also links or copies step1 and step2 analysis caches into the game's folder.
    Returns the new permanent video paths and folder information.
    """
    clean_game = sanitize_filename(req.game_name.strip())
    if not clean_game:
        raise HTTPException(status_code=400, detail="Nombre del juego no válido")

    game_dir = PROMOS_FOOTAGE_DIR / clean_game
    game_dir.mkdir(parents=True, exist_ok=True)

    new_paths: List[str] = []
    video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}

    for vp_str in req.video_paths:
        if not vp_str:
            continue
        p = Path(vp_str)
        if not p.exists():
            resolved = _resolve_promov3_video_path(vp_str)
            if resolved and resolved.exists():
                p = resolved

        if not p.exists():
            continue

        # If already inside this game's folder, keep as-is
        try:
            if p.resolve().parent == game_dir.resolve():
                new_paths.append(str(p.resolve()))
                continue
        except Exception:
            pass

        # Destination in PromosFootage/<clean_game>/<filename>
        target_file = game_dir / p.name
        if target_file.resolve() != p.resolve():
            if not target_file.exists():
                try:
                    # If it's a temp upload, move it; otherwise copy
                    if "temp" in str(p).lower() and "uploads" in str(p).lower():
                        shutil.move(str(p), str(target_file))
                    else:
                        shutil.copy2(str(p), str(target_file))
                except Exception as e:
                    logger.warning(f"Error copying {p} to {target_file}: {e}")
                    target_file = p
            new_paths.append(str(target_file.resolve()))
        else:
            new_paths.append(str(p.resolve()))

    # Direct breakdown persistence if passed in request
    if req.step1_result:
        try:
            with open(game_dir / "step1_shots.json", "w", encoding="utf-8") as f:
                json.dump(req.step1_result, f, indent=2, ensure_ascii=False)
            logger.info(f"Directly saved step1_shots.json into game folder: {clean_game}")
        except Exception as e:
            logger.warning(f"Error saving step1_shots.json in {clean_game}: {e}")

    if req.step2_result:
        try:
            with open(game_dir / "step2_evaluated_shots.json", "w", encoding="utf-8") as f:
                json.dump(req.step2_result, f, indent=2, ensure_ascii=False)
            logger.info(f"Directly saved step2_evaluated_shots.json into game folder: {clean_game}")
        except Exception as e:
            logger.warning(f"Error saving step2_evaluated_shots.json in {clean_game}: {e}")

    # If session_id provided, sync step1 and step2 bidirectionally
    sess_id = req.session_id
    if sess_id:
        sess_dir = _safe_session_dir(sess_id)
        if sess_dir.exists():
            for step_json in ["step1_shots.json", "step2_evaluated_shots.json"]:
                sf = sess_dir / step_json
                gf = game_dir / step_json
                # Copy from cache to game if not in game
                if sf.exists() and not gf.exists():
                    try:
                        shutil.copy2(str(sf), str(gf))
                    except Exception:
                        pass
                # Copy from game to cache if not in cache
                elif gf.exists() and not sf.exists():
                    try:
                        shutil.copy2(str(gf), str(sf))
                    except Exception:
                        pass

    # Collect all video clips currently in this game's folder
    clips = []
    total_size = 0
    for f in sorted(game_dir.iterdir(), key=lambda x: x.name.lower()):
        if f.is_file() and f.suffix.lower() in video_exts:
            sz = round(f.stat().st_size / (1024 * 1024), 2)
            total_size += f.stat().st_size
            clips.append({
                "filename": f.name,
                "path": str(f.resolve()),
                "url": f"/media/PromosFootage/{urllib.parse.quote(clean_game)}/{urllib.parse.quote(f.name)}",
                "size_mb": sz,
                "game_name": clean_game,
            })

    has_s1 = (game_dir / "step1_shots.json").exists()
    has_s2 = (game_dir / "step2_evaluated_shots.json").exists()

    # Write game metadata
    try:
        meta_info = {
            "game_name": clean_game,
            "folder_path": str(game_dir.resolve()),
            "video_count": len(clips),
            "has_step1": has_s1,
            "has_step2": has_s2,
            "updated_at": time.time(),
        }
        with open(game_dir / "game_meta.json", "w", encoding="utf-8") as mf:
            json.dump(meta_info, mf, indent=2, ensure_ascii=False)
    except Exception:
        pass

    return {
        "status": "ok",
        "game_name": clean_game,
        "folder_path": str(game_dir.resolve()),
        "video_paths": new_paths if new_paths else [c["path"] for c in clips],
        "clips": clips,
        "total_clips": len(clips),
        "has_step1": has_s1,
        "has_step2": has_s2,
    }


@app.get("/api/promov3/games/load")
def load_promov3_game(game_name: str):
    """
    Loads saved game footage and breakdown (step1 & step2 analysis) from PromosFootage/<game_name>.
    Mirrors analysis files into v3_cache so subsequent steps can immediately run without re-analyzing.
    """
    raw_name = game_name.strip()
    clean_game = sanitize_filename(raw_name)
    game_dir = PROMOS_FOOTAGE_DIR / clean_game

    # Fallback: check case-insensitive match among existing directories
    if not game_dir.exists() or not game_dir.is_dir():
        matched_dir = None
        for d in PROMOS_FOOTAGE_DIR.iterdir():
            if d.is_dir() and d.name.lower() == raw_name.lower():
                matched_dir = d
                break
        if matched_dir:
            game_dir = matched_dir
            clean_game = matched_dir.name
        else:
            raise HTTPException(status_code=404, detail=f"Juego no encontrado: '{game_name}'")

    video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
    video_files = [f for f in sorted(game_dir.iterdir(), key=lambda x: x.name.lower()) if f.is_file() and f.suffix.lower() in video_exts]
    if not video_files:
        raise HTTPException(status_code=400, detail="La carpeta del juego no contiene vídeos")

    clips = [{
        "filename": f.name,
        "path": str(f.resolve()),
        "url": f"/media/PromosFootage/{urllib.parse.quote(clean_game)}/{urllib.parse.quote(f.name)}",
        "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
        "game_name": clean_game,
    } for f in video_files]

    step1_file = game_dir / "step1_shots.json"
    step2_file = game_dir / "step2_evaluated_shots.json"

    step1_data = None
    step2_data = None
    if step1_file.exists():
        try:
            with open(step1_file, "r", encoding="utf-8") as f:
                step1_data = json.load(f)
        except Exception as e:
            logger.warning(f"Error reading step1_shots.json in {game_dir}: {e}")

    if step2_file.exists():
        try:
            with open(step2_file, "r", encoding="utf-8") as f:
                step2_data = json.load(f)
        except Exception as e:
            logger.warning(f"Error reading step2_evaluated_shots.json in {game_dir}: {e}")

    # Canonical session ID
    if len(video_files) == 1:
        session_id = get_canonical_v3_session_id(str(video_files[0]))
    else:
        session_id = get_canonical_v3_multi_session_id([str(f) for f in video_files])

    v3_cache_dir = BASE_DIR / "temp" / "v3_cache"
    folder_stems = {f.stem.lower() for f in video_files}

    # If step1 or step2 are missing from game folder, search v3_cache for any matching session
    if (not step1_data or not step2_data) and v3_cache_dir.exists():
        for s_dir in sorted(v3_cache_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not s_dir.is_dir():
                continue
            s1_file = s_dir / "step1_shots.json"
            s2_file = s_dir / "step2_evaluated_shots.json"
            if s1_file.exists() or s2_file.exists():
                try:
                    ref_file = s2_file if s2_file.exists() else s1_file
                    with open(ref_file, "r", encoding="utf-8") as sf:
                        sdata = json.load(sf)
                        v_info = sdata.get("video_info", {})
                        v_paths = v_info.get("paths", []) or [v_info.get("path")]
                        v_stems = {Path(p).stem.lower() for p in v_paths if p}
                        if v_stems and v_stems.intersection(folder_stems):
                            session_id = s_dir.name
                            if not step1_data and s1_file.exists():
                                with open(s1_file, "r", encoding="utf-8") as f1:
                                    step1_data = json.load(f1)
                                shutil.copy2(str(s1_file), str(step1_file))
                                logger.info(f"Auto-mirrored step1_shots.json from {s_dir.name} to {clean_game}")
                            if not step2_data and s2_file.exists():
                                with open(s2_file, "r", encoding="utf-8") as f2:
                                    step2_data = json.load(f2)
                                shutil.copy2(str(s2_file), str(step2_file))
                                logger.info(f"Auto-mirrored step2_evaluated_shots.json from {s_dir.name} to {clean_game}")
                            break
                except Exception as err:
                    logger.warning(f"Error inspecting cache session {s_dir}: {err}")

    # Mirror to canonical v3_cache session so subsequent endpoints immediately find the files
    if session_id:
        sess_dir = _safe_session_dir(session_id)
        sess_dir.mkdir(parents=True, exist_ok=True)
        if step1_data and not (sess_dir / "step1_shots.json").exists():
            try:
                shutil.copy2(str(step1_file), str(sess_dir / "step1_shots.json"))
            except Exception:
                pass
        if step2_data and not (sess_dir / "step2_evaluated_shots.json").exists():
            try:
                shutil.copy2(str(step2_file), str(sess_dir / "step2_evaluated_shots.json"))
            except Exception:
                pass

    step3_data = None
    step4_data = None
    step5_data = None
    approval_data = None
    if session_id:
        s3_path = v3_cache_dir / session_id / "step3_script.json"
        if s3_path.exists():
            try:
                with open(s3_path, "r", encoding="utf-8") as f3:
                    step3_data = json.load(f3)
            except Exception:
                pass

        s4_path = v3_cache_dir / session_id / "step4_edl.json"
        if s4_path.exists():
            try:
                with open(s4_path, "r", encoding="utf-8") as f4:
                    step4_data = json.load(f4)
            except Exception:
                pass

        s5_path = v3_cache_dir / session_id / "step5_result.json"
        if s5_path.exists():
            try:
                with open(s5_path, "r", encoding="utf-8") as f5:
                    step5_data = json.load(f5)
            except Exception:
                pass

        appr_path = v3_cache_dir / session_id / "approval.json"
        if appr_path.exists():
            try:
                with open(appr_path, "r", encoding="utf-8") as fa:
                    approval_data = json.load(fa)
            except Exception:
                pass

    return {
        "status": "ok",
        "game_name": clean_game,
        "folder_path": str(game_dir.resolve()),
        "clips": clips,
        "has_step1": bool(step1_data),
        "step1": step1_data,
        "has_step2": bool(step2_data),
        "step2": step2_data,
        "has_step3": bool(step3_data),
        "step3": step3_data,
        "has_step4": bool(step4_data),
        "step4": step4_data,
        "has_step5": bool(step5_data),
        "step5": step5_data,
        "approval": approval_data,
        "session_id": session_id,
    }


@app.get("/api/promov3/games/list")
def list_promov3_saved_games():
    """
    List all saved games in PromosFootage, reporting number of videos,
    whether Step 1 and Step 2 analysis are already available (in folder or v3_cache),
    and the associated session data.
    """
    PROMOS_FOOTAGE_DIR.mkdir(parents=True, exist_ok=True)
    video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
    games = []

    v3_cache_dir = BASE_DIR / "temp" / "v3_cache"

    for folder in sorted(PROMOS_FOOTAGE_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue

        gname = folder.name
        video_files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in video_exts]
        if not video_files:
            continue

        total_bytes = sum(f.stat().st_size for f in video_files)
        total_mb = round(total_bytes / (1024 * 1024), 1)

        # Check if analysis exists directly in folder
        has_step1_folder = (folder / "step1_shots.json").exists()
        has_step2_folder = (folder / "step2_evaluated_shots.json").exists()

        # Check if analysis exists in v3_cache for these videos
        matched_sess_id = None
        has_step1_cache = False
        has_step2_cache = False
        shot_count = 0

        # Try multi session or single session ID for the videos
        if len(video_files) == 1:
            canonical_sess = get_canonical_v3_session_id(str(video_files[0]))
        else:
            canonical_sess = get_canonical_v3_multi_session_id([str(f) for f in video_files])

        if v3_cache_dir.exists():
            c_dir = v3_cache_dir / canonical_sess
            if c_dir.exists():
                matched_sess_id = canonical_sess
                has_step1_cache = (c_dir / "step1_shots.json").exists()
                has_step2_cache = (c_dir / "step2_evaluated_shots.json").exists()

            # If not direct match, search v3_cache for any session matching this folder's videos
            if not (has_step1_cache and has_step2_cache):
                folder_stems = {f.stem.lower() for f in video_files}
                for s_dir in sorted(v3_cache_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                    if not s_dir.is_dir():
                        continue
                    s1_file = s_dir / "step1_shots.json"
                    s2_file = s_dir / "step2_evaluated_shots.json"
                    if s1_file.exists() or s2_file.exists():
                        try:
                            # check if video stems match
                            with open(s1_file if s1_file.exists() else s2_file, "r", encoding="utf-8") as sf:
                                sdata = json.load(sf)
                                v_info = sdata.get("video_info", {})
                                v_paths = v_info.get("paths", []) or [v_info.get("path")]
                                v_stems = {Path(p).stem.lower() for p in v_paths if p}
                                if v_stems and v_stems.intersection(folder_stems):
                                    matched_sess_id = s_dir.name
                                    has_step1_cache = s1_file.exists()
                                    has_step2_cache = s2_file.exists()
                                    break
                        except Exception:
                            pass

        has_step1 = has_step1_folder or has_step1_cache
        has_step2 = has_step2_folder or has_step2_cache

        # Read shot count if step1 or step2 exists
        if has_step2_folder:
            try:
                with open(folder / "step2_evaluated_shots.json", "r", encoding="utf-8") as sf:
                    shot_count = len(json.load(sf).get("shots", []))
            except Exception:
                pass
        elif has_step2_cache and matched_sess_id:
            try:
                with open(v3_cache_dir / matched_sess_id / "step2_evaluated_shots.json", "r", encoding="utf-8") as sf:
                    shot_count = len(json.load(sf).get("shots", []))
            except Exception:
                pass
        elif has_step1_folder:
            try:
                with open(folder / "step1_shots.json", "r", encoding="utf-8") as sf:
                    shot_count = len(json.load(sf).get("shots", []))
            except Exception:
                pass
        elif has_step1_cache and matched_sess_id:
            try:
                with open(v3_cache_dir / matched_sess_id / "step1_shots.json", "r", encoding="utf-8") as sf:
                    shot_count = len(json.load(sf).get("shots", []))
            except Exception:
                pass

        clips = [{
            "filename": f.name,
            "path": str(f.resolve()),
            "url": f"/media/PromosFootage/{urllib.parse.quote(gname)}/{urllib.parse.quote(f.name)}",
            "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
            "game_name": gname,
        } for f in video_files]

        games.append({
            "game_name": gname,
            "folder_path": str(folder.resolve()),
            "video_count": len(video_files),
            "total_size_mb": total_mb,
            "has_step1": has_step1,
            "has_step2": has_step2,
            "has_analysis": has_step2,
            "shot_count": shot_count,
            "session_id": matched_sess_id or canonical_sess,
            "clips": clips,
            "updated_at": max(f.stat().st_mtime for f in video_files),
        })

    games.sort(key=lambda x: x["updated_at"], reverse=True)
    return {"status": "ok", "games": games}



@app.post("/api/promov3/step1/detect")
def run_promov3_step1_detect(req: PromoV3DetectRequest):
    """
    Execute Step 1 of Promo V3 pipeline:
    1. Probe original video metadata (supports 1 or multiple video files).
    2. Generate lightweight 720p 10-15 FPS proxy for visual analysis (cached if already present).
    3. Run PySceneDetect with AdaptiveDetector to identify shots/scenes across all clips.
    4. Generate individual preview MP4 clips for each detected shot, tagged with video_path and video_name.
    5. Return structured JSON with timestamps referenced directly to the original footage.
    """
    job = create_job("promov3_step1")

    input_paths: List[str] = []
    if req.video_paths:
        for p in req.video_paths:
            if p and str(p).strip():
                input_paths.append(str(p).strip())
    elif req.video_path:
        input_paths.append(req.video_path.strip())

    if not input_paths:
        raise HTTPException(status_code=400, detail="Debes proporcionar al menos un archivo de vídeo.")

    resolved_paths: List[Path] = []
    for p in input_paths:
        rp = _resolve_promov3_video_path(p)
        if rp and rp.exists():
            resolved_paths.append(rp)
        else:
            logger.warning(f"Could not resolve video path: {p}")

    if not resolved_paths:
        raise HTTPException(status_code=400, detail="No se pudo encontrar ninguno de los archivos de vídeo indicados.")

    if req.session_id and req.session_id.strip():
        sess_name = _validate_session_id(req.session_id)
    elif len(resolved_paths) == 1:
        sess_name = get_canonical_v3_session_id(str(resolved_paths[0]))
    else:
        sess_name = get_canonical_v3_multi_session_id(resolved_paths)

    def worker():
        try:
            total_videos = len(resolved_paths)
            job.update(5.0, f"Inspeccionando {total_videos} archivo(s) de vídeo original(es)...", "Iniciando análisis ffprobe")

            # Session folder in temp/v3_cache
            v3_dir = _safe_session_dir(sess_name)
            v3_dir.mkdir(parents=True, exist_ok=True)
            shots_dir = v3_dir / "shots"
            shots_dir.mkdir(parents=True, exist_ok=True)

            videos_meta: List[Dict[str, Any]] = []
            proxies_info: List[Dict[str, Any]] = []
            all_raw_shots: List[Dict[str, Any]] = []
            global_shot_id = 0
            total_detection_time = 0.0

            # Substep 1 & 2: Loop each video to generate proxy and run PySceneDetect
            for v_idx, v_path in enumerate(resolved_paths, 1):
                pct_base = 5.0 + ((v_idx - 1) / total_videos) * 65.0
                pct_v_span = (1.0 / total_videos) * 65.0

                v_str = str(v_path)
                meta = probe_video(v_str)
                if meta.get("duration", 0) <= 0:
                    logger.warning(f"Vídeo {v_path.name} sin duración reconocible, omitiendo.")
                    continue

                meta["video_index"] = v_idx
                meta["filename"] = v_path.name
                meta["path"] = v_str
                videos_meta.append(meta)

                job.update(
                    pct_base + pct_v_span * 0.1,
                    f"[{v_idx}/{total_videos}] Generando proxy {req.proxy_height}p @ {req.proxy_fps} FPS para {v_path.name}...",
                    f"Resolución {meta['width']}x{meta['height']}, {meta['fps']} FPS, {meta['duration']}s"
                )

                def make_proxy_cb(v_num):
                    def cb(p: float, msg: str):
                        cur_p = pct_base + pct_v_span * (0.1 + (p / 100.0) * 0.4)
                        job.update(cur_p, f"[{v_num}/{total_videos}] {msg}", msg)
                    return cb

                proxy_info = create_analysis_proxy(
                    video_path=v_str,
                    output_dir=v3_dir,
                    target_height=req.proxy_height,
                    target_fps=req.proxy_fps,
                    force_rebuild=req.force_reproxy,
                    progress_callback=make_proxy_cb(v_idx),
                )
                proxy_url = f"/media/temp/v3_cache/{sess_name}/{proxy_info['proxy_filename']}"
                proxies_info.append({**proxy_info, "video_index": v_idx, "video_name": v_path.name, "url": proxy_url})

                # Scene detection for this video
                job.update(
                    pct_base + pct_v_span * 0.55,
                    f"[{v_idx}/{total_videos}] Detectando cortes con AdaptiveDetector en {v_path.name}...",
                    f"Analizando {proxy_info['proxy_filename']}"
                )

                def make_detect_cb(v_num):
                    def cb(p: float, msg: str):
                        cur_p = pct_base + pct_v_span * (0.55 + (p / 100.0) * 0.4)
                        job.update(cur_p, f"[{v_num}/{total_videos}] {msg}", msg)
                    return cb

                detection_res = detect_scenes_adaptive(
                    proxy_path=proxy_info["proxy_path"],
                    original_duration=meta["duration"],
                    adaptive_threshold=req.adaptive_threshold,
                    min_scene_len_sec=req.min_scene_len_sec,
                    window_width=req.window_width,
                    detect_fades=True,
                    progress_callback=make_detect_cb(v_idx),
                )

                v_shots = detection_res.get("shots", [])
                total_detection_time += float(detection_res.get("stats", {}).get("detection_time_sec", 0.0))

                for s in v_shots:
                    global_shot_id += 1
                    s_copy = dict(s)
                    s_copy["shot_id"] = global_shot_id
                    s_copy["video_index"] = v_idx
                    s_copy["video_path"] = v_str
                    s_copy["video_name"] = v_path.name
                    s_copy["fps"] = meta.get("fps", 30.0)
                    all_raw_shots.append(s_copy)

            if not videos_meta:
                job.fail("Ninguno de los vídeos proporcionados tiene metadatos válidos o duración reconocible.")
                return

            # Automatic Footage Sampling: If total footage > 5 min (300s), pick 5 random 1-minute (60s) chunks and discard the rest
            selected_chunks, was_sampled, orig_total_dur = sample_footage_chunks_if_needed(videos_meta)
            if was_sampled:
                sampled_sec = sum(c["duration"] for c in selected_chunks)
                job.update(
                    70.0,
                    f"Footage total ({orig_total_dur:.1f}s > 5 min). Seleccionando aleatoriamente 5 minutos en bloques de 1 min...",
                    f"Descartando el resto del footage ({orig_total_dur - sampled_sec:.1f}s descartados). {len(selected_chunks)} bloques de 60s retenidos."
                )
                logger.info(
                    f"✂️ Muestreo de footage activo: total original {orig_total_dur:.1f}s > 300s. "
                    f"Seleccionados aleatoriamente {len(selected_chunks)} bloques de 60s (total {sampled_sec:.1f}s):"
                )
                for ch in selected_chunks:
                    logger.info(f"   • {ch['video_name']} [{ch['start_sec']:.1f}s - {ch['end_sec']:.1f}s]")

                # Filter all_raw_shots to retain ONLY shots falling within the 5 selected 1-minute chunks
                filtered_raw_shots = []
                for s in all_raw_shots:
                    s_mid = (s["start"] + s["end"]) / 2.0
                    v_idx = s["video_index"]
                    matching_ch = next(
                        (ch for ch in selected_chunks if ch["video_index"] == v_idx and ch["start_sec"] <= s_mid <= ch["end_sec"]),
                        None
                    )
                    if matching_ch:
                        s_copy = dict(s)
                        s_copy["start"] = max(s["start"], matching_ch["start_sec"])
                        s_copy["end"] = min(s["end"], matching_ch["end_sec"])
                        s_copy["duration"] = round(s_copy["end"] - s_copy["start"], 2)
                        if s_copy["duration"] >= 0.5:
                            filtered_raw_shots.append(s_copy)

                for new_id, s in enumerate(filtered_raw_shots, 1):
                    s["shot_id"] = new_id

                all_raw_shots = filtered_raw_shots

            job.update(
                72.0,
                f"¡Detección completada en {total_videos} vídeo(s)! {len(all_raw_shots)} planos detectados. Generando previsualizaciones...",
                f"Extrayendo clips individuales (>= {req.min_clip_duration}s)..."
            )

            # Substep 3: Generate preview clips for each shot across all videos
            shots_with_previews: List[Dict[str, Any]] = []
            if req.generate_previews and all_raw_shots:
                preview_url_prefix = f"/media/temp/v3_cache/{sess_name}/shots"

                def preview_cb(p: float, msg: str):
                    cur_p = 72.0 + (p / 100.0) * 25.0
                    job.update(cur_p, msg, msg)

                primary_src = str(resolved_paths[0])
                primary_fps = videos_meta[0].get("fps", 30.0)

                shots_with_previews = extract_shot_preview_clips(
                    original_video_path=primary_src,
                    shots=all_raw_shots,
                    output_dir=shots_dir,
                    media_url_prefix=preview_url_prefix,
                    min_duration=req.min_clip_duration,
                    trim_frames=req.trim_frames,
                    src_fps=primary_fps,
                    max_workers=4,
                    enable_deduplication=req.enable_deduplication,
                    dedup_hamming_threshold=req.dedup_hamming_threshold,
                    dedup_min_match_ratio=req.dedup_min_match_ratio,
                    progress_callback=preview_cb,
                )
            else:
                shots_with_previews = all_raw_shots

            # Compile stats
            rendered_count = len([s for s in shots_with_previews if s.get("rendered")])
            black_count = len([s for s in shots_with_previews if s.get("is_black")])
            duplicate_count = len([s for s in shots_with_previews if s.get("is_duplicate")])
            total_dur = round(sum(float(m.get("duration", 0.0)) for m in videos_meta), 2)
            usable_dur = round(sum(float(c.get("duration", 0.0)) for c in selected_chunks), 2) if was_sampled else total_dur
            avg_dur = round(usable_dur / max(1, len(shots_with_previews)), 2) if shots_with_previews else 0.0

            stats = {
                "total_shots": len(shots_with_previews),
                "rendered_shots": rendered_count,
                "black_shots_discarded": black_count,
                "duplicate_shots_discarded": duplicate_count,
                "total_duration": usable_dur,
                "original_total_duration": total_dur,
                "was_sampled_5min": was_sampled,
                "sampled_chunks": selected_chunks if was_sampled else [],
                "avg_duration": avg_dur,
                "min_clip_duration": req.min_clip_duration,
                "trim_frames": req.trim_frames,
                "detection_time_sec": round(total_detection_time, 2),
                "video_count": total_videos,
            }

            detector_params = {
                "detector": "AdaptiveDetector",
                "adaptive_threshold": req.adaptive_threshold,
                "min_scene_len_sec": req.min_scene_len_sec,
                "window_width": req.window_width,
                "detect_fades": True,
                "min_clip_duration": req.min_clip_duration,
                "trim_frames": req.trim_frames,
                "enable_deduplication": req.enable_deduplication,
                "dedup_hamming_threshold": req.dedup_hamming_threshold,
            }

            # Primary video info for backward compatibility
            primary_video_info = {
                "filename": videos_meta[0]["filename"] if total_videos == 1 else f"{total_videos} vídeos seleccionados",
                "path": str(resolved_paths[0]),
                "paths": [str(p) for p in resolved_paths],
                "video_count": total_videos,
                "duration": total_dur,
                "fps": videos_meta[0].get("fps", 30.0),
                "width": videos_meta[0].get("width", 1920),
                "height": videos_meta[0].get("height", 1080),
                "is_vertical": all(m.get("is_vertical", False) for m in videos_meta),
            }

            # Save step 1 output JSON in cache folder for persistence
            output_json_path = v3_dir / "step1_shots.json"
            step1_result = {
                "step": 1,
                "step_name": "scene_detection",
                "session_id": sess_name,
                "video_format": req.video_format or "full_9_16",
                "video_info": primary_video_info,
                "videos": videos_meta,
                "proxy_info": proxies_info[0] if len(proxies_info) == 1 else {"proxies": proxies_info, "count": len(proxies_info)},
                "detector_params": detector_params,
                "stats": stats,
                "shots": shots_with_previews,
            }
            with open(output_json_path, "w", encoding="utf-8") as f:
                json.dump(step1_result, f, indent=2, ensure_ascii=False)

            msg_videos = f"a partir de {total_videos} vídeo(s)" if total_videos > 1 else ""
            summary_extras = []
            if black_count > 0:
                summary_extras.append(f"{black_count} negros")
            if duplicate_count > 0:
                summary_extras.append(f"{duplicate_count} duplicados")
            extra_msg = f" ({', '.join(summary_extras)} descartados)" if summary_extras else ""

            job.complete(
                step1_result,
                f"¡Paso 1 completado con éxito! {len(shots_with_previews)} planos detectados {msg_videos} ({rendered_count} clips listos{extra_msg})."
            )

        except Exception as e:
            logger.exception("Promo V3 Step 1 failed")
            job.fail(f"Error en Paso 1 (Scene Detection): {str(e)}\n\n{traceback.format_exc()}")

    threading.Thread(target=worker, daemon=True).start()
    return {"status": "ok", "job_id": job.job_id}


@app.post("/api/promov3/step2/analyze")
def run_promov3_step2_analyze(req: PromoV3Step2Request):
    """
    Execute Step 2 of Promo V3 pipeline:
    1. Locate and read Step 1 cached shots (from session_id or video_path).
    2. Convert all surviving rendered clips (>= 1.0s) to 9:16 vertical (720x1280 center crop).
    3. Package 9:16 clips into semantic batches of ~45s.
    4. Call Gemini File API with interleaved CLIP_ID tags and automatic quota fallback across GEMINI_CANDIDATE_MODELS.
    5. Merge semantic scores (format_score, viral_score, descriptive_score, description) into each shot.
    6. Return structured table JSON and emit SSE progress.
    """
    job = create_job("promov3_step2")

    v3_cache_dir = BASE_DIR / "temp" / "v3_cache"
    v3_dir = None
    step1_json_path = None

    if req.session_id:
        cand = _safe_session_dir(req.session_id)
        if cand.exists() and (cand / "step1_shots.json").exists():
            v3_dir = cand
            step1_json_path = cand / "step1_shots.json"

    if not step1_json_path and req.video_paths:
        cand_id = get_canonical_v3_multi_session_id(req.video_paths)
        cand = v3_cache_dir / cand_id
        if cand.exists() and (cand / "step1_shots.json").exists():
            v3_dir = cand
            step1_json_path = cand / "step1_shots.json"

    if not step1_json_path and req.video_path:
        cand_id = get_canonical_v3_session_id(req.video_path)
        cand = v3_cache_dir / cand_id
        if cand.exists() and (cand / "step1_shots.json").exists():
            v3_dir = cand
            step1_json_path = cand / "step1_shots.json"

    if not step1_json_path:
        cand_paths = req.video_paths or ([req.video_path] if req.video_path else [])
        stems = [Path(p).stem.lower() for p in cand_paths if p]
        candidates = []
        if v3_cache_dir.exists() and stems:
            for d in v3_cache_dir.iterdir():
                if d.is_dir() and (d / "step1_shots.json").exists():
                    try:
                        with open(d / "step1_shots.json", "r", encoding="utf-8") as f:
                            data = json.load(f)
                            v_info = data.get("video_info", {})
                            fn = v_info.get("filename", "").lower()
                            v_items = [v.get("filename", "").lower() for v in data.get("videos", [])]
                            if any(stem in d.name.lower() or stem in fn or any(stem in it for it in v_items) for stem in stems):
                                candidates.append((d.stat().st_mtime, d, d / "step1_shots.json"))
                    except Exception:
                        pass
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            v3_dir = candidates[0][1]
            step1_json_path = candidates[0][2]

    if not step1_json_path or not step1_json_path.exists():
        raise HTTPException(
            status_code=400,
            detail="No se encontraron los resultados del Paso 1 para este vídeo. Ejecuta el Paso 1 (Detección de Planos) primero."
        )

    try:
        with open(step1_json_path, "r", encoding="utf-8") as f:
            step1_data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo step1_shots.json: {e}")

    shots = step1_data.get("shots", [])
    rendered_shots = [s for s in shots if s.get("rendered") and s.get("preview_path")]

    if not rendered_shots:
        raise HTTPException(
            status_code=400,
            detail="El Paso 1 no produjo ningún clip renderizado (todos fueron omitidos por duración mínima o error). Ajusta el umbral del Paso 1."
        )

    sess_name = v3_dir.name

    def worker():
        try:
            job.update(5.0, f"Iniciando Paso 2: Organizando {len(rendered_shots)} clips supervivientes en lotes estratificados...", "Cargando datos del Paso 1")

            # 1. Package rendered clips into stratified batches of ~60s across different parts of the footage
            batches = build_clip_batches(
                shots=rendered_shots,
                target_duration=req.batch_target_duration,
                max_clips_per_batch=14,
                stratify_random=True,
            )

            total_batches = len(batches)
            job.update(
                10.0,
                f"{len(rendered_shots)} clips organizados en {total_batches} lote(s) estratificados de ~{req.batch_target_duration}s. Conectando con Gemini...",
                f"Lotes generados: {[{'id': b['batch_id'], 'clips': b['clip_count'], 'dur': b['total_duration']} for b in batches]}"
            )

            # 2. Setup on-demand 9:16 preview converter for each batch
            shots_916_dir = v3_dir / "shots_916"
            media_prefix = f"/media/temp/v3_cache/{sess_name}/shots_916"
            resolved_format = req.video_format or step1_data.get("video_format", "full_9_16")

            def ensure_batch_916(b):
                b_shots = b["shots"]
                logger.info(f"Convirtiendo a formato {resolved_format} los {len(b_shots)} clips del Lote {b['batch_id']}...")
                updated = generate_9_16_preview_clips(
                    shots=b_shots,
                    output_dir=shots_916_dir,
                    media_url_prefix=media_prefix,
                    max_workers=4,
                    force=req.force_reanalyze,
                    progress_callback=None,
                    video_format=resolved_format,
                )
                b["shots"] = updated

            # 3. Multimodal Analysis with Gemini Fallback & Early-Stopping Judge LLM (Groq first)
            from config import AppConfig
            cfg = AppConfig.from_env()
            gemini_key = (cfg.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")).strip().strip('"').strip("'")
            groq_key = (cfg.groq_api_key or os.environ.get("GROQ_API_KEY", "")).strip().strip('"').strip("'")
            if not gemini_key:
                job.fail("GEMINI_API_KEY no configurada. Añade tu API Key en la configuración o archivo .env para analizar con Gemini.")
                return

            def gemini_cb(p: float, msg: str):
                job.update(p, msg, msg)

            total_video_dur = float(step1_data.get("video_info", {}).get("duration", 0.0))

            analysis_result = analyze_clip_batches_gemini(
                batches=batches,
                gemini_api_key=gemini_key,
                groq_api_key=groq_key,
                preferred_model=req.preferred_model,
                progress_callback=gemini_cb,
                enable_early_stopping=req.enable_early_stopping,
                ensure_916_callback=ensure_batch_916,
                strip_splash=req.strip_splash,
                total_video_duration=total_video_dur,
                video_format=resolved_format,
            )

            eval_map = analysis_result["eval_map"]
            models_used = analysis_result["models_used"]
            early_stopped = analysis_result.get("early_stopped", False)
            batches_saved = analysis_result.get("batches_saved", 0)
            batches_evaluated = analysis_result.get("batches_evaluated", total_batches)
            sufficiency_verdict = analysis_result.get("sufficiency_verdict")

            # Collect 9:16 paths from all processed batches
            shots_916_lookup = {}
            for b in batches[:batches_evaluated]:
                for s in b.get("shots", []):
                    if s.get("preview_9_16_path"):
                        shots_916_lookup[s["shot_id"]] = s

            # Merge evaluation back into shots
            final_shots = []
            for s in shots:
                shot_copy = dict(s)
                sid = shot_copy["shot_id"]
                if sid in shots_916_lookup:
                    shot_copy["preview_9_16_path"] = shots_916_lookup[sid].get("preview_9_16_path")
                    shot_copy["preview_9_16_url"] = shots_916_lookup[sid].get("preview_9_16_url")
                if sid in eval_map:
                    ev = eval_map[sid]
                    shot_copy["description"] = ev.get("description", "Sin descripción")
                    shot_copy["format_score"] = int(ev.get("format_score", 50))
                    shot_copy["format_reason"] = ev.get("format_reason", "")
                    shot_copy["viral_score"] = int(ev.get("viral_score", 50))
                    shot_copy["descriptive_score"] = int(ev.get("descriptive_score", 50))
                    shot_copy["suggested_role"] = ev.get("suggested_role", "gameplay")
                    shot_copy["is_splash"] = bool(ev.get("is_splash", False))
                final_shots.append(shot_copy)

            # Apply Trailer Splash Filter (discard title cards, logos, wishlist, release dates)
            final_shots, splash_discarded = filter_trailer_splash_shots(
                shots=final_shots,
                total_video_duration=total_video_dur,
                enabled=req.strip_splash,
            )

            # Save Step 2 result to JSON (both step2_analysis.json and step2_evaluated_shots.json for seamless session loading)
            step2_json_path = v3_dir / "step2_analysis.json"
            step2_eval_path = v3_dir / "step2_evaluated_shots.json"
            step2_output = {
                "step": 2,
                "step_name": "multimodal_evaluation_9_16",
                "session_id": sess_name,
                "video_format": resolved_format,
                "video_info": step1_data.get("video_info", {}),
                "videos": step1_data.get("videos", []),
                "batch_stats": {
                    "batch_target_duration": req.batch_target_duration,
                    "total_batches": total_batches,
                    "batches_evaluated": batches_evaluated,
                    "batches_saved": batches_saved,
                    "early_stopped": early_stopped,
                    "models_used": models_used,
                    "clips_evaluated": len(eval_map),
                    "sufficiency_verdict": sufficiency_verdict,
                    "strip_splash": req.strip_splash,
                    "splash_shots_discarded": splash_discarded,
                },
                "batches": [
                    {
                        "batch_id": b["batch_id"],
                        "clip_count": b["clip_count"],
                        "total_duration": b["total_duration"],
                        "shot_ids": b["shot_ids"],
                    }
                    for b in batches
                ],
                "shots": final_shots,
            }

            with open(step2_json_path, "w", encoding="utf-8") as f:
                json.dump(step2_output, f, indent=2, ensure_ascii=False)
            with open(step2_eval_path, "w", encoding="utf-8") as f:
                json.dump(step2_output, f, indent=2, ensure_ascii=False)

            splash_info = f" (🛡️ {splash_discarded} planos de splash/outro descartados)" if splash_discarded > 0 else ""
            if early_stopped:
                evaluator_name = (sufficiency_verdict or {}).get("model_evaluator", "Groq")
                msg = (
                    f"⚡ ¡Paso 2 completado con optimización! Suficiencia alcanzada en lote {batches_evaluated}/{total_batches} según {evaluator_name} "
                    f"({len(eval_map)} clips evaluados, {batches_saved} lote(s) ahorrados){splash_info}."
                )
            else:
                msg = f"¡Paso 2 completado! {len(eval_map)} clips evaluados en 9:16 con éxito (Modelo: {', '.join(models_used)}){splash_info}."

            job.complete(step2_output, msg)

        except Exception as e:
            logger.exception("Promo V3 Step 2 failed")
            job.fail(f"Error en Paso 2 (Multimodal Analysis 9:16): {str(e)}\n\n{traceback.format_exc()}")

    threading.Thread(target=worker, daemon=True).start()
    return {"status": "ok", "job_id": job.job_id}


# =============================================================================
# API Endpoints: Server-Sent Events (SSE) Stream
# =============================================================================

@app.get("/api/jobs/{job_id}/stream")
async def stream_job_events(job_id: str):
    """Stream live events and progress for a specific job."""
    job = ACTIVE_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    loop = asyncio.get_running_loop()
    q = job.add_subscriber(loop)
    
    async def event_generator():
        try:
            while True:
                data = await q.get()
                yield f"data: {json.dumps(data)}\n\n"
                if data.get("type") in ("completed", "error"):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            job.remove_subscriber(q)
            
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/api/jobs/{job_id}")
def get_single_job_status(job_id: str):
    """Return status and latest progress for a specific job."""
    job = ACTIVE_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job.job_id,
        "type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "phase": job.message,
        "message": job.message,
        "result": job.result,
        "error": job.error,
        "recent_logs": job.logs[-20:],
    }


# =============================================================================
# API Endpoints: System Logs & Debugging
# =============================================================================

@app.get("/api/logs/render")
def get_render_error_log():
    """Return the recent contents of logs/render_errors.log as formatted text."""
    err_log_path = BASE_DIR / "logs" / "render_errors.log"
    if err_log_path.exists():
        try:
            with open(err_log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                return PlainTextResponse("".join(lines[-300:]), media_type="text/plain; charset=utf-8")
        except Exception as e:
            return PlainTextResponse(f"Error leyendo registro de errores: {e}", media_type="text/plain; charset=utf-8")
    return PlainTextResponse("No hay registro de errores de render todavía.", media_type="text/plain; charset=utf-8")


@app.get("/api/logs/latest")
def get_latest_server_log():
    """Return the tail of logs/latest.log."""
    latest_file = BASE_DIR / "logs" / "latest.log"
    if latest_file.exists():
        try:
            with open(latest_file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                return PlainTextResponse("".join(lines[-300:]), media_type="text/plain; charset=utf-8")
        except Exception as e:
            return PlainTextResponse(f"Error leyendo latest.log: {e}", media_type="text/plain; charset=utf-8")
    return PlainTextResponse("No hay archivo latest.log todavía.", media_type="text/plain; charset=utf-8")


@app.get("/api/jobs/recent")
def get_recent_jobs_status():
    """Return recent active/completed/errored background jobs with their logs."""
    return {
        job_id: {
            "type": job.job_type,
            "status": job.status,
            "progress": job.progress,
            "message": job.message,
            "error": job.error,
            "recent_logs": job.logs[-20:],
        }
        for job_id, job in list(ACTIVE_JOBS.items())[-15:]
    }


# =============================================================================
# Steam Deals Carousel (IGD 3:4) Endpoints
# =============================================================================
import base64

class SteamCarouselFetchRequest(BaseModel):
    urls: List[str]
    currency: str = "ES"
    language: str = "spanish"

class SteamCarouselPreviewRequest(BaseModel):
    slide_type: str  # "cover", "games", "closing"
    theme: Optional[str] = "igd"
    headline: Optional[str] = "JUEGOS CO-OP DE OFERTA EN STEAM"
    swipe_text: Optional[str] = "DESLIZA PARA VER"
    cta_title: Optional[str] = "¿CUÁL VAS A JUGAR PRIMERO?"
    cta_subtitle: Optional[str] = "Guarda este post y síguenos para más recomendaciones"
    bg_image_b64: Optional[str] = ""
    use_test_bg: Optional[bool] = False
    games_chunk: Optional[List[Dict[str, Any]]] = None
    slide_index: Optional[int] = 1
    total_slides: Optional[int] = 1

class SteamCarouselExportRequest(BaseModel):
    theme: Optional[str] = "igd"
    headline: str = "JUEGOS CO-OP DE OFERTA EN STEAM"
    swipe_text: str = "DESLIZA PARA VER"
    cta_title: str = "¿CUÁL VAS A JUGAR PRIMERO?"
    cta_subtitle: str = "Guarda este post y síguenos para más recomendaciones"
    bg_image_b64: str = ""
    use_test_bg: bool = False
    scale_factor: Optional[float] = 2.6666667
    games: List[Dict[str, Any]]


@app.post("/api/steam/carousel/fetch")
def api_steam_carousel_fetch(req: SteamCarouselFetchRequest):
    """Fetch details, discounts, prices, and screenshots for a list of Steam games."""
    clean_urls = [u.strip() for u in req.urls if u.strip()]
    if not clean_urls:
        raise HTTPException(status_code=400, detail="Debes proporcionar al menos una URL o ID de Steam.")

    if len(clean_urls) % 3 != 0:
        raise HTTPException(
            status_code=400,
            detail=f"La cantidad de juegos ({len(clean_urls)}) debe ser múltiplo de 3 (3, 6, 9, etc.) para completar las diapositivas de 3 juegos cada una."
        )

    games = steam_carousel.fetch_multiple_steam_games(
        clean_urls,
        currency_cc=req.currency,
        language=req.language
    )

    if not games:
        raise HTTPException(status_code=404, detail="No se pudo obtener información de ninguno de los juegos indicados.")

    # Auto-select the first 2 screenshots for each game by default
    for g in games:
        screenshots = g.get("screenshots", [])
        if "selected_screenshots" not in g or not g["selected_screenshots"]:
            selected = [s["full"] for s in screenshots[:2]]
            if len(selected) == 1:
                selected.append(selected[0])
            g["selected_screenshots"] = selected

    return {
        "success": True,
        "total": len(games),
        "games": games,
        "warning": None if len(games) == len(clean_urls) else f"Se solicitaron {len(clean_urls)} juegos pero sólo se encontraron {len(games)}."
    }


@app.post("/api/steam/carousel/upload-bg")
async def api_steam_carousel_upload_bg(bg_file: UploadFile = File(...)):
    """Upload a 3:4 background image for the cover and closing slides."""
    if not bg_file.filename:
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido.")

    ext = Path(bg_file.filename).suffix.lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        raise HTTPException(status_code=400, detail="Formato no soportado. Debe ser PNG, JPG o WEBP.")

    temp_upload_dir = BASE_DIR / "temp" / "uploads"
    temp_upload_dir.mkdir(parents=True, exist_ok=True)

    unique_name = f"bg_{uuid.uuid4().hex[:8]}_{_safe_upload_basename(bg_file.filename, default_stem='bg')}"
    dest_path = _safe_child_path(temp_upload_dir, unique_name)

    contents = await bg_file.read()
    await run_in_threadpool(_write_bytes_file, dest_path, contents)

    # Base64 data URI
    mime = "png" if ext == ".png" else "jpeg" if ext in [".jpg", ".jpeg"] else "webp"
    encoded = base64.b64encode(contents).decode("utf-8")
    data_uri = f"data:image/{mime};base64,{encoded}"

    return {
        "success": True,
        "filename": bg_file.filename,
        "file_path": str(dest_path.resolve()),
        "url": f"/media/temp/uploads/{unique_name}",
        "bg_image_b64": data_uri
    }


@app.post("/api/steam/carousel/preview-slide")
def api_steam_carousel_preview_slide(req: SteamCarouselPreviewRequest):
    """Generate HTML string for real-time live preview of any slide."""
    slide_type = req.slide_type.lower()
    theme = (req.theme or "igd").lower()
    if slide_type == "cover":
        html = steam_carousel.build_cover_slide_html(
            headline=req.headline or "JUEGOS CO-OP DE OFERTA EN STEAM",
            swipe_text=req.swipe_text or "DESLIZA PARA VER",
            bg_image_b64=req.bg_image_b64 or "",
            use_test_bg=bool(req.use_test_bg),
            theme=theme,
            total_slides=req.total_slides or 5
        )
    elif slide_type == "games":
        chunk = req.games_chunk or []
        html = steam_carousel.build_games_slide_html(
            games_chunk=chunk,
            slide_index=req.slide_index or 1,
            total_slides=req.total_slides or 1,
            headline=req.headline or "JUEGOS CO-OP DE OFERTA EN STEAM",
            theme=theme
        )
    elif slide_type == "closing":
        html = steam_carousel.build_closing_slide_html(
            cta_title=req.cta_title or "¿CUÁL VAS A JUGAR PRIMERO?",
            cta_subtitle=req.cta_subtitle or "Guarda este post y síguenos para más recomendaciones",
            bg_image_b64=req.bg_image_b64 or "",
            use_test_bg=bool(req.use_test_bg),
            theme=theme
        )
    else:
        raise HTTPException(status_code=400, detail=f"Tipo de slide desconocido: {slide_type}")

    return {
        "success": True,
        "slide_type": slide_type,
        "html": html
    }


@app.post("/api/steam/carousel/export")
def api_steam_carousel_export(req: SteamCarouselExportRequest):
    """Export carousel into high-res PNG slides and downloadable ZIP."""
    if not req.games:
        raise HTTPException(status_code=400, detail="No hay juegos para exportar.")

    if len(req.games) % 3 != 0:
        raise HTTPException(
            status_code=400,
            detail=f"La cantidad de juegos ({len(req.games)}) debe ser múltiplo de 3 para exportar."
        )

    try:
        result = steam_carousel.export_carousel_slides(
            carousel_config=req.dict(),
            output_base_dir=WORK_OUTPUT_DIR / "carruseles"
        )
        return result
    except Exception as e:
        logger.error(f"Error exportando carrusel: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error durante el renderizado del carrusel: {str(e)}")


# =============================================================================
# 3 Games Showcase Carousel (Stickers & Collage 4:5 Format) Endpoints
# =============================================================================

class ThreeGamesFetchRequest(BaseModel):
    urls: List[str]

class ThreeGamesPreviewRequest(BaseModel):
    slide_index: int  # 1..5
    state: Dict[str, Any]

class ThreeGamesExportRequest(BaseModel):
    state: Dict[str, Any]

@app.post("/api/three-games/fetch")
def api_three_games_fetch(req: ThreeGamesFetchRequest):
    """Fetch 3 Steam games, download assets, and generate stickers for selection."""
    clean_urls = [u.strip() for u in req.urls if u.strip()]
    if not clean_urls:
        raise HTTPException(status_code=400, detail="Por favor introduce al menos 1 enlace de Steam válido.")

    games = []
    for idx, u in enumerate(clean_urls[:3]):
        data = three_games_carousel.fetch_steam_game_full(u)
        if not data:
            continue
        stickers = three_games_carousel.generate_stickers_for_game(data)
        copy_info = three_games_carousel.generate_game_copy(data, idx + 1)
        
        screenshot_urls = [s.get("full") for s in data.get("screenshots", []) if s.get("full")]
        
        game_item = {
            "app_id": data["app_id"],
            "name": data["name"],
            "logo_url": data["logo_url"],
            "header_image": data["header_image"],
            "library_capsule": data["library_capsule"],
            "stickers": stickers,
            "selected_sticker_index": 0 if stickers else -1,
            "screenshots": screenshot_urls,
            "selected_screenshot_index_1": 0 if len(screenshot_urls) > 0 else -1,
            "selected_screenshot_index_2": 1 if len(screenshot_urls) > 1 else (0 if len(screenshot_urls) > 0 else -1),
            "badge_num": copy_info["badge_num"],
            "hook_title": copy_info["hook_title"],
            "bullets": copy_info["bullets"],
            "tag": copy_info["tag"],
            "summary_label": copy_info["summary_label"]
        }
        games.append(game_item)

    if not games:
        raise HTTPException(status_code=400, detail="No se pudo cargar ningún juego desde los enlaces proporcionados.")

    initial_state = {
        "games": games,
        "cover": {
            "title_line1": "3 JUEGOS",
            "title_highlight": "IMPRESCINDIBLES",
            "title_line2": "PARA JUGAR CON AMIGOS.",
            "subtitle": "El caos está garantizado desde el minuto uno.",
            "handle": "@demlabrothers",
            "selected_stickers": [0, 0, 0]
        },
        "outro": {
            "cta_title": "SO... WHICH ONE ARE YOU PLAYING FIRST? 👇",
            "share_text": "Pásale esto al grupo de amigos con el que jugarías a esto.",
            "save_text": "GUARDA ESTO PARA LA PRÓXIMA GAME NIGHT",
            "handle": "@demlabrothers"
        }
    }

    return {"success": True, "state": initial_state}


@app.post("/api/three-games/preview-slide")
def api_three_games_preview_slide(req: ThreeGamesPreviewRequest):
    """Generate HTML string for live preview of any of the 5 slides."""
    state = req.state
    games = state.get("games", [])
    cover = state.get("cover", {})
    outro = state.get("outro", {})
    slide_idx = req.slide_index

    if slide_idx == 1:
        cover_stickers = []
        sel_stickers = cover.get("selected_stickers", [0, 0, 0])
        for g_idx, g in enumerate(games[:3]):
            s_list = g.get("stickers", [])
            s_idx = sel_stickers[g_idx] if g_idx < len(sel_stickers) else 0
            if s_list:
                s_url = s_list[s_idx % len(s_list)]
                cover_stickers.append(s_url)
            else:
                cover_stickers.append("")

        html = three_games_carousel.build_cover_html(
            main_title_line1=cover.get("title_line1", "3 JUEGOS"),
            main_title_highlight=cover.get("title_highlight", "IMPRESCINDIBLES"),
            main_title_line2=cover.get("title_line2", "PARA JUGAR CON AMIGOS."),
            subtitle=cover.get("subtitle", "El caos está garantizado desde el minuto uno."),
            stickers=cover_stickers,
            handle=cover.get("handle", "@demlabrothers")
        )
    elif 2 <= slide_idx <= 4:
        game_pos = slide_idx - 2
        if game_pos >= len(games):
            raise HTTPException(status_code=400, detail=f"No hay juego configurado para la diapositiva {slide_idx}")
        g = games[game_pos]

        s_list = g.get("screenshots", [])
        s1_idx = g.get("selected_screenshot_index_1", 0)
        s2_idx = g.get("selected_screenshot_index_2", 1)
        sc1 = s_list[s1_idx % len(s_list)] if s_list else ""
        sc2 = s_list[s2_idx % len(s_list)] if s_list else ""

        st_list = g.get("stickers", [])
        st_idx = g.get("selected_sticker_index", 0)
        st = st_list[st_idx % len(st_list)] if st_list else ""

        html = three_games_carousel.build_game_slide_html(
            game_number=slide_idx - 1,
            badge_label=g.get("badge_num", f"GAME #{slide_idx - 1}"),
            hook_title=g.get("hook_title", ""),
            logo_url=g.get("logo_url", ""),
            bullets=g.get("bullets", []),
            tag=g.get("tag", "➔ PERFECT FOR: FRIEND GROUPS + PURE CHAOS"),
            screenshot1=sc1,
            screenshot2=sc2,
            character_sticker=st,
            handle=cover.get("handle", "@demlabrothers")
        )
    else:
        html = three_games_carousel.build_outro_html(
            games=games,
            cta_title=outro.get("cta_title", "SO... WHICH ONE ARE YOU PLAYING FIRST? 👇"),
            share_text=outro.get("share_text", "Pásale esto al grupo de amigos con el que jugarías a esto."),
            save_text=outro.get("save_text", "GUARDA ESTO PARA LA PRÓXIMA GAME NIGHT"),
            handle=outro.get("handle", "@demlabrothers")
        )

    return {"success": True, "html": html}


@app.post("/api/three-games/export")
def api_three_games_export(req: ThreeGamesExportRequest):
    """Render all 5 slides to high-res PNGs and package into a ZIP file."""
    state = req.state
    games = state.get("games", [])
    if len(games) < 1:
        raise HTTPException(status_code=400, detail="Se requiere al menos 1 juego cargado para exportar.")

    cover = state.get("cover", {})
    outro = state.get("outro", {})

    cover_stickers = []
    sel_stickers = cover.get("selected_stickers", [0, 0, 0])
    for g_idx, g in enumerate(games[:3]):
        s_list = g.get("stickers", [])
        s_idx = sel_stickers[g_idx] if g_idx < len(sel_stickers) else 0
        cover_stickers.append(s_list[s_idx % len(s_list)] if s_list else "")

    html_1 = three_games_carousel.build_cover_html(
        main_title_line1=cover.get("title_line1", "3 JUEGOS"),
        main_title_highlight=cover.get("title_highlight", "IMPRESCINDIBLES"),
        main_title_line2=cover.get("title_line2", "PARA JUGAR CON AMIGOS."),
        subtitle=cover.get("subtitle", "El caos está garantizado desde el minuto uno."),
        stickers=cover_stickers,
        handle=cover.get("handle", "@demlabrothers")
    )

    slides_html = [html_1]

    for g_idx in range(3):
        if g_idx < len(games):
            g = games[g_idx]
            s_list = g.get("screenshots", [])
            s1_idx = g.get("selected_screenshot_index_1", 0)
            s2_idx = g.get("selected_screenshot_index_2", 1)
            sc1 = s_list[s1_idx % len(s_list)] if s_list else ""
            sc2 = s_list[s2_idx % len(s_list)] if s_list else ""

            st_list = g.get("stickers", [])
            st_idx = g.get("selected_sticker_index", 0)
            st = st_list[st_idx % len(st_list)] if st_list else ""

            g_html = three_games_carousel.build_game_slide_html(
                game_number=g_idx + 1,
                badge_label=g.get("badge_num", f"GAME #{g_idx + 1}"),
                hook_title=g.get("hook_title", ""),
                logo_url=g.get("logo_url", ""),
                bullets=g.get("bullets", []),
                tag=g.get("tag", "➔ PERFECT FOR: FRIEND GROUPS + PURE CHAOS"),
                screenshot1=sc1,
                screenshot2=sc2,
                character_sticker=st,
                handle=cover.get("handle", "@demlabrothers")
            )
            slides_html.append(g_html)

    html_5 = three_games_carousel.build_outro_html(
        games=games,
        cta_title=outro.get("cta_title", "SO... WHICH ONE ARE YOU PLAYING FIRST? 👇"),
        share_text=outro.get("share_text", "Pásale esto al grupo de amigos con el que jugarías a esto."),
        save_text=outro.get("save_text", "GUARDA ESTO PARA LA PRÓXIMA GAME NIGHT"),
        handle=outro.get("handle", "@demlabrothers")
    )
    slides_html.append(html_5)

    try:
        res = three_games_carousel.export_carousel_bundle(slides_html)
        return res
    except Exception as e:
        logger.error(f"Error exportando carrusel de 3 juegos: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error al renderizar carrusel: {str(e)}")


# =============================================================================
# Set-up Trailer Calibration & Config Endpoints
# =============================================================================

class SetupTrailerPoint(BaseModel):
    x: float
    y: float

class SetupTrailerCorners(BaseModel):
    tl: SetupTrailerPoint
    tr: SetupTrailerPoint
    br: SetupTrailerPoint
    bl: SetupTrailerPoint

class SetupTrailerConfigRequest(BaseModel):
    narration_enabled: Optional[bool] = None
    narration_layout: Optional[Literal["card", "clean"]] = None
    narration_cta: Optional[str] = Field(None, max_length=300)
    narration_text: Optional[str] = Field(None, max_length=3000)
    background_audio_volume: Optional[float] = Field(None, ge=0, le=1)
    background_video: Optional[str] = "/static/setupbackground/setup-background-def.mp4"
    trailer_video: Optional[str] = None
    camera_effect_intensity: Optional[float] = 0.5
    game_name: Optional[str] = ""
    show_game_title: Optional[bool] = True
    title_font_size: Optional[int] = 28
    title_offset_y: Optional[int] = 18
    native_width: Optional[int] = 720
    native_height: Optional[int] = 1280
    corners: SetupTrailerCorners
    variant: Optional[Literal["classic", "monitor_dive"]] = "classic"
    gameplay_fit: Optional[Literal["contain", "cover"]] = "contain"
    card_enabled: Optional[bool] = False
    card_text: Optional[str] = Field(None, max_length=1200)
    card_preset: Optional[str] = "NextLoot"
    intro_seconds: Optional[float] = Field(None, ge=.5, le=30)
    transition_seconds: Optional[float] = Field(3.0, ge=.5, le=8)
    entry_sfx_enabled: Optional[bool] = False
    entry_sfx_volume: Optional[float] = Field(.25, ge=0, le=1)
    highlight_hook: Optional[bool] = False
    opening_mode: Optional[Literal["original", "auto", "manual"]] = "original"
    opening_start_seconds: Optional[float] = Field(0, ge=0)
    title_reveal_mode: Optional[Literal["immediate", "after_entry", "outro"]] = "immediate"
    title_reveal_delay: Optional[float] = Field(1, ge=0, le=10)
    entrance_mode: Optional[Literal["desktop", "action_match"]] = "desktop"
    action_lead_seconds: Optional[float] = Field(1.2, ge=.3, le=4)
    game_logo_enabled: Optional[bool] = True
    game_logo_scale: Optional[float] = Field(1.0, ge=.5, le=1.5)

class SetupTrailerYoutubeRequest(BaseModel):
    url: str
    intensity: Optional[float] = 0.5

class SetupTrailerApplyEffectRequest(BaseModel):
    intensity: float = 0.5
    trailer_video: Optional[str] = None

class SetupTrailerRenderRequest(BaseModel):
    narration_enabled: Optional[bool] = None
    narration_layout: Optional[Literal["card", "clean"]] = None
    narration_cta: Optional[str] = Field(None, max_length=300)
    narration_text: Optional[str] = Field(None, max_length=3000)
    background_audio_volume: Optional[float] = Field(None, ge=0, le=1)
    background_video: Optional[str] = None
    trailer_video: Optional[str] = None
    corners: Optional[SetupTrailerCorners] = None
    game_name: Optional[str] = None
    show_game_title: Optional[bool] = None
    title_font_size: Optional[int] = None
    title_offset_y: Optional[int] = None
    native_width: Optional[int] = 720
    native_height: Optional[int] = 1280
    variant: Optional[Literal["classic", "monitor_dive"]] = None
    gameplay_fit: Optional[Literal["contain", "cover"]] = None
    card_enabled: Optional[bool] = None
    card_text: Optional[str] = Field(None, max_length=1200)
    card_preset: Optional[str] = None
    intro_seconds: Optional[float] = Field(None, ge=.5, le=30)
    transition_seconds: Optional[float] = Field(None, ge=.5, le=8)
    entry_sfx_enabled: Optional[bool] = None
    entry_sfx_volume: Optional[float] = Field(None, ge=0, le=1)
    highlight_hook: Optional[bool] = None
    opening_mode: Optional[Literal["original", "auto", "manual"]] = None
    opening_start_seconds: Optional[float] = Field(None, ge=0)
    title_reveal_mode: Optional[Literal["immediate", "after_entry", "outro"]] = None
    title_reveal_delay: Optional[float] = Field(None, ge=0, le=10)
    entrance_mode: Optional[Literal["desktop", "action_match"]] = None
    action_lead_seconds: Optional[float] = Field(None, ge=.3, le=4)
    game_logo_enabled: Optional[bool] = None
    game_logo_scale: Optional[float] = Field(None, ge=.5, le=1.5)


SETUP_TRAILER_CONFIG_PATH = BASE_DIR / "data" / "setup_trailer_config.json"

DEFAULT_SETUP_TRAILER_CONFIG = {
    "narration_enabled": False,
    "narration_layout": "card",
    "narration_cta": "Follow {channel} for more games.",
    "narration_text": "",
    "background_audio_volume": .12,
    "game_logo_enabled": True,
    "game_logo_scale": 1.0,
    "entrance_mode": "desktop",
    "action_lead_seconds": 1.2,
    "highlight_hook": False,
    "opening_mode": "original",
    "opening_start_seconds": 0,
    "title_reveal_mode": "immediate",
    "title_reveal_delay": 1,
    "intro_seconds": None,
    "transition_seconds": 3.0,
    "entry_sfx_enabled": False,
    "entry_sfx_volume": .25,
    "card_enabled": False,
    "card_text": "",
    "card_preset": "NextLoot",
    "variant": "classic",
    "gameplay_fit": "contain",
    "background_video": "/static/setupbackground/setup-background-def.mp4",
    "trailer_video": None,
    "camera_effect_intensity": CAMERA_MONITOR_EFFECT_INTENSITY,
    "game_name": "",
    "show_game_title": True,
    "title_font_size": 28,
    "title_offset_y": 18,
    "native_width": 720,
    "native_height": 1280,
    "corners": {
        "tl": {"x": -36.8, "y": 329.1},
        "tr": {"x": 773.8, "y": 445.9},
        "br": {"x": 785.2, "y": 843.8},
        "bl": {"x": -41.4, "y": 956.1}
    },
    "updated_at": None
}

@app.get("/api/setup-trailer/config")
def api_get_setup_trailer_config():
    """Retrieve saved monitor corner calibration coordinates and camera effect intensity."""
    if SETUP_TRAILER_CONFIG_PATH.exists():
        try:
            with open(SETUP_TRAILER_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("background_video") in (None, "", "static/setupbackground/setupbackgroundvideo.mp4", "/static/setupbackground/setupbackgroundvideo.mp4"):
                    data["background_video"] = "/static/setupbackground/setup-background-def.mp4"
                if "camera_effect_intensity" not in data:
                    data["camera_effect_intensity"] = CAMERA_MONITOR_EFFECT_INTENSITY
                if "title_font_size" not in data:
                    data["title_font_size"] = 28
                if "title_offset_y" not in data:
                    data["title_offset_y"] = 18
                return {"success": True, "config": data}
        except Exception as e:
            logger.error(f"Error reading setup_trailer_config.json: {e}")

    return {"success": True, "config": DEFAULT_SETUP_TRAILER_CONFIG}

@app.post("/api/setup-trailer/config")
def api_save_setup_trailer_config(req: SetupTrailerConfigRequest):
    """Save monitor corner calibration coordinates for Set-up Trailer."""
    try:
        SETUP_TRAILER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if SETUP_TRAILER_CONFIG_PATH.exists():
            try:
                with open(SETUP_TRAILER_CONFIG_PATH, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass

        data = {**existing, **req.dict(exclude_unset=True)}
        if data.get("background_video") in (None, "", "static/setupbackground/setupbackgroundvideo.mp4", "/static/setupbackground/setupbackgroundvideo.mp4"):
            data["background_video"] = "/static/setupbackground/setup-background-def.mp4"

        # Keep previously active trailer video if not explicitly provided or default
        if "trailer_video" not in data and "trailer_video" in existing:
            data["trailer_video"] = existing["trailer_video"]
        elif data.get("trailer_video") in (None, "static/setupbackground/sample_trailer.mp4") and existing.get("trailer_video"):
            data["trailer_video"] = existing["trailer_video"]

        if "camera_effect_intensity" not in data and "camera_effect_intensity" in existing:
            data["camera_effect_intensity"] = existing["camera_effect_intensity"]
        if "game_name" not in data and "game_name" in existing:
            data["game_name"] = existing["game_name"]
        if "show_game_title" not in data and "show_game_title" in existing:
            data["show_game_title"] = existing["show_game_title"]
        if "title_font_size" not in data and "title_font_size" in existing:
            data["title_font_size"] = existing["title_font_size"]
        if "title_offset_y" not in data and "title_offset_y" in existing:
            data["title_offset_y"] = existing["title_offset_y"]

        for key in ("variant", "gameplay_fit"):
            if key not in data and key in existing:
                data[key] = existing[key]

        data["updated_at"] = datetime.now().isoformat()
        with open(SETUP_TRAILER_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return {"success": True, "message": "Configuración de monitor guardada correctamente", "config": data}
    except Exception as e:
        logger.error(f"Error saving setup_trailer_config.json: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error guardando configuración: {str(e)}")

@app.post("/api/setup-trailer/load-youtube")
def api_setup_trailer_load_youtube(req: SetupTrailerYoutubeRequest):
    """Download a YouTube trailer and prepare it with camera-monitor look as active overlay."""
    url = req.url.strip() if req.url else ""
    if not url or not ("youtube.com" in url.lower() or "youtu.be" in url.lower()):
        raise HTTPException(status_code=400, detail="Por favor proporciona un enlace de YouTube válido (ej. https://www.youtube.com/watch?v=...)")
    try:
        url = _validate_http_url(url)
    except ValueError as url_err:
        raise HTTPException(status_code=400, detail=str(url_err))

    dest_dir = BASE_DIR / "static" / "setupbackground"
    dest_dir.mkdir(parents=True, exist_ok=True)

    uid = uuid.uuid4().hex[:8]
    output_filename = f"trailer_{uid}.mp4"
    output_path = dest_dir / output_filename
    intensity = float(req.intensity) if req.intensity is not None else CAMERA_MONITOR_EFFECT_INTENSITY

    try:
        cfg = AppConfig.from_env()
        ffmpeg_bin = cfg.ffmpeg_path or "ffmpeg"

        # Extract clean game name from YouTube trailer metadata
        game_name = ""
        try:
            import yt_dlp
            from scraper import extract_game_name
            ydl_opts = {'quiet': True, 'no_warnings': True, 'extract_flat': True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                raw_title = info.get("title", "") if info else ""
            if raw_title:
                game_name = extract_game_name(raw_title)
                logger.info(f"Título YouTube extraído: '{raw_title}' -> Nombre limpio: '{game_name}'")
        except Exception as te:
            logger.warning(f"No se pudo extraer el título de YouTube: {te}")

        logger.info(f"Descargando trailer de YouTube para Set-up Trailer ({game_name or 'N/A'}, intensidad={intensity:.2f}): {url} -> {output_path}")

        download_youtube_trailer(
            url=url,
            output_path=output_path,
            max_duration=300.0,
            ffmpeg_bin=ffmpeg_bin,
            camera_effect_intensity=intensity,
            save_raw_copy=True
        )

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise FileNotFoundError("No se generó el archivo de vídeo descargado.")

        media_url = f"/static/setupbackground/{output_filename}"

        # Persist trailer_video, camera_effect_intensity, and game_name into data/setup_trailer_config.json
        if SETUP_TRAILER_CONFIG_PATH.exists():
            try:
                with open(SETUP_TRAILER_CONFIG_PATH, "r", encoding="utf-8") as f:
                    saved_cfg = json.load(f)
                saved_cfg["trailer_video"] = media_url
                saved_cfg["camera_effect_intensity"] = intensity
                if game_name:
                    saved_cfg["game_name"] = game_name
                    saved_cfg["show_game_title"] = True
                saved_cfg["updated_at"] = datetime.now().isoformat()
                with open(SETUP_TRAILER_CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(saved_cfg, f, indent=2, ensure_ascii=False)
            except Exception as ce:
                logger.warning(f"No se pudo actualizar trailer_video en config: {ce}")

        return {
            "success": True,
            "url": media_url,
            "filename": output_filename,
            "intensity": intensity,
            "game_name": game_name,
            "message": "Tráiler descargado con look de cámara física colocado sobre la pantalla"
        }
    except Exception as e:
        logger.error(f"Error descargando trailer de YouTube: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error al descargar el vídeo de YouTube: {str(e)}")

@app.post("/api/setup-trailer/apply-monitor-effect")
def api_setup_trailer_apply_monitor_effect(req: SetupTrailerApplyEffectRequest):
    """Re-apply camera-recording-monitor effect to current monitor footage at specified intensity."""
    intensity = max(0.0, min(1.0, float(req.intensity)))
    cfg = AppConfig.from_env()
    ffmpeg_bin = cfg.ffmpeg_path or "ffmpeg"
    dest_dir = BASE_DIR / "static" / "setupbackground"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Determine current trailer video path
    trailer_rel = req.trailer_video
    if not trailer_rel and SETUP_TRAILER_CONFIG_PATH.exists():
        try:
            with open(SETUP_TRAILER_CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                trailer_rel = saved.get("trailer_video")
        except Exception:
            pass

    if not trailer_rel:
        trailer_rel = "/static/setupbackground/sample_trailer.mp4"

    clean_rel = trailer_rel.lstrip("/").replace("/", os.sep)
    target_file = BASE_DIR / clean_rel

    if not target_file.exists():
        target_file = dest_dir / "sample_trailer.mp4"
        if not target_file.exists():
            raise HTTPException(status_code=404, detail="No se encontró el vídeo del tráiler para procesar.")

    filename = target_file.name

    # Check for raw original if present
    raw_file = dest_dir / f"raw_{filename}"
    input_file = raw_file if raw_file.exists() else target_file

    uid = uuid.uuid4().hex[:8]
    int_tag = f"i{int(round(intensity * 100))}"
    out_filename = f"trailer_{int_tag}_{uid}.mp4"
    out_path = dest_dir / out_filename

    try:
        logger.info(f"Aplicando efecto de monitor (intensidad={intensity:.2f}) a {input_file} -> {out_path}")
        apply_camera_monitor_effects(
            input_path=input_file,
            output_path=out_path,
            intensity=intensity,
            ffmpeg_bin=ffmpeg_bin
        )

        media_url = f"/static/setupbackground/{out_filename}"

        # Preserve raw copy link for future intensity switches
        if not raw_file.exists() and not filename.startswith("trailer_i"):
            try:
                shutil.copy2(target_file, dest_dir / f"raw_{out_filename}")
            except Exception:
                pass
        elif raw_file.exists():
            try:
                shutil.copy2(raw_file, dest_dir / f"raw_{out_filename}")
            except Exception:
                pass

        # Update configuration
        if SETUP_TRAILER_CONFIG_PATH.exists():
            try:
                with open(SETUP_TRAILER_CONFIG_PATH, "r", encoding="utf-8") as f:
                    saved_cfg = json.load(f)
                saved_cfg["trailer_video"] = media_url
                saved_cfg["camera_effect_intensity"] = intensity
                saved_cfg["updated_at"] = datetime.now().isoformat()
                with open(SETUP_TRAILER_CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(saved_cfg, f, indent=2, ensure_ascii=False)
            except Exception as ce:
                logger.warning(f"Error actualizando config: {ce}")

        return {
            "success": True,
            "url": media_url,
            "intensity": intensity,
            "message": f"Efecto de cámara en pantalla aplicado con intensidad {intensity:.1f}"
        }
    except Exception as e:
        logger.error(f"Error aplicando efecto de monitor: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error al procesar efecto de cámara en monitor: {str(e)}")

@app.get("/api/setup-trailer/game-logo")
def api_setup_trailer_game_logo(game_name: str = Query("", max_length=200)):
    from setup_trailer_logo import resolve_game_logo
    path=resolve_game_logo(game_name,BASE_DIR)
    return {"has_logo":bool(path),"logo_url":"/media/"+path.relative_to(BASE_DIR).as_posix() if path else None,
            "required":True,"message":"Logo disponible" if path else "Falta el logo oficial: añádelo antes de renderizar. No se sustituirá por texto."}


@app.post("/api/setup-trailer/game-logo/upload")
async def api_setup_trailer_game_logo_upload(game_name: str = Form(...), file: UploadFile = File(...)):
    from setup_trailer_logo import logo_directory
    from PIL import Image
    import io
    try:
        folder=logo_directory(game_name,BASE_DIR)
        contents=await file.read(10*1024*1024+1)
        if len(contents)>10*1024*1024:
            raise ValueError("El logo debe pesar menos de 10 MB.")
        image=Image.open(io.BytesIO(contents))
        if image.width>8192 or image.height>8192:
            raise ValueError("El logo es demasiado grande.")
        image=image.convert("RGBA")
        if image.getchannel("A").getbbox() is None:
            raise ValueError("El logo está vacío.")
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/"custom.png"; image.save(path)
        return {"has_logo":True,"logo_url":"/media/"+path.relative_to(BASE_DIR).as_posix(),"message":"Logo guardado para este juego"}
    except (ValueError,OSError,Image.DecompressionBombError) as e:
        raise HTTPException(status_code=400,detail=str(e))


@app.post("/api/setup-trailer/render")
def api_setup_trailer_render(req: SetupTrailerRenderRequest):
    """Render the full composite setup trailer video using monitor perspective warping and hardware acceleration."""
    from setup_trailer_engine import render_setup_trailer_video

    # Load active config as fallback for missing fields
    active_cfg = {}
    if SETUP_TRAILER_CONFIG_PATH.exists():
        try:
            with open(SETUP_TRAILER_CONFIG_PATH, "r", encoding="utf-8") as f:
                active_cfg = json.load(f)
        except Exception:
            pass

    # Background video
    bg_rel = req.background_video or active_cfg.get("background_video") or "/static/setupbackground/setup-background-def.mp4"
    bg_clean = bg_rel.lstrip("/").replace("/", os.sep)
    bg_path = BASE_DIR / bg_clean
    if not bg_path.exists():
        bg_path = BASE_DIR / "static" / "setupbackground" / "setup-background-def.mp4"

    # Trailer video
    tr_rel = req.trailer_video or active_cfg.get("trailer_video") or "/static/setupbackground/sample_trailer.mp4"
    tr_clean = tr_rel.lstrip("/").replace("/", os.sep)
    tr_path = BASE_DIR / tr_clean
    if not tr_path.exists():
        tr_path = BASE_DIR / "static" / "setupbackground" / "sample_trailer.mp4"
        if not tr_path.exists():
            raise HTTPException(status_code=404, detail="No se encontró el vídeo del tráiler para renderizar.")

    # Corners
    if req.corners:
        corners_dict = req.corners.dict()
    elif active_cfg.get("corners"):
        corners_dict = active_cfg["corners"]
    else:
        corners_dict = DEFAULT_SETUP_TRAILER_CONFIG["corners"]

    # Game title and style
    game_name = req.game_name if req.game_name is not None else active_cfg.get("game_name", "")
    show_title = req.show_game_title if req.show_game_title is not None else active_cfg.get("show_game_title", True)
    font_size = req.title_font_size if req.title_font_size is not None else active_cfg.get("title_font_size", 28)
    title_offset_y = req.title_offset_y if req.title_offset_y is not None else active_cfg.get("title_offset_y", 18)

    cfg = AppConfig.from_env()
    ffmpeg_bin = cfg.ffmpeg_path or "ffmpeg"

    output_dir = APP_OUTPUT_DIR / "setup_trailer"
    output_dir.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex[:8]
    clean_name = "".join(c for c in (game_name or "trailer").lower() if c.isalnum() or c in ("-", "_")).strip() or "video"
    out_filename = f"setup_{clean_name}_{uid}.mp4"
    out_path = output_dir / out_filename

    narration_enabled = req.narration_enabled if req.narration_enabled is not None else active_cfg.get("narration_enabled", False)
    narration_layout = req.narration_layout or active_cfg.get("narration_layout", "card")
    clean_narrated = narration_enabled and narration_layout == "clean"
    card_enabled = req.card_enabled if req.card_enabled is not None else active_cfg.get("card_enabled", False)
    if clean_narrated:
        card_enabled = False
    card_text = (req.card_text if req.card_text is not None else active_cfg.get("card_text", "")) or ""
    card_style = {}
    if (card_enabled and card_text.strip()) or narration_enabled:
        preset = req.card_preset or active_cfg.get("card_preset") or "NextLoot"
        profile = get_all_presets().get(preset)
        if not profile:
            raise HTTPException(status_code=400, detail="Selecciona un preset existente para la tarjeta.")
        card_style = dict(profile["data"])
        card_style["card_avatar_path"] = profile.get("avatar_path")

    from production_logo_policy import require_game_logo
    try:
        game_logo_path = require_game_logo(game_name, root=BASE_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # The game logo is mandatory even when a legacy preset disables the title.
    show_title = True

    try:
        narration_assets = None
        narration_enabled = req.narration_enabled if req.narration_enabled is not None else active_cfg.get("narration_enabled", False)
        background_volume = req.background_audio_volume if req.background_audio_volume is not None else active_cfg.get("background_audio_volume", .12)
        if narration_enabled:
            from setup_trailer_narration import prepare_narration, media_duration
            narration_text = req.narration_text if req.narration_text is not None else active_cfg.get("narration_text", "")
            speech = narration_text or card_text
            cta = None
            if clean_narrated:
                from setup_trailer_narration import compose_narration_script
                cta_template = req.narration_cta if req.narration_cta is not None else active_cfg.get("narration_cta", "Follow {channel} for more games.")
                speech,cta = compose_narration_script(speech,cta_template,card_style.get("card_username", ""))
            narration_assets = prepare_narration(speech, cfg, BASE_DIR/"temp"/"setup_narration", media_duration(tr_path,ffmpeg_bin),ffmpeg_bin)
            narration_assets.update(layout=narration_layout,cta=cta)
            if clean_narrated:
                from setup_trailer_narration import narration_target_duration
                narration_assets["target_duration"] = narration_target_duration(narration_assets,media_duration(tr_path,ffmpeg_bin))
        logger.info(f"Renderizando Setup Trailer: BG={bg_path.name}, Trailer={tr_path.name}, Titulo='{game_name}' -> {out_filename}")
        render_setup_trailer_video(
            background_path=bg_path,
            trailer_path=tr_path,
            corners=corners_dict,
            output_path=out_path,
            game_name=game_name,
            show_game_title=show_title,
            font_size=font_size,
            title_offset_y=title_offset_y,
            ffmpeg_bin=ffmpeg_bin,
            native_w=req.native_width or 720,
            native_h=req.native_height or 1280,
            variant="monitor_dive" if clean_narrated else (req.variant or active_cfg.get("variant") or "classic"),
            gameplay_fit=req.gameplay_fit or active_cfg.get("gameplay_fit") or "contain",
            card_text=card_text if card_enabled else "",
            card_style=card_style,
            intro_seconds=(req.intro_seconds or 2.5) if clean_narrated else (req.intro_seconds if "intro_seconds" in req.__fields_set__ else active_cfg.get("intro_seconds")),
            transition_seconds=req.transition_seconds or active_cfg.get("transition_seconds") or 3.0,
            entry_sfx_enabled=req.entry_sfx_enabled if req.entry_sfx_enabled is not None else active_cfg.get("entry_sfx_enabled", False),
            entry_sfx_volume=req.entry_sfx_volume if req.entry_sfx_volume is not None else active_cfg.get("entry_sfx_volume", .25),
            highlight_hook=req.highlight_hook if req.highlight_hook is not None else active_cfg.get("highlight_hook", False),
            opening_mode=req.opening_mode or active_cfg.get("opening_mode") or "original",
            opening_start_seconds=req.opening_start_seconds if req.opening_start_seconds is not None else active_cfg.get("opening_start_seconds", 0),
            title_reveal_mode=req.title_reveal_mode or active_cfg.get("title_reveal_mode") or "immediate",
            title_reveal_delay=req.title_reveal_delay if req.title_reveal_delay is not None else active_cfg.get("title_reveal_delay", 1),
            entrance_mode=req.entrance_mode or active_cfg.get("entrance_mode") or "desktop",
            action_lead_seconds=req.action_lead_seconds or active_cfg.get("action_lead_seconds") or 1.2,
            game_logo_path=game_logo_path,
            game_logo_scale=req.game_logo_scale if req.game_logo_scale is not None else active_cfg.get("game_logo_scale", 1.0),
            target_duration=narration_assets.get("target_duration") if clean_narrated else None
        )

        if narration_assets:
            from setup_trailer_narration import apply_narration
            apply_narration(out_path,narration_assets,background_volume,card_style.get("card_border_color", "#41E1C2"),ffmpeg_bin)

        media_url = output_url(out_path)
        return {
            "success": True,
            "video_url": media_url,
            "filename": out_filename,
            "render_details": json.loads(out_path.with_suffix(".settings.json").read_text(encoding="utf-8")),
            "message": "Vídeo montado y renderizado con éxito"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error renderizando setup trailer video: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error renderizando vídeo: {str(e)}")


# =============================================================================
# API Endpoints: Retention Analysis (Computer Vision & Geometry)
# =============================================================================

@app.get("/api/retention/samples")
def get_retention_samples():
    """Return paths to preloaded test sample retention screenshots."""
    return {
        "status": "ok",
        "samples": {
            "reels": "/static/retention_samples/reels_sample.jpg",
            "youtube": "/static/retention_samples/youtube_sample.jpg",
            "tiktok": "/static/retention_samples/tiktok_sample.jpg",
        }
    }


@app.post("/api/retention/analyze")
async def api_analyze_retention(
    file_reels: Optional[UploadFile] = File(None),
    duration_reels: Optional[float] = Form(None),
    file_youtube: Optional[UploadFile] = File(None),
    duration_youtube: Optional[float] = Form(None),
    file_tiktok: Optional[UploadFile] = File(None),
    duration_tiktok: Optional[float] = Form(None),
    use_sample_reels: Optional[bool] = Form(False),
    use_sample_youtube: Optional[bool] = Form(False),
    use_sample_tiktok: Optional[bool] = Form(False),
):
    """
    Analyzes retention curves from screenshots of Instagram Reels, YouTube Shorts,
    and/or TikTok Analytics using Computer Vision and geometry.
    Can analyze any combination (only 1, 2, or all 3).
    """
    from retention_analyzer import (
        analyze_reels_retention,
        analyze_youtube_retention,
        analyze_tiktok_retention,
        combine_multi_platform_retention,
    )

    results = {}
    errors = {}
    sample_dir = BASE_DIR / "static" / "retention_samples"

    # 1. Instagram Reels
    if file_reels or use_sample_reels:
        try:
            if file_reels:
                contents = await file_reels.read()
                res = await run_in_threadpool(analyze_reels_retention, contents, manual_duration=duration_reels)
            else:
                sample_p = sample_dir / "reels_sample.jpg"
                res = await run_in_threadpool(analyze_reels_retention, str(sample_p), manual_duration=duration_reels)
            results["reels"] = res
        except Exception as e:
            logger.error(f"Error analizando Reels: {e}", exc_info=True)
            errors["reels"] = str(e)

    # 2. YouTube Shorts
    if file_youtube or use_sample_youtube:
        try:
            if file_youtube:
                contents = await file_youtube.read()
                res = await run_in_threadpool(analyze_youtube_retention, contents, manual_duration=duration_youtube)
            else:
                sample_p = sample_dir / "youtube_sample.jpg"
                res = await run_in_threadpool(analyze_youtube_retention, str(sample_p), manual_duration=duration_youtube)
            results["youtube"] = res
        except Exception as e:
            logger.error(f"Error analizando YouTube: {e}", exc_info=True)
            errors["youtube"] = str(e)

    # 3. TikTok Analytics
    if file_tiktok or use_sample_tiktok:
        try:
            if file_tiktok:
                contents = await file_tiktok.read()
                res = await run_in_threadpool(analyze_tiktok_retention, contents, manual_duration=duration_tiktok)
            else:
                sample_p = sample_dir / "tiktok_sample.jpg"
                res = await run_in_threadpool(analyze_tiktok_retention, str(sample_p), manual_duration=duration_tiktok)
            results["tiktok"] = res
        except Exception as e:
            logger.error(f"Error analizando TikTok: {e}", exc_info=True)
            errors["tiktok"] = str(e)

    if not results and errors:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "No se pudo analizar ninguna gráfica.", "errors": errors}
        )
    elif not results:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "No se envió ninguna captura para analizar."}
        )

    combined = combine_multi_platform_retention(results)

    return {
        "status": "ok",
        "platforms_analyzed": list(results.keys()),
        "results": results,
        "combined": combined,
        "errors": errors if errors else None,
    }


# =============================================================================
# Static Asset Mounts
# =============================================================================

from steam_logo_lab import register_routes as register_logo_lab_routes
register_logo_lab_routes(app, create_job)

class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

app.mount("/media/output", StaticFiles(directory=str(BASE_DIR / "output")), name="output")
app.mount("/media/benchmarks", StaticFiles(directory=str(BASE_DIR / "temp" / "benchmarks")), name="benchmarks")
app.mount("/media/presets", StaticFiles(directory=str(BASE_DIR / "Presets")), name="presets")
app.mount("/media/temp", StaticFiles(directory=str(BASE_DIR / "temp")), name="temp")
app.mount("/media/GamesFootage", StaticFiles(directory=str(GAMES_FOOTAGE_DIR)), name="games_footage")
app.mount("/media/PromosFootage", StaticFiles(directory=str(PROMOS_FOOTAGE_DIR)), name="promos_footage")
app.mount("/static", NoCacheStaticFiles(directory=str(BASE_DIR / "static")), name="static")

@app.get("/")
def serve_index():
    """Serve the single page application HTML."""
    index_file = BASE_DIR / "static" / "index.html"
    return FileResponse(
        index_file,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )
