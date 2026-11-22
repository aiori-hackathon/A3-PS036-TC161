"""Unit tests for resource-monitor aggregation (no Docker, no rsrcsamp binary required)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cipherops import rsrcmon  # noqa: E402


def test_summarize_peaks_and_labels():
    samples = [
        {"label": "sign_verify", "cpu_pct": 80.0, "rss_bytes": 1000, "hwm_bytes": 1200,
         "cgroup_memory_current": -1, "cgroup_memory_peak": -1, "source": "proc"},
        {"label": "sign_verify", "cpu_pct": 90.0, "rss_bytes": 2000, "hwm_bytes": 2200,
         "cgroup_memory_current": -1, "cgroup_memory_peak": -1, "source": "proc"},
        {"label": "wire_cache", "cpu_pct": 10.0, "rss_bytes": 1500, "hwm_bytes": 2200,
         "cgroup_memory_current": 5000, "cgroup_memory_peak": 6000, "source": "cgroup_v2"},
    ]
    s = rsrcmon.summarize(samples)
    assert s["n_samples"] == 3
    assert s["by_label"]["sign_verify"]["cpu_pct_peak"] == 90.0
    assert s["by_label"]["sign_verify"]["mem_bytes_peak"] == 2000
    assert s["by_label"]["wire_cache"]["mem_bytes_peak"] == 5000  # prefers cgroup current
    assert s["overall"]["cpu_pct_peak"] == 90.0


def test_summarize_weights_by_wall_time_and_keeps_run_order():
    samples = [
        {"label": "zones", "wall_ms": 100.0, "cpu_pct": 50.0, "host_cpu_pct": 10.0,
         "host_mem_total_bytes": 8_000_000_000, "host_mem_available_bytes": 4_000_000_000},
        {"label": "sign_verify", "wall_ms": 1000.0, "cpu_pct": 100.0, "host_cpu_pct": 20.0,
         "host_mem_total_bytes": 8_000_000_000, "host_mem_available_bytes": 3_500_000_000},
        {"label": "sign_verify", "wall_ms": 250.0, "cpu_pct": 20.0, "host_cpu_pct": 5.0,
         "host_mem_total_bytes": 8_000_000_000, "host_mem_available_bytes": 3_800_000_000},
    ]
    s = rsrcmon.summarize(samples)
    assert list(s["by_label"]) == ["zones", "sign_verify"]            # order of first appearance, not alphabetical
    sv = s["by_label"]["sign_verify"]
    assert sv["wall_s"] == 1.25
    assert sv["cpu_seconds"] == 1.05                                   # 1.0 s at 100% + 0.25 s at 20%
    assert sv["cpu_pct_mean"] == 84.0                                  # not the plain mean of 100 and 20
    assert sv["cpu_pct_peak"] == 100.0
    assert s["overall"]["cpu_seconds"] == 1.1
    # Host: 0.1s@10% + 1.0s@20% + 0.25s@5% = 0.2225 busy-seconds / 1.35 s wall
    assert s["overall"]["host_cpu_pct_peak"] == 20.0
    assert s["overall"]["host_mem_available_bytes_min"] == 3_500_000_000
    assert s["overall"]["host_cpu_pct_mean"] == round(100.0 * 0.2225 / 1.35, 3)


def test_memory_in_use_leaves_out_inactive_file_cache():
    row = {"cgroup_memory_current": 300, "cgroup_memory_inactive_file": 180, "cgroup_memory_anon": 100,
           "rss_bytes": 90}
    assert rsrcmon.memory_in_use(row) == (120, "cgroup_working_set")
    assert rsrcmon.memory_in_use({"cgroup_memory_current": 300, "rss_bytes": 90}) == (300, "cgroup_current")
    assert rsrcmon.memory_in_use({"cgroup_memory_current": -1, "rss_bytes": 90}) == (90, "proc_rss")
    agg = rsrcmon.summarize([row])["overall"]
    assert agg["mem_bytes_peak"] == 120 and agg["mem_kind"] == "cgroup_working_set"
    assert agg["mem_anon_bytes_peak"] == 100


def test_lab_sampler_uses_cpu_time_between_polls():
    rate = rsrcmon.DockerLabSampler._rate_since_last_poll
    last: dict = {}
    first = {"container": "eco-root", "cpu_total_usec": 1_000_000, "ts_mono_ns": 10_000_000_000,
             "cpu_pct": 3.0, "wall_ms": 100.0, "cpus_allowed": 2}
    rate(first, last)
    assert first["window"] == "once" and first["cpu_pct"] == 3.0       # nothing to compare with yet
    # 2 s later the container has used 1.5 s more CPU; the 0.1 s window happened to see it idle.
    second = {"container": "eco-root", "cpu_total_usec": 2_500_000, "ts_mono_ns": 12_000_000_000,
              "cpu_pct": 0.0, "wall_ms": 100.0, "cpus_allowed": 2}
    rate(second, last)
    assert second["window"] == "since_last_poll"
    assert second["cpu_pct"] == 75.0 and second["cpu_pct_per_cpu"] == 37.5 and second["wall_ms"] == 2000.0
    # A restarted container starts counting from zero again: keep the short window instead of a negative rate.
    third = {"container": "eco-root", "cpu_total_usec": 40_000, "ts_mono_ns": 14_000_000_000,
             "cpu_pct": 9.0, "wall_ms": 100.0, "cpus_allowed": 2}
    rate(third, last)
    assert third["window"] == "once" and third["cpu_pct"] == 9.0


def test_load_jsonl(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"label":"a","cpu_pct":1}\nnot-json\n{"label":"b","cpu_pct":2}\n', encoding="utf-8")
    rows = rsrcmon.load_jsonl(p)
    assert len(rows) == 2
    assert rows[1]["label"] == "b"


def test_find_sampler_missing(monkeypatch):
    monkeypatch.setattr(rsrcmon, "SAMPLER", "/nonexistent/rsrcsamp")
    monkeypatch.setattr(rsrcmon.shutil, "which", lambda _n: None)
    # Inside the benchmark image the sampler is installed, so "missing" means "none of the candidates can be run".
    monkeypatch.setattr(rsrcmon.os, "access", lambda *_a, **_k: False)
    assert rsrcmon.find_sampler() is None


@pytest.mark.skipif(sys.platform == "win32", reason="rsrcsamp is a Linux /proc + cgroup tool")
def test_rsrcsamp_builds_and_once():
    import os
    import subprocess

    harness = ROOT / "harness"
    if not (harness / "rsrcsamp.c").is_file():
        pytest.skip("no rsrcsamp.c")
    subprocess.run(["make", "-C", str(harness), "rsrcsamp"], check=True, capture_output=True)
    bin_path = harness / "rsrcsamp"
    assert bin_path.is_file()
    cp = subprocess.run(
        [str(bin_path), "--once", "--pid", str(os.getpid()), "--label", "test"],
        capture_output=True, text=True, check=True,
    )
    row = json.loads(cp.stdout.strip().splitlines()[-1])
    assert row["label"] == "test"
    assert "cpu_pct" in row
    assert row["rss_bytes"] > 0
    assert row["cpu_total_usec"] >= 0 and row["cpus_allowed"] >= 1


@pytest.mark.skipif(sys.platform == "win32", reason="rsrcsamp is a Linux /proc + cgroup tool")
def test_rsrcsamp_keeps_every_label_sent_together(tmp_path: Path):
    """Two labels written in one go must both take effect: the second one used to stay unread."""
    import os
    import subprocess
    import time

    harness = ROOT / "harness"
    subprocess.run(["make", "-C", str(harness), "rsrcsamp"], check=True, capture_output=True)
    out = tmp_path / "s.jsonl"
    proc = subprocess.Popen([str(harness / "rsrcsamp"), "--out", str(out), "--interval-ms", "100",
                             "--pid", str(os.getpid()), "--label", "start"], stdin=subprocess.PIPE, text=True)
    time.sleep(0.25)
    proc.stdin.write("LABEL first\nLABEL second\n")
    proc.stdin.flush()
    time.sleep(0.25)
    proc.stdin.write("QUIT\n")
    proc.stdin.flush()
    assert proc.wait(timeout=5) == 0
    labels = [r["label"] for r in rsrcmon.load_jsonl(out)]
    assert labels[0] == "start" and labels[-1] == "second"
    assert "first" not in labels            # it lasted no time at all, so no row carries it
