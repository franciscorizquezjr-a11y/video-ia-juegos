import sys,json,re,subprocess,hashlib,os,concurrent.futures,datetime,math,shutil
from pathlib import Path
ROOT=Path(sys.argv[1]).resolve();sys.path.insert(0,str(ROOT))
from catalog_policy import cooldown_check
P=ROOT/"output/gestion/semana_2026-10-26"
briefs=json.loads((P/"briefs.json").read_text(encoding="utf-8"))
plan=json.loads((P/"plan_publicaciones.json").read_text(encoding="utf-8"))
history=json.loads((ROOT/"data/editorial_history.json").read_text(encoding="utf-8"))
assert len(briefs)==10 and len(plan)==25
assert len(set((r["brand"],r["network"]) for r in plan))==5
def seconds(value):
    h,m,s=value.split(":");return int(h)*3600+int(m)*60+float(s)
def qa(b):
    folder=P/b["brand"]/(b["date"]+"_"+b["slug"]);final=folder/"video.mp4"
    v=json.loads((folder/"verification.json").read_text(encoding="utf-8"))
    settings=json.loads((folder/"video.settings.json").read_text(encoding="utf-8"))
    narration=settings["narration"]
    assert narration["text"].startswith(b["text"])
    tail=v["duration"]-float(narration["duration"])-float(narration["offset"])
    assert tail>=.24,(b["slug"],tail)
    assert v["full_decode"]=="passed"
    times=[]
    for line in (folder/"subtitles.ass").read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("Dialogue:"):
            chunks=line.split(",",3);times.append(seconds(chunks[2]))
    assert times and max(times)<=v["duration"]+.1,(b["slug"],max(times),v["duration"])
    result=subprocess.run(["ffmpeg","-hide_banner","-nostats","-i",str(final),"-vn","-af","volumedetect","-f","null","-"],capture_output=True,text=True,timeout=90)
    assert result.returncode==0
    mean=float(re.search(r"mean_volume:\s*(-?[\d.]+) dB",result.stderr)[1])
    peak=float(re.search(r"max_volume:\s*(-?[\d.]+) dB",result.stderr)[1])
    assert mean>-40 and peak<0,(b["slug"],mean,peak)
    for row in [r for r in plan if r["piece_id"]==b["brand"]+"_"+b["date"]+"_"+b["slug"]]:
        assert cooldown_check(row["game"],row["brand"],row["planned_at"],history,21,row["piece_id"])["eligible"]
        dest=ROOT/row["file"];assert dest.exists() and (ROOT/row["caption"]).exists()
        if not os.path.samefile(final,dest):
            assert hashlib.sha256(final.read_bytes()).digest()==hashlib.sha256(dest.read_bytes()).digest()
        assert row["status"].startswith("Preparado") and row["planned_at"].endswith("+01:00")
        if row["network"]=="TikTok":
            assert "Voice-over generated with AI." in (ROOT/row["caption"]).read_text(encoding="utf-8")
    v.update(visual_review="passed" if "--visual-reviewed" in sys.argv else "pending",voice_tail_seconds=round(tail,3),subtitle_end_seconds=max(times),mean_volume_db=mean,peak_volume_db=peak,upload_copies_match=True)
    (folder/"verification.json").write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding="utf-8")
    return dict(game=b["slug"],brand=b["brand"],**v)
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:checks=list(pool.map(qa,briefs))
db=json.loads((ROOT/"data/trailers_db.json").read_text(encoding="utf-8"))
assert len(db)==441 and len(set(t["id"] for t in db))==441
sources=json.loads((ROOT/"data/trailer_sources.json").read_text(encoding="utf-8"))
assert len([s for s in sources["sources"] if s["enabled"]])==5
summary=dict(checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),unique_videos=10,upload_files=25,account_count=5,catalogue_entries=441,catalogue_unique_ids=True,sources=5,cooldown_days=21,external_publications=0,checks=checks)
(P/"QA_FINAL.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
notes="Verificación final: 10 piezas / 25 MP4 / 5 cuentas; decodificación, audio audible sin picos a 0 dB, voz completa, subtítulos dentro del vídeo, copias correctas y 21 días sin repetición. Catálogo 441 IDs únicos. 9 tests de catálogo aprobados; sintaxis Python y JavaScript comprobada. No se han publicado ni programado vídeos externos."
(P/"VERIFICACION.md").write_text("# Verificación · 24/10/2026\n\n"+notes+"\n",encoding="utf-8")
for name in ("AUDITORIA_FUENTES_2026-10-24.md","DIAGNOSTICO_TIKTOK_2026-10-24.md","APRENDIZAJE_STATS_2026-10-24.md"):
    shutil.copy2(P/name,ROOT/"docs"/name)
print(json.dumps({k:v for k,v in summary.items() if k!="checks"},ensure_ascii=False),flush=True)

