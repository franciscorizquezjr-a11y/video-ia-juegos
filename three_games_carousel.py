"""
three_games_carousel.py — 3 Games Showcase Carousel (Stickers & Collage 4:5 Format)

Generates 5-slide high-energy Instagram/TikTok carousels (1080x1350 px)
featuring 3 games with cut-out character stickers, angled gameplay screenshots,
torn paper accents, yellow brush strokes, and bold typography.
"""

import os
import re
import json
import time
import shutil
import base64
import logging
import zipfile
import subprocess
from project_paths import WORK_OUTPUT_DIR, output_url
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import requests
from PIL import Image, ImageOps
import numpy as np
import cv2
import rembg

logger = logging.getLogger("three_games_carousel")
BASE_DIR = Path(__file__).resolve().parent

CACHE_DIR = BASE_DIR / "static" / "carousel_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR = BASE_DIR / "static" / "carousel_assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR = WORK_OUTPUT_DIR / "three_games_carousels"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Shared rembg session (loads model once)
_REMBG_SESSION = None

def get_rembg_session():
    global _REMBG_SESSION
    if _REMBG_SESSION is None:
        try:
            _REMBG_SESSION = rembg.new_session("u2net")
        except Exception as e:
            logger.warning(f"Could not initialize u2net session: {e}")
            _REMBG_SESSION = None
    return _REMBG_SESSION


# ==============================================================================
# STEAM DATA EXTRACTION
# ==============================================================================

def parse_steam_app_id(url_or_term: str) -> Optional[int]:
    """Extract app_id from Steam URL or return integer if direct ID."""
    if not url_or_term:
        return None
    url_or_term = str(url_or_term).strip()
    if url_or_term.isdigit():
        return int(url_or_term)
    match = re.search(r"/app/(\d+)", url_or_term)
    if match:
        return int(match.group(1))
    try:
        search_url = "https://store.steampowered.com/api/storesearch/"
        params = {"term": url_or_term, "l": "spanish", "cc": "ES"}
        resp = requests.get(search_url, params=params, timeout=6)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            if items and items[0].get("id"):
                return int(items[0]["id"])
    except Exception as e:
        logger.warning(f"Error searching steam app for '{url_or_term}': {e}")
    return None

def fetch_steam_game_full(url_or_id: str) -> Optional[Dict[str, Any]]:
    """Fetch complete game assets, screenshots, and metadata from Steam."""
    app_id = parse_steam_app_id(url_or_id)
    if not app_id:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    details_url = "https://store.steampowered.com/api/appdetails"
    params = {"appids": str(app_id), "cc": "ES", "l": "spanish"}

    try:
        resp = requests.get(details_url, params=params, headers=headers, timeout=12)
        resp.raise_for_status()
        raw_data = resp.json()
        app_entry = raw_data.get(str(app_id)) or (next(iter(raw_data.values()), {}) if raw_data else {})
        if not app_entry.get("success"):
            return None

        data = app_entry.get("data", {})
        name = data.get("name", f"Steam App {app_id}")
        short_desc = data.get("short_description", "")
        clean_desc = re.sub(r"<[^>]+>", "", short_desc).strip()

        # Genre tags
        genres = [g.get("description", "") for g in data.get("genres", [])]
        categories = [c.get("description", "") for c in data.get("categories", [])]

        # Official Logo transparent PNG
        logo_url = f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/logo.png"

        # Asset candidates for character extraction
        library_capsule = f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/library_600x900.jpg"
        header_image = data.get("header_image") or f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/header.jpg"
        hero_capsule = f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/library_hero.jpg"

        # Screenshots
        raw_screenshots = data.get("screenshots", [])
        screenshots = []
        for idx, s in enumerate(raw_screenshots):
            full = s.get("path_full") or s.get("path_thumbnail")
            thumb = s.get("path_thumbnail") or full
            if full:
                screenshots.append({
                    "id": idx,
                    "full": full,
                    "thumb": thumb
                })

        return {
            "app_id": app_id,
            "name": name,
            "store_url": f"https://store.steampowered.com/app/{app_id}",
            "description": clean_desc,
            "genres": genres,
            "categories": categories,
            "logo_url": logo_url,
            "library_capsule": library_capsule,
            "header_image": header_image,
            "hero_capsule": hero_capsule,
            "screenshots": screenshots
        }
    except Exception as e:
        logger.error(f"Error fetching steam game {app_id}: {e}")
        return None


