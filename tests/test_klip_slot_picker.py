"""Manual slot replacement against isolated session files, without providers."""
import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail


class SlotPickerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'tests')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.session = root / 'temp' / 'v3_cache' / 'fixture'
        self.session.mkdir(parents=True)
        names = {'_promov3_slot_candidates', 'promov3_slot_candidates_endpoint', 'promov3_replace_slot_endpoint'}
        tree = ast.parse((ROOT / 'web_server.py').read_text(encoding='utf-8'))
        nodes = [n for n in tree.body if getattr(n, 'name', '') in names]
        for node in nodes:
            node.decorator_list = []
        self.ns = dict(BASE_DIR=root, Path=Path, json=json, time=time,
                       HTTPException=HTTPException, PromoV3SlotShotRequest=SimpleNamespace)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'web_server.py', 'exec'), self.ns)
        shots = [dict(shot_id=1, start=0, end=5, suggested_role='gameplay'),
                 dict(shot_id=2, start=10, end=15, suggested_role='discard', description='Rejected', video_path='other.mov', preview_url='/clip.mp4'),
                 dict(shot_id=3, start=20, end=21, format_score=20),
                 dict(shot_id=4, start=30, end=35, is_splash=True),
                 dict(shot_id=5, start=40, end=45, suggested_role='gameplay')]
        self.write('step2_evaluated_shots.json', dict(shots=shots))
        self.edl = dict(script_mode='caveman', timeline=[
            dict(shot_id=1, duration=3, source_in=0, source_out=3, start_time_in_video=0,
                 end_time_in_video=3, sentence_id=1, script_segment='Me build.', role='hook', transition_out={'type': 'white_flash'}),
            dict(shot_id=4, duration=2, source_in=30, start_time_in_video=3, end_time_in_video=5)])
        self.req = SimpleNamespace(session_id='fixture', video_paths=None, video_path=None,
                                   edl=self.edl, slot_index=0, shot_id=2)

    def write(self, filename, value):
        (self.session / filename).write_text(json.dumps(value), encoding='utf-8')

    def test_candidates_show_all_discards_with_duplicate_and_duration_reasons(self):
        result = self.ns['promov3_slot_candidates_endpoint'](self.req)
        shots = {s['shot_id']: s for s in result['shots']}
        self.assertEqual(set(shots), {2, 3, 4, 5})
        self.assertEqual(shots[5]['rejection_reason'], 'No seleccionado por el Director IA')
        self.assertEqual(shots[2]['unavailable_reason'], '')
        self.assertIn('corto', shots[3]['unavailable_reason'])
        self.assertIn('otro slot', shots[4]['unavailable_reason'])

    def test_replacement_preserves_timeline_and_persists_approval_and_invalidates_render(self):
        before = copy.deepcopy(self.edl)
        self.write('step5_result.json', {'output': 'old.mp4'})
        result = self.ns['promov3_replace_slot_endpoint'](self.req)['edl']
        cut = result['timeline'][0]
        self.assertEqual(cut['shot_id'], 2)
        self.assertEqual(cut['source_in'], 10)
        self.assertEqual(cut['source_out'], 13)
        self.assertEqual(cut['video_path'], 'other.mov')
        self.assertEqual(cut['preview_9_16_url'], '/clip.mp4')
        for key in ['duration', 'sentence_id', 'script_segment', 'start_time_in_video', 'end_time_in_video', 'role', 'transition_out']:
            self.assertEqual(cut[key], before['timeline'][0][key])
        self.assertEqual(result['timeline'][1], before['timeline'][1])
        self.assertEqual(self.edl, before)
        self.assertEqual(json.loads((self.session / 'step4_edl.json').read_text()), result)
        self.assertEqual(json.loads((self.session / 'step2_evaluated_shots.json').read_text())['director_approved_shot_ids'], [2])
        self.assertFalse((self.session / 'step5_result.json').exists())

    def test_replacement_follows_best_moment_cut_start_mode(self):
        shots = json.loads((self.session / 'step2_evaluated_shots.json').read_text())['shots']
        shots[1]['peak_offset_s'] = 2.0  # shot 2: 10-15 s, peak 2 s in
        self.write('step2_evaluated_shots.json', dict(shots=shots))
        self.edl['cut_start_mode'] = 'best_moment'
        self.edl['timeline'][0].update(source_offset_s=0.7, source_offset_reason='skip_black_opening')
        cut = self.ns['promov3_replace_slot_endpoint'](self.req)['edl']['timeline'][0]
        self.assertEqual(cut['source_offset_reason'], 'peak_hint')
        self.assertAlmostEqual(cut['source_offset_s'], 1.1, places=3)
        self.assertAlmostEqual(cut['source_in'], 11.1, places=3)
        self.assertAlmostEqual(cut['source_out'], 14.1, places=3)
        self.assertLessEqual(cut['source_out'], 15)

    def test_replacement_in_shot_start_mode_drops_the_previous_offset(self):
        self.edl['timeline'][0].update(source_offset_s=0.7, source_offset_reason='skip_black_opening')
        cut = self.ns['promov3_replace_slot_endpoint'](self.req)['edl']['timeline'][0]
        self.assertEqual(cut['source_in'], 10)
        self.assertNotIn('source_offset_s', cut)
        self.assertNotIn('source_offset_reason', cut)

    def test_invalid_choices_do_not_modify_session(self):
        for shot_id in [1, 3, 4, 999]:
            with self.subTest(shot_id=shot_id):
                self.req.shot_id = shot_id
                with self.assertRaises(HTTPException):
                    self.ns['promov3_replace_slot_endpoint'](self.req)
        self.assertFalse((self.session / 'step4_edl.json').exists())

    def test_bad_slot_and_path_are_rejected(self):
        self.req.slot_index = 20
        with self.assertRaises(HTTPException):
            self.ns['promov3_slot_candidates_endpoint'](self.req)
        self.req.slot_index = 0
        self.req.session_id = '../fixture'
        with self.assertRaises(HTTPException):
            self.ns['promov3_slot_candidates_endpoint'](self.req)


if __name__ == '__main__':
    unittest.main()
