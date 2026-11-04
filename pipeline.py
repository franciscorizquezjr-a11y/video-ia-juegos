"""
pipeline.py — Pipeline Orchestrator

This is the heart of the AI Video Pipeline — the class that orchestrates
the entire workflow from raw gameplay video to polished promotional short.

Architecture Role:
- Coordinates the 8-step pipeline by calling the appropriate modules
  (LLM, Voice, FFmpeg) in sequence.
- Each step is a separate method with a single responsibility.
- State is accumulated in a PipelineContext dataclass, which serves as
  the single source of truth for the current run.
- The pipeline never directly calls Groq, ElevenLabs, or FFmpeg — it
  delegates to the specialized modules.

Error Handling:
- Each step validates its inputs and outputs.
- If a step fails, the pipeline logs the error, cleans up temp files,
  and raises the appropriate exception.
- The run() method wraps everything in a try/finally to ensure cleanup.

Flow:
    Pipeline.run(input_video)
        ├── analyze_video()           Step 1: LLM creative analysis
        ├── generate_voice()          Step 2: ElevenLabs TTS
        ├── measure_audio()           Step 3: FFprobe duration
        ├── generate_edit_breakdown() Step 4: LLM editing plan
        ├── validate_edit_plan()      Validate JSON timing constraints
        ├── render_clips()            Step 5-6: FFmpeg cut clips
        ├── concatenate_clips()       Step 6: FFmpeg join clips
        ├── merge_audio()             Step 7: FFmpeg combine audio + video
        ├── export()                  Step 8: FFmpeg final export
        └── cleanup()                 Remove temp files
"""

import json
import logging
from pathlib import Path

from config import AppConfig
from ffmpeg_wrapper import FFmpegWrapper
from llm import LLMClient
from schemas import (
    EditBreakdown,
    PipelineContext,
    Storyboard,
    storyboard_to_dict,
    validate_edit_breakdown,
    validate_storyboard,
)
from utils import (
    PipelineError,
    ValidationError,
    cleanup_directory,
    ensure_directories,
    generate_run_id,
    validate_file_exists,
)
from voice import VoiceGenerator

logger = logging.getLogger(__name__)


