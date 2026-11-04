"""Security of the local web server: Host/Origin guard, CORS, masked API keys,
path safety and yt-dlp argument hygiene. No external APIs are called."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import web_server as server

ROOT = Path(__file__).resolve().parents[1]
MASK = "\u2022"


class LocalGuardTests(unittest.TestCase):
    def client(self, base_url="http://127.0.0.1:8004"):
        return TestClient(server.app, base_url=base_url)

    def test_health_works_for_local_hosts(self):
        for base in ("http://127.0.0.1:8004", "http://localhost:8004", "http://[::1]:8004", "http://testserver"):
            with self.subTest(base=base):
                res = self.client(base).get("/api/health")
                self.assertEqual(res.status_code, 200)
                self.assertTrue(res.json()["ready"])

    def test_foreign_host_is_rejected_dns_rebinding(self):
        res = self.client("http://evil.example:8004").get("/api/health")
        self.assertEqual(res.status_code, 400)
        res = self.client("http://127.0.0.1.evil.example").get("/api/config")
        self.assertEqual(res.status_code, 400)

    def test_extra_hosts_can_be_allowed_explicitly(self):
        with patch.dict(os.environ, {"IA_VIDEO_ALLOWED_HOSTS": "studio-pc.lan"}):
            self.assertEqual(self.client("http://studio-pc.lan:8004").get("/api/health").status_code, 200)

    def test_requests_without_origin_keep_working(self):
        # curl / python / Codex style: no Origin, no Sec-Fetch-* headers.
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, "ENV_FILE_PATH", Path(tmp) / ".env"):
            res = self.client().post("/api/config", json={})
        self.assertEqual(res.status_code, 200, res.text)

    def test_cross_origin_writes_are_blocked(self):
        c = self.client()
        res = c.post("/api/config", json={}, headers={"Origin": "https://evil.example"})
        self.assertEqual(res.status_code, 403)
        res = c.post("/api/config", json={}, headers={"Origin": "null"})
        self.assertEqual(res.status_code, 403)
        res = c.delete("/api/calendar/x", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(res.status_code, 403)

    def test_local_origin_writes_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, "ENV_FILE_PATH", Path(tmp) / ".env"):
            for origin in ("http://127.0.0.1:8004", "http://localhost:3000", "http://[::1]:8004"):
                with self.subTest(origin=origin):
                    res = self.client().post("/api/config", json={}, headers={"Origin": origin, "Sec-Fetch-Site": "same-origin"})
                    self.assertEqual(res.status_code, 200, res.text)

    def test_cors_only_reflects_local_origins(self):
        c = self.client()
        res = c.get("/api/health", headers={"Origin": "http://localhost:5173"})
        self.assertEqual(res.headers.get("access-control-allow-origin"), "http://localhost:5173")
        res = c.get("/api/health", headers={"Origin": "https://evil.example"})
        self.assertIsNone(res.headers.get("access-control-allow-origin"))
        pre = c.options("/api/config", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        self.assertNotEqual(pre.headers.get("access-control-allow-origin"), "https://evil.example")

    def test_sse_stream_still_works_through_guard(self):
        job = server.create_job("guard-sse")
        job.complete({"video_url": "/x.mp4", "warnings": ["w1"]})
        try:
            res = self.client().get(f"/api/jobs/{job.job_id}/stream")
            self.assertEqual(res.status_code, 200)
            payload = json.loads(res.text.removeprefix("data: ").strip())
            self.assertEqual(payload["type"], "completed")
            self.assertEqual(payload["result"]["warnings"], ["w1"])
        finally:
            server.ACTIVE_JOBS.pop(job.job_id, None)

    def test_rejection_helper(self):
        self.assertIsNone(server._local_request_rejection("GET", {"host": "localhost:8004", "origin": "https://evil.example"}))
        self.assertEqual(server._local_request_rejection("GET", {"host": "attacker.com"})[0], 400)
        self.assertEqual(server._local_request_rejection("PATCH", {"host": "127.0.0.1", "origin": "http://evil"})[0], 403)
        self.assertIsNone(server._local_request_rejection("POST", {"host": "127.0.0.1"}))
        self.assertIsNone(server._local_request_rejection("POST", {}))


class ConfigMaskingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_path = Path(self.tmp.name) / ".env"
        self.env_path.write_text(
            "# comment stays\r\nGROQ_API_KEY=gsk_realgroqkey1234\r\nGEMINI_API_KEY=AIza-real-gemini-9876\r\n"
            "ELEVENLABS_API_KEY=xi-real-eleven-5555\r\nOTHER=keep\r\n", encoding="utf-8")
        env = {"GROQ_API_KEY": "gsk_realgroqkey1234", "GEMINI_API_KEY": "AIza-real-gemini-9876",
               "ELEVENLABS_API_KEY": "xi-real-eleven-5555"}
        p1 = patch.dict(os.environ, env)
        p2 = patch.object(server, "ENV_FILE_PATH", self.env_path)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        self.client = TestClient(server.app, base_url="http://127.0.0.1:8004")

    def test_get_config_masks_keys(self):
        res = self.client.get("/api/config")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["groq_api_key"], MASK * 4 + "1234")
        self.assertEqual(data["gemini_api_key"], MASK * 4 + "9876")
        self.assertEqual(data["elevenlabs_api_key"], MASK * 4 + "5555")
        self.assertTrue(data["groq_api_key_set"] and data["gemini_api_key_set"] and data["elevenlabs_api_key_set"])
        for raw in ("gsk_realgroqkey1234", "AIza-real-gemini-9876", "xi-real-eleven-5555"):
            self.assertNotIn(raw, res.text)
        self.assertIn("default_resolution", data)

    def test_mask_helper(self):
        self.assertEqual(server._mask_secret(""), "")
        self.assertEqual(server._mask_secret(None), "")
        self.assertEqual(server._mask_secret("abc"), MASK * 4)
        self.assertEqual(server._mask_secret("abcdefgh"), MASK * 4 + "efgh")

    def test_saving_masked_or_empty_values_keeps_real_keys(self):
        before = self.env_path.read_bytes()
        res = self.client.post("/api/config", json={
            "groq_api_key": MASK * 4 + "1234", "gemini_api_key": "", "elevenlabs_api_key": None,
            "elevenlabs_voice_id": "   "})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.env_path.read_bytes(), before)
        self.assertEqual(os.environ["GROQ_API_KEY"], "gsk_realgroqkey1234")
        self.assertIn("GROQ_API_KEY", res.json()["ignored_keys"])

    def test_real_value_is_written_atomically_preserving_other_lines(self):
        res = self.client.post("/api/config", json={"groq_api_key": "gsk_newkey_0000", "gemini_api_key": MASK * 4 + "9876"})
        self.assertEqual(res.status_code, 200, res.text)
        text = self.env_path.read_bytes().decode("utf-8")
        self.assertIn("GROQ_API_KEY=gsk_newkey_0000\r\n", text)
        self.assertIn("GEMINI_API_KEY=AIza-real-gemini-9876\r\n", text)
        self.assertIn("# comment stays\r\n", text)
        self.assertIn("OTHER=keep\r\n", text)
        self.assertEqual(os.environ["GROQ_API_KEY"], "gsk_newkey_0000")
        self.assertEqual(res.json()["groq_api_key"], MASK * 4 + "0000")
        self.assertEqual([p.name for p in self.env_path.parent.iterdir()], [".env"])  # no temp leftovers

    def test_crlf_injection_is_rejected(self):
        before = self.env_path.read_bytes()
        res = self.client.post("/api/config", json={"groq_api_key": "abc\nEVIL=1"})
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/config", json={"elevenlabs_voice_id": "v\r\nX=1"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.env_path.read_bytes(), before)

    def test_clear_keys_option_empties_a_key(self):
        res = self.client.post("/api/config", json={"clear_keys": ["groq_api_key"]})
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"GROQ_API_KEY=\r\n", self.env_path.read_bytes())
        self.assertIn(b"GEMINI_API_KEY=AIza-real-gemini-9876\r\n", self.env_path.read_bytes())
        self.assertFalse(res.json()["groq_api_key_set"])

    def test_gamedev_settings_are_masked_and_masks_ignored(self):
        with patch.dict(os.environ, {"SCRAPECREATORS_API_KEY": "sc_secret_abcd", "STEAMWORKS_API_KEY": "steam_secret_wxyz"}):
            self.env_path.write_text("SCRAPECREATORS_API_KEY=sc_secret_abcd\nSTEAMWORKS_API_KEY=steam_secret_wxyz\n", encoding="utf-8")
            data = self.client.get("/api/gamedev/settings").json()
            self.assertEqual(data["scrapecreators_api_key"], MASK * 4 + "abcd")
            self.assertEqual(data["steamworks_api_key"], MASK * 4 + "wxyz")
            self.assertTrue(data["has_scrapecreators_key"] and data["has_steamworks_key"])
            res = self.client.post("/api/gamedev/settings", json={
                "scrapecreators_api_key": data["scrapecreators_api_key"], "steamworks_api_key": ""})
            self.assertEqual(res.status_code, 200)
            self.assertIn("SCRAPECREATORS_API_KEY=sc_secret_abcd", self.env_path.read_text(encoding="utf-8"))
            self.assertIn("STEAMWORKS_API_KEY=steam_secret_wxyz", self.env_path.read_text(encoding="utf-8"))
            self.assertNotIn("sc_secret_abcd", res.text)

    def test_client_key_fallback_helper(self):
        self.assertEqual(server._client_secret_or_env(MASK * 4 + "1234", "server"), "server")
        self.assertEqual(server._client_secret_or_env("", "server"), "server")
        self.assertEqual(server._client_secret_or_env(None, " server "), "server")
        self.assertEqual(server._client_secret_or_env(" typed ", "server"), "typed")

    def test_elevenlabs_voices_uses_server_key_when_client_sends_mask(self):
        fake = Mock(status_code=200)
        fake.json.return_value = {"voices": []}
        with patch("requests.get", return_value=fake) as get:
            res = self.client.get("/api/elevenlabs/voices", params={"api_key": MASK * 4 + "5555"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(get.call_args.kwargs["headers"]["xi-api-key"], "xi-real-eleven-5555")

    def test_hook_generation_uses_server_keys_when_client_sends_masks(self):
        with patch.object(server, "generate_hook_with_groq", return_value="hook") as gen:
            res = self.client.post("/api/hook/generate", json={
                "game_name": "Boat", "groq_api_key": MASK * 4 + "1234", "gemini_api_key": MASK * 4})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(gen.call_args.args[1], "gsk_realgroqkey1234")
        self.assertEqual(gen.call_args.kwargs["gemini_api_key"], "AIza-real-gemini-9876")


class PathSafetyTests(unittest.TestCase):
    def test_session_id_validation(self):
        for good in ("sess_1a2b3c4d_Mi_Juego", "sess_multi_ab12cd34_Trailer_3v", "sess_1700000000",
                     "global", "sess_ab12_Caf\u00e9_\u00d1and\u00fa", "legacy session (copy)"):
            with self.subTest(good=good):
                self.assertEqual(server._validate_session_id(good), good)
        for bad in ("", "  ", "..", "../x", "a/b", "a\\b", "C:evil", "x..y", "sess.", "a\x00b", "a*b", "x" * 201):
            with self.subTest(bad=bad):
                with self.assertRaises(HTTPException) as ctx:
                    server._validate_session_id(bad)
                self.assertEqual(ctx.exception.status_code, 400)

    def test_safe_session_dir_stays_in_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "v3_cache"
            cache.mkdir()
            self.assertEqual(server._safe_session_dir("sess_x", cache_dir=cache), cache.resolve() / "sess_x")
            with self.assertRaises(HTTPException):
                server._safe_session_dir("../outside", cache_dir=cache)

    def test_session_endpoints_reject_traversal(self):
        client = TestClient(server.app, base_url="http://127.0.0.1:8004")
        res = client.get("/api/promov3/step3/retention", params={"session_id": "../../data"})
        self.assertEqual(res.status_code, 400)
        res = client.get("/api/promov3/session", params={"session_id": "..\\..\\data"})
        self.assertEqual(res.status_code, 400)
        res = client.post("/api/promov3/step4/save", json={"session_id": "../evil", "edl": {}})
        self.assertEqual(res.status_code, 400)
        res = client.post("/api/promov3/step5/remix_sfx", json={"session_id": "..", "sfx_tracks": []})
        self.assertEqual(res.status_code, 400)

    def test_upload_basename_sanitizer(self):
        f = server._safe_upload_basename
        self.assertEqual(f("../../evil.mp4"), "evil.mp4")
        self.assertEqual(f("..\\..\\Windows\\win.ini"), "win.ini")
        self.assertEqual(f("C:\\clips\\a:b?.mp4"), "a_b_.mp4")
        self.assertEqual(f("CON.mp4"), "_CON.mp4")
        self.assertTrue(f("..").startswith("upload_"))
        self.assertTrue(f("").startswith("upload_"))
        self.assertTrue(f("x" * 400 + ".mp4").endswith(".mp4"))
        self.assertLessEqual(len(f("x" * 400 + ".mp4")), 180)

    def test_generic_upload_stays_in_uploads_folder(self):
        client = TestClient(server.app, base_url="http://127.0.0.1:8004")
        res = client.post("/api/upload", files={"file": ("../../escape_test.txt", b"hi", "text/plain")})
        self.assertEqual(res.status_code, 200, res.text)
        saved = Path(res.json()["saved_path"])
        try:
            self.assertEqual(saved.parent, (ROOT / "temp" / "uploads").resolve())
            self.assertTrue(saved.name.endswith("_escape_test.txt"))
            self.assertEqual(saved.read_bytes(), b"hi")
        finally:
            saved.unlink(missing_ok=True)

    def test_footage_delete_accepts_only_bare_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            footage = tmp / "footage"
            footage.mkdir()
            (footage / "clip.mp4").write_bytes(b"x")
            secret = tmp / "secret.mp4"
            secret.write_bytes(b"s")
            games_file = tmp / "gamedev_games.json"
            games_file.write_text(json.dumps([{"id": "g1", "name": "G", "footage_folder": str(footage)}]), encoding="utf-8")
            client = TestClient(server.app, base_url="http://127.0.0.1:8004")
            with patch.object(server, "GAMEDEV_GAMES_FILE", games_file):
                for bad in ("..%5Csecret.mp4", "..%2F..%2Fsecret.mp4", "a..b.mp4"):
                    with self.subTest(bad=bad):
                        res = client.delete(f"/api/gamedev/games/g1/footage/{bad}")
                        self.assertIn(res.status_code, (400, 404))
                self.assertTrue(secret.exists())
                res = client.delete("/api/gamedev/games/g1/footage/clip.mp4")
                self.assertEqual(res.status_code, 200, res.text)
                self.assertFalse((footage / "clip.mp4").exists())


class YtDlpArgumentTests(unittest.TestCase):
    def test_url_validation(self):
        ok = server._validate_http_url(" https://www.youtube.com/watch?v=abc ")
        self.assertEqual(ok, "https://www.youtube.com/watch?v=abc")
        for bad in ("--exec=calc", "-o/tmp/x", "file:///etc/passwd", "ftp://host/x", "javascript:alert(1)",
                    "https://", "https://exa mple.com", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    server._validate_http_url(bad)

    def test_download_command_ends_options_before_url(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return Mock(returncode=1, stderr="boom")

        with tempfile.TemporaryDirectory() as tmp, patch.object(server.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(RuntimeError):
                server.download_youtube_trailer("https://youtu.be/abc", Path(tmp) / "out.mp4")
        self.assertEqual(len(calls), 2)
        for cmd in calls:
            self.assertEqual(cmd[-2:], ["--", "https://youtu.be/abc"])
            self.assertIn("--no-check-certificates", cmd)

    def test_download_rejects_option_like_url_without_running(self):
        with patch.object(server.subprocess, "run") as run, tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                server.download_youtube_trailer("--exec=calc", Path(tmp) / "out.mp4")
            run.assert_not_called()

    def test_ingest_endpoint_rejects_non_http_url(self):
        client = TestClient(server.app, base_url="http://127.0.0.1:8004")
        with patch.object(server, "download_youtube_clip") as dl:
            res = client.post("/api/ingest/youtube", json={"url": "file:///youtube.com/etc/passwd"})
        self.assertEqual(res.status_code, 400)
        dl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
