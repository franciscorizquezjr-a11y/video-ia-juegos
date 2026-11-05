"""Portable font resolution for the PIL renderers (cards, overlays, badges).

The renderers were written for Windows and used to hardcode
``C:\\Windows\\Fonts\\<file>``. This module keeps that behaviour on Windows
(the same files are chosen first) and adds sensible lookups for Linux and
macOS so the same code renders readable text everywhere.

Search order, first match wins:

1. ``%WINDIR%\\Fonts`` (Windows system fonts; checked whenever WINDIR is set,
   defaulting to ``C:\\Windows`` on Windows) and then the per-user Windows font
   folder ``%LOCALAPPDATA%\\Microsoft\\Windows\\Fonts``.
2. Linux font folders: ``/usr/share/fonts/**``, ``/usr/local/share/fonts/**``,
   ``~/.local/share/fonts/**`` and ``~/.fonts/**``.
3. macOS font folders: ``/Library/Fonts``, ``~/Library/Fonts`` and
   ``/System/Library/Fonts/**``.

When none of the requested file names exists, a few metric-compatible aliases
are tried (for example Liberation Sans for Arial), then DejaVu Sans
(``DejaVuSans-Bold.ttf`` when ``bold``), and :func:`load_font` finally falls
back to ``PIL.ImageFont.load_default()``.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Sequence, Union

FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")

# Free / macOS equivalents tried only after every requested name failed in
# every folder, so Windows keeps picking exactly the same files as before.
FONT_ALIASES = {
    "arial.ttf": ("LiberationSans-Regular.ttf", "Arial.ttf", "Arimo-Regular.ttf"),
    "arialbd.ttf": ("LiberationSans-Bold.ttf", "Arial Bold.ttf", "Arial_Bold.ttf", "Arimo-Bold.ttf"),
    "ariblk.ttf": ("Arial Black.ttf", "Arial_Black.ttf"),
    "impact.ttf": ("Impact.ttf",),
    "calibri.ttf": ("Carlito-Regular.ttf",),
    "calibrib.ttf": ("Carlito-Bold.ttf",),
    "times.ttf": ("LiberationSerif-Regular.ttf", "Times New Roman.ttf", "Tinos-Regular.ttf"),
    "timesbd.ttf": ("LiberationSerif-Bold.ttf", "Times New Roman Bold.ttf", "Tinos-Bold.ttf"),
    "georgia.ttf": ("Georgia.ttf", "Gelasio-Regular.ttf"),
    "georgiab.ttf": ("Georgia Bold.ttf", "Gelasio-Bold.ttf"),
    "consolas.ttf": ("LiberationMono-Regular.ttf", "Cousine-Regular.ttf"),
    "consolab.ttf": ("LiberationMono-Bold.ttf", "Cousine-Bold.ttf"),
    "comic.ttf": ("Comic Sans MS.ttf", "Comic_Sans_MS.ttf"),
    "comicbd.ttf": ("Comic Sans MS Bold.ttf", "Comic_Sans_MS_Bold.ttf"),
}

DEJAVU_REGULAR = "DejaVuSans.ttf"
DEJAVU_BOLD = "DejaVuSans-Bold.ttf"

FontNames = Union[str, os.PathLike, Sequence[Union[str, os.PathLike]]]


def windows_font_dirs() -> List[Path]:
    """Windows font folders in priority order (system first, then per-user)."""
    dirs: List[Path] = []
    windir = os.environ.get("WINDIR") or os.environ.get("windir")
    if not windir and os.name == "nt":
        windir = r"C:\Windows"
    if windir:
        dirs.append(Path(windir) / "Fonts")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        dirs.append(Path(local_app_data) / "Microsoft" / "Windows" / "Fonts")
    return dirs


def unix_font_dirs() -> List[Path]:
    """Linux and macOS font folders in priority order (scanned recursively)."""
    dirs = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]
    try:
        home: Optional[Path] = Path.home()
    except (RuntimeError, KeyError, OSError):  # no resolvable home (service accounts)
        home = None
    data_home = os.environ.get("XDG_DATA_HOME") or (str(home / ".local" / "share") if home else "")
    if data_home:
        dirs.append(Path(data_home) / "fonts")
    if home:
        dirs.append(home / ".fonts")
    dirs.append(Path("/Library/Fonts"))
    if home:
        dirs.append(home / "Library" / "Fonts")
    dirs.append(Path("/System/Library/Fonts"))
    return dirs


def font_search_dirs() -> List[Path]:
    """All font folders in the order they are searched."""
    return windows_font_dirs() + unix_font_dirs()


@lru_cache(maxsize=16)
def _font_index(dirs: tuple) -> dict:
    """Map lowercase file name -> path for every font file under ``dirs``.

    Earlier folders win when the same file name exists in several places.
    """
    index: dict = {}
    for raw in dirs:
        directory = Path(raw)
        if not directory.is_dir():
            continue
        for root, _subdirs, files in os.walk(directory):
            for file_name in sorted(files):
                if file_name.lower().endswith(FONT_EXTENSIONS):
                    index.setdefault(file_name.lower(), str(Path(root) / file_name))
    return index


def clear_font_cache() -> None:
    """Forget the scanned font folders (useful after installing fonts or in tests)."""
    _font_index.cache_clear()


def _as_name_list(names: FontNames) -> List[str]:
    if isinstance(names, (str, os.PathLike)):
        names = [names]
    return [os.fspath(name) for name in names if name]


def _name_variants(name: str) -> List[str]:
    """``name`` itself, plus .ttf/.otf variants when no extension was given."""
    if Path(name).suffix.lower() in FONT_EXTENSIONS:
        return [name]
    return [name + ext for ext in (".ttf", ".otf")]


def iter_font_candidates(
    names: FontNames,
    bold: bool = False,
    search_dirs: Optional[Iterable[Union[str, os.PathLike]]] = None,
    include_fallback: bool = True,
) -> Iterator[str]:
    """Yield existing font paths in priority order (no duplicates).

    ``search_dirs`` replaces the default folders (Windows folders must then be
    passed explicitly as well); it exists mainly for tests.
    """
    requested = _as_name_list(names)
    if search_dirs is None:
        win_dirs = windows_font_dirs()
        other_dirs = unix_font_dirs()
    else:
        win_dirs = []
        other_dirs = [Path(d) for d in search_dirs]
    index = _font_index(tuple(str(d) for d in other_dirs))
    seen = set()

    def emit(path: str) -> Optional[str]:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return None
        seen.add(key)
        return path

    def lookup(file_names: List[str]) -> Iterator[str]:
        # Windows folders keep the exact historic priority: folder first,
        # then names in the order given by the caller.
        for directory in win_dirs:
            for file_name in file_names:
                for variant in _name_variants(Path(file_name).name):
                    candidate = directory / variant
                    if candidate.is_file():
                        hit = emit(str(candidate))
                        if hit:
                            yield hit
        # Other platforms: caller priority first, case-insensitive match.
        for file_name in file_names:
            for variant in _name_variants(Path(file_name).name):
                hit_path = index.get(variant.lower())
                if hit_path:
                    hit = emit(hit_path)
                    if hit:
                        yield hit

    # Absolute (or relative existing) paths given by the caller come first.
    plain_names: List[str] = []
    for name in requested:
        path = Path(name)
        if (path.is_absolute() or len(path.parts) > 1) and path.is_file():
            hit = emit(str(path))
            if hit:
                yield hit
        plain_names.append(path.name)

    yield from lookup(plain_names)

    aliases: List[str] = []
    for name in plain_names:
        aliases.extend(FONT_ALIASES.get(name.lower(), ()))
    if aliases:
        yield from lookup(aliases)

    if include_fallback:
        fallback = [DEJAVU_BOLD, DEJAVU_REGULAR] if bold else [DEJAVU_REGULAR, DEJAVU_BOLD]
        yield from lookup(fallback)


def find_font(
    names: FontNames,
    bold: bool = False,
    search_dirs: Optional[Iterable[Union[str, os.PathLike]]] = None,
    include_fallback: bool = True,
) -> Optional[str]:
    """Return the path of the first available font among ``names``.

    ``names`` are file names in priority order (e.g. ``["impact.ttf",
    "ariblk.ttf"]``). Falls back to DejaVu Sans (bold when ``bold``) unless
    ``include_fallback`` is False. Returns ``None`` when nothing is installed;
    :func:`load_font` then uses PIL's built-in default font.
    """
    for path in iter_font_candidates(names, bold=bold, search_dirs=search_dirs,
                                     include_fallback=include_fallback):
        return path
    return None


def load_default_font(size: Optional[float] = None):
    """PIL's built-in font, sized when the installed Pillow supports it (>=10.1)."""
    from PIL import ImageFont

    if size:
        try:
            return ImageFont.load_default(size=max(1, int(size)))
        except TypeError:
            pass
    return ImageFont.load_default()


def load_font(
    names: FontNames,
    size: float,
    bold: bool = False,
    search_dirs: Optional[Iterable[Union[str, os.PathLike]]] = None,
):
    """Load the first usable font among ``names`` at ``size`` pixels.

    Never raises for a missing font: unreadable candidates are skipped and the
    final fallback is :func:`load_default_font`.
    """
    from PIL import ImageFont

    pixel_size = max(1, int(size))
    for path in iter_font_candidates(names, bold=bold, search_dirs=search_dirs):
        try:
            return ImageFont.truetype(path, pixel_size)
        except OSError:
            continue
    return load_default_font(pixel_size)
