"""
video_analyzer.py — Program 1: Shot & Second-by-Second Multimodal Video Analyzer

Analyzes all video footage in a directory:
1. Probes technical video metadata (duration, resolution, fps).
2. Performs shot & cut transition detection (PySceneDetect or OpenCV fallback).
3. Uploads footage to Gemini File API to generate deep multimodal second-by-second
   breakdown (visuals, audio, action intensity, hook score).
4. Saves output as [filename].breakdown.json conforming to schemas_v1.VideoBreakdown.
"""

from __future__ import annotations

import api_usage as _api_usage

import argparse
import json
import logging
import math
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, List, Optional, Tuple

import cv2

from config import AppConfig
from schemas_v1 import ShotScene, SecondData, VideoBreakdown, VideoMetadata
from utils import FFmpegError, safe_json_parse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("video_analyzer")

SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}

# =============================================================================
# Gemini Multimodal Vision Models (Ordered by Speed, Efficiency & Capability)
# =============================================================================
GEMINI_VISION_MODELS: List[str] = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.5-pro",
    "gemini-2.5-pro",
    "gemini-3.1-pro-preview",
]


# =============================================================================
# Video Chunking & Merging Logic
# =============================================================================

def compute_video_chunks(duration_seconds: float) -> List[Tuple[float, float, float]]:
    """
    Compute time ranges (start_sec, end_sec, dur_sec) for video analysis:
    - <= 300s (<= 5 min): 1 chunk (no split needed).
    - 300s < dur <= 900s (5 to 15 min): 3 equal chunks (dur/3 each, all <= 5 min).
    - > 900s (> 15 min): N chunks where N = math.ceil(dur / 300.0) so all chunks are <= 300s (max 5 min).
    """
    dur = float(duration_seconds)
    if dur <= 300.0:
        return [(0.0, dur, dur)]

    if dur <= 900.0:
        num_chunks = 3
    else:
        num_chunks = max(3, math.ceil(dur / 300.0))

    chunk_dur = round(dur / num_chunks, 2)
    chunks = []
    for i in range(num_chunks):
        st = round(i * chunk_dur, 2)
        if i == num_chunks - 1:
            et = dur
        else:
            et = round((i + 1) * chunk_dur, 2)
        c_dur = round(et - st, 2)
        if c_dur > 0:
            chunks.append((st, et, c_dur))
    return chunks


def merge_chunk_breakdowns(
    original_video_path: Path,
    full_metadata: VideoMetadata,
    chunk_ranges: List[Tuple[float, float, float]],
    chunk_breakdowns: List[VideoBreakdown],
    footage_is_already_9_16: bool = False,
) -> VideoBreakdown:
    """
    Merge multiple chunk breakdowns into a single consolidated VideoBreakdown.
    Recalculates scene start/end times and second-by-second timestamps with each chunk's time offset.
    """
    if len(chunk_breakdowns) == 1:
        bd = chunk_breakdowns[0]
        bd_dict = bd.model_dump()
        bd_dict["source_file"] = str(original_video_path)
        bd_dict["metadata"] = full_metadata.model_dump()
        return VideoBreakdown.model_validate(bd_dict)

    all_scenes: List[ShotScene] = []
    all_seconds: List[SecondData] = []
    scene_counter = 1
    is_native_vertical = bool(footage_is_already_9_16 or (full_metadata.height > full_metadata.width))
    summaries = []

    for i, (st_offset, et_offset, c_dur) in enumerate(chunk_ranges):
        bd = chunk_breakdowns[i]
        if bd.overall_summary:
            summaries.append(f"[Parte {i+1}/{len(chunk_ranges)} ({st_offset:.0f}s-{et_offset:.0f}s)]: {bd.overall_summary}")

        for sc in bd.scenes:
            sc_dict = sc.model_dump()
            sc_dict["scene_id"] = scene_counter
            scene_counter += 1
            sc_dict["start_time"] = round(sc.start_time + st_offset, 2)
            sc_dict["end_time"] = round(sc.end_time + st_offset, 2)
            sc_dict["duration"] = round(sc_dict["end_time"] - sc_dict["start_time"], 2)
            if is_native_vertical:
                sc_dict["score_9_16"] = 10
                sc_dict["is_9_16_acceptable"] = True
                sc_dict["reason_9_16"] = "Metraje vertical 9:16 nativo"
            all_scenes.append(ShotScene.model_validate(sc_dict))

        for sec in bd.seconds_breakdown:
            sec_dict = sec.model_dump()
            sec_dict["second"] = int(round(sec.second + st_offset))
            all_seconds.append(SecondData.model_validate(sec_dict))

    # Determine top hook scenes
    top_hook_scenes: List[int] = []
    opening_scenes = [sc for sc in all_scenes if sc.start_time <= 30.0]
    for sc in sorted(opening_scenes, key=lambda x: (x.hook_potential, x.action_intensity), reverse=True):
        if sc.scene_id not in top_hook_scenes:
            top_hook_scenes.append(sc.scene_id)
    for sc in sorted(all_scenes, key=lambda x: (x.hook_potential, x.action_intensity), reverse=True):
        if sc.scene_id not in top_hook_scenes:
            top_hook_scenes.append(sc.scene_id)
        if len(top_hook_scenes) >= 5:
            break

    # Final splash start time from last chunk
    final_splash_start_time = None
    last_chunk_bd = chunk_breakdowns[-1]
    if last_chunk_bd.final_splash_start_time is not None:
        last_offset = chunk_ranges[-1][0]
        final_splash_start_time = round(last_chunk_bd.final_splash_start_time + last_offset, 2)

    combined_summary = "\n\n".join(summaries) if summaries else (chunk_breakdowns[0].overall_summary if chunk_breakdowns else "")
    combined_title = chunk_breakdowns[0].video_title if chunk_breakdowns else original_video_path.stem

    return VideoBreakdown(
        source_file=str(original_video_path),
        video_title=combined_title,
        overall_summary=combined_summary,
        metadata=full_metadata,
        scenes=all_scenes,
        seconds_breakdown=all_seconds,
        top_hook_scenes=top_hook_scenes,
        final_splash_start_time=final_splash_start_time,
    )


