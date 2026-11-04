"""Voice settings, natural pauses and pause-compaction quality. No external API calls."""
import base64
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

import audio_aligner
from audio_quality import analyse_pcm, compact_aligned_pauses, compact_file_pauses
from natural_narration import clamp_voice_speed, prepare_text, synthesize, voice_settings


def eleven_response(text="Hello world"):
    alignment = {
        "characters": list(text),
        "character_start_times_seconds": [i * .04 for i in range(len(text))],
        "character_end_times_seconds": [(i + 1) * .04 for i in range(len(text))],
    }
    response = Mock(status_code=200)
    response.json.return_value = {"audio_base64": base64.b64encode(b"candidate").decode(),
                                  "normalized_alignment": alignment}
    return response


class NaturalNarrationVoiceSettingsTests(unittest.TestCase):
    def synth(self, **kwargs):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "voice.mp3"
            with patch("natural_narration.api_usage.usage_post", return_value=eleven_response()) as call, \
                 patch("audio_quality.compact_file_pauses", side_effect=lambda p, words, **kw: (words, [])), \
                 patch("audio_quality.require_narration", return_value={"passed": True}):
                words = synthesize("Hello world", out, "fake", **kwargs)
            self.assertTrue(out.exists())
            report = json.loads(out.with_suffix(".quality.json").read_text(encoding="utf-8"))
        self.assertEqual([w["word"] for w in words], ["Hello", "world"])
        return call.call_args.kwargs["json"]["voice_settings"], report

    def test_default_call_keeps_previous_voice_settings(self):
        settings, report = self.synth()
        self.assertEqual(settings, {"stability": .5, "similarity_boost": .75, "speed": 1.0})
        self.assertEqual(report["voice_settings"], settings)

    def test_speed_stability_and_similarity_reach_elevenlabs(self):
        settings, _ = self.synth(speed=1.15, stability=.35, similarity_boost=.9)
        self.assertEqual(settings, {"stability": .35, "similarity_boost": .9, "speed": 1.15})

    def test_speed_is_clamped_to_the_elevenlabs_range(self):
        self.assertEqual(self.synth(speed=1.6)[0]["speed"], 1.2)
        self.assertEqual(self.synth(speed=.4)[0]["speed"], .7)
        self.assertEqual(clamp_voice_speed("bad"), 1.0)
        self.assertEqual(clamp_voice_speed(float("nan")), 1.0)
        self.assertEqual(voice_settings(1.1, 3, -1), {"stability": 1.0, "similarity_boost": 0.0, "speed": 1.1})

    def test_markup_is_still_rejected_with_a_friendly_message(self):
        with self.assertRaises(ValueError) as error:
            prepare_text('Wait. <break time="0.25s" /> Now.')
        self.assertIn("<break", str(error.exception))
        self.assertIn("puntuación", str(error.exception))
        with self.assertRaises(ValueError) as error:
            prepare_text("[pause] Now.")
        self.assertIn("[pause]", str(error.exception))


