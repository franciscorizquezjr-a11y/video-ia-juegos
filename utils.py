"""
utils.py — Shared Utilities for the AI Video Pipeline

This module provides:
- Custom exception hierarchy for structured error handling across the pipeline
- Logging configuration with both console and file output
- File system helpers (directory creation, cleanup, validation)
- Timestamp formatting for FFmpeg commands
- Safe JSON parsing with markdown fence stripping
- Unique run ID generation for tracking pipeline executions

Every other module in the project imports from here. This is the foundation layer.
"""

import api_usage as _api_usage

import json
import logging
import math
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

logger = logging.getLogger(__name__)


# =============================================================================
# Custom Exception Hierarchy
#
# All pipeline-specific exceptions inherit from PipelineError.
# This allows callers to catch broad (PipelineError) or specific errors.
# Each exception type maps to a distinct failure domain in the system.
# =============================================================================

class PipelineError(Exception):
    """Base exception for all pipeline-related errors.

    All custom exceptions in this project inherit from PipelineError,
    enabling callers to catch any pipeline failure with a single except clause
    while still allowing fine-grained handling of specific failure types.
    """
    pass


class LLMError(PipelineError):
    """Raised when the LLM provider (Groq) encounters an error.

    Covers: authentication failures, rate limits, malformed responses,
    network timeouts, and JSON parsing failures from LLM output.
    """
    pass


class VoiceGenerationError(PipelineError):
    """Raised when ElevenLabs voice generation fails.

    Covers: API authentication, rate limits, voice not found,
    text too long, network errors, and file save failures.
    """
    pass


class FFmpegError(PipelineError):
    """Raised when an FFmpeg or FFprobe command fails.

    Covers: binary not found, invalid input files, encoding errors,
    filter graph failures, and subprocess timeouts.
    """
    pass


class ValidationError(PipelineError):
    """Raised when data validation fails.

    Covers: missing required fields in JSON, invalid timestamp ranges,
    schema mismatches, and timing constraint violations.
    """
    pass


class ConfigError(PipelineError):
    """Raised when configuration loading or validation fails.

    Covers: missing .env file, missing required API keys,
    invalid configuration values, and path resolution failures.
    """
    pass


# =============================================================================
# Logging Setup
# =============================================================================

