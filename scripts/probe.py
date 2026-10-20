"""Quick single-trial probe: sizes and per-operation speed for every mode.

    python3 scripts/probe.py [n_names]
"""

import sys
import tempfile
from pathlib import Path

import numpy as np

from cipherops import analytical
from cipherops.codepoints import MODES
from cipherops.harness import run_trial
from cipherops.workload import build_zone, signing_messages, write_messages

n_names = int(sys.argv[1]) if len(sys.argv) > 1 else 200
zone = build_zone("probe", n_names, 1)
with tempfile.TemporaryDirectory() as td:
    for key, mode in MODES.items():
        msgs, _ = signing_messages(zone, mode)
        path = Path(td) / f"{key}.bin"
        write_messages(path, msgs)
        r = run_trial(mode, path, None, 16)
        n = r["n_messages"]
        if mode.family == "mtl":
            sign = sum(r["append_ns"]) + r["ladder_sign_ns"] + sum(r["condensed_ns"])
            ver = r["ladder_verify_ns"] + sum(r["verify_condensed_ns"])
            print(f"{key:<12} n={n} sign_total={sign / 1e9:.3f}s (ladder {r['ladder_sign_ns'] / 1e6:.1f} ms, "
                  f"append/msg {np.median(r['append_ns']) / 1e3:.1f} us) verify_total={ver / 1e6:.2f} ms "
                  f"cold_full={np.median(r['verify_full_ns']) / 1e6:.2f} ms")
            print(f"{'':<12} condensed min/median/max={min(r['condensed_bytes'])}/{int(np.median(r['condensed_bytes']))}/"
                  f"{max(r['condensed_bytes'])} (formula max {analytical.max_condensed_signature(16, n)}) "
                  f"ladder={r['signed_ladder_bytes']} (formula max "
                  f"{analytical.max_signed_ladder(16, n, 7856)}, rungs {analytical.rung_count(n)}) "
                  f"pk={r['public_key_bytes']} rss_growth={r['memory']['rss_after_append'] - r['memory']['rss_after_keygen']}")
        else:
            print(f"{key:<12} n={n} sign/msg={np.median(r['sign_ns']) / 1e6:.3f} ms verify/msg="
                  f"{np.median(r['verify_ns']) / 1e6:.3f} ms sig={int(np.median(r['signature_bytes']))} "
                  f"pk={r['public_key_bytes']} exit={r['exit_code']}")
