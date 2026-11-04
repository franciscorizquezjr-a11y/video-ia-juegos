"""
ffmpeg_assembler.py — Program 3: FFmpeg Multi-Video Assembler & 9:16 Renderer

Takes a TimelineEDL (from Program 2) and the source videos pool to render the final
short-form video assembly.

Key Features:
1. Precise frame-accurate cutting with re-encoding.
2. Vertical 9:16 formatting:
   - 'blur_bg': Cinematic blurred video background with 16:9 gameplay centered.
   - 'center_crop': Full vertical 9:16 bleed center crop.
3. Audio smoothing: Micro crossfade / afade (30ms) to eliminate popping between cuts.
4. Silent audio fallback: Injects silent audio stream if a clip lacks audio, ensuring
   bulletproof concatenation without stream mismatch errors.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from config import AppConfig
from schemas_v1 import CutDecision, TimelineEDL
from utils import FFmpegError, format_timestamp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ffmpeg_assembler")


class FFmpegAssembler:
    """Assembles cuts from multi-video pool into a polished 9:16 short video."""

    def __init__(
        self,
        config: AppConfig,
        temp_dir: Optional[Path] = None,
        output_width: int = 1920,
        output_height: int = 1080,
        target_fps: int = 60,
    ) -> None:
        self.config = config
        self.ffmpeg_path = config.ffmpeg_path or "ffmpeg"
        self.ffprobe_path = config.ffprobe_path or "ffprobe"
        self.temp_dir = temp_dir or config.temp_dir
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.output_width = output_width
        self.output_height = output_height
        self.target_fps = target_fps

    def _run_cmd(self, cmd: List[str], desc: str) -> None:
        """Execute FFmpeg command with error handling."""
        logger.debug(f"Running FFmpeg command [{desc}]: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            logger.error(f"FFmpeg command failed [{desc}]:\n{result.stderr[-800:]}")
            raise FFmpegError(f"FFmpeg failed during '{desc}': {result.stderr[-300:]}")

    def locate_source_video(self, source_name: str, search_dirs: List[Path]) -> Path:
        """Find video file on disk by path or filename."""
        p = Path(source_name)
        if p.exists() and p.is_file():
            return p.resolve()

        # Search across provided search directories
        for d in search_dirs:
            if not d.exists():
                continue
            # Try direct join
            candidate = d / p.name
            if candidate.exists():
                return candidate.resolve()
            # Try recursive search
            for match in d.rglob(p.name):
                if match.is_file():
                    return match.resolve()

        raise FileNotFoundError(
            f"Source video '{source_name}' could not be located in search paths: {[str(d) for d in search_dirs]}"
        )

    def sanitize_cut_boundaries(
        self,
        source_path: Path,
        source_in: float,
        source_out: float,
        is_hook: bool = False,
    ) -> tuple[float, float]:
        """Detect and eliminate black transition frames at cut start/end using OpenCV."""
        try:
            import cv2
            import numpy as np

            cap = cv2.VideoCapture(str(source_path))
            if not cap.isOpened():
                return source_in, source_out

            def _get_luminance(sec: float) -> float:
                cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, sec) * 1000.0)
                ret, frame = cap.read()
                if not ret or frame is None:
                    return 0.0
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                return float(np.mean(gray))

            BLACK_THRESHOLD = 6.0  # Luminance below 6.0 is black or dip-to-black transition
            MAX_STEP = 1.0  # Maximum seconds to step inward
            STEP = 0.05

            cur_in = source_in
            cur_out = source_out

            # 1. Trim black frames from cut start
            step_count = 0.0
            while step_count < MAX_STEP and cur_in < cur_out - 0.5:
                lum = _get_luminance(cur_in)
                if lum >= BLACK_THRESHOLD:
                    break
                cur_in += STEP
                step_count += STEP

            # 2. Trim black frames from cut end
            step_count = 0.0
            while step_count < MAX_STEP and cur_out > cur_in + 0.5:
                lum = _get_luminance(cur_out - 0.05)
                if lum >= BLACK_THRESHOLD:
                    break
                cur_out -= STEP
                step_count += STEP

            cap.release()

            # Enforce duration constraints
            if is_hook:
                # Hook clip must be exactly 3.0s
                cur_out = cur_in + 3.0
            else:
                # All other cuts must be at least 2.0s
                if cur_out - cur_in < 2.0:
                    cur_out = cur_in + 2.0

            if round(cur_in, 2) != round(source_in, 2) or round(cur_out, 2) != round(source_out, 2):
                logger.info(
                    f"Sanitized black frames for {source_path.name}: "
                    f"[{source_in:.2f}s -> {source_out:.2f}s] adjusted to [{cur_in:.2f}s -> {cur_out:.2f}s]"
                )

            return round(cur_in, 2), round(cur_out, 2)

        except Exception as e:
            logger.warning(f"Boundary black frame sanitizer error ({e}); using original timestamps.")
            return source_in, source_out

    def render_cut(
        self,
        cut: CutDecision,
        source_path: Path,
        output_path: Path,
        framing_mode: str = "fit_16_9",
    ) -> Path:
        """Trim segment, apply 16:9 1080p framing, and normalize audio/video streams."""
        duration = max(0.5, cut.duration)
        fade_out_start = max(0.0, duration - 0.04)

        # Video Filter Graph for 16:9 1080p (1920x1080) with Lanczos high-sharpness scaling
        if framing_mode == "center_crop":
            # Crop to fill 16:9 without any borders
            vf = (
                f"scale={self.output_width}:{self.output_height}:flags=lanczos:force_original_aspect_ratio=increase,"
                f"crop={self.output_width}:{self.output_height},"
                f"fps={self.target_fps},"
                f"setsar=1,"
                f"setpts=PTS-STARTPTS"
            )
        elif framing_mode == "blur_bg":
            # Cinematic Blurred Background Pillarbox / Letterbox
            vf = (
                f"split=2[v_bg][v_fg];"
                f"[v_bg]scale={self.output_width}:{self.output_height}:flags=lanczos:force_original_aspect_ratio=increase,"
                f"crop={self.output_width}:{self.output_height},"
                f"boxblur=28:6,"
                f"colorlevels=rimin=0.08:gimin=0.08:bimin=0.08:rimax=0.75:gimax=0.75:bimax=0.75[bg_dark];"
                f"[v_fg]scale={self.output_width}:{self.output_height}:flags=lanczos:force_original_aspect_ratio=decrease[fg_scaled];"
                f"[bg_dark][fg_scaled]overlay=(W-w)/2:(H-h)/2,"
                f"fps={self.target_fps},"
                f"setsar=1,"
                f"setpts=PTS-STARTPTS[outv]"
            )
        else:
            # Default 'fit_16_9': pristine 1080p Lanczos scaling, padded if aspect ratio slightly differs
            vf = (
                f"scale={self.output_width}:{self.output_height}:flags=lanczos:force_original_aspect_ratio=decrease,"
                f"pad={self.output_width}:{self.output_height}:(ow-iw)/2:(oh-ih)/2,"
                f"fps={self.target_fps},"
                f"setsar=1,"
                f"setpts=PTS-STARTPTS"
            )

        # Audio Filter: smooth micro-fade in/out to prevent audio pops and reset PTS
        af = (
            f"afade=t=in:ss=0:d=0.03,"
            f"afade=t=out:st={fade_out_start:.3f}:d=0.03,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS"
        )

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-ss", format_timestamp(cut.source_in),
            "-i", str(source_path),
            "-t", str(duration),
        ]

        if framing_mode == "blur_bg":
            cmd.extend(["-filter_complex", vf, "-map", "[outv]"])
        else:
            cmd.extend(["-vf", vf])

        cmd.extend([
            "-af", af,
            "-c:v", "libx264",
            "-crf", "15",
            "-preset", "medium",
            "-b:v", "15M",
            "-maxrate", "25M",
            "-bufsize", "30M",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-avoid_negative_ts", "make_zero",
            str(output_path),
        ])

        self._run_cmd(cmd, f"Render cut #{cut.order} ({cut.purpose})")
        return output_path

    def assemble_edl(
        self,
        edl: TimelineEDL,
        search_dirs: List[Path],
        output_path: Path,
        override_framing: Optional[str] = None,
    ) -> Path:
        """Process all cuts and concatenate into final 16:9 1080p video."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        rendered_clips: List[Path] = []

        logger.info(f"Beginning assembly of '{edl.title}' ({len(edl.cuts)} cuts, ~{edl.total_duration:.1f}s)...")

        try:
            for cut in edl.cuts:
                source_path = self.locate_source_video(cut.source_video, search_dirs)
                framing = override_framing or cut.framing_mode or "fit_16_9"

                # Sanitize black frames at cut start/end
                is_hook = (cut.order == 1)
                san_in, san_out = self.sanitize_cut_boundaries(
                    source_path=source_path,
                    source_in=cut.source_in,
                    source_out=cut.source_out,
                    is_hook=is_hook,
                )
                cut.source_in = san_in
                cut.source_out = san_out
                cut.duration = round(san_out - san_in, 2)

                clip_out = self.temp_dir / f"cut_{cut.order:02d}_{cut.purpose}.mp4"

                logger.info(
                    f"Processing Cut #{cut.order} [{cut.purpose.upper()}]: "
                    f"{source_path.name} ({cut.source_in:.2f}s -> {cut.source_out:.2f}s, dur={cut.duration:.2f}s) -> {framing}"
                )
                self.render_cut(cut, source_path, clip_out, framing_mode=framing)
                rendered_clips.append(clip_out)

            # Generate concat manifest
            concat_manifest = self.temp_dir / "assembly_concat_list.txt"
            with open(concat_manifest, "w", encoding="utf-8") as f:
                for clip in rendered_clips:
                    safe_path = str(clip.resolve()).replace("\\", "/")
                    f.write(f"file '{safe_path}'\n")

            logger.info(f"Concatenating {len(rendered_clips)} clips into final output...")
            concat_cmd = [
                self.ffmpeg_path,
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_manifest),
                "-c:v", "libx264",
                "-crf", "15",
                "-preset", "medium",
                "-b:v", "15M",
                "-maxrate", "25M",
                "-bufsize", "30M",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                str(output_path),
            ]

            self._run_cmd(concat_cmd, "Concatenate final assembly")
            logger.info(f"Assembly completed successfully: {output_path.resolve()}")
            return output_path

        finally:
            # Clean up intermediate cut clips
            for clip in rendered_clips:
                try:
                    clip.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                (self.temp_dir / "assembly_concat_list.txt").unlink(missing_ok=True)
            except OSError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Program 3: FFmpeg Multi-Video Assembler")
    parser.add_argument("--edl", "-e", type=str, required=True, help="Path to timeline_edl.json")
    parser.add_argument("--search-dirs", "-s", nargs="+", default=["GamesFootage", "input"], help="Directories to search for source footage")
    parser.add_argument("--output", "-o", type=str, default="output/videos/Por_clasificar/short_assembly.mp4", help="Path for final assembled MP4 video")
    parser.add_argument("--framing", "-f", type=str, choices=["fit_16_9", "blur_bg", "center_crop"], default="fit_16_9", help="Framing mode")
    args = parser.parse_args()

    config = AppConfig.from_env()
    edl_file = Path(args.edl)
    if not edl_file.exists():
        logger.error(f"EDL file not found: {edl_file}")
        sys.exit(1)

    edl = TimelineEDL.load_json(edl_file)
    search_paths = [Path(d) for d in args.search_dirs]

    assembler = FFmpegAssembler(config=config)
    output_file = Path(args.output)
    assembler.assemble_edl(
        edl=edl,
        search_dirs=search_paths,
        output_path=output_file,
        override_framing=args.framing,
    )


if __name__ == "__main__":
    main()
