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
- **Automatic transcription** — drop in any video; faster-whisper transcribes
  its own audio for perfectly timed word-level subtitles. No arguments needed:
  `sublime-titler video.mp4` just works.
- **Reddit-story defaults** — the styling matches the viral story-video look
  out of the box: Nunito SemiBold at 90 px (auto-scaled), white on black
  outline, center position, karaoke highlight, pop-in animation.
- **Adaptive line chunking** — short words pair up, long words get a line to
  themselves, so every subtitle stays short, punchy, and readable.
- **Pop-in animation** — every line pops onto the screen with a snappy scale-in.
- **Works on any video** — portrait, landscape, square, any resolution. The
  style auto-scales to the video's height (no more hardcoded 1080×1920).
- **Alternate input modes**:
  - `--subtitle file.srt` — existing SRT files (JSON sidecar = karaoke timing)
  - `--text script.txt` — plain text, spread evenly over the video
  - `--audio narration.wav` — transcribe an external audio file instead
- **Fully styleable** — font, size, colors, highlight color, position,
  outline, shadow, margins, bold, animations. All optional with sensible defaults.
- **Story-video intro** — `--title-duration` + `--title-words` reproduce the
  "subtitles stay hidden while the title is read, then fade in" effect.
- **Lightweight** — pure Python stdlib + ffmpeg (libass). Whisper is optional.

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

Whisper transcription (the default subtitle source) needs one optional
dependency:

```bash
pip install .[whisper]   # or: pip install faster-whisper
```

### 🚀 GPU acceleration

If you have an NVIDIA GPU, transcription runs on it automatically (float16):

```bash
pip install .[gpu]   # faster-whisper + CUDA runtime libraries (cublas/cudnn)
```

No torch needed. The tool detects CUDA through ctranslate2 and, on Windows,
adds the NVIDIA pip-package DLL directories to `PATH` itself — so GPU
transcription works with zero extra configuration. You'll see it in the output:

```
  model ready  [device: cuda | compute_type: float16]
  transcribing:   5.4s / 12.0s (44.8%)
```

On the first run, whisper downloads the model (default `medium`, ~1.5 GB) from
Hugging Face using **Xet**, their high-performance multi-connection transfer
(usually 5–20× faster than the default single-connection download), with a live
progress bar. If your network can't reach `huggingface.co` directly, a mirror
works too — and the tool automatically falls back to `huggingface.co` if the
configured mirror is unreachable:

```bash
set HF_ENDPOINT=https://hf-mirror.com    # Windows
export HF_ENDPOINT=https://hf-mirror.com # macOS/Linux
```

The tool bundles **Nunito SemiBold** (SIL OFL 1.1) — the font used for the
viral story-video look — so it works out of the box. Use `--font` for any
other `.ttf`/`.otf`.

---

## 🚀 Usage

```bash
# The one-command happy path: whisper transcribes the video's own audio,
# times every word, and burns karaoke subtitles in the Reddit-story style.
sublime-titler video.mp4

# Plain text script → subtitles spread over the video
sublime-titler video.mp4 --text script.txt

# Existing SRT file
sublime-titler video.mp4 --subtitle captions.srt

# Transcribe an external audio file instead of the video's audio
sublime-titler video.mp4 --audio narration.wav

# Use a faster/smaller whisper model (first use downloads it from Hugging Face)
sublime-titler video.mp4 --whisper-model base

# Full story-video intro: hide subs while the title is read, fade the last word in
sublime-titler video.mp4 --text script.txt \
    --title-duration 5 --title-words "my craziest day ever"
```

Output defaults to `<input>_subtitled.mp4`, or pass a second positional arg:

```bash
sublime-titler input.mp4 output.mp4
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
| `--position` | `middle` | `bottom`, `middle`, `top`, and corner variants (middle = Reddit-story look) |
| `--margin-v` | `10` | Vertical margin |
| `--no-bold` / `--no-pop` / `--no-karaoke` | — | Disable bold, pop animation, highlighting |
| `--chunk` | `auto` | Words per line: `auto` (adaptive 1–2), `0` (keep as-is), or fixed `N` |

### 🔧 Encoding & transcription

| Flag | Description |
|---|---|
| `--whisper-model NAME` | Whisper model for transcription: `tiny`/`base`/`small`/`medium`/`large-v3` (default `medium`; first use downloads it) |
| `--ffmpeg PATH` | Path to ffmpeg (auto-detected otherwise) |
| `--video-codec NAME` | Override encoder (`libx264`, `av1_nvenc`, …) — defaults to `av1_nvenc` when available |
| `--keep-ass` | Keep the intermediate `.ass` file next to the output |
| `--verbose` | Print the ffmpeg command |

---

## 🧠 How it works

```
                ┌──────────┐   no --text/--subtitle/--audio?
input video ────┤  ffprobe ├──► YES ──► extract audio ──► whisper transcription
                └──────────┘                                   │
                  resolution                                    ▼
                  (auto-scales styling)                  word-level segments
                                                               │
script.txt ─┐                                                  │
captions.srt ┼─► segments ───────────────────► adaptive chunking ─► karaoke timing
narration.wav┘                                                   │
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
all preserved verbatim, and the default styling matches the original config
(Nunito 90 px, center position, 9/3 outline/shadow). What's new:

- **Any resolution** — `PlayResX/PlayResY` come from the input video, and font
  size / outline / shadow scale with it.
- **Any position** — alignment is a flag instead of hardcoded center.
- **Any source** — the video's own audio (whisper), an SRT, plain text, or an
  external audio file.
- **Whisper is optional** — karaoke timing works from text and SRT too.

---

## 🎬 Example

```bash
# The whole pipeline in one command: transcribe → time → style → burn
sublime-titler my_clip.mp4

# 1. Write a script (if you don't want whisper)
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