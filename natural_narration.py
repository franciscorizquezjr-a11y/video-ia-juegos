"""Continuous narration: authored punctuation, no accelerated hook or splicing."""
import base64,json,re,uuid
from pathlib import Path
import api_usage

def prepare_text(text):
 if not text.strip():raise ValueError('Empty narration')
 if re.search(r'<[^>]*>|\[.*?\]',text):raise ValueError('Use normal punctuation, without pause tags or stage directions')
 if '...' in text or 'â€¦' in text:raise ValueError('Replace ellipses with normal punctuation to avoid drawn-out speech')
 return re.sub(r'\s+',' ',text).strip()

def synthesize(text,output,api_key,voice_id='AHc7z8dzjrGlVbbQ8enm',model_id='eleven_multilingual_v2',ffmpeg='ffmpeg'):
 from audio_aligner import parse_elevenlabs_alignment
 from audio_quality import require_narration, compact_file_pauses
 text=prepare_text(text);output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
 response=api_usage.usage_post(f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps',headers={'xi-api-key':api_key,'Content-Type':'application/json'},json={'text':text,'model_id':model_id,'voice_settings':{'stability':.5,'similarity_boost':.75,'speed':1.0}},timeout=90)
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
  candidate.replace(output)
  output.with_suffix('.quality.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
 finally:candidate.unlink(missing_ok=True)
 return words
