#!/usr/bin/env python3
"""Run sublime-titler without installing:  python main.py <video> --text script.txt"""

import sys

from sublime_titler.cli import main

if __name__ == "__main__":
    sys.exit(main())