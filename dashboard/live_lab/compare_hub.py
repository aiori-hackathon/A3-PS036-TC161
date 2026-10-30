"""Compare algorithms and compare runs, for both measurement paths, in one shared shape.

Two sources exist and are never mixed on one chart:

- ``offline``: the pinned single-core harness (``results/runs/<id>``): five modes, repeated trials.
- ``live``: the Docker resolver lab (a saved benchmark suite, or one lab's Prepare plus a saved run): scenarios.

Both are reduced to one frame per dataset, one row per algorithm, with the same columns (``STD_COLUMNS``), so the
dashboard can draw the same table and charts for either source. Everything here is a pure function over files the
benchmark already wrote; nothing talks to Docker.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd
import yaml

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.append(str(_SRC))
from cipherops import results  # noqa: E402
from live_lab import compare  # noqa: E402
from cipherops.codepoints import MODES  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"

STD_COLUMNS = ["key", "label", "sign_s", "sign_lo", "sign_hi", "sign_basis", "verify_s", "verify_lo", "verify_hi",
               "size_bytes", "wire_bytes", "truncations", "tcp_exchanges", "cache_bytes", "cold_ms", "warm_ms"]

# column -> (short title, unit). Every one is "lower is better".
METRICS = {
    "sign_s": ("Signing time", "s"),
    "verify_s": ("Verification time", "s"),
    "size_bytes": ("Signature size", "B"),
    "wire_bytes": ("Response size", "B"),
    "truncations": ("Truncated (TC) responses", "n"),
    "cache_bytes": ("Cache size", "B"),
}

# What the same column means in each source, so a label never claims more than the number is.
METRIC_NOTES = {
    "offline": {
        "sign_s": "Whole zone, median of the trials. Direct SLH-DSA on a capped dataset is extrapolated from a sample.",
        "verify_s": "Whole zone verified once, with a cached ladder for MTL (warm). Median of the trials.",
        "size_bytes": "All signatures of the zone (for MTL: condensed signatures plus one signed ladder).",
        "wire_bytes": "Real DNS messages for the lookup workload at a 1232-byte EDNS buffer, including TCP retries.",
        "truncations": "Answers that did not fit the UDP buffer and fell back to TCP.",
        "cache_bytes": "Signature material a validating resolver would hold after the workload (measured).",
    },
    "live": {
        "sign_s": "Estimated from repeated sample trials, extrapolated to the whole dataset (Docker lab, shared CPUs).",
        "verify_s": "ldns-verify-zone over every signed zone, estimated the same way (not the resolver).",
        "size_bytes": "Size of the signed zone files.",
        "wire_bytes": "Resolver to authoritative bytes on the cold pass, counted from the packet capture.",
        "truncations": "Truncated upstream responses on the cold pass.",
        "cache_bytes": "Unbound's RRset cache after the cold pass.",
    },
}

SHORT = {
    "slh-dsa-mtl": "SLH-DSA + MTL", "slh-dsa": "SLH-DSA", "ed25519": "Ed25519", "ecdsa-p256": "ECDSA P-256",
    "rsa-2048": "RSA-2048", "mtl": "SLH-DSA + MTL", "mixed-mtl": "Mixed MTL", "ecdsa": "ECDSA P-256", "rsa": "RSA-2048",
}
COLORS = {
    "rsa-2048": "#7f8fa9", "ecdsa-p256": "#4c78a8", "ed25519": "#9aa9c4", "slh-dsa": "#e4572e", "slh-dsa-mtl": "#2e9e6f",
    "rsa": "#7f8fa9", "ecdsa": "#4c78a8", "mixed-mtl": "#8e6bbf", "mtl": "#2e9e6f",
}
ORDER = ["rsa-2048", "rsa", "ecdsa-p256", "ecdsa", "ed25519", "slh-dsa", "mixed-mtl", "slh-dsa-mtl", "mtl"]
SIGN_METRICS = ("sign_s", "verify_s")


def order_keys(keys) -> list[str]:
    keys = list(dict.fromkeys(keys))
    return [k for k in ORDER if k in keys] + sorted(k for k in keys if k not in ORDER)


def short(key: str) -> str:
    return SHORT.get(key, key)


def _na(v):
    return float("nan") if v is None else v


# ─────────────────────────── offline harness runs ───────────────────────────

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def offline_facts(config: dict, env: dict) -> dict[str, str]:
    """What a reader needs to judge whether two runs are comparable, in one flat dict."""
    hw, sw = env.get("hardware", {}), env.get("software", {})
    return {
        "Stack profile": str(env.get("stack") or "—"),
        "MTL commit": str(sw.get("mtl_reference_commit") or "—")[:10],
        "liboqs": str(sw.get("liboqs_tag") or "—"),
        "OpenSSL": str(sw.get("openssl_tag") or "—"),
        "CPU": str(hw.get("cpu_model") or "—"),
        "Cores used": str(hw.get("cpu_affinity") or "—"),
        "Runs": str(config.get("trials", "—")),
        "Zone sizes": ", ".join(f"{d['name']} ({d['n_names']:,})" for d in config.get("datasets", [])) or "—",
        "Seed": str(config.get("seed", "—")),
        "Code version": str(env.get("git_sha") or "—")[:14],
    }


def offline_catalogue(results_dir: Path = RESULTS_DIR) -> list[dict]:
    """Finished offline runs, newest first. Only the small config and environment files are read."""
    out = []
    for path in results.list_runs(results_dir):
        try:
            config = yaml.safe_load((path / "config.yaml").read_text())
        except (OSError, ValueError):
            continue
        env = _read_json(path / "environment.json")
        facts = offline_facts(config, env)
        stamp = path.name.split("-", 1)[0]
        out.append({"id": path.name, "path": path, "source": "offline", "stack": facts["Stack profile"],
                    "trials": config.get("trials"), "facts": facts, "datasets": [d["name"] for d in config["datasets"]],
                    "title": f"{config.get('run_name', path.name)} · {facts['Stack profile']} · "
                             f"{config.get('trials')} trials · {stamp}"})
    return out


def evolution_catalogue(results_dir: Path = RESULTS_DIR) -> list[dict]:
    """Offline runs that include the evolving-zone experiment."""
    return [e for e in offline_catalogue(results_dir) if (e["path"] / "evolution.parquet").is_file()]


def noise_band(run: results.Run, dataset: str, metric: str) -> float | None:
    """The A/A noise band (fraction) for one dataset and one of 'signing' or 'verification (warm)', if the run had one."""
    for r in run.summary.get("noise_floor", []):
        if r["dataset"] == dataset and r["metric"] == metric:
            return float(r["band"])
    return None


def offline_frame(run: results.Run, dataset: str, udp_limit: int = 1232) -> pd.DataFrame:
    """One row per mode for one dataset of an offline run."""
    F = run.frames
    timing = results.timing_table(F["trials"])
    timing = timing[timing.dataset == dataset].set_index("mode")
    wl = F["workload"]
    wl = wl[(wl.dataset == dataset) & (wl.udp_limit == udp_limit)].set_index("mode")
    cc = F["cache"]
    cc = cc[(cc.dataset == dataset) & (cc.scope == "resolver") & (cc.kind == "measured")].set_index("mode")
    tr = F["trials"]
    tr = tr[tr.dataset == dataset]
    rows = []
    for mk in run.mode_order:
        if mk not in timing.index:
            continue
        t = timing.loc[mk]

        def eff(col: str):
            ext = col.replace("_ns", "_extrapolated_ns")
            if bool(t["limited"]) and ext in t and pd.notna(t[ext]):
                return t[ext], t.get(ext + "_lo"), t.get(ext + "_hi"), True
            return t[col], t.get(col + "_lo"), t.get(col + "_hi"), False

        s, slo, shi, s_ext = eff("sign_total_ns")
        v, vlo, vhi, _ = eff("verify_warm_total_ns")
        g = tr[tr["mode"] == mk]
        material = (float(g.signature_material_bytes.median()) if MODES[mk].family == "mtl"
                    else float(g.signature_bytes_median.median()) * int(t["dataset_rrsets"]))
        rows.append({
            "key": mk, "label": short(mk),
            "sign_s": s / 1e9, "sign_lo": _na(slo) / 1e9, "sign_hi": _na(shi) / 1e9,
            "sign_basis": "extrapolated" if s_ext else "measured",
            "verify_s": v / 1e9, "verify_lo": _na(vlo) / 1e9, "verify_hi": _na(vhi) / 1e9,
            "size_bytes": material,
            "wire_bytes": float(wl.loc[mk, "dns_bytes"]) if mk in wl.index else float("nan"),
            "truncations": float(wl.loc[mk, "truncations"]) if mk in wl.index else float("nan"),
            "tcp_exchanges": float(wl.loc[mk, "tcp_exchanges"]) if mk in wl.index else float("nan"),
            "cache_bytes": float(cc.loc[mk, "signature_material_bytes"]) if mk in cc.index else float("nan"),
            "cold_ms": float("nan"), "warm_ms": float("nan"),
        })
    return pd.DataFrame(rows, columns=STD_COLUMNS)


def offline_scaling(run: results.Run, udp_limit: int = 1232) -> pd.DataFrame:
    """All datasets of an offline run stacked, with the RRset count of each in ``rrsets``."""
    parts = []
    sizes = run.frames["datasets"].set_index("dataset")["rrsets"]
    for ds in run.dataset_order:
        parts.append(offline_frame(run, ds, udp_limit).assign(dataset=ds, rrsets=int(sizes.get(ds, 0))))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def offline_paired(run: results.Run, dataset: str, baseline: str) -> pd.DataFrame:
    """Per-trial ratio baseline / mode for signing and warm verification (above 1 = the mode is faster)."""
    rows = []
    for mk in run.mode_order:
        if mk == baseline:
            continue
        t = results.paired_ratio_table(run.frames["trials"], baseline, mk)
        t = t[t.dataset == dataset] if len(t) else t
        for _, r in t.iterrows():
            col = "sign_s" if r.metric == "signing" else "verify_s"
            rows.append({"key": mk, "col": col, "ratio": r.ratio, "lo": r.ratio_lo, "hi": r.ratio_hi,
                         "trials": int(r.trials), "extrapolated": bool(r.extrapolated)})
    return pd.DataFrame(rows, columns=["key", "col", "ratio", "lo", "hi", "trials", "extrapolated"])


# ─────────────────────────── live lab data ───────────────────────────

def live_facts(suite: dict) -> dict[str, str]:
    prov = suite.get("provenance") or {}
    pins = prov.get("stack_pins") or {}
    sizes = suite.get("sizes") or []
    return {
        "Stack profile": str(prov.get("stack") or suite.get("stack") or "—"),
        "MTL commit": str(pins.get("MTL_COMMIT") or "—")[:10],
        "liboqs": str(pins.get("LIBOQS_TAG") or "—"),
        "OpenSSL": str(pins.get("OPENSSL_TAG") or "—"),
        "CPU": str(prov.get("cpu_model") or "—"),
        "Cores used": str(prov.get("cpu_isolation") or "—"),
        "Runs": str(suite.get("trials", "—")),
        "Zone sizes": ", ".join(f"{n:,}" for n in sizes) or "—",
        "Seed": "—",
        "Code version": str(prov.get("git_commit") or "—")[:12] + (" (dirty)" if prov.get("git_dirty") else ""),
        "CPU governor": str(prov.get("cpu_governor") or "—"),
    }


def live_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Map a ``compare.suite_frame`` (or single-lab merge) frame onto the shared columns."""
    if df is None or df.empty:
        return pd.DataFrame(columns=STD_COLUMNS)

    def col(name: str):
        return df[name] if name in df else pd.Series([float("nan")] * len(df), index=df.index)

    out = pd.DataFrame({
        "key": df["scenario"], "label": df["scenario"].map(short),
        "sign_s": col("sign_s"), "sign_lo": col("sign_lo_s"), "sign_hi": col("sign_hi_s"),
        "sign_basis": "estimated" if "sign_trials" in df else "measured",
        "verify_s": col("verify_s"), "verify_lo": float("nan"), "verify_hi": float("nan"),
        "size_bytes": col("signed_bytes"), "wire_bytes": col("upstream_bytes"), "truncations": col("truncated"),
        "tcp_exchanges": col("tcp_conns"), "cache_bytes": col("cache_rrset"),
        "cold_ms": col("cold_ms"), "warm_ms": col("warm_ms"),
    })
    return out[STD_COLUMNS]


