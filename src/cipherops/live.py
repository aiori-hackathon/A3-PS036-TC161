"""Live mode: a real client asks a real authoritative server for the query workload.

NSD serves the run's signed zone (real signatures) inside the container. The
client sends each lookup as a real UDP packet; on TC=1 it really retries over
TCP; when a UDP answer does not arrive in time it resends, like a resolver.
An MTL client without a ladder sends one real query carrying the
mtl-mode-full EDNS option over TCP. NSD does not implement that option, so a
second NSD instance, whose zone carries full signatures, stands in for a
server that does.

Network conditions come from Linux tc-netem on the interface the packets
cross (needs NET_ADMIN). On loopback every packet crosses the qdisc once, so
the one-way delay is half the round-trip time. The loopback MTU is set to
1500 so large UDP answers are IP-fragmented and TCP answers are segmented as
on Ethernet; one lost fragment loses the whole datagram.

`run` measures every dataset, both EDNS buffer sizes and every network profile,
repeated, and saves pcaps of the first repetition. `serve` keeps one NSD per
mode running for the dashboard to query from the host. Signatures are not
validated by the client; verification cost comes from the harness.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import dns.rdatatype
import pandas as pd
import yaml

from . import dnswire
from .codepoints import MODES
from .crosscheck import HOST, NSD, PORT, load_run_zone, signed_zone_text
from .harness import load_signatures

FULL_HOST = "127.0.0.2"
LO_MTU = 1500
PROFILES = {
    "lan": {"rtt_ms": 0, "loss_pct": 0.0, "udp_timeout_s": 1.0, "label": "LAN: no added delay or loss"},
    "wan": {"rtt_ms": 50, "loss_pct": 0.0, "udp_timeout_s": 0.4, "label": "WAN: 50 ms round trip"},
    "lossy": {"rtt_ms": 50, "loss_pct": 1.0, "udp_timeout_s": 0.4, "label": "Lossy WAN: 50 ms round trip, 1% packet loss"},
}
UDP_TRIES = 4
TCP_TIMEOUT_S = 10.0
NEEDS_CAP = ("network shaping needs the NET_ADMIN capability and tc: run it with "
             "`docker compose -f docker/compose.yml run --rm live ...`")


class Capture:
    """tcpdump writing DNS traffic (and IP fragments) to a pcap; a no-op without tcpdump.

    With `staged`, tcpdump writes to the container's /tmp and the file is moved into place on exit:
    the results bind mount is too slow for line-rate loopback bursts. `stats` holds tcpdump's counters."""

    def __init__(self, path: Path, iface: str = "lo", ports: str = f"port {PORT}", quiet: bool = False,
                 staged: bool = True):
        self.path, self.iface, self.ports, self.quiet, self.proc = path, iface, ports, quiet, None
        self.out = Path(tempfile.mkdtemp(prefix="cap-")) / path.name if staged else path
        self.stats: dict[str, int] = {}

    def __enter__(self):
        if not shutil.which("tcpdump"):
            print("   (tcpdump not installed: no pcap)")
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(["tcpdump", "-i", self.iface, "-p", "-U", "--immediate-mode", "-B", "131072",
                                      "-s", "65535", "-Z", "root", "-w", str(self.out),
                                      f"({self.ports}) or (ip[6:2] & 0x1fff != 0)"],
                                     stderr=subprocess.PIPE, text=True)
        line = self.proc.stderr.readline()
        if "listening" not in line:
            raise RuntimeError(f"tcpdump failed: {line}{self.proc.stderr.read()}")
        return self

    def __exit__(self, *exc):
        if self.proc:
            size, deadline = -1, time.monotonic() + 30
            while time.monotonic() < deadline:
                time.sleep(0.3)
                if self.out.stat().st_size == size:
                    break
                size = self.out.stat().st_size
            self.proc.send_signal(signal.SIGINT)
            report = self.proc.communicate(timeout=10)[1]
            for ln in report.splitlines():
                words = ln.split()
                if len(words) > 2 and words[0].isdigit() and words[1] == "packets":
                    self.stats[" ".join(words[2:]).replace(" ", "_")] = int(words[0])
            if self.out != self.path:
                shutil.move(self.out, self.path)
                self.out.parent.rmdir()
            if not self.quiet:
                print(f"   pcap: {self.path} ({self.stats})")
        return False


