import unittest,tempfile,json,io
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
from scraper import TrailerDatabaseManager,scrape_channel_trailers_rss

class SourceProvenanceTest(unittest.TestCase):
    def test_refresh_preserves_editorial_state_and_source_rights(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"trailers.json"
            path.write_text(json.dumps([{"id":"abc","title":"Old","status":"archived","custom_note":"retain"}]),encoding="utf-8")
            db=TrailerDatabaseManager(path)
            added=db.add_or_update_trailer({"id":"abc","title":"New","rights_status":"discovery_only","source_channel_id":"UCexample"})
            self.assertFalse(added)
            self.assertEqual(db.trailers[0]["status"],"archived")
            self.assertEqual(db.trailers[0]["custom_note"],"retain")
            self.assertEqual(db.trailers[0]["rights_status"],"discovery_only")
            self.assertTrue(db.add_or_update_trailer({"id":"new","rights_status":"publisher_policy","source_method":"youtube_rss"}))
            self.assertEqual(db.trailers[0]["rights_status"],"publisher_policy")

    def test_official_feed_metadata_does_not_imply_unrestricted_footage(self):
        now=datetime.now(timezone.utc).isoformat()
        xml=f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><yt:videoId>test123</yt:videoId><title>Example Game - Official Trailer</title><published>{now}</published></entry></feed>'.encode()
        source={"name":"Publisher","channel_id":"UCexample","rss_url":"https://www.youtube.com/feeds/videos.xml?channel_id=UCexample","rights_status":"check_embargo"}
        with patch("scraper.urllib.request.urlopen",return_value=io.BytesIO(xml)):
            rows=scrape_channel_trailers_rss(source,24)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["rights_status"],"check_embargo")
        self.assertEqual(rows[0]["source_channel_id"],"UCexample")
        self.assertEqual(rows[0]["content_kind"],"trailer")
        self.assertEqual(rows[0]["game_name"],"Example Game")

