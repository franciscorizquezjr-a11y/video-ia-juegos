"""
promo_v3_engine.py — Core Engine for Promo V3 Pipeline (Step-by-Step Modular Architecture)
Designed for Jupyter Notebook-style execution, inspection, and parameter tuning.
"""

from __future__ import annotations

import api_usage as _api_usage

import json
import logging
import math
import os
import random
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from project_paths import output_url
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("promo_v3_engine")

BASE_DIR = Path(__file__).parent.resolve()

FFMPEG_CMD = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_CMD = shutil.which("ffprobe") or "ffprobe"


def probe_video(video_path: str | Path) -> Dict[str, Any]:
    """Probe video file using ffprobe to obtain exact metadata."""
    video_path = Path(video_path).resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cmd = [
        FFPROBE_CMD,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,duration,codec_name,nb_frames:format=duration,size,bit_rate",
        "-of", "json",
        str(video_path),
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
    except Exception as e:
        logger.warning(f"ffprobe failed for {video_path}: {e}")
        data = {}

    streams = data.get("streams", [])
    fmt = data.get("format", {})

    width = 0
    height = 0
    fps = 30.0
    codec = "unknown"
    duration = 0.0

    if streams:
        v_stream = streams[0]
        width = int(v_stream.get("width", 0))
        height = int(v_stream.get("height", 0))
        codec = v_stream.get("codec_name", "unknown")

        r_fps = v_stream.get("r_frame_rate", "30/1")
        if "/" in r_fps:
            num, den = r_fps.split("/")
            fps = float(num) / max(float(den), 1.0)
        else:
            fps = float(r_fps)

        if "duration" in v_stream and v_stream["duration"]:
            try:
                duration = float(v_stream["duration"])
            except ValueError:
                pass

    if duration <= 0.0 and "duration" in fmt and fmt["duration"]:
        try:
            duration = float(fmt["duration"])
        except ValueError:
            duration = 0.0

    size_bytes = int(fmt.get("size", 0)) or (video_path.stat().st_size if video_path.exists() else 0)

    return {
        "path": str(video_path),
        "filename": video_path.name,
        "width": width,
        "height": height,
        "aspect_ratio": round(width / max(height, 1), 3),
        "is_vertical": height > width,
        "fps": round(fps, 2),
        "duration": round(duration, 3),
        "codec": codec,
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 2),
    }


get_video_metadata = probe_video


def sample_footage_chunks_if_needed(
    videos_meta: List[Dict[str, Any]],
    chunk_duration_sec: float = 60.0,
    max_total_sec: float = 300.0,
    num_chunks_target: int = 5,
    seed: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], bool, float]:
    """
    If total duration of all input videos is > max_total_sec (5 min / 300s):
    Partition input videos into candidate contiguous 1-minute (60s) chunks.
    Randomly select num_chunks_target (5) distinct chunks (totaling 300s = 5 min)
    and return them sorted chronologically.

    Returns:
      (selected_chunks, was_sampled, total_original_duration)
    """
    total_dur = sum(float(m.get("duration", 0.0)) for m in videos_meta)
    if total_dur <= max_total_sec + 0.5:
        return ([], False, total_dur)

    rng = random.Random(seed) if seed is not None else random.Random()
    candidate_chunks: List[Dict[str, Any]] = []

    for meta in videos_meta:
        v_idx = meta.get("video_index", 1)
        v_path = meta.get("path", "")
        v_name = meta.get("filename", Path(v_path).name if v_path else "video")
        v_dur = float(meta.get("duration", 0.0))

        if v_dur <= 0.5:
            continue

        start_t = 0.0
        while start_t + chunk_duration_sec <= v_dur + 0.1:
            end_t = start_t + chunk_duration_sec
            candidate_chunks.append({
                "video_index": v_idx,
                "video_path": str(v_path),
                "video_name": str(v_name),
                "start_sec": round(start_t, 3),
                "end_sec": round(end_t, 3),
                "duration": round(end_t - start_t, 3),
            })
            start_t += chunk_duration_sec

        if v_dur >= chunk_duration_sec and (v_dur - start_t) >= 15.0:
            tail_start = round(v_dur - chunk_duration_sec, 3)
            if not any(abs(c["start_sec"] - tail_start) < 5.0 for c in candidate_chunks if c["video_index"] == v_idx):
                candidate_chunks.append({
                    "video_index": v_idx,
                    "video_path": str(v_path),
                    "video_name": str(v_name),
                    "start_sec": tail_start,
                    "end_sec": round(v_dur, 3),
                    "duration": round(chunk_duration_sec, 3),
                })
        elif v_dur < chunk_duration_sec and v_dur >= 5.0:
            candidate_chunks.append({
                "video_index": v_idx,
                "video_path": str(v_path),
                "video_name": str(v_name),
                "start_sec": 0.0,
                "end_sec": round(v_dur, 3),
                "duration": round(v_dur, 3),
            })

    if not candidate_chunks:
        return ([], False, total_dur)

    if len(candidate_chunks) > num_chunks_target:
        selected = rng.sample(candidate_chunks, num_chunks_target)
    else:
        selected = candidate_chunks

    selected = sorted(selected, key=lambda c: (c["video_index"], c["start_sec"]))
    return (selected, True, total_dur)


