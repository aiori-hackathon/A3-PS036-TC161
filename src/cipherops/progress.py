"""Timestamped progress lines on stderr, shared by every stage of a run."""

from __future__ import annotations

import sys
import time


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def stage(number: int, total: int, title: str, files: str) -> None:
    bar = "=" * 78
    print(f"\n{bar}\n STEP {number}/{total}  {title}\n   code: {files}\n{bar}", file=sys.stderr, flush=True)
