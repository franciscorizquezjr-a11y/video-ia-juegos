"""Optional voice and synchronized captions for any Set-up Trailer scene."""
import hashlib
import json
import math
import shutil
import subprocess
import uuid
from pathlib import Path


def _probe_media(path, ffmpeg_bin="ffmpeg"):
    sibling=Path(ffmpeg_bin).with_name("ffprobe.exe" if str(ffmpeg_bin).endswith(".exe") else "ffprobe")
    probe=str(sibling) if sibling.is_file() else shutil.which("ffprobe")
    if not probe:
        raise RuntimeError("Se necesita FFprobe para sincronizar la narración.")
    result=subprocess.run([probe,"-v","error","-show_format","-show_streams","-of","json",str(path)],capture_output=True,text=True,check=True)
    info=json.loads(result.stdout)
    duration=float(info["format"]["duration"])
    if not math.isfinite(duration) or duration<=0:
        raise ValueError("Duración del medio inválida.")
    return duration,any(stream.get("codec_type")=="audio" for stream in info["streams"])


def media_duration(path, ffmpeg_bin="ffmpeg"):
    return _probe_media(path,ffmpeg_bin)[0]



def prepare_narration(text, cfg, cache_dir, max_duration, ffmpeg_bin="ffmpeg"):
    from audio_aligner import synthesize_elevenlabs_with_timestamps
    text=(text or "").strip()
    if not text:
        raise ValueError("Escribe un guion de narración o el texto de la tarjeta.")
    identity=json.dumps([text,cfg.elevenlabs_voice_id,cfg.elevenlabs_model,"continuous_v2",1.0],ensure_ascii=False)
    folder=Path(cache_dir)/hashlib.sha256(identity.encode()).hexdigest()[:24]
    folder.mkdir(parents=True,exist_ok=True)
    audio=folder/"voice.mp3"; timing=folder/"words.json"
    if audio.is_file() and timing.is_file():
        words=json.loads(timing.read_text(encoding="utf-8"))
    else:
        _,words,_=synthesize_elevenlabs_with_timestamps(text,audio,cfg.elevenlabs_api_key,cfg.elevenlabs_voice_id,cfg.elevenlabs_model,natural_delivery=True)
        if not words:
            raise ValueError("La voz no contiene tiempos de palabras para los subtítulos. Reintenta la generación.")
        timing.write_text(json.dumps(words,ensure_ascii=False,indent=2),encoding="utf-8")
    from audio_quality import require_narration
    require_narration(audio, words=words, ffmpeg=ffmpeg_bin)
    duration=media_duration(audio,ffmpeg_bin)
    if duration+.2>max_duration-.25:
        raise ValueError(f"La narración dura {duration:.1f} s y no cabe en el vídeo de {max_duration:.1f} s. Acorta el guion o usa un tráiler más largo.")
    return {"audio":audio,"words":words,"duration":duration,"text":text,"offset":.2}


def apply_narration(video, assets, background_volume=.12, highlight_color="#41E1C2", ffmpeg_bin="ffmpeg"):
    from audio_aligner import generate_kinetic_ass_subtitles
    video=Path(video)
    duration,has_background_audio=_probe_media(video,ffmpeg_bin)
    offset=float(assets.get("offset",.2))
    if float(assets["duration"])+offset>duration-.25:
        raise ValueError("La narración no cabe en la duración del vídeo.")
    volume=float(background_volume)
    if not 0<=volume<=1:
        raise ValueError("El volumen de fondo debe estar entre 0 y 1.")
    words=[dict(w,start=float(w["start"])+offset,end=float(w["end"])+offset) for w in assets["words"]]
    ass=video.with_suffix(".narration.ass")
    generate_kinetic_ass_subtitles({"words":words},ass,font_size=68,margin_v=430,word_by_word=False,skip_steam_hook=False,highlight_color=highlight_color)
    # Relative subtitle path avoids Windows drive/FFmpeg filter escaping issues.
    temporary=video.with_name(video.stem+"_narrated_"+uuid.uuid4().hex[:8]+".mp4")
    bed=f"[0:a]volume={volume:.6f}[bed];" if has_background_audio else f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.6f}[bed];"
    filters=(bed+
             f"[1:a]volume=2.5,adelay={round(offset*1000)}:all=1,apad,atrim=duration={duration:.6f}[voice];"
             "[bed][voice]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89:level=0:latency=1[a]")
    cmd=[ffmpeg_bin,"-v","error","-y","-i",str(video.resolve()),"-i",str(Path(assets["audio"]).resolve()),
         "-vf",f"ass=filename='{ass.name}'","-filter_complex",filters,"-map","0:v:0","-map","[a]",
         "-c:v","libx264","-preset","veryfast","-crf","18","-pix_fmt","yuv420p","-c:a","aac","-b:a","192k",
         "-t",str(duration),"-movflags","+faststart",str(temporary.resolve())]
    try:
        subprocess.run(cmd,cwd=video.parent,check=True,capture_output=True)
        temporary.replace(video)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("No se pudo componer la narración y los subtítulos: "+exc.stderr.decode(errors="replace")[-1200:]) from exc
    finally:
        temporary.unlink(missing_ok=True)
    settings=video.with_suffix(".settings.json")
    data=json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    data["narration"]={"enabled":True,"text":assets["text"],"duration":assets["duration"],"offset":offset,"background_volume":volume,"voice_gain":2.5,"subtitles":"chunked_word_highlight","subtitle_margin_v":430,"audio":str(assets["audio"]),"layout":assets.get("layout","card"),"cta":assets.get("cta"),"target_duration":assets.get("target_duration")}
    settings.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    return video


def compose_narration_script(text, cta, channel):
    """Keep the body editable and add one account-specific closing sentence."""
    body=(text or "").strip()
    if not body:
        raise ValueError("Escribe un guion de narración o el texto de la tarjeta.")
    ending=(cta or "Follow {channel} for more games.").replace("{channel}",channel or "this channel").strip()
    if not ending:
        raise ValueError("Escribe una llamada a la acción para cerrar el vídeo.")
    # A script already ending with this CTA must not read it twice.
    if body.rstrip(".!? ").casefold().endswith(ending.rstrip(".!? ").casefold()):
        return body,ending
    separator=" " if body.endswith((".","!","?")) else ". "
    return body+separator+ending,ending


def narration_target_duration(assets, maximum):
    """Leave only a short opening/closing margin, without speeding up the voice."""
    target=math.ceil((float(assets["duration"])+float(assets.get("offset",.2))+.30)*30)/30
    if target>float(maximum)+.001:
        raise ValueError("El guion y la llamada a la acción no caben en el tráiler. Acórtalos o elige un vídeo más largo.")
    return target


def trim_trailer_duration(source, output, duration, ffmpeg_bin="ffmpeg"):
    source=Path(source);output=Path(output)
    if not math.isfinite(duration) or duration<=0 or duration>media_duration(source,ffmpeg_bin)+.04:
        raise ValueError("La duración de narración no cabe en el fragmento elegido del tráiler.")
    output.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run([ffmpeg_bin,"-v","error","-y","-i",str(source),"-t",str(duration),
                    "-map","0:v:0","-map","0:a?","-vf","fps=30","-c:v","libx264","-preset","veryfast","-crf","18",
                    "-c:a","aac","-b:a","192k","-pix_fmt","yuv420p",str(output)],check=True,capture_output=True)
    return output
