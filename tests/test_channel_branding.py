"""Verify optional badges with the production filter and actual FFmpeg frames."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from pydantic import ValidationError
from channel_branding import ChannelBrandingSettings, build_channel_badge, add_channel_branding_filter

class ChannelBrandingTests(unittest.TestCase):
    def test_disabled_does_not_read_assets_or_change_filter_graph(self):
        settings = ChannelBrandingSettings(preset='MissingPreset')
        self.assertIsNone(build_channel_badge(settings))
        inputs, filters = ['-i', 'source.mp4'], ['[0:v]null[v_base]']
        self.assertEqual(add_channel_branding_filter(inputs, filters, '[v_base]', settings,
                                                    1080, 1920, Path('unused.png')), '[v_base]')
        self.assertEqual(inputs, ['-i', 'source.mp4'])
        self.assertEqual(filters, ['[0:v]null[v_base]'])

    def test_badge_scales_position_and_alpha_in_4k(self):
        settings = ChannelBrandingSettings(enabled=True, opacity=50)
        badge, x, y = build_channel_badge(settings)
        large, lx, ly = build_channel_badge(settings, 2160, 3840)
        self.assertEqual((x,y), (60,240))
        self.assertEqual((lx,ly), (120,480))
        self.assertEqual(large.height, badge.height * 2)
        self.assertAlmostEqual(large.width, badge.width * 2, delta=5)
        self.assertEqual(badge.getchannel('A').getextrema()[1], 128)

    def test_invalid_paths_and_ranges_rejected(self):
        for options in ({'preset':'../IGD'}, {'opacity':101}, {'size':0}, {'text':'x'*65}):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                ChannelBrandingSettings(**options)
        with self.assertRaises(ValueError):
            build_channel_badge(ChannelBrandingSettings(enabled=True,preset='MissingPreset'))

    def test_long_text_and_edge_positions_remain_inside_canvas(self):
        image, x, y = build_channel_badge(ChannelBrandingSettings(
            enabled=True, text='LongChannelName'*4, x=1000, y=1800, size=160))
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x+image.width,1080)
        self.assertLessEqual(y+image.height,1920)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required')
    def test_export_overlay_repeats_through_last_frame_and_preserves_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'test.mp4'
            inputs=['-f','lavfi','-i','color=c=black:s=180x320:r=10:d=1',
                    '-f','lavfi','-i','sine=frequency=440:duration=1']
            filters=['[0:v]null[v_base]']
            stream=add_channel_branding_filter(inputs,filters,'[v_base]',
                ChannelBrandingSettings(enabled=True),180,320,Path(directory)/'badge.png')
            subprocess.run([shutil.which('ffmpeg'),'-v','error','-y',*inputs,
                '-filter_complex',';'.join(filters),'-map',stream,'-map','1:a',
                '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-t','1',str(out)],check=True)
            for t in ('0','0.8'):
                raw=subprocess.check_output([shutil.which('ffmpeg'),'-v','error','-ss',t,
                    '-i',str(out),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
                self.assertEqual(len(raw),180*320*3)
                self.assertGreater(max(raw),100)
                # Branding remains in the top-left region, away from gameplay/captions.
                self.assertEqual(max(raw[160*180*3:]),0)
            import json
            info=json.loads(subprocess.check_output([shutil.which('ffprobe'),'-v','error',
                '-show_entries','stream=codec_type,duration,width,height','-of','json',str(out)]))
            streams={stream['codec_type']:stream for stream in info['streams']}
            self.assertEqual(streams['video']['width'],180)
            self.assertAlmostEqual(float(streams['audio']['duration']),1,delta=.05)

if __name__ == '__main__':
    unittest.main()