# =============================================================================
# Video Technical Probing & Proxy Generation
# =============================================================================

def probe_video_metadata(video_path: Path) -> VideoMetadata:
    """Extract metadata using OpenCV with fallback."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Unable to open video file: {video_path}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps if fps > 0 else 0.0
    cap.release()

    return VideoMetadata(
        duration_seconds=round(duration, 2),
        width=width,
        height=height,
        fps=round(fps, 2),
        filename=video_path.name,
    )


def is_valid_video_file(video_path: Path) -> bool:
    """Validate that a video file exists, is non-empty, and can be cleanly read without corrupt NAL headers."""
    if not video_path.exists() or video_path.stat().st_size < 1024:
        return False
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", str(video_path)],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if res.returncode != 0:
            return False
        stderr_lower = res.stderr.lower()
        if any(err_token in stderr_lower for err_token in ["nal", "corrupt", "invalid", "error splitting"]):
            return False
        return True
    except Exception:
        # Fallback to OpenCV
        try:
            cap = cv2.VideoCapture(str(video_path))
            opened = cap.isOpened()
            count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if opened else 0
            cap.release()
            return opened and count > 0
        except Exception:
            return False


def get_or_create_720p_proxy(
    video_path: Path,
    temp_dir: Optional[Path] = None,
    ffmpeg_bin: str = "ffmpeg",
    target_height: int = 720,
    target_aspect: str = "auto",
    start_time: Optional[float] = None,
    duration: Optional[float] = None,
    chunk_index: Optional[int] = None,
    total_chunks: Optional[int] = None,
) -> Path:
    """
    Generate or return a lightweight proxy video for fast OpenCV cut detection and Gemini upload.
    If start_time and duration are specified, extracts and scales that specific chunk directly.
    If target_aspect is '9:16', horizontal videos are center-cropped directly to 720x1280 vertical,
    allowing Gemini to evaluate the real framed vertical output.
    Uses atomic writes and integrity validation to prevent corrupted files from ever being cached.
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    meta = probe_video_metadata(video_path)

    # Check if original can be used directly without proxy (only if no time slice requested)
    is_chunk = (start_time is not None and duration is not None and (chunk_index is not None or duration < meta.duration_seconds - 0.5))
    if not is_chunk:
        if target_aspect == "9:16":
            if meta.height > meta.width and min(meta.width, meta.height) <= 720:
                logger.debug(f"Video {video_path.name} is already native vertical <= 720p. Skipping proxy.")
                return video_path
        else:
            min_dim = min(meta.width, meta.height)
            if min_dim <= target_height and min_dim > 0:
                logger.debug(f"Video {video_path.name} is already {meta.width}x{meta.height} (<= {target_height}p). Skipping proxy.")
                return video_path

    # Prepare proxy destination in temp/proxies/
    if temp_dir is None:
        temp_dir = video_path.parent.parent / "temp" if (video_path.parent.parent / "temp").exists() else Path("temp")
    proxy_dir = temp_dir / "proxies"
    proxy_dir.mkdir(parents=True, exist_ok=True)

    # Safe clean name incorporating target resolution/aspect and chunk index
    clean_stem = re.sub(r"[^\w\-]", "_", video_path.stem)
    chunk_suffix = f"_chunk_{chunk_index + 1}_of_{total_chunks}" if (chunk_index is not None and total_chunks is not None) else ""
    if target_aspect == "9:16":
        proxy_filename = f"{clean_stem}{chunk_suffix}_9_16_proxy.mp4"
    else:
        proxy_filename = f"{clean_stem}{chunk_suffix}_{target_height}p_proxy.mp4"
    proxy_path = proxy_dir / proxy_filename

    # Check cache: if proxy exists, verify its integrity before accepting it
    if proxy_path.exists():
        if is_valid_video_file(proxy_path) and proxy_path.stat().st_mtime >= video_path.stat().st_mtime:
            logger.info(f"Using cached proxy: {proxy_path.name} ({proxy_path.stat().st_size / (1024*1024):.1f} MB)")
            return proxy_path
        else:
            logger.warning(f"Cached proxy {proxy_path.name} is corrupted, invalid, or outdated. Purging to re-encode...")
            try:
                proxy_path.unlink()
            except Exception:
                pass

    if target_aspect == "9:16":
        logger.info(
            f"Creating 9:16 vertical proxy (720x1280){' ' + chunk_suffix if chunk_suffix else ''} for AI vision analysis: {video_path.name} "
            f"({meta.width}x{meta.height} -> 720x1280 center-cropped)..."
        )
        if meta.width > meta.height:
            scale_filter = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,format=yuv420p"
        else:
            scale_filter = "scale=720:-2,format=yuv420p"
    else:
        logger.info(
            f"Creating {target_height}p proxy{' ' + chunk_suffix if chunk_suffix else ''} for fast AI analysis: {video_path.name} "
            f"({meta.width}x{meta.height} -> ~{target_height}p)..."
        )
        if meta.width >= meta.height:
            scale_filter = f"scale=-2:{target_height},format=yuv420p"
        else:
            scale_filter = f"scale={target_height}:-2,format=yuv420p"

    # Write to a unique temporary file first to guarantee atomic creation
    temp_proxy_path = proxy_dir / f"{proxy_path.stem}.part_{uuid.uuid4().hex[:6]}.mp4"

    cmd = [ffmpeg_bin, "-y"]
    if start_time is not None and start_time > 0:
        cmd.extend(["-ss", f"{start_time:.2f}"])
    cmd.extend(["-i", str(video_path)])
    if duration is not None and duration > 0:
        cmd.extend(["-t", f"{duration:.2f}"])
    cmd.extend([
        "-vf", scale_filter,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "96k",
        "-movflags", "+faststart",
        str(temp_proxy_path),
    ])

    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if res.returncode != 0 or not temp_proxy_path.exists():
            logger.warning(
                f"FFmpeg proxy generation failed for {video_path.name} (code {res.returncode}):\n{res.stderr[-300:]}\n"
                "Falling back to original video for analysis."
            )
            if temp_proxy_path.exists():
                try: temp_proxy_path.unlink()
                except Exception: pass
            return video_path

        # Verify integrity of generated proxy before placing into cache
        if not is_valid_video_file(temp_proxy_path):
            logger.warning(
                f"Generated proxy {temp_proxy_path.name} failed integrity check. Falling back to original video."
            )
            if temp_proxy_path.exists():
                try: temp_proxy_path.unlink()
                except Exception: pass
            return video_path

        # Atomically replace final proxy file
        if proxy_path.exists():
            try: proxy_path.unlink()
            except Exception: pass
        temp_proxy_path.replace(proxy_path)

        orig_size_mb = video_path.stat().st_size / (1024 * 1024)
        proxy_size_mb = proxy_path.stat().st_size / (1024 * 1024)
        logger.info(
            f"Proxy ready and verified: {proxy_path.name} [{proxy_size_mb:.1f} MB]"
        )
        return proxy_path

    except Exception as e:
        logger.warning(f"Error executing FFmpeg for proxy: {e}. Falling back to original video.")
        if temp_proxy_path.exists():
            try: temp_proxy_path.unlink()
            except Exception: pass
        return video_path


