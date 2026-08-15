"""Command-line interface for sublime-titler.

Burn gorgeous karaoke-style subtitles into any video.  Subtitles can come
from three explicit sources, or be generated automatically:

* *(nothing)*         — the video's own audio is transcribed with faster-whisper
  for perfectly timed word-level subtitles (the default — just run
  ``sublime-titler video.mp4``)
* ``--subtitle file.srt`` — an existing SRT file (with an optional
  ``*_timings.json`` sidecar for word-by-word highlighting)
* ``--text file.txt``    — a plain text file; each line is spread evenly over
  the video's duration and given synthetic word timings
* ``--audio file.wav``   — transcribe an external audio file instead of the
  video's audio

Whisper transcription requires the optional ``faster-whisper`` dependency.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from typing import List, Optional

from . import __version__
from .ass import SubtitleStyle, build_ass
from .core import (
    SubtitleSegment,
    chunk_segments,
    load_timings_json,
    parse_srt,
)
from .render import (
    burn_subtitles,
    default_font,
    find_ffmpeg,
    probe_video,
)


# --------------------------------------------------------------------------- #
# Subtitle source builders
# --------------------------------------------------------------------------- #

def _segments_from_srt(path: str, chunk: Optional[int]) -> List[SubtitleSegment]:
    """Load an SRT file; use the timings sidecar when present, then chunk."""
    segments = load_timings_json(path) or parse_srt(path)
    return chunk_segments(segments, max_words=chunk)


def _segments_from_text(path: str, duration: float, chunk: Optional[int]) -> List[SubtitleSegment]:
    """Spread the lines of a text file evenly across ``duration`` seconds.

    Each line becomes a segment with evenly-distributed synthetic word timings,
    so karaoke highlighting works without a Whisper transcription.
    """
    with open(path, "r", encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    if not lines:
        raise ValueError(f"text file {path!r} contains no non-empty lines")

    n = len(lines)
    seg_dur = duration / n
    segments: List[SubtitleSegment] = []
    for i, line in enumerate(lines):
        t0 = i * seg_dur
        t1 = (i + 1) * seg_dur
        words = line.split()
        if words:
            w_dur = seg_dur / len(words)
            word_timings = [
                (w, t0 + j * w_dur, t0 + (j + 1) * w_dur)
                for j, w in enumerate(words)
            ]
        else:
            word_timings = None
        segments.append(SubtitleSegment(t0, t1, line, word_timings))

    return chunk_segments(segments, max_words=chunk)


def _ensure_cuda_dlls_on_path() -> None:
    """Add NVIDIA pip-package DLL dirs (cublas/cudnn) to PATH on Windows.

    ctranslate2 needs the CUDA runtime DLLs at load time.  When installed via
    ``pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`` they live under
    ``site-packages/nvidia/*/bin``; without this bootstrap, transcription on
    GPU fails with "Library cublas64_12.dll is not found".
    """
    if os.name != "nt":
        return
    try:
        import site
        bases = [site.getusersitepackages()] + list(site.getsitepackages())
    except Exception:
        return
    dll_dirs = []
    for base in bases:
        nv_dir = os.path.join(base, "nvidia")
        if not os.path.isdir(nv_dir):
            continue
        for pkg in os.listdir(nv_dir):
            bin_dir = os.path.join(nv_dir, pkg, "bin")
            if os.path.isdir(bin_dir):
                dll_dirs.append(bin_dir)
    if dll_dirs:
        os.environ["PATH"] = os.pathsep.join(dll_dirs + [os.environ.get("PATH", "")])


def _segments_from_audio(path: str, chunk: Optional[int], model_name: str = "medium") -> List[SubtitleSegment]:
    """Transcribe an audio file with faster-whisper for word-level timings.

    GPU is detected through ctranslate2's own CUDA support (no torch needed).
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "transcription requires the optional dependency 'faster-whisper'. "
            "Install with: pip install faster-whisper"
        ) from exc

    import ctranslate2

    _ensure_cuda_dlls_on_path()
    device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    from .download import ensure_whisper_model
    model_dir, _ = ensure_whisper_model(model_name)
    model = WhisperModel(model_dir, device=device, compute_type=compute_type)
    print(f"  model ready  [device: {device} | compute_type: {compute_type}]")

    segments_gen, info = model.transcribe(path, beam_size=5, word_timestamps=True)
    duration = float(getattr(info, "duration", 0) or 0)

    segments: List[SubtitleSegment] = []
    for segment in segments_gen:
        if duration > 0:
            print(f"\r  transcribing: {segment.end:6.1f}s / {duration:.1f}s "
                  f"({min(100.0, segment.end / duration * 100):4.1f}%)", end="", flush=True)
        if getattr(segment, "words", None):
            for word in segment.words:
                word_text = word.word.strip()
                segments.append(
                    SubtitleSegment(
                        start_time=word.start,
                        end_time=word.end,
                        text=word_text,
                        word_timings=[(word_text, word.start, word.end)],
                    )
                )
        else:
            segments.append(
                SubtitleSegment(
                    start_time=segment.start,
                    end_time=segment.end,
                    text=segment.text.strip(),
                )
            )
    if duration > 0:
        print()
    print(f"  transcription complete: {len(segments)} words")
    return chunk_segments(segments, max_words=chunk)


def _extract_audio(ffmpeg: str, video_path: str, out_wav: str) -> None:
    """Extract the audio track of a video to a 16 kHz mono WAV (whisper's input)."""
    import subprocess
    result = subprocess.run(
        [ffmpeg, "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", out_wav],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not os.path.isfile(out_wav):
        raise RuntimeError(f"could not extract audio from {video_path}: {result.stderr[-500:]}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sublime-titler",
        description="Burn gorgeous karaoke-style subtitles into any video.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("input", help="Input video file (any resolution / aspect ratio)")
    p.add_argument("output", nargs="?", help="Output video path (default: <input>_subtitled.mp4)")

    src = p.add_argument_group("subtitle source (optional)")
    src.add_argument("--subtitle", metavar="SRT", help="SRT file (uses *_timings.json sidecar if present)")
    src.add_argument("--text", metavar="TXT", help="Plain text file; lines spread evenly over the video")
    src.add_argument("--audio", metavar="WAV", help="Transcribe an external audio file instead of the video's own audio")
    src.add_argument("--whisper-model", default="medium", metavar="NAME",
                     help="Whisper model for transcription (tiny/base/small/medium/large-v3). "
                          "First use downloads it from Hugging Face")
    src.add_argument("--duration", type=float, metavar="SEC",
                     help="Override the content duration used by --text (default: input video duration)")
    src.add_argument("--chunk", metavar="N", default="auto",
                     help="Words per line: 'auto' (adaptive 1-2), '0' (no chunking), or a fixed N")

    style = p.add_argument_group("styling")
    style.add_argument("--font", default=None, help="Font file (.ttf/.otf); default: bundled Nunito SemiBold")
    style.add_argument("--font-size", type=int, default=0, help="Font size in ASS units (0 = auto-scale to video)")
    style.add_argument("--color", default=None, help="Text colour as #RRGGBB (default: white)")
    style.add_argument("--highlight", default=None, help="Karaoke highlight colour #RRGGBB (default: random)")
    style.add_argument("--outline-color", default=None, help="Outline colour #RRGGBB (default: black)")
    style.add_argument("--back-color", default=None, help="Box/back colour #RRGGBB (default: semi-transparent black)")
    style.add_argument("--outline", type=int, default=0, help="Outline width (0 = auto-scale)")
    style.add_argument("--shadow", type=int, default=0, help="Shadow depth (0 = auto-scale)")
    style.add_argument("--position", default="middle", choices=[
        "bottom", "bottom-left", "bottom-right",
        "middle", "middle-left", "middle-right",
        "top", "top-left", "top-right",
    ], help="Subtitle position on screen (default 'middle' matches the Reddit-story look)")
    style.add_argument("--margin-v", type=int, default=10, help="Vertical margin in ASS units")
    style.add_argument("--no-bold", action="store_true", help="Use the regular (non-bold) font weight")
    style.add_argument("--no-pop", action="store_true", help="Disable the scale pop-in animation")
    style.add_argument("--no-karaoke", action="store_true", help="Disable word-by-word highlighting")

    title = p.add_argument_group("title fade-in (story-video intro)")
    title.add_argument("--title-duration", type=float, default=0.0, metavar="SEC",
                       help="Hide subtitles until the title is done reading (0 = show all)")
    title.add_argument("--title-words", default=None, metavar="WORDS",
                       help="The title's words, e.g. --title-words 'my crazy story'; the segment "
                            "containing its last word fades in")

    enc = p.add_argument_group("encoding")
    enc.add_argument("--ffmpeg", default=None, help="Path to ffmpeg (default: autodetect)")
    enc.add_argument("--video-codec", default=None, help="Override video encoder (e.g. libx264, av1_nvenc)")
    enc.add_argument("--keep-ass", action="store_true", help="Keep the intermediate .ass file next to the output")
    enc.add_argument("--verbose", action="store_true", help="Print the ffmpeg command")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _parse_chunk(value: str) -> Optional[int]:
    if value == "auto":
        return None
    n = int(value)
    if n < 0:
        raise argparse.ArgumentTypeError("--chunk must be 'auto', 0, or a positive integer")
    return n


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    explicit = [args.subtitle, args.text, args.audio]
    given = [s for s in explicit if s]
    if len(given) > 1:
        print("error: provide at most one of --subtitle, --text, or --audio", file=sys.stderr)
        return 2
    for src in given:
        if not os.path.isfile(src):
            print(f"error: subtitle source not found: {src}", file=sys.stderr)
            return 2
    if not os.path.isfile(args.input):
        print(f"error: input video not found: {args.input}", file=sys.stderr)
        return 2

    output = args.output or os.path.splitext(args.input)[0] + "_subtitled.mp4"

    # 1. Locate ffmpeg + probe the input video
    ffmpeg, ffprobe = find_ffmpeg(args.ffmpeg)
    info = probe_video(ffprobe, args.input)
    if info.width == 0 or info.height == 0:
        print(f"error: could not determine resolution of {args.input}", file=sys.stderr)
        return 1
    print(f"video: {args.input}  ({info.width}x{info.height}, "
          f"{info.duration:.1f}s, audio={'yes' if info.has_audio else 'no'})")

    # 2. Build subtitle segments
    chunk = _parse_chunk(args.chunk)
    duration = args.duration or info.duration or 30.0
    temp_wav = None
    if args.subtitle:
        segments = _segments_from_srt(args.subtitle, chunk)
    elif args.text:
        segments = _segments_from_text(args.text, duration, chunk)
    else:
        # Default: transcribe the video's own audio (or --audio override).
        audio_source = args.audio or args.input
        if args.audio is None and not info.has_audio:
            print("error: the input video has no audio track — provide --text, --subtitle, "
                  "or --audio with an external audio file", file=sys.stderr)
            return 1
        if args.audio is None:
            temp_wav = os.path.join(tempfile.gettempdir(), f"sublime_titler_audio_{os.getpid()}.wav")
            print(f"transcribing audio with whisper ({args.whisper_model})…")
            _extract_audio(ffmpeg, args.input, temp_wav)
            audio_source = temp_wav
        else:
            print(f"transcribing {args.audio} with whisper ({args.whisper_model})…")
        try:
            segments = _segments_from_audio(audio_source, chunk, args.whisper_model)
        finally:
            if temp_wav and os.path.exists(temp_wav):
                try:
                    os.remove(temp_wav)
                except OSError:
                    pass

    if not segments:
        print("error: no subtitle segments could be produced", file=sys.stderr)
        return 1
    print(f"subtitles: {len(segments)} lines ({'karaoke' if any(s.word_timings for s in segments) else 'plain'})")

    # 3. Style + ASS generation
    def ass_color(value: Optional[str], default: str) -> str:
        if not value:
            return default
        if value.startswith("#"):
            return hex_to_ass_color(value)
        return value

    from .ass import hex_to_ass_color

    font_path = args.font or default_font()
    style = SubtitleStyle(
        font_path=font_path,
        font_size=args.font_size,
        primary_color=ass_color(args.color, SubtitleStyle.primary_color),
        outline_color=ass_color(args.outline_color, "&H00000000"),
        back_color=ass_color(args.back_color, "&H80000000"),
        highlight_color=ass_color(args.highlight, None),
        position=args.position,
        margin_v=args.margin_v,
        bold=not args.no_bold,
        pop_animation=not args.no_pop,
        karaoke=not args.no_karaoke,
        play_res_w=info.width,
        play_res_h=info.height,
    )

    ass_path = os.path.join(tempfile.gettempdir(), f"sublime_titler_{os.getpid()}.ass")
    if args.keep_ass:
        ass_path = os.path.splitext(output)[0] + ".ass"
    build_ass(
        segments, ass_path, style,
        title_duration=args.title_duration,
        title_words=args.title_words.split() if args.title_words else None,
    )

    # 4. Burn in
    codec_args = ["-c:v", args.video_codec] if args.video_codec else None

    print(f"rendering: {output}  (ffmpeg: {ffmpeg})")
    if args.verbose:
        print(f"  ass: {ass_path}")
    burn_subtitles(
        ffmpeg, args.input, ass_path, output,
        font_path=font_path, codec_args=codec_args, verbose=args.verbose,
    )

    if not args.keep_ass:
        try:
            os.remove(ass_path)
        except OSError:
            pass

    print(f"done: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())