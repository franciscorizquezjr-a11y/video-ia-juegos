"""
steam_carousel.py — Steam Deals Carousel (3:4 Format) Generator for IGD & GUAM

Generates multi-slide 3:4 Instagram/TikTok carousels featuring Steam games on sale,
supporting official brand themes:
- IGD (Indie Games Discovery): Forest green & olive accents (#7CA032) on warm cream (#EAE0C8)
- GUAM (Games Under A Minute): Vibrant brand orange (#FF7D00) with sleek charcoal (#1C140D) on warm cream (#EDE5D8)
- 3 games per slide with 2 selected screenshots each (asymmetric magazine layout)
- Steam pricing with original strikethrough, discount % badge, and final price
- 1080x1440 px headless Chrome rendering into output/ with 4K Ultra HD ZIP export
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
from project_paths import output_url
from pathlib import Path
from typing import List, Dict, Any, Optional
import requests

logger = logging.getLogger("steam_carousel")
BASE_DIR = Path(__file__).resolve().parent

# Default brand assets
STEAM_BADGE_PATH = BASE_DIR / "Presets" / "Steam" / "steam_new_badge.png"
IGD_LOGO_PATH = BASE_DIR / "Presets" / "IGD" / "IGD_Logo.png"
GUAM_LOGO_PATH = BASE_DIR / "Presets" / "GUAM" / "GUAM_Logo.png"

# ==============================================================================
# BRAND THEMES CONFIGURATION (IGD & GUAM)
# ==============================================================================

THEMES: Dict[str, Dict[str, Any]] = {
    "igd": {
        "id": "igd",
        "name": "Indie Games Discovery",
        "short_name": "IGD",
        "brand_name": "Indie Games Discovery",
        "brand_icon": "🌿",
        "handle": "@indiegamesdiscovery",
        "logo_path": IGD_LOGO_PATH,
        # Backgrounds
        "bg_color": "#eae0c8",
        "text_headline": "#142312",
        "text_sub": "#4a5846",
        # Primary olive green
        "primary_color": "#7ca032",
        "primary_dark": "#23370d",
        "primary_soft": "rgba(124, 160, 50, 0.15)",
        "primary_border": "rgba(124, 160, 50, 0.35)",
        # Price pill
        "price_pill_bg": "#23370d",
        "price_original_color": "#9cb486",
        "price_discount_bg": "#7ca032",
        "price_discount_color": "#ffffff",
        "price_final_color": "#ffffff",
        # Test background & overlays
        "test_bg_gradient": "radial-gradient(circle at 50% 25%, #f6f0df 0%, #eae0c8 55%, #dfd3b7 100%)",
        "gradient_overlay": """linear-gradient(
            to bottom,
            rgba(234, 224, 200, 0.0) 0%,
            rgba(234, 224, 200, 0.25) 35%,
            rgba(234, 224, 200, 0.85) 55%,
            #eae0c8 75%,
            #eae0c8 100%
        )""",
        "closing_logo_shadow": "0 16px 45px rgba(20, 35, 18, 0.16)",
        "card_shadow": "0 4px 16px rgba(20, 35, 18, 0.12)",
        "dot_inactive_color": "rgba(124, 160, 50, 0.35)",
    },
    "guam": {
        "id": "guam",
        "name": "Games Under A Minute",
        "short_name": "GUAM",
        "brand_name": "Games Under A Minute",
        "brand_icon": "⚡",
        "handle": "@gamesunderaminute",
        "logo_path": GUAM_LOGO_PATH,
        # Almost black background (#0c0c0e) for sleek, high-contrast dark aesthetic
        "bg_color": "#0c0c0e",
        "text_headline": "#ffffff",
        "text_sub": "#cbd5e1",
        # GUAM official brand orange (#ff7d00 from GUAM_Logo.png)
        "primary_color": "#ff7d00",
        "primary_dark": "#060608",
        "primary_soft": "rgba(255, 125, 0, 0.16)",
        "primary_border": "rgba(255, 125, 0, 0.4)",
        # Price pill on sleek dark background
        "price_pill_bg": "#060608",
        "price_original_color": "#94a3b8",
        "price_discount_bg": "#ff7d00",
        "price_discount_color": "#ffffff",
        "price_final_color": "#ffffff",
        # Test background & overlays fading smoothly to almost black
        "test_bg_gradient": "radial-gradient(circle at 50% 25%, #23232b 0%, #121216 55%, #08080a 100%)",
        "gradient_overlay": """linear-gradient(
            to bottom,
            rgba(12, 12, 14, 0.0) 0%,
            rgba(12, 12, 14, 0.2) 35%,
            rgba(12, 12, 14, 0.75) 52%,
            rgba(12, 12, 14, 0.95) 68%,
            #0c0c0e 82%,
            #0c0c0e 100%
        )""",
        "closing_logo_shadow": "0 16px 45px rgba(255, 125, 0, 0.35)",
        "card_shadow": "0 8px 24px rgba(0, 0, 0, 0.5)",
        "dot_inactive_color": "rgba(255, 255, 255, 0.35)",
    }
}

def get_theme_config(theme: Optional[str] = "igd") -> Dict[str, Any]:
    """Retrieve theme configuration dictionary by name ('igd' or 'guam')."""
    key = str(theme or "igd").strip().lower()
    return THEMES.get(key, THEMES["igd"])

def get_base64_image(file_path: Path) -> str:
    """Read a local image and return a data URI."""
    if not file_path.exists():
        return ""
    suffix = file_path.suffix.lower().replace(".", "")
    if suffix == "jpg":
        suffix = "jpeg"
    with open(file_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return f"data:image/{suffix};base64,{encoded}"

def parse_steam_app_id(url_or_term: str) -> Optional[int]:
    """Extract app_id from Steam URL or return integer if direct ID."""
    if not url_or_term:
        return None
    url_or_term = str(url_or_term).strip()
    
    # Check for direct numeric ID
    if url_or_term.isdigit():
        return int(url_or_term)
        
    # Check for URL match like /app/381210 or /app/381210/
    match = re.search(r"/app/(\d+)", url_or_term)
    if match:
        return int(match.group(1))
        
    # Fallback: search game by name via Steam Store Search API
    try:
        search_url = "https://store.steampowered.com/api/storesearch/"
        params = {"term": url_or_term, "l": "spanish", "cc": "ES"}
        resp = requests.get(search_url, params=params, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            if items and items[0].get("id"):
                return int(items[0]["id"])
    except Exception as e:
        logger.warning(f"Error searching steam app for '{url_or_term}': {e}")
        
    return None

def fetch_steam_game_details(url_or_id: str, currency_cc: str = "ES", language: str = "spanish") -> Optional[Dict[str, Any]]:
    """
    Fetch game details, prices, discounts, and screenshots from Steam API.
    """
    app_id = parse_steam_app_id(url_or_id)
    if not app_id:
        return None
        
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    details_url = "https://store.steampowered.com/api/appdetails"
    params = {"appids": str(app_id), "cc": currency_cc, "l": language}
    
    try:
        resp = requests.get(details_url, params=params, headers=headers, timeout=12)
        resp.raise_for_status()
        raw_data = resp.json()
        
        app_entry = raw_data.get(str(app_id)) or (next(iter(raw_data.values()), {}) if raw_data else {})
        if not app_entry.get("success"):
            return None
            
        data = app_entry.get("data", {})
        name = data.get("name", f"Steam App {app_id}")
        is_free = bool(data.get("is_free", False))
        
        # Price overview
        price_overview = data.get("price_overview", {})
        initial_formatted = price_overview.get("initial_formatted", "")
        final_formatted = price_overview.get("final_formatted", "")
        discount_percent = price_overview.get("discount_percent", 0)
        
        if is_free:
            final_formatted = "Free to Play" if language == "english" else "Gratis"
            initial_formatted = ""
            discount_percent = 0
        elif not final_formatted:
            # If price_overview is missing (e.g. unreleased or bundle)
            final_formatted = "Ver en Steam"
            initial_formatted = ""
            discount_percent = 0
            
        # Screenshots
        raw_screenshots = data.get("screenshots", [])
        screenshots = []
        for s in raw_screenshots:
            thumb = s.get("path_thumbnail") or s.get("path_full")
            full = s.get("path_full") or s.get("path_thumbnail")
            if thumb and full:
                screenshots.append({
                    "id": s.get("id", len(screenshots)),
                    "thumb": thumb,
                    "full": full
                })
                
        return {
            "app_id": app_id,
            "name": name,
            "store_url": f"https://store.steampowered.com/app/{app_id}",
            "is_free": is_free,
            "has_discount": (discount_percent > 0),
            "discount_percent": discount_percent,
            "initial_formatted": initial_formatted,
            "final_formatted": final_formatted,
            "screenshots": screenshots
        }
    except Exception as e:
        logger.error(f"Failed to fetch Steam details for app_id {app_id}: {e}")
        return None

def fetch_multiple_steam_games(urls: List[str], currency_cc: str = "ES", language: str = "spanish") -> List[Dict[str, Any]]:
    """Fetch details for a list of Steam URLs."""
    results = []
    for u in urls:
        u_str = u.strip()
        if not u_str:
            continue
        details = fetch_steam_game_details(u_str, currency_cc=currency_cc, language=language)
        if details:
            results.append(details)
        else:
            logger.warning(f"Could not fetch details for URL: {u_str}")
    return results


# ==============================================================================
# HTML BUILDERS FOR 3:4 SLIDES (1080 x 1440 px)
# ==============================================================================

def get_headline_tag(headline: str) -> str:
    """Extract the first 2 words of the headline as the category tag (e.g. 'JUEGOS CO-OP')."""
    if not headline:
        return "STEAM DEALS"
    words = str(headline).strip().split()
    if len(words) >= 2:
        return f"{words[0]} {words[1]}".upper()
    elif len(words) == 1:
        return words[0].upper()
    return "STEAM DEALS"

def get_shared_css(cfg: Dict[str, Any]) -> str:
    """Generate shared CSS matching the selected theme (IGD or GUAM)."""
    return f"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=JetBrains+Mono:wght@600;700;800&family=Newsreader:ital,opsz,wght@0,6..72,700;0,6..72,800;1,6..72,700&display=swap');

* {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}}

body {{
    width: 1080px;
    height: 1440px;
    overflow: hidden;
    background-color: {cfg['bg_color']};
    color: {cfg['text_headline']};
    font-family: 'Inter', -apple-system, sans-serif;
    -webkit-font-smoothing: antialiased;
}}

.slide-canvas {{
    width: 1080px;
    height: 1440px;
    position: relative;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    background-color: {cfg['bg_color']};
}}

.bg-layer {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    z-index: 1;
}}

.gradient-overlay {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    z-index: 2;
    background: {cfg['gradient_overlay']};
}}

.test-bg-gradient {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    z-index: 1;
    background: {cfg['test_bg_gradient']};
}}

.content-layer {{
    position: relative;
    z-index: 10;
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
}}
"""

