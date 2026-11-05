"""Portable font resolver: Windows keeps its fonts, other OSes get real fallbacks."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fonts


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a real font")
    return path


class FontResolverTests(unittest.TestCase):
    def setUp(self):
        fonts.clear_font_cache()
        self.addCleanup(fonts.clear_font_cache)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def windows_env(self, windir, local_app_data=""):
        return patch.dict(os.environ, {"WINDIR": str(windir), "LOCALAPPDATA": str(local_app_data)})

    def test_windows_fonts_folder_wins_in_caller_order(self):
        windir = self.root / "Windows"
        impact = touch(windir / "Fonts" / "impact.ttf")
        ariblk = touch(windir / "Fonts" / "ariblk.ttf")
        with self.windows_env(windir):
            self.assertEqual(fonts.find_font(["impact.ttf", "ariblk.ttf"], bold=True), str(impact))
            self.assertEqual(fonts.find_font(["ariblk.ttf", "impact.ttf"], bold=True), str(ariblk))
            impact.unlink()
            self.assertEqual(fonts.find_font(["impact.ttf", "ariblk.ttf"], bold=True), str(ariblk))

    def test_windows_font_beats_dejavu_and_system_fonts(self):
        windir = self.root / "Windows"
        segoe = touch(windir / "Fonts" / "segoeuib.ttf")
        with self.windows_env(windir):
            self.assertEqual(fonts.find_font(["segoeuib.ttf"], bold=True), str(segoe))
            self.assertEqual(next(fonts.iter_font_candidates(["segoeuib.ttf"], bold=True)), str(segoe))

    def test_system_windows_folder_keeps_priority_over_per_user_fonts(self):
        windir = self.root / "Windows"
        user_fonts = self.root / "Local" / "Microsoft" / "Windows" / "Fonts"
        impact = touch(windir / "Fonts" / "impact.ttf")
        user_only = touch(user_fonts / "ariblk.ttf")
        with self.windows_env(windir, self.root / "Local"):
            # Historic behaviour: first match inside C:\Windows\Fonts wins.
            self.assertEqual(fonts.find_font(["ariblk.ttf", "impact.ttf"]), str(impact))
            impact.unlink()
            self.assertEqual(fonts.find_font(["ariblk.ttf", "impact.ttf"]), str(user_only))

    @unittest.skipIf(os.name == "nt", "WINDIR always applies on Windows")
    def test_no_windows_folders_without_windir_off_windows(self):
        env = {k: v for k, v in os.environ.items() if k.upper() not in ("WINDIR", "LOCALAPPDATA")}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(fonts.windows_font_dirs(), [])

    def test_unix_lookup_is_recursive_and_case_insensitive(self):
        hit = touch(self.root / "truetype" / "msttcorefonts" / "Impact.TTF")
        self.assertEqual(fonts.find_font(["impact.ttf"], search_dirs=[self.root], include_fallback=False), str(hit))
        self.assertEqual(fonts.find_font("Impact", search_dirs=[self.root], include_fallback=False), str(hit))

    def test_metric_compatible_alias_only_after_exact_names(self):
        alias = touch(self.root / "liberation" / "LiberationSans-Bold.ttf")
        self.assertEqual(fonts.find_font(["arialbd.ttf"], bold=True, search_dirs=[self.root]), str(alias))
        exact = touch(self.root / "ms" / "arialbd.ttf")
        fonts.clear_font_cache()
        self.assertEqual(fonts.find_font(["arialbd.ttf"], bold=True, search_dirs=[self.root]), str(exact))

    def test_dejavu_fallback_respects_bold(self):
        regular = touch(self.root / "dejavu" / "DejaVuSans.ttf")
        bold = touch(self.root / "dejavu" / "DejaVuSans-Bold.ttf")
        self.assertEqual(fonts.find_font(["missing.ttf"], bold=True, search_dirs=[self.root]), str(bold))
        self.assertEqual(fonts.find_font(["missing.ttf"], bold=False, search_dirs=[self.root]), str(regular))
        self.assertIsNone(fonts.find_font(["missing.ttf"], search_dirs=[self.root], include_fallback=False))

    def test_nothing_installed_returns_none_and_sized_default_font(self):
        self.assertIsNone(fonts.find_font(["missing.ttf"], search_dirs=[self.root]))
        font = fonts.load_font(["missing.ttf"], 40, bold=True, search_dirs=[self.root])
        box = font.getbbox("NEW STEAM GAME")
        self.assertGreater(box[2] - box[0], 0)
        if hasattr(font, "size"):  # Pillow >= 10.1 sizes its default font
            self.assertEqual(font.size, 40)

    def test_unreadable_candidates_are_skipped(self):
        touch(self.root / "impact.ttf")  # fake file: truetype() raises OSError
        font = fonts.load_font(["impact.ttf"], 32, search_dirs=[self.root])
        self.assertIsNotNone(font.getbbox("GAME NAME"))

    def test_absolute_path_is_used_first(self):
        custom = touch(self.root / "brand" / "MyBrand.ttf")
        touch(self.root / "fonts" / "impact.ttf")
        self.assertEqual(fonts.find_font([str(custom), "impact.ttf"], search_dirs=[self.root / "fonts"]), str(custom))

    def test_real_fallback_font_renders_at_requested_size(self):
        path = fonts.find_font(["definitely-missing-font.ttf"], bold=True)
        if not path:
            self.skipTest("No DejaVu font installed on this machine")
        font = fonts.load_font(["definitely-missing-font.ttf"], 64, bold=True)
        self.assertEqual(getattr(font, "path", path), path)
        self.assertGreater(font.getbbox("NEW")[3], 30)


if __name__ == "__main__":
    unittest.main()
