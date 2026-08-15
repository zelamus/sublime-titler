"""ASS subtitle generation — karaoke word highlighting, pop-in animation,
and optional title fade-in, all with a fully parameterised style.

This is the visual heart of the tool, extracted from the original Reddit-story
generator.  Each word is written as an inline-override block so the *currently
spoken* word flashes in a highlight colour and scales up to 115%, then settles
back to white at 100% — a classic karaoke / TikTok-story subtitle effect.
"""

from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .core import SubtitleSegment

__all__ = [
    "HIGHLIGHT_COLORS",
    "SubtitleStyle",
    "font_family_name",
    "build_ass",
]

# ASS colours for the active-word highlight (&HAABBGGRR format).
# One is picked randomly per render unless the user pins one with --highlight.
HIGHLIGHT_COLORS = [
    "&H0000FFFF",   # yellow
    "&H0000FF00",   # lime green
    "&H00FF6B00",   # electric blue
    "&H000080FF",   # orange
    "&H00FF00FF",   # magenta
    "&H0000BFFF",   # gold
    "&H00FF4500",   # dodger blue
    "&H004169FF",   # coral
]

WHITE = "&H00FFFFFF"   # fully opaque white
INVISIBLE = "&HFF000000"  # fully transparent (alpha FF = invisible)

# Friendly position name → ASS Alignment number.
#   1-3 bottom row, 4-6 middle row, 7-9 top row; 2/5/8 are centred.
POSITIONS = {
    "bottom": 2,
    "bottom-left": 1,
    "bottom-right": 3,
    "middle": 5,
    "middle-left": 4,
    "middle-right": 6,
    "top": 8,
    "top-left": 7,
    "top-right": 9,
}


def hex_to_ass_color(hex_color: str) -> str:
    """Convert ``#RRGGBB`` / ``RRGGBB`` to an opaque ASS colour ``&H00BBGGRR``."""
    value = hex_color.strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"expected a 6-digit hex colour like #FFD700, got {hex_color!r}")
    r, g, b = value[0:2], value[2:4], value[4:6]
    return f"&H00{b}{g}{r}".upper()


def font_family_name(font_path: str) -> str:
    """Derive the ASS font family name from a font file path.

    Nunito keeps its family name (matches how the original project loaded it);
    any other font uses the file name without extension.
    """
    font_filename = os.path.basename(font_path)
    if "Nunito" in font_filename:
        return "Nunito"
    return os.path.splitext(font_filename)[0]


@dataclass
class SubtitleStyle:
    """Everything that controls how subtitles look in the ASS file."""

    font_path: Optional[str] = None
    font_size: int = 0                 # 0 → auto-scale relative to video height
    primary_color: str = WHITE
    outline_color: str = "&H00000000"  # black
    back_color: str = "&H80000000"     # semi-transparent black box
    highlight_color: Optional[str] = None  # None → random from palette
    outline: int = 0                   # 0 → auto-scale
    shadow: int = 0                    # 0 → auto-scale
    bold: bool = True
    position: str = "middle"
    margin_v: int = 10
    play_res_w: int = 1920
    play_res_h: int = 1080
    pop_animation: bool = True         # scale 90→100% pop on every line
    karaoke: bool = True               # word-by-word highlight (needs timings)
    max_font_size: int = 140

    def resolved_font_size(self) -> int:
        """Font size relative to the playback resolution (90 @ 1920 tall)."""
        if self.font_size > 0:
            return self.font_size
        scaled = round(90 * self.play_res_h / 1920)
        return max(24, min(self.max_font_size, scaled))

    def resolved_outline(self) -> int:
        if self.outline > 0:
            return self.outline
        return max(2, round(9 * self.play_res_h / 1920))

    def resolved_shadow(self) -> int:
        if self.shadow > 0:
            return self.shadow
        return max(1, round(3 * self.play_res_h / 1920))

    def alignment(self) -> int:
        return POSITIONS.get(self.position, 2)


