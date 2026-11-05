"""Step 4 cut_start_mode='best_moment': start inside the shot after fades/frozen openings,
never leaving the shot and never changing durations or the unique-shot coverage."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import promo_v3_engine as engine

ROOT = Path(__file__).resolve().parents[1]
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
SCRIPT = "Here's a new Steam game. Build a fortress. Defend it from waves. Then raid your friends."


def make_source(path, lead="black", lead_s=1.0, body_s=5.0):
    """lead_s of black/white/frozen frames followed by moving test pattern."""
    if not lead:
        subprocess.run([engine.FFMPEG_CMD, "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"testsrc2=s=320x180:r=30:d={body_s}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                       check=True, capture_output=True)
        return
    if lead == "frozen":
        lead_src = f"testsrc2=s=320x180:r=30:d=0.04,loop=loop={int(lead_s * 30)}:size=1:start=0,setpts=N/30/TB"
    else:
        lead_src = f"color=c={lead}:s=320x180:r=30:d={lead_s}"
    graph = (f"{lead_src}[a];testsrc2=s=320x180:r=30:d={body_s}[b];"
             f"[a]format=yuv420p,setsar=1[a1];[b]format=yuv420p,setsar=1[b1];[a1][b1]concat=n=2:v=1:a=0[v]")
    subprocess.run([engine.FFMPEG_CMD, "-v", "error", "-y", "-filter_complex", graph, "-map", "[v]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)


@unittest.skipUnless(HAS_FFMPEG, "FFmpeg required")
class BestMomentOffsetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def shot(self, video, start=0.0, end=6.0, **extra):
        return dict(shot_id=1, start=start, end=end, duration=end - start, video_path=str(video), **extra)

    def test_black_fade_in_is_skipped(self):
        video = self.root / "black_lead.mp4"
        make_source(video, "black", 1.0)
        offset, reason = engine._choose_cut_start_offset(self.shot(video), 3.0, video)
        self.assertAlmostEqual(offset, 1.0, delta=0.15)
        self.assertIn("black", reason)
        self.assertLessEqual(offset + 3.0, 6.0)

    def test_white_flash_opening_is_skipped(self):
        video = self.root / "white_lead.mp4"
        make_source(video, "white", 0.6)
        offset, reason = engine._choose_cut_start_offset(self.shot(video, end=5.6), 3.0, video)
        self.assertAlmostEqual(offset, 0.6, delta=0.15)
        self.assertIn("white", reason)

    def test_frozen_opening_followed_by_motion_is_skipped(self):
        video = self.root / "frozen_lead.mp4"
        make_source(video, "frozen", 0.8)
        offset, reason = engine._choose_cut_start_offset(self.shot(video, end=5.8), 3.0, video)
        self.assertGreaterEqual(offset, 0.5)
        self.assertLessEqual(offset, 1.0)
        self.assertIn("static", reason)

    def test_offset_never_leaves_the_shot(self):
        video = self.root / "long_black.mp4"
        make_source(video, "black", 0.6)
        # Only 0.5 s of shot left after the cut: the 0.6 s skip is clamped so the cut
        # still ends inside the shot (with the safety margin).
        offset, _ = engine._choose_cut_start_offset(self.shot(video, end=3.5), 3.0, video)
        self.assertLessEqual(offset + 3.0, 3.5 - engine._BEST_MOMENT_SAFETY_MARGIN_S + 1e-6)
        self.assertGreater(offset, 0.0)

    def test_clean_opening_and_unusable_inputs_fall_back_to_shot_start(self):
        video = self.root / "clean.mp4"
        make_source(video, None, body_s=6.0)
        self.assertEqual(engine._choose_cut_start_offset(self.shot(video), 3.0, video), (0.0, "shot_start"))
        self.assertEqual(engine._choose_cut_start_offset(self.shot(video, end=3.0), 3.0, video), (0.0, "no_slack"))
        missing = self.root / "missing.mp4"
        self.assertEqual(engine._choose_cut_start_offset(self.shot(missing), 3.0, missing), (0.0, "fallback_no_video"))
        with patch.object(engine, "_analyze_shot_opening", side_effect=RuntimeError("decoder")):
            self.assertEqual(engine._choose_cut_start_offset(self.shot(video), 3.0, video)[1], "fallback_analysis_failed")

    def test_peak_hint_is_used_when_present(self):
        video = self.root / "clean.mp4"
        make_source(video, None)
        offset, reason = engine._choose_cut_start_offset(self.shot(video, peak_offset_s=2.0), 3.0, video)
        self.assertEqual(reason, "peak_hint")
        self.assertAlmostEqual(offset, 1.1, places=3)


@unittest.skipUnless(HAS_FFMPEG, "FFmpeg required")
class BestMomentEdlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.video = self.root / "trailer.mp4"
        # Each 6 s shot starts with 1 s of black (fade-in) followed by gameplay.
        parts = []
        for i in range(6):
            part = self.root / f"part{i}.mp4"
            make_source(part, "black", 1.0, body_s=5.0)
            parts.append(part)
        concat = self.root / "parts.txt"
        concat.write_text("".join(engine._concat_list_entry(p) for p in parts), encoding="utf-8")
        subprocess.run([engine.FFMPEG_CMD, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
                        "-c", "copy", str(self.video)], check=True, capture_output=True)
        self.shots = [dict(shot_id=i + 1, start=i * 6.0, end=i * 6.0 + 6.0, duration=6.0, video_path=str(self.video),
                           viral_score=90 - i, format_score=80, description=f"Gameplay {i}") for i in range(6)]

    def edl(self, mode):
        with patch.dict(engine.os.environ, {"GROQ_API_KEY": ""}):
            return engine.orchestrate_promov3_step4_edl(self.shots, SCRIPT, voice_duration=12.0, hook_duration=2.0,
                                                        cut_start_mode=mode)

    def test_best_moment_keeps_durations_order_and_bounds(self):
        base = self.edl("shot_start")
        best = self.edl("best_moment")
        self.assertEqual(base["cut_start_mode"], "shot_start")
        self.assertEqual(best["cut_start_mode"], "best_moment")
        self.assertEqual([c["shot_id"] for c in base["timeline"]], [c["shot_id"] for c in best["timeline"]])
        self.assertEqual([c["duration"] for c in base["timeline"]], [c["duration"] for c in best["timeline"]])
        self.assertAlmostEqual(sum(c["duration"] for c in best["timeline"]), 12.0, places=2)
        self.assertEqual(len({c["shot_id"] for c in best["timeline"]}), len(best["timeline"]))
        shots = {s["shot_id"]: s for s in self.shots}
        shifted = 0
        for cut in best["timeline"]:
            shot = shots[cut["shot_id"]]
            self.assertGreaterEqual(cut["source_in"], shot["start"])
            self.assertLessEqual(cut["source_in"] + cut["duration"], shot["end"] + 1e-6)
            self.assertAlmostEqual(cut["source_out"], cut["source_in"] + cut["duration"], places=3)
            self.assertIn("source_offset_reason", cut)
            if cut["source_offset_s"] > 0:
                shifted += 1
                self.assertIn("black", cut["source_offset_reason"])
        self.assertGreater(shifted, 0)
        # The default mode keeps the historical EDL untouched.
        for cut in base["timeline"]:
            self.assertEqual(cut["source_in"], shots[cut["shot_id"]]["start"])
            self.assertNotIn("source_offset_reason", cut)

    def test_unknown_mode_falls_back_to_shot_start(self):
        edl = self.edl("whatever")
        self.assertEqual(edl["cut_start_mode"], "shot_start")


@unittest.skipUnless(HAS_FFMPEG, "FFmpeg required")
class BestMomentRenderTests(unittest.TestCase):
    """The render re-times hook cuts (balanced split, single cut, xfade tails). A best_moment
    start must then move back inside its shot instead of showing frames of the next shot."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_rebalanced_hook_cut_never_runs_past_its_shot(self):
        src = self.root / "src.mp4"
        # shot 1: 0-6 s, shot 2: 6-8 s (0.6 s black opening), shot 3: 8-14 s.
        graph = ("testsrc2=s=160x90:r=30:d=6[a];color=c=black:s=160x90:r=30:d=0.6[b];"
                 "testsrc=s=160x90:r=30:d=1.4[c];color=c=red:s=160x90:r=30:d=6[d];"
                 "[a]format=yuv420p,setsar=1[a1];[b]format=yuv420p,setsar=1[b1];[c]format=yuv420p,setsar=1[c1];"
                 "[d]format=yuv420p,setsar=1[d1];[a1][b1][c1][d1]concat=n=4:v=1:a=0[v]")
        subprocess.run([engine.FFMPEG_CMD, "-v", "error", "-y", "-filter_complex", graph, "-map", "[v]",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True, capture_output=True)
        shots = [dict(shot_id=1, start=0.0, end=6.0, duration=6.0, video_path=str(src)),
                 dict(shot_id=2, start=6.0, end=8.0, duration=2.0, video_path=str(src)),
                 dict(shot_id=3, start=8.0, end=14.0, duration=6.0, video_path=str(src))]
        # Step 4 fills a 4.5 s hook greedily (3.0 + 1.5); the render balances it (2.5 + 2.0).
        timeline = [
            {"order": 1, "shot_id": 1, "role": "hook", "duration": 3.0, "source_in": 0.0, "source_out": 3.0,
             "video_path": str(src), "transition_out": {"type": "cut", "duration": 0.0}},
            {"order": 2, "shot_id": 2, "role": "hook", "duration": 1.5, "source_in": 6.0, "source_out": 7.5,
             "video_path": str(src), "transition_out": {"type": "white_flash", "duration": 0.15}},
            {"order": 3, "shot_id": 3, "role": "gameplay", "duration": 3.0, "source_in": 8.0, "source_out": 11.0,
             "video_path": str(src), "transition_out": {"type": "cut", "duration": 0.0}},
        ]
        engine._apply_best_moment_starts(timeline, {s["shot_id"]: s for s in shots})
        self.assertGreater(timeline[1]["source_offset_s"], 0.0)  # the black opening of shot 2 was skipped
        edl = {"script_mode": "normal", "total_duration": 7.5, "sentence_1_end_s": 4.5, "hook_duration": 4.5,
               "timeline": timeline, "script_text": "x", "cut_start_mode": "best_moment"}
        sess = self.root / "temp" / "v3_cache" / "sbm"
        sess.mkdir(parents=True)
        (sess / "step2_evaluated_shots.json").write_text(json.dumps({"shots": shots}), encoding="utf-8")
        voice = self.root / "voice.wav"
        subprocess.run([engine.FFMPEG_CMD, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=7.5:sample_rate=48000",
                        "-af", "volume=0.2", str(voice)], check=True, capture_output=True)
        calls = []
        real = engine._extract_subclip

        def record(src_video, start_time, duration, vf, out_path, has_audio):
            calls.append((round(float(start_time), 3), round(float(start_time) + float(duration), 3)))
            return real(src_video, start_time, duration, vf, out_path, has_audio)

        with patch.object(engine, "BASE_DIR", self.root), patch.object(engine, "_extract_subclip", side_effect=record):
            engine.render_promov3_final_video(edl, src, voice, None, self.root / "output", temp_dir=self.root / "rt",
                                              enable_steam_hook=False, session_id="sbm")
        self.assertEqual(len(calls), 3)
        for (start, end), shot in zip(calls, shots):
            self.assertGreaterEqual(start, shot["start"] - 1e-6)
            self.assertLessEqual(end, shot["end"] + 1e-6, f"cut extracted past the end of shot {shot['shot_id']}")

    def test_refit_only_moves_best_moment_starts_back_to_the_shot_start(self):
        cut = {"shot_id": 2, "source_in": 6.4, "duration": 1.5, "source_offset_s": 0.4, "source_offset_reason": "skip_black_opening"}
        engine._note_best_moment_shot_end(cut, {2: {"shot_id": 2, "start": 6.0, "end": 8.0, "duration": 2.0}})
        engine._fit_best_moment_cut_in_shot(cut, 1.5)  # fits: untouched
        self.assertEqual(cut["source_in"], 6.4)
        engine._fit_best_moment_cut_in_shot(cut, 1.8)  # needs 0.2 s more: partial refit
        self.assertAlmostEqual(cut["source_in"], 6.2, places=3)
        self.assertAlmostEqual(cut["source_offset_s"], 0.2, places=3)
        engine._fit_best_moment_cut_in_shot(cut, 5.0)  # never before the shot start
        self.assertAlmostEqual(cut["source_in"], 6.0, places=3)
        self.assertEqual(cut["source_offset_s"], 0.0)
        # A stale offset (bigger than the real distance to the shot start) never moves
        # the start into the previous shot.
        stale = {"shot_id": 2, "source_in": 6.1, "duration": 1.5, "source_offset_s": 0.9}
        engine._note_best_moment_shot_end(stale, {2: {"shot_id": 2, "start": 6.0, "end": 8.0, "duration": 2.0}})
        engine._fit_best_moment_cut_in_shot(stale, 3.0)
        self.assertAlmostEqual(stale["source_in"], 6.0, places=3)
        plain = {"shot_id": 1, "source_in": 0.0, "duration": 3.0, "source_offset_s": 0.0}
        engine._note_best_moment_shot_end(plain, {})
        engine._fit_best_moment_cut_in_shot(plain, 9.0)  # shot_start cuts keep the historical behaviour
        self.assertEqual(plain["source_in"], 0.0)
        self.assertNotIn("_shot_end_s", plain)


if __name__ == "__main__":
    unittest.main()