# ==============================================================================
# CHARACTER CUTOUT & STICKER OUTLINE GENERATION
# ==============================================================================

def download_image_to_file(url: str, dest_path: Path) -> bool:
    """Download image to disk if not already cached."""
    if dest_path.exists() and dest_path.stat().st_size > 1000:
        return True
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code == 200:
            with open(dest_path, "wb") as f:
                f.write(r.content)
            return True
    except Exception as e:
        logger.warning(f"Failed downloading {url} to {dest_path}: {e}")
    return False

def create_sticker_from_image(
    input_path: Path,
    output_path: Path,
    outline_width: int = 18,
    outline_color: Tuple[int, int, int] = (255, 255, 255),
    max_dimension: int = 900
) -> bool:
    """
    Remove background and add an organic white sticker border using morphology dilation.
    """
    if output_path.exists() and output_path.stat().st_size > 1000:
        return True

    try:
        img = Image.open(input_path).convert("RGBA")
        # Scale if larger than max_dimension
        if max(img.size) > max_dimension:
            img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

        session = get_rembg_session()
        cutout = rembg.remove(img, session=session) if session else rembg.remove(img)

        # Extract alpha mask
        alpha = np.array(cutout.split()[-1])
        if np.max(alpha) < 30:
            # Empty cutout, return original
            return False

        # Dilate mask for sticker outline
        k_size = outline_width * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_size, k_size))
        dilated_alpha = cv2.dilate(alpha, kernel, iterations=1)

        # Build white outline layer
        h, w = alpha.shape
        white_pixels = np.zeros((h, w, 4), dtype=np.uint8)
        white_mask = dilated_alpha > 25
        white_pixels[white_mask] = [*outline_color, 255]

        # Blend smooth anti-aliased edge
        blur_kernel = (5, 5)
        blurred_outline = cv2.GaussianBlur(white_pixels[:, :, 3], blur_kernel, 0)
        white_pixels[:, :, 3] = np.where(dilated_alpha > 80, 255, blurred_outline)

        outline_img = Image.fromarray(white_pixels, "RGBA")
        final_sticker = Image.alpha_composite(outline_img, cutout)

        # Crop transparent margins with padding
        bbox = final_sticker.getbbox()
        if bbox:
            pad = 10
            crop_box = (
                max(0, bbox[0] - pad),
                max(0, bbox[1] - pad),
                min(final_sticker.width, bbox[2] + pad),
                min(final_sticker.height, bbox[3] + pad)
            )
            final_sticker = final_sticker.crop(crop_box)

        final_sticker.save(output_path, "PNG", optimize=True)
        return True
    except Exception as e:
        logger.error(f"Error creating sticker from {input_path}: {e}")
        return False

def generate_stickers_for_game(game_data: Dict[str, Any]) -> List[str]:
    """
    Generate sticker options for a game from its library capsule, header, and top screenshot.
    Returns relative web URLs (e.g., '/static/carousel_cache/...').
    """
    app_id = game_data["app_id"]
    sticker_urls = []

    candidates = [
        ("capsule", game_data.get("library_capsule")),
        ("hero", game_data.get("hero_capsule")),
        ("header", game_data.get("header_image")),
    ]
    # Add top 2 screenshots
    screenshots = game_data.get("screenshots", [])
    if screenshots:
        candidates.append(("screen0", screenshots[0].get("full")))
    if len(screenshots) > 1:
        candidates.append(("screen1", screenshots[1].get("full")))

    for tag, url in candidates:
        if not url:
            continue
        raw_file = CACHE_DIR / f"{app_id}_{tag}_raw.jpg"
        sticker_file = CACHE_DIR / f"{app_id}_{tag}_sticker.png"

        if not sticker_file.exists():
            if download_image_to_file(url, raw_file):
                create_sticker_from_image(raw_file, sticker_file, outline_width=18)

        if sticker_file.exists() and sticker_file.stat().st_size > 1000:
            sticker_urls.append(f"/static/carousel_cache/{sticker_file.name}")

    return sticker_urls


# ==============================================================================
# HOOK & TEXT GENERATION
# ==============================================================================

