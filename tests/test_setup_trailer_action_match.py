import json
import shutil
import subprocess
import tempfile
import unittest
from array import array
from pathlib import Path
from setup_trailer_engine import render_setup_trailer_video
from setup_trailer_motion import monitor_timeline

FF=shutil.which("ffmpeg")
PROBE=shutil.which("ffprobe")

@unittest.skipUnless(FF and PROBE,"FFmpeg required")
class ActionMatchTests(unittest.TestCase):
    def ff(self,args):
        return subprocess.run([FF,"-v","error","-y",*args],capture_output=True,check=True).stdout

    def test_budget_and_delayed_reveal_clock(self):
        for duration in (.3,1.2,6,30,90):
            timeline=monitor_timeline(duration,"A readable paragraph for this game.",entrance_mode="action_match")
            self.assertGreater(timeline["lead"],0)
            self.assertLess(timeline["gameplay_start"],timeline["return_start"])
        desktop=monitor_timeline(30,"Words.")
        action=monitor_timeline(30,"Words.",entrance_mode="action_match")
        self.assertAlmostEqual(action["gameplay_start"]-desktop["gameplay_start"],2)

    def test_two_sources_keep_action_and_audio_continuous(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bg=root/"bg.mp4"
            self.ff(["-f","lavfi","-i","color=gray:s=180x320:r=30:d=1","-c:v","libx264",str(bg)])
            for source_size,fit in (("160x90","contain"),("90x160","cover")):
                with self.subTest(size=source_size,fit=fit):
                    source=root/(source_size+".mp4")
                    video=(f"color=red:s={source_size}:r=30:d=6,"
                           "drawbox=x=0:y=0:w=iw:h=ih:color=green:t=fill:enable='gte(t,1)*lt(t,2)',"
                           "drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable='gte(t,2)*lt(t,3)',"
                           "drawbox=x=0:y=0:w=iw:h=ih:color=yellow:t=fill:enable='gte(t,3)'")
                    tone=r"aevalsrc=0.2*sin(2*PI*if(lt(t\,1)\,440\,if(lt(t\,2)\,660\,880))*t):d=6:s=48000"
                    self.ff(["-f","lavfi","-i",video,"-f","lavfi","-i",tone,"-c:v","libx264","-c:a","aac",str(source)])
                    corners={"tl":{"x":15,"y":120},"tr":{"x":165,"y":135},"bl":{"x":20,"y":220},"br":{"x":160,"y":235}}
                    out=root/(source_size+"_out.mp4")
                    render_setup_trailer_video(bg,source,corners,out,show_game_title=False,canvas_w=180,canvas_h=320,native_w=180,native_h=320,
                        ffmpeg_bin=FF,variant="monitor_dive",gameplay_fit=fit,entrance_mode="action_match",entry_sfx_enabled=False)
                    def pixel(t,x,y):
                        frame=self.ff(["-ss",str(t),"-i",str(out),"-frames:v","1","-pix_fmt","rgb24","-f","rawvideo","-"])
                        k=(y*180+x)*3
                        return frame[k:k+3]
                    self.assertGreater(pixel(.2,90,170)[0],200,"Cold open starts with live red action")
                    self.assertGreater(pixel(1.3,90,170)[1],90,"The next green moment continues inside the monitor")
                    self.assertGreater(pixel(2.3,90,170)[2],200,"No reset to the beginning after the transition")
                    edge_time=monitor_timeline(6,entrance_mode="action_match")["lead"]-.04
                    outside=pixel(edge_time,5,50)
                    self.assertGreater(min(outside),65,"Transparent guard must not smear red frame borders over the desktop")
                    pcm=self.ff(["-i",str(out),"-vn","-ar","48000","-ac","1","-f","f32le","-"])
                    samples=array("f");samples.frombytes(pcm)
                    for start,hz in ((.2,440),(1.3,660),(2.3,880)):
                        part=samples[int(start*48000):int((start+.2)*48000)]
                        frequency=sum(a<=0<b for a,b in zip(part,part[1:]))/.2
                        self.assertAlmostEqual(frequency,hz,delta=15)
                    meta=json.loads(subprocess.check_output([PROBE,"-v","error","-show_streams","-show_format","-of","json",str(out)]))
                    self.assertAlmostEqual(float(meta["format"]["duration"]),6,delta=.1)

if __name__ == "__main__": unittest.main()
