"""Continuous narration: authored punctuation, no accelerated hook or splicing."""
import base64,json,math,re,uuid
from pathlib import Path
import api_usage

# ElevenLabs accepts voice speed between 0.7 and 1.2; outside it the request fails.
SPEED_MIN=0.7
SPEED_MAX=1.2


def _clamp(value,low,high,default):
 try:value=float(value)
 except (TypeError,ValueError):return default
 if not math.isfinite(value):return default
 return min(high,max(low,value))


def clamp_voice_speed(speed,default=1.0):
 """Speed actually sent to ElevenLabs (0.7-1.2); invalid values use the default."""
 return round(_clamp(speed,SPEED_MIN,SPEED_MAX,default),3)


def voice_settings(speed=1.0,stability=0.5,similarity_boost=0.75):
 """ElevenLabs voice_settings with every value clamped to its valid range."""
 return {'stability':round(_clamp(stability,0.0,1.0,0.5),3),
         'similarity_boost':round(_clamp(similarity_boost,0.0,1.0,0.75),3),
         'speed':clamp_voice_speed(speed)}


def prepare_text(text):
 if not text.strip():raise ValueError('La narración está vacía: escribe el guion antes de generar la voz.')
 markup=re.search(r'<[^>]*>|\[.*?\]',text)
 if markup:
  raise ValueError(f'El guion contiene una etiqueta o acotación que la voz leería en voz alta: "{markup.group(0)[:40]}". '
                   'Quítala y marca las pausas con puntuación normal (comas y puntos).')
 if '...' in text or 'â€¦' in text:
  raise ValueError('El guion contiene puntos suspensivos ("..."), que alargan la voz de forma artificial. '
                   'Sustitúyelos por un punto o una coma.')
 return re.sub(r'\s+',' ',text).strip()

def synthesize(text,output,api_key,voice_id='AHc7z8dzjrGlVbbQ8enm',model_id='eleven_multilingual_v2',ffmpeg='ffmpeg',speed=1.0,stability=0.5,similarity_boost=0.75):
 from audio_aligner import parse_elevenlabs_alignment
 from audio_quality import require_narration, compact_file_pauses
 text=prepare_text(text);output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
 settings=voice_settings(speed,stability,similarity_boost)
 response=api_usage.usage_post(f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps',headers={'xi-api-key':api_key,'Content-Type':'application/json'},json={'text':text,'model_id':model_id,'voice_settings':settings},timeout=90)
 if response.status_code!=200:raise RuntimeError(f'Narration request failed: HTTP {response.status_code}')
 data=response.json();alignment=data.get('normalized_alignment') or data.get('alignment') or {}
 if '<' in ''.join(alignment.get('characters',[])):raise ValueError('Invalid markup in alignment')
 words=[w for w in parse_elevenlabs_alignment(alignment) if re.search(r'\w',w['word'])]
 if not words or not data.get('audio_base64'):raise ValueError('Missing narration or word timing')
 candidate=output.with_name(output.stem+'.candidate_'+uuid.uuid4().hex[:8]+output.suffix)
 try:
  candidate.write_bytes(base64.b64decode(data['audio_base64'],validate=True))
  words,pause_repairs=compact_file_pauses(candidate,words,ffmpeg=ffmpeg)
  report=require_narration(candidate,words=words,expected_text=text,ffmpeg=ffmpeg)
  report["quiet_spans_removed"]=pause_repairs
  report["speech_time_stretch"]=False
  report["voice_settings"]=settings
  candidate.replace(output)
  output.with_suffix('.quality.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
 finally:candidate.unlink(missing_ok=True)
 return words