# Backwards compatibility
IGD_SHARED_CSS = get_shared_css(THEMES["igd"])

def build_cover_slide_html(
    headline: str = "JUEGOS CO-OP DE OFERTA EN STEAM",
    swipe_text: str = "DESLIZA PARA VER",
    bg_image_b64: str = "",
    use_test_bg: bool = False,
    theme: str = "igd",
    total_slides: int = 5
) -> str:
    """Generate HTML for Slide 1 (Cover). Note: No Steam logo on cover per user request."""
    cfg = get_theme_config(theme)
    shared_css = get_shared_css(cfg)

    # Dynamic dots matching total_slides (minimum 1)
    num_dots = max(1, int(total_slides or 5))
    dots_spans = [
        '<span class="dot active"></span>' if i == 0 else '<span class="dot"></span>'
        for i in range(num_dots)
    ]
    dots_html = "\n                ".join(dots_spans)

    bg_element = ""
    if bg_image_b64 and not use_test_bg:
        bg_element = f'<img src="{bg_image_b64}" class="bg-layer" /><div class="gradient-overlay cover-gradient"></div>'
    else:
        bg_element = '<div class="test-bg-gradient"></div><div class="gradient-overlay cover-gradient"></div>'

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<style>
{shared_css}

.cover-gradient {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    z-index: 2;
    background: {cfg['gradient_overlay']};
}}

