"""Trials over scenarios x UDP limits x network profiles, with per-hop captures.

For every scenario the frozen signed zones are loaded into the NSD servers and
the root trust anchor into Unbound. Each trial empties Unbound's caches
(including the ladder cache), replays the workload once (cold pass) and then
again (warm pass). Each pass is captured on the resolver's interface and split
per hop: stub <-> resolver, resolver <-> root / TLD / SLD servers.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pandas as pd

from cipherops import pcap
from cipherops.stats import bootstrap_ci

from . import control
from .client import replay, workload
from .config import CLIENT_IP, ECO, UNBOUND_IP, Lab
from .sign import check_frozen, load_manifest
from .topology import write_active

STAT_KEYS = ("total.num.queries", "total.num.cachehits", "total.num.cachemiss", "total.recursion.time.avg",
             "total.recursion.time.median", "num.answer.secure", "num.answer.bogus", "num.answer.rcode.NOERROR",
             "num.answer.rcode.NXDOMAIN", "num.answer.rcode.SERVFAIL", "num.query.tcpout", "num.query.udpout",
             "mem.cache.rrset", "mem.cache.message", "mem.mod.validator", "mem.mod.iterator")


def hop_rows(path: Path, lab: Lab) -> list[dict]:
    df = pcap.read(path)
    tiers = {CLIENT_IP: "client"}
    for tier, ips in lab.tier_ips().items():
        tiers.update({ip: tier for ip in ips})
    out = []
    if df.empty:
        return out
    df["peer"] = df.dst.where(df.src == UNBOUND_IP, df.src)
    df["tier"] = df.peer.map(tiers)
    df["to_resolver"] = df.dst == UNBOUND_IP
    for tier in ("client", "root", "tld", "sld"):
        d = df[df.tier == tier]
        resp_from_peer = (d.dns_qr == True) & (d.to_resolver == (tier != "client"))  # noqa: E712
        out.append({
            "hop": tier,
            "packets": len(d),
            "udp_bytes": int(d[d.proto == "UDP"].payload.sum()),
            "tcp_bytes": int(d[d.proto == "TCP"].payload.sum()),
            "fragments": int((d.proto == "IP fragment").sum()),
            "queries": int(((d.dns_qr == False) & (d.payload > 0) & d.proto.isin(["UDP", "TCP"])).sum()),  # noqa: E712
            "truncated": int(((d.proto == "UDP") & (d.dns_tc == True) & resp_from_peer).sum()),  # noqa: E712
            "tcp_connections": int((d.tcp_flags == "SYN").sum()),
        })
    for r in out:
        r["bytes"] = r["udp_bytes"] + r["tcp_bytes"]
    return out


def _pass_row(rows: list[dict]) -> dict:
    q = pd.DataFrame(rows)
    ok = q[~q.timeout]
    return {
        "queries": len(q),
        "median_ms": float(ok.latency_ms.median()) if len(ok) else float("nan"),
        "p95_ms": float(ok.latency_ms.quantile(0.95)) if len(ok) else float("nan"),
        "mean_ms": float(ok.latency_ms.mean()) if len(ok) else float("nan"),
        "total_ms": float(q.latency_ms.sum()),
        "validated_pct": 100.0 * float(q.ad.mean()),
        "servfail": int((q.rcode == "SERVFAIL").sum()),
        "timeouts": int(q.timeout.sum()),
        "stub_tcp_fallbacks": int(q.tc.sum()),
        "stub_bytes": int((q.query_bytes + q.response_bytes + q.tcp_query_bytes + q.tcp_response_bytes).sum()),
    }


def environment(lab: Lab) -> dict:
    info = control._docker().info()
    env = {k: v for k, v in os.environ.items() if k.startswith(("ECO_", "CIPHEROPS_"))}
    images = {}
    for s in [*lab.servers, "unbound", "client"]:
        c = control.get(s)
        images[s] = {"image": c.image.tags[0] if c.image.tags else c.image.id, "image_id": c.image.id}
    return {"captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "client_env": env,
            "docker": {k: info.get(k) for k in ("ServerVersion", "OperatingSystem", "KernelVersion", "NCPU", "MemTotal")},
            "images": images}


def run(lab: Lab, scenarios: list[str], udp_limits: list[int], profiles: list[str], trials: int, log=print) -> Path:
    run_id = time.strftime("%Y%m%d-%H%M%S")
    rdir = ECO / "results" / lab.tag / run_id
    (rdir / "pcap").mkdir(parents=True)
    items = workload(lab)
    pd.DataFrame(items).to_csv(rdir / "workload.csv", index=False)
    meta = {"lab": lab.cfg, "scenarios": scenarios, "udp_limits": udp_limits, "profiles": profiles,
            "trials": trials, "environment": environment(lab),
            "manifests": {s: load_manifest(lab, s) for s in scenarios}}
    (rdir / "run.json").write_text(json.dumps(meta, indent=2, default=str))

    queries, passes, hops = [], [], []
    for scenario in scenarios:
        check_frozen(lab, scenario)
        manifest = load_manifest(lab, scenario)
        for udp in udp_limits:
            write_active(lab, scenario, manifest, udp)
            control.restart(list(lab.servers) + ["unbound"])
            control.wait_servers(lab)
            control.unbound_ready()
            for profile in profiles:
                control.shape(lab, lab.cfg["network"]["profiles"][profile])
                for trial in range(trials):
                    control.flush_resolver()
                    for pass_ in ("cold", "warm"):
                        key = {"scenario": scenario, "udp_limit": udp, "profile": profile, "trial": trial, "pass": pass_}
                        name = f"{scenario}-{udp}-{profile}-t{trial}-{pass_}.pcap"
                        log(f"  {scenario:<10} udp {udp}  {profile:<5}  trial {trial + 1}/{trials}  {pass_} pass")
                        control.start_capture(f"/results/{lab.tag}/{run_id}/pcap/{name}")
                        t0 = time.time()
                        rows = replay(lab, items)
                        wall = time.time() - t0
                        time.sleep(0.3)
                        dropped = control.stop_capture()
                        if dropped:
                            log(f"      WARNING: capture dropped {dropped} packets; byte counts for this pass are low")
                        st = control.unbound_stats()
                        h = hop_rows(rdir / "pcap" / name, lab)
                        queries += [{**key, **r} for r in rows]
                        hops += [{**key, **r} for r in h]
                        up = [r for r in h if r["hop"] != "client"]
                        prow = {**key, "wall_s": wall, **_pass_row(rows), "capture_dropped": dropped,
                                "upstream_bytes": sum(r["bytes"] for r in up),
                                "upstream_queries": sum(r["queries"] for r in up),
                                "upstream_truncated": sum(r["truncated"] for r in up),
                                "upstream_tcp_connections": sum(r["tcp_connections"] for r in up),
                                "upstream_fragments": sum(r["fragments"] for r in up),
                                **{f"hop_{r['hop']}_bytes": r["bytes"] for r in h},
                                **{k: st.get(k) for k in STAT_KEYS}}
                        passes.append(prow)
                        log(f"      median {prow['median_ms']:.1f} ms  validated {prow['validated_pct']:.1f}%  "
                            f"upstream {prow['upstream_bytes'] / 1e3:.1f} KB in {prow['upstream_queries']} queries "
                            f"(unbound sent {st.get('num.query.udpout', 0) + st.get('num.query.tcpout', 0):.0f})  "
                            f"servfail {prow['servfail']}  timeouts {prow['timeouts']}")
                    pd.DataFrame(passes).to_csv(rdir / "passes.csv", index=False)
    pd.DataFrame(queries).to_csv(rdir / "queries.csv", index=False)
    pd.DataFrame(hops).to_csv(rdir / "hops.csv", index=False)
    summarize(rdir)
    return rdir


SUMMARY_COLS = ("median_ms", "p95_ms", "total_ms", "upstream_bytes", "upstream_queries", "upstream_truncated",
                "upstream_tcp_connections", "hop_client_bytes", "hop_root_bytes", "hop_tld_bytes", "hop_sld_bytes",
                "validated_pct", "servfail", "timeouts", "mem.cache.rrset", "mem.cache.message")


def summarize(rdir: Path) -> pd.DataFrame:
    p = pd.read_csv(rdir / "passes.csv")
    rows = []
    for key, g in p.groupby(["scenario", "udp_limit", "profile", "pass"], sort=False):
        row = dict(zip(["scenario", "udp_limit", "profile", "pass"], key))
        row["trials"] = len(g)
        for c in SUMMARY_COLS:
            if c in g and g[c].notna().any():
                v = g[c].dropna().astype(float)
                row[c] = float(v.median())
                if c in ("median_ms", "total_ms", "upstream_bytes"):
                    row[f"{c}_ci_lo"], row[f"{c}_ci_hi"] = bootstrap_ci(v.to_numpy())
        rows.append(row)
    s = pd.DataFrame(rows)
    s.to_csv(rdir / "summary.csv", index=False)
    return s