def live_suite_entry(s: dict) -> dict:
    """Catalogue entry for a finished live-lab suite (``s`` is one item of ``suite.list_suites``)."""
    suite = _read_json(Path(s["path"]) / "suite.json")
    facts = live_facts(suite)
    return {"id": s["id"], "path": s["path"], "source": "live", "title": s["title"],
            "stack": facts["Stack profile"], "trials": suite.get("trials"), "facts": facts,
            "datasets": sorted(int(k) for k in (suite.get("sizes_detail") or {}))}


def stats_label(run: results.Run) -> str:
    return "Intervals: " + results.stats.ci_label(int(run.config.get("trials", 0))) + "."


def load_offline(path: Path) -> results.Run:
    return results.load(Path(path))


# Result files a finished harness run leaves next to the report. The per-operation log, raw traces and
# signing inputs stay in the run folder: they are large and are not what the Results page shows.
OFFLINE_BUNDLE_FILES = (
    "report.html", "report.md", "summary.json", "config.yaml", "environment.json",
    "trials.csv", "control.csv", "responses.csv", "workload.csv", "cache.csv", "sizes.csv",
    "datasets.csv", "evolution.csv", "evolution_direct.csv", "crosscheck.csv",
    "live.csv", "live_summary.csv",
)


def offline_bundle(root: Path) -> bytes:
    """Zip of the report, its figures, and the result tables for one offline run."""
    root = Path(root)
    buf = io.BytesIO()
    wrote = False
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in OFFLINE_BUNDLE_FILES:
            path = root / name
            if path.is_file():
                zf.write(path, arcname=name)
                wrote = True
        figures = root / "figures"
        if figures.is_dir():
            for fig in sorted(p for p in figures.iterdir() if p.is_file()):
                zf.write(fig, arcname=f"figures/{fig.name}")
                wrote = True
    return buf.getvalue() if wrote else b""