def generate_game_copy(game_data: Dict[str, Any], index: int) -> Dict[str, Any]:
    """
    Create catchy hooks, 3 informal bullet points, and tags for a game slide.
    """
    name = game_data.get("name", "Game")
    desc = game_data.get("description", "")
    genres = [g.lower() for g in game_data.get("genres", [])]

    # Hook question / challenge
    hook_proposals = [
        f"WHAT IF YOUR FRIENDS PLAYED {name.upper()}?",
        f"YOUR JOB IS SIMPLE: SURVIVE THE CHAOS.",
        f"THE ULTIMATE TEST FOR YOUR FRIEND GROUP.",
        f"HOW FAST CAN YOUR FRIENDS RUIN THIS?",
        f"TRUST NO ONE. ESPECIALLY YOUR FRIENDS."
    ]
    hook = hook_proposals[(index - 1) % len(hook_proposals)]

    # Bullets
    bullets = [
        "Jump in with your best friends.",
        "Simple rules, complete escalating chaos.",
        "Basically, you decide who gets thrown out first."
    ]

    if "co-op" in " ".join(genres) or "multijugador" in " ".join(genres) or "multiplayer" in " ".join(genres):
        bullets[0] = "Designed specifically for friend groups."
        bullets[2] = "Get ready for screaming and laughing in Discord."
    elif "survival" in " ".join(genres) or "supervivencia" in " ".join(genres):
        bullets[1] = "Keep everyone alive while everything falls apart."

    tag = "➔ PERFECT FOR: FRIEND GROUPS + PURE CHAOS"
    if "terror" in " ".join(genres) or "horror" in " ".join(genres):
        tag = "➔ PERFECT FOR: GAME NIGHT JUMPSCARES"
    elif "estrategia" in " ".join(genres) or "simulador" in " ".join(genres):
        tag = "➔ PERFECT FOR: CHAOTIC MANAGEMENT"

    return {
        "badge_num": f"GAME #{index}",
        "hook_title": hook,
        "bullets": bullets,
        "tag": tag,
        "summary_label": f"{name} — Pure Chaos"
    }


# ==============================================================================
# HTML BUILDER (1080 x 1350 px, Instagram 4:5 format)
# ==============================================================================

SHARED_COLLAGE_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Anton&family=Inter:wght@400;600;700;800;900&family=Montserrat:wght@700;800;900&display=swap');

* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

body {
    width: 1080px;
    height: 1350px;
    overflow: hidden;
    background-color: #F8F9FA;
    color: #111827;
    font-family: 'Inter', -apple-system, sans-serif;
    -webkit-font-smoothing: antialiased;
}

