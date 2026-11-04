"""
voice.py — ElevenLabs Text-to-Speech Module

This module is the SOLE interface between the application and the ElevenLabs
API. No other module should import or use the ElevenLabs SDK directly.

Design Decisions:
- Isolated module: authentication, generation, and file saving are all
  encapsulated here, making it easy to swap TTS providers in the future.
- Streaming save: the ElevenLabs SDK returns a byte iterator, which we
  stream directly to disk to minimize memory usage for long audio files.
- Retry with backoff: ElevenLabs rate limits are handled gracefully with
  exponential backoff, especially important on free/starter plans.
- Configurable voice: voice ID, model, and output format are all
  injected via constructor, loaded from the central config.

Usage:
    voice_gen = VoiceGenerator(api_key="...", voice_id="...")
    output_path = voice_gen.generate("Hello world", Path("output.mp3"))
"""

import api_usage as _api_usage

import logging
import time
from pathlib import Path

from utils import VoiceGenerationError

logger = logging.getLogger(__name__)


class VoiceGenerator:
    """Isolated ElevenLabs text-to-speech module.

    This is the sole interface between the application and the ElevenLabs
    API. No other module should import or use the ElevenLabs SDK directly.

    Responsibilities:
    - Authentication with ElevenLabs
    - Text-to-speech generation
    - Audio file saving (MP3) via streaming
    - Retry logic for API failures
    - Comprehensive logging

    Attributes:
        voice_id: The ElevenLabs voice identifier to use.
        model_id: The TTS model (e.g., eleven_v4).
        output_format: Audio format string (e.g., mp3_44100_128).
        max_retries: Maximum retry attempts for failed API calls.
        retry_delay: Base delay between retries (seconds).
    """

    def __init__(
        self,
        api_key: str,
        voice_id: str = "JBFqnCBsd6RMkjVDRZzb",
        model_id: str = "eleven_v4",
        output_format: str = "mp3_44100_128",
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ) -> None:
        """Initialize the voice generator with ElevenLabs credentials.

        Args:
            api_key: ElevenLabs API authentication key.
            voice_id: ID of the voice to use for generation.
                     Default is "JBFqnCBsd6RMkjVDRZzb" (George voice).
            model_id: TTS model identifier. "eleven_v4" offers
                     the best quality across 29 languages.
            output_format: Audio output format. "mp3_44100_128" provides
                         good quality at reasonable file size.
            max_retries: Maximum retry attempts for failed API calls.
            retry_delay: Base delay between retries in seconds.

        Raises:
            VoiceGenerationError: If the ElevenLabs SDK cannot be imported
                                 or the client fails to initialize.
        """
        self.voice_id = voice_id
        self.model_id = model_id
        self.output_format = output_format
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        try:
            from elevenlabs.client import ElevenLabs
            self._client = ElevenLabs(api_key=api_key)
        except ImportError:
            raise VoiceGenerationError(
                "The 'elevenlabs' package is not installed. "
                "Install it with: pip install elevenlabs"
            )
        except Exception as e:
            raise VoiceGenerationError(
                f"Failed to initialize ElevenLabs client: {e}"
            )

        logger.info(
            f"Voice generator initialized (voice: {voice_id}, "
            f"model: {model_id}, format: {output_format})"
        )

    # =========================================================================
    # Public Methods
    # =========================================================================

    def generate(self, text: str, output_path: Path) -> Path:
        """Generate speech from text and save as an audio file.

        Sends the text to ElevenLabs for TTS generation, then streams
        the audio bytes directly to disk to minimize memory usage.

        The method includes retry logic for transient API failures
        (rate limits, network issues).

        Args:
            text: The voiceover script text to synthesize.
            output_path: Where to save the generated audio file.

        Returns:
            The Path to the saved audio file (same as output_path).

        Raises:
            VoiceGenerationError: If generation fails after all retry
                                 attempts, or if the output file cannot
                                 be written.
            ValueError: If text is empty.

        Example:
            >>> generator = VoiceGenerator(api_key="...")
            >>> path = generator.generate("Welcome to the game!", Path("voice.mp3"))
        """
        if not text.strip():
            raise ValueError("Voiceover text cannot be empty.")

        logger.info(
            f"Generating voiceover: {len(text)} characters → {output_path.name}"
        )

        # Ensure the output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Generate audio with retry logic
        audio_iterator = self._generate_with_retry(text)

        # Stream audio bytes directly to file (memory-efficient)
        try:
            bytes_written = 0
            with open(output_path, "wb") as f:
                for chunk in audio_iterator:
                    f.write(chunk)
                    bytes_written += len(chunk)
        except IOError as e:
            raise VoiceGenerationError(
                f"Failed to write audio file to {output_path}: {e}"
            ) from e

        file_size_kb = bytes_written / 1024
        logger.info(
            f"Voiceover saved: {output_path.name} "
            f"({file_size_kb:.1f} KB, {len(text)} chars)"
        )

        return output_path

    def list_voices(self) -> list[dict]:
        """List all available ElevenLabs voices.

        Useful for discovering voice IDs and selecting a voice
        for the pipeline configuration.

        Returns:
            List of dictionaries with keys: name, voice_id, category.
            Returns an empty list if the API call fails.

        Example:
            >>> generator = VoiceGenerator(api_key="...")
            >>> voices = generator.list_voices()
            >>> for v in voices:
            ...     print(f"{v['name']}: {v['voice_id']}")
        """
        try:
            logger.info("Fetching available voices from ElevenLabs...")
            voices_response = self._client.voices.get_all()

            voices = [
                {
                    "name": voice.name,
                    "voice_id": voice.voice_id,
                    "category": getattr(voice, "category", "unknown"),
                }
                for voice in voices_response.voices
            ]

            logger.info(f"Found {len(voices)} available voices")
            return voices

        except Exception as e:
            logger.warning(f"Failed to list voices: {e}")
            return []

    # =========================================================================
    # Internal Methods
    # =========================================================================

    def _generate_with_retry(self, text: str):
        """Core TTS generation with exponential backoff retry.

        Attempts to generate speech up to max_retries times,
        with increasing delays between attempts.

        Args:
            text: The text to synthesize.

        Returns:
            An iterator of audio bytes from ElevenLabs.

        Raises:
            VoiceGenerationError: If all retry attempts are exhausted.
        """
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                logger.debug(
                    f"TTS generation attempt {attempt + 1}/{self.max_retries}"
                )

                # Call the ElevenLabs TTS API
                # Returns an iterator of audio bytes (streaming response)
                audio_iterator = _api_usage.usage_tts(self._client.text_to_speech.convert, 
                    voice_id=self.voice_id,
                    text=text,
                    model_id=self.model_id,
                    output_format=self.output_format,
                )

                logger.debug("TTS generation successful")
                return audio_iterator

            except Exception as e:
                last_error = e
                wait_time = self.retry_delay * (2 ** attempt)

                # Check if it's a rate limit error (HTTP 429)
                status_code = getattr(e, "status_code", None)
                if status_code == 429:
                    logger.warning(
                        f"ElevenLabs rate limited (attempt {attempt + 1}/"
                        f"{self.max_retries}). "
                        f"Waiting {wait_time:.1f}s before retry..."
                    )
                else:
                    logger.warning(
                        f"ElevenLabs error (attempt {attempt + 1}/"
                        f"{self.max_retries}): {e}. "
                        f"Waiting {wait_time:.1f}s before retry..."
                    )

                time.sleep(wait_time)

        # All retries exhausted
        raise VoiceGenerationError(
            f"Voice generation failed after {self.max_retries} attempts. "
            f"Last error: {last_error}"
        )