# ─────────────────────────── comparison arithmetic ───────────────────────────

def fmt_x(x: float | None) -> str:
    """'12× lower' when the value is below the baseline (all metrics: lower is better), '3.1× higher' when above."""
    if x is None or pd.isna(x) or x <= 0:
        return "—"
    if abs(x - 1) < 0.05:
        return "same"
    return f"{_num(x)}× {'lower' if x >= 1 else 'higher'}"


def _num(x: float) -> str:
    n = x if x >= 1 else 1 / x
    return f"{n:,.1f}" if n < 10 else f"{n:,.0f}"


def _interval(lo: float, hi: float) -> str:
    """The interval of a ratio in the same words as the ratio; when it crosses 1 the direction is not settled."""
    if lo >= 1.0 or hi <= 1.0:
        first, second = sorted((_num(lo), _num(hi)), key=lambda t: float(t.replace(",", "")))
        return f"[{first}×, {second}×]"
    return "[interval includes no difference]"


def fmt_value(v: float | None, unit: str) -> str:
    if v is None or pd.isna(v):
        return "—"
    if unit == "s":
        return compare.fmt_seconds(v) if v >= 1e-3 else f"{v * 1e6:.0f} µs"
    if unit == "B":
        return compare.fmt_bytes(v)
    return f"{v:,.0f}"


