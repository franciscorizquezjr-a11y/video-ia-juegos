"""
ffmpeg_wrapper.py — FFmpeg Subprocess Wrapper

This module is the SOLE interface between the application and FFmpeg.
No other module should call FFmpeg or FFprobe commands directly.

Design Philosophy:
- Clean Python methods that internally build FFmpeg command-line strings
  and execute them via subprocess.run().
- Every FFmpeg flag is commented inline explaining its purpose.
- All operations return the Path to the output file on success.
- All operations raise FFmpegError on failure with full command and stderr.
- The wrapper verifies FFmpeg availability at initialization time.

Video Processing Strategy:
- cut_clip() re-encodes by default for frame-accurate cuts (best quality).
- crop_to_vertical() handles horizontal→vertical conversion automatically,
  detecting input dimensions and applying center-crop + scale.
- concatenate() re-encodes to ensure clip compatibility.
- export() adds -movflags +faststart for web streaming optimization.

Usage:
    ff = FFmpegWrapper(ffmpeg_path="ffmpeg", temp_dir=Path("temp"))
    ff.cut_clip(Path("source.mp4"), Path("clip1.mp4"), start=12.5, end=17.0)
    ff.concatenate([Path("clip1.mp4"), Path("clip2.mp4")], Path("edited.mp4"))
"""

import json
import logging
import os
import subprocess
from pathlib import Path

from utils import FFmpegError, format_timestamp, validate_file_exists

logger = logging.getLogger(__name__)


