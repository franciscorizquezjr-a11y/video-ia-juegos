"""json_store.py — Safe, atomic JSON persistence for shared local state.

Small helper used by the web server and the scraper to read and write the
JSON files under ``data/`` (calendar, schedule, games, uploaded videos,
trailer database...) without ever losing user data:

* ``load_json(path, default)`` returns ``default`` ONLY when the file does not
  exist. A corrupt or unreadable file is moved aside to
  ``<name>.corrupt-<timestamp>`` (never deleted) and a ``JsonStoreError`` is
  raised, so callers cannot accidentally follow a failed load with a save that
  wipes the original content.
* ``save_json_atomic(path, data)`` writes to a temporary file in the same
  directory and swaps it in with ``os.replace`` (atomic on Windows and POSIX),
  UTF-8 with ``ensure_ascii=False`` and ``indent=2``.
* ``update(path, default, fn)`` performs load → modify → save under the same
  per-path lock, so concurrent requests cannot interleave and drop writes.

Only the standard library is used; paths may be ``str`` or ``pathlib.Path``.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, "os.PathLike[str]", Path]

__all__ = [
    "JsonStoreError",
    "load_json",
    "save_json_atomic",
    "update",
    "lock_for",
]


class JsonStoreError(RuntimeError):
    """Raised when a JSON state file exists but cannot be read or parsed.

    ``quarantined_path`` points to the ``.corrupt-<timestamp>`` copy when the
    original file could be moved aside (``None`` if even that failed).
    """

    def __init__(self, message: str, path: Path, quarantined_path: Path | None = None):
        super().__init__(message)
        self.path = path
        self.quarantined_path = quarantined_path


_LOCKS_GUARD = threading.Lock()
_LOCKS: Dict[str, threading.RLock] = {}


def _key(path: PathLike) -> str:
    p = Path(path)
    try:
        p = p.resolve()
    except OSError:
        p = Path(os.path.abspath(str(p)))
    # Windows paths are case-insensitive: normalise so both spellings share a lock.
    return os.path.normcase(str(p))


def lock_for(path: PathLike) -> threading.RLock:
    """Return the re-entrant lock that guards ``path`` inside this process."""
    k = _key(path)
    with _LOCKS_GUARD:
        lk = _LOCKS.get(k)
        if lk is None:
            lk = threading.RLock()
            _LOCKS[k] = lk
        return lk


def _quarantine(path: Path) -> Path | None:
    """Move a corrupt file aside without deleting it. Returns the new path."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.corrupt-{stamp}")
    n = 1
    while target.exists():
        target = path.with_name(f"{path.name}.corrupt-{stamp}-{n}")
        n += 1
    try:
        os.replace(str(path), str(target))
        return target
    except OSError as move_err:
        # Moving failed (file locked by another program on Windows, read-only
        # folder...). Keep the original in place and try to leave a copy.
        logger.error("No se pudo apartar el JSON corrupto %s: %s", path, move_err)
        try:
            import shutil

            shutil.copy2(str(path), str(target))
            return target
        except OSError:
            return None


def load_json(path: PathLike, default: Any = None) -> Any:
    """Load JSON from ``path``.

    Returns a deep copy of ``default`` when the file does not exist. If the file
    exists but is unreadable or contains invalid JSON, it is moved aside to
    ``<name>.corrupt-<timestamp>`` and :class:`JsonStoreError` is raised.
    """
    p = Path(path)
    with lock_for(p):
        if not p.exists():
            return copy.deepcopy(default)
        try:
            with open(p, "r", encoding="utf-8-sig") as fh:
                return json.load(fh)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            quarantined = None
            if not isinstance(exc, PermissionError):
                quarantined = _quarantine(p)
            where = f" Se ha conservado una copia en '{quarantined.name}'." if quarantined else ""
            raise JsonStoreError(
                f"No se pudo leer el archivo de datos '{p.name}': {exc}.{where} "
                f"No se ha sobrescrito ningún dato.",
                p,
                quarantined,
            ) from exc


def save_json_atomic(path: PathLike, data: Any) -> None:
    """Atomically write ``data`` as UTF-8 JSON (``ensure_ascii=False``, ``indent=2``)."""
    p = Path(path)
    with lock_for(p):
        p.parent.mkdir(parents=True, exist_ok=True)
        # Serialise first so a non-serialisable payload never touches the disk.
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(payload)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass
            _replace_with_retry(tmp_name, str(p))
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


def _replace_with_retry(src: str, dst: str, attempts: int = 5) -> None:
    """``os.replace`` with a short retry: on Windows an antivirus or indexer can
    briefly hold the destination open and make the swap fail with
    PermissionError."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.05 * (i + 1))


def update(path: PathLike, default: Any, fn: Callable[[Any], Any]) -> Any:
    """Load → modify → save ``path`` under its lock and return the saved value.

    ``fn`` receives the current data (or a copy of ``default`` when the file
    does not exist) and may mutate it in place or return a new value. A load
    error propagates as :class:`JsonStoreError` and nothing is written.
    """
    p = Path(path)
    with lock_for(p):
        current = load_json(p, default)
        result = fn(current)
        new_data = current if result is None else result
        save_json_atomic(p, new_data)
        return new_data