# =============================================================================
# Shot / Scene Boundary Detection
# =============================================================================

def detect_scenes_pyscenedetect(video_path: Path) -> List[Tuple[float, float]]:
    """Detect shot transitions using PySceneDetect if installed."""
    try:
        from scenedetect import SceneManager, open_video
        from scenedetect.detectors import ContentDetector

        video = open_video(str(video_path))
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=27.0))
        scene_manager.detect_scenes(video)
        scene_list = scene_manager.get_scene_list()

        return [(s[0].get_seconds(), s[1].get_seconds()) for s in scene_list]
    except ImportError:
        logger.debug("scenedetect is not installed. Using OpenCV fallback.")
        return []
    except Exception as e:
        logger.warning(f"PySceneDetect failed: {e}. Using OpenCV fallback.")
        return []


def detect_scenes_opencv(video_path: Path, min_scene_len: float = 1.5) -> List[Tuple[float, float]]:
    """Fast native shot boundary detection using HSV histogram differences."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps if fps > 0 else 0.0

    # Sample 4 frames per second for high-speed analysis
    sample_step = max(1, int(fps / 4.0))
    prev_hist = None
    cuts: List[float] = [0.0]

    current_frame = 0
    while cap.isOpened():
        cap.set(cv2.CAP_PROP_POS_FRAMES, current_frame)
        ret, frame = cap.read()
        if not ret:
            break

        timestamp = current_frame / fps
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)

        if prev_hist is not None:
            # Bhattacharyya distance or correlation
            diff = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA)
            # A score > 0.55 indicates a dramatic visual change (cut)
            if diff > 0.55 and (timestamp - cuts[-1]) >= min_scene_len:
                cuts.append(round(timestamp, 2))

        prev_hist = hist
        current_frame += sample_step

    cap.release()

    if cuts[-1] < duration:
        cuts.append(round(duration, 2))

    # Format into (start, end) tuples
    scenes = []
    for i in range(len(cuts) - 1):
        s_in = cuts[i]
        s_out = cuts[i + 1]
        if s_out > s_in:
            scenes.append((s_in, s_out))

    return scenes


def get_video_scenes(video_path: Path, duration: float) -> List[Tuple[float, float]]:
    """Determine scenes using PySceneDetect or OpenCV, with safe uniform chunk fallback."""
    scenes = detect_scenes_pyscenedetect(video_path)
    if not scenes:
        scenes = detect_scenes_opencv(video_path)

    # Fallback: if no cuts found (single continuous shot), chunk every 3-4s
    if len(scenes) <= 1 and duration > 5.0:
        chunk_size = 3.0
        scenes = []
        curr = 0.0
        while curr < duration:
            end = min(curr + chunk_size, duration)
            scenes.append((round(curr, 2), round(end, 2)))
            curr = end

    return scenes


# =============================================================================
# Multimodal Analysis via Gemini
# =============================================================================

class GeminiVideoAnalyzer:
    """Handles Gemini File API upload, polling, and structured content generation with quota fallback."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self.api_key = api_key
        self.configured_model = model
        self.max_retries = 3
        self.retry_delay = 2.0

        try:
            from google import genai
            self._client = genai.Client(api_key=api_key)
        except ImportError:
            raise RuntimeError(
                "The 'google-genai' package is required. Install with: pip install google-genai"
            )

    def get_candidate_models(self) -> List[str]:
        """Return ordered list of vision-capable models, prioritizing the configured model."""
        models: List[str] = []
        if self.configured_model and self.configured_model not in models:
            models.append(self.configured_model)
        for m in GEMINI_VISION_MODELS:
            if m not in models:
                models.append(m)
        return models

    def analyze_video(
        self,
        video_path: Path,
        metadata: VideoMetadata,
        prompt_template: str,
        detected_scenes: List[Tuple[float, float]],
        original_video_path: Optional[Path] = None,
        target_aspect: str = "auto",
    ) -> VideoBreakdown:
        """Upload video to Gemini File API and obtain structured breakdown with automatic quota fallback."""
        from google.genai import types
        from google.genai import errors

        uploaded_file = None
        last_error_msg = "Unknown error"
        target_source_path = original_video_path or video_path

        try:
            for attempt in range(self.max_retries):
                try:
                    # Adaptive candidate file selection:
                    # If initial attempt failed with cloud processing failure and video_path was a proxy,
                    # automatically fall back to original source video!
                    file_to_upload = video_path
                    if attempt > 0 and target_source_path != video_path and target_source_path.exists():
                        logger.warning(
                            f"Previous upload of proxy {video_path.name} failed in Gemini Cloud. "
                            f"Falling back to original source video: {target_source_path.name}"
                        )
                        file_to_upload = target_source_path

                    logger.info(
                        f"Uploading {file_to_upload.name} ({metadata.duration_seconds:.1f}s) to Gemini File API "
                        f"(attempt {attempt + 1}/{self.max_retries})..."
                    )
                    uploaded_file = self._client.files.upload(file=file_to_upload)

                    # Poll file processing status until ACTIVE or FAILED
                    while True:
                        uploaded_file = self._client.files.get(name=uploaded_file.name)
                        state = uploaded_file.state.name
                        if state != "PROCESSING":
                            break
                        logger.info(f"Video is processing in Gemini Cloud... ({file_to_upload.name})")
                        time.sleep(3)

                    if state == "FAILED":
                        err_msg = getattr(uploaded_file.error, "message", "The file failed to be processed.")
                        last_error_msg = err_msg
                        logger.warning(
                            f"Gemini video processing failed on attempt {attempt + 1} for {file_to_upload.name}: {err_msg}"
                        )
                        try:
                            self._client.files.delete(name=uploaded_file.name)
                        except Exception:
                            pass
                        uploaded_file = None

                        # If failed file was a proxy on disk, purge it so it won't be cached
                        if file_to_upload != target_source_path and file_to_upload.exists():
                            try:
                                logger.info(f"Purging problematic proxy from disk: {file_to_upload.name}")
                                file_to_upload.unlink()
                            except Exception:
                                pass

                        if attempt < self.max_retries - 1:
                            wait_time = self.retry_delay * (2 ** attempt)
                            logger.info(f"Retrying Gemini upload/processing in {wait_time:.1f}s...")
                            time.sleep(wait_time)
                            continue
                        else:
                            raise RuntimeError(
                                f"Gemini video processing failed after {self.max_retries} attempts: {last_error_msg}"
                            )

                    logger.info(f"Video is ACTIVE and ready for multimodal inference: {uploaded_file.name}")
                    break

                except errors.APIError as e:
                    status_code = getattr(e, "code", None)
                    if status_code in (429, 503, 500) and attempt < self.max_retries - 1:
                        wait_time = self.retry_delay * (2 ** attempt)
                        logger.warning(f"Upload API error {status_code}. Retrying in {wait_time:.1f}s...")
                        time.sleep(wait_time)
                    else:
                        raise

            if uploaded_file is None:
                raise RuntimeError(f"Failed to prepare video file in Gemini File API: {last_error_msg}")

            # Prepare Prompt with resilient formatting
            try:
                formatted_prompt = prompt_template.format(
                    source_filename=target_source_path.name,
                    duration_seconds=metadata.duration_seconds,
                    duration_seconds_int=int(metadata.duration_seconds),
                    width=metadata.width,
                    height=metadata.height,
                    fps=metadata.fps,
                )
            except Exception as fmt_err:
                logger.warning(f"str.format error ({fmt_err}), falling back to direct string replacement...")
                formatted_prompt = (
                    prompt_template
                    .replace("{source_filename}", str(target_source_path.name))
                    .replace("{duration_seconds:.2f}", f"{metadata.duration_seconds:.2f}")
                    .replace("{duration_seconds}", f"{metadata.duration_seconds:.2f}")
                    .replace("{duration_seconds_int}", str(int(metadata.duration_seconds)))
                    .replace("{width}", str(metadata.width))
                    .replace("{height}", str(metadata.height))
                    .replace("{fps}", str(metadata.fps))
                )

            # Pass detected scene cut hints to anchor Gemini's temporal reasoning
            if detected_scenes:
                scene_hints = "\n## Pre-detected Technical Cuts (timestamps in seconds):\n"
                scene_hints += ", ".join([f"{s[0]:.1f}-{s[1]:.1f}s" for s in detected_scenes[:40]])
                formatted_prompt += scene_hints

            # If the proxy passed to Gemini is in real 9:16 vertical format
            if target_aspect == "9:16" or metadata.height > metadata.width:
                vertical_notice = (
                    "\n\n## 📱 METRAJE EN FORMATO VERTICAL 9:16 REAL (DIRECTIVA CRÍTICA):\n"
                    "- El archivo de vídeo que estás analizando YA ha sido transformado y encuadrado exactamente en formato vertical 9:16 para móviles (720x1280).\n"
                    "- Lo que ves en pantalla es EXACTAMENTE lo que verán los espectadores en TikTok, YouTube Shorts e Instagram Reels.\n"
                    "- ¡NO necesitas imaginar ni proyectar ningún recorte mental!\n"
                    "- Para cada plano o escena detectada, evalúa directamente la composición visual que ves en pantalla:\n"
                    "  * Si la acción, personaje o gameplay principal se ve centrado, claro y atractivo: asigna `score_9_16`: 8-10 (`is_9_16_acceptable`: true).\n"
                    "  * Si la escena es perfectamente comprensible y utilizable: asigna `score_9_16`: 5-7 (`is_9_16_acceptable`: true).\n"
                    "  * ÚNICAMENTE si la acción o texto esencial han quedado mutilados por los bordes laterales: asigna `score_9_16`: 1-4 (`is_9_16_acceptable`: false).\n"
                )
                formatted_prompt += vertical_notice

            # Configure Generation for JSON
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.4,
            )

            candidate_models = self.get_candidate_models()
            raw_text = None
            last_gen_err = None

            for idx, model_name in enumerate(candidate_models):
                logger.info(
                    f"Requesting deep multimodal breakdown from vision model [{idx+1}/{len(candidate_models)}] '{model_name}'..."
                )
                try:
                    response = _api_usage.usage_generate(self._client.models.generate_content, 
                        model=model_name,
                        contents=[uploaded_file, formatted_prompt],
                        config=config,
                    )
                    if response and response.text:
                        raw_text = response.text
                        logger.info(f"Multimodal vision inference successful with model '{model_name}'.")
                        break
                except Exception as gen_err:
                    last_gen_err = gen_err
                    err_str = str(gen_err)
                    is_quota = any(q in err_str.lower() for q in [
                        "429", "quota", "resource_exhausted", "resourceexhausted", "rate_limit", "rate limit", "too many requests"
                    ])
                    is_not_found = any(u in err_str.lower() for u in ["404", "not_found", "not found", "no longer available"])
                    is_unavailable = any(u in err_str.lower() for u in [
                        "503", "unavailable", "500", "overloaded", "high demand"
                    ])

                    if is_quota:
                        logger.warning(
                            f"⚠️ Error de Cuota / Rate Limit (429) en el modelo '{model_name}'. "
                            f"Cambiando inmediatamente al siguiente modelo de visión..."
                        )
                    elif is_not_found:
                        logger.warning(
                            f"⚠️ Modelo '{model_name}' no disponible o discontinuado ({err_str[:100]}). "
                            f"Probando siguiente modelo de visión..."
                        )
                    elif is_unavailable:
                        logger.warning(
                            f"⚠️ Modelo '{model_name}' con alta demanda/temporalmente no disponible ({err_str[:100]}). "
                            f"Probando siguiente modelo de visión..."
                        )
                        time.sleep(1.0)
                    else:
                        logger.warning(
                            f"Error en modelo de visión '{model_name}': {err_str[:120]}. "
                            f"Probando siguiente modelo..."
                        )

            if not raw_text:
                raise RuntimeError(
                    f"Error al analizar el vídeo con IA Multimodal tras probar todos los modelos de visión ({candidate_models}): {last_gen_err}"
                )

            # Clean markdown codeblocks if model returned any
            raw_text = re.sub(r"^```json\s*", "", raw_text.strip(), flags=re.IGNORECASE)
            raw_text = re.sub(r"^```\s*", "", raw_text.strip())
            raw_text = re.sub(r"\s*```$", "", raw_text.strip())

            data = safe_json_parse(raw_text)
            # Ensure metadata consistency (preserves original video path and original high-res dimensions)
            data["source_file"] = str(original_video_path or video_path)
            data["metadata"] = metadata.model_dump()

            # Sanitize scenes to prevent negative durations, inverted timestamps, or missing 9:16 fields
            is_native_vertical = bool(metadata.height > metadata.width)
            if isinstance(data.get("scenes"), list):
                for sc in data["scenes"]:
                    if isinstance(sc, dict):
                        try:
                            s = max(0.0, float(sc.get("start_time", 0.0)))
                            e = max(0.0, float(sc.get("end_time", 0.0)))
                            if e < s:
                                s, e = e, s
                            dur = sc.get("duration")
                            if dur is None or float(dur) <= 0.0:
                                dur = max(0.1, round(e - s, 2))
                            else:
                                dur = max(0.1, abs(float(dur)))
                            if e <= s:
                                e = round(s + dur, 2)
                            sc["start_time"] = round(s, 2)
                            sc["end_time"] = round(e, 2)
                            sc["duration"] = round(dur, 2)

                            if is_native_vertical:
                                sc["score_9_16"] = 10
                                sc["is_9_16_acceptable"] = True
                                sc["reason_9_16"] = "Metraje vertical 9:16 nativo (resolución vertical)"
                            else:
                                raw_sc = sc.get("score_9_16")
                                if raw_sc is not None:
                                    sc_val = max(1, min(10, int(round(float(raw_sc)))))
                                    sc["score_9_16"] = sc_val
                                    sc["is_9_16_acceptable"] = (sc_val >= 5)
                                else:
                                    raw_acc = sc.get("is_9_16_acceptable", True)
                                    sc["is_9_16_acceptable"] = bool(raw_acc)
                                    sc["score_9_16"] = 8 if raw_acc else 3
                        except Exception:
                            pass

            breakdown = VideoBreakdown.model_validate(data)
            return breakdown

        finally:
            if uploaded_file is not None:
                try:
                    logger.info(f"Cleaning up file from Gemini servers: {uploaded_file.name}")
                    self._client.files.delete(name=uploaded_file.name)
                except Exception as e:
                    logger.debug(f"Failed to delete remote file: {e}")