def _ass_time(seconds: float) -> str:
    cs = int(round((seconds % 1) * 100))
    s = int(seconds) % 60
    m = (int(seconds) // 60) % 60
    h = int(seconds) // 3600
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def _esc(text: str) -> str:
    """Escape backslashes for ASS inline overrides."""
    return text.replace("\\", "\\\\")


def _word_tag(word: str, w_start: float, w_end: float, seg_start: float,
              highlight: str, fade_ms: Optional[int] = None) -> str:
    """Build the karaoke override tag for a single word.

    The word sits white at 100% scale; from ``w_start`` it flashes to the
    highlight colour at 115% scale for ~80 ms, then settles back.
    ``fade_ms`` (optional) additionally fades the whole word in from
    transparent over that many milliseconds — used for the title fade-in.
    """
    word = _esc(word)
    t0 = max(0, int((w_start - seg_start) * 1000))
    t1 = t0 + 80
    t2 = max(t1 + 1, int((w_end - seg_start) * 1000))
    t3 = t2 + 80

    prefix = ""
    if fade_ms:
        prefix = f"\\alpha&HFF&\\fscx90\\fscy90\\t(0,{fade_ms},\\alpha&H00&\\fscx100\\fscy100)"
    return (
        f"{{\\c{WHITE}\\fscx100\\fscy100{prefix}"
        f"\\t({t0},{t1},\\c{highlight}\\fscx115\\fscy115)"
        f"\\t({t2},{t3},\\c{WHITE}\\fscx100\\fscy100)}}{word}"
    )


def build_ass(
    segments: Sequence[SubtitleSegment],
    ass_path: str,
    style: SubtitleStyle,
    title_duration: float = 0.0,
    title_words: Optional[Sequence[str]] = None,
) -> str:
    """Render segments to an ASS file with karaoke highlighting.

    Parameters
    ----------
    segments:
        Subtitle lines, ideally with per-word timings for karaoke mode.
    ass_path:
        Where to write the ASS file.
    style:
        Visual style (font, size, colours, position, …).
    title_duration:
        When > 0, subtitles that play before the title finishes reading are
        written invisible, and the segment containing the last title word(s)
        fades in — mimicking the original story-video intro.  Default 0 = all
        subtitles visible from the start.
    title_words:
        The words of the title (used to find the fade-in trigger segment).
    """
    if style.highlight_color:
        highlight = style.highlight_color
        if highlight.startswith("#") or (
            len(highlight) == 6 and all(c in "0123456789abcdefABCDEF" for c in highlight)
        ):
            highlight = hex_to_ass_color(highlight)
    else:
        highlight = random.choice(HIGHLIGHT_COLORS)

    font_name = font_family_name(style.font_path) if style.font_path else "Arial"

    # Title fade-in trigger words — the last two meaningful title words.
    title_words = [w.strip(".,!?\"' ").lower() for w in (title_words or []) if w.strip()]
    trigger_words = (
        set(title_words[-2:])
        if len(title_words) >= 2
        else set(title_words[-1:])
        if title_words
        else set()
    )

    def seg_contains_trigger(text: str) -> bool:
        if not trigger_words:
            return False
        words_in_seg = {w.strip(".,!?\"' ").lower() for w in text.split()}
        return bool(trigger_words & words_in_seg)

    # ── Pass 1: locate the fade-in segment (only when title logic is on) ─────
    fade_in_idx = None
    if trigger_words and title_duration > 0:
        for idx, seg in enumerate(segments):
            if seg.start_time <= title_duration and seg_contains_trigger(seg.text):
                fade_in_idx = idx
                break
        if fade_in_idx is None:
            for idx, seg in enumerate(segments):
                if seg.start_time >= title_duration:
                    fade_in_idx = idx
                    break

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(
            "[Script Info]\n"
            "Title: Subtitles\n"
            "ScriptType: v4.00+\n"
            f"PlayResX: {style.play_res_w}\n"
            f"PlayResY: {style.play_res_h}\n"
            "WrapStyle: 0\n\n"
        )
        f.write(
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        )
        f.write(
            f"Style: Default,{font_name},{style.resolved_font_size()},"
            f"{style.primary_color},&H000000FF,{style.outline_color},{style.back_color},"
            f"{'-1' if style.bold else '0'},0,0,0,100,100,0,0,1,"
            f"{style.resolved_outline()},{style.resolved_shadow()},"
            f"{style.alignment()},10,10,{style.margin_v},1\n\n"
        )
        f.write("[Events]\n")
        f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

        pop = "{\\fscx90\\fscy90\\t(0,60,\\fscx100\\fscy100)}"

        for idx, seg in enumerate(segments):
            seg_start = seg.start_time
            seg_end = seg.end_time
            word_timings = seg.word_timings or []
            use_karaoke = style.karaoke and bool(word_timings)

            is_invisible = fade_in_idx is not None and idx < fade_in_idx
            is_fade_in = fade_in_idx is not None and idx == fade_in_idx

            if is_invisible:
                f.write(
                    f"Dialogue: 0,{_ass_time(seg_start)},{_ass_time(seg_end)},"
                    f"Default,,0,0,0,,{{\\alpha&HFF&}}{_esc(seg.text)}\n"
                )
                continue

            if is_fade_in:
                fade_ms = min(300, max(80, int((seg_end - seg_start) * 1000)))
                if use_karaoke:
                    parts = [
                        _word_tag(w[0], w[1], w[2], seg_start, highlight, fade_ms=fade_ms)
                        for w in word_timings
                    ]
                else:
                    parts = [
                        f"{{\\alpha&HFF&\\t(0,{fade_ms},\\alpha&H00&)"
                        f"\\fscx90\\fscy90\\t(0,{fade_ms},\\fscx100\\fscy100)}}{_esc(seg.text)}"
                    ]
                f.write(
                    f"Dialogue: 0,{_ass_time(seg_start)},{_ass_time(seg_end)},"
                    f"Default,,0,0,0,,{' '.join(parts)}\n"
                )
                continue

            # Normal segment
            if use_karaoke:
                parts = [
                    _word_tag(w[0], w[1], w[2], seg_start, highlight)
                    for w in word_timings
                ]
            else:
                parts = [f"{pop}{_esc(seg.text)}" if style.pop_animation else _esc(seg.text)]
            f.write(
                f"Dialogue: 0,{_ass_time(seg_start)},{_ass_time(seg_end)},"
                f"Default,,0,0,0,,{' '.join(parts)}\n"
            )

    return ass_path