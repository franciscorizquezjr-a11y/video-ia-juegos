"""Per-game logo resolution and layout for the reusable monitor format."""
import hashlib
import io
import json
import math
import re
import unicodedata
from pathlib import Path, PureWindowsPath
from PIL import Image

ROOT=Path(__file__).resolve().parent


def game_key(name):
    normalized=unicodedata.normalize("NFKD",name or "").casefold()
    return "".join(c for c in normalized if c.isalnum())


def logo_directory(name,root=ROOT):
    key=game_key(name)
    if not key:
        raise ValueError("Escribe el nombre del juego antes de añadir su logo.")
    return root/"temp"/"game_logos_official"/hashlib.sha256(key.encode()).hexdigest()[:20]


def valid_logo(path):
    try:
        with Image.open(path) as im:
            return 0 < im.width <= 8192 and 0 < im.height <= 8192 and im.convert("RGBA").getchannel("A").getbbox() is not None
    except Exception:
        return False


def readable_logo(path):
    """A damaged or inaccessible cache entry must not prevent a video render."""
    try:
        return path.is_file() and valid_logo(path)
    except OSError:
        return False


def transparent_logo(path):
    """Prefer logos with real transparency over rectangular trailer captures."""
    try:
        with Image.open(path) as image:
            alpha=image.convert("RGBA").getchannel("A")
            return alpha.getbbox() is not None and alpha.getextrema()[0] < 255
    except (OSError,ValueError):
        return False


def resolve_game_logo(name,root=ROOT,allow_download=True):
    fallback_candidates=[]
    key=game_key(name)
    if not key:
        return fallback_candidates[0] if fallback_candidates else None
    cache=logo_directory(name,root)
    # Read legacy entries, but write new logos outside the damaged old cache.
    legacy=root/"temp"/"setup_trailer_logos"/hashlib.sha256(key.encode()).hexdigest()[:20]
    for file in (cache/"custom.png",legacy/"custom.png"):
        if readable_logo(file):
            return file
    for parent in (root/"PromosFootage",root/"GamesFootage"):
        for folder in parent.iterdir() if parent.exists() else []:
            if folder.is_dir() and game_key(folder.name)==key:
                for file in (folder/"custom_game_logo.png",folder/"game_logo.png",folder/"logo.png"):
                    if readable_logo(file):
                        if file.name == "custom_game_logo.png" or transparent_logo(file):
                            return file
                        fallback_candidates.append(file)
    # Match session metadata; never take an arbitrary global/custom logo from another game.
    sessions=root/"temp"/"v3_cache"
    for meta in sorted(sessions.glob("*/step3_script.json")) if sessions.exists() else []:
        try:
            data=json.loads(meta.read_text(encoding="utf-8"))
            session_name=data.get("game_name")
            if not session_name:
                video=data.get("video_path") or next(iter(data.get("video_paths") or []),"")
                session_name=PureWindowsPath(video).parent.name if isinstance(video,str) else ""
            if game_key(session_name)!=key:
                continue
            file=meta.parent/"custom_game_logo.png"
            if readable_logo(file):
                return file
        except (OSError,ValueError,TypeError):
            continue
    for file in (cache/"official.png",legacy/"official.png"):
        if readable_logo(file):
            if transparent_logo(file):
                return file
            fallback_candidates.append(file)
    if not allow_download:
        return fallback_candidates[0] if fallback_candidates else None
    try:
        import requests
        response=requests.get("https://store.steampowered.com/api/storesearch/",params={"term":name,"l":"english","cc":"US"},timeout=4)
        response.raise_for_status()
        match=next((item for item in response.json().get("items",[]) if game_key(item.get("name"))==key),None)
        if not match or not str(match.get("id","")).isdigit():
            return fallback_candidates[0] if fallback_candidates else None
        url=f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{match['id']}/logo.png"
        response=requests.get(url,timeout=4)
        response.raise_for_status()
        if len(response.content)>10*1024*1024:
            return fallback_candidates[0] if fallback_candidates else None
        image=Image.open(io.BytesIO(response.content))
        if image.width>8192 or image.height>8192:
            return fallback_candidates[0] if fallback_candidates else None
        image=image.convert("RGBA")
        if image.getchannel("A").getbbox() is None:
            return fallback_candidates[0] if fallback_candidates else None
        cache.mkdir(parents=True,exist_ok=True)
        path=cache/"official.png"; image.save(path)
        return path
    except Exception:
        return fallback_candidates[0] if fallback_candidates else None


def build_logo_overlay(logo_path,output_path,corners,width,height,native_w,native_h,large=False,scale=1.0,gap=18,font_size=28):
    with Image.open(logo_path) as source:
        image=source.convert("RGBA")
    bounds=image.getchannel("A").getbbox()
    if not bounds:
        raise ValueError("El logo no contiene píxeles visibles.")
    image=image.crop(bounds)
    sx,sy=width/native_w,height/native_h
    if large:
        top=min(p["y"] for p in corners.values())*sy
        bottom=max(p["y"] for p in corners.values())*sy
        margin=height*.04
        above=max(0,top-height*.035-margin)
        below=max(0,height-bottom-margin*2)
        use_above=above>=height*.16 or above>=below
        available=above if use_above else below
        max_h=max(1,min(height*.26*scale,available*.9))
        max_w=width*min(.9,.7*scale)
        anchor_y=margin+above/2 if use_above else bottom+margin+below/2
        angle=0
        anchor_x=width/2
    else:
        tl,tr=corners["tl"],corners["tr"]
        angle=math.atan2((tr["y"]-tl["y"])*sy,(tr["x"]-tl["x"])*sx)
        max_w=width*.6;max_h=max(1,font_size*sx)
        anchor_x=(tl["x"]+tr["x"])/2*sx+gap*sx*math.sin(angle)
        anchor_y=(tl["y"]+tr["y"])/2*sy-gap*sy*math.cos(angle)-max_h/2
    ratio=min(max_w/image.width,max_h/image.height)
    image=image.resize((max(1,round(image.width*ratio)),max(1,round(image.height*ratio))),Image.Resampling.LANCZOS)
    if angle:
        image=image.rotate(-math.degrees(angle),resample=Image.Resampling.BICUBIC,expand=True)
    canvas=Image.new("RGBA",(width,height),(0,0,0,0))
    canvas.alpha_composite(image,(round(anchor_x-image.width/2),round(anchor_y-image.height/2)))
    canvas.save(output_path)
    return output_path
