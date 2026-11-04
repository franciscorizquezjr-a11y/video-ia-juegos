"""
scraper.py — YouTube Trailer Scraping & Pre-Production Database Manager

Handles:
- Fetching latest trailer videos from target YouTube channels (Indie Games Hub, GameTrailers).
- Timeframe filtering (e.g. 24 hours, 3 days, 7 days).
- Extracting clean Game Names from video titles.
- Storing and managing trailer metadata in a local JSON database (data/trailers_db.json).
"""

import json
import logging
import os
import re
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

import json_store
from json_store import JsonStoreError

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "trailers_db.json"

TARGET_CHANNELS = [
    {
        "name": "Indie Games Hub",
        "handle": "@IndieHub01",
        "channel_id": "UCt8CJiye87FMbEwt0npPMuQ",
        "rss_url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCt8CJiye87FMbEwt0npPMuQ",
        "preset_hint": "IGD"
    },
    {
        "name": "GameTrailers (IGN)",
        "handle": "@GameTrailers",
        "channel_id": "UCJx5KP-pCUmL9eZUv-mIcNw",
        "rss_url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCJx5KP-pCUmL9eZUv-mIcNw",
        "preset_hint": "GUAM"
    }
]


# Reviewed public RSS discovery sources; footage rights remain separate.
from catalog_policy import load_feed_sources
if (BASE_DIR / "data" / "trailer_sources.json").is_file():
    TARGET_CHANNELS = load_feed_sources()


def extract_game_name(video_title: str) -> str:
    """Extract clean game title from a YouTube trailer video title.

    Examples:
        'BOREWORKS - Official Gameplay Trailer' -> 'BOREWORKS'
        'Hot Wheels Infinite Rush - Official Launch Trailer' -> 'Hot Wheels Infinite Rush'
        'Tomb Raider: Legacy of Atlantis - Official Overview' -> 'Tomb Raider: Legacy of Atlantis'
        'Grand Theft Auto VI Trailer 1' -> 'Grand Theft Auto VI'
        'Cyberpunk 2077: Phantom Liberty — Official Gameplay Trailer' -> 'Cyberpunk 2077: Phantom Liberty'
        'SILENT HILL 2 | Teaser Trailer | KONAMI' -> 'SILENT HILL 2'
    """
    if not video_title:
        return "Juego Desconocido"

    # 1. Split by strong separators: dash, en-dash, em-dash, pipe (|)
    parts = re.split(r"\s+[-|\u2013\u2014]\s+|\s*\|\s*", video_title, maxsplit=1)
    if len(parts) > 1 and len(parts[0].strip()) > 1:
        first_part = parts[0].strip()
    else:
        # Check colon followed by trailer-related terms (preserving subtitles like 'Cyberpunk 2077: Phantom Liberty')
        colon_trailer = re.split(r":\s+(?=(?:official|gameplay|launch|teaser|trailer|overview|announcement|reveal))", video_title, maxsplit=1, flags=re.IGNORECASE)
        if len(colon_trailer) > 1 and len(colon_trailer[0].strip()) > 1:
            first_part = colon_trailer[0].strip()
        else:
            first_part = video_title.strip()

    # Remove quotes or brackets if present
    cleaned = re.sub(r'^["\'\u201c\u00ab]+|["\'\u201d\u00bb]+$', "", first_part).strip()

    # If first part looks like 'Official Trailer', fallback to title cleaning
    if len(cleaned) < 2 or cleaned.lower() in ("official trailer", "gameplay trailer", "teaser"):
        cleaned = re.sub(
            r"(?i)\s*[-|\u2013\u2014]?\s*(official\s+)?(gameplay\s+)?(launch\s+)?(announcement\s+)?(teaser|trailer|overview|game|kickstarter).*",
            "",
            video_title
        ).strip()

    # Strip trailing Trailer/Teaser/Gameplay suffixes even if no hyphen separator was present
    cleaned = re.sub(
        r"(?i)\s*[-|\u2013\u2014:]?\s*(official\s+)?(gameplay\s+)?(launch\s+)?(announcement\s+)?(reveal\s+)?(teaser|trailer|overview)(\s+\d+)?$",
        "",
        cleaned
    ).strip()

    return cleaned if cleaned else video_title.strip()


