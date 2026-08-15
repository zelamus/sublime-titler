"""Subtitle timing — segment model, adaptive word chunking, and SRT/JSON I/O.

This module was extracted from a Reddit-story video generator and generalised so
it can be used on any video.  The heart of it is the *adaptive chunking* logic
that splits transcribed speech into short punchy lines (1–2 words each) that
look great when highlighted word-by-word as a karaoke-style subtitle.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

__all__ = [
    "SubtitleSegment",
    "chunk_segments",
    "chunk_size_for_words",
    "parse_srt",
    "write_srt",
    "load_timings_json",
    "format_timecode",
    "parse_timecode",
]


@dataclass
class SubtitleSegment:
    """A single subtitle line with start/end times and optional per-word timing.

    ``word_timings`` is a list of ``(word, start_seconds, end_seconds)`` tuples.
    When present, the ASS renderer can highlight each word as it is spoken.
    """

    start_time: float
    end_time: float
    text: str
    word_timings: Optional[List[Tuple[str, float, float]]] = field(default=None)


# --------------------------------------------------------------------------- #
# Timing helpers
# --------------------------------------------------------------------------- #

def format_timecode(seconds: float) -> str:
    """Format seconds as an SRT timecode: ``HH:MM:SS,mmm``."""
    ms = int((seconds - int(seconds)) * 1000)
    s = int(seconds) % 60
    m = (int(seconds) // 60) % 60
    h = int(seconds) // 3600
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def parse_timecode(timecode: str) -> float:
    """Parse an SRT timecode (``HH:MM:SS,mmm``) back into seconds."""
    h, m, rest = timecode.split(":", 2)
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


# --------------------------------------------------------------------------- #
# SRT parsing / writing
# --------------------------------------------------------------------------- #

def parse_srt(path: str) -> List[SubtitleSegment]:
    """Parse a standard SRT file into a list of segments.

    Segments have no per-word timing (an SRT file does not carry that data), so
    ``word_timings`` is ``None`` — the ASS renderer falls back to plain styled
    lines with a pop-in animation.  For word-by-word highlighting provide a
    ``_timings.json`` sidecar next to the SRT (see :func:`write_srt`).
    """
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = re.split(r"\n\n+", content.strip())
    segments: List[SubtitleSegment] = []

    for block in blocks:
        if not block.strip():
            continue
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        ts_match = re.match(
            r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})",
            lines[1],
        )
        if not ts_match:
            continue
        text = " ".join(lines[2:]).strip()
        segments.append(
            SubtitleSegment(
                start_time=parse_timecode(ts_match.group(1)),
                end_time=parse_timecode(ts_match.group(2)),
                text=text,
            )
        )
    return segments


def write_srt(segments: List[SubtitleSegment], filename: str) -> str:
    """Write segments to an SRT file, plus a ``*_timings.json`` sidecar.

    The JSON sidecar stores the same segments with their per-word timings, which
    is what powers karaoke-style word highlighting.  It is written next to the
    SRT as ``<filename-without-ext>_timings.json`` and is automatically picked
    up by :func:`load_timings_json`.
    """
    with open(filename, "w", encoding="utf-8") as f:
        for i, segment in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{format_timecode(segment.start_time)} --> {format_timecode(segment.end_time)}\n")
            f.write(f"{segment.text}\n\n")

    json_path = filename.replace(".srt", "_timings.json")
    timings_data = [
        {
            "start": seg.start_time,
            "end": seg.end_time,
            "text": seg.text,
            "word_timings": seg.word_timings,
        }
        for seg in segments
    ]
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(timings_data, f)
    return json_path


def load_timings_json(srt_path: str) -> Optional[List[SubtitleSegment]]:
    """Load the ``*_timings.json`` sidecar for an SRT file, if present.

    Returns ``None`` when the sidecar is missing or unreadable, so callers can
    transparently fall back to the plain SRT.
    """
    json_path = srt_path.replace(".srt", "_timings.json")
    if not __import__("os").path.exists(json_path):
        return None
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [
            SubtitleSegment(
                start_time=seg["start"],
                end_time=seg["end"],
                text=seg["text"],
                word_timings=seg.get("word_timings"),
            )
            for seg in data
        ]
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Adaptive word chunking — the signature look
# --------------------------------------------------------------------------- #

def chunk_size_for_words(words: List[str]) -> int:
    """Return 1 or 2 words per subtitle chunk based on average word length.

    Long words (avg >= 7 chars) get a line to themselves; short words are
    paired up.  This keeps every line short, punchy, and easy to read.
    """
    if not words:
        return 1
    sample = words[:2]
    avg_len = sum(len(w) for w in sample) / len(sample)
    return 1 if avg_len >= 7 else 2


def chunk_segments(
    segments: List[SubtitleSegment],
    max_words: Optional[int] = None,
) -> List[SubtitleSegment]:
    """Group word-level segments into short subtitle chunks.

    Parameters
    ----------
    segments:
        Word-level segments (typically produced by a Whisper transcription).
    max_words:
        ``None`` → adaptive chunking (1–2 words per line, the signature look).
        ``0``   → keep segments as-is (no chunking).
        ``N``   → fixed N words per line (best-effort).

    Timing is tracked separately from the words so chunking never loses or
    misaligns timing, even when a segment had no per-word timing (the segment
    duration is spread evenly across its words in that case).
    """
    if not segments:
        return []

    # ``max_words == 0`` keeps the original segment boundaries untouched —
    # each segment becomes one subtitle line, with per-word timings filled in
    # (spread evenly) so karaoke highlighting still works when enabled.
    if max_words == 0:
        result: List[SubtitleSegment] = []
        for segment in segments:
            words = segment.text.split()
            if not words:
                continue
            timings = segment.word_timings
            if not timings or len(timings) != len(words):
                n = len(words)
                dur = (segment.end_time - segment.start_time) / n
                timings = [
                    (w, segment.start_time + i * dur, segment.start_time + (i + 1) * dur)
                    for i, w in enumerate(words)
                ]
            result.append(SubtitleSegment(segment.start_time, segment.end_time, segment.text, timings))
        return result

    # Flatten everything into two parallel lists:
    #   all_words   : str
    #   all_timings : (word, start, end) | None — one entry per word
    all_words: List[str] = []
    all_timings: List[Optional[Tuple[str, float, float]]] = []

    for segment in segments:
        words = segment.text.split()
        if not words:
            continue
        if segment.word_timings and len(segment.word_timings) == len(words):
            all_words.extend(words)
            all_timings.extend(segment.word_timings)
        else:
            # No per-word timing — spread the segment timing evenly.
            n = len(words)
            dur = (segment.end_time - segment.start_time) / n
            for i, w in enumerate(words):
                all_words.append(w)
                t0 = segment.start_time + i * dur
                t1 = t0 + dur
                all_timings.append((w, t0, t1))

    if not all_words:
        return []

    new_segments: List[SubtitleSegment] = []
    i = 0
    while i < len(all_words):
        if max_words is None:
            target = chunk_size_for_words(all_words[i:])
        else:
            target = max_words
        end = min(i + target, len(all_words))

        chunk_words = all_words[i:end]
        chunk_timings = [t for t in all_timings[i:end] if t is not None]

        start_time = chunk_timings[0][1] if chunk_timings else (all_timings[i][1] if all_timings[i] else 0.0)
        end_time = chunk_timings[-1][2] if chunk_timings else (all_timings[end - 1][2] if all_timings[end - 1] else 0.0)

        new_segments.append(
            SubtitleSegment(
                start_time=start_time,
                end_time=end_time,
                text=" ".join(chunk_words),
                word_timings=chunk_timings if chunk_timings else None,
            )
        )
        i = end

    return new_segments