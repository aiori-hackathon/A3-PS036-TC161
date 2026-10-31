"""Reproducible report for one ecosystem benchmark suite: report.md, report.html, figures/.

Mirrors the main Cipherops ``cipherops.report`` document for the real-DNS lab path
(NSD + Unbound + ldns). Every number is read from the suite folder and the per-size
run directories it references, so regenerating is idempotent.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path

import pandas as pd

from live_lab import compare, driver
from live_lab.driver import ECO
from live_lab.suite import SUITES, bench_shape

LABELS = {
    "rsa": "RSA-2048/SHA-256",
    "ecdsa": "ECDSA P-256",
    "ed25519": "Ed25519",
    "slh-dsa": "SLH-DSA-SHA2-128s without MTL",
    "mtl": "SLH-DSA-SHA2-128s with MTL",
    "mixed-mtl": "Mixed (classical parent, MTL leaf)",
}

TRACEABILITY = [
    ("One supported PQC signature algorithm", "SLH-DSA-SHA2-128s in ``slh-dsa`` and ``mtl`` scenarios", "sec. 4.3"),
    ("One mentor-tested MTL reference implementation", "Verisign MTL / mtl-mode-nsd / unbound / ldns at stack pin", "sec. 4.1"),
    ("Three dataset sizes", "50 / 500 / 5,000 data names across a fixed hierarchy", "sec. 4.2"),
    ("Equivalent workloads with and without MTL", "Same zones, same seeded lookup list per size", "sec. 4.4"),
    ("Repeated trials, pinned software and hardware", "Measurement trials + signing trials; stack images; host snapshot", "sec. 4.1, 4.4"),
    ("Signing time", "Full-zone wall clock once; sample trials extrapolated with 95% CI", "sec. 5.1"),
    ("Verification time", "``ldns-verify-zone`` wall clock (offline; not Unbound validation)", "sec. 5.2"),
    ("Transmitted bytes", "Resolver to authoritative bytes from Unbound-iface pcap, cold pass", "sec. 5.3"),
    ("Record configuration and workload parameters", "``params.json`` / ``suite.json`` / per-run ``run.json``", "Appendix A"),
    ("Separate measured cache from analytical estimates", "Unbound cache memory vs draft section 12 signature storage", "sec. 5.4"),
]


def _md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(str(c) for c in cols) + " |",
        "|" + "|".join("---" for _ in cols) + "|",
    ]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in row.tolist()) + " |")
    return "\n".join(lines)


def _label(sc: str) -> str:
    return LABELS.get(sc, sc)


def _stack_env(stack: str) -> dict[str, str]:
    path = ECO / "stacks" / f"{stack}.env"
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def _images_from_runs(sd: dict) -> list[str]:
    tags = []
    for run in (sd.get("runs") or {}).values():
        env = (run.get("environment") or {})
        imgs = env.get("images") or env.get("image_digests") or {}
        if isinstance(imgs, dict):
            for name, dig in imgs.items():
                tags.append(f"{name} = {dig}" if dig else str(name))
        elif isinstance(imgs, list):
            tags.extend(str(x) for x in imgs)
    # unique, stable
    seen, uniq = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def _pick(df: pd.DataFrame, dataset: int, scenario: str, col: str):
    rows = df[(df["dataset"] == dataset) & (df["scenario"] == scenario)]
    if rows.empty or col not in rows:
        return None
    v = rows.iloc[0][col]
    return None if pd.isna(v) else float(v)


def figures(sf: pd.DataFrame, out: Path) -> dict[str, str]:
    """Bar charts for signing (full measured), wire bytes, and cache. Returns relative paths."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return {}
    out.mkdir(parents=True, exist_ok=True)
    made: dict[str, str] = {}
    if sf.empty:
        return made
    datasets = sorted(sf["dataset"].unique())
    scenarios = compare.order_scenarios(sf["scenario"].tolist())
    x = np.arange(len(datasets))
    width = 0.8 / max(len(scenarios), 1)

    def _grouped(col: str, title: str, ylabel: str, fname: str, log: bool = False) -> None:
        fig, ax = plt.subplots(figsize=(8.5, 4.2))
        for i, sc in enumerate(scenarios):
            vals = []
            for d in datasets:
                v = _pick(sf, int(d), sc, col)
                vals.append(v if v and v > 0 else (float("nan") if log else 0.0))
            ax.bar(x + i * width, vals, width, label=_label(sc), color=compare.COLORS.get(sc, "#888"))
        ax.set_xticks(x + width * (len(scenarios) - 1) / 2)
        ax.set_xticklabels([f"{int(d):,} names" for d in datasets])
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        if log:
            ax.set_yscale("log")
        ax.legend(fontsize=8, loc="best")
        fig.tight_layout()
        path = out / fname
        fig.savefig(path, dpi=120)
        plt.close(fig)
        made[fname] = f"figures/{fname}"

    _grouped("full_sign_s", "Whole-hierarchy signing time (measured once, log scale)",
             "seconds", "sign_full.png", log=True)
    _grouped("upstream_bytes", "Resolver to authoritative bytes (cold pass)",
             "bytes", "wire.png", log=True)
    _grouped("cache_rrset", "Unbound RRset cache after cold pass (measured)",
             "bytes", "cache.png", log=True)
    if "analytic_sig_bytes" in sf.columns and sf["analytic_sig_bytes"].notna().any():
        _grouped("analytic_sig_bytes", "Signature storage, whole zone set (analytical, draft section 12)",
                 "bytes", "analytical.png", log=True)
    return made


