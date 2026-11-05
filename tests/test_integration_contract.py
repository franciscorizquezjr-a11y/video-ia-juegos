"""Integration checks for the shared API contract after merging the parallel packages.

The server passes new options through ``_filter_supported_kwargs`` (dropped with a
warning when a target function lacks them). These tests make sure nothing is dropped
now that every layer is merged, that request models keep the documented defaults,
and that the frontend sends the same field names. No external APIs are called.
"""
import inspect
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import audio_aligner
import natural_narration
import promo_v3_engine
import script_retention
import utils
import web_server as server

ROOT = Path(__file__).resolve().parents[1]


class FunctionKwargsContractTests(unittest.TestCase):
    CONTRACT = {
        promo_v3_engine.render_promov3_final_video: {
            "audio_normalize": True, "audio_ducking": True, "output_fps": 30, "enable_steam_hook": True,
        },
        promo_v3_engine.orchestrate_promov3_step4_edl: {"cut_start_mode": "shot_start"},
        audio_aligner.synthesize_elevenlabs_with_timestamps: {"similarity_boost": 0.75, "stability": 0.5},
        natural_narration.synthesize: {"speed": 1.0, "stability": 0.5, "similarity_boost": 0.75},
        utils.generate_v2_script: {"hook_type": "steam_new_game", "opening_text": "", "speech_speed": 1.12},
        script_retention.build_retention_script_prompt: {
            "hook_type": "steam_new_game", "opening_text": "", "speech_speed": 1.12,
        },
    }

    def test_contract_kwargs_and_defaults(self):
        for fn, expected in self.CONTRACT.items():
            params = inspect.signature(fn).parameters
            for name, default in expected.items():
                with self.subTest(fn=fn.__name__, kwarg=name):
                    self.assertIn(name, params)
                    self.assertEqual(params[name].default, default)

    def test_server_never_drops_contract_options(self):
        calls = [
            (server.generate_v2_script, {"opening_text": "", "speech_speed": 1.1}),
            (server.modify_v2_script_length, {"hook_type": None, "opening_text": "", "speech_speed": 1.1,
                                              "script_mode": "normal", "game_genre": ""}),
            (server.synthesize_elevenlabs_with_timestamps, {"similarity_boost": 0.75}),
            (server.orchestrate_promov3_step4_edl, {"cut_start_mode": "best_moment"}),
            (server.render_promov3_final_video, {"audio_normalize": True, "audio_ducking": True, "output_fps": 60}),
            (server.remix_promov3_audio_sfx, {"audio_normalize": True, "audio_ducking": False}),
        ]
        for fn, options in calls:
            with self.subTest(fn=getattr(fn, "__name__", fn)):
                accepted, dropped = server._filter_supported_kwargs(fn, options)
                self.assertEqual(dropped, [])
                self.assertEqual(accepted, options)


class RequestModelDefaultsTests(unittest.TestCase):
    def assertDefaults(self, model, expected):
        fields = model.model_fields if hasattr(model, "model_fields") else model.__fields__
        for name, default in expected.items():
            with self.subTest(model=model.__name__, field=name):
                self.assertIn(name, fields)
                self.assertEqual(fields[name].default, default)

    def test_request_model_defaults(self):
        self.assertDefaults(server.V2ScriptGenerateRequest, {"hook_type": "steam_new_game", "opening_text": "", "speech_speed": None})
        self.assertDefaults(server.V2ScriptModifyRequest, {"hook_type": None, "opening_text": "", "speech_speed": None})
        self.assertDefaults(server.PromoV3Step3SaveRequest, {"hook_type": "steam_new_game", "opening_text": ""})
        self.assertDefaults(server.PromoProcessRequest, {"hook_type": "steam_new_game", "opening_text": ""})
        self.assertDefaults(server.PromoV3SynthesizeVoiceRequest, {"speed": 1.1, "stability": 0.5, "similarity_boost": 0.75})
        self.assertDefaults(server.PromoV3Step4OrchestrateRequest, {"cut_start_mode": "shot_start"})
        self.assertDefaults(server.PromoV3Step5RenderRequest, {
            "enable_steam_hook": True, "audio_normalize": True, "audio_ducking": True, "output_fps": 30,
        })


