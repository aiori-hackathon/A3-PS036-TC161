"""Zone shapes for the live lab: size presets, the Custom numbers, and the RRSIG / Prepare-time estimates."""

from __future__ import annotations

import json
import re

# Zone-size presets for the UI (written into configs/_dashboard.yaml before Prepare).
ZONE_SIZE_PRESETS: dict[str, dict] = {
    "small": {
        "label": "Small (smoke)",
        "name": "eco-ui-small",
        "tlds": ["test"],
        "sld_count": 2,
        "sld_sizes": [5],
        "sld_servers": 1,
        "n_queries_default": 40,
    },
    "medium": {
        "label": "Medium",
        "name": "eco-ui-medium",
        "tlds": ["test"],
        "sld_count": 4,
        "sld_sizes": [25, 50],
        "sld_servers": 1,
        "n_queries_default": 200,
    },
    "large": {
        "label": "Large (lab-scale)",
        "name": "eco-ui-large",
        "tlds": ["test", "lab"],
        "sld_count": 8,
        "sld_sizes": [25, 100, 25, 250],
        "sld_servers": 2,
        "n_queries_default": 1000,
    },
    # Numbers come from the sidebar; see custom_shape(). Defaults mirror Medium.
    "custom": {
        "label": "Custom…",
        "name": "eco-ui-custom",
        "tlds": ["test"],
        "sld_count": 4,
        "sld_sizes": [25, 50],
        "sld_servers": 1,
        "n_queries_default": 200,
    },
}

CUSTOM_SIZE = "custom"
DEFAULT_SEED = 20261005

# The lab's address plan (ecolab.config) gives TLD servers 10.53.0.11-19 and leaf servers 10.53.0.21-29.
MAX_TLDS = 9
MAX_SLD_SERVERS = 9
_TLD_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,14}$")

# Exact RRSIG model (checked against real signed zones). Every name has its record plus an NSEC3 proof, so
# 2 per name, and a zone adds 7 of its own (SOA, NS, DNSKEY, NSEC3PARAM, apex NSEC3 and so on).
# A TLD signs one name per child, the root one per TLD, and the root has one extra record: see exact_rrsigs.
RRSIG_PER_NAME = 2
RRSIG_PER_ZONE = 7
# Rough sign-time model, calibrated on the Medium lab (353 RRSIGs across root, TLD and leaf zones).
# Only meant to warn about long Prepares, not to predict them.
SIGN_SECONDS_PER_RRSIG = {
    "rsa": 0.0058, "ecdsa": 0.0007, "ed25519": 0.0007, "slh-dsa": 0.33, "mtl": 0.0087, "mixed-mtl": 0.0087,
}
PREPARE_OVERHEAD_S = 1.5          # key generation and checks per zone and scenario

# Defaults for the numbers the Benchmark tile can change (match configs/lab.yaml).
WORKLOAD_DEFAULTS = {"nx_fraction": 0.10, "zone_zipf_s": 1.0, "name_zipf_s": 1.1, "timeout_s": 5.0}
PROFILE_DEFAULTS: dict[str, dict[str, float]] = {
    "lan": {"client_ms": 0, "root_ms": 0, "tld_ms": 0, "sld_ms": 0, "loss_pct": 0},
    "wan": {"client_ms": 2, "root_ms": 20, "tld_ms": 35, "sld_ms": 50, "loss_pct": 0},
    "lossy": {"client_ms": 2, "root_ms": 20, "tld_ms": 35, "sld_ms": 50, "loss_pct": 1},
}


def _int_list(text: str) -> list[int]:
    out = []
    for tok in re.split(r"[,\s;]+", (text or "").strip()):
        if tok:
            out.append(int(tok))
    return out