def create_analysis_proxy(
    video_path: str | Path,
    output_dir: str | Path,
    target_height: int = 720,
    target_fps: int = 12,
    force_rebuild: bool = False,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Dict[str, Any]:
    """
    Generate an ultralight 720p reduced-framerate proxy specifically for visual analysis.
    Preserves original aspect ratio.
    Target framerate is typically 10-15 FPS.
    Optimized for extremely fast decoding and PySceneDetect processing.
    """
    src_path = Path(video_path).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"Source video not found: {src_path}")

    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Deterministic proxy filename based on stem, height, fps, and modification timestamp
    src_stat = src_path.stat()
    file_signature = f"{src_path.stem}_{int(src_stat.st_mtime)}_{target_height}p_{target_fps}fps"
    proxy_filename = f"proxy_{file_signature}.mp4"
    proxy_path = out_dir / proxy_filename

    # If proxy already exists and not forced, reuse cached proxy!
    if proxy_path.exists() and proxy_path.stat().st_size > 1024 and not force_rebuild:
        logger.info(f"Reusing existing analysis proxy: {proxy_path.name}")
        if progress_callback:
            progress_callback(100.0, f"Proxy 720p recuperado de caché: {proxy_path.name}")
        proxy_meta = probe_video(proxy_path)
        return {
            "proxy_path": str(proxy_path),
            "proxy_filename": proxy_path.name,
            "cached": True,
            "target_height": target_height,
            "target_fps": target_fps,
            **proxy_meta,
        }

    if progress_callback:
        progress_callback(10.0, f"Generando proxy temporal 720p @ {target_fps} FPS para análisis...")

    t0 = time.time()

    # Scale filter preserving aspect ratio with even dimensions:
    # If landscape: height=720, width=-2
    # If portrait: width=720, height=-2
    # Expression handles both automatically:
    vf_scale = f"scale=if(gte(iw\\,ih)\\,-2\\,{target_height}):if(gte(iw\\,ih)\\,{target_height}\\,-2),fps={target_fps}"

    cmd = [
        FFMPEG_CMD,
        "-y",
        "-i", str(src_path),
        "-vf", vf_scale,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-an",  # Strip audio for maximum proxy speed and compactness
        "-movflags", "+faststart",
        str(proxy_path),
    ]

    logger.info(f"Generating proxy with ffmpeg: {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error(f"FFmpeg proxy generation failed: {res.stderr}")
        raise RuntimeError(f"Failed to generate analysis proxy: {res.stderr}")

    dt = time.time() - t0
    logger.info(f"Proxy generated in {dt:.2f}s: {proxy_path.name}")

    if progress_callback:
        progress_callback(100.0, f"Proxy temporal generado con éxito en {dt:.2f}s")

    proxy_meta = probe_video(proxy_path)
    return {
        "proxy_path": str(proxy_path),
        "proxy_filename": proxy_path.name,
        "cached": False,
        "target_height": target_height,
        "target_fps": target_fps,
        "generation_time_sec": round(dt, 2),
        **proxy_meta,
    }


def detect_scenes_adaptive(
    proxy_path: str | Path,
    original_duration: float,
    adaptive_threshold: float = 1.5,
    min_scene_len_sec: float = 0.6,
    window_width: int = 8,
    detect_fades: bool = True,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Dict[str, Any]:
    """
    Run PySceneDetect with AdaptiveDetector and ThresholdDetector on the proxy video.
    Adaptive thresholding helps prevent false cuts caused by fast gameplay camera panning,
    while ThresholdDetector permanently identifies and slices fades to and from black.
    Timestamps are mapped directly to seconds referenced to original footage.
    """
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import AdaptiveDetector, ThresholdDetector
    except ImportError as e:
        raise ImportError(f"PySceneDetect is not installed: {e}")

    proxy_path = Path(proxy_path).resolve()
    if not proxy_path.exists():
        raise FileNotFoundError(f"Proxy file not found: {proxy_path}")

    if progress_callback:
        progress_callback(10.0, "Abriendo proxy de análisis e inicializando PySceneDetect...")

    t0 = time.time()

    video = open_video(str(proxy_path))
    scene_manager = SceneManager()

    # Calculate min_scene_len in seconds string for PySceneDetect
    min_len_str = f"{min_scene_len_sec:.2f}s"

    adaptive_det = AdaptiveDetector(
        adaptive_threshold=adaptive_threshold,
        min_scene_len=min_len_str,
        window_width=window_width,
    )
    scene_manager.add_detector(adaptive_det)

    # Always detect cuts and slow fades into/out of black (ThresholdDetector permanently active)
    threshold_det = ThresholdDetector(
        threshold=12.0,
        min_scene_len=min_len_str,
    )
    scene_manager.add_detector(threshold_det)

    if progress_callback:
        progress_callback(30.0, f"Analizando cambios de plano y fundidos a negro (AdaptiveThreshold={adaptive_threshold}, MinLength={min_scene_len_sec}s)...")

    # Run detection
    scene_manager.detect_scenes(video)
    scene_list = scene_manager.get_scene_list()

    dt = time.time() - t0

    if progress_callback:
        progress_callback(90.0, f"Detección completada: {len(scene_list)} cortes identificados en {dt:.2f}s")

    # Build structured list of shots referenced to the original video timebase
    shots: List[Dict[str, Any]] = []

    if not scene_list:
        # If no cuts were detected, treat the entire video as 1 shot
        shots.append({
            "shot_id": 1,
            "start": 0.0,
            "end": round(original_duration, 2),
            "duration": round(original_duration, 2),
            "start_timecode": "00:00:00.00",
            "end_timecode": format_timecode(original_duration),
        })
    else:
        for idx, (scene_start, scene_end) in enumerate(scene_list):
            s_sec = float(scene_start.seconds)
            e_sec = float(scene_end.seconds)

            # Ensure first shot starts strictly at 0.0 if close
            if idx == 0 and s_sec < 0.2:
                s_sec = 0.0

            # Ensure continuous boundary from previous shot
            if idx > 0:
                s_sec = shots[idx - 1]["end"]

            dur = max(round(e_sec - s_sec, 2), 0.05)

            shots.append({
                "shot_id": idx + 1,
                "start": round(s_sec, 2),
                "end": round(e_sec, 2),
                "duration": dur,
                "start_timecode": format_timecode(s_sec),
                "end_timecode": format_timecode(e_sec),
            })

        # Check tail: if last shot ends before original duration significantly, extend or add tail shot
        if shots:
            last_shot = shots[-1]
            if original_duration > last_shot["end"] + 0.3:
                tail_dur = round(original_duration - last_shot["end"], 2)
                shots.append({
                    "shot_id": len(shots) + 1,
                    "start": last_shot["end"],
                    "end": round(original_duration, 2),
                    "duration": tail_dur,
                    "start_timecode": format_timecode(last_shot["end"]),
                    "end_timecode": format_timecode(original_duration),
                })
            else:
                # Clamp last shot to original duration
                last_shot["end"] = round(original_duration, 2)
                last_shot["duration"] = round(last_shot["end"] - last_shot["start"], 2)
                last_shot["end_timecode"] = format_timecode(last_shot["end"])

    # Compute summary statistics
    durations = [s["duration"] for s in shots]
    stats = {
        "total_shots": len(shots),
        "total_duration": round(original_duration, 2),
        "avg_duration": round(sum(durations) / max(len(durations), 1), 2),
        "min_duration": round(min(durations), 2) if durations else 0.0,
        "max_duration": round(max(durations), 2) if durations else 0.0,
        "detection_time_sec": round(dt, 2),
    }

    detector_params = {
        "detector": "AdaptiveDetector + ThresholdDetector (Filtro fundidos siempre activo)",
        "adaptive_threshold": adaptive_threshold,
        "min_scene_len_sec": min_scene_len_sec,
        "window_width": window_width,
        "detect_fades": True,
    }

    return {
        "shots": shots,
        "stats": stats,
        "detector_params": detector_params,
    }


def is_clip_black(clip_path: str | Path, max_samples: int = 5, black_threshold: float = 12.0) -> bool:
    """
    Check if an extracted clip is predominantly a black screen / fade-to-black interstitial.
    Samples frames across duration; if all sampled frames have mean intensity < black_threshold,
    the clip is considered a black screen and can be discarded.
    """
    clip_p = Path(clip_path).resolve()
    if not clip_p.exists() or clip_p.stat().st_size < 1024:
        return False
    try:
        import cv2
        cap = cv2.VideoCapture(str(clip_p))
        if not cap.isOpened():
            return False
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            return False

        sample_count = min(max_samples, total_frames)
        sample_indices = [
            int(total_frames * (i + 1) / (sample_count + 1))
            for i in range(sample_count)
        ]

        for idx in sample_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            if frame.mean() >= black_threshold:
                cap.release()
                return False

        cap.release()
        return True
    except Exception as e:
        logger.warning(f"Error checking if clip is black: {e}")
        return False


def compute_frame_phash(gray_frame: Any) -> int:
    """
    Compute 64-bit DCT perceptual hash (pHash) for a single grayscale video frame.
    Resizes to 32x32, performs 2D DCT, extracts top-left 8x8 low frequency coefficients,
    and thresholds against median.
    """
    try:
        import cv2
        import numpy as np
        resized = cv2.resize(gray_frame, (32, 32), interpolation=cv2.INTER_AREA)
        dct = cv2.dct(np.float32(resized))
        dct_low = dct[:8, :8]
        med = np.median(dct_low)
        diff = dct_low > med
        return int("".join("1" if b else "0" for b in diff.flatten()), 2)
    except Exception as e:
        logger.debug(f"Error computing frame phash: {e}")
        return 0


def extract_shot_fingerprint(
    video_path: str | Path,
    n_samples: int = 7,
    min_variance: float = 8.0,
    min_mean: float = 12.0,
) -> List[int]:
    """
    Extract a visual fingerprint consisting of 64-bit pHash integers across sampled frames.
    Ignores solid black / flat frames to prevent false matches on fade interstitials.
    """
    clip_p = Path(video_path).resolve()
    if not clip_p.exists() or clip_p.stat().st_size < 1024:
        return []
    try:
        import cv2
        cap = cv2.VideoCapture(str(clip_p))
        if not cap.isOpened():
            return []
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            return []

        step = max(1, total_frames // (n_samples + 1))
        sample_indices = [step * (i + 1) for i in range(n_samples) if step * (i + 1) < total_frames]

        hashes: List[int] = []
        for idx in sample_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # Skip flat / near-black frames
            if gray.std() < min_variance or gray.mean() < min_mean:
                continue
            h = compute_frame_phash(gray)
            if h != 0:
                hashes.append(h)

        cap.release()
        return hashes
    except Exception as e:
        logger.warning(f"Error extracting fingerprint for {clip_p.name}: {e}")
        return []


def are_shots_duplicate(
    hashes_candidate: List[int],
    hashes_reference: List[int],
    max_hamming: int = 7,
    min_match_ratio: float = 0.6,
    min_matched_frames: int = 2,
) -> Tuple[bool, float, float]:
    """
    Determine if candidate shot is a duplicate of reference shot using pHash Hamming distance.
    Candidate is considered duplicate if >= min_match_ratio (default 60%) of its informative frames
    match a frame in reference shot within max_hamming bits (default <= 7 out of 64 bits).

    Returns: (is_duplicate, match_ratio, avg_min_hamming)
    """
    if not hashes_candidate or not hashes_reference:
        return False, 0.0, 99.0

    matches = 0
    min_dists: List[int] = []
    for hc in hashes_candidate:
        min_d = min(bin(hc ^ hr).count("1") for hr in hashes_reference)
        min_dists.append(min_d)
        if min_d <= max_hamming:
            matches += 1

    ratio = matches / len(hashes_candidate)
    avg_d = sum(min_dists) / len(min_dists) if min_dists else 99.0
    req_frames = min(min_matched_frames, len(hashes_candidate))

    is_dup = (ratio >= min_match_ratio) and (matches >= req_frames) and (avg_d <= 9.0)
    return is_dup, ratio, avg_d


def detect_and_filter_duplicate_shots(
    shots: List[Dict[str, Any]],
    max_hamming: int = 7,
    min_match_ratio: float = 0.6,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> List[Dict[str, Any]]:
    """
    Detect duplicate and near-duplicate clips using deterministic perceptual hashing (pHash) without LLMs.
    Identifies redundant footage across multiple video files (e.g. repeated scenes in game trailers)
    or within the same video file.

    For any shot detected as duplicate:
      - Sets shot['rendered'] = False
      - Sets shot['is_duplicate'] = True
      - Sets shot['duplicate_of'] = original_shot_id
      - Sets shot['duplicate_of_video'] = original_shot['video_name']
      - Sets shot['duplicate_similarity'] = similarity_ratio
      - Sets shot['skip_reason'] = 'Clip duplicado (idéntico a Shot #X...)'
    """
    if not shots:
        return shots

    t0 = time.time()
    # Filter candidate shots that are currently rendered / alive
    alive_shots = [s for s in shots if s.get("rendered") and s.get("preview_path") and not s.get("is_black")]
    if len(alive_shots) <= 1:
        return shots

    # Ensure fingerprints are present (extract in parallel if missing)
    missing_fps = [s for s in alive_shots if not s.get("phash_fingerprint")]
    if missing_fps:
        logger.info(f"Extracting pHash fingerprints for {len(missing_fps)} shots...")
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_shot = {
                executor.submit(extract_shot_fingerprint, s["preview_path"]): s
                for s in missing_fps
            }
            for future in as_completed(future_to_shot):
                s = future_to_shot[future]
                try:
                    s["phash_fingerprint"] = future.result()
                except Exception as e:
                    logger.warning(f"Failed extracting fingerprint for shot #{s.get('shot_id')}: {e}")
                    s["phash_fingerprint"] = []

    # Iterative deduplication: compare candidate shots against surviving unique shots
    surviving_shots: List[Dict[str, Any]] = []
    duplicate_count = 0

    for candidate in alive_shots:
        cand_hashes = candidate.get("phash_fingerprint") or []
        if not cand_hashes:
            surviving_shots.append(candidate)
            continue

        matched_original = None
        best_ratio = 0.0
        best_avg_d = 99.0

        for ref in surviving_shots:
            ref_hashes = ref.get("phash_fingerprint") or []
            if not ref_hashes:
                continue

            is_dup, ratio, avg_d = are_shots_duplicate(
                cand_hashes,
                ref_hashes,
                max_hamming=max_hamming,
                min_match_ratio=min_match_ratio,
            )
            if is_dup:
                matched_original = ref
                best_ratio = ratio
                best_avg_d = avg_d
                break

        if matched_original is not None:
            duplicate_count += 1
            candidate["rendered"] = False
            candidate["is_duplicate"] = True
            candidate["duplicate_of"] = matched_original["shot_id"]
            candidate["duplicate_of_video"] = matched_original.get("video_name") or ""
            candidate["duplicate_similarity"] = round(float(best_ratio), 2)

            orig_video = matched_original.get("video_name")
            cand_video = candidate.get("video_name")
            video_context = f" de {orig_video}" if (orig_video and orig_video != cand_video) else ""
            candidate["skip_reason"] = f"Clip duplicado (idéntico a Shot #{matched_original['shot_id']}{video_context})"

            logger.info(
                f"👥 Shot #{candidate['shot_id']} ({cand_video or 'video'}) descartado como DUPLICADO "
                f"de Shot #{matched_original['shot_id']} ({orig_video or 'video'}) "
                f"[match={best_ratio*100:.0f}%, dist={best_avg_d:.1f}]"
            )
        else:
            surviving_shots.append(candidate)

    dt = time.time() - t0
    logger.info(
        f"Deduplication completed in {dt:.2f}s: {duplicate_count} duplicate shot(s) detected and discarded "
        f"out of {len(alive_shots)} clips ({len(surviving_shots)} unique surviving)."
    )
    if progress_callback and duplicate_count > 0:
        progress_callback(98.0, f"Detección de duplicados: {duplicate_count} clips repetidos descartados.")

    return shots


def extract_single_preview_clip(
    src_video_path: Path,
    shot: Dict[str, Any],
    out_dir: Path,
    media_url_prefix: str,
    trim_frames: int = 5,
    src_fps: float = 30.0,
) -> Dict[str, Any]:
    """
    Extract an individual preview clip for a shot using ffmpeg fast seek.
    Trims the first and last `trim_frames` (default 5) to prevent visual bleeding
    or leaks from adjacent shots caused by framerate reduction in the analysis proxy.
    Automatically identifies and skips clips that are black screen fades.
    """
    shot_id = shot["shot_id"]
    raw_start = float(shot.get("raw_start", shot["start"]))
    raw_end = float(shot.get("raw_end", shot["end"]))

    # Convert frame count to seconds based on video FPS
    trim_sec = round(trim_frames / max(src_fps, 1.0), 3) if trim_frames > 0 else 0.0

    # Apply trimming to boundaries
    trimmed_start = raw_start + trim_sec
    trimmed_end = raw_end - trim_sec

    # Safety check: ensure trimmed clip has adequate duration
    if trimmed_end > trimmed_start + 0.15:
        start_sec = round(trimmed_start, 3)
        end_sec = round(trimmed_end, 3)
        actual_trimmed_frames = trim_frames
    else:
        start_sec = raw_start
        end_sec = raw_end
        actual_trimmed_frames = 0

    duration = max(round(end_sec - start_sec, 2), 0.1)

    clip_filename = f"shot_{shot_id:03d}_{start_sec:.2f}_{end_sec:.2f}_t{actual_trimmed_frames}.mp4"
    clip_path = out_dir / clip_filename
    preview_url = f"{media_url_prefix}/{clip_filename}"

    shot_copy = dict(shot)
    shot_copy["raw_start"] = raw_start
    shot_copy["raw_end"] = raw_end
    shot_copy["start"] = start_sec
    shot_copy["end"] = end_sec
    shot_copy["duration"] = duration
    shot_copy["start_timecode"] = format_timecode(start_sec)
    shot_copy["end_timecode"] = format_timecode(end_sec)
    shot_copy["trim_frames"] = actual_trimmed_frames
    shot_copy["preview_path"] = str(clip_path)
    shot_copy["preview_url"] = preview_url

    # If clip already exists and is non-empty, check if black screen or reuse!
    if clip_path.exists() and clip_path.stat().st_size > 1024:
        if is_clip_black(clip_path):
            shot_copy["rendered"] = False
            shot_copy["is_black"] = True
            shot_copy["skip_reason"] = "Fundido a negro (descartado)"
            shot_copy["preview_url"] = None
            shot_copy["preview_path"] = None
            try:
                clip_path.unlink()
            except Exception:
                pass
            return shot_copy
        return shot_copy

    cmd = [
        FFMPEG_CMD,
        "-y",
        "-ss", str(start_sec),
        "-to", str(end_sec),
        "-i", str(src_video_path),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "24",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(clip_path),
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.warning(f"Failed to extract shot {shot_id}: {res.stderr}")
        # Try fallback without audio if audio stream failed
        cmd_fallback = [
            FFMPEG_CMD,
            "-y",
            "-ss", str(start_sec),
            "-to", str(end_sec),
            "-i", str(src_video_path),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "24",
            "-an",
            "-movflags", "+faststart",
            str(clip_path),
        ]
        subprocess.run(cmd_fallback, capture_output=True)

    # Check if newly extracted clip is a black screen fade
    if clip_path.exists() and clip_path.stat().st_size > 1024:
        if is_clip_black(clip_path):
            logger.info(f"Shot #{shot_id} descartado por fundido a negro (pantalla negra detectada).")
            shot_copy["rendered"] = False
            shot_copy["is_black"] = True
            shot_copy["skip_reason"] = "Fundido a negro (descartado)"
            shot_copy["preview_url"] = None
            shot_copy["preview_path"] = None
            try:
                clip_path.unlink()
            except Exception:
                pass
            return shot_copy

        # Precompute visual fingerprint for deduplication
        try:
            shot_copy["phash_fingerprint"] = extract_shot_fingerprint(clip_path)
        except Exception as e:
            logger.debug(f"Error computing fingerprint for shot {shot_id}: {e}")
            shot_copy["phash_fingerprint"] = []

    return shot_copy


def extract_shot_preview_clips(
    original_video_path: str | Path,
    shots: List[Dict[str, Any]],
    output_dir: str | Path,
    media_url_prefix: str,
    min_duration: float = 1.0,
    trim_frames: int = 5,
    src_fps: float = 30.0,
    max_workers: int = 4,
    enable_deduplication: bool = True,
    dedup_hamming_threshold: int = 7,
    dedup_min_match_ratio: float = 0.6,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> List[Dict[str, Any]]:
    """
    Extract lightweight preview mp4 clips for detected shots in parallel.
    Filters by min_duration: only shots with duration >= min_duration (default 1.0s)
    will be rendered.
    Trims the first and last `trim_frames` (default 5) to eliminate adjacent shot leaks.
    """
    src_path = Path(original_video_path).resolve() if original_video_path else None
    if src_path and not src_path.exists():
        has_custom_paths = any(s.get("video_path") for s in shots)
        if not has_custom_paths:
            raise FileNotFoundError(f"Original video not found: {src_path}")

    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    total = len(shots)
    if total == 0:
        return []

    # Initialize results array with copies, flagging shots under min_duration
    results: List[Dict[str, Any]] = []
    shots_to_render_indices = []

    for idx, shot in enumerate(shots):
        shot_copy = dict(shot)
        dur = shot.get("duration", 0)
        if dur < min_duration:
            shot_copy["preview_path"] = None
            shot_copy["preview_url"] = None
            shot_copy["rendered"] = False
            shot_copy["skip_reason"] = f"Duración menor a {min_duration}s"
            shot_copy["trim_frames"] = 0
        else:
            shot_copy["rendered"] = True
            shots_to_render_indices.append(idx)
        results.append(shot_copy)

    render_count = len(shots_to_render_indices)
    if render_count == 0:
        logger.info(f"All {total} shots are shorter than {min_duration}s, none rendered.")
        if progress_callback:
            progress_callback(100.0, f"Ningún plano supera los {min_duration}s de duración.")
        return results

    trim_info = f" (recorte: -{trim_frames}f inicio/fin)" if trim_frames > 0 else ""
    if progress_callback:
        progress_callback(10.0, f"Renderizando {render_count}/{total} clips de previsualizacion (>= {min_duration}s){trim_info}...")

    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(
                extract_single_preview_clip,
                Path(shots[idx].get("video_path") or src_path),
                shots[idx],
                out_dir,
                media_url_prefix,
                trim_frames,
                float(shots[idx].get("fps") or src_fps),
            ): idx
            for idx in shots_to_render_indices
        }

        completed_count = 0
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                shot_with_preview = future.result()
                shot_with_preview["rendered"] = shot_with_preview.get("rendered", True)
                results[idx] = shot_with_preview
            except Exception as e:
                logger.error(f"Error extracting preview for shot index {idx}: {e}")
                results[idx]["preview_url"] = None
                results[idx]["rendered"] = False

            completed_count += 1
            pct = 10.0 + (completed_count / max(1, render_count)) * 85.0
            if progress_callback and (completed_count % max(1, render_count // 5) == 0 or completed_count == render_count):
                progress_callback(pct, f"Generados {completed_count}/{render_count} clips de previsualizacion (>= {min_duration}s)...")

    # Run duplicate detection across all surviving rendered clips
    if enable_deduplication:
        detect_and_filter_duplicate_shots(
            results,
            max_hamming=dedup_hamming_threshold,
            min_match_ratio=dedup_min_match_ratio,
            progress_callback=progress_callback,
        )

    actual_rendered = len([s for s in results if s.get("rendered")])
    black_count = len([s for s in results if s.get("is_black")])
    dup_count = len([s for s in results if s.get("is_duplicate")])
    dt = time.time() - t0
    logger.info(
        f"Extracted {actual_rendered} shot preview clips in {dt:.2f}s "
        f"(trimmed {trim_frames} frames, {black_count} fundidos a negro descartados, "
        f"{dup_count} clips duplicados descartados, {total - render_count} < {min_duration}s)"
    )

    if progress_callback:
        msg_extra = []
        if black_count > 0:
            msg_extra.append(f"{black_count} negros")
        if dup_count > 0:
            msg_extra.append(f"{dup_count} duplicados")
        extra_str = f" ({', '.join(msg_extra)} descartados)" if msg_extra else ""
        progress_callback(100.0, f"Clips de previsualizacion (>= {min_duration}s) listos en {dt:.2f}s!{extra_str}")

    return results


def format_timecode(seconds: float) -> str:
    """Format seconds into MM:SS.SS or HH:MM:SS.SS timecode string."""
    seconds = max(0.0, float(seconds))
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:05.2f}"
    return f"{mins:02d}:{secs:05.2f}"


# =============================================================================
# PASO 2: 9:16 Vertical Conversion & Multi-Clip Batch Packaging
# =============================================================================

def convert_single_clip_to_916(
    src_clip_path: str | Path,
    out_clip_path: str | Path,
    target_width: int = 720,
    target_height: int = 1280,
    force: bool = False,
    video_format: str = "full_9_16",
) -> Path:
    """
    Convert a rendered preview clip to 9:16 vertical (720x1280):
    - 'full_9_16': standard center crop filling entire 9:16 frame.
    - 'blur_3_4': 3:4 centered gameplay foreground with blurred background filling top/bottom.
    using fast FFmpeg encoding with ultrafast preset.
    """
    src_path = Path(src_clip_path).resolve()
    out_path = Path(out_clip_path).resolve()

    if not src_path.exists():
        raise FileNotFoundError(f"Source clip not found for 9:16 conversion: {src_path}")

    if not force and out_path.exists() and out_path.stat().st_size > 1000:
        return out_path

    import shutil
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if video_format == "blur_1_1":
        # Fast 1:1 square center crop proxy without blur (blur is added only at final montage/render to save time)
        fg_w = target_width
        fg_h = target_width
        try:
            meta = get_video_metadata(src_path)
            w = int(meta.get("width", 0))
            h = int(meta.get("height", 0))
            if h > 0 and w > 0:
                aspect = w / float(h)
                # If already ~1:1 aspect ratio (0.95 to 1.05)
                if 0.95 <= aspect <= 1.05:
                    if w == fg_w and h == fg_h:
                        shutil.copyfile(src_path, out_path)
                        return out_path
                    else:
                        scale_filter = f"scale={fg_w}:{fg_h},setsar=1,format=yuv420p"
                elif h > w and aspect < 0.65:
                    scale_filter = f"scale={fg_w}:{fg_h}:force_original_aspect_ratio=increase,crop={fg_w}:{fg_h},setsar=1,format=yuv420p"
                else:
                    # Horizontal (16:9 / landscape) footage: cut 16:9 to center 1:1 square preserving full height
                    scale_filter = f"crop=ih:ih:(iw-ow)/2:0,scale={fg_w}:{fg_h},setsar=1,format=yuv420p"
            else:
                scale_filter = f"crop=ih:ih:(iw-ow)/2:0,scale={fg_w}:{fg_h},setsar=1,format=yuv420p"
        except Exception:
            scale_filter = f"crop=ih:ih:(iw-ow)/2:0,scale={fg_w}:{fg_h},setsar=1,format=yuv420p"
    elif video_format == "blur_3_4":
        # Fast 3:4 center crop proxy without blur (blur is added only at final montage/render to save time)
        fg_w = target_width
        fg_h = int(round(target_width * 4 / 3))
        try:
            meta = get_video_metadata(src_path)
            w = int(meta.get("width", 0))
            h = int(meta.get("height", 0))
            if h > 0 and w > 0:
                aspect = w / float(h)
                # If already ~3:4 aspect ratio (0.70 to 0.80)
                if 0.70 <= aspect <= 0.80:
                    if w == fg_w and h == fg_h:
                        shutil.copyfile(src_path, out_path)
                        return out_path
                    else:
                        scale_filter = f"scale={fg_w}:{fg_h},setsar=1,format=yuv420p"
                elif h > w and aspect < 0.65:
                    scale_filter = f"scale={fg_w}:{fg_h}:force_original_aspect_ratio=increase,crop={fg_w}:{fg_h},setsar=1,format=yuv420p"
                else:
                    # Horizontal (16:9 / landscape) footage: cut 16:9 to center 3:4 preserving full height
                    scale_filter = f"crop=ih*3/4:ih:(iw-ow)/2:0,scale={fg_w}:{fg_h},setsar=1,format=yuv420p"
            else:
                scale_filter = f"crop=ih*3/4:ih:(iw-ow)/2:0,scale={fg_w}:{fg_h},setsar=1,format=yuv420p"
        except Exception:
            scale_filter = f"crop=ih*3/4:ih:(iw-ow)/2:0,scale={fg_w}:{fg_h},setsar=1,format=yuv420p"
    else:
        # Check if src_path is ALREADY in 9:16 vertical format to save processing time
        try:
            meta = get_video_metadata(src_path)
            w = int(meta.get("width", 0))
            h = int(meta.get("height", 0))
            if h > 0 and w > 0:
                aspect = w / float(h)
                # If already 9:16 vertical (0.48 <= aspect <= 0.62)
                if h > w and 0.48 <= aspect <= 0.62:
                    if w == target_width and h == target_height:
                        logger.info(f"Clip {src_path.name} ya está en 9:16 vertical ({w}x{h}). Copiando directamente...")
                        shutil.copyfile(src_path, out_path)
                        return out_path
                    else:
                        scale_filter = f"scale={target_width}:{target_height},format=yuv420p"
                else:
                    scale_filter = (
                        f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
                        f"crop={target_width}:{target_height},"
                        f"format=yuv420p"
                    )
            else:
                scale_filter = f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase,crop={target_width}:{target_height},format=yuv420p"
        except Exception:
            scale_filter = f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase,crop={target_width}:{target_height},format=yuv420p"

    temp_out = out_path.parent / f"{out_path.stem}.part_{int(time.time()*1000)}.mp4"

    cmd = [
        FFMPEG_CMD,
        "-y",
        "-i", str(src_path),
        "-vf", scale_filter,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "24",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(temp_out),
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        if temp_out.exists() and temp_out.stat().st_size > 500:
            if out_path.exists():
                out_path.unlink()
            temp_out.rename(out_path)
            return out_path
        raise RuntimeError("FFmpeg 9:16 output file is empty or missing")
    except Exception as e:
        if temp_out.exists():
            try:
                temp_out.unlink()
            except Exception:
                pass
        raise RuntimeError(f"FFmpeg failed converting {src_path.name} to 9:16: {e}")


def generate_9_16_preview_clips(
    shots: List[Dict[str, Any]],
    output_dir: str | Path,
    media_url_prefix: str,
    max_workers: int = 4,
    force: bool = False,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    video_format: str = "full_9_16",
) -> List[Dict[str, Any]]:
    """
    Takes surviving rendered shots from Step 1 and converts them to 9:16 vertical
    previews in parallel using FFmpeg (full_9_16 or blur_3_4).
    """
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rendered_shots = [s for s in shots if s.get("rendered") and s.get("preview_path")]
    total_rendered = len(rendered_shots)
    if total_rendered == 0:
        logger.warning("No rendered shots available to convert to 9:16")
        return shots

    if video_format == "blur_1_1":
        suffix = "11crop"
        target_name = "1:1"
    elif video_format == "blur_3_4":
        suffix = "34crop"
        target_name = "3:4"
    else:
        suffix = "916"
        target_name = "9:16"
    logger.info(f"Converting {total_rendered} surviving shots to {target_name} ({video_format}) previews in {out_dir.name}...")
    if progress_callback:
        progress_callback(5.0, f"Iniciando conversión a {target_name} ({video_format}) de {total_rendered} clips...")

    t0 = time.time()
    results = [dict(s) for s in shots]
    shot_id_to_idx = {s["shot_id"]: idx for idx, s in enumerate(results)}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {}
        for s in rendered_shots:
            sid = s["shot_id"]
            src_file = s["preview_path"]
            out_file = out_dir / f"shot_{sid:03d}_{suffix}.mp4"
            fut = executor.submit(convert_single_clip_to_916, src_file, out_file, 720, 1280, force, video_format)
            future_to_id[fut] = sid

        done_count = 0
        for future in as_completed(future_to_id):
            sid = future_to_id[future]
            idx = shot_id_to_idx[sid]
            try:
                out_path = future.result()
                results[idx]["preview_9_16_path"] = str(out_path)
                results[idx]["preview_9_16_url"] = f"{media_url_prefix}/shot_{sid:03d}_{suffix}.mp4"
            except Exception as e:
                logger.error(f"Error converting shot #{sid} to {target_name}: {e}")
                results[idx]["preview_9_16_path"] = None
                results[idx]["preview_9_16_url"] = None

            done_count += 1
            pct = 5.0 + (done_count / total_rendered) * 40.0
            if progress_callback:
                progress_callback(pct, f"Convertidos a {target_name}: {done_count}/{total_rendered} clips...")

    dt = time.time() - t0
    logger.info(f"Converted {done_count} clips to {target_name} in {dt:.2f}s")
    if progress_callback:
        progress_callback(45.0, f"¡{done_count} clips transformados a {target_name} en {dt:.1f}s!")

    return results


def build_clip_batches(
    shots: List[Dict[str, Any]],
    target_duration: float = 60.0,
    max_clips_per_batch: int = 14,
    stratify_random: bool = True,
) -> List[Dict[str, Any]]:
    """
    Groups surviving clips into semantic batches with cumulative duration around ~60s
    (without stitching video files).

    If stratify_random is True (default), clips are sampled across different temporal
    parts of the footage (start, middle, end, late-mid, early-mid) and shuffled within
    each stratum, ensuring that Batch 1 has high diversity of gameplay mechanics, environments,
    and visual hooks from across the entire video.
    """
    rendered = [s for s in shots if s.get("rendered") and (s.get("preview_9_16_path") or s.get("preview_path"))]
    if not rendered:
        return []

    # Sort chronologically by start time first
    rendered_sorted = sorted(rendered, key=lambda s: float(s.get("start", 0.0)))
    total_dur = sum(float(s.get("duration", 0.0)) for s in rendered_sorted)

    if stratify_random and len(rendered_sorted) > 3 and total_dur > target_duration:
        # Divide timeline into temporal strata (up to 5 strata: intro, early, mid, late, end)
        num_strata = min(5, len(rendered_sorted))
        strata: List[List[Dict[str, Any]]] = [[] for _ in range(num_strata)]
        for i, s in enumerate(rendered_sorted):
            bin_idx = min(num_strata - 1, (i * num_strata) // len(rendered_sorted))
            strata[bin_idx].append(s)

        # Shuffle clips within each stratum for random variety
        for b in strata:
            random.shuffle(b)

        # Order of stratum sampling: Start, Middle, End, Early-Mid, Late-Mid
        # This guarantees Batch 1 gets moments from completely different parts of the footage
        if num_strata == 5:
            sampling_order = [0, 2, 4, 1, 3]
        elif num_strata == 4:
            sampling_order = [0, 2, 3, 1]
        elif num_strata == 3:
            sampling_order = [0, 2, 1]
        else:
            sampling_order = list(range(num_strata))

        interleaved_shots: List[Dict[str, Any]] = []
        max_stratum_len = max(len(b) for b in strata)
        for step in range(max_stratum_len):
            for s_idx in sampling_order:
                if s_idx < len(strata) and step < len(strata[s_idx]):
                    interleaved_shots.append(strata[s_idx][step])
        candidate_shots = interleaved_shots
    else:
        candidate_shots = rendered_sorted

    batches: List[Dict[str, Any]] = []
    current_batch_shots: List[Dict[str, Any]] = []
    current_dur = 0.0

    for s in candidate_shots:
        dur = float(s.get("duration", 0.0))
        # If adding this shot exceeds target_duration AND we already have substantial duration (>= 40s),
        # or we reach max_clips_per_batch, close current batch
        if current_batch_shots and (
            (current_dur + dur > target_duration and current_dur >= 40.0)
            or len(current_batch_shots) >= max_clips_per_batch
        ):
            batches.append({
                "batch_id": len(batches) + 1,
                "shots": current_batch_shots,
                "total_duration": round(current_dur, 2),
                "clip_count": len(current_batch_shots),
                "shot_ids": [shot["shot_id"] for shot in current_batch_shots],
            })
            current_batch_shots = []
            current_dur = 0.0

        current_batch_shots.append(s)
        current_dur += dur

    if current_batch_shots:
        batches.append({
            "batch_id": len(batches) + 1,
            "shots": current_batch_shots,
            "total_duration": round(current_dur, 2),
            "clip_count": len(current_batch_shots),
            "shot_ids": [shot["shot_id"] for shot in current_batch_shots],
        })

    return batches


# =============================================================================
# PASO 2: Gemini Multimodal Analysis with Automatic Quota Fallback
# =============================================================================

GEMINI_CANDIDATE_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-flash-lite-latest",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-flash-latest",
]


def _call_gemini_file_api_with_retry(
    operation: Callable[[], Any],
    description: str,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    progress: float = 50.0,
) -> Any:
    """Retry transient File API failures without restarting the whole batch."""
    import httpx
    from google.genai.errors import APIError

    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except (httpx.TransportError, APIError) as exc:
            if isinstance(exc, APIError) and exc.code not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt == max_attempts:
                raise RuntimeError(
                    f"{description}: no se pudo completar la comunicación con Gemini "
                    f"tras {max_attempts} intentos. Vuelve a ejecutar el Paso 2. "
                    "Si persiste, comprueba la conexión y el acceso a Gemini."
                ) from exc
            wait_s = 2.0 ** attempt
            message = (
                f"{description}: fallo temporal de comunicación con Gemini. "
                f"Reintento {attempt + 1}/{max_attempts} en {wait_s:.0f}s..."
            )
            logger.warning("%s (%s)", message, type(exc).__name__)
            if progress_callback:
                progress_callback(progress, message)
            time.sleep(wait_s)


def analyze_single_batch_gemini(
    client: Any,
    batch: Dict[str, Any],
    candidate_models: List[str],
    progress_callback: Optional[Callable[[float, str], None]] = None,
    base_progress: float = 50.0,
    progress_slice: float = 40.0,
    video_format: str = "full_9_16",
) -> Dict[str, Any]:
    """
    Uploads all 9:16 clips of a single batch to Gemini File API, queries with interleaved
    CLIP_ID tags in sub-chunks of at most 7 clips to minimize server load and token spikes,
    and retries across active candidate models with exponential backoff on 503 or 429 errors.
    """
    from google.genai import types

    uploaded_files: List[Any] = []
    file_map: Dict[int, Any] = {}

    try:
        # 1. Upload clips in batch to Gemini File API
        total_shots = len(batch["shots"])
        for idx, shot in enumerate(batch["shots"]):
            sid = shot["shot_id"]
            raw_path = shot.get("preview_9_16_path") or shot.get("preview_path")
            if not raw_path:
                logger.warning(f"Shot #{sid} no tiene preview_9_16_path ni preview_path. Omitiendo subida a Gemini...")
                continue
            p916 = Path(str(raw_path)).resolve()
            if not p916.exists():
                logger.warning(f"Archivo de clip no encontrado para Shot #{sid}: {p916}. Omitiendo subida...")
                continue

            logger.info(f"Subiendo a Gemini File API: Shot #{sid} ({p916.name}, batch {batch['batch_id']})...")
            p = base_progress + (idx / max(1, total_shots)) * (progress_slice * 0.35)
            if progress_callback:
                progress_callback(p, f"[Lote {batch['batch_id']}] Subiendo clip #{sid} ({shot.get('duration', 0.0):.1f}s) a Gemini...")

            f_obj = _call_gemini_file_api_with_retry(
                lambda: client.files.upload(file=str(p916)),
                f"[Lote {batch['batch_id']}] Subida del clip #{sid}",
                progress_callback, p,
            )
            # Track immediately so a failed status request still triggers cleanup.
            uploaded_files.append(f_obj)
            # Wait until active
            while True:
                f_obj = _call_gemini_file_api_with_retry(
                    lambda: client.files.get(name=f_obj.name),
                    f"[Lote {batch['batch_id']}] Consulta del clip #{sid}",
                    progress_callback, p,
                )
                if f_obj.state.name != "PROCESSING":
                    break
                time.sleep(1.0)

            if f_obj.state.name == "FAILED":
                raise RuntimeError(f"Error procesando vídeo en Gemini Cloud: {f_obj.error}")

            file_map[sid] = f_obj

        # 2. Divide batch into manageable sub-chunks (max 7 clips per prompt)
        CHUNK_SIZE = 7
        shots_list = batch["shots"]
        chunks = [shots_list[i:i + CHUNK_SIZE] for i in range(0, len(shots_list), CHUNK_SIZE)]
        total_chunks = len(chunks)

        all_evaluated_clips: List[Dict[str, Any]] = []
        models_used: List[str] = []

        gen_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.3,
        )

        if video_format == "blur_1_1":
            format_intro = (
                "CADA CLIP ES UN ARCHIVO DE VÍDEO EN PROPORCIÓN 1:1 CUADRADO (720x720).\n"
                "Este clip representa exactamente el encuadre 1:1 central del gameplay que se mostrará en el vídeo vertical final (donde la parte superior e inferior restante se rellenará con fondo borroso en el render final).\n"
                "Lo que ves en pantalla es exactamente el encuadre 1:1 del contenido jugable.\n\n"
            )
            format_eval_instruction = (
                "2. 'format_score' (entero 0 a 100): ¿Cómo de óptimo es este clip para el encuadre en proporción 1:1 cuadrada en el que se presenta? "
                "Ten en cuenta que el encuadre 1:1 conserva un 77% más de visión lateral que un recorte vertical 9:16 completo tradicional (relación cuadrada 1:1). "
                "Puntúa 75-100 si la acción principal, personaje o elementos jugables relevantes están bien encuadrados dentro de este recuadro 1:1 y se ven con claridad. "
                "Puntúa 40-70 si la acción es comprensible pero los personajes o eventos están muy pegados a los bordes laterales del recuadro 1:1. "
                "Puntúa 0-39 ÚNICAMENTE si la acción clave, personajes o menús esenciales han quedado mutilados o cortados por los laterales del encuadre 1:1.\n"
                "3. 'format_reason': Explicación breve de la puntuación de formato en 1:1 (ej: 'El combate queda centrado en 1:1 con visión cuadrada limpia', 'Los menús y acción se aprecian bien en 1:1', 'El personaje queda demasiado cortado por el lateral').\n"
            )
        elif video_format == "blur_3_4":
            format_intro = (
                "CADA CLIP ES UN ARCHIVO DE VÍDEO EN PROPORCIÓN 3:4 (720x960).\n"
                "Este clip representa exactamente el encuadre 3:4 central del gameplay que se mostrará en el vídeo vertical final (donde la parte superior e inferior restante se rellenará con fondo borroso en el render final).\n"
                "Lo que ves en pantalla es exactamente el encuadre 3:4 del contenido jugable.\n\n"
            )
            format_eval_instruction = (
                "2. 'format_score' (entero 0 a 100): ¿Cómo de óptimo es este clip para el encuadre en proporción 3:4 en el que se presenta? "
                "Ten en cuenta que el encuadre 3:4 conserva un 33% más de visión lateral que un recorte vertical 9:16 completo tradicional. "
                "Puntúa 75-100 si la acción principal, personaje o elementos jugables relevantes están bien encuadrados dentro de este recuadro 3:4 y se ven con claridad. "
                "Puntúa 40-70 si la acción es comprensible pero los personajes o eventos están muy pegados a los bordes laterales del encuadre 3:4. "
                "Puntúa 0-39 ÚNICAMENTE si la acción clave, personajes o menús esenciales han quedado mutilados o cortados por los laterales del encuadre 3:4.\n"
                "3. 'format_reason': Explicación breve de la puntuación de formato en 3:4 (ej: 'El combate queda perfectamente centrado en 3:4 con visión amplia', 'Los menús laterales se leen bien en 3:4', 'El personaje queda demasiado cortado por el lateral').\n"
            )
        else:
            format_intro = (
                "CADA CLIP ES UN ARCHIVO DE VÍDEO DISTINTO Y YA HA SIDO CONVERTIDO A FORMATO VERTICAL 9:16 COMPLETO (720x1280).\n"
                "Lo que ves en pantalla es exactamente lo que verá el espectador en su móvil.\n\n"
            )
            format_eval_instruction = (
                "2. 'format_score' (entero 0 a 100): ¿Cómo de óptimo es este clip para el formato vertical 9:16 completo en el que se está viendo? "
                "Puntúa 75-100 si la acción principal o el personaje están bien centrados en el eje vertical. "
                "Puntúa 40-70 si la acción es comprensible pero algo lateral. "
                "Puntúa 0-39 ÚNICAMENTE si elementos clave de la jugabilidad, textos críticos o menús esenciales han quedado mutilados por los laterales.\n"
                "3. 'format_reason': Explicación breve de la puntuación de formato (ej: 'El combate está centrado y claro', 'El HUD lateral se recorta pero el juego se entiende perfectamente').\n"
            )

        for c_idx, sub_shots in enumerate(chunks):
            sub_progress_base = base_progress + progress_slice * 0.35 + (c_idx / total_chunks) * (progress_slice * 0.65)
            sub_progress_slice = (progress_slice * 0.65) / total_chunks

            prompt_parts: List[Any] = [
                "Eres un analista de contenidos de videojuegos y director experto en vídeo vertical de formato corto (TikTok, Instagram Reels, YouTube Shorts).\n\n"
                "A continuación tienes una colección de clips individuales extraídos de gameplay. "
                + format_intro +
                "Tu misión es analizar CADA clip por separado y evaluar las siguientes dimensiones clave:\n"
                "1. 'description': Descripción DETALLADA y EXTENSA (2-4 frases completas, unas 40-70 palabras) de todo lo que ocurre visualmente en el plano. "
                "No te limites a una frase genérica como 'el personaje dispara'. Debes detallar con precisión: "
                "(a) La acción exacta del jugador y mecánicas ejecutadas (ej: esquivar rodando, apuntar con mira telescópica, construir un muro defensivo, activar una habilidad definitiva, craftear en el inventario); "
                "(b) La atmósfera y entorno (ej: ruinas industriales oxidadas bajo una tormenta eléctrica, laboratorio futurista con luces de neón); "
                "(c) Dinamismo y elementos visuales (ej: enemigos enfrentados, explosiones volumétricas, partículas mágicas, velocidad y físicas del movimiento); "
                "(d) Perspectiva y encuadre (ej: cámara en tercera persona pegada al hombro, vista isométrica o primera persona táctica).\n"
                + format_eval_instruction +
                "4. 'viral_score' (entero 0 a 100): Potencial de gancho visual (hook stopper). "
                "Puntúa 80-100 para momentos con impacto visual inmediato, velocidad, explosiones, caídas o situaciones cómicas/sorprendentes ideales para los primeros 3 segundos de un Short. "
                "Puntúa bajo si es un momento lento o de transición.\n"
                "5. 'descriptive_score' (entero 0 a 100): En contraposición al viral score, ¿cómo de representativo es el clip respecto a las mecánicas nucleares y el loop jugable habitual del juego? "
                "Buscamos clips que muestren lo que el jugador hará la mayor parte del tiempo (combate estándar, recolección, crafteo, exploración, puzzles), aunque no tengan explosiones virales.\n"
                "6. 'suggested_role': Rol recomendado para el montaje ('hook', 'gameplay', 'payoff', 'transition', 'discard').\n"
                "7. 'is_splash' (booleano true/false): ¿Es este clip una tarjeta de título/splash final del tráiler, logo del juego, fecha de lanzamiento ('Available Now', 'Coming Soon', etc.), wishlist en Steam/consolas o pantalla de créditos/outro final del tráiler? "
                "Devuelve true si el plano muestra predominantemente logotipos comerciales, fechas, wishlist o créditos finales sin gameplay real relevante. Devuelve false si es metraje jugable, cinemática de la historia o combate.\n\n"
                "Debes responder OBLIGATORIAMENTE con un JSON que cumpla estrictamente este formato:\n"
                "{\n"
                '  "clips": [\n'
                '    {\n'
                '      "clip_id": 1,\n'
                '      "description": "El jugador pilota un vehículo todoterreno blindado derrapando a toda velocidad por una curva cerrada al borde de un precipicio rocoso. Al salir del derrape activa un propulsor de nitro morado y embiste frontalmente a una torreta enemiga, haciéndola volar por los aires en una explosión de chispas y fuego con físicas dinámicas.",\n'
                '      "format_score": 90,\n'
                '      "format_reason": "El vehículo y la explosión se mantienen en el tercio central de la pantalla vertical.",\n'
                '      "viral_score": 88,\n'
                '      "descriptive_score": 75,\n'
                '      "suggested_role": "hook",\n'
                '      "is_splash": false\n'
                '    }\n'
                '  ]\n'
                "}\n\n"
                "=== LISTA DE CLIPS A EVALUAR EN ESTE SUBLOTE ===\n"
            ]

            for s in sub_shots:
                sid = s["shot_id"]
                prompt_parts.append(f"\n--- INICIO CLIP_ID: {sid} (Duración limpia: {s['duration']:.2f}s) ---")
                prompt_parts.append(file_map[sid])
                prompt_parts.append(f"--- FIN CLIP_ID: {sid} ---\n")

            prompt_parts.append("\nDevuelve ÚNICAMENTE el JSON estructurado con la lista 'clips' conteniendo cada clip_id.")

            # Model Query Loop with 2-pass retry and exponential backoff
            chunk_success = False
            chunk_evaluated: List[Dict[str, Any]] = []
            chunk_errors: List[str] = []

            for pass_num in range(2):
                if chunk_success:
                    break
                if pass_num > 0:
                    logger.info(f"[Lote {batch['batch_id']}] Iniciando pase 2 de reintento sobre modelos Gemini...")
                    time.sleep(3.0)

                for m_idx, model_name in enumerate(candidate_models):
                    max_attempts = 2
                    for attempt in range(max_attempts):
                        if progress_callback:
                            p = sub_progress_base + (m_idx / len(candidate_models)) * sub_progress_slice
                            suffix = f" (parte {c_idx+1}/{total_chunks})" if total_chunks > 1 else ""
                            progress_callback(p, f"[Lote {batch['batch_id']}{suffix}] Analizando clips con modelo '{model_name}'...")

                        try:
                            resp = _api_usage.usage_generate(client.models.generate_content, 
                                model=model_name,
                                contents=prompt_parts,
                                config=gen_config,
                            )
                            if resp and resp.text:
                                data = json.loads(resp.text)
                                chunk_evaluated = data.get("clips", [])
                                if model_name not in models_used:
                                    models_used.append(model_name)
                                logger.info(f"Inferencia multimodal exitosa en Lote {batch['batch_id']} (sub-lote {c_idx+1}/{total_chunks}) con modelo '{model_name}'")
                                chunk_success = True
                                break
                        except Exception as e:
                            err_str = str(e)
                            err_str_l = err_str.lower()
                            chunk_errors.append(f"{model_name}: {err_str[:120]}")

                            if "404" in err_str_l:
                                logger.warning(f"Modelo '{model_name}' no encontrado (404). Saltando a siguiente...")
                                break

                            is_quota = any(q in err_str_l for q in ["429", "quota", "resource_exhausted", "rate limit", "too many requests"])
                            is_busy = any(b in err_str_l for b in ["503", "unavailable", "500", "overloaded", "high demand", "demand"])

                            if (is_quota or is_busy) and attempt < max_attempts - 1:
                                wait_s = 2.0 * (attempt + 1)
                                logger.warning(f"Error temporal ({'cuota 429' if is_quota else 'alta demanda 503'}) en '{model_name}'. Reintentando en {wait_s:.0f}s...")
                                time.sleep(wait_s)
                            else:
                                logger.warning(f"Error en modelo '{model_name}': {err_str[:100]}. Probando siguiente modelo...")
                                time.sleep(1.0)
                                break
                    if chunk_success:
                        break

            if not chunk_success:
                raise RuntimeError(
                    f"Fallo en lote {batch['batch_id']} (sub-lote {c_idx+1}/{total_chunks}) tras probar modelos Gemini: "
                    + "; ".join(chunk_errors[-4:])
                )

            all_evaluated_clips.extend(chunk_evaluated)

        # 4. Parse & Normalize Structured JSON
        for c in all_evaluated_clips:
            c["is_splash"] = bool(c.get("is_splash", False))
            if c["is_splash"]:
                c["suggested_role"] = "discard"

        # Ensure all shots in the batch have an evaluation entry
        evaluated_ids = {c["clip_id"] for c in all_evaluated_clips if "clip_id" in c}
        for s in batch["shots"]:
            sid = s["shot_id"]
            if sid not in evaluated_ids:
                all_evaluated_clips.append({
                    "clip_id": sid,
                    "description": f"Plano de gameplay de {s['duration']:.1f}s centrado en la acción del jugador.",
                    "format_score": 75,
                    "format_reason": "Encuadre vertical viable para montaje.",
                    "viral_score": 65,
                    "descriptive_score": 70,
                    "suggested_role": "gameplay",
                    "is_splash": False,
                })

        return {
            "batch_id": batch["batch_id"],
            "model_used": ", ".join(models_used) if models_used else "gemini",
            "clips": all_evaluated_clips,
        }

    finally:
        # 5. Clean up uploaded files in Gemini File API
        for f in uploaded_files:
            try:
                client.files.delete(name=f.name)
            except Exception:
                pass


GROQ_SUFFICIENCY_MODELS = [
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]


def evaluate_batch_sufficiency(
    evaluated_shots_so_far: List[Dict[str, Any]],
    groq_api_key: Optional[str] = None,
    gemini_client: Any = None,
    gemini_candidate_models: Optional[List[str]] = None,
    target_video_duration: float = 30.0,
) -> Dict[str, Any]:
    """
    Evaluates whether the clips analyzed so far are sufficient to construct
    a high-retention vertical promo video (Short of ~30-45s) without needing to
    process remaining batches.

    Prioritizes Groq (openai/gpt-oss-120b / gpt-oss-20b / qwen3.8-27b) in JSON mode
    to preserve 100% of Gemini tokens and quotas for multimodal video processing.
    Automatically falls back to Gemini if Groq is unconfigured or fails.

    Evaluates:
    1. Strong visual hook (Viral Score >= 75 or explosive/shocking action for 0-3s).
    2. Variety of gameplay mechanics and actions (not monotonous).
    3. Total usable duration (>= 35s with format_score >= 45).
    4. Vertical framing viability.
    """
    total_clips = len(evaluated_shots_so_far)
    total_dur = sum(float(s.get("duration", 0.0)) for s in evaluated_shots_so_far)
    usable_clips = [
        s for s in evaluated_shots_so_far
        if int(s.get("format_score", 50)) >= 45
        and not s.get("is_splash")
        and s.get("suggested_role") != "discard"
    ]
    usable_dur = sum(float(s.get("duration", 0.0)) for s in usable_clips)

    # User directive: Require at least 60.0 seconds (1 minute) of usable non-discarded footage
    # before considering stopping batch analysis.
    MIN_USABLE_DUR_REQ = 60.0
    if usable_dur < MIN_USABLE_DUR_REQ:
        logger.info(
            f"Metraje útil acumulado ({usable_dur:.1f}s) es inferior al mínimo de {MIN_USABLE_DUR_REQ:.1f}s (1 min) requeridos. "
            f"Continuando con el análisis del siguiente lote en Gemini..."
        )
        return {
            "is_sufficient": False,
            "usable_duration_seconds": round(usable_dur, 1),
            "verdict_reason": (
                f"Se requiere al menos 1 minuto (60.0s) de metraje útil no descartado. "
                f"Actualmente hay {usable_dur:.1f}s útiles."
            ),
        }

    clips_summary = []
    for s in evaluated_shots_so_far:
        cid = s.get("clip_id", s.get("shot_id"))
        if s.get("is_splash"):
            clips_summary.append(
                f"- Clip #{cid} | [DESCARTADO: SPLASH / OUTRO TRAILER] | Duración: {s.get('duration', 0):.1f}s | "
                f"Descripción: \"{s.get('description', '')}\""
            )
        else:
            clips_summary.append(
                f"- Clip #{cid} | Duración: {s.get('duration', 0):.1f}s | "
                f"Viral: {s.get('viral_score', 50)}/100 | Descriptivo: {s.get('descriptive_score', 50)}/100 | "
                f"Formato 9:16: {s.get('format_score', 50)}/100 | Rol: {s.get('suggested_role', 'gameplay')}\n"
                f"  Descripción: \"{s.get('description', '')}\""
            )
    clips_text = "\n".join(clips_summary)

    prompt = (
        "Eres el Director Editorial y Supervisor de Montaje de una agencia de trailers y vídeos virales para videojuegos (TikTok, Instagram Reels, YouTube Shorts).\n\n"
        f"Tu objetivo es determinar con alto criterio profesional si la colección de clips de gameplay analizados HASTA AHORA ({total_clips} clips, {total_dur:.1f}s en total, con {usable_dur:.1f}s viables en vertical) es SUFICIENTE para montar un vídeo promocional vertical (Short de ~{int(target_video_duration)} segundos) con retención máxima y gancho viral, O SI ES NECESARIO seguir analizando más clips del metraje restante.\n\n"
        "=== CRITERIOS DE SUFICIENCIA OBLIGATORIOS ===\n"
        "1. GANCHO VISUAL (HOOK STOPPER) PARA LOS PRIMEROS 3 SEGUNDOS:\n"
        "   - Debe haber AL MENOS 1 clip con alto potencial viral (Viral Score >= 75 o momentos de impacto visual llamativo: explosión, velocidad, choque cómico, monstruo imponente o giro sorprendente) que atrape la atención al instante.\n"
        "2. DIVERSIDAD DE ACCIONES Y MECÁNICAS:\n"
        "   - Los clips no deben ser monótonos ni mostrar la misma acción repetida (ej: solo caminar o solo disparar una pistola idéntica).\n"
        "   - Debe haber variedad de situaciones (ej: movimiento/exploración + combate/tiroteo + entorno/habilidad o variedad de enemigos/armas).\n"
        "3. DURACIÓN ÚTIL ACUMULADA:\n"
        f"   - La suma de duraciones de los clips con encuadre viable (Format Score >= 45) y NO descartados debe ser de AL MENOS 60.0 segundos (1 minuto) para tener metraje de sobra y evitar quedarse sin vídeo si la locución dura más de lo esperado. Actualmente hay {usable_dur:.1f}s útiles.\n"
        "4. ENCUADRE VERTICAL 9:16:\n"
        "   - La mayoría de clips deben mantener la acción centralizada y legible.\n\n"
        f"=== CLIPS ANALIZADOS HASTA AHORA ===\n{clips_text}\n\n"
        "=== TU DECISIÓN ===\n"
        f"- Si se cumplen los criterios (hay un buen hook, variedad de acciones y al menos 60.0s útiles de metraje no descartado), marca 'is_sufficient': true.\n"
        "- Si falta un hook contundente, o los clips son muy monótonos, o no hay al menos 60.0s de metraje útil no descartado, marca 'is_sufficient': false para continuar con el siguiente lote.\n\n"
        "Devuelve OBLIGATORIAMENTE un JSON válido con esta estructura exacta:\n"
        "{\n"
        '  "is_sufficient": true,\n'
        '  "confidence": 85,\n'
        '  "hook_clip_id": 3,\n'
        '  "hook_reason": "El Clip #... es perfecto para los primeros 3 segundos porque...",\n'
        '  "variety_score": 80,\n'
        '  "actions_detected": ["Acción 1", "Acción 2", "Acción 3"],\n'
        f'  "usable_duration_seconds": {round(usable_dur, 1)},\n'
        '  "verdict_reason": "Explicación en 1-2 frases de por qué es suficiente (o qué falta)."\n'
        "}"
    )

    # 1. Primary evaluation: Groq (Preserves 100% of Gemini quota for video)
    clean_groq_key = (groq_api_key or os.environ.get("GROQ_API_KEY", "")).strip().strip('"').strip("'")
    if clean_groq_key:
        import requests
        headers = {
            "Authorization": f"Bearer {clean_groq_key}",
            "Content-Type": "application/json",
        }
        for model_name in GROQ_SUFFICIENCY_MODELS:
            payload = {
                "model": model_name,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Eres el Director Editorial y Supervisor de Montaje de una agencia de trailers y vídeos virales para videojuegos. "
                            "Responde ÚNICAMENTE en formato JSON válido."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
                "max_tokens": 800,
            }
            try:
                logger.info(f"Evaluando suficiencia de lote con Groq ({model_name})...")
                resp = _api_usage.usage_post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=20,
                )
                if resp.status_code == 200:
                    res_data = resp.json()
                    content = res_data["choices"][0]["message"]["content"]
                    data = json.loads(content)
                    logger.info(
                        f"Evaluación de suficiencia con Groq ({model_name}) exitosa: "
                        f"is_sufficient={data.get('is_sufficient')}, hook=#{data.get('hook_clip_id')}"
                    )
                    return {
                        "is_sufficient": bool(data.get("is_sufficient", False)),
                        "confidence": int(data.get("confidence", 70)),
                        "hook_clip_id": data.get("hook_clip_id"),
                        "hook_reason": data.get("hook_reason", ""),
                        "variety_score": int(data.get("variety_score", 50)),
                        "actions_detected": data.get("actions_detected", []),
                        "usable_duration_seconds": float(data.get("usable_duration_seconds", usable_dur)),
                        "verdict_reason": data.get("verdict_reason", "Evaluación completada con Groq"),
                        "model_evaluator": f"Groq ({model_name})",
                    }
                else:
                    logger.warning(f"Groq ({model_name}) HTTP {resp.status_code}: {resp.text[:150]}")
            except Exception as e:
                logger.warning(f"Error evaluando suficiencia con Groq ({model_name}): {e}")

    # 2. Fallback evaluation: Gemini
    if gemini_client and gemini_candidate_models:
        from google.genai import types
        logger.info("Recurriendo a Gemini para la evaluación de suficiencia de metraje...")
        gen_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        )
        for model_name in gemini_candidate_models:
            try:
                resp = _api_usage.usage_generate(gemini_client.models.generate_content, 
                    model=model_name,
                    contents=prompt,
                    config=gen_config,
                )
                if resp and resp.text:
                    data = json.loads(resp.text)
                    return {
                        "is_sufficient": bool(data.get("is_sufficient", False)),
                        "confidence": int(data.get("confidence", 70)),
                        "hook_clip_id": data.get("hook_clip_id"),
                        "hook_reason": data.get("hook_reason", ""),
                        "variety_score": int(data.get("variety_score", 50)),
                        "actions_detected": data.get("actions_detected", []),
                        "usable_duration_seconds": float(data.get("usable_duration_seconds", usable_dur)),
                        "verdict_reason": data.get("verdict_reason", "Evaluación completada con Gemini"),
                        "model_evaluator": f"Gemini ({model_name})",
                    }
            except Exception as e:
                logger.warning(f"Error evaluando suficiencia con Gemini '{model_name}': {e}. Probando siguiente modelo...")
                time.sleep(0.5)

    return {
        "is_sufficient": False,
        "confidence": 0,
        "hook_clip_id": None,
        "hook_reason": "",
        "variety_score": 50,
        "actions_detected": [],
        "usable_duration_seconds": usable_dur,
        "verdict_reason": "No se pudo determinar suficiencia por LLM, continuando con lotes.",
        "model_evaluator": None,
    }


# Backward-compatible alias
evaluate_batch_sufficiency_gemini = evaluate_batch_sufficiency


def analyze_clip_batches_gemini(
    batches: List[Dict[str, Any]],
    gemini_api_key: Optional[str] = None,
    groq_api_key: Optional[str] = None,
    preferred_model: Optional[str] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    enable_early_stopping: bool = True,
    target_video_duration: float = 30.0,
    ensure_916_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    strip_splash: bool = True,
    total_video_duration: float = 0.0,
    video_format: str = "full_9_16",
) -> Dict[str, Any]:
    """
    Executes multimodal semantic evaluation across batches using Gemini.
    Evaluates early-stopping sufficiency after each batch using a Judge LLM (Groq first,
    fallback to Gemini), allowing the pipeline to stop early and save processing time and quota.
    """
    from google import genai

    api_key = (gemini_api_key or os.environ.get("GEMINI_API_KEY", "")).strip().strip('"').strip("'")
    if not api_key:
        raise ValueError("GEMINI_API_KEY no está configurada en el entorno o .env")

    client = genai.Client(api_key=api_key)

    candidate_models = list(GEMINI_CANDIDATE_MODELS)
    if preferred_model and preferred_model in candidate_models:
        candidate_models.remove(preferred_model)
        candidate_models.insert(0, preferred_model)
    elif preferred_model:
        candidate_models.insert(0, preferred_model)

    total_batches = len(batches)
    batch_results: List[Dict[str, Any]] = []
    eval_map: Dict[int, Dict[str, Any]] = {}
    models_used: List[str] = []
    early_stopped = False
    sufficiency_verdict: Optional[Dict[str, Any]] = None

    # Global lookup of shot metadata across all batches
    shots_metadata_map = {
        s["shot_id"]: s
        for b in batches
        for s in b.get("shots", [])
    }

    if progress_callback:
        progress_callback(48.0, f"Iniciando evaluación con Gemini en {total_batches} lote(s) de ~60s ({video_format})...")

    for b_idx, batch in enumerate(batches):
        batch_id = batch["batch_id"]

        # 1. Ensure this batch's clips have 9:16 preview converted
        if ensure_916_callback:
            ensure_916_callback(batch)

        base_p = 50.0 + (b_idx / total_batches) * 45.0
        slice_p = 45.0 / total_batches

        logger.info(f"Iniciando análisis de Lote {batch_id}/{total_batches} ({batch['clip_count']} clips, {batch['total_duration']}s, formato: {video_format})...")
        if progress_callback:
            progress_callback(base_p, f"[Lote {batch_id}/{total_batches}] Analizando {batch['clip_count']} clips (~{batch['total_duration']}s, {video_format}) con Gemini...")

        b_res = analyze_single_batch_gemini(
            client=client,
            batch=batch,
            candidate_models=candidate_models,
            progress_callback=progress_callback,
            base_progress=base_p,
            progress_slice=slice_p * 0.85,
            video_format=video_format,
        )
        batch_results.append(b_res)

        m_used = b_res.get("model_used")
        if m_used and m_used not in models_used:
            models_used.append(m_used)

        for clip_eval in b_res.get("clips", []):
            cid = clip_eval.get("clip_id")
            if cid is not None:
                eval_map[cid] = clip_eval

        # 2. Check early stopping with Sufficiency Judge LLM (Groq first, fallback Gemini)
        if enable_early_stopping:
            # Aggregate all evaluated shots across all batches processed so far
            evaluated_shots_so_far = []
            for b in batch_results:
                for s in b.get("clips", []):
                    sid = s.get("clip_id")
                    shot_meta = shots_metadata_map.get(sid, {})
                    evaluated_shots_so_far.append({
                        **s,
                        "duration": float(shot_meta.get("duration", 4.0)),
                        "start": float(shot_meta.get("start", 0.0)),
                        "end": float(shot_meta.get("end", 0.0)),
                    })

            # If strip_splash is enabled, filter trailer splash shots before checking sufficiency
            if strip_splash:
                evaluated_shots_so_far, _ = filter_trailer_splash_shots(
                    shots=evaluated_shots_so_far,
                    total_video_duration=total_video_duration,
                    enabled=True,
                )

            if progress_callback:
                progress_callback(base_p + slice_p * 0.9, f"[Lote {batch_id}/{total_batches}] Evaluando suficiencia con Director IA (Groq)...")

            verdict = evaluate_batch_sufficiency(
                evaluated_shots_so_far=evaluated_shots_so_far,
                groq_api_key=groq_api_key,
                gemini_client=client,
                gemini_candidate_models=candidate_models,
                target_video_duration=target_video_duration,
            )
            sufficiency_verdict = verdict

            if verdict.get("is_sufficient"):
                early_stopped = True
                saved_batches = total_batches - (b_idx + 1)
                evaluator_str = verdict.get("model_evaluator", "Groq")
                logger.info(
                    f"⚡ ¡Suficiencia alcanzada en Lote {batch_id}/{total_batches} ({evaluator_str})! "
                    f"Ahorrados {saved_batches} lotes. Razón: {verdict.get('verdict_reason')}"
                )
                if progress_callback:
                    hook_info = f" (Hook: Clip #{verdict.get('hook_clip_id')})" if verdict.get("hook_clip_id") else ""
                    progress_callback(
                        95.0,
                        f"⚡ ¡Suficiencia alcanzada en Lote {batch_id} vía {evaluator_str}! "
                        f"{len(eval_map)} clips evaluados{hook_info}. "
                        f"Ahorrados {saved_batches} lotes de metraje."
                    )
                break
            else:
                logger.info(
                    f"Lote {batch_id} no considerado suficiente todavía ({verdict.get('model_evaluator')}). "
                    f"Razón: {verdict.get('verdict_reason')}. Continuando con lote siguiente..."
                )

    if progress_callback:
        progress_callback(98.0, "Consolidando evaluaciones semánticas del metraje...")

    return {
        "batch_results": batch_results,
        "eval_map": eval_map,
        "models_used": models_used,
        "total_evaluated": len(eval_map),
        "early_stopped": early_stopped,
        "batches_evaluated": len(batch_results),
        "total_batches": total_batches,
        "batches_saved": (total_batches - len(batch_results)) if early_stopped else 0,
        "sufficiency_verdict": sufficiency_verdict,
    }


# =============================================================================
# PASO 2: Trailer Outro / Splash Screen Filter
# =============================================================================

SPLASH_KEYWORDS = {
    "title card", "logo", "wishlist", "release date", "steam", "available now",
    "coming soon", "pre-order", "outro", "credits", "splash screen", "store page",
    "playstation", "xbox", "nintendo", "switch", "epic games", "copyright",
    "wishlist now", "out now", "available on", "early access", "launching",
    "launch date", "wishlist on", "add to wishlist", "coming to", "get it on"
}


def filter_trailer_splash_shots(
    shots: List[Dict[str, Any]],
    total_video_duration: float = 0.0,
    enabled: bool = True,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Detects and filters out trailer outro/splash screen cards (game title, release date,
    wishlist on Steam/consoles, store badges, publisher outro credits).

    A shot is identified as splash if:
    1. Gemini marked it with is_splash=True (visual identification of logos/cards).
    2. OR it occurs in the trailer's tail (last 30% or last 25s) AND its description or role
       matches SPLASH_KEYWORDS or outro roles.
    3. Once the trailer outro splash sequence starts near the tail, all subsequent shots
       through the end of the trailer are also treated as part of the outro credits.

    When flagged:
    - shot["is_splash"] = True
    - shot["suggested_role"] = "discard"
    - shot["skip_reason"] = "Splash promocional / Outro (descartado)"
    - Scores are depressed so it is never selected as a hook or body cut.
    """
    if not enabled or not shots:
        return shots, 0

    # Multi-video support: If shots originate from multiple distinct video files,
    # evaluate trailer splash/outro per video source individually
    distinct_vids = {s.get("video_path") for s in shots if s.get("video_path")}
    if len(distinct_vids) > 1:
        total_disc = 0
        merged_shots: List[Dict[str, Any]] = []
        for v_path in dict.fromkeys(s.get("video_path") for s in shots):
            group_shots = [s for s in shots if s.get("video_path") == v_path]
            v_dur = max([float(s.get("end", s.get("start", 0.0) + s.get("duration", 0.0))) for s in group_shots] or [60.0])
            filtered_group, disc = filter_trailer_splash_shots(group_shots, total_video_duration=v_dur, enabled=enabled)
            total_disc += disc
            merged_shots.extend(filtered_group)
        merged_shots.sort(key=lambda s: s.get("shot_id", 0))
        return merged_shots, total_disc

    dur = float(total_video_duration)
    if dur <= 5.0:
        dur = max([float(s.get("end", s.get("start", 0.0) + s.get("duration", 0.0))) for s in shots] or [60.0])

    tail_threshold = max(0.0, dur - max(25.0, dur * 0.30))

    # Phase 1: Identify splash start candidates
    splash_start_candidates = []
    for s in shots:
        s_start = float(s.get("start", 0.0))
        gemini_splash = bool(s.get("is_splash", False))

        desc = (str(s.get("description", "")) + " " + str(s.get("format_reason", ""))).lower()
        role = str(s.get("suggested_role", "")).lower()

        has_kw = any(kw in desc for kw in SPLASH_KEYWORDS)
        has_role = role in ("outro", "branding", "title_card", "splash", "logo")

        # Visual Gemini detection (especially in the second half of video)
        if gemini_splash and s_start >= (dur * 0.4):
            splash_start_candidates.append(s_start)
        # Semantic keyword / role detection in trailer tail
        elif (has_kw or has_role) and s_start >= tail_threshold:
            splash_start_candidates.append(s_start)

    splash_cut_point = min(splash_start_candidates) if splash_start_candidates else None

    discarded_count = 0
    updated_shots = []
    for s in shots:
        s_copy = dict(s)
        s_start = float(s_copy.get("start", 0.0))
        is_splash = False

        if splash_cut_point is not None and s_start >= splash_cut_point:
            is_splash = True
        elif bool(s_copy.get("is_splash", False)) and s_start >= (dur * 0.4):
            is_splash = True

        if is_splash:
            s_copy["is_splash"] = True
            s_copy["suggested_role"] = "discard"
            s_copy["skip_reason"] = "Splash promocional / Outro (descartado)"
            s_copy["viral_score"] = min(int(s_copy.get("viral_score", 10)), 15)
            s_copy["format_score"] = min(int(s_copy.get("format_score", 10)), 15)
            s_copy["descriptive_score"] = min(int(s_copy.get("descriptive_score", 10)), 15)
            discarded_count += 1
        else:
            s_copy["is_splash"] = False

        updated_shots.append(s_copy)

    if discarded_count > 0:
        logger.info(
            f"Filtro Splash Final (Promo V3): {discarded_count} plano(s) descartados "
            f"(Outro trailer detectado a partir de {splash_cut_point:.1f}s)." if splash_cut_point is not None else
            f"Filtro Splash Final (Promo V3): {discarded_count} plano(s) descartados por visión IA."
        )

    return updated_shots, discarded_count


# =============================================================================
# PASO 4: Sequence Assembly & Short Director (EDL) with Contextual Transitions
# =============================================================================

TRANSITION_SPECS = {
    "white_flash": {
        "name": "Flash Blanco",
        "badge": "⚡ Flash Blanco (0.15s)",
        "duration": 0.15,
        "description": "Micro-destello blanco de alta energía al final del hook.",
    },
    "glitch_pixelize": {
        "name": "Glitch Pixelado",
        "badge": "👾 Glitch Pixelado (0.30s)",
        "duration": 0.30,
        "description": "Distorsión cibernética para giros de acción, cyberpunk, sci-fi o caos.",
    },
    "circle_zoom": {
        "name": "Zoom Radial",
        "badge": "🎯 Zoom Radial (0.35s)",
        "duration": 0.35,
        "description": "Apertura circular centrada en un detalle o cambio de foco narrativo.",
    },
    "cut": {
        "name": "Corte Limpio",
        "badge": "Corte Limpio",
        "duration": 0.0,
        "description": "Corte directo estándar sin efecto.",
    }
}


def extract_script_segment_for_time_range(
    start_s: float,
    end_s: float,
    alignment_data: Optional[Any] = None,
    script_text: str = ""
) -> str:
    """Extract the spoken script text during the timestamp window [start_s, end_s]."""
    words = []
    if isinstance(alignment_data, dict):
        words = alignment_data.get("words") or []
    elif isinstance(alignment_data, list):
        words = alignment_data

    if words:
        matched = []
        for w in words:
            w_start = float(w.get("start", 0.0))
            w_end = float(w.get("end", 0.0))
            if w_end >= start_s - 0.15 and w_start <= end_s + 0.15:
                matched.append(str(w.get("word", "")).strip())
        if matched:
            text = " ".join([m for m in matched if m])
            text = re.sub(r'\s+([.,!?:;])', r'\1', text)
            if len(text.strip()) > 1:
                return text.strip()

    if script_text:
        cleaned_script = script_text.strip()
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n+', cleaned_script) if s.strip()]
        if not sentences:
            sentences = [cleaned_script]

        total_len = max(sum(len(s) for s in sentences), 1)
        total_dur = max(end_s, 1.0)
        char_per_sec = total_len / total_dur if total_dur > 0 else 10
        target_char_start = start_s * char_per_sec
        target_char_end = end_s * char_per_sec

        accum = 0
        matched_sentences = []
        for s in sentences:
            c_len = len(s)
            s_start = accum
            s_end = accum + c_len
            accum += c_len
            if s_end >= target_char_start - 5 and s_start <= target_char_end + 5:
                matched_sentences.append(s)

        if matched_sentences:
            return " ".join(matched_sentences)
        return sentences[0]

    return "Gameplay del juego"


def score_shot_for_segment(shot: Dict[str, Any], segment_text: str, game_name: str = "") -> float:
    """Calculate a semantic matching score between a spoken script segment and a candidate shot."""
    desc = str(shot.get("description", "")).lower()
    role = str(shot.get("suggested_role", "")).lower()
    seg = (segment_text or "").lower()

    if not seg:
        return float(shot.get("viral_score", 50)) * 0.5 + float(shot.get("descriptive_score", 50)) * 0.5

    base_score = float(shot.get("viral_score", 50)) * 0.2 + float(shot.get("descriptive_score", 50)) * 0.3

    import re
    words_in_seg = [w for w in re.findall(r'\w+', seg) if len(w) > 3]
    matches = 0
    for w in words_in_seg:
        if w in desc:
            matches += 1

    match_bonus = min(matches * 20.0, 60.0)

    domain_keywords = {
        "build": ["build", "construction", "craft", "house", "cottage", "decorat", "base", "casa", "constru", "edific", "madera", "crea", "hacer"],
        "fight": ["fight", "combat", "boss", "weapon", "sword", "shoot", "attack", "enemy", "batalla", "luch", "espada", "dispar", "jefe", "enemigo", "armas", "accion", "pelea", "enemigos"],
        "explore": ["explore", "map", "forest", "world", "landscape", "travel", "secret", "explora", "bosque", "mundo", "paisaje", "viaj", "secreto", "caminar", "recorrer", "isla", "ciudad"],
        "custom": ["customize", "character", "outfit", "skin", "menu", "inventory", "personaliz", "personaje", "ropa", "inventario", "menú", "estilo", "look"],
        "multiplayer": ["friend", "coop", "multiplayer", "team", "together", "amigo", "cooperat", "equipo", "juntos", "multijugador", "amigos"],
        "farm": ["farm", "crop", "plant", "harvest", "animal", "pet", "granja", "cultivo", "plantar", "cosecha", "mascota", "animales"],
        "relax": ["relax", "cozy", "chill", "peaceful", "tranquil", "relaj", "cozy", "paz", "tranquilo", "calma"],
    }

    domain_score = 0.0
    for domain, kw_list in domain_keywords.items():
        seg_has_domain = any(kw in seg for kw in kw_list)
        desc_has_domain = any(kw in desc for kw in kw_list)
        if seg_has_domain and desc_has_domain:
            domain_score += 50.0
        elif seg_has_domain and not desc_has_domain:
            domain_score -= 10.0

    return base_score + match_bonus + domain_score


def _get_shot_available_duration(shot: Optional[Dict[str, Any]], default: float = 3.0) -> float:
    """Returns the usable footage duration of a shot in seconds without looping."""
    if not shot or not isinstance(shot, dict):
        return default
    dur = float(shot.get("duration") or 0.0)
    if dur <= 0.0:
        st = float(shot.get("start", shot.get("start_time", shot.get("source_in", 0.0))))
        et = float(shot.get("end", shot.get("end_time", shot.get("source_out", 0.0))))
        if et > st:
            dur = et - st
    return dur if dur > 0.1 else default


def _distribute_hook_durations(total_dur: float, shots: List[Dict[str, Any]]) -> List[float]:
    """
    Distributes total_dur across shots such that:
    1. sum(result) == round(total_dur, 3)
    2. No cut exceeds min(2.8, shot_available_dur) if possible.
    3. Pacing is as balanced as possible across the cuts.
    """
    K = len(shots)
    if K <= 0:
        return []
    if K == 1:
        return [round(total_dur, 3)]

    caps = [max(0.4, min(2.8, _get_shot_available_duration(s))) for s in shots]
    tot_cap = sum(caps)
    if tot_cap < total_dur:
        scale = total_dur / tot_cap
        caps = [c * scale for c in caps]

    allocated = [0.0] * K
    remaining = total_dur
    active_indices = set(range(K))

    while active_indices and remaining > 0.001:
        share = remaining / len(active_indices)
        capped_in_this_round = False
        for idx in list(active_indices):
            if share >= caps[idx]:
                allocated[idx] = caps[idx]
                remaining -= caps[idx]
                active_indices.remove(idx)
                capped_in_this_round = True
        if not capped_in_this_round:
            for idx in active_indices:
                allocated[idx] = share
            remaining = 0.0
            break

    res = [round(a, 3) for a in allocated]
    diff = round(total_dur - sum(res), 3)
    res[-1] = round(res[-1] + diff, 3)
    return res


def _director_cut_limit(start_time: float) -> float:
    """Keep 3s pacing through second 12, then allow cuts up to 5s."""
    if start_time < 12.0 - 0.001:
        return min(3.0, 12.0 - start_time)
    return 5.0


def _plan_complete_unique_timeline(shots, raw_cuts, target, hook_end, alignment, script, game_name):
    """Find coverage first, then rank feasible choices by the script/director preference."""
    unique = {str(s["shot_id"]): s for s in shots}
    shots = list(unique.values())
    capacities = [min(5.0, _get_shot_available_duration(s)) for s in shots]
    hook_end = min(hook_end, target)
    raw_ranges = []
    raw_time = 0.0
    for c in raw_cuts:
        duration = max(0.0, float(c.get("duration", 0.0)))
        raw_ranges.append((raw_time, raw_time + duration, c))
        raw_time += duration

    def context(time, duration):
        segment = extract_script_segment_for_time_range(time, time + duration, alignment, script)
        preferred = next((c for start, end, c in raw_ranges if start <= time < end), {})
        return segment, preferred

    def budget(time):
        cap = min(_director_cut_limit(time), target - time)
        if time < hook_end - 0.001:
            cap = min(cap, hook_end - time)
        return cap

    failed = set()

    def cover_opening(time, available):
        if time >= target - 0.001 or time >= max(12.0, hook_end) - 0.001:
            return [] if sum(capacities[i] for i in available) >= target - time - 0.001 else None
        # This optimistic bound rejects only genuinely impossible coverage.
        if sum(capacities[i] for i in available) < target - time - 0.001:
            return None
        key = (round(time, 6), tuple(sorted(round(capacities[i], 6) for i in available)))
        if key in failed:
            return None
        cap = budget(time)
        segment, preferred = context(time, cap)
        choices = sorted(available, key=lambda i: (
            str(shots[i]["shot_id"]) == str(preferred.get("shot_id")),
            score_shot_for_segment(shots[i], segment, game_name),
            -max(0.0, capacities[i] - cap),
        ), reverse=True)
        tried_capacities = set()
        for i in choices:
            capacity = capacities[i]
            if capacity in tried_capacities:
                continue  # Same duration has identical coverage feasibility.
            tried_capacities.add(capacity)
            duration = min(cap, capacity)
            if duration <= 0.001:
                continue
            rest = tuple(j for j in available if j != i)
            suffix = cover_opening(round(time + duration, 6), rest)
            if suffix is not None:
                return [(i, duration)] + suffix
        failed.add(key)
        return None

    opening = cover_opening(0.0, tuple(range(len(shots))))
    if opening is None:
        raise InsufficientUniqueShotsError(
            "El metraje apto no alcanza para cubrir toda la locución sin repetir planos "
            "con los límites de 3s al inicio y 5s después. Aprueba planos descartados por Gemini."
        )
    plan = list(opening)
    used = {i for i, duration in plan}
    time = round(sum(duration for i, duration in plan), 6)
    # After the opening, each remaining shot contributes up to 5s. Taking its full
    # usable duration cannot reduce coverage, regardless of semantic ordering.
    while time < target - 0.001:
        available = [i for i in range(len(shots)) if i not in used]
        segment, preferred = context(time, budget(time))
        i = max(available, key=lambda j: (
            str(shots[j]["shot_id"]) == str(preferred.get("shot_id")),
            score_shot_for_segment(shots[j], segment, game_name),
        ))
        duration = min(capacities[i], budget(time))
        plan.append((i, duration))
        used.add(i)
        time = round(time + duration, 6)

    result = []
    time = 0.0
    for i, duration in plan:
        segment, preferred = context(time, duration)
        role = "hook" if time < hook_end - 0.001 else "payoff" if time + duration >= target - 0.001 else "gameplay"
        result.append(dict(preferred, shot_id=shots[i]["shot_id"], duration=duration,
            resolved_shot=shots[i], role=role, script_segment=segment))
        time = round(time + duration, 6)
    return result


class InsufficientUniqueShotsError(ValueError):
    """The director needs additional user-approved footage."""


def _caveman_sentence_ranges(script, target, alignment=None, hook_end=None):
    """One continuous range per spoken sentence, including pauses and audio tails."""
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n+', script) if s.strip()]
    if not sentences:
        raise ValueError("El guion Caveman está vacío.")
    words = alignment.get("words", []) if isinstance(alignment, dict) else (alignment or [])
    if isinstance(alignment, dict) and not words and alignment.get("characters"):
        from audio_aligner import parse_elevenlabs_alignment
        words = parse_elevenlabs_alignment(alignment)
    norm = lambda value: re.sub(r'[^a-z0-9]', '', str(value).lower())
    tokens = [norm(token) for sentence in sentences for token in sentence.split() if norm(token)]
    word_tokens = [norm(word.get("word", "")) for word in words]
    token_to_word = {}
    if word_tokens:
        from difflib import SequenceMatcher
        for match in SequenceMatcher(None, tokens, word_tokens, autojunk=False).get_matching_blocks():
            token_to_word.update({match.a + i: match.b + i for i in range(match.size)})
    ends, offset = [], 0
    for sentence in sentences:
        offset += sum(bool(norm(token)) for token in sentence.split())
        word_index = token_to_word.get(offset - 1)
        if word_index is not None:
            ends.append(float(words[word_index]["end"]))
        else:
            ends.append(target * offset / max(1, len(tokens)))
    # A legacy session without word timestamps can still use its saved hook cut.
    if not words and hook_end and len(ends) > 1:
        first = min(float(hook_end), target - 0.001)
        old_first = ends[0]
        ends = [first] + [first + (end - old_first) * (target - first) / max(0.001, target - old_first) for end in ends[1:]]
    ends[-1] = target
    result, start = [], 0.0
    for index, (sentence, end) in enumerate(zip(sentences, ends)):
        end = round(min(target, end), 3)
        if end <= start:
            raise ValueError("Los tiempos de la voz no permiten separar las frases Caveman. Vuelve a sintetizar la voz.")
        result.append({"sentence_id": f"sentence_{index + 1:02d}", "text": sentence,
                       "start": start, "end": end, "duration": round(end - start, 3)})
        start = end
    return result


def _caveman_visual_match_score(shot, text, game_name):
    # Body selection is semantic: viral potential must not beat a matching USP.
    score = score_shot_for_segment(shot, text, game_name)
    score -= float(shot.get("viral_score", 50)) * 0.2 + float(shot.get("descriptive_score", 50)) * 0.3
    ignored = {"the", "but", "and", "for", "new", "game", "now", "this", "you", "your", "with", "from", "then", "can", "has", "are"}
    terms = {word.rstrip('s') for word in re.findall(r'\w+', text.lower()) if len(word) >= 3 and word not in ignored}
    visual = {word.rstrip('s') for word in re.findall(r'\w+', str(shot.get("description", "")).lower())}
    return score + len(terms & visual) * 20 + float(shot.get("descriptive_score", 50)) * 0.05


def _assign_sentence_shots(ranges, shots, raw_cuts, game_name):
    """Maximum-score assignment of distinct, sufficiently long shots (Hungarian)."""
    n, m = len(ranges), len(shots)
    if n > m:
        return None
    costs = []
    for index, sentence in enumerate(ranges):
        preferred = next((cut for cut in raw_cuts
                          if cut.get("sentence_id") == sentence["sentence_id"]
                          or str(cut.get("script_segment", "")).strip() == sentence["text"]), {})
        costs.append([
            -(_caveman_visual_match_score(shot, sentence["text"], game_name)
              + (200 if str(shot["shot_id"]) == str(preferred.get("shot_id")) else 0))
            if _get_shot_available_duration(shot) >= sentence["duration"] - 0.001 else 1e9
            for shot in shots
        ])
    u, v, p, way = [0.0] * (n + 1), [0.0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minimum, used = [float("inf")] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], float("inf"), 0
            for j in range(1, m + 1):
                if not used[j]:
                    current = costs[i0 - 1][j - 1] - u[i0] - v[j]
                    if current < minimum[j]:
                        minimum[j], way[j] = current, j0
                    if minimum[j] < delta:
                        delta, j1 = minimum[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minimum[j] -= delta
            j0 = j1
            if not p[j0]:
                break
        while j0:
            j1 = way[j0]
            p[j0], j0 = p[j1], j1
    assignment = [None] * n
    for j in range(1, m + 1):
        if p[j]:
            if costs[p[j] - 1][j - 1] >= 1e9:
                return None
            assignment[p[j] - 1] = shots[j - 1]
    return assignment


def _cover_caveman_with_short_clips(shots, raw_cuts, ranges, game_name):
    """Cover whole sentences with unique bundles, reserving footage for later phrases."""
    minimum = 0.8
    capacities = [max(0.0, _get_shot_available_duration(s)) for s in shots]
    failed = set()

    def cover(index, available):
        if index == len(ranges):
            return []
        remaining = sum(s['duration'] for s in ranges[index:])
        if len(available) < len(ranges) - index or sum(capacities[i] for i in available) < remaining - 0.001:
            return None
        key = (index, tuple(sorted(round(capacities[i], 6) for i in available)))
        if key in failed:
            return None
        sentence = ranges[index]
        duration = sentence['duration']
        preferred = {str(c.get('shot_id')) for c in raw_cuts
                     if c.get('sentence_id') == sentence['sentence_id']
                     or str(c.get('script_segment', '')).strip() == sentence['text']}
        ordered = sorted(available, key=lambda i: (
            float(shots[i].get('viral_score', 50)) if index == 0 else
            _caveman_visual_match_score(shots[i], sentence['text'], game_name)
            + (200 if str(shots[i]['shot_id']) in preferred else 0),
            capacities[i]), reverse=True)

        def bundles(offset, chosen, capacity, count):
            if len(chosen) == count:
                if capacity >= duration - 0.001:
                    yield chosen
                return
            needed = count - len(chosen)
            if len(ordered) - offset < needed or capacity + sum(sorted((capacities[i] for i in ordered[offset:]), reverse=True)[:needed]) < duration - 0.001:
                return
            tried = set()
            for pos in range(offset, len(ordered)):
                i = ordered[pos]
                signature = round(capacities[i], 6)
                if signature in tried:
                    continue
                tried.add(signature)
                yield from bundles(pos + 1, chosen + [i], capacity + capacities[i], count)

        # Prefer one clip. Split only when full coverage requires it.
        max_count = min(int((duration + 1e-9) / minimum), len(available) - (len(ranges) - index - 1))
        for count in range(1, max_count + 1):
            for chosen in bundles(0, [], 0.0, count):
                suffix = cover(index + 1, tuple(i for i in available if i not in chosen))
                if suffix is None:
                    continue
                durations = [minimum] * count
                extra = duration - minimum * count
                for part, i in enumerate(chosen):
                    added = min(max(0.0, extra), capacities[i] - minimum)
                    durations[part] += added
                    extra -= added
                return [(index, i, round(d, 6)) for i, d in zip(chosen, durations)] + suffix
        failed.add(key)
        return None

    plan = cover(0, tuple(i for i, capacity in enumerate(capacities) if capacity >= minimum))
    if plan is None:
        raise InsufficientUniqueShotsError(
            "El metraje apto no alcanza para cubrir toda la locución sin repetir planos, "
            "incluso combinando clips de al menos 0.8s. Aprueba más planos o añade metraje."
        )
    return plan


def _plan_caveman_timeline(shots, raw_cuts, ranges, game_name):
    """Prefer one clip per phrase; combine short unique clips if coverage requires it."""
    shots = list({str(shot["shot_id"]): shot for shot in shots}.values())
    hook_candidates = sorted(shots, key=lambda shot: (shot.get("viral_score", 50), shot.get("format_score", 50)), reverse=True)
    assignment = None
    for hook in hook_candidates:
        if _get_shot_available_duration(hook) < ranges[0]["duration"] - 0.001:
            continue
        body = _assign_sentence_shots(ranges[1:], [shot for shot in shots if shot is not hook], raw_cuts, game_name)
        if body is not None:
            assignment = [hook] + body
            break
    if assignment is None:
        plan = _cover_caveman_with_short_clips(shots, raw_cuts, ranges, game_name)
        result = []
        for part, (index, shot_index, duration) in enumerate(plan):
            sentence, shot = ranges[index], shots[shot_index]
            ends_hook = index == 0 and (part == len(plan) - 1 or plan[part + 1][0] != 0)
            result.append({"shot_id": shot["shot_id"], "resolved_shot": shot,
                           "sentence_id": sentence["sentence_id"], "duration": duration,
                           "script_segment": sentence["text"],
                           "role": "hook" if index == 0 else "payoff" if index == len(ranges) - 1 else "gameplay",
                           "transition_out": {"type": "white_flash" if ends_hook else "cut"},
                           "director_notes": "Cobertura con planos únicos; mínimo 0.8s por clip al combinar planos cortos."})
        return result
    return [{"shot_id": shot["shot_id"], "resolved_shot": shot,
             "sentence_id": sentence["sentence_id"], "duration": sentence["duration"],
             "script_segment": sentence["text"],
             "role": "hook" if index == 0 else "payoff" if index == len(ranges) - 1 else "gameplay",
             "transition_out": {"type": "white_flash" if index == 0 else "cut"},
             "director_notes": "Gancho: mayor viral score viable / scroll stopper." if index == 0 else "Un clip por frase, elegido por correspondencia visual con la locución."}
            for index, (sentence, shot) in enumerate(zip(ranges, assignment))]


def orchestrate_promov3_step4_edl(
    step2_shots: List[Dict[str, Any]],
    script_text: str,
    target_duration: float = 30.0,
    voice_duration: Optional[float] = None,
    hook_duration: Optional[float] = None,
    game_name: str = "",
    game_genre: str = "",
    groq_api_key: Optional[str] = None,
    gemini_client: Any = None,
    gemini_candidate_models: Optional[List[str]] = None,
    allow_secondary_transition: bool = True,
    alignment_data: Optional[Any] = None,
    banned_shot_ids: Optional[List[int]] = None,
    video_format: str = "full_9_16",
    approved_shot_ids: Optional[List[int]] = None,
    script_mode: str = "normal",
) -> Dict[str, Any]:
    """
    Executes Step 4: AI Creative Director & Sequence Assembly (EDL).
    
    Orchestrates the analyzed shots from Step 2 to align with the voiceover script from Step 3:
    1. Cut #1 (Hook): Matches exact hook duration, forced transition_out: 'white_flash' (0.15s).
    2. Body Cuts: Maximum 3s before second 12, then maximum 5s with semantic voiceover match, default 'cut'.
    3. Contextual Secondary Transition (At Director's judgment):
       - If game is 'cozy' / relaxing / puzzle: STRICTLY FORBIDDEN to use 'glitch_pixelize'.
         Uses 'circle_zoom' or clean 'cut'.
       - If game is action / cyberpunk / horror / sci-fi / chaotic: uses 'glitch_pixelize' (0.30s) or 'circle_zoom' (0.35s)
         at key narrative twists or feature shifts.
       - Maximum 1 secondary transition across the entire video.
    4. Cut #N (Payoff / CTA): Resolution cut with 'cut'.
    """
    final_target_dur = round(voice_duration if (voice_duration and voice_duration > 5.0) else target_duration, 2)
    final_hook_dur = round(hook_duration if (hook_duration and hook_duration > 0.5) else 2.5, 3)
    sentence_ranges = None
    if script_mode == "caveman":
        final_target_dur = round(voice_duration or target_duration, 3)
        sentence_ranges = _caveman_sentence_ranges(script_text, final_target_dur, alignment_data, hook_duration)
        final_hook_dur = sentence_ranges[0]["end"]

    # User approvals override Gemini exclusions, but never explicit user bans.
    banned_set = {str(sid) for sid in (banned_shot_ids or [])}
    approved_set = {str(sid) for sid in (approved_shot_ids or [])}
    usable_shots = [
        s for s in step2_shots
        if str(s.get("shot_id")) not in banned_set
        and (str(s.get("shot_id")) in approved_set or (
            s.get("format_score", 50) >= 40
            and s.get("suggested_role") != "discard"
            and not s.get("is_splash")
        ))
    ]
    if not usable_shots:
        raise InsufficientUniqueShotsError(
            "No hay planos aprobados disponibles. Selecciona planos descartados para completar el vídeo."
        )

    shots_map = {s["shot_id"]: s for s in step2_shots}

    # Select the top 35 candidate shots with highest viral and format potential for prompt brevity
    top_candidates = sorted(usable_shots, key=lambda s: (str(s["shot_id"]) in approved_set, s.get("viral_score", 50), s.get("format_score", 50)), reverse=True)
    if script_mode != "caveman":
        top_candidates = top_candidates[:35]
    shots_catalog_text = []
    for s in top_candidates:
        sid = s.get("shot_id")
        desc = s.get("description", "Sin descripción")
        role = s.get("suggested_role", "gameplay")
        shots_catalog_text.append(
            f"- Shot #{sid} | Duración: {s.get('duration', 4.0):.1f}s | "
            f"Viral: {s.get('viral_score', 50)}/100 | Formato: {s.get('format_score', 50)}/100 | "
            f"Rol sugerido: {role}\n"
            f"  Visual: \"{desc}\""
        )
    catalog_str = "\n".join(shots_catalog_text)

    alignment_str = ""
    words_list = alignment_data.get("words") if isinstance(alignment_data, dict) else (alignment_data if isinstance(alignment_data, list) else [])
    if words_list:
        phrases = []
        cur_phrase = []
        p_start = None
        for w in words_list:
            if p_start is None:
                p_start = float(w.get("start", 0.0))
            w_word = str(w.get("word", "")).strip()
            cur_phrase.append(w_word)
            p_end = float(w.get("end", 0.0))
            if any(p in w_word for p in [".", "!", "?", "\n"]):
                phrase_txt = " ".join(cur_phrase)
                phrases.append(f"[{p_start:.2f}s - {p_end:.2f}s]: \"{phrase_txt}\"")
                cur_phrase = []
                p_start = None
        if cur_phrase:
            p_end = float(words_list[-1].get("end", final_target_dur))
            phrases.append(f"[{p_start or 0.0:.2f}s - {p_end:.2f}s]: \"{' '.join(cur_phrase)}\"")
        if phrases:
            alignment_str = "=== TIMESTAMPS Y TRANSCRIPCIÓN EXACTA DE ELEVENLABS ===\n" + "\n".join(phrases) + "\n\n"

    prompt = (
        "Eres el Director Creativo y Supervisor de Montaje de una agencia de trailers y vídeos virales para videojuegos (TikTok, Reels, Shorts).\n\n"
        "Tu misión es crear una Edit Decision List (EDL) seleccionando los mejores planos del metraje analizado y organizándolos "
        "en una secuencia que cubra TODA la locución sin huecos ni planos repetidos. La cobertura tiene prioridad sobre la coincidencia semántica; adapta cortes y duración para aprovechar el metraje apto.\n\n"
        "=== DATOS DEL PROYECTO ===\n"
        f"- Videojuego: '{game_name or 'Videojuego'}'\n"
        f"- Género / Estilo del juego: '{game_genre or 'No especificado'}'\n"
        f"- Guión completo:\n\"{script_text}\"\n"
        f"- Duración total del montaje: {final_target_dur:.3f} segundos\n"
        f"- Duración exacta del Hook (Frase 1 de apertura): {final_hook_dur:.3f} segundos\n\n"
        f"{alignment_str}"
        "=== CATÁLOGO DE PLANOS ANALIZADOS DISPONIBLES ===\n"
        f"{catalog_str}\n\n"
        "=== REGLAS DE MONTAJE Y TRANSICIONES OBLIGATORIAS ===\n"
        "REGLA GLOBAL: Cada shot_id solo puede aparecer UNA VEZ en TODO el vídeo, incluyendo hook, cuerpo y cierre. No reutilices planos aunque estén separados por otros cortes.\n"
        f"1. HOOK / GANCHO (PRIMERA FRASE DEL GUIÓN - DURACIÓN TOTAL EXACTA: {final_hook_dur:.3f}s):\n"
        "   - RITMO: Durante los primeros 12 segundos, cada corte dura como máximo 3 segundos. Desde el segundo 12, cada corte dura como máximo 5 segundos. Si un corte cruza el segundo 12, divídelo en ese punto.\n"
        "   - REGLA DE NO BUCLEO / DURACIÓN REAL DISPONIBLE: NUNCA uses un plano durante más tiempo del que dura en el catálogo. Está TERMINANTEMENTE PROHIBIDO buclear o repetir fotogramas de un plano para cubrir el hook.\n"
        f"   - Si la duración del Hook ({final_hook_dur:.3f}s) es <= 3.0s:\n"
        f"     * Si dispones de un plano viral cuya duración real en el catálogo sea suficiente (>= {final_hook_dur:.3f}s): Puedes usar 1 solo corte (role='hook') de {final_hook_dur:.3f}s con transition_out: 'white_flash' (0.15s).\n"
        f"     * Si los planos disponibles duran menos de {final_hook_dur:.3f}s (o ninguno dura suficiente para cubrir el hook sin bucle): DEBES usar 2 o más planos diferentes dentro del hook para no buclear metraje. Cada plano debe respetar su duración disponible y no superar 3.0s.\n"
        f"   - Si la duración del Hook ({final_hook_dur:.3f}s) es > 3.0s: DEBES dividir el hook en 2 o más cortes distintos (role='hook'), cambiando de plano/clip cada 1.5s - 2.8s (NUNCA más de 3.0s por plano ni más de lo que dura cada plano).\n"
        "   - Transiciones en los cortes del Hook:\n"
        "     * Todos los cortes internos intermedios dentro del hook llevan transition_out: 'cut' (corte limpio directo).\n"
        f"     * TRANSICIÓN DE SALIDA OBLIGATORIA DEL HOOK: El ÚLTIMO corte que compone el hook (el corte que termina exactamente en el segundo {final_hook_dur:.3f}) DEBE llevar OBLIGATORIAMENTE transition_out: 'white_flash' (Flash Blanco, duración 0.15s). La transición con flash NO tiene por qué ser la primera entre 2 clips: si el hook tiene 2 o 3 clips, los cortes previos son cortes limpios y el último termina en el segundo {final_hook_dur:.3f} con el flash blanco hacia el cuerpo.\n"
        f"   - CAMBIO DE CLIP OBLIGATORIO: Justo al terminar el hook ({final_hook_dur:.3f}s), el siguiente corte (el inicio del cuerpo del vídeo) DEBE cambiar obligatoriamente a otro clip/plano diferente al último plano del hook.\n\n"
        "2. CORTES INTERMEDIOS (CUERPO Y GAMEPLAY) - RITMO SEGÚN EL TIEMPO DEL VÍDEO:\n"
        "   - Primeros 12 segundos: máximo 3 segundos por corte y cambio a otro plano. A partir del segundo 12: máximo 5 segundos por corte y cambio a otro plano.\n"
        "   - Usa cortes de hasta 5 segundos después del segundo 12 para aprovechar el metraje sin reutilizar planos. Nunca excedas la duración real disponible del plano.\n"
        "   - Divide frases largas en distintos planos cuando superen el máximo aplicable (3s antes del segundo 12, 5s después).\n"
        "   - Sincronización semántica: Cada plano debe coincidir visualmente con lo que se está diciendo en esa parte del guión.\n"
        "   - Transición por defecto: 'cut' (corte limpio directo).\n\n"
        "3. REGLA ESTRICTA DE TRANSICIÓN SECUNDARIA (Máximo 1 en todo el vídeo):\n"
        f"   - Permitir transición secundaria: {'SÍ' if allow_secondary_transition else 'NO'}.\n"
        "   - Si está permitida, evalúa si en algún punto del guión hay un giro narrativo, revelación, cambio drástico de tema o aceleración del gameplay:\n"
        "     * REGLA DE GÉNERO COZY (ESTRICTA): Si el juego es 'Cozy', relajante, simulador pacífico, granja, puzzle o visual: "
        "ESTÁ COMPLETAMENTE PROHIBIDO usar 'glitch_pixelize'. En su lugar, si amerita transición, utiliza 'circle_zoom' (zoom radial enfocado) o mantén 'cut'.\n"
        "     * REGLA DE GÉNERO ACCIÓN / CAOS / SCI-FI: Si el juego es de acción, shooter, cyberpunk, terror, sci-fi, hacking o caótico: "
        "puedes usar 'glitch_pixelize' (0.30s) en el momento del giro o breach, o 'circle_zoom' (0.35s).\n"
        "     * Si el guión no tiene un giro claro o el ritmo es uniforme, mantén 'cut'. No metas transiciones innecesarias.\n\n"
        "4. CORTE FINAL (PAYOFF / CTA):\n"
        "   - Selecciona el mejor plano de cierre para acompañar la llamada a la acción final.\n"
        "   - transition_out: 'cut'.\n\n"
        "5. AJUSTE Y COBERTURA DE LA LOCUCIÓN:\n"
        f"   - La suma de las duraciones de los cortes debe sumar EXACTAMENTE {final_target_dur:.1f}s (+/- 0.3s).\n"
        "   - En cada corte, indica en 'script_segment' la frase o las palabras exactas del guión que suenan durante ese plano.\n\n"
        "Devuelve OBLIGATORIAMENTE un JSON válido con esta estructura exacta:\n"
        "{\n"
        '  "title": "Montaje Director Promo V3",\n'
        '  "concept_summary": "Explicación breve del arco narrativo del montaje y el ritmo elegido",\n'
        '  "game_genre_detected": "...",\n'
        '  "secondary_transition_applied": true,\n'
        '  "secondary_transition_reason": "Explicación de por qué se eligió o descartó la segunda transición",\n'
        f'  "total_duration": {final_target_dur},\n'
        '  "timeline": [\n'
        '    {\n'
        '      "order": 1,\n'
        '      "shot_id": 1,\n'
        '      "role": "hook",\n'
        f'      "duration": {min(final_hook_dur, 2.5):.2f},\n'
        '      "script_segment": "Frase de apertura del guión...",\n'
        '      "transition_out": {\n'
        f'        "type": "{"white_flash" if final_hook_dur <= 3.0 else "cut"}",\n'
        f'        "duration": {0.15 if final_hook_dur <= 3.0 else 0.0},\n'
        '        "reason": "Fin del hook e inicio de la explicación"\n'
        '      },\n'
        '      "director_notes": "Plano de alto impacto para enganchar al espectador"\n'
        '    }\n'
        '  ]\n'
        "}"
    )

    if sentence_ranges is not None:
        start = prompt.index("=== REGLAS DE MONTAJE Y TRANSICIONES OBLIGATORIAS ===")
        end = prompt.index("Devuelve OBLIGATORIAMENTE", start)
        rules = (
            "=== MONTAJE CAVEMAN: PRIORIZA UN CLIP POR FRASE, GARANTIZA COBERTURA ===\n"
            "Cada frase debe tener EXACTAMENTE un corte siempre que permita completar toda la locución. "
            "Si no puedes cubrir todos los cortes con planos de duración igual o mayor, combina dos o más "
            "clips cortos únicos para rellenarlos por completo. Cada clip usado al combinar debe durar al menos 0.8 segundos. "
            "Reserva planos para las frases posteriores; no dejes huecos ni repitas shot_id. "
            "Estos tiempos sustituyen los límites de 3s/5s y las reglas de dividir el hook.\n"
            "Gancho: elige el plano con mayor viral_score, más shocking o scroll stopper entre los que cubran toda la frase. "
            "Si debes combinar clips, empieza con el más viral viable y completa el gancho con otros clips únicos. "
            "No necesita ilustrar literalmente el texto del hook.\n"
            "Cuerpo: selecciona el plano con mayor correspondencia entre la frase completa y la acción visible, "
            "priorizando acciones, objetos y USP sobre viral_score. Evalúa TODAS las frases para reservar los mejores planos.\n"
            "Cada shot_id se usa una sola vez, sin bucles. La suma de los clips debe cubrir exactamente la frase asignada. "
            "Hook termina con white_flash; el resto usa cut en el límite entre frases. "
            "Incluye sentence_id y script_segment exactos en cada corte.\n"
            + "\n".join(f'{s["sentence_id"]} [{s["start"]:.3f}-{s["end"]:.3f}s]: {s["text"]}' for s in sentence_ranges)
            + "\n\n"
        )
        prompt = prompt[:start] + rules + prompt[end:]
        prompt = prompt.replace("La cobertura tiene prioridad sobre la coincidencia semántica; adapta cortes y duración para aprovechar el metraje apto.",
                                "Prioriza un clip por frase; si falta cobertura, combina clips de al menos 0.8s sin dejar huecos ni duplicados.")

    clean_groq_key = (groq_api_key or os.environ.get("GROQ_API_KEY", "")).strip().strip('"').strip("'")
    edl_raw_data = None
    model_evaluator = None

    # 1. Try Groq (Preserve Gemini Quotas)
    if clean_groq_key:
        import requests
        headers = {
            "Authorization": f"Bearer {clean_groq_key}",
            "Content-Type": "application/json",
        }
        for model_name in GROQ_SUFFICIENCY_MODELS:
            payload = {
                "model": model_name,
                "messages": [
                    {
                        "role": "system",
                        "content": "Eres el Director Creativo de Montaje para videojuegos virales. Responde exclusivamente en JSON válido.",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
                "max_tokens": 2048,
            }
            try:
                logger.info(f"Orquestando montaje EDL con Director IA vía Groq ({model_name})...")
                resp = _api_usage.usage_post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=25,
                )
                if resp.status_code == 200:
                    res_data = resp.json()
                    content = res_data["choices"][0]["message"]["content"]
                    edl_raw_data = json.loads(content)
                    model_evaluator = f"Groq ({model_name})"
                    logger.info(f"Orquestación EDL exitosa con Groq ({model_name}).")
                    break
                else:
                    logger.warning(f"Groq ({model_name}) HTTP {resp.status_code}: {resp.text[:150]}")
            except Exception as e:
                logger.warning(f"Error orquestando con Groq ({model_name}): {e}")

    # 2. Fallback to Gemini
    if not edl_raw_data and gemini_client and gemini_candidate_models:
        from google.genai import types
        logger.info("Recurriendo a Gemini para la orquestación del montaje EDL...")
        gen_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        )
        for model_name in gemini_candidate_models:
            try:
                resp = _api_usage.usage_generate(gemini_client.models.generate_content, 
                    model=model_name,
                    contents=prompt,
                    config=gen_config,
                )
                if resp and resp.text:
                    edl_raw_data = json.loads(resp.text)
                    model_evaluator = f"Gemini ({model_name})"
                    break
            except Exception as e:
                logger.warning(f"Error orquestando con Gemini '{model_name}': {e}. Probando siguiente...")

    # 3. Deterministic algorithmic fallback if LLMs fail
    if sentence_ranges is not None and (not edl_raw_data or not edl_raw_data.get("timeline")):
        edl_raw_data = {"timeline": [], "concept_summary": "Un clip por frase, hook viral y cuerpo sincronizado semánticamente."}
        model_evaluator = "Director Algorítmico Caveman (Fallback)"
    if sentence_ranges is None and (not edl_raw_data or not edl_raw_data.get("timeline")):
        logger.warning("Fallo en Director LLM. Generando montaje algorítmico estructurado...")
        model_evaluator = "Director Algorítmico (Fallback)"
        sorted_by_viral = sorted(usable_shots, key=lambda s: s.get("viral_score", 50), reverse=True)
        import math
        top_shot_dur = _get_shot_available_duration(sorted_by_viral[0]) if sorted_by_viral else 3.0
        single_cut_viable = (final_hook_dur <= 3.0 and top_shot_dur >= final_hook_dur - 0.15)

        hook_cand_shots = [sorted_by_viral[0]] if sorted_by_viral else []
        used_hook_shot_ids = {sorted_by_viral[0]["shot_id"]} if sorted_by_viral else set()

        if not single_cut_viable:
            needed_hook_parts = max(2, math.ceil(final_hook_dur / 2.8), math.ceil(final_hook_dur / min(2.8, max(0.8, top_shot_dur))))
            for s in sorted_by_viral:
                if len(hook_cand_shots) >= needed_hook_parts:
                    break
                if s["shot_id"] not in used_hook_shot_ids:
                    hook_cand_shots.append(s)
                    used_hook_shot_ids.add(s["shot_id"])
            while len(hook_cand_shots) < needed_hook_parts and sorted_by_viral:
                hook_cand_shots.append(sorted_by_viral[len(hook_cand_shots) % len(sorted_by_viral)])

        hook_durations = _distribute_hook_durations(final_hook_dur, hook_cand_shots)
        fallback_timeline = []
        for hp_idx, cand_hook_shot in enumerate(hook_cand_shots):
            h_dur = hook_durations[hp_idx]
            is_last_hook_part = (hp_idx == len(hook_cand_shots) - 1)
            fallback_timeline.append({
                "order": len(fallback_timeline) + 1,
                "shot_id": cand_hook_shot["shot_id"],
                "role": "hook",
                "duration": h_dur,
                "script_segment": "Gancho de apertura",
                "transition_out": {
                    "type": "white_flash" if is_last_hook_part else "cut",
                    "duration": 0.15 if is_last_hook_part else 0.0,
                    "reason": "Fin del hook e inicio de la explicación" if is_last_hook_part else "Corte dinámico (<3s) dentro del hook",
                },
                "director_notes": f"Apertura viral clip {hp_idx+1}/{len(hook_cand_shots)} (<3s)." if not is_last_hook_part else "Cierre del hook con Flash Blanco hacia el cuerpo.",
            })

        rem_shots = [s for s in usable_shots if s["shot_id"] not in used_hook_shot_ids]
        if not rem_shots:
            rem_shots = list(usable_shots)

        rem_time = final_target_dur - final_hook_dur
        is_cozy = any(w in (game_genre or "").lower() for w in ["cozy", "relax", "farm", "café", "cafe", "puzzle"])
        applied_sec = False

        cur_time = final_hook_dur
        while rem_time > 1.5 and rem_shots:
            dur = min(_director_cut_limit(cur_time), round(rem_time, 2))
            if rem_time - dur < 2.0 and rem_time > dur:
                dur = round(rem_time / 2.0, 2)

            seg_txt = extract_script_segment_for_time_range(cur_time, cur_time + dur, alignment_data, script_text)
            rem_shots.sort(key=lambda s: score_shot_for_segment(s, seg_txt, game_name), reverse=True)
            cur_shot = rem_shots.pop(0)

            t_type = "cut"
            t_reason = ""
            if not applied_sec and allow_secondary_transition and len(fallback_timeline) == 2:
                t_type = "circle_zoom" if is_cozy else "glitch_pixelize"
                t_reason = "Cambio de foco temático según género"
                applied_sec = True

            fallback_timeline.append({
                "order": len(fallback_timeline) + 1,
                "shot_id": cur_shot["shot_id"],
                "role": "payoff" if (rem_time - dur <= 0.5) else "gameplay",
                "duration": dur,
                "script_segment": seg_txt,
                "transition_out": {
                    "type": t_type,
                    "duration": 0.30 if t_type != "cut" else 0.0,
                    "reason": t_reason,
                },
                "director_notes": f"Coincidencia semántica visual con la locución: \"{seg_txt}\"",
            })
            cur_time += dur
            rem_time -= dur

        edl_raw_data = {
            "title": f"Montaje {game_name or 'Promo V3'}",
            "concept_summary": "Montaje dinámico con gancho viral inicial y progresión de mecánicas.",
            "game_genre_detected": game_genre or "General",
            "secondary_transition_applied": applied_sec,
            "secondary_transition_reason": "Transición contextual aplicada en corte intermedio." if applied_sec else "No requerida",
            "total_duration": final_target_dur,
            "timeline": fallback_timeline,
        }

    # The LLM suggests matching shots; the coverage planner owns the final durations.
    if sentence_ranges is not None:
        edl_raw_data["timeline"] = _plan_caveman_timeline(usable_shots, edl_raw_data.get("timeline", []), sentence_ranges, game_name)
    else:
        edl_raw_data["timeline"] = _plan_complete_unique_timeline(
            usable_shots, edl_raw_data.get("timeline", []), final_target_dur,
            final_hook_dur, alignment_data, script_text, game_name,
        )
    num_hook_cuts = sum(c.get("role") == "hook" for c in edl_raw_data["timeline"])

    # 5. Process and enrich timeline cuts
    accumulated_time = 0.0
    processed_timeline = []
    last_hook_cut_idx = num_hook_cuts - 1

    for idx, cut in enumerate(edl_raw_data.get("timeline", [])):
        sid = cut.get("shot_id")
        orig_shot = shots_map.get(sid) or shots_map.get(str(sid))
        if not orig_shot:
            orig_shot = usable_shots[idx % len(usable_shots)]
            sid = orig_shot["shot_id"]

        dur = float(cut.get("duration", 3.0))
        trans_out = cut.get("transition_out", {})
        t_type = trans_out.get("type", "cut")

        # Hook cuts configuration
        if idx < last_hook_cut_idx:
            t_type = "cut"
            spec = TRANSITION_SPECS["cut"]
            t_dur = 0.0
            cut["director_notes"] = f"Hook (Clip {idx+1}/{num_hook_cuts}): corte dinámico (<3s) dentro del gancho para retención."
        elif idx == last_hook_cut_idx:
            t_type = "white_flash"
            spec = TRANSITION_SPECS["white_flash"]
            t_dur = float(spec["duration"])
            cut["director_notes"] = f"Hook (Cierre de frase 1 a {final_hook_dur:.3f}s): Salida obligatoria con transición Flash Blanco hacia el cuerpo."

        orig_shot = cut["resolved_shot"]
        sid = orig_shot["shot_id"]

        # Enforce cozy rule on secondary transitions
        is_cozy_genre = any(w in (game_genre or "").lower() for w in ["cozy", "relax", "farm", "café", "cafe", "puzzle", "pacífico", "tranquilo"])
        if is_cozy_genre and t_type == "glitch_pixelize":
            logger.info("Regla estricta aplicada: Se sustituye 'glitch_pixelize' por 'circle_zoom' en juego de género cozy.")
            t_type = "circle_zoom"

        if idx > last_hook_cut_idx:
            spec = TRANSITION_SPECS.get(t_type, TRANSITION_SPECS["cut"])
            t_dur = float(trans_out.get("duration", spec["duration"]))

        start_v = round(accumulated_time, 3)
        end_v = round(accumulated_time + dur, 3)
        accumulated_time = end_v

        source_in = float(orig_shot.get("start", orig_shot.get("start_time", 0.0)))
        source_out = round(source_in + dur, 3)

        role_val = "hook" if idx <= last_hook_cut_idx else (cut.get("role") if cut.get("role") and cut.get("role") != "hook" else (orig_shot.get("suggested_role") if orig_shot.get("suggested_role") != "hook" else "gameplay"))

        if role_val == "discard":
            role_val = "gameplay"

        processed_timeline.append({
            "order": len(processed_timeline) + 1,
            "shot_id": sid,
            "role": role_val,
            "duration": round(dur, 3),
            "start_time_in_video": start_v,
            "end_time_in_video": end_v,
            "source_in": source_in,
            "source_out": source_out,
            "video_path": orig_shot.get("video_path") or orig_shot.get("source_video_path") or "",
            "video_name": orig_shot.get("video_name") or (Path(orig_shot.get("video_path", "")).name if orig_shot.get("video_path") else ""),
            "video_index": orig_shot.get("video_index", 1),
            "preview_9_16_url": orig_shot.get("preview_9_16_url") or orig_shot.get("preview_url", ""),
            "preview_9_16_path": orig_shot.get("preview_9_16_path", ""),
            "shot_description": orig_shot.get("description", ""),
            "script_segment": cut.get("script_segment", ""),
            "sentence_id": cut.get("sentence_id"),
            "director_notes": cut.get("director_notes", ""),
            "transition_out": {
                "type": t_type,
                "name": spec["name"],
                "badge": spec["badge"],
                "duration": t_dur,
                "reason": trans_out.get("reason", "Transición contextual") if t_type != "cut" else "Corte limpio",
            }
        })

    # Never save or report success for an incomplete or repeated montage.
    if abs(accumulated_time - final_target_dur) > 0.01:
        raise InsufficientUniqueShotsError("No se pudo cubrir toda la locución con planos únicos.")
    if len({str(c["shot_id"]) for c in processed_timeline}) != len(processed_timeline):
        raise ValueError("El montaje contiene planos repetidos.")

    # Post-process every cut in processed_timeline to guarantee accurate script_segment text alignment
    for cut in processed_timeline:
        st = cut.get("start_time_in_video", 0.0)
        et = cut.get("end_time_in_video", st + cut.get("duration", 2.0))
        seg = (cut.get("script_segment") or "").strip()
        if not seg or seg == "Ajuste fino de duración" or seg == "Desarrollo del juego" or seg == "Gancho de apertura":
            real_seg = extract_script_segment_for_time_range(st, et, alignment_data, script_text)
            cut["script_segment"] = real_seg
            if "Ajuste fino" in cut.get("director_notes", "") or not cut.get("director_notes") or cut.get("director_notes") == "Plano adicional para cubrir la duración exacta de la locución.":
                cut["director_notes"] = f"Planificación visual para la locución: \"{real_seg}\""

    actual_total = round(accumulated_time, 2)
    secondary_applied = any(c["transition_out"]["type"] in ["glitch_pixelize", "circle_zoom"] for c in processed_timeline[1:])

    return {
        "step": 4,
        "step_name": "sequence_assembly_edl",
        "title": edl_raw_data.get("title", f"Montaje Director {game_name or 'Promo V3'}"),
        "concept_summary": edl_raw_data.get("concept_summary", "Montaje sincronizado con gancho viral y ritmo narrativo."),
        "game_name": game_name,
        "game_genre": game_genre or edl_raw_data.get("game_genre_detected", ""),
        "script_text": script_text,
        "director_model": model_evaluator,
        "script_mode": script_mode,
        "secondary_transition_applied": secondary_applied,
        "secondary_transition_reason": edl_raw_data.get("secondary_transition_reason", ""),
        "video_format": video_format,
        "target_duration": final_target_dur,
        "total_duration": actual_total,
        "cut_count": len(processed_timeline),
        "timeline": processed_timeline,
        "sentence_1_end_s": final_hook_dur,
        "hook_duration": final_hook_dur,
    }


# =============================================================================
# PASO 5: Sequence Assembly, Transitions, Subtitles & Steam Hook Rendering
# =============================================================================

def check_media_has_audio(media_path: str | Path) -> bool:
    """Check if media file contains at least one audio stream."""
    cmd = [
        FFPROBE_CMD,
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        str(media_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return "audio" in res.stdout.strip().lower()
    except Exception:
        return False


def get_media_duration(media_path: str | Path) -> float:
    """Retrieve exact duration of media in seconds using ffprobe."""
    cmd = [
        FFPROBE_CMD,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(media_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(res.stdout.strip())
    except Exception:
        return 0.0


def resolve_promov3_video_path(
    candidate_path: str | Path | None,
    game_name: str = "",
    session_id: str = "",
) -> Optional[Path]:
    """
    Robustly resolves a video file path for Promo V3.
    If the exact path exists, returns it.
    Otherwise, intelligently searches:
    1. temp/uploads/<filename>
    2. PromosFootage/<game_name>/<filename>
    3. GamesFootage/<game_name>/<filename>
    4. Recursively across PromosFootage/ and GamesFootage/
    5. Session directory in temp/v3_cache/<session_id>/
    6. Matching proxy file in temp/v3_cache/<session_id>/ as fallback
    7. All of temp/v3_cache/
    8. Any single video file in PromosFootage/<game_name>/
    """
    if not candidate_path:
        return None

    if isinstance(candidate_path, dict):
        candidate_path = candidate_path.get("path") or candidate_path.get("video_path")
        if not candidate_path:
            return None

    p = Path(candidate_path)
    if p.exists() and p.is_file():
        return p.resolve()

    filename = p.name
    if not filename:
        return None

    # 1. Check temp/uploads
    upload_cand = BASE_DIR / "temp" / "uploads" / filename
    if upload_cand.exists() and upload_cand.is_file():
        logger.info(f"Video resuelto en temp/uploads: {upload_cand}")
        return upload_cand.resolve()

    # 2. Check PromosFootage / game_name
    promos_dir = BASE_DIR / "PromosFootage"
    games_dir = BASE_DIR / "GamesFootage"
    if game_name:
        gf_cand = promos_dir / game_name / filename
        if gf_cand.exists() and gf_cand.is_file():
            logger.info(f"Video resuelto en PromosFootage/{game_name}: {gf_cand}")
            return gf_cand.resolve()

        gf2_cand = games_dir / game_name / filename
        if gf2_cand.exists() and gf2_cand.is_file():
            logger.info(f"Video resuelto en GamesFootage/{game_name}: {gf2_cand}")
            return gf2_cand.resolve()

    # 3. Search PromosFootage recursively
    if promos_dir.exists():
        for found in promos_dir.rglob(filename):
            if found.is_file():
                logger.info(f"Video resuelto en PromosFootage: {found}")
                return found.resolve()

    # 4. Search GamesFootage recursively
    if games_dir.exists():
        for found in games_dir.rglob(filename):
            if found.is_file():
                logger.info(f"Video resuelto en GamesFootage: {found}")
                return found.resolve()

    # 5. Search session cache
    if session_id:
        sess_dir = BASE_DIR / "temp" / "v3_cache" / session_id
        if sess_dir.exists():
            direct_sess = sess_dir / filename
            if direct_sess.exists() and direct_sess.is_file():
                return direct_sess.resolve()
            # Emergency proxy fallback
            proxy_matches = list(sess_dir.glob("proxy_*.mp4"))
            if proxy_matches:
                stem = p.stem
                stem_matches = [m for m in proxy_matches if stem in m.name]
                fallback_proxy = stem_matches[0] if stem_matches else proxy_matches[0]
                logger.warning(f"Video original '{filename}' no encontrado. Usando proxy de emergencia: {fallback_proxy}")
                return fallback_proxy.resolve()

    # 6. Global search in temp/v3_cache
    v3_cache_dir = BASE_DIR / "temp" / "v3_cache"
    if v3_cache_dir.exists():
        for found in v3_cache_dir.rglob(filename):
            if found.is_file():
                logger.info(f"Video resuelto en temp/v3_cache: {found}")
                return found.resolve()

    # 7. Check if there is only 1 video in PromosFootage/game_name
    if game_name and promos_dir.exists():
        game_folder = promos_dir / game_name
        if game_folder.exists():
            mp4s = [f for f in game_folder.iterdir() if f.is_file() and f.suffix.lower() == ".mp4"]
            if len(mp4s) == 1:
                logger.info(f"Video resuelto como único vídeo en PromosFootage/{game_name}: {mp4s[0]}")
                return mp4s[0].resolve()

    return None


def _run_ffmpeg_cmd(cmd: List[str], desc: str = "Operación FFmpeg") -> subprocess.CompletedProcess:
    """Executes an FFmpeg command with comprehensive error logging and informative exception messages."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.strip() if e.stderr else (e.stdout.strip() if e.stdout else str(e))
        logger.error(f"{desc} falló (código {e.returncode}):\n{err_msg}")
        raise RuntimeError(f"{desc} falló (código {e.returncode}): {err_msg}") from e


def _extract_subclip(
    src_video: Path,
    start_time: float,
    duration: float,
    vf: str,
    out_path: Path,
    has_audio: bool,
):
    """Helper to extract and format a single video cut matching target dimensions and audio rate."""
    if not src_video.exists():
        raise FileNotFoundError(f"El vídeo fuente para el subclip no existe: {src_video}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Different colour tags on concatenated H.264 clips cause FFmpeg to reinitialise
    # the composition filters. PTS-STARTPTS then restarts at the body, dropping
    # the hook's worth of frames and truncating the last shot.
    vf += ",setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    video_options = ["-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709",
                     "-color_primaries", "bt709", "-color_trc", "bt709", "-write_tmcd", "0"]
    if has_audio:
        cmd = [
            FFMPEG_CMD, "-y",
            "-ss", f"{max(0.0, start_time):.3f}",
            "-stream_loop", "-1",
            "-i", str(src_video),
            "-t", f"{max(0.1, duration):.3f}",
            "-vf", vf,
            "-map", "0:v:0", "-map", "0:a:0",
            "-af", "aresample=async=1000,aformat=sample_rates=48000:channel_layouts=stereo",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            *video_options,
            "-movflags", "+faststart",
            str(out_path),
        ]
    else:
        cmd = [
            FFMPEG_CMD, "-y",
            "-ss", f"{max(0.0, start_time):.3f}",
            "-stream_loop", "-1",
            "-i", str(src_video),
            "-f", "lavfi", "-t", f"{max(0.1, duration):.3f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t", f"{max(0.1, duration):.3f}",
            "-vf", vf,
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            *video_options,
            "-movflags", "+faststart",
            str(out_path),
        ]
    _run_ffmpeg_cmd(cmd, f"Extracción de subclip '{out_path.name}' ({src_video.name})")


def render_promov3_final_video(
    edl: Dict[str, Any],
    source_video_path: str | Path,
    voiceover_audio_path: str | Path,
    alignment_data: Optional[Dict[str, Any]],
    output_dir: str | Path,
    game_name: str = "",
    resolution: str = "1080p",
    enable_steam_hook: bool = True,
    steam_logo_scale: float = 1.3,
    steam_logo_y: int = 540,
    steam_text_scale: float = 1.6,
    steam_text_y: int = 1160,
    captions_font_size: int = 84,
    captions_y: int = 600,
    captions_highlight_color: str = "#FFF380",
    gameplay_volume: float = 0.15,
    temp_dir: Optional[str | Path] = None,
    session_id: Optional[str] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    video_format: str = "full_9_16",
    steam_hook_mode: str = "classic",
    intro_subtitles_y: int = 340,
    intro_subtitles_size: int = 84,
    enable_game_name_reveal: bool = False,
    game_name_reveal_y: int = 800,
    game_name_reveal_scale: float = 1.0,
    custom_game_logo_path: Optional[str | Path] = None,
    channel_branding: Optional[Dict[str, Any]] = None,
    require_official_game_logo: bool = False,
) -> Dict[str, Any]:
    """
    Renders the complete Promo V3 vertical video from the Step 4 EDL:
    1. Cuts high-resolution segments from the source video at 9:16 vertical crop (or 3:4 centered with blurred bg).
    2. Applies cinematic transitions:
       - White Flash (0.15s) at end of Hook (Cut #1).
       - Circle Zoom (0.35s) or Glitch Pixelize (0.30s) at secondary turning points via xfade.
       - Clean Cut for standard pacing.
    3. Concatenates all assembled segments into a base video stream.
    4. Generates dynamic animated Steam Opening Hook overlay (0-2s) with pop-in logo & bounce text.
    5. Generates word-by-word kinetic ASS subtitles with custom color palette and vertical position.
    6. Mixes ElevenLabs voiceover audio with ducked background gameplay audio.
    7. Encodes final high-bitrate MP4 with faststart flags for social media readiness.
    """
    def update_progress(pct: float, msg: str):
        if progress_callback:
            progress_callback(pct, msg)
        logger.info(f"[{pct:.1f}%] {msg}")

    # The presentation mode also governs callers that leave the legacy flag enabled.
    enable_steam_hook = bool(enable_steam_hook and steam_hook_mode != "none")
    update_progress(5.0, "Iniciando renderizado final Promo V3...")

    if not game_name:
        game_name = (edl.get("game_name") or "").strip()
    if not game_name and source_video_path:
        pn = Path(source_video_path).parent.name
        if pn and pn.lower() not in ("promosfootage", "footage", "temp", "input", "videos", "output", "render_tmp", "clips", "test", "v3_cache"):
            game_name = pn
    if not game_name and edl.get("timeline"):
        for cut in edl.get("timeline", []):
            cp = cut.get("video_path")
            if cp:
                pn = Path(cp).parent.name
                if pn and pn.lower() not in ("promosfootage", "footage", "temp", "input", "videos", "output", "render_tmp", "clips", "test", "v3_cache"):
                    game_name = pn
                    break

    if require_official_game_logo:
        from production_logo_policy import require_game_logo
        custom_game_logo_path = require_game_logo(game_name, custom_game_logo_path)
        enable_game_name_reveal = True

    source_video = resolve_promov3_video_path(source_video_path, game_name=game_name, session_id=session_id)
    if not source_video and source_video_path:
        source_video = Path(source_video_path).resolve()
    if source_video and not source_video.exists():
        if not any(c.get("video_path") for c in edl.get("timeline", [])):
            raise FileNotFoundError(f"Video de origen no encontrado: {source_video}")

    voiceover_audio = Path(voiceover_audio_path).resolve()
    if not voiceover_audio.exists():
        raise FileNotFoundError(f"Audio de locución no encontrado: {voiceover_audio}")

    from audio_quality import require_narration
    require_narration(voiceover_audio, ffmpeg=FFMPEG_CMD)

    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resolution setup (1080p vs 4K)
    is_4k = (resolution or "").strip().upper() == "4K"
    if is_4k:
        out_w, out_h = 2160, 3840
        font_sz = int(round(captions_font_size * 2))
        intro_font_sz = int(round(intro_subtitles_size * 2))
        margin_v = int(round(captions_y * 2))
    else:
        out_w, out_h = 1080, 1920
        font_sz = int(round(captions_font_size))
        intro_font_sz = int(round(intro_subtitles_size))
        margin_v = int(round(captions_y))

    # Note: generate_steam_hook_overlay_video expects base normalized 1080p coordinates and scales internally by height/1920
    hook_logo_y = int(round(steam_logo_y)) if steam_logo_y is not None else 540
    if steam_hook_mode == "subtitles_above" and (steam_logo_y in (540, None)):
        hook_logo_y = 1420
    hook_text_y = int(round(steam_text_y)) if steam_text_y is not None else 1160

    # Duration calculations
    voice_dur = get_media_duration(voiceover_audio)
    if voice_dur <= 0.0:
        voice_dur = float(edl.get("total_duration", 30.0))

    cuts = list(edl.get("timeline", []))
    sentence_clip_mode = edl.get("script_mode") == "caveman"
    if not cuts:
        raise ValueError("No hay planos definidos en la EDL para renderizar el montaje.")

    # Extract word alignment data early for timing synchronization
    words_list = []
    if isinstance(alignment_data, dict):
        words_list = alignment_data.get("words") or []
        if not words_list and alignment_data.get("characters"):
            try:
                from audio_aligner import parse_elevenlabs_alignment
                words_list = parse_elevenlabs_alignment(alignment_data)
            except Exception as e:
                logger.warning(f"Error parsing character alignment: {e}")
    elif isinstance(alignment_data, list):
        words_list = alignment_data

    # 1. Spoken Hook Sentence End (Target Hook duration)
    target_hook_end = float(cuts[0].get("duration", 3.0)) if cuts else 3.0
    script_txt = edl.get("script_text", "") or (alignment_data.get("script_text") if isinstance(alignment_data, dict) else "") or (cuts[0].get("script_segment", "") if cuts else "")

    manual_s1_end = cuts[0].get("duration") if sentence_clip_mode else (edl.get("sentence_1_end_s") or edl.get("hook_duration"))
    if manual_s1_end and float(manual_s1_end) > 0.5:
        target_hook_end = round(float(manual_s1_end), 3)
        logger.info(f"Duración de la primera frase fijada exactamente por el usuario: {target_hook_end:.3f}s")
    else:
        try:
            from audio_aligner import get_first_sentence_end_time, get_standalone_sentence_1_duration
            if words_list:
                parsed_hook_end = get_first_sentence_end_time({"words": words_list}, script_text=script_txt)
                if parsed_hook_end > 1.0:
                    target_hook_end = round(parsed_hook_end, 3)
                    logger.info(f"Final exacto de la primera frase según alineación de voz real (voiceover.mp3): {target_hook_end:.3f}s")
            else:
                from config import AppConfig
                cfg = AppConfig.from_env()
                eleven_key = (cfg.elevenlabs_api_key or os.environ.get("ELEVENLABS_API_KEY", "")).strip()
                voice_id = (cfg.elevenlabs_voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "TX3LPaxmHKxFdv7VOQHJ")).strip()

                s1_dur = get_standalone_sentence_1_duration(
                    script_text=script_txt,
                    api_key=eleven_key,
                    voice_id=voice_id
                )
                if s1_dur > 0.5:
                    target_hook_end = round(s1_dur, 3)
                    logger.info(f"Duración de la primera frase generada aislada en ElevenLabs: {target_hook_end:.3f}s")
        except Exception as e:
            logger.warning(f"No se pudo calcular la duración de la primera frase: {e}")

    # Load analyzed shots catalog from session cache if available to obtain distinct clips for multi-cut hook
    session_shots = []
    if session_id:
        v3_cache_dir = BASE_DIR / "temp" / "v3_cache" / session_id
        for sf_name in ["step2_evaluated_shots.json", "step1_shots.json", "step2_analysis.json"]:
            cand_p = v3_cache_dir / sf_name
            if cand_p.exists():
                try:
                    with open(cand_p, "r", encoding="utf-8") as sf:
                        s_data = json.load(sf)
                        session_shots = s_data.get("shots", [])
                        if session_shots:
                            break
                except Exception:
                    pass

    # Separate incoming cuts into Hook cuts vs Body cuts
    existing_hook_cuts = []
    first_wf_idx = None
    for c_idx, c in enumerate(cuts):
        if c.get("transition_out", {}).get("type") == "white_flash":
            first_wf_idx = c_idx
            break

    if first_wf_idx is not None:
        existing_hook_cuts = [dict(c) for c in cuts[:first_wf_idx + 1]]
        existing_body_cuts = [dict(c) for c in cuts[first_wf_idx + 1:]]
    else:
        hook_by_role = []
        for c in cuts:
            if c.get("role") == "hook":
                hook_by_role.append(dict(c))
            else:
                break
        if hook_by_role:
            existing_hook_cuts = hook_by_role
            existing_body_cuts = [dict(c) for c in cuts[len(hook_by_role):]]
        else:
            existing_hook_cuts = [dict(cuts[0])]
            existing_body_cuts = [dict(c) for c in cuts[1:]]

    # Normalize Hook cuts adhering strictly to <3s retention rule, available shot footage, and exact target_hook_end
    shots_by_id = {s.get("shot_id"): s for s in session_shots if isinstance(s, dict) and s.get("shot_id") is not None}
    s0 = existing_hook_cuts[0] if existing_hook_cuts else {}
    s0_id = s0.get("shot_id")
    s0_shot = shots_by_id.get(s0_id)
    s0_avail = _get_shot_available_duration(s0_shot) if s0_shot else _get_shot_available_duration(s0, default=float(s0.get("duration", 3.0)))
    single_cut_viable = (len(existing_hook_cuts) == 1 and (sentence_clip_mode or target_hook_end <= 3.0) and s0_avail >= (target_hook_end - 0.15))
    if sentence_clip_mode and not single_cut_viable:
        raise ValueError("La EDL Caveman necesita un único clip que cubra el gancho completo. Regenera el montaje.")

    import math
    final_hook_cuts = []
    if single_cut_viable:
        sc = dict(existing_hook_cuts[0])
        sc["duration"] = round(target_hook_end, 3)
        sc["role"] = "hook"
        s_in = float(sc.get("source_in", 0.0))
        sc["source_out"] = round(s_in + target_hook_end, 3)
        sc["transition_out"] = {"type": "white_flash", "name": "Flash Blanco", "duration": 0.15}
        final_hook_cuts.append(sc)
    else:
        # Multiple cuts are REQUIRED because:
        # - target_hook_end > 3.0s (retention rule)
        # - OR s0_avail < target_hook_end - 0.15 (footage is shorter than hook, looping prohibited)
        # - OR existing_hook_cuts already has multiple cuts
        needed_hook_cuts = max(
            len(existing_hook_cuts),
            math.ceil(target_hook_end / 2.8),
            math.ceil(target_hook_end / min(2.8, max(0.8, s0_avail)))
        )
        if needed_hook_cuts < 2:
            needed_hook_cuts = 2

        used_hook_shots = {c.get("shot_id") for c in existing_hook_cuts if c.get("shot_id") is not None}
        cand_hook_cuts = list(existing_hook_cuts)

        while len(cand_hook_cuts) < needed_hook_cuts:
            cand_shot = None
            if session_shots:
                for s in session_shots:
                    if s.get("shot_id") not in used_hook_shots and (not s.get("is_splash", False)):
                        cand_shot = s
                        break
            if not cand_shot and existing_body_cuts:
                for bc in existing_body_cuts:
                    if bc.get("shot_id") not in used_hook_shots:
                        cand_shot = bc
                        break
            if cand_shot:
                used_hook_shots.add(cand_shot.get("shot_id"))
                sc = dict(existing_hook_cuts[0])
                sc["shot_id"] = cand_shot.get("shot_id")
                sc["video_path"] = cand_shot.get("video_path") or cand_shot.get("source_video_path") or sc.get("video_path")
                sc["video_name"] = cand_shot.get("video_name") or sc.get("video_name")
                s_start = float(cand_shot.get("start", cand_shot.get("start_time", cand_shot.get("source_in", 0.0))))
                sc["source_in"] = round(s_start, 3)
                sc["role"] = "hook"
                sc["duration"] = _get_shot_available_duration(cand_shot)
                cand_hook_cuts.append(sc)
            else:
                # Fallback: duplicate first cut but advance source_in
                sc = dict(existing_hook_cuts[0])
                prev_in = float(cand_hook_cuts[-1].get("source_in", 0.0))
                prev_dur = float(cand_hook_cuts[-1].get("duration", 2.0))
                sc["source_in"] = round(prev_in + prev_dur, 3)
                sc["role"] = "hook"
                cand_hook_cuts.append(sc)

        hook_durations = _distribute_hook_durations(
            target_hook_end,
            [shots_by_id.get(c.get("shot_id"), c) for c in cand_hook_cuts]
        )

        for p, c in enumerate(cand_hook_cuts):
            part_dur = hook_durations[p]
            sc = dict(c)
            sc["duration"] = part_dur
            sc["role"] = "hook"
            s_in = float(sc.get("source_in", 0.0))
            sc["source_out"] = round(s_in + part_dur, 3)
            if p < len(cand_hook_cuts) - 1:
                sc["transition_out"] = {"type": "cut", "name": "Corte Limpio", "duration": 0.0}
            else:
                sc["transition_out"] = {"type": "white_flash", "name": "Flash Blanco", "duration": 0.15}
            final_hook_cuts.append(sc)

    # Match the director pacing; do not split valid late 5s cuts back into 3s cuts.
    final_body_cuts = []
    pacing_time = sum(float(c.get("duration", 0.0)) for c in final_hook_cuts)
    for c in existing_body_cuts:
        remaining = float(c.get("duration", 3.0))
        source_in = float(c.get("source_in", 0.0))
        while remaining > 0.001:
            duration = remaining if sentence_clip_mode else min(remaining, _director_cut_limit(pacing_time))
            sc = dict(c)
            sc["duration"] = duration
            sc["source_in"] = round(source_in, 3)
            sc["source_out"] = round(source_in + duration, 3)
            remaining = round(remaining - duration, 6)
            if remaining > 0.001:
                sc["transition_out"] = {"type": "cut", "name": "Corte Limpio", "duration": 0.0}
            final_body_cuts.append(sc)
            source_in += duration
            pacing_time = round(pacing_time + duration, 6)

    cuts = final_hook_cuts + final_body_cuts
    last_hook_idx = len(final_hook_cuts) - 1
    num_hook_cuts = len(final_hook_cuts)
    hook_dur = target_hook_end

    # Temporary render workspace
    work_dir = Path(temp_dir).resolve() if temp_dir else (out_dir / f"temp_promov3_{int(time.time()*1000)}")
    work_dir.mkdir(parents=True, exist_ok=True)
    clips_dir = work_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    source_has_audio = check_media_has_audio(source_video) if (source_video and source_video.exists()) else True
    def _build_cut_vf(src_v_path: Path) -> str:
        if video_format in ("blur_3_4", "blur_1_1"):
            fg_w = out_w
            blur_rad = 28 if is_4k else 20
            blur_pwr = 8 if is_4k else 10
            if video_format == "blur_1_1":
                # 1:1 square centered: fg_w x fg_w (1080x1080 in 1080p, 2160x2160 in 4K)
                # Fit the complete source inside the square foreground.
                fg_h = out_w
                fg_crop_filter = f"scale={fg_w}:{fg_h}:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1"
            else:
                # 3:4 centered: fg_w x (fg_w * 4/3) (1080x1440 in 1080p, 2160x2880 in 4K)
                # Fit the complete source inside the 3:4 foreground.
                fg_h = int(round(out_w * 4 / 3))
                fg_crop_filter = f"scale={fg_w}:{fg_h}:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1"

            return (
                f"split=2[bg][fg];"
                f"[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},"
                f"boxblur={blur_rad}:{blur_pwr},eq=brightness=-0.10:saturation=1.1[bg_blur];"
                f"[fg]{fg_crop_filter}[fg_crop];"
                f"[bg_blur][fg_crop]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30"
            )
        else:
            return f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},setsar=1,fps=30"

    # Enforce visually distinct clip for the cut immediately following the Hook
    if not sentence_clip_mode and len(cuts) > last_hook_idx + 1:
        last_h_shot = cuts[last_hook_idx].get("shot_id")
        last_h_vp = cuts[last_hook_idx].get("video_path")
        next_b_shot = cuts[last_hook_idx + 1].get("shot_id")
        next_b_vp = cuts[last_hook_idx + 1].get("video_path")
        if (last_h_shot is not None and last_h_shot == next_b_shot) or (last_h_vp and next_b_vp and last_h_vp == next_b_vp and abs(float(cuts[last_hook_idx + 1].get("source_in", 0.0)) - float(cuts[last_hook_idx].get("source_in", 0.0))) < 1.0):
            for alt_idx in range(last_hook_idx + 2, len(cuts)):
                if cuts[alt_idx].get("video_path") != last_h_vp or cuts[alt_idx].get("shot_id") != last_h_shot:
                    logger.info(f"Intercambiando Corte #{last_hook_idx + 2} con Corte #{alt_idx + 1} para garantizar un cambio de clip visual obligatorio tras el Hook.")
                    dur1 = cuts[last_hook_idx + 1]["duration"]
                    dur_alt = cuts[alt_idx]["duration"]
                    t_out1 = cuts[last_hook_idx + 1].get("transition_out", {"type": "cut", "duration": 0.0})
                    t_out_alt = cuts[alt_idx].get("transition_out", {"type": "cut", "duration": 0.0})
                    cuts[last_hook_idx + 1], cuts[alt_idx] = dict(cuts[alt_idx]), dict(cuts[last_hook_idx + 1])
                    cuts[last_hook_idx + 1]["duration"] = dur1
                    cuts[last_hook_idx + 1]["transition_out"] = t_out1
                    cuts[alt_idx]["duration"] = dur_alt
                    cuts[alt_idx]["transition_out"] = t_out_alt
                    break

    acc = 0.0
    for c in cuts:
        c["start_time_in_video"] = round(acc, 3)
        acc += float(c.get("duration", 3.0))
        c["end_time_in_video"] = round(acc, 3)

    segment_paths: List[Path] = []
    applied_transitions: List[Dict[str, Any]] = []

    update_progress(12.0, f"Extrayendo y componiendo {len(cuts)} cortes en 9:16 ({resolution})...")

    idx = 0
    apply_white_fade_in = False

    while idx < len(cuts):
        cut = cuts[idx]
        cur_dur = float(cut.get("duration", 3.0))
        cur_in = float(cut.get("source_in", 0.0))
        trans = cut.get("transition_out", {})
        t_type = trans.get("type", "cut")

        pct = 15.0 + (idx / len(cuts)) * 45.0
        update_progress(pct, f"Procesando corte #{idx+1}/{len(cuts)}: plano #{cut.get('shot_id', idx+1)} ({cut.get('role', 'gameplay')})")

        can_xfade = (t_type in ("circle_zoom", "glitch_pixelize")) and (idx + 1 < len(cuts))

        if can_xfade:
            next_cut = cuts[idx + 1]
            next_dur = float(next_cut.get("duration", 3.0))
            next_in = float(next_cut.get("source_in", 0.0))
            
            xfade_dur = 0.35 if t_type == "circle_zoom" else 0.30
            xfade_trans = "circleopen" if t_type == "circle_zoom" else "pixelize"

            cut_a_path = clips_dir / f"cut_{idx}_a.mp4"
            cut_b_path = clips_dir / f"cut_{idx+1}_b.mp4"
            joined_path = clips_dir / f"joined_{idx}_{idx+1}.mp4"

            cut_v_path = cut.get("video_path") or source_video_path
            next_cut_v_path = next_cut.get("video_path") or source_video_path
            src_v_a = resolve_promov3_video_path(cut_v_path, game_name=game_name, session_id=session_id) or Path(cut_v_path).resolve()
            src_v_b = resolve_promov3_video_path(next_cut_v_path, game_name=game_name, session_id=session_id) or Path(next_cut_v_path).resolve()
            if not src_v_a.exists():
                raise FileNotFoundError(f"Vídeo de origen no encontrado para el corte #{idx+1}: '{cut_v_path}'")
            if not src_v_b.exists():
                raise FileNotFoundError(f"Vídeo de origen no encontrado para el corte #{idx+2}: '{next_cut_v_path}'")
            has_audio_a = check_media_has_audio(src_v_a) if src_v_a.exists() else source_has_audio
            has_audio_b = check_media_has_audio(src_v_b) if src_v_b.exists() else source_has_audio

            vf_a = _build_cut_vf(src_v_a)
            if apply_white_fade_in:
                vf_a += ",fade=t=in:st=0:d=0.07:color=white"
                apply_white_fade_in = False
            vf_a += ",format=yuv420p"

            _extract_subclip(
                src_v_a,
                start_time=cur_in,
                duration=cur_dur + xfade_dur,
                vf=vf_a,
                out_path=cut_a_path,
                has_audio=has_audio_a,
            )

            vf_b = _build_cut_vf(src_v_b) + ",format=yuv420p"
            _extract_subclip(
                src_v_b,
                start_time=next_in,
                duration=next_dur,
                vf=vf_b,
                out_path=cut_b_path,
                has_audio=has_audio_b,
            )

            try:
                join_cmd = [
                    FFMPEG_CMD, "-y",
                    "-i", str(cut_a_path),
                    "-i", str(cut_b_path),
                    "-filter_complex",
                    f"[0:v][1:v]xfade=transition={xfade_trans}:duration={xfade_dur:.2f}:offset={cur_dur:.3f}[v];"
                    f"[0:a][1:a]acrossfade=d={xfade_dur:.2f}[a]",
                    "-map", "[v]",
                    "-map", "[a]",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k",
                    str(joined_path),
                ]
                _run_ffmpeg_cmd(join_cmd, f"Unión xfade '{xfade_trans}' entre cortes {idx+1} y {idx+2}")
                segment_paths.append(joined_path)
                applied_transitions.append({
                    "cut_index": idx + 1,
                    "type": t_type,
                    "name": TRANSITION_SPECS.get(t_type, {}).get("name", t_type),
                    "duration": xfade_dur,
                })
            except Exception as xfade_err:
                logger.warning(f"Error en xfade ({xfade_err}), usando cortes directos de respaldo")
                segment_paths.append(cut_a_path)
                segment_paths.append(cut_b_path)

            idx += 2
            continue

        # Normal cut or white_flash
        cut_out_path = clips_dir / f"cut_{idx}.mp4"
        cut_v_path = cut.get("video_path") or source_video_path
        src_v = resolve_promov3_video_path(cut_v_path, game_name=game_name, session_id=session_id) or Path(cut_v_path).resolve()
        if not src_v.exists():
            raise FileNotFoundError(f"Vídeo de origen no encontrado para el corte #{idx+1}: '{cut_v_path}'")
        has_audio = check_media_has_audio(src_v) if src_v.exists() else source_has_audio

        vf = _build_cut_vf(src_v)
        if apply_white_fade_in:
            vf += ",fade=t=in:st=0:d=0.07:color=white"
            apply_white_fade_in = False

        if t_type == "white_flash" and cur_dur > 0.15:
            vf += f",fade=t=out:st={cur_dur - 0.08:.3f}:d=0.08:color=white"
            apply_white_fade_in = True
            applied_transitions.append({
                "cut_index": idx + 1,
                "type": "white_flash",
                "name": "Flash Blanco",
                "duration": 0.15,
            })
        vf += ",format=yuv420p"

        _extract_subclip(
            src_v,
            start_time=cur_in,
            duration=cur_dur,
            vf=vf,
            out_path=cut_out_path,
            has_audio=has_audio,
        )
        segment_paths.append(cut_out_path)
        idx += 1

    # Concatenate all cuts into a base assembled video
    update_progress(65.0, "Concatenando planos de la Edit Decision List...")
    concat_txt = work_dir / "concat_list.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for sp in segment_paths:
            f.write(f"file '{sp.resolve().as_posix()}'\n")

    temp_assembled_video = work_dir / "temp_assembled.mp4"
    concat_cmd = [
        FFMPEG_CMD, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_txt),
        "-c", "copy",
        "-movflags", "+faststart",
        str(temp_assembled_video),
    ]
    _run_ffmpeg_cmd(concat_cmd, "Concatenación de planos ensamblados")

    # Retention Guard: Ensure assembled video duration is at least as long as voiceover duration to prevent freeze at the end
    assembled_dur = get_media_duration(temp_assembled_video)
    if assembled_dur > 0 and assembled_dur < voice_dur - 0.2:
        if sentence_clip_mode:
            raise ValueError("El render Caveman no cubre toda la voz. Regenera el montaje; no se repetirán clips para alargarlo.")
        import math
        logger.info(f"Metraje ensamblado ({assembled_dur:.2f}s) es menor que la locución ({voice_dur:.2f}s). Bucleando planos para cubrir la locución completa...")
        loop_count = math.ceil(voice_dur / assembled_dur) + 1
        with open(concat_txt, "w", encoding="utf-8") as f:
            for sp in segment_paths:
                f.write(f"file '{sp.resolve().as_posix()}'\n")
            body_paths = segment_paths[num_hook_cuts:] if len(segment_paths) > num_hook_cuts else segment_paths
            for _ in range(loop_count - 1):
                for sp in body_paths:
                    f.write(f"file '{sp.resolve().as_posix()}'\n")
        temp_assembled_looped = work_dir / "temp_assembled_looped.mp4"
        concat_cmd_loop = [
            FFMPEG_CMD, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_txt),
            "-c", "copy",
            "-movflags", "+faststart",
            str(temp_assembled_looped),
        ]
        _run_ffmpeg_cmd(concat_cmd_loop, "Extensión de vídeo base ensamblado")
        temp_assembled_video = temp_assembled_looped
        temp_assembled_video = temp_assembled_looped

    # 2. Classic splash lasts 1s; modern mode follows the spoken Steam prefix.
    steam_hook_overlay_dur = 1.0 if steam_hook_mode == "classic" else min(target_hook_end, 1.8)
    if enable_steam_hook and words_list and steam_hook_mode != "classic":
        try:
            from audio_aligner import filter_steam_hook_words
            _, overlay_end_time = filter_steam_hook_words(words_list)
            if float(overlay_end_time) > 0.8:
                steam_hook_overlay_dur = max(1.2, float(overlay_end_time))
        except Exception as e:
            logger.debug(f"Could not calculate spoken steam hook overlay duration: {e}")

    # Steam Hook Overlay
    hook_overlay_mov = work_dir / "steam_hook_overlay.mov"
    has_hook_overlay = False
    if enable_steam_hook and steam_hook_overlay_dur > 0.5:
        update_progress(72.0, f"Generando animación de Steam Hook ({steam_hook_overlay_dur:.1f}s)...")
        try:
            from utils import generate_steam_hook_overlay_video
            generate_steam_hook_overlay_video(
                output_mov_path=hook_overlay_mov,
                duration=steam_hook_overlay_dur,
                width=out_w,
                height=out_h,
                fps=30,
                ffmpeg_bin=FFMPEG_CMD,
                logo_scale=float(steam_logo_scale),
                logo_y_pos=int(hook_logo_y),
                text_scale=float(steam_text_scale),
                text_y_pos=int(hook_text_y),
                mode=steam_hook_mode,
            )
            has_hook_overlay = hook_overlay_mov.exists()
        except Exception as hook_err:
            logger.warning(f"Error generando Steam Hook overlay: {hook_err}")
            has_hook_overlay = False

    # Game Name Reveal Overlay (Optional widget revealing Steam game logo)
    reveal_overlay_mov = work_dir / "game_name_reveal_overlay.mov"
    has_reveal_overlay = False

    # Exact flash cut timestamp where the hook transitions into the body
    flash_cut_timestamp = round(sum(float(c.get("duration", 0.0)) for c in final_hook_cuts), 3) if final_hook_cuts else round(target_hook_end, 3)

    # User requirement: widget appears a tiny bit after the white flash transition
    # (The flash ends at flash_cut_timestamp + 0.07s; +0.15s lands right after flash settles)
    reveal_start_s = round(flash_cut_timestamp + 0.15, 3) if flash_cut_timestamp > 0.1 else (steam_hook_overlay_dur if has_hook_overlay else 0.0)
    reveal_end_s = reveal_start_s

    if enable_game_name_reveal:
        update_progress(76.0, "Generando widget animado de revelación de nombre del juego...")
        try:
            from utils import fetch_steam_game_logo, find_game_name_spoken_time, generate_game_name_reveal_overlay_video

            # Fallback game_name resolution if still empty
            resolved_game_name = (game_name or "").strip()
            if not resolved_game_name and session_id:
                try:
                    s3_file = Path("temp/v3_cache") / session_id / "step3_script.json"
                    if s3_file.exists():
                        with open(s3_file, "r", encoding="utf-8") as s3f:
                            s3d = json.load(s3f)
                            resolved_game_name = (s3d.get("game_name") or "").strip()
                            if not resolved_game_name:
                                vp = s3d.get("video_path") or (s3d.get("video_paths", [None])[0])
                                if vp:
                                    pn = Path(vp).parent.name
                                    if pn and pn.lower() not in ("promosfootage", "footage", "temp", "input", "videos", "output", "render_tmp", "clips", "test", "v3_cache"):
                                        resolved_game_name = pn
                except Exception:
                    pass

            # Fallback time: if game title not found in words, default to near the end of the voiceover
            default_fallback = max(reveal_start_s + 4.0, (voice_dur - 2.5) if voice_dur > 5.0 else (reveal_start_s + 3.0))
            spoken_name_time = find_game_name_spoken_time(
                words_list,
                game_name=resolved_game_name,
                fallback_time=default_fallback
            )
            # Duration extends until game name is pronounced in voiceover + 1.2s payoff window (capped before video ends)
            reveal_end_s = min(voice_dur - 0.2, round(spoken_name_time + 1.2, 3)) if voice_dur > 0 else round(spoken_name_time + 1.2, 3)
            reveal_total_dur = max(1.5, round(reveal_end_s - reveal_start_s, 3))
            reveal_end_s = round(reveal_start_s + reveal_total_dur, 3)
            wipe_dur = max(0.5, round(spoken_name_time - reveal_start_s, 3))

            game_logo_p = fetch_steam_game_logo(
                resolved_game_name,
                work_dir,
                session_id=session_id,
                custom_logo_path=custom_game_logo_path,
                allow_text_fallback=not require_official_game_logo,
            )
            if require_official_game_logo and not game_logo_p:
                raise ValueError("Falta el logo oficial del juego; render detenido.")
            generate_game_name_reveal_overlay_video(
                output_mov_path=reveal_overlay_mov,
                game_logo_path=game_logo_p,
                duration=reveal_total_dur,
                reveal_duration=wipe_dur,
                width=out_w,
                height=out_h,
                fps=30,
                ffmpeg_bin=FFMPEG_CMD,
                widget_y=int(game_name_reveal_y),
                widget_scale=float(game_name_reveal_scale),
            )
            has_reveal_overlay = reveal_overlay_mov.exists()
            if require_official_game_logo and not has_reveal_overlay:
                raise RuntimeError("No se pudo crear la capa del logo oficial; render detenido.")
            if has_reveal_overlay:
                logger.info(f"Widget Game Name Reveal generado ('{resolved_game_name}': {reveal_start_s:.2f}s -> {reveal_end_s:.2f}s, wipe={wipe_dur:.2f}s)")
        except Exception as rev_err:
            if require_official_game_logo:
                raise RuntimeError("No se pudo mostrar el logo oficial del juego. Revisa el recurso y vuelve a renderizar.") from rev_err
            logger.warning(f"Error generando Game Name Reveal overlay: {rev_err}")
            has_reveal_overlay = False

    # Kinetic Captions (.ass)
    ass_sub_path = work_dir / "kinetic_captions.ass"
    has_subtitles = False

    if words_list:
        update_progress(80.0, f"Generando subtítulos dinámicos kinetic ({len(words_list)} palabras)...")
        try:
            from audio_aligner import generate_kinetic_ass_subtitles
            highlight_col = captions_highlight_color or "#FFF380"
            generate_kinetic_ass_subtitles(
                alignment_data={"words": words_list},
                output_ass_path=ass_sub_path,
                video_width=out_w,
                video_height=out_h,
                font_size=font_sz,
                word_by_word=True,
                margin_v=margin_v,
                highlight_color=highlight_col,
                skip_steam_hook=has_hook_overlay,
                steam_hook_mode=steam_hook_mode,
                intro_subtitles_y=int(intro_subtitles_y),
                intro_font_size=int(intro_font_sz),
                intro_duration=float(steam_hook_overlay_dur) if has_hook_overlay else 0.0,
            )
            has_subtitles = ass_sub_path.exists()
        except Exception as ass_err:
            logger.warning(f"Error generando subtítulos ASS: {ass_err}")
            has_subtitles = False

    # Final Audio Mix & Export
    update_progress(88.0, "Mezclando audio con ducking, quemando subtítulos y renderizando con FFmpeg...")
    clean_game_name = "".join(c for c in (game_name or "promov3") if c.isalnum() or c in ("-", "_")).lower() or "promo"
    run_id = int(time.time())
    final_filename = f"{clean_game_name}_promo_v3_{resolution}_{run_id}.mp4"
    final_output_path = out_dir / final_filename

    bg_vol = float(gameplay_volume if gameplay_volume is not None else 0.15)
    if bg_vol > 1.0:
        bg_vol = bg_vol / 100.0
    bg_vol = max(0.0, min(1.0, bg_vol))

    has_assembled_audio = check_media_has_audio(temp_assembled_video) if bg_vol > 0.001 else False
    safe_ass = str(ass_sub_path.resolve()).replace("\\", "/").replace(":", "\\:")

    # Keep the original timeline even when composing legacy cached clips with
    # mixed metadata. All clips are already normalised to the same dimensions.
    inputs = ["-reinit_filter", "0", "-i", str(temp_assembled_video), "-i", str(voiceover_audio)]
    v_filters = []
    v_filters.append("[0:v]setpts=PTS-STARTPTS[v_base]")
    curr_v = "[v_base]"
    in_idx = 2

    if has_hook_overlay:
        inputs.extend(["-i", str(hook_overlay_mov)])
        v_filters.append(f"{curr_v}[{in_idx}:v]overlay=0:0:enable='between(t,0,{steam_hook_overlay_dur:.2f})'[v_hook]")
        curr_v = "[v_hook]"
        in_idx += 1

    if has_reveal_overlay:
        inputs.extend(["-i", str(reveal_overlay_mov)])
        v_filters.append(f"[{in_idx}:v]setpts=PTS-STARTPTS+{reveal_start_s:.2f}/TB[v_rev_pts]")
        v_filters.append(f"{curr_v}[v_rev_pts]overlay=0:0:enable='between(t,{reveal_start_s:.2f},{reveal_end_s:.2f})':eof_action=pass[v_rev]")
        curr_v = "[v_rev]"
        in_idx += 1

    # Optional badge is shared with the browser preview and retained by the ASS fallback.
    from channel_branding import ChannelBrandingSettings, add_channel_branding_filter
    branding_settings = ChannelBrandingSettings.model_validate(channel_branding or {})
    curr_v = add_channel_branding_filter(inputs, v_filters, curr_v, branding_settings,
                                         out_w, out_h, work_dir / "channel_branding.png")
    in_idx = inputs.count("-i")

    # Prepare fallback video filter without subtitles
    fallback_filters = list(v_filters)
    if fallback_filters:
        fallback_filters.append(f"{curr_v}null[vout]")
        fallback_v = ";".join(fallback_filters)
    else:
        fallback_v = "[0:v]copy[vout]"

    if has_subtitles:
        v_filters.append(f"{curr_v}ass='{safe_ass}'[vout]")
        comp_filter = ";".join(v_filters)
    else:
        if v_filters:
            v_filters.append(f"{curr_v}null[vout]")
            comp_filter = ";".join(v_filters)
        else:
            comp_filter = "[0:v]copy[vout]"

    audio_prep_filters = []
    audio_prep_filters.append("[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,volume=1.0[v_a]")
    amix_inputs = ["[v_a]"]

    if has_assembled_audio:
        audio_prep_filters.append(f"[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,volume={bg_vol:.2f}[g_a]")
        amix_inputs.append("[g_a]")

    custom_sfx_tracks = edl.get("sfx_tracks") if isinstance(edl, dict) else None
    r2_timing_info = None

    # Calculate the exact timestamp of the visual cut with White Flash
    flash_cut_timestamp = round(sum(float(c.get("duration", 0.0)) for c in final_hook_cuts), 3) if final_hook_cuts else round(target_hook_end, 3)

    if custom_sfx_tracks:
        sfx_base = BASE_DIR / "static" / "sfx"
        logger.info(f"Integrando {len(custom_sfx_tracks)} pistas de SFX personalizadas de la línea de tiempo (Flash cut en {flash_cut_timestamp:.3f}s)...")

        for s_idx_num, trk in enumerate(custom_sfx_tracks, 1):
            rel = trk.get("relative_path") or trk.get("file") or trk.get("name") or "raiser1.wav"
            if not any(rel.endswith(ext) for ext in [".wav", ".mp3", ".mp4", ".ogg", ".flac", ".m4a"]):
                rel = f"{rel}.wav"

            sf_path = sfx_base / rel
            if not sf_path.exists():
                sf_path = sfx_base / "raisers" / Path(rel).name
            if not sf_path.exists():
                matches = list(sfx_base.rglob(Path(rel).name))
                if matches:
                    sf_path = matches[0]

            if sf_path.exists():
                inputs.extend(["-i", str(sf_path)])
                in_idx = inputs.count("-i") - 1
                vol = float(trk.get("volume", 0.80))
                start_s = float(trk.get("start_s", 0.0))
                trim_s = float(trk.get("trim_start_s", 0.0))

                # Auto-align Raiser 2 if not explicitly pinned by the user, ensuring peak lands on the flash cut
                is_r2 = ("raiser2" in rel.lower()) or (trk.get("id") == "trk_raiser2") or (trk.get("name") == "raiser2")
                if is_r2 and trk.get("auto_sync") is True and trk.get("reason") and not trk.get("pinned"):
                    expected_start = round(flash_cut_timestamp - 1.240, 3)
                    if expected_start < 0:
                        trim_s = round(abs(expected_start), 3)
                        start_s = 0.0
                    else:
                        start_s = expected_start
                        trim_s = 0.0
                    logger.info(f"   • Auto-sincronizado Raiser 2 ({sf_path.name}) para impacto en Flash Blanco a {flash_cut_timestamp:.3f}s: inicio={start_s:.3f}s, trim={trim_s:.3f}s")

                f_parts = ["aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"]
                if trim_s > 0.01:
                    f_parts.append(f"atrim=start={trim_s:.3f},asetpts=PTS-STARTPTS")
                if start_s > 0.01:
                    start_ms = int(round(start_s * 1000))
                    f_parts.append(f"adelay={start_ms}|{start_ms}")
                f_parts.append(f"volume={vol:.2f}")

                filter_str = f"[{in_idx}:a]" + ",".join(f_parts) + f"[cust_sfx_{s_idx_num}]"
                audio_prep_filters.append(filter_str)
                amix_inputs.append(f"[cust_sfx_{s_idx_num}]")
                logger.info(f"   • SFX #{s_idx_num} ({sf_path.name}): inicio a {start_s:.2f}s, vol {int(vol*100)}%")
    elif edl.get("allow_automatic_sfx") is True and edl.get("automatic_sfx_reason") and edl.get("automatic_sfx_anchor") is not None:
        # Legacy raisers require an explicit editorial opt-in and a real action anchor.
        # Check for Raiser SFX in static/sfx/raisers/
        sfx_dir = BASE_DIR / "static" / "sfx" / "raisers"
        r1_path = sfx_dir / "raiser1.wav"
        if not r1_path.exists():
            r1_path = sfx_dir / "raiser1.mp3"
        if not r1_path.exists():
            r1_path = sfx_dir / "Raiser1.mp4"

        r2_path = sfx_dir / "raiser2.wav"
        if not r2_path.exists():
            r2_path = sfx_dir / "raiser2.mp3"
        if not r2_path.exists():
            r2_path = sfx_dir / "Raiser2.mp4"

        r1_idx = None
        r2_idx = None

        if r1_path.exists():
            inputs.extend(["-i", str(r1_path)])
            r1_idx = inputs.count("-i") - 1

        if r2_path.exists():
            inputs.extend(["-i", str(r2_path)])
            r2_idx = inputs.count("-i") - 1

        if r1_idx is not None:
            dur1 = get_media_duration(r1_path)
            if dur1 <= 0.1:
                dur1 = 1.4
            target_r1_end = steam_hook_overlay_dur if (enable_steam_hook and steam_hook_overlay_dur > 0.5) else 1.8
            if dur1 > target_r1_end:
                trim_off1 = round(dur1 - target_r1_end, 3)
                audio_prep_filters.append(
                    f"[{r1_idx}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,atrim=start={trim_off1:.3f},asetpts=PTS-STARTPTS,volume=0.80[sfx1]"
                )
                logger.info(f"Integrando SFX Raiser 1 (recortado {trim_off1:.2f}s para terminar exactamente cuando desaparece el splash de NEW STEAM GAME a {target_r1_end:.2f}s): {r1_path.name}")
            else:
                r1_delay_ms = int(round((target_r1_end - dur1) * 1000))
                if r1_delay_ms > 0:
                    audio_prep_filters.append(
                        f"[{r1_idx}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,adelay={r1_delay_ms}|{r1_delay_ms},volume=0.80[sfx1]"
                    )
                else:
                    audio_prep_filters.append(
                        f"[{r1_idx}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,volume=0.80[sfx1]"
                    )
                logger.info(f"Integrando SFX Raiser 1 (inicia a {max(0.0, target_r1_end - dur1):.2f}s para terminar exactamente cuando desaparece el splash de NEW STEAM GAME a {target_r1_end:.2f}s): {r1_path.name}")
            amix_inputs.append("[sfx1]")

        if r2_idx is not None:
            dur2 = get_media_duration(r2_path)
            if dur2 <= 0.1:
                dur2 = 1.66
            r2_peak_offset = 1.240 if "raiser2" in str(r2_path).lower() else dur2
            target_r2_start = flash_cut_timestamp - r2_peak_offset

            if target_r2_start < 0:
                trim_off = round(abs(target_r2_start), 3)
                audio_prep_filters.append(
                    f"[{r2_idx}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,atrim=start={trim_off:.3f},asetpts=PTS-STARTPTS,volume=0.80[sfx2]"
                )
                r2_start_time = 0.0
                r2_climax_time = round(flash_cut_timestamp, 3)
                r2_end_time = round(max(0.0, dur2 - trim_off), 3)
            else:
                r2_delay_ms = int(round(target_r2_start * 1000))
                r2_start_time = round(target_r2_start, 3)
                r2_climax_time = round(flash_cut_timestamp, 3)
                r2_end_time = round(target_r2_start + dur2, 3)
                if r2_delay_ms > 0:
                    audio_prep_filters.append(
                        f"[{r2_idx}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,adelay={r2_delay_ms}|{r2_delay_ms},volume=0.80[sfx2]"
                    )
                else:
                    audio_prep_filters.append(
                        f"[{r2_idx}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,volume=0.80[sfx2]"
                    )

            r2_timing_info = {
                "sentence_1_end_s": round(flash_cut_timestamp, 3),
                "sfx_start_s": round(r2_start_time, 3),
                "sfx_end_s": round(r2_end_time, 3),
                "sfx_climax_peak_s": round(r2_climax_time, 3),
                "file_duration_s": round(dur2, 3),
            }
            update_progress(89.0, f"Integrado SFX Raiser 2 a {r2_start_time:.2f}s")
            amix_inputs.append("[sfx2]")

    audio_chain = ";".join(audio_prep_filters)
    audio_mix = f";{''.join(amix_inputs)}amix=inputs={len(amix_inputs)}:duration=first:dropout_transition=0:normalize=0[aout]"
    audio_filter = f";{audio_chain}{audio_mix}"

    silent_video_cache = work_dir / "silent_video.mp4"
    master_audio_cache = work_dir / "master_audio.mp3"

    # Step 1: Render Silent Video with Subtitles + Steam Hook (no audio)
    update_progress(92.0, "Generando vídeo mudo con subtítulos y gráfica de Steam...")
    silent_cmd = [
        FFMPEG_CMD, "-y",
        *inputs,
        "-filter_complex", comp_filter,
        "-map", "[vout]",
        "-an",
        "-c:v", "libx264",
        "-crf", "16",
        "-preset", "medium",
        "-pix_fmt", "yuv420p",
        "-t", f"{voice_dur:.3f}",
        "-movflags", "+faststart",
        str(silent_video_cache),
    ]

    try:
        _run_ffmpeg_cmd(silent_cmd, "Composición de vídeo mudo con subtítulos ASS")
    except Exception as sil_err:
        logger.warning(f"Error en vídeo mudo con ASS ({sil_err}), reintentando sin filtro ASS...")
        fallback_cmd = [
            FFMPEG_CMD, "-y",
            *inputs,
            "-filter_complex", fallback_v,
            "-map", "[vout]",
            "-an",
            "-c:v", "libx264",
            "-crf", "16",
            "-preset", "medium",
            "-pix_fmt", "yuv420p",
            "-t", f"{voice_dur:.3f}",
            "-movflags", "+faststart",
            str(silent_video_cache),
        ]
        _run_ffmpeg_cmd(fallback_cmd, "Composición de vídeo mudo sin filtro ASS (fallback)")

    # Step 2: Render Master Audio Track
    update_progress(96.0, "Mezclando pista maestra de audio (Voz + SFX + Gameplay)...")
    audio_cmd = [
        FFMPEG_CMD, "-y",
        *inputs,
        "-filter_complex", audio_filter[1:],  # strip leading ';'
        "-map", "[aout]",
        "-c:a", "libmp3lame",
        "-b:a", "320k",
        "-t", f"{voice_dur:.3f}",
        str(master_audio_cache),
    ]
    _run_ffmpeg_cmd(audio_cmd, "Mezcla de pista maestra de audio")

    # Step 3: Fast Mux (Combine Silent Video + Master Audio)
    update_progress(98.0, "Fusionando vídeo mudo y pista de audio maestra...")
    mux_cmd = [
        FFMPEG_CMD, "-y",
        "-i", str(silent_video_cache),
        "-i", str(master_audio_cache),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "320k",
        "-movflags", "+faststart",
        str(final_output_path),
    ]
    _run_ffmpeg_cmd(mux_cmd, "Fusión final de vídeo y pista maestra de audio")

    # Do not report success for an audio-only tail masked by the mux duration.
    silent_duration = get_media_duration(silent_video_cache)
    if silent_duration < voice_dur - 0.1:
        raise ValueError(f"El vídeo compuesto termina antes que la voz ({silent_duration:.3f}s / {voice_dur:.3f}s).")

    # Copy files to session cache dir for easy serving
    resolved_session_id = session_id or edl.get("session_id") or (Path(temp_dir).parent.name if temp_dir and Path(temp_dir).parent.name not in ("temp", "") else voiceover_audio.parent.name)
    session_cache_dir = BASE_DIR / "temp" / "v3_cache" / resolved_session_id
    session_cache_dir.mkdir(parents=True, exist_ok=True)
    sess_silent = session_cache_dir / "silent_video.mp4"
    sess_audio = session_cache_dir / "master_audio.mp3"
    try:
        shutil.copy2(silent_video_cache, sess_silent)
        shutil.copy2(master_audio_cache, sess_audio)
    except Exception:
        pass

    file_size_bytes = final_output_path.stat().st_size if final_output_path.exists() else 0
    file_size_mb = round(file_size_bytes / (1024 * 1024), 2)
    video_url = output_url(final_output_path, root=BASE_DIR / "output")
    silent_url = f"/media/temp/v3_cache/{resolved_session_id}/silent_video.mp4"
    master_audio_url = f"/media/temp/v3_cache/{resolved_session_id}/master_audio.mp3"

    update_progress(100.0, "¡Pre-renderizado completado! Vídeo mudo y pista de audio maestra generados.")

    return {
        "status": "completed",
        "video_url": video_url,
        "video_urls": [video_url],
        "video_path": str(final_output_path),
        "output_path": str(final_output_path),
        "silent_video_url": silent_url,
        "master_audio_url": master_audio_url,
        "filename": final_filename,
        "game_name": game_name,
        "resolution": resolution,
        "width": out_w,
        "height": out_h,
        "duration": round(voice_dur, 2),
        "cut_count": len(cuts),
        "transitions_applied": applied_transitions,
        "has_steam_hook": has_hook_overlay,
        "channel_branding": branding_settings.model_dump(),
        "has_subtitles": has_subtitles,
        "file_size_mb": file_size_mb,
        "gameplay_volume": round(bg_vol * 100, 1),
        "sfx_timing": r2_timing_info,
    }


def remix_promov3_audio_sfx(
    session_dir: str | Path,
    sfx_tracks: List[Dict[str, Any]],
    gameplay_volume: float = 0.15,
) -> Dict[str, Any]:
    """
    Fast SFX & Audio Re-mixer (~1.5s execution):
    Takes an existing session folder with step5_result.json, voiceover.mp3, and temp_assembled_video.mp4.
    Re-mixes audio streams according to user-customized sfx_tracks parameters (start_s, trim_start_s, volume)
    and muxes them with the pre-rendered video using `-c:v copy` (zero video re-encoding overhead).
    Updates step5_result.json and returns updated render metadata.
    """
    sess_dir = Path(session_dir).resolve()
    step5_file = sess_dir / "step5_result.json"
    if not step5_file.exists():
        raise FileNotFoundError(f"No step5_result.json found in {sess_dir}")

    with open(step5_file, "r", encoding="utf-8") as f:
        step5_data = json.load(f)

    video_path = Path(step5_data.get("video_path", ""))
    if not video_path.exists():
        raise FileNotFoundError(f"Rendered video file not found: {video_path}")

    step3_file = sess_dir / "step3_voice.json"
    voice_path = None
    if step3_file.exists():
        try:
            with open(step3_file, "r", encoding="utf-8") as f:
                s3_data = json.load(f)
                voice_path = Path(s3_data.get("audio_path", ""))
        except Exception:
            pass

    raw_assembled_video = sess_dir / "temp_assembled_video.mp4"

    inputs = ["-i", str(video_path)]  # Input 0: pre-rendered video for -map 0:v

    has_raw_gameplay = False
    gameplay_stream_idx = 0
    if raw_assembled_video.exists():
        inputs.extend(["-i", str(raw_assembled_video)])
        has_raw_gameplay = check_media_has_audio(raw_assembled_video)
        gameplay_stream_idx = (len(inputs) // 2) - 1

    step3_file = sess_dir / "step3_voice.json"
    voice_path = None
    if step3_file.exists():
        try:
            with open(step3_file, "r", encoding="utf-8") as f:
                s3_data = json.load(f)
                voice_path = Path(s3_data.get("audio_path", ""))
        except Exception:
            pass

    if not voice_path or not voice_path.exists():
        voice_path = sess_dir / "voiceover.mp3"

    has_voice = False
    voice_stream_idx = None
    if voice_path and voice_path.exists():
        inputs.extend(["-i", str(voice_path)])
        has_voice = True
        voice_stream_idx = (len(inputs) // 2) - 1

    bg_vol = float(gameplay_volume if gameplay_volume is not None else 0.15)
    if bg_vol > 1.0:
        bg_vol = bg_vol / 100.0
    bg_vol = max(0.0, min(1.0, bg_vol))

    audio_prep_filters = []
    amix_inputs = []

    if has_voice and voice_stream_idx is not None:
        audio_prep_filters.append(f"[{voice_stream_idx}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,volume=1.0[v_a]")
        amix_inputs.append("[v_a]")

    if bg_vol > 0.001 and has_raw_gameplay:
        audio_prep_filters.append(f"[{gameplay_stream_idx}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,volume={bg_vol:.2f}[g_a]")
        amix_inputs.append("[g_a]")

    sfx_base = BASE_DIR / "static" / "sfx"

    for idx, trk in enumerate(sfx_tracks):
        rel = trk.get("relative_path") or trk.get("file") or trk.get("name") or "raiser1.wav"
        if not any(rel.endswith(ext) for ext in [".wav", ".mp3", ".mp4", ".ogg", ".flac", ".m4a"]):
            rel = f"{rel}.wav"

        sf_path = sfx_base / rel
        if not sf_path.exists():
            sf_path = sfx_base / "raisers" / Path(rel).name
        if not sf_path.exists():
            matches = list(sfx_base.rglob(Path(rel).name))
            if matches:
                sf_path = matches[0]

        if sf_path.exists():
            inputs.extend(["-i", str(sf_path)])
            s_idx = (len(inputs) // 2) - 1
            vol = float(trk.get("volume", 0.80))
            start_s = float(trk.get("start_s", 0.0))
            trim_s = float(trk.get("trim_start_s", 0.0))

            f_parts = ["aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"]
            if trim_s > 0.01:
                f_parts.append(f"atrim=start={trim_s:.3f},asetpts=PTS-STARTPTS")
            if start_s > 0.01:
                start_ms = int(round(start_s * 1000))
                f_parts.append(f"adelay={start_ms}|{start_ms}")
            f_parts.append(f"volume={vol:.2f}")

            filter_str = f"[{s_idx}:a]" + ",".join(f_parts) + f"[sfx_{idx}]"
            audio_prep_filters.append(filter_str)
            amix_inputs.append(f"[sfx_{idx}]")

    if not amix_inputs:
        amix_inputs.append("[0:a]")

    audio_chain = ";".join(audio_prep_filters)
    audio_mix = f";{''.join(amix_inputs)}amix=inputs={len(amix_inputs)}:duration=first:dropout_transition=0:normalize=0[aout]"
    audio_filter = f"{audio_chain}{audio_mix}"

    remix_filename = f"{video_path.stem}_remixed{video_path.suffix}"
    remix_path = video_path.parent / remix_filename

    cmd = [
        FFMPEG_CMD, "-y",
        *inputs,
        "-filter_complex", audio_filter,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "320k",
        "-movflags", "+faststart",
        str(remix_path),
    ]

    _run_ffmpeg_cmd(cmd, "Remezcla de pista de audio en vídeo exportado")

    # Export/update master_audio.mp3 in session cache as well
    cmd_audio = [
        FFMPEG_CMD, "-y",
        *inputs,
        "-filter_complex", audio_filter,
        "-map", "[aout]",
        "-c:a", "libmp3lame",
        "-b:a", "320k",
        str(sess_dir / "master_audio.mp3"),
    ]
    try:
        _run_ffmpeg_cmd(cmd_audio, "Remezcla de master_audio.mp3")
    except Exception as e:
        logger.warning(f"Error actualizando master_audio.mp3 re-mezclado: {e}")

    remix_url = output_url(remix_path, root=BASE_DIR / "output")
    step5_data["video_url"] = remix_url
    step5_data["video_urls"] = [remix_url]
    step5_data["video_path"] = str(remix_path)
    step5_data["silent_video_url"] = f"/media/temp/v3_cache/{sess_dir.name}/silent_video.mp4"
    step5_data["master_audio_url"] = f"/media/temp/v3_cache/{sess_dir.name}/master_audio.mp3"
    step5_data["filename"] = remix_filename
    step5_data["remixed_sfx_tracks"] = sfx_tracks

    with open(step5_file, "w", encoding="utf-8") as f:
        json.dump(step5_data, f, indent=2, ensure_ascii=False)

    return step5_data

