"""Require an existing game logo; never manufacture a title image as fallback."""
from pathlib import Path
from setup_trailer_logo import resolve_game_logo, readable_logo

ROOT = Path(__file__).resolve().parent

def require_game_logo(game_name, custom_path=None, root=ROOT, allow_download=True):
    name = str(game_name or "").strip()
    if not name:
        raise ValueError("Escribe el nombre del juego y añade su logo oficial antes de renderizar.")
    if custom_path:
        candidate = Path(custom_path)
        if readable_logo(candidate):
            return candidate.resolve()
        raise ValueError("El logo seleccionado está dañado o no existe. Sube el logo oficial del juego.")
    candidate = resolve_game_logo(name, Path(root), allow_download=allow_download)
    if candidate and readable_logo(candidate):
        return candidate.resolve()
    raise ValueError(f"Falta el logo oficial de {name}. Añádelo antes de renderizar; no se sustituirá por texto.")
