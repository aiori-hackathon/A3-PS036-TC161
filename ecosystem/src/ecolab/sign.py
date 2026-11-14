"""Sign the hierarchy with Verisign's MTL-mode ldns, bottom-up.

Second-level zones are signed first, then each TLD with its children's DS
records, then the root, whose KSK DS becomes Unbound's trust anchor. Every
signed zone must pass ldns-verify-zone and nsd-checkzone. The signed files are
then frozen: the manifest records their SHA-256 so every trial serves exactly
the same bytes (SLH-DSA signing is randomised, re-signing would not be).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import Lab, ZoneSpec
from .zones import unsigned_text

_EVENT_LOCK = threading.Lock()


def emit(event: str, **fields) -> None:
    """One machine-readable line (``@@EVENT {json}``) for the dashboard's live view.

    Silent unless ``ECOLAB_EVENTS=1``, so the command line and the benchmark suite print exactly what
    they always did. Zones sign in a thread pool, so the lock keeps each line whole.
    """
    if os.environ.get("ECOLAB_EVENTS") != "1":
        return
    with _EVENT_LOCK:
        print("@@EVENT " + json.dumps({"event": event, "t": round(time.time(), 3), **fields}), flush=True)


def _run(args: list[str], cwd: Path) -> str:
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed ({p.returncode}):\n{p.stdout}\n{p.stderr}")
    return p.stdout


# Config names that the two ldns branches spell differently (same DNSSEC algorithm number).
LDNS_NAMES = {"SLH-DSA-SHA2-128s": {"draft06": "SLH_DSA_SHA2_128s", "latest": "SLH_DSA_PURE_SHA2_128S"}}


def ldns_name(alg: str, stack: str) -> str:
    table = LDNS_NAMES.get(alg, {})
    if stack in table:
        return table[stack]
    # PowerDNS auth lanes reuse the draft06 ldns signer pins (same algorithm spellings).
    if stack.startswith("powerdns-") and "draft06" in table:
        return table["draft06"]
    return alg


def _keygen(alg: str, origin: str, ksk: bool, cwd: Path, stack: str) -> str:
    args = ["ldns-keygen", "-a", ldns_name(alg, stack)]
    if alg.upper().startswith("RSA"):
        args += ["-b", "2048"]
    if ksk:
        args.append("-k")
    return _run(args + [origin], cwd).strip().splitlines()[-1]


def _ds(key: str, cwd: Path) -> list[str]:
    out = _run(["ldns-key2ds", "-n", "-2", f"{key}.key"], cwd)
    return [l.strip() for l in out.splitlines() if "\tDS\t" in l or " DS " in l]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rrsig_count(path: Path) -> int:
    """Number of RRSIG records in a signed zone file.

    Only real RRSIG records count: an NSEC3 line lists RRSIG in its type bitmap, and matching on the
    bare word used to count every NSEC3 record as well (about 1.5x too many).
    """
    n = 0
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.split(None, 5)  # the base64 signature tail is never needed
            if any(parts[i] == "IN" and parts[i + 1] == "RRSIG" for i in range(min(4, len(parts) - 1))):
                n += 1
    return n


def sign_zone(lab: Lab, scenario: str, z: ZoneSpec, child_ds: dict[str, list[str]], out: Path) -> dict:
    alg = lab.cfg["scenarios"][scenario][z.tier]
    sig = lab.cfg["signing"]
    kdir = out / "keys" / z.file_stem
    kdir.mkdir(parents=True, exist_ok=True)
    unsigned = kdir / f"{z.file_stem}.zone"
    unsigned.write_text(unsigned_text(lab, z, child_ds))

    ksk = _keygen(alg, z.origin, True, kdir, lab.stack)
    keys = [ksk] if lab.cfg["hierarchy"]["key_scheme"] == "csk" else [ksk, _keygen(alg, z.origin, False, kdir, lab.stack)]
    signed = out / f"{z.file_stem}.zone.signed"
    t0 = time.perf_counter()
    _run(["ldns-signzone", "-n", "-t", str(sig["nsec3_iterations"]), "-i", str(sig["inception"]),
          "-e", str(sig["expiration"]), "-o", z.origin, "-f", str(signed), str(unsigned), *keys], kdir)
    sign_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    _run(["ldns-verify-zone", str(signed)], kdir)
    verify_s = time.perf_counter() - t0
    _run(["nsd-checkzone", z.origin, str(signed)], kdir)

    return {"origin": z.origin, "tier": z.tier, "server": z.server, "algorithm": alg,
            "keys": keys, "ds": _ds(ksk, kdir), "n_names": z.n_names,
            "signed_file": signed.name, "signed_bytes": signed.stat().st_size,
            "signed_sha256": _sha256(signed), "rrsig_count": _rrsig_count(signed),
            "sign_seconds": round(sign_s, 3), "verify_seconds": round(verify_s, 3)}


def sign_scenario(lab: Lab, scenario: str, jobs: int = 1, log=print) -> dict:
    out = lab.work / "zones" / scenario
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    done: dict[str, dict] = {}
    emit("scenario_start", scenario=scenario, algorithms=lab.cfg["scenarios"][scenario], jobs=jobs,
         zones=[{"origin": z.origin, "tier": z.tier, "parent": z.parent, "n_names": z.n_names} for z in lab.zones])
    for tier in ("sld", "tld", "root"):
        todo = [z for z in lab.zones if z.tier == tier]
        child_ds = {o: r["ds"] for o, r in done.items()}

        def one(z: ZoneSpec) -> dict:
            emit("zone_start", scenario=scenario, origin=z.origin, tier=z.tier)
            r = sign_zone(lab, scenario, z, child_ds, out)
            log(f"  [{scenario}] {z.origin:<14} {r['algorithm']:<32} {r['rrsig_count']:>5} RRSIGs  "
                f"{r['signed_bytes'] / 1e6:7.2f} MB  signed in {r['sign_seconds']:.1f} s")
            emit("zone_done", scenario=scenario, origin=r["origin"], tier=r["tier"], algorithm=r["algorithm"],
                 n_names=r["n_names"], rrsigs=r["rrsig_count"], sign_s=r["sign_seconds"],
                 verify_s=r["verify_seconds"], signed_bytes=r["signed_bytes"])
            return r

        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            for r in pool.map(one, todo):
                done[r["origin"]] = r
    manifest = {"scenario": scenario, "algorithms": lab.cfg["scenarios"][scenario],
                "trust_anchor": done["."]["ds"], "signing": lab.cfg["signing"], "jobs": jobs,
                "zones": [done[z.origin] for z in lab.zones]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    emit("scenario_done", scenario=scenario)
    return manifest


def load_manifest(lab: Lab, scenario: str) -> dict:
    return json.loads((lab.work / "zones" / scenario / "manifest.json").read_text())


def check_frozen(lab: Lab, scenario: str) -> None:
    m = load_manifest(lab, scenario)
    for z in m["zones"]:
        f = lab.work / "zones" / scenario / z["signed_file"]
        if _sha256(f) != z["signed_sha256"]:
            raise RuntimeError(f"{f} changed since signing (SHA-256 mismatch)")


def sign_trials(lab: Lab, scenario: str, trials: int, names: list[int], log=print, warmup: int = 1) -> list[dict]:
    """Repeat signing and verification of sample leaf zones; one row per trial and sample size.

    Each sample uses the scenario's leaf-tier algorithm and the same synthetic content generator as the
    real zones. Two or more sample sizes let the report separate the fixed cost per zone from the cost
    per RRSIG, and extrapolate to the whole dataset without signing it again for every trial. Nothing
    here is served: the frozen zones from sign_scenario stay untouched.

    ``warmup`` extra rounds on the smallest sample run first and are discarded: the first signing after a
    start pays one-off costs (loading the libraries, cold caches) that are not part of the repeated cost.
    """
    tld = next(z for z in lab.zones if z.tier == "tld")
    first = next(z for z in lab.zones if z.tier == "sld")
    rows = []
    base = lab.work / "signtrials" / scenario
    for t in range(1 - int(warmup), int(trials) + 1):
        for n in ([min(names)] if t < 1 else names):
            spec = ZoneSpec(f"sample.{tld.origin}", "sld", first.server, tld.origin, int(n),
                            int(lab.cfg["seed"]) + 104729)
            out = base / f"t{t}-n{n}"
            if out.exists():
                shutil.rmtree(out)
            out.mkdir(parents=True)
            r = sign_zone(lab, scenario, spec, {}, out)
            if t < 1:
                log(f"  [{scenario}] warm-up, {n} names (discarded)")
                shutil.rmtree(out, ignore_errors=True)
                continue
            rows.append({"scenario": scenario, "trial": t, "names": spec.n_names, "algorithm": r["algorithm"],
                         "rrsigs": r["rrsig_count"], "sign_s": r["sign_seconds"], "verify_s": r["verify_seconds"],
                         "signed_bytes": r["signed_bytes"]})
            log(f"  [{scenario}] trial {t}/{trials}, {n} names: {r['rrsig_count']} RRSIGs, "
                f"sign {r['sign_seconds']:.2f} s, verify {r['verify_seconds']:.3f} s")
            shutil.rmtree(out, ignore_errors=True)
    shutil.rmtree(base, ignore_errors=True)
    return rows