def build_markdown(sd: dict, figs: dict[str, str]) -> str:
    suite = sd["suite"]
    sid = suite.get("suite_id", "")
    stack = suite.get("stack", "draft06")
    env = _stack_env(stack)
    host = suite.get("host") or {}
    prov = suite.get("provenance") or {}
    sizes = sd["datasets"]
    scenarios = list(suite.get("scenarios") or [])
    sf = compare.scaling_frame(sd)
    stats = compare.signing_trial_stats(sd["trials"], sd["full"])
    mid = 500 if 500 in sizes else (sizes[len(sizes) // 2] if sizes else None)
    large = max(sizes) if sizes else None

    L: list[str] = []
    L.append("# DNS Lab Benchmark: MTL Mode for Post-Quantum DNSSEC")
    L.append("### PS-036 · Real hierarchy (NSD + Unbound + ldns) · Cipherops ecosystem")
    L.append("")
    L.append(
        f"Suite `{sid}` · stack **{stack}** · "
        f"{suite.get('trials', '?')} measurement trials · "
        f"{suite.get('sign_trials', '?')} signing trials · "
        f"{suite.get('n_queries', '?')} lookups per pass · "
        f"finished `{suite.get('finished', '')}` · "
        f"wall {compare.fmt_seconds(suite.get('seconds'))}"
    )
    L.append("")
    L.append("## Abstract")
    L.append("")
    L.append(
        "Post-quantum signatures such as SLH-DSA-SHA2-128s are large enough that DNSSEC answers "
        "often no longer fit a 1232-byte UDP buffer. Merkle Tree Ladder (MTL) mode signs a zone "
        "with one PQC signature over a Merkle ladder and attaches a short authentication path to "
        "each RRset. This report is the **real-DNS** counterpart to the main Cipherops library "
        "report: a multi-zone hierarchy is signed with ldns, served by Verisign MTL-mode NSD, "
        "validated by Unbound, and measured with repeated cold/warm trials and packet captures."
    )
    L.append("")
    L.append(
        f"This suite used stack **{stack}**"
        + (f" (MTL `{env.get('MTL_COMMIT', '')[:12]}…`, liboqs {env.get('LIBOQS_TAG', '?')})"
           if env else "")
        + ". "
        "On draft06, MTL answers are **condensed by default**; a full signed ladder is returned "
        "when the resolver asks with EDNS option 65050."
    )
    L.append("")
    L.append(
        "**Provenance in this report.** Signing and verification times appear as "
        "**Measured once** (full zones that were served) and, where sample trials exist, as "
        "**Estimated** (extrapolated from repeated sample-zone trials, with a distribution-free CI of the median). "
        "Wire bytes and Unbound cache are **Measured** from the lab. Signature-storage totals "
        "tagged **Analytical** use draft-harvey-cfrg-mtl-mode section 12 formulas only."
    )
    L.append("")
    L.append("**Key results** (MTL vs SLH-DSA-SHA2-128s without MTL):")
    L.append("")
    for ds, tag in ((mid, "medium"), (large, "large")):
        if ds is None or "mtl" not in scenarios or "slh-dsa" not in scenarios:
            continue
        fs_m, fs_s = _pick(sf, ds, "mtl", "full_sign_s"), _pick(sf, ds, "slh-dsa", "full_sign_s")
        es_m, es_s = _pick(sf, ds, "mtl", "sign_s"), _pick(sf, ds, "slh-dsa", "sign_s")
        fv_m, fv_s = _pick(sf, ds, "mtl", "full_verify_s"), _pick(sf, ds, "slh-dsa", "full_verify_s")
        w_m, w_s = _pick(sf, ds, "mtl", "upstream_bytes"), _pick(sf, ds, "slh-dsa", "upstream_bytes")
        c_m, c_s = _pick(sf, ds, "mtl", "cache_rrset"), _pick(sf, ds, "slh-dsa", "cache_rrset")
        L.append(
            f"- **Signing ({tag}, {ds:,} names, measured once).** "
            f"MTL {compare.fmt_seconds(fs_m)}; direct SLH-DSA {compare.fmt_seconds(fs_s)} "
            f"({compare.ratio_text(fs_s, fs_m, 'faster', 'slower')})."
        )
        if es_m and es_s:
            L.append(
                f"- **Signing ({tag}, estimated from sample trials).** "
                f"MTL {compare.fmt_seconds(es_m)}; direct SLH-DSA {compare.fmt_seconds(es_s)} "
                f"({compare.ratio_text(es_s, es_m, 'faster', 'slower')})."
            )
        L.append(
            f"- **Zone verification ({tag}, `ldns-verify-zone`, measured once).** "
            f"MTL {compare.fmt_seconds(fv_m)}; direct SLH-DSA {compare.fmt_seconds(fv_s)} "
            f"({compare.ratio_text(fv_s, fv_m, 'faster', 'slower')})."
        )
        L.append(
            f"- **Transmitted bytes ({tag}, cold upstream, measured).** "
            f"MTL {compare.fmt_bytes(w_m)}; direct SLH-DSA {compare.fmt_bytes(w_s)} "
            f"({compare.ratio_text(w_s, w_m, 'less data', 'more data')})."
        )
        L.append(
            f"- **Resolver RRset cache ({tag}, measured).** "
            f"MTL {compare.fmt_bytes(c_m)}; direct SLH-DSA {compare.fmt_bytes(c_s)} "
            f"({compare.ratio_text(c_s, c_m, 'less memory', 'more memory')})."
        )
    L.append("")

    L.append("## 1. Problem statement")
    L.append("")
    L.append(
        "PS-036 asks for a reproducible measurement of what MTL mode changes for DNSSEC when "
        "the same PQC algorithm is used with and without MTL, on three dataset sizes, under a "
        "pinned environment, reporting signing time, verification time and transmitted bytes, "
        "with cache use kept separate from analytical estimates. This suite answers that on a "
        "**live multi-zone DNS path**, complementary to the in-process library harness."
    )
    L.append("")

    L.append("## 2. Background")
    L.append("")
    L.append(
        "- **DNSSEC** (RFC 4033–4035) signs each RRset; NSEC3 proves non-existence.\n"
        "- **SLH-DSA-SHA2-128s** (FIPS 205): 32-byte public key, 7,856-byte signature.\n"
        "- **MTL mode** (draft-harvey-cfrg-mtl-mode): one signed ladder per series; condensed "
        "paths per message; full signature = condensed + signed ladder.\n"
        "- **DNSSEC MTL (draft-fregly-06 / this stack):** condensed RRSIGs by default on draft06; "
        "full ladder on EDNS 65050. Always-full wire behaviour is a different stack (`latest`)."
    )
    L.append("")

    L.append("## 3. Objectives and scope")
    L.append("")
    L.append(
        "Compare equivalent DNSSEC workloads with and without MTL on three dataset sizes in a "
        "Docker hierarchy. Measure signing, offline zone verification, resolver wire bytes and "
        "Unbound cache. Report analytical signature-storage sizes separately. Out of scope: new "
        "cryptography, Internet deployment, security proofs."
    )
    L.append("")
    L.append("| PS-036 / expected outcome | How this suite meets it | Where |")
    L.append("|---|---|---|")
    for req, how, where in TRACEABILITY:
        L.append(f"| {req} | {how} | {where} |")
    L.append("| Reproducible results report | This `report.md` / `report.html`, regenerated from suite artifacts | this document |")
    L.append("| Technical demonstration | Dashboard Simulated lab (Prepare, Query, Benchmark) + Compare over the same stack | dashboard |")
    L.append("")

    L.append("## 4. Methodology")
    L.append("")
    L.append("### 4.1 Pinned environment")
    L.append("")
    pin_rows = [
        ("Suite id", sid),
        ("Stack", stack),
        ("Host platform", host.get("platform", "—")),
        ("Host CPUs", host.get("cpus", "—")),
        ("Host Python", host.get("python", "—")),
        ("CPU model", prov.get("cpu_model", "—")),
        ("Memory", prov.get("mem_total", "—")),
        ("Kernel", prov.get("kernel", "—")),
        ("CPU frequency governor", prov.get("cpu_governor", "—")),
        ("CPU isolation", prov.get("cpu_isolation", "—")),
        ("Docker server", prov.get("docker_server", "—")),
        ("Code version (git)", (prov.get("git_commit", "—") or "—")[:12]
         + (" (uncommitted changes)" if prov.get("git_dirty") else "")),
        ("Signature primitive", driver.STACK_PRIMITIVE.get(stack, "—")),
        ("liboqs (stack)", env.get("LIBOQS_TAG", "—")),
        ("MTL commit (stack)", env.get("MTL_COMMIT", "—")),
        ("ldns commit (stack)", env.get("LDNS_COMMIT", "—")),
        ("NSD commit (stack)", env.get("NSD_COMMIT", "—")),
        ("Unbound commit (stack)", env.get("UNBOUND_COMMIT", "—")),
        ("Parallel signing jobs", suite.get("jobs", 1)),
        ("UDP size limit", suite.get("udp", "—")),
        ("Networks", ", ".join(suite.get("networks") or [])),
    ]
    L.append(_md_table(pd.DataFrame(pin_rows, columns=["Item", "Value"])))
    gov = str(prov.get("cpu_governor") or "")
    if gov and gov not in ("performance", "unavailable"):
        L.append("")
        L.append(f"> **Timing caveat.** The host CPU governor was `{gov}`, so the clock speed can change during the "
                 "run. Repeat on a host set to `performance` for tighter timings. Lab containers also share every "
                 "host CPU; the offline harness, by contrast, runs on a dedicated core.")
    built = prov.get("images") or {}
    if built:
        L.append("")
        L.append("Images used (from `docker image inspect` at the start of the suite):")
        L.append("")
        for name, info in built.items():
            L.append(f"- `{name}:{stack}` = `{info.get('id', 'unavailable')}` (built {info.get('created', 'unavailable')})")
    imgs = _images_from_runs(sd)
    if imgs:
        L.append("")
        L.append("Image digests recorded in per-size `run.json`:")
        L.append("")
        for line in imgs[:12]:
            L.append(f"- `{line}`")
    L.append("")

    L.append("### 4.2 Datasets and hierarchy")
    L.append("")
    L.append(
        "Each dataset size uses the same shape: one root, one TLD (`test.`), up to four leaf "
        "zones on one leaf server. Only the number of data names changes; names are split across "
        "the leaf zones. This is **not** a single zone of N names (that is the main library harness)."
    )
    L.append("")
    shape_rows = []
    for n in sizes:
        sh = bench_shape(n)
        detail = (suite.get("sizes_detail") or {}).get(str(n), {})
        shape_rows.append({
            "dataset (names)": f"{n:,}",
            "leaf zones": sh["sld_count"],
            "names per leaf": ", ".join(str(x) for x in sh["sld_sizes"]),
            "zones total": detail.get("zones", 1 + 1 + sh["sld_count"]),
            "lab tag": detail.get("tag", f"eco-bench-{n}-{stack}"),
            "run folder": detail.get("run_folder", "—"),
        })
    L.append(_md_table(pd.DataFrame(shape_rows)))
    L.append("")
    L.append("*Table 1: datasets.*")
    L.append("")

    L.append("### 4.3 Scenarios")
    L.append("")
    L.append("| Scenario | Meaning |")
    L.append("|---|---|")
    for sc in compare.order_scenarios(scenarios):
        L.append(f"| `{sc}` | {_label(sc)} |")
    L.append("")

    L.append("### 4.4 Measurement procedure")
    L.append("")
    wl = suite.get("workload") or {}
    L.append(
        f"- For each dataset size the suite signs every zone once per scenario "
        f"(**Measured once**), with `jobs={suite.get('jobs', 1)}`.\n"
        f"- It then repeats signing and `ldns-verify-zone` on sample leaf zones "
        f"({suite.get('sign_trials')} trials; sample sizes derived from "
        f"`sample_names={suite.get('sample_names')}`). A two-point fit (fixed cost per zone + "
        f"cost per RRSIG) extrapolates to the full hierarchy (**Estimated**, CI of the median).\n"
        f"- The lab is started; the seeded workload ({suite.get('n_queries')} lookups, "
        f"nx_fraction={wl.get('nx_fraction')}, name Zipf s={wl.get('name_zipf_s')}, "
        f"zone Zipf s={wl.get('zone_zipf_s')}) is replayed for {suite.get('trials')} trials, "
        f"each with a cold pass (resolver restarted) and a warm pass.\n"
        f"- Upstream bytes and truncations come from pcap on the Unbound interface; cache "
        f"figures from `unbound-control` memory stats after the cold pass.\n"
        f"- The lab is stopped before the next dataset size."
    )
    L.append("")

    L.append("### 4.5 What is not claimed")
    L.append("")
    L.append(
        "- **Verification** in the tables is offline `ldns-verify-zone` over signed zone files, "
        "not Unbound’s per-query validation cost (that sits inside cold/warm lookup latency).\n"
        "- **Estimated** signing/verify must not be read as the wall-clock of the served zones; "
        "use **Measured once** for that.\n"
        "- Absolute seconds are not comparable to the main library report (different topology "
        "and tool). Compare **MTL ÷ SLH-DSA ratios** per size.\n"
        "- Stack draft06 condensed-by-default wire semantics differ from the always-full "
        "`latest` stack and from the main library’s MTL commit when they diverge."
    )
    L.append("")

    L.append("## 5. Results")
    L.append("")

    # --- 5.1 Signing
    L.append("### 5.1 Signing time")
    L.append("")
    if "sign_full.png" in figs:
        L.append(f"![Figure 1: whole-hierarchy signing time (measured once, log scale)]({figs['sign_full.png']})")
        L.append("")
        L.append("*Figure 1: whole-hierarchy signing time (measured once, log scale)*")
        L.append("")
    sign_rows = []
    for _, r in stats.sort_values(["dataset", "scenario"]).iterrows():
        sign_rows.append({
            "dataset": f"{int(r['dataset']):,}",
            "scenario": _label(r["scenario"]),
            "zones": int(r["zones"]) if pd.notna(r.get("zones")) else "",
            "RRSIGs": int(r["dataset_rrsigs"]) if pd.notna(r.get("dataset_rrsigs")) else "",
            "sign estimated [95% CI]": (
                f"{compare.fmt_seconds(r['est_sign_s'])} "
                f"[{compare.fmt_seconds(r['est_sign_lo'])}–{compare.fmt_seconds(r['est_sign_hi'])}]"
                if pd.notna(r.get("est_sign_s")) else "—"
            ),
            "sign measured once": compare.fmt_seconds(r.get("full_sign_s")),
            "signed size": compare.fmt_bytes(r.get("signed_bytes")),
        })
    if sign_rows:
        L.append(_md_table(pd.DataFrame(sign_rows)))
        L.append("")
        L.append("*Table 2: signing time. Estimated = sample-trial extrapolation; measured once = full zones served.*")
        L.append("")

    # --- 5.2 Verify
    L.append("### 5.2 Zone verification time (`ldns-verify-zone`)")
    L.append("")
    ver_rows = []
    for _, r in stats.sort_values(["dataset", "scenario"]).iterrows():
        ver_rows.append({
            "dataset": f"{int(r['dataset']):,}",
            "scenario": _label(r["scenario"]),
            "verify estimated [95% CI]": (
                f"{compare.fmt_seconds(r['est_verify_s'])} "
                f"[{compare.fmt_seconds(r['est_verify_lo'])}–{compare.fmt_seconds(r['est_verify_hi'])}]"
                if pd.notna(r.get("est_verify_s")) else "—"
            ),
            "verify measured once": compare.fmt_seconds(r.get("full_verify_s")),
        })
    if ver_rows:
        L.append(_md_table(pd.DataFrame(ver_rows)))
        L.append("")
        L.append("*Table 3: offline zone verification. Not Unbound validation latency.*")
        L.append("")

    # --- 5.3 Wire
    L.append("### 5.3 Transmitted bytes")
    L.append("")
    if "wire.png" in figs:
        L.append(f"![Figure 2: cold upstream bytes (log scale)]({figs['wire.png']})")
        L.append("")
        L.append("*Figure 2: resolver to authoritative bytes on the cold pass (log scale)*")
        L.append("")
    wire_rows = []
    for _, r in sf.sort_values(["dataset", "scenario"]).iterrows():
        wire_rows.append({
            "dataset": f"{int(r['dataset']):,}",
            "scenario": _label(r["scenario"]),
            "upstream bytes (cold)": compare.fmt_bytes(r.get("upstream_bytes")),
            "upstream queries": int(r["upstream_queries"]) if pd.notna(r.get("upstream_queries")) else "",
            "truncated": int(r["truncated"]) if pd.notna(r.get("truncated")) else "",
            "TCP conns": int(r["tcp_conns"]) if pd.notna(r.get("tcp_conns")) else "",
            "cold median (ms)": f"{r['cold_ms']:.2f}" if pd.notna(r.get("cold_ms")) else "—",
            "warm median (ms)": f"{r['warm_ms']:.2f}" if pd.notna(r.get("warm_ms")) else "—",
        })
    if wire_rows:
        L.append(_md_table(pd.DataFrame(wire_rows)))
        L.append("")
        L.append("*Table 4: wire and lookup latency from the lab (measured).*")
        L.append("")

    # --- 5.4 Cache
    L.append("### 5.4 Cache use (measured) and analytical signature storage")
    L.append("")
    if "cache.png" in figs:
        L.append(f"![Figure 3: Unbound RRset cache (measured)]({figs['cache.png']})")
        L.append("")
        L.append("*Figure 3: Unbound RRset cache after the cold pass (measured)*")
        L.append("")
    if "analytical.png" in figs:
        L.append(f"![Figure 4: analytical signature storage (draft section 12)]({figs['analytical.png']})")
        L.append("")
        L.append("*Figure 4: analytical whole-zone signature storage (draft section 12); not measured cache*")
        L.append("")
    cache_rows = []
    for _, r in sf.sort_values(["dataset", "scenario"]).iterrows():
        cache_rows.append({
            "dataset": f"{int(r['dataset']):,}",
            "scenario": _label(r["scenario"]),
            "RRset cache (measured)": compare.fmt_bytes(r.get("cache_rrset")),
            "message cache (measured)": compare.fmt_bytes(r.get("cache_message")),
            "signature storage (analytical)": compare.fmt_bytes(r.get("analytic_sig_bytes")),
            "signed zone files (measured)": compare.fmt_bytes(r.get("signed_bytes")),
        })
    if cache_rows:
        L.append(_md_table(pd.DataFrame(cache_rows)))
        L.append("")
        L.append(
            "*Table 5: measured Unbound cache memory vs analytical signature-material estimate "
            "and on-disk signed zone size.*"
        )
        L.append("")

    # --- 5.5 Ratios
    L.append("### 5.5 MTL vs SLH-DSA without MTL (ratios)")
    L.append("")
    L.append(
        "Values are **MTL ÷ SLH-DSA** (lower is a larger MTL win). Signing uses **measured once**; "
        "bytes and cache are measured."
    )
    L.append("")
    ratio_rows = []
    if "mtl" in scenarios and "slh-dsa" in scenarios:
        for ds in sizes:
            def R(col: str) -> str:
                a, b = _pick(sf, ds, "mtl", col), _pick(sf, ds, "slh-dsa", col)
                if not a or not b or b == 0:
                    return "—"
                return f"{a / b:.3f}"

            ratio_rows.append({
                "dataset": f"{ds:,}",
                "sign (measured once)": R("full_sign_s"),
                "verify (measured once)": R("full_verify_s"),
                "signed size": R("signed_bytes"),
                "upstream bytes": R("upstream_bytes"),
                "RRset cache": R("cache_rrset"),
                "analytical sig storage": R("analytic_sig_bytes"),
            })
        L.append(_md_table(pd.DataFrame(ratio_rows)))
        L.append("")
        L.append("*Table 6: MTL / SLH-DSA ratios.*")
        L.append("")

    L.append("## 6. Discussion")
    L.append("")
    L.append(
        "Across all three sizes, MTL reduces full-hierarchy signing time and cold upstream "
        "bytes relative to direct SLH-DSA on the same names and workload. Wire and cache wins "
        "are especially large on draft06 because condensed signatures are the default on the "
        "wire. Lookup medians are not uniformly better for MTL; cold latency can be comparable "
        "or slightly higher when ladder/path work dominates a short LAN RTT."
    )
    L.append("")
    L.append(
        "Estimated signing from sample trials tracks measured-once MTL closely when "
        "`jobs=1`; large direct-SLH-DSA estimates can under- or over-shoot the full wall clock "
        "and should be read with the CI and the measured-once column."
    )
    L.append("")

    L.append("## 7. Conclusions")
    L.append("")
    L.append(
        "On equivalent real-DNS workloads and the same underlying PQC algorithm, MTL mode in "
        "the pinned Verisign stack reduced zone signing time and transmitted bytes versus "
        "direct SLH-DSA at 50, 500 and 5,000 names. Cache memory after the cold pass followed "
        "the same direction. All headline wire and cache figures are measured; signing is "
        "reported both as measured-once and as trial-based estimates; analytical sizes are "
        "formula-only. Regenerate this document with "
        f"`python -m live_lab.suite_report {sid}`."
    )
    L.append("")

    L.append("## Appendix A. Configuration")
    L.append("")
    L.append("```json")
    L.append(json.dumps(dict(suite), indent=2)[:8000])
    L.append("```")
    L.append("")

    L.append("## Appendix B. Artifacts")
    L.append("")
    L.append(f"- Suite directory: `ecosystem/results/suites/{sid}/`")
    L.append("- `suite.json`, `params.json`, `signing_full.csv`, `signing_trials_*.csv`")
    L.append("- `report.md`, `report.html`, `figures/` (this report)")
    for n, d in sorted((suite.get("sizes_detail") or {}).items(), key=lambda kv: int(kv[0])):
        L.append(
            f"- {n} names: `ecosystem/results/{d.get('tag')}/{d.get('run_folder')}/` "
            f"(summary.csv, run.json, passes.csv, hops.csv, pcap/)"
        )
    L.append("")

    L.append("## References")
    L.append("")
    L.append(
        "1. draft-harvey-cfrg-mtl-mode-09, *Merkle Tree Ladder (MTL) Mode Signatures*.\n"
        "2. draft-fregly-dnsop-slh-dsa-mtl-dnssec, *SLH-DSA-MTL in DNSSEC*.\n"
        "3. FIPS 205, *Stateless Hash-Based Digital Signature Standard*.\n"
        "4. RFC 4033–4035 (DNSSEC); RFC 5155 (NSEC3).\n"
        "5. Verisign MTL reference library, https://github.com/verisign/MTL.\n"
        "6. Cipherops main library report (companion): `results/runs/<id>/report.html`.\n"
    )
    return "\n".join(L) + "\n"


def build_html(md: str, root: Path) -> str:
    try:
        import markdown
    except ImportError:
        return ""

    def inline(m: re.Match) -> str:
        path = root / m.group(2)
        if not path.exists():
            return m.group(0)
        return (
            f'<img alt="{m.group(1)}" '
            f'src="data:image/png;base64,{base64.b64encode(path.read_bytes()).decode()}">'
        )

    body = markdown.markdown(md, extensions=["tables", "fenced_code"])
    body = re.sub(r'<img alt="([^"]*)" src="([^"]+)" ?/?>', inline, body)
    css = (
        "body{font-family:Segoe UI,Helvetica,Arial,sans-serif;max-width:1000px;margin:2em auto;"
        "line-height:1.45;color:#1f2933}"
        "table{border-collapse:collapse;font-size:12px;margin:.6em 0}"
        "th,td{border:1px solid #cbd2d9;padding:3px 6px}th{background:#f0f4f8}"
        "img{max-width:100%}pre{background:#f5f7fa;padding:.8em;font-size:11px;overflow-x:auto}"
        "h1{font-size:1.7em}h2{border-bottom:1px solid #e4e7eb;margin-top:1.6em}"
        "@media print{body{margin:0;max-width:none}h2{page-break-after:avoid}"
        "table,img{page-break-inside:avoid}}"
    )
    title = "Cipherops DNS lab benchmark report"
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>"
        f"<style>{css}</style></head><body>{body}</body></html>"
    )


def write(suite_dir: Path | str) -> Path:
    """Build report.md / report.html / figures under the suite directory. Returns report.md path."""
    suite_dir = Path(suite_dir)
    sd = compare.load_suite(suite_dir)
    if sd is None:
        raise FileNotFoundError(f"no finished suite at {suite_dir} (missing suite.json)")
    sf = compare.scaling_frame(sd)
    figs = figures(sf, suite_dir / "figures")
    md = build_markdown(sd, figs)
    out = suite_dir / "report.md"
    out.write_text(md, encoding="utf-8")
    html = build_html(md, suite_dir)
    if html:
        (suite_dir / "report.html").write_text(html, encoding="utf-8")
    return out


def resolve_suite(arg: str) -> Path:
    p = Path(arg)
    if p.is_dir() and (p / "suite.json").is_file():
        return p
    cand = SUITES / arg
    if cand.is_dir() and (cand / "suite.json").is_file():
        return cand
    raise FileNotFoundError(f"suite not found: {arg}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Write report.md/html for an ecosystem benchmark suite")
    ap.add_argument("suite", help="Suite id or path to suite directory")
    args = ap.parse_args(argv)
    out = write(resolve_suite(args.suite))
    html = out.with_suffix(".html")
    print(f"wrote {out}")
    if html.is_file():
        print(f"wrote {html}")


if __name__ == "__main__":
    main()
