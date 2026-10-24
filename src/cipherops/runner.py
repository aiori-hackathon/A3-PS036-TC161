"""Command line entry point.

    cipherops preflight                 go/no-go gate (PS-036)
    cipherops run --config FILE         full benchmark run
    cipherops report --run DIR          regenerate the markdown report
    cipherops crosscheck --run DIR      validate byte counts against a real NSD server
    cipherops live --run DIR            real client vs real NSD: every zone, buffer and network profile
    cipherops serve --run DIR           keep NSD running so the dashboard can send real queries
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import analytical, cache, dnswire, envinfo, preflight, stats
from .codepoints import MODES, MTL_SECURITY_PARAMETER_N, SLH_DSA_SHA2_128S_SIG_BYTES
from .harness import HARNESS, load_signatures, run_trial
from .progress import log, stage
from .workload import build_zone, signing_messages, write_messages, zone_text

CLASSICAL_SIG_BYTES = {"ed25519": 64, "ecdsa-p256": 64, "rsa-2048": 256}
OP_ARRAYS = ("append_ns", "condensed_ns", "verify_condensed_ns", "verify_full_ns", "sign_ns", "verify_ns")
STEPS = 7


def _secs(ns: float) -> str:
    return f"{ns / 1e9:.3f} s" if ns >= 1e8 else f"{ns / 1e6:.2f} ms"


def load_config(path: Path, overrides: argparse.Namespace) -> dict:
    cfg = yaml.safe_load(Path(path).read_text())
    if overrides.trials is not None:
        cfg["trials"] = overrides.trials
    if overrides.modes:
        cfg["modes"] = overrides.modes.split(",")
    if overrides.datasets:
        keep = set(overrides.datasets.split(","))
        cfg["datasets"] = [d for d in cfg["datasets"] if d["name"] in keep]
    unknown = [m for m in cfg["modes"] if m not in MODES]
    if unknown:
        raise SystemExit(f"unknown modes: {unknown}")
    return cfg


def trial_metrics(res: dict, dataset: str, mode_key: str, trial: int, n_total: int) -> dict:
    mode = MODES[mode_key]
    n = res["n_messages"]
    row = {
        "dataset": dataset, "mode": mode_key, "family": mode.family, "algorithm": res["algorithm"],
        "trial": trial, "n_signed": n, "dataset_rrsets": n_total, "limited": n < n_total,
        "keygen_ns": res["keygen_ns"], "exit_code": res["exit_code"],
        "failures_total": int(sum(res["failures"].values())),
        "tamper_all_rejected": bool(all(res["tamper"].values())),
        "public_key_bytes": res["public_key_bytes"],
        "maxrss_bytes": res["memory"].get("maxrss"),
    }
    if mode.family == "mtl":
        sign_total = sum(res["append_ns"]) + res["ladder_sign_ns"] + sum(res["condensed_ns"])
        verify_cond = sum(res["verify_condensed_ns"])
        row.update({
            "sign_total_ns": sign_total,
            "sign_append_total_ns": sum(res["append_ns"]),
            "ladder_sign_ns": res["ladder_sign_ns"],
            "condensed_extract_total_ns": sum(res["condensed_ns"]),
            "ladder_verify_ns": res["ladder_verify_ns"],
            "verify_condensed_total_ns": verify_cond,
            "verify_warm_total_ns": res["ladder_verify_ns"] + verify_cond,
            "verify_condensed_median_ns": float(np.median(res["verify_condensed_ns"])),
            "verify_full_median_ns": float(np.median(res["verify_full_ns"])) if res["verify_full_ns"] else None,
            "signature_bytes_median": float(np.median(res["condensed_bytes"])),
            "signature_bytes_max": int(max(res["condensed_bytes"])),
            "signed_ladder_bytes": res["signed_ladder_bytes"],
            "signature_material_bytes": int(sum(res["condensed_bytes"])) + res["signed_ladder_bytes"],
            "node_set_rss_growth_bytes": res["memory"]["rss_after_append"] - res["memory"]["rss_after_keygen"],
        })
    else:
        sign_total = sum(res["sign_ns"])
        row.update({
            "sign_total_ns": sign_total,
            "verify_warm_total_ns": sum(res["verify_ns"]),
            "verify_full_median_ns": float(np.median(res["verify_ns"])),
            "signature_bytes_median": float(np.median(res["signature_bytes"])),
            "signature_bytes_max": int(max(res["signature_bytes"])),
            "signature_material_bytes": int(sum(res["signature_bytes"])),
        })
    row["sign_per_rrset_ns"] = row["sign_total_ns"] / n
    row["verify_per_rrset_ns"] = row["verify_warm_total_ns"] / n
    if row["limited"]:
        row["sign_total_extrapolated_ns"] = row["sign_per_rrset_ns"] * n_total
        row["verify_warm_total_extrapolated_ns"] = row["verify_per_rrset_ns"] * n_total
    return row


def op_rows(res: dict, dataset: str, mode_key: str, trial: int) -> list[dict]:
    rows = []
    for op in OP_ARRAYS:
        for idx, ns in enumerate(res.get(op, [])):
            rows.append({"dataset": dataset, "mode": mode_key, "trial": trial, "op": op.removesuffix("_ns"),
                         "index": idx, "ns": ns})
    return rows


def direct_limit(cfg: dict, dataset: str, mode_key: str) -> int | None:
    if MODES[mode_key].family != "pqc-direct":
        return None
    return (cfg.get("direct_sign_limit") or {}).get(dataset)


def sample_indices(n: int, limit: int) -> list[int]:
    """Evenly spaced RRsets across canonical order, so a capped sample covers every record class."""
    if limit >= n:
        return list(range(n))
    return sorted({int(round(i * (n - 1) / (limit - 1))) for i in range(limit)})


def run_trials(cfg: dict, root: Path, paths: dict, sizes: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    modes = cfg["modes"]
    smallest = min(cfg["datasets"], key=lambda d: d["n_names"])["name"]
    log(f"warm-up: each mode runs once on '{smallest}'; results are discarded (loads libraries, warms caches)")
    for mk in modes:
        for w in range(cfg.get("warmup_trials", 1)):
            log(f"  warm-up {mk} on {smallest} ({w + 1})")
            run_trial(MODES[mk], paths[(smallest, mk)], None, cfg["full_verify_sample"])
    log(f"measured trials: {cfg['trials']} x {len(cfg['datasets'])} datasets x {len(modes)} modes, "
        "mode order shuffled each time (seeded), one mtlbench process per trial")
    shown: set[str] = set()

    rng = random.Random(cfg["seed"])
    trials, ops = [], []
    raw_dir = root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for t in range(cfg["trials"]):
        for ds in cfg["datasets"]:
            order = list(modes)
            rng.shuffle(order)
            for mk in order:
                name = ds["name"]
                out_dir = root / "artifacts" / name / mk if t == 0 else None
                start = time.time()
                res = run_trial(MODES[mk], paths[(name, mk)], out_dir, cfg["full_verify_sample"])
                with gzip.open(raw_dir / f"{name}__{mk}__t{t}.json.gz", "wt") as fh:
                    json.dump(res, fh)
                row = trial_metrics(res, name, mk, t, sizes[name])
                trials.append(row)
                ops.extend(op_rows(res, name, mk, t))
                if mk not in shown:
                    shown.add(mk)
                    log(f"  harness command ({mk}): {' '.join(Path(c).name if '/' in c else c for c in res['command'])}")
                ok = row["exit_code"] == 0 and row["failures_total"] == 0 and row["tamper_all_rejected"]
                status = "all signatures verified, tampering rejected" if ok else "CHECK FAILED"
                log(f"trial {t + 1}/{cfg['trials']} {name:<7} {mk:<12} signed={row['n_signed']:>6} "
                    f"sign={row['sign_total_ns'] / 1e9:8.3f}s verify={row['verify_warm_total_ns'] / 1e9:8.3f}s "
                    f"wall={time.time() - start:6.1f}s  {status}")
                if MODES[mk].family == "mtl":
                    log(f"      MTL: append {row['n_signed']} leaves {_secs(row['sign_append_total_ns'])}, "
                        f"sign ladder once {_secs(row['ladder_sign_ns'])}, "
                        f"condensed sigs {_secs(row['condensed_extract_total_ns'])}; "
                        f"condensed sig {row['signature_bytes_max']} B, ladder {row['signed_ladder_bytes']:,} B")
                else:
                    log(f"      signature {row['signature_bytes_max']:,} B each, "
                        f"{_secs(row['sign_per_rrset_ns'])} per signature"
                        + (f"  (sample of {row['n_signed']} of {row['dataset_rrsets']} RRsets, "
                           f"zone total extrapolated {row['sign_total_extrapolated_ns'] / 1e9:.1f} s)"
                           if row["limited"] else ""))
    return pd.DataFrame(trials), pd.DataFrame(ops)


def analyse(cfg: dict, root: Path, zones: dict, trials: pd.DataFrame) -> dict[str, pd.DataFrame]:
    wcfg = cfg["wire"]
    responses, workload_rows, cache_rows, size_rows = [], [], [], []
    for ds in cfg["datasets"]:
        name = ds["name"]
        zone = zones[name]
        n_total = len(zone.items)
        wl = dnswire.query_workload(zone, wcfg["workload"]["n_queries"], wcfg["workload"]["nx_fraction"],
                                    wcfg["workload"]["zipf_s"], cfg["seed"])
        kinds = Counter(k for k, _, _ in wl)
        log(f"dataset {name}: workload of {len(wl):,} lookups (DNSKEY {kinds['dnskey']}, "
            f"existing names {kinds['positive']:,}, non-existent {kinds['nxdomain']:,}; "
            f"{len({(q, t) for _, q, t in wl}):,} distinct)")
        for mk in cfg["modes"]:
            mode = MODES[mk]
            sub = trials[(trials.dataset == name) & (trials["mode"] == mk)]
            limited = bool(sub.limited.any())
            sigs = load_signatures(mode, root / "artifacts" / name / mk, limited)
            log(f"  {name} {mk}: building real DNS answers with the real signatures")
            catalog = dnswire.response_catalog(zone, sigs, wcfg["nx_sample"], cfg["seed"])
            for row in catalog:
                responses.append({"dataset": name, "mode": mk, "family": mode.family, **row})
            default = [r for r in catalog if r["variant"] == "default" and r["udp_limit"] == 1232]
            fit = 100 * sum(r["fits_udp"] for r in default) / len(default)
            log(f"      {len(default)} answers measured; {fit:.0f}% fit in 1232 B UDP; "
                f"median answer {int(np.median([r['response_bytes'] for r in default])):,} B")
            cached_1232 = None
            for limit in dnswire.UDP_LIMITS:
                totals, cached = dnswire.workload_accounting(zone, sigs, wl, limit)
                workload_rows.append({"dataset": name, "mode": mk, "family": mode.family, "udp_limit": limit,
                                      "signatures_reused_from_sample": sigs.reused, **totals})
                log(f"      workload @ EDNS {limit}: {totals['transmitted_responses']} sent, "
                    f"{totals['truncations']} truncated -> TCP, {totals['mtl_full_fetches']} ladder fetch, "
                    f"{totals['dns_bytes']:,} bytes on the wire")
                if limit == 1232:
                    cached_1232 = cached
            measured = cache.resolver_cache_measured(cached_1232, sigs.signed_ladder)
            log(f"      resolver cache (measured): {measured['cached_responses']} answers, "
                f"{measured['signature_material_bytes']:,} B of signatures"
                f"{' incl. ladder' if measured['ladder_bytes'] else ''}, "
                f"Python heap {measured['python_heap_bytes']:,} B")
            cache_rows.append({"dataset": name, "mode": mk, "scope": "resolver", "kind": "measured", **measured})
            cache_rows.append({"dataset": name, "mode": mk, "scope": "resolver", "kind": "analytical",
                               **cache.resolver_cache_analytical(mode, measured["cached_rrsigs"], n_total,
                                                                 CLASSICAL_SIG_BYTES.get(mk))})
            if mode.family == "mtl":
                cache_rows.append({"dataset": name, "mode": mk, "scope": "signer", "kind": "measured",
                                   "node_set_rss_growth_bytes": float(sub.node_set_rss_growth_bytes.median()),
                                   "maxrss_bytes": float(sub.maxrss_bytes.median())})
                cache_rows.append({"dataset": name, "mode": mk, "scope": "signer", "kind": "analytical",
                                   **cache.signer_state_analytical(n_total)})
                n = MTL_SECURITY_PARAMETER_N
                size_rows += [
                    {"dataset": name, "quantity": "condensed_signature", "kind": "measured_max",
                     "bytes": int(sub.signature_bytes_max.max())},
                    {"dataset": name, "quantity": "condensed_signature", "kind": "analytical_max",
                     "bytes": analytical.max_condensed_signature(n, n_total)},
                    {"dataset": name, "quantity": "signed_ladder", "kind": "measured",
                     "bytes": int(sub.signed_ladder_bytes.max())},
                    {"dataset": name, "quantity": "signed_ladder", "kind": "analytical_max",
                     "bytes": analytical.max_signed_ladder(n, n_total, SLH_DSA_SHA2_128S_SIG_BYTES)},
                    {"dataset": name, "quantity": "rungs", "kind": "analytical",
                     "bytes": analytical.rung_count(n_total)},
                ]
            storage = analytical.zone_signature_storage(n_total, SLH_DSA_SHA2_128S_SIG_BYTES, MTL_SECURITY_PARAMETER_N)
            if mode.family in ("mtl", "pqc-direct"):
                key = "mtl" if mode.family == "mtl" else "direct"
                size_rows.append({"dataset": name, "quantity": f"zone_signature_storage_{key}",
                                  "kind": "analytical", "bytes": storage[key]})
                measured_total = (sub.signature_material_bytes.median() if not limited
                                  else sub.signature_bytes_median.median() * n_total)
                size_rows.append({"dataset": name, "quantity": f"zone_signature_storage_{key}",
                                  "kind": "measured" if not limited else "measured_per_sig_x_n",
                                  "bytes": int(measured_total)})
    return {"responses": pd.DataFrame(responses), "workload": pd.DataFrame(workload_rows),
            "cache": pd.DataFrame(cache_rows), "sizes": pd.DataFrame(size_rows)}


def summarise(trials: pd.DataFrame, frames: dict[str, pd.DataFrame]) -> dict:
    out: dict = {"trials": {}, "transmitted": {}, "response_fit": {}}
    for (ds, mk), g in trials.groupby(["dataset", "mode"]):
        entry = {}
        for col in ("sign_total_ns", "sign_per_rrset_ns", "verify_warm_total_ns", "verify_per_rrset_ns",
                    "verify_full_median_ns", "keygen_ns"):
            vals = g[col].dropna()
            if len(vals):
                entry[col] = {**stats.summarize(vals), "median_ci95": stats.bootstrap_ci(vals)}
        entry["all_checks_passed"] = bool((g.exit_code == 0).all() and (g.failures_total == 0).all()
                                          and g.tamper_all_rejected.all())
        entry["limited"] = bool(g.limited.any())
        out["trials"].setdefault(ds, {})[mk] = entry
    wl = frames["workload"]
    for _, r in wl.iterrows():
        out["transmitted"].setdefault(r.dataset, {}).setdefault(r["mode"], {})[int(r.udp_limit)] = {
            k: (int(r[k]) if not isinstance(r[k], (bool, np.bool_)) else bool(r[k]))
            for k in ("dns_bytes", "response_bytes", "rrsig_signature_bytes", "truncations", "tcp_exchanges",
                      "mtl_full_fetches", "transmitted_responses")}
    resp = frames["responses"]
    d = resp[(resp.variant == "default") & (resp.udp_limit == 1232)]
    for (ds, mk), g in d.groupby(["dataset", "mode"]):
        out["response_fit"].setdefault(ds, {})[mk] = {
            kind: {"fit_1232_pct": float(gg.fits_udp.mean() * 100), "median_bytes": float(gg.response_bytes.median())}
            for kind, gg in g.groupby("record_class")}
    return out


def write_frame(df: pd.DataFrame, root: Path, name: str) -> None:
    df.to_parquet(root / f"{name}.parquet", index=False)
    if name != "ops":
        df.to_csv(root / f"{name}.csv", index=False)


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, args)
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + cfg["run_name"]
    root = Path(args.results) / "runs" / run_id
    root.mkdir(parents=True, exist_ok=True)
    stage(1, STEPS, "Configuration and pinned environment", "runner.py (load_config), envinfo.py (collect)")
    (root / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    env = envinfo.collect(HARNESS)
    (root / "environment.json").write_text(json.dumps(env, indent=2))
    log(f"run folder: {root}")
    log(f"config: {args.config}  seed={cfg['seed']}  trials={cfg['trials']}  modes={', '.join(cfg['modes'])}")
    log("datasets: " + ", ".join(f"{d['name']}={d['n_names']} names" for d in cfg["datasets"]))
    hw, sw = env["hardware"], env["software"]
    log(f"CPU: {hw['cpu_model']}  pinned to core(s) {hw['cpu_affinity']}")
    log(f"libraries: Verisign MTL {sw['mtl_reference_commit'][:10]} ({sw['mtl_reference_cflags']}), "
        f"liboqs {sw['liboqs_tag']}, {sw['openssl']}")

    stage(2, STEPS, "Go/no-go gate (PS-036: defer if the reference implementation fails)", "preflight.py")
    if args.skip_preflight:
        log("skipped (--skip-preflight)")
    else:
        verdict = preflight.run(root, HARNESS)
        log(f"verdict: {verdict['verdict']}")
        if verdict["verdict"] != "GO":
            print(json.dumps(verdict, indent=2))
            return 3

    stage(3, STEPS, "Build zones and RFC 4034 signing inputs", "workload.py (build_zone, signing_messages)")
    zones, paths, sizes, dataset_rows = {}, {}, {}, []
    for i, ds in enumerate(cfg["datasets"]):
        zone = build_zone(ds["name"], ds["n_names"], cfg["seed"] + i)
        zones[ds["name"]] = zone
        sizes[ds["name"]] = len(zone.items)
        classes = Counter(it.record_class for it in zone.items)
        log(f"zone '{ds['name']}' ({zone.origin}): {ds['n_names']} names -> {len(zone.items)} RRsets "
            f"(apex {classes['apex']}, small {classes['small']}, medium {classes['medium']}, "
            f"large {classes['large']}, NSEC3 {classes['nsec3']}), seed {cfg['seed'] + i}")
        for mk in cfg["modes"]:
            mode = MODES[mk]
            ddir = root / "datasets" / ds["name"] / mk
            ddir.mkdir(parents=True, exist_ok=True)
            msgs, manifest = signing_messages(zone, mode)
            write_messages(ddir / "messages.bin", msgs)
            paths[(ds["name"], mk)] = ddir / "messages.bin"
            limit = direct_limit(cfg, ds["name"], mk)
            sample = sample_indices(len(msgs), limit) if limit else list(range(len(msgs)))
            signed = set(sample)
            for i, row in enumerate(manifest):
                row["signed_in_trials"] = i in signed
            if limit and len(sample) < len(msgs):
                write_messages(ddir / "messages_sample.bin", [msgs[i] for i in sample])
                paths[(ds["name"], mk)] = ddir / "messages_sample.bin"
            pd.DataFrame(manifest).to_csv(ddir / "manifest.csv", index=False)
            log(f"  {mk:<12} {len(msgs)} signing inputs, {sum(map(len, msgs)):,} bytes -> "
                f"datasets/{ds['name']}/{mk}/messages.bin"
                + (f"  (capped: trials sign {len(sample)} evenly spaced RRsets)" if len(sample) < len(msgs) else ""))
            if mk == cfg["modes"][0]:
                (root / "datasets" / ds["name"] / "zone.txt").write_text(zone_text(zone, mode))
                counts = pd.DataFrame(manifest).record_class.value_counts().to_dict()
                dataset_rows.append({"dataset": ds["name"], "n_names": ds["n_names"], "rrsets": len(msgs),
                                     "signing_input_bytes": sum(map(len, msgs)), **counts})
    datasets = pd.DataFrame(dataset_rows)
    write_frame(datasets, root, "datasets")

    stage(4, STEPS, "Sign and verify every RRset, repeated trials",
          "runner.py (run_trials) -> harness.py (run_trial) -> harness/mtlbench.c -> libmtl / liboqs / OpenSSL")
    trials, ops = run_trials(cfg, root, paths, sizes)
    write_frame(trials, root, "trials")
    write_frame(ops, root, "ops")
    log(f"saved trials.csv ({len(trials)} rows), ops.parquet ({len(ops):,} individual operations), "
        "raw/*.json.gz, artifacts/ (real signatures)")

    stage(5, STEPS, "DNS answers, transmitted bytes and cache usage",
          "dnswire.py (response_catalog, query_workload, workload_accounting), cache.py, analytical.py")
    frames = analyse(cfg, root, zones, trials)
    for name, df in frames.items():
        write_frame(df, root, name)
    log("saved responses.csv, workload.csv, cache.csv, sizes.csv")

    stage(6, STEPS, "Statistics: medians and bootstrap 95% confidence intervals", "stats.py, runner.py (summarise)")
    summary = summarise(trials, frames)
    (root / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    for ds in cfg["datasets"]:
        for mk in cfg["modes"]:
            e = summary["trials"][ds["name"]][mk]
            s, v = e["sign_total_ns"], e["verify_warm_total_ns"]
            log(f"  {ds['name']:<7} {mk:<12} sign {s['median'] / 1e9:8.3f} s "
                f"[{s['median_ci95'][0] / 1e9:.3f}, {s['median_ci95'][1] / 1e9:.3f}]  "
                f"verify {v['median'] / 1e9:8.4f} s  checks {'passed' if e['all_checks_passed'] else 'FAILED'}"
                + ("  (signing sample, see extrapolation)" if e["limited"] else ""))
    log("saved summary.json")

    stage(7, STEPS, "Report and figures", "report.py, results.py")
    from . import report
    report.write(root)
    log(f"report: {root / 'report.md'} and report.html, figures/")
    failed = trials[(trials.exit_code != 0) | (trials.failures_total != 0) | (~trials.tamper_all_rejected)]
    log(f"DONE. {len(trials)} trials, {len(failed)} with failed checks. Results in {root}")
    log("view it: streamlit run dashboard/app.py   (pick this run in the sidebar)")
    return 1 if len(failed) else 0


def cmd_preflight(args: argparse.Namespace) -> int:
    verdict = preflight.run(Path(args.results), HARNESS)
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["verdict"] == "GO" else 3


def cmd_report(args: argparse.Namespace) -> int:
    from . import report
    print(report.write(Path(args.run)))
    return 0


def cmd_crosscheck(args: argparse.Namespace) -> int:
    from . import crosscheck, report
    df = crosscheck.run(Path(args.run), args.dataset, args.sample)
    print(crosscheck.summary(df).to_string(index=False))
    report.write(Path(args.run))
    return 0


def _csv(text: str | None) -> list[str] | None:
    return [x.strip() for x in text.split(",") if x.strip()] if text else None


def cmd_live(args: argparse.Namespace) -> int:
    from . import live, report
    live.run(Path(args.run), _csv(args.datasets), tuple(int(x) for x in _csv(args.udp)),
             tuple(_csv(args.profiles)), args.repeats, args.queries)
    report.write(Path(args.run))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from . import live
    live.serve(Path(args.run), args.dataset, args.profile, args.base_port)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cipherops")
    sub = p.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("preflight")
    pf.add_argument("--results", default="results")
    pf.set_defaults(func=cmd_preflight)
    pr = sub.add_parser("run")
    pr.add_argument("--config", default="configs/scenarios.yaml")
    pr.add_argument("--results", default="results")
    pr.add_argument("--trials", type=int)
    pr.add_argument("--modes")
    pr.add_argument("--datasets")
    pr.add_argument("--skip-preflight", action="store_true")
    pr.set_defaults(func=cmd_run)
    rp = sub.add_parser("report")
    rp.add_argument("--run", required=True)
    rp.set_defaults(func=cmd_report)
    cx = sub.add_parser("crosscheck", help="compare the byte model with responses from a real NSD server")
    cx.add_argument("--run", required=True)
    cx.add_argument("--dataset", help="default: the largest dataset")
    cx.add_argument("--sample", type=int, default=300)
    cx.set_defaults(func=cmd_crosscheck)
    lv = sub.add_parser("live", help="real client queries a real NSD server serving the run's signed zone")
    lv.add_argument("--run", required=True)
    lv.add_argument("--datasets", "--dataset", help="comma-separated; default: every dataset of the run")
    lv.add_argument("--queries", type=int, help="default: wire.workload.n_queries from the run config")
    lv.add_argument("--udp", default="1232,4096", help="EDNS UDP buffer sizes, comma-separated")
    lv.add_argument("--profiles", default="lan,wan,lossy", help="network profiles (live.PROFILES), comma-separated")
    lv.add_argument("--repeats", type=int, default=3)
    lv.set_defaults(func=cmd_live)
    sv = sub.add_parser("serve", help="keep one NSD per mode running for the dashboard (docker service 'serve')")
    sv.add_argument("--run", required=True)
    sv.add_argument("--dataset", help="default: the largest dataset")
    sv.add_argument("--profile", default="lan", help="network profile applied to eth0 (live.PROFILES)")
    sv.add_argument("--base-port", type=int, default=5301)
    sv.set_defaults(func=cmd_serve)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