# =============================================================================
# Main Program 1 Orchestrator
# =============================================================================

class VideoAnalyzer:
    """Scans folders, runs local detection, and coordinates Gemini breakdowns with chunking."""

    def __init__(
        self,
        config: AppConfig,
        prompts_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
    ) -> None:
        self.config = config
        self.prompts_dir = prompts_dir or config.prompts_dir
        self.output_dir = output_dir or (config.input_dir.parent / "data" / "breakdowns")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        prompt_path = self.prompts_dir / "video_breakdown_prompt.txt"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt template not found: {prompt_path}")
        self.prompt_template = prompt_path.read_text(encoding="utf-8")

        # Initialize Gemini Client
        api_key = config.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not configured in .env or environment")
        
        # Use config model or fallback to 2.5-flash / 3.5-flash
        model = config.gemini_model or "gemini-2.5-flash"
        self.client = GeminiVideoAnalyzer(api_key=api_key, model=model)

    def process_video(
        self,
        video_path: Path,
        force: bool = False,
        footage_is_already_9_16: bool = False,
        target_aspect: str = "auto",
    ) -> VideoBreakdown:
        """Process a video (with automatic chunking for >5min files and granular caching) into a breakdown JSON file."""
        aspect_suffix = "9_16." if target_aspect == "9:16" else ""
        output_file = self.output_dir / f"{video_path.stem}.{aspect_suffix}breakdown.json"

        # If complete breakdown exists and not force, reuse it directly
        if output_file.exists() and not force:
            logger.info(f"Breakdown already exists (cached): {output_file.name}")
            bd = VideoBreakdown.load_json(output_file)
            if footage_is_already_9_16 or (bd.metadata.height > bd.metadata.width):
                for sc in bd.scenes:
                    sc.score_9_16 = 10
                    sc.is_9_16_acceptable = True
                    sc.reason_9_16 = "Metraje vertical 9:16 nativo"
            return bd

        logger.info(f"Analyzing video: {video_path.name} (target_aspect={target_aspect})")
        full_metadata = probe_video_metadata(video_path)
        dur = full_metadata.duration_seconds
        logger.info(f"Metadata: {full_metadata.width}x{full_metadata.height}, {full_metadata.fps}fps, {dur}s ({dur/60.0:.1f} min)")

        # Compute chunk time ranges (<=5min: 1 chunk; 5-15min: 3 chunks of dur/3; >15min: N chunks <= 5min)
        chunk_ranges = compute_video_chunks(dur)
        num_chunks = len(chunk_ranges)
        if num_chunks > 1:
            logger.info(
                f"Video exceeds 5 minutes ({dur:.1f}s). Split into {num_chunks} chunks of ~{chunk_ranges[0][2]:.1f}s each for robust analysis."
            )

        # Chunks cache directory
        chunks_dir = self.output_dir / ".chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)

        chunk_breakdowns: List[VideoBreakdown] = []

        for idx, (c_start, c_end, c_dur) in enumerate(chunk_ranges):
            chunk_tag = f"chunk_{idx+1}_of_{num_chunks}"
            chunk_file = chunks_dir / f"{video_path.stem}.{chunk_tag}.{aspect_suffix}breakdown.json"

            # Check if this specific chunk was already analyzed in a previous attempt
            if chunk_file.exists() and not force:
                logger.info(f"Reusing already analyzed chunk [{idx+1}/{num_chunks}] from cache: {chunk_file.name}")
                chunk_bd = VideoBreakdown.load_json(chunk_file)
                chunk_breakdowns.append(chunk_bd)
                continue

            logger.info(
                f"Processing chunk [{idx+1}/{num_chunks}] ({c_start:.1f}s - {c_end:.1f}s, dur={c_dur:.1f}s) for {video_path.name}..."
            )

            chunk_meta = VideoMetadata(
                duration_seconds=c_dur,
                width=full_metadata.width,
                height=full_metadata.height,
                fps=full_metadata.fps,
                filename=f"{video_path.stem}_{chunk_tag}{video_path.suffix}",
            )

            # Generate lightweight proxy for this chunk
            if getattr(self.config, "enable_proxy_analysis", True):
                analysis_path = get_or_create_720p_proxy(
                    video_path=video_path,
                    temp_dir=getattr(self.config, "temp_dir", None),
                    ffmpeg_bin=getattr(self.config, "ffmpeg_path", "ffmpeg"),
                    target_height=getattr(self.config, "proxy_target_height", 720),
                    target_aspect=target_aspect,
                    start_time=c_start,
                    duration=c_dur,
                    chunk_index=idx if num_chunks > 1 else None,
                    total_chunks=num_chunks if num_chunks > 1 else None,
                )
            else:
                analysis_path = video_path

            chunk_scenes = get_video_scenes(analysis_path, c_dur)
            logger.info(f"Chunk [{idx+1}/{num_chunks}] detected {len(chunk_scenes)} technical shots/scenes")

            chunk_bd = self.client.analyze_video(
                video_path=analysis_path,
                metadata=chunk_meta,
                prompt_template=self.prompt_template,
                detected_scenes=chunk_scenes,
                original_video_path=video_path,
                target_aspect=target_aspect,
            )

            # Save chunk breakdown immediately to disk so it is never re-run if a later chunk fails
            chunk_bd.save_json(chunk_file)
            logger.info(f"Chunk [{idx+1}/{num_chunks}] breakdown saved to disk: {chunk_file.name}")
            chunk_breakdowns.append(chunk_bd)

        # Merge all chunks into consolidated full breakdown
        merged_breakdown = merge_chunk_breakdowns(
            original_video_path=video_path,
            full_metadata=full_metadata,
            chunk_ranges=chunk_ranges,
            chunk_breakdowns=chunk_breakdowns,
            footage_is_already_9_16=footage_is_already_9_16,
        )

        if footage_is_already_9_16 or (full_metadata.height > full_metadata.width):
            for sc in merged_breakdown.scenes:
                sc.score_9_16 = 10
                sc.is_9_16_acceptable = True
                sc.reason_9_16 = "Metraje vertical 9:16 nativo"

        merged_breakdown.save_json(output_file)
        logger.info(
            f"Full consolidated breakdown successfully saved to: {output_file} "
            f"({len(merged_breakdown.scenes)} scenes, {len(merged_breakdown.seconds_breakdown)} seconds)"
        )
        return merged_breakdown

    def process_directory(self, input_dir: Path, force: bool = False) -> List[VideoBreakdown]:
        """Process all videos in input_dir."""
        video_files = [
            f for f in input_dir.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]

        if not video_files:
            logger.warning(f"No supported video files found in {input_dir}")
            return []

        logger.info(f"Found {len(video_files)} video(s) in {input_dir}")
        results: List[VideoBreakdown] = []
        for i, video_path in enumerate(video_files, 1):
            logger.info(f"[{i}/{len(video_files)}] Processing {video_path.name}...")
            try:
                breakdown = self.process_video(video_path, force=force)
                results.append(breakdown)
            except Exception as e:
                logger.error(f"Error analyzing {video_path.name}: {e}", exc_info=True)

        logger.info(f"Completed analysis. {len(results)}/{len(video_files)} breakdowns ready in {self.output_dir}")
        return results