.slide-canvas {
    width: 1080px;
    height: 1350px;
    position: relative;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    background-color: #F4F4F6;
    background-image: 
        radial-gradient(#E2E8F0 1.2px, transparent 1.2px),
        radial-gradient(#E2E8F0 1.2px, #F4F4F6 1.2px);
    background-size: 32px 32px;
    background-position: 0 0, 16px 16px;
}

/* Yellow brush highlight container */
.brush-highlight {
    position: relative;
    display: inline-block;
    z-index: 2;
}

.brush-highlight-bg {
    position: absolute;
    inset: -6px -16px -8px -16px;
    z-index: -1;
    width: calc(100% + 32px);
    height: calc(100% + 14px);
    pointer-events: none;
}

/* Sticker Character Outline & Shadow */
.sticker-img {
    filter: drop-shadow(0 14px 28px rgba(0,0,0,0.35)) drop-shadow(0 4px 10px rgba(0,0,0,0.2));
    transition: transform 0.2s ease;
    user-select: none;
}

/* Angled polaroid / polygonal screenshot cuts */
.slanted-screenshot {
    position: relative;
    border: 7px solid #FFFFFF;
    border-radius: 14px;
    box-shadow: 0 16px 36px rgba(0,0,0,0.22), 0 4px 12px rgba(0,0,0,0.12);
    overflow: hidden;
    background: #000;
}

.slanted-screenshot img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}

/* Comic doodle burst */
.doodle-burst {
    position: absolute;
    width: 72px;
    height: 60px;
    pointer-events: none;
    z-index: 10;
}
"""

def build_cover_html(
    main_title_line1: str,
    main_title_highlight: str,
    main_title_line2: str,
    subtitle: str,
    stickers: List[str],  # 3 stickers: [game1_sticker, game2_sticker, game3_sticker]
    handle: str = "@indiegames"
) -> str:
    """Build Slide 1 (Cover / Hook with 3 overlapping stickers)."""
    s1 = stickers[0] if len(stickers) > 0 else ""
    s2 = stickers[1] if len(stickers) > 1 else ""
    s3 = stickers[2] if len(stickers) > 2 else ""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
{SHARED_COLLAGE_CSS}

.cover-header {{
    padding: 70px 70px 0 70px;
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    z-index: 10;
}}

.handle-pill {{
    font-size: 20px;
    font-weight: 700;
    color: #475569;
    margin-bottom: 24px;
    display: inline-flex;
    align-items: center;
    gap: 8px;
}}

.cover-title {{
    font-family: 'Anton', 'Montserrat', sans-serif;
    font-size: 88px;
    line-height: 0.95;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #0F172A;
    margin-bottom: 22px;
    position: relative;
}}

.cover-subtitle {{
    font-size: 26px;
    font-weight: 600;
    color: #475569;
    letter-spacing: -0.2px;
}}

.characters-stage {{
    position: absolute;
    bottom: -20px;
    left: 0;
    right: 0;
    height: 780px;
    z-index: 5;
    pointer-events: none;
}}

.char-back-left {{
    position: absolute;
    bottom: 160px;
    left: 30px;
    width: 480px;
    max-height: 540px;
    z-index: 6;
    transform: rotate(-4deg);
}}

.char-back-right {{
    position: absolute;
    bottom: 140px;
    right: 20px;
    width: 500px;
    max-height: 560px;
    z-index: 7;
    transform: rotate(3deg);
}}

.char-front-center {{
    position: absolute;
    bottom: 10px;
    left: 50%;
    transform: translateX(-50%);
    width: 620px;
    max-height: 580px;
    z-index: 12;
}}

.corner-splatter-1 {{
    position: absolute;
    top: 50px;
    left: 30px;
    width: 140px;
    opacity: 0.85;
    z-index: 1;
}}
.corner-splatter-2 {{
    position: absolute;
    bottom: 260px;
    right: 20px;
    width: 160px;
    opacity: 0.8;
    z-index: 4;
}}
</style>
</head>
<body>
<div class="slide-canvas">
    <img src="/static/carousel_assets/paint_splatter_1.svg" class="corner-splatter-1" alt="" />
    <img src="/static/carousel_assets/paint_splatter_2.svg" class="corner-splatter-2" alt="" />

    <!-- Top Text Header -->
    <div class="cover-header">
        <div class="handle-pill">
            <span>{handle}</span>
            <span style="color: #38bdf8;">✔</span>
        </div>
        
        <div class="cover-title">
            <img src="/static/carousel_assets/doodle_emphasis.svg" class="doodle-burst" style="top: -45px; left: 80px;" alt="" />
            <div>
                <span class="brush-highlight">
                    <img src="/static/carousel_assets/brush_yellow_block.svg" class="brush-highlight-bg" alt="" />
                    <span style="color: #0F172A;">{main_title_line1}</span>
                </span>
                <span style="color: #0F172A;"> {main_title_highlight}</span>
            </div>
            <div style="margin-top: 10px; color: #0F172A;">{main_title_line2}</div>
        </div>

        <div class="cover-subtitle">{subtitle}</div>
    </div>

    <!-- 3 Characters Triangle Composition -->
    <div class="characters-stage">
        <!-- Back Left Character (Game 1) -->
        {"<img src='" + s1 + "' class='sticker-img char-back-left' alt='' />" if s1 else ""}
        <!-- Back Right Character (Game 2) -->
        {"<img src='" + s2 + "' class='sticker-img char-back-right' alt='' />" if s2 else ""}
        <!-- Front Center Character (Game 3) -->
        {"<img src='" + s3 + "' class='sticker-img char-front-center' alt='' />" if s3 else ""}
    </div>
</div>
</body>
</html>"""

def build_game_slide_html(
    game_number: int,
    badge_label: str,
    hook_title: str,
    logo_url: str,
    bullets: List[str],
    tag: str,
    screenshot1: str,
    screenshot2: str,
    character_sticker: str,
    handle: str = "@indiegames"
) -> str:
    """Build Slide 2, 3, or 4 for an individual game."""
    bullet_items = "".join([f"<li style='margin-bottom: 12px;'>{b}</li>" for b in bullets])

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
{SHARED_COLLAGE_CSS}

.game-slide-header {{
    padding: 55px 70px 0 70px;
    z-index: 10;
}}

.top-bar-meta {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
}}

