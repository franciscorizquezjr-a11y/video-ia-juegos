"""PAO PAO campaign draft: studio kit, authored shots, literal final CTA, no automatic SFX."""
from pathlib import Path
import json,subprocess,math,sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from audio_quality import require_narration,validate_sfx
FF=r'E:\ffmpeg\ffmpeg-2026-01-07-git-af6a1dd0b2-essentials_build\bin\ffmpeg.exe';FP=str(Path(FF).with_name('ffprobe.exe'))
WORK=ROOT/'output/trabajo/pao_pao_2026-11-02'
SOURCE=ROOT/'PromosFootage/PAO PAO/trapster_2026-11-02/media_kit/PAO_PAO_Vertical_2min.mp4'
FUNNY=SOURCE.with_name('PAO_PAO_Funny_Moments.mp4')
LOGO=SOURCE.with_name('Horizontal_PaoPao_Logo.png')
def run(args):
 p=subprocess.run(args,capture_output=True,timeout=600)
 if p.returncode:raise RuntimeError(p.stderr.decode(errors='replace')[-1500:])
 return p.stdout
def probe(p):return json.loads(run([FP,'-v','error','-show_streams','-show_format','-of','json',str(p)]))
def stamp(t):
 n=round(t*100);return f'{n//360000}:{n//6000%60:02}:{n//100%60:02}.{n%100:02}'
