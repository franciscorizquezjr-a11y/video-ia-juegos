"""
test_retention_cv.py — Unit tests for Retention Analysis CV Engine and Endpoints
"""

import unittest
from pathlib import Path
from retention_analyzer import (
    analyze_retention_screenshot,
    analyze_reels_retention,
    analyze_youtube_retention,
    analyze_tiktok_retention,
    combine_multi_platform_retention,
    auto_detect_platform_type,
)


class TestRetentionCV(unittest.TestCase):
    def setUp(self):
        self.sample_dir = Path("static/retention_samples")
        self.reels_path = self.sample_dir / "reels_sample.jpg"
        self.yt_path = self.sample_dir / "youtube_sample.jpg"
        self.tt_path = self.sample_dir / "tiktok_sample.jpg"

    def test_platform_classification(self):
        self.assertEqual(auto_detect_platform_type(self.reels_path), "reels")
        self.assertEqual(auto_detect_platform_type(self.yt_path), "youtube")
        self.assertEqual(auto_detect_platform_type(self.tt_path), "tiktok")

    def test_reels_retention_extraction(self):
        res = analyze_reels_retention(self.reels_path, manual_duration=26.0)
        self.assertTrue(res["detected"])
        self.assertEqual(res["platform"], "reels")
        self.assertEqual(res["duration_seconds"], 26.0)
        self.assertIn("100%", res["grid_lines"])
        self.assertIn("0%", res["grid_lines"])
        self.assertEqual(len(res["retention_per_second"]), 27)  # 0s to 26s inclusive
        
        # Check initial hook retention (~99%) and final drop (< 5%)
        pts = res["retention_per_second"]
        self.assertGreater(pts[0]["retention_pct"], 90.0)
        self.assertLess(pts[-1]["retention_pct"], 10.0)
        self.assertIn("debug_image", res)
        self.assertTrue(res["debug_image"].startswith("data:image/jpeg;base64,"))

    def test_youtube_retention_extraction(self):
        res = analyze_youtube_retention(self.yt_path, manual_duration=29.0)
        self.assertTrue(res["detected"])
        self.assertEqual(res["platform"], "youtube")
        self.assertEqual(res["duration_seconds"], 29.0)
        self.assertEqual(len(res["retention_per_second"]), 30)  # 0s to 29s inclusive
        
        # YouTube shorts starts > 100% due to loops
        pts = res["retention_per_second"]
        self.assertGreater(pts[0]["retention_pct"], 100.0)
        self.assertLess(pts[-1]["retention_pct"], 35.0)
        self.assertIn("debug_image", res)

    def test_tiktok_retention_extraction(self):
        res = analyze_tiktok_retention(self.tt_path, manual_duration=31.0)
        self.assertTrue(res["detected"])
        self.assertEqual(res["platform"], "tiktok")
        self.assertEqual(res["duration_seconds"], 31.0)
        self.assertEqual(len(res["retention_per_second"]), 32)  # 0s to 31s inclusive
        
        pts = res["retention_per_second"]
        self.assertGreater(pts[0]["retention_pct"], 80.0)
        self.assertLess(pts[-1]["retention_pct"], 15.0)
        self.assertIn("debug_image", res)

    def test_combination_subsets(self):
        r_yt = analyze_youtube_retention(self.yt_path, manual_duration=29.0)
        r_tt = analyze_tiktok_retention(self.tt_path, manual_duration=31.0)
        
        # Test 2 platforms combined (YouTube + TikTok)
        combined = combine_multi_platform_retention({"youtube": r_yt, "tiktok": r_tt})
        self.assertEqual(combined["max_duration_seconds"], 31)
        self.assertEqual(len(combined["comparison_by_second"]), 32)
        
        first_sec = combined["comparison_by_second"][0]
        self.assertIn("youtube", first_sec)
        self.assertIn("tiktok", first_sec)
        self.assertNotIn("reels", first_sec)
        self.assertIsNotNone(first_sec["average"])


if __name__ == "__main__":
    unittest.main()