class FrontendFieldNamesTests(unittest.TestCase):
    def test_frontend_sends_contract_field_names(self):
        source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        for key in ("hook_type", "opening_text", "speech_speed", "similarity_boost", "cut_start_mode",
                    "audio_normalize", "audio_ducking", "output_fps", "enable_steam_hook"):
            with self.subTest(key=key):
                self.assertRegex(source, rf"\b{key}\s*:")
        for value in ("steam_new_game", "so_in_this_new_game", "direct", "custom", "shot_start", "best_moment"):
            with self.subTest(value=value):
                self.assertIn(f"'{value}'", source)
        self.assertIn("groq_api_key_set", source)
        self.assertNotRegex(source, r"api_key=\$\{")


class ScriptModifyOpeningTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app, base_url="http://127.0.0.1:8004")

    def test_modify_endpoint_passes_opening_options(self):
        captured = {}

        def fake_modify(**kwargs):
            captured.update(kwargs)
            return {"script": "Campaign line. You sail.", "word_count": 4, "estimated_duration": 2.0,
                    "target_duration": 30.0, "retention_data": {}}

        with patch.object(server, "modify_v2_script_length", side_effect=fake_modify) as mocked:
            mocked.__signature__ = inspect.signature(utils.modify_v2_script_length)
            res = self.client.post("/api/v2/script/modify", json={
                "script_text": "You sail.", "action": "shorter", "game_name": "Boat",
                "hook_type": "custom", "opening_text": "Campaign line.", "speech_speed": 1.05,
                "groq_api_key": "gsk_test",
            })
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(captured["hook_type"], "custom")
        self.assertEqual(captured["opening_text"], "Campaign line.")
        self.assertEqual(captured["speech_speed"], 1.05)

    def test_modify_without_opening_keeps_current_request(self):
        captured = {}

        def fake_modify(**kwargs):
            captured.update(kwargs)
            return {"script": "x", "word_count": 1, "estimated_duration": 0.4, "target_duration": 30.0, "retention_data": {}}

        with patch.object(server, "modify_v2_script_length", side_effect=fake_modify) as mocked:
            mocked.__signature__ = inspect.signature(utils.modify_v2_script_length)
            res = self.client.post("/api/v2/script/modify", json={
                "script_text": "You sail.", "action": "shorter", "game_name": "Boat", "groq_api_key": "gsk_test",
            })
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIsNone(captured["hook_type"])
        self.assertEqual(captured["speech_speed"], 1.1)

    def test_regenerate_forwards_opening_to_generator(self):
        with patch.object(utils, "generate_v2_script", return_value={"script": "ok"}) as gen:
            utils.modify_v2_script_length(
                current_script="You sail.", action="regenerate", game_name="Boat", groq_api_key="gsk_test",
                hook_type="direct", speech_speed=1.0,
            )
        kwargs = gen.call_args.kwargs
        self.assertEqual(kwargs["hook_type"], "direct")
        self.assertEqual(kwargs["opening_text"], "")
        self.assertEqual(kwargs["speech_speed"], 1.0)

    def test_regenerate_without_opening_is_unchanged(self):
        with patch.object(utils, "generate_v2_script", return_value={"script": "ok"}) as gen:
            utils.modify_v2_script_length(current_script="You sail.", action="regenerate", game_name="Boat",
                                          groq_api_key="gsk_test")
        self.assertEqual(set(gen.call_args.kwargs),
                         {"game_name", "target_duration", "tone", "briefing_text", "groq_api_key", "gemini_api_key"})

    def _fake_groq(self, text, sink):
        def fake_post(url, json=None, headers=None, timeout=None):
            sink.append(json["messages"][-1]["content"])
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"choices": [{"message": {"content": text}}]}
            return resp
        return fake_post

    def test_custom_opening_is_kept_when_modifying(self):
        prompts = []
        with patch.object(utils._api_usage, "usage_post", side_effect=self._fake_groq("You build a raft and the sea fights back.", prompts)):
            res = utils.modify_v2_script_length(
                current_script="Campaign line. You build a raft.", action="shorter", game_name="Boat",
                groq_api_key="gsk_test", hook_type="custom", opening_text="Campaign line.",
            )
        self.assertTrue(res["script"].startswith("Campaign line."), res["script"])
        self.assertIn('MUST begin with exactly these words: "Campaign line."', prompts[0])

    def test_steam_default_modify_prompt_is_unchanged(self):
        prompts_default, prompts_steam = [], []
        with patch.object(utils._api_usage, "usage_post", side_effect=self._fake_groq("Short script.", prompts_default)):
            utils.modify_v2_script_length(current_script="A b c.", action="shorter", game_name="Boat", groq_api_key="k")
        with patch.object(utils._api_usage, "usage_post", side_effect=self._fake_groq("Short script.", prompts_steam)):
            res = utils.modify_v2_script_length(current_script="A b c.", action="shorter", game_name="Boat",
                                                groq_api_key="k", hook_type="steam_new_game")
        self.assertEqual(prompts_default[0], prompts_steam[0])
        self.assertNotIn("OPENING RULE", prompts_steam[0])
        self.assertEqual(res["script"], "Short script.")


class ElevenLabsVoicesPostTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app, base_url="http://127.0.0.1:8004")

    def _voices_response(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"voices": [{"voice_id": "v1", "name": "George", "category": "premade"}]}
        return resp

    def test_post_uses_typed_key_from_body(self):
        with patch("requests.get", return_value=self._voices_response()) as get:
            res = self.client.post("/api/elevenlabs/voices", json={"api_key": "sk_typed_key"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["total_count"], 1)
        self.assertEqual(get.call_args.kwargs["headers"]["xi-api-key"], "sk_typed_key")
        self.assertNotIn("sk_typed_key", get.call_args.args[0])

    def test_post_with_masked_key_falls_back_to_server_key(self):
        with patch("requests.get", return_value=self._voices_response()) as get, \
                patch.dict("os.environ", {"ELEVENLABS_API_KEY": "server-side-key"}):
            res = self.client.post("/api/elevenlabs/voices", json={"api_key": "••••-key"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn("•", get.call_args.kwargs["headers"]["xi-api-key"])

    def test_post_with_foreign_origin_is_rejected(self):
        with patch("requests.get") as get:
            res = self.client.post("/api/elevenlabs/voices", json={"api_key": "x"},
                                   headers={"Origin": "https://evil.example"})
        self.assertEqual(res.status_code, 403)
        get.assert_not_called()


class SteamImpactGetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.client = TestClient(server.app, base_url="http://127.0.0.1:8004")

    def test_steam_impact_get_does_not_persist_demo_data(self):
        games_file = self.dir / "gamedev_games.json"
        games_file.write_text(json.dumps([{"id": "g1", "name": "Boat"}]), encoding="utf-8")
        before = games_file.read_bytes()
        with patch.object(server, "GAMEDEV_GAMES_FILE", games_file):
            res = self.client.get("/api/gamedev/games/g1/steam-impact")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertTrue(data["is_simulated"])
        self.assertTrue(data["simulated_insights"])
        self.assertEqual(games_file.read_bytes(), before)


class OpeningConsistencyTests(unittest.TestCase):
    def test_custom_without_text_uses_the_same_fallback_everywhere(self):
        self.assertEqual(script_retention.resolve_hook_type("custom", ""), "steam_new_game")
        self.assertEqual(server._effective_hook_type("custom", ""), "steam_new_game")
        self.assertTrue(server._uses_steam_splash(server._effective_hook_type("custom", "  ")))
        self.assertFalse(server._uses_steam_splash(server._effective_hook_type("custom", "Campaign line.")))
        self.assertFalse(server._uses_steam_splash(server._effective_hook_type("direct")))


class RevealFontTests(unittest.TestCase):
    def test_reveal_overlay_has_no_hardcoded_windows_font_dir(self):
        source = (ROOT / "utils.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r'font_dir\s*=\s*"C:', source))


if __name__ == "__main__":
    unittest.main()
