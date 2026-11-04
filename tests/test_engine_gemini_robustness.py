"""Gemini Step 2 robustness: bounded File API polling, HTTP timeout and a same-model
strict-JSON retry before moving on to the next model."""
import itertools
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import promo_v3_engine as engine
from promo_v3_engine import analyze_single_batch_gemini


class GeminiJsonRetryTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.file = SimpleNamespace(name="files/clip-1", state=SimpleNamespace(name="ACTIVE"))
        self.client.files.upload.return_value = self.file
        self.client.files.get.return_value = self.file
        self.batch = {"batch_id": 1, "shots": [{"shot_id": 1, "duration": 2.0, "preview_path": "clip-1.mp4"}]}
        for target in (patch("promo_v3_engine.Path.exists", return_value=True), patch("promo_v3_engine.time.sleep")):
            target.start()
            self.addCleanup(target.stop)

    def analyze(self, models=("model-a", "model-b")):
        return analyze_single_batch_gemini(self.client, self.batch, list(models))

    def responses(self, *texts):
        self.client.models.generate_content.side_effect = [SimpleNamespace(text=t) for t in texts]

    def test_invalid_json_retries_same_model_once_with_strict_instruction(self):
        self.responses("Sure! Here is the analysis: clips...", '{"clips": [{"clip_id": 1, "description": "Fight"}]}')
        result = self.analyze()
        calls = self.client.models.generate_content.call_args_list
        self.assertEqual([c.kwargs["model"] for c in calls], ["model-a", "model-a"])
        self.assertNotIn(engine.STRICT_JSON_REMINDER, calls[0].kwargs["contents"])
        self.assertEqual(calls[1].kwargs["contents"][-1], engine.STRICT_JSON_REMINDER)
        self.assertEqual(result["clips"][0]["description"], "Fight")
        self.assertEqual(result["model_used"], "model-a")

    def test_second_invalid_answer_moves_to_next_model(self):
        self.responses("not json", "{still not json", '{"clips": [{"clip_id": 1, "description": "Race"}]}')
        result = self.analyze()
        models = [c.kwargs["model"] for c in self.client.models.generate_content.call_args_list]
        self.assertEqual(models, ["model-a", "model-a", "model-b"])
        self.assertEqual(result["clips"][0]["description"], "Race")

    def test_markdown_fenced_json_needs_no_retry(self):
        self.responses('```json\n{"clips": [{"clip_id": 1, "description": "Jump"}]}\n```')
        result = self.analyze()
        self.client.models.generate_content.assert_called_once()
        self.assertEqual(result["clips"][0]["description"], "Jump")

    def test_valid_json_without_clips_list_is_retried(self):
        self.responses('{"answer": "ok"}', '{"clips": [{"clip_id": 1, "description": "Swim"}]}')
        result = self.analyze(models=("model-a",))
        self.assertEqual(self.client.models.generate_content.call_count, 2)
        self.assertEqual(result["clips"][0]["description"], "Swim")


class GeminiProcessingDeadlineTests(unittest.TestCase):
    def test_clip_stuck_in_processing_times_out_and_is_deleted(self):
        client = Mock()
        processing = SimpleNamespace(name="files/clip-1", state=SimpleNamespace(name="PROCESSING"))
        client.files.upload.return_value = processing
        client.files.get.return_value = processing
        batch = {"batch_id": 3, "shots": [{"shot_id": 7, "duration": 2.0, "preview_path": "clip.mp4"}]}
        clock = itertools.count(start=0.0, step=40.0)
        with patch("promo_v3_engine.Path.exists", return_value=True), \
             patch("promo_v3_engine.time.sleep"), \
             patch("promo_v3_engine.time.monotonic", side_effect=lambda: next(clock)), \
             patch.object(engine, "GEMINI_FILE_PROCESSING_TIMEOUT_S", 100.0):
            with self.assertRaises(TimeoutError) as caught:
                analyze_single_batch_gemini(client, batch, ["model-a"])
        self.assertIn("#7", str(caught.exception))
        self.assertLessEqual(client.files.get.call_count, 5)
        client.files.delete.assert_called_once_with(name="files/clip-1")
        client.models.generate_content.assert_not_called()


class GeminiClientTimeoutTests(unittest.TestCase):
    def test_client_gets_http_timeout(self):
        with patch("google.genai.Client") as client_cls:
            engine._make_genai_client("key")
        options = client_cls.call_args.kwargs["http_options"]
        self.assertEqual(options.timeout, engine.GEMINI_HTTP_TIMEOUT_MS)
        self.assertEqual(client_cls.call_args.kwargs["api_key"], "key")

    def test_old_sdk_without_http_options_still_works(self):
        fallback = object()
        with patch("google.genai.Client", side_effect=[TypeError("unexpected keyword"), fallback]) as client_cls:
            self.assertIs(engine._make_genai_client("key"), fallback)
        self.assertEqual(client_cls.call_args.kwargs, {"api_key": "key"})

    def test_batch_analysis_uses_timeout_client(self):
        with patch.object(engine, "_make_genai_client", return_value=Mock()) as factory:
            engine.analyze_clip_batches_gemini([], gemini_api_key="key", enable_early_stopping=False)
        factory.assert_called_once_with("key")


if __name__ == "__main__":
    unittest.main()
