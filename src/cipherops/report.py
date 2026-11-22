"""Reproducible report for one run directory: report.md, report.html, figures/.

Every number in the report is read from the run's own result files, so
`cipherops report --run DIR` regenerates an identical document.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import evolution, results, stats
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
        ax.set_title(f"{title}: median of {run.config['trials']} trials, bars = {stats.ci_label(int(run.config['trials']))}")
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

    if len(run.frames.get("evolution", [])) and len(run.frames.get("evolution_direct", [])):
        mtl_e, dir_e = run.frames["evolution"], run.frames["evolution_direct"]
        cost = evolution.update_cost(mtl_e, dir_e)
        cov = evolution.coverage_table(mtl_e)
        ks = sorted(cost.k.unique())
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.6))
        ax = axes[0]
        width = 0.8 / (1 + len(ds))
        series = [("Direct SLH-DSA (k signatures)", cost[cost.dataset == ds[0]].set_index("k").direct_round_ns, colors["slh-dsa"])]
        shade = ["#7fcf8f", "#2f9e44", "#1b6b2e"]
        series += [(f"MTL, {d} zone (1 ladder signature)", cost[cost.dataset == d].set_index("k").mtl_round_ns,
                    shade[min(i, 2)]) for i, d in enumerate(ds)]
        for j, (name, vals, col) in enumerate(series):
            xs = [i + (j - (len(series) - 1) / 2) * width for i in range(len(ks))]
            ax.bar(xs, [vals.get(k, np.nan) / 1e9 for k in ks], width, label=name, color=col)
        ax.set_xticks(range(len(ks)), [f"k = {k}" for k in ks])
        ax.set_yscale("log")
        ax.grid(axis="y", which="both", alpha=0.25)
        ax.set_ylabel("seconds per update round (log scale)")
        ax.set_title("Cost of one update round", fontsize=10)
        ax.legend(fontsize=7, frameon=False)
        ax = axes[1]
        d0 = cov[cov.dataset == ds[-1]]
        for k, g in d0.groupby("k"):
            ax.plot(g.age, g.share * 100, marker="o", label=f"k = {k}")
        ax.set_xticks(sorted(d0.age.unique()))
        ax.set_xlabel("age of the resolver's cached ladder (rounds)")
        ax.set_ylabel("answers still verifying (%)")
        ax.set_ylim(0, 102)
        ax.set_title(f"Condensed signatures against an older ladder, {ds[-1]} zone", fontsize=10)
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8, frameon=False)
        fig.tight_layout()
        fig.savefig(out / "evolution.png", dpi=150)
        plt.close(fig)
        made["evolution"] = "figures/evolution.png"

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


def fmt_ratio(r: pd.Series) -> str:
    """'123× [118×, 130×]': median of the per-trial ratios and its interval."""
    if r is None or pd.isna(r.ratio):
        return "–"
    digits = 0 if r.ratio >= 100 else (1 if r.ratio >= 10 else 2)
    f = lambda v: f"{v:,.{digits}f}×"  # noqa: E731
    return f"{f(r.ratio)} [{f(r.ratio_lo)}, {f(r.ratio_hi)}]"


def _floor(run: results.Run) -> pd.DataFrame:
    mode = run.config.get("control")
    if not mode or "control" not in run.frames:
        return pd.DataFrame()
    return results.noise_floor_table(run.frames["trials"], run.frames["control"], mode)


def _paired(run: results.Run, ds: str, metric: str) -> pd.Series | None:
    t = results.paired_ratio_table(run.frames["trials"], "slh-dsa", "slh-dsa-mtl")
    t = t[(t.dataset == ds) & (t.metric == metric)] if len(t) else t
    return t.iloc[0] if len(t) else None


def _headline(run: results.Run, timing: pd.DataFrame) -> list[str]:
    big = run.dataset_order[-1]
    if _pair(timing, big) is None:
        return []
    measured = [ds for ds in run.dataset_order if not _pair(timing, ds)[0].limited]
    out = []
    if measured:
        ds = measured[-1]
        d, m = _pair(timing, ds)
        sp = _paired(run, ds, "signing")
        out += [
            f"- **Signing ({ds} zone, {int(m.dataset_rrsets):,} RRsets, fully measured).** MTL signed the zone in "
            f"{ci(m, 'sign_total_ns')}; direct SLH-DSA took {ci(d, 'sign_total_ns')}. MTL was "
            f"{fmt_ratio(sp)} faster (median of the per-trial ratios, interval in brackets).",
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


def _noise_section(run: results.Run, floor: pd.DataFrame) -> str:
    out: list[str] = []
    if not floor.empty:
        mode = run.config["control"]
        out.append(f"#### Measurement noise: the A/A control\n\n"
                   f"`{mode}` ran twice inside every trial, on the same input, as if it were two different modes. "
                   "The true ratio is 1, so the table below is the measurement's own noise. A difference between "
                   "two modes that is smaller than this band must not be reported as a difference.\n")
        fl = floor.assign(order=floor.dataset.map({d: i for i, d in enumerate(run.dataset_order)})).sort_values(["metric", "order"])
        out.append(md_table(pd.DataFrame({
            "dataset": fl.dataset, "metric": fl.metric, "trials": fl.trials,
            "A/A ratio, median [interval]": [f"{r.ratio:.3f} [{r.ratio_lo:.3f}, {r.ratio_hi:.3f}]" for _, r in fl.iterrows()],
            "noise band": [f"±{b * 100:.1f}%" for b in fl.band]})))
        out.append("\n*Table 4c: A/A control (one mode against its own copy).*\n")
    tm = run.environment.get("timer") or {}
    if tm.get("operations"):
        er = tm["empty_region"]
        out.append("#### Measurement noise: the timer\n\n"
                   f"Every library call is timed with `{tm['clock']}`. An empty timed region cost "
                   f"{er['median_ns']} ns (median; p99 {er['p99_ns']} ns), and the smallest step the clock was seen "
                   f"to take is {tm['smallest_observed_step_ns']} ns. The two fastest operations were timed both "
                   "one call at a time (as in the benchmark) and as 1,000 calls in one region divided by 1,000:\n")
        rows = [{"operation": name.replace("_", " "), "per call, median": fmt_seconds(o["per_call_median_ns"]),
                 "batch of 1,000, per call": fmt_seconds(o["batch_per_call_median_ns"]),
                 "difference": f"{o['per_call_vs_batch_pct']:+.1f}%",
                 "timer cost as share of the operation": f"{o['timer_share_of_operation'] * 100:.1f}%"}
                for name, o in tm["operations"].items()]
        out.append(md_table(pd.DataFrame(rows)))
        verdict = ("Per-call timing is reliable for these operations (timer cost at most "
                   f"{tm['max_timer_share'] * 100:.0f}% of the operation)." if tm["per_call_timing_reliable"]
                   else "**The timer cost is a large share of the fastest operation; read their per-call times with care.**")
        out.append(f"\n*Table 4d: timer calibration.* {verdict}\n")
    return "\n".join(out)


def _evolution_section(run: results.Run, figs: dict[str, str]) -> str:
    F = run.frames
    if "evolution" not in F or F["evolution"].empty or "evolution_direct" not in F:
        return ""
    mtl, direct = F["evolution"], F["evolution_direct"]
    ev = run.config["evolution"]
    out: list[str] = []
    out.append("### 5.7 Evolving zone: updates and cached ladders\n")
    out.append("MTL is built for a series that keeps growing, so a single signed zone is its best case. Here each "
               f"series starts from the full dataset and runs rounds in which **k** RRsets change (k = {', '.join(map(str, ev['k']))}; "
               f"{', '.join(map(str, ev['rounds']))} rounds). Every change appends a new leaf, old leaves never "
               "leave the series, and each round signs one new ladder. The baseline signs every changed RRset with "
               "plain SLH-DSA. All figures in Tables 12 to 15 are measured with the reference library; an RRset "
               "'change' is modelled as the same-size message with its last 8 bytes replaced, chosen uniformly at "
               "random, so the cost of an update is real but its content is synthetic.\n")
    def by_zone(df: pd.DataFrame) -> pd.DataFrame:
        rank = {d: i for i, d in enumerate(run.dataset_order)}
        return df.assign(_o=df.dataset.map(rank)).sort_values(["_o", "k"]).drop(columns="_o").reset_index(drop=True)

    cost = by_zone(evolution.update_cost(mtl, direct))
    out.append("#### Cost of one update round\n")
    rows = []
    for _, r in cost.iterrows():
        rows.append({"dataset": r.dataset, "k": int(r.k), "MTL: append k leaves + 1 ladder signature": f"{fmt_seconds(r.mtl_round_ns)} [{fmt_seconds(r.mtl_lo_ns)}–{fmt_seconds(r.mtl_hi_ns)}]",
                     "direct: k signatures": fmt_seconds(r.direct_round_ns),
                     "direct ÷ MTL (per-trial, interval)": fmt_ratio(pd.Series({"ratio": r.ratio, "ratio_lo": r.ratio_lo, "ratio_hi": r.ratio_hi})),
                     "resolver verifies, MTL (1 ladder)": fmt_seconds(r.mtl_resolver_ns),
                     "resolver verifies, direct (k signatures)": fmt_seconds(r.direct_resolver_ns)})
    out.append(md_table(pd.DataFrame(rows)))
    out.append("\n*Table 12: cost of one update round, median over rounds and trials. 'Resolver verifies' is the work to "
               "take in the update: one ladder signature against k signatures.*\n")
    if "evolution" in figs:
        cap = ("Figure 6: cost of one update round (left) and how long a cached ladder keeps covering the "
               "current answers (right)")
        out.append(f"\n![{cap}]({figs['evolution']})\n\n*{cap}*\n")
    one = cost[cost.k == cost.k.min()]
    if len(one):
        r = one.iloc[0]
        verdict = ("MTL is not faster at k = 1: it still signs one ladder per round, the same SLH-DSA signature "
                   "direct signing needs for one changed RRset"
                   if r.ratio_lo <= 1.5 and r.ratio_hi >= 0.67 else
                   f"MTL is {fmt_ratio(pd.Series({'ratio': r.ratio, 'ratio_lo': r.ratio_lo, 'ratio_hi': r.ratio_hi}))} "
                   "faster even at k = 1")
        out.append(f"**Reading.** {verdict}. As k grows, direct signing pays k signatures per round and MTL still "
                   "pays one, so the advantage grows roughly with k. At small k, MTL's gain is in bytes and in "
                   "verification, not in signing time.\n")
    out.append("#### How long a cached ladder stays useful\n")
    cov = evolution.coverage_table(mtl)
    cov = cov[cov.age > 0]
    piv = by_zone(cov.pivot_table(index=["dataset", "k"], columns="age", values="share").reset_index())
    piv.columns = [c if isinstance(c, str) else f"ladder {int(c)} round(s) old" for c in piv.columns]
    for c in piv.columns[2:]:
        piv[c] = (piv[c] * 100).map(lambda v: f"{v:.1f}%")
    out.append(md_table(piv))
    out.append("\n*Table 13: share of the current zone's RRsets whose condensed signature verifies against the cached "
               "ladder of that age (measured by the library, every RRset, every round in which that age exists). "
               "A leaf appended after the cached ladder was signed has no rung in it and is rejected; every other "
               "RRset verifies. The rejected share is therefore the share of RRsets changed since that ladder, and "
               "it is the share of answers that need a full signature (about 8 KB over TCP) from a resolver that "
               "does not refresh.*\n")
    g = evolution.growth(mtl)
    last = by_zone(g.sort_values("round").groupby(["dataset", "k"]).tail(1).merge(
        g[g["round"] == 0][["dataset", "k", "ladder_bytes", "condensed_bytes_max", "leaf_count"]],
        on=["dataset", "k"], suffixes=("", "_start")))
    out.append("#### Growth of the series\n")
    out.append(md_table(pd.DataFrame({
        "dataset": last.dataset, "k": last.k, "rounds": last["round"],
        "leaves": [f"{int(a):,} → {int(b):,}" for a, b in zip(last.leaf_count_start, last.leaf_count)],
        "rungs at the end": last.rung_count.astype(int),
        "signed ladder": [f"{fmt_bytes(a)} → {fmt_bytes(b)}" for a, b in zip(last.ladder_bytes_start, last.ladder_bytes)],
        "largest condensed signature": [f"{int(a)} B → {int(b)} B" for a, b in zip(last.condensed_bytes_max_start, last.condensed_bytes_max)],
        "signer RSS": last.rss_bytes.map(fmt_bytes)})))
    out.append("\n*Table 14: the series only grows. Rungs follow the binary breakdown of the leaf count (11 leaves = "
               "8 + 2 + 1), so the ladder size moves up and down while the leaf count rises; the condensed signature "
               "grows by 16 bytes whenever the leaf count reaches a power of two.*\n")
    st = evolution.stale_by_round(mtl)
    if st.stale_tested.sum():
        worst = by_zone(st.groupby(["dataset", "k"]).share.min().reset_index())
        out.append("#### Published signatures when the ladder changes\n")
        out.append(md_table(pd.DataFrame({
            "dataset": worst.dataset, "k": worst.k,
            "lowest share still valid": [f"{v * 100:.1f}%" if pd.notna(v) else "–" for v in worst.share],
            "rounds with any signature invalidated": [int(((st.dataset == d) & (st.k == k) & (st.share < 1)).sum())
                                                       for d, k in zip(worst.dataset, worst.k)]})))
        out.append("\n*Table 15: condensed signatures issued when a leaf was created, verified against the newest "
                   "ladder. When rungs merge as the series grows, the path of an earlier signature no longer ends at "
                   "a rung of the new ladder: it still verifies against the older ladder, but the signature has to be "
                   "reissued for resolvers that hold the new one. At a power-of-two leaf count every earlier "
                   "signature is affected.*\n")
    eb = evolution.effective_bytes(mtl)
    if len(eb):
        e = eb[(eb.dataset == run.dataset_order[-1]) & (eb.age > 0)]
        piv = e.pivot_table(index="k", columns="age", values="effective_bytes").reset_index()
        piv.columns = [c if isinstance(c, str) else f"ladder {int(c)} old" for c in piv.columns]
        for c in piv.columns[1:]:
            piv[c] = piv[c].map(lambda v: f"{v:,.0f} B")
        out.append(f"#### Signature bytes per answer, {run.dataset_order[-1]} zone (derived)\n")
        out.append(md_table(piv))
        out.append("\n*Table 16: derived, not measured. Measured condensed size, plus the measured uncovered share "
                   "times a full signature (condensed + ladder). It is an upper bound: it assumes every uncovered "
                   "answer fetches a full signature, while a real resolver refreshes its ladder once and then "
                   "covers the rest. Compare with 7,856 B for plain SLH-DSA.*\n")
    return "\n".join(out)


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
    add("### PS-036 · PQC DNSSEC MTL Benchmark Using a Reference Implementation · AIORI Hackathon 3\n")
    add(f"Run `{run.root.name}` · go/no-go gate: **{run.preflight.get('verdict', 'missing')}** · "
        f"{cfg['trials']} trials · seed {cfg['seed']} · stack `{env.get('stack', 'unknown')}` · "
        f"git `{env.get('git_sha', 'unknown')}`\n")

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
        ("Stack profile", env.get("stack")),
        ("SLH-DSA call (liboqs name)", MODES["slh-dsa"].harness_algo),
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
        "4. Every harness mode verifies its own output and rejects tampering, wrong keys, truncated signatures "
        "and signatures presented for another message.\n"
        "5. The harness output is verified by a different program: `mtlverify` for MTL and OpenSSL for the "
        "non-MTL SLH-DSA signatures (an interoperability check, not a known-answer test).\n\n"
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
        f"- **Noise.** The clock's own cost and resolution are measured at the start of the run (Table 4d)"
        + (f", and `{cfg['control']}` runs twice inside every trial as an A/A control (Table 4c)."
           if cfg.get("control") else ".") + "\n"
        f"- **Correctness.** A trial counts only if every signature verifies and tampered messages, "
        f"signatures and ladders are rejected.\n"
        f"- **Statistics.** We report the median across trials with a distribution-free confidence interval "
        f"of the median (order statistics). With {cfg['trials']} trials it is the "
        f"{stats.ci_label(int(cfg['trials']))}.\n"
        f"- **Transmitted bytes.** Measured on real DNS messages encoded by dnspython, using the signatures "
        f"from trial 0.\n")

    add("## 5. Results\n")
    add("### 5.1 Signing time\n")
    fig("sign", "Figure 1: whole-zone signing time (log scale)")
    rows = []
    for _, r in timing.iterrows():
        rows.append({"dataset": r.dataset, "mode": label(r["mode"]), "RRsets signed": f"{r.n_signed:,}/{r.dataset_rrsets:,}",
                     "sign zone, median [CI]": ci(r, "sign_total_ns"),
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
                     "verify zone (warm), median [CI]": ci(r, "verify_warm_total_ns"),
                     "per RRset (warm)": fmt_seconds(r.verify_per_rrset_ns),
                     "one signature, cold": fmt_seconds(r.get("verify_full_median_ns")),
                     "checks passed": "yes" if r.checks_passed else "**NO**"})
    add(md_table(pd.DataFrame(rows)))
    add("\n*Table 4: verification time. For MTL, \"cold\" means a full signature verified without a cached "
        "ladder; for other modes it is the median single-signature verification.*\n")

    floor = _floor(run)
    pr = results.paired_ratio_table(F["trials"], "slh-dsa", "slh-dsa-mtl", floor=floor)
    if len(pr):
        add("#### Paired ratios: direct SLH-DSA divided by MTL\n")
        add("Every mode runs inside each trial, so each trial gives one ratio. The table shows the median of those "
            "per-trial ratios and its distribution-free interval; it is what supports a statement such as "
            "\"N times faster\".\n")
        pr = pr.assign(order=pr.dataset.map({d: i for i, d in enumerate(run.dataset_order)})).sort_values(["metric", "order"])
        add(md_table(pd.DataFrame({
            "dataset": pr.dataset, "metric": pr.metric, "trials": pr.trials,
            "ratio, median [interval]": [fmt_ratio(r) for _, r in pr.iterrows()],
            "interval covers": [f"{c * 100:.0f}%" for c in pr.coverage],
            "basis": ["extrapolated direct total" if e else "measured" for e in pr.extrapolated],
            **({"against the A/A noise floor": [
                ("clearly outside" if a else "**not distinguishable from noise**") + f" (±{b * 100:.1f}%)"
                for a, b in zip(pr.above_noise, pr.noise_band)]} if "above_noise" in pr else {})})))
        add("\n*Table 4b: paired per-trial ratios.*\n")
    add(_noise_section(run, floor))
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
    rsrc_path = run.root / "rsrc_summary.json"
    if rsrc_path.is_file():
        try:
            rsrc = json.loads(rsrc_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rsrc = {}
        if rsrc.get("n_samples"):
            ov = rsrc.get("overall") or {}
            whole = ov.get("source") == "cgroup_v2"
            add("### 5.4a CPU and memory while the benchmark ran\n")
            add("Measured by `rsrcsamp`, a small C program that reads the kernel's own counters once a second "
                "and at every stage change. Two layers are recorded so a continuously hosted deployment can be sized:\n\n"
                + ("- **Solution** — the whole benchmark container (Python driver and every `mtlbench` child): "
                   "cgroup v2 `cpu.stat`, `memory.current`, `memory.stat`.\n"
                   if whole else
                   "- **Solution** — the Python driver process: `/proc/<pid>/stat` and `status`.\n")
                + "- **Host** — the machine those containers share: `/proc/stat` (overall CPU busy %) and "
                  "`/proc/meminfo` (`MemAvailable`). On Docker Desktop this is the WSL2 VM; on a Linux testbed "
                  "it is the hardware.\n\n"
                + "Solution CPU % is CPU time divided by wall time, so 100% is one core fully busy"
                + (f"; this run could use {ov['cpus_allowed']} core(s)" if ov.get("cpus_allowed") else "")
                + (f" on a host with {ov['cpus_online']} online"
                   if ov.get("cpus_online") else "")
                + ". Host CPU % is the share of all host cores that were busy. Memory figures exclude reclaimable "
                  "file cache.\n")

            def _num(v, digits: int = 1) -> str:
                return "—" if v is None else f"{v:,.{digits}f}"

            def _mem(v) -> str:
                return "—" if v is None else fmt_bytes(v)

            rows = [{"stage": lab,
                     "wall time (s)": _num(agg.get("wall_s")),
                     "solution CPU (s)": _num(agg.get("cpu_seconds")),
                     "solution CPU % mean": _num(agg.get("cpu_pct_mean")),
                     "solution CPU % peak": _num(agg.get("cpu_pct_peak")),
                     "solution memory peak": _mem(agg.get("mem_bytes_peak")),
                     "solution anon peak": _mem(agg.get("mem_anon_bytes_peak")),
                     "host CPU % mean": _num(agg.get("host_cpu_pct_mean")),
                     "host CPU % peak": _num(agg.get("host_cpu_pct_peak")),
                     "host MemAvailable low": _mem(agg.get("host_mem_available_bytes_min"))}
                    for lab, agg in (rsrc.get("by_label") or {}).items()]
            if rows:
                add(md_table(pd.DataFrame(rows)))
                kind = {"cgroup_working_set": "`memory.current` minus inactive file cache (same family as "
                                              "`docker stats`)",
                        "cgroup_current": "`memory.current`, which includes file cache",
                        "proc_rss": "the driver's resident set (VmRSS)"}.get(ov.get("mem_kind"), "as reported")
                host_total = _mem(ov.get("host_mem_total_bytes"))
                add(f"\n*Table 9a: measured, from `rsrc_samples.jsonl` (kept with the run so the figures remain "
                    f"after execution). Solution memory is {kind}; anon is heap/stack without file cache. "
                    f"Host memory total was {host_total}. The report stage is missing from this table because "
                    f"it is written before that stage ends; `rsrc_summary.json` has the full series.*\n")

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

    add(_evolution_section(run, figs))
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
        "scripts/run_benchmark.sh preflight                     # build the image from the stack profile, go/no-go gate\n"
        "scripts/run_benchmark.sh configs/scenarios.yaml        # full run -> results/runs/<id>/\n"
        f"python -m cipherops.runner report --run results/runs/{run.root.name}\n"
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
