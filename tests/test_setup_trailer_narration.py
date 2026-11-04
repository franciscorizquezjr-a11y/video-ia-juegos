import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from setup_trailer_narration import media_duration,prepare_narration,apply_narration,compose_narration_script,narration_target_duration,trim_trailer_duration

@unittest.skipUnless(shutil.which("ffmpeg"),"FFmpeg required")
class NarrationTests(unittest.TestCase):
    def tone(self,path,duration=1):
        subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","sine=frequency=880:sample_rate=48000","-t",str(duration),"-c:a","pcm_s16le","-f","wav",str(path)],check=True,capture_output=True)

    def test_audio_duration_cache_and_reject_overlong_without_regenerating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=SimpleNamespace(elevenlabs_voice_id="test",elevenlabs_model="test",elevenlabs_api_key="unused")
            def tts(text,path,*args,**kwargs):
                self.tone(path)
                return str(path),[{"word":"hello","start":0,"end":.9}],{}
            with patch("audio_aligner.synthesize_elevenlabs_with_timestamps",side_effect=tts) as synth:
                first=prepare_narration("hello",cfg,root,3)
                second=prepare_narration("hello",cfg,root,3)
                self.assertEqual(first["audio"],second["audio"])
                self.assertEqual(synth.call_count,1)
                self.assertAlmostEqual(media_duration(first["audio"]),1,places=1)
                with self.assertRaisesRegex(ValueError,"no cabe"):
                    prepare_narration("hello",cfg,root,1)
                self.assertEqual(synth.call_count,1)

    def test_clean_script_uses_channel_once_and_keeps_language(self):
        text,cta=compose_narration_script("Juega con amigos", "Sigue a {channel} para más juegos.","Mi Canal")
        self.assertEqual(text,"Juega con amigos. Sigue a Mi Canal para más juegos.")
        self.assertEqual(compose_narration_script(text,cta,"Mi Canal")[0],text)
        with self.assertRaises(ValueError):compose_narration_script("",cta,"Mi Canal")
        target=narration_target_duration({"duration":23.28,"offset":.2},33)
        self.assertAlmostEqual(target,23.8)
        self.assertLess(target-23.28-.2,.34)
        with self.assertRaises(ValueError):narration_target_duration({"duration":23.28},20)

    def test_clean_camera_render_follows_voice_length_without_card(self):
        from setup_trailer_engine import render_setup_trailer_video
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bg=root/"bg.mp4";source=root/"source.mp4";out=root/"out.mp4"
            for path,color,size in [(bg,"navy","180x320"),(source,"red","160x90")]:
                subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i",f"color={color}:s={size}:r=30","-t","4","-c:v","libx264",str(path)],check=True,capture_output=True)
            corners={"tl":{"x":10,"y":125},"tr":{"x":170,"y":125},"bl":{"x":10,"y":215},"br":{"x":170,"y":215}}
            render_setup_trailer_video(bg,source,corners,out,canvas_w=180,canvas_h=320,native_w=180,native_h=320,show_game_title=False,variant="monitor_dive",card_text="",intro_seconds=.5,entry_sfx_enabled=True,target_duration=2)
            self.assertAlmostEqual(media_duration(out),2,delta=.05)
            details=json.loads(out.with_suffix(".settings.json").read_text(encoding="utf-8"))
            self.assertFalse(details["card_enabled"])
            self.assertEqual(details["export_duration"],2)
            self.assertEqual(list((root/"temp_assets").glob("narration_fit_*")),[])
            with self.assertRaises(ValueError):trim_trailer_duration(source,root/"bad.mp4",5)

    def test_silent_trailer_can_be_narrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);video=root/"silent.mp4";voice=root/"voice.wav";self.tone(voice)
            subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","color=navy:s=180x320:r=30","-t","2","-c:v","libx264",str(video)],check=True,capture_output=True)
            apply_narration(video,{"audio":voice,"duration":1,"text":"hello","words":[{"word":"hello","start":0,"end":.9}]})
            info=json.loads(subprocess.check_output(["ffprobe","-v","error","-show_streams","-of","json",str(video)]))
            self.assertTrue(any(s["codec_type"]=="audio" for s in info["streams"]))

    def test_mixed_voice_delay_muted_background_and_synchronized_captions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);video=root/"test.mp4";voice=root/"voice.wav";self.tone(voice)
            subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","color=navy:s=180x320:r=30","-f","lavfi","-i","sine=frequency=220:sample_rate=48000","-t","3","-c:v","libx264","-c:a","aac",str(video)],check=True,capture_output=True)
            assets={"audio":voice,"duration":1,"offset":.2,"text":"hello friend","words":[{"word":"hello","start":0,"end":.4},{"word":"friend","start":.4,"end":.9}]}
            apply_narration(video,assets,background_volume=0)
            self.assertAlmostEqual(media_duration(video),3,places=1)
            ass=video.with_suffix(".narration.ass").read_text()
            self.assertIn("0:00:00.20",ass)
            self.assertIn("HELLO",ass)
            settings=json.loads(video.with_suffix(".settings.json").read_text())
            self.assertEqual(settings["narration"]["background_volume"],0)
            import array
            data=subprocess.check_output(["ffmpeg","-v","error","-i",str(video),"-t","0.14","-f","s16le","-ac","1","-"])
            self.assertLess(max(abs(x) for x in array.array("h",data)),30)
            data=subprocess.check_output(["ffmpeg","-v","error","-ss","0.4","-i",str(video),"-t","0.2","-f","s16le","-ac","1","-"])
            self.assertGreater(max(abs(x) for x in array.array("h",data)),500)
            original=video.read_bytes()
            with self.assertRaises(ValueError):apply_narration(video,dict(assets,duration=4))
            self.assertEqual(video.read_bytes(),original)

if __name__=="__main__":unittest.main()