def detect_final_splash_start(breakdown: VideoBreakdown) -> Optional[float]:
    """Detect the start timestamp of the final outro/splash screen (title logo, release date, wishlist)."""
    dur = breakdown.metadata.duration_seconds
    if dur <= 5.0:
        return None

    # 1. Check explicit Gemini field
    if breakdown.final_splash_start_time is not None:
        val = float(breakdown.final_splash_start_time)
        if 0.5 * dur <= val < dur:
            return round(val, 2)

    # 2. Semantic & keyword analysis on scenes in the last 30% of the trailer
    SPLASH_KEYWORDS = {
        "title card", "logo", "wishlist", "release date", "steam", "available now",
        "coming soon", "pre-order", "outro", "credits", "splash screen", "store page",
        "playstation", "xbox", "nintendo", "switch", "epic games", "copyright"
    }

    tail_threshold = max(0.0, dur - max(25.0, dur * 0.30))
    outro_candidates = []

    for scene in breakdown.scenes:
        if scene.start_time < tail_threshold:
            continue

        desc = (scene.visual_summary or "").lower()
        role = (scene.recommended_role or "").lower()

        is_splash = (
            role in ("outro", "branding", "title_card", "splash", "logo")
            or any(kw in desc for kw in SPLASH_KEYWORDS)
        )

        if is_splash:
            outro_candidates.append(scene.start_time)

    if outro_candidates:
        return round(min(outro_candidates), 2)

    return None


