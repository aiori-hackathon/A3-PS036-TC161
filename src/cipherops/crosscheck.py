"""Cross-check the message-level byte model against a real authoritative server.

The zone from a finished run is written as a signed zone file, using the real
RRSIGs exported in that run's artifacts, and served by an unmodified NSD on
127.0.0.1:53 inside the container. For a sample of queries we compare, per mode:

  * response size over TCP and over UDP with a 4096-byte buffer, against
    dnswire's encoded response
  * the TC bit at a 1232-byte buffer, against the model's fit decision

NSD does not validate signatures, so the lab algorithm numbers load fine.
MTL full signatures are fetched with an EDNS option that NSD does not
implement, so only condensed (default) responses are cross-checked.
"""

from __future__ import annotations

import random
import socket
import struct
import subprocess
import tempfile
import time
from pathlib import Path

import dns.flags
import dns.message
import dns.rdatatype
import pandas as pd
import yaml

from . import dnswire
from .codepoints import MODES
from .harness import load_signatures
from .workload import build_zone, rrsig_template

HOST = "127.0.0.1"
PORT = 53
NSD_CONF = """\
server:
    ip-address: {host}@{port}
    port: {port}
    username: ""
    zonesdir: "{dir}"
    pidfile: "{dir}/nsd.pid"
    database: ""
    xfrdfile: "{dir}/xfrd.state"
    zonelistfile: "{dir}/zone.list"
    logfile: "{dir}/nsd.log"
    hide-version: yes
    minimal-responses: yes
    refuse-any: yes
    ipv4-edns-size: 4096
    ipv6-edns-size: 4096
    rrl-ratelimit: 0
    verbosity: 1
remote-control:
    control-enable: no
zone:
    name: "{origin}"
    zonefile: "zone.db"
"""


def signed_zone_text(zone, sigs, full_for: set[int] = frozenset()) -> str:
    """Zone file with one RRSIG per RRset; indexes in full_for carry the MTL full signature."""
    builder = dnswire.ResponseBuilder(zone, sigs)
    lines = []
    for idx, rrset in enumerate(builder.rrsets):
        lines.append(rrset.to_text())
        rrsig = rrsig_template(rrset, sigs.mode, builder.key_tag, zone.origin,
                               sigs.rrsig_field(idx, full=idx in full_for))
        lines.append(f"{rrset.name} {rrset.ttl} IN RRSIG {rrsig.to_text()}")
    return "\n".join(lines) + "\n"


def _udp(wire: bytes, timeout: float = 3.0, port: int = PORT, host: str = HOST) -> bytes:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(wire, (host, port))
        return s.recv(65535)


def _tcp(wire: bytes, timeout: float = 5.0, port: int = PORT, host: str = HOST) -> bytes:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(struct.pack(">H", len(wire)) + wire)
        hdr = b""
        while len(hdr) < 2:
            hdr += s.recv(2 - len(hdr))
        (ln,) = struct.unpack(">H", hdr)
        data = b""
        while len(data) < ln:
            data += s.recv(ln - len(data))
        return data


