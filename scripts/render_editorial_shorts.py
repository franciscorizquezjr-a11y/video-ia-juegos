"""Render authored single-game shorts with the project's narration quality gates.

Consumes a reviewed JSON recipe. Does not upload or schedule content.
"""
from pathlib import Path
import argparse, hashlib, json, math, re, subprocess, sys, wave
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from audio_quality import require_narration, validate_sfx

FF = r'E:\ffmpeg\ffmpeg-2026-01-07-git-af6a1dd0b2-essentials_build\bin\ffmpeg.exe'
FP = str(Path(FF).with_name('ffprobe.exe'))


def run(args):
    p = subprocess.run(args, capture_output=True, timeout=900)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors='replace')[-2200:])
    return p.stdout


def probe(path):
    return json.loads(run([FP, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))


def stamp(t):
    n = round(t * 100)
    return f'{n // 360000}:{n // 6000 % 60:02}:{n // 100 % 60:02}.{n % 100:02}'


def token(s):
    return re.sub(r'[^\w]', '', s.lower())


def phrase_at(words, phrase, start=0):
    needles = [token(p) for p in phrase.split()]
    haystack = [token(w['word']) for w in words]
    for i in range(start, len(haystack) - len(needles) + 1):
        if haystack[i:i + len(needles)] == needles:
            return i, float(words[i]['start'])
    raise ValueError('Authored beat not in narration: ' + phrase)


def create_sfx(path, duration, peak, kind):
    """Small original UI/impact accents; no reused trailer soundtrack."""
    rate = 48000
    t = np.arange(round(duration * rate)) / rate
    if kind == 'ui':
        signal = np.sin(2 * np.pi * (1300 * t + 1000 * t * t)) * np.exp(-40 * t)
    else:
        signal = np.sin(2 * np.pi * (170 * t - 240 * t * t)) * np.exp(-27 * t)
    signal *= np.minimum(1, t / .004) * np.minimum(1, (duration - t) / .012)
    signal *= peak / max(float(np.abs(signal).max()), .000001)
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        stream.writeframes((signal * 32767).astype('<i2').tobytes())


def render(recipe_path):
    recipe_path = Path(recipe_path).resolve()
    b = json.loads(recipe_path.read_text(encoding='utf-8'))
    work = ROOT / b['work']
    output = ROOT / b['output']
    work.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    voice = ROOT / b['voice']
    words = json.loads((ROOT / b['words']).read_text(encoding='utf-8'))
    quality = require_narration(voice, words=words, expected_text=b['script'], ffmpeg=FF, playback_tempo=1.0)
    duration = math.ceil((quality['duration'] + .13) * 30) / 30
    if not 15 <= duration <= 35:
        raise ValueError('Revise the script; do not stretch speech to force duration')
    starts = []
    cursor = 0
    for beat in b['beats']:
        if beat['phrase'] is None:
            t = 0
        else:
            cursor, t = phrase_at(words, beat['phrase'], cursor)
        starts.append((round(t * 30) / 30, beat))
    shots = []
    for i, (t, beat) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else duration
        frames = round(end * 30) - round(t * 30)
        if frames < 5:
            raise ValueError('Authored shot too short')
        source = ROOT / beat.get('source', b['source'])
        info = probe(source)
        v = next(s for s in info['streams'] if s['codec_type'] == 'video')
        if beat['source_in'] + (end - t) > float(info['format']['duration']) - .05:
            raise ValueError('Shot overruns its source')
        crop_w = round(int(v['height']) * 9 / 16 / 2) * 2
        if crop_w > int(v['width']):
            raise ValueError('This recipe expects wide or exact portrait footage')
        cx = beat.get('center_x', int(v['width']) / 2)
        x = round(max(0, min(int(v['width']) - crop_w, cx - crop_w / 2)) / 2) * 2
        shots.append({**beat, 'source': str(source.relative_to(ROOT)), 'timeline_in': t,
                      'duration': end - t, 'frames': frames,
                      'crop': [x, 0, crop_w, int(v['height'])]})

    rgb = b['color'].lstrip('#')
    color = '&H00' + rgb[4:6] + rgb[2:4] + rgb[0:2] + '&'
    header = ('[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nScaledBorderAndShadow: yes\n\n'
              '[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n'
              'Style: Default,Arial Black,84,&H00FFFFFF,&H000000FF,&H00000000,&H60000000,-1,0,0,0,100,100,0,0,1,6,4,2,60,60,380,1\n\n'
              '[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n')
    lines = []
    for i, w in enumerate(words):
        end = min(float(w['end']) + .07, float(words[i + 1]['start']) if i + 1 < len(words) else duration)
        text = w['word'].upper().replace('DOESNT', "DOESN'T")
        text = text.replace('{', '').replace('}', '').replace('\\', '')
        lines.append(f'Dialogue: 0,{stamp(w["start"])},{stamp(end)},Default,,0,0,380,,{{\\c{color if i % 3 == 0 else "&H00FFFFFF&"}}}{text}\n')
    captions = work / 'captions.ass'
    captions.write_text(header + ''.join(lines), encoding='utf-8')

    overlay = Image.new('RGBA', (1080, 1920))
    draw = ImageDraw.Draw(overlay)
    for y in range(530):
        draw.line((0, y, 1080, y), fill=(8, 10, 18, round(115 * max(0, 1 - y / 530))))
    logo = Image.open(ROOT / b['logo']).convert('RGBA')
    box = logo.getchannel('A').getbbox()
    if not box:
        raise ValueError('Game logo is empty')
    logo = logo.crop(box)
    logo.thumbnail(tuple(b.get('logo_size', [780, 210])), Image.Resampling.LANCZOS)
    logo_pos = b.get('logo_position') or [(1080 - logo.width) // 2, 155]
    overlay.alpha_composite(logo, tuple(logo_pos))
    overlay_path = work / 'overlay.png'
    overlay.save(overlay_path)

    sfx = []
    for i, cue in enumerate(b.get('sfx', [])):
        _, t = phrase_at(words, cue['phrase'])
        anchor = t + cue.get('offset', 0)
        cue = dict(cue, timeline_in=anchor, start=anchor, anchor_time=anchor)
        if not 0 <= cue['timeline_in'] <= duration - cue['duration']:
            raise ValueError('SFX outside timeline')
        validate_sfx([cue])
        path = work / f'accent_{i}.wav'
        create_sfx(path, cue['duration'], cue['peak_limit'], cue['kind'])
        cue['file'] = str(path.relative_to(ROOT))
        sfx.append(cue)

    args = [FF, '-v', 'error', '-y']
    filters = []
    for i, shot in enumerate(shots):
        args += ['-ss', str(shot['source_in']), '-threads', '1', '-i', str(ROOT / shot['source'])]
        x, y, w, h = shot['crop']
        filters.append(f'[{i}:v]fps=30,crop={w}:{h}:{x}:{y},scale=1080:1920:flags=lanczos,setsar=1,trim=end_frame={shot["frames"]},setpts=PTS-STARTPTS[v{i}]')
    n = len(shots)
    args += ['-i', str(overlay_path), '-i', str(voice)]
    for cue in sfx:
        args += ['-i', str(ROOT / cue['file'])]
    filters.append(''.join(f'[v{i}]' for i in range(n)) + f'concat=n={n}:v=1:a=0[base]')
    filters.append(f'[base][{n}:v]overlay=0:0[logo]')
    ass = str(captions).replace('\\', '/').replace(':', '\\:')
    filters.append(f"[logo]ass=filename='{ass}',format=yuv420p[v]")
    filters.append(f'[{n + 1}:a]volume=0.92,apad,atrim=duration={duration}[voice]')
    for i, cue in enumerate(sfx):
        delay = round(cue['timeline_in'] * 1000)
        filters.append(f'[{n + 2 + i}:a]adelay={delay}:all=1[fx{i}]')
    mix = '[voice]' + ''.join(f'[fx{i}]' for i in range(len(sfx)))
    filters.append(mix + f'amix=inputs={1 + len(sfx)}:duration=first:normalize=0,alimiter=limit=0.95:level=false[a]')
    candidate = output.with_name('video.candidate.mp4')
    run([*args, '-filter_complex_threads', '1', '-filter_complex', ';'.join(filters),
         '-map', '[v]', '-map', '[a]', '-t', str(duration), '-c:v', 'libx264',
         '-threads', '2', '-preset', 'veryfast', '-crf', '19', '-c:a', 'aac',
         '-b:a', '192k', '-movflags', '+faststart', str(candidate)])
    run([FF, '-v', 'error', '-xerror', '-i', str(candidate), '-f', 'null', '-'])
    info = probe(candidate)
    v = next(s for s in info['streams'] if s['codec_type'] == 'video')
    a = next(s for s in info['streams'] if s['codec_type'] == 'audio')
    assert (v['width'], v['height'], v['pix_fmt']) == (1080, 1920, 'yuv420p')
    assert v['codec_name'] == 'h264' and a['codec_name'] == 'aac'
    assert abs(float(v['duration']) - float(a['duration'])) < .06
    final_quality = require_narration(candidate, words=words, expected_text=b['script'], ffmpeg=FF, playback_tempo=1.0)
    candidate.replace(output)
    report = {'decode': 'passed', 'resolution': [1080, 1920], 'duration': duration,
              'voice': quality, 'final_audio': final_quality, 'sfx': sfx,
              'playback_tempo': 1.0, 'source_audio_included': False,
              'visual_review': 'pending', 'auditory_review': 'pending',
              'published': False, 'scheduled': False, 'publish_gate': b['publish_gate'],
              'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}
    (work / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (work / 'edit_plan.json').write_text(json.dumps({'duration': duration, 'shots': shots, 'sfx': sfx,
        'narration_speed': 1.0, 'source_audio': 'muted; no trailer music reused',
        'no_blurred_background': True}, ensure_ascii=False, indent=2), encoding='utf-8')
    sheet = Image.new('RGB', (1440, 450), '#11111b')
    for i, fraction in enumerate([.02, .18, .36, .53, .72, .94]):
        p = work / f'qa_{i}.jpg'
        run([FF, '-v', 'error', '-y', '-ss', str(duration * fraction), '-i', str(output),
             '-frames:v', '1', '-vf', 'scale=240:427', str(p)])
        with Image.open(p) as frame:
            sheet.paste(frame, (i * 240, 0))
    sheet.save(work / 'review.jpg', quality=92)
    print('RENDER_VERIFIED', b['game'], duration, len(shots), 'shots', flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recipe')
    render(parser.parse_args().recipe)
