"""
benchmark.py — Video Rendering Benchmark Suite

Tests, measures, and stores rendering performance across:
- Resolutions: 1080p (1080x1920) vs 4K (2160x3840)
- Hardware: CPU (libx264) vs GPU (h264_nvenc)
- Blur intensities: 100% (20:10), 75% (15:8), 50% (10:5)

All clips are strictly 10.0 seconds (cut from 0s to 10s).
Outputs are stored in temp/benchmarks/ and never in output/.
"""

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ffmpeg_wrapper import FFmpegWrapper
from utils import download_youtube_clip, generate_run_id

logger = logging.getLogger(__name__)

BENCHMARK_HISTORY_FILE = Path("data/benchmark_history.json")

BLUR_LEVELS = {
    "100% (Actual)": {"radius": 20, "power": 10, "label": "100%"},
    "75% Intensidad": {"radius": 15, "power": 8, "label": "75%"},
    "50% Intensidad": {"radius": 10, "power": 5, "label": "50%"},
}

RESOLUTIONS = {
    "1080p": {"width": 1080, "height": 1920, "label": "1080x1920 (Full HD)"},
    "4K": {"width": 2160, "height": 3840, "label": "2160x3840 (Ultra HD)"},
}

HARDWARE_MODES = {
    "CPU": {"codec": "libx264", "label": "CPU (libx264)"},
    "GPU": {"codec": "h264_nvenc", "label": "GPU (h264_nvenc)"},
}


