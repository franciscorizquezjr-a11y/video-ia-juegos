"""Promo V3 render engine: master audio (ducking / loudness), captions safety, fps and
temp-folder isolation, exercised with tiny synthetic media and no external APIs."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import promo_v3_engine as engine

ROOT = Path(__file__).resolve().parents[1]
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def make_video(path, color="red", seconds=4.0, fps=30, audio_freq=None, audio_volume=0.5):
    cmd = [engine.FFMPEG_CMD, "-v", "error", "-y", "-f", "lavfi", "-i",
           f"color=c={color}:s=320x180:r={fps}:d={seconds}"]
    if audio_freq:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={audio_freq}:duration={seconds}:sample_rate=48000",
                "-af", f"volume={audio_volume}", "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    run(cmd)


def make_tone(path, seconds=4.0, freq=440, volume=0.25, expr=None):
    """Mono-ish test tone; expr lets a test carve silent gaps (aevalsrc)."""
    source = expr or f"sine=frequency={freq}:duration={seconds}:sample_rate=48000"
    run([engine.FFMPEG_CMD, "-v", "error", "-y", "-f", "lavfi", "-i", source,
         "-af", f"volume={volume}", "-ar", "48000", str(path)])


def integrated_loudness(path):
    """(integrated LUFS, true peak dBTP) via ebur128."""
    res = subprocess.run([engine.FFMPEG_CMD, "-hide_banner", "-nostats", "-i", str(path),
                          "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    summary = res.stderr[res.stderr.rfind("Summary:"):]
    loud = float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1))
    peak_match = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    peak = float(peak_match.group(1)) if peak_match else 0.0
    return loud, peak


def band_level(path, start, duration, freq):
    """Mean level (dB) of a narrow band around freq in [start, start+duration]."""
    res = subprocess.run([engine.FFMPEG_CMD, "-hide_banner", "-nostats", "-ss", str(start), "-t", str(duration),
                          "-i", str(path), "-af", f"bandpass=f={freq}:width_type=h:w=80,volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return float(re.search(r"mean_volume:\s+(-?[\d.]+) dB", res.stderr).group(1))


def words_for(text, start=0.1, step=0.4):
    out = []
    t = start
    for token in text.split():
        out.append({"word": token, "start": round(t, 3), "end": round(t + step - 0.05, 3)})
        t += step
    return out


def simple_edl(video, seconds_per_cut=(2.0, 2.0)):
    cuts = []
    start = 0.0
    for i, dur in enumerate(seconds_per_cut):
        cuts.append({"shot_id": i + 1, "duration": dur, "source_in": start, "video_path": str(video),
                     "role": "hook" if i == 0 else "gameplay",
                     "transition_out": {"type": "white_flash" if i == 0 else "cut"}})
        start += dur
    return {"script_mode": "caveman", "total_duration": sum(seconds_per_cut),
            "sentence_1_end_s": seconds_per_cut[0], "timeline": cuts,
            "script_text": "Build the tallest tower. Then defend it."}


class PrerenderedCutsMixin:
    """Tests that only inspect overlay/caption/audio decisions reuse one real 1080x1920
    2 s cut (made once with the engine's own extractor) instead of re-encoding each cut."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._clip_tmp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        folder = Path(cls._clip_tmp.name)
        source = folder / "source.mp4"
        make_video(source, "red", 2.0, audio_freq=2000)
        cls._clip = folder / "cut.mp4"
        engine._extract_subclip(source, 0.0, 2.0, "scale=1080:1920,setsar=1,fps=30,format=yuv420p", cls._clip, True)

    @classmethod
    def tearDownClass(cls):
        cls._clip_tmp.cleanup()
        super().tearDownClass()

    def fast_extract(self, src_video, start_time, duration, vf, out_path, has_audio):
        if abs(float(duration) - 2.0) > 1e-6:
            raise AssertionError("prerendered cuts only cover 2 s cuts")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self._clip, out_path)