class AlignerVoicePassthroughTests(unittest.TestCase):
    def run_natural(self, **kwargs):
        with tempfile.TemporaryDirectory() as folder:
            with patch("natural_narration.synthesize", return_value=[{"word": "Go", "start": 0, "end": .2}]) as synth:
                _, words, info = audio_aligner.synthesize_elevenlabs_with_timestamps(
                    "Go.", Path(folder) / "voice.mp3", "key", "voice", **kwargs)
        self.assertEqual(words[0]["word"], "Go")
        return synth.call_args.kwargs, info

    def test_natural_delivery_passes_voice_settings_and_reports_real_speed(self):
        sent, info = self.run_natural(speed=1.05, stability=.4, similarity_boost=.8)
        self.assertEqual((sent["speed"], sent["stability"], sent["similarity_boost"]), (1.05, .4, .8))
        self.assertEqual(info["speed_full"], 1.05)
        self.assertEqual(info["synthesis_mode"], "continuous")
        self.assertEqual((info["stability"], info["similarity_boost"]), (.4, .8))

    def test_natural_delivery_uses_the_ui_default_and_clamps(self):
        sent, info = self.run_natural()
        self.assertEqual((sent["speed"], sent["stability"], sent["similarity_boost"]), (1.1, .5, .75))
        self.assertEqual(info["speed_full"], 1.1)
        self.assertEqual(self.run_natural(speed=1.5)[1]["speed_full"], 1.2)
        self.assertEqual(self.run_natural(speed=None)[1]["speed_full"], 1.1)

    def test_legacy_path_forwards_similarity_boost(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(audio_aligner, "_single_elevenlabs_tts_call", return_value=(b"audio", [], {})) as call:
                audio_aligner.synthesize_elevenlabs_with_timestamps(
                    "Only the hook.", Path(folder) / "voice.mp3", "key", "voice",
                    speed=1.0, natural_delivery=False, similarity_boost=.6)
        self.assertEqual(call.call_args.kwargs["similarity_boost"], .6)
        self.assertEqual(call.call_args.kwargs["speed"], 1.05)


class CrossfadedPauseTests(unittest.TestCase):
    rate = 16000

    def pcm(self):
        rate = self.rate
        t = np.arange(rate) / rate
        tone = .1 * np.sin(2 * np.pi * 227 * t)
        # Quiet room tone (below the -38 dBFS silence gate). The removed span is not a whole
        # number of hum periods, so a hard cut leaves a waveform step.
        n = round(.8 * rate)
        hum = .008 * np.sin(2 * np.pi * 47 * np.arange(n) / rate + 1.3)
        return np.concatenate([tone, hum, tone]).astype(np.float32)

    words = [{"word": "first", "start": 0, "end": 1}, {"word": "second", "start": 1.8, "end": 2.8}]

    def test_crossfade_keeps_word_timing_and_audio_continuous(self):
        x = self.pcm()
        fixed, aligned, cuts = compact_aligned_pauses(x, self.rate, self.words)
        hard, hard_aligned, hard_cuts = compact_aligned_pauses(x, self.rate, self.words, crossfade=0)
        self.assertEqual(len(cuts), 1)
        self.assertAlmostEqual(cuts[0]["crossfade"], .015, places=3)
        self.assertEqual(cuts[0]["removed"], hard_cuts[0]["removed"])
        # Same length and word timings as a hard cut: timings stay consistent with audio.
        removed = round(cuts[0]["end"] * self.rate) - round(cuts[0]["start"] * self.rate)
        self.assertEqual(len(fixed), len(x) - removed)
        self.assertEqual(len(fixed), len(hard))
        self.assertEqual(aligned, hard_aligned)
        starts = [w["start"] for w in aligned]
        self.assertEqual(starts, sorted(starts))
        for before, after in zip(self.words, aligned):
            self.assertAlmostEqual(after["end"] - after["start"], before["end"] - before["start"], places=4)
        self.assertTrue(analyse_pcm(fixed, self.rate, aligned)["passed"])
        # Speech is untouched; only the silence around the splice changes.
        self.assertTrue(np.array_equal(fixed[:self.rate], x[:self.rate]))
        self.assertTrue(np.array_equal(fixed[-self.rate:], x[-self.rate:]))
        splice = round(cuts[0]["start"] * self.rate)
        window = slice(splice - 400, splice + 400)
        smooth_step = np.abs(np.diff(fixed[window].astype(np.float64))).max()
        hard_step = np.abs(np.diff(hard[window].astype(np.float64))).max()
        natural_step = np.abs(np.diff(x[self.rate + 10:self.rate + 800].astype(np.float64))).max()
        self.assertGreater(hard_step, 4 * natural_step)
        self.assertLess(smooth_step, 2.5 * natural_step)
        self.assertEqual(fixed.dtype, x.dtype)

    def test_hard_cut_option_matches_previous_behaviour(self):
        x = self.pcm()
        hard, _, cuts = compact_aligned_pauses(x, self.rate, self.words, crossfade=0)
        a, b = round(cuts[0]["start"] * self.rate), round(cuts[0]["end"] * self.rate)
        self.assertTrue(np.array_equal(hard, np.concatenate([x[:a], x[b:]])))
        self.assertEqual(cuts[0]["crossfade"], 0)

    def test_integer_samples_are_crossfaded_without_overflow(self):
        rate = self.rate
        tone = np.round(3000 * np.sin(2 * np.pi * 227 * np.arange(rate) / rate))
        x = np.concatenate([tone, np.zeros(round(.8 * rate)), tone]).astype(np.int16)
        fixed, aligned, cuts = compact_aligned_pauses(x, self.rate, self.words)
        self.assertEqual(fixed.dtype, np.int16)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(aligned[1]["start"], round(1.8 - cuts[0]["removed"], 4))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class PauseFileFormatTests(unittest.TestCase):
    words = [{"word": "first", "start": 0, "end": 1}, {"word": "second", "start": 1.8, "end": 2.8}]

    def make(self, path, codec):
        graph = ("sine=frequency=227:sample_rate=44100:duration=1,volume=0.3[a];"
                 "anullsrc=r=44100:cl=mono,atrim=duration=0.8[s];"
                 "sine=frequency=227:sample_rate=44100:duration=1,volume=0.3[b];"
                 "[a][s][b]concat=n=3:v=0:a=1")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", graph] + codec + [str(path)],
                       check=True, capture_output=True)

    def probe(self, path):
        info = json.loads(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))
        stream = info["streams"][0]
        return stream["codec_name"], int(stream["sample_rate"]), float(info["format"]["duration"])

    def test_wav_stays_pcm_and_mp3_keeps_name_rate_and_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wav = root / "voice.candidate_x.wav"
            mp3 = root / "voice.candidate_y.mp3"
            self.make(wav, ["-c:a", "pcm_s16le"])
            self.make(mp3, ["-c:a", "libmp3lame", "-b:a", "128k"])
            for path, codec in ((wav, "pcm_s16le"), (mp3, "mp3")):
                aligned, cuts = compact_file_pauses(path, self.words, ffmpeg="ffmpeg")
                self.assertEqual(len(cuts), 1)
                name, rate, duration = self.probe(path)
                self.assertEqual(name, codec)
                self.assertEqual(rate, 44100)  # no resampling to 48 kHz
                self.assertAlmostEqual(duration, 2.8 - cuts[0]["removed"], delta=.06)
                self.assertAlmostEqual(aligned[1]["start"], 1.8 - cuts[0]["removed"], places=3)
            self.assertEqual(sorted(p.name for p in root.iterdir()), sorted([wav.name, mp3.name]))
            with wave.open(str(wav)) as stream:
                self.assertEqual(stream.getsampwidth(), 2)