.cover-content {{
    padding: 80px 70px 90px 70px;
    justify-content: space-between;
    align-items: center;
    text-align: center;
}}

/* Brand top bar */
.top-brand {{
    display: flex;
    align-items: center;
    gap: 12px;
    background: {cfg['primary_color']};
    border: none;
    padding: 12px 28px;
    border-radius: 999px;
    box-shadow: {cfg['card_shadow']};
}}

.top-brand-text {{
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 0.14em;
    color: #ffffff;
    text-transform: uppercase;
}}

/* Center Area (Hook lowered to lower third) */
.cover-center-area {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 24px;
    max-width: 960px;
    margin-top: auto;
    margin-bottom: 70px;
}}


.cover-headline {{
    font-family: 'Newsreader', Georgia, serif;
    font-size: 76px;
    font-weight: 800;
    line-height: 1.08;
    color: {cfg['text_headline']};
    text-transform: uppercase;
    letter-spacing: 0.01em;
    text-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
}}

/* Bottom Area */
.bottom-area {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 26px;
    width: 100%;
}}

.cover-swipe-pill {{
    display: inline-flex;
    align-items: center;
    gap: 14px;
    background: {cfg['primary_color']};
    border: none;
    padding: 14px 38px;
    border-radius: 999px;
    box-shadow: {cfg['card_shadow']};
}}