class FFmpegWrapper:
    """Production-quality FFmpeg subprocess wrapper.

    Provides clean Python methods for all video/audio operations needed
    by the pipeline. Internally builds FFmpeg command-line arguments and
    executes them via subprocess.

    No other module in the project should call FFmpeg directly. All video
    and audio processing goes through this wrapper.

    Attributes:
        ffmpeg_path: Path to the FFmpeg binary.
        ffprobe_path: Path to the FFprobe binary.
        temp_dir: Directory for intermediate files.
        log_level: FFmpeg log verbosity level.
    """

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        temp_dir: Path | str = "temp",
        log_level: str = "warning",
    ) -> None:
        """Initialize the FFmpeg wrapper and verify binary availability.

        Checks that both ffmpeg and ffprobe are accessible by running
        their version commands. This catches missing installations early,
        before any processing begins.

        Args:
            ffmpeg_path: Path or command name for the FFmpeg binary.
            ffprobe_path: Path or command name for the FFprobe binary.
            temp_dir: Directory for intermediate/temporary files.
            log_level: FFmpeg log level (quiet, error, warning, info, debug).

        Raises:
            FFmpegError: If ffmpeg or ffprobe cannot be found or executed.
        """
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.temp_dir = Path(temp_dir)
        self.log_level = log_level

        # Create the temp directory if it doesn't exist
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # Verify FFmpeg is accessible
        self._verify_binary(self.ffmpeg_path, "FFmpeg")
        self._verify_binary(self.ffprobe_path, "FFprobe")

        logger.info(
            f"FFmpeg wrapper initialized "
            f"(ffmpeg: {ffmpeg_path}, ffprobe: {ffprobe_path})"
        )

    def _verify_binary(self, binary_path: str, name: str) -> None:
        """Verify that an FFmpeg binary is accessible.

        Args:
            binary_path: Path or command name to check.
            name: Human-readable name for error messages.

        Raises:
            FFmpegError: If the binary is not found or not executable.
        """
        try:
            result = subprocess.run(
                [binary_path, "-version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                # Extract version from first line of output
                version_line = result.stdout.split("\n")[0]
                logger.debug(f"{name} found: {version_line}")
            else:
                raise FFmpegError(f"{name} returned non-zero exit code")
        except FileNotFoundError:
            raise FFmpegError(
                f"{name} not found at '{binary_path}'.\n"
                f"Please install FFmpeg:\n"
                f"  • Windows: winget install FFmpeg\n"
                f"  • macOS: brew install ffmpeg\n"
                f"  • Linux: sudo apt install ffmpeg\n"
                f"  • Or download from: https://ffmpeg.org/download.html\n"
                f"Then set FFMPEG_PATH in your .env file if not on PATH."
            )
        except subprocess.TimeoutExpired:
            raise FFmpegError(f"{name} timed out during version check")

    # =========================================================================
    # Core Operations
    # =========================================================================

    def cut_clip(
        self,
        input_path: Path,
        output_path: Path,
        start: float,
        end: float,
        reencode: bool = True,
    ) -> Path:
        """Extract a segment from a video file.

        Cuts a clip from start to end time. Re-encodes by default for
        frame-accurate cuts (the user chose quality over speed).

        Without re-encoding (-c copy), cuts happen at the nearest
        keyframe which can be ±0.5s imprecise. With re-encoding,
        cuts are frame-accurate but slower.

        FFmpeg command (reencode=True):
            ffmpeg -y -ss {start} -i {input} -t {duration}
                   -c:v libx264 -crf 18 -preset medium
                   -c:a aac -b:a 192k
                   -avoid_negative_ts make_zero
                   {output}

        FFmpeg command (reencode=False):
            ffmpeg -y -ss {start} -i {input} -t {duration}
                   -c copy -avoid_negative_ts make_zero
                   {output}

        Args:
            input_path: Path to the source video file.
            output_path: Where to save the extracted clip.
            start: Start time in seconds.
            end: End time in seconds.
            reencode: If True, re-encode for frame-accurate cuts.
                     If False, use stream copy for speed.

        Returns:
            Path to the output clip file.

        Raises:
            FFmpegError: If the FFmpeg command fails.
            FileNotFoundError: If the input file doesn't exist.
            ValueError: If start >= end or either is negative.
        """
        validate_file_exists(input_path, "Input video")

        if start < 0 or end < 0:
            raise ValueError(f"Timestamps cannot be negative: start={start}, end={end}")
        if start >= end:
            raise ValueError(f"Start ({start}) must be before end ({end})")

        duration = end - start
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build the FFmpeg command
        cmd = [
            self.ffmpeg_path,
            "-y",                              # Overwrite output without asking
            "-ss", format_timestamp(start),     # Seek to start (before -i for fast seek)
            "-i", str(input_path),              # Input file
            "-t", str(duration),                # Duration to extract (not -to, which is absolute)
        ]

        if reencode:
            # Re-encode for frame-accurate cuts
            cmd.extend([
                "-c:v", "libx264",             # H.264 video codec
                "-crf", "18",                  # Quality: 0=lossless, 23=default, 51=worst
                "-preset", "medium",           # Encoding speed/quality tradeoff
                "-c:a", "aac",                 # AAC audio codec
                "-b:a", "192k",                # Audio bitrate
            ])
        else:
            # Stream copy: fast but cuts only at keyframes
            cmd.extend([
                "-c", "copy",                  # Copy streams without re-encoding
            ])

        cmd.extend([
            "-avoid_negative_ts", "make_zero", # Fix timestamp issues from seeking
            str(output_path),
        ])

        self._run(cmd, f"Cut clip {start:.2f}s → {end:.2f}s ({duration:.2f}s)")
        return output_path

    def concatenate(self, clip_paths: list[Path], output_path: Path) -> Path:
        """Join multiple video clips into a single video.

        Uses the FFmpeg concat demuxer with a temporary file list.
        Re-encodes all clips to ensure compatibility (different clips
        may have slightly different encoding parameters).

        FFmpeg command:
            ffmpeg -y -f concat -safe 0 -i concat_list.txt
                   -c:v libx264 -crf 18 -preset medium
                   -c:a aac -b:a 192k
                   {output}

        Args:
            clip_paths: List of Path objects for clips to join, in order.
            output_path: Where to save the concatenated video.

        Returns:
            Path to the concatenated output file.

        Raises:
            FFmpegError: If the FFmpeg command fails.
            ValueError: If clip_paths is empty.
        """
        if not clip_paths:
            raise ValueError("Cannot concatenate empty list of clips")

        for clip_path in clip_paths:
            validate_file_exists(clip_path, f"Clip file")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Create a temporary concat list file
        # Format: file '/path/to/clip.mp4' (one per line)
        concat_list_path = self.temp_dir / "concat_list.txt"
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for clip_path in clip_paths:
                # Use forward slashes and escape single quotes for FFmpeg
                safe_path = str(clip_path.resolve()).replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

        logger.debug(
            f"Concat list created with {len(clip_paths)} clips: "
            f"{concat_list_path}"
        )

        cmd = [
            self.ffmpeg_path,
            "-y",                              # Overwrite output
            "-f", "concat",                    # Use concat demuxer
            "-safe", "0",                      # Allow absolute paths in list
            "-i", str(concat_list_path),        # Input is the concat list file
            "-c:v", "libx264",                 # Re-encode video for compatibility
            "-crf", "18",                      # Quality level
            "-preset", "medium",               # Encoding speed
            "-c:a", "aac",                     # Re-encode audio
            "-b:a", "192k",                    # Audio bitrate
            str(output_path),
        ]

        try:
            self._run(cmd, f"Concatenate {len(clip_paths)} clips")
        finally:
            # Clean up the temporary concat list file
            try:
                concat_list_path.unlink(missing_ok=True)
            except OSError:
                pass

        return output_path

    def replace_audio(
        self,
        video_path: Path,
        audio_path: Path,
        output_path: Path,
        audio_starts_at: float = 0.0,
    ) -> Path:
        """Replace a video's audio track with a different audio file.

        Takes the video stream from the first input and the audio stream
        from the second input. Video is copied without re-encoding.

        FFmpeg command:
            ffmpeg -y -i {video} -i {audio}
                   -map 0:v -map 1:a
                   -c:v copy -c:a aac -b:a 192k
                   -shortest
                   {output}

        Args:
            video_path: Path to the video file (video stream source).
            audio_path: Path to the audio file (audio stream source).
            output_path: Where to save the merged result.
            audio_starts_at: Offset in seconds to delay audio start.
                            Default 0.0 (audio starts immediately).

        Returns:
            Path to the output file with replaced audio.

        Raises:
            FFmpegError: If the FFmpeg command fails.
            FileNotFoundError: If either input file doesn't exist.
        """
        validate_file_exists(video_path, "Video file")
        validate_file_exists(audio_path, "Audio file")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.ffmpeg_path,
            "-y",                              # Overwrite output
            "-i", str(video_path),             # First input: video source
            "-i", str(audio_path),             # Second input: audio source
            "-map", "0:v",                     # Take video stream from first input
            "-map", "1:a",                     # Take audio stream from second input
            "-c:v", "copy",                    # Don't re-encode video (fast)
            "-c:a", "aac",                     # Re-encode audio to AAC
            "-b:a", "192k",                    # Audio bitrate
            "-shortest",                       # End when shorter stream ends
            str(output_path),
        ]

        self._run(cmd, "Replace audio track")
        return output_path

    def add_music(
        self,
        video_path: Path,
        music_path: Path,
        output_path: Path,
        music_volume: float = 0.15,
    ) -> Path:
        """Mix background music with existing audio in a video.

        Preserves the original audio at full volume and mixes in the
        music track at the specified volume level.

        FFmpeg command:
            ffmpeg -y -i {video} -i {music}
                   -filter_complex "[1:a]volume={vol}[music];
                                    [0:a][music]amix=inputs=2:duration=first[out]"
                   -map 0:v -map "[out]"
                   -c:v copy -c:a aac -b:a 192k
                   {output}

        Args:
            video_path: Path to the video with existing audio.
            music_path: Path to the background music file.
            output_path: Where to save the result.
            music_volume: Volume level for the music (0.0 to 1.0).
                         Default 0.15 (15% volume — subtle background).

        Returns:
            Path to the output file with mixed audio.

        Raises:
            FFmpegError: If the FFmpeg command fails.
        """
        validate_file_exists(video_path, "Video file")
        validate_file_exists(music_path, "Music file")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build the audio filter graph:
        # 1. Reduce music volume
        # 2. Mix original audio + quiet music
        # 3. Use duration of first input (the video) to stop when video ends
        filter_complex = (
            f"[1:a]volume={music_volume}[music];"
            f"[0:a][music]amix=inputs=2:duration=first[out]"
        )

        cmd = [
            self.ffmpeg_path,
            "-y",                              # Overwrite output
            "-i", str(video_path),             # First input: video with audio
            "-i", str(music_path),             # Second input: music track
            "-filter_complex", filter_complex, # Audio mixing filter graph
            "-map", "0:v",                     # Take video from first input
            "-map", "[out]",                   # Take mixed audio from filter
            "-c:v", "copy",                    # Don't re-encode video
            "-c:a", "aac",                     # Encode mixed audio as AAC
            "-b:a", "192k",                    # Audio bitrate
            str(output_path),
        ]

        self._run(cmd, "Mix background music")
        return output_path

    # =========================================================================
    # Transform Operations
    # =========================================================================

    def resize(
        self,
        input_path: Path,
        output_path: Path,
        width: int,
        height: int,
    ) -> Path:
        """Scale a video to exact dimensions.

        Uses the scale filter with -2 alignment to ensure dimensions
        are divisible by 2 (required by most codecs).

        Args:
            input_path: Path to the input video.
            output_path: Where to save the resized video.
            width: Target width in pixels (-1 to auto-calculate from height).
            height: Target height in pixels (-1 to auto-calculate from width).

        Returns:
            Path to the resized output file.

        Raises:
            FFmpegError: If the FFmpeg command fails.
        """
        validate_file_exists(input_path, "Input video")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Use -2 instead of exact value to ensure divisibility by 2
        # This prevents codec errors from odd dimensions
        scale_filter = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i", str(input_path),
            "-vf", scale_filter,               # Video filter: scale with padding
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "medium",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path),
        ]

        self._run(cmd, f"Resize to {width}x{height}")
        return output_path

    def crop_to_vertical(
        self,
        input_path: Path,
        output_path: Path,
        target_width: int = 1080,
        target_height: int = 1920,
    ) -> Path:
        """Auto-crop a video to vertical (9:16) aspect ratio.

        Detects the input video's dimensions and:
        - If horizontal (wider than tall): center-crops to 9:16, then scales
        - If already vertical: just scales to target dimensions
        - If square: crops to 9:16 from center

        This is a key feature for converting gameplay footage (typically
        16:9 horizontal) into vertical shorts for TikTok/Reels/Shorts.

        FFmpeg filter (for horizontal input):
            crop=ih*9/16:ih,scale=1080:1920

        Args:
            input_path: Path to the input video.
            output_path: Where to save the cropped/scaled video.
            target_width: Target width (default 1080 for vertical).
            target_height: Target height (default 1920 for vertical).

        Returns:
            Path to the vertical output file.

        Raises:
            FFmpegError: If the FFmpeg command fails.
        """
        validate_file_exists(input_path, "Input video")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Probe the input video to get its dimensions
        info = self.get_video_info(input_path)
        src_width = info.get("width", 1920)
        src_height = info.get("height", 1080)
        is_horizontal = src_width > src_height

        logger.info(
            f"Input video: {src_width}x{src_height} "
            f"({'horizontal' if is_horizontal else 'vertical/square'})"
        )

        if is_horizontal:
            # Horizontal video → center-crop to 9:16 ratio, then scale
            # crop=ih*9/16:ih crops the center horizontally
            # The crop width = height * 9/16 (keeping full height, narrowing width)
            filter_chain = (
                f"crop=ih*9/16:ih,"            # Center-crop to 9:16 ratio
                f"scale={target_width}:{target_height}"  # Scale to target size
            )
        else:
            # Already vertical or square → just scale to target
            filter_chain = f"scale={target_width}:{target_height}"

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i", str(input_path),
            "-vf", filter_chain,               # Video filter chain
            "-c:v", "libx264",                 # Re-encode (required after crop)
            "-crf", "18",
            "-preset", "medium",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path),
        ]

        self._run(
            cmd,
            f"Crop to vertical {target_width}x{target_height}"
        )
        return output_path

    def compose_vertical_with_black_bg(
        self,
        input_path: Path,
        card_path: Path,
        output_path: Path,
        target_width: int = 2160,
        target_height: int = 3840,
        card_y: int = 250,
        zoom: float = 1.0,
        video_y_position: int = 0,
    ) -> Path:
        """Compose a vertical video with black background and a card overlay at the top.

        Centers the 16:9 input video vertically and overlays the PNG text card
        at the top.
        """
        validate_file_exists(input_path, "Input video")
        validate_file_exists(card_path, "Card image")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        fg_height = int(target_width * 9 / 16)
        if fg_height % 2 != 0:
            fg_height += 1

        scaled_w = int(target_width * zoom)
        scaled_h = int(fg_height * zoom)
        if scaled_w % 2 != 0:
            scaled_w += 1
        if scaled_h % 2 != 0:
            scaled_h += 1

        filter_graph = (
            f"[0:v]scale={scaled_w}:{scaled_h},crop={target_width}:{scaled_h},"
            f"pad={target_width}:{target_height}:0:({target_height}-{scaled_h})/2+({video_y_position}):black[tmp];"
            f"[tmp][1:v]overlay=(main_w-overlay_w)/2:{card_y}[outv]"
        )

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-threads", "0",
            "-i", str(input_path),
            "-i", str(card_path),
            "-filter_complex", filter_graph,
            "-map", "[outv]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path),
        ]

        self._run(cmd, f"Compose vertical (black bg) {target_width}x{target_height}", timeout=1200)
        return output_path

    def compose_vertical_with_blurred_bg(
        self,
        input_path: Path,
        card_path: Path,
        output_path: Path,
        target_width: int = 2160,
        target_height: int = 3840,
        card_y: int = 250,
        zoom: float = 1.0,
        video_y_position: int = 0,
    ) -> Path:
        """Compose a vertical video with a blurred video background and a card overlay.

        Takes the 16:9 input video, scales and blurs it to cover the 9:16 background,
        overlays the original 16:9 video in the center, and overlays the text card
        at the top.
        """
        validate_file_exists(input_path, "Input video")
        validate_file_exists(card_path, "Card image")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        fg_height = int(target_width * 9 / 16)
        if fg_height % 2 != 0:
            fg_height += 1

        scaled_w = int(target_width * zoom)
        scaled_h = int(fg_height * zoom)
        if scaled_w % 2 != 0:
            scaled_w += 1
        if scaled_h % 2 != 0:
            scaled_h += 1

        filter_graph = (
            f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
            f"crop={target_width}:{target_height},"
            f"boxblur=20:10[bg];"
            f"[0:v]scale={scaled_w}:{scaled_h},crop={target_width}:{scaled_h}[fg];"
            f"[bg][fg]overlay=0:({target_height}-{scaled_h})/2+({video_y_position})[tmp];"
            f"[tmp][1:v]overlay=(main_w-overlay_w)/2:{card_y}[outv]"
        )

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-threads", "0",
            "-i", str(input_path),
            "-i", str(card_path),
            "-filter_complex", filter_graph,
            "-map", "[outv]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path),
        ]

        self._run(cmd, f"Compose vertical (blurred bg) {target_width}x{target_height}", timeout=1200)
        return output_path

    def create_base_vertical_split_blur(
        self,
        input_path: Path,
        output_path: Path,
        target_width: int = 2160,
        target_height: int = 3840,
        zoom: float = 1.0,
        video_y_position: int = 0,
        blur_radius: int = 20,
        blur_power: int = 10,
        vcodec: str = "libx264",
    ) -> Path:
        """Create a vertical split layout with a blurred background, but without overlaying any card."""
        validate_file_exists(input_path, "Input video")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        fg_height = int(target_width * 9 / 16)
        if fg_height % 2 != 0:
            fg_height += 1

        scaled_w = int(target_width * zoom)
        scaled_h = int(fg_height * zoom)
        if scaled_w % 2 != 0:
            scaled_w += 1
        if scaled_h % 2 != 0:
            scaled_h += 1

        filter_graph = (
            f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
            f"crop={target_width}:{target_height},"
            f"boxblur={blur_radius}:{blur_power}[bg];"
            f"[0:v]scale={scaled_w}:{scaled_h},crop={target_width}:{scaled_h}[fg];"
            f"[bg][fg]overlay=0:({target_height}-{scaled_h})/2+({video_y_position})[outv]"
        )

        codec_args = []
        if vcodec == "h264_nvenc":
            codec_args = ["-c:v", "h264_nvenc", "-cq:v", "18", "-preset", "p4"]
        else:
            codec_args = ["-c:v", "libx264", "-crf", "18", "-preset", "superfast"]

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-threads", "0",
            "-i", str(input_path),
            "-filter_complex", filter_graph,
            "-map", "[outv]",
            "-map", "0:a?",
            *codec_args,
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path),
        ]

        self._run(cmd, f"Base vertical split blur {target_width}x{target_height} ({vcodec}, blur {blur_radius}:{blur_power})", timeout=1200)
        return output_path

    def create_base_vertical_split_black(
        self,
        input_path: Path,
        output_path: Path,
        target_width: int = 2160,
        target_height: int = 3840,
        zoom: float = 1.0,
        video_y_position: int = 0,
    ) -> Path:
        """Create a vertical split layout with a black background, but without overlaying any card."""
        validate_file_exists(input_path, "Input video")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        fg_height = int(target_width * 9 / 16)
        if fg_height % 2 != 0:
            fg_height += 1

        scaled_w = int(target_width * zoom)
        scaled_h = int(fg_height * zoom)
        if scaled_w % 2 != 0:
            scaled_w += 1
        if scaled_h % 2 != 0:
            scaled_h += 1

        filter_graph = (
            f"[0:v]scale={scaled_w}:{scaled_h},crop={target_width}:{scaled_h},"
            f"pad={target_width}:{target_height}:0:({target_height}-{scaled_h})/2+({video_y_position}):black[outv]"
        )

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-threads", "0",
            "-i", str(input_path),
            "-filter_complex", filter_graph,
            "-map", "[outv]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "superfast",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path),
        ]

        self._run(cmd, f"Base vertical split black {target_width}x{target_height}", timeout=1200)
        return output_path

    def overlay_card_on_video(
        self,
        input_video_path: Path,
        card_image_path: Path,
        output_path: Path,
        card_y: int = 250,
    ) -> Path:
        """Overlay a static PNG card image on top of an existing vertical video."""
        validate_file_exists(input_video_path, "Input video")
        validate_file_exists(card_image_path, "Card image")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        filter_graph = f"[0:v][1:v]overlay=(main_w-overlay_w)/2:{card_y}[outv]"

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-threads", "0",
            "-i", str(input_video_path),
            "-i", str(card_image_path),
            "-filter_complex", filter_graph,
            "-map", "[outv]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "superfast",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path),
        ]

        self._run(cmd, f"Overlay card on vertical video {input_video_path.name}", timeout=600)
        return output_path

    def vertical(self, input_path: Path, output_path: Path) -> Path:
        """Shortcut to convert video to vertical 9:16 (1080x1920).

        Convenience method that calls crop_to_vertical with default
        dimensions for social media vertical format.

        Args:
            input_path: Path to the input video.
            output_path: Where to save the vertical video.

        Returns:
            Path to the vertical output file.
        """
        return self.crop_to_vertical(input_path, output_path, 1080, 1920)

    def horizontal(self, input_path: Path, output_path: Path) -> Path:
        """Convert video to horizontal 16:9 (1920x1080).

        For cases where a horizontal output is needed instead of vertical.

        Args:
            input_path: Path to the input video.
            output_path: Where to save the horizontal video.

        Returns:
            Path to the horizontal output file.
        """
        return self.resize(input_path, output_path, 1920, 1080)

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def get_duration(self, file_path: Path) -> float:
        """Get the duration of a media file in seconds.

        Uses ffprobe to read the container's duration metadata.
        Works for both audio and video files.

        FFprobe command:
            ffprobe -v quiet -show_entries format=duration -of csv=p=0 {file}

        Args:
            file_path: Path to the audio or video file.

        Returns:
            Duration in seconds as a float.

        Raises:
            FFmpegError: If ffprobe fails or returns invalid data.
            FileNotFoundError: If the file doesn't exist.
        """
        validate_file_exists(file_path, "Media file")

        cmd = [
            self.ffprobe_path,
            "-v", "quiet",                     # Suppress all output except data
            "-show_entries", "format=duration", # Request only duration field
            "-of", "csv=p=0",                  # Output as plain number (no headers)
            str(file_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                raise FFmpegError(
                    f"FFprobe failed for {file_path.name}: {result.stderr}"
                )

            duration = float(result.stdout.strip())
            logger.info(f"Duration of {file_path.name}: {duration:.3f}s")
            return duration

        except ValueError as e:
            raise FFmpegError(
                f"Could not parse duration from ffprobe output: "
                f"'{result.stdout.strip()}'"
            ) from e
        except subprocess.TimeoutExpired:
            raise FFmpegError(
                f"FFprobe timed out reading {file_path.name}"
            )

    def get_video_info(self, file_path: Path) -> dict:
        """Get detailed information about a video file.

        Uses ffprobe with JSON output to extract comprehensive metadata
        including dimensions, duration, codecs, and frame rate.

        FFprobe command:
            ffprobe -v quiet -print_format json
                    -show_format -show_streams {file}

        Args:
            file_path: Path to the video file.

        Returns:
            Dictionary with keys: width, height, duration, codec,
            fps, audio_codec, sample_rate. Missing values default
            to sensible fallbacks.

        Raises:
            FFmpegError: If ffprobe fails.
        """
        validate_file_exists(file_path, "Video file")

        cmd = [
            self.ffprobe_path,
            "-v", "quiet",                     # Suppress warnings
            "-print_format", "json",           # Output as JSON
            "-show_format",                    # Include container info
            "-show_streams",                   # Include stream details
            str(file_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                raise FFmpegError(
                    f"FFprobe failed for {file_path.name}: {result.stderr}"
                )

            probe_data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise FFmpegError(
                f"Could not parse ffprobe JSON output: {e}"
            ) from e
        except subprocess.TimeoutExpired:
            raise FFmpegError(
                f"FFprobe timed out reading {file_path.name}"
            )

        # Extract info from the first video stream
        info = {
            "width": 0,
            "height": 0,
            "duration": 0.0,
            "codec": "unknown",
            "fps": 0.0,
            "audio_codec": "unknown",
            "sample_rate": 0,
        }

        for stream in probe_data.get("streams", []):
            if stream.get("codec_type") == "video":
                info["width"] = int(stream.get("width", 0))
                info["height"] = int(stream.get("height", 0))
                info["codec"] = stream.get("codec_name", "unknown")

                # Parse frame rate from "30/1" or "30000/1001" format
                r_frame_rate = stream.get("r_frame_rate", "0/1")
                try:
                    num, den = r_frame_rate.split("/")
                    info["fps"] = float(num) / float(den) if float(den) else 0.0
                except (ValueError, ZeroDivisionError):
                    info["fps"] = 0.0

            elif stream.get("codec_type") == "audio":
                info["audio_codec"] = stream.get("codec_name", "unknown")
                info["sample_rate"] = int(stream.get("sample_rate", 0))

        # Duration from format section (most reliable source)
        format_info = probe_data.get("format", {})
        try:
            info["duration"] = float(format_info.get("duration", 0.0))
        except (ValueError, TypeError):
            info["duration"] = 0.0

        logger.info(
            f"Video info: {info['width']}x{info['height']}, "
            f"{info['duration']:.2f}s, {info['fps']:.1f}fps, "
            f"codec: {info['codec']}"
        )
        return info

    def export(
        self,
        input_path: Path,
        output_path: Path,
        codec: str = "libx264",
        crf: int = 18,
        preset: str = "medium",
        audio_codec: str = "aac",
        audio_bitrate: str = "192k",
    ) -> Path:
        """Export a video with final quality settings.

        Applies production-quality encoding settings and optimizations
        for web delivery (faststart, yuv420p pixel format).

        FFmpeg command:
            ffmpeg -y -i {input}
                   -c:v libx264 -crf 18 -preset medium
                   -pix_fmt yuv420p
                   -c:a aac -b:a 192k
                   -movflags +faststart
                   {output}

        Args:
            input_path: Path to the input video.
            output_path: Where to save the exported video.
            codec: Video codec (default: libx264).
            crf: Constant Rate Factor for quality (default: 18).
            preset: Encoding speed preset (default: medium).
            audio_codec: Audio codec (default: aac).
            audio_bitrate: Audio bitrate (default: 192k).

        Returns:
            Path to the exported output file.

        Raises:
            FFmpegError: If the FFmpeg command fails.
        """
        validate_file_exists(input_path, "Input video")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i", str(input_path),
            "-c:v", codec,                     # Video codec
            "-crf", str(crf),                  # Quality level
            "-preset", preset,                 # Encoding speed/quality tradeoff
            "-pix_fmt", "yuv420p",             # Pixel format for max compatibility
            "-c:a", audio_codec,               # Audio codec
            "-b:a", audio_bitrate,             # Audio bitrate
            "-movflags", "+faststart",         # Move metadata to start for streaming
            str(output_path),
        ]

        self._run(cmd, f"Final export → {output_path.name}")
        return output_path

    # =========================================================================
    # Internal Methods
    # =========================================================================

    def _run(
        self,
        command: list[str],
        description: str,
        timeout: int = 600,
    ) -> subprocess.CompletedProcess:
        """Execute an FFmpeg command via subprocess.

        Logs the command at DEBUG level and the description at INFO level.
        On failure, logs the full stderr and raises FFmpegError with
        diagnostic information.

        Args:
            command: The complete command as a list of strings.
            description: Human-readable description of what this command does.
            timeout: Maximum execution time in seconds (default: 600 = 10min).

        Returns:
            The completed subprocess result.

        Raises:
            FFmpegError: If the command returns a non-zero exit code
                        or times out.
        """
        # Add the loglevel flag if not already present
        if "-loglevel" not in command and "-v" not in command:
            # Insert after the binary path (position 1)
            command.insert(1, "-loglevel")
            command.insert(2, self.log_level)

        # Log the command for debugging
        cmd_str = " ".join(str(c) for c in command)
        logger.debug(f"FFmpeg command: {cmd_str}")
        logger.info(f"FFmpeg: {description}")

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode != 0:
                # Log stderr for diagnostics
                stderr_preview = result.stderr[-1000:] if result.stderr else "No stderr"
                logger.error(
                    f"FFmpeg command failed (exit code {result.returncode}):\n"
                    f"  Command: {cmd_str}\n"
                    f"  Stderr: {stderr_preview}"
                )
                raise FFmpegError(
                    f"FFmpeg failed: {description}\n"
                    f"Exit code: {result.returncode}\n"
                    f"Error output: {stderr_preview}"
                )

            logger.debug(f"FFmpeg completed successfully: {description}")
            return result

        except subprocess.TimeoutExpired:
            raise FFmpegError(
                f"FFmpeg timed out after {timeout}s: {description}\n"
                f"Command: {cmd_str}"
            )
        except FileNotFoundError:
            raise FFmpegError(
                f"FFmpeg binary not found: {command[0]}\n"
                f"Ensure FFmpeg is installed and FFMPEG_PATH is set correctly."
            )

    def _build_command(self, *args: str) -> list[str]:
        """Build an FFmpeg command with standard flags.

        Prepends the FFmpeg binary path and adds standard flags
        (-y for overwrite, -loglevel for verbosity control).

        Args:
            *args: Additional command-line arguments.

        Returns:
            Complete command as a list of strings.
        """
        return [
            self.ffmpeg_path,
            "-y",                              # Always overwrite output
            "-loglevel", self.log_level,       # Control verbosity
            *args,
        ]
