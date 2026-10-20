"""Reproducible report for one run directory: report.md, report.html, figures/.

Every number in the report is read from the run's own result files, so
`cipherops report --run DIR` regenerates an identical document.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pandas as pd
import yaml

from . import results
from .codepoints import EDNS_MTL_MODE_FULL, MODES
from .results import fmt_bytes, fmt_seconds

PSEUDOCODE = """\
# Signer: one MTL series per zone, built with the reference library
key = mtllib_key_new("SLH-DSA-SHA2-128s-MTL-SHA2-128")
for rrset in canonical_order(zone):                      # includes the NSEC3 chain
    data = rrsig_rdata_without_signature(rrset) + canonical_wire(rrset)  # RFC 4034 3.1.8.1
    leaf[rrset] = mtllib_sign_append(key, data)          # hashing only
ladder = mtllib_sign_get_signed_ladder(key)              # one SLH-DSA-SHA2-128s signature
for rrset in zone:
    rrsig[rrset].signature = MTL_TYPE_CONDENSED + mtllib_sign_get_condensed_sig(key, leaf[rrset])

# Validator
if ladder_cache is empty:                                # first contact only
    full = query(qname, qtype, edns=[MTL_MODE_FULL], over=TCP)
    mtllib_verify_signed_ladder(pubkey, full.ladder)     # one SLH-DSA verification
    ladder_cache = full.ladder