def load_benchmark_history() -> list[dict]:
    """Load benchmark records from data/benchmark_history.json."""
    if not BENCHMARK_HISTORY_FILE.exists():
        return []
    try:
        with open(BENCHMARK_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to read benchmark history: {e}")
        return []


def save_benchmark_record(record: dict) -> None:
    """Save a new benchmark record into data/benchmark_history.json."""
    history = load_benchmark_history()
    history.insert(0, record)
    BENCHMARK_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(BENCHMARK_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to save benchmark record: {e}")


def clear_benchmark_history() -> None:
    """Clear all past benchmark history."""
    if BENCHMARK_HISTORY_FILE.exists():
        try:
            with open(BENCHMARK_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump([], f)
        except Exception as e:
            logger.error(f"Failed to clear benchmark history: {e}")


def prepare_benchmark_10s_clip(
    source_input: str,
    temp_dir: Path,
    ffmpeg_bin: str = "ffmpeg"
) -> Path:
    """Download from YouTube or cut local video to exactly 0.0s to 10.0s."""
    temp_dir.mkdir(parents=True, exist_ok=True)
    target_clip = temp_dir / "benchmark_source_10s.mp4"

    if source_input.startswith("http://") or source_input.startswith("https://"):
        download_youtube_clip(
            url=source_input,
            start_sec=0.0,
            end_sec=10.0,
            output_path=target_clip,
            ffmpeg_bin=ffmpeg_bin
        )
    else:
        src_path = Path(source_input)
        if not src_path.exists():
            raise FileNotFoundError(f"Archivo de origen no encontrado: {src_path}")
        
        # Cut 0s to 10s using FFmpeg
        cmd = [
            ffmpeg_bin,
            "-y",
            "-ss", "0.0",
            "-t", "10.0",
            "-i", str(src_path),
            "-c:v", "libx264",
            "-preset", "superfast",
            "-c:a", "aac",
            str(target_clip)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Error cortando clip de 10s: {res.stderr}")

    return target_clip


def run_full_benchmark(
    source_input: str,
    ffmpeg_bin: str = "ffmpeg",
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> dict:
    """Execute the complete 12-combination benchmark.

    Tests:
      - 2 Resolutions (1080p, 4K)
      - 2 Hardware Encoders (CPU, GPU)
      - 3 Blur Levels (100%, 75%, 50%)

    Returns:
      Dictionary with test results, comparative stats, and file paths.
    """
    run_id = generate_run_id()
    benchmark_dir = Path("temp") / "benchmarks" / f"run_{run_id}"
    benchmark_dir.mkdir(parents=True, exist_ok=True)

    # 1. Prepare 10.0s source clip
    if progress_callback:
        progress_callback(0, 12, "Descargando y recortando clip de prueba (segundos 0 a 10)...")

    source_10s_clip = prepare_benchmark_10s_clip(source_input, benchmark_dir, ffmpeg_bin)
    ffmpeg = FFmpegWrapper(ffmpeg_path=ffmpeg_bin)

    results = []
    total_steps = len(RESOLUTIONS) * len(HARDWARE_MODES) * len(BLUR_LEVELS)
    current_step = 0

    # 2. Iterate through all combinations
    for res_name, res_info in RESOLUTIONS.items():
        w = res_info["width"]
        h = res_info["height"]

        for hw_name, hw_info in HARDWARE_MODES.items():
            codec = hw_info["codec"]

            for blur_name, blur_info in BLUR_LEVELS.items():
                current_step += 1
                combo_name = f"{res_name} | {hw_name} | Blur {blur_info['label']}"
                
                if progress_callback:
                    progress_callback(current_step, total_steps, f"Renderizando {combo_name}...")

                out_filename = f"{res_name}_{hw_name}_blur_{blur_info['label'].replace('%', 'pct')}.mp4"
                out_path = benchmark_dir / out_filename

                # Run render and time with high precision
                t_start = time.perf_counter()
                ffmpeg.create_base_vertical_split_blur(
                    input_path=source_10s_clip,
                    output_path=out_path,
                    target_width=w,
                    target_height=h,
                    zoom=1.0,
                    video_y_position=0,
                    blur_radius=blur_info["radius"],
                    blur_power=blur_info["power"],
                    vcodec=codec
                )
                t_end = time.perf_counter()
                render_time = round(t_end - t_start, 3)

                # Duration is exactly 10.0s
                time_per_sec = round(render_time / 10.0, 3)
                speedup_x = round(10.0 / render_time, 2) if render_time > 0 else 0.0

                results.append({
                    "resolution": res_name,
                    "resolution_label": res_info["label"],
                    "width": w,
                    "height": h,
                    "hardware": hw_name,
                    "codec": codec,
                    "blur_level": blur_name,
                    "blur_label": blur_info["label"],
                    "blur_radius": blur_info["radius"],
                    "blur_power": blur_info["power"],
                    "render_time_sec": render_time,
                    "time_per_sec_video": time_per_sec,
                    "speedup_realtime": speedup_x,
                    "output_file": str(out_path),
                    "file_size_mb": round(out_path.stat().st_size / (1024 * 1024), 2) if out_path.exists() else 0.0
                })

    # 3. Calculate comparative stats
    # Average 1080p vs 4K
    times_1080p = [r["render_time_sec"] for r in results if r["resolution"] == "1080p"]
    times_4k = [r["render_time_sec"] for r in results if r["resolution"] == "4K"]
    avg_1080p = round(sum(times_1080p) / len(times_1080p), 2) if times_1080p else 0
    avg_4k = round(sum(times_4k) / len(times_4k), 2) if times_4k else 0
    ratio_4k_vs_1080p = round(avg_4k / avg_1080p, 2) if avg_1080p > 0 else 0

    # Average CPU vs GPU
    times_cpu = [r["render_time_sec"] for r in results if r["hardware"] == "CPU"]
    times_gpu = [r["render_time_sec"] for r in results if r["hardware"] == "GPU"]
    avg_cpu = round(sum(times_cpu) / len(times_cpu), 2) if times_cpu else 0
    avg_gpu = round(sum(times_gpu) / len(times_gpu), 2) if times_gpu else 0
    gpu_speedup_percent = round(((avg_cpu - avg_gpu) / avg_cpu) * 100, 1) if avg_cpu > 0 else 0

    # Blur impact
    times_blur_100 = [r["render_time_sec"] for r in results if r["blur_label"] == "100%"]
    times_blur_50 = [r["render_time_sec"] for r in results if r["blur_label"] == "50%"]
    avg_blur_100 = round(sum(times_blur_100) / len(times_blur_100), 2) if times_blur_100 else 0
    avg_blur_50 = round(sum(times_blur_50) / len(times_blur_50), 2) if times_blur_50 else 0
    blur_reduction_percent = round(((avg_blur_100 - avg_blur_50) / avg_blur_100) * 100, 1) if avg_blur_100 > 0 else 0

    fastest_result = min(results, key=lambda x: x["render_time_sec"])

    benchmark_summary = {
        "run_id": run_id,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source_input,
        "clip_duration_sec": 10.0,
        "avg_1080p_sec": avg_1080p,
        "avg_4k_sec": avg_4k,
        "ratio_4k_vs_1080p": ratio_4k_vs_1080p,
        "avg_cpu_sec": avg_cpu,
        "avg_gpu_sec": avg_gpu,
        "gpu_speedup_percent": gpu_speedup_percent,
        "avg_blur_100_sec": avg_blur_100,
        "avg_blur_50_sec": avg_blur_50,
        "blur_reduction_percent": blur_reduction_percent,
        "fastest_combo": f"{fastest_result['resolution']} + {fastest_result['hardware']} + Blur {fastest_result['blur_label']}",
        "fastest_time_sec": fastest_result["render_time_sec"],
        "fastest_time_per_sec": fastest_result["time_per_sec_video"],
        "results": results
    }

    # Save to persistent history
    save_benchmark_record(benchmark_summary)
    return benchmark_summary
