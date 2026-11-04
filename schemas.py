"""
schemas.py — Data Contracts & Validation for the AI Video Pipeline

This module defines the structured data types that flow between pipeline steps.
It serves as the "contract layer" — every piece of data passed between modules
conforms to one of these schemas.

Architecture Role:
- Scene, Storyboard: Output of Step 1 (LLM creative analysis)
- Clip, EditBreakdown: Output of Step 4 (LLM edit breakdown)
- PipelineContext: Accumulates state across all pipeline steps

Design Decisions:
- Dataclasses over Pydantic: keeps dependencies minimal while providing
  type safety and IDE support.
- Validation functions are standalone: they accept raw dicts (from JSON)
  and return typed dataclass instances, or raise ValidationError.
- PipelineContext is mutable: each pipeline step reads from and writes
  to the same context object, providing a single source of truth.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from utils import ValidationError

logger = logging.getLogger(__name__)


# =============================================================================
# Step 1 Schemas — Creative Analysis Output
# =============================================================================

@dataclass
class Scene:
    """Represents a single scene in the creative storyboard.

    A scene is a conceptual unit of the short-form video, describing
    WHAT should appear and WHY — not HOW to edit it technically.

    Attributes:
        order: Position in the narrative sequence (1-indexed).
        description: Detailed description of what happens in this scene.
        purpose: Role in the narrative arc (hook, buildup, climax, payoff, etc.).
        visual_notes: Specific visual details that make this moment compelling.
        duration_suggestion: Qualitative duration hint (short, medium, long).
    """
    order: int
    description: str
    purpose: str
    visual_notes: str = ""
    duration_suggestion: str = ""


@dataclass
class Storyboard:
    """Creative analysis output from Step 1 of the pipeline.

    Contains the LLM's creative vision for the short-form video,
    including narrative structure, emotional progression, and the
    voiceover script. This is purely creative — no timestamps or
    technical editing instructions.

    Attributes:
        title: Catchy title for the short-form video.
        hook: Description of the opening hook and why it grabs attention.
        narrative_flow: Overall narrative arc description.
        emotional_progression: Sequence of emotions the viewer should feel.
        pacing: Description of the pacing strategy.
        scenes: Ordered list of Scene objects forming the narrative.
        voiceover_script: Complete voiceover text for TTS generation.
    """
    title: str
    hook: str
    narrative_flow: str
    emotional_progression: list[str]
    pacing: str
    scenes: list[Scene]
    voiceover_script: str


# =============================================================================
# Step 4 Schemas — Edit Breakdown Output
# =============================================================================

@dataclass
class Clip:
    """Represents a single clip in the editing breakdown.

    A clip is a precise segment of the SOURCE video, defined by
    exact timestamps. The LLM selects and orders these clips to
    realize the creative vision from the storyboard.

    Attributes:
        order: Position in the final video sequence (1-indexed).
        start: Start time in the source video (seconds).
        end: End time in the source video (seconds).
        purpose: Role in the narrative (hook, buildup, climax, etc.).
        description: Why this clip was chosen and what it shows.
        transition: Transition type (currently only 'cut' is supported).
        speed: Playback speed multiplier (1.0 = normal).
    """
    order: int
    start: float
    end: float
    purpose: str
    description: str = ""
    transition: str = "cut"
    speed: float = 1.0

    @property
    def duration(self) -> float:
        """Calculate the duration of this clip in seconds.

        Returns:
            Duration as end - start, in seconds.
        """
        return self.end - self.start


@dataclass
class EditBreakdown:
    """Structured editing plan output from Step 4 of the pipeline.

    This is the bridge between creative intent (storyboard) and
    technical execution (FFmpeg commands). Contains frame-accurate
    timestamps that Python uses to generate FFmpeg commands.

    The LLM NEVER generates FFmpeg commands — it only fills in this
    data structure. Python reads it and handles all technical execution.

    Attributes:
        total_duration: Expected total duration of the edited video (seconds).
        clips: Ordered list of Clip objects to extract from the source.
        audio_sync_notes: Notes about how clips align with voiceover moments.
    """
    total_duration: float
    clips: list[Clip]
    audio_sync_notes: str = ""


# =============================================================================
# Pipeline State — Accumulates Results Across Steps
# =============================================================================

@dataclass
class PipelineContext:
    """Accumulates state across all pipeline steps.

    This is the single source of truth for a pipeline run. Each step
    reads from previous steps' outputs and writes its own results here.

    The context is passed through the entire pipeline, eliminating the
    need for complex return values or global state.

    Attributes:
        run_id: Unique identifier for this pipeline run.
        input_video: Path to the source gameplay/trailer video.
        storyboard: Parsed creative storyboard (Step 1 output).
        storyboard_raw: Raw JSON dict from the LLM (Step 1, for reference).
        voice_audio_path: Path to the generated voiceover MP3 (Step 2 output).
        audio_duration: Exact duration of the voiceover in seconds (Step 3 output).
        edit_breakdown: Parsed editing plan (Step 4 output).
        edit_breakdown_raw: Raw JSON dict from the LLM (Step 4, for reference).
        clip_paths: Paths to individual cut clips (Steps 5-6 output).
        concatenated_video_path: Path to the concatenated video (Step 6 output).
        merged_video_path: Path to the video with merged audio (Step 7 output).
        final_output_path: Path to the final exported video (Step 8 output).
    """
    run_id: str
    input_video: Path

    # Step 1 outputs
    storyboard: Storyboard | None = None
    storyboard_raw: dict | None = None

    # Step 2-3 outputs
    voice_audio_path: Path | None = None
    audio_duration: float = 0.0

    # Step 4 outputs
    edit_breakdown: EditBreakdown | None = None
    edit_breakdown_raw: dict | None = None

    # Steps 5-6 outputs
    clip_paths: list[Path] = field(default_factory=list)
    concatenated_video_path: Path | None = None

    # Steps 7-8 outputs
    merged_video_path: Path | None = None
    final_output_path: Path | None = None


# =============================================================================
# Validation Functions
#
# These functions accept raw dicts (from JSON parsing) and return
# typed dataclass instances. They validate structure and constraints,
# raising ValidationError with descriptive messages on failure.
# =============================================================================

def validate_storyboard(data: dict) -> Storyboard:
    """Validate and parse a raw dict into a Storyboard instance.

    Checks that all required fields are present and correctly typed.
    Scenes are parsed into Scene dataclass instances.

    Args:
        data: Raw dictionary from LLM JSON output.

    Returns:
        A validated Storyboard instance.

    Raises:
        ValidationError: If required fields are missing or malformed,
                        with a descriptive message listing all issues.
    """
    errors: list[str] = []

    # Check required top-level fields
    required_fields = ["title", "scenes", "voiceover_script"]
    for field_name in required_fields:
        if field_name not in data:
            errors.append(f"Missing required field: '{field_name}'")

    if errors:
        raise ValidationError(
            "Storyboard validation failed:\n"
            + "\n".join(f"  • {e}" for e in errors)
        )

    # Validate scenes list
    raw_scenes = data.get("scenes", [])
    if not isinstance(raw_scenes, list) or len(raw_scenes) == 0:
        raise ValidationError(
            "Storyboard must contain at least one scene. "
            f"Got: {type(raw_scenes).__name__} with {len(raw_scenes) if isinstance(raw_scenes, list) else 0} items."
        )

    # Parse scenes into Scene dataclass instances
    scenes: list[Scene] = []
    for i, raw_scene in enumerate(raw_scenes):
        if not isinstance(raw_scene, dict):
            errors.append(f"Scene {i + 1} is not a dict: {type(raw_scene).__name__}")
            continue

        # Require at least order, description, and purpose
        for key in ["description", "purpose"]:
            if key not in raw_scene:
                errors.append(f"Scene {i + 1} missing required field: '{key}'")

        if not errors:
            scenes.append(Scene(
                order=raw_scene.get("order", i + 1),
                description=raw_scene["description"],
                purpose=raw_scene["purpose"],
                visual_notes=raw_scene.get("visual_notes", ""),
                duration_suggestion=raw_scene.get("duration_suggestion", ""),
            ))

    if errors:
        raise ValidationError(
            "Storyboard scene validation failed:\n"
            + "\n".join(f"  • {e}" for e in errors)
        )

    # Build and return the Storyboard instance
    storyboard = Storyboard(
        title=data.get("title", "Untitled"),
        hook=data.get("hook", ""),
        narrative_flow=data.get("narrative_flow", ""),
        emotional_progression=data.get("emotional_progression", []),
        pacing=data.get("pacing", ""),
        scenes=scenes,
        voiceover_script=data["voiceover_script"],
    )

    logger.info(
        f"Storyboard validated: '{storyboard.title}' with "
        f"{len(storyboard.scenes)} scenes"
    )
    return storyboard


def validate_edit_breakdown(data: dict, expected_duration: float) -> EditBreakdown:
    """Validate and parse a raw dict into an EditBreakdown instance.

    Checks structural validity and timing constraints. Warns (but does
    not error) if the total clip duration is more than 10% off from
    the expected duration — timing mismatches are common with LLMs
    and the pipeline handles them gracefully.

    Args:
        data: Raw dictionary from LLM JSON output.
        expected_duration: Expected total duration in seconds
                          (audio_duration + tail_padding).

    Returns:
        A validated EditBreakdown instance.

    Raises:
        ValidationError: If required fields are missing, clips have
                        invalid timestamp ranges, or structural issues.
    """
    errors: list[str] = []

    # Check for clips array
    if "clips" not in data:
        raise ValidationError("Edit breakdown must contain a 'clips' array.")

    raw_clips = data["clips"]
    if not isinstance(raw_clips, list) or len(raw_clips) == 0:
        raise ValidationError(
            "Edit breakdown must contain at least one clip. "
            f"Got: {type(raw_clips).__name__}"
        )

    # Parse and validate each clip
    clips: list[Clip] = []
    for i, raw_clip in enumerate(raw_clips):
        clip_id = f"Clip {i + 1}"

        if not isinstance(raw_clip, dict):
            errors.append(f"{clip_id} is not a dict: {type(raw_clip).__name__}")
            continue

        # Require start, end, and purpose
        for key in ["start", "end", "purpose"]:
            if key not in raw_clip:
                errors.append(f"{clip_id} missing required field: '{key}'")

        if f"{clip_id} missing required field: 'start'" in errors:
            continue
        if f"{clip_id} missing required field: 'end'" in errors:
            continue

        start = float(raw_clip["start"])
        end = float(raw_clip["end"])

        # Validate timestamp range
        if start < 0:
            errors.append(f"{clip_id} has negative start time: {start}")
        if end <= start:
            errors.append(
                f"{clip_id} has invalid range: start={start} >= end={end}"
            )

        if not errors or not any(clip_id in e for e in errors):
            clips.append(Clip(
                order=raw_clip.get("order", i + 1),
                start=start,
                end=end,
                purpose=raw_clip["purpose"],
                description=raw_clip.get("description", ""),
                transition=raw_clip.get("transition", "cut"),
                speed=float(raw_clip.get("speed", 1.0)),
            ))

    if errors:
        raise ValidationError(
            "Edit breakdown validation failed:\n"
            + "\n".join(f"  • {e}" for e in errors)
        )

    # Sort clips by order
    clips.sort(key=lambda c: c.order)

    # Calculate total duration and compare with expected
    total_clip_duration = sum(c.duration for c in clips)
    total_duration = data.get("total_duration", total_clip_duration)

    # Warn if timing is significantly off (>10%)
    if expected_duration > 0:
        deviation = abs(total_clip_duration - expected_duration) / expected_duration
        if deviation > 0.10:
            logger.warning(
                f"Clip duration mismatch: clips total {total_clip_duration:.2f}s "
                f"but expected {expected_duration:.2f}s "
                f"(deviation: {deviation:.1%}). "
                f"The pipeline will proceed but the video may not perfectly "
                f"sync with the voiceover."
            )
        else:
            logger.info(
                f"Clip timing validated: {total_clip_duration:.2f}s total "
                f"(expected {expected_duration:.2f}s, deviation: {deviation:.1%})"
            )

    breakdown = EditBreakdown(
        total_duration=total_duration,
        clips=clips,
        audio_sync_notes=data.get("audio_sync_notes", ""),
    )

    logger.info(
        f"Edit breakdown validated: {len(breakdown.clips)} clips, "
        f"{total_clip_duration:.2f}s total duration"
    )
    return breakdown


# =============================================================================
# Serialization Helpers
# =============================================================================

def storyboard_to_dict(storyboard: Storyboard) -> dict:
    """Convert a Storyboard instance to a plain dictionary.

    Useful for serializing back to JSON (e.g., for logging or
    passing back to the LLM in Step 4).

    Args:
        storyboard: The Storyboard instance to convert.

    Returns:
        A plain dictionary representation.
    """
    return asdict(storyboard)


def edit_breakdown_to_dict(breakdown: EditBreakdown) -> dict:
    """Convert an EditBreakdown instance to a plain dictionary.

    Useful for serializing to JSON for logging or debugging.

    Args:
        breakdown: The EditBreakdown instance to convert.

    Returns:
        A plain dictionary representation.
    """
    return asdict(breakdown)
