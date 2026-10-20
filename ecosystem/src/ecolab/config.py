"""Lab configuration and the fixed address plan of the private DNS hierarchy."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ECO = Path(__file__).resolve().parents[2]          # ecosystem/
SUBNET = "10.53.0.0/24"
ROOT_IP = "10.53.0.2"
TLD_BASE = 10                                      # TLD servers: 10.53.0.11, .12, ...
SLD_BASE = 20                                      # SLD servers: 10.53.0.21, .22, ...
UNBOUND_IP = "10.53.0.53"
CLIENT_IP = "10.53.0.100"
ROOT_NS = "a.root-servers."
TIERS = ("root", "tld", "sld")


@dataclass
class Server:
    name: str            # compose service and container suffix
    ip: str
    tier: str            # root | tld | sld
    zones: list[str] = field(default_factory=list)


@dataclass
class ZoneSpec:
    origin: str          # absolute, e.g. "zone03.lab."
    tier: str
    server: str
    parent: str | None   # parent zone origin, None for the root
    n_names: int = 0     # synthetic data names (second-level zones only)
    seed: int = 0

    @property
    def file_stem(self) -> str:
        return "root" if self.origin == "." else self.origin.rstrip(".")

    @property
    def ns_name(self) -> str:
        return ROOT_NS if self.origin == "." else f"ns1.{self.origin}"


@dataclass
class Lab:
    cfg: dict
    servers: dict[str, Server]
    zones: list[ZoneSpec]

    @property
    def name(self) -> str:
        return self.cfg["name"]

    @property
    def stack(self) -> str:
        return os.environ.get("ECO_STACK", "dev")

    @property
    def tag(self) -> str:
        """Lab name plus component stack: signed zones and results never mix across stacks."""
        return f"{self.name}-{self.stack}"

    @property
    def work(self) -> Path:
        return ECO / "work" / self.tag

    def zone(self, origin: str) -> ZoneSpec:
        return next(z for z in self.zones if z.origin == origin)

    def children(self, origin: str) -> list[ZoneSpec]:
        return [z for z in self.zones if z.parent == origin]

    def server_ip(self, name: str) -> str:
        return self.servers[name].ip

    def tier_ips(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {t: [] for t in TIERS}
        for s in self.servers.values():
            out[s.tier].append(s.ip)
        return out


def load(path: str | Path) -> Lab:
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        p = ECO / p
    cfg = yaml.safe_load(p.read_text())
    h = cfg["hierarchy"]
    seed = int(cfg["seed"])

    servers: dict[str, Server] = {"root": Server("root", ROOT_IP, "root")}
    zones = [ZoneSpec(".", "root", "root", None)]
    servers["root"].zones.append(".")

    for i, tld in enumerate(h["tlds"]):
        name = f"tld-{tld}"
        servers[name] = Server(name, f"10.53.0.{TLD_BASE + 1 + i}", "tld", [f"{tld}."])
        zones.append(ZoneSpec(f"{tld}.", "tld", name, "."))

    for k in range(int(h["sld_servers"])):
        servers[f"sld{k + 1}"] = Server(f"sld{k + 1}", f"10.53.0.{SLD_BASE + 1 + k}", "sld")

    sizes = h["sld_sizes"]
    for i in range(int(h["sld_count"])):
        tld = h["tlds"][i % len(h["tlds"])]
        origin = f"zone{i:02d}.{tld}."
        srv = f"sld{i % int(h['sld_servers']) + 1}"
        servers[srv].zones.append(origin)
        zones.append(ZoneSpec(origin, "sld", srv, f"{tld}.", int(sizes[i % len(sizes)]), seed + 7919 * (i + 1)))

    return Lab(cfg, servers, zones)
