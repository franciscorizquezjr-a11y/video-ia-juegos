"""Data safety: atomic JSON store, corrupt-file quarantine, no demo data persisted
from GET, bounded job registry, protected uploads cleanup. No external APIs."""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import json_store
from json_store import JsonStoreError
import web_server as server


class JsonStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_missing_file_returns_copy_of_default(self):
        default = {"items": []}
        data = json_store.load_json(self.dir / "missing.json", default)
        self.assertEqual(data, default)
        data["items"].append(1)
        self.assertEqual(default, {"items": []})

    def test_corrupt_file_is_moved_aside_and_raises(self):
        path = self.dir / "calendar.json"
        path.write_text('[{"id": 1}, ', encoding="utf-8")
        with self.assertRaises(JsonStoreError) as ctx:
            json_store.load_json(path, [])
        self.assertFalse(path.exists())
        quarantined = list(self.dir.glob("calendar.json.corrupt-*"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_text(encoding="utf-8"), '[{"id": 1}, ')
        self.assertEqual(ctx.exception.quarantined_path, quarantined[0])

    def test_save_is_atomic_utf8_and_readable(self):
        path = self.dir / "sub" / "data.json"
        json_store.save_json_atomic(path, {"name": "Caf\u00e9 \u00d1and\u00fa \U0001F3AE"})
        raw = path.read_text(encoding="utf-8")
        self.assertIn("Caf\u00e9", raw)  # ensure_ascii=False
        self.assertIn('\n  "name"', raw)  # indent=2
        self.assertEqual(json_store.load_json(path, None)["name"], "Caf\u00e9 \u00d1and\u00fa \U0001F3AE")
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["data.json"])

    def test_failed_serialisation_keeps_previous_file(self):
        path = self.dir / "data.json"
        json_store.save_json_atomic(path, [1, 2, 3])
        with self.assertRaises(TypeError):
            json_store.save_json_atomic(path, {"bad": object()})
        self.assertEqual(json_store.load_json(path, []), [1, 2, 3])

    def test_update_is_serialised_by_lock(self):
        path = self.dir / "counter.json"

        def bump(data):
            value = data.get("n", 0)
            time.sleep(0.001)
            data["n"] = value + 1

        threads = [threading.Thread(target=json_store.update, args=(path, {}, bump)) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(json_store.load_json(path, {})["n"], 20)

    def test_update_does_not_write_when_load_fails(self):
        path = self.dir / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(JsonStoreError):
            json_store.update(path, [], lambda d: d.append(1))
        self.assertFalse(path.exists())  # nothing written over it
        self.assertEqual(len(list(self.dir.glob("broken.json.corrupt-*"))), 1)


class ServerStateFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.client = TestClient(server.app, base_url="http://127.0.0.1:8004")

    def test_corrupt_calendar_is_never_wiped_by_a_save(self):
        cal = self.dir / "calendar_events.json"
        cal.write_text('[{"id": "keep-me"', encoding="utf-8")
        with patch.object(server, "CALENDAR_DATA_FILE", cal):
            res = self.client.post("/api/calendar", json={"game_name": "Boat", "date": "2026-11-04"})
        self.assertEqual(res.status_code, 500)
        self.assertIn("calendar_events.json", res.json()["detail"])
        self.assertFalse(cal.exists())
        backups = list(self.dir.glob("calendar_events.json.corrupt-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), '[{"id": "keep-me"')

    def test_calendar_roundtrip_with_locking_decorator(self):
        cal = self.dir / "calendar_events.json"
        with patch.object(server, "CALENDAR_DATA_FILE", cal):
            res = self.client.post("/api/calendar", json={"game_name": "Boat", "date": "2026-11-04", "account": "IGD"})
            self.assertEqual(res.status_code, 200, res.text)
            events = self.client.get("/api/calendar").json()["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["game_name"], "Boat")

    def test_insights_get_does_not_persist_demo_metrics(self):
        games_file = self.dir / "gamedev_games.json"
        original = [{"id": "g1", "name": "Boat", "footage_folder": str(self.dir)}]
        games_file.write_text(json.dumps(original), encoding="utf-8")
        before = games_file.read_bytes()
        with patch.object(server, "GAMEDEV_GAMES_FILE", games_file):
            data = self.client.get("/api/gamedev/games/g1/insights").json()
            evo = self.client.get("/api/gamedev/games/g1/evolutions").json()
        self.assertTrue(data["simulated"])
        self.assertTrue(data["insights"])
        self.assertTrue(all(item["simulated"] for item in data["insights"]))
        self.assertTrue(evo["simulated"])
        self.assertEqual(games_file.read_bytes(), before)

    def test_insights_get_with_real_data_is_not_simulated(self):
        games_file = self.dir / "gamedev_games.json"
        games_file.write_text(json.dumps([{"id": "g1", "name": "Boat", "insights": [
            {"upload_date": "2026-10-01", "views_tiktok": 10, "views_instagram": 5, "views_shorts": 1, "total_views": 16}
        ]}]), encoding="utf-8")
        with patch.object(server, "GAMEDEV_GAMES_FILE", games_file):
            data = self.client.get("/api/gamedev/games/g1/insights").json()
        self.assertFalse(data["simulated"])
        self.assertNotIn("simulated", data["insights"][0])


class JobRegistryTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(server.ACTIVE_JOBS)
        server.ACTIVE_JOBS.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        server.ACTIVE_JOBS.clear()
        server.ACTIVE_JOBS.update(self.saved)

    def _job(self, jid, status, finished_at=None):
        job = server.Job(jid, "test")
        job.status = status
        job.finished_at = finished_at
        server.ACTIVE_JOBS[jid] = job
        return job

    def test_old_finished_jobs_are_evicted_running_kept(self):
        now = time.time()
        self._job("old_done", "completed", now - 7 * 3600)
        self._job("old_err", "error", now - 7 * 3600)
        self._job("recent", "completed", now - 60)
        self._job("running", "running")
        removed = server._evict_finished_jobs(now=now)
        self.assertEqual(removed, 2)
        self.assertEqual(set(server.ACTIVE_JOBS), {"recent", "running"})

    def test_only_newest_200_finished_are_kept(self):
        now = time.time()
        for i in range(230):
            self._job(f"j{i}", "completed", now - i)
        for i in range(5):
            self._job(f"run{i}", "running")
        server._evict_finished_jobs(now=now)
        finished = [j for j in server.ACTIVE_JOBS.values() if j.status != "running"]
        self.assertEqual(len(finished), 200)
        self.assertIn("j0", server.ACTIVE_JOBS)
        self.assertNotIn("j229", server.ACTIVE_JOBS)
        self.assertEqual(sum(1 for j in server.ACTIVE_JOBS.values() if j.status == "running"), 5)

    def test_complete_and_fail_stamp_finished_at(self):
        job = server.create_job("stamp")
        self.assertIsNone(job.finished_at)
        job.complete({"ok": True})
        self.assertIsNotNone(job.finished_at)


class UploadsCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.uploads = root / "uploads"
        self.cache = root / "v3_cache"
        self.uploads.mkdir()
        (self.cache / "sess_a").mkdir(parents=True)
        old = time.time() - 72 * 3600
        for name in ("voice_ab12_take.mp3", "voice_ab12_take.wav", "used_clip.mp4", "orphan.mp4", "fresh.mp4"):
            f = self.uploads / name
            f.write_bytes(b"x")
            if name != "fresh.mp4":
                os.utime(f, (old, old))

    def test_referenced_files_are_kept(self):
        (self.cache / "sess_a" / "step3_script.json").write_text(json.dumps({
            "voice_path": "C:\\\\app\\\\temp\\\\uploads\\\\voice_ab12_take.wav",
            "nested": {"clips": ["/media/temp/uploads/used_clip.mp4?t=1"]},
        }), encoding="utf-8")
        purged = server._cleanup_stale_uploads(self.uploads, self.cache)
        self.assertEqual(purged, 1)
        remaining = sorted(p.name for p in self.uploads.iterdir())
        self.assertEqual(remaining, ["fresh.mp4", "used_clip.mp4", "voice_ab12_take.mp3", "voice_ab12_take.wav"])

    def test_unreadable_session_json_skips_deletion(self):
        (self.cache / "sess_a" / "step3_script.json").write_text("{broken", encoding="utf-8")
        purged = server._cleanup_stale_uploads(self.uploads, self.cache)
        self.assertEqual(purged, 0)
        self.assertEqual(len(list(self.uploads.iterdir())), 5)


class ScraperDbTests(unittest.TestCase):
    def test_corrupt_trailer_db_is_preserved(self):
        from scraper import TrailerDatabaseManager
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "trailers_db.json"
            db.write_text('[{"id": "a"}', encoding="utf-8")
            mgr = TrailerDatabaseManager(db)
            self.assertEqual(mgr.trailers, [])
            self.assertIsNotNone(mgr.load_error)
            backups = list(Path(tmp).glob("trailers_db.json.corrupt-*"))
            self.assertEqual(len(backups), 1)
            mgr.trailers.append({"id": "b"})
            mgr.save()
            self.assertEqual(json.loads(db.read_text(encoding="utf-8")), [{"id": "b"}])
            self.assertEqual(backups[0].read_text(encoding="utf-8"), '[{"id": "a"}')

    def test_non_list_db_is_never_overwritten(self):
        from scraper import TrailerDatabaseManager
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "trailers_db.json"
            db.write_text('{"unexpected": true}', encoding="utf-8")
            mgr = TrailerDatabaseManager(db)
            mgr.trailers.append({"id": "x"})
            mgr.save()
            self.assertEqual(db.read_text(encoding="utf-8"), '{"unexpected": true}')

    def test_roundtrip(self):
        from scraper import TrailerDatabaseManager
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "trailers_db.json"
            mgr = TrailerDatabaseManager(db)
            mgr.trailers = [{"id": "a"}, {"id": "a"}, {"id": "b"}]
            mgr.save()
            self.assertEqual([t["id"] for t in TrailerDatabaseManager(db).trailers], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
