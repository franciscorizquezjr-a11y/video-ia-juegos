"""File API failures must not discard a batch on the first connection reset."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import httpx
from google.genai.errors import APIError

from promo_v3_engine import analyze_single_batch_gemini


class GeminiFileRetryTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.file = SimpleNamespace(name="files/clip-1", state=SimpleNamespace(name="ACTIVE"))
        self.client.files.upload.return_value = self.file
        self.client.files.get.return_value = self.file
        self.client.models.generate_content.return_value = SimpleNamespace(
            text='{"clips": [{"clip_id": 1, "description": "Gameplay"}]}'
        )
        self.batch = {"batch_id": 1, "shots": [
            {"shot_id": 1, "duration": 2.0, "preview_path": "clip-1.mp4"}
        ]}
        self.progress = Mock()
        self.exists = patch("promo_v3_engine.Path.exists", return_value=True)
        self.sleep = patch("promo_v3_engine.time.sleep")
        self.exists.start()
        self.mock_sleep = self.sleep.start()
        self.addCleanup(self.exists.stop)
        self.addCleanup(self.sleep.stop)

    def analyze(self):
        return analyze_single_batch_gemini(
            self.client, self.batch, ["test-model"], self.progress
        )

    def test_connection_reset_during_upload_recovers(self):
        self.client.files.upload.side_effect = [httpx.ConnectError("WinError 10054"), self.file]
        result = self.analyze()
        self.assertEqual(result["clips"][0]["description"], "Gameplay")
        self.assertEqual(self.client.files.upload.call_count, 2)
        self.mock_sleep.assert_called_once_with(2.0)
        self.assertTrue(any("Reintento 2/4" in c.args[1] for c in self.progress.call_args_list))
        self.client.files.delete.assert_called_once_with(name=self.file.name)

    def test_status_timeout_recovers_without_uploading_again(self):
        self.client.files.get.side_effect = [httpx.ReadTimeout("timeout"), self.file]
        self.analyze()
        self.client.files.upload.assert_called_once()
        self.assertEqual(self.client.files.get.call_count, 2)
        self.client.models.generate_content.assert_called_once()

    def test_status_failures_are_bounded_and_uploaded_file_is_cleaned(self):
        error = httpx.ConnectError("WinError 10054")
        self.client.files.get.side_effect = error
        with self.assertRaisesRegex(RuntimeError, "tras 4 intentos") as caught:
            self.analyze()
        self.assertIs(caught.exception.__cause__, error)
        self.assertEqual(self.client.files.get.call_count, 4)
        self.assertEqual(self.mock_sleep.call_args_list, [call(2.0), call(4.0), call(8.0)])
        self.client.files.delete.assert_called_once_with(name=self.file.name)
        self.client.models.generate_content.assert_not_called()

    def test_upload_failures_are_bounded(self):
        self.client.files.upload.side_effect = httpx.ConnectError("reset")
        with self.assertRaisesRegex(RuntimeError, "Subida del clip #1"):
            self.analyze()
        self.assertEqual(self.client.files.upload.call_count, 4)
        self.client.files.get.assert_not_called()

    def test_transient_api_errors_recover(self):
        for code in (429, 503):
            with self.subTest(code=code):
                self.client.files.upload.side_effect = [APIError(code, {}), self.file]
                self.assertEqual(self.analyze()["clips"][0]["clip_id"], 1)

    def test_auth_errors_are_not_retried(self):
        error = APIError(403, {})
        self.client.files.upload.side_effect = error
        with self.assertRaises(APIError):
            self.analyze()
        self.client.files.upload.assert_called_once()
        self.mock_sleep.assert_not_called()

    def test_local_file_errors_are_not_retried(self):
        self.client.files.upload.side_effect = FileNotFoundError("clip removed")
        with self.assertRaises(FileNotFoundError):
            self.analyze()
        self.client.files.upload.assert_called_once()
        self.mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
