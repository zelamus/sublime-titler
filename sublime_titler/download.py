"""Whisper model downloader using Hugging Face's official high-performance path.

faster-whisper's ``WhisperModel("name")`` downloads through
``huggingface_hub.snapshot_download`` using a single connection and shows no
useful progress.  Hugging Face provides two faster transports:

* ``hf_transfer`` (deprecated) and its successor
* **Xet** (``HF_XET_HIGH_PERFORMANCE=1``) — a content-addressed CDN that
  saturates the connection with many parallel chunks (measured ~3.5 MB/s on
  this machine vs ~0.2 MB/s single-connection).

This module calls ``faster_whisper.download_model`` (which is
``snapshot_download(local_dir=..., local_dir_use_symlinks=False)``) with the
Xet flag on, so the model lands in a plain directory that is passed straight
to ``WhisperModel(path)``.  ``HF_ENDPOINT`` is still respected for mirrors;
``huggingface_hub`` raises if the configured mirror is unreachable, so we
fall back to ``huggingface.co`` when the configured endpoint fails.
"""

from __future__ import annotations

import os
from typing import Tuple

__all__ = ["ensure_whisper_model"]

MODEL_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")


def cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_CACHE_HOME")
    base = base or os.path.expanduser("~/.cache")
    return os.path.join(base, "sublime-titler", "whisper")


def model_path(model_name: str) -> str:
    return os.path.join(cache_dir(), f"faster-whisper-{model_name}")


def _complete(model_dir: str) -> bool:
    """All model files present and non-empty?"""
    return all(
        os.path.isfile(os.path.join(model_dir, f)) and os.path.getsize(os.path.join(model_dir, f)) > 0
        for f in MODEL_FILES
    )


def _snapshot(model_name: str, dest: str) -> None:
    from faster_whisper.utils import download_model

    os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"
    try:
        download_model(model_name, output_dir=dest)
    except Exception as first_err:
        # A configured mirror (HF_ENDPOINT) may be unreachable — retry on the
        # official endpoint before giving up.
        if os.environ.get("HF_ENDPOINT"):
            saved = os.environ.pop("HF_ENDPOINT")
            try:
                download_model(model_name, output_dir=dest)
                return
            finally:
                os.environ["HF_ENDPOINT"] = saved
        raise first_err


def ensure_whisper_model(model_name: str) -> Tuple[str, bool]:
    """Return (model_dir, downloaded_this_run). Downloads missing files first."""
    dest = model_path(model_name)
    if _complete(dest):
        return dest, False

    print(f"  model '{model_name}' not cached — downloading to {dest}")
    os.makedirs(dest, exist_ok=True)
    _snapshot(model_name, dest)
    return dest, True