class TrailerDatabaseManager:
    """Manager for loading, updating, and querying trailer items in local storage."""

    def __init__(self, db_file: Path = DB_PATH) -> None:
        self.db_file = Path(db_file)
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        # Set when the DB file existed but could not be read. If the corrupt file
        # could not be moved aside, saving is disabled so it is never overwritten.
        self.load_error: str | None = None
        self._save_blocked = False
        self.trailers = self._load()

    def _deduplicate(self, items: list[dict]) -> list[dict]:
        """Ensure list contains only unique video IDs, preserving order."""
        seen_ids = set()
        unique = []
        for t in items:
            vid_id = t.get("id")
            if vid_id and vid_id not in seen_ids:
                seen_ids.add(vid_id)
                unique.append(t)
        return unique

    def _load(self) -> list[dict]:
        """Load JSON database file.

        A missing file means an empty DB. A corrupt/unreadable file is moved aside to
        ``<name>.corrupt-<timestamp>`` by json_store (never deleted); the manager then
        starts empty. If it could not be moved aside, saving is blocked so the
        original is never overwritten with an empty list.
        """
        try:
            data = json_store.load_json(self.db_file, [])
        except JsonStoreError as e:
            logger.error(f"Failed to read trailer DB at {self.db_file}: {e}")
            self.load_error = str(e)
            self._save_blocked = e.quarantined_path is None
            return []
        if not isinstance(data, list):
            logger.error(f"Trailer DB at {self.db_file} is not a JSON list; it will not be modified.")
            self.load_error = f"{self.db_file.name} no contiene una lista JSON"
            self._save_blocked = True
            return []
        return self._deduplicate(data)

    def save(self) -> None:
        """Save trailers list to JSON database file (atomic write)."""
        if self._save_blocked:
            logger.error(
                f"Not saving trailer DB at {self.db_file}: the existing file could not be read "
                f"and was kept untouched ({self.load_error})."
            )
            return
        try:
            self.trailers = self._deduplicate(self.trailers)
            json_store.save_json_atomic(self.db_file, self.trailers)
        except Exception as e:
            logger.error(f"Failed to save trailer DB at {self.db_file}: {e}")

    def add_or_update_trailer(self, video_data: dict) -> bool:
        """Add a new trailer or update existing trailer item. Return True if new."""
        vid_id = video_data.get("id")
        if not vid_id:
            return False

        existing = next((t for t in self.trailers if t["id"] == vid_id), None)
        if existing:
            existing["title"] = video_data.get("title", existing.get("title"))
            existing["game_name"] = video_data.get("game_name", existing.get("game_name"))
            existing["channel"] = video_data.get("channel", existing.get("channel"))
            existing["url"] = video_data.get("url", existing.get("url"))
            existing["thumbnail"] = video_data.get("thumbnail", existing.get("thumbnail"))
            if video_data.get("published_at"):
                existing["published_at"] = video_data["published_at"]
            for key in ("source_channel_id", "source_method", "rights_status", "metadata_refreshed_at", "content_kind"):
                if key in video_data:
                    existing[key] = video_data[key]
            return False
        else:
            new_item = {
                "id": vid_id,
                "title": video_data.get("title", ""),
                "game_name": video_data.get("game_name", "Juego Desconocido"),
                "channel": video_data.get("channel", "YouTube"),
                "url": video_data.get("url", f"https://www.youtube.com/watch?v={vid_id}"),
                "thumbnail": video_data.get("thumbnail", f"https://img.youtube.com/vi/{vid_id}/hqdefault.jpg"),
                "status": "feed",
                "preset_hint": video_data.get("preset_hint", "IGD"),
                "published_at": video_data.get("published_at", ""),
                "scraped_at": datetime.now().isoformat()
            }
            for key in ("source_channel_id", "source_method", "rights_status", "metadata_refreshed_at", "content_kind"):
                if key in video_data:
                    new_item[key] = video_data[key]
            self.trailers.insert(0, new_item)
            return True

    def set_status(self, video_id: str, status: str) -> None:
        """Set trailer status ('feed', 'archived')."""
        for t in self.trailers:
            if t["id"] == video_id:
                t["status"] = status
                break
        self.save()

    def clear_all(self) -> None:
        """Delete all trailer entries from the database."""
        self.trailers = []
        self.save()

    def get_feed_items(self, channel_filter: str | None = None, search_query: str | None = None) -> list[dict]:
        """Get active feed items (not archived), sorted by scraped_at descending."""
        items = [t for t in self.trailers if t.get("status") != "archived"]
        if channel_filter and channel_filter != "Todos":
            items = [t for t in items if t.get("channel") == channel_filter]
        if search_query:
            query = search_query.lower()
            items = [
                t for t in items
                if query in t.get("game_name", "").lower() or query in t.get("title", "").lower()
            ]
        return items


