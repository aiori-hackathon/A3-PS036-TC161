"""Timestamped progress lines on stderr, shared by every stage of a run."""

from __future__ import annotations

import json
import os
import sys
import time


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def stage(number: int, total: int, title: str, files: str) -> None:
    bar = "=" * 78
    print(f"\n{bar}\n STEP {number}/{total}  {title}\n   code: {files}\n{bar}", file=sys.stderr, flush=True)
    # The dashboard's progress bar reads this file. Container stdout is often fully buffered
    # by the Docker client until the process exits, so the stderr lines above can arrive too late.
    _write_progress_file(number, total, title)


def _write_progress_file(number: int, total: int, title: str) -> None:
    path = os.environ.get("CIPHEROPS_PROGRESS_FILE")
    if not path:
        return
    try:
        payload = json.dumps({"step": number, "total": total, "title": title, "updated": time.time()})
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        return
