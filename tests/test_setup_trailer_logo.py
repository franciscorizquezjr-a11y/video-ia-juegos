import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
from PIL import Image
from setup_trailer_logo import resolve_game_logo,logo_directory,build_logo_overlay
from setup_trailer_engine import render_setup_trailer_video

class GameLogoTests(unittest.TestCase):
    def make_logo(self,path,color="blue"):
        path.parent.mkdir(parents=True,exist_ok=True)
        image=Image.new("RGBA",(100,80),(0,0,0,0))
        image.paste(color,(20,25,80,55));image.save(path)
        return path

    def test_game_identity_and_custom_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);session=root/"temp/v3_cache/session_a"
            custom=self.make_logo(session/"custom_game_logo.png")
            (session/"step3_script.json").write_text(json.dumps({"game_name":"Game A!"}))
            self.assertEqual(resolve_game_logo("Game A",root,False),custom)
            self.assertIsNone(resolve_game_logo("Game B",root,False))
            self.make_logo(logo_directory("Game A",root)/"official.png","red")
            self.assertEqual(resolve_game_logo("Game A",root,False),custom)
            custom_b=self.make_logo(logo_directory("Game B",root)/"custom.png","green")
            self.assertEqual(resolve_game_logo("Game B",root,False),custom_b)

    def test_unreadable_cache_falls_back_to_game_logo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            logo=self.make_logo(root/"GamesFootage"/"Target Game"/"game_logo.png")
            damaged=logo_directory("Target Game",root)/"custom.png"
            original=Path.is_file
            def checked(path):
                if path==damaged:
                    raise OSError(1392,"Unreadable cache")
                return original(path)
            with patch.object(Path,"is_file",checked):
                self.assertEqual(resolve_game_logo("Target Game",root,False),logo)

    def test_official_search_requires_exact_game_and_network_failure_is_optional(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);image=Image.new("RGBA",(50,30),(0,0,255,255));buf=io.BytesIO();image.save(buf,format="PNG")
            search=Mock();search.json.return_value={"items":[{"id":1,"name":"Different game"},{"id":2,"name":"Target Game"}]}
            download=Mock();download.content=buf.getvalue()
            with patch("requests.get",side_effect=[search,download]) as get:
                path=resolve_game_logo("Target Game",root)
                self.assertTrue(path.is_file())
                self.assertIn("/2/logo.png",get.call_args_list[1].args[0])
            with patch("requests.get",side_effect=OSError("offline")):
                self.assertIsNone(resolve_game_logo("No logo game",root))

    def test_transparent_padding_and_proportions_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);logo=self.make_logo(root/"logo.png")
            corners={"tl":{"x":10,"y":130},"tr":{"x":170,"y":130},"bl":{"x":10,"y":220},"br":{"x":170,"y":220}}
            out=root/"overlay.png";build_logo_overlay(logo,out,corners,180,320,180,320,large=True)
            image=Image.open(out);bounds=image.getchannel("A").getbbox()
            self.assertLessEqual(bounds[3],130)
            self.assertAlmostEqual((bounds[2]-bounds[0])/(bounds[3]-bounds[1]),2,delta=.1)
            self.assertEqual(image.getpixel((0,0))[3],0)

    @unittest.skipUnless(shutil.which("ffmpeg"),"FFmpeg required")
    def test_render_logo_is_hidden_before_reveal_and_survives_outro(self):
        ff=shutil.which("ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);logo=self.make_logo(root/"logo.png");bg=root/"bg.mp4";source=root/"source.mp4"
            for path,color,size in ((bg,"gray","180x320"),(source,"red","160x90")):
                subprocess.run([ff,"-v","error","-y","-f","lavfi","-i",f"color={color}:s={size}:r=30","-t","6","-c:v","libx264",str(path)],check=True,capture_output=True)
            corners={"tl":{"x":10,"y":130},"tr":{"x":170,"y":130},"bl":{"x":10,"y":220},"br":{"x":170,"y":220}}
            out=root/"out.mp4"
            render_setup_trailer_video(bg,source,corners,out,game_name="Test",font_size=18,canvas_w=180,canvas_h=320,native_w=180,native_h=320,
                ffmpeg_bin=ff,variant="monitor_dive",title_reveal_mode="after_entry",title_reveal_delay=.2,game_logo_path=logo,entry_sfx_enabled=False)
            def pixel(t):
                data=subprocess.check_output([ff,"-v","error","-ss",str(t),"-i",str(out),"-frames:v","1","-pix_fmt","rgb24","-f","rawvideo","-"])
                k=(65*180+90)*3;return data[k:k+3]
            self.assertLess(max(pixel(.3))-min(pixel(.3)),15)
            for t in (4.3,5.85):
                self.assertGreater(pixel(t)[2],200)
                self.assertLess(pixel(t)[0],35)
            self.assertTrue(json.loads(out.with_suffix(".settings.json").read_text())["game_logo_used"])

if __name__=="__main__": unittest.main()
