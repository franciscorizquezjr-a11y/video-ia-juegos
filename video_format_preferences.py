"""Persist a preferred single-game framing per channel, preserving other settings."""
import json,os,re,tempfile,threading
from pathlib import Path

FORMATS=("full_9_16","blur_3_4","blur_1_1")
LOCK=threading.RLock()

def _brand(value):
    value=str(value or "").strip()
    if not re.fullmatch(r"[\w -]{1,80}",value):
        raise ValueError("Nombre de canal no válido.")
    return value

def _load(path):
    path=Path(path)
    if not path.exists():return {}
    try:
        data=json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data,dict):raise ValueError()
        return data
    except (OSError,ValueError) as exc:
        raise ValueError("No se pueden leer las preferencias; no se han sobrescrito.") from exc

def preferred_video_format(path,brand="NextLoot"):
    brand=_brand(brand)
    with LOCK:
        row=_load(path).get(brand,{})
        value=row.get("single_game_video_format") if isinstance(row,dict) else None
    return value if value in FORMATS else "full_9_16"

def save_video_format(path,brand,value):
    brand=_brand(brand)
    if value not in FORMATS:raise ValueError("Formato no válido.")
    path=Path(path)
    with LOCK:
        data=_load(path)
        if brand in data and not isinstance(data[brand],dict):
            raise ValueError("Las preferencias del canal no son válidas; no se han sobrescrito.")
        row=data.setdefault(brand,{})
        row["single_game_video_format"]=value
        row["single_game_format_options"]=list(FORMATS)
        row["legacy_layouts_preserved"]=True
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,prefix="video_format_",suffix=".tmp",delete=False) as file:
                temporary=Path(file.name)
                json.dump(data,file,ensure_ascii=False,indent=2);file.write("\n");file.flush();os.fsync(file.fileno())
            os.replace(temporary,path)
        finally:
            if temporary and temporary.exists():temporary.unlink()
    return value
