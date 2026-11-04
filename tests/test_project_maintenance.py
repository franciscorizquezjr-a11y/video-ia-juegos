import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from scripts.maintenance import contained,plan_cleanup,apply_cleanup

class MaintenanceTests(unittest.TestCase):
    def test_workspace_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):contained(Path(tmp),"../outside.txt")
            with self.assertRaises(ValueError):contained(Path(tmp),".")

    def test_only_regenerable_files_from_verified_completed_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);session=root/"temp/v3_cache/session";work=session/"render_tmp";work.mkdir(parents=True)
            for file in ['voiceover.mp3','step4_edl.json','silent_video.mp4']:(session/file).write_text('preserve')
            (session/'step5_result.json').write_text(json.dumps({'filename':'ready.mp4','status':'completed'}))
            for file in ['temp_assembled.mp4','master_audio.mp3']:(work/file).write_text('test')
            with patch('scripts.maintenance.valid_video',return_value=True):plan=plan_cleanup(root)
            self.assertEqual([a['path'] for a in plan['actions']],['temp/v3_cache/session/render_tmp/temp_assembled.mp4'])
            with patch('scripts.maintenance.subprocess.run',return_value=Mock(returncode=0,stdout='0')):
                result=apply_cleanup(plan,root)
            self.assertEqual(result[0]['status'],'deleted')
            for file in ['voiceover.mp3','step4_edl.json','silent_video.mp4']:self.assertTrue((session/file).is_file())
            self.assertTrue((work/'master_audio.mp3').is_file())
            with patch('scripts.maintenance.valid_video',return_value=False):self.assertEqual(plan_cleanup(root)['actions'],[])

    def test_modified_plan_cannot_delete_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'data/private.json';p.parent.mkdir();p.write_text('preserve')
            plan={'root':str(root),'actions':[{'action':'delete','path':'data/private.json','bytes':8}]}
            with patch('scripts.maintenance.subprocess.run',return_value=Mock(returncode=0,stdout='0')):
                with self.assertRaises(ValueError):apply_cleanup(plan,root)
            self.assertTrue(p.is_file())

    def test_busy_ffmpeg_blocks_cleanup(self):
        import os
        if os.name!='nt':self.skipTest('Windows process guard')
        with tempfile.TemporaryDirectory() as tmp:
            with patch('scripts.maintenance.subprocess.run',return_value=Mock(returncode=0,stdout='1')):
                with self.assertRaises(RuntimeError):apply_cleanup({'root':tmp,'actions':[]},Path(tmp))

if __name__=='__main__':unittest.main()
