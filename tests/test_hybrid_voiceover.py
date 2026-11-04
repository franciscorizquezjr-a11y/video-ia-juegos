"""Exercise hybrid TTS cuts with synthetic audio; no external API calls."""
import math
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch
import wave

import audio_aligner as audio
from script_retention import build_retention_script_prompt


def tone_bytes(seconds, frequency):
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as stream:
        stream.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        stream.writeframes(b"".join(
            struct.pack("<h", int(6000 * math.sin(2 * math.pi * frequency * i / 24000)))
            for i in range(int(seconds * 24000))
        ))
    return buf.getvalue()


class HybridVoiceoverTests(unittest.TestCase):
    def test_single_sentence_uses_hook_speed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            with patch.object(audio, "_single_elevenlabs_tts_call", return_value=(b"audio", [], {})) as call:
                _, _, alignment = audio.synthesize_elevenlabs_with_timestamps(
                    "Only the hook.", Path(tmp) / "voice.mp3", "fake", "fake", speed=1.20, natural_delivery=False)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(call.call_args.kwargs["speed"], 1.25)
            self.assertEqual(alignment["synthesis_mode"], "hook_only")

    def test_repeated_word_does_not_move_sentence_boundary(self):
        words = [{"word": w, "start": i / 10, "end": (i + 1) / 10}
                 for i, w in enumerate("Go go go go Next sentence".split())]
        self.assertEqual(audio.get_first_sentence_end_time(
            {"words": words}, "Go go go go. Next sentence."), 0.4)

    def test_duration_budget_scales_with_requested_duration(self):
        self.assertIn("70 to 80 words", build_retention_script_prompt("Game"))
        self.assertIn("approximately 45s", build_retention_script_prompt("Game", 45))
        self.assertIn("105 to 120 words", build_retention_script_prompt("Game", 45))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_two_requests_stitch_only_first_sentence_and_preserve_every_word(self):
        text = "Go go go go. Next sentence. Last sentence."
        tokens = text.split()
        # The next word starts within 50ms of the cut: the old time filters
        # included it twice. Index-based stitching must preserve it just once.
        words_a = [{"word": w.rstrip("."), "start": i * .2, "end": (i + 1) * .2}
                   for i, w in enumerate(tokens)]
        words_b = [{"word": w.rstrip("."), "start": i * .1, "end": (i + 1) * .1}
                   for i, w in enumerate(tokens[:6])]
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            out = Path(tmp) / "voice.mp3"
            with patch.object(audio, "_single_elevenlabs_tts_call", side_effect=[
                (tone_bytes(2, 440), words_a, {}),
                (tone_bytes(1, 880), words_b, {}),
            ]) as call:
                _, words, alignment = audio.synthesize_elevenlabs_with_timestamps(
                    text, out, "fake", "fake", natural_delivery=False)
            self.assertEqual(call.call_count, 2)
            self.assertEqual(call.call_args_list[0].kwargs["speed"], 1.12)
            self.assertEqual(call.call_args_list[1].kwargs["speed"], 1.15)
            self.assertEqual(call.call_args_list[1].args[0], "Go go go go. Next sentence.")
            self.assertEqual([w["word"] for w in words], [w.rstrip(".") for w in tokens])
            self.assertAlmostEqual(words[4]["start"], .4)
            self.assertEqual(alignment["synthesis_mode"], "hybrid")
            self.assertAlmostEqual(audio.get_audio_duration(out), 1.6, delta=.08)
            self.assertFalse(list(Path(tmp).glob("temp_*.mp3")))

    def test_hook_failure_reuses_full_request(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            out = Path(tmp) / "voice.mp3"
            words = [{"word": "Go", "start": 0, "end": .2}]
            with patch.object(audio, "_single_elevenlabs_tts_call", side_effect=[
                (b"full audio", words, {}), RuntimeError("hook failed"),
            ]) as call:
                _, actual, alignment = audio.synthesize_elevenlabs_with_timestamps(
                    "Go now. Keep going.", out, "fake", "fake", natural_delivery=False)
            self.assertEqual(call.call_count, 2)
            self.assertEqual(out.read_bytes(), b"full audio")
            self.assertEqual(actual, words)
            self.assertIsNone(alignment["speed_hook"])
            self.assertEqual(alignment["synthesis_mode"], "full_fallback")


if __name__ == "__main__":
    unittest.main()
