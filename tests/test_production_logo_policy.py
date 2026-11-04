import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from production_logo_policy import require_game_logo

class ProductionLogoPolicyTests(unittest.TestCase):
    def test_missing_logo_blocks_without_creating_text_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaisesRegex(ValueError,"Falta el logo oficial"):
                require_game_logo("Missing Game",root=root,allow_download=False)
            self.assertEqual(list(root.rglob("*.png")),[])

    def test_matching_library_logo_survives_unreadable_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);logo=root/"PromosFootage/Target/game_logo.png"
            logo.parent.mkdir(parents=True);Image.new("RGBA",(80,30),"red").save(logo)
            self.assertEqual(require_game_logo("Target",root=root,allow_download=False),logo.resolve())
            with self.assertRaisesRegex(ValueError,"Falta el logo oficial"):
                require_game_logo("Other Game",root=root,allow_download=False)

    def test_damaged_custom_logo_does_not_silently_select_another(self):
        with tempfile.TemporaryDirectory() as tmp:
            logo=Path(tmp)/"broken.png";logo.write_bytes(b"broken image")
            with patch("production_logo_policy.resolve_game_logo") as resolve:
                with self.assertRaisesRegex(ValueError,"dañado"):
                    require_game_logo("Game",logo,root=Path(tmp))
                resolve.assert_not_called()

    def test_empty_name_cannot_bypass_requirement(self):
        with self.assertRaisesRegex(ValueError,"nombre"):
            require_game_logo("")

if __name__=="__main__":unittest.main()