def custom_shape(*, tlds: str, sld_count: int, sizes: str, servers: int,
                 seed: int = DEFAULT_SEED) -> tuple[dict | None, list[str], list[str]]:
    """Validate the Custom zone numbers.

    Returns (shape, errors, warnings). ``shape`` is None when there are errors. Its ``name`` embeds a
    short hash of the numbers, so signed zones and results of different shapes never share a folder.
    """
    errors: list[str] = []
    warnings: list[str] = []

    tld_list: list[str] = []
    for tok in re.split(r"[,\s;]+", (tlds or "").strip().lower()):
        if tok:
            tld_list.append(tok.strip("."))
    if not tld_list:
        errors.append("Enter at least one TLD.")
    bad = [t for t in tld_list if not _TLD_RE.match(t)]
    if bad:
        errors.append(f"TLD names use letters, digits and '-' (max 15 chars): {', '.join(bad)}.")
    if len(set(tld_list)) != len(tld_list):
        errors.append("TLD names must be unique.")
    if len(tld_list) > MAX_TLDS:
        errors.append(f"At most {MAX_TLDS} TLDs (the lab's address plan).")

    try:
        size_list = _int_list(sizes)
    except ValueError:
        size_list = []
        errors.append("Names per leaf zone must be whole numbers, e.g. 25, 100, 250.")
    else:
        if not size_list:
            errors.append("Enter at least one size for names per leaf zone.")
        elif min(size_list) < 1 or max(size_list) > 20000:
            errors.append("Names per leaf zone must be between 1 and 20000.")
        elif len(size_list) > 64:
            errors.append("At most 64 size entries.")

    sld_count = int(sld_count)
    servers = int(servers)
    if not 1 <= sld_count <= 200:
        errors.append("Leaf zones must be between 1 and 200.")
    if not 1 <= servers <= MAX_SLD_SERVERS:
        errors.append(f"Leaf servers must be between 1 and {MAX_SLD_SERVERS}.")
    elif 1 <= sld_count <= 200 and servers > sld_count:
        errors.append("More leaf servers than leaf zones: some servers would host nothing.")

    if errors:
        return None, errors, warnings

    shape = {
        "label": "Custom…",
        "tlds": tld_list,
        "sld_count": sld_count,
        "sld_sizes": size_list,
        "sld_servers": servers,
        "seed": int(seed),
        "custom": True,
    }
    import hashlib
    digest = hashlib.sha1(json.dumps(
        [tld_list, sld_count, size_list, servers, int(seed)]).encode()).hexdigest()[:8]
    shape["name"] = f"eco-ui-custom-{digest}"
    names = _total_names(shape)
    shape["n_queries_default"] = max(40, min(2000, names * 2))
    if names > 2000:
        warnings.append(f"{names:,} names in total: signing with SLH-DSA will take a long time.")
    if len(tld_list) + servers + 3 > 12:
        warnings.append("Many containers will start; check Docker has enough memory.")
    return shape, errors, warnings


def _total_names(shape: dict) -> int:
    sizes = shape["sld_sizes"]
    return sum(int(sizes[i % len(sizes)]) for i in range(int(shape["sld_count"])))


def shape_for(zone_size: str, custom: dict | None = None) -> tuple[dict | None, list[str], list[str]]:
    """The zone shape for a preset key, or the validated Custom numbers."""
    if zone_size == CUSTOM_SIZE:
        return custom_shape(**(custom or {"tlds": "test", "sld_count": 4, "sizes": "25, 50", "servers": 1}))
    if zone_size not in ZONE_SIZE_PRESETS:
        raise ValueError(f"unknown zone size {zone_size!r}")
    return ZONE_SIZE_PRESETS[zone_size], [], []


def exact_rrsigs(names: int, zones: int) -> int:
    """RRSIGs for a whole hierarchy of ``zones`` zones whose leaf zones hold ``names`` names in total.

    Leaf zone = 2*names + 7, TLD = 2*children + 7, root = 2*TLDs + 8. Every zone except the root is a child
    of exactly one other, so the "children" terms add 2 per non-root zone, and the root has one extra.
    """
    return RRSIG_PER_NAME * names + RRSIG_PER_ZONE * zones + RRSIG_PER_NAME * (zones - 1) + 1


def shape_stats(shape: dict, scenarios: list[str], jobs: int = 2) -> dict:
    """Plain numbers describing a zone shape, plus a rough Prepare-time estimate."""
    zones = 1 + len(shape["tlds"]) + int(shape["sld_count"])
    names = _total_names(shape)
    rrsigs = exact_rrsigs(names, zones)
    cpu = sum(SIGN_SECONDS_PER_RRSIG.get(s, 0.01) for s in scenarios) * rrsigs
    wall = cpu / max(1, int(jobs)) + PREPARE_OVERHEAD_S * zones * max(1, len(scenarios)) / max(1, int(jobs))
    return {
        "zones": zones,
        "names": names,
        # root + TLD servers + leaf servers, plus the resolver and the client
        "containers": 1 + len(shape["tlds"]) + int(shape["sld_servers"]) + 2,
        "est_rrsigs": int(rrsigs),
        "est_prepare_s": wall,
    }


def format_duration(seconds: float) -> str:
    s = int(round(seconds))
    if s < 90:
        return f"~{max(s, 1)} s"
    if s < 5400:
        return f"~{round(s / 60)} min"
    return f"~{s / 3600:.1f} h"
