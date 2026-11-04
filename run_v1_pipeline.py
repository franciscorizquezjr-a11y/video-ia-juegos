"""
run_v1_pipeline.py — Master Orchestrator CLI for V1 Multi-Video Pipeline

Connects and orchestrates the 3 programs:
1. video_analyzer.py: Generates second-by-second & shot breakdowns for all videos in a folder.
2. video_sequencer.py: AI Director selects cuts from pool for maximum viral retention (EDL).
3. ffmpeg_assembler.py: Renders cuts, applies 9:16 vertical styling (blur_bg/center_crop), and joins.

Usage:
    # Run full end-to-end pipeline:
    py run_v1_pipeline.py --input-dir "GamesFootage/Where Did I Park" --target-duration 30

    # Run specific step:
    py run_v1_pipeline.py --step analyze --input-dir "GamesFootage/Where Did I Park"
    py run_v1_pipeline.py --step sequence --breakdowns-dir "data/breakdowns" --target-duration 30
    py run_v1_pipeline.py --step render --edl "output/timeline_edl.json" --framing blur_bg
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from config import AppConfig
from ffmpeg_assembler import FFmpegAssembler
from schemas_v1 import TimelineEDL
from video_analyzer import VideoAnalyzer
from video_sequencer import VideoSequencer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("v1_pipeline")


def run_pipeline(
    input_dir: Path,
    breakdowns_dir: Path,
    edl_output: Path,
    video_output: Path,
    target_duration: float = 30.0,
    framing: str = "fit_16_9",
    force_analysis: bool = False,
    step: str = "all",
    model: str | None = None,
) -> None:
    start_time = time.time()
    config = AppConfig.from_env()
    if model:
        config.gemini_model = model

    breakdowns_dir.mkdir(parents=True, exist_ok=True)
    edl_output.parent.mkdir(parents=True, exist_ok=True)
    video_output.parent.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 65)
    print("🎬 AI MULTI-VIDEO 16:9 1080p PIPELINE (V1)")
    print("=" * 65)
    print(f"• Input Directory:     {input_dir.resolve()}")
    print(f"• Breakdowns Dir:      {breakdowns_dir.resolve()}")
    print(f"• Target Duration:     {target_duration} seconds")
    print(f"• Framing Mode:        {framing}")
    print(f"• Target Output:       {video_output.resolve()}")
    print(f"• Execution Step:      {step.upper()}")
    print("=" * 65 + "\n")

    # -------------------------------------------------------------------------
    # STEP 1: Video Analysis (Breakdown Generator)
    # -------------------------------------------------------------------------
    if step in ("all", "analyze"):
        print("\n▶ [STEP 1/3] Running Video Analyzer...")
        if not input_dir.exists():
            logger.error(f"Input directory does not exist: {input_dir}")
            sys.exit(1)

        analyzer = VideoAnalyzer(config=config, output_dir=breakdowns_dir)
        breakdowns = analyzer.process_directory(input_dir, force=force_analysis)
        if not breakdowns:
            logger.error("No breakdowns generated. Halting pipeline.")
            sys.exit(1)
        print(f"✓ Step 1 Complete: {len(breakdowns)} breakdown(s) ready in {breakdowns_dir}")

    # -------------------------------------------------------------------------
    # STEP 2: AI Sequencer (Director EDL Generator)
    # -------------------------------------------------------------------------
    if step in ("all", "sequence"):
        print("\n▶ [STEP 2/3] Running AI Video Sequencer (EDL Generator)...")
        sequencer = VideoSequencer(config=config)
        breakdowns = sequencer.load_breakdowns(breakdowns_dir)

        edl = sequencer.generate_edl(
            breakdowns=breakdowns,
            target_duration=target_duration,
            framing_mode=framing,
        )
        edl.save_json(edl_output)
        print(f"✓ Step 2 Complete: EDL saved with {len(edl.cuts)} cuts to {edl_output}")
    else:
        # Load existing EDL if skipping step 2
        if step == "render":
            if not edl_output.exists():
                logger.error(f"EDL file not found at {edl_output}. Run sequence step first.")
                sys.exit(1)
            edl = TimelineEDL.load_json(edl_output)

    # -------------------------------------------------------------------------
    # STEP 3: FFmpeg Assembly & Rendering
    # -------------------------------------------------------------------------
    if step in ("all", "render"):
        print("\n▶ [STEP 3/3] Running FFmpeg Assembler (9:16 Render)...")
        assembler = FFmpegAssembler(config=config)
        search_dirs = [input_dir, config.input_dir, config.input_dir.parent / "GamesFootage"]

        final_path = assembler.assemble_edl(
            edl=edl,
            search_dirs=search_dirs,
            output_path=video_output,
            override_framing=framing,
        )
        print(f"\n🎉 SUCCESS! Final short video rendered at: {final_path.resolve()}")

    elapsed = time.time() - start_time
    print(f"\nTotal execution time: {elapsed:.1f} seconds\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Master Orchestrator CLI for V1 Multi-Video Short-Form Generator"
    )
    parser.add_argument(
        "--input-dir", "-i",
        type=str,
        default="GamesFootage/Where Did I Park",
        help="Path to folder containing source video pool",
    )
    parser.add_argument(
        "--breakdowns-dir", "-b",
        type=str,
        default="data/breakdowns",
        help="Path to directory for breakdown JSONs",
    )
    parser.add_argument(
        "--edl", "-e",
        type=str,
        default="output/trabajo/timeline_edl.json",
        help="Path for timeline EDL JSON file",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="output/videos/Por_clasificar/v1_short_draft.mp4",
        help="Path for the final rendered video MP4",
    )
    parser.add_argument(
        "--target-duration", "-t",
        type=float,
        default=30.0,
        help="Target video duration in seconds (default: 30.0)",
    )
    parser.add_argument(
        "--framing", "-f",
        type=str,
        choices=["fit_16_9", "blur_bg", "center_crop"],
        default="fit_16_9",
        help="16:9 formatting style ('fit_16_9', 'blur_bg', or 'center_crop')",
    )
    parser.add_argument(
        "--step", "-s",
        type=str,
        choices=["all", "analyze", "sequence", "render"],
        default="all",
        help="Which pipeline stage to execute",
    )
    parser.add_argument(
        "--force-analysis",
        action="store_true",
        help="Force re-analyzing videos even if breakdown JSON already exists",
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=None,
        help="Gemini model identifier override",
    )

    args = parser.parse_args()

    run_pipeline(
        input_dir=Path(args.input_dir),
        breakdowns_dir=Path(args.breakdowns_dir),
        edl_output=Path(args.edl),
        video_output=Path(args.output),
        target_duration=args.target_duration,
        framing=args.framing,
        force_analysis=args.force_analysis,
        step=args.step,
        model=args.model,
    )


if __name__ == "__main__":
    main()
