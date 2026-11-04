"""
schemas_v1.py — Data Contracts & Schemas for V1 Multi-Video Pipeline

Defines structured data types for:
- VideoBreakdown (Program 1 output): Scene-by-scene and second-by-second breakdown.
- TimelineEDL (Program 2 output): Edit Decision List for multi-video assembly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# =============================================================================
# Program 1 Schemas: Video Breakdown
# =============================================================================

class SecondData(BaseModel):
    """Detailed information for an individual second in a video."""
    second: int = Field(..., description="Timestamp in whole seconds from video start (0, 1, 2...)")
    visual_action: str = Field(..., description="Concise description of visual action happening in this second")
    audio_event: str = Field(default="", description="Key audio event (sound effect, voice, silence, music cue)")
    visual_energy_score: int = Field(default=5, ge=1, le=10, description="Visual action/motion intensity (1=static menu, 10=intense climax)")
    audio_energy_score: int = Field(default=5, ge=1, le=10, description="Acoustic energy/volume level (1=silent, 10=loud impact/shout)")
    tags: List[str] = Field(default_factory=list, description="Keywords describing the moment (e.g., 'drift', 'fail', 'near_miss')")


class ShotScene(BaseModel):
    """Represents a coherent camera shot or continuous scene segment."""
    scene_id: int = Field(..., description="Incremental scene identifier (1, 2, 3...)")
    start_time: float = Field(..., ge=0.0, description="Start time in seconds")
    end_time: float = Field(..., ge=0.0, description="End time in seconds")
    duration: float = Field(..., ge=0.0, description="Duration in seconds (end_time - start_time)")
    camera_shot_type: str = Field(default="medium", description="Camera shot type (wide, close-up, dynamic, top-down, etc.)")
    visual_summary: str = Field(..., description="Summary of what is happening in this continuous shot")
    action_intensity: int = Field(default=5, ge=1, le=10, description="Action intensity score from 1 to 10")
    hook_potential: int = Field(default=5, ge=1, le=10, description="Viral/hook potential for the first 3 seconds (1 to 10)")
    audio_description: str = Field(default="", description="Description of audio track during this shot")
    has_onscreen_text: bool = Field(default=False, description="Whether this shot contains prominent static UI text or subtitles that might clip in 9:16")
    focal_point: str = Field(default="center", description="Where the main visual focus is located: 'center', 'left', 'right'")
    score_9_16: int = Field(
        default=7,
        ge=1,
        le=10,
        description="Suitability score for 9:16 vertical crop (1-10). 10=action/character perfectly centered, 7-8=good gameplay with acceptable corner HUD crop, 5-6=usable/secondary to avoid repetition, 1-4=poor/unusable (split-screen or action at extreme edges)."
    )
    is_9_16_acceptable: bool = Field(
        default=True,
        description="Whether this shot is acceptable in 9:16 vertical framing (True if score_9_16 >= 5)."
    )
    reason_9_16: Optional[str] = Field(
        default="",
        description="Reason why this shot is or isn't acceptable in 9:16 vertical framing."
    )
    recommended_role: str = Field(
        default="buildup",
        description="Suggested narrative role if used in a short-form video ('hook', 'buildup', 'climax', 'payoff', etc.)"
    )

    @model_validator(mode="before")
    @classmethod
    def sanitize_scene_timings(cls, data: Any) -> Any:
        if isinstance(data, dict):
            try:
                st = max(0.0, float(data.get("start_time", 0.0)))
                et = max(0.0, float(data.get("end_time", 0.0)))
                if et < st:
                    st, et = et, st
                dur = data.get("duration")
                if dur is None or float(dur) <= 0.0:
                    dur = max(0.1, round(et - st, 2))
                else:
                    dur = max(0.1, abs(float(dur)))
                if et <= st:
                    et = round(st + dur, 2)
                data["start_time"] = round(st, 2)
                data["end_time"] = round(et, 2)
                data["duration"] = round(dur, 2)

                # 9:16 Score & Acceptability Sanitization
                raw_score = data.get("score_9_16")
                raw_acc = data.get("is_9_16_acceptable")
                if raw_score is not None:
                    try:
                        sc_val = int(round(float(raw_score)))
                        sc_val = max(1, min(10, sc_val))
                    except Exception:
                        sc_val = 7
                    data["score_9_16"] = sc_val
                    if raw_acc is None:
                        data["is_9_16_acceptable"] = (sc_val >= 5)
                    else:
                        data["is_9_16_acceptable"] = bool(raw_acc)
                elif raw_acc is not None:
                    acc_bool = bool(raw_acc)
                    data["is_9_16_acceptable"] = acc_bool
                    data["score_9_16"] = 8 if acc_bool else 3
                else:
                    data["score_9_16"] = 7
                    data["is_9_16_acceptable"] = True
            except Exception:
                pass
        return data

    @field_validator("duration", mode="before")
    @classmethod
    def compute_or_validate_duration(cls, v: Any) -> float:
        try:
            val = float(v)
            return max(0.1, abs(val))
        except (ValueError, TypeError):
            return 1.0


class VideoMetadata(BaseModel):
    """Technical metadata of the source video."""
    duration_seconds: float = Field(..., ge=0.0)
    width: int = Field(default=1920)
    height: int = Field(default=1080)
    fps: float = Field(default=30.0)
    filename: str = Field(default="")


class VideoBreakdown(BaseModel):
    """Complete multimodal breakdown of a single video file."""
    source_file: str = Field(..., description="Relative or absolute path to the source video")
    video_title: str = Field(default="", description="Descriptive title or context of the footage")
    overall_summary: str = Field(default="", description="Executive summary of the whole video content")
    metadata: VideoMetadata
    scenes: List[ShotScene] = Field(default_factory=list, description="Scene/shot breakdown")
    seconds_breakdown: List[SecondData] = Field(default_factory=list, description="Second-by-second breakdown")
    top_hook_scenes: List[int] = Field(default_factory=list, description="List of scene_id values recommended as opening hooks")
    final_splash_start_time: Optional[float] = Field(
        default=None,
        description="Timestamp in seconds where the final outro/splash screen, logo, release date or wishlist screen begins"
    )

    def save_json(self, destination_path: Path) -> None:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with open(destination_path, "w", encoding="utf-8") as f:
            f.write(self.model_dump_json(indent=2))

    @classmethod
    def load_json(cls, file_path: Path) -> VideoBreakdown:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.model_validate(data)


# =============================================================================
# Program 2 Schemas: Edit Decision List (EDL)
# =============================================================================

class CutDecision(BaseModel):
    """A single cut selected from the video pool for the final short-form montage."""
    order: int = Field(..., ge=1, description="Sequential position in the final video (1, 2, 3...)")
    source_video: str = Field(..., description="Path or identifier of the source video from which this cut is taken")
    source_in: float = Field(..., ge=0.0, description="Start timestamp in the source video (seconds)")
    source_out: float = Field(..., ge=0.0, description="End timestamp in the source video (seconds)")
    duration: float = Field(..., ge=0.0, description="Duration in seconds (source_out - source_in)")
    purpose: str = Field(
        default="buildup",
        description="Narrative retention role of this clip ('hook', 'buildup', 'climax', 'payoff', etc.)"
    )
    description: str = Field(default="", description="Why this clip was selected and what happens in it")
    narrative_alignment: Optional[str] = Field(default=None, description="Semantic alignment with voiceover line")
    framing_mode: str = Field(
        default="center_crop",
        description="Framing technique for this cut ('fit_16_9', 'blur_bg', or 'center_crop')"
    )
    speed: float = Field(default=1.0, ge=0.25, le=4.0, description="Playback speed multiplier")


class TimelineEDL(BaseModel):
    """Edit Decision List generated by the AI Director."""
    title: str = Field(default="Short-Form Montage", description="Concept title for the generated short")
    concept_summary: str = Field(default="", description="Narrative thesis or angle behind the clip selection")
    target_duration: float = Field(default=30.0, ge=1.0, le=180.0, description="Requested target duration in seconds")
    total_duration: float = Field(default=0.0, ge=0.0, description="Sum of all cut durations")
    aspect_ratio: str = Field(default="16:9", description="Target output aspect ratio")
    pacing_strategy: str = Field(default="rapid_cuts_with_action_climax", description="Editing pacing style applied")
    cuts: List[CutDecision] = Field(default_factory=list, description="Ordered list of cuts")

    def save_json(self, destination_path: Path) -> None:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with open(destination_path, "w", encoding="utf-8") as f:
            f.write(self.model_dump_json(indent=2))

    @classmethod
    def load_json(cls, file_path: Path) -> TimelineEDL:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.model_validate(data)

    def validate_timings(self) -> None:
        """Validate mathematical consistency of cuts."""
        calculated_total = 0.0
        for i, cut in enumerate(self.cuts, 1):
            if cut.source_out <= cut.source_in:
                raise ValueError(
                    f"Cut #{i} ({cut.source_video}) has invalid timestamps: "
                    f"source_in={cut.source_in} >= source_out={cut.source_out}"
                )
            expected_dur = round(cut.source_out - cut.source_in, 3)
            if abs(cut.duration - expected_dur) > 0.05:
                # auto-correct minor duration mismatch
                cut.duration = expected_dur
            calculated_total += cut.duration

        self.total_duration = round(calculated_total, 2)
