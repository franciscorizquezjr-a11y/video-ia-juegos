import sys,json,urllib.request,datetime,time
from pathlib import Path
ROOT=Path(sys.argv[1]);P=ROOT/"output/gestion/semana_2026-10-26"
briefs=json.loads((P/"briefs.json").read_text(encoding="utf-8"))
rows=[]
for b in briefs:
    m=json.loads((ROOT/"input/semana_2026-10-26"/(b["slug"]+".source.json")).read_text(encoding="utf-8"))
    url="https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid="+str(m["steam_appid"])
    row={"game":m["name"],"steam_appid":m["steam_appid"],"source_url":url,"observed_at_utc":datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        with urllib.request.urlopen(url,timeout=20) as r:d=json.load(r)
        row["current_steam_players"]=d["response"].get("player_count")
        row["result"]=d["response"].get("result")
    except Exception as e:row["error"]=str(e)[:160]
    rows.append(row)
    print(row["game"],row.get("current_steam_players",row.get("error")),flush=True)
    time.sleep(.25)
(P/"steam_activity_snapshot.json").write_text(json.dumps({"notes":["Concurrent Steam players at one point in time, not YouTube searches, sales or a trend ranking.","Upcoming games may have no public player count; this does not imply no demand."],"games":rows},ensure_ascii=False,indent=2),encoding="utf-8")

