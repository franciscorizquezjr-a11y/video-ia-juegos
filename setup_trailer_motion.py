"""Reusable camera move for Set-up Trailer; no game or preset-specific assets."""
import json
import math
import shutil
import subprocess
from pathlib import Path


def probe_trailer(path, ffmpeg_bin="ffmpeg"):
    sibling = Path(ffmpeg_bin).with_name("ffprobe.exe" if str(ffmpeg_bin).endswith(".exe") else "ffprobe")
    probe = str(sibling) if sibling.is_file() else shutil.which("ffprobe")
    if not probe:
        raise RuntimeError("Se necesita FFprobe para calcular la entrada al monitor.")
    result = subprocess.run([probe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True, check=True)
    meta = json.loads(result.stdout)
    stream = next(s for s in meta["streams"] if s.get("codec_type") == "video")
    duration = float(stream.get("duration") or meta["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("El tráiler debe tener una duración válida.")
    return duration, stream["width"] / stream["height"], any(s.get("codec_type") == "audio" for s in meta["streams"])


def motion_timing(duration, text="", intro_seconds=None, transition_seconds=3.0):
    """Reading budget at 220 words/min, with room reserved for gameplay and return."""
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Duración inválida.")
    reading = max(4.0, len(text.split()) * 60 / 220 + 1.0)
    requested = reading if intro_seconds is None else float(intro_seconds)
    if not math.isfinite(requested) or requested <= 0 or not math.isfinite(transition_seconds) or transition_seconds <= 0:
        raise ValueError("Los tiempos de entrada deben ser positivos.")
    return min(requested, duration*.45), min(transition_seconds, duration*.15), min(1.5, duration*.1), min(.8, duration*.08)


def monitor_timeline(duration, text="", intro_seconds=None, transition_seconds=3.0, entrance_mode="desktop", action_lead_seconds=1.2):
    if entrance_mode not in {"desktop", "action_match"}:
        raise ValueError("Entrada de formato desconocida.")
    if not math.isfinite(action_lead_seconds) or action_lead_seconds <= 0:
        raise ValueError("La duración del adelanto debe ser positiva.")
    hold = min(action_lead_seconds, duration*.2) if entrance_mode == "action_match" else 0.0
    pullout = min(.8, duration*.06) if entrance_mode == "action_match" else 0.0
    lead = hold+pullout
    reading, entry, outro, exit_move = motion_timing(duration-lead,text,intro_seconds,transition_seconds)
    return dict(hold=hold,pullout=pullout,lead=lead,reading=reading,push_start=lead+reading,
                entry=entry,gameplay_start=lead+reading+entry,outro=outro,exit_move=exit_move,
                return_start=duration-outro-exit_move)


def build_action_match_filter(timeline, aspect, corners, width, height, native_w, native_h, fit, fps=30):
    """Move the SAME live source into the monitor; no freeze, repeated shot or audio cut."""
    hold,pullout,lead=(timeline[k] for k in ("hold","pullout","lead"))
    factor = max(width/aspect,height) if fit == "cover" else min(width/aspect,height)
    frame_h,frame_w=factor,factor*aspect
    x,y=(width-frame_w)/2,(height-frame_h)/2
    start=[(x,y),(x+frame_w,y),(x,y+frame_h),(x+frame_w,y+frame_h)]
    end=[(corners[k]["x"]*width/native_w,corners[k]["y"]*height/native_h) for k in ("tl","tr","bl","br")]
    progress=f"clip((on/{fps}-{hold:.6f})/{pullout:.6f},0,1)"
    eased=f"({progress}*{progress}*(3-2*{progress}))"
    coords=[]
    for i,((sx,sy),(ex,ey)) in enumerate(zip(start,end)):
        coords += [f"x{i}='{sx:.6f}+({ex-sx:.6f})*{eased}'",f"y{i}='{sy:.6f}+({ey-sy:.6f})*{eased}'"]
    # Transparent guard pixels prevent perspective from stretching opaque edge pixels.
    return (f"[opening_src]trim=duration={lead:.6f},split=2[opening_blur_src][opening_frame_src];"
            f"[opening_blur_src]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},boxblur=25:2,setsar=1,format=yuva420p,"
            f"fade=t=out:st={hold:.6f}:d={pullout:.6f}:alpha=1[opening_blur];"
            f"[regular][opening_blur]overlay=0:0:eof_action=pass:repeatlast=0[opening_bed];"
            f"[opening_frame_src]format=yuva420p,scale={width-4}:{height-4},pad={width}:{height}:2:2:color=black@0,"
            "perspective="+":".join(coords)+":sense=destination:eval=frame:interpolation=linear[opening_frame];"
            "[opening_bed][opening_frame]overlay=0:0:eof_action=pass:repeatlast=0[outv]")


def build_entry_audio_filter(duration, intro, entry, has_audio, volume=.25):
    """A deterministic airy whoosh, generated locally, peaks as the camera enters."""
    sound_duration = min(1.2, entry)
    start = intro + entry - sound_duration
    attack = sound_duration * .8
    graph = (f"anoisesrc=color=pink:sample_rate=48000:duration={duration:.6f}:seed=73,"
             f"highpass=f=500,lowpass=f=6500,afade=t=in:st={start:.6f}:d={attack:.6f},"
             f"afade=t=out:st={start+attack:.6f}:d={sound_duration-attack:.6f},volume={volume:.6f},"
             f"atrim=duration={duration:.6f}[entry_sfx];")
    if has_audio:
        # Smooth ducking preserves dialogue and avoids an abrupt volume step.
        end = intro + entry
        duck = f"1-0.25*clip((t-{start:.6f})/0.12,0,1)*clip(({end:.6f}-t)/0.12,0,1)"
        graph += (f"[1:a]volume='{duck}':eval=frame,apad,atrim=duration={duration:.6f}[trailer_audio];"
                  "[trailer_audio][entry_sfx]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=0:latency=1[outa]")
    else:
        graph += "[entry_sfx]alimiter=limit=0.95:level=0:latency=1[outa]"
    return graph


def build_monitor_motion_filter(duration, aspect, corners, width, height, native_w, native_h, fit="contain", fps=30, text="", intro_seconds=None, transition_seconds=3.0, entrance_mode="desktop", action_lead_seconds=1.2):
    if fit not in {"contain", "cover"}:
        raise ValueError("Encuadre de gameplay desconocido.")
    timeline=monitor_timeline(duration,text,intro_seconds,transition_seconds,entrance_mode,action_lead_seconds)
    intro,transition,outro,exit_duration=(timeline[k] for k in ("push_start","entry","outro","exit_move"))
    reading_start=timeline["lead"]
    reading=timeline["reading"]
    # Perspective's 'on' is its output frame number. Both branches run at fixed FPS.
    t = f"on/{fps}"
    push = f"clip(({t}-{intro:.6f})/{transition:.6f},0,1)"
    # A small eased move while reading keeps the card legible before the long push.
    approach = f"(0.1*clip(({t}-{reading_start:.6f})/{reading:.6f},0,1)+0.9*{push})"
    progress = f"({approach}*clip(({duration-outro:.6f}-{t})/{exit_duration:.6f},0,1))"
    eased = f"({progress}*{progress}*(3-2*{progress}))"
    # Crop a centered viewport INSIDE the calibrated screen, preserving source aspect.
    u = min(1.0, (width / height) / aspect)
    v = min(1.0, aspect / (width / height))
    def point(a, b):
        weights = ((1-a)*(1-b), a*(1-b), (1-a)*b, a*b)
        pts = [corners[k] for k in ("tl", "tr", "bl", "br")]
        return (sum(w*p["x"] for w,p in zip(weights,pts))*width/native_w,
                sum(w*p["y"] for w,p in zip(weights,pts))*height/native_h)
    target = [point(a,b) for a,b in (((1-u)/2,(1-v)/2),((1+u)/2,(1-v)/2),((1-u)/2,(1+v)/2),((1+u)/2,(1+v)/2))]
    initial = [(0,0),(width,0),(0,height),(width,height)]
    coords = []
    for i, ((x,y),(tx,ty)) in enumerate(zip(initial,target)):
        coords.extend([f"x{i}='{x}+({tx-x:.6f})*{eased}'", f"y{i}='{y}+({ty-y:.6f})*{eased}'"])
    camera = "[setup_complete]setsar=1,perspective=" + ":".join(coords) + ":sense=source:eval=frame:interpolation=cubic[camera];"
    if fit == "contain":
        hero = (f"[hero_src]split=2[blur_src][sharp_src];[blur_src]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},boxblur=25:2,setsar=1[blurred];"
                f"[sharp_src]scale={width}:{height}:force_original_aspect_ratio=decrease,setsar=1[sharp];[blurred][sharp]overlay=(W-w)/2:(H-h)/2[hero];")
    else:
        hero = f"[hero_src]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1[hero];"
    # Hide the perspective crop behind a clean gameplay image near the end of the push.
    fade = transition * .35
    # FFmpeg's alpha fades avoid evaluating a time expression for every color pixel.
    hero_fade = (f"[hero]format=yuva420p,fade=t=in:st={intro+transition*.65:.6f}:d={fade:.6f}:alpha=1,"
                 f"fade=t=out:st={duration-outro-exit_duration:.6f}:d={exit_duration*.35:.6f}:alpha=1[hero_alpha];")
    graph=camera+hero+hero_fade
    if entrance_mode == "action_match":
        graph += "[camera][hero_alpha]overlay=0:0:shortest=1[regular];"
        graph += build_action_match_filter(timeline,aspect,corners,width,height,native_w,native_h,fit,fps)
    else:
        graph += "[camera][hero_alpha]overlay=0:0:shortest=1[outv]"
    return graph
