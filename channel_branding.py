"""Optional channel badges shared by Klip preview and FFmpeg export."""
from __future__ import annotations
import json
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent

class ChannelBrandingSettings(BaseModel):
    enabled: bool = False
    preset: str = Field(default="NextLoot", max_length=80, pattern=r"^[\w -]+$")
    text: str = Field(default="@nextlootgames", max_length=64)
    size: int = Field(default=64, ge=32, le=160)
    x: int = Field(default=60, ge=0, le=1000)
    y: int = Field(default=240, ge=0, le=1800)
    opacity: int = Field(default=80, ge=10, le=100)


def build_channel_badge(settings: ChannelBrandingSettings, width=1080, height=1920,
                        presets_dir: Path | None = None):
    """Return (RGBA image, x, y), or None when disabled. Coordinates reference 1080p."""
    if not settings.enabled:
        return None
    root = (presets_dir or BASE_DIR / "Presets").resolve()
    folder = (root / settings.preset).resolve()
    if folder.parent != root or not folder.is_dir():
        raise ValueError("No se encuentra el preset de marca seleccionado.")
    files = sorted(folder.glob("*.json"))
    if not files:
        raise ValueError("El preset de marca no tiene configuracion.")
    data = json.loads(files[0].read_text(encoding="utf-8"))
    scale = width / 1080
    size = max(1, round(settings.size * scale))
    font_size = max(1, round(settings.size * .47 * scale))
    font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    font = None
    for candidate in (font_dir / "seguisb.ttf", font_dir / "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(str(candidate), font_size)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default(size=font_size)
    text = " ".join(settings.text.split())
    stroke = max(1, round(scale))
    box = font.getbbox(text, stroke_width=stroke) if text else (0, 0, 0, 0)
    text_w, text_h = box[2] - box[0], box[3] - box[1]
    gap = round(12 * scale) if text else 0
    badge = Image.new("RGBA", (size + gap + text_w, max(size, text_h)), (0, 0, 0, 0))
    avatar_name = Path(str(data.get("card_avatar_path") or "")).name
    avatar = folder / avatar_name if avatar_name else None
    if not avatar or not avatar.is_file():
        avatar = next((p for p in sorted(folder.iterdir())
                       if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")), None)
    if avatar and avatar.resolve().parent == folder:
        with Image.open(avatar) as image:
            image = image.convert("RGBA")
            image.thumbnail((size, size), Image.Resampling.LANCZOS)
            badge.alpha_composite(image, ((size-image.width)//2, (badge.height-image.height)//2))
    else:
        ImageDraw.Draw(badge).rounded_rectangle((0, 0, size-1, size-1), radius=size//5,
                                                fill=data.get("card_avatar_bg", "#a78bfa"))
    if text:
        ImageDraw.Draw(badge).text((size+gap-box[0], (badge.height-text_h)//2-box[1]), text,
                                  font=font, fill="white", stroke_width=stroke,
                                  stroke_fill=(0, 0, 0, 220))
    max_width = max(1, width - min(round(settings.x * scale), width-1))
    if badge.width > max_width:
        badge.thumbnail((max_width, badge.height), Image.Resampling.LANCZOS)
    badge.putalpha(badge.getchannel("A").point(lambda a: round(a * settings.opacity / 100)))
    x = min(round(settings.x * scale), width-badge.width)
    y = min(round(settings.y * height / 1920), height-badge.height)
    return badge, max(0, x), max(0, y)


def add_channel_branding_filter(inputs, filters, current_video, settings, width, height, badge_path):
    """Append the badge input/filter; disabled settings leave the graph untouched."""
    result = build_channel_badge(settings, width, height)
    if result is None:
        return current_video
    image, x, y = result
    image.save(badge_path)
    index = inputs.count("-i")
    inputs.extend(["-i", str(badge_path)])
    filters.append(f"{current_video}[{index}:v]overlay={x}:{y}:eof_action=repeat:repeatlast=1[v_brand]")
    return "[v_brand]"