.game-badge-num {{
    font-family: 'Anton', sans-serif;
    font-size: 26px;
    color: #0F172A;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    text-transform: uppercase;
}}

.hook-headline {{
    font-family: 'Anton', sans-serif;
    font-size: 64px;
    line-height: 1.05;
    text-transform: uppercase;
    color: #0F172A;
    position: relative;
    margin-bottom: 22px;
}}

.game-logo-container {{
    margin-bottom: 22px;
    max-width: 440px;
    height: 110px;
    display: flex;
    align-items: center;
}}

.game-logo-container img {{
    max-width: 100%;
    max-height: 110px;
    object-fit: contain;
    filter: drop-shadow(0 6px 14px rgba(0,0,0,0.18));
}}

.pitch-content {{
    max-width: 480px;
    z-index: 12;
    position: relative;
}}

.pitch-bullets {{
    list-style: none;
    font-size: 22px;
    line-height: 1.35;
    color: #334155;
    font-weight: 600;
    margin-bottom: 26px;
}}

.pitch-tag-pill {{
    display: inline-flex;
    align-items: center;
    position: relative;
    font-family: 'Montserrat', sans-serif;
    font-weight: 900;
    font-size: 19px;
    letter-spacing: 0.3px;
    color: #0F172A;
    padding: 12px 24px;
    z-index: 2;
}}

/* Slanted Gameplay Screenshots Stage */
.screenshots-stage {{
    position: absolute;
    right: 50px;
    top: 240px;
    width: 520px;
    height: 800px;
    z-index: 4;
}}

.shot-top {{
    position: absolute;
    top: 40px;
    right: 10px;
    width: 480px;
    height: 310px;
    transform: rotate(4.5deg);
    z-index: 5;
}}

.shot-bottom {{
    position: absolute;
    top: 310px;
    left: -20px;
    width: 490px;
    height: 320px;
    transform: rotate(-3.5deg);
    z-index: 6;
}}

/* Character sticker pop-out overlapping screenshots */
.hero-character-sticker {{
    position: absolute;
    bottom: -15px;
    right: 40px;
    width: 580px;
    max-height: 590px;
    z-index: 15;
    transform: rotate(-1deg);
}}

.bg-brush-accent {{
    position: absolute;
    top: 140px;
    right: 30px;
    width: 320px;
    opacity: 0.85;
    z-index: 1;
}}
</style>
</head>
<body>
<div class="slide-canvas">
    <img src="/static/carousel_assets/paint_splatter_1.svg" class="bg-brush-accent" alt="" />

    <!-- Top Metadata Header -->
    <div class="game-slide-header">
        <div class="top-bar-meta">
            <span class="game-badge-num">{badge_label}</span>
            <span style="font-weight: 700; color: #475569; font-size: 19px;">{handle} <span style="color:#38bdf8;">✔</span></span>
        </div>

        <div class="hook-headline">
            <span class="brush-highlight">
                <img src="/static/carousel_assets/brush_yellow_slanted.svg" class="brush-highlight-bg" alt="" />
                <span>{hook_title}</span>
            </span>
        </div>

        <!-- Official Steam Logo -->
        <div class="game-logo-container">
            {"<img src='" + logo_url + "' alt='' />" if logo_url else f"<h2 style='font-family: Anton; font-size: 42px; color: #0F172A;'>{badge_label}</h2>"}
        </div>

        <!-- Pitch bullets & tag -->
        <div class="pitch-content">
            <ul class="pitch-bullets">
                {bullet_items}
            </ul>

            <div class="pitch-tag-pill">
                <img src="/static/carousel_assets/brush_yellow_tag.svg" class="brush-highlight-bg" alt="" />
                <span>{tag}</span>
            </div>
        </div>
    </div>

    <!-- Slanted Screenshots Composition -->
    <div class="screenshots-stage">
        {"<div class='slanted-screenshot shot-top'><img src='" + screenshot1 + "' alt='' /></div>" if screenshot1 else ""}
        {"<div class='slanted-screenshot shot-bottom'><img src='" + screenshot2 + "' alt='' /></div>" if screenshot2 else ""}
    </div>

    <!-- 3D Pop-Out Character Sticker -->
    {"<img src='" + character_sticker + "' class='sticker-img hero-character-sticker' alt='' />" if character_sticker else ""}
