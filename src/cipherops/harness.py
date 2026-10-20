"""Thin wrapper around the compiled mtlbench binary (one process per trial)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .codepoints import Mode
from .dnswire import Signatures
from .workload import read_blob_set

HARNESS = os.environ.get("CIPHEROPS_HARNESS", "/work/harness/mtlbench")


class HarnessError(RuntimeError):
    pass


def run_trial(mode: Mode, messages: Path, out_dir: Path | None, full_verify: int) -> dict:
    cmd = [HARNESS, mode.harness_mode, "--messages", str(messages), "--algo", mode.harness_algo,
           "--full-verify", str(full_verify)]
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd += ["--out-dir", str(out_dir)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if not proc.stdout.strip():
        raise HarnessError(f"{' '.join(cmd)} produced no result (exit {proc.returncode}): {proc.stderr[-2000:]}")
    result = json.loads(proc.stdout)
    result["exit_code"] = proc.returncode
    result["stderr_tail"] = proc.stderr[-2000:]
    result["command"] = cmd
    return result


def load_signatures(mode: Mode, out_dir: Path, limited: bool) -> Signatures:
    if mode.family == "mtl":
        return Signatures(mode, read_blob_set(out_dir / "condensed.bin"),
                          (out_dir / "signed_ladder.bin").read_bytes())
    return Signatures(mode, read_blob_set(out_dir / "signatures.bin"), reused=limited)
