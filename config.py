"""
config.py — Configuration Management for the AI Video Pipeline

This module is the single source of truth for all application configuration.
It loads values from a .env file (using python-dotenv) and provides them
as a strongly-typed AppConfig dataclass.

Design Decisions:
- Dataclass over dict: provides IDE autocompletion, type checking, and
  prevents typos in config key names.
- Defaults for optional values: the pipeline works out-of-the-box with
  sensible defaults; only API keys are required.
- Path resolution: all paths are resolved relative to the project root,
  making the config portable across machines.
- Validation at load time: catches misconfiguration early, before any
  expensive API calls or video processing begins.

Usage:
    config = AppConfig.from_env()
    print(config.gemini_api_key)
    print(config.output_dir)
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from project_paths import APP_OUTPUT_DIR, OUTPUT_ROOT

from dotenv import load_dotenv

from utils import ConfigError


# The project root is the directory containing this config.py file
_PROJECT_ROOT = Path(__file__).resolve().parent


@dataclass
class AppConfig:
    """Central configuration container for the AI Video Pipeline.

    All configurable values are stored here. Values are loaded from
    environment variables (typically set via a .env file), with sensible
    defaults for optional settings.

    Required settings (must be set in .env):
        - groq_api_key: API key for the Groq LLM provider
        - elevenlabs_api_key: API key for ElevenLabs TTS

    Optional settings have defaults that work for most use cases.

    Attributes:
        gemini_api_key: Gemini API authentication key.
        gemini_model: Gemini model identifier for multimodal analysis.
        elevenlabs_api_key: ElevenLabs API authentication key.
        elevenlabs_voice_id: Voice to use for TTS generation.
        elevenlabs_model: ElevenLabs model for speech synthesis.
        elevenlabs_output_format: Audio output format string.
        input_dir: Directory containing source video files.
        output_dir: Directory for final exported videos.
        temp_dir: Directory for intermediate processing files.
        logs_dir: Directory for log files.
        prompts_dir: Directory containing LLM prompt templates.
        ffmpeg_path: Path to the FFmpeg binary.
        ffprobe_path: Path to the FFprobe binary.
        target_duration: Target duration for the output video (seconds).
        audio_tail_padding: Extra visual-only time after voiceover ends (seconds).
        max_retries: Maximum retry attempts for API calls.
        retry_delay: Base delay between retries (seconds, used with exponential backoff).
        video_codec: Video codec for final export.
        video_crf: Constant Rate Factor for video quality (lower = better, 0-51).
        audio_codec: Audio codec for final export.
        audio_bitrate: Audio bitrate for final export.
        output_width: Output video width in pixels.
        output_height: Output video height in pixels.
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_to_file: Whether to write logs to a file.
    """

    # --- API Keys (required) ---
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    groq_api_key: str = ""

    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"
    elevenlabs_model: str = "eleven_v4"
    elevenlabs_output_format: str = "mp3_44100_128"

    # --- Paths ---
    input_dir: Path = field(default_factory=lambda: _PROJECT_ROOT / "input")
    output_dir: Path = field(default_factory=lambda: APP_OUTPUT_DIR)
    temp_dir: Path = field(default_factory=lambda: _PROJECT_ROOT / "temp")
    logs_dir: Path = field(default_factory=lambda: _PROJECT_ROOT / "logs")
    prompts_dir: Path = field(default_factory=lambda: _PROJECT_ROOT / "prompts")
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    # --- Pipeline Settings ---
    target_duration: float = 30.0
    audio_tail_padding: float = 0.5
    max_retries: int = 3
    retry_delay: float = 2.0
    enable_proxy_analysis: bool = True
    proxy_target_height: int = 720

    # --- Video Export Settings ---
    video_codec: str = "libx264"
    video_crf: int = 18
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    output_width: int = 1920
    output_height: int = 1080
    video_style: str = "fit_16_9"
    video_zoom: float = 1.0
    video_y_position: int = 0

    # --- Card Styling Settings ---
    card_username: str = "GAMEGIL"
    card_handle: str = "@officialgamegil"
    card_bg_color: str = "#ffffff"
    card_text_color: str = "#0f172a"
    card_border_color: str = "#e2e8f0"
    card_border_width: int = 12
    card_radius: int = 70
    card_avatar_text: str = "G"
    card_avatar_bg: str = "#7ca032"
    card_avatar_path: str = ""
    card_font_username: str = "Segoe UI"
    card_font_handle: str = "Segoe UI"
    card_font_body: str = "Segoe UI"
    card_width: int = 1728
    card_height: int = 980
    card_font_size_body: int = 76
    card_y_position: int = 440
    auto_cut: bool = True
    card_text: str = ""
    card_avatar_radius: int = 105
    card_font_size_username: int = 72
    card_font_size_handle: int = 56
    game_name: str = ""
    overlay_type: str = "card"  # "card" or "instagram_classic"

    # --- Logging ---
    log_level: str = "INFO"
    log_to_file: bool = True

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "AppConfig":
        """Load configuration from a .env file and environment variables.

        Reads the .env file (defaults to project root), then populates
        an AppConfig instance from environment variables. Validates that
        required API keys are present.

        Args:
            env_path: Optional explicit path to the .env file.
                      Defaults to {project_root}/.env.

        Returns:
            A fully populated and validated AppConfig instance.

        Raises:
            ConfigError: If required configuration values are missing
                        or invalid.

        Example:
            >>> config = AppConfig.from_env()
            >>> config = AppConfig.from_env(Path("/custom/.env"))
        """
        # Load the .env file into os.environ
        if env_path is None:
            env_path = _PROJECT_ROOT / ".env"

        if env_path.exists():
            load_dotenv(env_path)
        else:
            # .env is optional — environment variables might be set directly
            # (e.g., in CI/CD or Docker). Warn but don't fail.
            import logging
            logging.getLogger(__name__).warning(
                f".env file not found at {env_path}. "
                f"Relying on environment variables."
            )

        # Helper to read an env var with a default value
        def env(key: str, default: str = "") -> str:
            return os.environ.get(key, default)

        def env_bool(key: str, default: bool = False) -> bool:
            return env(key, str(default)).lower() in ("true", "1", "yes")

        def env_float(key: str, default: float = 0.0) -> float:
            value = env(key, str(default))
            try:
                return float(value)
            except ValueError:
                raise ConfigError(
                    f"Invalid float value for {key}: '{value}'"
                )

        def env_int(key: str, default: int = 0) -> int:
            value = env(key, str(default))
            try:
                return int(value)
            except ValueError:
                raise ConfigError(
                    f"Invalid integer value for {key}: '{value}'"
                )

        def env_path_resolve(key: str, default: str) -> Path:
            """Resolve a path relative to the project root."""
            raw = env(key, default)
            path = Path(raw)
            if not path.is_absolute():
                path = _PROJECT_ROOT / path
            return path

        # Build the config instance
        config = cls(
            # API Keys
            gemini_api_key=env("GEMINI_API_KEY"),
            gemini_model=env("GEMINI_MODEL", "gemini-3.6-flash"),
            groq_api_key=env("GROQ_API_KEY", ""),
            elevenlabs_api_key=env("ELEVENLABS_API_KEY"),
            elevenlabs_voice_id=env("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb"),
            elevenlabs_model=env("ELEVENLABS_MODEL", "eleven_v4"),
            elevenlabs_output_format=env("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128"),

            # Paths
            input_dir=env_path_resolve("INPUT_DIR", "input"),
            output_dir=(APP_OUTPUT_DIR if env_path_resolve("OUTPUT_DIR", "output") == OUTPUT_ROOT else env_path_resolve("OUTPUT_DIR", "output")),
            temp_dir=env_path_resolve("TEMP_DIR", "temp"),
            logs_dir=env_path_resolve("LOGS_DIR", "logs"),
            prompts_dir=env_path_resolve("PROMPTS_DIR", "prompts"),
            ffmpeg_path=env("FFMPEG_PATH", "ffmpeg"),
            ffprobe_path=env("FFPROBE_PATH", "ffprobe"),

            # Pipeline
            target_duration=env_float("TARGET_DURATION", 30.0),
            audio_tail_padding=env_float("AUDIO_TAIL_PADDING", 0.5),
            max_retries=env_int("MAX_RETRIES", 3),
            retry_delay=env_float("RETRY_DELAY", 2.0),
            enable_proxy_analysis=env("ENABLE_PROXY_ANALYSIS", "true").lower() in ("true", "1", "yes"),
            proxy_target_height=env_int("PROXY_TARGET_HEIGHT", 720),

            # Video Export
            video_codec=env("VIDEO_CODEC", "libx264"),
            video_crf=env_int("VIDEO_CRF", 18),
            audio_codec=env("AUDIO_CODEC", "aac"),
            audio_bitrate=env("AUDIO_BITRATE", "192k"),
            output_width=env_int("OUTPUT_WIDTH", 1080),
            output_height=env_int("OUTPUT_HEIGHT", 1920),
            video_style=env("VIDEO_STYLE", "vertical_full"),
            card_username=env("CARD_USERNAME", "GAMEGIL"),
            card_handle=env("CARD_HANDLE", "@officialgamegil"),
            card_bg_color=env("CARD_BG_COLOR", "#ffffff"),
            card_text_color=env("CARD_TEXT_COLOR", "#0f172a"),
            card_border_color=env("CARD_BORDER_COLOR", "#e2e8f0"),
            card_border_width=env_int("CARD_BORDER_WIDTH", 12),
            card_radius=env_int("CARD_RADIUS", 70),
            card_avatar_text=env("CARD_AVATAR_TEXT", "G"),
            card_avatar_bg=env("CARD_AVATAR_BG", "#7ca032"),
            card_avatar_path=env("CARD_AVATAR_PATH", ""),
            card_font_username=env("CARD_FONT_USERNAME", "Segoe UI"),
            card_font_handle=env("CARD_FONT_HANDLE", "Segoe UI"),
            card_font_body=env("CARD_FONT_BODY", "Segoe UI"),
            card_width=env_int("CARD_WIDTH", 1728),
            card_height=env_int("CARD_HEIGHT", 980),
            card_font_size_body=env_int("CARD_FONT_SIZE_BODY", 76),
            card_y_position=env_int("CARD_Y_POSITION", 440),
            video_y_position=env_int("VIDEO_Y_POSITION", 180),
            auto_cut=env_bool("AUTO_CUT", True),
            card_text=env("CARD_TEXT", ""),
            card_avatar_radius=env_int("CARD_AVATAR_RADIUS", 105),
            card_font_size_username=env_int("CARD_FONT_SIZE_USERNAME", 72),
            card_font_size_handle=env_int("CARD_FONT_SIZE_HANDLE", 56),
            game_name=env("GAME_NAME", ""),

            # Logging
            log_level=env("LOG_LEVEL", "INFO"),
            log_to_file=env_bool("LOG_TO_FILE", True),
        )

        # Validate required values
        config._validate()

        return config

    def _validate(self) -> None:
        """Validate that all required configuration values are set.

        Called automatically by from_env(). Checks:
        - Required API keys are non-empty
        - Numeric values are in valid ranges
        - Paths can be resolved

        Raises:
            ConfigError: With a descriptive message listing all
                        validation failures found.
        """
        errors: list[str] = []

        # Required API keys
        if not self.gemini_api_key:
            errors.append(
                "GEMINI_API_KEY is not set. "
                "Get your API key at https://aistudio.google.com/"
            )
        if not self.elevenlabs_api_key:
            errors.append(
                "ELEVENLABS_API_KEY is not set. "
                "Get your API key at https://elevenlabs.io/"
            )

        # Numeric ranges
        if self.target_duration <= 0:
            errors.append(f"TARGET_DURATION must be positive, got {self.target_duration}")
        if self.audio_tail_padding < 0:
            errors.append(f"AUDIO_TAIL_PADDING cannot be negative, got {self.audio_tail_padding}")
        if self.max_retries < 0:
            errors.append(f"MAX_RETRIES cannot be negative, got {self.max_retries}")
        if not (0 <= self.video_crf <= 51):
            errors.append(f"VIDEO_CRF must be 0-51, got {self.video_crf}")
        if self.output_width <= 0 or self.output_height <= 0:
            errors.append(
                f"Output dimensions must be positive, "
                f"got {self.output_width}x{self.output_height}"
            )

        if errors:
            raise ConfigError(
                "Configuration validation failed:\n"
                + "\n".join(f"  • {e}" for e in errors)
            )

    def get_all_directories(self) -> list[Path]:
        """Return all configured directories for batch creation.

        Returns:
            List of Path objects for input_dir, output_dir, temp_dir,
            logs_dir, and prompts_dir.
        """
        return [
            self.input_dir,
            self.output_dir,
            self.temp_dir,
            self.logs_dir,
            self.prompts_dir,
        ]
