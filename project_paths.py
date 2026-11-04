"""Canonical export layout shared by all render entry points."""
from pathlib import Path
from urllib.parse import quote
ROOT=Path(__file__).resolve().parent
OUTPUT_ROOT=ROOT/'output'
APP_OUTPUT_DIR=OUTPUT_ROOT/'videos'/'Por_clasificar'
WORK_OUTPUT_DIR=OUTPUT_ROOT/'trabajo'
MANAGEMENT_DIR=OUTPUT_ROOT/'gestion'
def output_url(path, root=None):
    relative=Path(path).resolve().relative_to(Path(root or OUTPUT_ROOT).resolve())
    return '/media/output/'+quote(relative.as_posix(),safe='/')
def app_output_url(filename):
    return output_url(APP_OUTPUT_DIR/filename)


# --- Safe folder names (session ids under temp/v3_cache, etc.) -------------------
_FORBIDDEN_NAME_CHARS = set('<>:"/\\|?*')


def validate_folder_name(name, max_len=200):
    """Return ``name`` stripped if it is ONE safe folder name, else raise ValueError.

    Accepts any valid Windows folder name (Unicode letters, digits, '_', '-', spaces,
    dots, brackets...), so legacy session ids keep working. Rejects path separators,
    '..', drive colons, wildcard/control characters and names ending in '.' or ' '
    (Windows silently strips those, which would alias another folder).
    """
    sid = str(name or '').strip()
    if (not sid or len(sid) > max_len or '..' in sid or sid.endswith(('.', ' '))
            or any(ch in _FORBIDDEN_NAME_CHARS or ord(ch) < 32 for ch in sid)):
        raise ValueError('Nombre de carpeta inválido')
    return sid


def safe_child_dir(parent, name):
    """``parent/name`` validated to stay a direct child of ``parent`` (resolved).

    A link/junction the user created inside ``parent`` may point elsewhere; any
    other escape raises ValueError.
    """
    import os
    sid = validate_folder_name(name)
    root = Path(parent).resolve()
    cand = root / sid
    if Path(os.path.abspath(str(cand))).parent != root:
        raise ValueError('Nombre de carpeta inválido')
    try:
        resolved = cand.resolve()
    except OSError:
        resolved = cand
    if resolved.parent != root:
        is_link = cand.is_symlink() or bool(getattr(cand, 'is_junction', lambda: False)())
        if not is_link:
            raise ValueError('Nombre de carpeta inválido')
    return cand