def setup_logging(
    log_level: str = "INFO",
    logs_dir: Path | None = None,
    log_to_file: bool = True,
) -> logging.Logger:
    """Configure application-wide logging with console and optional file output.

    Sets up a root logger with:
    - Console handler: colored, human-readable format for development
    - File handler (optional): detailed format with timestamps for debugging

    Log files are named with a timestamp to avoid overwriting previous runs.

    Args:
        log_level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        logs_dir: Directory to write log files to. Created if it doesn't exist.
        log_to_file: Whether to write logs to a file in addition to console.

    Returns:
        The configured root logger instance.

    Example:
        >>> logger = setup_logging("DEBUG", Path("logs"), log_to_file=True)
        >>> logger.info("Pipeline started")
    """
    # Resolve the numeric log level from the string name
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Configure the root logger — all module loggers inherit from this
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Clear any existing handlers to avoid duplicate output on re-initialization
    root_logger.handlers.clear()

    # --- Console Handler ---
    # Uses a concise format: [LEVEL] module: message
    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_format = logging.Formatter(
        fmt="%(asctime)s │ %(levelname)-8s │ %(name)-20s │ %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_format)
    root_logger.addHandler(console_handler)

    # --- File Handler (optional) ---
    # Uses a detailed format with full timestamps for post-mortem debugging
    if log_to_file and logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = logs_dir / f"pipeline_{timestamp}.log"

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # Always capture everything in file
        file_format = logging.Formatter(
            fmt="%(asctime)s │ %(levelname)-8s │ %(name)-25s │ %(funcName)-20s │ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_format)
        root_logger.addHandler(file_handler)

        try:
            latest_file = logs_dir / "latest.log"
            latest_handler = logging.FileHandler(latest_file, mode="a", encoding="utf-8")
            latest_handler.setLevel(logging.DEBUG)
            latest_handler.setFormatter(file_format)
            root_logger.addHandler(latest_handler)
        except Exception:
            pass

        root_logger.debug(f"Log file created: {log_file}")

    return root_logger


# =============================================================================
# Directory & File Utilities
# =============================================================================

def ensure_directories(*dirs: Path) -> None:
    """Create directories if they don't already exist.

    Silently succeeds if directories already exist (idempotent).
    Creates parent directories as needed.

    Args:
        *dirs: One or more Path objects to create.

    Example:
        >>> ensure_directories(Path("input"), Path("output"), Path("temp"))
    """
    for directory in dirs:
        directory.mkdir(parents=True, exist_ok=True)


def cleanup_directory(dir_path: Path) -> int:
    """Remove all files and subdirectories from a directory.

    The directory itself is preserved (only its contents are deleted).
    Returns the number of items removed for logging purposes.

    Args:
        dir_path: Path to the directory to clean.

    Returns:
        Number of files and directories removed.
    """
    logger = logging.getLogger(__name__)

    if not dir_path.exists():
        logger.debug(f"Directory does not exist, nothing to clean: {dir_path}")
        return 0

    removed_count = 0
    for item in dir_path.iterdir():
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed_count += 1
        except OSError as e:
            # Log but don't crash — cleanup failures shouldn't halt the pipeline
            logger.warning(f"Failed to remove {item}: {e}")

    logger.info(f"Cleaned {removed_count} items from {dir_path}")
    return removed_count


def validate_file_exists(path: Path, description: str = "File") -> None:
    """Verify that a file exists and raise a descriptive error if not.

    Provides human-readable error messages that help users understand
    exactly which file is missing and where it was expected.

    Args:
        path: Path to the file to check.
        description: Human-readable description of what this file is
                     (e.g., "Input video", "Voice audio").

    Raises:
        FileNotFoundError: If the file does not exist, with a message
                          that includes the description and full path.

    Example:
        >>> validate_file_exists(Path("input/gameplay.mp4"), "Input video")
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{description} not found: {path.resolve()}\n"
            f"Please ensure the file exists at the expected location."
        )
    if not path.is_file():
        raise FileNotFoundError(
            f"{description} exists but is not a file: {path.resolve()}\n"
            f"Expected a file, got a directory."
        )


# =============================================================================
# Timestamp & Formatting Utilities
# =============================================================================

def format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS.mmm format for FFmpeg commands.

    FFmpeg accepts timestamps in HH:MM:SS.milliseconds format.
    This function handles the conversion precisely.

    Args:
        seconds: Time in seconds (e.g., 125.750).

    Returns:
        Formatted string (e.g., "00:02:05.750").

    Raises:
        ValueError: If seconds is negative.

    Example:
        >>> format_timestamp(125.75)
        '00:02:05.750'
        >>> format_timestamp(0.5)
        '00:00:00.500'
    """
    if seconds < 0:
        raise ValueError(f"Timestamp cannot be negative: {seconds}")

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60

    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


# =============================================================================
# JSON Utilities
# =============================================================================

def safe_json_parse(text: str) -> dict:
    """Parse a JSON string with robust error handling.

    Handles common LLM output issues:
    - Strips markdown code fences (```json ... ```)
    - Strips leading/trailing whitespace
    - Removes invalid trailing commas before closing braces/brackets
    - Auto-escapes unescaped double quotes in values as a fallback
    - Provides clear error messages on parse failure

    Args:
        text: Raw text that should contain JSON, possibly wrapped
              in markdown fences.

    Returns:
        Parsed dictionary.

    Raises:
        ValueError: If the text cannot be parsed as valid JSON.
    """
    # Strip markdown code fences
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?\s*```\s*$", "", cleaned)
    cleaned = cleaned.strip()

    # Repair common LLM JSON syntax issues
    # 1. Remove trailing commas in arrays/objects (e.g. [1, 2,] -> [1, 2])
    cleaned = re.sub(r',\s*([\]}])', r'\1', cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        # 2. Fallback repair: If it fails because of unescaped quotes inside string properties
        # (e.g. "description": "Showcases the "cozy" cabin"), try to escape them.
        try:
            repaired = _repair_unescaped_quotes(cleaned)
            return json.loads(repaired)
        except Exception:
            pass

        # Provide a preview of the text for debugging
        preview = cleaned[:500] + "..." if len(cleaned) > 500 else cleaned
        raise ValueError(
            f"Failed to parse JSON response.\n"
            f"Error: {e}\n"
            f"Text preview:\n{preview}"
        ) from e


def _repair_unescaped_quotes(json_str: str) -> str:
    """Attempt to escape unescaped double quotes in JSON string values.

    For example: "description": "The plane has a "cozy" cabin"
    Should become: "description": "The plane has a \\"cozy\\" cabin"
    """
    result = []
    i = 0
    n = len(json_str)
    in_string = False

    while i < n:
        char = json_str[i]
        if char == '\\':
            # Append backslash and the next character (escaped character)
            result.append(char)
            if i + 1 < n:
                result.append(json_str[i + 1])
            i += 2
        elif char == '"':
            if not in_string:
                # Start of a string
                in_string = True
                result.append(char)
                i += 1
            else:
                # We are inside a string. Is this the end of the string or an unescaped quote?
                # Scan ahead to see the next non-whitespace character
                next_char = None
                j = i + 1
                while j < n:
                    if not json_str[j].isspace():
                        next_char = json_str[j]
                        break
                    j += 1

                # If the next non-whitespace char is a structural JSON character
                # (or if we reached the end of the JSON document), this is the closing quote.
                if next_char in (":", ",", "}", "]") or next_char is None:
                    in_string = False
                    result.append(char)
                else:
                    # This is an unescaped double quote inside the string
                    result.append('\\"')
                i += 1
        else:
            result.append(char)
            i += 1

    return "".join(result)


# =============================================================================
# Run ID Generation
# =============================================================================

def generate_run_id() -> str:
    """Generate a unique identifier for a pipeline run.

    Combines a timestamp with a short UUID for uniqueness.
    Used to namespace temporary files and log entries per run.

    Returns:
        A string like "20240115_143052_a1b2c3d4" that is both
        human-readable (timestamp) and unique (UUID suffix).

    Example:
        >>> run_id = generate_run_id()
        >>> print(run_id)  # "20260711_190134_a1b2c3d4"
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_uuid = uuid.uuid4().hex[:8]
    return f"{timestamp}_{short_uuid}"


# =============================================================================
# Card Preset Import/Export
# =============================================================================

# Keys that define the visual style of a card preset
CARD_PRESET_KEYS = [
    "card_username", "card_handle", "card_bg_color", "card_text_color",
    "card_border_color", "card_border_width", "card_radius",
    "card_avatar_text", "card_avatar_bg",
    "card_font_username", "card_font_handle", "card_font_body",
    "card_width", "card_height", "card_font_size_body", "card_y_position",
    "card_avatar_radius", "card_font_size_username", "card_font_size_handle",
    "video_zoom", "video_y_position",
]


def export_card_preset(session_state) -> dict:
    """Export current card styling keys from Streamlit session_state to a dict.

    Args:
        session_state: Streamlit session_state object.

    Returns:
        Dictionary with all card preset key-value pairs.
    """
    preset = {}
    for key in CARD_PRESET_KEYS:
        if key in session_state:
            preset[key] = session_state[key]
    return preset


def import_card_preset(data: dict, session_state) -> list[str]:
    """Import card preset values from a dict into Streamlit session_state.

    Only updates keys that exist in the CARD_PRESET_KEYS list, silently
    ignoring unknown keys.

    Args:
        data: Dictionary with card preset key-value pairs.
        session_state: Streamlit session_state object.

    Returns:
        List of keys that were successfully imported.
    """
    imported = []
    for key in CARD_PRESET_KEYS:
        if key in data:
            session_state[key] = data[key]
            imported.append(key)
    return imported


# =============================================================================
# Steam Game Search Utility
# =============================================================================

def clean_steam_html(raw_html: str) -> str:
    """Helper to cleanly convert Steam store HTML into legible plain text paragraphs."""
    if not raw_html:
        return ""
    import html as html_module
    # Replace line breaks, list items, and paragraph tags with newlines
    text = re.sub(r"<(?:br|p|li|h\d)[^>]*>", "\n", raw_html, flags=re.IGNORECASE)
    # Strip any remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    # Collapse multiple blank lines or spaces
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n".join(lines)


def search_steam_game(game_name: str, language: str = "english") -> str:
    """Search for a game on Steam and return both its short and detailed long description.

    Uses the public Steam Store API:
    1. Searches for the game name using the store search endpoint.
    2. Gets the app details for the best matching result.
    3. Extracts and cleans both short_description and detailed_description / about_the_game.
    4. Formats both into a comprehensive overview for viral script generation.

    Args:
        game_name: Name of the game to search for (e.g. "Portal 2").
        language: Preferred language for Steam API query (defaults to "english").

    Returns:
        The game's combined short and long description in plain text, or an empty string
        if not found or on any error.
    """
    try:
        import requests
    except ImportError:
        logger.warning("requests library not installed, cannot search Steam")
        return ""

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
        }
        # Step 1: Search for the game
        search_url = "https://store.steampowered.com/api/storesearch/"
        search_params = {"term": game_name, "l": language, "cc": "US" if language == "english" else "ES"}
        resp = requests.get(search_url, params=search_params, headers=headers, timeout=10)
        resp.raise_for_status()
        search_data = resp.json()

        items = search_data.get("items", [])
        if not items:
            logger.warning(f"No Steam results found for: {game_name}")
            return ""

        app_id = items[0].get("id")
        if not app_id:
            return ""

        # Step 2: Get app details
        details_url = "https://store.steampowered.com/api/appdetails"
        details_params = {"appids": str(app_id), "l": language, "cc": "US" if language == "english" else "ES"}
        resp2 = requests.get(details_url, params=details_params, headers=headers, timeout=10)
        resp2.raise_for_status()
        details_data = resp2.json()

        # Resilient app_data extraction (Steam sometimes keys under redirected/soundtrack/parent app ID)
        app_data = details_data.get(str(app_id)) or (next(iter(details_data.values()), {}) if details_data else {})
        if not app_data.get("success"):
            return ""

        data = app_data.get("data", {})
        raw_short = data.get("short_description", "")
        raw_long = data.get("about_the_game", "") or data.get("detailed_description", "")

        clean_short = clean_steam_html(raw_short)
        clean_long = clean_steam_html(raw_long)

        # Cap long description at ~2500 chars to avoid token bloating while keeping core mechanics and features
        if len(clean_long) > 2500:
            clean_long = clean_long[:2500].rsplit("\n", 1)[0] + "..."

        # Combine short and long descriptions
        parts = []
        if clean_short:
            parts.append(f"Short Summary:\n{clean_short}")
        if clean_long and clean_long != clean_short:
            parts.append(f"Detailed Description & Features:\n{clean_long}")

        combined = "\n\n".join(parts) if parts else (clean_short or clean_long)

        logger.info(f"Steam descriptions (short & long) retrieved for '{game_name}' (appid={app_id}): {len(combined)} chars")
        return combined

    except Exception as e:
        logger.warning(f"Steam search failed for '{game_name}': {e}")
        return ""




def generate_hook_with_groq(game_name: str, groq_api_key: str, prompt_variation: int = 0, gemini_api_key: str | None = None) -> str:
    """Search for the game on Steam and generate a hook text using Groq API (with model fallback & Gemini fallback).

    Args:
        game_name: Name of the game.
        groq_api_key: The user's Groq API Key.
        prompt_variation: An integer to vary the prompt slightly and get different results.
        gemini_api_key: Optional Gemini API Key for fallback generation.

    Returns:
        The generated hook text in English.
    """
    import requests
    clean_groq_key = groq_api_key.strip().strip('"').strip("'") if groq_api_key else ""
    clean_gemini_key = gemini_api_key.strip().strip('"').strip("'") if gemini_api_key else ""

    if not clean_groq_key and not clean_gemini_key:
        raise ValueError("Falta la API Key de Groq o Gemini.")

    desc = search_steam_game(game_name)
    
    # Customize system instruction for variety
    variations = [
        "Sé directo, emocionante y crea mucha intriga. Empieza con una frase corta e impactante.",
        "Sé curioso, haz una pregunta llamativa o destaca un contraste irónico o brutal del juego.",
        "Enfócate en la jugabilidad, el caos, o la diversión cooperativa/sigilo.",
        "Haz énfasis en la atmósfera y en lo adictivo que es el bucle de juego.",
    ]
    var_text = variations[prompt_variation % len(variations)]

    examples = (
        "Excellent hook examples (follow as reference for structure and style):\n"
        "- 'A co-op game where you carry a mafia body bag to the top of a skyscraper except the body isn't dead and it keeps fighting back.'\n"
        "- 'No rubber banding rivals. Everyone races at their real skill level and if you see a car you want you can just challenge the owner for it.'\n"
        "- 'A solo developer made an open-world racing RPG set in 90s Japan inspired by Auto Modellista and Midnight Club.'\n"
        "- 'A sandcastle building game with real water and sand physics. Carve too thin and your tower may slump.'\n"
    )

    if desc:
        prompt = (
            f"Game name: '{game_name}'.\n"
            f"Official Steam description: {desc}\n\n"
            f"{examples}\n"
            f"Additional style instruction: {var_text}\n\n"
            f"CRITICAL RULES:\n"
            f"1. Write the hook text in ENGLISH.\n"
            f"2. The hook text MUST consist of 3 to 4 sentences, and must be around 30 words in total, but NEVER less than 30 words (3-4 frases de alrededor de 30 palabras en total, nunca menos de 30).\n"
            f"3. Do NOT invent any features, mechanics, or facts that are not present in the Steam description. Base the hook SOLELY on the official Steam details.\n"
            f"4. Output ONLY the generated hook text. Do not include quotes, explanations, intros, or hashtags.\n"
            f"5. Optimize the hook to maximize scroll stop: make the first sentence extremely compelling, punchy, or intriguing to instantly grab the viewer's attention and stop them from scrolling.\n"
            f"6. Make the hook sound extremely natural, casual, and conversational, like a real gamer explaining the game to a friend. STRICTLY avoid corporate sales pitches, advertisements, or marketing slogans (avoid words/phrases like 'get ready to', 'introducing', 'experience the thrill', 'immersive')."
        )
    else:
        prompt = (
            f"Game name: '{game_name}'.\n\n"
            f"{examples}\n"
            f"Additional style instruction: {var_text}\n\n"
            f"CRITICAL RULES:\n"
            f"1. Write the hook text in ENGLISH.\n"
            f"2. The hook text MUST consist of 3 to 4 sentences, and must be around 30 words in total, but NEVER less than 30 words (3-4 frases de alrededor de 30 palabras en total, nunca menos de 30).\n"
            f"3. Do NOT invent features or facts about the game.\n"
            f"4. Output ONLY the generated hook text. Do not include quotes, explanations, intros, or hashtags.\n"
            f"5. Optimize the hook to maximize scroll stop: make the first sentence extremely compelling, punchy, or intriguing to instantly grab the viewer's attention and stop them from scrolling.\n"
            f"6. Make the hook sound extremely natural, casual, and conversational, like a real gamer explaining the game to a friend. STRICTLY avoid corporate sales pitches, advertisements, or marketing slogans (avoid words/phrases like 'get ready to', 'introducing', 'experience the thrill', 'immersive')."
        )

    # 1. Try Groq API with multiple candidate models if Groq key is provided
    last_error = None
    if clean_groq_key:
        models_to_try = [
            "qwen/qwen3.8-27b",
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
        ]
        headers = {
            "Authorization": f"Bearer {clean_groq_key}",
            "Content-Type": "application/json",
        }
        
        for model_name in models_to_try:
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": "You are a professional copywriting and gaming creator for TikTok and YouTube Shorts, specializing in organic video game hooks. You write exclusively in English and optimize all texts to sound extremely natural, conversational, and non-salesy, avoiding marketing pitches, corporate jargon, and buzzwords while still maximizing scroll stop (instantly grabbing attention)."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.8,
            }
            try:
                resp = _api_usage.usage_post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=15)
                if resp.status_code == 200:
                    res_data = resp.json()
                    hook = res_data["choices"][0]["message"]["content"].strip().strip('"').strip("'")
                    try:
                        from audio_aligner import clean_script_text_for_voice
                        return clean_script_text_for_voice(hook)
                    except Exception:
                        return hook
                elif resp.status_code == 401:
                    last_error = "API Key de Groq no válida (Unauthorized 401)"
                    break
                else:
                    err_text = resp.text
                    last_error = f"Groq HTTP {resp.status_code}: {err_text}"
                    if "model_decommissioned" in err_text or "decommissioned" in err_text:
                        continue
                    if resp.status_code == 400:
                        break
            except Exception as e:
                last_error = str(e)

    # 2. Fallback to Gemini API if Groq failed or key not provided
    if clean_gemini_key:
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=clean_gemini_key)
            system_instruction = "You are a professional copywriting and gaming creator for TikTok and YouTube Shorts, specializing in organic video game hooks. You write exclusively in English and optimize all texts to sound extremely natural, conversational, and non-salesy."
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.8,
            )
            response = _api_usage.usage_generate(client.models.generate_content, 
                model="gemini-2.5-flash",
                contents=prompt,
                config=config,
            )
            if response.text:
                hook = response.text.strip().strip('"').strip("'")
                try:
                    from audio_aligner import clean_script_text_for_voice
                    return clean_script_text_for_voice(hook)
                except Exception:
                    return hook
        except Exception as gem_e:
            logger.warning(f"Gemini fallback failed for hook generation: {gem_e}")

    if clean_groq_key and last_error:
        raise RuntimeError(f"No se pudo conectar con Groq ({last_error}). Verifica tu Groq API Key.")
    else:
        raise RuntimeError("No se pudo generar el texto del gancho. Verifica tus llaves de API (Groq / Gemini).")


def modify_hook_length(
    current_hook: str,
    action: str,
    groq_api_key: str,
    gemini_api_key: str | None = None
) -> str:
    """Modify an existing hook text to be slightly shorter or longer while preserving scroll-stop impact.

    Args:
        current_hook: The current hook text.
        action: Either 'shorter' or 'longer'.
        groq_api_key: The user's Groq API Key.
        gemini_api_key: Optional Gemini API Key for fallback generation.

    Returns:
        The modified hook text in English.
    """
    import requests
    clean_groq_key = groq_api_key.strip().strip('"').strip("'") if groq_api_key else ""
    clean_gemini_key = gemini_api_key.strip().strip('"').strip("'") if gemini_api_key else ""

    if not clean_groq_key and not clean_gemini_key:
        raise ValueError("Falta la API Key de Groq o Gemini.")

    if action.lower() == "shorter":
        instruction = (
            f"Original hook text:\n'{current_hook}'\n\n"
            "TASK:\n"
            "Rewrite the hook text to make it SLIGHTLY SHORTER and more concise (around 15-22 words total, or 1-2 punchy sentences).\n"
            "CRITICAL RULES:\n"
            "1. Preserve the maximum scroll-stopping impact of the first sentence.\n"
            "2. Maintain the same conversational, natural, casual gamer tone in ENGLISH.\n"
            "3. Do NOT add salesy or marketing jargon.\n"
            "4. Output ONLY the modified hook text, without quotes or explanations."
        )
    else:
        instruction = (
            f"Original hook text:\n'{current_hook}'\n\n"
            "TASK:\n"
            "Expand the hook text to make it SLIGHTLY LONGER (around 40-50 words total, or 3-4 descriptive sentences) by weaving in slightly more exciting context or intriguing gameplay details.\n"
            "CRITICAL RULES:\n"
            "1. Preserve the maximum scroll-stopping impact of the first sentence.\n"
            "2. Maintain the same conversational, natural, casual gamer tone in ENGLISH.\n"
            "3. Do NOT add salesy or marketing jargon.\n"
            "4. Output ONLY the modified hook text, without quotes or explanations."
        )

    last_error = None
    if clean_groq_key:
        models_to_try = [
            "qwen/qwen3.8-27b",
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
        ]
        headers = {
            "Authorization": f"Bearer {clean_groq_key}",
            "Content-Type": "application/json",
        }
        
        for model_name in models_to_try:
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": "You are an expert social media copywriter for gaming content on TikTok and Shorts."},
                    {"role": "user", "content": instruction}
                ],
                "temperature": 0.7,
            }
            try:
                resp = _api_usage.usage_post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=15)
                if resp.status_code == 200:
                    res_data = resp.json()
                    new_h = res_data["choices"][0]["message"]["content"].strip().strip('"').strip("'")
                    try:
                        from audio_aligner import clean_script_text_for_voice
                        return clean_script_text_for_voice(new_h)
                    except Exception:
                        return new_h
                elif resp.status_code == 401:
                    last_error = "API Key de Groq no válida (Unauthorized 401)"
                    break
                else:
                    err_text = resp.text
                    last_error = f"Groq HTTP {resp.status_code}: {err_text}"
                    if "model_decommissioned" in err_text or "decommissioned" in err_text:
                        continue
                    if resp.status_code == 400:
                        break
            except Exception as e:
                last_error = str(e)

    if clean_gemini_key:
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=clean_gemini_key)
            config = types.GenerateContentConfig(
                system_instruction="You are an expert social media copywriter for gaming content on TikTok and Shorts.",
                temperature=0.7,
            )
            response = _api_usage.usage_generate(client.models.generate_content, 
                model="gemini-2.5-flash",
                contents=instruction,
                config=config,
            )
            if response.text:
                new_h = response.text.strip().strip('"').strip("'")
                try:
                    from audio_aligner import clean_script_text_for_voice
                    return clean_script_text_for_voice(new_h)
                except Exception:
                    return new_h
        except Exception as gem_e:
            logger.warning(f"Gemini fallback failed for hook length modification: {gem_e}")

    if clean_groq_key and last_error:
        raise RuntimeError(f"No se pudo conectar con Groq ({last_error}).")
    else:
        raise RuntimeError("No se pudo modificar el gancho. Verifica tus llaves de API.")


def clean_v2_script_text(raw_text: str) -> str:
    """Sanitize AI script output: remove quotes, markdown, subtitle timestamps, line numbers, stage tags,
    replace commas/colons with semicolons, apply contractions, and format cleanly for TTS."""
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
        # Remove bracketed scene cues like [Music], [Cut to gameplay], [Audio]
        l = re.sub(r"\[.*?\]", "", l).strip()
        # Remove HTML/XML tags
        l = re.sub(r"<[^>]+>", "", l).strip()
        # Remove common speaker prefixes like 'Voiceover:', 'Narrator:', 'Guión:'
        l = re.sub(r'^(?:["\'`]\s*)?(?:voiceover|narrator|script|guión|guion|locución|locucion|voice)\s*:\s*', '', l, flags=re.IGNORECASE).strip()
        if l:
            cleaned_lines.append(l)

    res = " ".join(cleaned_lines).strip()
    res = re.sub(r'^(?:["\'`]\s*)?(?:voiceover|narrator|script|guión|guion|locución|locucion|voice)\s*:\s*', '', res, flags=re.IGNORECASE).strip()

    # Clean double dashes and em dashes
    res = re.sub(r"\s*--+\s*", "; ", res)
    res = re.sub(r"\s*[—–]\s*", "; ", res)

    # Clean ellipses (... or .. or …) which stall TTS engines
    res = re.sub(r"\.{2,}|\u2026", ".", res)

    # Strip surrounding quotes
    res = res.strip('"').strip("'").strip("`").strip()

    # Apply voice punctuation (semicolons for pauses) and contractions (keeping 'you are' separate)
    try:
        from audio_aligner import clean_script_text_for_voice
        res = clean_script_text_for_voice(res)
    except Exception as e:
        logger.warning(f"Error applying clean_script_text_for_voice in clean_v2_script_text: {e}")

    return res


def is_script_text_equivalent(t1: str | None, t2: str | None) -> bool:
    """Check if two script texts are functionally equivalent (ignoring punctuation differences like commas vs semicolons, casing, and whitespace)."""
    if not t1 or not t2:
        return False
    w1 = [w.lower() for w in re.findall(r'\b\w+\b', t1)]
    w2 = [w.lower() for w in re.findall(r'\b\w+\b', t2)]
    return bool(w1 and w2 and w1 == w2)


