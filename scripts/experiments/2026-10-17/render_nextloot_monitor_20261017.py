from pathlib import Path
import sys
PROJECT_ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(PROJECT_ROOT))
import json, shutil, subprocess
from utils import create_text_card
from setup_trailer_engine import render_setup_trailer_video
root=PROJECT_ROOT
assets=root/'static/setupbackground'
assets.mkdir(parents=True,exist_ok=True)
out=root/'output/trabajo/setup_trailer'
out.mkdir(parents=True,exist_ok=True)
image=assets/'nextloot_violet_setup_20261017.png'
shutil.copy2(r'C:\Users\franc\.codex\generated_images\01a1460e-e289-7c22-bdee-84f76a0c1eaf\exec-df076d96-baa1-4a43-bfa0-cb99f4df951c.png',image)
text="You literally just fling shoes at everything to solve physics puzzles. It's a rebellious teen causing chaos while outsmarting grumpy neighbors and bosses. Each level has multiple solutions; so you can master the perfect trick shot to destroy the boring world."
card=assets/'nextloot_shoe_long_purple_card.png'
create_text_card(text,card,username='Next Loot Games',handle='@nextlootgames',width=950,height=540,bg_color='#332052',text_color='#ffffff',border_color='#a78bfa',border_width=5,radius=35,avatar_text='NL',avatar_bg='#a78bfa',avatar_image_path=root/'Presets/NextLoot/NextLoot_Logo.png',font_size_body=38,avatar_radius=42,font_size_username=38,font_size_handle=26)
def run(args):
    p=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*args],capture_output=True,text=True)
    if p.returncode: raise RuntimeError(p.stderr)
bg=assets/'nextloot_shoe_setup_card_20261017.mp4'
run(['-loop','1','-framerate','30','-i',str(image),'-i',str(card),'-filter_complex','[0:v]scale=1080:1920[bg];[bg][1:v]overlay=65:110,format=yuv420p[v]','-map','[v]','-t','2','-c:v','libx264','-preset','veryfast','-crf','18',str(bg)])
source=root/'PromosFootage/Shoe It All!/yt_05f569.mp4'
clip=root/'input/shoe_setup_30s_20261017.mp4'
run(['-ss','3','-i',str(source),'-t','30','-vf','scale=1280:720,fps=30','-c:v','libx264','-preset','veryfast','-crf','18','-c:a','aac','-b:a','192k',str(clip)])
# Screen interior measured on generated 941x1672 image, converted to the UI's 720x1280 coordinates.
corners={name:{'x':x/941*720,'y':y/1672*1280} for name,(x,y) in {'tl':(57,616),'tr':(885,616),'br':(885,1018),'bl':(57,1018)}.items()}
config={'background_video':'/static/setupbackground/'+bg.name,'trailer_video':'/input/'+clip.name,'camera_effect_intensity':0.5,'game_name':'Shoe It All!','show_game_title':True,'title_font_size':26,'title_offset_y':17,'native_width':720,'native_height':1280,'corners':corners,'updated_at':'2026-10-17','card_text':text}
(root/'data/setup_trailer_config.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
(root/'output/trabajo/setup_trailer/shoe_setup_recipe_20261017.json').write_text(json.dumps({'config':config,'image_prompt':'Photorealistic vertical gaming setup, blank monitor, violet lighting, clear upper wall for purple card. Built-in image generation.','source':str(source),'source_start_seconds':3,'duration_seconds':30},indent=2),encoding='utf-8')
print('Background, purple card and monitor calibration ready.',flush=True)
final=out/'nextloot_shoe_monitor_purple_20261017.mp4'
render_setup_trailer_video(bg,clip,corners,final,game_name='Shoe It All!',font_size=26,title_offset_y=17)
run(['-ss','8','-i',str(final),'-frames:v','1',str(out/'nextloot_shoe_monitor_purple_preview.jpg')])
print('DONE '+str(final),flush=True)