class SetupTrailerVoiceOptionsTests(unittest.TestCase):
    def prepare(self, root, **kwargs):
        from setup_trailer_narration import prepare_narration
        cfg = SimpleNamespace(elevenlabs_voice_id="voice", elevenlabs_model="model", elevenlabs_api_key="key")

        def tts(text, path, *args, **kw):
            Path(path).write_bytes(b"audio")
            return str(path), [{"word": "hello", "start": 0, "end": .5}], {}

        with patch("audio_aligner.synthesize_elevenlabs_with_timestamps", side_effect=tts) as synth, \
             patch("audio_quality.require_narration", return_value={"passed": True}), \
             patch("setup_trailer_narration.media_duration", return_value=1.0):
            assets = prepare_narration("hello", cfg, root, 5, **kwargs)
        return synth, assets

    def test_defaults_keep_cache_identity_and_historical_voice(self):
        with tempfile.TemporaryDirectory() as folder:
            synth, assets = self.prepare(Path(folder))
            kwargs = synth.call_args.kwargs
            self.assertEqual((kwargs["speed"], kwargs["stability"], kwargs["similarity_boost"]), (1.0, .5, .75))
            self.assertTrue(kwargs["natural_delivery"])
            identity = json.dumps(["hello", "voice", "model", "continuous_v2", 1.0], ensure_ascii=False)
            self.assertEqual(Path(assets["audio"]).parent.name, hashlib.sha256(identity.encode()).hexdigest()[:24])

    def test_custom_voice_settings_are_forwarded_and_cached_separately(self):
        with tempfile.TemporaryDirectory() as folder:
            synth, default_assets = self.prepare(Path(folder))
            synth, assets = self.prepare(Path(folder), speed=1.1, stability=.3)
            kwargs = synth.call_args.kwargs
            self.assertEqual((kwargs["speed"], kwargs["stability"], kwargs["similarity_boost"]), (1.1, .3, .75))
            self.assertNotEqual(Path(assets["audio"]).parent, Path(default_assets["audio"]).parent)


if __name__ == "__main__":
    unittest.main()
