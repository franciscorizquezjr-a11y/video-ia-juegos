"""
video_sequencer.py — Program 2: Multi-Video AI Director & EDL Generator

Ingests all [video].breakdown.json files from a pool of footage and acts as a
creative short-form director to generate a high-retention Edit Decision List (EDL).

Retention Principles Enforced:
1. 3-Second Rule: Selects the highest hook potential moment across the pool for cut #1.
2. Dynamic Pacing: 1.5s - 4.0s cuts to maximize watch time.
3. Retention Arc: Hook (0-3s) -> Buildup (3-14s) -> Climax (14-24s) -> Payoff (24-30s).
4. Strict Timestamp Validation: Verifies bounds against source video durations.
"""

from __future__ import annotations

import api_usage as _api_usage

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import AppConfig
from schemas_v1 import CutDecision, TimelineEDL, VideoBreakdown
from utils import safe_json_parse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("video_sequencer")


class VideoSequencer:
    """Orchestrates pool ingestion and AI Director reasoning for multi-video assembly."""

    def __init__(
        self,
        config: AppConfig,
        prompts_dir: Optional[Path] = None,
    ) -> None:
        self.config = config
        self.prompts_dir = prompts_dir or config.prompts_dir

        prompt_path = self.prompts_dir / "sequencer_director_prompt.txt"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Sequencer prompt not found: {prompt_path}")
        self.prompt_template = prompt_path.read_text(encoding="utf-8")

        # Initialize Gemini client
        api_key = config.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not configured in .env or environment")

        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.model = config.gemini_model or "gemini-2.5-flash"

    def _generate_director_response(self, prompt: str, temperature: float = 0.4) -> str:
        """Execute prompt against AI Director with Groq (ultra-fast JSON) prioritized, falling back across multiple Gemini models."""
        from google.genai import types
        import requests
        import time

        last_error = None

        # 1. Try Groq first if key is configured (pure text/JSON reasoning is instant & immune to video API 503 spikes)
        groq_key = (getattr(self.config, "groq_api_key", None) or os.environ.get("GROQ_API_KEY", "")).strip().strip('"').strip("'")
        if groq_key:
            groq_models = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
            headers = {
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json",
            }
            for gm in groq_models:
                try:
                    logger.info(f"Prompting AI Director via Groq ({gm})...")
                    payload = {
                        "model": gm,
                        "messages": [
                            {"role": "system", "content": "You are an elite short-form video director. You MUST output ONLY valid JSON matching the requested EDL format."},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": temperature,
                        "response_format": {"type": "json_object"}
                    }
                    resp = _api_usage.usage_post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=30)
                    if resp.status_code == 200:
                        data = resp.json()
                        return data["choices"][0]["message"]["content"]
                    else:
                        logger.warning(f"Groq model {gm} returned {resp.status_code}: {resp.text[:100]}")
                except Exception as groq_err:
                    logger.warning(f"Groq error with model {gm}: {groq_err}")

        # 2. Try Gemini models (with retry and cross-model fallback)
        gemini_director_models = [
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-flash-latest",
            "gemini-flash-lite-latest",
            "gemini-3.8-flash",
            "gemini-3.7-flash",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-1.5-flash",
            "gemini-1.5-flash-8b",
            "gemini-1.5-pro",
            "gemini-2.5-pro",
            "gemini-3.1-pro-preview",
        ]
        candidate_models = []
        if self.model and self.model not in candidate_models and self.model != "gemini-2.5-pro":
            candidate_models.append(self.model)
        for m in gemini_director_models:
            if m not in candidate_models and m != "gemini-2.5-pro":
                candidate_models.append(m)

        for m in candidate_models:
            for attempt in range(2):
                try:
                    logger.info(f"Prompting AI Director ({m}) [attempt {attempt+1}]...")
                    config = types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=temperature,
                    )
                    response = _api_usage.usage_generate(self.client.models.generate_content, 
                        model=m,
                        contents=[prompt],
                        config=config,
                    )
                    if response and response.text:
                        return response.text
                except Exception as e:
                    err_str = str(e)
                    last_error = err_str
                    is_quota = any(k in err_str.lower() for k in ["429", "quota", "resource_exhausted", "rate_limit", "rate limit", "too many requests"])
                    is_not_found = any(k in err_str.lower() for k in ["404", "not_found", "not found", "no longer available"])
                    is_transient = any(k in err_str.lower() for k in ["503", "unavailable", "500", "overloaded", "high demand"])
                    if is_quota:
                        logger.warning(f"⚠️ AI Director Gemini model {m} hit quota limit: {err_str[:100]}. Switching to next model...")
                        break
                    elif is_not_found:
                        logger.warning(f"Gemini model {m} not available (404/deprecated): {err_str[:100]}. Switching to next model...")
                        break
                    elif is_transient:
                        logger.warning(f"Gemini model {m} unavailable (attempt {attempt+1}): {err_str[:100]}")
                        time.sleep(1.0 * (attempt + 1))
                    else:
                        logger.warning(f"Gemini model {m} failed: {err_str[:100]}")
                        break

        raise RuntimeError(f"Error del Director IA al secuenciar el vídeo: {last_error}")

    def load_breakdowns(self, breakdowns_dir: Path) -> List[VideoBreakdown]:
        """Load all breakdown JSON files from directory."""
        json_files = list(breakdowns_dir.glob("*.breakdown.json"))
        if not json_files:
            # Also check for regular .json if they match schema
            json_files = [f for f in breakdowns_dir.glob("*.json") if not f.name.endswith(".edl.json")]

        if not json_files:
            raise FileNotFoundError(f"No breakdown JSON files found in {breakdowns_dir}")

        breakdowns: List[VideoBreakdown] = []
        for file in json_files:
            try:
                bd = VideoBreakdown.load_json(file)
                breakdowns.append(bd)
            except Exception as e:
                logger.warning(f"Skipping {file.name}: failed to parse as VideoBreakdown ({e})")

        if not breakdowns:
            raise ValueError(f"No valid VideoBreakdown files could be loaded from {breakdowns_dir}")

        logger.info(f"Successfully loaded {len(breakdowns)} breakdown(s) from {breakdowns_dir}")
        return breakdowns

    def build_pool_catalog(self, breakdowns: List[VideoBreakdown]) -> Dict[str, Any]:
        """Construct a lightweight, structured catalog of all available scenes in the pool."""
        catalog = {
            "total_videos_in_pool": len(breakdowns),
            "videos": [],
        }

        for bd in breakdowns:
            video_entry = {
                "source_file": bd.source_file,
                "video_title": bd.video_title,
                "duration_seconds": bd.metadata.duration_seconds,
                "overall_summary": bd.overall_summary,
                "top_hook_scenes": bd.top_hook_scenes,
                "candidate_scenes": [],
            }

            for sc in bd.scenes:
                # Include essential decision-making fields
                video_entry["candidate_scenes"].append({
                    "scene_id": sc.scene_id,
                    "start_time": sc.start_time,
                    "end_time": sc.end_time,
                    "duration": sc.duration,
                    "visual_summary": sc.visual_summary,
                    "action_intensity": sc.action_intensity,
                    "hook_potential": sc.hook_potential,
                    "recommended_role": sc.recommended_role,
                    "has_onscreen_text": sc.has_onscreen_text,
                    "focal_point": sc.focal_point,
                    "score_9_16": getattr(sc, "score_9_16", 8 if getattr(sc, "is_9_16_acceptable", True) else 3),
                    "is_9_16_acceptable": getattr(sc, "is_9_16_acceptable", True),
                    "reason_9_16": getattr(sc, "reason_9_16", "") or "",
                })

            catalog["videos"].append(video_entry)

        return catalog

    def generate_edl(
        self,
        breakdowns: List[VideoBreakdown],
        target_duration: float = 30.0,
        framing_mode: str = "fit_16_9",
        variant_index: int = 1,
        total_variants: int = 1,
        hook_text: Optional[str] = None,
        exclude_opening_scenes: Optional[List[int]] = None,
        footage_is_already_9_16: bool = False,
    ) -> TimelineEDL:
        """Call Gemini Director to generate the high-retention Edit Decision List."""
        from google.genai import types

        pool_catalog = self.build_pool_catalog(breakdowns)
        min_duration = max(5.0, target_duration - 2.5)
        max_duration = target_duration + 2.5

        prompt = self.prompt_template.format(
            target_duration=target_duration,
            min_duration=min_duration,
            max_duration=max_duration,
            pool_catalog_json=json.dumps(pool_catalog, indent=2),
        )

        # Inject multi-video variant & diversity instructions if generating multiple videos
        if total_variants > 1:
            variant_directive = (
                f"\n\n## Multi-Video Campaign Directive (Variant {variant_index} of {total_variants})\n"
                f"- Specific Hook / Advertising Angle for this Video: \"{hook_text or 'Alternative hook angle'}\"\n"
                f"- VARIATION & DIVERSITY RULES (MANDATORY):\n"
                f"  1. The opening hook moment (Cut #1, 3.0s) MUST be distinct and different from other variants in the campaign.\n"
            )
            if exclude_opening_scenes:
                variant_directive += (
                    f"     CRITICAL: DO NOT use scene IDs {exclude_opening_scenes} as the opening hook (Cut #1). "
                    f"Select an alternative high-hook scene from the pool.\n"
                )
            variant_directive += (
                f"  2. Vary the scene progression, climax, and pacing. "
                f"While footage clips can repeat across the campaign, the sequence and spotlighted gameplay actions must feel fresh and unique.\n"
            )
            prompt += variant_directive

        # Inject 9:16 vertical directives
        if footage_is_already_9_16:
            prompt += (
                "\n\n## NATIVE 9:16 VERTICAL FOOTAGE DIRECTIVE:\n"
                "- ALL source footage is already natively recorded in 9:16 vertical orientation.\n"
                "- All scenes are 100% vertical-ready (score_9_16: 10). Select any scene freely based on narrative tension, gameplay interest, and pacing without any crop restrictions.\n"
                "- STRICT ANTI-REPETITION RULE: NEVER repeat the same scene or clip when other clips are available in the pool.\n"
            )
        elif framing_mode in ("vertical_split_blur", "blur_bg", "center_crop"):
            prompt += (
                "\n\n## 9:16 VERTICAL SELECTION & ANTI-REPETITION DIRECTIVE:\n"
                "- The final video is formatted in 9:16 VERTICAL orientation.\n"
                "- Each scene in the catalog has a `score_9_16` (1 to 10) and `reason_9_16`.\n"
                "- PRIORITY RULES:\n"
                "  1. HIGH PREFERENCE: Prioritize scenes with `score_9_16` >= 7 (centered action, clear focal point).\n"
                "  2. USABLE DIVERSITY: If scenes with score >= 7 are limited, freely select scenes with `score_9_16` 5-6 to guarantee montage variety and pacing.\n"
                "  3. STRICT ANTI-REPETITION (CRITICAL): NEVER repeat the exact same scene or clip over and over just to get a higher score! A montage with different gameplay scenes is always better than looping the same clip multiple times.\n"
                "  4. EXCLUSION: Only avoid scenes with `score_9_16` <= 3 (e.g. split-screen co-op where crop cuts players, unreadable wide spreadsheets) unless no other footage is available.\n"
            )

        # Slightly higher temperature for variants to promote creative divergence
        temp = 0.5 if variant_index == 1 else 0.7
        raw_text = self._generate_director_response(prompt, temperature=temp)
        raw_text = re.sub(r"^```json\s*", "", raw_text.strip(), flags=re.IGNORECASE)
        raw_text = re.sub(r"^```\s*", "", raw_text.strip())
        raw_text = re.sub(r"\s*```$", "", raw_text.strip())

        data = safe_json_parse(raw_text)
        data["aspect_ratio"] = "16:9"

        # Build source duration lookup table for bounds checking
        durations_lookup = {}
        for bd in breakdowns:
            durations_lookup[bd.source_file] = bd.metadata.duration_seconds
            durations_lookup[Path(bd.source_file).name] = bd.metadata.duration_seconds

        # Sanitize and validate cut bounds (Enforce Hook = 3.0s, All cuts >= 2.0s)
        if "cuts" in data and isinstance(data["cuts"], list):
            for i, cut in enumerate(data["cuts"]):
                cut["order"] = i + 1
                if "framing_mode" not in cut or not cut["framing_mode"]:
                    cut["framing_mode"] = framing_mode

                source_vid = cut.get("source_video", "")
                max_dur = durations_lookup.get(source_vid) or durations_lookup.get(Path(source_vid).name)

                # If source video not exact match, try matching by basename
                if max_dur is None:
                    for k, dur in durations_lookup.items():
                        if Path(k).stem in source_vid or Path(source_vid).stem in k:
                            cut["source_video"] = k
                            max_dur = dur
                            break

                s_in = max(0.0, float(cut.get("source_in", 0.0)))

                if i == 0:
                    # RULE: Opening hook clip (Cut #1) MUST be exactly 3.0s
                    target_dur = 3.0
                    if max_dur is not None and max_dur > 0:
                        if s_in + target_dur > max_dur:
                            s_in = max(0.0, max_dur - target_dur)
                        s_out = min(max_dur, s_in + target_dur)
                    else:
                        s_out = s_in + target_dur
                else:
                    # RULE: Duration between cuts MUST be between 1.5s and 3.0s (never >3s without a cut)
                    s_out = float(cut.get("source_out", s_in + 2.5))
                    if s_out - s_in < 1.5:
                        s_out = s_in + 1.5
                    if s_out - s_in > 3.0:
                        s_out = s_in + 3.0
                    if max_dur is not None and max_dur > 0:
                        if s_out > max_dur:
                            s_out = max_dur
                            if s_out - s_in < 1.5:
                                s_in = max(0.0, s_out - 1.5)
                        if s_in >= s_out:
                            s_in = max(0.0, s_out - 1.5)

                if "description" not in cut or not cut["description"]:
                    cut["description"] = (
                        cut.get("narrative_alignment")
                        or cut.get("reason")
                        or cut.get("visual_description")
                        or f"Gameplay cut #{i+1}"
                    )
                if "purpose" not in cut or not cut["purpose"]:
                    cut["purpose"] = "hook" if i == 0 else "buildup"
                if "speed" not in cut or cut["speed"] is None:
                    cut["speed"] = 1.0

                cut["source_in"] = round(s_in, 2)
                cut["source_out"] = round(s_out, 2)
                cut["duration"] = round(s_out - s_in, 2)

        edl = TimelineEDL.model_validate(data)
        edl.validate_timings()

        logger.info(
            f"EDL created: '{edl.title}' with {len(edl.cuts)} cuts, total duration {edl.total_duration:.2f}s"
        )
        return edl

    def generate_v2_semantic_edl(
        self,
        breakdowns: List[VideoBreakdown],
        voiceover_timeline: List[Dict[str, Any]],
        target_duration: float = 30.0,
        framing_mode: str = "center_crop",
        footage_is_already_9_16: bool = False,
    ) -> TimelineEDL:
        """Call AI Director with semantic voiceover timeline to align gameplay cuts to spoken narration."""
        from google.genai import types

        prompt_path = self.prompts_dir / "v2_semantic_sequencer_prompt.txt"
        if prompt_path.exists():
            template = prompt_path.read_text(encoding="utf-8")
        else:
            template = self.prompt_template

        pool_catalog = self.build_pool_catalog(breakdowns)

        prompt = template.format(
            target_duration=round(target_duration, 1),
            voiceover_timeline_json=json.dumps(voiceover_timeline, indent=2, ensure_ascii=False),
            pool_catalog_json=json.dumps(pool_catalog, indent=2, ensure_ascii=False),
        )

        if footage_is_already_9_16:
            prompt += (
                "\n\n## NATIVE 9:16 VERTICAL FOOTAGE DIRECTIVE:\n"
                "- ALL source footage is already natively recorded in 9:16 vertical orientation.\n"
                "- All scenes are 100% vertical-ready (score_9_16: 10). Match scenes freely to the voiceover narrative without crop restrictions.\n"
                "- STRICT ANTI-REPETITION: NEVER repeat the same scene or clip when other clips are available in the pool.\n"
            )
        elif framing_mode in ("center_crop", "vertical_split_blur", "blur_bg"):
            prompt += (
                "\n\n## 9:16 VERTICAL SELECTION & ANTI-REPETITION DIRECTIVE:\n"
                "- The final video is formatted in 9:16 VERTICAL orientation.\n"
                "- Each scene in the catalog has a `score_9_16` (1 to 10) and `reason_9_16`.\n"
                "- PRIORITY RULES:\n"
                "  1. HIGH PREFERENCE: Prioritize scenes with `score_9_16` >= 7 (centered action, readable gameplay).\n"
                "  2. USABLE DIVERSITY: If scenes with score >= 7 are limited, freely select scenes with `score_9_16` 5-6 to guarantee visual diversity that matches the voiceover pacing.\n"
                "  3. STRICT ANTI-REPETITION (CRITICAL): NEVER repeat the same gameplay scene or clip over and over just to get a higher 9:16 score! Dynamic gameplay variety matching the spoken words is essential.\n"
                "  4. EXCLUSION: Only avoid scenes with `score_9_16` <= 3 (e.g. split-screen co-op where crop cuts players, unreadable wide spreadsheets) unless no other footage is available.\n"
            )

        raw_text = self._generate_director_response(prompt, temperature=0.4)
        raw_text = re.sub(r"^```json\s*", "", raw_text.strip(), flags=re.IGNORECASE)
        raw_text = re.sub(r"^```\s*", "", raw_text.strip())
        raw_text = re.sub(r"\s*```$", "", raw_text.strip())

        data = safe_json_parse(raw_text)
        data["aspect_ratio"] = "16:9" if framing_mode == "fit_16_9" else "9:16"

        durations_lookup = {}
        for bd in breakdowns:
            durations_lookup[bd.source_file] = bd.metadata.duration_seconds
            durations_lookup[Path(bd.source_file).name] = bd.metadata.duration_seconds

        if "cuts" in data and isinstance(data["cuts"], list):
            total_dur = 0.0
            for i, cut in enumerate(data["cuts"]):
                cut["order"] = i + 1
                if "framing_mode" not in cut or not cut["framing_mode"]:
                    cut["framing_mode"] = framing_mode
                if "description" not in cut or not cut["description"]:
                    cut["description"] = (
                        cut.get("narrative_alignment")
                        or cut.get("reason")
                        or cut.get("visual_description")
                        or f"Gameplay cut #{i+1}"
                    )
                if "purpose" not in cut or not cut["purpose"]:
                    cut["purpose"] = "hook" if i == 0 else "buildup"
                if "speed" not in cut or cut["speed"] is None:
                    cut["speed"] = 1.0

                s_file = cut.get("source_video", "")
                max_len = durations_lookup.get(s_file, durations_lookup.get(Path(s_file).name, 180.0))
                s_in = max(0.0, float(cut.get("source_in", 0.0)))
                s_out = min(max_len, float(cut.get("source_out", s_in + 3.0)))
                if s_out <= s_in:
                    s_out = min(max_len, s_in + 3.0)
                dur = s_out - s_in
                if dur < 1.5 and max_len > (s_in + 1.5):
                    s_out = min(max_len, s_in + 1.5)
                elif dur > 3.0:
                    s_out = min(max_len, s_in + 3.0)
                cut["source_in"] = round(s_in, 2)
                cut["source_out"] = round(s_out, 2)
                cut["duration"] = round(s_out - s_in, 2)
                total_dur += cut["duration"]

            data["total_duration"] = round(total_dur, 2)
            if "target_duration" not in data or not data["target_duration"]:
                data["target_duration"] = round(target_duration, 1)

        edl = TimelineEDL.model_validate(data)
        edl.validate_timings()
        logger.info(f"V2 Semantic EDL created: '{edl.title}' with {len(edl.cuts)} cuts, total duration {edl.total_duration:.2f}s")
        return edl


