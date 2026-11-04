"""
retention_analyzer.py — Computer Vision and Geometry-based Retention Curve Extractor
Extracts second-by-second audience retention data from screenshots of:
- Instagram Reels
- YouTube Shorts
- TikTok Analytics

100% Computer Vision & Geometry (OpenCV + NumPy). ZERO LLMs.
"""

import cv2
import numpy as np
import base64
import statistics
from typing import Dict, List, Any, Optional
from pathlib import Path


def _image_to_base64(img_bgr: np.ndarray, format: str = "jpeg") -> str:
    """Encode OpenCV BGR image to base64 Data URI string."""
    ext = ".jpg" if format == "jpeg" else ".png"
    success, buffer = cv2.imencode(ext, img_bgr)
    if not success:
        return ""
    b64_str = base64.b64encode(buffer).decode("utf-8")
    mime = "image/jpeg" if format == "jpeg" else "image/png"
    return f"data:{mime};base64,{b64_str}"


def _load_image(input_data) -> np.ndarray:
    """Load image from filepath, bytes, or numpy array."""
    if isinstance(input_data, np.ndarray):
        return input_data
    elif isinstance(input_data, (str, Path)):
        img = cv2.imread(str(input_data))
        if img is None:
            raise ValueError(f"No se pudo cargar la imagen desde la ruta: {input_data}")
        return img
    elif isinstance(input_data, bytes):
        arr = np.frombuffer(input_data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("No se pudo decodificar el buffer de imagen.")
        return img
    else:
        raise TypeError(f"Tipo de entrada no soportado: {type(input_data)}")


def _compute_retention_metrics(points: List[Dict[str, Any]], duration: float) -> Dict[str, Any]:
    """Calculate key analytical metrics from second-by-second retention curve."""
    if not points:
        return {}

    pcts = [p["retention_pct"] for p in points]
    initial = pcts[0] if len(pcts) > 0 else 0.0
    r_3s = pcts[min(3, len(pcts) - 1)] if len(pcts) > 0 else 0.0
    hook_drop = round(initial - r_3s, 1)

    half_sec = int(round(duration / 2))
    halfway = pcts[min(half_sec, len(pcts) - 1)] if len(pcts) > 0 else 0.0
    final = pcts[-1] if len(pcts) > 0 else 0.0
    avg = round(float(np.mean(pcts)), 1)

    # Find biggest drop between consecutive seconds
    max_drop = 0.0
    max_drop_sec = 0
    for i in range(len(pcts) - 1):
        drop = pcts[i] - pcts[i + 1]
        if drop > max_drop:
            max_drop = drop
            max_drop_sec = points[i + 1]["second"]

    return {
        "initial_retention": round(initial, 1),
        "retention_3s": round(r_3s, 1),
        "hook_drop_3s": hook_drop,
        "halfway_retention": round(halfway, 1),
        "final_retention": round(final, 1),
        "average_retention": avg,
        "biggest_drop_second": int(max_drop_sec),
        "biggest_drop_pct": round(max_drop, 1),
    }


# =============================================================================
# 1. INSTAGRAM REELS RETENTION ANALYZER
# =============================================================================

def analyze_reels_retention(
    image_input,
    manual_duration: Optional[float] = None
) -> Dict[str, Any]:
    """
    Extracts retention curve from Instagram Reels analytics screenshot.
    - Grid lines: 3 horizontal lines (100%, 50%, 0%)
    - Curve: Dark/Black line (< 90) on white background.
    """
    img = _load_image(image_input)
    H, W, _ = img.shape
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 1. Detect candidate horizontal grid lines (light grey 220-248 across >= 55% width)
    row_light_counts = np.sum((gray >= 220) & (gray <= 248), axis=1)
    candidates = np.where(row_light_counts > W * 0.55)[0]

    lines = []
    current_group = []
    for r in candidates:
        if not current_group or r == current_group[-1] + 1:
            current_group.append(r)
        else:
            lines.append(int(np.mean(current_group)))
            current_group = [r]
    if current_group:
        lines.append(int(np.mean(current_group)))

    # Find triplet of grid lines with equal spacing (100%, 50%, 0%)
    best_triplet = None
    min_spacing_diff = 999
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            for k in range(j + 1, len(lines)):
                s1 = lines[j] - lines[i]
                s2 = lines[k] - lines[j]
                if 25 <= s1 <= 250 and abs(s1 - s2) <= 4:
                    diff = abs(s1 - s2)
                    if diff < min_spacing_diff:
                        min_spacing_diff = diff
                        best_triplet = (lines[i], lines[j], lines[k])

    if not best_triplet:
        y_100 = int(H * 0.46)
        y_0 = int(H * 0.61)
        y_50 = int((y_100 + y_0) / 2)
    else:
        y_100, y_50, y_0 = best_triplet

    # 2. Find black curve pixels within the chart vertical range
    search_y_top = max(0, y_100 - 15)
    search_y_bot = min(H, y_0 + 15)

    x_has_curve = []
    for x in range(W):
        col = gray[search_y_top:search_y_bot, x]
        if np.any(col < 90):
            x_has_curve.append(x)

    if not x_has_curve:
        raise ValueError("No se pudo detectar la curva de retención de Instagram Reels.")

    x_start = x_has_curve[0]
    x_end = x_has_curve[-1]

    # Duration: default 26.0s or manual override
    duration = float(manual_duration) if manual_duration and manual_duration > 0 else 26.0

    # 3. Extract curve points per column
    curve_x = []
    curve_pct = []
    for x in range(x_start, x_end + 1):
        col = gray[search_y_top:search_y_bot, x]
        black_idx = np.where(col < 90)[0]
        if len(black_idx) > 0:
            y_val = search_y_top + np.median(black_idx)
            pct = 100.0 * (y_0 - y_val) / (y_0 - y_100)
            pct = max(0.0, min(100.0, pct))
            curve_x.append(x)
            curve_pct.append(pct)

    # Interpolate to second-by-second
    total_sec = int(round(duration))
    time_per_px = duration / max(1, (x_end - x_start))
    seconds = np.arange(0, total_sec + 1)
    pts_sec = np.interp(
        seconds,
        [(x - x_start) * time_per_px for x in curve_x],
        curve_pct
    )

    sec_data = [
        {"second": int(s), "retention_pct": round(float(p), 1)}
        for s, p in zip(seconds, pts_sec)
    ]

    metrics = _compute_retention_metrics(sec_data, duration)

    # 4. Generate Debug Overlay Image
    debug_crop_y1 = max(0, y_100 - 30)
    debug_crop_y2 = min(H, y_0 + 40)
    debug_crop_x1 = max(0, x_start - 30)
    debug_crop_x2 = min(W, x_end + 30)

    debug_img = img.copy()
    cv2.line(debug_img, (0, y_100), (W, y_100), (255, 140, 0), 1)  # Orange 100%
    cv2.line(debug_img, (0, y_50), (W, y_50), (255, 200, 0), 1)   # Yellow 50%
    cv2.line(debug_img, (0, y_0), (W, y_0), (180, 180, 180), 1)   # Grey 0%

    for i in range(len(curve_x) - 1):
        pt1 = (curve_x[i], int(y_0 - (curve_pct[i] / 100.0) * (y_0 - y_100)))
        pt2 = (curve_x[i+1], int(y_0 - (curve_pct[i+1] / 100.0) * (y_0 - y_100)))
        cv2.line(debug_img, pt1, pt2, (0, 255, 0), 2)
    cv2.circle(debug_img, (curve_x[0], int(y_0 - (curve_pct[0] / 100.0) * (y_0 - y_100))), 5, (0, 0, 255), -1)
    cv2.circle(debug_img, (curve_x[-1], int(y_0 - (curve_pct[-1] / 100.0) * (y_0 - y_100))), 5, (0, 0, 255), -1)

    debug_crop = debug_img[debug_crop_y1:debug_crop_y2, debug_crop_x1:debug_crop_x2]

    return {
        "platform": "reels",
        "platform_name": "Instagram Reels",
        "detected": True,
        "duration_seconds": duration,
        "grid_lines": {"100%": y_100, "50%": y_50, "0%": y_0},
        "bounds": {"x_start": int(x_start), "x_end": int(x_end), "y_100": int(y_100), "y_0": int(y_0)},
        "retention_per_second": sec_data,
        "metrics": metrics,
        "debug_image": _image_to_base64(debug_crop),
    }


# =============================================================================
# 2. YOUTUBE SHORTS RETENTION ANALYZER
# =============================================================================

def analyze_youtube_retention(
    image_input,
    manual_duration: Optional[float] = None
) -> Dict[str, Any]:
    """
    Extracts retention curve from YouTube Shorts analytics screenshot.
    - Grid lines: 4 horizontal lines (100%, 66%, 33%, 0%)
    - Curve: Magenta / Purple-Pink line (#d946ef) tracking retention (often > 100%).
    """
    img = _load_image(image_input)
    H, W, _ = img.shape
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 1. Detect candidate horizontal grid lines
    row_light_counts = np.sum((gray >= 220) & (gray <= 248), axis=1)
    candidates = np.where(row_light_counts > W * 0.5)[0]

    lines = []
    current_group = []
    for r in candidates:
        if not current_group or r == current_group[-1] + 1:
            current_group.append(r)
        else:
            lines.append(int(np.mean(current_group)))
            current_group = [r]
    if current_group:
        lines.append(int(np.mean(current_group)))

    # Pink / Magenta detector: (R + B) - 2*G
    pink_score = (img[:, :, 2].astype(int) + img[:, :, 0].astype(int)) - 2 * img[:, :, 1].astype(int)
    pink_mask = (pink_score > 60) & (img[:, :, 2] > 120) & (img[:, :, 0] > 80) & (img[:, :, 1] < 140)

    # Filter pink pixels only in the chart region (Y > 0.45 * H)
    chart_y_min = int(H * 0.45)
    pink_mask[:chart_y_min, :] = False

    y_pink, x_pink = np.where(pink_mask)
    if len(y_pink) == 0:
        raise ValueError("No se pudo detectar la curva magenta de YouTube Shorts.")

    y_pink_center = np.mean(y_pink)

    # Find quadruplet of grid lines (100%, 66%, 33%, 0%) closest to pink curve
    best_quad = None
    min_dist_to_pink = 9999
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            for k in range(j + 1, len(lines)):
                for l in range(k + 1, len(lines)):
                    s1 = lines[j] - lines[i]
                    s2 = lines[k] - lines[j]
                    s3 = lines[l] - lines[k]
                    if 25 <= s1 <= 150 and max(abs(s1 - s2), abs(s2 - s3), abs(s1 - s3)) <= 3:
                        quad_center = (lines[i] + lines[l]) / 2.0
                        dist = abs(quad_center - y_pink_center)
                        if dist < min_dist_to_pink:
                            min_dist_to_pink = dist
                            best_quad = (lines[i], lines[j], lines[k], lines[l])

    if not best_quad:
        y_0 = int(y_pink.max() + 30)
        y_100 = int(y_0 - 170)
        y_66 = int(y_0 - 114)
        y_33 = int(y_0 - 57)
    else:
        y_100, y_66, y_33, y_0 = best_quad

    # 2. X Bounds of pink curve within the chart vertical range
    chart_pink = pink_mask[max(0, y_100 - 60):min(H, y_0 + 10), :]
    yp_idx, xp_idx = np.where(chart_pink)
    x_start = xp_idx.min()
    x_end = xp_idx.max()

    duration = float(manual_duration) if manual_duration and manual_duration > 0 else 29.0

    # 3. Extract curve points
    curve_x = []
    curve_pct = []
    scale = 100.0 / max(1, (y_0 - y_100))
    search_top = max(0, y_100 - 60)
    search_bot = min(H, y_0 + 10)

    for x in range(x_start, x_end + 1):
        col_score = pink_score[search_top:search_bot, x]
        best_idx = np.argmax(col_score)
        if col_score[best_idx] > 35:
            best_y = search_top + best_idx
            pct = (y_0 - best_y) * scale
            pct = max(0.0, pct)
            curve_x.append(x)
            curve_pct.append(pct)

    # Interpolate second-by-second
    total_sec = int(round(duration))
    time_per_px = duration / max(1, (x_end - x_start))
    seconds = np.arange(0, total_sec + 1)
    pts_sec = np.interp(
        seconds,
        [(x - curve_x[0]) * time_per_px for x in curve_x],
        curve_pct
    )

    sec_data = [
        {"second": int(s), "retention_pct": round(float(p), 1)}
        for s, p in zip(seconds, pts_sec)
    ]

    metrics = _compute_retention_metrics(sec_data, duration)

    # 4. Generate Debug Overlay
    debug_crop_y1 = max(0, y_100 - 50)
    debug_crop_y2 = min(H, y_0 + 35)
    debug_crop_x1 = max(0, x_start - 30)
    debug_crop_x2 = min(W, x_end + 30)

    debug_img = img.copy()
    cv2.line(debug_img, (0, y_100), (W, y_100), (255, 0, 180), 1)  # 100%
    cv2.line(debug_img, (0, y_66), (W, y_66), (200, 0, 200), 1)   # 66%
    cv2.line(debug_img, (0, y_33), (W, y_33), (160, 0, 160), 1)   # 33%
    cv2.line(debug_img, (0, y_0), (W, y_0), (180, 180, 180), 1)    # 0%

    for i in range(len(curve_x) - 1):
        pt1 = (curve_x[i], int(y_0 - (curve_pct[i] / 100.0) * (y_0 - y_100)))
        pt2 = (curve_x[i+1], int(y_0 - (curve_pct[i+1] / 100.0) * (y_0 - y_100)))
        cv2.line(debug_img, pt1, pt2, (0, 255, 0), 2)
    cv2.circle(debug_img, (curve_x[0], int(y_0 - (curve_pct[0] / 100.0) * (y_0 - y_100))), 5, (0, 0, 255), -1)
    cv2.circle(debug_img, (curve_x[-1], int(y_0 - (curve_pct[-1] / 100.0) * (y_0 - y_100))), 5, (0, 0, 255), -1)

    debug_crop = debug_img[debug_crop_y1:debug_crop_y2, debug_crop_x1:debug_crop_x2]

    return {
        "platform": "youtube",
        "platform_name": "YouTube Shorts",
        "detected": True,
        "duration_seconds": duration,
        "grid_lines": {"100%": y_100, "66%": y_66, "33%": y_33, "0%": y_0},
        "bounds": {"x_start": int(x_start), "x_end": int(x_end), "y_100": int(y_100), "y_0": int(y_0)},
        "retention_per_second": sec_data,
        "metrics": metrics,
        "debug_image": _image_to_base64(debug_crop),
    }


# =============================================================================
# 3. TIKTOK RETENTION ANALYZER
# =============================================================================

def analyze_tiktok_retention(
    image_input,
    manual_duration: Optional[float] = None
) -> Dict[str, Any]:
    """
    Extracts retention curve from TikTok Analytics screenshot.
    - Video player at top ends around Y ~ 520.
    - Chart sits right below it (Y ~ 540 to 620).
    - Grid lines: Dotted horizontal lines at 100%, 50%, and solid baseline at 0%.
    - Curve: Blue line at the upper crest of light blue gradient fill.
    - Scrubber: Filter out vertical line and circle scrubber handle.
    """
    img = _load_image(image_input)
    H, W, _ = img.shape
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 1. Detect video player bottom edge (first predominantly white row after y=300)
    row_white = np.sum(gray > 240, axis=1)
    white_after_video = np.where((np.arange(len(row_white)) > 300) & (row_white > W * 0.82))[0]
    y_video_bottom = int(white_after_video[0]) if len(white_after_video) > 0 else int(H * 0.51)

    # 2. Locate the chart baseline (0%) within 60 to 120 px below video player
    baseline_candidates = []
    for y in range(y_video_bottom + 55, min(H, y_video_bottom + 125)):
        row = gray[y, :]
        cnt = np.sum((row >= 200) & (row <= 245))
        baseline_candidates.append((y, cnt))

    baseline_candidates.sort(key=lambda x: x[1], reverse=True)
    y_0 = baseline_candidates[0][0] if baseline_candidates else int(y_video_bottom + 95)

    # Dotted line at 50% and 100% (~38 px spacing)
    spacing = 38
    y_50 = y_0 - spacing
    y_100 = y_0 - spacing * 2

    # 3. Extract Blue Curve Points
    search_top = max(0, y_100 - 10)
    search_bot = min(H, y_0 + 2)

    raw_xs = []
    raw_pcts = []

    for x in range(35, int(W * 0.85)):
        col = img[search_top:search_bot, x]
        # Metric for the blue curve: B - R contrast (must be blue, not grey axis text)
        diff = col[:, 0].astype(int) - col[:, 2].astype(int)
        dark = 255 - np.mean(col, axis=1)
        score = np.where(diff > 12, diff * 2 + dark, 0)
        best_idx = np.argmax(score)

        if score[best_idx] > 20:
            best_y = search_top + best_idx
            pct = 100.0 * (y_0 - best_y) / max(1, (y_0 - y_100))
            pct = max(0.0, min(100.0, pct))
            raw_xs.append(x)
            raw_pcts.append(pct)

    if not raw_xs:
        raise ValueError("No se pudo detectar la curva azul de TikTok Analytics.")

    # 4. Clean Scrubber Outliers with Rolling Median Filter
    clean_pcts = []
    window = 5
    for i in range(len(raw_pcts)):
        start_idx = max(0, i - window)
        end_idx = min(len(raw_pcts), i + window + 1)
        clean_pcts.append(statistics.median(raw_pcts[start_idx:end_idx]))

    x_start = raw_xs[0]
    x_end = raw_xs[-1]

    duration = float(manual_duration) if manual_duration and manual_duration > 0 else 31.0

    # Interpolate second-by-second
    total_sec = int(round(duration))
    time_per_px = duration / max(1, (x_end - x_start))
    seconds = np.arange(0, total_sec + 1)
    pts_sec = np.interp(
        seconds,
        [(x - x_start) * time_per_px for x in raw_xs],
        clean_pcts
    )

    sec_data = [
        {"second": int(s), "retention_pct": round(float(p), 1)}
        for s, p in zip(seconds, pts_sec)
    ]

    metrics = _compute_retention_metrics(sec_data, duration)

    # 5. Generate Debug Overlay
    debug_crop_y1 = max(0, y_100 - 25)
    debug_crop_y2 = min(H, y_0 + 35)
    debug_crop_x1 = max(0, x_start - 25)
    debug_crop_x2 = min(W, x_end + 45)

    debug_img = img.copy()
    cv2.line(debug_img, (0, y_100), (W, y_100), (0, 200, 255), 1)  # 100%
    cv2.line(debug_img, (0, y_50), (W, y_50), (0, 165, 255), 1)   # 50%
    cv2.line(debug_img, (0, y_0), (W, y_0), (180, 180, 180), 1)    # 0%

    for i in range(len(raw_xs) - 1):
        pt1 = (raw_xs[i], int(y_0 - (clean_pcts[i] / 100.0) * (y_0 - y_100)))
        pt2 = (raw_xs[i+1], int(y_0 - (clean_pcts[i+1] / 100.0) * (y_0 - y_100)))
        cv2.line(debug_img, pt1, pt2, (0, 255, 0), 2)
    cv2.circle(debug_img, (raw_xs[0], int(y_0 - (clean_pcts[0] / 100.0) * (y_0 - y_100))), 5, (0, 0, 255), -1)
    cv2.circle(debug_img, (raw_xs[-1], int(y_0 - (clean_pcts[-1] / 100.0) * (y_0 - y_100))), 5, (0, 0, 255), -1)

    debug_crop = debug_img[debug_crop_y1:debug_crop_y2, debug_crop_x1:debug_crop_x2]

    return {
        "platform": "tiktok",
        "platform_name": "TikTok Analytics",
        "detected": True,
        "duration_seconds": duration,
        "grid_lines": {"100%": y_100, "50%": y_50, "0%": y_0},
        "bounds": {"x_start": int(x_start), "x_end": int(x_end), "y_100": int(y_100), "y_0": int(y_0)},
        "retention_per_second": sec_data,
        "metrics": metrics,
        "debug_image": _image_to_base64(debug_crop),
    }


# =============================================================================
# 4. UNIFIED & MULTI-PLATFORM RETENTION ANALYZER
# =============================================================================

def auto_detect_platform_type(image_input) -> str:
    """Detect if an image is YouTube Shorts, TikTok, or Reels by visual characteristics."""
    img = _load_image(image_input)
    H, W, _ = img.shape

    # 1. Check for YouTube Magenta in the chart region (Y in [H*0.45, H*0.85])
    chart_crop = img[int(H * 0.45):int(H * 0.85), :]
    pink_score = (chart_crop[:, :, 2].astype(int) + chart_crop[:, :, 0].astype(int)) - 2 * chart_crop[:, :, 1].astype(int)
    pink_px = np.sum((pink_score > 60) & (chart_crop[:, :, 2] > 120) & (chart_crop[:, :, 0] > 80) & (chart_crop[:, :, 1] < 140))
    if pink_px > 100:
        return "youtube"

    # 2. Check for TikTok video preview player + blue curve in chart
    mid_crop = img[int(H * 0.45):int(H * 0.75), :]
    blue_diff = mid_crop[:, :, 0].astype(int) - mid_crop[:, :, 2].astype(int)
    blue_px = np.sum((blue_diff > 25) & (mid_crop[:, :, 0] > 150))
    if blue_px > 80:
        return "tiktok"

    # 3. Default to Reels (black curve on white background with 3 grid lines)
    return "reels"


def analyze_retention_screenshot(
    image_input,
    platform: str = "auto",
    manual_duration: Optional[float] = None
) -> Dict[str, Any]:
    """
    Main entry point for analyzing a single screenshot.
    Platform can be 'auto', 'reels', 'youtube', or 'tiktok'.
    """
    target_platform = platform.lower().strip()
    if target_platform == "auto":
        target_platform = auto_detect_platform_type(image_input)

    if target_platform in ("reels", "instagram"):
        return analyze_reels_retention(image_input, manual_duration)
    elif target_platform in ("youtube", "shorts", "yt"):
        return analyze_youtube_retention(image_input, manual_duration)
    elif target_platform in ("tiktok", "tt"):
        return analyze_tiktok_retention(image_input, manual_duration)
    else:
        raise ValueError(f"Plataforma desconocida: '{platform}'. Usa 'reels', 'youtube' o 'tiktok'.")


def combine_multi_platform_retention(
    results_by_platform: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Combines results from multiple platforms (e.g. YT + Reels, TT + Reels, all 3).
    Aligns timelines second-by-second and computes average curve and comparative metrics.
    """
    if not results_by_platform:
        return {}

    # Find max duration among platforms
    max_duration = 0
    for p, data in results_by_platform.items():
        dur = data.get("duration_seconds", 0)
        if dur > max_duration:
            max_duration = int(round(dur))

    # Align second-by-second
    comparison_table = []
    for s in range(max_duration + 1):
        row = {"second": s}
        active_vals = []

        for p_name, data in results_by_platform.items():
            pts = data.get("retention_per_second", [])
            val = None
            if s < len(pts):
                val = pts[s]["retention_pct"]
            elif len(pts) > 0:
                val = pts[-1]["retention_pct"]

            row[p_name] = val
            if val is not None:
                active_vals.append(val)

        row["average"] = round(float(np.mean(active_vals)), 1) if active_vals else None
        comparison_table.append(row)

    return {
        "max_duration_seconds": max_duration,
        "comparison_by_second": comparison_table,
        "platforms_included": list(results_by_platform.keys()),
    }
