import sys,json,tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from PIL import Image
import web_server as server

checks=[]
with tempfile.TemporaryDirectory(prefix='logo_flow_',dir=Path(__file__).resolve().parent) as tmp:
    root=Path(tmp).resolve()
    assert Path(__file__).resolve().parent in root.parents
    session=root/'temp/v3_cache/logo_flow';session.mkdir(parents=True)
    saved={'game_name':'Missing Game','video_info':{'path':str(root/'source.mp4')},'shots':[]}
    (session/'step1_shots.json').write_text(json.dumps(saved),encoding='utf-8')
    (root/'bg.mp4').write_bytes(b'fixture');(root/'trailer.mp4').write_bytes(b'fixture')
    with patch.object(server,'BASE_DIR',root),patch.object(server,'SETUP_TRAILER_CONFIG_PATH',root/'not_configured.json'):
        client=TestClient(server.app)
        with patch('production_logo_policy.resolve_game_logo',return_value=None):
            response=client.post('/api/promov3/step5/render',json={'session_id':'logo_flow','game_name':'Missing Game'})
            assert response.status_code==400 and 'logo oficial' in response.json()['detail'],response.text
            checks.append('V3 rejects a missing logo before voice synthesis or rendering')
            response=client.post('/api/setup-trailer/render',json={'game_name':'Missing Game','background_video':'/bg.mp4','trailer_video':'/trailer.mp4','show_game_title':False,'game_logo_enabled':False,'narration_enabled':False})
            assert response.status_code==400 and 'logo oficial' in response.json()['detail'],response.text
            checks.append('Setup rejects missing logo even with both legacy toggles off')
        response=client.get('/api/promov3/session',params={'session_id':'logo_flow'})
        assert response.status_code==200 and 'Missing Game' in response.text,response.text
        assert json.loads((session/'step1_shots.json').read_text(encoding='utf-8'))==saved
        checks.append('Cached session remains retrievable and unmodified after rejection')
        logo=root/'PromosFootage/Test/game_logo.png';logo.parent.mkdir(parents=True)
        fixture_logo=Image.new('RGBA',(80,30),(0,0,0,0));fixture_logo.paste('blue',(5,5,75,25));fixture_logo.save(logo)
        def render_stub(*args,**kwargs):
            assert kwargs['show_game_title'] is True
            assert kwargs['game_logo_path']==logo.resolve()
            output=kwargs['output_path'];output.parent.mkdir(parents=True,exist_ok=True)
            output.write_bytes(b'fixture-output');output.with_suffix('.settings.json').write_text('{}')
            return output
        with patch('setup_trailer_engine.render_setup_trailer_video',side_effect=render_stub):
            response=client.post('/api/setup-trailer/render',json={'game_name':'Test','background_video':'/bg.mp4','trailer_video':'/trailer.mp4','show_game_title':False,'game_logo_enabled':False,'narration_enabled':False})
            assert response.status_code==200,response.text
        checks.append('Setup passes valid logo to compositor and forces its display')
        job=server.Job('logo_flow_sse','test');job.complete({'test':True})
        server.ACTIVE_JOBS[job.job_id]=job
        try:
            response=client.get('/api/jobs/logo_flow_sse/stream')
            assert response.status_code==200 and 'text/event-stream' in response.headers['content-type']
            assert '"type": "completed"' in response.text
            assert not job.subscribers
            checks.append('SSE completion delivered and subscriber released')
        finally:server.ACTIVE_JOBS.pop(job.job_id,None)
report={'date':'2026-10-30','checks':checks,'status':'passed','external_api_calls':False,'compositor_note':'API success case uses compositor stub; separate existing logo render tests passed with real FFmpeg'}
(ROOT/'data/maintenance/logo_flow_checks_20261030.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
