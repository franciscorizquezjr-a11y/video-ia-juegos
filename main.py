"""
main.py — Entry Point for the AI Video Pipeline

This is the CLI entry point for the pipeline. It handles:
- Argument parsing
- Configuration loading
- Logging initialization
- Pipeline execution
- Top-level error handling and exit codes

Usage:
    # Run the full pipeline
    python main.py --input input/gameplay.mp4

    # Run with custom output name
    python main.py --input input/gameplay.mp4 --output highlights.mp4

    # Dry run (validate config and inputs only)
    python main.py --input input/gameplay.mp4 --dry-run

    # List available ElevenLabs voices
    python main.py --list-voices

    # Run with debug logging
    python main.py --input input/gameplay.mp4 --log-level DEBUG
"""

import argparse
import sys
import time
import logging
from pathlib import Path

# Force UTF-8 encoding on standard streams to prevent UnicodeEncodeError on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from config import AppConfig
from pipeline import Pipeline
from utils import (
    ConfigError,
    FFmpegError,
    LLMError,
    PipelineError,
    ValidationError,
    VoiceGenerationError,
    setup_logging,
)


def create_argument_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser.

    Returns:
        Configured ArgumentParser instance with all supported arguments.
    """
    parser = argparse.ArgumentParser(
        prog="AI Video Pipeline",
        description=(
            "Automatically transform gameplay/trailer videos into polished, "
            "engaging promotional shorts for TikTok, Instagram Reels, "
            "and YouTube Shorts."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py --input input/gameplay.mp4\n"
            "  python main.py --input input/gameplay.mp4 --output highlights.mp4\n"
            "  python main.py --input input/gameplay.mp4 --dry-run\n"
            "  python main.py --list-voices\n"
        ),
    )

    parser.add_argument(
        "--input", "-i",
        type=Path,
        help="Path to the source gameplay/trailer video (MP4).",
    )

    parser.add_argument(
        "--output", "-o",
        type=str,
        default="final.mp4",
        help="Filename for the final output video (default: final.mp4).",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and inputs without processing.",
    )

    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="List available ElevenLabs voices and exit.",
    )

    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Override the logging level from config.",
    )

    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Path to a custom .env file (default: .env in project root).",
    )

    return parser


def list_voices(config: AppConfig) -> None:
    """List available ElevenLabs voices and print them.

    Args:
        config: Application configuration with ElevenLabs API key.
    """
    from voice import VoiceGenerator

    voice_gen = VoiceGenerator(
        api_key=config.elevenlabs_api_key,
        voice_id=config.elevenlabs_voice_id,
    )

    voices = voice_gen.list_voices()

    if not voices:
        print("No voices found or API error occurred.")
        return

    print(f"\n{'─' * 60}")
    print(f"  Available ElevenLabs Voices ({len(voices)} found)")
    print(f"{'─' * 60}")
    print(f"  {'Name':<25} {'Voice ID':<30} {'Category'}")
    print(f"  {'─' * 25} {'─' * 30} {'─' * 15}")

    for voice in sorted(voices, key=lambda v: v["name"]):
        print(
            f"  {voice['name']:<25} "
            f"{voice['voice_id']:<30} "
            f"{voice['category']}"
        )

    print(f"{'─' * 60}")
    print(f"  Set ELEVENLABS_VOICE_ID in .env to use a specific voice.")
    print()


def main() -> int:
    """Main entry point for the AI Video Pipeline.

    Parses arguments, loads configuration, initializes logging,
    and runs the pipeline. Returns an exit code (0 for success,
    1 for failure).

    Returns:
        Exit code: 0 for success, 1 for pipeline errors,
        2 for configuration errors, 130 for keyboard interrupt.
    """
    parser = create_argument_parser()
    args = parser.parse_args()

    # ── Load Configuration ──
    try:
        config = AppConfig.from_env(env_path=args.env_file)
    except ConfigError as e:
        print(f"\n❌ Configuration Error:\n{e}\n", file=sys.stderr)
        print("Please check your .env file. Copy .env.example to .env and fill in your API keys.")
        return 2

    # ── Override log level if specified ──
    if args.log_level:
        config.log_level = args.log_level

    # ── Initialize Logging ──
    setup_logging(
        log_level=config.log_level,
        logs_dir=config.logs_dir,
        log_to_file=config.log_to_file,
    )

    logger = logging.getLogger(__name__)

    # ── Handle --list-voices ──
    if args.list_voices:
        list_voices(config)
        return 0

    # ── Validate --input is provided ──
    if args.input is None:
        parser.print_help()
        print("\n❌ Error: --input is required (unless using --list-voices).")
        return 2

    # ── Run the Pipeline ──
    try:
        logger.info("=" * 60)
        logger.info("AI Video Pipeline — Starting")
        logger.info("=" * 60)

        start_time = time.time()

        pipeline = Pipeline(config)
        output_path = pipeline.run(
            input_video=args.input,
            output_name=args.output,
            dry_run=args.dry_run,
        )

        elapsed = time.time() - start_time

        logger.info(f"Pipeline completed in {elapsed:.1f} seconds")
        print(f"\n✅ Success! Video exported to: {output_path}")
        print(f"   Time elapsed: {elapsed:.1f}s")

        return 0

    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        print(f"\n❌ File not found:\n{e}", file=sys.stderr)
        return 1

    except ConfigError as e:
        logger.error(f"Configuration error: {e}")
        print(f"\n❌ Configuration error:\n{e}", file=sys.stderr)
        return 2

    except ValidationError as e:
        logger.error(f"Validation error: {e}")
        print(f"\n❌ Validation error:\n{e}", file=sys.stderr)
        return 1

    except LLMError as e:
        logger.error(f"LLM error: {e}")
        print(f"\n❌ LLM error (Gemini):\n{e}", file=sys.stderr)
        print("Check your GEMINI_API_KEY and network connection.")
        return 1

    except VoiceGenerationError as e:
        logger.error(f"Voice generation error: {e}")
        print(f"\n❌ Voice generation error (ElevenLabs):\n{e}", file=sys.stderr)
        print("Check your ELEVENLABS_API_KEY and network connection.")
        return 1

    except FFmpegError as e:
        logger.error(f"FFmpeg error: {e}")
        print(f"\n❌ FFmpeg error:\n{e}", file=sys.stderr)
        print("Ensure FFmpeg is installed and FFMPEG_PATH is correct.")
        return 1

    except PipelineError as e:
        logger.error(f"Pipeline error: {e}")
        print(f"\n❌ Pipeline error:\n{e}", file=sys.stderr)
        return 1

    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user (Ctrl+C)")
        print("\n\n⚠️  Pipeline interrupted by user.")
        return 130

    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        print(f"\n❌ Unexpected error:\n{e}", file=sys.stderr)
        print("Check the logs for full details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
