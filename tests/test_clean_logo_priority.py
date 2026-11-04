import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image
from setup_trailer_logo import resolve_game_logo,logo_directory

class CleanLogoPriorityTests(unittest.TestCase):
    def setup_images(self,root):
        square=root/"PromosFootage/Test/game_logo.png";square.parent.mkdir(parents=True)
        Image.new("RGBA",(40,20),"green").save(square)
        clean=Image.new("RGBA",(40,20),(0,0,0,0));clean.paste("white",(3,3,37,17))
        return square,clean

    def test_clean_cached_official_beats_opaque_library_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);square,clean=self.setup_images(root)
            logo=logo_directory("Test",root)/"official.png";logo.parent.mkdir(parents=True);clean.save(logo)
            self.assertEqual(resolve_game_logo("Test",root,False),logo)

    def test_download_attempt_precedes_rectangular_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);square,clean=self.setup_images(root);buffer=io.BytesIO();clean.save(buffer,format="PNG")
            search=Mock();search.json.return_value={"items":[{"id":123,"name":"Test"}]}
            download=Mock();download.content=buffer.getvalue()
            with patch("requests.get",side_effect=[search,download]) as get:
                result=resolve_game_logo("Test",root)
                self.assertNotEqual(result,square);self.assertEqual(result,logo_directory("Test",root)/"official.png")
                self.assertEqual(get.call_count,2)

    def test_opaque_fallback_remains_available_when_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);square,_=self.setup_images(root)
            with patch("requests.get",side_effect=OSError("offline")):
                self.assertEqual(resolve_game_logo("Test",root),square)

if __name__=="__main__":unittest.main()