@unittest.skipUnless(HAS_FFMPEG, "FFmpeg required")
class MasterAudioHelperTests(unittest.TestCase):
    """The shared master chain used by the render and the SFX re-mixer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        # Voice speaks 0-2 s, then a 2 s gap; gameplay is a constant 2 kHz bed.
        self.voice = self.root / "voice.wav"
        make_tone(self.voice, volume=1.0, expr=(
            "aevalsrc=if(lt(t\\,2)\\,0.3*sin(2*PI*440*t)\\,0):s=48000:d=4"))
        self.gameplay = self.root / "gameplay.wav"
        make_tone(self.gameplay, seconds=4, freq=2000, volume=0.5)

    def render(self, name, **kwargs):
        out = self.root / f"{name}.wav"
        work = self.root / f"work_{name}"
        work.mkdir()
        options = dict(duration=4.0, ducking=True, normalize=False)
        options.update(kwargs)
        info = engine._render_master_audio(self.voice, self.gameplay, 0.5, [], out, work, **options)
        self.assertTrue(out.is_file())
        return out, info

    def test_ducking_pulls_gameplay_down_only_while_voice_speaks(self):
        ducked, info = self.render("ducked", ducking=True)
        self.assertTrue(info["ducking_applied"])
        under_voice = band_level(ducked, 0.6, 1.0, 2000)
        in_gap = band_level(ducked, 2.8, 1.0, 2000)
        self.assertLess(under_voice, in_gap - 4.0)

        flat, info_flat = self.render("flat", ducking=False)
        self.assertFalse(info_flat["ducking_applied"])
        self.assertAlmostEqual(band_level(flat, 0.6, 1.0, 2000), band_level(flat, 2.8, 1.0, 2000), delta=1.5)

    def test_normalize_reaches_minus_14_lufs_with_peak_safety(self):
        normalized, info = self.render("norm", normalize=True)
        self.assertTrue(info["normalized"])
        self.assertEqual(info["normalization_mode"], "two_pass")
        self.assertIsNotNone(info["measured_loudness"])
        self.assertFalse(any("modo alternativo" in w for w in info["warnings"]))
        loud, peak = integrated_loudness(normalized)
        self.assertAlmostEqual(loud, -14.0, delta=1.5)
        self.assertLessEqual(peak, -0.5)

    def test_old_ffmpeg_without_limiter_latency_keeps_measured_two_pass(self):
        real = engine._run_ffmpeg_cmd

        def old_ffmpeg(cmd, *args, **kwargs):
            if any("latency=true" in str(part) for part in cmd):
                raise RuntimeError("Option 'latency' not found")
            return real(cmd, *args, **kwargs)

        with patch.object(engine, "_run_ffmpeg_cmd", side_effect=old_ffmpeg):
            normalized, info = self.render("old_ffmpeg", normalize=True)
        self.assertTrue(info["normalized"])
        self.assertEqual(info["normalization_mode"], "two_pass_compat_limiter")
        self.assertTrue(any("modo alternativo" in w for w in info["warnings"]))
        loud, peak = integrated_loudness(normalized)
        self.assertAlmostEqual(loud, -14.0, delta=1.5)
        self.assertLessEqual(peak, -0.5)

    def test_normalize_off_keeps_plain_mix_level(self):
        quiet = self.root / "quiet_voice.wav"
        make_tone(quiet, seconds=4, freq=440, volume=0.05)
        results = {}
        for normalize in (False, True):
            out = self.root / f"quiet_{normalize}.wav"
            work = self.root / f"work_quiet_{normalize}"
            work.mkdir()
            info = engine._render_master_audio(quiet, None, 0.0, [], out, work, duration=4.0, normalize=normalize)
            self.assertEqual(info["normalized"], normalize)
            results[normalize] = integrated_loudness(out)[0]
        self.assertLess(results[False], -24.0)  # untouched quiet mix
        self.assertAlmostEqual(results[True], -14.0, delta=1.5)

    def test_failed_ducking_falls_back_to_fixed_volume_with_warning(self):
        with patch.object(engine, "DUCKING_RATIO", 999):  # out of range for sidechaincompress
            _, info = self.render("fallback", ducking=True)
        self.assertFalse(info["ducking_applied"])
        self.assertTrue(any("ducking" in w for w in info["warnings"]))

    def test_sfx_are_delayed_trimmed_and_mixed(self):
        sfx = self.root / "hit.wav"
        make_tone(sfx, seconds=1.0, freq=5000, volume=0.6)
        out = self.root / "sfx.wav"
        work = self.root / "work_sfx"
        work.mkdir()
        engine._render_master_audio(self.voice, None, 0.0, [{"path": sfx, "start_s": 2.5, "trim_s": 0.2, "volume": 1.0}],
                                    out, work, duration=4.0, ducking=True, normalize=False)
        self.assertLess(band_level(out, 0.5, 1.0, 5000), -60)
        self.assertGreater(band_level(out, 2.6, 0.5, 5000), -40)


@unittest.skipUnless(HAS_FFMPEG, "FFmpeg required")
class RenderIntegrationTests(PrerenderedCutsMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.voice = self.root / "voice.wav"
        make_tone(self.voice, seconds=4.0, freq=440, volume=0.2)

    def render(self, edl, video, temp_dir=None, output_dir=None, alignment_data=None, fast=False, **kwargs):
        options = dict(enable_steam_hook=False, gameplay_volume=0.3, session_id="fixture")
        options.update(kwargs)
        extract = self.fast_extract if fast else engine._extract_subclip
        with patch.object(engine, "BASE_DIR", self.root), patch.object(engine, "_extract_subclip", side_effect=extract):
            return engine.render_promov3_final_video(
                edl, video, self.voice, alignment_data, output_dir or (self.root / "output"),
                temp_dir=temp_dir or (self.root / "render"), **options)

    def test_default_render_normalises_ducks_and_cleans_its_run_folder(self):
        video = self.root / "source.mp4"
        make_video(video, "red", 4.0, audio_freq=2000, audio_volume=0.8)
        checked_clips = []
        real_extract = engine._extract_subclip

        def extract_and_check(*args, **kwargs):
            real_extract(*args, **kwargs)
            streams = json.loads(subprocess.check_output([engine.FFPROBE_CMD, "-v", "error",
                "-show_entries", "stream=codec_type,color_space,r_frame_rate", "-of", "json",
                str(kwargs["out_path"])], text=True))["streams"]
            visual = next(s for s in streams if s["codec_type"] == "video")
            self.assertEqual(visual["color_space"], "bt709")
            self.assertEqual(visual["r_frame_rate"], "30/1")
            checked_clips.append(kwargs["out_path"])

        with patch.object(engine, "_extract_subclip", side_effect=extract_and_check):
            result = self.render(simple_edl(video), video)

        self.assertEqual(len(checked_clips), 2)
        rendered = Path(result["video_path"])
        self.assertTrue(engine.check_media_has_audio(rendered))
        self.assertAlmostEqual(engine.get_media_duration(rendered), 4.0, delta=0.15)
        loud, _ = integrated_loudness(rendered)
        self.assertAlmostEqual(loud, -14.0, delta=1.5)
        self.assertIsInstance(result["warnings"], list)
        self.assertTrue(result["audio_normalized"])
        self.assertTrue(result["audio_ducking_applied"])
        self.assertEqual(result["output_fps"], 30)
        # Unique run folder removed; latest silent video kept at its historical place.
        self.assertEqual(list((self.root / "render").glob("run_*")), [])
        self.assertTrue((self.root / "render" / "silent_video.mp4").is_file())
        session = self.root / "temp" / "v3_cache" / "fixture"
        self.assertTrue((session / "master_audio.mp3").is_file())
        self.assertTrue((session / "gameplay_audio.flac").is_file())
        self.assertEqual(result["gameplay_audio_path"], str(session / "gameplay_audio.flac"))
        audio_codec = subprocess.check_output([engine.FFPROBE_CMD, "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(rendered)], text=True).strip()
        self.assertEqual(audio_codec, "aac")

    def test_audio_options_off_keep_fixed_volume_mix(self):
        video = self.root / "source.mp4"
        make_video(video, "blue", 4.0, audio_freq=2000)
        result = self.render(simple_edl(video), video, audio_normalize=False, audio_ducking=False, fast=True)
        self.assertFalse(result["audio_normalized"])
        self.assertFalse(result["audio_ducking_applied"])
        self.assertTrue(engine.check_media_has_audio(result["video_path"]))
        loud, _ = integrated_loudness(result["video_path"])
        self.assertGreater(abs(loud - (-14.0)), 2.0)

    def test_failed_caption_burn_is_reported_not_silent(self):
        video = self.root / "source.mp4"
        make_video(video, "red", 4.0)
        real_run = engine._run_ffmpeg_cmd

        def fail_ass(cmd, desc="", cwd=None):
            if desc.startswith("Composición de vídeo mudo con subtítulos ASS"):
                raise RuntimeError("libass missing")
            return real_run(cmd, desc, cwd=cwd)

        with patch.object(engine, "_run_ffmpeg_cmd", side_effect=fail_ass):
            result = self.render(simple_edl(video), video, fast=True,
                                 alignment_data={"words": words_for("Build the tallest tower then defend it")})
        self.assertFalse(result["has_subtitles"])
        self.assertTrue(any("subtítulos" in w for w in result["warnings"]))
        self.assertTrue(Path(result["video_path"]).is_file())

    def test_captions_burn_with_apostrophes_brackets_and_non_ascii_in_paths(self):
        name = "it's [demo], ñandú"
        if os.name != "nt":
            name += " a:b"
        base = self.root / name
        video = base / "source's clip.mp4"
        base.mkdir()
        make_video(video, "red", 4.0)
        words = words_for("Build the tallest tower then defend it", start=0.0, step=0.5)
        # Delivered videos must stay under output/ (download URLs); the work folder and
        # the source carry the awkward characters.
        result = self.render(simple_edl(video), video, temp_dir=base / "render tmp",
                             output_dir=self.root / "output" / name, alignment_data={"words": words})
        self.assertTrue(result["has_subtitles"], result["warnings"])
        self.assertFalse(any("subtítulos" in w for w in result["warnings"]))
        # Red background has no green: bright green pixels come from the burnt captions.
        frame = subprocess.run([engine.FFMPEG_CMD, "-v", "error", "-ss", "1.2", "-i", result["video_path"],
                                "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                               check=True, capture_output=True).stdout
        greens = frame[1::3]
        self.assertGreater(sum(1 for g in greens if g > 180), 200)

    def test_output_60_fps_with_60_fps_source(self):
        video = self.root / "source60.mp4"
        make_video(video, "red", 4.0, fps=60)
        result = self.render(simple_edl(video), video, output_fps=60)
        self.assertEqual(result["output_fps"], 60)
        self.assertFalse(any("fps" in w for w in result["warnings"]))
        self.assertAlmostEqual(engine._probe_frame_rate(result["video_path"]), 60.0, delta=0.5)
        self.assertAlmostEqual(engine.get_media_duration(result["video_path"]), 4.0, delta=0.15)

    def test_output_60_fps_falls_back_to_30_for_30_fps_source(self):
        video = self.root / "source30.mp4"
        make_video(video, "red", 4.0, fps=30)
        result = self.render(simple_edl(video), video, output_fps=60)
        self.assertEqual(result["output_fps"], 30)
        self.assertTrue(any("30 fps" in w for w in result["warnings"]))
        self.assertAlmostEqual(engine._probe_frame_rate(result["video_path"]), 30.0, delta=0.5)

    def test_xfade_transition_keeps_60_fps(self):
        video = self.root / "source60.mp4"
        make_video(video, "green", 5.0, fps=60)
        edl = simple_edl(video, (2.0, 1.5, 0.5))
        edl["script_mode"] = "normal"
        edl["timeline"][1]["transition_out"] = {"type": "circle_zoom"}
        edl["sentence_1_end_s"] = 2.0
        with patch("audio_quality.require_narration", return_value={"passed": True}):
            result = self.render(edl, video, output_fps=60)
        self.assertEqual(result["output_fps"], 60)
        self.assertTrue(any(t["type"] == "circle_zoom" for t in result["transitions_applied"]))
        self.assertAlmostEqual(engine._probe_frame_rate(result["video_path"]), 60.0, delta=0.5)

    def test_sfx_remix_reuses_master_chain_and_gameplay_stem(self):
        video = self.root / "source.mp4"
        make_video(video, "red", 4.0)
        result = self.render(simple_edl(video), video, fast=True, gameplay_volume=0.6)
        session = self.root / "temp" / "v3_cache" / "fixture"
        # As saved by the server (_finalize_render_result adds render_options).
        result["render_options"] = {"audio_normalize": True, "audio_ducking": True, "output_fps": 30}
        (session / "step5_result.json").write_text(json.dumps(result), encoding="utf-8")
        (session / "step3_voice.json").write_text(json.dumps({"audio_path": str(self.voice)}), encoding="utf-8")
        sfx_dir = self.root / "static" / "sfx" / "hits"
        sfx_dir.mkdir(parents=True)
        make_tone(sfx_dir / "boom.wav", seconds=0.5, freq=5000, volume=0.8)
        with patch.object(engine, "BASE_DIR", self.root):
            with_bed = engine.remix_promov3_audio_sfx(session, [{"relative_path": "hits/boom.wav", "start_s": 2.5,
                                                                  "volume": 1.0}], gameplay_volume=0.6)
            remixed = Path(with_bed["video_path"])
            bed_level = band_level(remixed, 0.5, 1.5, 2000)
            boom_level = band_level(remixed, 2.55, 0.3, 5000)
            loud, _ = integrated_loudness(remixed)
            without_bed = engine.remix_promov3_audio_sfx(session, [], gameplay_volume=0.0)
        self.assertTrue(with_bed["audio_normalized"])
        self.assertTrue(with_bed["audio_ducking_applied"])
        self.assertAlmostEqual(loud, -14.0, delta=1.5)
        self.assertGreater(boom_level, -45)
        self.assertGreater(bed_level, band_level(without_bed["video_path"], 0.5, 1.5, 2000) + 15)
        self.assertEqual(list((session / "render_tmp").glob("run_*")), [])
        saved = json.loads((session / "step5_result.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["remixed_sfx_tracks"], [])
        # A remix with ducking off is what session recovery restores (render_options too).
        with patch.object(engine, "BASE_DIR", self.root):
            engine.remix_promov3_audio_sfx(session, [], gameplay_volume=0.6, audio_ducking=False)
        saved = json.loads((session / "step5_result.json").read_text(encoding="utf-8"))
        self.assertFalse(saved["audio_ducking"])
        self.assertEqual(saved["render_options"], {"audio_normalize": True, "audio_ducking": False, "output_fps": 30})

    def test_two_renders_of_same_session_use_separate_work_folders(self):
        video = self.root / "source.mp4"
        make_video(video, "red", 4.0)
        seen = []
        real_new = engine._new_render_run_dir

        def spy(base, session):
            path = real_new(base, session)
            seen.append(path)
            return path

        with patch.object(engine, "_new_render_run_dir", side_effect=spy):
            first = self.render(simple_edl(video), video, fast=True)
            second = self.render(simple_edl(video), video, fast=True)
        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0], seen[1])
        self.assertTrue(all("fixture" in p.name for p in seen))
        self.assertNotEqual(first["video_path"], second["video_path"])


@unittest.skipUnless(HAS_FFMPEG, "FFmpeg required")
class OpeningSplashTests(PrerenderedCutsMixin, unittest.TestCase):
    """Splash/caption timing for formula vs direct/custom openings."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.voice = self.root / "voice.wav"
        make_tone(self.voice, seconds=4.0, freq=440, volume=0.2)
        self.video = self.root / "source.mp4"
        make_video(self.video, "red", 4.0)
        self.overlay_calls = []
        self.ass_calls = []

    def fake_overlay(self, output_mov_path, duration, width, height, fps, **kwargs):
        self.overlay_calls.append({"duration": duration, "fps": fps, "mode": kwargs.get("mode")})
        run([engine.FFMPEG_CMD, "-v", "error", "-y", "-f", "lavfi", "-i",
             f"color=c=black:s={width}x{height}:r={fps}:d={duration}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-f", "mov", str(output_mov_path)])
        return Path(output_mov_path)

    def fake_ass(self, **kwargs):
        self.ass_calls.append(kwargs)  # writes nothing: render continues without captions

    def render(self, words, edl_extra=None, **kwargs):
        edl = simple_edl(self.video)
        edl.update(edl_extra or {})
        options = dict(enable_steam_hook=True, gameplay_volume=0.0, session_id="fixture")
        options.update(kwargs)
        with patch.object(engine, "BASE_DIR", self.root), \
             patch.object(engine, "_extract_subclip", side_effect=self.fast_extract), \
             patch("utils.generate_steam_hook_overlay_video", side_effect=self.fake_overlay), \
             patch("audio_aligner.generate_kinetic_ass_subtitles", side_effect=self.fake_ass):
            return engine.render_promov3_final_video(edl, self.video, self.voice, {"words": words},
                                                     self.root / "output", temp_dir=self.root / "render", **options)

    def test_disabled_splash_starts_captions_at_zero(self):
        result = self.render(words_for("This castle builder lets you raid friends"), enable_steam_hook=False)
        self.assertEqual(self.overlay_calls, [])
        self.assertFalse(self.ass_calls[0]["skip_steam_hook"])
        self.assertEqual(self.ass_calls[0]["intro_duration"], 0.0)
        self.assertFalse(result["has_steam_hook"])

    def test_direct_opening_keeps_short_splash_in_modern_mode(self):
        # "This game ..." would be taken as a Steam prefix by the loose matcher.
        words = words_for("This game lets you tame dragons and raid castles", step=0.4)
        self.render(words, steam_hook_mode="subtitles_above")
        self.assertAlmostEqual(self.overlay_calls[0]["duration"], 1.8, places=3)
        self.assertEqual(self.ass_calls[0]["intro_duration"], 1.8)

    def test_formula_opening_keeps_prefix_timing_in_modern_mode(self):
        words = words_for("Here's a new Steam game where you tame dragons", step=0.5)
        self.render(words, steam_hook_mode="subtitles_above")
        prefix_end = words[4]["end"]
        self.assertAlmostEqual(self.overlay_calls[0]["duration"], max(1.2, prefix_end), places=3)

    def test_classic_splash_hides_only_its_own_second(self):
        result = self.render(words_for("Raid castles with your friends tonight"), steam_hook_mode="classic")
        self.assertEqual(self.overlay_calls[0]["duration"], 1.0)
        self.assertTrue(self.ass_calls[0]["skip_steam_hook"])
        self.assertEqual(self.ass_calls[0]["intro_duration"], 1.0)
        self.assertEqual(result["steam_hook_duration"], 1.0)

    def test_legacy_splash_raiser_not_forced_without_splash(self):
        raisers = self.root / "static" / "sfx" / "raisers"
        raisers.mkdir(parents=True)
        make_tone(raisers / "raiser1.wav", seconds=1.4, freq=900)
        make_tone(raisers / "raiser2.wav", seconds=1.66, freq=1200)
        captured = []
        real_master = engine._render_master_audio

        def spy(*args, **kwargs):
            captured.append([Path(s["path"]).name for s in args[3]])
            return real_master(*args, **kwargs)

        flags = {"allow_automatic_sfx": True, "automatic_sfx_reason": "impact", "automatic_sfx_anchor": 2.0}
        words = words_for("Raid castles with your friends tonight")
        with patch.object(engine, "_render_master_audio", side_effect=spy):
            self.render(words, edl_extra=flags, enable_steam_hook=False)
            self.render(words, edl_extra=flags, enable_steam_hook=True, steam_hook_mode="classic")
        self.assertEqual(captured[0], ["raiser2.wav"])
        self.assertEqual(captured[1], ["raiser1.wav", "raiser2.wav"])


