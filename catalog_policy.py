"""Editorial cooldown and validated public-feed configuration; no network calls."""
import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
ACTIVE_STATUSES = {"published", "scheduled", "publicado", "programado"}

def game_key(game):
    text = unicodedata.normalize("NFKD", str(game)).encode("ascii", "ignore").decode().casefold()
    key = re.sub(r"[^a-z0-9]", "", text)
    return {"escapeacademy2back2school": "escapeacademy2"}.get(key, key)

def day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])

def cooldown_check(game, brand, planned_date, history, days=21, piece_id=None):
    """Per-brand cooldown; the same piece may be distributed across its networks."""
    target = day(planned_date)
    conflicts = []
    for row in history:
        if row.get("status", "").casefold() not in ACTIVE_STATUSES:
            continue
        if row.get("brand") != brand or game_key(row.get("game", "")) != game_key(game):
            continue
        if piece_id and row.get("piece_id") == piece_id:
            continue
        value = row.get("published_at") or row.get("planned_at") or row.get("date")
        if not value:
            continue
        delta = (target - day(value)).days
        if 0 <= delta < days:
            conflicts.append(row)
    return {"eligible": not conflicts, "cooldown_days": days,
            "next_eligible_date": max((day(r.get("published_at") or r.get("planned_at") or r["date"]) + timedelta(days=days)).isoformat() for r in conflicts) if conflicts else None,
            "conflicts": conflicts}

def load_feed_sources(path=None):
    path = Path(path or ROOT / "data" / "trailer_sources.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    result = []
    seen = set()
    for row in data["sources"]:
        if not row.get("enabled", False):
            continue
        cid = row.get("channel_id", "")
        if not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", cid):
            raise ValueError("Invalid YouTube channel ID")
        url = urlparse(row.get("rss_url", ""))
        if url.scheme != "https" or url.hostname != "www.youtube.com" or url.path != "/feeds/videos.xml" or parse_qs(url.query).get("channel_id") != [cid]:
            raise ValueError("Only the declared YouTube public RSS feed is allowed")
        if cid in seen:
            continue
        seen.add(cid)
        result.append(dict(row))
    return result