.cover-swipe-text {{
    font-size: 22px;
    font-weight: 800;
    letter-spacing: 0.16em;
    color: #ffffff;
    text-transform: uppercase;
}}

.cover-swipe-arrow {{
    font-size: 24px;
    color: #ffffff;
}}

.dots-indicator {{
    display: flex;
    gap: 8px;
    align-items: center;
}}

.dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: {cfg.get('dot_inactive_color', cfg['primary_soft'])};
}}

.dot.active {{
    width: 26px;
    border-radius: 999px;
    background: {cfg['primary_color']};
}}
</style>
</head>
<body>
<div class="slide-canvas">
    {bg_element}
    <div class="content-layer cover-content">
        <div class="top-brand">
            <span style="font-size: 20px;">{cfg['brand_icon']}</span>
            <span class="top-brand-text">{cfg['brand_name']}</span>
        </div>

        <div class="cover-center-area">
            <h1 class="cover-headline">{headline}</h1>
        </div>

        <div class="bottom-area">
            <div class="cover-swipe-pill">
                <span class="cover-swipe-text">{swipe_text}</span>
                <span class="cover-swipe-arrow">➔</span>
            </div>
            <div class="dots-indicator">
                {dots_html}
            </div>
        </div>
    </div>
</div>
</body>
</html>"""
    return html

def build_games_slide_html(
    games_chunk: List[Dict[str, Any]],
    slide_index: int = 1,
    total_slides: int = 3,
    headline: str = "JUEGOS CO-OP DE OFERTA EN STEAM",
    category_tag: str = "",
    theme: str = "igd"
) -> str:
    """
    Generate HTML for a Content Slide containing exactly 3 games.
    Features:
    - Theme-specific light cream background (#EAE0C8 for IGD / #EDE5D8 for GUAM)
    - Theme-specific brand color boxes & borders (#7CA032 for IGD / #FF7D00 for GUAM)
    - Asymmetric screenshots: left small shot with price pill underneath, right large shot
    - Price pill includes: Original strikethrough price, % Discount badge, Final price
    - Top nav bar shows the first 2 words of the headline (e.g. 'JUEGOS CO-OP')
    - Compact spacing between the 3 games
    """
    cfg = get_theme_config(theme)
    shared_css = get_shared_css(cfg)

    if not category_tag:
        category_tag = get_headline_tag(headline)

    games_html_parts = []

    for g in games_chunk:
        name = g.get("name", "Steam Game")
        screenshots = g.get("selected_screenshots") or []
        img1 = screenshots[0] if len(screenshots) > 0 else ""
        img2 = screenshots[1] if len(screenshots) > 1 else img1

        has_discount = g.get("has_discount", False)
        discount_pct = g.get("discount_percent", 0)
        initial_p = g.get("initial_formatted", "")
        final_p = g.get("final_formatted", "")

        # Compute discount percentage if not explicitly set
        if has_discount and initial_p and (not discount_pct or int(discount_pct) <= 0):
            try:
                num_init = float(re.sub(r"[^\d,\.]", "", initial_p).replace(",", "."))
                num_final = float(re.sub(r"[^\d,\.]", "", final_p).replace(",", "."))
                if num_init > 0 and num_init > num_final:
                    discount_pct = int(round((1.0 - num_final / num_init) * 100))
            except Exception:
                discount_pct = 0

        # Price tag HTML: Original strikethrough, % discount badge, final price
        if has_discount and initial_p:
            discount_badge_html = ""
            if discount_pct and int(discount_pct) > 0:
                discount_badge_html = f'<span class="price-discount">-{int(discount_pct)}%</span>'

            price_tag_html = f"""
            <div class="game-price-pill">
                <span class="price-original">{initial_p}</span>
                {discount_badge_html}
                <span class="price-final">{final_p}</span>
            </div>
            """
        elif final_p:
            price_tag_html = f"""
            <div class="game-price-pill">
                <span class="price-final">{final_p}</span>
            </div>
            """
        else:
            price_tag_html = ""

        part = f"""
        <div class="game-block">
            <!-- Header Box with Game Title -->
            <div class="game-title-bar">
                <h2 class="game-title-text">{name}</h2>
            </div>
            <!-- Media Box: Left Small Shot + Price Pill, Right Big Shot -->
            <div class="game-media-frame">
                <div class="game-media-left-col">
                    <div class="game-shot-wrapper-small">
                        <img src="{img1}" class="game-shot-img" />
                    </div>
                    {price_tag_html}
                </div>
                <div class="game-media-right-col">
                    <div class="game-shot-wrapper-big">
                        <img src="{img2}" class="game-shot-img" />
                    </div>
                </div>
            </div>
        </div>
        """
        games_html_parts.append(part)

    games_content = "\n".join(games_html_parts)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<style>
{shared_css}

.games-content {{
    padding: 32px 42px 32px 42px;
    height: 100%;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
}}

/* Top navigation & branding */
.top-nav-bar {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    width: 100%;
    margin-bottom: 12px;
}}

.nav-brand-title {{
    font-size: 22px;
    font-weight: 800;
    letter-spacing: 0.16em;
    color: {cfg['primary_color']};
    text-transform: uppercase;
}}

.nav-swipe-btn {{
    width: 48px;
    height: 48px;
    border-radius: 50%;
    background: {cfg['primary_color']};
    border: none;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #ffffff;
    font-size: 22px;
    font-weight: 800;
    box-shadow: {cfg['card_shadow']};
}}

/* 3 Game Blocks Container with compact gap */
.games-list {{
    display: flex;
    flex-direction: column;
    gap: 16px;
    flex: 1;
    justify-content: space-between;
}}

.game-block {{
    display: flex;
    flex-direction: column;
    gap: 8px;
}}

/* Title Box */
.game-title-bar {{
    background: {cfg['primary_color']};
    border: none;
    border-radius: 12px;
    padding: 10px 18px;
    display: flex;
    align-items: center;
    box-shadow: {cfg['card_shadow']};
}}

.game-title-text {{
    font-family: 'Newsreader', Georgia, serif;
    font-size: 28px;
    font-weight: 800;
    color: #ffffff;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    line-height: 1.1;
    text-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
}}

/* Screenshots Asymmetric Frame: Left Small + Price, Right Large */
.game-media-frame {{
    background: {cfg['primary_color']};
    border: none;
    border-radius: 14px;
    padding: 8px;
    display: flex;
    gap: 8px;
    height: 350px;
    box-shadow: {cfg['card_shadow']};
}}

.game-media-left-col {{
    width: 40%;
    display: flex;
    flex-direction: column;
    gap: 8px;
    height: 100%;
}}

.game-shot-wrapper-small {{
    flex: 1;
    border-radius: 8px;
    overflow: hidden;
    background: #000;
}}

.game-media-right-col {{
    flex: 1;
    height: 100%;
    display: flex;
}}

.game-shot-wrapper-big {{
    width: 100%;
    height: 100%;
    border-radius: 8px;
    overflow: hidden;
    background: #000;
}}

.game-shot-img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}}

/* Price Box under Left Screenshot (Original + % Discount + Final Price) */
.game-price-pill {{
    background: {cfg['price_pill_bg']};
    border: none;
    border-radius: 8px;
    padding: 6px 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    height: 48px;
    flex-shrink: 0;
}}

.price-original {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 18px;
    font-weight: 600;
    color: {cfg['price_original_color']};
    text-decoration: line-through;
    white-space: nowrap;
}}

.price-discount {{
    font-family: 'Inter', -apple-system, sans-serif;
    font-size: 17px;
    font-weight: 800;
    color: {cfg['price_discount_color']};
    background: {cfg['price_discount_bg']};
    padding: 2px 7px;
    border-radius: 5px;
    line-height: 1.1;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    letter-spacing: -0.02em;
    white-space: nowrap;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.25);
}}

.price-final {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 23px;
    font-weight: 800;
    color: {cfg['price_final_color']};
    white-space: nowrap;
}}
</style>
</head>
<body>
<div class="slide-canvas">
    <div class="content-layer games-content">
        <!-- Top Nav Bar with 2 first words of headline -->
        <div class="top-nav-bar">
            <div class="nav-brand-title">{category_tag}</div>
            <div class="nav-swipe-btn">➔</div>
        </div>

        <!-- 3 Games Cards -->
        <div class="games-list">
            {games_content}
        </div>
    </div>
</div>
</body>
</html>"""
    return html

def build_closing_slide_html(
    cta_title: str = "¿CUÁL VAS A JUGAR PRIMERO?",
    cta_subtitle: str = "Guarda este post y síguenos para más recomendaciones",
    bg_image_b64: str = "",
    use_test_bg: bool = False,
    theme: str = "igd"
) -> str:
    """Generate HTML for Slide Final (Closing) with theme branding."""
    cfg = get_theme_config(theme)
    shared_css = get_shared_css(cfg)
    brand_logo_b64 = get_base64_image(cfg["logo_path"])

    bg_element = ""
    if bg_image_b64 and not use_test_bg:
        bg_element = f'<img src="{bg_image_b64}" class="bg-layer" /><div class="gradient-overlay closing-gradient"></div>'
    else:
        bg_element = '<div class="test-bg-gradient"></div><div class="gradient-overlay closing-gradient"></div>'

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<style>
{shared_css}

.closing-gradient {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    z-index: 2;
    background: linear-gradient(
        to bottom,
        rgba(0, 0, 0, 0.0) 0%,
        rgba(0, 0, 0, 0.0) 35%,
        rgba(0, 0, 0, 0.4) 50%,
        {cfg['bg_color']} 72%,
        {cfg['bg_color']} 100%
    );
}}

.closing-content {{
    padding: 60px 60px 80px 60px;
    justify-content: flex-end;
    align-items: center;
    text-align: center;
    gap: 24px;
}}

.closing-logo-wrapper {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
}}

