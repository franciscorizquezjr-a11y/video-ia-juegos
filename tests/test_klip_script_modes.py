"""Klip script/voice contracts, using fake providers and isolated session files."""
import ast
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Literal, Optional
import unittest
from unittest.mock import Mock, patch

from pydantic import BaseModel, ValidationError
from script_retention import build_retention_script_prompt
import utils

ROOT = Path(__file__).resolve().parents[1]


def endpoint_namespace(root):
    # Load just these production contracts/functions; importing the entire server
    # starts unrelated media dependencies. All provider calls are replaced below.
    names = {
        'V2ScriptGenerateRequest', 'PromoV3Step3SaveRequest',
        'PromoV3SynthesizeVoiceRequest', 'save_promov3_step3_endpoint',
        'PromoV3Step4OrchestrateRequest',
        'synthesize_promov3_voice_endpoint',
    }
    tree = ast.parse((ROOT / 'web_server.py').read_text(encoding='utf-8'))
    selected = [node for node in tree.body if getattr(node, 'name', '') in names]
    for node in selected:
        node.decorator_list = []
    ns = dict(BaseModel=BaseModel, Optional=Optional, List=List, Dict=Dict,
              Any=Any, Literal=Literal, Path=Path, BASE_DIR=root, json=json,
              re=re, os=os, time=time, logger=logging.getLogger(__name__),
              is_script_text_equivalent=lambda a, b: a == b)
    exec(compile(ast.Module(body=selected, type_ignores=[]), 'web_server.py', 'exec'), ns)
    return ns


class KlipScriptModeTests(unittest.TestCase):
    def test_normal_keeps_existing_profile_and_caveman_keeps_retention(self):
        normal = build_retention_script_prompt('Game')
        caveman = build_retention_script_prompt('Game', script_mode='caveman', game_genre='survival')
        self.assertIn('MODULAR HOOK CONSTRUCTION', normal)
        self.assertIn('BUDDY HYPE', normal)
        self.assertNotIn('MODULAR HOOK CONSTRUCTION', caveman)
        self.assertNotIn('MANDATORY LINGUISTIC PROFILE & VOCABULARY RULES', caveman)
        self.assertIn('New [game genre] game explained by caveman.', caveman)
        self.assertIn('NO conjunctions inside sentences', caveman)
        self.assertIn('Genre supplied by creator: survival', caveman)
        for prompt in (normal, caveman):
            self.assertIn('20 CORE RETENTION PRINCIPLES', prompt)
            self.assertIn('SECOND_ESCALATION', prompt)
            self.assertIn('resolves', prompt)

    def test_generation_passes_caveman_profile_to_provider(self):
        response = Mock(status_code=200)
        response.json.return_value = {'choices': [{'message': {'content': json.dumps({
            'hook': {'text': 'New survival game explained by caveman.',
                     'first_words': {'text': 'New'}, 'game_genre': {'text': 'survival game'},
                     'first_punch': {'text': 'explained by caveman'}, 'second_punch': None},
            'sentences': [{'text': 'New survival game explained by caveman.', 'block': 'HOOK'},
                          {'text': 'Me build boat.', 'block': 'SETUP'},
                          {'text': 'But sea monster big.', 'block': 'FIRST_ESCALATION'}],
        })}}]}
        with patch.object(utils, 'search_steam_game', return_value='Build boats. Survive monsters.'), \
             patch.object(utils, 'load_viral_script_examples', return_value=''), \
             patch('requests.post', return_value=response) as post:
            result = utils.generate_v2_script('Boat', groq_api_key='fake',
                                              script_mode='caveman', game_genre='survival')
        self.assertTrue(result['script'].startswith('New survival game explained by caveman.'))
        self.assertEqual(result['retention_data']['sentences'][2]['text'], 'But sea monster big.')
        messages = post.call_args.kwargs['json']['messages']
        self.assertIn('simple caveman English', messages[0]['content'])
        self.assertIn('NO conjunctions inside sentences', messages[1]['content'])

    def test_voice_selection_persists_and_resynthesis_invalidates_downstream(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            root = Path(folder)
            ns = endpoint_namespace(root)
            cfg = SimpleNamespace(elevenlabs_api_key='fake', elevenlabs_voice_id='configured')
            ns['AppConfig'] = SimpleNamespace(from_env=lambda: cfg)
            ns['get_media_duration'] = lambda path: 3.0
            tts = Mock(return_value=(None, [], {}))
            ns['synthesize_elevenlabs_with_timestamps'] = tts
            save_request = ns['PromoV3Step3SaveRequest'](
                session_id='fixture', game_name='Boat', script_mode='caveman',
                game_genre='survival', script_text='New survival game explained by caveman.',
                word_count=7, estimated_duration=3)
            ns['save_promov3_step3_endpoint'](save_request)
            session = root / 'temp/v3_cache/fixture'
            for voice in ('WrPknjKhmIXkCONEtG3j', None):
                for name in ('step4_edl.json', 'step5_result.json'):
                    (session / name).write_text('{}')
                result = ns['synthesize_promov3_voice_endpoint'](
                    ns['PromoV3SynthesizeVoiceRequest'](session_id='fixture',
                        script_text=save_request.script_text, voice_id=voice))
                expected = voice or 'configured'
                self.assertEqual(tts.call_args.kwargs['voice_id'], expected)
                self.assertEqual(result['voice_id'], expected)
                saved = ns['save_promov3_step3_endpoint'](save_request)['data']
                self.assertEqual(saved['voice_id'], expected)
                self.assertEqual(saved['script_mode'], 'caveman')
                self.assertEqual(saved['game_genre'], 'survival')
                self.assertFalse((session / 'step4_edl.json').exists())
                self.assertFalse((session / 'step5_result.json').exists())

    def test_legacy_requests_default_to_normal(self):
        ns = endpoint_namespace(ROOT)
        self.assertEqual(ns['V2ScriptGenerateRequest'](game_name='Boat').script_mode, 'normal')
        with self.assertRaises(ValidationError):
            ns['V2ScriptGenerateRequest'](game_name='Boat', script_mode='unknown')
        self.assertIsNone(ns['PromoV3Step4OrchestrateRequest']().script_mode)
        self.assertEqual(ns['PromoV3Step4OrchestrateRequest'](script_mode='caveman').script_mode, 'caveman')


if __name__ == '__main__':
    unittest.main()
