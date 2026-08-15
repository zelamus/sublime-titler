"""Command-line interface for sublime-titler.

Burn gorgeous karaoke-style subtitles into any video.  Subtitles can come
from three sources:

* ``--subtitle file.srt`` — an existing SRT file (with an optional
  ``*_timings.json`` sidecar for word-by-word highlighting)
* ``--text file.txt``    — a plain text file; each line is spread evenly over
  the video's duration and given synthetic word timings
* ``--audio file.wav``   — transcribe an audio file with faster-whisper for
  perfectly timed word-level subtitles (requires ``faster-whisper``)
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
    pick_video_codec,
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


def _segments_from_audio(path: str, chunk: Optional[int]) -> List[SubtitleSegment]:
    """Transcribe an audio file with faster-whisper for word-level timings."""
    try:
        from faster_whisper import WhisperModel
        import torch  # noqa: F401  (used only to pick the device)
    except ImportError as exc:
        raise RuntimeError(
            "--audio requires the optional dependency 'faster-whisper' "
            "(and torch). Install with: pip install faster-whisper torch"
        ) from exc

    model = WhisperModel(
        "medium",
        device="cuda" if torch.cuda.is_available() else "cpu",
        compute_type="float16" if torch.cuda.is_available() else "int8",
    )
    whisper_segments, _ = model.transcribe(path, beam_size=5, word_timestamps=True)

    segments: List[SubtitleSegment] = []
    for segment in whisper_segments:
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
    return chunk_segments(segments, max_words=chunk)


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

    src = p.add_argument_group("subtitle source (pick one)")
    src.add_argument("--subtitle", metavar="SRT", help="SRT file (uses *_timings.json sidecar if present)")
    src.add_argument("--text", metavar="TXT", help="Plain text file; lines spread evenly over the video")
    src.add_argument("--audio", metavar="WAV", help="Audio file transcribed with faster-whisper (word-level timing)")
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
    style.add_argument("--position", default="bottom", choices=[
        "bottom", "bottom-left", "bottom-right",
        "middle", "middle-left", "middle-right",
        "top", "top-left", "top-right",
    ], help="Subtitle position on screen")
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

    sources = [args.subtitle, args.text, args.audio]
    given = [s for s in sources if s]
    if len(given) != 1:
        print("error: provide exactly one of --subtitle, --text, or --audio", file=sys.stderr)
        return 2
    if not os.path.isfile(given[0]):
        print(f"error: subtitle source not found: {given[0]}", file=sys.stderr)
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
    if args.subtitle:
        segments = _segments_from_srt(args.subtitle, chunk)
    elif args.text:
        segments = _segments_from_text(args.text, duration, chunk)
    else:
        segments = _segments_from_audio(args.audio, chunk)

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
    codec_args = None
    if args.video_codec:
        codec_args = ["-c:v", args.video_codec]
    else:
        codec_args, chosen = pick_video_codec(ffmpeg)
        if args.verbose:
            print(f"encoder: {chosen}")

    print(f"rendering: {output}  (ffmpeg: {ffmpeg})")
    if args.verbose:
        print(f"  ass: {ass_path}")
    burn_subtitles(ffmpeg, args.input, ass_path, output, font_path=font_path, codec_args=codec_args)

    if not args.keep_ass:
        try:
            os.remove(ass_path)
        except OSError:
            pass

    print(f"done: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())