class Network:
    """tc-netem on one interface. `share` is the fraction of the round trip added per packet."""

    def __init__(self, dev: str = "lo", share: float = 0.5, mtu: int | None = LO_MTU, required: bool = True):
        self.dev, self.share, self.mtu, self.required = dev, share, mtu, required
        self.active, self.mtu0, self.profile = False, None, None

    def _cmd(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, text=True)

    def __enter__(self):
        problem = None
        if not (shutil.which("tc") and shutil.which("ip")):
            problem = "tc/ip not installed"
        else:
            self.mtu0 = int(Path(f"/sys/class/net/{self.dev}/mtu").read_text())
            if self.mtu:
                r = self._cmd("ip", "link", "set", "dev", self.dev, "mtu", str(self.mtu))
                problem = r.stderr.strip() if r.returncode else None
            if problem is None:
                r = self._cmd("tc", "qdisc", "replace", "dev", self.dev, "root", "netem", "delay", "0ms")
                problem = r.stderr.strip() if r.returncode else None
        if problem:
            if self.required:
                raise RuntimeError(f"{NEEDS_CAP} ({problem})")
            print(f"   (no network shaping: {problem}; MTU left at the default)")
            return self
        self.active = True
        return self

    def apply(self, profile: str) -> None:
        self.profile = profile
        if not self.active:
            return
        p = PROFILES[profile]
        args = ["tc", "qdisc", "replace", "dev", self.dev, "root", "netem",
                "delay", f"{p['rtt_ms'] * self.share:g}ms"]
        if p["loss_pct"]:
            args += ["loss", f"{p['loss_pct']:g}%"]
        r = self._cmd(*args)
        if r.returncode:
            raise RuntimeError(f"tc failed: {r.stderr}")

    def __exit__(self, *exc):
        if self.active:
            self._cmd("tc", "qdisc", "del", "dev", self.dev, "root")
            if self.mtu and self.mtu0:
                self._cmd("ip", "link", "set", "dev", self.dev, "mtu", str(self.mtu0))
        return False


def _recv_tcp(s: socket.socket) -> bytes:
    def exact(n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("connection closed mid-message")
            buf += chunk
        return buf
    (ln,) = struct.unpack(">H", exact(2))
    return exact(ln)


def exchange(wire: bytes, host: str = HOST, port: int = PORT, force_tcp: bool = False,
             udp_timeout: float = 1.0, udp_tries: int = UDP_TRIES, tcp_timeout: float = TCP_TIMEOUT_S,
             trace: list | None = None) -> dict:
    """One real lookup as a resolver does it: UDP with resends, then TCP when the answer is truncated.

    dns_bytes counts every DNS message sent or received, including resent queries and the 2-byte
    TCP length prefixes. With `trace`, each step is appended with its time and the answer is kept."""
    t0 = time.perf_counter_ns()
    qid = struct.unpack(">H", wire[:2])[0]
    res = {"qid": qid, "t_start": time.time(), "transport": "tcp" if force_tcp else "udp", "udp_retries": 0,
           "retry_bytes": 0, "tc_bytes": 0, "truncated": False, "failed": False, "response_bytes": 0,
           "dns_bytes": 0}

    def step(event: str, transport: str, nbytes: int = 0, info: str = "") -> None:
        if trace is not None:
            trace.append({"ms": (time.perf_counter_ns() - t0) / 1e6, "event": event, "transport": transport,
                          "bytes": nbytes, "info": info})

    def done(resp: bytes | None) -> dict:
        res["ms"] = (time.perf_counter_ns() - t0) / 1e6
        res["t_end"] = time.time()
        if resp is not None:
            res["response_bytes"] = len(resp)
            if trace is not None:
                res["response_wire"] = resp
        return res

    if not force_tcp:
        udp = None
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, port))
            for attempt in range(udp_tries):
                s.send(wire)
                res["dns_bytes"] += len(wire)
                step("send", "UDP", len(wire), "query" if attempt == 0 else f"query resent (try {attempt + 1})")
                deadline = time.monotonic() + udp_timeout
                try:
                    while udp is None:
                        s.settimeout(max(deadline - time.monotonic(), 1e-3))
                        data = s.recv(65535)
                        if len(data) >= 2 and struct.unpack(">H", data[:2])[0] == qid:
                            udp = data
                    break
                except (socket.timeout, ConnectionError) as e:
                    res["udp_retries"] += 1
                    step("timeout", "UDP", 0, f"no answer in {udp_timeout * 1000:.0f} ms"
                         if isinstance(e, socket.timeout) else "port unreachable")
        res["retry_bytes"] = res["dns_bytes"] - len(wire)
        if udp is None:
            res["failed"] = True
            return done(None)
        res["dns_bytes"] += len(udp)
        # TC from the header byte: parsing the whole answer with dnspython would cost ~0.3 ms per lookup.
        tc = len(udp) >= 4 and bool(udp[2] & 0x02)
        step("recv", "UDP", len(udp), "answer truncated (TC=1): retry over TCP" if tc else "answer")
        if not tc:
            return done(udp)
        res.update(transport="udp+tcp", truncated=True, tc_bytes=len(udp))
    try:
        with socket.create_connection((host, port), timeout=tcp_timeout) as s:
            step("connect", "TCP", 0, "3-way handshake done")
            s.sendall(struct.pack(">H", len(wire)) + wire)
            res["dns_bytes"] += 2 + len(wire)
            step("send", "TCP", 2 + len(wire), "query" + (" with mtl-mode-full option" if force_tcp else ""))
            resp = _recv_tcp(s)
    except OSError as e:
        res["failed"] = True
        step("error", "TCP", 0, str(e))
        return done(None)
    res["dns_bytes"] += 2 + len(resp)
    step("recv", "TCP", 2 + len(resp), "answer")
    return done(resp)