def vs_baseline(frame: pd.DataFrame, baseline: str) -> pd.DataFrame:
    """baseline / value for every metric (above 1 = lower than the baseline = better). NaN where either is missing."""
    base = frame[frame.key == baseline]
    out = frame[["key"]].copy()
    for col in METRICS:
        b = float(base[col].iloc[0]) if len(base) and pd.notna(base[col].iloc[0]) else float("nan")
        out[col] = [b / v if pd.notna(v) and v > 0 and pd.notna(b) and b > 0 else float("nan") for v in frame[col]]
    return out


def algorithm_table(frame: pd.DataFrame, baseline: str, paired: pd.DataFrame | None = None) -> pd.DataFrame:
    """Readable table: one row per algorithm, a value and a 'vs baseline' column per metric.

    Where a per-trial paired ratio exists (offline signing and verification) it replaces the ratio of medians and
    carries its interval, so 'N× lower [lo, hi]' is a statement about the trials, not about two separate medians.
    """
    x = vs_baseline(frame, baseline).set_index("key")
    rows = []
    for _, r in frame.iterrows():
        row = {"Algorithm": r["label"] + ("  (baseline)" if r["key"] == baseline else "")}
        for col, (title, unit) in METRICS.items():
            if frame[col].isna().all():
                continue
            row[title] = fmt_value(r[col], unit)
            if r["key"] == baseline:
                row[f"{title}: vs {short(baseline)}"] = ""
                continue
            text = fmt_x(x.loc[r["key"], col])
            if paired is not None and not paired.empty:
                p = paired[(paired.key == r["key"]) & (paired.col == col)]
                if len(p):
                    p = p.iloc[0]
                    text = f"{fmt_x(p.ratio)} {_interval(p.lo, p.hi)}"
            row[f"{title}: vs {short(baseline)}"] = text
        rows.append(row)
    return pd.DataFrame(rows)