class Pipeline:
    """Orchestrates the AI Video Pipeline from input to final export.

    The Pipeline coordinates the entire workflow by calling specialized
    modules (LLMClient, VoiceGenerator, FFmpegWrapper) in sequence.
    It never directly interacts with external APIs or FFmpeg — that's
    the responsibility of the individual modules.

    Each pipeline step is a separate method with a single responsibility.
    State is accumulated in a PipelineContext dataclass that flows
    through all steps.

    Attributes:
        config: Application configuration (API keys, paths, settings).
        llm: LLM client for creative analysis and edit breakdown.
        voice: Voice generator for TTS.
        ffmpeg: FFmpeg wrapper for all video/audio processing.

    Usage:
        config = AppConfig.from_env()
        pipeline = Pipeline(config)
        result = pipeline.run(Path("input/gameplay.mp4"))
        print(f"Video exported to: {result}")
    """

    def __init__(self, config: AppConfig) -> None:
        """Initialize the pipeline with all required modules.

        Creates instances of LLMClient, VoiceGenerator, and FFmpegWrapper
        using settings from the AppConfig. Also ensures all required
        directories exist.

        Args:
            config: Fully validated AppConfig instance.
        """
        self.config = config

        # Ensure all directories exist
        ensure_directories(*config.get_all_directories())

        # Initialize the LLM client (Gemini)
        self.llm = LLMClient(
            api_key=config.gemini_api_key,
            model=config.gemini_model,
            max_retries=config.max_retries,
            retry_delay=config.retry_delay,
        )

        # Initialize the voice generator (ElevenLabs)
        self.voice = VoiceGenerator(
            api_key=config.elevenlabs_api_key,
            voice_id=config.elevenlabs_voice_id,
            model_id=config.elevenlabs_model,
            output_format=config.elevenlabs_output_format,
            max_retries=config.max_retries,
            retry_delay=config.retry_delay,
        )

        # Initialize the FFmpeg wrapper
        self.ffmpeg = FFmpegWrapper(
            ffmpeg_path=config.ffmpeg_path,
            ffprobe_path=config.ffprobe_path,
            temp_dir=config.temp_dir,
        )

        # Load prompt templates
        self._analyze_prompt = self._load_prompt("analyze_video.txt")
        self._edit_breakdown_prompt = self._load_prompt("edit_breakdown.txt")

        logger.info("Pipeline initialized successfully")

    def _load_prompt(self, filename: str) -> str:
        """Load a prompt template from the prompts directory.

        Args:
            filename: Name of the prompt file to load.

        Returns:
            The prompt template text.

        Raises:
            FileNotFoundError: If the prompt file doesn't exist.
        """
        prompt_path = self.config.prompts_dir / filename
        validate_file_exists(prompt_path, f"Prompt template '{filename}'")

        with open(prompt_path, "r", encoding="utf-8") as f:
            prompt = f.read()

        logger.debug(f"Loaded prompt template: {filename} ({len(prompt)} chars)")
        return prompt

    # =========================================================================
    # Main Entry Point
    # =========================================================================

    def run(
        self,
        input_video: Path,
        output_name: str = "final.mp4",
        dry_run: bool = False,
        styles_list: list = None,
    ) -> Path | list[Path]:
        """Execute the complete pipeline from input video to final export.

        This is the main entry point. It runs all 8 steps in sequence:
        1. Analyze video (LLM)
        2. Generate voiceover (ElevenLabs)
        3. Measure audio duration (FFprobe)
        4. Generate edit breakdown (LLM)
        5. Validate the edit plan
        6. Render individual clips (FFmpeg)
        7. Concatenate clips (FFmpeg)
        8. Merge audio + video (FFmpeg)
        9. Final export (FFmpeg)

        Args:
            input_video: Path to the source gameplay/trailer video.
            output_name: Filename for the final output (default: final.mp4).
            dry_run: If True, validate config and inputs but don't process.

        Returns:
            Path to the final exported video file.

        Raises:
            FileNotFoundError: If the input video doesn't exist.
            PipelineError: If any step fails.
        """
        # Validate input
        validate_file_exists(input_video, "Input video")

        # Create pipeline context — the single source of truth for this run
        ctx = PipelineContext(
            run_id=generate_run_id(),
            input_video=input_video,
        )

        logger.info(f"{'=' * 60}")
        logger.info(f"PIPELINE RUN: {ctx.run_id}")
        logger.info(f"Input: {input_video.name}")
        logger.info(f"{'=' * 60}")

        if dry_run:
            logger.info("DRY RUN — validating configuration only")
            self._dry_run_checks(ctx)
            logger.info("DRY RUN complete — all checks passed")
            return Path("(dry run — no output)")

        try:
            is_card_style = self.config.video_style in ["vertical_split_black", "vertical_split_blur"]
            
            if is_card_style:
                logger.info("Card layout style detected. ElevenLabs voice and Gemini script generations are bypassed.")
                ctx.audio_duration = self.config.target_duration
                
                if not self.config.auto_cut:
                    logger.info("Auto-cut is disabled. Bypassing highlight analysis, clip rendering and audio merging. Using source video directly.")
                    ctx.merged_video_path = ctx.input_video
                else:
                    # Auto-cut is enabled: we need to find best highlights but WITHOUT voiceover
                    # Step 1: LLM analyzes the video (to generate hook/storyboard)
                    self.analyze_video(ctx)
                    # Bypassed voice and measure steps
                    # Step 4: LLM creates the editing breakdown (fits clips to target_duration)
                    self.generate_edit_breakdown(ctx)
                    # Step 5: Validate the edit plan
                    self.validate_edit_plan(ctx)
                    # Step 6: Cut individual clips
                    self.render_clips(ctx)
                    # Step 7: Concatenate clips
                    self.concatenate_clips(ctx)
                    # Step 8: Skip merge audio (use concatenated video's original audio track directly)
                    ctx.merged_video_path = ctx.concatenated_video_path
            else:
                # Normal pipeline flow (full vertical crop)
                # Step 1: LLM analyzes the video
                self.analyze_video(ctx)
                # Step 2: Generate voiceover audio
                self.generate_voice(ctx)
                # Step 3: Measure the exact audio duration
                self.measure_audio(ctx)
                # Step 4: LLM creates the editing breakdown
                self.generate_edit_breakdown(ctx)
                # Step 5: Validate the edit plan against constraints
                self.validate_edit_plan(ctx)
                # Step 6: Cut individual clips from the source video
                self.render_clips(ctx)
                # Step 7: Concatenate clips into a single video
                self.concatenate_clips(ctx)
                # Step 8: Merge voiceover audio with the edited video
                self.merge_audio(ctx)

            # Step 9: Final export with quality settings and card overlays
            output_path = self.export(ctx, output_name, styles_list=styles_list)

            logger.info(f"{'=' * 60}")
            logger.info(f"PIPELINE COMPLETE: {output_path}")
            logger.info(f"{'=' * 60}")

            return output_path

        except Exception as e:
            logger.error(f"Pipeline failed at run {ctx.run_id}: {e}")
            raise

        finally:
            # Always clean up temporary files
            self.cleanup(ctx)

    # =========================================================================
    # Pipeline Steps
    # =========================================================================

    def analyze_video(self, ctx: PipelineContext) -> None:
        """Step 1: Send the video to the LLM for creative analysis.

        The LLM watches the entire video and produces:
        - A storyboard with narrative arc, scenes, and pacing
        - A voiceover script for TTS generation

        The LLM makes ONLY creative decisions — no timestamps, no
        FFmpeg commands, no technical editing instructions.

        Args:
            ctx: Pipeline context to store results in.

        Raises:
            LLMError: If the LLM request fails.
            ValidationError: If the response doesn't match the schema.
        """
        logger.info("─── Step 1: Video Analysis (LLM) ───")

        # Send video + prompt to the LLM
        raw_storyboard = self.llm.analyze_video(
            ctx.input_video,
            self._analyze_prompt,
        )

        # Validate and parse into typed dataclass
        storyboard = validate_storyboard(raw_storyboard)

        # Store results in context
        ctx.storyboard = storyboard
        ctx.storyboard_raw = raw_storyboard

        # Log the storyboard summary
        logger.info(f"Storyboard: '{storyboard.title}'")
        logger.info(f"Scenes: {len(storyboard.scenes)}")
        logger.info(f"Voiceover: {len(storyboard.voiceover_script)} chars")

        # Save storyboard JSON for debugging
        self._save_debug_json(ctx, "storyboard.json", raw_storyboard)

    def generate_voice(self, ctx: PipelineContext) -> None:
        """Step 2: Generate voiceover audio from the script.

        Sends the voiceover script (from Step 1) to ElevenLabs TTS.
        The generated MP3 is saved to the temp directory.

        Args:
            ctx: Pipeline context (reads voiceover_script, writes voice_audio_path).

        Raises:
            VoiceGenerationError: If TTS generation fails.
            PipelineError: If no storyboard is available (Step 1 must run first).
        """
        logger.info("─── Step 2: Voice Generation (ElevenLabs) ───")

        if ctx.storyboard is None:
            raise PipelineError(
                "Cannot generate voice: no storyboard available. "
                "Step 1 (analyze_video) must run first."
            )

        # Path for the generated audio file
        voice_path = self.config.temp_dir / f"voice_{ctx.run_id}.mp3"

        # Generate and save the voiceover
        self.voice.generate(
            text=ctx.storyboard.voiceover_script,
            output_path=voice_path,
        )

        ctx.voice_audio_path = voice_path
        logger.info(f"Voiceover saved: {voice_path.name}")

    def measure_audio(self, ctx: PipelineContext) -> None:
        """Step 3: Measure the exact duration of the generated audio.

        The audio duration becomes the reference for the final video:
        - Final video duration = audio_duration + tail_padding
        - The voiceover starts at 0.0 seconds
        - The tail padding is visual-only (ending shot, fade-out)

        Args:
            ctx: Pipeline context (reads voice_audio_path, writes audio_duration).

        Raises:
            FFmpegError: If ffprobe fails to read the audio file.
            PipelineError: If no audio file is available.
        """
        logger.info("─── Step 3: Measure Audio Duration ───")

        if ctx.voice_audio_path is None:
            raise PipelineError(
                "Cannot measure audio: no voice file available. "
                "Step 2 (generate_voice) must run first."
            )

        # Use FFprobe to get the exact duration
        duration = self.ffmpeg.get_duration(ctx.voice_audio_path)
        ctx.audio_duration = duration

        target = duration + self.config.audio_tail_padding
        logger.info(
            f"Audio duration: {duration:.3f}s "
            f"(target video duration: {target:.3f}s = "
            f"{duration:.3f}s audio + {self.config.audio_tail_padding}s padding)"
        )

    def generate_edit_breakdown(self, ctx: PipelineContext) -> None:
        """Step 4: Generate the frame-accurate editing plan.

        Sends the storyboard, voiceover script, and exact audio duration
        back to the LLM. The LLM creates a precise editing breakdown
        with timestamps from the source video.

        The LLM returns ONLY structured JSON — never FFmpeg commands.

        Args:
            ctx: Pipeline context (reads storyboard + duration, writes edit_breakdown).

        Raises:
            LLMError: If the LLM request fails.
            ValidationError: If the response doesn't match the schema.
        """
        logger.info("─── Step 4: Edit Breakdown (LLM) ───")

        if ctx.storyboard is None or ctx.audio_duration == 0:
            raise PipelineError(
                "Cannot generate edit breakdown: storyboard or audio duration "
                "not available. Steps 1-3 must run first."
            )

        # Get the source video duration to pass to the LLM
        source_duration = self.ffmpeg.get_duration(ctx.input_video)

        # Send context to the LLM
        raw_breakdown = self.llm.generate_edit_breakdown(
            storyboard=storyboard_to_dict(ctx.storyboard),
            voiceover_script=ctx.storyboard.voiceover_script,
            audio_duration=ctx.audio_duration,
            source_duration=source_duration,
            prompt_template=self._edit_breakdown_prompt,
        )

        # Validate and parse into typed dataclass
        target_duration = ctx.audio_duration + self.config.audio_tail_padding
        breakdown = validate_edit_breakdown(raw_breakdown, target_duration)

        # Store results in context
        ctx.edit_breakdown = breakdown
        ctx.edit_breakdown_raw = raw_breakdown

        # Log clip summary
        logger.info(f"Edit breakdown: {len(breakdown.clips)} clips")
        for clip in breakdown.clips:
            logger.info(
                f"  Clip {clip.order}: {clip.start:.2f}s → {clip.end:.2f}s "
                f"({clip.duration:.2f}s) [{clip.purpose}]"
            )

        # Save breakdown JSON for debugging
        self._save_debug_json(ctx, "edit_breakdown.json", raw_breakdown)

    def validate_edit_plan(self, ctx: PipelineContext) -> None:
        """Validate the edit plan against source video constraints.

        Checks that:
        - All clip timestamps are within the source video's duration
        - No clip has negative or zero duration
        - Clips are ordered correctly

        This catches LLM errors before FFmpeg processing begins.

        Args:
            ctx: Pipeline context (reads edit_breakdown, input_video).

        Raises:
            ValidationError: If any clip has invalid timestamps.
        """
        logger.info("─── Validating Edit Plan ───")

        if ctx.edit_breakdown is None:
            raise PipelineError("No edit breakdown available to validate.")

        # Get the source video duration for bounds checking
        source_duration = self.ffmpeg.get_duration(ctx.input_video)

        errors: list[str] = []
        for clip in ctx.edit_breakdown.clips:
            if clip.start < 0:
                errors.append(
                    f"Clip {clip.order} has negative start: {clip.start}"
                )
            if clip.end > source_duration:
                errors.append(
                    f"Clip {clip.order} end ({clip.end:.2f}s) exceeds "
                    f"source duration ({source_duration:.2f}s)"
                )
            if clip.duration <= 0:
                errors.append(
                    f"Clip {clip.order} has zero or negative duration: "
                    f"{clip.duration:.2f}s"
                )

        if errors:
            raise ValidationError(
                "Edit plan validation failed:\n"
                + "\n".join(f"  • {e}" for e in errors)
            )

        logger.info(
            f"Edit plan validated: {len(ctx.edit_breakdown.clips)} clips, "
            f"all within source duration ({source_duration:.2f}s)"
        )

    def render_clips(self, ctx: PipelineContext) -> None:
        """Steps 5-6: Cut individual clips from the source video.

        Uses FFmpeg to extract each clip defined in the edit breakdown.
        Clips are re-encoded for frame-accurate cuts.

        Args:
            ctx: Pipeline context (reads edit_breakdown, writes clip_paths).

        Raises:
            FFmpegError: If any clip extraction fails.
        """
        logger.info("─── Steps 5-6: Render Clips (FFmpeg) ───")

        if ctx.edit_breakdown is None:
            raise PipelineError("No edit breakdown available for rendering.")

        ctx.clip_paths = []

        for clip in ctx.edit_breakdown.clips:
            # Name clips sequentially for clarity
            clip_filename = f"clip_{ctx.run_id}_{clip.order:03d}.mp4"
            clip_path = self.config.temp_dir / clip_filename

            logger.info(
                f"Cutting clip {clip.order}/{len(ctx.edit_breakdown.clips)}: "
                f"{clip.start:.2f}s → {clip.end:.2f}s [{clip.purpose}]"
            )

            self.ffmpeg.cut_clip(
                input_path=ctx.input_video,
                output_path=clip_path,
                start=clip.start,
                end=clip.end,
                reencode=True,  # Frame-accurate cuts (user preference)
            )

            ctx.clip_paths.append(clip_path)

        logger.info(f"All {len(ctx.clip_paths)} clips rendered successfully")

    def concatenate_clips(self, ctx: PipelineContext) -> None:
        """Step 6 (cont.): Join all clips into a single video.

        Uses FFmpeg concat demuxer to join the clips in order.

        Args:
            ctx: Pipeline context (reads clip_paths, writes concatenated_video_path).

        Raises:
            FFmpegError: If concatenation fails.
        """
        logger.info("─── Step 6: Concatenate Clips (FFmpeg) ───")

        if not ctx.clip_paths:
            raise PipelineError("No clips available for concatenation.")

        concat_path = self.config.temp_dir / f"edited_{ctx.run_id}.mp4"

        self.ffmpeg.concatenate(
            clip_paths=ctx.clip_paths,
            output_path=concat_path,
        )

        ctx.concatenated_video_path = concat_path
        logger.info(f"Clips concatenated: {concat_path.name}")

    def merge_audio(self, ctx: PipelineContext) -> None:
        """Step 7: Combine edited video with voiceover audio.

        Replaces the concatenated video's audio with the generated
        voiceover. The video stream is copied (no re-encoding needed).

        Args:
            ctx: Pipeline context (reads concatenated_video_path + voice_audio_path,
                 writes merged_video_path).

        Raises:
            FFmpegError: If audio merge fails.
        """
        logger.info("─── Step 7: Merge Audio (FFmpeg) ───")

        if ctx.concatenated_video_path is None or ctx.voice_audio_path is None:
            raise PipelineError(
                "Cannot merge audio: missing video or audio file."
            )

        merged_path = self.config.temp_dir / f"merged_{ctx.run_id}.mp4"

        self.ffmpeg.replace_audio(
            video_path=ctx.concatenated_video_path,
            audio_path=ctx.voice_audio_path,
            output_path=merged_path,
        )

        ctx.merged_video_path = merged_path
        logger.info(f"Audio merged: {merged_path.name}")

    def generate_hook_text(self, ctx: PipelineContext) -> str:
        """Resolve the hook text to use on the card.

        If card_text is provided, returns it directly.
        Otherwise, falls back to storyboard hook or a default.

        Args:
            ctx: Current pipeline context.

        Returns:
            The hook text string.
        """
        # Priority 1: User-provided card text
        if self.config.card_text:
            logger.info("Using user-provided card text")
            return self.config.card_text

        # Priority 2: Storyboard hook from video analysis
        if ctx.storyboard and ctx.storyboard.hook:
            return ctx.storyboard.hook

        # Priority 3: Fallback
        return "¡Mira este momentazo de gameplay!"

    def export(self, ctx: PipelineContext, output_name: str = "final.mp4", styles_list: list = None) -> Path | list[Path]:
        """Step 8: Final export with production quality settings.

        Applies the configured export settings (codec, CRF, bitrate)
        and saves to the output directory. Also crops to vertical
        format if the video isn't already 9:16.
        """
        logger.info("─── Step 8: Export (FFmpeg) ───")

        if ctx.merged_video_path is None:
            raise PipelineError("Cannot export: no merged video available.")

        # Determine target aspect ratio (vertical vs horizontal)
        target_width = self.config.output_width
        target_height = self.config.output_height
        target_is_vertical = target_width < target_height

        # Resolution scaling factor relative to 4K baseline (2160x3840)
        # All presets and default card positions are defined for 2160x3840 (4K vertical).
        # For 1080p (1080x1920), res_scale = 0.5; for 4K (2160x3840), res_scale = 1.0.
        res_scale = (target_width / 2160.0) if target_is_vertical else 1.0

        # Check source video dimensions
        video_info = self.ffmpeg.get_video_info(ctx.merged_video_path)
        src_width = video_info.get("width", 0)
        src_height = video_info.get("height", 0)
        src_is_vertical = src_width < src_height

        # If styles_list is provided and has elements, use the optimized multi-preset export
        if styles_list:
            import re
            logger.info(f"Exporting with {len(styles_list)} preset styles (res_scale: {res_scale:.2f})...")
            exported_paths = []

            base_videos_by_zoom = {}

            # Generate hook text once
            hook_text = self.generate_hook_text(ctx)

            # 2. For each preset, generate card and overlay on the base video
            for style_dict in styles_list:
                # Sanitize filename
                clean_name = re.sub(r"[^\w\-]", "_", style_dict.get("name", "preset"))
                
                if len(styles_list) == 1 and clean_name in ("Personalizado", "default", "Default"):
                    out_filename = f"{Path(output_name).stem}{Path(output_name).suffix}"
                else:
                    out_filename = f"{Path(output_name).stem}_{clean_name}{Path(output_name).suffix}"
                out_path = self.config.output_dir / out_filename

                style = self.config.video_style
                zoom_val = float(style_dict.get("video_zoom", self.config.video_zoom))
                raw_y_pos = int(style_dict.get("video_y_position", self.config.video_y_position))
                y_pos_val = int(round(raw_y_pos * res_scale))

                # Resolve or create the base video for this zoom and Y position level
                cache_key = (zoom_val, y_pos_val)
                if cache_key not in base_videos_by_zoom:
                    base_temp_path = self.config.temp_dir / f"base_export_{ctx.run_id}_zoom_{zoom_val:.2f}_y_{y_pos_val}.mp4"
                    
                    if target_is_vertical and not src_is_vertical:
                        if style == "vertical_split_blur":
                            logger.info(f"Generating vertical split blur base video for zoom {zoom_val:.2f}, y_offset {y_pos_val}...")
                            self.ffmpeg.create_base_vertical_split_blur(
                                input_path=ctx.merged_video_path,
                                output_path=base_temp_path,
                                target_width=target_width,
                                target_height=target_height,
                                zoom=zoom_val,
                                video_y_position=y_pos_val,
                            )
                        elif style == "vertical_split_black":
                            logger.info(f"Generating vertical split black base video for zoom {zoom_val:.2f}, y_offset {y_pos_val}...")
                            self.ffmpeg.create_base_vertical_split_black(
                                input_path=ctx.merged_video_path,
                                output_path=base_temp_path,
                                target_width=target_width,
                                target_height=target_height,
                                zoom=zoom_val,
                                video_y_position=y_pos_val,
                            )
                        else:
                            # Default vertical_full: crop to vertical
                            logger.info("Generating vertical full crop base video...")
                            self.ffmpeg.crop_to_vertical(
                                input_path=ctx.merged_video_path,
                                output_path=base_temp_path,
                                target_width=target_width,
                                target_height=target_height,
                            )
                    elif not target_is_vertical and src_is_vertical:
                        logger.info("Resizing vertical source to horizontal base...")
                        self.ffmpeg.resize(
                            input_path=ctx.merged_video_path,
                            output_path=base_temp_path,
                            width=target_width,
                            height=target_height,
                        )
                    else:
                        if src_width != target_width or src_height != target_height:
                            logger.info("Resizing source base video...")
                            self.ffmpeg.resize(
                                input_path=ctx.merged_video_path,
                                output_path=base_temp_path,
                                width=target_width,
                                height=target_height,
                            )
                        else:
                            import shutil
                            shutil.copy2(ctx.merged_video_path, base_temp_path)
                    
                    base_videos_by_zoom[cache_key] = base_temp_path

                base_video_for_preset = base_videos_by_zoom[cache_key]

                if target_is_vertical and not src_is_vertical and style in ["vertical_split_black", "vertical_split_blur"]:
                    card_path = self.config.temp_dir / f"card_{ctx.run_id}_{clean_name}.png"
                    from utils import create_text_card

                    raw_card_w = int(style_dict.get("card_width", self.config.card_width))
                    raw_card_h = int(style_dict.get("card_height", self.config.card_height))
                    raw_card_y = int(style_dict.get("card_y_position", self.config.card_y_position))
                    raw_font_body = int(style_dict.get("card_font_size_body", self.config.card_font_size_body))
                    raw_font_user = int(style_dict.get("card_font_size_username", self.config.card_font_size_username))
                    raw_font_handle = int(style_dict.get("card_font_size_handle", self.config.card_font_size_handle))
                    raw_avatar_r = int(style_dict.get("card_avatar_radius", self.config.card_avatar_radius))
                    raw_border_w = int(style_dict.get("card_border_width", self.config.card_border_width))
                    raw_radius = int(style_dict.get("card_radius", self.config.card_radius))

                    eff_card_w = int(round(raw_card_w * res_scale))
                    eff_card_h = int(round(raw_card_h * res_scale))
                    eff_card_y = int(round(raw_card_y * res_scale))
                    eff_font_body = max(16, int(round(raw_font_body * res_scale)))
                    eff_font_user = max(14, int(round(raw_font_user * res_scale)))
                    eff_font_handle = max(12, int(round(raw_font_handle * res_scale)))
                    eff_avatar_r = max(15, int(round(raw_avatar_r * res_scale)))
                    eff_border_w = max(1, int(round(raw_border_w * res_scale)))
                    eff_radius = max(4, int(round(raw_radius * res_scale)))

                    create_text_card(
                        text=hook_text,
                        output_path=card_path,
                        username=style_dict.get("card_username", self.config.card_username),
                        handle=style_dict.get("card_handle", self.config.card_handle),
                        bg_color=style_dict.get("card_bg_color", self.config.card_bg_color),
                        text_color=style_dict.get("card_text_color", self.config.card_text_color),
                        border_color=style_dict.get("card_border_color", self.config.card_border_color),
                        border_width=eff_border_w,
                        radius=eff_radius,
                        avatar_text=style_dict.get("card_avatar_text", self.config.card_avatar_text),
                        avatar_bg=style_dict.get("card_avatar_bg", self.config.card_avatar_bg),
                        avatar_image_path=style_dict.get("card_avatar_path", self.config.card_avatar_path),
                        font_username_family=style_dict.get("card_font_username", self.config.card_font_username),
                        font_handle_family=style_dict.get("card_font_handle", self.config.card_font_handle),
                        font_body_family=style_dict.get("card_font_body", self.config.card_font_body),
                        width=eff_card_w,
                        height=eff_card_h,
                        font_size_body=eff_font_body,
                        avatar_radius=eff_avatar_r,
                        font_size_username=eff_font_user,
                        font_size_handle=eff_font_handle,
                        overlay_type=style_dict.get("overlay_type", getattr(self.config, "overlay_type", "card")),
                    )

                    # Fast card overlay on base vertical video
                    self.ffmpeg.overlay_card_on_video(
                        input_video_path=base_video_for_preset,
                        card_image_path=card_path,
                        output_path=out_path,
                        card_y=eff_card_y
                    )
                else:
                    # No card needed (e.g. vertical_full or horizontal video)
                    # Just copy base video to the target path
                    import shutil
                    shutil.copy2(base_video_for_preset, out_path)

                exported_paths.append(out_path)
                logger.info(f"Exported preset '{style_dict.get('name')}' to {out_path}")

            # Clean up base video(s)
            for bv_path in base_videos_by_zoom.values():
                if bv_path.exists():
                    try:
                        bv_path.unlink()
                    except Exception:
                        pass

            return exported_paths

        # --- ORIGINAL SINGLE PRESET FLOW ---
        output_path = self.config.output_dir / output_name
        export_source = ctx.merged_video_path

        # Handle aspect ratio conversion dynamically
        if target_is_vertical and not src_is_vertical:
            style = self.config.video_style
            if style in ["vertical_split_black", "vertical_split_blur"]:
                eff_card_w = int(round(self.config.card_width * res_scale))
                eff_card_h = int(round(self.config.card_height * res_scale))
                eff_card_y = int(round(self.config.card_y_position * res_scale))
                eff_video_y = int(round(self.config.video_y_position * res_scale))
                eff_font_body = max(16, int(round(self.config.card_font_size_body * res_scale)))
                eff_font_user = max(14, int(round(self.config.card_font_size_username * res_scale)))
                eff_font_handle = max(12, int(round(self.config.card_font_size_handle * res_scale)))
                eff_avatar_r = max(15, int(round(self.config.card_avatar_radius * res_scale)))
                eff_border_w = max(1, int(round(self.config.card_border_width * res_scale)))
                eff_radius = max(4, int(round(self.config.card_radius * res_scale)))

                hook_text = self.generate_hook_text(ctx)
                card_path = self.config.temp_dir / f"card_{ctx.run_id}.png"
                from utils import create_text_card
                create_text_card(
                    text=hook_text,
                    output_path=card_path,
                    username=self.config.card_username,
                    handle=self.config.card_handle,
                    bg_color=self.config.card_bg_color,
                    text_color=self.config.card_text_color,
                    border_color=self.config.card_border_color,
                    border_width=eff_border_w,
                    radius=eff_radius,
                    avatar_text=self.config.card_avatar_text,
                    avatar_bg=self.config.card_avatar_bg,
                    avatar_image_path=self.config.card_avatar_path,
                    font_username_family=self.config.card_font_username,
                    font_handle_family=self.config.card_font_handle,
                    font_body_family=self.config.card_font_body,
                    width=eff_card_w,
                    height=eff_card_h,
                    font_size_body=eff_font_body,
                    avatar_radius=eff_avatar_r,
                    font_size_username=eff_font_user,
                    font_size_handle=eff_font_handle,
                    overlay_type=getattr(self.config, "overlay_type", "card"),
                )

                composed_path = self.config.temp_dir / f"composed_{ctx.run_id}.mp4"
                if style == "vertical_split_black":
                    logger.info("Target is vertical split (black bg), input is horizontal — composing split layout")
                    self.ffmpeg.compose_vertical_with_black_bg(
                        input_path=ctx.merged_video_path,
                        card_path=card_path,
                        output_path=composed_path,
                        target_width=target_width,
                        target_height=target_height,
                        card_y=eff_card_y,
                        zoom=self.config.video_zoom,
                        video_y_position=eff_video_y,
                    )
                else:
                    logger.info("Target is vertical split (blurred bg), input is horizontal — composing split layout")
                    self.ffmpeg.compose_vertical_with_blurred_bg(
                        input_path=ctx.merged_video_path,
                        card_path=card_path,
                        output_path=composed_path,
                        target_width=target_width,
                        target_height=target_height,
                        card_y=eff_card_y,
                        zoom=self.config.video_zoom,
                        video_y_position=eff_video_y,
                    )
                export_source = composed_path
            else:
                logger.info(f"Target is vertical ({target_width}x{target_height}), input is horizontal ({src_width}x{src_height}) — cropping to vertical 9:16")
                converted_path = self.config.temp_dir / f"vertical_{ctx.run_id}.mp4"
                self.ffmpeg.crop_to_vertical(
                    input_path=ctx.merged_video_path,
                    output_path=converted_path,
                    target_width=target_width,
                    target_height=target_height,
                )
                export_source = converted_path
        elif not target_is_vertical and src_is_vertical:
            logger.info(f"Target is horizontal ({target_width}x{target_height}), input is vertical ({src_width}x{src_height}) — resizing to target dimensions")
            converted_path = self.config.temp_dir / f"horizontal_{ctx.run_id}.mp4"
            self.ffmpeg.resize(
                input_path=ctx.merged_video_path,
                output_path=converted_path,
                width=target_width,
                height=target_height,
            )
            export_source = converted_path
        else:
            if src_width != target_width or src_height != target_height:
                logger.info(f"Resizing video from {src_width}x{src_height} to target {target_width}x{target_height}")
                resized_path = self.config.temp_dir / f"resized_{ctx.run_id}.mp4"
                self.ffmpeg.resize(
                    input_path=ctx.merged_video_path,
                    output_path=resized_path,
                    width=target_width,
                    height=target_height,
                )
                export_source = resized_path

        self.ffmpeg.export(
            input_path=export_source,
            output_path=output_path,
            codec=self.config.video_codec,
            crf=self.config.video_crf,
            audio_codec=self.config.audio_codec,
            audio_bitrate=self.config.audio_bitrate,
        )

        ctx.final_output_path = output_path
        logger.info(f"Final video exported: {output_path}")
        return output_path

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def cleanup(self, ctx: PipelineContext) -> None:
        """Clean up temporary files from the pipeline run.

        Removes all temp files created during this run while preserving
        the temp directory itself. Called in the finally block of run().

        Only cleans files matching this run's ID to avoid interfering
        with concurrent runs (future feature).

        Args:
            ctx: Pipeline context with the run ID.
        """
        logger.info("Cleaning up temporary files...")

        try:
            removed = 0
            for item in self.config.temp_dir.iterdir():
                if ctx.run_id in item.name:
                    try:
                        item.unlink()
                        removed += 1
                    except OSError as e:
                        logger.warning(f"Failed to remove temp file {item}: {e}")

            logger.info(f"Cleanup complete: {removed} temp files removed")
        except Exception as e:
            # Cleanup failures should never crash the pipeline
            logger.warning(f"Cleanup error (non-fatal): {e}")

    def _save_debug_json(self, ctx: PipelineContext, filename: str, data: dict) -> None:
        """Save JSON data to the temp directory for debugging.

        These files are useful for understanding what the LLM returned
        and debugging pipeline issues without re-running the LLM calls.

        Args:
            ctx: Pipeline context for the run ID.
            filename: Name for the JSON file.
            data: Dictionary to serialize.
        """
        try:
            debug_path = self.config.temp_dir / f"{ctx.run_id}_{filename}"
            with open(debug_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.debug(f"Debug JSON saved: {debug_path.name}")
        except Exception as e:
            logger.warning(f"Failed to save debug JSON {filename}: {e}")

    def _dry_run_checks(self, ctx: PipelineContext) -> None:
        """Perform validation checks without processing.

        Verifies:
        - Input video exists and is readable
        - FFmpeg can probe the input video
        - Prompt templates are loaded
        - All directories exist

        Args:
            ctx: Pipeline context.
        """
        logger.info("Checking input video...")
        info = self.ffmpeg.get_video_info(ctx.input_video)
        logger.info(
            f"  Video: {info['width']}x{info['height']}, "
            f"{info['duration']:.2f}s, {info['fps']:.1f}fps"
        )

        logger.info("Checking prompt templates...")
        logger.info(f"  analyze_video.txt: {len(self._analyze_prompt)} chars")
        logger.info(f"  edit_breakdown.txt: {len(self._edit_breakdown_prompt)} chars")

        logger.info("Checking directories...")
        for dir_path in self.config.get_all_directories():
            status = "✓ exists" if dir_path.exists() else "✗ missing"
            logger.info(f"  {dir_path}: {status}")

        logger.info("All dry-run checks passed")
