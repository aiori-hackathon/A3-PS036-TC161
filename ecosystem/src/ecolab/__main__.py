"""ecolab command line.

  prepare CONFIG             sign every scenario (tools image of the stack) and write the compose file
  smoke   CONFIG             resolve a few names per scenario through Unbound and require AD (validated)
  run     CONFIG             full trials; results in ecosystem/results/<lab>/<run id>/
  summary RUN_DIR            rebuild summary.csv from passes.csv
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
    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