class NSD:
    def __init__(self, zone_text: str, origin: str, port: int = PORT, host: str = HOST):
        self.dir = Path(tempfile.mkdtemp(prefix="cx-nsd-"))
        self.port, self.host = port, host
        (self.dir / "zone.db").write_text(zone_text)
        (self.dir / "nsd.conf").write_text(NSD_CONF.format(host=host, port=port, dir=self.dir, origin=origin))
        self.proc = None

    def __enter__(self):
        check = subprocess.run(["nsd-checkzone", "bench.example.", str(self.dir / "zone.db")],
                               capture_output=True, text=True)
        if check.returncode != 0:
            raise RuntimeError(f"nsd-checkzone failed: {check.stdout}{check.stderr}")
        self.proc = subprocess.Popen(["nsd", "-d", "-c", str(self.dir / "nsd.conf")],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        probe = dns.message.make_query("bench.example.", "SOA").to_wire()
        for _ in range(100):
            try:
                _udp(probe, timeout=0.2, port=self.port, host=HOST if self.host == "0.0.0.0" else self.host)
                return self
            except OSError:
                time.sleep(0.1)
        self.__exit__()
        raise RuntimeError("nsd did not start: " + (self.dir / "nsd.log").read_text(errors="replace")[-1500:])

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def load_run_zone(run_dir: Path, dataset: str | None = None):
    """Rebuild the exact zone a run signed (default: its largest dataset)."""
    cfg = yaml.safe_load((Path(run_dir) / "config.yaml").read_text())
    ds_list = cfg["datasets"]
    idx, ds = next((i, d) for i, d in enumerate(ds_list)
                   if d["name"] == (dataset or max(ds_list, key=lambda d: d["n_names"])["name"]))
    return cfg, ds, build_zone(ds["name"], ds["n_names"], cfg["seed"] + idx)


def run(run_dir: Path, dataset: str | None = None, sample: int = 300) -> pd.DataFrame:
    run_dir = Path(run_dir)
    cfg, ds, zone = load_run_zone(run_dir, dataset)
    rng = random.Random(cfg["seed"])
    data_items = [it for it in zone.items if it.record_class in ("small", "medium", "large")]
    targets = [("dnskey", zone.origin, dns.rdatatype.DNSKEY)]
    targets += [("positive", it.owner, it.rdtype) for it in rng.sample(data_items, min(sample, len(data_items)))]
    targets += [("nxdomain", n, dns.rdatatype.A) for n in dnswire.nx_names(zone, max(20, sample // 5), cfg["seed"] + 7)]

    rows = []
    for mk in cfg["modes"]:
        mode = MODES[mk]
        artifacts = run_dir / "artifacts" / ds["name"] / mk
        sigs = load_signatures(mode, artifacts, limited=False)
        builder = dnswire.ResponseBuilder(zone, sigs)
        with NSD(signed_zone_text(zone, sigs), zone.origin.to_text()):
            for kind, qname, qtype in targets:
                _, model_r = builder.response(kind, qname, qtype, 4096)
                model_bytes = dnswire.wire_len(model_r)
                q4096 = builder.query(qname, qtype, 4096, False)
                q1232 = builder.query(qname, qtype, 1232, False)
                tcp = _tcp(q4096.to_wire())
                udp4096 = _udp(q4096.to_wire())
                udp1232 = _udp(q1232.to_wire())
                tc4096 = bool(dns.message.from_wire(udp4096, ignore_trailing=True).flags & dns.flags.TC)
                tc1232 = bool(dns.message.from_wire(udp1232, ignore_trailing=True).flags & dns.flags.TC)
                rows.append({
                    "dataset": ds["name"], "mode": mk, "kind": kind, "qname": qname.to_text(),
                    "qtype": dns.rdatatype.to_text(qtype), "model_bytes": model_bytes,
                    "nsd_tcp_bytes": len(tcp), "nsd_udp4096_bytes": len(udp4096),
                    "nsd_udp4096_tc": tc4096, "model_fits_1232": model_bytes <= 1232,
                    "nsd_udp1232_bytes": len(udp1232), "nsd_udp1232_tc": tc1232,
                })
    df = pd.DataFrame(rows)
    df["delta_tcp_bytes"] = df.nsd_tcp_bytes - df.model_bytes
    df["tc_agrees_1232"] = df.nsd_udp1232_tc == ~df.model_fits_1232
    df.to_csv(run_dir / "crosscheck.csv", index=False)
    return df


def summary(df: pd.DataFrame) -> pd.DataFrame:
    return (df.groupby(["mode", "kind"])
              .agg(queries=("model_bytes", "size"),
                   exact_size_match_pct=("delta_tcp_bytes", lambda s: 100 * (s == 0).mean()),
                   median_delta_bytes=("delta_tcp_bytes", "median"),
                   max_abs_delta_bytes=("delta_tcp_bytes", lambda s: s.abs().max()),
                   tc_agreement_1232_pct=("tc_agrees_1232", lambda s: 100 * s.mean()))
              .reset_index())