mtllib_verify(pubkey, data, condensed_signature, ladder_cache)   # hash path check
"""

TRACEABILITY = [
    ("One supported PQC signature algorithm", "SLH-DSA-SHA2-128s (FIPS 205) in every PQC mode", "§2, §4"),
    ("One mentor-tested MTL reference implementation", "Verisign MTL C library at a pinned commit; no own crypto", "§4, Appendix A"),
    ("Three dataset sizes", "small / medium / large synthetic zones", "§4.3, Table 2"),
    ("Equivalent workloads with and without MTL", "byte-identical RRsets and RFC 4034 signing inputs for every mode", "§4.3"),
    ("Repeated trials, pinned software and hardware", "N trials, one process per trial, interleaved order, pinned image, cpuset", "§4.1, §4.5"),
    ("Signing time", "measured per library call, whole-zone totals with 95% CIs", "§5.1"),
    ("Verification time", "warm (cached ladder) and cold (full signature)", "§5.2"),
    ("Transmitted bytes", "real DNS messages, 1232/4096 B buffers, TC + TCP fallback", "§5.3"),
    ("Record configuration and workload parameters", "config.yaml, environment.json, datasets table", "Appendix A–B"),
    ("Separate measured cache usage from analytical estimates", "separate tables, `kind` column", "§5.4"),
    ("Defer if implementation and fixtures unavailable", "go/no-go gate; run aborts with NO-GO", "§4.2, Appendix C"),
    ("No new crypto, resolver, Internet deployment, security proof", "none built or claimed", "§7"),
]


# ---------------------------------------------------------------- helpers

def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in row.tolist()) + " |")
    return "\n".join(lines)


def label(mk: str) -> str:
    return MODES[mk].label


def ci(row: pd.Series, col: str) -> str:
    if col not in row or pd.isna(row.get(col)):
        return "–"
    return f"{fmt_seconds(row[col])} [{fmt_seconds(row[col + '_lo'])}–{fmt_seconds(row[col + '_hi'])}]"


def ordered(df: pd.DataFrame, run: results.Run) -> pd.DataFrame:
    df = df.copy()
    df["_d"] = df["dataset"].map({d: i for i, d in enumerate(run.dataset_order)})
    if "mode" in df:
        df["_m"] = df["mode"].map({m: i for i, m in enumerate(run.mode_order)})
        df = df.sort_values(["_d", "_m"])
    else:
        df = df.sort_values("_d")
    return df.drop(columns=[c for c in ("_d", "_m") if c in df])


# ---------------------------------------------------------------- figures

def figures(run: results.Run, out: Path) -> dict[str, str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return {}
    out.mkdir(parents=True, exist_ok=True)
    colors = {"rsa-2048": "#9aa5b1", "ecdsa-p256": "#6b7c8f", "ed25519": "#3e4c59",
              "slh-dsa": "#d64545", "slh-dsa-mtl": "#2f9e44"}
    ds, modes = run.dataset_order, run.mode_order
    timing = results.timing_table(run.frames["trials"])
    made: dict[str, str] = {}

    def grouped(ax, table, value, lo=None, hi=None, scale=1.0, cats=None, key="dataset"):
        cats = cats or ds
        width = 0.8 / len(modes)
        for j, mk in enumerate(modes):
            sub = table[table["mode"] == mk].set_index(key).reindex(cats)
            x = [i + (j - (len(modes) - 1) / 2) * width for i in range(len(cats))]
            y = sub[value] * scale
            err = None
            if lo and hi:
                err = [(sub[value] - sub[lo]) * scale, (sub[hi] - sub[value]) * scale]
            bars = ax.bar(x, y, width, label=label(mk), color=colors.get(mk), yerr=err, capsize=2)
            if "extrapolated" in sub:
                for bar, extra in zip(bars, sub["extrapolated"].fillna(False)):
                    if extra:
                        bar.set_hatch("///")
                        bar.set_edgecolor("white")
        ax.set_xticks(range(len(cats)), cats)
        ax.set_yscale("log")
        ax.grid(axis="y", which="both", alpha=0.25)

    def save(fig, axes, name, extra_handles=()):
        handles, labels = axes[0].get_legend_handles_labels()
        for h in extra_handles:
            handles.append(h)
            labels.append(h.get_label())
        fig.legend(handles, labels, loc="lower center", ncol=3, fontsize=8, frameon=False)
        fig.tight_layout(rect=(0, 0.13, 1, 1))
        fig.savefig(out / f"{name}.png", dpi=150)
        plt.close(fig)
        made[name] = f"figures/{name}.png"

    from matplotlib.patches import Patch
    for key, col, title in (("sign", "sign_total_ns", "Whole-zone signing time"),
                            ("verify", "verify_warm_total_ns", "Whole-zone verification time (warm)")):
        t = timing.copy()
        t["extrapolated"] = t["limited"]
        ex_col = col.replace("_ns", "_extrapolated_ns")
        if ex_col in t:
            factor = t.dataset_rrsets / t.n_signed
            for c_src, c_dst in ((ex_col, col), (col + "_lo", col + "_lo"), (col + "_hi", col + "_hi")):
                vals = t[c_src] if c_src == ex_col else t[c_src] * factor
                t.loc[t.limited, c_dst] = vals[t.limited]
        fig, ax = plt.subplots(figsize=(9, 4.8))
        grouped(ax, t, col, col + "_lo", col + "_hi", 1e-9)
        ax.set_ylabel("seconds (log scale)")
        ax.set_title(f"{title}: median of {run.config['trials']} trials, 95% bootstrap CI")
        hatch = [Patch(facecolor="#d64545", hatch="///", edgecolor="white",
                       label="extrapolated from a measured sample")] if t.limited.any() else []
        save(fig, [ax], key, hatch)

    r = run.frames["responses"]
    big = ds[-1]
    r = r[(r.dataset == big) & (r.variant == "default") & (r.udp_limit == 1232)]
    agg = r.groupby(["mode", "record_class"]).response_bytes.agg(["median", "min", "max"]).reset_index()
    classes = [c for c in CLASS_ORDER if c in set(r.record_class)]
    fig, ax = plt.subplots(figsize=(9, 4.6))
    grouped(ax, agg, "median", "min", "max", cats=classes, key="record_class")
    ax.axhline(1232, ls=":", color="k")
    ax.text(len(classes) - 0.5, 1232 * 1.08, "1232 B", ha="right", fontsize=8)
    ax.set_ylabel("DNS response bytes (log scale)")
    ax.set_title(f"Response sizes by record class, {big} dataset (median, min–max; measured)")
    save(fig, [ax], "responses")

    w = run.frames["workload"]
    fig, ax = plt.subplots(figsize=(9, 4.6))
    grouped(ax, w[w.udp_limit == 1232], "dns_bytes")
    ax.set_ylabel("DNS bytes transmitted (log scale)")
    ax.set_title("Lookup workload, EDNS buffer 1232 B (measured message sizes)")
    save(fig, [ax], "workload")

    c = run.frames["cache"]
    c = c[c.scope == "resolver"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), sharey=True)
    for ax, kind in zip(axes, ("measured", "analytical")):
        grouped(ax, c[c.kind == kind], "signature_material_bytes")
        ax.set_title(f"Resolver cache signature material: {kind.upper()}")
    axes[0].set_ylabel("bytes (log scale)")
    save(fig, axes, "cache")

    live = live_summary(run)
    if live is not None:
        big = [d for d in ds if d in set(live.dataset)][-1]
        limits = sorted(live.udp_limit.unique())
        profiles = list(dict.fromkeys(live.profile))
        fig, axes = plt.subplots(1, len(limits), figsize=(5.5 * len(limits), 4.6), sharey=True, squeeze=False)
        for ax, lim in zip(axes[0], limits):
            grouped(ax, live[(live.dataset == big) & (live.udp_limit == lim)], "total_ms", "total_ms_min",
                    "total_ms_max", 1e-3, cats=profiles, key="profile")
            ax.set_title(f"EDNS buffer {lim} B", fontsize=10)
        axes[0][0].set_ylabel("seconds for the whole workload (log scale)")
        fig.suptitle(f"Live: real client and NSD, {big} dataset (median and range of "
                     f"{int(live.repeats.max())} repeats)", fontsize=11)
        save(fig, list(axes[0]), "live")
    return made


def live_summary(run: results.Run) -> pd.DataFrame | None:
    path = run.root / "live_summary.csv"
    if not path.exists():
        return None
    live = pd.read_csv(path)
    return live if "profile" in live else None


# ---------------------------------------------------------------- sections

CLASS_ORDER = ["apex", "small", "medium", "large", "nxdomain"]


def _pair(timing: pd.DataFrame, ds: str) -> tuple[pd.Series, pd.Series] | None:
    t = timing[timing.dataset == ds].set_index("mode")
    if not {"slh-dsa", "slh-dsa-mtl"} <= set(t.index):
        return None
    return t.loc["slh-dsa"], t.loc["slh-dsa-mtl"]


def _fit_by_mode(run: results.Run, ds: str) -> pd.Series:
    f = results.fit_table(run.frames["responses"])
    f = f[(f.dataset == ds) & (f.record_class != "apex")]
    return (f.fit_pct * f.responses).groupby(f["mode"]).sum() / f.responses.groupby(f["mode"]).sum()


def _headline(run: results.Run, timing: pd.DataFrame) -> list[str]:
    big = run.dataset_order[-1]
    if _pair(timing, big) is None:
        return []
    measured = [ds for ds in run.dataset_order if not _pair(timing, ds)[0].limited]
    out = []
    if measured:
        ds = measured[-1]
        d, m = _pair(timing, ds)
        out += [
            f"- **Signing ({ds} zone, {int(m.dataset_rrsets):,} RRsets, fully measured).** MTL signed the zone in "
            f"{ci(m, 'sign_total_ns')}; direct SLH-DSA took {ci(d, 'sign_total_ns')}. That is "
            f"{d.sign_total_ns / m.sign_total_ns:,.0f} times faster (median, 95% CI in brackets).",
            f"- **Verification ({ds} zone, warm).** MTL took {fmt_seconds(m.verify_warm_total_ns)} for every RRSIG in "
            f"the zone; direct SLH-DSA took {fmt_seconds(d.verify_warm_total_ns)}.",
        ]
    d, m = _pair(timing, big)
    if d.limited:
        out.append(f"- **Signing ({big} zone, {int(m.dataset_rrsets):,} RRsets).** MTL measured "
                   f"{fmt_seconds(m.sign_total_ns)}. Direct SLH-DSA signed {int(d.n_signed):,} sampled RRsets at "
                   f"{fmt_seconds(d.sign_per_rrset_ns)} each, which extrapolates to "
                   f"{fmt_seconds(d.get('sign_total_extrapolated_ns'))} for the whole zone *(extrapolated)*.")
    w = run.frames["workload"]
    w = w[(w.dataset == big) & (w.udp_limit == 1232)].set_index("mode")
    fits = _fit_by_mode(run, big)
    out += [
        f"- **Response size ({big} zone).** At a 1232-byte EDNS buffer, {fits['slh-dsa-mtl']:.0f}% of MTL responses "
        f"fit in UDP, compared with {fits['slh-dsa']:.0f}% for direct SLH-DSA.",
        f"- **Transmitted bytes ({big} zone).** The lookup workload transmitted "
        f"{fmt_bytes(w.loc['slh-dsa-mtl', 'dns_bytes'])} with MTL and {fmt_bytes(w.loc['slh-dsa', 'dns_bytes'])} "
        f"with direct SLH-DSA. {int(w.loc['slh-dsa', 'truncations'])} truncation and TCP-retry events became "
        f"{int(w.loc['slh-dsa-mtl', 'truncations'])}, plus {int(w.loc['slh-dsa-mtl', 'mtl_full_fetches'])} "
        f"full-signature fetch.",
    ]
    return out


def _discussion(run: results.Run) -> str:
    f = results.fit_table(run.frames["responses"])
    f = f[(f["mode"] == "slh-dsa-mtl") & (f.record_class != "apex")]
    sz = run.frames["trials"]
    sz = sz[sz["mode"] == "slh-dsa-mtl"].groupby("dataset").signature_bytes_max.max()
    lines = []
    for ds in run.dataset_order:
        g = f[f.dataset == ds].set_index("record_class")
        short = [f"{c} {g.loc[c, 'fit_pct']:.0f}%" for c in CLASS_ORDER if c in g.index and g.loc[c, "fit_pct"] < 100]
        if ds in sz.index:
            lines.append(f"  - {ds} zone (condensed signature {int(sz[ds])} B): "
                         + ("all record classes fit" if not short else "below 100%: " + ", ".join(short)) + ".")
    return ("- **Transmitted bytes.** Direct SLH-DSA responses never fit 1232 bytes, because one signature is "
            "7,856 bytes. MTL condensed signatures grow with log2 of the number of RRsets in the series. The "
            "MTL responses that still truncate at 1232 bytes are:\n" + "\n".join(lines) + "\n"
            "  - Large TXT answers exceed the limit even with classical signatures. An NXDOMAIN carries up to "
            "four RRSIGs (SOA and up to three NSEC3), so negative answers outgrow UDP first as the zone grows. "
            "Compact denial of existence, or splitting a zone into several MTL series, would reduce this; both "
            "are left as future work.\n")


def _intcols(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for c in cols:
        if c in df:
            df[c] = df[c].map(lambda v: "" if pd.isna(v) else f"{int(v):,}")
    return df


def build_markdown(run: results.Run, figs: dict[str, str]) -> str:
    F = run.frames
    timing = ordered(results.timing_table(F["trials"]), run)
    env = run.environment
    hw, sw = env.get("hardware", {}), env.get("software", {})
    cfg = run.config
    L: list[str] = []
    add = L.append

    def fig(key: str, caption: str) -> None:
        if key in figs:
            add(f"\n![{caption}]({figs[key]})\n\n*{caption}*\n")

    add("# Benchmarking MTL Mode for Post-Quantum DNSSEC Signatures")
    add("### PS-036 · PQC DNSSEC MTL Benchmark Using a Reference Implementation · IETF 126 Hackathon\n")
    add(f"Run `{run.root.name}` · go/no-go gate: **{run.preflight.get('verdict', 'missing')}** · "
        f"{cfg['trials']} trials · seed {cfg['seed']} · git `{env.get('git_sha', 'unknown')}`\n")

    add("## Abstract\n")
    add("Post-quantum signatures such as SLH-DSA-SHA2-128s are 7,856 bytes long. A DNSSEC response that carries "
        "one no longer fits the 1232-byte UDP size recommended for DNS, so resolvers fall back to TCP. Merkle "
        "Tree Ladder (MTL) mode (draft-harvey-cfrg-mtl-mode) signs a whole zone with one PQC signature over a "
        "Merkle ladder. Each RRset then carries only a short authentication path. We benchmark signing time, "
        "verification time, transmitted bytes and cache usage for DNSSEC zones of three sizes, comparing "
        "classical algorithms, direct SLH-DSA-SHA2-128s and SLH-DSA-SHA2-128s in MTL mode. All cryptography comes "
        "from the supplied Verisign MTL reference library, liboqs and OpenSSL, in a pinned container.\n")
    add("**Key results** (directly measured unless marked *extrapolated*):\n")
    L.extend(_headline(run, timing))

    add("\n## 1. Problem statement\n")
    add("DNSSEC relies on UDP, and on responses that fit a single unfragmented datagram (1232 bytes since DNS "
        "Flag Day 2020). Post-quantum signature schemes standardised by NIST have signatures that are one to "
        "two orders of magnitude larger than RSA or ECDSA. PS-036 asks for a reproducible measurement of what "
        "MTL mode changes for DNSSEC, compared with using the same PQC algorithm directly, using a "
        "reference implementation rather than new cryptography.\n")

    add("## 2. Background\n")
    add("- **DNSSEC** (RFC 4033–4035) signs each RRset. The RRSIG signature covers the RRSIG RDATA and the "
        "canonical RRset (RFC 4034 §3.1.8.1). NSEC3 (RFC 5155) proves non-existence.\n"
        "- **SLH-DSA-SHA2-128s** (FIPS 205) is a stateless hash-based signature: 32-byte public key, "
        "7,856-byte signature.\n"
        "- **MTL mode** (draft-harvey-cfrg-mtl-mode-09) appends each message to a Merkle node set:\n"
        "  - A *ladder* of rungs covering all messages is signed once with the underlying scheme.\n"
        "  - A *condensed signature* is a short authentication path from one message to a rung.\n"
        "  - A *full signature* is the condensed signature plus the signed ladder.\n"
        "  - A verifier that has cached a ladder checks only hashes.\n"
        "- **DNSSEC MTL mode** (draft-fregly-dnsop-slh-dsa-mtl-dnssec) prefixes the RRSIG signature with an "
        "MTL-Type byte. Validators request full signatures with an EDNS option, preferably over TCP.\n")

    add("## 3. Objectives and scope\n")
    add("Compare equivalent DNSSEC signing workloads with and without MTL, on three dataset sizes, measuring "
        "signing time, verification time and transmitted bytes. Report cache usage, keeping direct "
        "measurements separate from analytical estimates. Classical algorithms are included as a reference "
        "point. Out of scope, per PS-036: new cryptography, a resolver implementation, Internet "
        "deployment and any security proof.\n")
    add(md_table(pd.DataFrame(TRACEABILITY, columns=["PS-036 requirement", "How it is met", "Where"])))

    add("\n## 4. Methodology\n")
    add("### 4.1 Pinned environment\n")
    add(md_table(pd.DataFrame([
        ("CPU", hw.get("cpu_model")), ("CPU affinity (cpuset)", str(hw.get("cpu_affinity"))),
        ("Memory", hw.get("mem_total")), ("Kernel", sw.get("kernel")),
        ("OS", "; ".join(sw.get("os_release", []))[:120]), ("Compiler", sw.get("gcc")),
        ("OpenSSL", sw.get("openssl")), ("liboqs", sw.get("liboqs_tag")),
        ("MTL reference commit", sw.get("mtl_reference_commit")),
        ("MTL compile flags", sw.get("mtl_reference_cflags")), ("Python", sw.get("python")),
        ("Container image", env.get("host_image_id")), ("Harness sha256", str(sw.get("harness_sha256"))[:16] + "…"),
    ], columns=["Item", "Value"])))
    add("\n### 4.2 Go/no-go gate\n")
    add("PS-036 requires deferring the project if the implementation or its fixtures are not available. Before "
        "any measurement, the runner checks:\n\n"
        "1. The reference library is installed.\n"
        "2. Its own unit tests and test vectors pass.\n"
        "3. Its key-generation, signing and verification tools round-trip with SLH-DSA-SHA2-128s-MTL, and a "
        "tampered message is rejected.\n"
        "4. Every harness mode verifies its own output and rejects tampering.\n\n"
        "The run aborts with `NO-GO` otherwise. The outcome of each check is listed in Appendix C.\n")
    add("### 4.3 Workloads\n")
    add("Each dataset is a deterministic synthetic zone `bench.example.`:\n\n"
        "- **small (60% of names):** one A or AAAA record\n"
        "- **medium (30%):** three MX or four AAAA records\n"
        "- **large (10%):** one 1–2 KB TXT record\n\n"
        "The zone also has the apex SOA, NS, DNSKEY and NSEC3PARAM, and a complete NSEC3 chain (0 iterations, "
        "RFC 9276). Each RRset is one signing message. The RFC 4034 signing inputs are produced by "
        "dnspython; a test checks that dnspython's standard validator accepts signatures over these exact bytes. "
        "Every mode signs the same RRsets with fixed inception and expiration times. The only differences "
        "between modes are the RRSIG algorithm number, key tag and DNSKEY, which belong to the signed data.\n")
    ds_df = ordered(F["datasets"], run).fillna(0)
    cols = ["dataset", "n_names", "rrsets"] + [c for c in CLASS_ORDER + ["nsec3"] if c in ds_df] + ["signing_input_bytes"]
    ds_df = _intcols(ds_df[cols], [c for c in cols if c not in ("dataset", "signing_input_bytes")])
    add(md_table(ds_df.assign(signing_input_bytes=ordered(F["datasets"], run).signing_input_bytes.map(fmt_bytes).values)))
    add("\n*Table 2: datasets.*\n")
    add("### 4.4 Modes\n")
    add(md_table(pd.DataFrame([(label(m), MODES[m].harness_mode, MODES[m].harness_algo, MODES[m].dnssec_alg)
                               for m in run.mode_order],
                              columns=["Mode", "Harness path", "Library algorithm", "DNSSEC alg #"])))
    add(f"\nThe DNSSEC algorithm numbers for SLH-DSA (direct and MTL) and the EDNS option `mtl-mode-full` "
        f"(code {EDNS_MTL_MODE_FULL}) are lab placeholders.\n")
    add("### 4.5 Measurement procedure\n")
    add(f"- Each trial runs one harness process per mode and dataset. The process links the reference library, "
        f"liboqs and OpenSSL, and times each library call with `CLOCK_MONOTONIC_RAW`.\n"
        f"- Modes run in a seeded random order inside each trial, after "
        f"{cfg.get('warmup_trials', 1)} warm-up trial(s). There are {cfg['trials']} measured trials.\n"
        f"- **MTL signing (whole zone)** = append every RRset + sign the ladder once + extract every condensed "
        f"signature.\n"
        f"- **MTL warm verification** = verify the ladder once + verify every condensed signature against it. The "
        f"verifier is built from the exported public key only.\n"
        f"- **MTL cold verification** = full signature without a cached ladder, on "
        f"{cfg['full_verify_sample']} RRsets.\n"
        f"- **Correctness.** A trial counts only if every signature verifies and tampered messages, "
        f"signatures and ladders are rejected.\n"
        f"- **Statistics.** We report the median across trials with a bootstrap 95% confidence interval "
        f"(2,000 resamples).\n"
        f"- **Transmitted bytes.** Measured on real DNS messages encoded by dnspython, using the signatures "
        f"from trial 0.\n")

    add("## 5. Results\n")
    add("### 5.1 Signing time\n")
    fig("sign", "Figure 1: whole-zone signing time (log scale)")
    rows = []
    for _, r in timing.iterrows():
        rows.append({"dataset": r.dataset, "mode": label(r["mode"]), "RRsets signed": f"{r.n_signed:,}/{r.dataset_rrsets:,}",
                     "sign zone, median [95% CI]": ci(r, "sign_total_ns"),
                     "per RRset": fmt_seconds(r.sign_per_rrset_ns),
                     "zone total (extrapolated)": fmt_seconds(r.get("sign_total_extrapolated_ns")) if r.limited else "",
                     "keygen": fmt_seconds(r.get("keygen_ns"))})
    add(md_table(pd.DataFrame(rows)))
    add("\n*Table 3: signing time.*\n")
    mt = F["trials"][F["trials"]["mode"] == "slh-dsa-mtl"]
    if len(mt):
        parts = ordered(mt.groupby("dataset")[["sign_append_total_ns", "ladder_sign_ns",
                                               "condensed_extract_total_ns"]].median().reset_index(), run)
        add("MTL signing time breakdown (medians):\n")
        add(md_table(pd.DataFrame({"dataset": parts.dataset,
                                   "append all RRsets": parts.sign_append_total_ns.map(fmt_seconds),
                                   "sign ladder (1 SLH-DSA)": parts.ladder_sign_ns.map(fmt_seconds),
                                   "extract condensed sigs": parts.condensed_extract_total_ns.map(fmt_seconds)})))

    add("\n### 5.2 Verification time\n")
    fig("verify", "Figure 2: whole-zone verification time with a cached ladder (log scale)")
    rows = []
    for _, r in timing.iterrows():
        rows.append({"dataset": r.dataset, "mode": label(r["mode"]),
                     "verify zone (warm), median [95% CI]": ci(r, "verify_warm_total_ns"),
                     "per RRset (warm)": fmt_seconds(r.verify_per_rrset_ns),
                     "one signature, cold": fmt_seconds(r.get("verify_full_median_ns")),
                     "checks passed": "yes" if r.checks_passed else "**NO**"})
    add(md_table(pd.DataFrame(rows)))
    add("\n*Table 4: verification time. For MTL, \"cold\" means a full signature verified without a cached "
        "ladder; for other modes it is the median single-signature verification.*\n")

    add("### 5.3 Transmitted bytes\n")
    sz = F["trials"].groupby(["dataset", "mode"]).agg(sig_median=("signature_bytes_median", "median"),
                                                      sig_max=("signature_bytes_max", "max"),
                                                      pub=("public_key_bytes", "first")).reset_index()
    if "signed_ladder_bytes" in F["trials"]:
        lad = F["trials"].groupby(["dataset", "mode"]).signed_ladder_bytes.max().reset_index()
        sz = sz.merge(lad, on=["dataset", "mode"], how="left")
    sz = ordered(sz, run)
    add(md_table(pd.DataFrame({"dataset": sz.dataset, "mode": sz["mode"].map(label),
                               "signature bytes (median / max)": [f"{a:,.0f} / {b:,}" for a, b in zip(sz.sig_median, sz.sig_max)],
                               "signed ladder bytes": sz.get("signed_ladder_bytes", pd.Series(dtype=float)).map(
                                   lambda v: "" if pd.isna(v) else f"{int(v):,}"),
                               "public key bytes": sz.pub})))
    add("\n*Table 5: signature sizes measured on library output. The MTL RRSIG field adds a 1-byte MTL-Type "
        "prefix.*\n")
    fig("responses", "Figure 3: DNS response sizes by record class, measured")
    f = results.fit_table(F["responses"])
    f = f[f.record_class != "apex"]
    piv = f.pivot_table(index=["dataset", "record_class"], columns="mode", values="fit_pct").round(1)
    piv = piv.reindex(pd.MultiIndex.from_product([run.dataset_order, [c for c in CLASS_ORDER if c != "apex"]]))
    piv = piv.dropna(how="all")
    piv.index.names = ["dataset", "record_class"]
    piv = piv[[m for m in run.mode_order if m in piv.columns]].rename(columns=label).reset_index()
    add(md_table(piv))
    add("\n*Table 6: percentage of responses that fit a 1232-byte EDNS UDP buffer without truncation.*\n")
    fig("workload", "Figure 4: total DNS bytes for the lookup workload at a 1232-byte buffer")
    w = ordered(F["workload"], run)
    add(md_table(pd.DataFrame({"dataset": w.dataset, "mode": w["mode"].map(label), "UDP buffer": w.udp_limit,
                               "responses": w.transmitted_responses, "DNS bytes": w.dns_bytes.map(fmt_bytes),
                               "of which RRSIG sig bytes": w.rrsig_signature_bytes.map(fmt_bytes),
                               "TC → TCP": w.truncations, "MTL full fetches": w.mtl_full_fetches,
                               "round trips (est.)": w.round_trips_est})))
    wl = cfg["wire"]["workload"]
    add(f"\n*Table 7: workload accounting. Workload: {wl['n_queries']} lookups following a Zipf(s={wl['zipf_s']}) "
        f"distribution, with {wl['nx_fraction']:.0%} going to non-existent names. Assumptions:*\n\n"
        "- *A cached answer is not re-sent.*\n"
        "- *An oversize answer costs a UDP query, a TC=1 reply and a TCP retry.*\n"
        "- *An MTL validator fetches one full signature over TCP on first contact.*\n\n"
        "*Byte counts are measured; round-trip and packet counts are analytical.*\n")

    add("### 5.4 Cache usage: measured\n")
    fig("cache", "Figure 5: resolver cache signature material. Left: measured. Right: analytical.")
    c = ordered(F["cache"], run)
    cm = c[(c.scope == "resolver") & (c.kind == "measured")]
    cm = _intcols(cm, ["cached_responses", "cached_rrsigs"])
    add(md_table(pd.DataFrame({"dataset": cm.dataset, "mode": cm["mode"].map(label),
                               "cached responses": cm.cached_responses, "RRSIGs": cm.cached_rrsigs,
                               "wire bytes": cm.cached_wire_bytes.map(fmt_bytes),
                               "signature bytes": cm.cached_signature_bytes.map(fmt_bytes),
                               "ladder bytes": cm.ladder_bytes.map(fmt_bytes),
                               "Python heap retained": cm.python_heap_bytes.map(fmt_bytes)})))
    add("\n*Table 8: measured. The cached responses are those transmitted in Table 7, parsed from wire and stored "
        "in dnspython's `LRUCache`. Heap figures come from tracemalloc.*\n")
    sm = c[(c.scope == "signer") & (c.kind == "measured")]
    if len(sm):
        add(md_table(pd.DataFrame({"dataset": sm.dataset,
                                   "MTL signer RSS growth while appending": sm.node_set_rss_growth_bytes.map(fmt_bytes),
                                   "max RSS": sm.maxrss_bytes.map(fmt_bytes)})))
        add("\n*Table 9: measured signer state of the reference library process (`/proc/self/statm`, page-granular, "
            "so growth below a few pages is within measurement resolution).*\n")
    add("### 5.5 Cache usage: analytical estimates\n")
    ca = _intcols(c[(c.scope == "resolver") & (c.kind == "analytical")], ["cached_rrsigs", "per_signature_bytes"])
    add(md_table(pd.DataFrame({"dataset": ca.dataset, "mode": ca["mode"].map(label), "RRSIGs": ca.cached_rrsigs,
                               "max bytes per signature": ca.per_signature_bytes,
                               "ladder bytes (max)": ca.ladder_bytes.map(fmt_bytes),
                               "signature material": ca.signature_material_bytes.map(fmt_bytes)})))
    sa = c[(c.scope == "signer") & (c.kind == "analytical")]
    if len(sa):
        add("\n" + md_table(pd.DataFrame({"dataset": sa.dataset,
                                          "node-set hash storage (2N−1)·n + N·n": sa.node_set_hash_bytes.map(fmt_bytes)})))
    add("\n*Table 10: analytical estimates from the draft-harvey section 12 size formulas, for the same RRSIG "
        "counts as Table 8. These are estimates, not measurements.*\n")
    add("### 5.6 MTL sizes compared with the draft formulas\n")
    s = ordered(F["sizes"], run)
    add(md_table(s.assign(bytes=s["bytes"].map(lambda v: f"{int(v):,}"))))
    add("\n*Table 11: measured MTL sizes next to the section 12 maxima.*\n")

    add("## 6. Discussion\n")
    add("- **Signing.** Direct SLH-DSA performs one expensive hash-based signature per RRset. MTL performs one "
        "per zone, for the ladder, plus hashing per RRset. The gap therefore grows with zone size.\n"
        "- **Verification.** With a cached ladder, MTL checks only an authentication path for each RRset. The "
        "SLH-DSA verification is paid once per ladder.\n"
        + _discussion(run) +
        "- **Cost of MTL.** It moves cost to first contact, where one full signature (the ladder) is "
        "fetched over TCP. A ladder covers a zone version: re-signing after a change produces a new ladder "
        "that validators must fetch again.\n")
    add("## 7. Limitations\n")
    kernel = str(sw.get("kernel", ""))
    wsl = ("On Docker Desktop (WSL2), `cpuset` pins the container to one *virtual* CPU, which the host may "
           "schedule on different physical cores, including efficiency cores on hybrid CPUs. This widens "
           "run-to-run spread; the confidence intervals reflect it. " if "microsoft" in kernel.lower() else "")
    add("- **Timing scope.** CPU timings are for one pinned core on the hardware recorded above, with the "
        "pinned library builds. They are not general performance claims. " + wsl + "\n"
        "- **Transport model.** Transmitted bytes come from a message-level model, not a resolver or server. "
        "Real resolvers differ in caching, retries and EDNS behaviour.\n"
        "- **Placeholder code points.** The algorithm numbers and the EDNS option code are not IANA-assigned.\n"
        "- **Fixture coverage.** The reference library's bundled known-answer vectors cover the scheme named in "
        "Appendix C. SLH-DSA-SHA2-128s-MTL is validated by the tool round trip and the harness gates.\n"
        "- **Compiler flags.** The MTL library is compiled with optimisation (see methodology). The as-shipped "
        "debug build is available as an ablation.\n"
        "- **No security claims.** This work makes no security claims and no new cryptography is introduced.\n")
    add("## 8. Conclusion\n")
    fits = _fit_by_mode(run, run.dataset_order[-1])
    fit_txt = ""
    if {"slh-dsa", "slh-dsa-mtl"} <= set(fits.index):
        fit_txt = (f" In the {run.dataset_order[-1]} zone, the share of responses that fit a 1232-byte UDP "
                   f"buffer rose from {fits['slh-dsa']:.0f}% to {fits['slh-dsa-mtl']:.0f}%.")
    add("On equivalent DNSSEC workloads and the same underlying PQC algorithm, MTL mode in the reference "
        "implementation replaces per-RRset SLH-DSA signatures with short authentication paths. In this "
        "environment it reduced zone signing time, warm verification time and transmitted bytes." + fit_txt +
        " The costs are one full-signature fetch per validator for each zone version, and negative answers "
        "that carry several condensed signatures grow with zone size. All figures are measured on the pinned "
        "environment, except those explicitly marked as extrapolated or analytical, and are reproducible "
        "with the commands below.\n")
    add("## 9. Reproducing this report\n")
    add("```bash\n"
        "docker compose -f docker/compose.yml build              # pinned image\n"
        "scripts/run_benchmark.sh preflight                     # go/no-go gate\n"
        "scripts/run_benchmark.sh configs/scenarios.yaml        # full run -> results/runs/<id>/\n"
        f"python -m cipherops.runner report --run results/runs/{run.root.name}\n"
        "streamlit run dashboard/app.py                         # interactive dashboard / demo\n"
        "```\n")
    add("### Demonstration pseudocode\n")
    add("```python\n" + PSEUDOCODE + "```\n")

    add("## Appendix A. Configuration\n")
    add("```yaml\n" + yaml.safe_dump(cfg, sort_keys=False) + "```\n")
    add("## Appendix B. Environment\n")
    add("```json\n" + json.dumps(env, indent=2) + "\n```\n")
    add("## Appendix C. Go/no-go gate\n")
    checks = pd.DataFrame(run.preflight.get("checks", []))
    if len(checks):
        checks["detail"] = checks["detail"].map(lambda s: re.sub(r"\s+", " ", str(s))[:160])
        add(md_table(checks))
    cx_path = run.root / "crosscheck.csv"
    if cx_path.exists():
        from .crosscheck import summary as cx_summary
        cx = pd.read_csv(cx_path)
        add("\n## Appendix D. Byte model cross-checked against a real authoritative server\n")
        add(f"The {cx.dataset.iloc[0]} zone was written as a signed zone file, using the real RRSIGs from this run, "
            "and served by an unmodified NSD inside the container. Sampled queries were sent over TCP and over "
            "UDP with 1232- and 4096-byte buffers. Each NSD response size is compared with the dnspython-encoded "
            "response used in §5.3, and the TC bit at 1232 bytes is compared with the model's fit decision. "
            "MTL full signatures are requested with an EDNS option that NSD does not implement, so only "
            "condensed responses are checked.\n")
        s = cx_summary(cx)
        s["mode"] = s["mode"].map(label)
        add(md_table(s.round(1)))
        add("\n*Table D1: positive delta = NSD response larger than the model.*\n")

    live = live_summary(run)
    if live is not None:
        meta_path = run.root / "live.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        add("\n## Appendix E. Live: real client, real authoritative server, simulated networks\n")
        prof_text = "; ".join(f"**{k}**: {v['label']}" for k, v in meta.get("profiles", {}).items())
        add(f"Each mode's signed zone was served by an unmodified NSD ({meta.get('nsd_version', 'NSD')}) inside the "
            "container. A client sent the lookup workload as real packets: UDP first, a resend when no answer "
            f"arrived in time (up to {meta.get('udp_tries', 4)} tries), and a TCP retry when the answer came back "
            "truncated. An MTL client fetched one full signature over TCP on first contact, from a second NSD whose "
            "zone holds full signatures (NSD does not implement the mtl-mode-full option). Network conditions were "
            f"set with Linux tc-netem on the loopback interface ({prof_text}). The loopback MTU was "
            f"{meta.get('mtu') or 'left at its default'}, so large UDP answers were IP-fragmented and TCP answers "
            "segmented as on Ethernet. Each cell was repeated "
            f"{int(live.repeats.max())} times; packets of the first repetition are in `pcap/` and open in "
            "Wireshark. Signatures are not validated by the client.\n")
        caps = meta.get("captures", {})
        if caps:
            dropped = sum(c.get("dropped_by_kernel", 0) for c in caps.values())
            add(f"{len(caps)} captures, {sum(c.get('captured', 0) for c in caps.values()):,} packets, "
                f"{dropped} dropped by the kernel. `scripts/pcap_bytes.py` compares the DNS payload in each pcap "
                "with the client's count; they differ only where netem dropped a packet after the client sent it.\n")
        fig("live", "Figure E1. Live workload time per network profile (median, range across repeats).")
        lan = live[live.profile == live.profile.iloc[0]].copy()
        lan["mode"] = lan["mode"].map(label)
        lan["model_delta_pct"] = (100 * (lan.live_bytes - lan.model_bytes) / lan.model_bytes).round(1)
        add(md_table(_intcols(lan[["dataset", "udp_limit", "mode", "sent", "udp_only", "tc_then_tcp",
                                   "ladder_fetches", "live_bytes", "model_bytes", "model_delta_pct"]],
                              ["sent", "udp_only", "tc_then_tcp", "ladder_fetches", "live_bytes", "model_bytes"])))
        add(f"\n*Table E1: bytes on the wire with the {live.profile.iloc[0]} profile, live against the "
            "message-level model of §5.3. NSD's truncated answers carry the records that fit, while the model "
            "assumes an empty TC=1 answer, so modes that truncate often (direct SLH-DSA, MTL, RSA) count more "
            "live bytes.*\n")
        big = [d for d in run.dataset_order if d in set(live.dataset)][-1]
        t = live[live.dataset == big].copy()
        t["mode"] = t["mode"].map(label)
        t["workload_s"] = t.apply(lambda r: f"{r.total_ms / 1e3:.2f} ({r.total_ms_min / 1e3:.2f}–"
                                            f"{r.total_ms_max / 1e3:.2f})", axis=1)
        t["lookup_median_ms"] = t.median_ms.round(2)
        t["lookup_p95_ms"] = t.p95_ms.round(2)
        add(md_table(_intcols(t[["profile", "udp_limit", "mode", "workload_s", "lookup_median_ms", "lookup_p95_ms",
                                 "tc_then_tcp", "udp_retries", "failed", "live_bytes"]],
                              ["tc_then_tcp", "udp_retries", "failed", "live_bytes"])))
        add(f"\n*Table E2: {big} dataset. Workload time is the median (min–max) across repeats; per-lookup "
            "times are medians across repeats; resends and failures are summed over all repeats; live bytes "
            "include resent queries.*\n")

    add("\n## References\n")
    add("1. draft-harvey-cfrg-mtl-mode-09, *Merkle Tree Ladder (MTL) Mode Signatures*.\n"
        "2. draft-fregly-dnsop-slh-dsa-mtl-dnssec, *SLH-DSA-MTL in DNSSEC*.\n"
        "3. FIPS 205, *Stateless Hash-Based Digital Signature Standard*.\n"
        "4. RFC 4033, RFC 4034, RFC 4035 (DNSSEC); RFC 5155 (NSEC3); RFC 9276 (NSEC3 guidance); RFC 6605 (ECDSA); "
        "RFC 8080 (Ed25519).\n"
        "5. Verisign MTL reference library, https://github.com/verisign/MTL.\n"
        "6. Open Quantum Safe liboqs, https://github.com/open-quantum-safe/liboqs.\n")
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
        return f'<img alt="{m.group(1)}" src="data:image/png;base64,{base64.b64encode(path.read_bytes()).decode()}">'

    body = markdown.markdown(md, extensions=["tables", "fenced_code"])
    body = re.sub(r'<img alt="([^"]*)" src="([^"]+)" ?/?>', inline, body)
    css = ("body{font-family:Segoe UI,Helvetica,Arial,sans-serif;max-width:1000px;margin:2em auto;line-height:1.45;"
           "color:#1f2933}table{border-collapse:collapse;font-size:12px;margin:.6em 0}th,td{border:1px solid #cbd2d9;"
           "padding:3px 6px}th{background:#f0f4f8}img{max-width:100%}pre{background:#f5f7fa;padding:.8em;"
           "font-size:11px;overflow-x:auto}h1{font-size:1.7em}h2{border-bottom:1px solid #e4e7eb;margin-top:1.6em}"
           "@media print{body{margin:0;max-width:none}h2{page-break-after:avoid}table,img{page-break-inside:avoid}}")
    return f"<!doctype html><html><head><meta charset='utf-8'><title>Cipherops report</title><style>{css}</style></head><body>{body}</body></html>"


def write(root: Path) -> Path:
    root = Path(root)
    run = results.load(root)
    figs = figures(run, root / "figures")
    md = build_markdown(run, figs)
    out = root / "report.md"
    out.write_text(md, encoding="utf-8")
    html = build_html(md, root)
    if html:
        (root / "report.html").write_text(html, encoding="utf-8")
    return out