def ensure_steam_hook_prefix(text: str) -> str:
    """Ensure the voiceover script begins naturally with 'Here\'s a new Steam game;' without duplicate intro phrases."""
    if not text:
        return "Here's a new Steam game you need to check out."
    cleaned = text.strip().strip('"').strip("'").strip("`").strip()

    # Regex to catch any leading hook phrase like "Here is a new game", "Here's a new Steam game", "This is a new game", "In this game", etc.
    # including chained or repeated intro phrases like "Here's a new game, here is a new action game"
    intro_chain_regex = (
        r"^(?:"
        r"(?:here(?:'s|s|\s+is)|this\s+is|check\s+out\s+this|so\s+in\s+this|in\s+this)\s+"
        r"(?:a\s+)?(?:new\s+)?(?:steam\s+)?(?:[\w\-]+\s+)?game[;,:\s]*"
        r")+"
        r"(.*)$"
    )

    m = re.match(intro_chain_regex, cleaned, flags=re.IGNORECASE | re.DOTALL)
    if m:
        remainder = m.group(1).strip()
        if remainder:
            first_w = remainder.split()[0].lower() if remainder.split() else ""
            if first_w in ("where", "that", "with", "about"):
                res = f"Here's a new Steam game {remainder}"
            else:
                remainder = remainder[0].upper() + remainder[1:] if len(remainder) > 1 else remainder.upper()
                res = f"Here's a new Steam game; {remainder}"
        else:
            res = "Here's a new Steam game."
    else:
        first_w = cleaned.split()[0].lower() if cleaned.split() else ""
        if first_w in ("where", "that", "with", "about"):
            res = f"Here's a new Steam game {cleaned}"
        else:
            cleaned = cleaned[0].upper() + cleaned[1:] if len(cleaned) > 1 else cleaned.upper()
            res = f"Here's a new Steam game; {cleaned}"

    try:
        from audio_aligner import clean_script_text_for_voice
        return clean_script_text_for_voice(res)
    except Exception:
        return res