.closing-logo {{
    width: 120px;
    height: 120px;
    border-radius: 28px;
    border: none;
    box-shadow: {cfg['closing_logo_shadow']};
    object-fit: contain;
}}

.closing-brand-name {{
    font-size: 22px;
    font-weight: 800;
    color: {cfg['primary_color']};
    letter-spacing: 0.12em;
    text-transform: uppercase;
}}

/* Middle CTA Area: above the @ pill */
.closing-center-area {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    max-width: 920px;
}}

.closing-headline {{
    font-family: 'Newsreader', Georgia, serif;
    font-size: 52px;
    font-weight: 800;
    line-height: 1.15;
    color: {cfg['text_headline']};
    text-transform: uppercase;
    text-shadow: 0 4px 18px rgba(0, 0, 0, 0.5);
}}

.closing-subtext {{
    font-size: 20px;
    font-weight: 600;
    color: {cfg['text_sub']};
    line-height: 1.35;
    max-width: 760px;
}}

/* Bottom Action Pill */
.closing-handle-pill {{
    display: inline-flex;
    align-items: center;
    gap: 12px;
    background: {cfg['primary_color']};
    border: none;
    padding: 14px 38px;
    border-radius: 999px;
    box-shadow: {cfg['card_shadow']};
}}

.closing-handle-text {{
    font-size: 24px;
    font-weight: 800;
    letter-spacing: 0.06em;
    color: #ffffff;
}}
</style>
</head>
<body>
<div class="slide-canvas">
    {bg_element}
    <div class="content-layer closing-content">
        <!-- Logo and brand name (just above the big text) -->
        <div class="closing-logo-wrapper">
            <img src="{brand_logo_b64}" class="closing-logo" />
            <div class="closing-brand-name">{cfg['brand_name']}</div>
        </div>

        <!-- Big CTA text (above the @ handle) -->
        <div class="closing-center-area">
            <h1 class="closing-headline">{cta_title}</h1>
            <p class="closing-subtext">{cta_subtitle}</p>
        </div>

        <!-- The @ handle pill (at the bottom) -->
        <div class="closing-handle-pill">
            <span style="color: #ffffff; font-size: 24px;">🔖</span>
            <span class="closing-handle-text">{cfg['handle']}</span>
        </div>
    </div>
