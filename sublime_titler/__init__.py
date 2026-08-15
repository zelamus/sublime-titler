"""sublime-titler — burn gorgeous karaoke-style subtitles into any video."""

__version__ = "0.2.1"

from .cli import main  # noqa: F401
from .core import SubtitleSegment  # noqa: F401

__all__ = ["main", "SubtitleSegment", "__version__"]