def ensure_hook_type_prefix(text: str, hook_type: str = "steam_new_game") -> str:
    """Ensure the voiceover script begins naturally with the selected hook type."""
    if not text:
        if hook_type == "so_in_this_new_game":
            return "So in this new game; you dive into an intense world where every choice matters."
        return "Here's a new Steam game you need to check out."

    cleaned = text.strip()

    if hook_type == "so_in_this_new_game":
        pattern = r"^(?:so\s+in\s+this\s+new\s+game|in\s+this\s+new\s+game)[;,:\s]*(.*)$"
        match = re.match(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            remainder = match.group(1).strip()
            if remainder:
                res = f"So in this new game; {remainder}"
            else:
                res = "So in this new game."
        else:
            steam_pat = r"^(?:(?:here(?:'s|s|\s+is))\s+a\s+new\s+steam\s+game)[;,:\s]*(.*)$"
            s_match = re.match(steam_pat, cleaned, flags=re.IGNORECASE)
            if s_match:
                rem = s_match.group(1).strip()
                if rem:
                    res = f"So in this new game; {rem}"
                else:
                    res = "So in this new game."
            else:
                res = f"So in this new game; {cleaned}"
        try:
            from audio_aligner import clean_script_text_for_voice
            return clean_script_text_for_voice(res)
        except Exception:
            return res

    return ensure_steam_hook_prefix(cleaned)


def _voice_clean_keep_opening(text: str, opening_text: str = "") -> str:
    """clean_script_text_for_voice, except a leading custom opening stays verbatim
    (no contractions or comma-to-semicolon changes inside a campaign-mandated phrase)."""
    try:
        from audio_aligner import clean_script_text_for_voice
    except Exception:
        return (text or "").strip()
    from script_retention import normalize_opening_text, opening_prefix_end
    body = (text or "").strip()
    opening = normalize_opening_text(opening_text)
    end = opening_prefix_end(body, opening) if opening else None
    if end is None:
        return clean_script_text_for_voice(body)
    head, rest = body[:end], clean_script_text_for_voice(body[end:])
    if not rest:
        return head.strip()
    joiner = "" if rest[:1] in ",.;:!?" else " "
    return head.strip() + joiner + rest


def apply_opening(text: str, hook_type: str = "steam_new_game", opening_text: str = "") -> str:
    """Return the voiceover script with the opener required by hook_type.

    - steam_new_game: ensure_steam_hook_prefix (current default behaviour).
    - so_in_this_new_game: ensure_hook_type_prefix (current behaviour for that mode).
    - custom: the script starts with opening_text verbatim (added if missing).
    - direct: a leading formulaic opener ("Here's a new Steam game", "So in this new game")
      is removed when the rest still reads as a sentence.
    Unknown hook types (e.g. "caveman") leave the text untouched.
    """
    from script_retention import (
        HOOK_TYPES, HOOK_TYPE_CUSTOM, HOOK_TYPE_DIRECT, HOOK_TYPE_SO_IN_THIS_NEW_GAME,
        ensure_opening_text, resolve_hook_type, strip_formulaic_opener,
    )
    raw_mode = str(hook_type or "steam_new_game").strip().lower()
    if raw_mode not in HOOK_TYPES:
        return (text or "").strip()
    mode = resolve_hook_type(raw_mode, opening_text)
    if mode == HOOK_TYPE_CUSTOM:
        return _voice_clean_keep_opening(ensure_opening_text(text or "", opening_text), opening_text)
    if mode == HOOK_TYPE_DIRECT:
        stripped = strip_formulaic_opener(text or "")
        try:
            from audio_aligner import clean_script_text_for_voice
            return clean_script_text_for_voice(stripped)
        except Exception:
            return stripped
    if mode == HOOK_TYPE_SO_IN_THIS_NEW_GAME:
        return ensure_hook_type_prefix(text, "so_in_this_new_game")
    return ensure_steam_hook_prefix(text)


def load_viral_script_examples(max_examples: int = 50) -> str:
    """
    Loads ALL viral reference scripts and structural analysis blueprint for the LLM prompt.
    """
    base_dir = Path(__file__).resolve().parent
    viral_dir = base_dir / "ViralScripts"
    if not viral_dir.exists():
        viral_dir = base_dir / "Viral Scripts"

    analysis_file = base_dir / "prompts" / "viral_scripts_structure_analysis.txt"
    if not analysis_file.exists():
        analysis_file = base_dir / "data" / "viral_scripts_structure_analysis.txt"

    analysis_summary = ""
    if analysis_file.exists():
        try:
            analysis_summary = analysis_file.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            logger.warning(f"Could not read viral analysis file: {e}")

    extracted_scripts = []
    if viral_dir.exists():
        valid_extensions = {".srt", ".txt"}
        files = [f for f in sorted(viral_dir.iterdir()) if f.is_file() and f.suffix.lower() in valid_extensions]
        for fpath in files:
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
                clean_text = clean_v2_script_text(content)
                if clean_text.lower().endswith("non-human."):
                    clean_text = clean_text[:-10].strip()
                if clean_text and len(clean_text.split()) >= 15:
                    extracted_scripts.append((fpath.name, clean_text))
            except Exception as e:
                logger.warning(f"Could not read viral script {fpath.name}: {e}")

    formatted_output = []
    if analysis_summary:
        formatted_output.append("=== MASTER VIRAL SCRIPT STRUCTURAL ANALYTICS ===\n" + analysis_summary)

    if extracted_scripts:
        formatted_output.append(f"=== ALL {len(extracted_scripts)} REAL VIRAL REFERENCE EXAMPLES FROM COLLECTION (STUDY HOOK LENGTH, PACING & TONALITY) ===")
        sampled = extracted_scripts[:max_examples] if max_examples else extracted_scripts
        for i, (fname, text) in enumerate(sampled, start=1):
            parts = re.split(r'(?<=[.!?])\s+', text)
            hook_sentence = parts[0].strip() if parts else text
            hook_words = len(hook_sentence.split())
            formatted_output.append(
                f"--- Reference Script #{i} ({fname}) ---\n"
                f"Opening Hook ({hook_words} words): \"{hook_sentence}\"\n"
                f"Full Script: \"{text}\""
            )

    return "\n\n".join(formatted_output)


def generate_v2_script(
    game_name: str,
    target_duration: float = 30.0,
    tone: str = "viral_hype",
    briefing_text: str = "",
    hook_type: str = "steam_new_game",
    groq_api_key: str = "",
    gemini_api_key: str | None = None,
    script_mode: str = "normal",
    game_genre: str = "",
    opening_text: str = "",
    speech_speed: float = 1.12,
) -> dict:
    """
    Generate high-retention viral promo script for Trapster / Short V2 IA using
    structured Hook Construction (First Words, Game Genre, First Punch, Second Punch)
    and Retention Construction (Setup, Escalation, Payoff, CTA, Open Loops).

    hook_type: "steam_new_game" (default), "so_in_this_new_game", "direct" (no formula
    opener) or "custom" (starts exactly with opening_text). speech_speed is the voice
    speed that will be used, so the word budget matches the final narration.
    """
    import requests
    from script_retention import (
        HOOK_TYPE_CUSTOM,
        HOOK_TYPE_DIRECT,
        build_retention_script_prompt,
        parse_and_repair_retention_script,
        resolve_hook_type,
    )

    opening_mode = resolve_hook_type(hook_type, opening_text)
    if script_mode == "caveman" and opening_mode == HOOK_TYPE_DIRECT:
        opening_mode = "steam_new_game"  # the caveman formula is its own opener
    opening_instruction = (
        "Follow the user prompt's OPENING MODE exactly; it overrides the openings of the reference scripts. "
        if opening_mode in (HOOK_TYPE_DIRECT, HOOK_TYPE_CUSTOM) else ""
    )

    clean_groq_key = groq_api_key.strip().strip('"').strip("'") if groq_api_key else ""
    clean_gemini_key = gemini_api_key.strip().strip('"').strip("'") if gemini_api_key else ""

    if not clean_groq_key and not clean_gemini_key:
        raise ValueError("Falta la API Key de Groq o Gemini.")

    # Retrieve Steam game info
    desc = search_steam_game(game_name) or "Un videojuego independiente con mecánicas de acción, estrategia y alto desafío."
    viral_references = load_viral_script_examples()

    # Build structured retention prompt embedding 20 Core Retention Principles
    prompt = build_retention_script_prompt(
        game_name=game_name,
        target_duration=target_duration,
        tone=tone,
        game_info=desc,
        briefing_text=briefing_text.strip() if briefing_text else "No additional briefing provided. Focus on high-retention viral gameplay hooks and emergent player chaos.",
        viral_references=viral_references,
        script_mode=script_mode,
        game_genre=game_genre,
        hook_type=opening_mode,
        opening_text=opening_text or "",
        speech_speed=speech_speed,
    )

    last_error = None
    raw_response = ""

    # 1. Try Groq with structured JSON mode
    if clean_groq_key:
        models_to_try = [
            "qwen/qwen3.8-27b",
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "gemma2-9b-it",
            "mixtral-8x7b-32768",
        ]
        headers = {
            "Authorization": f"Bearer {clean_groq_key}",
            "Content-Type": "application/json",
        }
        for model_name in models_to_try:
            payload = {
                "model": model_name,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an elite viral gaming scriptwriter and creative director for TikTok, Reels, and YouTube Shorts (Trapster). " +
                            ("Write in simple caveman English with short sentences, following the user prompt's exact hook and retention rules. " if script_mode == "caveman" else "Write in an authentic 'Buddy Hype' conversational tone (excited gamer friend on Discord/TikTok). ") +
                            opening_instruction + "Use kinetic action verbs and concrete physical nouns. Strictly avoid corporate buzzwords (epic, amazing, incredible, delve into, immerse yourself). "
                            "You must respond ONLY with valid JSON conforming to the requested schema. "
                            "Never include markdown formatting, backticks, or conversational text."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.7,
            }
            try:
                resp = _api_usage.usage_post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=25)
                if resp.status_code == 200:
                    raw_response = resp.json()["choices"][0]["message"]["content"].strip()
                    break
                elif resp.status_code == 401:
                    last_error = "API Key de Groq no válida (401)"
                    break
                else:
                    last_error = f"Groq {resp.status_code}: {resp.text}"
            except Exception as e:
                last_error = str(e)

    # 2. Fallback to Gemini with JSON response mode
    if not raw_response and clean_gemini_key:
        gemini_models = [
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
        for g_model in gemini_models:
            try:
                from google import genai
                from google.genai import types
                client = genai.Client(api_key=clean_gemini_key)
                config = types.GenerateContentConfig(
                    system_instruction=(
                        "You are an elite viral gaming scriptwriter and creative director for TikTok, Reels, and YouTube Shorts (Trapster). " +
                        ("Write in simple caveman English with short sentences, following the user prompt's exact hook and retention rules. " if script_mode == "caveman" else "Write in an authentic 'Buddy Hype' conversational tone (excited gamer friend on Discord/TikTok). ") +
                        opening_instruction + "Use kinetic action verbs and concrete physical nouns. Strictly avoid corporate buzzwords (epic, amazing, incredible, delve into, immerse yourself). "
                        "You must respond ONLY with valid JSON conforming to the requested schema. "
                        "Never include markdown formatting, backticks, or conversational text."
                    ),
                    response_mime_type="application/json",
                    temperature=0.7,
                )
                response = _api_usage.usage_generate(client.models.generate_content, 
                    model=g_model,
                    contents=prompt,
                    config=config,
                )
                if response.text:
                    raw_response = response.text.strip()
                    break
            except Exception as gem_e:
                logger.warning(f"Gemini model {g_model} failed for retention script generation: {gem_e}")

    if not raw_response:
        if last_error:
            raise RuntimeError(f"Error generando guión con Groq: {last_error}")
        raise RuntimeError("No se pudo generar el guión. Comprueba tus API Keys de Groq o Gemini.")

    # Parse, normalize, and validate structured RetentionScript
    retention_obj = parse_and_repair_retention_script(
        raw_content=raw_response,
        game_name=game_name,
        target_duration=target_duration,
        hook_type=opening_mode,
        opening_text=opening_text or "",
    )

    clean_text = retention_obj.clean_script_text
    try:
        if opening_mode == HOOK_TYPE_CUSTOM:
            # Keep the campaign-mandated opening verbatim.
            clean_text = _voice_clean_keep_opening(clean_text, opening_text)
        else:
            from audio_aligner import clean_script_text_for_voice
            clean_text = clean_script_text_for_voice(clean_text)
    except Exception:
        pass

    word_count = len(clean_text.split())
    est_duration = round(word_count / 2.3, 1)

    return {
        "script": clean_text,
        "word_count": word_count,
        "estimated_duration": est_duration,
        "target_duration": target_duration,
        "retention_data": retention_obj.to_dict(),
    }


def modify_v2_script_length(
    current_script: str,
    action: str,
    game_name: str,
    target_duration: float = 30.0,
    custom_instruction: str = "",
    groq_api_key: str = "",
    gemini_api_key: str | None = None,
    hook_type: str | None = None,
    opening_text: str = "",
    speech_speed: float | None = None,
    script_mode: str = "normal",
    game_genre: str = "",
) -> dict:
    """Modify an existing V2 promo script in English (shorter, longer, change hook, custom instruction, more viral, regenerate).

    Optional opening options (all default to the current behaviour):
    - hook_type / opening_text: "custom" keeps opening_text as the exact first words of the
      modified script; "direct" asks for no formulaic opener. Other values leave the
      modification prompts unchanged. "regenerate" passes them to generate_v2_script.
    - speech_speed: voice speed used for the regenerated word budget (None = generator default).
    """
    import requests
    clean_groq_key = groq_api_key.strip().strip('"').strip("'") if groq_api_key else ""
    clean_gemini_key = gemini_api_key.strip().strip('"').strip("'") if gemini_api_key else ""

    if not clean_groq_key and not clean_gemini_key:
        raise ValueError("Falta la API Key de Groq o Gemini.")

    cur_words = len(current_script.split())
    act = (action or "").lower().strip()

    from script_retention import HOOK_TYPE_CUSTOM, HOOK_TYPE_DIRECT, resolve_hook_type
    clean_opening = (opening_text or "").strip()
    opening_mode = resolve_hook_type(hook_type, clean_opening) if hook_type else ""
    opening_rule = ""
    if opening_mode == HOOK_TYPE_CUSTOM:
        safe_opening = clean_opening.replace('"', "'")
        opening_rule = (
            f"\nOPENING RULE: The script MUST begin with exactly these words: \"{safe_opening}\". "
            "Keep them verbatim as the first words, then continue."
        )
    elif opening_mode == HOOK_TYPE_DIRECT:
        opening_rule = (
            "\nOPENING RULE: Do NOT start with a formulaic opener such as \"Here's a new Steam game\", "
            "\"So in this new game\", \"Check out this\" or \"Imagine\". The first 3-5 words must already "
            "state the strongest selling point or payoff."
        )

    if act in ("change_hook", "hook", "new_hook"):
        instruction = (
            f"Original voiceover script for '{game_name}' ({cur_words} words):\n"
            f"\"{current_script}\"\n\n"
            f"MISSION: Replace ONLY the opening hook (the first 1-2 sentences / first 3 seconds) with a brand new, ultra-addictive scroll-stopping hook IN ENGLISH, while leaving the rest of the script and its narrative payoff intact.\n"
            f"CRITICAL RULES:\n"
            f"1. Create a shocking, paradoxical, high-tension, or irresistible opening hook for the beginning (inspired by proven viral gaming hook archetypes: Inverted Mechanic, Shared Chaos Hybrid, High-Stakes Mystery, or Deceptive Scope).\n"
            f"2. Seamlessly transition from the new hook into the remainder of the script.\n"
            f"3. Keep total word count around {cur_words} words (~{int(target_duration)}s duration).\n"
            f"4. The output script MUST be 100% in English.\n"
            f"5. STRICTLY PROHIBITED: Do NOT output timestamps (00:00:00), line numbers, or scene cues. Do NOT use double dashes (--) or ellipses (...).\n"
            f"6. Return ONLY the raw spoken text of the entire script with the new hook."
        )
    elif act in ("custom_instruction", "custom", "instruct"):
        user_notes = custom_instruction.strip() if custom_instruction else "Make the script more engaging, dynamic, and viral."
        instruction = (
            f"Original voiceover script for '{game_name}' ({cur_words} words):\n"
            f"\"{current_script}\"\n\n"
            f"USER MODIFICATION INSTRUCTIONS:\n"
            f"\"{user_notes}\"\n\n"
            f"MISSION: Rewrite and modify the voiceover script according to the user instructions above, while maintaining viral short-form retention principles.\n"
            f"CRITICAL RULES:\n"
            f"1. Strictly incorporate the user's requested changes.\n"
            f"2. Keep a powerful scroll-stopping hook in the first 3 seconds.\n"
            f"3. Maintain natural spoken cadence and breathing pauses in English (~{int(target_duration)}s duration).\n"
            f"4. The output script MUST be 100% in English.\n"
            f"5. STRICTLY PROHIBITED: Do NOT output timestamps (00:00:00), line numbers, or scene cues. Do NOT use double dashes (--) or ellipses (...).\n"
            f"6. Return ONLY the raw spoken text of the modified script."
        )
    elif act in ("more_viral", "viral", "hype"):
        instruction = (
            f"Original voiceover script for '{game_name}' ({cur_words} words):\n"
            f"\"{current_script}\"\n\n"
            f"MISSION: Rewrite this script with maximum viral energy, hype, and intense retention IN ENGLISH.\n"
            f"CRITICAL RULES:\n"
            f"1. Upgrade the hook into an extreme, shocking scroll-stopper.\n"
            f"2. Amplify the drama, stakes, and curiosity in every single sentence.\n"
            f"3. Maintain natural spoken cadence in English (~{int(target_duration)}s duration).\n"
            f"4. The output script MUST be 100% in English.\n"
            f"5. STRICTLY PROHIBITED: Do NOT output timestamps (00:00:00), line numbers, or scene cues. Do NOT use double dashes (--) or ellipses (...).\n"
            f"6. Return ONLY the raw spoken text of the revised script."
        )
    elif act == "shorter":
        target_words = max(25, int(cur_words * 0.75))
        instruction = (
            f"Original voiceover script for '{game_name}' ({cur_words} words):\n"
            f"\"{current_script}\"\n\n"
            f"MISSION: Shorten this script to approximately {target_words} words IN ENGLISH.\n"
            f"CRITICAL RULES:\n"
            f"1. Keep the high-impact scroll-stopping hook in the first 3 seconds.\n"
            f"2. Cut secondary fluff or repetition, going straight to the excitement and intrigue.\n"
            f"3. Maintain natural conversational flow and cadence in English (avoid robotic lists or bullet points).\n"
            f"4. The output script MUST be in English.\n"
            f"5. STRICTLY PROHIBITED: Do NOT output timestamps (00:00:00), line numbers, or scene cues. Do NOT use double dashes (--) or ellipses (...).\n"
            f"6. Return ONLY the raw spoken text of the shortened script."
        )
    elif act == "longer":
        target_words = min(160, int(cur_words * 1.35))
        instruction = (
            f"Original voiceover script for '{game_name}' ({cur_words} words):\n"
            f"\"{current_script}\"\n\n"
            f"MISSION: Expand and lengthen this script to approximately {target_words} words IN ENGLISH.\n"
            f"CRITICAL RULES:\n"
            f"1. Keep the strong initial hook intact.\n"
            f"2. Add dramatic tension, deeper gameplay mechanics, and strengthen the 'Open Loop' mystery/challenge.\n"
            f"3. Write with natural conversational pacing and rhythm for spoken voiceover in English.\n"
            f"4. The output script MUST be in English.\n"
            f"5. STRICTLY PROHIBITED: Do NOT output timestamps (00:00:00), line numbers, or scene cues. Do NOT use double dashes (--) or ellipses (...).\n"
            f"6. Return ONLY the raw spoken text of the expanded script."
        )
    else:
        # Regenerate fresh angle
        regen_kwargs = {}
        if hook_type:
            regen_kwargs["hook_type"] = hook_type
            regen_kwargs["opening_text"] = clean_opening
        if speech_speed is not None:
            regen_kwargs["speech_speed"] = speech_speed
        if script_mode and script_mode != "normal":
            regen_kwargs["script_mode"] = script_mode
        if game_genre:
            regen_kwargs["game_genre"] = game_genre
        return generate_v2_script(
            game_name=game_name,
            target_duration=target_duration,
            tone="viral_hype",
            briefing_text="",
            groq_api_key=clean_groq_key,
            gemini_api_key=clean_gemini_key,
            **regen_kwargs,
        )

    if opening_rule:
        instruction += opening_rule

    last_error = None
    script_text = ""

    models_to_try = [
        "qwen/qwen3.8-27b",
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "gemma2-9b-it",
        "mixtral-8x7b-32768",
    ]

    if clean_groq_key:
        headers = {
            "Authorization": f"Bearer {clean_groq_key}",
            "Content-Type": "application/json",
        }
        for model_name in models_to_try:
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": "You are an elite viral gaming scriptwriter for TikTok, Reels, and YouTube Shorts. Use authentic social media gaming vocabulary with simple, punchy, high-impact word choices that never sound childish or dumb. Return ONLY the modified voiceover script in natural English with NO timestamps, NO line numbers, and NO brackets."},
                    {"role": "user", "content": instruction}
                ],
                "temperature": 0.7,
            }
            try:
                resp = _api_usage.usage_post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=20)
                if resp.status_code == 200:
                    script_text = resp.json()["choices"][0]["message"]["content"].strip()
                    break
                elif resp.status_code == 401:
                    last_error = "API Key de Groq no válida (401)"
                    break
                else:
                    last_error = f"Groq {resp.status_code}: {resp.text}"
            except Exception as e:
                last_error = str(e)
    if not script_text and clean_gemini_key:
        gemini_models = [
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
        for g_model in gemini_models:
            try:
                from google import genai
                from google.genai import types
                client = genai.Client(api_key=clean_gemini_key)
                config = types.GenerateContentConfig(
                    system_instruction="You are an elite viral gaming scriptwriter for TikTok, Reels, and YouTube Shorts. Use authentic social media gaming vocabulary with simple, punchy, high-impact word choices that never sound childish or dumb. Return ONLY the modified voiceover script in natural English with NO timestamps, NO line numbers, and NO brackets.",
                    temperature=0.7,
                )
                response = _api_usage.usage_generate(client.models.generate_content, 
                    model=g_model,
                    contents=instruction,
                    config=config,
                )
                if response.text:
                    script_text = response.text.strip()
                    break
            except Exception as gem_e:
                logger.warning(f"Gemini model {g_model} failed for script modification: {gem_e}")

    if not script_text:
        raise RuntimeError(f"No se pudo modificar el guión: {last_error or 'Error desconocido'}")

    script_text = clean_v2_script_text(script_text)
    if opening_mode in (HOOK_TYPE_CUSTOM, HOOK_TYPE_DIRECT):
        # Enforce the chosen opening even if the model ignored the rule.
        script_text = apply_opening(script_text, opening_mode, clean_opening)
    word_count = len(script_text.split())
    est_duration = round(word_count / 2.3, 1)

    from script_retention import parse_and_repair_retention_script
    retention_obj = parse_and_repair_retention_script(
        raw_content=script_text,
        game_name=game_name,
        target_duration=target_duration,
    )

    return {
        "script": script_text,
        "word_count": word_count,
        "estimated_duration": est_duration,
        "target_duration": target_duration,
        "retention_data": retention_obj.to_dict(),
    }


def download_youtube_clip(url: str, start_sec: float, end_sec: float, output_path: Path, ffmpeg_bin: str = "ffmpeg") -> Path:
    """Download a specific segment of a YouTube video using yt-dlp and FFmpeg.

    Args:
        url: YouTube video URL.
        start_sec: Start time in seconds.
        end_sec: End time in seconds.
        output_path: Destination path for the cut mp4 file.
        ffmpeg_bin: Path to the FFmpeg binary.

    Returns:
        Path to the output file.
    """
    import subprocess
    import shutil
    import sys

    clean_url = str(url or "").strip()
    if not clean_url.lower().startswith(("http://", "https://")):
        raise ValueError("La URL de YouTube debe empezar por http:// o https://")
    url = clean_url

    # Ensure temp dir exists (project-root temp/, independent of the working directory)
    temp_dir = Path(__file__).resolve().parent / "temp"
    temp_dir.mkdir(exist_ok=True)
    temp_full_path = temp_dir / "yt_download_full.mp4"
    
    # Remove temp files if they exist
    if temp_full_path.exists():
        temp_full_path.unlink()
        
    logger.info(f"Downloading YouTube video: {url}")
    
    # Resolve ffmpeg directory to help yt-dlp find it for merging formats
    if Path(ffmpeg_bin).is_absolute():
        ffmpeg_dir = str(Path(ffmpeg_bin).parent)
    else:
        which_ff = shutil.which(ffmpeg_bin) or shutil.which("ffmpeg")
        ffmpeg_dir = str(Path(which_ff).parent) if which_ff else None

    node_bin = shutil.which("node")

    cmd_ytdlp = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        "--no-check-certificates",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "-o", str(temp_full_path),
    ]
    if ffmpeg_dir:
        cmd_ytdlp += ["--ffmpeg-location", ffmpeg_dir]
    if node_bin:
        cmd_ytdlp += ["--js-runtimes", f"node:{node_bin}"]
    cmd_ytdlp += ["--", url]  # "--": the URL can never be parsed as a yt-dlp option
    
    try:
        result = subprocess.run(cmd_ytdlp, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            logger.warning(f"Advanced format download failed: {result.stderr}. Trying simple stream download...")
            cmd_ytdlp_simple = [
                sys.executable, "-m", "yt_dlp",
                "-f", "bestvideo+bestaudio/best",
                "--merge-output-format", "mp4",
                "--no-check-certificates",
                "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "-o", str(temp_full_path),
            ]
            if ffmpeg_dir:
                cmd_ytdlp_simple += ["--ffmpeg-location", ffmpeg_dir]
            if node_bin:
                cmd_ytdlp_simple += ["--js-runtimes", f"node:{node_bin}"]
            cmd_ytdlp_simple += ["--", url]
            
            result_simple = subprocess.run(cmd_ytdlp_simple, capture_output=True, text=True, timeout=600)
            if result_simple.returncode != 0:
                raise RuntimeError(f"yt-dlp failed: {result_simple.stderr or result.stderr}")
            
        if not temp_full_path.exists():
            raise FileNotFoundError("yt-dlp did not generate the downloaded video file.")
            
        # If no custom end_sec is specified (start_sec <= 0 and end_sec <= 0), keep full video
        if start_sec <= 0 and end_sec <= 0:
            logger.info("Preservando vídeo de YouTube completo sin recortar.")
            if output_path.exists():
                output_path.unlink()
            shutil.move(str(temp_full_path), str(output_path))
            return output_path

        # Cut the video using FFmpeg
        logger.info(f"Cutting YouTube video segment: {start_sec}s to {end_sec}s")
        
        duration = max(0.1, end_sec - start_sec)
        cmd_cut = [
            ffmpeg_bin,
            "-y",
            "-ss", str(start_sec),
            "-t", str(duration),
            "-i", str(temp_full_path),
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "superfast",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path)
        ]
        
        result_cut = subprocess.run(cmd_cut, capture_output=True, text=True, timeout=300)
        if result_cut.returncode != 0:
            raise RuntimeError(f"FFmpeg cutting failed: {result_cut.stderr}")
            
        # Clean up full video
        if temp_full_path.exists():
            temp_full_path.unlink()
            
        return output_path
        
    except Exception as e:
        # Clean up
        if temp_full_path.exists():
            temp_full_path.unlink()
        raise RuntimeError(f"Error downloading or cutting YouTube video: {e}")


