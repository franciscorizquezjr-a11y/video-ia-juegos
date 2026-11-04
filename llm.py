"""
llm.py — LLM Abstraction Layer (Gemini Provider via Google GenAI SDK)

This module is the SOLE interface between the application and the LLM provider.
No other module should import or use the Google GenAI SDK directly.

Architecture Design:
- The rest of the application communicates through the public methods
  defined here (analyze_video, generate_edit_breakdown).
- Switching to OpenAI or Anthropic later requires changing ONLY this file.
- The module handles: client initialization, video upload (File API),
  polling for processing status, prompt construction, JSON mode, and retry logic.

Video Upload Strategy:
- Google GenAI File API: Videos are uploaded to Google's servers. Since they
  require preprocessing, the client polls their status until the state becomes
  'ACTIVE' before triggering inference.

JSON Mode:
- All requests configure GenerateContentConfig with response_mime_type="application/json"
  to guarantee structured JSON responses.
"""

import api_usage as _api_usage

import json
import logging
import time
from pathlib import Path

from utils import LLMError, ValidationError, safe_json_parse

logger = logging.getLogger(__name__)


class LLMClient:
    """Abstraction layer for LLM interactions via the Gemini API.

    This module is the SOLE interface between the application and the LLM
    provider. No other module should import or use the google-genai SDK directly.

    The architecture is designed so that switching to another provider
    requires changing ONLY this module — the rest of the application
    communicates through the public methods defined here.

    Attributes:
        model: The Gemini model identifier used for all requests.
        max_retries: Maximum number of retry attempts for failed requests.
        retry_delay: Base delay in seconds between retries (exponential backoff).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.6-flash",
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ) -> None:
        """Initialize the LLM client with Gemini credentials.

        Args:
            api_key: Gemini API authentication key.
            model: Model identifier for completions.
                   Must support multimodal (video) input.
            max_retries: Maximum retry attempts for failed API calls.
            retry_delay: Base delay between retries (doubles each attempt).

        Raises:
            LLMError: If the SDK cannot be imported or the client
                     fails to initialize.
        """
        self.model = model
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        try:
            from google import genai
            # Initialize Client. It automatically loads API key or reads GEMINI_API_KEY from environment
            self._client = genai.Client(api_key=api_key)
        except ImportError:
            raise LLMError(
                "The 'google-genai' package is not installed. "
                "Install it with: pip install google-genai"
            )
        except Exception as e:
            raise LLMError(f"Failed to initialize Gemini client: {e}")

        logger.info(f"LLM client initialized with Gemini model: {model}")

    # =========================================================================
    # Public Methods — The API exposed to the rest of the application
    # =========================================================================

    def analyze_video(self, video_path: Path, prompt_template: str) -> dict:
        """Analyze a video and generate a creative storyboard.

        Step 1 of the pipeline. Uploads the video file using the GenAI File API,
        polls until it is processed, and sends it to the LLM with the creative
        direction prompt. The LLM watches the video and returns a structured
        storyboard with narrative arc, scenes, and a voiceover script.

        Args:
            video_path: Path to the input gameplay/trailer video (MP4).
            prompt_template: The creative analysis prompt text.

        Returns:
            Parsed JSON dictionary containing the storyboard.
            Expected keys: title, scenes, voiceover_script, hook,
            narrative_flow, emotional_progression, pacing.

        Raises:
            LLMError: If the video cannot be uploaded, the API request
                     fails after all retries, or the response cannot
                     be parsed as valid JSON.
            FileNotFoundError: If video_path does not exist.
        """
        logger.info(f"Starting video analysis: {video_path.name}")

        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        # Step 1: Upload the file with retry logic
        from google.genai import errors
        uploaded_file = None
        for attempt in range(self.max_retries):
            try:
                file_size_mb = video_path.stat().st_size / (1024 * 1024)
                logger.info(f"Uploading video file to Gemini (attempt {attempt + 1}/{self.max_retries}): {video_path.name} ({file_size_mb:.1f} MB)")
                uploaded_file = self._client.files.upload(file=video_path)
                break
            except errors.APIError as e:
                status_code = getattr(e, "code", None)
                if status_code in (429, 503, 500) and attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)
                    logger.warning(f"Gemini upload temporary error (Code {status_code}). Retrying in {wait_time:.1f}s...")
                    time.sleep(wait_time)
                else:
                    raise LLMError(f"Failed to upload video to Gemini File API: {e}") from e
            except Exception as e:
                raise LLMError(f"Unexpected upload error: {e}") from e

        # Step 2: Poll file processing state until active with retry logic
        try:
            while True:
                state_name = None
                # Get the state with retry
                for attempt in range(self.max_retries):
                    try:
                        uploaded_file = self._client.files.get(name=uploaded_file.name)
                        state_name = uploaded_file.state.name
                        break
                    except errors.APIError as e:
                        status_code = getattr(e, "code", None)
                        if status_code in (429, 503, 500) and attempt < self.max_retries - 1:
                            wait_time = self.retry_delay * (2 ** attempt)
                            logger.warning(f"Gemini polling temporary error (Code {status_code}). Retrying in {wait_time:.1f}s...")
                            time.sleep(wait_time)
                        else:
                            raise
                
                if state_name != "PROCESSING":
                    break
                
                logger.info("Video is processing on Gemini servers... waiting 5 seconds")
                time.sleep(5)
            
            if state_name == "FAILED":
                raise LLMError(f"Gemini video processing failed: {uploaded_file.error.message}")
            
            logger.info(f"Video is active and ready for inference: {uploaded_file.uri}")
        except Exception as e:
            # Clean up file reference if something fails and the upload succeeded
            if uploaded_file is not None:
                try:
                    self._client.files.delete(name=uploaded_file.name)
                except:
                    pass
            raise LLMError(f"Error during video processing/polling: {e}") from e

        # Step 3: Call the model
        try:
            contents = [uploaded_file, prompt_template]
            response_text = self._send_request(contents, json_mode=True)
        finally:
            # Always clean up the uploaded file from Google's servers to be clean
            try:
                logger.info(f"Cleaning up uploaded file from Gemini servers: {uploaded_file.name}")
                self._client.files.delete(name=uploaded_file.name)
            except Exception as e:
                logger.warning(f"Failed to clean up uploaded file {uploaded_file.name}: {e}")

        # Step 4: Parse and validate
        result = self._parse_json_response(response_text)
        self._validate_storyboard(result)

        logger.info(
            f"Video analysis complete: '{result.get('title', 'Untitled')}' "
            f"with {len(result.get('scenes', []))} scenes"
        )
        return result

    def generate_edit_breakdown(
        self,
        storyboard: dict,
        voiceover_script: str,
        audio_duration: float,
        source_duration: float,
        prompt_template: str,
    ) -> dict:
        """Generate a frame-accurate editing plan from the storyboard.

        Step 4 of the pipeline. Sends the creative storyboard, voiceover
        script, and exact audio duration back to the LLM. The LLM creates
        a precise editing breakdown with timestamps from the source video.

        Args:
            storyboard: The storyboard dict from Step 1.
            voiceover_script: The voiceover text from Step 1.
            audio_duration: Exact duration of the generated voiceover
                           audio in seconds (from Step 3).
            source_duration: Exact duration of the original source video (seconds).
            prompt_template: The edit breakdown prompt template.

        Returns:
            Parsed JSON dictionary containing the edit breakdown.
            Expected keys: clips (list), total_duration.

        Raises:
            LLMError: If the API request fails after all retries or
                     the response cannot be parsed as valid JSON.
        """
        logger.info(
            f"Generating edit breakdown (audio: {audio_duration:.2f}s, source: {source_duration:.2f}s)"
        )

        # Format the prompt template with context from previous steps
        target_duration = audio_duration + 0.5  # audio + tail padding
        prompt = prompt_template.format(
            storyboard_json=json.dumps(storyboard, indent=2),
            voiceover_script=voiceover_script,
            audio_duration=f"{audio_duration:.2f}",
            target_duration=f"{target_duration:.2f}",
            source_duration=f"{source_duration:.2f}",
            audio_tail_padding="0.5",
        )

        # Send as a text-only request
        contents = [prompt]
        response_text = self._send_request(contents, json_mode=True)

        # Parse and validate
        result = self._parse_json_response(response_text)
        self._validate_edit_breakdown(result, target_duration)

        logger.info(
            f"Edit breakdown generated: {len(result.get('clips', []))} clips"
        )
        return result

    # =========================================================================
    # Internal Methods — Request Handling & Retry Logic
    # =========================================================================

    def _send_request(
        self,
        contents: list,
        json_mode: bool = False,
    ) -> str:
        """Send a content generation request to Gemini with retry logic.

        Implements exponential backoff for transient failures (rate limits,
        network errors). Permanent errors fail immediately.

        Args:
            contents: The list of content blocks (files, texts).
            json_mode: If True, request JSON output format.

        Returns:
            The response content string from the model.

        Raises:
            LLMError: If all retry attempts are exhausted.
        """
        from google.genai import types
        from google.genai import errors

        # Build configurations
        config_args = {
            "temperature": 0.7,
        }
        if json_mode:
            config_args["response_mime_type"] = "application/json"

        config = types.GenerateContentConfig(**config_args)
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                logger.debug(
                    f"Sending Gemini request (attempt {attempt + 1}/{self.max_retries})"
                )

                response = _api_usage.usage_generate(self._client.models.generate_content, 
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                content = response.text

                if not content:
                    raise LLMError("Gemini returned an empty response.")

                logger.debug(
                    f"Gemini response received: {len(content)} characters"
                )
                return content

            except errors.APIError as e:
                # Check for rate limit (429), service overloaded (503), or internal error (500)
                status_code = getattr(e, "code", None)
                if status_code in (429, 503, 500):
                    wait_time = self.retry_delay * (2 ** attempt)
                    logger.warning(
                        f"Gemini temporary error (Code {status_code}) on attempt {attempt + 1}/{self.max_retries}. "
                        f"Waiting {wait_time:.1f}s before retry..."
                    )
                    time.sleep(wait_time)
                    last_error = e
                else:
                    logger.error(f"Gemini API error (Status Code {status_code}): {e}")
                    raise LLMError(f"Gemini API error: {e}") from e

            except Exception as e:
                # Unexpected local or connection error
                logger.error(f"Unexpected error in Gemini request: {e}")
                raise LLMError(f"Unexpected LLM error: {e}") from e

        # All retries exhausted
        raise LLMError(
            f"Gemini request failed after {self.max_retries} attempts. "
            f"Last error: {last_error}"
        )

    # =========================================================================
    # Internal Methods — Response Parsing & Validation
    # =========================================================================

    def _parse_json_response(self, text: str) -> dict:
        """Parse an LLM response as JSON.

        Uses safe_json_parse from utils to handle common LLM output
        quirks like markdown code fences around JSON.

        Args:
            text: Raw response text from the LLM.

        Returns:
            Parsed dictionary.

        Raises:
            LLMError: If the text cannot be parsed as valid JSON.
        """
        try:
            return safe_json_parse(text)
        except ValueError as e:
            raise LLMError(
                f"Failed to parse LLM response as JSON: {e}\n"
                f"Raw response (first 500 chars): {text[:500]}"
            ) from e

    def _validate_storyboard(self, data: dict) -> None:
        """Validate that a storyboard response has required fields.

        This is a lightweight check at the LLM layer. The full schema
        validation happens in schemas.py.

        Args:
            data: Parsed JSON dictionary from the LLM.

        Raises:
            LLMError: If critical fields are missing.
        """
        required = ["title", "scenes", "voiceover_script"]
        missing = [f for f in required if f not in data]

        if missing:
            raise LLMError(
                f"LLM storyboard response is missing required fields: "
                f"{', '.join(missing)}. "
                f"Keys received: {list(data.keys())}"
            )

        if not isinstance(data["scenes"], list) or len(data["scenes"]) == 0:
            raise LLMError(
                "LLM storyboard response has no scenes. "
                "The model may not have understood the prompt."
            )

        if not data["voiceover_script"].strip():
            raise LLMError(
                "LLM storyboard response has an empty voiceover script."
            )

        logger.debug("Storyboard response passed LLM-level validation")

    def _validate_edit_breakdown(self, data: dict, expected_duration: float) -> None:
        """Validate that an edit breakdown response has required fields.

        This is a lightweight check at the LLM layer. Full validation
        happens in schemas.py.

        Args:
            data: Parsed JSON dictionary from the LLM.
            expected_duration: Expected total duration for timing check.

        Raises:
            LLMError: If critical fields are missing.
        """
        if "clips" not in data:
            raise LLMError(
                "LLM edit breakdown response is missing 'clips' array. "
                f"Keys received: {list(data.keys())}"
            )

        clips = data["clips"]
        if not isinstance(clips, list) or len(clips) == 0:
            raise LLMError(
                "LLM edit breakdown response has no clips. "
                "The model may not have understood the prompt."
            )

        # Check that each clip has start and end times
        for i, clip in enumerate(clips):
            if "start" not in clip or "end" not in clip:
                raise LLMError(
                    f"Clip {i + 1} is missing 'start' or 'end' timestamp. "
                    f"Keys received: {list(clip.keys())}"
                )

        logger.debug("Edit breakdown response passed LLM-level validation")
