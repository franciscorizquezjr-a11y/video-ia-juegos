from pathlib import Path
import json,requests,time,os
root=Path.cwd()
scene=next(x for x in json.loads((root/'static/setupbackground/scenes.json').read_text(encoding='utf-8')) if x['id']=='partyloot_coop')
texts={"youtube":"Your next co-op adventure fits inside two tiny cars. Explore a giant home laboratory with a friend, solve puzzles together, and unlock gadgets for your RC cars. Play online or split-screen. Who's driving with you?","instagram":"Send this to your co-op partner. Two tiny RC cars, one giant home laboratory, and puzzles you can only solve together. Online or split-screen. Who's driving?"}
for i in range(60):
 try:
  if requests.get('http://127.0.0.1:8004/api/setup-trailer/config',timeout=3).status_code==200:break
 except requests.RequestException:pass
 time.sleep(2)
final=root/'output/gestion/PartyLoot/en/WheelMates';final.mkdir(parents=True,exist_ok=True)
for platform,text in texts.items():
 payload={k:scene[k] for k in ('background_video','native_width','native_height','corners')}
 payload.update(trailer_video='/input/partyloot_wheelmates_'+platform+'.mp4',game_name='WheelMates',show_game_title=True,title_font_size=42,title_offset_y=18,variant='monitor_dive',gameplay_fit='contain',card_enabled=True,card_text=text,card_preset='PartyLoot',intro_seconds=None,transition_seconds=3 if platform=='youtube' else 2.5,entry_sfx_enabled=True,entry_sfx_volume=.22,highlight_hook=True,opening_mode='original',opening_start_seconds=0,title_reveal_mode='after_entry',title_reveal_delay=.5,entrance_mode='action_match',action_lead_seconds=1,game_logo_enabled=True,game_logo_scale=1)
 (final/(platform+'_recipe.json')).write_text(json.dumps(payload,indent=2),encoding='utf-8')
 response=requests.post('http://127.0.0.1:8004/api/setup-trailer/render',json=payload,timeout=1500);response.raise_for_status();result=response.json()
 (final/(platform+'_result.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
 assert result.get('success'),result
 source=root/'output/trabajo/setup_trailer'/result['filename'];target=final/('youtube_shorts.mp4' if platform=='youtube' else 'instagram_reel.mp4')
 if not target.exists():os.link(source,target)
 print(platform,result['video_url'],flush=True)
if not (final/'tiktok_pendiente.mp4').exists():os.link(final/'instagram_reel.mp4',final/'tiktok_pendiente.mp4')
