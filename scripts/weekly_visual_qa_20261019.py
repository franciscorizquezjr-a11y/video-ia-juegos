from pathlib import Path
import json,subprocess,time
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[1];PACK=ROOT/'output/gestion/semana_2026-10-19'
for brand in ['PartyLoot','NextLoot']:
    sheet=Image.new('RGB',(810,5*510),'#151922');draw=ImageDraw.Draw(sheet)
    for n,folder in enumerate(sorted((PACK/brand).iterdir())):
        if not folder.is_dir():continue
        video=folder/'video.mp4'
        if brand=='NextLoot' and 'valheim' in folder.name:
            deadline=time.monotonic()+1200
            while time.monotonic()<deadline:
                result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
                if result['render_details']['source'].endswith('_montage_v2.mp4'):break
                time.sleep(2)
        duration=float(json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',str(video)]))['format']['duration'])
        for i,t in enumerate([.6,10,duration-.8]):
            frame=folder/f'qa_final_{i}.jpg';subprocess.run(['ffmpeg','-v','error','-y','-ss',str(t),'-i',str(video),'-frames:v','1','-vf','scale=270:480',str(frame)],check=True,capture_output=True)
            sheet.paste(Image.open(frame),(i*270,n*510+30));draw.text((i*270+5,n*510+6),folder.name+f' / {t:.1f}s',fill='white')
    sheet.save(PACK/(brand+'_revision.jpg'));print(brand,'visual QA sheet saved',flush=True)