# =============================================================================
# Image Card Generation Utilities
# =============================================================================

def create_text_card(
    text: str,
    output_path: Path,
    username: str = "GAMEGIL",
    handle: str = "@officialgamegil",
    width: int = 1600,
    height: int = 600,
    bg_color: str = "#ffffff",
    text_color: str = "#0f172a",
    border_color: str = "#e2e8f0",
    border_width: int = 5,
    radius: int = 30,
    avatar_text: str = "G",
    avatar_bg: str = "#7c3aed",
    avatar_image_path: str | Path | None = None,
    font_username_family: str = "Segoe UI",
    font_handle_family: str = "Segoe UI",
    font_body_family: str = "Segoe UI",
    font_size_body: int = 42,
    avatar_radius: int = 50,
    font_size_username: int = 36,
    font_size_handle: int = 30,
    overlay_type: str = "card",  # "card" or "instagram_classic"
    highlight_first_sentence: bool = False,
    highlight_color: str = "#facc15",
) -> Path:
    """Generate a rounded-corner card image or clean Instagram Classic text overlay.

    Styles:
    - 'card': Social media post with avatar, username, handle, rounded border card.
    - 'instagram_classic': Pure, high-impact bold sans-serif text (Instagram Stories/Reels Classic) with drop-shadow/stroke, centered without card.
    """
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    import os

    def hex_to_rgba(hex_str: str, alpha: int = 255) -> tuple[int, int, int, int]:
        """Helper to convert hex string to RGBA tuple."""
        try:
            hex_str = hex_str.lstrip('#')
            if len(hex_str) == 3:
                hex_str = ''.join(c*2 for c in hex_str)
            r = int(hex_str[0:2], 16)
            g = int(hex_str[2:4], 16)
            b = int(hex_str[4:6], 16)
            return (r, g, b, alpha)
        except Exception:
            return (255, 255, 255, alpha)

    # Font Mapping
    font_map = {
        "Arial": ("arial.ttf", "arialbd.ttf"),
        "Calibri": ("calibri.ttf", "calibrib.ttf"),
        "Segoe UI": ("segoeui.ttf", "segoeuib.ttf"),
        "Impact": ("impact.ttf", "impact.ttf"),
        "Comic Sans": ("comic.ttf", "comicbd.ttf"),
        "Times New Roman": ("times.ttf", "timesbd.ttf"),
        "Georgia": ("georgia.ttf", "georgiab.ttf"),
        "Consolas": ("consolas.ttf", "consolab.ttf"),
    }

    def load_custom_font(family: str, size: int, bold: bool = False):
        # %WINDIR%/Fonts first (same files as before on Windows), then Linux/macOS
        # font folders, DejaVu Sans and finally PIL's default font.
        from fonts import load_font
        files = font_map.get(family, ("arial.ttf", "arialbd.ttf"))
        file_name = files[1] if bold else files[0]
        try:
            return load_font([file_name], size, bold=bold)
        except Exception:
            return ImageFont.load_default()

    # Convert hex inputs to RGBA
    col_bg = hex_to_rgba(bg_color)
    col_text = hex_to_rgba(text_color)
    col_handle = hex_to_rgba(text_color, alpha=140)  # secondary/semi-opaque
    col_border = hex_to_rgba(border_color)
    col_avatar_bg = hex_to_rgba(avatar_bg)

    # 1. Create a transparent canvas
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    scale_card = width / 2000.0

    # -------------------------------------------------------------------------
    # MODE 2: INSTAGRAM CLASSIC SIMPLE TEXT
    # -------------------------------------------------------------------------
    if overlay_type == "instagram_classic":
        # Instagram Classic uses a bold, clean sans-serif (San Francisco / Helvetica / Arial Bold)
        # with strong contrast outline & shadow so it pops cleanly over video gameplay.
        effective_font_size = max(24, font_size_body)
        classic_font = load_custom_font("Arial", effective_font_size, bold=True)
        max_text_width = width - max(40, int(round(200 * scale_card)))

        def get_text_width(txt, fnt):
            try:
                return draw.textlength(txt, font=fnt)
            except Exception:
                return len(txt) * (effective_font_size * 0.55)

        words = text.split()
        lines = []
        current_line = ""
        for word in words:
            test_line = current_line + " " + word if current_line else word
            if get_text_width(test_line, classic_font) < max_text_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)

        # If too many lines, scale down slightly
        if len(lines) > 4:
            scaled_size = int(effective_font_size * 0.82)
            classic_font = load_custom_font("Arial", scaled_size, bold=True)
            lines = []
            current_line = ""
            for word in words:
                test_line = current_line + " " + word if current_line else word
                if get_text_width(test_line, classic_font) < max_text_width:
                    current_line = test_line
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = word
            if current_line:
                lines.append(current_line)

        line_height = int(effective_font_size * 1.35)
        total_text_height = len(lines) * line_height
        start_y = max(20, (height - total_text_height) // 2)

        stroke_w = max(2, int(round(effective_font_size * 0.055)))
        stroke_color = (0, 0, 0, 230)
        shadow_offset = max(2, int(round(4 * scale_card)))

        for idx, line in enumerate(lines):
            try:
                line_w = draw.textlength(line, font=classic_font)
            except Exception:
                line_w = len(line) * (effective_font_size * 0.55)
            line_x = (width - line_w) // 2
            line_y = start_y + idx * line_height

            # Deep drop shadow for legibility
            draw.text(
                (line_x + shadow_offset, line_y + shadow_offset),
                line,
                fill=(0, 0, 0, 180),
                font=classic_font,
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0, 180)
            )
            # Main bold text with crisp outline (Classic Instagram style)
            draw.text(
                (line_x, line_y),
                line,
                fill=col_text,
                font=classic_font,
                stroke_width=stroke_w,
                stroke_fill=stroke_color
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, "PNG")
        return output_path

    # -------------------------------------------------------------------------
    # MODE 1: SOCIAL CARD (POST / TWEET)
    # -------------------------------------------------------------------------
    # 2. Draw card background (rounded rectangle filling the canvas)
    card_margin_x = max(2, int(round(border_width / 2)))
    card_margin_y = max(2, int(round(border_width / 2)))
    card_x0 = card_margin_x
    card_y0 = card_margin_y
    card_x1 = width - card_margin_x
    card_y1 = height - card_margin_y

    draw.rounded_rectangle(
        [card_x0, card_y0, card_x1, card_y1],
        radius=radius,
        fill=col_bg,
        outline=col_border,
        width=border_width
    )

    # 3. Load fonts dynamically
    font_name = load_custom_font(font_username_family, font_size_username, bold=True)
    font_handle = load_custom_font(font_handle_family, font_size_handle, bold=False)
    font_body = load_custom_font(font_body_family, font_size_body, bold=True)
    avatar_font = load_custom_font("Segoe UI", int(avatar_radius * 1.2), bold=True)

    # 4. Draw Profile Section
    pad = max(12, int(round(50 * scale_card)))
    avatar_x = card_x0 + pad
    avatar_y = card_y0 + pad
    avatar_r = avatar_radius
    
    avatar_drawn = False
    
    if avatar_image_path:
        avatar_img_path = Path(avatar_image_path)
        if avatar_img_path.exists():
            try:
                # Open uploaded image
                img = Image.open(avatar_img_path).convert("RGBA")
                size = (2 * avatar_r, 2 * avatar_r)
                # Crop and resize to circular dimensions
                img = ImageOps.fit(img, size, Image.Resampling.LANCZOS)
                
                # Create mask
                mask = Image.new("L", size, 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.ellipse((0, 0, size[0], size[1]), fill=255)
                
                # Paste on canvas
                canvas.paste(img, (avatar_x, avatar_y), mask)
                avatar_drawn = True
            except Exception:
                pass  # Fallback to letter avatar if error

    if not avatar_drawn:
        # Draw avatar circle
        draw.ellipse(
            [avatar_x, avatar_y, avatar_x + 2*avatar_r, avatar_y + 2*avatar_r],
            fill=col_avatar_bg
        )
        
        # Center letter inside avatar circle
        try:
            bbox = draw.textbbox((0, 0), avatar_text, font=avatar_font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        except Exception:
            text_w = len(avatar_text) * int(avatar_radius * 0.6)
            text_h = int(avatar_radius * 0.6)
            
        letter_x = avatar_x + avatar_r - text_w // 2
        letter_y = avatar_y + avatar_r - text_h // 2 - max(2, int(round(5 * scale_card)))
        draw.text(
            (letter_x, letter_y),
            avatar_text,
            fill=(255, 255, 255, 255),
            font=avatar_font
        )

    # Draw Username and Handle centered vertically next to avatar
    text_x = avatar_x + 2*avatar_r + max(10, int(round(30 * scale_card)))
    text_gap = max(4, int(round(10 * scale_card)))
    text_block_h = font_size_username + font_size_handle + text_gap
    text_y = avatar_y + avatar_r - text_block_h // 2
    
    # Draw Display Name (Matches avatar background color for visual synergy)
    draw.text(
        (text_x, text_y),
        username,
        fill=col_avatar_bg,
        font=font_name
    )
    
    # Draw Handle
    draw.text(
        (text_x, text_y + font_size_username + text_gap),
        handle,
        fill=col_handle,
        font=font_handle
    )

    # 5. Wrap and draw body text
    body_x = card_x0 + pad
    avatar_bottom = avatar_y + 2*avatar_r
    text_bottom = text_y + text_block_h
    body_y = max(avatar_bottom, text_bottom) + max(12, int(round(40 * scale_card)))
    max_body_width = (card_x1 - card_x0) - (2 * pad)
    
    # Helper to measure text width
    def get_text_width(txt, fnt):
        try:
            return draw.textlength(txt, font=fnt)
        except Exception:
            return len(txt) * 10

    words = text.split()
    lines = []
    current_line = ""
    
    for word in words:
        test_line = current_line + " " + word if current_line else word
        if get_text_width(test_line, font_body) < max_body_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)

    # If the text is too long (more than 5 lines), scale down the font size by 20%
    if len(lines) > 5:
        scaled_size = int(font_size_body * 0.8)
        font_body = load_custom_font(font_body_family, scaled_size, bold=True)
        # Re-wrap
        lines = []
        current_line = ""
        for word in words:
            test_line = current_line + " " + word if current_line else word
            if get_text_width(test_line, font_body) < max_body_width:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)

    # Optional hook emphasis changes color only, preserving the paragraph and wrapping.
    import re
    first_end = re.search(r"[.!?](?=\s|$)", text)
    highlighted_words = len(text[:first_end.end()].split()) if first_end else len(words)
    word_index = 0
    # Draw lines with proportional line height
    line_height = int(font_size_body * 1.3)
    for idx, line in enumerate(lines):
        if highlight_first_sentence:
            x = body_x
            for word in line.split():
                color = hex_to_rgba(highlight_color) if word_index < highlighted_words else col_text
                draw.text((x, body_y + idx * line_height), word, fill=color, font=font_body)
                x += get_text_width(word + " ", font_body)
                word_index += 1
        else:
            draw.text((body_x, body_y + idx * line_height), line, fill=col_text, font=font_body)

    # 6. Save the PNG card
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, "PNG")
    return output_path


# =============================================================================
# Mockup Video Preview Utility
# =============================================================================