def _lookups(builder, workload, udp_limit: int, is_mtl: bool, udp_timeout: float) -> tuple[list[dict], int]:
    """The workload as a resolver with an empty cache sends it; returns rows and the cache-hit count."""
    seen, have_ladder, rows = set(), False, []
    for kind, qname, qtype in workload:
        if (qname, qtype) in seen:
            continue
        seen.add((qname, qtype))
        base = {"kind": kind, "qname": qname.to_text(), "qtype": dns.rdatatype.to_text(qtype)}
        res = exchange(builder.query(qname, qtype, udp_limit, False).to_wire(), udp_timeout=udp_timeout)
        rows.append({**base, "seq": len(rows), "ladder_fetch": False, **res})
        if is_mtl and not have_ladder:
            res = exchange(builder.query(qname, qtype, udp_limit, True).to_wire(), FULL_HOST, force_tcp=True)
            rows.append({**base, "seq": len(rows), "ladder_fetch": True, **res})
            have_ladder = True
    return rows, len(workload) - len(seen)


def _first_index(builder, zone, workload) -> int:
    kind, name, rdtype = workload[0]
    return builder.index[(zone.origin, dns.rdatatype.SOA)] if kind == "nxdomain" else builder.index[(name, rdtype)]


def _signatures(cfg: dict, run_dir: Path, dataset: str, mk: str):
    mode = MODES[mk]
    limited = bool((cfg.get("direct_sign_limit") or {}).get(dataset)) and mode.family == "pqc-direct"
    return load_signatures(mode, run_dir / "artifacts" / dataset / mk, limited)


