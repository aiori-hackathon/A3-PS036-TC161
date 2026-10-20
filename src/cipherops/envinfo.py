"""Snapshot of the pinned software and hardware environment for every run."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path


def _cmd(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return "unavailable"


def _cpu_model() -> str:
    for line in _read("/proc/cpuinfo").splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return platform.processor() or "unavailable"


def _sha256(path: str) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return "unavailable"


def collect(harness_path: str) -> dict:
    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else []
    packages = {}
    for name in ("dnspython", "numpy", "pandas", "pyarrow", "PyYAML", "cryptography", "matplotlib", "Markdown"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = "missing"
    meminfo = _read("/proc/meminfo").splitlines()
    return {
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_sha": os.environ.get("CIPHEROPS_GIT_SHA", "unknown"),
        "host_image_id": os.environ.get("CIPHEROPS_HOST_IMAGE_ID", "unknown"),
        "hardware": {
            "cpu_model": _cpu_model(),
            "logical_cpus_visible": os.cpu_count(),
            "cpu_affinity": affinity,
            "cpuset_requested": os.environ.get("CIPHEROPS_CPUSET", "unset"),
            "scaling_governor": _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
            "mem_total": meminfo[0] if meminfo else "unavailable",
        },
        "software": {
            "os_release": _read("/etc/os-release").splitlines()[:4],
            "kernel": platform.release(),
            "python": sys.version.split()[0],
            "gcc": _cmd(["gcc", "--version"]).splitlines()[0] if _cmd(["gcc", "--version"]) else "unavailable",
            "openssl": _cmd(["/usr/local/bin/openssl", "version"]),
            "openssl_tag": os.environ.get("CIPHEROPS_OPENSSL_TAG", "unknown"),
            "liboqs_tag": os.environ.get("CIPHEROPS_LIBOQS_TAG", "unknown"),
            "mtl_reference_commit": os.environ.get("CIPHEROPS_MTL_COMMIT", "unknown"),
            "mtl_reference_cflags": _read("/opt/mtl_cflags.txt"),
            "harness_sha256": _sha256(harness_path),
            "python_packages": packages,
        },
    }