def create_video_preview(
    card_image_path: Path,
    output_preview_path: Path,
    style: str = "vertical_split_blur",
    username: str = "GAMEGIL",
    card_y_position: int = 440,
    zoom: float = 1.0,
    video_y_position: int = 180,
    video_path: str | Path | None = None,
) -> Path:
    """Generate a 1080x1920 phone mockup preview displaying the card on a vertical video background.

    Simulates how the final video output will look in TikTok/Reels:
    - Renders black or blurred gradient backdrop (or blurred frame from actual video).
    - Draws a 16:9 gameplay box (using real extracted frame if available).
    - Overlays the text card scaled down with exact pixel accuracy.
    - Adds TikTok-style interactions (likes, comments, share) and caption texts.
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
    import os
    import subprocess
    import hashlib

    # 1. Create canvas
    width, height = 1080, 1920
    canvas = Image.new("RGBA", (width, height), (15, 23, 42, 255))  # Dark Slate 900
    draw = ImageDraw.Draw(canvas)

    # Convert vertical offset to 1080p scale from 4K baseline
    eff_vy = int(round(video_y_position * 0.5))

    # 2. Extract real frame from video if available
    real_frame_img = None
    if video_path:
        v_path = Path(video_path)
        if v_path.exists():
            try:
                # Deterministic cache name based on video file and mtime
                cache_key = hashlib.md5(f"{v_path.name}_{v_path.stat().st_mtime}".encode()).hexdigest()[:10]
                # Project-root temp/ (not the process cwd) and the configured FFmpeg.
                frame_cache_path = Path(__file__).resolve().parent / "temp" / f"frame_cache_{cache_key}.jpg"
                frame_cache_path.parent.mkdir(parents=True, exist_ok=True)
                if not frame_cache_path.exists():
                    ffmpeg_cmd = os.environ.get("FFMPEG_PATH") or "ffmpeg"
                    subprocess.run(
                        [ffmpeg_cmd, "-y", "-ss", "00:00:02", "-i", str(v_path), "-vframes", "1", "-q:v", "2", str(frame_cache_path)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
                    )
                if frame_cache_path.exists():
                    real_frame_img = Image.open(frame_cache_path).convert("RGBA")
            except Exception:
                real_frame_img = None

    # 3. Draw style-specific background
    if style == "vertical_split_blur":
        if real_frame_img:
            # Blur the actual video frame for hyper-realistic background
            bg_frame = real_frame_img.resize((width, height), Image.Resampling.BILINEAR)
            blurred_bg = bg_frame.filter(ImageFilter.GaussianBlur(80))
            # Dim slightly for contrast
            dark_dim = Image.new("RGBA", (width, height), (0, 0, 0, 70))
            blurred_bg.paste(dark_dim, (0, 0), dark_dim)
            canvas.paste(blurred_bg, (0, 0))
        else:
            # Generate abstract blurred background (glassmorphic spheres)
            bg_canvas = Image.new("RGBA", (width, height), (30, 41, 59, 255))
            bg_draw = ImageDraw.Draw(bg_canvas)
            bg_draw.ellipse([100, 300, 700, 900], fill=(124, 58, 237, 180))
            bg_draw.ellipse([400, 900, 1000, 1500], fill=(236, 72, 153, 140))
            bg_draw.ellipse([-200, 1200, 400, 1800], fill=(59, 130, 246, 160))
            blurred_bg = bg_canvas.filter(ImageFilter.GaussianBlur(100))
            canvas.paste(blurred_bg, (0, 0))
    else:
        # Default/Black background
        bg_canvas = Image.new("RGBA", (width, height), (15, 23, 42, 255))
        canvas.paste(bg_canvas, (0, 0))

    # 4. Draw Centered Gameplay Container (16:9 aspect ratio + zoom + vertical offset)
    fg_w = width
    fg_h = int(fg_w * 9 / 16)
    fg_y0 = (height - fg_h) // 2 + eff_vy
    fg_y1 = fg_y0 + fg_h

    if real_frame_img:
        # Scale with zoom
        scaled_w = int(fg_w * zoom)
        scaled_h = int(fg_h * zoom)
        gameplay_resized = real_frame_img.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
        # Crop to box
        crop_x = (scaled_w - fg_w) // 2
        crop_y = (scaled_h - fg_h) // 2
        gameplay_cropped = gameplay_resized.crop((crop_x, crop_y, crop_x + fg_w, crop_y + fg_h))
        canvas.paste(gameplay_cropped, (0, fg_y0))
        # Subtle border
        draw.rectangle([0, fg_y0, fg_w, fg_y1], outline=(255, 255, 255, 40), width=2)
    else:
        # Draw dark slate gameplay box with play button
        draw.rectangle([0, fg_y0, fg_w, fg_y1], fill=(30, 41, 59, 255), outline=(51, 65, 85, 255), width=3)
        center_x = fg_w // 2
        center_y = fg_y0 + (fg_h // 2)
        play_size = 35
        pt1 = (center_x - play_size//2 + 5, center_y - play_size//2)
        pt2 = (center_x - play_size//2 + 5, center_y + play_size//2)
        pt3 = (center_x + play_size//2 + 5, center_y)
        draw.ellipse([center_x - 50, center_y - 50, center_x + 50, center_y + 50], fill=(0, 0, 0, 120), outline=(255, 255, 255, 180), width=3)
        draw.polygon([pt1, pt2, pt3], fill=(255, 255, 255, 220))

    # 5. Load, resize, and paste the text card with 1:1 fidelity
    if card_image_path.exists():
        try:
            card_img = Image.open(card_image_path).convert("RGBA")
            orig_w, orig_h = card_img.size
            if orig_w > 1080:
                scale_card = 1080.0 / 2160.0
            else:
                scale_card = 1.0
            card_scaled_w = int(round(orig_w * scale_card))
            card_scaled_h = int(round(orig_h * scale_card))
            card_scaled = card_img.resize((card_scaled_w, card_scaled_h), Image.Resampling.LANCZOS)
            
            card_x = (width - card_scaled_w) // 2
            card_y = int(round(card_y_position * 0.5))
            
            canvas.paste(card_scaled, (card_x, card_y), card_scaled)
        except Exception:
            pass

    # 5. Draw Simulated Social Media Overlays (TikTok/Shorts style UI)
    try:
        from fonts import load_font
        font_regular = load_font(["arial.ttf"], 30)
        font_bold = load_font(["arialbd.ttf"], 30, bold=True)
        font_small = load_font(["arial.ttf"], 24)
    except Exception:
        font_regular = ImageFont.load_default()
        font_bold = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # A. Right Column Interaction Icons
    icons_x = width - 90
    
    # Draw like heart
    draw.ellipse([icons_x - 25, 1100 - 25, icons_x + 25, 1100 + 25], fill=(0, 0, 0, 100))
    draw.text((icons_x - 12, 1085), "❤️", fill=(255, 255, 255, 220), font=font_bold)
    draw.text((icons_x - 28, 1135), "124K", fill=(255, 255, 255, 220), font=font_small)

    # Draw comment bubble
    draw.ellipse([icons_x - 25, 1220 - 25, icons_x + 25, 1220 + 25], fill=(0, 0, 0, 100))
    draw.text((icons_x - 12, 1205), "💬", fill=(255, 255, 255, 220), font=font_bold)
    draw.text((icons_x - 22, 1255), "1.8K", fill=(255, 255, 255, 220), font=font_small)

    # Draw share arrow
    draw.ellipse([icons_x - 25, 1340 - 25, icons_x + 25, 1340 + 25], fill=(0, 0, 0, 100))
    draw.text((icons_x - 10, 1325), "➡️", fill=(255, 255, 255, 220), font=font_bold)
    draw.text((icons_x - 28, 1375), "Share", fill=(255, 255, 255, 220), font=font_small)

    # B. Bottom Left Profile & Caption Info
    text_y = height - 250
    draw.text((50, text_y), f"@{username.lower()}", fill=(255, 255, 255, 255), font=font_bold)
    draw.text((50, text_y + 45), "¡Este juego cooperativo de sigilo es una pasada! 🎮🔥", fill=(255, 255, 255, 220), font=font_regular)
    draw.text((50, text_y + 85), "#gaming #indiedev #gamer #shorts", fill=(59, 130, 246, 255), font=font_bold)
    draw.text((50, text_y + 130), "🎵 Sonido original - GAMEGIL", fill=(255, 255, 255, 180), font=font_small)

    # 6. Save mockup preview
    output_preview_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_preview_path, "PNG")
    return output_preview_path


# =============================================================================
# Steam Opening Hook Overlay Generator (Logo Pop-in + NEW STEAM GAME Bounce)
# =============================================================================

def _ease_out_back(x: float, c1: float = 1.70158) -> float:
    """Elastic overshoot curve for pop-in zoom and bounce animations."""
    c3 = c1 + 1
    return 1 + c3 * math.pow(x - 1, 3) + c1 * math.pow(x - 1, 2)


def generate_steam_hook_overlay_video(
    output_mov_path: Path | str,
    badge_path: Path | str | None = None,
    duration: float = 1.8,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    ffmpeg_bin: str = "ffmpeg",
    logo_scale: float = 1.0,
    logo_y_pos: int | None = None,
    text_scale: float = 1.0,
    text_y_pos: int | None = None,
    mode: str = "classic",
) -> Path:
    """Render a transparent video overlay clip with the animated Steam Badge (Pop-in Zoom)
    and optionally the 3-row stacked text 'NEW STEAM GAME' (Bounce Animation).
    If mode == 'subtitles_above', only the Steam Badge is rendered (positioned at the bottom by default).
    Allows full control over logo size/position and text size/position.
    """
    from PIL import Image, ImageDraw, ImageFont
    import time

    out_path = Path(output_mov_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Resolve Steam Badge Path
    base_dir = Path(__file__).resolve().parent
    badge_p = None
    if badge_path and Path(badge_path).exists():
        badge_p = Path(badge_path)
    else:
        for candidate in [
            base_dir / "static" / "assets" / "steam_new_badge.png",
            base_dir / "Presets" / "Steam" / "steam_new_badge.png",
            base_dir / "static" / "steam_new_badge.png",
        ]:
            if candidate.exists():
                badge_p = candidate
                break

    # If badge does not exist yet, generate it on the fly
    if not badge_p or not badge_p.exists():
        badge_p = base_dir / "static" / "assets" / "steam_new_badge.png"
        try:
            from scratch.generate_steam_badge import create_steam_new_badge
            create_steam_new_badge(badge_p, size=1024)
        except Exception:
            pass

    num_frames = max(1, int(duration * fps))

    # 2. Prepare Steam Badge Image (with logo_scale)
    scale_factor = max(0.2, min(3.0, float(logo_scale)))
    badge_base_size = int(width * 0.44 * scale_factor)  # default ~475px in 1080p, ~950px in 4K
    if badge_p and badge_p.exists():
        try:
            badge_raw = Image.open(badge_p).convert("RGBA")
            badge_resized = badge_raw.resize((badge_base_size, badge_base_size), Image.Resampling.LANCZOS)
        except Exception:
            badge_resized = Image.new("RGBA", (badge_base_size, badge_base_size), (20, 50, 100, 255))
    else:
        badge_resized = Image.new("RGBA", (badge_base_size, badge_base_size), (20, 50, 100, 255))

    # 3. Prepare Stacked 'NEW STEAM GAME' Text (only for classic mode)
    static_text_img = None
    text_w, text_h = 0, 0
    if mode != "subtitles_above":
        t_scale = max(0.2, min(3.0, float(text_scale)))
        font_size = int(width * 0.165 * t_scale)  # ~178px in 1080p, ~356px in 4K
        line_spacing = int(font_size * 0.94)

        try:
            from fonts import load_font
            hook_font = load_font(["impact.ttf", "ariblk.ttf", "arialbd.ttf"], font_size, bold=True)
        except Exception:
            hook_font = ImageFont.load_default()

        text_rows = [
            ("NEW", (255, 255, 255, 255)),
            ("STEAM", (0, 116, 228, 255)),  # Electric Steam Blue #0074E4
            ("GAME", (255, 255, 255, 255)),
        ]
        stroke_w = max(4, int(width * 0.012 * t_scale))

        temp_canvas_h = int(line_spacing * 4 + font_size + 100)
        temp_canvas = Image.new("RGBA", (width, temp_canvas_h), (0, 0, 0, 0))
        st_draw = ImageDraw.Draw(temp_canvas)

        for idx, (txt, col) in enumerate(text_rows):
            try:
                bbox = st_draw.textbbox((0, 0), txt, font=hook_font, stroke_width=stroke_w)
                tw = bbox[2] - bbox[0]
            except Exception:
                tw = len(txt) * font_size * 0.6
            tx = (width - tw) / 2
            ty = 40 + idx * line_spacing

            try:
                st_draw.text((tx, ty), txt, font=hook_font, fill=col, stroke_width=stroke_w, stroke_fill=(0, 0, 0, 255))
            except Exception:
                for ox in range(-stroke_w, stroke_w + 1, 2):
                    for oy in range(-stroke_w, stroke_w + 1, 2):
                        st_draw.text((tx + ox, ty + oy), txt, font=hook_font, fill=(0, 0, 0, 255))
                st_draw.text((tx, ty), txt, font=hook_font, fill=col)

        text_bbox = temp_canvas.getbbox()
        if text_bbox:
            static_text_img = temp_canvas.crop(text_bbox)
        else:
            static_text_img = temp_canvas

        text_w, text_h = static_text_img.size

    # Position Y coordinates (normalized to 1080x1920 base)
    if logo_y_pos is not None:
        badge_center_y = int(int(logo_y_pos) * (height / 1920.0))
    else:
        # In subtitles_above mode, default Steam logo is at the bottom (e.g. 74% / ~1420px)
        badge_center_y = int(height * 0.74) if mode == "subtitles_above" else int(height * 0.28)

    if text_y_pos is not None:
        text_center_y = int(int(text_y_pos) * (height / 1920.0))
    else:
        text_center_y = int(height * 0.57)   # default ~1094px

    badge_center_y = max(badge_base_size // 2 + 20, min(height - badge_base_size // 2 - 20, badge_center_y))
    if static_text_img:
        text_center_y = max(text_h // 2 + 20, min(height - text_h // 2 - 40, text_center_y))

    # 4. Stream Raw RGBA Frames directly into FFmpeg stdin pipe (ultra-fast)
    cmd = [
        ffmpeg_bin, "-y",
        "-f", "rawvideo",
        "-pix_fmt", "rgba",
        "-s", f"{width}x{height}",
        "-r", str(fps),
        "-i", "-",
        "-c:v", "png",
        "-pix_fmt", "rgba",
        str(out_path)
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for f in range(num_frames):
        t = f / float(fps)
        frame = Image.new("RGBA", (width, height), (0, 0, 0, 0))

        alpha_mult = 1.0
        fade_out_start = max(0.5, duration - 0.20)
        if t > fade_out_start:
            alpha_mult = max(0.0, 1.0 - (t - fade_out_start) / 0.20)

        # A. Badge Pop-in Zoom animation (0.0s -> 0.28s)
        badge_pop_dur = 0.28
        if t < badge_pop_dur:
            progress = t / badge_pop_dur
            badge_anim_scale = _ease_out_back(progress, c1=1.8)
        else:
            badge_anim_scale = 1.0

        if badge_anim_scale > 0.01 and alpha_mult > 0.01:
            bw = max(2, int(badge_base_size * badge_anim_scale))
            bh = max(2, int(badge_base_size * badge_anim_scale))
            scaled_badge = badge_resized.resize((bw, bh), Image.Resampling.BILINEAR)
            bx = (width - bw) // 2
            by = badge_center_y - bh // 2

            if alpha_mult < 0.99:
                r, g, b, a = scaled_badge.split()
                a = a.point(lambda p: int(p * alpha_mult))
                scaled_badge.putalpha(a)

            frame.paste(scaled_badge, (bx, by), scaled_badge)

        # B. Text Bounce Animation (0.05s -> 0.40s) — Only in classic mode
        if mode != "subtitles_above" and static_text_img:
            text_start_t = 0.05
            text_pop_dur = 0.35
            if t < text_start_t:
                text_anim_scale = 0.0
            elif t < text_start_t + text_pop_dur:
                progress = (t - text_start_t) / text_pop_dur
                text_anim_scale = _ease_out_back(progress, c1=2.2)
            else:
                text_anim_scale = 1.0

            if text_anim_scale > 0.01 and alpha_mult > 0.01:
                tw = max(2, int(text_w * text_anim_scale))
                th = max(2, int(text_h * text_anim_scale))
                scaled_text = static_text_img.resize((tw, th), Image.Resampling.BILINEAR)
                tx = (width - tw) // 2
                ty = text_center_y - th // 2

                if alpha_mult < 0.99:
                    r, g, b, a = scaled_text.split()
                    a = a.point(lambda p: int(p * alpha_mult))
                    scaled_text.putalpha(a)

                frame.paste(scaled_text, (tx, ty), scaled_text)

        proc.stdin.write(frame.tobytes())

    proc.stdin.close()
    proc.wait()

    return out_path


def fetch_steam_game_logo(
    game_name: str,
    output_dir: Path | str | None = None,
    session_id: str = "",
    custom_logo_path: Path | str | None = None,
    allow_text_fallback: bool = True,
) -> Optional[Path]:
    """Search Steam store for game_name, download official transparent logo.png, or use user custom logo."""
    if not game_name and not custom_logo_path:
        return None
    import requests, io
    from PIL import Image, ImageDraw, ImageFont

    base_dir = Path(__file__).resolve().parent
    cache_dir = Path(output_dir) if output_dir else (base_dir / "temp" / "v3_cache" / (session_id or "global") / "steam_logos")
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 1. Custom uploaded logo check (highest priority)
    if custom_logo_path and Path(custom_logo_path).exists() and Path(custom_logo_path).stat().st_size > 100:
        return Path(custom_logo_path).resolve()

    cand_custom = [
        cache_dir / "custom_game_logo.png",
        cache_dir.parent / "custom_game_logo.png",
        base_dir / "temp" / "v3_cache" / session_id / "custom_game_logo.png" if session_id else None,
        cache_dir / "custom_game_logo.jpg",
        cache_dir.parent / "custom_game_logo.jpg",
    ]
    for cc in cand_custom:
        if cc and cc.exists() and cc.stat().st_size > 100:
            return cc.resolve()

    if not game_name:
        return None

    safe_name = re.sub(r"[^\w\-]", "_", game_name.strip()).lower()
    cached_path = cache_dir / f"logo_{safe_name}.png"
    if cached_path.exists() and cached_path.stat().st_size > 500:
        return cached_path

    # Query Steam Store Search API
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}
    search_url = "https://store.steampowered.com/api/storesearch/"
    app_id = None
    try:
        # Search english first, then spanish
        r = requests.get(search_url, params={"term": game_name, "l": "english", "cc": "US"}, headers=headers, timeout=6)
        if r.status_code == 200:
            items = r.json().get("items", [])
            if items and items[0].get("id"):
                app_id = items[0]["id"]
        if not app_id:
            r = requests.get(search_url, params={"term": game_name, "l": "spanish", "cc": "ES"}, headers=headers, timeout=6)
            if r.status_code == 200:
                items = r.json().get("items", [])
                if items and items[0].get("id"):
                    app_id = items[0]["id"]
    except Exception as e:
        logger.warning(f"Error searching Steam store for '{game_name}': {e}")

    if app_id:
        logo_urls = [
            f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/logo.png",
            f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/logo.png",
            f"https://steamcdn-a.akamaihd.net/steam/apps/{app_id}/logo.png",
        ]
        for l_url in logo_urls:
            try:
                resp = requests.get(l_url, headers=headers, timeout=6)
                if resp.status_code == 200 and len(resp.content) > 500 and "image" in resp.headers.get("content-type", "image"):
                    with open(cached_path, "wb") as f:
                        f.write(resp.content)
                    logger.info(f"Steam logo oficial descargado para '{game_name}' (App ID {app_id}): {cached_path}")
                    return cached_path
            except Exception as e:
                logger.debug(f"Attempt failed for {l_url}: {e}")

    if not allow_text_fallback:
        return None

    # Fallback: Generate a clean stylized title card image
    try:
        fw, fh = 640, 240
        card = Image.new("RGBA", (fw, fh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(card)
        from fonts import load_font
        title_font_names = ["impact.ttf", "ariblk.ttf"]
        font_size = 72
        font = load_font(title_font_names, font_size, bold=True)
        words = game_name.upper().split()
        title_text = " ".join(words)
        bbox = draw.textbbox((0, 0), title_text, font=font, stroke_width=4)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if tw > fw - 40:
            font_size = int(font_size * ((fw - 40) / tw))
            font = load_font(title_font_names, max(24, font_size), bold=True)
            bbox = draw.textbbox((0, 0), title_text, font=font, stroke_width=4)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = (fw - tw) // 2
        ty = (fh - th) // 2
        draw.text((tx, ty), title_text, font=font, fill=(255, 255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0, 255))
        card.save(cached_path)
        return cached_path
    except Exception as e:
        logger.warning(f"No se pudo generar fallback para logo de '{game_name}': {e}")
        return None


def check_steam_game_logo_status(
    game_name: str,
    output_dir: Path | str | None = None,
    session_id: str = "",
) -> Dict[str, Any]:
    """Check if game logo is available from Steam or if custom logo was uploaded."""
    import requests, time
    base_dir = Path(__file__).resolve().parent
    cache_dir = Path(output_dir) if output_dir else (base_dir / "temp" / "v3_cache" / (session_id or "global") / "steam_logos")
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w\-]", "_", (game_name or "").strip()).lower()

    # 1. Check custom uploaded logo
    cand_custom = [
        cache_dir / "custom_game_logo.png",
        cache_dir.parent / "custom_game_logo.png",
        base_dir / "temp" / "v3_cache" / session_id / "custom_game_logo.png" if session_id else None,
        cache_dir / "custom_game_logo.jpg",
        cache_dir.parent / "custom_game_logo.jpg",
    ]
    for cc in cand_custom:
        if cc and cc.exists() and cc.stat().st_size > 100:
            url = f"/media/temp/v3_cache/{session_id}/custom_game_logo.png?t={int(time.time())}" if session_id else ""
            return {
                "status": "custom",
                "has_logo": True,
                "logo_url": url,
                "logo_path": str(cc.resolve()),
                "source": "custom",
                "message": "Logo personalizado subido por el usuario."
            }

    if not game_name:
        return {
            "status": "not_found",
            "has_logo": False,
            "logo_url": None,
            "source": "none",
            "message": "No se ha especificado el nombre del juego."
        }

    # 2. Check cached steam logo
    cached_path = cache_dir / f"logo_{safe_name}.png"
    if cached_path.exists() and cached_path.stat().st_size > 500:
        url = f"/media/temp/v3_cache/{session_id}/steam_logos/logo_{safe_name}.png?t={int(time.time())}" if session_id else ""
        return {
            "status": "steam",
            "has_logo": True,
            "logo_url": url,
            "logo_path": str(cached_path.resolve()),
            "source": "steam",
            "message": f"Logo oficial obtenido de Steam para '{game_name}'."
        }

    # 3. Query Steam Store Search API
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}
    search_url = "https://store.steampowered.com/api/storesearch/"
    app_id = None
    app_name = None
    try:
        r = requests.get(search_url, params={"term": game_name, "l": "english", "cc": "US"}, headers=headers, timeout=6)
        if r.status_code == 200:
            items = r.json().get("items", [])
            if items and items[0].get("id"):
                app_id = items[0]["id"]
                app_name = items[0].get("name")
        if not app_id:
            r = requests.get(search_url, params={"term": game_name, "l": "spanish", "cc": "ES"}, headers=headers, timeout=6)
            if r.status_code == 200:
                items = r.json().get("items", [])
                if items and items[0].get("id"):
                    app_id = items[0]["id"]
                    app_name = items[0].get("name")
    except Exception as e:
        logger.warning(f"Error checking Steam store for '{game_name}': {e}")

    if app_id:
        logo_urls = [
            f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/logo.png",
            f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/logo.png",
            f"https://steamcdn-a.akamaihd.net/steam/apps/{app_id}/logo.png",
        ]
        for l_url in logo_urls:
            try:
                resp = requests.get(l_url, headers=headers, timeout=6)
                if resp.status_code == 200 and len(resp.content) > 500 and "image" in resp.headers.get("content-type", "image"):
                    with open(cached_path, "wb") as f:
                        f.write(resp.content)
                    url = f"/media/temp/v3_cache/{session_id}/steam_logos/logo_{safe_name}.png?t={int(time.time())}" if session_id else ""
                    return {
                        "status": "steam",
                        "has_logo": True,
                        "logo_url": url,
                        "logo_path": str(cached_path.resolve()),
                        "app_id": app_id,
                        "app_name": app_name,
                        "source": "steam",
                        "message": f"Logo oficial descargado de Steam (App ID {app_id}: {app_name})."
                    }
            except Exception:
                pass

        # Game found on Steam, but logo.png returned 404
        return {
            "status": "not_found",
            "has_logo": False,
            "logo_url": None,
            "app_id": app_id,
            "app_name": app_name,
            "source": "none",
            "message": f"El juego fue encontrado en Steam ('{app_name}', App ID {app_id}), pero Steam no tiene un logo transparente publicado (logo.png devuelve 404). Sube tu propio logo a continuación."
        }

    return {
        "status": "not_found",
        "has_logo": False,
        "logo_url": None,
        "source": "none",
        "message": f"No se encontró el juego '{game_name}' en la tienda de Steam. Sube el logo manualmente a continuación."
    }


def find_game_name_spoken_time(words: List[Dict[str, Any]], game_name: str, fallback_time: float = 4.5) -> float:
    """Finds the timestamp in seconds when the game name is pronounced in the voiceover.
    Prioritizes anchor phrases ('this is [game_name]', 'called [game_name]') and consecutive
    multi-word title matches, avoiding loose substring false positives.
    """
    if not words:
        return fallback_time

    clean_name = re.sub(r"[^\w\s]", "", (game_name or "").lower()).strip()
    name_tokens = [t for t in clean_name.split() if len(t) > 1 or t.isalnum()]

    cleaned_words = []
    for w in words:
        raw_w = str(w.get("word", "")).strip()
        cw = re.sub(r"[^\w]", "", raw_w.lower())
        if cw:
            cleaned_words.append({
                "orig": raw_w,
                "clean": cw,
                "start": float(w.get("start", 0.0)),
                "end": float(w.get("end", 0.0))
            })

    if not cleaned_words:
        return fallback_time

    # Strategy 1: Anchor search ("this is <game_name>", "called <game_name>", "named <game_name>")
    if name_tokens:
        for idx in range(len(cleaned_words) - len(name_tokens) + 1):
            is_anchored = False
            if idx >= 2 and cleaned_words[idx - 2]["clean"] == "this" and cleaned_words[idx - 1]["clean"] == "is":
                is_anchored = True
            elif idx >= 1 and cleaned_words[idx - 1]["clean"] in ("called", "named", "is", "play", "its"):
                is_anchored = True

            match = all(cleaned_words[idx + k]["clean"] == name_tokens[k] for k in range(len(name_tokens)))
            if match and is_anchored:
                return round(cleaned_words[idx + len(name_tokens) - 1]["end"], 3)

        # Strategy 2: Multi-token exact sequence match (prefer latest match in the script, as payoff is near the end)
        multi_matches = []
        for idx in range(len(cleaned_words) - len(name_tokens) + 1):
            if all(cleaned_words[idx + k]["clean"] == name_tokens[k] for k in range(len(name_tokens))):
                multi_matches.append(round(cleaned_words[idx + len(name_tokens) - 1]["end"], 3))
        if multi_matches:
            return multi_matches[-1]

        # Strategy 3: Single token exact word match (avoid substring matching like 'stream' in 'livestream')
        if len(name_tokens) == 1:
            tok = name_tokens[0]
            single_matches = []
            for w in cleaned_words:
                if w["clean"] == tok or (len(tok) >= 5 and w["clean"] in (tok, tok + "s", tok + "es", tok.rstrip("s"))):
                    single_matches.append(round(w["end"], 3))
            if single_matches:
                return single_matches[-1]

    # Strategy 4: If game_name was empty or unparsed, discover reveal phrase directly from transcript
    # (look for "this is [Title...]" or "called [Title...]")
    for idx, w in enumerate(cleaned_words):
        if w["clean"] == "this" and idx + 1 < len(cleaned_words) and cleaned_words[idx + 1]["clean"] == "is":
            title_end = None
            for step in range(2, min(7, len(cleaned_words) - idx)):
                nw = cleaned_words[idx + step]
                if nw["clean"] in ("wishlist", "steam", "available", "now", "link", "download", "play"):
                    break
                title_end = nw["end"]
                if nw["orig"].endswith((".", "!", "?")):
                    break
            if title_end and title_end > fallback_time:
                return round(title_end, 3)

    return fallback_time


def generate_game_name_reveal_overlay_video(
    output_mov_path: Path | str,
    game_logo_path: Path | str,
    duration: float = 3.5,
    reveal_duration: float = 2.5,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    ffmpeg_bin: str = "ffmpeg",
    widget_y: int = 800,
    widget_scale: float = 1.0,
) -> Path:
    """Renders a transparent video overlay clip with the 'GAME NAME' reveal widget matching the reference design.
    The game logo (from Steam) is hidden behind a red rectangle with 'GAME NAME' text that progressively wipes
    from left to right to uncover the official game logo as the narrator speaks the game title.
    """
    from PIL import Image, ImageDraw, ImageFont, ImageChops

    out_path = Path(output_mov_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    num_frames = max(1, int(duration * fps))

    res_scale = height / 1920.0
    box_base_w = int(760 * widget_scale * res_scale)
    box_base_h = int(270 * widget_scale * res_scale)
    corner_r_base = int(32 * widget_scale * res_scale)
    border_w_base = int(8 * widget_scale * res_scale)
    cy = int(widget_y * res_scale)

    logo_img = None
    if game_logo_path and Path(game_logo_path).exists():
        try:
            logo_img = Image.open(game_logo_path).convert("RGBA")
        except Exception as e:
            logger.warning(f"Error cargando logo del juego {game_logo_path}: {e}")

    # Impact, then Arial Black (same files as before on Windows); portable fallback elsewhere.
    from fonts import load_font
    reveal_font_names = ["impact.ttf", "ariblk.ttf"]
    fsize_base = int(152 * widget_scale * res_scale)

    cmd = [
        ffmpeg_bin, "-y",
        "-f", "rawvideo",
        "-pix_fmt", "rgba",
        "-s", f"{width}x{height}",
        "-r", str(fps),
        "-i", "-",
        "-c:v", "png",
        "-pix_fmt", "rgba",
        str(out_path)
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    pop_in_dur = min(0.25, duration * 0.15)
    wipe_start = min(0.35, max(0.10, duration * 0.05))

    # Reveal wipe finishes at reveal_duration or duration - 1.20s, ensuring logo is 100% uncovered before widget ends
    if reveal_duration and reveal_duration > 0.5:
        wipe_end = min(duration - 0.25, max(wipe_start + 0.3, reveal_duration))
    else:
        wipe_end = max(wipe_start + 0.5, duration - 1.20)

    fade_out_start = max(wipe_end, duration - 0.25)

    for f in range(num_frames):
        t = f / float(fps)
        if t < pop_in_dur:
            anim_scale = _ease_out_back(t / max(0.01, pop_in_dur), c1=1.6)
        else:
            anim_scale = 1.0

        if t > fade_out_start:
            alpha_mult = max(0.0, 1.0 - (t - fade_out_start) / max(0.01, duration - fade_out_start))
        else:
            alpha_mult = 1.0

        if t < wipe_start:
            progress = 0.0
        elif t >= wipe_end:
            progress = 1.0
        else:
            progress = min(1.0, max(0.0, (t - wipe_start) / max(0.01, wipe_end - wipe_start)))
            if progress >= 0.98:
                progress = 1.0

        frame = Image.new("RGBA", (width, height), (0, 0, 0, 0))

        if anim_scale > 0.01 and alpha_mult > 0.01:
            box_w = max(10, int(box_base_w * anim_scale))
            box_h = max(10, int(box_base_h * anim_scale))
            corner_r = max(4, int(corner_r_base * anim_scale))
            border_w = max(2, int(border_w_base * anim_scale))

            ix0 = border_w
            iy0 = border_w
            ix1 = max(ix0 + 1, box_w - border_w)
            iy1 = max(iy0 + 1, box_h - border_w)
            ir = max(2, corner_r - border_w)

            card = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
            card_draw = ImageDraw.Draw(card)
            card_draw.rounded_rectangle([(0, 0), (box_w, box_h)], radius=corner_r, fill=(0, 0, 0, 95))

            # Draw Logo inside inner bounds
            if logo_img:
                pad_x = int(35 * widget_scale * res_scale * anim_scale)
                pad_y = int(25 * widget_scale * res_scale * anim_scale)
                max_lw = max(10, (ix1 - ix0) - pad_x * 2)
                max_lh = max(10, (iy1 - iy0) - pad_y * 2)
                lw, lh = logo_img.size
                ratio = min(max_lw / lw, max_lh / lh)
                nlw = max(2, int(lw * ratio))
                nlh = max(2, int(lh * ratio))
                res_logo = logo_img.resize((nlw, nlh), Image.Resampling.BILINEAR)
                card.paste(res_logo, (ix0 + ((ix1 - ix0) - nlw) // 2, iy0 + ((iy1 - iy0) - nlh) // 2), res_logo)

            # Red reveal block covering from x = red_left to ix1 strictly inside inner borders (never spilling over)
            if progress < 1.0 and ix1 > ix0:
                red_inner = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
                red_draw = ImageDraw.Draw(red_inner)
                red_draw.rounded_rectangle([(ix0, iy0), (ix1, iy1)], radius=ir, fill=(235, 0, 0, 255))

                fsize = max(10, int(fsize_base * anim_scale))
                fnt = load_font(reveal_font_names, fsize, bold=True)
                txt = "GAME NAME"
                st_w = max(1, int(4 * widget_scale * res_scale * anim_scale))
                bbox = red_draw.textbbox((0, 0), txt, font=fnt, stroke_width=st_w)
                # Fit the larger label inside the border, including its stroke.
                available_w = max(1, ix1 - ix0 - 2 * st_w)
                if bbox[2] - bbox[0] > available_w:
                    fsize = max(10, int(fsize * available_w / (bbox[2] - bbox[0])))
                    fnt = load_font(reveal_font_names, fsize, bold=True)
                    bbox = red_draw.textbbox((0, 0), txt, font=fnt, stroke_width=st_w)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                tx = (box_w - tw) // 2 - bbox[0]
                ty = (box_h - th) // 2 - bbox[1]

                red_draw.text((tx, ty), txt, font=fnt, fill=(255, 255, 255, 255), stroke_width=st_w, stroke_fill=(0, 0, 0, 255))

                mask = Image.new("L", (box_w, box_h), 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.rounded_rectangle([(ix0, iy0), (ix1, iy1)], radius=ir, fill=255)
                if progress > 0.0:
                    red_left = ix0 + int(progress * (ix1 - ix0))
                    mask_draw.rectangle([(0, 0), (red_left, box_h)], fill=0)

                card.paste(red_inner, (0, 0), mask)

            # Border on top of card covering outer edges
            card_draw.rounded_rectangle([(border_w//2, border_w//2), (box_w - border_w//2, box_h - border_w//2)], radius=corner_r, outline=(255, 255, 255, 255), width=border_w)

            # Clean outer corner clipping to guarantee zero pixel leakage
            outer_mask = Image.new("L", (box_w, box_h), 0)
            ImageDraw.Draw(outer_mask).rounded_rectangle([(0, 0), (box_w, box_h)], radius=corner_r, fill=255)
            r, g, b, a = card.split()
            a = ImageChops.multiply(a, outer_mask)
            if alpha_mult < 0.99:
                a = a.point(lambda p: int(p * alpha_mult))
            card.putalpha(a)

            cx = width // 2
            card_x0 = cx - box_w // 2
            card_y0 = cy - box_h // 2
            frame.paste(card, (card_x0, card_y0), card)

        proc.stdin.write(frame.tobytes())

    proc.stdin.close()
    proc.wait()
    return out_path


# =============================================================================
# Camera-Recorded Monitor Look (Physical Display & Sensor Simulation)
# =============================================================================

# Global default intensity: 0.0 (clean bypass) to 1.0 (maximum)
# Recommended testing values: 0.3 (subtle), 0.5 (balanced realistic default), 0.7 (pronounced)
CAMERA_MONITOR_EFFECT_INTENSITY: float = 0.5


def build_camera_monitor_filtergraph(
    intensity: float = 0.5,
    enable_blur: bool = True,
    enable_chroma: bool = True,
    enable_bloom: bool = True,
    enable_exposure_curve: bool = True,
    enable_glare: bool = True,
    enable_pixel_grid: bool = True,
    enable_vignette: bool = True,
    enable_flicker: bool = True,
    enable_noise: bool = True,
) -> str:
    """Build an FFmpeg complex filtergraph that simulates a physical camera recording
    a computer monitor screen.

    Applied EXCLUSIVELY to the footage shown inside the physical monitor display.
    Does NOT affect setup background footage, desktop, monitor casing, or outer composition.

    Visual components calibrated to stay realistic and subtle (never looks like a cheap CRT/glitch filter):
    1. Optical slight defocus: gentle lens dispersion softening raw raster polygons without hurting HUD readability.
    2. Subtle chromatic aberration: microscopic subpixel lens fringe at the periphery.
    3. Specular highlight bloom / halation: optical flare around high-luminance elements.
    4. Screen tone curve: lifts blacks to match illuminated display physics; rolls off highlight clipping.
    5. Screen surface reflection / room glare: soft matte ambient room sheen on the panel glass.
    6. Micro pixel structure & moiré: subtle subpixel grid and angular interference without scanlines.
    7. Lens corner optical vignette: subtle optical falloff towards panel edges.
    8. Micro-exposure temporal variation: 0.2% shutter/PWM beat simulating physical sensor capture.
    9. Camera sensor noise: fine temporal CMOS ISO noise that integrates seamlessly.
    """
    I = max(0.0, min(1.0, float(intensity)))
    if I < 0.01:
        return "[0:v]null[outv]"

    # 1. Optical slight defocus
    blur_filter = f"gblur=sigma={0.2 + 0.6 * I:.2f}:steps=1," if enable_blur else ""

    # 2. Subtle chromatic aberration
    chroma_filter = "chromashift=cbh=1:crh=-1:edge=smear," if (enable_chroma and I >= 0.25) else ""

    # 3. Tone response curve (shadow lift + highlight knee roll-off)
    if enable_exposure_curve:
        lift = 0.018 * I
        knee = 1.0 - 0.025 * I
        p1 = 0.2 + lift * 0.5
        p3 = 0.8 - (1.0 - knee) * 0.5
        curves_filter = f"curves=m='0/{lift:.3f} 0.2/{p1:.3f} 0.5/0.5 0.8/{p3:.3f} 1.0/{knee:.3f}'"
    else:
        curves_filter = ""

    # 4. Bloom opacity
    bloom_opacity = 0.08 * I

    # 5. Screen glare opacity
    glare_opacity = 0.025 * I

    # 6. Micro pixel structure & moire interference
    grid_alpha = 0.020 * I
    moire_alpha = 0.008 * I
    grid_filters = ""
    if enable_pixel_grid:
        grid_filters = f"drawgrid=w=3:h=3:t=1:c=black@{grid_alpha:.3f},drawgrid=w=19:h=23:t=1:c=black@{moire_alpha:.3f},"

    # 7. Lens vignette falloff
    vig_divisor = 35.0 - 15.0 * I
    vig_filter = f"vignette=a=PI/{vig_divisor:.1f}," if enable_vignette else ""

    # 8. Micro exposure temporal variation
    amp = 0.004 * I
    flicker_filter = f"eq=brightness='{amp:.4f}*sin(n*0.26)':eval=frame," if enable_flicker else ""

    # 9. Camera sensor noise
    c0s = 1.0 + 3.0 * I
    noise_filter = f"noise=c0s={c0s:.1f}:c0f=u+t" if enable_noise else "null"

    parts = []
    # Glare branch
    if enable_glare and glare_opacity > 0.001:
        parts.append("gradients=s=640x360:c0=0xffffff:c1=0x000000:type=radial:x0=140:y0=90:x1=500:y1=300:r=60[gsrc]")

    cf = f",{curves_filter.strip()}" if curves_filter else ""

    if enable_bloom and bloom_opacity > 0.001:
        parts.append(f"[0:v]{blur_filter}{chroma_filter}format=gbrp,split=2[main_b][bsrc]")
        parts.append("bsrc_expr: [bsrc]curves=m='0/0 0.7/0 0.9/0.25 1/1',gblur=sigma=12:steps=1[bloom]".replace("bsrc_expr: ", ""))
        parts.append(f"[main_b][bloom]blend=all_mode=addition:all_opacity={bloom_opacity:.3f},format=yuv420p{cf}[v_bloomed]")
    else:
        parts.append(f"[0:v]{blur_filter}{chroma_filter}format=yuv420p{cf}[v_bloomed]")

    if enable_glare and glare_opacity > 0.001:
        parts.append("[gsrc][v_bloomed]scale2ref[glare][v_base]")
        parts.append(f"[v_base][glare]blend=all_mode=addition:all_opacity={glare_opacity:.4f}:shortest=1,format=yuv420p[v_glared]")
        post_glare_in = "[v_glared]"
    else:
        post_glare_in = "[v_bloomed]"

    post_chain = f"{grid_filters}{vig_filter}{flicker_filter}{noise_filter}"
    parts.append(f"{post_glare_in}{post_chain}[outv]")

    return ";".join(parts)


def apply_camera_monitor_effects(
    input_path: Path,
    output_path: Path,
    intensity: float = 0.5,
    ffmpeg_bin: str = "ffmpeg",
    max_duration: float = None,
) -> Path:
    """Apply physical camera-recording-monitor simulation to an input video.

    Executes in a single pass of FFmpeg preserving audio, resolution, aspect ratio,
    and generating high-bitrate MP4 with faststart flags.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = output_path.parent / "temp_fx"
    temp_dir.mkdir(exist_ok=True)
    temp_out = temp_dir / f"fx_{uuid.uuid4().hex[:8]}.mp4"

    I = max(0.0, min(1.0, float(intensity)))

    cmd = [ffmpeg_bin, "-y", "-i", str(input_path)]
    if max_duration:
        cmd += ["-t", str(max_duration)]

    if I >= 0.01:
        fg = build_camera_monitor_filtergraph(intensity=I)
        cmd += [
            "-filter_complex", fg,
            "-map", "[outv]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "17",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(temp_out)
        ]
    else:
        # Clean bypass: normal pristine transcode
        cmd += [
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "16",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(temp_out)
        ]

    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        logger.error(f"Error applying camera monitor effect with FFmpeg: {res.stderr}")
        raise RuntimeError(f"FFmpeg camera monitor effect failed: {res.stderr[:200]}")

    if not temp_out.exists() or temp_out.stat().st_size == 0:
        raise FileNotFoundError("FFmpeg did not generate the camera monitor effect video.")

    shutil.move(str(temp_out), str(output_path))
    return output_path
