"""web_server plumbing of the shared API contract (opening/hook types, voice
settings, cut start mode, render options and warnings). Engines/providers are
replaced by fakes; nothing calls Gemini, Groq, ElevenLabs or FFmpeg."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import production_logo_policy
import web_server as server


def wait_job(job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = server.ACTIVE_JOBS.get(job_id)
        if job and job.status != "running":
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


class HelperTests(unittest.TestCase):
    def test_spoken_opening_per_hook_type(self):
        body = "You build a boat and the sea fights back."
        self.assertTrue(server._apply_spoken_opening(body, "steam_new_game").lower().startswith("here's a new steam game"))
        self.assertTrue(server._apply_spoken_opening(body, "so_in_this_new_game").startswith("So in this new game, you build"))
        self.assertEqual(server._apply_spoken_opening(body, "direct"), body)
        custom = server._apply_spoken_opening(body, "custom", "Trapster found this gem.")
        self.assertEqual(custom, "Trapster found this gem. " + body)
        # Already present (case/punctuation-insensitive): not duplicated.
        self.assertEqual(server._apply_spoken_opening("trapster found this gem! " + body, "custom", "Trapster found this gem."),
                         "trapster found this gem! " + body)
        # custom without text behaves like direct
        self.assertEqual(server._apply_spoken_opening(body, "custom", ""), body)

    def test_splash_only_for_steam_style_openers(self):
        self.assertTrue(server._uses_steam_splash("steam_new_game"))
        self.assertTrue(server._uses_steam_splash("so_in_this_new_game"))
        self.assertTrue(server._uses_steam_splash(None))
        self.assertFalse(server._uses_steam_splash("direct"))
        self.assertFalse(server._uses_steam_splash("custom"))

    def test_speech_speed_resolution(self):
        self.assertEqual(server._resolve_speech_speed(None), 1.1)
        self.assertEqual(server._resolve_speech_speed(None, 1.0), 1.0)
        self.assertEqual(server._resolve_speech_speed(1.05, 1.0), 1.05)
        self.assertEqual(server._resolve_speech_speed(3.0), 1.2)
        self.assertEqual(server._resolve_speech_speed(0.1), 0.7)

    def test_filter_supported_kwargs(self):
        def legacy(a, b=1):
            return a

        def modern(a, b=1, opening_text=""):
            return a

        def anything(a, **kw):
            return a

        self.assertEqual(server._filter_supported_kwargs(legacy, {"opening_text": "x"}), ({}, ["opening_text"]))
        self.assertEqual(server._filter_supported_kwargs(modern, {"opening_text": "x"}), ({"opening_text": "x"}, []))
        self.assertEqual(server._filter_supported_kwargs(anything, {"z": 1}), ({"z": 1}, []))

    def test_finalize_render_result_merges_warnings_and_keeps_keys(self):
        engine = {"video_url": "/v.mp4", "has_subtitles": False, "output_fps": 30,
                  "warnings": ["Subtítulos no quemados", "dup"]}
        out = server._finalize_render_result(engine, {"audio_normalize": True, "audio_ducking": False, "output_fps": 60},
                                             ["dup", "fps fallback"], ["audio_ducking"])
        self.assertEqual(out["video_url"], "/v.mp4")
        self.assertFalse(out["has_subtitles"])
        self.assertEqual(out["warnings"][:3], ["dup", "fps fallback", "Subtítulos no quemados"])
        self.assertTrue(any("audio_ducking" in w for w in out["warnings"]))
        self.assertEqual(out["output_fps"], 30)  # engine value wins (real fps after fallback)
        self.assertEqual(out["requested_output_fps"], 60)
        self.assertEqual(out["render_options"], {"audio_normalize": True, "audio_ducking": False, "output_fps": 30})
        self.assertEqual(server._finalize_render_result({}, {"output_fps": 30})["warnings"], [])

    def test_request_models_defaults_preserve_behaviour(self):
        self.assertEqual(server.V2ScriptGenerateRequest(game_name="G").hook_type, "steam_new_game")
        self.assertEqual(server.V2ScriptGenerateRequest(game_name="G").opening_text, "")
        self.assertIsNone(server.V2ScriptGenerateRequest(game_name="G").speech_speed)
        self.assertEqual(server.PromoProcessRequest(footage_paths=[], game_name="G").opening_text, "")
        self.assertEqual(server.PromoV3SynthesizeVoiceRequest(script_text="x").similarity_boost, 0.75)
        self.assertEqual(server.PromoV3Step4OrchestrateRequest().cut_start_mode, "shot_start")
        r5 = server.PromoV3Step5RenderRequest()
        self.assertEqual((r5.audio_normalize, r5.audio_ducking, r5.output_fps, r5.enable_steam_hook), (True, True, 30, True))
        self.assertEqual(server.PromoV3Step3SaveRequest(game_name="G", script_text="s", word_count=1,
                                                        estimated_duration=1).opening_text, "")


class EndpointPlumbingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cache = self.root / "temp" / "v3_cache"
        self.cache.mkdir(parents=True)
        p = patch.object(server, "V3_CACHE_DIR", self.cache)
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(server.app, base_url="http://127.0.0.1:8004")

    def session(self, name="sess_fixture"):
        d = self.cache / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ------------------------------------------------------------ script generation
    def test_v2_script_generation_passes_opening_and_speed(self):
        captured = {}

        def fake_generate(game_name, target_duration=30.0, tone="viral_hype", briefing_text="", hook_type="steam_new_game",
                          groq_api_key="", gemini_api_key=None, script_mode="normal", game_genre="",
                          opening_text="", speech_speed=1.12):
            captured.update(hook_type=hook_type, opening_text=opening_text, speech_speed=speech_speed)
            return {"script": f"{opening_text} body".strip(), "retention_data": None}

        with patch.object(server, "generate_v2_script", side_effect=fake_generate) as gen:
            gen.__signature__ = __import__("inspect").signature(fake_generate)
            res = self.client.post("/api/v2/script/generate", json={
                "game_name": "Boat", "hook_type": "custom", "opening_text": "Trapster found this.", "speech_speed": 1.05})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(captured, {"hook_type": "custom", "opening_text": "Trapster found this.", "speech_speed": 1.05})
            self.assertEqual(res.json()["speech_speed"], 1.05)

            res = self.client.post("/api/v2/script/generate", json={"game_name": "Boat"})
            self.assertEqual(captured["hook_type"], "steam_new_game")
            self.assertEqual(captured["speech_speed"], 1.1)  # default voice speed

            (self.session() / "step3_script.json").write_text(json.dumps({"speed": 1.0}), encoding="utf-8")
            res = self.client.post("/api/v2/script/generate", json={"game_name": "Boat", "session_id": "sess_fixture",
                                                                    "hook_type": "direct"})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(captured["speech_speed"], 1.0)  # the session's chosen voice speed
            self.assertEqual(captured["hook_type"], "direct")

    def test_v2_script_generation_with_legacy_generator_enforces_custom_opening(self):
        def legacy_generate(game_name, target_duration=30.0, tone="viral_hype", briefing_text="", hook_type="steam_new_game",
                            groq_api_key="", gemini_api_key=None, script_mode="normal", game_genre=""):
            return {"script": "A body without the opener.", "retention_data": None}

        with patch.object(server, "generate_v2_script", legacy_generate):
            res = self.client.post("/api/v2/script/generate", json={
                "game_name": "Boat", "hook_type": "custom", "opening_text": "Campaign line."})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["script"], "Campaign line. A body without the opener.")

    # ------------------------------------------------------------ step 3 voice
    def test_step3_synthesize_passes_voice_settings(self):
        captured = {}

        def fake_tts(text, output_path, api_key, voice_id, model_id="eleven_v4", speed=1.1, stability=0.5,
                     speed_hook=None, natural_delivery=True, similarity_boost=0.75):
            captured.update(speed=speed, stability=stability, similarity_boost=similarity_boost)
            Path(output_path).write_bytes(b"mp3")
            return str(output_path), [], {}

        self.session()
        with patch.object(server, "BASE_DIR", self.root), \
             patch.object(server, "synthesize_elevenlabs_with_timestamps", fake_tts), \
             patch.object(server, "get_media_duration", return_value=3.0):
            res = self.client.post("/api/promov3/step3/synthesize-voice", json={
                "session_id": "sess_fixture", "script_text": "Hello there. Second line.",
                "speed": 1.05, "stability": 0.4, "similarity_boost": 0.9})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(captured, {"speed": 1.05, "stability": 0.4, "similarity_boost": 0.9})
        voice = json.loads((self.cache / "sess_fixture" / "step3_voice.json").read_text(encoding="utf-8"))
        self.assertEqual(voice["similarity_boost"], 0.9)
        script = json.loads((self.cache / "sess_fixture" / "step3_script.json").read_text(encoding="utf-8"))
        self.assertEqual((script["speed"], script["stability"], script["similarity_boost"]), (1.05, 0.4, 0.9))

    def test_step3_synthesize_with_legacy_voice_engine(self):
        def legacy_tts(text, output_path, api_key, voice_id, model_id="eleven_v4", speed=1.1, stability=0.5,
                       speed_hook=None, natural_delivery=True):
            Path(output_path).write_bytes(b"mp3")
            return str(output_path), [], {}

        self.session()
        with patch.object(server, "BASE_DIR", self.root), \
             patch.object(server, "synthesize_elevenlabs_with_timestamps", legacy_tts), \
             patch.object(server, "get_media_duration", return_value=3.0):
            res = self.client.post("/api/promov3/step3/synthesize-voice", json={
                "session_id": "sess_fixture", "script_text": "Hello there.", "similarity_boost": 0.9})
        self.assertEqual(res.status_code, 200, res.text)

    def test_step3_save_persists_opening_text(self):
        with patch.object(server, "BASE_DIR", self.root):
            res = self.client.post("/api/promov3/step3/save", json={
                "session_id": "sess_fixture", "game_name": "Boat", "hook_type": "custom",
                "opening_text": "Campaign line.", "script_text": "Campaign line. Body.", "word_count": 3,
                "estimated_duration": 2})
        self.assertEqual(res.status_code, 200, res.text)
        data = json.loads((self.cache / "sess_fixture" / "step3_script.json").read_text(encoding="utf-8"))
        self.assertEqual((data["hook_type"], data["opening_text"]), ("custom", "Campaign line."))

    # ------------------------------------------------------------ step 4
    def _prepare_step4_session(self):
        d = self.session()
        (d / "step2_evaluated_shots.json").write_text(json.dumps({"shots": [
            {"shot_id": 1, "start": 0, "end": 4, "suggested_role": "gameplay"}]}), encoding="utf-8")
        (d / "step3_script.json").write_text(json.dumps({"script_text": "Hello world.", "game_name": "Boat"}), encoding="utf-8")
        (d / "step3_voice.json").write_text(json.dumps({"script_text": "Hello world.", "voice_duration": 4.0,
                                                         "words": []}), encoding="utf-8")
        (d / "voiceover.mp3").write_bytes(b"mp3")
        return d

    def test_step4_passes_cut_start_mode(self):
        captured = {}

        def fake_orchestrate(step2_shots, script_text, target_duration=30.0, voice_duration=None, hook_duration=None,
                             game_name="", game_genre="", groq_api_key=None, gemini_client=None,
                             gemini_candidate_models=None, allow_secondary_transition=True, alignment_data=None,
                             banned_shot_ids=None, video_format="full_9_16", approved_shot_ids=None,
                             script_mode="normal", cut_start_mode="shot_start"):
            captured["cut_start_mode"] = cut_start_mode
            return {"timeline": [], "script_mode": script_mode}

        d = self._prepare_step4_session()
        with patch.object(server, "orchestrate_promov3_step4_edl", fake_orchestrate):
            res = self.client.post("/api/promov3/step4/orchestrate", json={"session_id": "sess_fixture",
                                                                            "cut_start_mode": "best_moment"})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(captured["cut_start_mode"], "best_moment")
            self.assertEqual(res.json()["edl"]["cut_start_mode"], "best_moment")
            saved = json.loads((d / "step4_edl.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["cut_start_mode"], "best_moment")

            res = self.client.post("/api/promov3/step4/orchestrate", json={"session_id": "sess_fixture",
                                                                            "cut_start_mode": "weird"})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(captured["cut_start_mode"], "shot_start")
            res = self.client.post("/api/promov3/step4/orchestrate", json={"session_id": "sess_fixture"})
            self.assertEqual(captured["cut_start_mode"], "shot_start")

    # ------------------------------------------------------------ step 5
    def _prepare_step5_session(self):
        d = self.session()
        voice = d / "voiceover.mp3"
        voice.write_bytes(b"mp3")
        (d / "step1_shots.json").write_text(json.dumps({"video_info": {"path": ""}, "game_name": "Boat"}), encoding="utf-8")
        (d / "step3_script.json").write_text(json.dumps({"script_text": "Hello world."}), encoding="utf-8")
        (d / "step3_voice.json").write_text(json.dumps({"script_text": "Hello world.", "audio_path": str(voice),
                                                         "words": []}), encoding="utf-8")
        return d

    def test_step5_passes_render_options_and_propagates_warnings(self):
        captured = {}

        def fake_render(**kwargs):
            captured.update(kwargs)
            return {"status": "completed", "video_url": "/media/output/x.mp4", "has_subtitles": False,
                    "warnings": ["No se pudieron quemar los subtítulos."]}

        d = self._prepare_step5_session()
        with patch.object(production_logo_policy, "require_game_logo", return_value=None), \
             patch.object(server, "resolve_promov3_video_path", return_value=None), \
             patch.object(server, "render_promov3_final_video", side_effect=fake_render):
            res = self.client.post("/api/promov3/step5/render", json={
                "session_id": "sess_fixture", "game_name": "Boat", "video_format": "full_9_16",
                "edl": {"timeline": [{"video_path": "missing.mp4", "duration": 2}], "script_text": "Hello world."},
                "audio_ducking": False, "output_fps": 24})
            self.assertEqual(res.status_code, 200, res.text)
            body = res.json()
            self.assertEqual(len(body["warnings"]), 1)
            self.assertIn("24", body["warnings"][0])
            job = wait_job(body["job_id"])
        self.assertEqual(job.status, "completed", job.error)
        self.assertEqual((captured["audio_normalize"], captured["audio_ducking"], captured["output_fps"]), (True, False, 30))
        self.assertEqual(captured["enable_steam_hook"], True)
        warnings = job.result["warnings"]
        self.assertTrue(any("24" in w for w in warnings))
        self.assertIn("No se pudieron quemar los subtítulos.", warnings)
        self.assertFalse(job.result["has_subtitles"])
        saved = json.loads((d / "step5_result.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["audio_normalize"], saved["audio_ducking"], saved["output_fps"]), (True, False, 30))
        self.assertEqual(saved["warnings"], warnings)
        status = self.client.get(f"/api/jobs/{body['job_id']}").json()
        self.assertEqual(status["result"]["warnings"], warnings)
        server.ACTIVE_JOBS.pop(body["job_id"], None)

    def test_step5_defaults_keep_current_behaviour(self):
        captured = {}

        def fake_render(**kwargs):
            captured.update(kwargs)
            return {"status": "completed", "video_url": "/media/output/x.mp4"}

        self._prepare_step5_session()
        with patch.object(production_logo_policy, "require_game_logo", return_value=None), \
             patch.object(server, "resolve_promov3_video_path", return_value=None), \
             patch.object(server, "render_promov3_final_video", side_effect=fake_render):
            res = self.client.post("/api/promov3/step5/render", json={
                "session_id": "sess_fixture", "game_name": "Boat", "video_format": "full_9_16",
                "edl": {"timeline": [{"video_path": "missing.mp4", "duration": 2}], "script_text": "Hello world."}})
            job = wait_job(res.json()["job_id"])
        self.assertEqual(job.status, "completed", job.error)
        self.assertEqual((captured["audio_normalize"], captured["audio_ducking"], captured["output_fps"]), (True, True, 30))
        self.assertEqual(job.result["warnings"], [])
        server.ACTIVE_JOBS.pop(res.json()["job_id"], None)

    def test_remix_reuses_saved_audio_options(self):
        captured = {}

        def fake_remix(session_dir, sfx_tracks, gameplay_volume=0.15, audio_normalize=True, audio_ducking=True):
            captured.update(audio_normalize=audio_normalize, audio_ducking=audio_ducking)
            return {"status": "completed"}

        d = self.session()
        (d / "step5_result.json").write_text(json.dumps({"audio_normalize": False, "audio_ducking": False,
                                                          "warnings": ["w"]}), encoding="utf-8")
        with patch.object(server, "remix_promov3_audio_sfx", fake_remix):
            res = self.client.post("/api/promov3/step5/remix_sfx", json={"session_id": "sess_fixture", "sfx_tracks": []})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(captured, {"audio_normalize": False, "audio_ducking": False})
            res = self.client.post("/api/promov3/step5/remix_sfx", json={"session_id": "sess_fixture", "sfx_tracks": [],
                                                                         "audio_ducking": True})
            self.assertEqual(captured["audio_ducking"], True)


class V2PromoPipelineOpeningTests(unittest.TestCase):
    """The separate 'Here's a new Steam game.' synthesis and the NEW STEAM GAME splash
    only apply to Steam-style openers; direct/custom keep captions from second 0."""

    def run_pipeline(self, hook_type, opening_text=""):
        calls = {"tts_text": None, "hook_audio": 0, "overlay": 0, "skip_steam_hook": None}

        def fake_tts(text, output_path, api_key, voice_id, model_id="eleven_v4", **kw):
            calls["tts_text"] = text
            words = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate(text.split())]
            return str(output_path), words, {}

        def fake_hook_audio(**kw):
            calls["hook_audio"] += 1
            return str(kw.get("output_path")), 1.5

        def fake_overlay(**kw):
            calls["overlay"] += 1
            Path(kw["output_mov_path"]).write_bytes(b"mov")

        def fake_ass(**kw):
            calls["skip_steam_hook"] = kw.get("skip_steam_hook")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            footage = root / "clip.mp4"
            footage.write_bytes(b"video")
            client = TestClient(server.app, base_url="http://127.0.0.1:8004")
            with patch.object(server, "BASE_DIR", root), \
                 patch.object(server, "synthesize_elevenlabs_with_timestamps", fake_tts), \
                 patch.object(server, "generate_elevenlabs_hook_audio", side_effect=lambda **kw: fake_hook_audio(**kw)), \
                 patch.object(server, "generate_steam_hook_overlay_video", side_effect=lambda **kw: fake_overlay(**kw)), \
                 patch.object(server, "generate_kinetic_ass_subtitles", side_effect=lambda **kw: fake_ass(**kw)), \
                 patch.object(server, "extract_clean_audio", return_value=None), \
                 patch.object(server, "get_audio_duration", return_value=6.0), \
                 patch.object(server, "check_video_has_audio", return_value=False), \
                 patch.object(server.subprocess, "run", return_value=Mock(returncode=0, stdout="", stderr="")):
                res = client.post("/api/promos/process", json={
                    "footage_paths": [str(footage)], "game_name": "Boat", "mode": "v2_voice_script",
                    "pipeline_version": "v2", "precut_video": True, "hook_type": hook_type,
                    "opening_text": opening_text, "script_text": "You build a boat and the sea fights back."})
                self.assertEqual(res.status_code, 200, res.text)
                job = wait_job(res.json()["job_id"])
                server.ACTIVE_JOBS.pop(job.job_id, None)
        self.assertEqual(job.status, "completed", job.error)
        return calls

    def test_steam_new_game_keeps_prefix_hook_audio_and_splash(self):
        calls = self.run_pipeline("steam_new_game")
        self.assertTrue(calls["tts_text"].lower().startswith("here's a new steam game"))
        self.assertEqual(calls["hook_audio"], 1)
        self.assertEqual(calls["overlay"], 1)
        self.assertTrue(calls["skip_steam_hook"])

    def test_direct_has_no_opener_no_splash_and_full_captions(self):
        calls = self.run_pipeline("direct")
        self.assertEqual(calls["tts_text"], "You build a boat and the sea fights back.")
        self.assertEqual(calls["hook_audio"], 0)
        self.assertEqual(calls["overlay"], 0)
        self.assertFalse(calls["skip_steam_hook"])

    def test_custom_uses_campaign_opening(self):
        calls = self.run_pipeline("custom", "Trapster found this gem.")
        self.assertEqual(calls["tts_text"], "Trapster found this gem. You build a boat and the sea fights back.")
        self.assertEqual(calls["hook_audio"], 0)
        self.assertEqual(calls["overlay"], 0)
        self.assertFalse(calls["skip_steam_hook"])

    def run_finished_video(self, hook_type, opening_text=""):
        calls = {"script": None, "overlay": 0, "skip_steam_hook": None}

        def fake_align(audio_path, script_text, target_duration, **kw):
            calls["script"] = script_text
            return {"words": [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25}
                              for i, w in enumerate(script_text.split())]}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            footage = root / "edited.mp4"
            footage.write_bytes(b"video")
            client = TestClient(server.app, base_url="http://127.0.0.1:8004")
            with patch.object(server, "BASE_DIR", root), \
                 patch.object(server, "align_script_words", side_effect=fake_align), \
                 patch.object(server, "generate_steam_hook_overlay_video",
                              side_effect=lambda **kw: calls.__setitem__("overlay", calls["overlay"] + 1)), \
                 patch.object(server, "generate_kinetic_ass_subtitles",
                              side_effect=lambda **kw: calls.__setitem__("skip_steam_hook", kw.get("skip_steam_hook"))), \
                 patch.object(server, "extract_clean_audio", return_value=None), \
                 patch.object(server, "get_audio_duration", return_value=6.0), \
                 patch.object(server.subprocess, "run", return_value=Mock(returncode=0, stdout="", stderr="")):
                res = client.post("/api/promos/process", json={
                    "footage_paths": [str(footage)], "game_name": "Boat", "mode": "v2_finished_video",
                    "hook_type": hook_type, "opening_text": opening_text,
                    "script_text": "You build a boat and the sea fights back."})
                job = wait_job(res.json()["job_id"])
                server.ACTIVE_JOBS.pop(job.job_id, None)
        self.assertEqual(job.status, "completed", job.error)
        return calls

    def test_finished_video_respects_hook_type(self):
        steam = self.run_finished_video("steam_new_game")
        self.assertTrue(steam["script"].lower().startswith("here's a new steam game"))
        self.assertTrue(steam["skip_steam_hook"])
        self.assertEqual(steam["overlay"], 1)
        direct = self.run_finished_video("direct")
        self.assertEqual(direct["script"], "You build a boat and the sea fights back.")
        self.assertFalse(direct["skip_steam_hook"])
        self.assertEqual(direct["overlay"], 0)
        custom = self.run_finished_video("custom", "Campaign line.")
        self.assertEqual(custom["script"], "Campaign line. You build a boat and the sea fights back.")

    def test_so_in_this_new_game_unchanged(self):
        calls = self.run_pipeline("so_in_this_new_game")
        self.assertTrue(calls["tts_text"].startswith("So in this new game, you build"))
        self.assertEqual(calls["hook_audio"], 0)
        self.assertEqual(calls["overlay"], 1)
        self.assertTrue(calls["skip_steam_hook"])


if __name__ == "__main__":
    unittest.main()