# ─────────────────────────── comparing two runs ───────────────────────────

IMPORTANT_FACTS = ("Stack profile", "MTL commit", "liboqs", "CPU", "Runs", "Cores used")


def facts_diff(a: dict[str, str], b: dict[str, str]) -> pd.DataFrame:
    keys = list(dict.fromkeys(list(a) + list(b)))
    rows = [{"Item": k, "Run A": a.get(k, "—"), "Run B": b.get(k, "—"), "Same": a.get(k, "—") == b.get(k, "—")}
            for k in keys]
    return pd.DataFrame(rows)


def comparability_notes(a: dict[str, str], b: dict[str, str]) -> list[str]:
    """Plain-language warnings about what differs between two runs, so a ratio is not read as pure speed."""
    notes = []
    if a.get("Stack profile") != b.get("Stack profile"):
        notes.append(f"**Different stacks** ({a.get('Stack profile')} and {b.get('Stack profile')}): the libraries, "
                     "and possibly the SLH-DSA form itself, differ. Differences reflect the software versions, not "
                     "only speed. Never present them as one result.")
    elif (a.get("MTL commit"), a.get("liboqs")) != (b.get("MTL commit"), b.get("liboqs")):
        notes.append("The MTL or liboqs version differs between the runs.")
    if a.get("CPU") != b.get("CPU"):
        notes.append("**Different CPUs:** absolute times are not comparable. Ratios between algorithms may still hold.")
    if a.get("Runs") != b.get("Runs"):
        notes.append("The number of trials differs, so the intervals have different coverage.")
    if a.get("Code version") != b.get("Code version"):
        notes.append("The code version differs.")
    if not notes:
        notes.append("Same stack, CPU and trial count: differences are run-to-run variation, which is what a "
                     "reproducibility check looks for.")
    return notes


def run_ratios(frame_a: pd.DataFrame, frame_b: pd.DataFrame, bands: dict[str, float] | None = None) -> pd.DataFrame:
    """Run B relative to run A for every algorithm and metric they share (1.0 = identical, above = B is higher)."""
    bands = bands or {}
    keys = [k for k in order_keys(frame_a.key) if k in set(frame_b.key)]
    rows = []
    for k in keys:
        ra, rb = frame_a[frame_a.key == k].iloc[0], frame_b[frame_b.key == k].iloc[0]
        for col, (title, unit) in METRICS.items():
            a, b = ra[col], rb[col]
            if pd.isna(a) or pd.isna(b) or a <= 0 or b <= 0:
                continue
            ratio = float(b) / float(a)
            band = bands.get(col)
            rows.append({"key": k, "label": short(k), "col": col, "metric": title, "unit": unit, "a": float(a),
                         "b": float(b), "ratio": ratio, "noise_band": band,
                         "within_noise": None if band is None else abs(ratio - 1) <= band})
    return pd.DataFrame(rows, columns=["key", "label", "col", "metric", "unit", "a", "b", "ratio", "noise_band",
                                       "within_noise"])
