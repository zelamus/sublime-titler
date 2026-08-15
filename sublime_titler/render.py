"""ffmpeg integration — binary discovery, video probing, and subtitle burn-in.

The ``subtitles=`` filter (libass) burns the generated ASS file directly into
the video.  On Windows the filter path has to be escaped in a very particular
way (backslashes and colons doubled), which is handled here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

__all__ = [
    "find_ffmpeg",
    "probe_video",
    "burn_subtitles",
    "VideoInfo",
]


@dataclass
class VideoInfo:
    width: int
    height: int
    duration: Optional[float] = None
    has_audio: bool = False


def find_ffmpeg(explicit: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Locate an ffmpeg binary and its sibling ffprobe.

    Search order:
      1. ``--ffmpeg`` / ``SUBLIME_TITLER_FFMPEG``
      2. ``<tool>/ffmpeg/bin/ffmpeg(.exe)`` — drop a bundled build here
      3. ``ffmpeg`` on PATH

    Returns ``(ffmpeg_path, ffprobe_path)``; ffprobe is optional (only needed
    for auto-resolution probing).
    """
    candidates: List[str] = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("SUBLIME_TITLER_FFMPEG")
    if env:
        candidates.append(env)

    bundled = Path(__file__).resolve().parent.parent / "ffmpeg" / "bin"
    candidates.append(str(bundled / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")))

    which = shutil.which("ffmpeg")
    if which:
        candidates.append(which)

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            probe = None
            ffprobe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
            sibling = str(Path(candidate).with_name(ffprobe_name))
            if os.path.isfile(sibling):
                probe = sibling
            else:
                probe = shutil.which("ffprobe")
            return candidate, probe

    raise RuntimeError(
        "ffmpeg not found. Install it (https://ffmpeg.org), add it to PATH, "
        "or pass --ffmpeg /path/to/ffmpeg."
    )


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe_video(ffprobe: Optional[str], video_path: str) -> VideoInfo:
    """Read width/height/duration and audio presence from a video via ffprobe."""
    if not ffprobe:
        raise RuntimeError("ffprobe not found — required to read the input video's resolution.")

    result = _run([
        ffprobe, "-v", "error",
        "-show_entries", "stream=codec_type,width,height,duration",
        "-of", "json",
        video_path,
    ])
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {video_path}: {result.stderr.strip()}")

    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    if not streams:
        raise RuntimeError(f"no video stream found in {video_path}")

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video_stream:
        raise RuntimeError(f"no video stream found in {video_path}")

    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    duration = None
    try:
        duration = float(video_stream.get("duration", 0))
    except (TypeError, ValueError):
        pass

    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    return VideoInfo(width=width, height=height, duration=duration, has_audio=has_audio)


def _escape_filter_path(path: str) -> str:
    """Escape a path for use inside an ffmpeg ``subtitles=`` filter argument.

    On Windows both backslashes and the drive colon must be doubled; on POSIX
    only the colon needs escaping.
    """
    if os.name == "nt":
        return path.replace("\\", "\\\\\\\\").replace(":", "\\\\:")
    return path.replace(":", "\\:")


def pick_video_codec(ffmpeg: str) -> Tuple[List[str], str]:
    """Choose a hardware encoder when available, else a solid libx264 default."""
    encoders = ""
    try:
        encoders = _run([ffmpeg, "-hide_banner", "-encoders"]).stdout
    except Exception:
        pass
    if "av1_nvenc" in encoders:
        return ["-c:v", "av1_nvenc", "-preset", "p4"], "av1_nvenc"
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "20"], "libx264"


LIBX264_ARGS = ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]


def burn_subtitles(
    ffmpeg: str,
    input_video: str,
    ass_path: str,
    output_path: str,
    font_path: Optional[str] = None,
    codec_args: Optional[List[str]] = None,
    extra_args: Optional[List[str]] = None,
) -> str:
    """Burn an ASS subtitle file into a video with ffmpeg.

    Uses the ``subtitles=`` (libass) filter so every ASS styling feature —
    karaoke overrides, animations, alpha fades — renders exactly as designed.
    """
    ass_abs = os.path.abspath(ass_path)
    filter_subs = f"subtitles={_escape_filter_path(ass_abs)}"

    if font_path and os.path.isfile(font_path):
        font_dir = os.path.dirname(os.path.abspath(font_path))
        filter_subs += f":fontsdir={_escape_filter_path(font_dir)}"

    # Pick the encoder if the caller did not.
    auto_picked = codec_args is None
    if auto_picked:
        codec_args, _ = pick_video_codec(ffmpeg)

    def _cmd(encoder: List[str]) -> List[str]:
        cmd: List[str] = [ffmpeg, "-y", "-i", input_video, "-vf", filter_subs]
        cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
        cmd += encoder
        cmd += ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
        if extra_args:
            cmd += extra_args
        cmd += [output_path]
        return cmd

    result = subprocess.run(_cmd(codec_args), capture_output=True, text=True)
    if result.returncode != 0 and auto_picked and codec_args != LIBX264_ARGS:
        codec_args = LIBX264_ARGS
        print("  av1_nvenc unavailable on this ffmpeg build — retrying with libx264")
        result = subprocess.run(_cmd(codec_args), capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n".join(
            line for line in (result.stderr or "").splitlines()
            if any(k in line for k in ("Error", "error", "Invalid", "failed", "Failed", "No such"))
        )
        raise RuntimeError(
            f"ffmpeg failed (rc={result.returncode}):\n{tail or result.stderr[-2000:]}"
        )
    return output_path


def default_font() -> Optional[str]:
    """Point at the font bundled inside the tool, if present."""
    bundled = Path(__file__).resolve().parent.parent / "fonts" / "Nunito-SemiBold.ttf"
    return str(bundled) if bundled.is_file() else None