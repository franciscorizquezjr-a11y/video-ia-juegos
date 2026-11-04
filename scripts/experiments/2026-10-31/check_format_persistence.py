import json,sys,tempfile,ast
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from video_format_preferences import preferred_video_format,save_video_format
from fastapi.testclient import TestClient
import web_server as server
checks=[]
with tempfile.TemporaryDirectory(prefix='format_preference_',dir=Path(__file__).resolve().parent) as tmp:
    root=Path(tmp).resolve();assert Path(__file__).resolve().parent in root.parents
    path=root/'data/production_preferences.json';path.parent.mkdir()
    original={'NextLoot':{'voice_id':'keep-me','official_game_logo_required':True},'PartyLoot':{'rank_accent_color':'#41e1c2'}}
    path.write_text(json.dumps(original))
    assert preferred_video_format(path,'NextLoot')=='full_9_16'
    save_video_format(path,'NextLoot','blur_3_4')
    assert preferred_video_format(path,'NextLoot')=='blur_3_4'
    assert preferred_video_format(path,'PartyLoot')=='full_9_16'
    data=json.loads(path.read_text());assert data['NextLoot']['voice_id']=='keep-me' and data['NextLoot']['official_game_logo_required']
    before=path.read_bytes()
    for brand,fmt in [('NextLoot','invalid'),('../other','full_9_16')]:
        try:save_video_format(path,brand,fmt)
        except ValueError:pass
        else:raise AssertionError('Invalid setting accepted')
        assert path.read_bytes()==before
    checks.extend(['default fullscreen without erasing settings','old layout survives cold read','per-brand isolation','invalid input cannot alter the file'])
    with patch.object(server,'BASE_DIR',root):
        client=TestClient(server.app)
        response=client.get('/api/production-preferences/video-format',params={'brand':'NextLoot'})
        assert response.status_code==200 and response.json()['video_format']=='blur_3_4'
        for brand,fmt in [('NextLoot','full_9_16'),('PartyLoot','blur_1_1')]:
            response=client.post('/api/production-preferences/video-format',json={'brand':brand,'video_format':fmt})
            assert response.status_code==200 and response.json()['saved']
        # A new client reads persisted JSON instead of relying on in-memory state.
        reopened=TestClient(server.app)
        assert reopened.get('/api/production-preferences/video-format?brand=NextLoot').json()['video_format']=='full_9_16'
        assert reopened.get('/api/production-preferences/video-format?brand=PartyLoot').json()['video_format']=='blur_1_1'
        checks.append('API save and reopening preserve independent channel choices')
    before=path.read_bytes();path.write_text('{broken')
    try:save_video_format(path,'NextLoot','full_9_16')
    except ValueError:pass
    else:raise AssertionError('Corrupt preferences overwritten')
    assert path.read_text()=='{broken';checks.append('corrupt file protected from overwrite')
for name in ['web_server.py','video_format_preferences.py']:ast.parse((ROOT/name).read_text(encoding='utf-8-sig'))
report={'date':'2026-10-31','checks':checks,'status':'passed','external_calls':False,'real_preferences_modified_by_test':False}
(ROOT/'data/maintenance/video_format_persistence_checks_20261031.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
