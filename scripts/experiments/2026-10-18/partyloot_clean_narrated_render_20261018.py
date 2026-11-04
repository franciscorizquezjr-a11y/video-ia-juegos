from pathlib import Path
import json,time,requests,os
root=Path.cwd();folder=root/'output/gestion/PartyLoot/en/WheelMates'
payload=json.loads((folder/'youtube_narrated_recipe.json').read_text())
payload.update(narration_layout='clean',narration_cta='Follow {channel} for your next game night.',card_enabled=True,intro_seconds=None)
(folder/'youtube_clean_recipe.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
for _ in range(90):
 try:
  r=requests.get('http://127.0.0.1:8004/openapi.json',timeout=3)
  if r.status_code==200 and 'narration_layout' in r.json()['components']['schemas']['SetupTrailerRenderRequest']['properties']:break
 except requests.RequestException:pass
 time.sleep(1)
else:raise RuntimeError('Clean narrated server did not start')
r=requests.post('http://127.0.0.1:8004/api/setup-trailer/render',json=payload,timeout=1500);r.raise_for_status();result=r.json();assert result.get('success'),result
details=result['render_details'];assert not details['card_enabled'];assert details['narration']['layout']=='clean';assert details['narration']['cta']=='Follow Party Loot for your next game night.'
(folder/'youtube_clean_result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
source=root/'output/trabajo/setup_trailer'/result['filename'];target=folder/'youtube_narrado_limpio.mp4'
if not target.exists():os.link(source,target)
print(result['video_url'],flush=True)