def scrape_channel_trailers_rss(channel_info: dict, timeframe_hours: int = 24) -> list[dict]:
    """Scrape trailers uploaded within timeframe_hours (24, 72, or 168 hours) using channel RSS feed."""
    rss_url = channel_info.get("rss_url")
    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=timeframe_hours)
    scraped = []

    if not rss_url:
        return []

    try:
        req = urllib.request.Request(
            rss_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "yt": "http://www.youtube.com/xml/schemas/2015"
        }

        for entry in root.findall("atom:entry", ns):
            title_elem = entry.find("atom:title", ns)
            pub_elem = entry.find("atom:published", ns)
            vid_elem = entry.find("yt:videoId", ns)

            if title_elem is None or pub_elem is None or vid_elem is None:
                continue

            vid_id = vid_elem.text
            title = title_elem.text
            pub_str = pub_elem.text

            try:
                pub_dt = datetime.fromisoformat(pub_str)
                if pub_dt < cutoff_dt:
                    continue
            except Exception:
                pass

            game_name = extract_game_name(title)
            scraped.append({
                "id": vid_id,
                "title": title,
                "game_name": game_name,
                "channel": channel_info["name"],
                "url": f"https://www.youtube.com/watch?v={vid_id}",
                "thumbnail": f"https://img.youtube.com/vi/{vid_id}/hqdefault.jpg",
                "preset_hint": channel_info.get("preset_hint", "IGD"),
                "published_at": pub_str,
                "source_channel_id": channel_info.get("channel_id", ""),
                "source_method": "youtube_rss",
                "rights_status": channel_info.get("rights_status", "unknown_check_rightsholder"),
                "metadata_refreshed_at": datetime.now(timezone.utc).isoformat(),
                "content_kind": "trailer" if re.search(r"\b(trailer|teaser)\b", title, re.I) else "news_or_short"
            })

        return scraped
    except Exception as e:
        logger.error(f"Error reading RSS feed for {channel_info['name']}: {e}")
        return []


def refresh_all_trailers(timeframe_hours: int = 24) -> tuple[int, int]:
    """Scrape all target channels for videos uploaded in the last timeframe_hours.

    Args:
        timeframe_hours: Hours to look back (24 = 24h, 72 = 3 days, 168 = 7 days).

    Returns:
        Tuple of (new_count, total_count).
    """
    db = TrailerDatabaseManager()
    new_count = 0

    for ch in TARGET_CHANNELS:
        scraped_list = scrape_channel_trailers_rss(ch, timeframe_hours=timeframe_hours)
        for item in scraped_list:
            if db.add_or_update_trailer(item):
                new_count += 1

    db.save()
    return new_count, len(db.trailers)