</div>
</body>
</html>"""

def build_outro_html(
    games: List[Dict[str, Any]],
    cta_title: str = "SO... WHICH ONE ARE YOU PLAYING FIRST? 👇",
    share_text: str = "Send this to the friend group you'd play these with.",
    save_text: str = "SAVE THIS FOR GAME NIGHT",
    handle: str = "@indiegames"
) -> str:
    """Build Slide 5 (Outro / Summary with 3 horizontal game cards & share callouts)."""
    cards_html = ""
    for g in games[:3]:
        name = g.get("name", "Game")
        summary = g.get("summary_label", f"{name} — Pure Chaos")
        # Use header or capsule as card background
        card_bg = g.get("header_image") or g.get("library_capsule") or ""
        logo = g.get("logo_url") or ""

        cards_html += f"""
        <div class="game-card-row">
            <div class="card-bg-wrap">
                {"<img src='" + card_bg + "' class='card-bg-img' alt='' />" if card_bg else ""}
                <div class="card-overlay"></div>
            </div>
            <div class="card-content">
                {"<img src='" + logo + "' class='card-logo' alt='' />" if logo else f"<div class='card-title-fallback'>{name}</div>"}
                <div class="card-pill-tag">
                    <span>— {summary.split('—')[-1].strip() if '—' in summary else 'Recommended'}</span>
                </div>
            </div>
        </div>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
{SHARED_COLLAGE_CSS}

.outro-header {{
    padding: 70px 70px 20px 70px;
    text-align: center;
    z-index: 10;
}}

.outro-title {{
    font-family: 'Anton', sans-serif;
    font-size: 78px;
    line-height: 1.02;
    text-transform: uppercase;
    color: #0F172A;
    margin-bottom: 35px;
}}

.cards-container {{
    padding: 0 70px;
    display: flex;
    flex-direction: column;
    gap: 24px;
    margin-bottom: 45px;
    z-index: 10;
}}

.game-card-row {{
    height: 195px;
    border-radius: 24px;
    overflow: hidden;
    position: relative;
    border: 5px solid #FFFFFF;
    box-shadow: 0 14px 30px rgba(0,0,0,0.18);
    display: flex;
    align-items: center;
    background: #1E293B;
}}

.card-bg-wrap {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
}}

.card-bg-img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
}}

.card-overlay {{
    position: absolute;
    inset: 0;
    background: linear-gradient(90deg, rgba(15,23,42,0.85) 0%, rgba(15,23,42,0.3) 65%, transparent 100%);
}}

.card-content {{
    position: relative;
    z-index: 5;
    padding: 0 35px;
    display: flex;
    flex-direction: column;
    gap: 10px;
}}

.card-logo {{
    max-height: 65px;
    max-width: 380px;
    object-fit: contain;
    filter: drop-shadow(0 4px 10px rgba(0,0,0,0.5));
}}

.card-title-fallback {{
    font-family: 'Anton', sans-serif;
    font-size: 40px;
    color: #FFFFFF;
    text-transform: uppercase;
}}

.card-pill-tag {{
    background: #FFBE1A;
    color: #0F172A;
    font-family: 'Montserrat', sans-serif;
    font-weight: 800;
    font-size: 15px;
    padding: 5px 14px;
    border-radius: 999px;
    align-self: flex-start;
    box-shadow: 0 4px 10px rgba(0,0,0,0.2);
}}

/* Bottom Action Callouts */
.bottom-actions-row {{
    margin: auto 70px 60px 70px;
    display: flex;
    align-items: center;
    gap: 20px;
    z-index: 10;
}}

.share-action-card {{
    flex: 1.2;
    background: #FFFFFF;
    border-radius: 20px;
    padding: 22px 26px;
    border: 3px solid #E2E8F0;
    display: flex;
    align-items: center;
    gap: 16px;
    box-shadow: 0 8px 20px rgba(0,0,0,0.06);
}}

.share-action-text {{
    font-size: 19px;
    font-weight: 700;
    color: #1E293B;
    line-height: 1.25;
}}

.save-action-card {{
    flex: 1;
    background: #FFBE1A;
    border-radius: 20px;
    padding: 22px 24px;
    display: flex;
    align-items: center;
    gap: 14px;
    box-shadow: 0 10px 24px rgba(255, 190, 26, 0.4);
}}

