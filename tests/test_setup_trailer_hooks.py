import json
import shutil
import subprocess
import tempfile
import unittest
from array import array
from pathlib import Path
from PIL import Image
from setup_trailer_hooks import choose_active_opening, prepare_opening, title_reveal_time
from setup_trailer_engine import render_setup_trailer_video
from utils import create_text_card

FF=shutil.which("ffmpeg")
PROBE=shutil.which("ffprobe")

@unittest.skipUnless(FF and PROBE,"FFmpeg required")
class TrailerHooksTests(unittest.TestCase):
    def run_ff(self,args):
        return subprocess.run([FF,"-v","error","-y",*args],capture_output=True,check=True).stdout

    def test_motion_selection_and_manual_audio_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/"source.mp4"
            self.run_ff(["-f","lavfi","-i","color=red:s=160x90:r=30:d=2","-f","lavfi","-i","testsrc2=s=160x90:r=30:d=4",
                "-f","lavfi","-i",r"aevalsrc=if(lt(t\,2)\,0.2*sin(2*PI*440*t)\,0.2*sin(2*PI*880*t)):d=6:s=48000",
                "-filter_complex","[0:v][1:v]concat=n=2:v=1:a=0[v]","-map","[v]","-map","2:a","-c:v","libx264","-c:a","aac",str(source)])
            offset=choose_active_opening(source,6,FF)
            self.assertGreaterEqual(offset,1.5,"Avoid the flat static opening")
            self.assertLess(offset,5.1)
            output,actual=prepare_opening(source,root/"manual.mp4","manual",2.5,FF)
            self.assertEqual(actual,2.5)
            duration=float(subprocess.check_output([PROBE,"-v","error","-show_entries","format=duration","-of","csv=p=0",str(output)]))
            self.assertAlmostEqual(duration,6,delta=.12)
            pcm=self.run_ff(["-i",str(output),"-vn","-ar","48000","-ac","1","-f","f32le","-"])
            samples=array("f");samples.frombytes(pcm)
            def hz(start):
                chunk=samples[int(start*48000):int((start+.25)*48000)]
                crossings=sum(a<=0<b for a,b in zip(chunk,chunk[1:]))
                return crossings/.25
            self.assertAlmostEqual(hz(.25),880,delta=12)
            self.assertAlmostEqual(hz(4.0),440,delta=12)
            with self.assertRaises(ValueError):
                prepare_opening(source,root/"bad.mp4","manual",7,FF)

    def test_hook_color_keeps_body_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            images=[]
            for enabled in (False,True):
                path=Path(tmp)/f"card_{enabled}.png"
                create_text_card("First hook sentence. Another sentence stays white.",path,width=950,height=540,
                    bg_color="#332052",font_size_body=38,highlight_first_sentence=enabled)
                images.append(Image.open(path).convert("RGB"))
            def yellow(im):
                return sum(r>180 and g>130 and b<100 for r,g,b in im.crop((0,160,950,500)).getdata())
            self.assertEqual(yellow(images[0]),0)
            self.assertGreater(yellow(images[1]),100)
            self.assertGreater(sum(min(px)>220 for px in images[1].crop((0,160,950,500)).getdata()),100)

    def test_delayed_title_is_absent_at_start_and_visible_in_gameplay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bg=root/"bg.mp4";source=root/"source.mp4"
            for path,color,size in ((bg,"gray","180x320"),(source,"red","160x90")):
                self.run_ff(["-f","lavfi","-i",f"color={color}:s={size}:r=30","-t","6","-c:v","libx264",str(path)])
            corners={"tl":{"x":10,"y":130},"tr":{"x":170,"y":130},"bl":{"x":10,"y":220},"br":{"x":170,"y":220}}
            paths=[]
            for show in (False,True):
                out=root/f"title_{show}.mp4";paths.append(out)
                render_setup_trailer_video(bg,source,corners,out,game_name="MYSTERY",show_game_title=show,font_size=18,
                    canvas_w=180,canvas_h=320,native_w=180,native_h=320,ffmpeg_bin=FF,variant="monitor_dive",
                    title_reveal_mode="after_entry",title_reveal_delay=.2,entry_sfx_enabled=False)
            def frame(path,t):
                return self.run_ff(["-ss",str(t),"-i",str(path),"-frames:v","1","-pix_fmt","rgb24","-f","rawvideo","-"])
            before=[frame(path,.3) for path in paths]
            self.assertLess(sum(abs(a-b) for a,b in zip(*before))/len(before[0]),.5)
            after=[frame(path,4.3) for path in paths]
            self.assertGreater(sum(abs(a-b)>30 for a,b in zip(*after)),100)
            details=json.loads(paths[1].with_suffix(".settings.json").read_text())
            self.assertAlmostEqual(details["title_reveal_seconds_actual"],3.8)

    def test_reveal_timing_modes(self):
        self.assertEqual(title_reveal_time(30,"monitor_dive","immediate"),0)
        self.assertEqual(title_reveal_time(30,"monitor_dive","outro"),28.5)
        self.assertGreater(title_reveal_time(30,"monitor_dive","after_entry"),7)

if __name__ == "__main__":
    unittest.main()
