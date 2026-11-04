"""Exercise the production FFmpeg composition with portrait and landscape clips."""
import ast
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required')
class BlurFormatTests(unittest.TestCase):
    def test_selected_format_has_centered_foreground_and_blurred_bands(self):
        tree = ast.parse((ROOT / 'promo_v3_engine.py').read_text(encoding='utf-8'))
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                        and n.name == '_build_cut_vf')
        for source_size in ('320x180', '180x320', '180x240'):
            for video_format, foreground_height in (('blur_3_4', 240), ('blur_1_1', 180),
                                                    ('full_9_16', 320)):
                with self.subTest(source=source_size, format=video_format):
                    scope = dict(Path=Path, video_format=video_format, out_w=180,
                                 out_h=320, is_4k=False)
                    exec(compile(ast.Module(body=[function], type_ignores=[]), 'render', 'exec'), scope)
                    frame = subprocess.check_output([
                        shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                        f'color=c=red:s={source_size}:r=30', '-vf',
                        scope['_build_cut_vf'](Path('unused')), '-frames:v', '1',
                        '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])
                    self.assertEqual(len(frame), 180 * 320 * 3)
                    red = lambda y: frame[(y * 180 + 90) * 3]
                    self.assertGreater(red(160), 240)
                    source_width, source_height = map(int, source_size.split('x'))
                    fit_height = min(foreground_height, int(180 * source_height / source_width))
                    if foreground_height < 320:
                        self.assertLess(red(10), red(160) - 10)
                        self.assertLess(red(309), red(160) - 10)
                        boundary = (320 - fit_height) // 2
                        self.assertGreater(red(boundary + 2), 240)
                        self.assertLess(red(boundary - 2), 240)
                    else:
                        self.assertGreater(red(10), 240)

    def test_portrait_keeps_top_bottom_and_sides_without_cropping(self):
        tree = ast.parse((ROOT / 'promo_v3_engine.py').read_text(encoding='utf-8'))
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                        and n.name == '_build_cut_vf')
        for video_format, foreground_height in (('blur_3_4', 240), ('blur_1_1', 180)):
            scope = dict(Path=Path, video_format=video_format, out_w=180, out_h=320, is_4k=False)
            exec(compile(ast.Module(body=[function], type_ignores=[]), 'render', 'exec'), scope)
            source = ('color=c=red:s=180x320:r=30,'
                      'drawbox=x=0:y=0:w=iw:h=30:color=blue:t=fill,'
                      'drawbox=x=0:y=290:w=iw:h=30:color=green:t=fill')
            frame = subprocess.check_output([shutil.which('ffmpeg'), '-v', 'error',
                '-f', 'lavfi', '-i', source, '-vf', scope['_build_cut_vf'](Path('unused')),
                '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])
            pixel = lambda y: frame[(y * 180 + 90) * 3:(y * 180 + 90) * 3 + 3]
            top = (320 - foreground_height) // 2
            self.assertGreater(pixel(top + 8)[2], 220, 'Top edge must remain visible')
            self.assertGreater(pixel(top + foreground_height - 8)[1], 100,
                               'Bottom edge must remain visible')


if __name__ == '__main__':
    unittest.main()