def run(run_dir: Path, datasets: list[str] | None = None, udp_limits: tuple[int, ...] = (1232, 4096),
        profiles: tuple[str, ...] = tuple(PROFILES), repeats: int = 3, n_queries: int | None = None) -> pd.DataFrame:
    run_dir = Path(run_dir)
    cfg = yaml.safe_load((run_dir / "config.yaml").read_text())
    names = list(datasets or [d["name"] for d in cfg["datasets"]])
    unknown = set(profiles) - set(PROFILES)
    if unknown:
        raise ValueError(f"unknown profile(s) {sorted(unknown)}; choose from {list(PROFILES)}")
    shaped = any(PROFILES[p]["rtt_ms"] or PROFILES[p]["loss_pct"] for p in profiles)
    pcap_dir = run_dir / "pcap"
    for old in pcap_dir.glob("live-*.pcap"):
        old.unlink()
    rows, cells, models, captures = [], [], [], {}
    started = time.time()
    with Network("lo", share=0.5, mtu=LO_MTU, required=shaped) as net:
        print(f"live: datasets {names}, profiles {list(profiles)}, EDNS buffers {list(udp_limits)}, "
              f"{repeats} repeat(s); NSD on {HOST}:{PORT}, loopback MTU {LO_MTU if net.active else 'default'}")
        for name in names:
            cfg, ds, zone = load_run_zone(run_dir, name)
            wl = cfg["wire"]["workload"]
            workload = dnswire.query_workload(zone, n_queries or wl["n_queries"], wl["nx_fraction"],
                                              wl["zipf_s"], cfg["seed"])
            for mk in cfg["modes"]:
                mode = MODES[mk]
                sigs = _signatures(cfg, run_dir, name, mk)
                builder = dnswire.ResponseBuilder(zone, sigs)
                is_mtl = mode.family == "mtl"
                for udp in udp_limits:
                    model, _ = dnswire.workload_accounting(zone, sigs, workload, udp)
                    models.append({"dataset": name, "mode": mk, "udp_limit": udp,
                                   "model_bytes": int(model["dns_bytes"])})
                print(f"\n== {name} ({len(zone.items):,} RRsets) / {mode.label}")
                net.apply("lan")
                full = (NSD(signed_zone_text(zone, sigs, {_first_index(builder, zone, workload)}),
                            zone.origin.to_text(), host=FULL_HOST) if is_mtl else contextlib.nullcontext())
                with NSD(signed_zone_text(zone, sigs), zone.origin.to_text()), full:
                    for rep in range(repeats):
                        for prof in profiles:
                            net.apply(prof)
                            for udp in udp_limits:
                                pcap = pcap_dir / f"live-{name}-{mk}-{prof}-{udp}.pcap"
                                cap = Capture(pcap, quiet=True) if rep == 0 else contextlib.nullcontext()
                                with cap:
                                    cell, hits = _lookups(builder, workload, udp, is_mtl,
                                                          PROFILES[prof]["udp_timeout_s"])
                                if rep == 0:
                                    captures[pcap.name] = cap.stats
                                    if cap.stats.get("dropped_by_kernel"):
                                        print(f"   WARNING: tcpdump dropped {cap.stats['dropped_by_kernel']} "
                                              f"packets in {pcap.name}")
                                for r in cell:
                                    r.update(dataset=name, mode=mk, profile=prof, udp_limit=udp, repeat=rep)
                                rows += cell
                                cells.append({"dataset": name, "mode": mk, "profile": prof, "udp_limit": udp,
                                              "lookups": len(workload), "cache_hits": hits})
                                df = pd.DataFrame(cell)
                                print(f"   rep {rep} {prof:<5} {udp:>4} B: {len(df):>4} exchanges, "
                                      f"TC->TCP {int(df.truncated.sum()):>3}, resends {int(df.udp_retries.sum()):>2}, "
                                      f"failed {int(df.failed.sum())}, {int(df.dns_bytes.sum()):>10,} B, "
                                      f"{df.ms.sum() / 1e3:7.2f} s")
    live = pd.DataFrame(rows)
    live.drop(columns=[c for c in live if c == "response_wire"]).to_csv(run_dir / "live.csv", index=False)
    summary = summarize(live, pd.DataFrame(models), pd.DataFrame(cells).drop_duplicates())
    summary.to_csv(run_dir / "live_summary.csv", index=False)
    meta = {"started": started, "finished": time.time(), "datasets": names, "modes": cfg["modes"],
            "profiles": {p: PROFILES[p] for p in profiles}, "udp_limits": list(udp_limits), "repeats": repeats,
            "n_queries": n_queries or cfg["wire"]["workload"]["n_queries"], "interface": "lo",
            "mtu": LO_MTU if net.active else None, "netem": net.active, "netem_delay_share": 0.5,
            "udp_tries": UDP_TRIES, "tcp_timeout_s": TCP_TIMEOUT_S, "pcap_repeat": 0,
            "nsd_version": _nsd_version(), "captures": captures}
    (run_dir / "live.json").write_text(json.dumps(meta, indent=2))
    print(f"\nlive: {len(live):,} exchanges in {(time.time() - started) / 60:.1f} min -> live.csv, "
          f"live_summary.csv, live.json, pcap/live-*.pcap")
    return summary