.save-action-text {{
    font-family: 'Anton', sans-serif;
    font-size: 24px;
    color: #0F172A;
    line-height: 1.1;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}}
</style>
</head>
<body>
<div class="slide-canvas">
    <img src="/static/carousel_assets/paint_splatter_2.svg" style="position: absolute; top: 40px; left: 30px; width: 180px; opacity: 0.7;" alt="" />

    <div class="outro-header">
        <div class="outro-title">
            <span>SO... WHICH ONE </span><br/>
            <span class="brush-highlight">
                <img src="/static/carousel_assets/brush_yellow_block.svg" class="brush-highlight-bg" alt="" />
                <span>ARE YOU PLAYING FIRST?</span>
            </span>
        </div>
    </div>

    <!-- 3 Game Horizontal Cards -->
    <div class="cards-container">
        {cards_html}
    </div>

    <!-- Bottom Social CTA Callouts -->
    <div class="bottom-actions-row">
        <div class="share-action-card">
            <span style="font-size: 34px;">✈️</span>
            <div class="share-action-text">
                {share_text}
                <div style="font-size: 14px; color: #64748B; font-weight: 500; margin-top: 4px;">Game names are in the pinned comment.</div>
            </div>
        </div>

        <div class="save-action-card">
            <span style="font-size: 32px;">💾</span>
            <div class="save-action-text">{save_text}</div>
        </div>
    </div>
</div>
</body>
</html>"""


# ==============================================================================
# HIGH-RES HEADLESS CHROMIUM RENDERING
# ==============================================================================

def find_chrome_executable() -> Optional[str]:
    """Find local Chrome or Edge executable on Windows."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None

def render_slide_to_png(
    html_content: str,
    output_png_path: Path,
    chrome_path: Optional[str] = None,
    scale_factor: float = 2.0
) -> bool:
    """Render 1080x1350 HTML slide to PNG via headless browser."""
    if not chrome_path:
        chrome_path = find_chrome_executable()
    if not chrome_path:
        raise RuntimeError("No se encontró Chrome ni Edge para renderizar las diapositivas.")

    output_png_path = Path(output_png_path)
    output_png_path.parent.mkdir(parents=True, exist_ok=True)
    temp_html = output_png_path.parent / f"_temp_{output_png_path.stem}.html"

    try:
        with open(temp_html, "w", encoding="utf-8") as f:
            f.write(html_content)

        cmd = [
            chrome_path,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--window-size=1080,1350",
            f"--force-device-scale-factor={scale_factor}",
            f"--screenshot={str(output_png_path.resolve())}",
            f"file:///{str(temp_html.resolve()).replace(os.sep, '/')}"
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            logger.error(f"Chrome screenshot failed: {proc.stderr}")
            return False

        return output_png_path.exists()
    finally:
        if temp_html.exists():
            try:
                temp_html.unlink()
            except Exception:
                pass


def export_carousel_bundle(
    slides_html: List[str],
    output_basename: str = "three_games_carousel"
) -> Dict[str, Any]:
    """
    Renders all 5 slides to PNG and packs them into a ZIP file.
    Returns dictionary with slide PNG paths, URLs, and ZIP download URL.
    """
    chrome_path = find_chrome_executable()
    if not chrome_path:
        raise RuntimeError("Navegador no encontrado para renderizado headless.")

    timestamp = int(time.time())
    run_dir = OUTPUT_DIR / f"{output_basename}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    slide_results = []
    png_files = []

    slide_names = ["01_portada", "02_juego1", "03_juego2", "04_juego3", "05_cierre"]

    for idx, (html, s_name) in enumerate(zip(slides_html, slide_names)):
        png_path = run_dir / f"slide_{s_name}.png"
        success = render_slide_to_png(html, png_path, chrome_path)
        if success:
            png_files.append(png_path)
            # Relative media URL
            rel_url = output_url(png_path)
            slide_results.append({
                "slide_index": idx + 1,
                "name": s_name,
                "url": rel_url
            })

    # Create ZIP archive
    zip_path = run_dir / f"{output_basename}_{timestamp}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in png_files:
            zf.write(p, arcname=p.name)

    zip_url = output_url(zip_path)

    return {
        "success": True,
        "slide_count": len(slide_results),
        "slides": slide_results,
        "zip_url": zip_url
    }
