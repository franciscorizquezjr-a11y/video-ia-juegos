"""
Setup Trailer Video Engine
Composes a physical monitor setup video with:
- Looped 9:16 background video (e.g. setup-background-def.mp4)
- Quad-warped trailer footage mapped onto calibrated monitor corners
- Precise polygon alpha mask eliminating border stretch/clamping artifacts
- Optional angled game title overlay with shadow and glow
- Audio exclusively from the trailer
- Hardware-accelerated rendering (NVENC with libx264 fallback)
- Video duration matching trailer duration exactly
"""

import os
import math
import uuid
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont

from fonts import load_font

logger = logging.getLogger(__name__)

def create_monitor_polygon_mask(
    output_path: Path,
    corners: Dict[str, Dict[str, float]],
    canvas_w: int = 1080,
    canvas_h: int = 1920,
    native_w: int = 720,
    native_h: int = 1280
) -> Path:
    """
    Creates a 1-bit / grayscale mask matching the quadrilateral of the calibrated monitor corners.
    White (255) inside the monitor quad, black (0) outside.
    """
    scale_x = canvas_w / native_w
    scale_y = canvas_h / native_h

    tl_x = corners["tl"]["x"] * scale_x
    tl_y = corners["tl"]["y"] * scale_y
    tr_x = corners["tr"]["x"] * scale_x
    tr_y = corners["tr"]["y"] * scale_y
    br_x = corners["br"]["x"] * scale_x
    br_y = corners["br"]["y"] * scale_y
    bl_x = corners["bl"]["x"] * scale_x
    bl_y = corners["bl"]["y"] * scale_y

    mask = Image.new("L", (canvas_w, canvas_h), 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon([(tl_x, tl_y), (tr_x, tr_y), (br_x, br_y), (bl_x, bl_y)], fill=255)
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mask.save(output_path, "PNG")
    return output_path


def render_angled_title(
    text: str,
    output_path: Path,
    corners: Dict[str, Dict[str, float]],
    canvas_w: int = 1080,
    canvas_h: int = 1920,
    native_w: int = 720,
    native_h: int = 1280,
    base_font_size: int = 28,
    gap_native: int = 18
) -> Optional[Path]:
    """
    Renders the game title text rotated and anchored along the top bezel of the monitor
    with text-shadow and cyan glow, matching the frontend CSS styling.
    """
    clean_text = (text or "").strip()
    if not clean_text:
        return None

    scale = canvas_w / native_w  # 1.5

    # Corners in native space
    tl = corners["tl"]
    tr = corners["tr"]

    dx = tr["x"] - tl["x"]
    dy = tr["y"] - tl["y"]
    angle_rad = math.atan2(dy, dx)
    angle_deg = math.degrees(angle_rad)

    mid_x = (tl["x"] + tr["x"]) / 2.0
    mid_y = (tl["y"] + tr["y"]) / 2.0

    # Perpendicular unit vector pointing UP from top monitor edge: (sin(angle), -cos(angle))
    anchor_native_x = mid_x + gap_native * math.sin(angle_rad)
    anchor_native_y = mid_y - gap_native * math.cos(angle_rad)

    anchor_canvas_x = anchor_native_x * scale
    anchor_canvas_y = anchor_native_y * scale

    font_size_canvas = int(round(base_font_size * scale))

    # Font selection - prefer heavy sans-serif fonts (%WINDIR%/Fonts first,
    # then Linux/macOS folders, DejaVu Sans Bold and PIL's default font).
    font = load_font(["ariblk.ttf", "impact.ttf", "segoeuib.ttf", "arialbd.ttf"],
                     font_size_canvas, bold=True)

    # Wrap text if wider than 85% of canvas width
    max_w = int(canvas_w * 0.85)
    words = clean_text.upper().split()
    lines = []
    current_line = []

    dummy_img = Image.new("RGBA", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy_img)

    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = dummy_draw.textbbox((0, 0), test_line, font=font)
        if (bbox[2] - bbox[0]) <= max_w:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
                current_line = [word]
            else:
                lines.append(word)
                current_line = []
    if current_line:
        lines.append(" ".join(current_line))
    if not lines:
        lines = [clean_text.upper()]

    line_spacing = int(font_size_canvas * 0.2)
    line_bboxes = [dummy_draw.textbbox((0, 0), l, font=font) for l in lines]
    line_widths = [b[2] - b[0] for b in line_bboxes]
    line_heights = [b[3] - b[1] for b in line_bboxes]

    pad = 30
    block_w = max(line_widths) + pad * 2
    block_h = sum(line_heights) + (len(lines) - 1) * line_spacing + pad * 2

    block_img = Image.new("RGBA", (block_w, block_h), (0, 0, 0, 0))
    block_draw = ImageDraw.Draw(block_img)

    cur_y = pad
    for idx, line in enumerate(lines):
        lw = line_widths[idx]
        cur_x = (block_w - lw) // 2

        # 1. Heavy drop shadow / outline (black)
        for ox, oy in [(-2, -2), (2, -2), (-2, 2), (2, 2), (0, 3), (0, 5), (3, 3), (-3, 3), (0, -2), (0, 2), (-2, 0), (2, 0)]:
            block_draw.text((cur_x + ox, cur_y + oy), line, font=font, fill=(0, 0, 0, 240))

        # 2. Cyan atmospheric glow
        for ox, oy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            block_draw.text((cur_x + ox, cur_y + oy), line, font=font, fill=(56, 189, 248, 160))

        # 3. Main crisp white text
        block_draw.text((cur_x, cur_y), line, font=font, fill=(255, 255, 255, 255))
        cur_y += line_heights[idx] + line_spacing

    # Rotate block counter-clockwise by angle_deg (matches screen clockwise rotation of angle_deg)
    rotated_block = block_img.rotate(-angle_deg, resample=Image.Resampling.BICUBIC, expand=True)
    rot_w, rot_h = rotated_block.size

    # Anchor bottom-center math
    dy_c = (block_h / 2.0) - pad
    center_x = anchor_canvas_x + dy_c * math.sin(angle_rad)
    center_y = anchor_canvas_y - dy_c * math.cos(angle_rad)

    paste_x = int(round(center_x - rot_w / 2.0))
    paste_y = int(round(center_y - rot_h / 2.0))

    final_canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    final_canvas.paste(rotated_block, (paste_x, paste_y), rotated_block)
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    final_canvas.save(output_path, "PNG")
    return output_path


def render_setup_trailer_video(
    background_path: Path,
    trailer_path: Path,
    corners: Dict[str, Dict[str, float]],
    output_path: Path,
    game_name: Optional[str] = None,
    show_game_title: bool = True,
    font_size: int = 28,
    title_offset_y: int = 18,
    canvas_w: int = 1080,
    canvas_h: int = 1920,
    native_w: int = 720,
    native_h: int = 1280,
    ffmpeg_bin: str = "ffmpeg",
    variant: str = "classic",
    gameplay_fit: str = "contain",
    card_text: str = "",
    card_style: Optional[Dict[str, Any]] = None,
    intro_seconds: Optional[float] = None,
    transition_seconds: float = 3.0,
    entry_sfx_enabled: bool = False,
    entry_sfx_volume: float = .25,
    highlight_hook: bool = False,
    opening_mode: str = "original",
    opening_start_seconds: float = 0,
    title_reveal_mode: str = "immediate",
    title_reveal_delay: float = 1,
    entrance_mode: str = "desktop",
    action_lead_seconds: float = 1.2,
    game_logo_path: Optional[Path] = None,
    game_logo_scale: float = 1.0,
    target_duration: Optional[float] = None
) -> Path:
    """
    Renders the composite video in 1080x1920:
    1. Generates the exact polygon mask.
    2. Generates the angled title overlay if enabled.
    3. Executes FFmpeg with hardware acceleration (NVENC or libx264).
    4. Duration matches trailer duration exactly.
    5. Preserves trailer audio, optionally mixing an entry whoosh for the camera variant.
    """
    if variant not in {"classic", "monitor_dive"}:
        raise ValueError("Variante de Set-up Trailer desconocida.")
    if entrance_mode not in {"desktop", "action_match"}:
        raise ValueError("Entrada de formato desconocida.")
    if variant != "monitor_dive":
        entrance_mode = "desktop"
    if gameplay_fit not in {"contain", "cover"}:
        raise ValueError("Encuadre de gameplay desconocido.")
    if not background_path.exists():
        raise FileNotFoundError(f"No se encontró el vídeo de fondo: {background_path}")
    if not trailer_path.exists():
        raise FileNotFoundError(f"No se encontró el vídeo del tráiler: {trailer_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = output_path.parent / "temp_assets"
    temp_dir.mkdir(parents=True, exist_ok=True)

    uid = uuid.uuid4().hex[:8]
    from setup_trailer_hooks import prepare_opening, title_reveal_time
    from setup_trailer_motion import probe_trailer, motion_timing, monitor_timeline
    original_trailer = trailer_path
    prepared_path = temp_dir / f"opening_{uid}.mp4"
    trailer_path, actual_start = prepare_opening(trailer_path, prepared_path, opening_mode, opening_start_seconds, ffmpeg_bin)
    fitted_path = None
    if target_duration is not None:
        from setup_trailer_narration import trim_trailer_duration
        fitted_path = temp_dir / f"narration_fit_{uid}.mp4"
        trailer_path = trim_trailer_duration(trailer_path,fitted_path,float(target_duration),ffmpeg_bin)
    trailer_duration, _, _ = probe_trailer(trailer_path, ffmpeg_bin)
    reveal_time = title_reveal_time(trailer_duration,variant,title_reveal_mode,card_text,intro_seconds,transition_seconds,title_reveal_delay,entrance_mode,action_lead_seconds)
    timeline=monitor_timeline(trailer_duration,card_text,intro_seconds,transition_seconds,entrance_mode,action_lead_seconds)
    from setup_trailer_logo import valid_logo, build_logo_overlay
    logo_path = Path(game_logo_path) if game_logo_path else None
    if logo_path and not valid_logo(logo_path):
        logo_path = None
    use_large_logo = bool(logo_path and show_game_title and (variant == "monitor_dive" or not card_text.strip()))
    big_logo_start = max(reveal_time,timeline["gameplay_start"]) if variant == "monitor_dive" else reveal_time
    hide_card_at = max(reveal_time,timeline["return_start"])
    large_logo_path = None
    flat_title_path = None
    mask_path = temp_dir / f"mask_{uid}.png"
    create_monitor_polygon_mask(
        output_path=mask_path,
        corners=corners,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        native_w=native_w,
        native_h=native_h
    )

    title_path = None
    if show_game_title and game_name and game_name.strip():
        t_path = temp_dir / f"title_{uid}.png"
        title_path = render_angled_title(
            text=game_name,
            output_path=t_path,
            corners=corners,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            native_w=native_w,
            native_h=native_h,
            base_font_size=font_size,
            gap_native=title_offset_y
        )
        if logo_path:
            title_path = build_logo_overlay(logo_path,t_path,corners,canvas_w,canvas_h,native_w,native_h,
                                           gap=title_offset_y,font_size=font_size)


    # Scale corners to canvas resolution
    scale_x = canvas_w / native_w
    scale_y = canvas_h / native_h

    tl_x = corners["tl"]["x"] * scale_x
    tl_y = corners["tl"]["y"] * scale_y
    tr_x = corners["tr"]["x"] * scale_x
    tr_y = corners["tr"]["y"] * scale_y
    br_x = corners["br"]["x"] * scale_x
    br_y = corners["br"]["y"] * scale_y
    bl_x = corners["bl"]["x"] * scale_x
    bl_y = corners["bl"]["y"] * scale_y

    title_enabled = f"gte(t,{reveal_time:.6f})"
    if use_large_logo:
        title_enabled += f"*lt(t,{big_logo_start:.6f})"

    # Build filtergraph
    # [1:v] is trailer -> scale to destination resolution, perspective warp, alphamerge with mask
    # [0:v] is background -> overlay warped monitor with shortest=1 so it terminates when trailer ends
    # [composed] + optional title overlay
    if title_path and title_path.exists():
        filtergraph = (
            f"[1:v]scale={canvas_w}:{canvas_h},format=yuva420p,"
            f"perspective=x0={tl_x:.2f}:y0={tl_y:.2f}:x1={tr_x:.2f}:y1={tr_y:.2f}:"
            f"x2={bl_x:.2f}:y2={bl_y:.2f}:x3={br_x:.2f}:y3={br_y:.2f}:"
            f"sense=destination:interpolation=cubic[warped];"
            f"[warped][2:v]alphamerge[masked_warped];"
            f"[0:v][masked_warped]overlay=0:0:shortest=1[composed];"
            f"[composed][3:v]overlay=0:0:enable='{title_enabled}'[outv]"
        )
        inputs = [
            "-stream_loop", "-1", "-i", str(background_path),
            "-i", str(trailer_path),
            "-i", str(mask_path),
            "-i", str(title_path)
        ]
    else:
        filtergraph = (
            f"[1:v]scale={canvas_w}:{canvas_h},format=yuva420p,"
            f"perspective=x0={tl_x:.2f}:y0={tl_y:.2f}:x1={tr_x:.2f}:y1={tr_y:.2f}:"
            f"x2={bl_x:.2f}:y2={bl_y:.2f}:x3={br_x:.2f}:y3={br_y:.2f}:"
            f"sense=destination:interpolation=cubic[warped];"
            f"[warped][2:v]alphamerge[masked_warped];"
            f"[0:v][masked_warped]overlay=0:0:shortest=1[outv]"
        )
        inputs = [
            "-stream_loop", "-1", "-i", str(background_path),
            "-i", str(trailer_path),
            "-i", str(mask_path)
        ]

    card_path = None
    if card_text.strip():
        from utils import create_text_card
        style = card_style or {}
        card_path = temp_dir / f"card_{uid}.png"
        cw, ch = int(canvas_w * .88), int(canvas_h * .28125)
        screen_top = min(p["y"] for p in corners.values()) * canvas_h / native_h
        screen_bottom = max(p["y"] for p in corners.values()) * canvas_h / native_h
        margin, title_gap = canvas_h * .04, canvas_h * .035
        above = max(0, screen_top - title_gap - margin)
        below = max(0, canvas_h - screen_bottom - margin * 2)
        if above >= ch * .6 or above >= below:
            ch = max(1, min(ch, int(above)))
            card_y = max(int(margin), int(screen_top - title_gap - ch))
        else:
            ch = max(1, min(ch, int(below)))
            card_y = int(screen_bottom + margin)
        if ch < canvas_h * .08:
            raise ValueError("El fondo no deja espacio para la tarjeta fuera del monitor. Desactívala o usa un fondo con más espacio libre.")
        body_size = max(14, int(cw * .04))
        # Account for create_text_card's wrapping and long-text font reduction.
        def line_count(font):
            lines, current = 1, ""
            for word in card_text.split():
                trial = (current + " " + word).strip()
                if font.getlength(trial) > cw * .94 and current:
                    lines += 1
                    current = word
                else:
                    current = trial
            return lines
        # Measure with the same font create_text_card resolves for its bold
        # "Segoe UI" body text, on any OS.
        while body_size > 12:
            font = load_font(["segoeuib.ttf"], body_size, bold=True)
            lines = line_count(font)
            if lines > 5:
                lines = line_count(load_font(["segoeuib.ttf"], max(1, int(body_size*.8)), bold=True))
            if lines * int(body_size*1.3) <= ch - int(cw*.145):
                break
            body_size -= 1
        create_text_card(card_text, card_path,
            username=style.get("card_username", ""), handle=style.get("card_handle", ""),
            width=cw, height=ch, bg_color=style.get("setup_card_bg_color", "#332052"),
            text_color=style.get("card_text_color", "#ffffff"), border_color=style.get("card_border_color", "#a78bfa"),
            border_width=max(2,int(cw*.005)), radius=int(cw*.037),
            avatar_text=style.get("card_avatar_text", ""), avatar_bg=style.get("card_avatar_bg", "#a78bfa"),
            avatar_image_path=style.get("card_avatar_path"), font_size_body=body_size,
            avatar_radius=max(8,int(cw*.044)), font_size_username=max(10,int(cw*.04)), font_size_handle=max(8,int(cw*.027)), highlight_first_sentence=highlight_hook)
        card_index = 4 if title_path else 3
        inputs.extend(["-i", str(card_path)])
        card_enable = f":enable='lt(t,{hide_card_at:.6f})'" if use_large_logo else ""
        filtergraph = filtergraph.replace("[outv]", "[scene]") + f";[scene][{card_index}:v]overlay=(W-w)/2:{card_y}{card_enable}[outv]"

    # Normalize the background to the calibrated output canvas for any source size.
    filtergraph = f"[0:v]scale={canvas_w}:{canvas_h},setsar=1[background];" + filtergraph.replace("[0:v][masked_warped]", "[background][masked_warped]")
    audio_map = "1:a?"
    if variant == "monitor_dive":
        from setup_trailer_motion import probe_trailer, build_monitor_motion_filter, motion_timing, build_entry_audio_filter
        duration, aspect, has_audio = probe_trailer(trailer_path, ffmpeg_bin)
        filtergraph = filtergraph.replace("[1:v]scale=", "[monitor_src]scale=")
        filtergraph = filtergraph.replace("[background][masked_warped]", "[background_fps][masked_warped]")
        filtergraph = filtergraph.replace("[outv]", "[setup_complete]")
        split = "split=3[monitor_src][hero_src][opening_src]" if entrance_mode == "action_match" else "split=2[monitor_src][hero_src]"
        filtergraph = (f"[1:v]fps=30,setpts=PTS-STARTPTS,{split};" +
                       filtergraph.replace("setsar=1[background];", "setsar=1,fps=30,setpts=PTS-STARTPTS[background_fps];"))
        filtergraph += ";" + build_monitor_motion_filter(duration, aspect, corners, canvas_w, canvas_h, native_w, native_h, gameplay_fit, text=card_text, intro_seconds=intro_seconds, transition_seconds=transition_seconds, entrance_mode=entrance_mode, action_lead_seconds=action_lead_seconds)
        if entry_sfx_enabled and entry_sfx_volume > 0:
            intro, entry = timeline["push_start"], timeline["entry"]
            filtergraph += ";" + build_entry_audio_filter(duration, intro, entry, has_audio, entry_sfx_volume)
            audio_map = "[outa]"

    # A readable flat reveal over gameplay, followed by the angled title on the desk.
    if title_path and not logo_path and title_reveal_mode == "after_entry" and variant == "monitor_dive":
        flat_title_path = temp_dir / f"reveal_{uid}.png"
        flat_corners = {"tl":{"x":native_w*.1,"y":native_h*.15},"tr":{"x":native_w*.9,"y":native_h*.15}}
        render_angled_title(game_name,flat_title_path,flat_corners,canvas_w,canvas_h,native_w,native_h,font_size,0)
        title_index = inputs.count("-i")
        inputs.extend(["-loop","1","-framerate","30","-i",str(flat_title_path)])
        return_start = timeline["return_start"]
        filtergraph = filtergraph.replace("[outv]","[before_reveal]")
        filtergraph += (f";[{title_index}:v]format=rgba,fade=t=in:st={reveal_time:.6f}:d=0.25:alpha=1[reveal];"
                        f"[before_reveal][reveal]overlay=0:0:shortest=1:enable='between(t,{reveal_time:.6f},{return_start:.6f})'[outv]")

    if use_large_logo:
        large_logo_path = temp_dir / f"game_logo_{uid}.png"
        build_logo_overlay(logo_path,large_logo_path,corners,canvas_w,canvas_h,native_w,native_h,large=True,scale=game_logo_scale)
        logo_index=inputs.count("-i")
        inputs.extend(["-loop","1","-framerate","30","-i",str(large_logo_path)])
        filtergraph=filtergraph.replace("[outv]","[before_logo]")
        filtergraph+=(f";[{logo_index}:v]format=rgba,fade=t=in:st={big_logo_start:.6f}:d=0.3:alpha=1[game_logo];"
                      f"[before_logo][game_logo]overlay=0:0:shortest=1:enable='gte(t,{big_logo_start:.6f})'[outv]")

    # Try NVENC first, fall back to libx264
    encoders_to_try = [
        ["-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "14M", "-pix_fmt", "yuv420p"],
        ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
    ]

    last_error = ""
    for enc_opts in encoders_to_try:
        cmd = [
            ffmpeg_bin,
            *inputs,
            "-filter_complex", filtergraph,
            "-map", "[outv]",
            "-map", audio_map,  # Original trailer, with optional locally generated entry whoosh
            *enc_opts,
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            "-y", str(output_path)
        ]
        
        logger.info(f"Rendering setup trailer video with encoder {enc_opts[1]}: {' '.join(cmd)}")
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            logger.info(f"Render complete: {output_path} ({output_path.stat().st_size / (1024*1024):.2f} MB)")
            import json
            output_path.with_suffix(".settings.json").write_text(json.dumps({
                "source": str(original_trailer), "opening_mode": opening_mode,
                "opening_start_seconds_actual": actual_start,
                "title_reveal_mode": title_reveal_mode,
                "title_reveal_seconds_actual": reveal_time,
                "highlight_hook": highlight_hook,
                "game_logo_used": bool(logo_path and show_game_title),
                "game_logo_path": str(logo_path) if logo_path else None,
                "entrance_mode": entrance_mode,
                "export_duration": trailer_duration,
                "card_enabled": bool(card_text.strip()),
                "entrance_timeline": timeline
            }, indent=2), encoding="utf-8")
            # Clean up temp assets
            try:
                if large_logo_path and large_logo_path.exists():
                    large_logo_path.unlink()
                if fitted_path and fitted_path.exists():
                    fitted_path.unlink()
                if prepared_path.exists():
                    prepared_path.unlink()
                if flat_title_path and flat_title_path.exists():
                    flat_title_path.unlink()
                if card_path and card_path.exists():
                    card_path.unlink()
                if mask_path.exists():
                    mask_path.unlink()
                if title_path and title_path.exists():
                    title_path.unlink()
            except Exception:
                pass
            return output_path
        else:
            last_error = res.stderr
            logger.warning(f"Encoder {enc_opts[1]} failed: {last_error[:300]}")

    raise RuntimeError(f"FFmpeg error al renderizar vídeo: {last_error}")
