"""Optional, deterministic opening selection for local trailers."""
import json
import logging
import math
import statistics
import subprocess

logger = logging.getLogger(__name__)


def choose_active_opening(path, duration, ffmpeg_bin="ffmpeg"):
    """Estimate an active, textured shot. This is a motion heuristic, not semantic AI."""
    width, height, fps = 96, 54, 3
    sample_end = min(120.0, duration * .85)
    if sample_end < 2:
        return 0.0
    result = subprocess.run([ffmpeg_bin, "-v", "error", "-i", str(path), "-t", str(sample_end),
                             "-vf", f"fps={fps},scale={width}:{height},format=gray", "-an", "-f", "rawvideo", "-"],
                            capture_output=True, check=True)
    size = width * height
    frames = [result.stdout[i:i+size] for i in range(0,len(result.stdout)-size+1,size)]
    scores = []
    for i, frame in enumerate(frames):
        mean = sum(frame)/size
        deviation = math.sqrt(sum((v-mean)**2 for v in frame)/size)
        if i == 0 or not 18 < mean < 242 or deviation < 12:
            scores.append(0.0)
            continue
        previous = frames[i-1]
        brightness_jump = abs(mean - sum(previous)/size)
        motion = sum(min(abs(a-b),45) for a,b in zip(frame,previous))/size
        scores.append(max(0, motion - brightness_jump*.8))
    # Favor sustained activity, rather than a single hard cut or flash.
    windows = [(statistics.median(scores[i:i+5]),i) for i in range(max(0,len(scores)-5))]
    if not windows:
        return 0.0
    score,index = max(windows,key=lambda v:v[0])
    return max(0.0,(index-1)/fps) if score > 1.5 else 0.0


def prepare_opening(source, output, mode, requested_start, ffmpeg_bin="ffmpeg"):
    from setup_trailer_motion import probe_trailer
    duration, _, audio = probe_trailer(source,ffmpeg_bin)
    if mode not in {"original","auto","manual"}:
        raise ValueError("Tipo de apertura desconocido.")
    if mode == "original":
        return source,0.0
    offset = choose_active_opening(source,duration,ffmpeg_bin) if mode == "auto" else float(requested_start)
    if not math.isfinite(offset) or offset < 0 or offset >= duration:
        raise ValueError("El inicio elegido debe estar dentro del tráiler.")
    if offset < 1/30:
        return source,0.0
    # Rotate the sequence, including audio; preserve every segment and the total duration.
    graph=(f"[0:v]split=2[a][b];[a]trim=start={offset:.6f},setpts=PTS-STARTPTS[tailv];"
           f"[b]trim=end={offset:.6f},setpts=PTS-STARTPTS[headv];")
    if audio:
        graph+=(f"[0:a]apad,atrim=duration={duration:.6f},asplit=2[c][d];[c]atrim=start={offset:.6f},asetpts=PTS-STARTPTS[taila];"
                f"[d]atrim=end={offset:.6f},asetpts=PTS-STARTPTS[heada];"
                "[tailv][taila][headv][heada]concat=n=2:v=1:a=1[v][aout]")
    else:
        graph+="[tailv][headv]concat=n=2:v=1:a=0[v]"
    cmd=[ffmpeg_bin,"-v","error","-y","-i",str(source),"-filter_complex",graph,"-map","[v]"]
    if audio:
        cmd += ["-map","[aout]","-c:a","aac","-b:a","192k"]
    cmd += ["-c:v","libx264","-preset","veryfast","-crf","18","-pix_fmt","yuv420p","-t",str(duration),str(output)]
    result=subprocess.run(cmd,capture_output=True,text=True)
    if result.returncode:
        raise RuntimeError("No se pudo preparar la apertura: " + result.stderr[-1600:])
    logger.info("Opening %s at %.3fs: %s",mode,offset,source.name)
    return output,offset


def title_reveal_time(duration, variant, mode, text="", intro_seconds=None, transition_seconds=3, delay=1, entrance_mode="desktop", action_lead_seconds=1.2):
    from setup_trailer_motion import monitor_timeline
    timeline=monitor_timeline(duration,text,intro_seconds,transition_seconds,entrance_mode,action_lead_seconds)
    outro=timeline["outro"]
    if mode == "immediate":
        return 0.0
    if mode == "outro":
        return duration-outro
    if mode != "after_entry":
        raise ValueError("Momento de revelación desconocido.")
    after = timeline["gameplay_start"] if variant == "monitor_dive" else min(2,duration*.2)
    return min(after+delay,max(0,duration-outro-min(.3,duration*.05)))
