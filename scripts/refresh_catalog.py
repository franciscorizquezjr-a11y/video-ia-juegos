"""Update only public RSS metadata, preserving prior status/custom fields."""
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
from scraper import TrailerDatabaseManager, extract_game_name
from catalog_policy import load_feed_sources

now = datetime.now(timezone.utc)
stamp = now.strftime("%Y%m%d_%H%M%S")
audit = ROOT / "data" / "catalog_refresh"
audit.mkdir(parents=True, exist_ok=True)
dbpath = ROOT / "data" / "trailers_db.json"
original = dbpath.read_bytes()
(audit / (stamp + "_before.json")).write_bytes(original)
mgr = TrailerDatabaseManager()
before = {r["id"]: dict(r) for r in mgr.trailers}
report = {"started_at":now.isoformat(), "before_count":len(before), "sources":[],
          "coverage":"Latest entries exposed by each public RSS; not a historical backfill or complete YouTube index."}
ns = {"a":"http://www.w3.org/2005/Atom","yt":"http://www.youtube.com/xml/schemas/2015","media":"http://search.yahoo.com/mrss/"}
aliases = {"mycopunk":"Mycopunk", "ballxpit":"BALL x PIT", "ball x pit":"BALL x PIT",
           "cult of the lamb":"Cult of the Lamb", "hokko spaces":"Hokko Spaces",
           "worm": "Worms: Galactic Tactics", "talos principle":"The Talos Principle 3"}
for source in load_feed_sources():
    row={"name":source["name"],"url":source["rss_url"],"new":0,"updated":0}
    try:
        response=requests.get(source["rss_url"],timeout=25)
        response.raise_for_status()
        root=ET.fromstring(response.content)
        row["feed_title"]=root.findtext("a:title",namespaces=ns)
        entries=root.findall("a:entry",ns)
        row["returned_entries"]=len(entries)
        for entry in entries:
            vid=entry.findtext("yt:videoId",namespaces=ns)
            title=entry.findtext("a:title",namespaces=ns)
            published=entry.findtext("a:published",namespaces=ns)
            if not vid or not title or not published:continue
            when=datetime.fromisoformat(published.replace("Z","+00:00"))
            if when < now-timedelta(days=45):continue
            game=next((name for token,name in aliases.items() if token in title.casefold()),None) or extract_game_name(title)
            data={"id":vid,"title":title,"game_name":game,"channel":source["name"],
                  "url":"https://www.youtube.com/watch?v="+vid,
                  "published_at":published,"preset_hint":source.get("preset_hint","NextLoot")}
            added=mgr.add_or_update_trailer(data)
            item=next(r for r in mgr.trailers if r["id"]==vid)
            stats=entry.find(".//media:statistics",ns)
            item.update(source_channel_id=source["channel_id"],source_method="youtube_rss",
                        source_rights_status=source["rights_status"],metadata_refreshed_at=now.isoformat(),
                        content_kind="trailer" if "trailer" in title.casefold() else "news_or_short")
            if stats is not None and stats.get("views","").isdigit():
                item.update(observed_view_count=int(stats.get("views")),metrics_observed_at=now.isoformat())
            row["new" if added else "updated"]+=1
        row["status"]="ok"
    except Exception as exc:
        row.update(status="error",error=type(exc).__name__+": "+str(exc)[:180])
    report["sources"].append(row)
    print(json.dumps(row,ensure_ascii=False),flush=True)
# Validate no old record or prior workflow state has disappeared.
current={r["id"]:r for r in mgr.trailers}
assert set(before)<=set(current)
for vid,row in before.items():
    for key in ("status","local_file_path","output_video"):
        if key in row:assert current[vid].get(key)==row[key]
assert len(current)==len(mgr.trailers)
temporary=dbpath.with_name("trailers_db.refresh.pending.json")
temporary.write_text(json.dumps(mgr.trailers,ensure_ascii=False,indent=2),encoding="utf-8")
temporary.replace(dbpath)
report.update(after_count=len(mgr.trailers),new_count=len(mgr.trailers)-len(before),finished_at=datetime.now(timezone.utc).isoformat())
(audit/(stamp+"_report.json")).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
(audit/"latest_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print("CATALOG",report["before_count"],"->",report["after_count"],flush=True)
