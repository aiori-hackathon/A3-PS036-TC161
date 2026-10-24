"""ecolab command line.

  prepare CONFIG             sign every scenario (tools image of the stack) and write the compose file
  smoke   CONFIG             resolve a few names per scenario through Unbound and require AD (validated)
  run     CONFIG             full trials; results in ecosystem/results/<lab>/<run id>/
  summary RUN_DIR            rebuild summary.csv from passes.csv
  activate CONFIG --scenario S   switch a running lab to scenario S (panel live demo)
  probe   QNAME [QTYPE]      one live lookup as JSON (panel live demo)
  flush                      restart Unbound to clear all caches including MTL ladders
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load


def _list(v: str | None, default: list) -> list:
    return [x.strip() for x in v.split(",") if x.strip()] if v else list(default)


def cmd_prepare(a) -> None:
    from .sign import sign_scenario
    from .topology import write_active, write_compose

    from .sign import load_manifest

    lab = load(a.config)
    scenarios = _list(a.scenarios, lab.cfg["scenarios"])
    print(f"Lab {lab.tag}: {len(lab.zones)} zones on {len(lab.servers)} NSD servers")
    for s in scenarios:
        m = sign_scenario(lab, s, jobs=a.jobs)
        print(f"  [{s}] trust anchor: {m['trust_anchor'][0]}")
    path = write_compose(lab, lab.stack)
    write_active(lab, scenarios[0], load_manifest(lab, scenarios[0]), int(lab.cfg["resolver"]["udp_limits"][0]))
    print(f"Compose file: ecosystem/{path.relative_to(path.parents[2]).as_posix()}")


def cmd_smoke(a) -> int:
    import dns.rdatatype

    from . import control
    from .client import lookup
    from .sign import load_manifest
    from .topology import write_active
    from .zones import DATA_CLASSES, content
    from cipherops.dnswire import nx_names

    lab = load(a.config)
    udp = int(lab.cfg["resolver"]["udp_limits"][0])
    failed = 0
    for s in _list(a.scenarios, lab.cfg["scenarios"]):
        print(f"[{s}] {lab.cfg['scenarios'][s]}")
        write_active(lab, s, load_manifest(lab, s), udp)
        control.restart(list(lab.servers) + ["unbound"])
        control.wait_servers(lab)
        control.unbound_ready()
        control.shape(lab, {})
        control.flush_resolver()
        checks = [(".", "DNSKEY")] + [(z.origin, "DNSKEY") for z in lab.zones if z.tier == "tld"]
        for z in (z for z in lab.zones if z.tier == "sld"):
            zc = content(z)
            it = next(i for i in zc.items if i.record_class in DATA_CLASSES)
            checks += [(it.owner.to_text(), dns.rdatatype.to_text(it.rdtype)),
                       (nx_names(zc, 1, 7)[0].to_text(), "A")]
        for qname, qtype in checks:
            r = lookup(qname, qtype, True, 15.0)
            ok = r["ad"] and r["rcode"] in ("NOERROR", "NXDOMAIN")
            failed += not ok
            print(f"   {'ok  ' if ok else 'FAIL'} {qname:<34} {qtype:<6} {r['rcode']:<8} AD={int(r['ad'])} "
                  f"{r['response_bytes'] + r['tcp_response_bytes']:>6} B {r['transport']:<7} {r['latency_ms']:8.1f} ms")
        st = control.unbound_stats()
        print(f"   unbound: secure {st.get('num.answer.secure', 0):.0f}  bogus {st.get('num.answer.bogus', 0):.0f}  "
              f"upstream tcp {st.get('num.query.tcpout', 0):.0f}")
        if failed:
            print(control.get("unbound").logs(tail=30).decode(errors="replace"))
    print("SMOKE " + ("PASSED" if not failed else f"FAILED ({failed} checks)"))
    return 1 if failed else 0


def cmd_run(a) -> None:
    from .run import run

    lab = load(a.config)
    rdir = run(lab,
               _list(a.scenarios, lab.cfg["scenarios"]),
               [int(x) for x in _list(a.udp, lab.cfg["resolver"]["udp_limits"])],
               _list(a.profiles, lab.cfg["network"]["run"]),
               int(a.trials or lab.cfg["trials"]))
    print(f"Results: {rdir}")


def cmd_summary(a) -> None:
    from .run import summarize

    print(summarize(Path(a.run_dir)).to_string(index=False))


def cmd_activate(a) -> int:
    """Switch the running lab to a scenario (rewrite active configs + restart)."""
    from . import control
    from .sign import load_manifest
    from .topology import write_active

    lab = load(a.config)
    scenario = a.scenario
    if scenario not in lab.cfg["scenarios"]:
        raise SystemExit(f"unknown scenario {scenario!r}; choose from {list(lab.cfg['scenarios'])}")
    udp = int(a.udp or lab.cfg["resolver"]["udp_limits"][0])
    write_active(lab, scenario, load_manifest(lab, scenario), udp)
    control.restart(list(lab.servers) + ["unbound"])
    control.wait_servers(lab)
    control.unbound_ready()
    control.shape(lab, {})
    print(f"active: {scenario}  udp={udp}  trust-anchor loaded")
    return 0


def cmd_probe(a) -> int:
    from .probe import main as probe_main

    argv = [a.qname, a.qtype, "--server", a.server, "--udp", str(a.udp)]
    if a.no_dnssec:
        argv.append("--no-dnssec")
    if a.no_ad:
        argv.append("--no-ad")
    if a.norecurse:
        argv.append("--norecurse")
    if a.mtl_full:
        argv.append("--mtl-full")
    return probe_main(argv)


def cmd_flush(a) -> int:
    from . import control

    control.flush_resolver()
    print("resolver caches cleared (Unbound process restarted)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ecolab")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("config")
    p.add_argument("--scenarios")
    p.add_argument("--jobs", type=int, default=4)
    p.set_defaults(fn=cmd_prepare)
    p = sub.add_parser("smoke")
    p.add_argument("config")
    p.add_argument("--scenarios")
    p.set_defaults(fn=cmd_smoke)
    p = sub.add_parser("run")
    p.add_argument("config")
    p.add_argument("--scenarios")
    p.add_argument("--udp")
    p.add_argument("--profiles")
    p.add_argument("--trials", type=int)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("summary")
    p.add_argument("run_dir")
    p.set_defaults(fn=cmd_summary)
    p = sub.add_parser("activate")
    p.add_argument("config")
    p.add_argument("--scenario", required=True)
    p.add_argument("--udp", type=int)
    p.set_defaults(fn=cmd_activate)
    p = sub.add_parser("probe")
    p.add_argument("qname")
    p.add_argument("qtype", nargs="?", default="A")
    p.add_argument("--server", default="10.53.0.53")
    p.add_argument("--udp", type=int, default=1232)
    p.add_argument("--no-dnssec", action="store_true")
    p.add_argument("--no-ad", action="store_true")
    p.add_argument("--norecurse", action="store_true")
    p.add_argument("--mtl-full", action="store_true")
    p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("flush")
    p.set_defaults(fn=cmd_flush)
    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