def main() -> None:
    parser = argparse.ArgumentParser(description="Program 2: Multi-Video AI Director & EDL Generator")
    parser.add_argument("--breakdowns-dir", "-b", type=str, default="data/breakdowns", help="Directory containing *.breakdown.json files")
    parser.add_argument("--target-duration", "-t", type=float, default=30.0, help="Target duration in seconds (default: 30.0)")
    parser.add_argument("--framing", "-f", type=str, choices=["fit_16_9", "blur_bg", "center_crop"], default="fit_16_9", help="Framing mode")
    parser.add_argument("--output", "-o", type=str, default="output/trabajo/timeline_edl.json", help="Path to save generated timeline_edl.json")
    parser.add_argument("--model", "-m", type=str, default=None, help="Gemini model identifier")
    args = parser.parse_args()

    config = AppConfig.from_env()
    if args.model:
        config.gemini_model = args.model

    breakdowns_path = Path(args.breakdowns_dir)
    if not breakdowns_path.exists():
        logger.error(f"Breakdowns directory does not exist: {breakdowns_path}")
        sys.exit(1)

    sequencer = VideoSequencer(config=config)
    breakdowns = sequencer.load_breakdowns(breakdowns_path)

    edl = sequencer.generate_edl(
        breakdowns=breakdowns,
        target_duration=args.target_duration,
        framing_mode=args.framing,
    )

    output_file = Path(args.output)
    edl.save_json(output_file)
    logger.info(f"Timeline EDL saved to: {output_file.resolve()}")


if __name__ == "__main__":
    main()
