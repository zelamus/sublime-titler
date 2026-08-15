<div align="center">

# 🌟 sublime-titler

**Burn gorgeous karaoke-style subtitles into any video.**

Every spoken word flashes and scales as it's said — the signature look of
viral story-time videos, now a reusable CLI for anything you want to caption.

```
         ┌────────────────────────────┐
         │   Some random gameplay     │
         │                            │
         │                            │
         │  every word gets its own   │
         │  highlight as it is spoken │
         └────────────────────────────┘
```

</div>

---

## ✨ Features

- **Word-by-word karaoke highlighting** — the active word flashes in a bright
  color and scales to 115% as it's spoken, then settles back to white.
- **Adaptive line chunking** — short words pair up, long words get a line to
  themselves, so every subtitle stays short, punchy, and readable.
- **Pop-in animation** — every line pops onto the screen with a snappy scale-in.
- **Works on any video** — portrait, landscape, square, any resolution. The
  style auto-scales to the video's height (no more hardcoded 1080×1920).
- **Three input modes**:
  - `--subtitle file.srt` — existing SRT files (JSON sidecar = karaoke timing)
  - `--text script.txt` — plain text, spread evenly over the video
  - `--audio narration.wav` — whisper transcription for perfectly timed words
- **Fully styleable** — font, size, colors, highlight color, position,
  outline, shadow, margins, bold, animations. All optional with sensible defaults.
- **Story-video intro** — `--title-duration` + `--title-words` reproduce the
  "subtitles stay hidden while the title is read, then fade in" effect.
- **Zero heavy dependencies** — pure Python stdlib + ffmpeg (libass).

---

## 📦 Installation

Requires **Python 3.9+** and **ffmpeg** (with libass — standard on every
official build).

```bash
git clone https://github.com/zelamus/sublime-titler.git
cd sublime-titler
pip install .            # installs the `sublime-titler` command
# or just run it in-place:
python main.py --help
```

The tool bundles **Nunito SemiBold** (SIL OFL 1.1) — the font used for the
viral story-video look — so it works out of the box. Use `--font` for any
other `.ttf`/`.otf`.

---

## 🚀 Usage

```bash
# Plain text script → subtitles spread over the video
sublime-titler video.mp4 --text script.txt

# Existing SRT file
sublime-titler video.mp4 --subtitle captions.srt

# Transcribe narration audio for perfect word timing (needs faster-whisper)
sublime-titler video.mp4 --audio narration.wav

# Full story-video intro: hide subs while the title is read, fade the last word in
sublime-titler video.mp4 --text script.txt \
    --title-duration 5 --title-words "my craziest day ever"
```

Output defaults to `<input>_subtitled.mp4`, or pass a second positional arg:

```bash
sublime-titler input.mp4 output.mp4 --text script.txt
```

### 🎨 Styling

```bash
sublime-titler video.mp4 --text script.txt \
    --font fonts/Nunito-SemiBold.ttf \
    --font-size 64 \
    --color #FFFFFF \
    --highlight #FFD700 \
    --outline-color #000000 \
    --outline 6 \
    --shadow 2 \
    --position bottom \
    --no-pop --no-karaoke --no-bold
```

| Flag | Default | Description |
|---|---|---|
| `--font` | bundled Nunito | Font file |
| `--font-size` | auto | ASS font size (scales with video height) |
| `--color` | `#FFFFFF` | Text color |
| `--highlight` | random | Karaoke highlight color (random from 8-color palette) |
| `--outline-color` | `#000000` | Outline color |
| `--back-color` | translucent black | Box/back color |
| `--outline` / `--shadow` | auto | Outline width / shadow depth |
| `--position` | `bottom` | `bottom`, `middle`, `top`, and corner variants |
| `--margin-v` | `10` | Vertical margin |
| `--no-bold` / `--no-pop` / `--no-karaoke` | — | Disable bold, pop animation, highlighting |
| `--chunk` | `auto` | Words per line: `auto` (adaptive 1–2), `0` (keep as-is), or fixed `N` |

### 🔧 Encoding

| Flag | Description |
|---|---|
| `--ffmpeg PATH` | Path to ffmpeg (auto-detected otherwise) |
| `--video-codec NAME` | Override encoder (`libx264`, `av1_nvenc`, …) — defaults to `av1_nvenc` when available |
| `--keep-ass` | Keep the intermediate `.ass` file next to the output |
| `--verbose` | Print the ffmpeg command |

---

## 🧠 How it works

```
input video ──ffprobe──► resolution (auto-scales styling)
                          │
script.txt ─┐             ▼
captions.srt ┼─► segments ─► adaptive chunking ─► karaoke word timing
narration.wav┘   (whisper)                        │
                                                  ▼
                                        ASS generator (libass styling)
                                                  │
                                          ffmpeg subtitles= filter
                                                  │
                                                  ▼
                                           subtitled output.mp4
```

The subtitle engine was extracted from a Reddit-story video generator and
generalized: the adaptive 1–2 word chunking, the word-flash highlight
(`\t`-animated inline overrides), the pop-in scale, and the title fade-in are
all preserved verbatim. What's new:

- **Any resolution** — `PlayResX/PlayResY` come from the input video, and font
  size / outline / shadow scale with it.
- **Any position** — alignment is a flag instead of hardcoded center.
- **Any source** — SRT, plain text, or raw audio instead of only TTS audio.
- **Whisper is optional** — karaoke timing works from text and SRT too.

---

## 🎬 Example

```bash
# 1. Write a script
cat > script.txt <<'EOF'
This tool was born inside a Reddit story generator.
Every word gets its own highlight as it is spoken.
EOF

# 2. Burn it into any clip
sublime-titler gameplay.mp4 --text script.txt

# 3. Ship it
#    done: gameplay_subtitled.mp4
```

---

## ⚖️ License

MIT. The bundled font is [Nunito](https://fonts.google.com/specimen/Nunito),
licensed under the SIL Open Font License 1.1.