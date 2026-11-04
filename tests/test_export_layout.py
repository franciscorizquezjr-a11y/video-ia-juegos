import json,os,shutil,subprocess,unittest
from pathlib import Path
from unittest.mock import patch
from project_paths import ROOT,OUTPUT_ROOT,APP_OUTPUT_DIR,output_url
from config import AppConfig
def find_ffmpeg():
 """FFMPEG_PATH (environment, then .env read-only), then ffmpeg on PATH; None if absent."""
 candidates=[os.environ.get('FFMPEG_PATH')]
 try:
  from dotenv import dotenv_values
  candidates.append(dotenv_values(ROOT/'.env').get('FFMPEG_PATH'))
 except Exception:pass
 candidates.append('ffmpeg')
 for candidate in candidates:
  if not candidate:continue
  if Path(candidate).is_file():return str(candidate)
  found=shutil.which(candidate)
  if found:return found
 return None
class ExportLayoutTests(unittest.TestCase):
 def test_legacy_root_setting_uses_new_video_folder(self):
  with patch.dict(os.environ,{'OUTPUT_DIR':'output'}):
   self.assertEqual(AppConfig.from_env().output_dir,APP_OUTPUT_DIR)
 def test_download_url_is_derived_from_actual_nested_path(self):
  self.assertEqual(output_url(OUTPUT_ROOT/'trabajo'/'dos palabras.zip'),'/media/output/trabajo/dos%20palabras.zip')
  with self.assertRaises(ValueError):output_url(ROOT/'input'/'source.mp4')
 def test_media_sessions_and_completion_event(self):
  ff=find_ffmpeg()
  if not ff:self.skipTest('FFmpeg not found: set FFMPEG_PATH or put ffmpeg on PATH')
  from fastapi.testclient import TestClient
  import web_server as server
  # Same Host the local app is opened with (the launcher prefers port 8004).
  client=TestClient(server.app,base_url='http://127.0.0.1:8004')
  probe=APP_OUTPUT_DIR/'_path_verification.mp4';probe.parent.mkdir(parents=True,exist_ok=True)
  try:
   subprocess.run([ff,'-v','error','-y','-f','lavfi','-i','color=c=blue:s=32x64:r=10','-t','0.3','-c:v','libx264','-pix_fmt','yuv420p',str(probe)],check=True)
   url=output_url(probe)
   response=client.get(url,headers={'Range':'bytes=0-31'})
   self.assertEqual(response.status_code,206);self.assertEqual(len(response.content),32)
   self.assertEqual(client.get('/api/health').status_code,200)
   sessions=client.get('/api/promov3/sessions');self.assertEqual(sessions.status_code,200)
   self.assertIsInstance(sessions.json()['sessions'],list)
   job=server.create_job('layout-verification');job.complete({'video_url':url})
   try:
    response=client.get('/api/jobs/'+job.job_id+'/stream')
    self.assertEqual(response.status_code,200)
    payload=json.loads(response.text.removeprefix('data: ').strip())
    self.assertEqual(payload['type'],'completed');self.assertEqual(payload['result']['video_url'],url)
   finally:server.ACTIVE_JOBS.pop(job.job_id,None)
  finally:probe.unlink(missing_ok=True)
if __name__=='__main__':unittest.main()