</div>
</body>
</html>"""
    return html


# ==============================================================================
# CAROUSEL RENDER & EXPORT ENGINE
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

def render_html_to_png(html_content: str, output_png_path: Path, chrome_path: str = None, scale_factor: float = 2.6666667) -> bool:
    """Render high-resolution 4K (2880x3840 px) HTML content to a PNG image using headless Chrome."""
    if not chrome_path:
        chrome_path = find_chrome_executable()
    if not chrome_path:
        raise RuntimeError("No Chrome or Edge browser executable found for rendering slides.")
        
    output_png_path = Path(output_png_path)
    temp_html = output_png_path.parent / f"_temp_{output_png_path.stem}.html"
    try:
        with open(temp_html, "w", encoding="utf-8") as f:
            f.write(html_content)
            
        cmd = [
            chrome_path,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--window-size=1080,1440",
            f"--force-device-scale-factor={scale_factor}",
            f"--screenshot={str(output_png_path.resolve())}",
            f"file:///{str(temp_html.resolve())}"
        ]
        
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            logger.error(f"Chrome screenshot failed: {proc.stderr}")
            return False
            
        return output_png_path.exists()
    finally:
        if temp_html.exists():
            try:
                os.remove(temp_html)
            except Exception:
                pass

def export_carousel_slides(
    carousel_config: Dict[str, Any],
    output_base_dir: Path
) -> Dict[str, Any]:
    """
    Export all slides of the carousel as individual 4K (2880x3840 px) PNG images into output/.
    carousel_config structure:
      - theme: str ("igd" or "guam")
      - headline: str
      - swipe_text: str
      - cta_title: str
      - cta_subtitle: str
      - bg_image_b64: str (or empty)
      - use_test_bg: bool
      - scale_factor: float (defaults to 2.6666667 for 2880x3840 4K Ultra HD)
      - games: List[Dict] with 'name', 'selected_screenshots', 'initial_formatted', etc.
    """
    theme = (carousel_config.get("theme") or "igd").strip().lower()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    folder_name = f"carrusel_{theme}_{timestamp}"
    target_dir = output_base_dir / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)
    
    chrome_path = find_chrome_executable()
    if not chrome_path:
        raise RuntimeError("Chrome o Edge no encontrado en el sistema para renderizar.")

    headline = carousel_config.get("headline", "JUEGOS CO-OP DE OFERTA EN STEAM")
    swipe_text = carousel_config.get("swipe_text", "DESLIZA PARA VER")
    cta_title = carousel_config.get("cta_title", "¿CUÁL VAS A JUGAR PRIMERO?")
    cta_subtitle = carousel_config.get("cta_subtitle", "Guarda este post y síguenos para más recomendaciones")
    bg_image_b64 = carousel_config.get("bg_image_b64", "")
    use_test_bg = bool(carousel_config.get("use_test_bg", False))
    scale_factor = float(carousel_config.get("scale_factor", 2.6666667))
    games = carousel_config.get("games", [])
    
    slide_files = []
    slide_num = 1

    # Calculate total slides: 1 cover + N content slides + 1 closing
    chunk_size = 3
    chunks = [games[i:i + chunk_size] for i in range(0, len(games), chunk_size)]
    total_content_slides = len(chunks)
    total_deck_slides = 1 + total_content_slides + 1
    
    # 1. Slide 1: Cover
    cover_html = build_cover_slide_html(
        headline=headline,
        swipe_text=swipe_text,
        bg_image_b64=bg_image_b64,
        use_test_bg=use_test_bg,
        theme=theme,
        total_slides=total_deck_slides
    )
    cover_png = target_dir / f"slide_{slide_num:02d}_portada.png"
    if render_html_to_png(cover_html, cover_png, chrome_path, scale_factor):
        slide_files.append({
            "slide_index": slide_num,
            "title": "Portada",
            "filename": cover_png.name,
            "path": str(cover_png.resolve()),
            "url": output_url(cover_png)
        })
    slide_num += 1
    
    # 2. Content Slides: 3 games per slide
    for i, chunk in enumerate(chunks):
        game_html = build_games_slide_html(
            games_chunk=chunk,
            slide_index=i + 1,
            total_slides=total_content_slides,
            headline=headline,
            theme=theme
        )
        game_png = target_dir / f"slide_{slide_num:02d}_juegos_{i+1}.png"
        if render_html_to_png(game_html, game_png, chrome_path, scale_factor):
            slide_files.append({
                "slide_index": slide_num,
                "title": f"Juegos ({i*3 + 1}-{i*3 + len(chunk)})",
                "filename": game_png.name,
                "path": str(game_png.resolve()),
                "url": output_url(game_png)
            })
        slide_num += 1
        
    # 3. Closing Slide
    closing_html = build_closing_slide_html(
        cta_title=cta_title,
        cta_subtitle=cta_subtitle,
        bg_image_b64=bg_image_b64,
        use_test_bg=use_test_bg,
        theme=theme
    )
    closing_png = target_dir / f"slide_{slide_num:02d}_cierre.png"
    if render_html_to_png(closing_html, closing_png, chrome_path, scale_factor):
        slide_files.append({
            "slide_index": slide_num,
            "title": "Cierre",
            "filename": closing_png.name,
            "path": str(closing_png.resolve()),
            "url": output_url(closing_png)
        })
        
    # 4. Pack ZIP file
    zip_filename = f"{folder_name}.zip"
    zip_path = output_base_dir / zip_filename
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for sf in slide_files:
            file_on_disk = Path(sf["path"])
            if file_on_disk.exists():
                zipf.write(file_on_disk, arcname=file_on_disk.name)
                
    return {
        "success": True,
        "theme": theme,
        "folder_name": folder_name,
        "folder_path": str(target_dir.resolve()),
        "folder_url": output_url(target_dir),
        "zip_url": output_url(zip_path),
        "total_slides": len(slide_files),
        "slides": slide_files,
        "resolution": "2880x3840 (4K Ultra HD)",
        "scale_factor": scale_factor
    }