class EngineSmallHelpersTests(unittest.TestCase):
    def test_formula_opener_detection_is_strict(self):
        opener = engine._has_formula_opener
        self.assertTrue(opener(words_for("Here's a new Steam game for you")))
        self.assertTrue(opener(words_for("Here is a brand new Steam game")))
        self.assertTrue(opener(words_for("So in this new game you build")))
        # Default-prompt openers without the word "Steam" are still formula openers.
        self.assertTrue(opener(words_for("Here's a new co-op game where you weld leaks")))
        self.assertTrue(opener(words_for("Here’s a new roguelike deckbuilder game where every card explodes")))
        self.assertTrue(opener(words_for("So in this new co-op game, you weld leaks")))
        self.assertTrue(opener(words_for("So in this new survival game you hunt")))
        self.assertFalse(opener(words_for("This game lets you tame dragons")))
        self.assertFalse(opener(words_for("You weld leaks in this new co-op game")))
        self.assertFalse(opener(words_for("Here you can raid castles")))
        self.assertFalse(opener([]))

    def test_concat_entries_escape_apostrophes(self):
        entry = engine._concat_list_entry(Path("/tmp/it's/clip.mp4"))
        self.assertTrue(entry.startswith("file '"))
        self.assertIn("it'\\''s", entry)

    def test_get_media_duration_logs_instead_of_failing_silently(self):
        with self.assertLogs("promo_v3_engine", level="WARNING") as logs:
            self.assertEqual(engine.get_media_duration(ROOT / "tests" / "does_not_exist.mp4"), 0.0)
        self.assertTrue(any("does_not_exist" in line for line in logs.output))

    def test_render_fps_resolution(self):
        warnings = []
        self.assertEqual(engine._resolve_render_fps(30, [], warnings), 30)
        self.assertEqual(warnings, [])
        self.assertEqual(engine._resolve_render_fps(24, [], warnings), 30)
        self.assertEqual(len(warnings), 1)
        with patch.object(engine, "_probe_frame_rate", side_effect=[59.94, 60.0]):
            self.assertEqual(engine._resolve_render_fps(60, [Path("a"), Path("b")], []), 60)
        with patch.object(engine, "_probe_frame_rate", side_effect=[60.0, 29.97]):
            fallback_warnings = []
            self.assertEqual(engine._resolve_render_fps(60, [Path("a"), Path("b")], fallback_warnings), 30)
            self.assertIn("29.97", fallback_warnings[0])


if __name__ == "__main__":
    unittest.main()
