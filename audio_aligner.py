"""
audio_aligner.py — Voiceover Audio Extraction, Timestamp Alignment & Dynamic Captions Generator

Handles:
- Extracting clean audio tracks from video or audio files (MP4, MOV, WAV, MP3, M4A, etc.) via FFmpeg.
- Probing accurate audio duration via FFprobe.
- Transcribing & aligning spoken words to millisecond timestamps (using faster-whisper or script-aligned timing).
- Generating high-visibility kinetic short-form ASS subtitles (Alex Hormozi / TikTok / Shorts style with active word highlights).
"""

import api_usage as _api_usage

import base64
import json
import logging
import math
import os
import re
import shutil
import subprocess
import time
import uuid
import requests
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

FFMPEG_CMD = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_CMD = shutil.which("ffprobe") or "ffprobe"


def get_audio_duration(file_path: str | Path) -> float:
    """Probe audio duration in seconds using ffprobe."""
    path_str = str(Path(file_path).resolve())
    if not os.path.exists(path_str):
        logger.error(f"File not found: {path_str}")
        return 0.0

    cmd = [
        FFPROBE_CMD,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path_str,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        dur = float(res.stdout.strip())
        return round(dur, 2)
    except Exception as e:
        logger.warning(f"FFprobe duration check failed for {path_str}: {e}")
        return 0.0


def check_video_has_audio(file_path: str | Path) -> bool:
    """Check whether a video/audio file contains at least one audio stream using ffprobe."""
    path_str = str(Path(file_path).resolve())
    if not os.path.exists(path_str):
        return False
    cmd = [
        FFPROBE_CMD,
        "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        path_str,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return bool(res.stdout.strip())
    except Exception:
        return False



def extract_clean_audio(input_file: str | Path, output_file: str | Path) -> str:
    """Extract audio track as 16kHz mono WAV or 192k MP3 for analysis and mixing."""
    in_str = str(Path(input_file).resolve())
    out_path = Path(output_file).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_str = str(out_path)

    cmd = [
        FFMPEG_CMD,
        "-y",
        "-i", in_str,
        "-vn",
        "-ac", "2",
        "-ar", "44100",
        "-b:a", "192k",
        out_str,
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        return out_str
    except Exception as e:
        logger.error(f"Failed to extract audio from {in_str}: {e}")
        # Fallback copy if already an audio file
        shutil.copy2(in_str, out_str)
        return out_str


def clean_subtitle_word(word: str) -> str:
    """Remove punctuation, quotes, question/exclamation marks, and unwanted symbols for clean kinetic captions,
    while strictly preserving intra-word hyphens in compound words (e.g. 'early-access', 'co-op', 'sci-fi')."""
    if not word:
        return ""
    text = str(word).strip()
    # 1. Strip quotes, punctuation, brackets, em-dashes, en-dashes, etc., keeping hyphens
    text = re.sub(r'[.,!?:;¿¡"“”\'‘’«»()\[\]{}—–_*~#/\\`^$|<>=+@%&]+', '', text)
    # 2. Strip leading/trailing non-alphanumeric chars (including leading/trailing hyphens)
    text = re.sub(r'^[^\w]+|[^\w]+$', '', text)
    # 3. Collapse multiple hyphens to single
    text = re.sub(r'-+', '-', text)
    # 4. Remove hyphens that are not between alphanumeric characters
    text = re.sub(r'(?<![a-zA-Z0-9])-|-(?![a-zA-Z0-9])', '', text)
    return text.strip()


def filter_steam_hook_words(words: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], float]:
    """Identify and remove the opening hook words ('Here\'s a new Steam game' / 'Here is a new Steam game')
    from the subtitle stream, returning the filtered words and the hook duration timestamp.
    """
    if not words:
        return [], 1.8

    def norm_w(w):
        return re.sub(r"[^a-zA-Z0-9]", "", str(w).lower())

    hook_tokens_1 = ["heres", "a", "new", "steam", "game"]
    hook_tokens_2 = ["here", "is", "a", "new", "steam", "game"]
    hook_tokens_3 = ["here", "a", "new", "steam", "game"]
    hook_tokens_4 = ["new", "steam", "game"]

    norm_words = [norm_w(w.get("word", "")) for w in words[:8]]

    matched_count = 0
    if len(norm_words) >= 5 and norm_words[:5] == hook_tokens_1:
        matched_count = 5
    elif len(norm_words) >= 6 and norm_words[:6] == hook_tokens_2:
        matched_count = 6
    elif len(norm_words) >= 5 and norm_words[:5] == hook_tokens_3:
        matched_count = 5
    elif len(norm_words) >= 3 and norm_words[:3] == hook_tokens_4:
        matched_count = 3
    else:
        # Check if contains steam and game within first 6 words
        for i in range(min(len(words), 6)):
            if norm_w(words[i].get("word", "")) in ("steam", "game"):
                matched_count = max(matched_count, i + 1)
        if matched_count < 3 and norm_words and norm_words[0] in ("here", "heres"):
            matched_count = min(len(words), 5)

    if matched_count > 0:
        hook_end = float(words[matched_count - 1].get("end", 1.8))
        filtered_words = words[matched_count:]
        return filtered_words, max(1.4, hook_end)

    return words, 1.8


def get_first_sentence_end_time(alignment_data: Any, script_text: str = "") -> float:
    """Find the exact end timestamp of the first full spoken sentence / opening hook phrase in alignment data."""
    if not alignment_data:
        return 3.0

    words = alignment_data.get("words") if isinstance(alignment_data, dict) else (alignment_data if isinstance(alignment_data, list) else [])
    if not words:
        return 3.0

    def clean_token(t: str) -> str:
        return re.sub(r"[^\w]", "", str(t).lower())

    # Priority 1: Match full sentence #1 from script_text against word alignment
    if script_text:
        raw = script_text.strip()
        # Split strictly by sentence-ending punctuation (. ! ? \n), NEVER by commas!
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n+', raw) if s.strip()]
        if not sentences:
            sentences = [raw]

        target_sentence = sentences[0]
        s0_tokens = [clean_token(t) for t in target_sentence.split() if clean_token(t)]

        target_len = len(s0_tokens)

        # Match against words alignment list
        if target_len > 0 and len(words) > 0:
            # Match the whole prefix: a repeated final word must not move the cut.
            matched_idx = min(len(words) - 1, max(0, target_len - 1))
            prefix = ""
            target_prefix = "".join(s0_tokens)
            for w_i, word in enumerate(words):
                prefix += clean_token(word.get("word", ""))
                if prefix == target_prefix:
                    matched_idx = w_i
                    break
                if not target_prefix.startswith(prefix):
                    break

            end_t = float(words[matched_idx].get("end", 0.0))
            if end_t > 0:
                word_str = words[matched_idx].get("word", "")
                logger.info(f"Final exacto de la frase 1 ('{target_sentence[:50]}...') detectado en palabra #{matched_idx+1} ('{word_str}'): {end_t:.2f}s")
                return round(end_t, 2)

    # Priority 2: Look for sentence-ending punctuation in words array (. ! ?)
    for i, w in enumerate(words):
        word_str = str(w.get("word", "")).strip()
        if i >= 3 and any(word_str.endswith(p) for p in [".", "!", "?", "\n"]):
            w_end = float(w.get("end", 0.0))
            if 1.2 <= w_end <= 7.0:
                logger.info(f"Final de primera frase por puntuación en palabra #{i+1} ('{word_str}'): {w_end:.2f}s")
                return round(w_end, 2)

    # Priority 3: Fallback word #10 (~3.0s)
    if len(words) >= 6:
        idx = min(len(words) - 1, 9)
        w_end = float(words[idx].get("end", 0.0))
        if w_end > 1.0:
            return round(w_end, 2)

    return 3.0


def get_standalone_sentence_1_duration(script_text: str, api_key: str = "", voice_id: str = "") -> float:
    """
    Calculates exact duration of sentence #1 by synthesizing it alone with ElevenLabs if needed,
    or estimating based on word count.
    """
    if not script_text:
        return 3.0
    raw = script_text.strip()
    parts = re.split(r'(?<=[.!?])\s+', raw)
    if not parts:
        return 3.0

    first_sentence = parts[0].strip()
    if len(first_sentence.split()) <= 6 and len(parts) > 1 and any(first_sentence.lower().startswith(p) for p in ["here's a new", "here is a new", "new steam"]):
        first_sentence = (parts[0] + " " + parts[1]).strip()

    if api_key and voice_id:
        try:
            import tempfile, time, os
            from config import BASE_DIR
            temp_dir = BASE_DIR / "temp" / "v3_cache"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_wav = temp_dir / f"temp_s1_{int(time.time()*1000)}.mp3"

            synthesize_elevenlabs_with_timestamps(
                text=first_sentence,
                output_path=temp_wav,
                api_key=api_key,
                voice_id=voice_id,
            )
            dur = get_audio_duration(temp_wav)
            if temp_wav.exists():
                try:
                    os.remove(temp_wav)
                except Exception:
                    pass
            if dur > 0.5:
                logger.info(f"Duración aislada de la primera frase sintetizada con ElevenLabs: {dur:.2f}s ('{first_sentence}')")
                return round(dur, 2)
        except Exception as e:
            logger.warning(f"Error sintetizando primera frase aislada en ElevenLabs: {e}")

    n_words = len(first_sentence.split())
    return max(1.5, round(n_words / 2.3, 2))


def clean_script_text_for_voice(raw_text: str) -> str:
    """Sanitize raw script text: remove markdown blocks, SRT timestamps, stage directions, speaker headers,
    apply contractions (e.g. 'Here is' -> "Here's"), and replace commas/colons with semicolons for TTS.
    """
    if not raw_text:
        return ""
    text = raw_text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = "\n".join(text.split("\n")[1:-1]).strip()

    cleaned_lines = []
    for line in text.splitlines():
        l = line.strip()
        if not l or l.isdigit() or "-->" in l:
            continue
        # Remove bracketed cues like [Music], [Gameplay cut]
        l = re.sub(r"\[.*?\]", "", l).strip()
        l = re.sub(r"<[^>]+>", "", l).strip()
        l = re.sub(r'^(?:["\'`]\s*)?(?:voiceover|narrator|script|guión|guion|locución|locucion|voice)\s*:\s*', '', l, flags=re.IGNORECASE).strip()
        if l:
            cleaned_lines.append(l)

    res = " ".join(cleaned_lines).strip()
    res = re.sub(r'^(?:["\'`]\s*)?(?:voiceover|narrator|script|guión|guion|locución|locucion|voice)\s*:\s*', '', res, flags=re.IGNORECASE).strip()
    res = res.strip('"\'` ')

    # Apply contractions/abbreviations (e.g. "Here is" -> "Here's")
    contractions = [
        (r"\bHere is\b", "Here's"),
        (r"\bhere is\b", "here's"),
        (r"\bThere is\b", "There's"),
        (r"\bthere is\b", "there's"),
        (r"\bThat is\b", "That's"),
        (r"\bthat is\b", "that's"),
        (r"\bIt is\b", "It's"),
        (r"\bit is\b", "it's"),
        (r"\bWhat is\b", "What's"),
        (r"\bwhat is\b", "what's"),
        (r"\bWho is\b", "Who's"),
        (r"\bwho is\b", "who's"),
        (r"\bWhere is\b", "Where's"),
        (r"\bwhere is\b", "where's"),
        (r"\bWhen is\b", "When's"),
        (r"\bwhen is\b", "when's"),
        (r"\bHow is\b", "How's"),
        (r"\bhow is\b", "how's"),
        (r"\bLet us\b", "Let's"),
        (r"\blet us\b", "let's"),
        (r"\bThey are\b", "They're"),
        (r"\bthey are\b", "they're"),
        (r"\bWe are\b", "We're"),
        (r"\bwe are\b", "we're"),
        (r"\bI am\b", "I'm"),
        (r"\bi am\b", "I'm"),
        (r"\bI have\b", "I've"),
        (r"\bi have\b", "I've"),
        (r"\bYou have\b", "You've"),
        (r"\byou have\b", "you've"),
        (r"\bWe have\b", "We've"),
        (r"\bwe have\b", "we've"),
        (r"\bThey have\b", "They've"),
        (r"\bthey have\b", "they've"),
        (r"\bHe has\b", "He's"),
        (r"\bhe has\b", "he's"),
        (r"\bShe has\b", "She's"),
        (r"\bshe has\b", "she's"),
        (r"\bIt has\b", "It's"),
        (r"\bit has\b", "it's"),
        (r"\bI will\b", "I'll"),
        (r"\bi will\b", "I'll"),
        (r"\bYou will\b", "You'll"),
        (r"\byou will\b", "you'll"),
        (r"\bHe will\b", "He'll"),
        (r"\bhe will\b", "he'll"),
        (r"\bShe will\b", "She'll"),
        (r"\bshe will\b", "she'll"),
        (r"\bWe will\b", "We'll"),
        (r"\bwe will\b", "we'll"),
        (r"\bThey will\b", "They'll"),
        (r"\bthey will\b", "they'll"),
        (r"\bIt will\b", "It'll"),
        (r"\bit will\b", "it'll"),
        (r"\bI would\b", "I'd"),
        (r"\bi would\b", "I'd"),
        (r"\bYou would\b", "You'd"),
        (r"\byou would\b", "you'd"),
        (r"\bHe would\b", "He'd"),
        (r"\bhe would\b", "he'd"),
        (r"\bShe would\b", "She'd"),
        (r"\bshe would\b", "she'd"),
        (r"\bWe would\b", "We'd"),
        (r"\bwe would\b", "we'd"),
        (r"\bThey would\b", "They'd"),
        (r"\bthey would\b", "they'd"),
        (r"\bDo not\b", "Don't"),
        (r"\bdo not\b", "don't"),
        (r"\bDoes not\b", "Doesn't"),
        (r"\bdoes not\b", "doesn't"),
        (r"\bDid not\b", "Didn't"),
        (r"\bdid not\b", "didn't"),
        (r"\bCannot\b", "Can't"),
        (r"\bcannot\b", "can't"),
        (r"\bCan not\b", "Can't"),
        (r"\bcan not\b", "can't"),
        (r"\bCould not\b", "Couldn't"),
        (r"\bcould not\b", "couldn't"),
        (r"\bShould not\b", "Shouldn't"),
        (r"\bshould not\b", "shouldn't"),
        (r"\bWould not\b", "Wouldn't"),
        (r"\bwould not\b", "wouldn't"),
        (r"\bIs not\b", "Isn't"),
        (r"\bis not\b", "isn't"),
        (r"\bAre not\b", "Aren't"),
        (r"\bare not\b", "aren't"),
        (r"\bWas not\b", "Wasn't"),
        (r"\bwas not\b", "wasn't"),
        (r"\bWere not\b", "Weren't"),
        (r"\bwere not\b", "weren't"),
        (r"\bHave not\b", "Haven't"),
        (r"\bhave not\b", "haven't"),
        (r"\bHas not\b", "Hasn't"),
        (r"\bhas not\b", "hasn't"),
        (r"\bHad not\b", "Hadn't"),
        (r"\bhad not\b", "hadn't"),
    ]
    for pattern, replacement in contractions:
        res = re.sub(pattern, replacement, res)

    # Replace commas (,) and colons (:) with semicolons (;) for pauses
    res = re.sub(r'[,:]+', ';', res)

    return res.strip()


def parse_elevenlabs_alignment(alignment: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse ElevenLabs character-level alignment dictionary into word-level timestamps.
    
    ElevenLabs format:
    {
        "characters": ["H", "e", "r", "e", "'", "s", " ", "a", " ", ...],
        "character_start_times_seconds": [0.0, 0.04, 0.08, ...],
        "character_end_times_seconds": [0.04, 0.08, 0.12, ...]
    }
    """
    if not alignment:
        return []
    chars = alignment.get("characters", [])
    starts = alignment.get("character_start_times_seconds", [])
    ends = alignment.get("character_end_times_seconds", [])
    if not chars or not starts or not ends:
        return []
    
    words_list = []
    curr_chars = []
    w_start = None
    w_end = None
    
    for i, ch in enumerate(chars):
        c_s = float(starts[i]) if i < len(starts) else 0.0
        c_e = float(ends[i]) if i < len(ends) else c_s + 0.05
        
        if ch.isspace():
            if curr_chars and w_start is not None:
                raw_word = "".join(curr_chars).strip()
                cleaned = clean_subtitle_word(raw_word)
                if not cleaned:
                    cleaned = raw_word
                if cleaned:
                    words_list.append({
                        "word": cleaned,
                        "start": round(w_start, 2),
                        "end": round(w_end if w_end is not None else c_s, 2),
                    })
                curr_chars = []
                w_start = None
                w_end = None
        else:
            if w_start is None:
                w_start = c_s
            w_end = c_e
            curr_chars.append(ch)
            
    if curr_chars and w_start is not None:
        raw_word = "".join(curr_chars).strip()
        cleaned = clean_subtitle_word(raw_word)
        if not cleaned:
            cleaned = raw_word
        if cleaned:
            words_list.append({
                "word": cleaned,
                "start": round(w_start, 2),
                "end": round(w_end, 2),
            })
            
    return words_list


def _single_elevenlabs_tts_call(
    text: str,
    api_key: str,
    voice_id: str,
    model_id: str = "eleven_v4",
    speed: float = 1.1,
    stability: float = 0.5,
) -> Tuple[bytes, List[Dict[str, Any]], Dict[str, Any]]:
    """Single ElevenLabs with-timestamps API call returning (audio_bytes, words_list, alignment_dict)."""
    cleaned_text = clean_script_text_for_voice(text)
    if not cleaned_text:
        raise ValueError("El texto para la síntesis de ElevenLabs está vacío.")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
    headers = {
        "xi-api-key": api_key.strip(),
        "Content-Type": "application/json",
    }
    payload = {
        "text": cleaned_text,
        "model_id": model_id or "eleven_v4",
        "voice_settings": {
            "stability": float(stability),
            "similarity_boost": 0.75,
            "speed": float(speed),
        }
    }

    logger.info(f"ElevenLabs TTS API Call (Voz: {voice_id[:8]}, Speed: {speed:.2f}, Stability: {stability:.2f}, Chars: {len(cleaned_text)})...")
    resp = _api_usage.usage_post(url, headers=headers, json=payload, timeout=60)
    if resp.status_code == 200:
        data = resp.json()
        audio_b64 = data.get("audio_base64", "")
        if not audio_b64:
            raise RuntimeError("La respuesta de ElevenLabs no contuvo audio_base64.")
        audio_bytes = base64.b64decode(audio_b64)
        alignment = data.get("alignment") or data.get("normalized_alignment") or {}
        words = parse_elevenlabs_alignment(alignment)
        return audio_bytes, words, alignment
    else:
        raise RuntimeError(f"ElevenLabs TTS con timestamps falló ({resp.status_code}): {resp.text}")


def synthesize_elevenlabs_with_timestamps(
    text: str,
    output_path: str | Path,
    api_key: str,
    voice_id: str,
    model_id: str = "eleven_v4",
    speed: float = 1.10,
    stability: float = 0.5,
    speed_hook: Optional[float] = None,
    natural_delivery: bool = True,
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    """
    Dual-Pass Hybrid ElevenLabs TTS Synthesizer & Stitched Audio Pipeline:
    1. Call A: Full script text at speed_full (base speed + 0.02 -> 1.12x by default).
    2. Call B: First two sentences (hook + 2nd sentence) at speed_hook (base speed + 0.05 -> 1.15x by default).
    3. Stitches audio: Sentence 1 (Hook) from Call B (1.15x) + Sentence 2..N (Rest of script) from Call A (1.12x).
    4. Merges & offsets word alignment timestamps for exact word-by-word synchronization.
    """
    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if natural_delivery:
        from natural_narration import synthesize
        words = synthesize(text, out_file, api_key, voice_id,
                           model_id=model_id or "eleven_multilingual_v2", ffmpeg=FFMPEG_CMD)
        return str(out_file), words, {"words": words, "synthesis_mode": "continuous",
                                     "speed_full": 1.0, "speed_hook": None,
                                     "technical_quality_passed": True}

    cleaned_text = clean_script_text_for_voice(text)
    if not cleaned_text:
        raise ValueError("El texto del guión está vacío para la síntesis de ElevenLabs.")

    base_speed = float(speed) if speed is not None else 1.10
    speed_full = round(base_speed + 0.02, 3)
    speed_hook_val = round(float(speed_hook) if speed_hook is not None else (base_speed + 0.05), 3)

    # Parse sentences (strictly by sentence-ending punctuation . ! ? \n)
    raw_sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n+', cleaned_text) if s.strip()]
    if not raw_sentences:
        raw_sentences = [cleaned_text]

    can_dual_pass = len(raw_sentences) >= 2

    if not can_dual_pass:
        logger.info(f"Guión de frase única. Realizando síntesis directa a velocidad {speed_hook_val:.2f}...")
        audio_bytes, words, align_dict = _single_elevenlabs_tts_call(
            cleaned_text, api_key, voice_id, model_id, speed=speed_hook_val, stability=stability
        )
        with open(out_file, "wb") as f:
            f.write(audio_bytes)
        align_dict = dict(align_dict, speed_full=None, speed_hook=speed_hook_val,
                          synthesis_mode="hook_only")
        return str(out_file), words, align_dict

    # Text for Call B: First two sentences (Hook + 2nd sentence to preserve natural cadence and tone)
    text_call_b = (raw_sentences[0] + " " + raw_sentences[1]).strip()

    temp_dir = out_file.parent
    ts_now = uuid.uuid4().hex
    file_b_path = temp_dir / f"temp_hook_{ts_now}.mp3"
    file_a_path = temp_dir / f"temp_full_{ts_now}.mp3"

    try:
        # Call A: Full script at speed_full (1.12x)
        logger.info(f"1/2 Solicitud A: Guión completo a velocidad {speed_full:.2f}x ({len(cleaned_text)} caracteres)...")
        audio_bytes_a, words_a, align_a = _single_elevenlabs_tts_call(
            cleaned_text, api_key, voice_id, model_id, speed=speed_full, stability=stability
        )
        with open(file_a_path, "wb") as f:
            f.write(audio_bytes_a)

        # Call B: Hook + 2nd sentence at speed_hook (1.15x)
        logger.info(f"2/2 Solicitud B: Hook + segunda oración a velocidad {speed_hook_val:.2f}x ('{text_call_b[:60]}...')...")
        audio_bytes_b, words_b, align_b = _single_elevenlabs_tts_call(
            text_call_b, api_key, voice_id, model_id, speed=speed_hook_val, stability=stability
        )
        with open(file_b_path, "wb") as f:
            f.write(audio_bytes_b)

        # Calculate exact sentence 1 end timestamp in Call B (1.15x speed) and Call A (1.12x speed)
        def hook_word_count(aligned_words):
            target = re.sub(r"[^\w]", "", raw_sentences[0].lower())
            prefix = ""
            for i, word in enumerate(aligned_words):
                prefix += re.sub(r"[^\w]", "", str(word.get("word", "")).lower())
                if prefix == target:
                    return i + 1
                if not target.startswith(prefix):
                    break
            raise ValueError("No se pudo localizar el hook en la alineación de ElevenLabs.")

        count_b = hook_word_count(words_b)
        count_a = hook_word_count(words_a)
        if count_a >= len(words_a) or count_b >= len(words_b):
            raise ValueError("Falta la segunda oración en la alineación de ElevenLabs.")
        cut_b = float(words_b[count_b - 1]["end"])
        cut_a = float(words_a[count_a - 1]["end"])
        if cut_b <= 0 or cut_a <= 0:
            raise ValueError("Tiempos de corte del hook inválidos.")

        logger.info(f"Corte Hook: Call B ({speed_hook_val:.2f}x)={cut_b:.3f}s | Call A ({speed_full:.2f}x)={cut_a:.3f}s")

        # Trim Sentence 1 from Call B and Sentence 2..N from Call A, then stitch with FFmpeg
        fg = f"[0:a]atrim=0:{cut_b:.6f},asetpts=PTS-STARTPTS[a1];[1:a]atrim=start={cut_a:.6f},asetpts=PTS-STARTPTS[a2];[a1][a2]concat=n=2:v=0:a=1[aout]"
        cmd = [
            FFMPEG_CMD, "-y",
            "-i", str(file_b_path),
            "-i", str(file_a_path),
            "-filter_complex", fg,
            "-map", "[aout]",
            "-c:a", "libmp3lame", "-b:a", "320k",
            str(out_file)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0 or not out_file.exists():
            raise RuntimeError(f"Fallo concatenando audio en FFmpeg: {res.stderr}")

        # Align word timestamps:
        # Part 1 (Hook): words from Call B up to cut_b
        words_p1 = [dict(w) for w in words_b[:count_b]]

        # Part 2 (Body): words from Call A starting from cut_a, shifted by time_offset
        time_offset = cut_b - cut_a
        words_p2_raw = words_a[count_a:]

        shifted_words_p2 = []
        for w in words_p2_raw:
            sw = dict(w)
            sw["start"] = round(max(cut_b, float(w.get("start", 0.0)) + time_offset), 3)
            sw["end"] = round(max(cut_b + 0.05, float(w.get("end", 0.0)) + time_offset), 3)
            shifted_words_p2.append(sw)

        final_words = words_p1 + shifted_words_p2
        logger.info(f"Locución Híbrida ElevenLabs (Hook a {speed_hook_val:.2f}x + Guión a {speed_full:.2f}x) ensamblada con éxito: {len(final_words)} palabras alineadas en {out_file.name}")
        return str(out_file), final_words, {
            "words": final_words, "speed_full": speed_full,
            "speed_hook": speed_hook_val, "synthesis_mode": "hybrid",
        }

    except Exception as e:
        logger.warning(f"Error en síntesis híbrida ElevenLabs ({e}), ejecutando síntesis de respaldo con guión completo...")
        # Reuse the completed full request instead of charging for it twice.
        if file_a_path.exists():
            audio_bytes, words, align_dict = audio_bytes_a, words_a, align_a
        else:
            raise
        align_dict = dict(align_dict, speed_full=speed_full,
                          speed_hook=None, synthesis_mode="full_fallback")
        with open(out_file, "wb") as f:
            f.write(audio_bytes)
        return str(out_file), words, align_dict
    finally:
        for p in [file_b_path, file_a_path]:
            if p.exists():
                try:
                    os.remove(p)
                except Exception:
                    pass


def generate_elevenlabs_hook_audio(
    api_key: str,
    voice_id: str,
    output_path: str | Path,
    hook_text: str = "Here's a new Steam game.",
    model_id: str = "eleven_v4",
) -> Tuple[str, float]:
    """Generate just the opening hook phrase audio with ElevenLabs to accurately measure its spoken duration."""
    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key.strip(),
        "Content-Type": "application/json",
    }
    payload = {
        "text": hook_text.strip(),
        "model_id": model_id or "eleven_v4",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "speed": 1.1,
        }
    }
    
    logger.info(f"Generando locución aislada del gancho Steam ('{hook_text}') con voz {voice_id[:8]}...")
    resp = _api_usage.usage_post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code == 200:
        with open(out_file, "wb") as f:
            f.write(resp.content)
        dur = get_audio_duration(out_file)
        if dur <= 0.3:
            dur = 1.6
        dur = round(dur, 2)
        logger.info(f"Gancho Steam generado con éxito: duración exacta de {dur:.2f}s ({out_file.name})")
        return str(out_file), dur
    else:
        logger.warning(f"No se pudo generar audio del gancho aislado ({resp.status_code}): {resp.text}. Duración por defecto: 1.6s")
        return "", 1.6


def align_script_words(
    audio_path: str | Path,
    script_text: str = "",
    target_duration: float = 30.0,
    known_hook_duration: Optional[float] = None,
) -> Dict[str, Any]:
    """Extract word-level and sentence-level timestamps from voiceover audio.
    
    Tries faster-whisper/whisper if available, otherwise calculates an intelligent
    rhythmic alignment based on spoken syllables and punctuation pauses, respecting known_hook_duration.
    """
    total_dur = get_audio_duration(audio_path)
    if total_dur <= 0.5:
        total_dur = target_duration if target_duration > 0 else 30.0

    # 1. Try local faster-whisper if installed
    try:
        from faster_whisper import WhisperModel
        logger.info("Using faster-whisper for word-level alignment...")
        model = WhisperModel("base", device="auto", compute_type="default")
        segments, info = model.transcribe(str(audio_path), word_timestamps=True, language="es")
        
        words_list = []
        sentences_list = []
        for segment in segments:
            sentences_list.append({
                "text": segment.text.strip(),
                "start": round(segment.start, 2),
                "end": round(segment.end, 2),
            })
            if segment.words:
                for w in segment.words:
                    clean_w = clean_subtitle_word(w.word.strip())
                    if clean_w:
                        words_list.append({
                            "word": clean_w,
                            "start": round(w.start, 2),
                            "end": round(w.end, 2),
                        })
        if words_list:
            return {
                "total_duration": total_dur,
                "words": words_list,
                "sentences": sentences_list,
                "method": "faster_whisper",
            }
    except Exception as e:
        logger.info(f"faster-whisper not available or skipped ({e}), using rhythmic script alignment.")

    # 2. Intelligent Rhythmic Alignment Fallback
    text = (script_text or "").strip()
    if not text:
        text = "Gameplay Trailer"

    # Split script into sentences and words
    cleaned_script = clean_script_text_for_voice(text)
    sentence_raws = [s.strip() for s in re.split(r'[.!?]+', cleaned_script) if s.strip()]
    if not sentence_raws:
        sentence_raws = [cleaned_script]
    all_raw_words = [w for w in cleaned_script.split() if w]
    if not all_raw_words:
        all_raw_words = ["Gameplay", "Trailer"]

    def norm_w(w):
        return re.sub(r"[^a-zA-Z0-9]", "", str(w).lower())

    hook_tokens_1 = ["heres", "a", "new", "steam", "game"]
    hook_tokens_2 = ["here", "is", "a", "new", "steam", "game"]
    hook_tokens_3 = ["here", "a", "new", "steam", "game"]
    hook_tokens_4 = ["new", "steam", "game"]

    norm_w_list = [norm_w(w) for w in all_raw_words[:8]]
    matched_hook_count = 0
    if len(norm_w_list) >= 5 and norm_w_list[:5] == hook_tokens_1:
        matched_hook_count = 5
    elif len(norm_w_list) >= 6 and norm_w_list[:6] == hook_tokens_2:
        matched_hook_count = 6
    elif len(norm_w_list) >= 5 and norm_w_list[:5] == hook_tokens_3:
        matched_hook_count = 5
    elif len(norm_w_list) >= 3 and norm_w_list[:3] == hook_tokens_4:
        matched_hook_count = 3
    else:
        for i in range(min(len(all_raw_words), 6)):
            if norm_w(all_raw_words[i]) in ("steam", "game"):
                matched_hook_count = max(matched_hook_count, i + 1)

    if known_hook_duration and matched_hook_count > 0 and len(all_raw_words) > matched_hook_count:
        hook_words = all_raw_words[:matched_hook_count]
        body_words = all_raw_words[matched_hook_count:]

        words_aligned = []
        # 1. Distribute hook words evenly across known_hook_duration
        h_dur_per_word = known_hook_duration / max(len(hook_words), 1)
        h_time = 0.0
        for w in hook_words:
            c_w = clean_subtitle_word(w)
            if not c_w:
                c_w = w
            words_aligned.append({
                "word": c_w,
                "start": round(h_time, 2),
                "end": round(h_time + h_dur_per_word, 2),
            })
            h_time += h_dur_per_word

        # 2. Distribute body words starting exactly at known_hook_duration
        cur_time = round(known_hook_duration, 2)
        remaining_time = max(0.5, total_dur - cur_time)
        body_weights = [max(len(w), 3) for w in body_words]
        sum_body_weights = sum(body_weights) if sum(body_weights) > 0 else 1

        for j, word in enumerate(body_words):
            w_dur = (body_weights[j] / sum_body_weights) * remaining_time
            w_s = round(cur_time, 2)
            w_e = round(cur_time + w_dur, 2)
            c_word = clean_subtitle_word(word)
            if not c_word:
                c_word = word
            words_aligned.append({
                "word": c_word,
                "start": w_s,
                "end": w_e,
            })
            cur_time = w_e

        return {
            "total_duration": total_dur,
            "words": words_aligned,
            "sentences": [{"text": cleaned_script, "start": 0.0, "end": total_dur}],
            "method": "rhythmic_alignment_hook_synced",
        }

    # Standard Rhythmic Alignment
    sentence_weights = [max(len(s), 5) for s in sentence_raws]
    sum_weights = sum(sentence_weights) if sum(sentence_weights) > 0 else 1

    cur_time = 0.0
    pause_between_sentences = 0.25  # seconds
    available_speech_time = max(total_dur - (len(sentence_raws) * pause_between_sentences), total_dur * 0.85)

    sentences_aligned = []
    words_aligned = []

    for i, s in enumerate(sentence_raws):
        s_weight = sentence_weights[i]
        s_dur = (s_weight / sum_weights) * available_speech_time
        s_start = round(cur_time, 2)
        s_end = round(cur_time + s_dur, 2)
        
        sentences_aligned.append({
            "text": s,
            "start": s_start,
            "end": s_end,
        })

        # Distribute words inside this sentence
        s_words = [w for w in s.split() if w]
        w_weights = [max(len(w), 3) for w in s_words]
        sum_w_weights = sum(w_weights) if sum(w_weights) > 0 else 1
        
        w_cur_time = s_start
        for j, word in enumerate(s_words):
            word_dur = (w_weights[j] / sum_w_weights) * (s_dur - 0.05)
            w_start = round(w_cur_time, 2)
            w_end = round(w_cur_time + word_dur, 2)
            c_word = clean_subtitle_word(word)
            if not c_word:
                c_word = word
            words_aligned.append({
                "word": c_word,
                "start": w_start,
                "end": w_end,
            })
            w_cur_time = w_end

        cur_time = s_end + pause_between_sentences

    return {
        "total_duration": total_dur,
        "words": words_aligned,
        "sentences": sentences_aligned,
        "method": "rhythmic_alignment",
    }


def hex_to_ass_color(hex_str: str, default: str = "&H0000E6FF") -> str:
    """Convert RGB hex color (#RRGGBB or RRGGBB) to ASS subtitle BGR format &H00BBGGRR."""
    if not hex_str:
        return default
    clean_hex = str(hex_str).strip().lstrip("#").lstrip("&H").lstrip("&h").rstrip("&")
    if len(clean_hex) == 3:
        clean_hex = "".join(c * 2 for c in clean_hex)
    if len(clean_hex) == 6:
        r, g, b = clean_hex[0:2], clean_hex[2:4], clean_hex[4:6]
        return f"&H00{b.upper()}{g.upper()}{r.upper()}"
    elif len(clean_hex) == 8 and clean_hex.startswith("00"):
        return f"&H{clean_hex.upper()}"
    return default


def generate_kinetic_ass_subtitles(
    alignment_data: Dict[str, Any],
    output_ass_path: str | Path,
    video_width: int = 1080,
    video_height: int = 1920,
    font_size: int = 86,
    word_by_word: bool = True,
    margin_v: int = 450,
    highlight_color: str = "&H0000E6FF",  # BGR format in ASS (default: Electric Yellow #FFE600)
    text_color: str = "&H00FFFFFF",
    outline_color: str = "&H00000000",
    skip_steam_hook: bool = True,
    steam_hook_mode: str = "classic",
    intro_subtitles_y: int = 340,
    intro_font_size: Optional[int] = None,
    intro_duration: float = 0.0,
) -> str:
    """Generate stylized ASS subtitles (single word-by-word popup or chunked, without punctuation) for TikTok/Reels/Shorts."""
    raw_words = alignment_data.get("words", [])
    intro_cutoff_time = float(intro_duration) if intro_duration > 0 else 0.0
    intro_word_count = 0

    if skip_steam_hook:
        if steam_hook_mode == "subtitles_above":
            filtered_words, hook_dur = filter_steam_hook_words(raw_words)
            intro_word_count = len(raw_words) - len(filtered_words)
            if intro_cutoff_time <= 0.0:
                intro_cutoff_time = hook_dur
            words = raw_words
        elif steam_hook_mode == "classic" and intro_cutoff_time > 0:
            # Resume captions at the splash cutoff, even mid-word. Do not discard
            # the whole spoken "Here's a new Steam game" prefix.
            words = []
            for word in raw_words:
                if float(word.get("end", 0.0)) > intro_cutoff_time:
                    clipped = dict(word)
                    clipped["start"] = max(intro_cutoff_time, float(word.get("start", 0.0)))
                    words.append(clipped)
        else:
            words, _ = filter_steam_hook_words(raw_words)
    else:
        words = raw_words

    out_path = Path(output_ass_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Normalize highlight color to ASS &H00BBGGRR format
    ass_highlight = hex_to_ass_color(highlight_color, default="&H0000E6FF")
    ass_text = hex_to_ass_color(text_color, default="&H00FFFFFF")

    def format_ass_time(sec: float) -> str:
        hours = int(sec // 3600)
        mins = int((sec % 3600) // 60)
        secs = int(sec % 60)
        centis = int(round((sec - int(sec)) * 100))
        return f"{hours:d}:{mins:02d}:{secs:02d}.{centis:02d}"

    intro_fs = intro_font_size or font_size
    outline_w = max(4, int(round(font_size * 0.075)))
    shadow_depth = max(6, int(round(font_size * 0.11)))
    shadow_color = "&H18000000"  # ~90% opacity deep dark cast shadow matching user reference image

    intro_outline_w = max(4, int(round(intro_fs * 0.075)))
    intro_shadow_depth = max(6, int(round(intro_fs * 0.11)))

    ass_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {video_width}",
        f"PlayResY: {video_height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Arial Black,{font_size},{ass_text},&H000000FF,{outline_color},{shadow_color},-1,0,0,0,100,100,1,0,1,{outline_w},{shadow_depth},2,40,40,{margin_v},1",
        f"Style: KineticWord,Arial Black,{font_size},{ass_highlight},&H000000FF,{outline_color},{shadow_color},-1,0,0,0,100,100,1,0,1,{outline_w},{shadow_depth},2,40,40,{margin_v},1",
        f"Style: IntroWord,Arial Black,{intro_fs},{ass_text},&H000000FF,{outline_color},{shadow_color},-1,0,0,0,100,100,1,0,1,{intro_outline_w},{intro_shadow_depth},5,40,40,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    intro_y_scaled = int(intro_subtitles_y * (video_height / 1920.0))

    if word_by_word:
        # High-retention single word-by-word pop mode (no punctuation)
        for i, w in enumerate(words):
            raw_word = clean_subtitle_word(str(w.get("word", "")).strip().upper())
            if not raw_word:
                continue

            w_start_val = float(w.get("start", 0.0))
            w_end_val = float(w.get("end", w_start_val + 0.35))
            if w_end_val <= w_start_val:
                w_end_val = w_start_val + 0.25

            # Extend slightly to next word start if close, preventing flickering
            if i + 1 < len(words):
                next_start = float(words[i + 1].get("start", w_end_val))
                if 0 < next_start - w_end_val < 0.1:
                    w_end_val = next_start

            w_start = format_ass_time(w_start_val)
            w_end = format_ass_time(w_end_val)

            # Check if this word belongs to the intro phrase
            is_intro = False
            if steam_hook_mode == "subtitles_above" and skip_steam_hook:
                if intro_cutoff_time > 0:
                    is_intro = (w_start_val < intro_cutoff_time)
                elif intro_word_count > 0:
                    is_intro = (i < intro_word_count)

            # Punch scale pop animation on appearance
            # Alternate highlight color on emphasized / longer words
            if len(raw_word) > 5 or i % 3 == 0:
                dialogue_text = r"{\c" + ass_highlight + r"&\t(0,70,\fscx112\fscy112)}" + raw_word
            else:
                dialogue_text = r"{\c" + ass_text + r"&\t(0,70,\fscx110\fscy110)}" + raw_word

            if is_intro:
                pos_tag = rf"{{\pos({video_width // 2},{intro_y_scaled})}}"
                ass_lines.append(
                    f"Dialogue: 0,{w_start},{w_end},IntroWord,,0,0,0,,{pos_tag}{dialogue_text}"
                )
            else:
                ass_lines.append(
                    f"Dialogue: 0,{w_start},{w_end},Default,,0,0,{margin_v},,{dialogue_text}"
                )
    else:
        # Chunked sentence mode (3-4 words per line with active word highlight, no punctuation)
        chunks = []
        chunk_size = 4
        for i in range(0, len(words), chunk_size):
            chunk = words[i:i + chunk_size]
            if chunk:
                chunks.append(chunk)

        for chunk in chunks:
            chunk_start = chunk[0]["start"]
            chunk_end = chunk[-1]["end"] + 0.15

            for active_idx, active_word in enumerate(chunk):
                w_start = format_ass_time(active_word["start"])
                w_end = format_ass_time(active_word["end"] if active_idx < len(chunk)-1 else chunk_end)

                formatted_words = []
                for idx, w in enumerate(chunk):
                    w_text = clean_subtitle_word(str(w.get("word", "")).upper())
                    if not w_text:
                        continue
                    if idx == active_idx:
                        formatted_words.append(r"{\c" + ass_highlight + r"&\t(0,80,\fscx108\fscy108)}" + w_text + r"{\r}")
                    else:
                        formatted_words.append(r"{\c" + ass_text + r"&}" + w_text)

                if formatted_words:
                    dialogue_text = " ".join(formatted_words)
                    ass_lines.append(
                        f"Dialogue: 0,{w_start},{w_end},Default,,0,0,{margin_v},,{dialogue_text}"
                    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ass_lines))

    return str(out_path)
