"""Technical narration gate. Passing is not proof of perceptual naturalness."""
import math,re,shutil,subprocess
from pathlib import Path
from difflib import SequenceMatcher
import numpy as np
class AudioQualityError(ValueError):pass

def analyse_pcm(samples,rate,words=None,expected_text=None,playback_tempo=1.0):
 x=np.asarray(samples,dtype=np.float64);errors=[];warnings=[]
 if x.ndim!=1 or not len(x) or not np.isfinite(x).all():
  return {'passed':False,'errors':['Invalid or empty audio'],'warnings':[]}
 duration=len(x)/rate;peak=float(np.abs(x).max());rms=float(np.sqrt(np.mean(x*x)))
 if playback_tempo!=1.0:errors.append('Narration was time-stretched; regenerate or revise the script')
 if rms<.001:errors.append('Voice is silent or too quiet')
 if np.mean(np.abs(x)>=.995)>.0002:errors.append('Clipped voice samples')
 hop=max(1,round(rate*.02));energy=np.array([np.sqrt(np.mean(x[i:i+hop]**2)) for i in range(0,len(x),hop)])
 quiet=energy<10**(-38/20);segments=[];start=None
 for i,value in enumerate(list(quiet)+[False]):
  if value and start is None:start=i
  if not value and start is not None:
   a=start*hop/rate;b=min(duration,i*hop/rate)
   if b-a>=.16:segments.append({'start':round(a,3),'end':round(b,3),'duration':round(b-a,3)})
   if a>.08 and b<duration-.08 and b-a>.65:errors.append(f'Internal silence of {b-a:.2f}s at {a:.2f}s')
   start=None
 if segments and segments[0]['start']==0 and segments[0]['duration']>.5:errors.append('Excessive lead-in silence')
 if segments and segments[-1]['end']>=duration-.03 and segments[-1]['duration']>.6:errors.append('Excessive trailing silence')
 lexical=[w for w in (words or []) if re.search(r'\w',str(w.get('word','')),re.UNICODE)]
 previous=-1
 for w in lexical:
  a=float(w['start']);b=float(w['end'])
  if not(math.isfinite(a) and math.isfinite(b) and 0<=a<b<=duration+.12) or a<previous-.03:errors.append('Invalid or non-monotonic word timing');break
  if b-a>1.5:errors.append('Abnormally long aligned word: '+str(w['word']))
  previous=a
 def tokens(text):return re.findall(r"[\w]+(?:['â€™][\w]+)?",text.lower())
 match=None
 if expected_text and lexical:
  expected=tokens(expected_text);actual=tokens(' '.join(str(w['word']) for w in lexical));match=SequenceMatcher(None,expected,actual).ratio()
  if match<.90:errors.append('Transcript/alignment differs from the authored script')
  elif match<1:warnings.append('Normalized transcript differs slightly; review names or numbers')
 # A repeated waveform is only a review flag, since sustained tones can be intentional.
 size=round(.4*rate)
 for i in range(0,len(x)-2*size,size):
  a=x[i:i+size];b=x[i+size:i+2*size]
  if np.sqrt(np.mean(a*a))>.015 and np.array_equal(a,b):warnings.append('Repeated audio block: auditory review required');break
 return {'passed':not errors,'duration':round(duration,3),'peak':round(peak,5),'rms':round(rms,5),'silences':segments,'transcript_match':match,'errors':errors,'warnings':warnings,'perceptual_review_required':True}

def inspect_narration(path,words=None,expected_text=None,ffmpeg='ffmpeg',playback_tempo=1.0):
 cmd=shutil.which(str(ffmpeg)) or str(ffmpeg)
 p=subprocess.run([cmd,'-v','error','-xerror','-i',str(path),'-vn','-ac','1','-ar','16000','-f','f32le','-'],capture_output=True,check=True)
 return analyse_pcm(np.frombuffer(p.stdout,dtype='<f4'),16000,words,expected_text,playback_tempo)

def require_narration(path,**kwargs):
 report=inspect_narration(path,**kwargs)
 if not report['passed']:raise AudioQualityError('; '.join(report['errors']))
 return report

def validate_sfx(cues):
 for cue in cues:
  if not cue.get('reason') or cue.get('anchor_time') is None:raise AudioQualityError('SFX requires a concrete editorial reason and an action anchor')
  if abs(float(cue['start'])-float(cue['anchor_time']))>.15:raise AudioQualityError('SFX does not match its action')
  if not 0<float(cue['duration'])<=.35 or not 0<float(cue['peak_limit'])<=.05:raise AudioQualityError('SFX too long or loud under narration')
 return True


# Short equal-power crossfade applied at every pause splice. The blend happens inside
# the silence that is already being removed, so the output length (and therefore every
# word timestamp) is identical to a hard cut, but without the click of a waveform step.
PAUSE_CROSSFADE_SECONDS=.015

def _splice_crossfade(x,cuts,fade):
    """Join x without the [a,b) sample spans, crossfading fade samples at each joint.
    Output sample n keeps the same position as with a hard cut: the last f kept samples
    before a fade out while the last f removed samples before b fade in (equal power)."""
    chunks=[];cursor=0;applied=[]
    for a,b in cuts:
        f=max(0,min(int(fade),a-cursor,b-a))
        chunks.append(x[cursor:a-f])
        if f:
            theta=(np.arange(f)+.5)/f*(np.pi/2)
            blend=x[a-f:a]*np.cos(theta)+x[b-f:b]*np.sin(theta)
            if np.issubdtype(x.dtype,np.integer):
                info=np.iinfo(x.dtype);blend=np.clip(np.round(blend),info.min,info.max)
            chunks.append(blend.astype(x.dtype,copy=False))
        applied.append(f);cursor=b
    chunks.append(x[cursor:])
    return np.concatenate(chunks),applied

