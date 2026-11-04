"""Sentence timing, shot selection and render preservation without external APIs."""
import json
from pathlib import Path
import tempfile
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch

import promo_v3_engine as engine

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = 'New survival game explained by caveman. Me build boat. But monster big. Me grab axe.'


def speech_words():
    words, start = [], 0.0
    for text, duration in zip(SCRIPT.split('. ') , [4, 2, 2, 2]):
        tokens = text.split()
        for index, token in enumerate(tokens):
            words.append({'word': token.rstrip('.'), 'start': start + duration * index / len(tokens),
                          'end': start + duration * (index + 1) / len(tokens)})
        start += duration
    return words


def shots():
    return [
        {'shot_id': 1, 'duration': 5, 'viral_score': 100, 'description': 'Shocking giant monster bursts through wall'},
        {'shot_id': 2, 'duration': 2, 'viral_score': 5, 'description': 'Player builds wooden boat'},
        {'shot_id': 3, 'duration': 2, 'viral_score': 20, 'description': 'Big monster chases player'},
        {'shot_id': 4, 'duration': 2, 'viral_score': 0, 'description': 'Player grabs axe'},
        {'shot_id': 5, 'duration': 5, 'viral_score': 90, 'description': 'Unrelated landscape'},
    ]


class CavemanMontageTests(unittest.TestCase):
    def test_sentence_boundaries_use_words_even_without_punctuation(self):
        ranges = engine._caveman_sentence_ranges(SCRIPT, 10, speech_words(), hook_end=2.5)
        self.assertEqual([s['end'] for s in ranges], [4, 6, 8, 10])
        self.assertEqual([s['text'] for s in ranges], [s.strip() + '.' for s in SCRIPT.rstrip('.').split('. ')])

    def test_viral_hook_and_semantic_body_in_fallback(self):
        with patch.dict(engine.os.environ, {'GROQ_API_KEY': ''}):
            edl = engine.orchestrate_promov3_step4_edl(shots(), SCRIPT, voice_duration=10,
                alignment_data={'words': speech_words()}, script_mode='caveman')
        self.assertEqual(edl['script_mode'], 'caveman')
        self.assertEqual([cut['shot_id'] for cut in edl['timeline']], [1, 2, 3, 4])
        self.assertEqual([cut['duration'] for cut in edl['timeline']], [4, 2, 2, 2])
        self.assertEqual(edl['cut_count'], 4)
        self.assertEqual(edl['timeline'][0]['transition_out']['type'], 'white_flash')
        self.assertEqual(edl['total_duration'], 10)

    def test_director_cannot_split_hook_or_combine_sentences(self):
        response = Mock(status_code=200)
        response.json.return_value = {'choices': [{'message': {'content': json.dumps({'timeline': [
            {'shot_id': 5, 'duration': 2, 'role': 'hook', 'script_segment': 'Wrong hook'},
            {'shot_id': 1, 'duration': 2, 'role': 'hook'},
            {'shot_id': 2, 'duration': 6, 'script_segment': 'Combined sentences'},
        ]})}}]}
        with patch('requests.post', return_value=response) as post:
            edl = engine.orchestrate_promov3_step4_edl(shots(), SCRIPT, voice_duration=10,
                alignment_data=speech_words(), script_mode='caveman', groq_api_key='fake')
        self.assertEqual([cut['shot_id'] for cut in edl['timeline']], [1, 2, 3, 4])
        prompt = post.call_args.kwargs['json']['messages'][1]['content']
        self.assertIn('EXACTAMENTE un corte', prompt)
        self.assertIn('combina dos o más', prompt)
        self.assertIn('al menos 0.8 segundos', prompt)
        self.assertIn('sentence_04', prompt)
        self.assertNotIn('DEBES dividir el hook', prompt)

    def test_global_assignment_reserves_long_clip_for_long_sentence(self):
        ranges = [{'sentence_id': 'sentence_02', 'text': 'Me grab axe.', 'duration': 2},
                  {'sentence_id': 'sentence_03', 'text': 'Me build boat.', 'duration': 5}]
        candidates = [{'shot_id': 1, 'duration': 5, 'description': 'Player grabs axe and builds boat'},
                      {'shot_id': 2, 'duration': 2, 'description': 'Player grabs axe'}]
        assignment = engine._assign_sentence_shots(ranges, candidates, [], '')
        self.assertEqual([s['shot_id'] for s in assignment], [2, 1])

    def test_insufficient_footage_never_repeats(self):
        ranges = engine._caveman_sentence_ranges(SCRIPT, 10, speech_words())
        with self.assertRaises(engine.InsufficientUniqueShotsError):
            engine._plan_caveman_timeline(shots()[:3], [], ranges, '')
        short = [dict(shot, duration=1) for shot in shots()]
        with self.assertRaises(engine.InsufficientUniqueShotsError):
            engine._plan_caveman_timeline(short, [], ranges, '')

    def test_short_clips_fill_hook_and_body_without_gaps_or_duplicates(self):
        candidates = [dict(shot_id=i + 1, duration=2, viral_score=100 - i,
                           description='Player builds boat and grabs axe') for i in range(6)]
        with patch.dict(engine.os.environ, {'GROQ_API_KEY': ''}):
            edl = engine.orchestrate_promov3_step4_edl(candidates, SCRIPT, voice_duration=10,
                alignment_data={'words': speech_words()}, script_mode='caveman')
        cuts = edl['timeline']
        self.assertEqual([c['duration'] for c in cuts], [2, 2, 2, 2, 2])
        self.assertEqual(cuts[0]['shot_id'], 1)
        self.assertEqual([c['role'] for c in cuts[:2]], ['hook', 'hook'])
        self.assertEqual(cuts[0]['transition_out']['type'], 'cut')
        self.assertEqual(cuts[1]['transition_out']['type'], 'white_flash')
        self.assertEqual(len({c['shot_id'] for c in cuts}), len(cuts))
        self.assertEqual(cuts[-1]['end_time_in_video'], 10)
        for left, right in zip(cuts, cuts[1:]):
            self.assertEqual(left['end_time_in_video'], right['start_time_in_video'])
        for sentence in engine._caveman_sentence_ranges(SCRIPT, 10, speech_words()):
            self.assertAlmostEqual(sum(c['duration'] for c in cuts if c['sentence_id'] == sentence['sentence_id']), sentence['duration'])

    def test_split_reserves_minimum_duration_for_final_piece(self):
        ranges = [dict(sentence_id='hook', text='New game.', duration=2)]
        plan = engine._plan_caveman_timeline([
            dict(shot_id=1, duration=1.7, viral_score=100),
            dict(shot_id=2, duration=0.9, viral_score=20),
            dict(shot_id=3, duration=0.7, viral_score=90),
        ], [], ranges, '')
        self.assertEqual([c['shot_id'] for c in plan], [1, 2])
        self.assertEqual([c['duration'] for c in plan], [1.2, 0.8])

    def test_split_reserves_long_shot_for_later_sentence(self):
        ranges = [dict(sentence_id='hook', text='New game.', duration=3),
                  dict(sentence_id='body', text='Me build.', duration=4)]
        plan = engine._plan_caveman_timeline([
            dict(shot_id=1, duration=4, viral_score=100),
            dict(shot_id=2, duration=1.5, viral_score=90),
            dict(shot_id=3, duration=1.5, viral_score=80),
        ], [], ranges, '')
        self.assertEqual([c['shot_id'] for c in plan], [2, 3, 1])

    def test_body_can_use_three_short_clips(self):
        ranges = [dict(sentence_id='hook', text='New game.', duration=1),
                  dict(sentence_id='body', text='Me build.', duration=2.4)]
        plan = engine._plan_caveman_timeline([
            dict(shot_id=1, duration=1, viral_score=100),
            *[dict(shot_id=i, duration=0.8, viral_score=10, description='Build house') for i in range(2, 5)],
        ], [], ranges, '')
        self.assertEqual([c['duration'] for c in plan], [1, 0.8, 0.8, 0.8])
        self.assertEqual([c['sentence_id'] for c in plan], ['hook', 'body', 'body', 'body'])

    def test_render_preserves_long_hook_body_and_sentence_crossing_second_12(self):
        class ExtractionComplete(Exception):
            pass
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            root = Path(folder)
            video, voice = root / 'source.mp4', root / 'voice.mp3'
            video.touch()
            voice.touch()
            cuts = [{'shot_id': i + 1, 'duration': duration, 'source_in': i * 10,
                     'video_path': str(video), 'role': 'hook' if i == 0 else 'gameplay',
                     'transition_out': {'type': 'white_flash' if i == 0 else 'cut'}}
                    for i, duration in enumerate([4, 6, 3])]
            edl = {'script_mode': 'caveman', 'sentence_1_end_s': 4, 'total_duration': 13,
                   'script_text': 'New game explained by caveman. Me build big boat. Monster big.', 'timeline': cuts}
            with patch('audio_quality.require_narration', return_value={'passed': True}), \
                 patch.object(engine, 'get_media_duration', return_value=13), \
                 patch.object(engine, 'check_media_has_audio', return_value=False), \
                 patch.object(engine, '_extract_subclip') as extract, \
                 patch.object(engine, '_run_ffmpeg_cmd', side_effect=ExtractionComplete):
                with self.assertRaises(ExtractionComplete):
                    engine.render_promov3_final_video(edl, video, voice, None, root / 'output',
                                                     enable_steam_hook=False, temp_dir=root / 'render')
            self.assertEqual([call.kwargs['duration'] for call in extract.call_args_list], [4, 6, 3])
            self.assertEqual([call.kwargs['start_time'] for call in extract.call_args_list], [0, 10, 20])

    def test_normal_still_uses_existing_pacing(self):
        with patch.dict(engine.os.environ, {'GROQ_API_KEY': ''}):
            edl = engine.orchestrate_promov3_step4_edl(
                [dict(shot, duration=5) for shot in shots()], SCRIPT, voice_duration=10, hook_duration=4)
        self.assertGreater(sum(cut['role'] == 'hook' for cut in edl['timeline']), 1)
        self.assertTrue(all(cut['duration'] <= 3 for cut in edl['timeline']))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_synthetic_render_resolution_duration_audio_and_sentence_clips(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            root = Path(folder)
            video, body_video, voice = root / 'source.mp4', root / 'body.mov', root / 'voice.wav'
            subprocess.run([engine.FFMPEG_CMD, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                'color=c=red:s=320x180:r=30:d=4', '-c:v', 'libx264', str(video)], check=True)
            subprocess.run([engine.FFMPEG_CMD, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                'color=c=blue:s=320x180:r=30:d=2', '-c:v', 'libx264',
                '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', str(body_video)], check=True)
            subprocess.run([engine.FFMPEG_CMD, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                'sine=frequency=440:duration=6', str(voice)], check=True)
            edl = {'script_mode': 'caveman', 'total_duration': 6, 'sentence_1_end_s': 4,
                'script_text': 'New game explained by caveman. Me see blue.',
                'timeline': [
                    {'shot_id': 1, 'duration': 4, 'source_in': 0, 'role': 'hook', 'video_path': str(video),
                     'transition_out': {'type': 'white_flash'}},
                    {'shot_id': 2, 'duration': 2, 'source_in': 0, 'role': 'gameplay', 'video_path': str(body_video),
                     'transition_out': {'type': 'cut'}},
                ]}
            with patch.object(engine, 'BASE_DIR', root):
                result = engine.render_promov3_final_video(edl, video, voice, None, root / 'output',
                    enable_steam_hook=False, gameplay_volume=0, session_id='fixture', temp_dir=root / 'render')
            rendered = Path(result['video_path'])
            self.assertEqual(result['cut_count'], 2)
            metadata = engine.probe_video(rendered)
            self.assertEqual((metadata['width'], metadata['height']), (1080, 1920))
            self.assertAlmostEqual(engine.get_media_duration(rendered), 6, delta=.15)
            self.assertTrue(engine.check_media_has_audio(rendered))
            for time_s, expected_channel in [(1, 0), (5, 2)]:
                rgb = subprocess.run([engine.FFMPEG_CMD, '-v', 'error', '-ss', str(time_s), '-i', str(rendered),
                    '-frames:v', '1', '-vf', 'scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                    check=True, capture_output=True).stdout
                self.assertGreater(rgb[expected_channel], 200)
            for clip in (root / 'render/clips').glob('cut_*.mp4'):
                streams = json.loads(subprocess.check_output([engine.FFPROBE_CMD, '-v', 'error',
                    '-show_entries', 'stream=codec_type,color_space,color_primaries,color_transfer',
                    '-of', 'json', str(clip)], text=True))['streams']
                visual = next(s for s in streams if s['codec_type'] == 'video')
                self.assertEqual(visual['color_space'], 'bt709')
                self.assertEqual(visual['color_primaries'], 'bt709')
                self.assertEqual(visual['color_transfer'], 'bt709')
                self.assertFalse(any(s['codec_type'] == 'data' for s in streams))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_composition_protects_legacy_clips_with_mixed_colour_tags(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            root = Path(folder)
            sources = [root / 'red.mp4', root / 'blue.mp4', root / 'green.mp4']
            for index, (source, color) in enumerate(zip(sources, ['red', 'blue', 'lime'])):
                tags = ['-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709'] if index == 1 else []
                subprocess.run([engine.FFMPEG_CMD, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    f'color=c={color}:s=320x180:r=30:d=2', '-c:v', 'libx264', *tags, str(source)], check=True)
            voice = root / 'voice.wav'
            subprocess.run([engine.FFMPEG_CMD, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                'sine=frequency=440:duration=6', str(voice)], check=True)
            cuts = [{'shot_id': i + 1, 'duration': 2, 'source_in': 0, 'video_path': str(source),
                     'role': 'hook' if i == 0 else 'gameplay',
                     'transition_out': {'type': 'white_flash' if i == 0 else 'cut'}}
                    for i, source in enumerate(sources)]
            edl = {'script_mode': 'caveman', 'timeline': cuts, 'total_duration': 6, 'sentence_1_end_s': 2}
            def use_legacy_clip(source, **kwargs):
                shutil.copy2(source, kwargs['out_path'])
            with patch.object(engine, 'BASE_DIR', root), patch.object(engine, '_extract_subclip', side_effect=use_legacy_clip):
                result = engine.render_promov3_final_video(edl, sources[0], voice, None, root / 'output',
                    enable_steam_hook=False, gameplay_volume=0, session_id='fixture', temp_dir=root / 'render')
            for time_s, channel in [(1, 0), (3, 2), (5, 1)]:
                rgb = subprocess.run([engine.FFMPEG_CMD, '-v', 'error', '-ss', str(time_s), '-i', result['video_path'],
                    '-frames:v', '1', '-vf', 'scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                    check=True, capture_output=True).stdout
                self.assertGreater(rgb[channel], 200)
            self.assertAlmostEqual(engine.get_media_duration(root / 'render/silent_video.mp4'), 6, delta=.1)


if __name__ == '__main__':
    unittest.main()
