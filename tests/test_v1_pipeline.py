"""
tests/test_v1_pipeline.py — Verification & Automated Tests for V1 Pipeline

Tests:
1. Schema integrity & timing validations (VideoBreakdown, TimelineEDL).
2. Synthetic video generation and FFmpeg multi-video assembly with 9:16 rendering.
3. Catalog construction in VideoSequencer.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from config import AppConfig
from ffmpeg_assembler import FFmpegAssembler
from schemas_v1 import (
    CutDecision,
    ShotScene,
    SecondData,
    TimelineEDL,
    VideoBreakdown,
    VideoMetadata,
)
from video_sequencer import VideoSequencer
from video_analyzer import (
    detect_final_splash_start,
    get_or_create_720p_proxy,
    probe_video_metadata,
    strip_trailer_final_splash,
)


class TestV1Schemas(unittest.TestCase):
    """Test schemas and timing validation logic."""

    def test_video_breakdown_serialization(self):
        meta = VideoMetadata(
            duration_seconds=12.5,
            width=1920,
            height=1080,
            fps=30.0,
            filename="game_01.mp4",
        )
        scene = ShotScene(
            scene_id=1,
            start_time=0.0,
            end_time=3.2,
            duration=3.2,
            camera_shot_type="wide",
            visual_summary="Car drifting around corner",
            action_intensity=8,
            hook_potential=9,
            audio_description="Tire screech",
            has_onscreen_text=False,
            focal_point="center",
            recommended_role="hook",
        )
        second = SecondData(
            second=0,
            visual_action="Car turns sharply",
            audio_event="Screech",
            visual_energy_score=8,
            audio_energy_score=7,
            tags=["car", "drift"],
        )
        bd = VideoBreakdown(
            source_file="input/game_01.mp4",
            video_title="Parking Game Test",
            overall_summary="Gameplay testing",
            metadata=meta,
            scenes=[scene],
            seconds_breakdown=[second],
            top_hook_scenes=[1],
        )

        # Test dump and load
        dumped = bd.model_dump_json()
        loaded = VideoBreakdown.model_validate_json(dumped)
        self.assertEqual(loaded.metadata.duration_seconds, 12.5)
        self.assertEqual(len(loaded.scenes), 1)
        self.assertEqual(loaded.scenes[0].hook_potential, 9)

    def test_timeline_edl_timing_validation(self):
        cut1 = CutDecision(
            order=1,
            source_video="game_01.mp4",
            source_in=0.0,
            source_out=3.0,
            duration=3.0,
            purpose="hook",
            description="Awesome hook",
            framing_mode="blur_bg",
        )
        cut2 = CutDecision(
            order=2,
            source_video="game_02.mp4",
            source_in=5.0,
            source_out=9.0,
            duration=4.0,
            purpose="climax",
            description="Big crash",
            framing_mode="blur_bg",
        )
        edl = TimelineEDL(
            title="Test Short",
            concept_summary="Testing retention flow",
            target_duration=7.0,
            total_duration=0.0,
            aspect_ratio="9:16",
            cuts=[cut1, cut2],
        )

        edl.validate_timings()
        self.assertEqual(edl.total_duration, 7.0)

        # Invalidate timing: source_out <= source_in
        cut1.source_out = 0.0
        with self.assertRaises(ValueError):
            edl.validate_timings()

    def test_strip_trailer_final_splash(self):
        """Test detection and exclusion of the final marketing/title splash card."""
        meta = VideoMetadata(
            duration_seconds=90.0,
            width=1920,
            height=1080,
            fps=30.0,
            filename="game_trailer.mp4",
        )
        scene1 = ShotScene(
            scene_id=1,
            start_time=0.0,
            end_time=40.0,
            duration=40.0,
            visual_summary="Intense combat gameplay",
            recommended_role="gameplay",
        )
        scene2 = ShotScene(
            scene_id=2,
            start_time=40.0,
            end_time=78.0,
            duration=38.0,
            visual_summary="Boss battle climax",
            recommended_role="climax",
        )
        scene_splash = ShotScene(
            scene_id=3,
            start_time=78.0,
            end_time=90.0,
            duration=12.0,
            visual_summary="Game Title Logo Card with Steam Wishlist and Release Date",
            recommended_role="outro",
            has_onscreen_text=True,
        )
        bd = VideoBreakdown(
            source_file="input/game_trailer.mp4",
            video_title="Game Trailer",
            overall_summary="Gameplay trailer",
            metadata=meta,
            scenes=[scene1, scene2, scene_splash],
            seconds_breakdown=[],
            top_hook_scenes=[1],
        )

        splash_time = detect_final_splash_start(bd)
        self.assertEqual(splash_time, 78.0)

        clean_bd, stripped_time = strip_trailer_final_splash(bd)
        self.assertEqual(stripped_time, 78.0)
        self.assertEqual(clean_bd.metadata.duration_seconds, 78.0)
        self.assertEqual(len(clean_bd.scenes), 2)
        # Ensure scene 3 is completely gone
        scene_ids = [s.scene_id for s in clean_bd.scenes]
        self.assertNotIn(3, scene_ids)


class TestFFmpegAssembler(unittest.TestCase):
    """Test actual FFmpeg rendering with synthetic clips."""

    @classmethod
    def setUpClass(cls):
        cls.config = AppConfig.from_env()
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="v1_test_"))

        # Create two 4-second synthetic test videos (16:9 1280x720) with audio tone
        cls.vid1 = cls.temp_dir / "synthetic_01.mp4"
        cls.vid2 = cls.temp_dir / "synthetic_02.mp4"

        # Synthetic Video 1: Red screen with 440Hz beep
        cmd1 = [
            cls.config.ffmpeg_path or "ffmpeg",
            "-y",
            "-f", "lavfi", "-i", "color=c=red:s=1280x720:d=4:r=30",
            "-f", "lavfi", "-i", "sine=f=440:d=4",
            "-c:v", "libx264", "-c:a", "aac",
            str(cls.vid1),
        ]
        subprocess.run(cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        # Synthetic Video 2: Blue screen with 880Hz beep
        cmd2 = [
            cls.config.ffmpeg_path or "ffmpeg",
            "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=1280x720:d=4:r=30",
            "-f", "lavfi", "-i", "sine=f=880:d=4",
            "-c:v", "libx264", "-c:a", "aac",
            str(cls.vid2),
        ]
        subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    def test_assemble_edl_blur_bg(self):
        """Test multi-video assembly with blurred background."""
        cut1 = CutDecision(
            order=1,
            source_video=str(self.vid1),
            source_in=0.5,
            source_out=2.5,
            duration=2.0,
            purpose="hook",
            description="Red clip hook",
            framing_mode="blur_bg",
        )
        cut2 = CutDecision(
            order=2,
            source_video=str(self.vid2),
            source_in=1.0,
            source_out=3.0,
            duration=2.0,
            purpose="climax",
            description="Blue clip climax",
            framing_mode="blur_bg",
        )
        edl = TimelineEDL(
            title="Synthetic Montage",
            target_duration=4.0,
            total_duration=4.0,
            aspect_ratio="9:16",
            cuts=[cut1, cut2],
        )

        assembler = FFmpegAssembler(
            config=self.config,
            temp_dir=self.temp_dir / "temp",
            output_width=1080,
            output_height=1920,
            target_fps=30,
        )
        output_file = self.temp_dir / "output_blur.mp4"
        assembler.assemble_edl(
            edl=edl,
            search_dirs=[self.temp_dir],
            output_path=output_file,
            override_framing="blur_bg",
        )
        self.assertTrue(output_file.exists())
        self.assertGreater(output_file.stat().st_size, 1000)

    def test_assemble_edl_fit_16_9(self):
        """Test 16:9 1080p multi-video assembly with fit_16_9."""
        cut1 = CutDecision(
            order=1,
            source_video=str(self.vid1),
            source_in=0.0,
            source_out=3.0,
            duration=3.0,
            purpose="hook",
            description="Red clip hook (3.0s)",
            framing_mode="fit_16_9",
        )
        cut2 = CutDecision(
            order=2,
            source_video=str(self.vid2),
            source_in=0.5,
            source_out=2.5,
            duration=2.0,
            purpose="climax",
            description="Blue clip climax (2.0s)",
            framing_mode="fit_16_9",
        )
        edl = TimelineEDL(
            title="Synthetic 16:9 Montage",
            target_duration=5.0,
            total_duration=5.0,
            aspect_ratio="16:9",
            cuts=[cut1, cut2],
        )

        assembler = FFmpegAssembler(
            config=self.config,
            temp_dir=self.temp_dir / "temp",
            output_width=1920,
            output_height=1080,
            target_fps=30,
        )
        output_file = self.temp_dir / "output_fit_16_9.mp4"
        assembler.assemble_edl(
            edl=edl,
            search_dirs=[self.temp_dir],
            output_path=output_file,
            override_framing="fit_16_9",
        )

        self.assertTrue(output_file.exists())
        self.assertGreater(output_file.stat().st_size, 1000)

        # Verify resolution is 1920x1080 via ffprobe
        probe_cmd = [
            self.config.ffprobe_path or "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            str(output_file),
        ]
        res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        w, h = [int(x.strip()) for x in res.stdout.strip().split(",")]
        self.assertEqual(w, 1920)
        self.assertEqual(h, 1080)

    def test_sanitize_black_frames(self):
        """Test that black transition frames are cleanly detected and avoided."""
        # Create a 4s video with 1s black at start, 2s green, 1s black at end
        black_vid = self.temp_dir / "synthetic_black_transitions.mp4"
        cmd = [
            self.config.ffmpeg_path or "ffmpeg",
            "-y",
            "-f", "lavfi", "-i", "color=c=black:s=640x360:d=1:r=30",
            "-f", "lavfi", "-i", "color=c=green:s=640x360:d=2:r=30",
            "-f", "lavfi", "-i", "color=c=black:s=640x360:d=1:r=30",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[outv]",
            "-map", "[outv]",
            "-c:v", "libx264",
            str(black_vid),
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        assembler = FFmpegAssembler(config=self.config)
        # Pass a cut that starts at 0.0 (which is black!)
        san_in, san_out = assembler.sanitize_cut_boundaries(
            source_path=black_vid,
            source_in=0.0,
            source_out=3.5,
            is_hook=False,
        )
        # Should have stepped forward past 0.0 into the green portion
        self.assertGreater(san_in, 0.0)


class TestProxyGeneration(unittest.TestCase):
    """Test 720p proxy generation, cache reuse, and resolution pass-through."""

    def setUp(self):
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_dir_obj.name)
        self.config = AppConfig(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", temp_dir=self.temp_dir)

    def tearDown(self):
        self.temp_dir_obj.cleanup()

    def test_proxy_generation_from_1080p(self):
        # Create a 1080p synthetic video (1920x1080, 2s)
        source_vid = self.temp_dir / "test_1080p.mp4"
        cmd = [
            self.config.ffmpeg_path,
            "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=1920x1080:d=2:r=30",
            "-c:v", "libx264",
            str(source_vid),
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        # Generate proxy
        proxy = get_or_create_720p_proxy(
            video_path=source_vid,
            temp_dir=self.temp_dir,
            ffmpeg_bin=self.config.ffmpeg_path,
            target_height=720,
        )

        self.assertTrue(proxy.exists())
        self.assertNotEqual(proxy, source_vid)

        # Verify proxy dimensions (height should be 720, width 1280)
        meta = probe_video_metadata(proxy)
        self.assertEqual(meta.height, 720)
        self.assertEqual(meta.width, 1280)

        # Test cache reuse: calling again should return the exact same proxy without errors
        proxy2 = get_or_create_720p_proxy(
            video_path=source_vid,
            temp_dir=self.temp_dir,
            ffmpeg_bin=self.config.ffmpeg_path,
            target_height=720,
        )
        self.assertEqual(proxy, proxy2)

    def test_proxy_passthrough_when_already_small(self):
        # Create a 360p video (640x360, 2s)
        source_vid = self.temp_dir / "test_360p.mp4"
        cmd = [
            self.config.ffmpeg_path,
            "-y",
            "-f", "lavfi", "-i", "color=c=red:s=640x360:d=2:r=30",
            "-c:v", "libx264",
            str(source_vid),
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        # Should return original path directly because min(width, height) <= 720
        proxy = get_or_create_720p_proxy(
            video_path=source_vid,
            temp_dir=self.temp_dir,
            ffmpeg_bin=self.config.ffmpeg_path,
            target_height=720,
        )
        self.assertEqual(proxy, source_vid)


if __name__ == "__main__":
    unittest.main()