def compact_aligned_pauses(samples, rate, words, crossfade=PAUSE_CROSSFADE_SECONDS):
    """Remove only central silence between timed words; never stretch or cut speech.
    Splices use a short equal-power crossfade (crossfade seconds, 0 = hard cut)."""
    x=np.asarray(samples);report=analyse_pcm(x,rate);cuts=[]
    fade_s=max(0.0,float(crossfade or 0))
    for gap in report.get('silences',[]):
        if not .5<gap['duration']<=1.2:continue
        previous=[w for w in words if float(w['end'])<=gap['start']+.15]
        following=[w for w in words if float(w['start'])>=gap['end']-.15]
        if not previous or not following:continue
        left=max(gap['start']+.04,float(previous[-1]['end'])+.03)
        right=min(gap['end']-.04,float(following[0]['start'])-.03)
        if right-left<=.38:continue
        a=left+.14;b=right-.14
        # Guard against timestamp errors: no aligned word may intersect a removed or
        # crossfaded span.
        if any(float(w['start'])<b and float(w['end'])>a-fade_s for w in words):continue
        cuts.append((round(a*rate),round(b*rate)))
    if not cuts:return x,[dict(w) for w in words],[]
    fixed,applied=_splice_crossfade(x,cuts,round(fade_s*rate))
    def offset(t):return sum(max(0,min(float(t),b/rate)-a/rate) for a,b in cuts)
    aligned=[dict(w,start=round(float(w['start'])-offset(w['start']),4),end=round(float(w['end'])-offset(w['end']),4)) for w in words]
    return fixed,aligned,[{'start':a/rate,'end':b/rate,'removed':(b-a)/rate,'crossfade':round(f/rate,4)} for (a,b),f in zip(cuts,applied)]

def _decode_mono_pcm(path,cmd):
    """Decode to mono float PCM at the file's own sample rate (no resampling)."""
    raw=subprocess.run([cmd,'-v','error','-i',str(path),'-vn','-ac','1','-c:a','pcm_f32le','-f','wav','-'],capture_output=True,check=True).stdout
    rate=None;data=None;pos=12
    if raw[:4]==b'RIFF' and raw[8:12]==b'WAVE':
        while pos+8<=len(raw):
            chunk=raw[pos:pos+4];size=int.from_bytes(raw[pos+4:pos+8],'little');body=pos+8
            if chunk==b'fmt ' and size>=8:rate=int.from_bytes(raw[body+4:body+8],'little')
            elif chunk==b'data':
                data=raw[body:];break  # piped WAV output cannot rewrite the data size
            pos=body+size+(size&1)
    if rate and data is not None and 8000<=rate<=384000:
        return np.frombuffer(data[:len(data)//4*4],dtype='<f4'),rate
    # Fallback: the previous fixed-rate decode.
    raw=subprocess.run([cmd,'-v','error','-i',str(path),'-vn','-ac','1','-ar','48000','-f','f32le','-'],capture_output=True,check=True).stdout
    return np.frombuffer(raw,dtype='<f4'),48000

# Re-encode settings per extension; the highest quality avoids audible generational loss.
_REENCODE_ARGS={'.mp3':['-c:a','libmp3lame','-q:a','0'],'':['-c:a','libmp3lame','-q:a','0','-f','mp3'],
                '.flac':['-c:a','flac'],'.m4a':['-c:a','aac','-b:a','256k'],'.aac':['-c:a','aac','-b:a','256k'],
                '.ogg':['-c:a','libvorbis','-q:a','8'],'.opus':['-c:a','libopus','-b:a','192k']}

def compact_file_pauses(path, words, ffmpeg='ffmpeg'):
    """Compact long pauses in place, keeping the same path and extension.
    WAV files are rewritten as PCM; compressed files are re-encoded once at top quality."""
    import wave
    path=Path(path);cmd=shutil.which(str(ffmpeg)) or str(ffmpeg)
    pcm,rate=_decode_mono_pcm(path,cmd);fixed,aligned,cuts=compact_aligned_pauses(pcm,rate,words)
    if cuts:
        suffix=path.suffix.lower()
        wav=path.with_name(path.stem+'.pausefix.wav');encoded=path.with_name(path.stem+'.pausefix'+path.suffix)
        # Exact inverse of FFmpeg's s16 -> float scaling (x/32768).
        pcm16=np.clip(np.round(np.asarray(fixed,dtype=np.float64)*32768),-32768,32767).astype('<i2')
        try:
            with wave.open(str(wav),'wb') as stream:
                stream.setparams((1,2,int(rate),0,'NONE','not compressed'));stream.writeframes(pcm16.tobytes())
            if suffix=='.wav':
                wav.replace(path)
            else:
                subprocess.run([cmd,'-v','error','-y','-i',str(wav)]+_REENCODE_ARGS.get(suffix,[])+[str(encoded)],capture_output=True,check=True)
                encoded.replace(path)
        finally:
            wav.unlink(missing_ok=True);encoded.unlink(missing_ok=True)
    return aligned,cuts