def summarize(live: pd.DataFrame, models: pd.DataFrame, cells: pd.DataFrame) -> pd.DataFrame:
    """Per (dataset, mode, profile, buffer): medians across repeats, with the range of the total time."""
    keys = ["dataset", "mode", "profile", "udp_limit"]
    d = live.assign(sent=~live.ladder_fetch, udp_only=live.transport == "udp")
    per_rep = d.groupby(keys + ["repeat"]).agg(
        sent=("sent", "sum"), udp_only=("udp_only", "sum"), tc_then_tcp=("truncated", "sum"),
        ladder_fetches=("ladder_fetch", "sum"), udp_retries=("udp_retries", "sum"), failed=("failed", "sum"),
        live_bytes=("dns_bytes", "sum"), total_ms=("ms", "sum"), median_ms=("ms", "median"),
        p95_ms=("ms", lambda s: s.quantile(0.95))).reset_index()
    s = per_rep.groupby(keys).agg(
        repeats=("repeat", "size"), sent=("sent", "median"), udp_only=("udp_only", "median"),
        tc_then_tcp=("tc_then_tcp", "median"), ladder_fetches=("ladder_fetches", "median"),
        udp_retries=("udp_retries", "sum"), failed=("failed", "sum"), live_bytes=("live_bytes", "median"),
        total_ms=("total_ms", "median"), total_ms_min=("total_ms", "min"), total_ms_max=("total_ms", "max"),
        median_ms=("median_ms", "median"), p95_ms=("p95_ms", "median")).reset_index()
    s = s.merge(cells, on=keys, how="left").merge(models, on=["dataset", "mode", "udp_limit"], how="left")
    rank = {c: {k: i for i, k in enumerate(live[c].unique())} for c in ("dataset", "profile", "mode")}
    s = s.sort_values(["dataset", "profile", "udp_limit", "mode"],
                      key=lambda c: c.map(rank[c.name]) if c.name in rank else c)
    cols = ["dataset", "mode", "profile", "udp_limit", "repeats", "lookups", "cache_hits", "sent", "udp_only",
            "tc_then_tcp", "ladder_fetches", "udp_retries", "failed", "live_bytes", "model_bytes", "total_ms",
            "total_ms_min", "total_ms_max", "median_ms", "p95_ms"]
    return s[cols].reset_index(drop=True)


def _nsd_version() -> str:
    try:
        out = subprocess.run(["nsd", "-v"], capture_output=True, text=True)
        return (out.stdout + out.stderr).splitlines()[0].strip()
    except (OSError, IndexError):
        return "unknown"


def serve(run_dir: Path, dataset: str | None = None, profile: str = "lan", base_port: int = 5301,
          full_port: int = 5310, dev: str = "eth0", info_dir: Path = Path("results/live-server")) -> None:
    """Serve the run's zone, one NSD per mode on 0.0.0.0, until stopped. Writes server.json and a pcap."""
    run_dir = Path(run_dir)
    cfg, ds, zone = load_run_zone(run_dir, dataset)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    info_dir.mkdir(parents=True, exist_ok=True)
    for old in info_dir.glob("server.*"):
        old.unlink()
    ports, full = {}, None
    with contextlib.ExitStack() as stack:
        for i, mk in enumerate(cfg["modes"]):
            sigs = _signatures(cfg, run_dir, ds["name"], mk)
            stack.enter_context(NSD(signed_zone_text(zone, sigs), zone.origin.to_text(), port=base_port + i,
                                    host="0.0.0.0"))
            ports[mk] = base_port + i
            if MODES[mk].family == "mtl":
                every = set(range(len(dnswire.ResponseBuilder(zone, sigs).rrsets)))
                stack.enter_context(NSD(signed_zone_text(zone, sigs, every), zone.origin.to_text(), port=full_port,
                                        host="0.0.0.0"))
                full = {"mode": mk, "port": full_port}
        net = stack.enter_context(Network(dev, share=1.0, mtu=None, required=profile != "lan"))
        net.apply(profile)
        stack.enter_context(Capture(info_dir / "server.pcap", iface=dev,
                                    ports=f"portrange {base_port}-{full_port}", quiet=True, staged=False))
        info = {"run": run_dir.name, "dataset": ds["name"], "rrsets": len(zone.items), "profile": profile,
                "profile_detail": PROFILES[profile], "netem": net.active, "host": "127.0.0.1", "ports": ports,
                "full": full, "modes": cfg["modes"], "started": time.time(), "pcap": "server.pcap",
                "nsd_version": _nsd_version()}
        (info_dir / "server.json").write_text(json.dumps(info, indent=2))
        stack.callback(lambda: (info_dir / "server.json").unlink(missing_ok=True))
        print(f"serving {ds['name']} zone of {run_dir.name} ({PROFILES[profile]['label']}):")
        for mk, port in ports.items():
            print(f"   {MODES[mk].label:<28} 127.0.0.1:{port}  (UDP and TCP)")
        if full:
            print(f"   {'MTL full signatures':<28} 127.0.0.1:{full_port}")
        print("dashboard: Live demo -> Send a real query.  Stop with Ctrl+C.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