def strip_trailer_final_splash(breakdown: VideoBreakdown) -> tuple[VideoBreakdown, Optional[float]]:
    """Strip out the final splash/outro card from the breakdown so the sequencer never uses it."""
    splash_start = detect_final_splash_start(breakdown)
    if splash_start is None or splash_start >= breakdown.metadata.duration_seconds - 0.5:
        return breakdown, None

    bd_dict = breakdown.model_dump()
    bd_dict["metadata"]["duration_seconds"] = splash_start

    clean_scenes = []
    for sc in bd_dict.get("scenes", []):
        s_in = sc.get("start_time", 0.0)
        s_out = sc.get("end_time", 0.0)
        if s_in >= splash_start:
            continue
        if s_out > splash_start:
            sc["end_time"] = splash_start
            sc["duration"] = round(splash_start - s_in, 2)
        clean_scenes.append(sc)

    bd_dict["scenes"] = clean_scenes

    clean_seconds = [
        sec for sec in bd_dict.get("seconds_breakdown", [])
        if sec.get("second", 0) < int(splash_start)
    ]
    bd_dict["seconds_breakdown"] = clean_seconds

    valid_scene_ids = {sc["scene_id"] for sc in clean_scenes}
    bd_dict["top_hook_scenes"] = [
        sid for sid in bd_dict.get("top_hook_scenes", []) if sid in valid_scene_ids
    ]

    clean_breakdown = VideoBreakdown.model_validate(bd_dict)
    logger.info(
        f"Stripped final splash for '{breakdown.source_file}': "
        f"original duration {breakdown.metadata.duration_seconds:.1f}s truncated to {splash_start:.1f}s "
        f"({len(breakdown.scenes)} -> {len(clean_breakdown.scenes)} scenes)"
    )
    return clean_breakdown, splash_start


def main() -> None:
    parser = argparse.ArgumentParser(description="Program 1: Shot & Second-by-Second Video Breakdown Analyzer")
    parser.add_argument("--input-dir", "-i", type=str, default="GamesFootage/Where Did I Park", help="Directory containing source footage")
    parser.add_argument("--output-dir", "-o", type=str, default="data/breakdowns", help="Directory where breakdown JSONs will be saved")
    parser.add_argument("--force", "-f", action="store_true", help="Force re-analysis even if breakdown JSON already exists")
    parser.add_argument("--model", "-m", type=str, default=None, help="Gemini model identifier (e.g. gemini-2.5-flash)")
    args = parser.parse_args()

    config = AppConfig.from_env()
    if args.model:
        config.gemini_model = args.model

    input_path = Path(args.input_dir)
    if not input_path.exists():
        logger.error(f"Input directory does not exist: {input_path}")
        sys.exit(1)

    output_path = Path(args.output_dir)
    analyzer = VideoAnalyzer(config=config, output_dir=output_path)
    analyzer.process_directory(input_path, force=args.force)


if __name__ == "__main__":
    main()
