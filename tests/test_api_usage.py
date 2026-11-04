import json
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import requests
import api_usage as usage

class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.patch=patch.object(usage,'DB',Path(self.tmp.name)/'usage.sqlite3');self.patch.start()
        self.keys=patch.object(usage,'keys',return_value={'gemini':'g-secret','groq':'q-secret','elevenlabs':'e-secret'});self.keys.start()
    def tearDown(self):self.keys.stop();self.patch.stop();self.tmp.cleanup()
    def response(self,data,status=200,headers=None):
        r=requests.Response();r.status_code=status;r._content=json.dumps(data).encode();r.headers.update(headers or {});return r
    def provider(self,id,days=7):return next(p for p in usage.dashboard(days)['providers'] if p['id']==id)

    def test_local_counters_and_no_secrets(self):
        usage.record('gemini','test-model',total_tokens=120,input_tokens=100,output_tokens=20)
        usage.record('gemini','test-model',ok=False)
        p=self.provider('gemini');self.assertEqual(p['local']['requests'],2);self.assertEqual(p['local']['errors'],1);self.assertEqual(p['local']['tokens'],120)
        data=json.dumps(usage.dashboard());self.assertNotIn('g-secret',data);self.assertNotIn('e-secret',data)
        usage.save_budget('gemini',5,'requests','daily');self.assertEqual(self.provider('gemini')['budget']['remaining'],3)

    def test_gemini_response_is_not_changed_and_unknown_units_are_visible(self):
        original=SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=20,candidates_token_count=10,total_token_count=35))
        self.assertIs(usage.usage_generate(lambda **kw:original,model='test'),original)
        usage.usage_generate(lambda **kw:SimpleNamespace(),model='test')
        p=self.provider('gemini');self.assertEqual(p['local']['tokens'],35);self.assertEqual(p['local']['unknown_token_calls'],1)

    def test_provider_failure_is_recorded_and_reraised(self):
        with self.assertRaises(ValueError):usage.usage_generate(lambda **kw:(_ for _ in ()).throw(ValueError('private prompt')),model='test')
        self.assertEqual(self.provider('gemini')['local']['errors'],1)
        self.assertNotIn('private prompt',usage.DB.read_bytes().decode(errors='ignore'))

    def test_groq_units_are_daily_requests_and_minute_tokens_with_expiry(self):
        headers={'x-ratelimit-limit-requests':'1000','x-ratelimit-remaining-requests':'998','x-ratelimit-limit-tokens':'8000','x-ratelimit-remaining-tokens':'7900','x-ratelimit-reset-requests':'2h','x-ratelimit-reset-tokens':'2s'}
        response=self.response({'usage':{'prompt_tokens':50,'completion_tokens':25,'total_tokens':75}},headers=headers)
        with patch.object(usage.requests,'post',return_value=response):self.assertIs(usage.usage_post('https://api.groq.com/openai/v1/chat/completions',json={'model':'qwen'},headers={'Authorization':'Bearer q-secret'}),response)
        p=self.provider('groq');self.assertEqual(p['local']['tokens'],75);self.assertEqual(p['quota']['models'][0]['limits']['remaining-requests'],998)
        snap=usage.snapshots('groq',usage.fingerprint('q-secret'))[0];snap['limits']['reset_tokens_at']=time.time()-1;usage.save_snapshot('groq',usage.fingerprint('q-secret'),'qwen',{'status':'ok','limits':snap['limits']})
        self.assertIsNone(self.provider('groq')['quota']['models'][0]['limits']['remaining-tokens'])
        with patch.object(usage,'keys',return_value={'gemini':'','elevenlabs':'','groq':'changed-key'}):self.assertEqual(self.provider('groq')['quota']['status'],'awaiting_request')

    def test_eleven_balance_permission_cache_and_key_change(self):
        r=self.response({'detail':{'status':'missing_permissions','message':'private diagnostic'}},401)
        with patch.object(usage.requests,'get',return_value=r) as get:
            usage.refresh_accounts();usage.refresh_accounts();self.assertEqual(get.call_count,1)
            self.assertEqual(self.provider('elevenlabs')['quota']['status'],'permission_required')
            usage.refresh_accounts(force=True);self.assertEqual(get.call_count,2)
        self.assertNotIn('private diagnostic',json.dumps(usage.dashboard()))
        with patch.object(usage,'keys',return_value={'gemini':'','groq':'','elevenlabs':'new-key'}):self.assertEqual(self.provider('elevenlabs')['quota']['status'],'not_refreshed')

    def test_eleven_zero_remaining_is_real_and_expired_is_unknown(self):
        response=self.response({'character_count':1000,'character_limit':1000,'next_character_count_reset_unix':int(time.time())+500,'character_refresh_period':'monthly_period','tier':'test','bank_details':'must-not-store'})
        with patch.object(usage.requests,'get',return_value=response):usage.refresh_accounts()
        p=self.provider('elevenlabs');self.assertEqual(p['quota']['remaining'],0);self.assertNotIn('bank_details',json.dumps(p))
        data=dict(p['quota']);data['reset_at']=1;usage.save_snapshot('elevenlabs',usage.fingerprint('e-secret'),'',data)
        self.assertEqual(self.provider('elevenlabs')['quota']['status'],'expired');self.assertIsNone(self.provider('elevenlabs')['quota']['remaining'])

    def test_eleven_double_calls_count_each_request_characters_are_estimates(self):
        response=self.response({'audio_base64':'not-saved'})
        with patch.object(usage.requests,'post',return_value=response):
            for _ in range(2):usage.usage_post('https://api.elevenlabs.io/v1/text-to-speech/id/with-timestamps',json={'text':'hello','model_id':'test'})
        p=self.provider('elevenlabs');self.assertEqual(p['local']['requests'],2);self.assertEqual(p['local']['characters'],10)
        self.assertNotIn('hello',usage.DB.read_bytes().decode(errors='ignore'))

    def test_sdk_stream_tracks_once_and_preserves_bytes(self):
        self.assertEqual(list(usage.usage_tts(lambda **kw:iter([b'a',b'b']),text='hello',model_id='test')),[b'a',b'b'])
        self.assertEqual(self.provider('elevenlabs')['local']['characters'],5)

    def test_invalid_budgets(self):
        for args in [('unknown',5,'requests','monthly'),('gemini',5,'characters','monthly'),('groq',0,'tokens','daily')]:
            with self.assertRaises(ValueError):usage.save_budget(*args)
        usage.save_budget('gemini',None,'requests','monthly');self.assertIsNone(self.provider('gemini')['budget']['remaining'])

    def test_parallel_records_and_non_provider_requests(self):
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:usage.record('groq','test',total_tokens=2),range(20)))
        self.assertEqual(self.provider('groq')['local']['requests'],20)
        with patch.object(usage.requests,'post',return_value='response'):
            self.assertEqual(usage.usage_post('https://other.example/test',json={'text':'secret'}),'response')
        self.assertEqual(self.provider('groq')['local']['requests'],20)

    def test_telemetry_failure_never_breaks_generation(self):
        response=SimpleNamespace(usage_metadata=None)
        with patch.object(usage,'connection',side_effect=OSError('disk')):
            self.assertIs(usage.usage_generate(lambda **kw:response,model='test'),response)

if __name__=='__main__':unittest.main()
