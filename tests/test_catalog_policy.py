import json
import tempfile
import unittest
from pathlib import Path
from catalog_policy import cooldown_check, load_feed_sources

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"game":"Shoe it All!", "brand":"NextLoot", "date":"2026-10-16", "status":"Publicado", "piece_id":"old"}]
    def test_before_boundary_blocked(self):
        self.assertFalse(cooldown_check("SHOE IT ALL", "NextLoot", "2026-11-05", self.rows)["eligible"])
    def test_boundary_allowed(self):
        self.assertTrue(cooldown_check("Shoe it All!", "NextLoot", "2026-11-06", self.rows)["eligible"])
    def test_draft_does_not_claim_publication(self):
        self.rows[0]["status"]="Borrador"
        self.assertTrue(cooldown_check("Shoe it All!", "NextLoot", "2026-10-20", self.rows)["eligible"])
    def test_crosspost_same_piece_allowed(self):
        self.assertTrue(cooldown_check("Shoe it All!", "NextLoot", "2026-10-16", self.rows, piece_id="old")["eligible"])
    def test_other_brand_and_future_do_not_block(self):
        self.assertTrue(cooldown_check("Shoe it All!", "PartyLoot", "2026-10-20", self.rows)["eligible"])
        self.assertTrue(cooldown_check("Shoe it All!", "NextLoot", "2026-10-15", self.rows)["eligible"])
    def test_source_validation(self):
        row={"enabled":True,"channel_id":"UCSc6s-ZJXKlSQr51dsox34g","rss_url":"https://evil.example/feeds/videos.xml"}
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"sources.json"
            p.write_text(json.dumps({"sources":[row]}))
            with self.assertRaises(ValueError):load_feed_sources(p)

if __name__ == "__main__":
    unittest.main()


class SubtitleAliasTest(unittest.TestCase):
    def test_known_subtitle_alias(self):
        history=[{"brand":"NextLoot","game":"Escape Academy 2","status":"Publicado","published_at":"2026-10-22"}]
        self.assertFalse(cooldown_check("Escape Academy 2: Back 2 School","NextLoot","2026-10-30",history)["eligible"])
