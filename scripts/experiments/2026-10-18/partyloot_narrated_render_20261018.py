from pathlib import Path
import json,time,requests,os
root=Path.cwd();folder=root/'output/gestion/PartyLoot/en/WheelMates'
payload=json.loads((folder/'youtube_recipe.json').read_text())
assets=json.loads((root/'input/partyloot_youtube_narration_assets.json').read_text())
payload.update(narration_enabled=True,narration_text=assets['text'],background_audio_volume=.12)
(folder/'youtube_narrated_recipe.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
for _ in range(90):
 try:
  response=requests.get('http://127.0.0.1:8004/openapi.json',timeout=3)
  if response.status_code==200 and 'narration_enabled' in response.json()['components']['schemas']['SetupTrailerRenderRequest']['properties']:break
 except requests.RequestException:pass
 time.sleep(2)
else:raise RuntimeError('New narration server did not start')
r=requests.post('http://127.0.0.1:8004/api/setup-trailer/render',json=payload,timeout=1500)
r.raise_for_status();result=r.json();assert result.get('success'),result
(folder/'youtube_narrated_result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
source=root/'output/trabajo/setup_trailer'/result['filename'];target=folder/'youtube_narrado.mp4'
if not target.exists():os.link(source,target)
print(result['video_url'],flush=True)
