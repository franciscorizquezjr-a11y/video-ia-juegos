import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from setup_trailer_engine import render_setup_trailer_video
from setup_trailer_motion import motion_timing

FFMPEG = shutil.which('ffmpeg')
FFPROBE = shutil.which('ffprobe')

@unittest.skipUnless(FFMPEG and FFPROBE, 'FFmpeg required')
class MonitorDiveRenderTests(unittest.TestCase):
    def test_short_and_long_timing_leaves_gameplay(self):
        for duration in (.3, 1.2, 4, 30):
            intro, move, outro, exit_move = motion_timing(duration)
            self.assertGreater(duration - intro - move - exit_move - outro, 0)

    def test_classic_and_motion_keep_duration_audio_and_full_gameplay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bg = root/'bg.mp4'
            trailer = root/'trailer.mp4'
            # Red gameplay with blue/green edges lets us detect unwanted central cropping.
            def run(args):
                subprocess.run([FFMPEG, '-v', 'error', '-y', *args], check=True, capture_output=True)
            run(['-f','lavfi','-i','color=gray:s=180x320:r=30','-t','1','-c:v','libx264',str(bg)])
            run(['-f','lavfi','-i','color=red:s=320x180:r=30,drawbox=x=0:y=0:w=40:h=ih:color=blue:t=fill,drawbox=x=280:y=0:w=40:h=ih:color=green:t=fill','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','4','-c:v','libx264','-c:a','aac',str(trailer)])
            corners={'tl':{'x':10,'y':125},'tr':{'x':170,'y':130},'bl':{'x':12,'y':215},'br':{'x':168,'y':220}}
            for variant,fit in [('classic','contain'),('monitor_dive','contain'),('monitor_dive','cover')]:
                with self.subTest(variant=variant,fit=fit):
                    out=root/f'{variant}_{fit}.mp4'
                    render_setup_trailer_video(bg,trailer,corners,out,show_game_title=False,canvas_w=180,canvas_h=320,native_w=180,native_h=320,ffmpeg_bin=FFMPEG,variant=variant,gameplay_fit=fit,card_text="A reusable game hook",card_style={"card_username":"Test Games","card_handle":"@test"})
                    meta=json.loads(subprocess.check_output([FFPROBE,'-v','error','-show_streams','-show_format','-of','json',str(out)]))
                    self.assertAlmostEqual(float(meta['format']['duration']),4,delta=.12)
                    self.assertTrue(any(s['codec_type']=='audio' for s in meta['streams']))
                    stream=next(s for s in meta['streams'] if s['codec_type']=='video')
                    self.assertEqual((stream['width'],stream['height']),(180,320))
                    def pixel(t,x,y):
                        raw=subprocess.check_output([FFMPEG,'-v','error','-ss',str(t),'-i',str(out),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
                        k=(y*180+x)*3
                        return raw[k:k+3]
                    # Intro and outro return to a neutral desktop at the top of the canvas.
                    for t in (.1,3.9):
                        color=pixel(t,90,10)
                        self.assertLess(max(color)-min(color),12)
                    if variant=='monitor_dive':
                        if fit=='contain':
                            self.assertGreater(pixel(2.8,5,160)[2],180,'Full gameplay must preserve blue left edge')
                            self.assertGreater(pixel(2.8,175,160)[1],90,'Full gameplay must preserve green right edge')
                        else:
                            self.assertGreater(pixel(2.8,5,160)[0],180,'Cover must show centered red gameplay')

    def test_short_portrait_without_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,size in [('bg','180x320'),('trailer','90x160')]:
                subprocess.run([FFMPEG,'-v','error','-y','-f','lavfi','-i',f'testsrc2=s={size}:r=30','-t','1.2','-c:v','libx264',str(root/f'{name}.mp4')],check=True,capture_output=True)
            corners={'tl':{'x':35,'y':85},'tr':{'x':145,'y':70},'bl':{'x':40,'y':260},'br':{'x':150,'y':245}}
            for enabled in (False,True):
                out=root/f'short_{enabled}.mp4'
                render_setup_trailer_video(root/'bg.mp4',root/'trailer.mp4',corners,out,game_name='Any Game',canvas_w=180,canvas_h=320,native_w=180,native_h=320,ffmpeg_bin=FFMPEG,variant='monitor_dive',card_text='Any other game',card_style={'card_username':'Another account'},entry_sfx_enabled=enabled)
                meta=json.loads(subprocess.check_output([FFPROBE,'-v','error','-show_streams','-show_format','-of','json',str(out)]))
                self.assertAlmostEqual(float(meta['format']['duration']),1.2,delta=.1)
                self.assertEqual(any(v['codec_type']=='audio' for v in meta['streams']),enabled)
                if enabled:
                    from array import array
                    pcm=subprocess.check_output([FFMPEG,'-v','error','-i',str(out),'-vn','-ar','48000','-ac','1','-f','f32le','-'])
                    samples=array('f'); samples.frombytes(pcm)
                    self.assertLess(max(abs(v) for v in samples[4800:14400]),.001,'No effect before entry')
                    self.assertGreater(max(abs(v) for v in samples[28800:33600]),.003,'Whoosh must coincide with entry')

    def test_reading_budget_and_manual_overrides(self):
        short=motion_timing(60,'word '*5)
        long=motion_timing(60,'word '*50)
        self.assertGreater(long[0],short[0])
        self.assertEqual(motion_timing(60,'',10,4),(10,4,1.5,.8))
        with self.assertRaises(ValueError):
            motion_timing(30,'',-1)

if __name__ == '__main__':
    unittest.main()
