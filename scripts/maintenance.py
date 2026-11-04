"""Conservative local cleanup: dry-run by default, --apply to execute known rules."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
RENDER_INTERMEDIATES={"temp_assembled.mp4","temp_assembled_looped.mp4","silent_video.mp4"}


def contained(root, relative):
    root=Path(root).resolve();path=(root/relative).resolve()
    if not path.is_relative_to(root) or path==root:
        raise ValueError("Ruta fuera del proyecto: "+str(relative))
    return path


def valid_video(path):
    try:
        result=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","json",str(path)],capture_output=True,text=True,check=True)
        return float(json.loads(result.stdout)["format"]["duration"])>0
    except (OSError,ValueError,subprocess.CalledProcessError):return False


def plan_cleanup(root=ROOT):
    root=Path(root).resolve();actions=[];skipped=[]
    def add_delete(path,reason):
        try:
            path=contained(root,path.relative_to(root));st=path.stat()
            if path.is_file():actions.append({"action":"delete","path":path.relative_to(root).as_posix(),"bytes":st.st_size,"links":st.st_nlink,"reason":reason})
        except OSError as exc:skipped.append({"path":str(path),"error":str(exc)})
    # Only completed V3 renders with a verified delivered output qualify.
    sessions=root/"temp/v3_cache"
    for session in sessions.iterdir() if sessions.is_dir() else []:
        try:
            result_file=session/"step5_result.json"
            if not result_file.is_file():continue
            result=json.loads(result_file.read_text(encoding="utf-8"))
            if result.get("status") not in {"completed","success"} and result.get("success") is not True:continue
            filename=result.get("filename","")
            if not filename or Path(filename).name!=filename:continue
            final=contained(root,Path("output/videos/Por_clasificar")/filename)
            if not final.is_file():final=contained(root,Path("output")/filename)
            if not valid_video(final):continue
            work=session/"render_tmp"
            for name in RENDER_INTERMEDIATES:
                candidate=work/name
                if candidate.is_file():
                    if name=="silent_video.mp4" and not valid_video(session/"silent_video.mp4"):continue
                    add_delete(candidate,"Intermedio regenerable de un render terminado; salida final verificada")
            clips=work/"clips"
            for candidate in clips.glob("*.mp4") if clips.is_dir() else []:
                if candidate.name.startswith(("cut_","joined_")):
                    add_delete(candidate,"Corte temporal: se reconstruye desde planos y EDL conservados")
        except (OSError,ValueError) as exc:skipped.append({"path":str(session),"error":str(exc)})
    # Known frame captures made while checking the Party Loot formats.
    temporary=root/"temp"
    for pattern in ["party_ig_?.jpg","party_yt_?.jpg","narrated_review_?.jpg","clean_narrated_review_?.jpg"]:
        for candidate in temporary.glob(pattern):add_delete(candidate,"Fotograma de revisión; se conservan los montajes de revisión completos")
    merged=root/"input/wanderburg_official_20261017.mp4"
    if valid_video(merged):
        for name in ["wanderburg_video_20261017.mp4","wanderburg_audio_20261017.mp4"]:
            candidate=root/"input"/name
            if candidate.is_file():add_delete(candidate,"Pista descargada antes de unir el tráiler; MP4 completo verificado")
    return {"root":str(root),"created_utc":datetime.now(timezone.utc).isoformat(),"actions":actions,"skipped":skipped,
            "protected":[".env",".venv","Presets","GamesFootage","PromosFootage","output/videos","output/gestion","JSON de sesiones, voces y planos","archivos ilegibles"]}


def apply_cleanup(plan,root=ROOT):
    root=Path(root).resolve()
    if Path(plan["root"]).resolve()!=root:raise ValueError("El plan pertenece a otro proyecto")
    if __import__("os").name=="nt":
        powershell=shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:raise RuntimeError("No se puede comprobar si hay un render activo")
        result=subprocess.run([powershell,"-NoProfile","-NonInteractive","-Command","@(Get-Process -Name ffmpeg -ErrorAction SilentlyContinue).Count"],capture_output=True,text=True,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        if result.returncode or not result.stdout.strip().isdigit() or int(result.stdout.strip())!=0:
            raise RuntimeError("No se limpia mientras FFmpeg está trabajando o no se puede comprobar su estado")
    results=[]
    for action in plan["actions"]:
        path=contained(root,action["path"])
        # Restrict mutation even if someone modifies a saved plan.
        rel=path.relative_to(root)
        if action["action"]!="delete" or rel.parts[0] not in {"temp","input"}:
            raise ValueError("Eliminación no autorizada por las reglas de mantenimiento")
        valid_rule=(len(rel.parts)>=5 and rel.parts[:2]==("temp","v3_cache") and rel.parts[3]=="render_tmp" and
                    (path.name in RENDER_INTERMEDIATES or (rel.parts[4]=="clips" and path.name.startswith(("cut_","joined_")) and path.suffix==".mp4")))
        valid_rule=valid_rule or (rel.parts[0]=="temp" and len(rel.parts)==2 and path.suffix==".jpg" and path.name.startswith(("party_ig_","party_yt_","narrated_review_","clean_narrated_review_")))
        valid_rule=valid_rule or rel.as_posix() in {"input/wanderburg_video_20261017.mp4","input/wanderburg_audio_20261017.mp4"}
        if not valid_rule:raise ValueError("Archivo fuera de las reglas verificadas: "+str(rel))
        try:
            st=path.stat()
            if st.st_size!=action["bytes"]:raise ValueError("El archivo ha cambiado: "+str(path))
            path.unlink()
            results.append({**action,"status":"deleted","freed_bytes":st.st_size if st.st_nlink==1 else 0})
        except OSError as exc:results.append({**action,"status":"skipped","error":str(exc),"freed_bytes":0})
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--apply",action="store_true");args=parser.parse_args()
    plan=plan_cleanup();folder=ROOT/"data/maintenance";folder.mkdir(parents=True,exist_ok=True)
    report=folder/("cleanup_"+datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")+".json")
    report.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding="utf-8")
    if args.apply:
        plan["results"]=apply_cleanup(plan);plan["freed_bytes"]=sum(r["freed_bytes"] for r in plan["results"])
        report.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding="utf-8")
        print("Liberados:",round(plan["freed_bytes"]/1048576,1),"MiB")
    else:print("Plan sin borrar:",len(plan["actions"]),"archivos;",round(sum(a["bytes"] for a in plan["actions"])/1048576,1),"MiB lógicos")
    print("Informe:",report)

if __name__=="__main__":main()
