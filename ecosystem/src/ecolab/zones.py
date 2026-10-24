"""Unsigned zone files for the hierarchy.

Second-level zones reuse the main benchmark's synthetic content
(cipherops.workload.build_zone): the same small / medium / large RRset mix and
the same seeding. ldns-signzone adds DNSKEY, RRSIG, NSEC3 and NSEC3PARAM, so
only the data RRsets are taken from build_zone. Parents carry NS, glue and DS
for every child, as a real delegation does.
"""

from __future__ import annotations

from cipherops.workload import TTL, Zone, build_zone

from .config import Lab, ZoneSpec

SERIAL = 2026101401
DATA_CLASSES = ("small", "medium", "large")


def content(z: ZoneSpec) -> Zone:
    return build_zone(z.file_stem, z.n_names, z.seed, origin=z.origin)


def _hostmaster(z: ZoneSpec) -> str:
    return "hostmaster.root-servers." if z.origin == "." else f"hostmaster.{z.origin}"


def unsigned_text(lab: Lab, z: ZoneSpec, child_ds: dict[str, list[str]]) -> str:
    ip = lab.server_ip(z.server)
    lines = [
        f"$ORIGIN {z.origin}",
        f"$TTL {TTL}",
        f"{z.origin} {TTL} IN SOA {z.ns_name} {_hostmaster(z)} {SERIAL} 7200 3600 1209600 3600",
        f"{z.origin} {TTL} IN NS {z.ns_name}",
        f"{z.ns_name} {TTL} IN A {ip}",
    ]
    for c in lab.children(z.origin):
        lines.append(f"{c.origin} {TTL} IN NS {c.ns_name}")
        lines.append(f"{c.ns_name} {TTL} IN A {lab.server_ip(c.server)}")
        lines.extend(child_ds[c.origin])
    if z.tier == "sld":
        for it in content(z).items:
            if it.record_class in DATA_CLASSES:
                lines.append(it.rrset.to_text())
    return "\n".join(lines) + "\n"