def main():
 from PIL import Image,ImageDraw,ImageFont
 words=json.loads((WORK/'words.json').read_text());meta=json.loads((WORK/'brief.json').read_text(encoding='utf-8'));script=meta['script'];meta['playback_tempo']=1.0
 assert script.endswith(meta['required_cta'])
 (WORK/'voice_metadata.json').write_text(json.dumps({'script':script,'voice_id':meta['voice_id'],'model':meta['voice_model'],'playback_tempo':1.0,'continuous_take':True},indent=2),encoding='utf-8')
 voice_report=require_narration(WORK/'voice.mp3',words=words,expected_text=script,ffmpeg=FF,playback_tempo=meta['playback_tempo']);validate_sfx([])
 duration=math.ceil(voice_report['duration']*30)/30
 assert duration<=30, 'Shorten the script; never speed up the voice to fit campaign'
 import re
 cta_tokens=re.findall(r'\w+',meta['required_cta'].lower());spoken_tokens=re.findall(r'\w+',' '.join(w['word'] for w in words).lower())
 assert spoken_tokens[-len(cta_tokens):]==cta_tokens, 'CTA missing, changed or not final'
 names=[w['word'].lower().strip('.,:;!?') for w in words]
 def at(phrase,start=0):
  tokens=phrase.lower().split()
  for i in range(start,len(names)-len(tokens)+1):
   if names[i:i+len(tokens)]==tokens or (len(tokens)==1 and names[i].startswith(tokens[0])):return i,float(words[i]['start'])
  raise ValueError('Missing authored beat '+phrase)
 beats=[(None,26.0,SOURCE),('but one',45.0,FUNNY),('in pao',40.0,SOURCE),('grow crops',57.0,SOURCE),('and explore',105.0,SOURCE),('split up',49.0,SOURCE),('or turn',37.0,FUNNY),('water',27.0,FUNNY),('who in',31.0,FUNNY),('pao pao is',41.0,SOURCE)]
 starts=[];cursor=0
 for phrase,source_in,source_path in beats:
  if phrase is None:t=0
  else:cursor,t=at(phrase,cursor)
  starts.append((round(t*30)/30,source_in,source_path))
 shots=[]
 for i,(t,source_in,source_path) in enumerate(starts):
  end=starts[i+1][0] if i+1<len(starts) else duration
  shots.append({'timeline_in':t,'source_in':source_in,'duration':end-t,'frames':round(end*30)-round(t*30),'source':str(source_path),'reason':beats[i][0] or 'restaurant opening'})
 header='[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Default,Arial Black,84,&H00FFFFFF,&H000000FF,&H00000000,&H60000000,-1,0,0,0,100,100,0,0,1,6,4,2,60,60,380,1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n'
 events=[]
 for i,w in enumerate(words):
  end=min(float(w['end'])+.07,float(words[i+1]['start']) if i+1<len(words) else duration)
  color='&H00C2E141&' if i%3==0 else '&H00FFFFFF&'
  events.append(f'Dialogue: 0,{stamp(w["start"])},{stamp(end)},Default,,0,0,380,,{{\\c{color}}}{w["word"].upper()}\n')
 (WORK/'captions.ass').write_text(header+''.join(events),encoding='utf-8')
 overlay=Image.new('RGBA',(1080,1920));draw=ImageDraw.Draw(overlay)
 for y in range(540):draw.line((0,y,1080,y),fill=(8,10,18,round(110*max(0,1-y/540))))
 logo=Image.open(LOGO).convert('RGBA');logo=logo.crop(logo.getchannel('A').getbbox());logo.thumbnail((780,250),Image.Resampling.LANCZOS);overlay.alpha_composite(logo,((1080-logo.width)//2,165));overlay.save(WORK/'overlay.png')
 price_file=ROOT/'PromosFootage/PAO PAO/trapster_2026-11-02/steam_price_2026-11-02.json'
 prices=json.loads(price_file.read_text(encoding='utf-8'))
 assert prices['regions']['us']['currency']=='USD' and prices['regions']['es']['currency']=='EUR'
 price_overlay=overlay.copy();draw=ImageDraw.Draw(price_overlay)
 draw.rounded_rectangle((100,465,980,740),radius=24,fill=(8,16,25,235),outline='#41e1c2',width=4)
 steam=Image.open(ROOT/'Presets/Steam/steam_logo_official_footer.png').convert('RGBA');steam=steam.resize((230,65),Image.Resampling.LANCZOS);price_overlay.alpha_composite(steam,(145,502))
 font=ImageFont.truetype(r'C:\Windows\Fonts\ariblk.ttf',84)
 us=prices['regions']['us'];es=prices['regions']['es']
 us_label='US $'+format(us['final']/100,'.2f');es_label='ES '+format(es['final']/100,'.2f').replace('.',',')+' €'
 draw.text((440,485),us_label,font=font,fill='white',stroke_width=1,stroke_fill='white')
 draw.text((145,615),es_label,font=font,fill='white',stroke_width=1,stroke_fill='white')
 if us['discount_percent']==es['discount_percent']:draw.text((720,615),'-'+str(us['discount_percent'])+'%',font=font,fill='#41e1c2')
 price_overlay.save(WORK/'overlay_price.png')
 price_start=starts[-1][0]

 args=[FF,'-v','error','-y'];filters=[]
 for i,s in enumerate(shots):
  args+=['-ss',str(s['source_in']),'-threads','1','-i',s['source']]
  filters.append(f'[{i}:v]fps=30,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,trim=end_frame={s["frames"]},setpts=PTS-STARTPTS[v{i}]')
 n=len(shots);args+=['-i',str(WORK/'overlay.png'),'-i',str(WORK/'overlay_price.png'),'-i',str(WORK/'voice.mp3')]
 filters.append(''.join(f'[v{i}]' for i in range(n))+f'concat=n={n}:v=1:a=0[base]');filters.append(f'[base][{n}:v]overlay=0:0[normal_logo]');filters.append(f"[normal_logo][{n+1}:v]overlay=0:0:enable='gte(t,{price_start})'[logo]")
 ass=str(WORK/'captions.ass').replace('\\','/').replace(':','\\:')
 filters.append(f"[logo]ass=filename='{ass}',format=yuv420p[v]")
 filters.append(f'[{n+2}:a]volume=0.92,apad[a]')
 target=WORK/'video.mp4';run([*args,'-filter_complex_threads','1','-filter_complex',';'.join(filters),'-map','[v]','-map','[a]','-t',str(duration),'-c:v','libx264','-threads','2','-preset','veryfast','-crf','19','-c:a','aac','-b:a','192k','-movflags','+faststart',str(target)])
 run([FF,'-v','error','-xerror','-i',str(target),'-f','null','-'])
 info=probe(target);v=next(s for s in info['streams'] if s['codec_type']=='video');a=next(s for s in info['streams'] if s['codec_type']=='audio')
 assert (v['width'],v['height'])==(1080,1920);assert abs(float(v['duration'])-float(a['duration']))<.06
 final_report=require_narration(target,words=words,expected_text=script,ffmpeg=FF)
 (WORK/'verification.json').write_text(json.dumps({'decode':'passed','resolution':[1080,1920],'duration':duration,'voice_quality':voice_report,'final_audio_quality':final_report,'sfx':[],'playback_tempo':1.0,'perceptual_review':'pending','campaign':'PAO PAO','required_cta_verified':True,'required_hashtag':'#paopaotraprelease','max_duration_verified':30,'own_gameplay_required':False,'steam_prices':prices,'steam_logo_official':True,'price_card_start':price_start,'revision':2},indent=2),encoding='utf-8')
 (WORK/'edit_plan.json').write_text(json.dumps({'duration':duration,'shots':shots,'sfx':[],'no_audio_time_stretch':True,'voice_single_take':True,'campaign':'PAO PAO','media_kit_only':True,'price_card_start':price_start,'steam_prices':prices,'cta':meta['required_cta']},indent=2),encoding='utf-8')
 sheet=Image.new('RGB',(1296,384))
 for i,fraction in enumerate([.02,.20,.38,.56,.75,.94]):
  p=WORK/f'qa_{i}.jpg';run([FF,'-v','error','-y','-ss',str(duration*fraction),'-i',str(target),'-frames:v','1','-vf','scale=216:384',str(p)]);sheet.paste(Image.open(p),(216*i,0))
 sheet.save(WORK/'review.jpg');print('RENDER_VERIFIED',duration,'s')
if __name__=='__main__':main()
