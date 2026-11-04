"""Gemini File API polling has a deadline and the genai client an HTTP timeout (no network)."""
import os
import tempfile
import unittest
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import llm
from utils import LLMError


def gemini_file(state, name="files/clip-1"):
    return SimpleNamespace(name=name, state=SimpleNamespace(name=state), uri="gs://x",
                           error=SimpleNamespace(message="boom"))


class WaitForGeminiFileTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch.object(llm.time, "sleep").start()
        self.clock = count(0, 10)  # every monotonic() call advances 10 s
        patch.object(llm.time, "monotonic", side_effect=lambda: next(self.clock)).start()
        self.addCleanup(patch.stopall)
        self.client = Mock()

    def test_returns_once_file_is_active(self):
        self.client.files.get.side_effect = [gemini_file("PROCESSING"), gemini_file("ACTIVE")]
        result = llm.wait_for_gemini_file_active(self.client, gemini_file("PROCESSING"),
                                                 timeout_seconds=300, poll_interval=3)
        self.assertEqual(result.state.name, "ACTIVE")
        self.sleep.assert_called_once_with(3)

    def test_failed_state_is_returned_not_raised(self):
        self.client.files.get.return_value = gemini_file("FAILED")
        self.assertEqual(llm.wait_for_gemini_file_active(self.client, gemini_file("PROCESSING")).state.name, "FAILED")

    def test_stuck_file_raises_clear_timeout(self):
        self.client.files.get.return_value = gemini_file("PROCESSING")
        with self.assertRaises(llm.GeminiFileProcessingTimeout) as caught:
            llm.wait_for_gemini_file_active(self.client, gemini_file("PROCESSING"),
                                            timeout_seconds=30, label="clip.mp4")
        self.assertIsInstance(caught.exception, TimeoutError)
        self.assertIsInstance(caught.exception, LLMError)
        self.assertIn("clip.mp4", str(caught.exception))
        self.assertIn("GEMINI_FILE_PROCESSING_TIMEOUT", str(caught.exception))
        self.assertLessEqual(self.client.files.get.call_count, 5)

    def test_zero_disables_the_deadline(self):
        self.clock = count(0, 10_000)  # hours pass between polls
        self.client.files.get.side_effect = [gemini_file("PROCESSING")] * 3 + [gemini_file("ACTIVE")]
        result = llm.wait_for_gemini_file_active(self.client, gemini_file("PROCESSING"), timeout_seconds=0)
        self.assertEqual(result.state.name, "ACTIVE")

    def test_environment_override_and_invalid_values(self):
        with patch.dict(os.environ, {"GEMINI_FILE_PROCESSING_TIMEOUT": "15"}):
            self.assertEqual(llm.gemini_file_processing_timeout(), 15.0)
        with patch.dict(os.environ, {"GEMINI_FILE_PROCESSING_TIMEOUT": "soon"}):
            self.assertEqual(llm.gemini_file_processing_timeout(), llm.GEMINI_FILE_PROCESSING_TIMEOUT_SECONDS)
        with patch.dict(os.environ, {"GEMINI_HTTP_TIMEOUT": "-5"}):
            self.assertEqual(llm.gemini_http_timeout(), 0.0)

    def test_custom_status_fetcher_is_used(self):
        fetch = Mock(return_value=gemini_file("ACTIVE"))
        llm.wait_for_gemini_file_active(self.client, gemini_file("PROCESSING"), get_file=fetch)
        fetch.assert_called_once_with("files/clip-1")
        self.client.files.get.assert_not_called()


class GenaiClientTimeoutTests(unittest.TestCase):
    def test_timeout_is_sent_in_milliseconds(self):
        genai = Mock()
        llm.create_genai_client(genai, "key", timeout_seconds=120)
        genai.Client.assert_called_once_with(api_key="key", http_options={"timeout": 120000})

    def test_old_sdk_without_http_options_falls_back(self):
        genai = Mock()
        genai.Client.side_effect = [TypeError("unexpected keyword 'http_options'"), "plain-client"]
        self.assertEqual(llm.create_genai_client(genai, "key", timeout_seconds=60), "plain-client")
        self.assertEqual(genai.Client.call_args_list[-1].kwargs, {"api_key": "key"})

    def test_zero_timeout_keeps_plain_client(self):
        genai = Mock()
        llm.create_genai_client(genai, "key", timeout_seconds=0)
        genai.Client.assert_called_once_with(api_key="key")

    def test_installed_sdk_accepts_the_timeout(self):
        try:
            from google import genai
        except ImportError:
            self.skipTest("google-genai not installed")
        client = llm.create_genai_client(genai, "dummy-key", timeout_seconds=90)
        self.assertIsNotNone(client)


class AnalyzerDeadlineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.video = Path(tmp.name) / "clip.mp4"
        self.video.write_bytes(b"\x00" * 16)
        self.client = Mock()
        self.client.files.upload.return_value = gemini_file("PROCESSING")
        self.client.files.get.return_value = gemini_file("PROCESSING")
        patch.object(llm.time, "sleep").start()
        clock = count(0, 100)
        patch.object(llm.time, "monotonic", side_effect=lambda: next(clock)).start()
        patch.dict(os.environ, {"GEMINI_FILE_PROCESSING_TIMEOUT": "250"}).start()
        self.addCleanup(patch.stopall)

    def test_llm_client_times_out_and_deletes_upload(self):
        with patch.object(llm, "create_genai_client", return_value=self.client):
            client = llm.LLMClient(api_key="dummy")
        with self.assertRaises(llm.GeminiFileProcessingTimeout):
            client.analyze_video(self.video, "prompt")
        self.client.files.delete.assert_called_with(name="files/clip-1")
        self.client.models.generate_content.assert_not_called()

    def test_video_analyzer_times_out_and_deletes_upload(self):
        import video_analyzer
        from schemas_v1 import VideoMetadata
        with patch.object(video_analyzer, "create_genai_client", return_value=self.client):
            analyzer = video_analyzer.GeminiVideoAnalyzer(api_key="dummy")
        with self.assertRaises(TimeoutError):
            analyzer.analyze_video(self.video, VideoMetadata(duration_seconds=5.0), "prompt", [])
        self.client.files.upload.assert_called_once()
        self.client.files.delete.assert_called_with(name="files/clip-1")
        self.client.models.generate_content.assert_not_called()


if __name__ == "__main__":
    unittest.main()
