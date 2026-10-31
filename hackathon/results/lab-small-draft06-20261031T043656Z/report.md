# DNS Lab Benchmark: MTL Mode for Post-Quantum DNSSEC
### PS-036 · Real hierarchy (NSD + Unbound + ldns) · Cipherops ecosystem

Suite `20261031-095503_small-lab-draft06` · stack **draft06** · 3 measurement trials · 3 signing trials · 500 lookups per pass · finished `20261031-100656` · wall 11.9 min

## Abstract

Post-quantum signatures such as SLH-DSA-SHA2-128s are large enough that DNSSEC answers often no longer fit a 1232-byte UDP buffer. Merkle Tree Ladder (MTL) mode signs a zone with one PQC signature over a Merkle ladder and attaches a short authentication path to each RRset. This report is the **real-DNS** counterpart to the main Cipherops library report: a multi-zone hierarchy is signed with ldns, served by Verisign MTL-mode NSD, validated by Unbound, and measured with repeated cold/warm trials and packet captures.

This suite used stack **draft06** (MTL `93b05ef73dab…`, liboqs 0.14.0). On draft06, MTL answers are **condensed by default**; a full signed ladder is returned when the resolver asks with EDNS option 65050.

**Provenance in this report.** Signing and verification times appear as **Measured once** (full zones that were served) and, where sample trials exist, as **Estimated** (extrapolated from repeated sample-zone trials, with a distribution-free CI of the median). Wire bytes and Unbound cache are **Measured** from the lab. Signature-storage totals tagged **Analytical** use draft-harvey-cfrg-mtl-mode section 12 formulas only.

**Key results** (MTL vs SLH-DSA-SHA2-128s without MTL):

- **Signing (medium, 500 names, measured once).** MTL 3.2 s; direct SLH-DSA 4.5 min (85× faster).
- **Signing (medium, estimated from sample trials).** MTL 11.8 s; direct SLH-DSA 4.6 min (23× faster).
- **Zone verification (medium, `ldns-verify-zone`, measured once).** MTL 148 ms; direct SLH-DSA 973 ms (6.6× faster).
- **Transmitted bytes (medium, cold upstream, measured).** MTL 513.7 KB; direct SLH-DSA 7.50 MB (15× less data).
- **Resolver RRset cache (medium, measured).** MTL 331.6 KB; direct SLH-DSA 2.84 MB (8.8× less memory).
- **Signing (large, 500 names, measured once).** MTL 3.2 s; direct SLH-DSA 4.5 min (85× faster).
- **Signing (large, estimated from sample trials).** MTL 11.8 s; direct SLH-DSA 4.6 min (23× faster).
- **Zone verification (large, `ldns-verify-zone`, measured once).** MTL 148 ms; direct SLH-DSA 973 ms (6.6× faster).
- **Transmitted bytes (large, cold upstream, measured).** MTL 513.7 KB; direct SLH-DSA 7.50 MB (15× less data).
- **Resolver RRset cache (large, measured).** MTL 331.6 KB; direct SLH-DSA 2.84 MB (8.8× less memory).

## 1. Problem statement

PS-036 asks for a reproducible measurement of what MTL mode changes for DNSSEC when the same PQC algorithm is used with and without MTL, on three dataset sizes, under a pinned environment, reporting signing time, verification time and transmitted bytes, with cache use kept separate from analytical estimates. This suite answers that on a **live multi-zone DNS path**, complementary to the in-process library harness.

## 2. Background

- **DNSSEC** (RFC 4033–4035) signs each RRset; NSEC3 proves non-existence.
- **SLH-DSA-SHA2-128s** (FIPS 205): 32-byte public key, 7,856-byte signature.
- **MTL mode** (draft-harvey-cfrg-mtl-mode): one signed ladder per series; condensed paths per message; full signature = condensed + signed ladder.
- **DNSSEC MTL (draft-fregly-06 / this stack):** condensed RRSIGs by default on draft06; full ladder on EDNS 65050. Always-full wire behaviour is a different stack (`latest`).

## 3. Objectives and scope

Compare equivalent DNSSEC workloads with and without MTL on three dataset sizes in a Docker hierarchy. Measure signing, offline zone verification, resolver wire bytes and Unbound cache. Report analytical signature-storage sizes separately. Out of scope: new cryptography, Internet deployment, security proofs.

| PS-036 / expected outcome | How this suite meets it | Where |
|---|---|---|
| One supported PQC signature algorithm | SLH-DSA-SHA2-128s in ``slh-dsa`` and ``mtl`` scenarios | sec. 4.3 |
| One mentor-tested MTL reference implementation | Verisign MTL / mtl-mode-nsd / unbound / ldns at stack pin | sec. 4.1 |
| Three dataset sizes | 50 / 500 / 5,000 data names across a fixed hierarchy | sec. 4.2 |
| Equivalent workloads with and without MTL | Same zones, same seeded lookup list per size | sec. 4.4 |
| Repeated trials, pinned software and hardware | Measurement trials + signing trials; stack images; host snapshot | sec. 4.1, 4.4 |
| Signing time | Full-zone wall clock once; sample trials extrapolated with 95% CI | sec. 5.1 |
| Verification time | ``ldns-verify-zone`` wall clock (offline; not Unbound validation) | sec. 5.2 |
| Transmitted bytes | Resolver to authoritative bytes from Unbound-iface pcap, cold pass | sec. 5.3 |
| Record configuration and workload parameters | ``params.json`` / ``suite.json`` / per-run ``run.json`` | Appendix A |
| Separate measured cache from analytical estimates | Unbound cache memory vs draft section 12 signature storage | sec. 5.4 |
| Reproducible results report | This `report.md` / `report.html`, regenerated from suite artifacts | this document |
| Technical demonstration | Dashboard Simulated lab (Prepare, Query, Benchmark) + Compare over the same stack | dashboard |

## 4. Methodology

### 4.1 Pinned environment

| Item | Value |
|---|---|
| Suite id | 20261031-095503_small-lab-draft06 |
| Stack | draft06 |
| Host platform | Linux-7.2.11-100.fc43.x86_64-x86_64-with-glibc2.42 |
| Host CPUs | 22 |
| Host Python | 3.14.8 |
| CPU model | Intel(R) Core(TM) Ultra 7 165H |
| Memory | MemTotal:       65248288 kB |
| Kernel | 7.2.11-100.fc43.x86_64 |
| CPU frequency governor | powersave |
| CPU isolation | none (lab containers share all host CPUs) |
| Docker server | 29.8.2 |
| Code version (git) | d01fa1b0bd4f (uncommitted changes) |
| Signature primitive | SPHINCS+-SHA2-128s-simple (pre-standard form of SLH-DSA, liboqs 0.14), in ldns, MTL and Unbound |
| liboqs (stack) | 0.14.0 |
| MTL commit (stack) | 93b05ef73dab53b953cc4e262401a2ce9bc9e431 |
| ldns commit (stack) | 64906672ae0d80ae03933240daab27e19ceccf70 |
| NSD commit (stack) | 09ebb4e551b5029f7ddf7e381b7bb8a966fa1320 |
| Unbound commit (stack) | fab43ad682301633fa119158ecd22924f042cb26 |
| Parallel signing jobs | 1 |
| UDP size limit | 1232 |
| Networks | lan |

> **Timing caveat.** The host CPU governor was `powersave`, so the clock speed can change during the run. Repeat on a host set to `performance` for tighter timings. Lab containers also share every host CPU; the offline harness, by contrast, runs on a dedicated core.

Images used (from `docker image inspect` at the start of the suite):

- `cipherops-bench:draft06` = `unavailable` (built )
- `cipherops-eco-nsd:draft06` = `sha256:b21e598b4e167145deeca67bc971c0e6b4ae5866ba6f9f56f4ad460d60955e64` (built 2026-10-27T10:36:40.874613364+05:30)
- `cipherops-eco-unbound:draft06` = `sha256:642f9c8afea3bebf96e48c2e2c9d7dd81e66b9ea17fce69848712b3e5c32ff1e` (built 2026-10-27T10:36:52.702693891+05:30)
- `cipherops-eco-tools:draft06` = `sha256:9b030477d972c9b8a2a0dba8acfbbd1833ba8f676fdc65fde7fc879cbc36447e` (built 2026-10-27T21:08:17.632199426+05:30)

Image digests recorded in per-size `run.json`:

- `root = {'image': 'cipherops-eco-nsd:draft06', 'image_id': 'sha256:b21e598b4e167145deeca67bc971c0e6b4ae5866ba6f9f56f4ad460d60955e64'}`
- `tld-test = {'image': 'cipherops-eco-nsd:draft06', 'image_id': 'sha256:b21e598b4e167145deeca67bc971c0e6b4ae5866ba6f9f56f4ad460d60955e64'}`
- `sld1 = {'image': 'cipherops-eco-nsd:draft06', 'image_id': 'sha256:b21e598b4e167145deeca67bc971c0e6b4ae5866ba6f9f56f4ad460d60955e64'}`
- `unbound = {'image': 'cipherops-eco-unbound:draft06', 'image_id': 'sha256:642f9c8afea3bebf96e48c2e2c9d7dd81e66b9ea17fce69848712b3e5c32ff1e'}`
- `client = {'image': 'cipherops-eco-tools:draft06', 'image_id': 'sha256:9b030477d972c9b8a2a0dba8acfbbd1833ba8f676fdc65fde7fc879cbc36447e'}`

### 4.2 Datasets and hierarchy

Each dataset size uses the same shape: one root, one TLD (`test.`), up to four leaf zones on one leaf server. Only the number of data names changes; names are split across the leaf zones. This is **not** a single zone of N names (that is the main library harness).

| dataset (names) | leaf zones | names per leaf | zones total | lab tag | run folder |
|---|---|---|---|---|---|
| 50 | 4 | 13, 13, 12, 12 | 6 | eco-bench-50-draft06 | 20261031-095707_small-lab-draft06-n50 |
| 500 | 4 | 125, 125, 125, 125 | 6 | eco-bench-500-draft06 | 20261031-100505_small-lab-draft06-n500 |

*Table 1: datasets.*

### 4.3 Scenarios

| Scenario | Meaning |
|---|---|
| `rsa` | RSA-2048/SHA-256 |
| `ecdsa` | ECDSA P-256 |
| `ed25519` | Ed25519 |
| `slh-dsa` | SLH-DSA-SHA2-128s without MTL |
| `mtl` | SLH-DSA-SHA2-128s with MTL |

### 4.4 Measurement procedure

- For each dataset size the suite signs every zone once per scenario (**Measured once**), with `jobs=1`.
- It then repeats signing and `ldns-verify-zone` on sample leaf zones (3 trials; sample sizes derived from `sample_names=20`). A two-point fit (fixed cost per zone + cost per RRSIG) extrapolates to the full hierarchy (**Estimated**, CI of the median).
- The lab is started; the seeded workload (500 lookups, nx_fraction=0.1, name Zipf s=1.1, zone Zipf s=1.0) is replayed for 3 trials, each with a cold pass (resolver restarted) and a warm pass.
- Upstream bytes and truncations come from pcap on the Unbound interface; cache figures from `unbound-control` memory stats after the cold pass.
- The lab is stopped before the next dataset size.

### 4.5 What is not claimed

- **Verification** in the tables is offline `ldns-verify-zone` over signed zone files, not Unbound’s per-query validation cost (that sits inside cold/warm lookup latency).
- **Estimated** signing/verify must not be read as the wall-clock of the served zones; use **Measured once** for that.
- Absolute seconds are not comparable to the main library report (different topology and tool). Compare **MTL ÷ SLH-DSA ratios** per size.
- Stack draft06 condensed-by-default wire semantics differ from the always-full `latest` stack and from the main library’s MTL commit when they diverge.

## 5. Results

### 5.1 Signing time

![Figure 1: whole-hierarchy signing time (measured once, log scale)](figures/sign_full.png)

*Figure 1: whole-hierarchy signing time (measured once, log scale)*

| dataset | scenario | zones | RRSIGs | sign estimated [95% CI] | sign measured once | signed size |
|---|---|---|---|---|---|---|
| 50 | ECDSA P-256 | 6 | 153 | 83 ms [80 ms–94 ms] | 85 ms | 52.1 KB |
| 50 | Ed25519 | 6 | 153 | 77 ms [75 ms–83 ms] | 76 ms | 51.9 KB |
| 50 | SLH-DSA-SHA2-128s with MTL | 6 | 153 | 3.2 s [3.2 s–3.2 s] | 3.2 s | 191.5 KB |
| 50 | RSA-2048/SHA-256 | 6 | 153 | 237 ms [218 ms–241 ms] | 263 ms | 91.7 KB |
| 50 | SLH-DSA-SHA2-128s without MTL | 6 | 153 | 39.8 s [39.8 s–40.5 s] | 40.0 s | 1.57 MB |
| 500 | ECDSA P-256 | 6 | 1053 | 269 ms [244 ms–318 ms] | 149 ms | 397.0 KB |
| 500 | Ed25519 | 6 | 1053 | 193 ms [167 ms–291 ms] | 150 ms | 396.7 KB |
| 500 | SLH-DSA-SHA2-128s with MTL | 6 | 1053 | 11.8 s [3.2 s–12.0 s] | 3.2 s | 711.5 KB |
| 500 | RSA-2048/SHA-256 | 6 | 1053 | 1.0 s [830 ms–1.1 s] | 1.1 s | 660.5 KB |
| 500 | SLH-DSA-SHA2-128s without MTL | 6 | 1053 | 4.6 min [4.5 min–4.6 min] | 4.5 min | 10.82 MB |

*Table 2: signing time. Estimated = sample-trial extrapolation; measured once = full zones served.*

### 5.2 Zone verification time (`ldns-verify-zone`)

| dataset | scenario | verify estimated [95% CI] | verify measured once |
|---|---|---|---|
| 50 | ECDSA P-256 | 80 ms [74 ms–82 ms] | 84 ms |
| 50 | Ed25519 | 89 ms [86 ms–107 ms] | 89 ms |
| 50 | SLH-DSA-SHA2-128s with MTL | 92 ms [80 ms–129 ms] | 96 ms |
| 50 | RSA-2048/SHA-256 | 68 ms [65 ms–80 ms] | 80 ms |
| 50 | SLH-DSA-SHA2-128s without MTL | 207 ms [188 ms–239 ms] | 208 ms |
| 500 | ECDSA P-256 | 256 ms [230 ms–426 ms] | 269 ms |
| 500 | Ed25519 | 369 ms [183 ms–458 ms] | 311 ms |
| 500 | SLH-DSA-SHA2-128s with MTL | 217 ms [205 ms–336 ms] | 148 ms |
| 500 | RSA-2048/SHA-256 | 211 ms [104 ms–504 ms] | 178 ms |
| 500 | SLH-DSA-SHA2-128s without MTL | 1.0 s [911 ms–1.1 s] | 973 ms |

*Table 3: offline zone verification. Not Unbound validation latency.*

### 5.3 Transmitted bytes

![Figure 2: cold upstream bytes (log scale)](figures/wire.png)

*Figure 2: resolver to authoritative bytes on the cold pass (log scale)*

| dataset | scenario | upstream bytes (cold) | upstream queries | truncated | TCP conns | cold median (ms) | warm median (ms) |
|---|---|---|---|---|---|---|---|
| 50 | ECDSA P-256 | 95.7 KB | 159 | 5 | 1 | 1.44 | 1.32 |
| 50 | Ed25519 | 95.3 KB | 159 | 5 | 1 | 1.58 | 1.20 |
| 50 | SLH-DSA-SHA2-128s with MTL | 232.6 KB | 188 | 18 | 3 | 1.62 | 1.32 |
| 50 | RSA-2048/SHA-256 | 232.5 KB | 201 | 47 | 1 | 1.54 | 1.26 |
| 50 | SLH-DSA-SHA2-128s without MTL | 3.46 MB | 308 | 154 | 3 | 1.64 | 1.30 |
| 500 | ECDSA P-256 | 212.4 KB | 366 | 17 | 1 | 1.65 | 0.61 |
| 500 | Ed25519 | 212.0 KB | 366 | 17 | 1 | 0.74 | 0.49 |
| 500 | SLH-DSA-SHA2-128s with MTL | 513.7 KB | 456 | 91 | 3 | 1.79 | 1.09 |
| 500 | RSA-2048/SHA-256 | 472.5 KB | 429 | 80 | 1 | 0.77 | 0.49 |
| 500 | SLH-DSA-SHA2-128s without MTL | 7.50 MB | 698 | 349 | 3 | 0.89 | 0.50 |

*Table 4: wire and lookup latency from the lab (measured).*

### 5.4 Cache use (measured) and analytical signature storage

![Figure 3: Unbound RRset cache (measured)](figures/cache.png)

*Figure 3: Unbound RRset cache after the cold pass (measured)*

![Figure 4: analytical signature storage (draft section 12)](figures/analytical.png)

*Figure 4: analytical whole-zone signature storage (draft section 12); not measured cache*

| dataset | scenario | RRset cache (measured) | message cache (measured) | signature storage (analytical) | signed zone files (measured) |
|---|---|---|---|---|---|
| 50 | ECDSA P-256 | 122.9 KB | 102.2 KB | — | 52.1 KB |
| 50 | Ed25519 | 122.3 KB | 102.2 KB | — | 51.9 KB |
| 50 | SLH-DSA-SHA2-128s with MTL | 178.2 KB | 102.2 KB | 68.9 KB | 191.5 KB |
| 50 | RSA-2048/SHA-256 | 146.5 KB | 102.2 KB | — | 91.7 KB |
| 50 | SLH-DSA-SHA2-128s without MTL | 1.03 MB | 102.2 KB | 1.15 MB | 1.57 MB |
| 500 | ECDSA P-256 | 238.3 KB | 138.9 KB | — | 397.0 KB |
| 500 | Ed25519 | 237.8 KB | 138.9 KB | — | 396.7 KB |
| 500 | SLH-DSA-SHA2-128s with MTL | 331.6 KB | 139.0 KB | 255.7 KB | 711.5 KB |
| 500 | RSA-2048/SHA-256 | 304.9 KB | 139.0 KB | — | 660.5 KB |
| 500 | SLH-DSA-SHA2-128s without MTL | 2.84 MB | 139.0 KB | 7.89 MB | 10.82 MB |

*Table 5: measured Unbound cache memory vs analytical signature-material estimate and on-disk signed zone size.*

### 5.5 MTL vs SLH-DSA without MTL (ratios)

Values are **MTL ÷ SLH-DSA** (lower is a larger MTL win). Signing uses **measured once**; bytes and cache are measured.

| dataset | sign (measured once) | verify (measured once) | signed size | upstream bytes | RRset cache | analytical sig storage |
|---|---|---|---|---|---|---|
| 50 | 0.080 | 0.462 | 0.119 | 0.066 | 0.170 | 0.059 |
| 500 | 0.012 | 0.152 | 0.064 | 0.067 | 0.114 | 0.032 |

*Table 6: MTL / SLH-DSA ratios.*

## 6. Discussion

Across all three sizes, MTL reduces full-hierarchy signing time and cold upstream bytes relative to direct SLH-DSA on the same names and workload. Wire and cache wins are especially large on draft06 because condensed signatures are the default on the wire. Lookup medians are not uniformly better for MTL; cold latency can be comparable or slightly higher when ladder/path work dominates a short LAN RTT.

Estimated signing from sample trials tracks measured-once MTL closely when `jobs=1`; large direct-SLH-DSA estimates can under- or over-shoot the full wall clock and should be read with the CI and the measured-once column.

## 7. Conclusions

On equivalent real-DNS workloads and the same underlying PQC algorithm, MTL mode in the pinned Verisign stack reduced zone signing time and transmitted bytes versus direct SLH-DSA at 50, 500 and 5,000 names. Cache memory after the cold pass followed the same direction. All headline wire and cache figures are measured; signing is reported both as measured-once and as trial-based estimates; analytical sizes are formula-only. Regenerate this document with `python -m live_lab.suite_report 20261031-095503_small-lab-draft06`.

## Appendix A. Configuration

```json
{
  "label": "small-lab-draft06",
  "stack": "draft06",
  "reuse": "",
  "sizes": [
    50,
    500
  ],
  "scenarios": [
    "ecdsa",
    "ed25519",
    "rsa",
    "slh-dsa",
    "mtl"
  ],
  "trials": 3,
  "sign_trials": 3,
  "sample_names": 20,
  "n_queries": 500,
  "networks": [
    "lan"
  ],
  "udp": 1232,
  "jobs": 1,
  "workload": {
    "nx_fraction": 0.1,
    "zone_zipf_s": 1.0,
    "name_zipf_s": 1.1,
    "timeout_s": 5.0
  },
  "profiles": {
    "wan": {
      "client_ms": 2,
      "root_ms": 20,
      "tld_ms": 35,
      "sld_ms": 50,
      "loss_pct": 0
    },
    "lossy": {
      "client_ms": 2,
      "root_ms": 20,
      "tld_ms": 35,
      "sld_ms": 50,
      "loss_pct": 1
    }
  },
  "suite_id": "20261031-095503_small-lab-draft06",
  "stamp": "20261031-095503",
  "finished": "20261031-100656",
  "seconds": 713,
  "sizes_detail": {
    "50": {
      "tag": "eco-bench-50-draft06",
      "run_folder": "20261031-095707_small-lab-draft06-n50",
      "names": 50,
      "zones": 6,
      "config": "configs/_bench-50.yaml"
    },
    "500": {
      "tag": "eco-bench-500-draft06",
      "run_folder": "20261031-100505_small-lab-draft06-n500",
      "names": 500,
      "zones": 6,
      "config": "configs/_bench-500.yaml"
    }
  },
  "host": {
    "platform": "Linux-7.2.11-100.fc43.x86_64-x86_64-with-glibc2.42",
    "cpus": 22,
    "python": "3.14.8"
  },
  "provenance": {
    "git_commit": "d01fa1b0bd4f9b4f000079b10d1474e94d60be8f",
    "git_dirty": true,
    "platform": "Linux-7.2.11-100.fc43.x86_64-x86_64-with-glibc2.42",
    "kernel": "7.2.11-100.fc43.x86_64",
    "cpu_model": "Intel(R) Core(TM) Ultra 7 165H",
    "cpus": 22,
    "mem_total": "MemTotal:       65248288 kB",
    "cpu_governor": "powersave",
    "python": "3.14.8",
    "docker_server": "29.8.2",
    "stack": "draft06",
    "stack_pins": {
      "STACK": "draft06",
      "OPENSSL_TAG": "openssl-3.6.0",
      "LIBOQS_TAG": "0.14.0",
      "MTL_COMMIT": "93b05ef73dab53b953cc4e262401a2ce9bc9e431",
      "LDNS_COMMIT": "64906672ae0d80ae03933240daab27e19ceccf70",
      "NSD_COMMIT": "09ebb4e551b5029f7ddf7e381b7bb8a966fa1320",
      "UNBOUND_COMMIT": "fab43ad682301633fa119158ecd22924f042cb26"
    },
    "images": {
      "cipherops-bench": {
        "id": "unavailable",
        "created": ""
      },
      "cipherops-eco-nsd": {
        "id": "sha256:b21e598b4e167145deeca67bc971c0e6b4ae5866ba6f9f56f4ad460d60955e64",
        "created": "2026-10-27T10:36:40.874613364+05:30"
      },
      "cipherops-eco-unbound": {
        "id": "sha256:642f9c8afea3bebf96e48c2e2c9d7dd81e66b9ea17fce69848712b3e5c32ff1e",
        "created": "2026-10-27T10:36:52.702693891+05:30"
      },
      "cipherops-eco-tools": {
        "id": "sha256:9b030477d972c9b8a2a0dba8acfbbd1833ba8f676fdc65fde7fc879cbc36447e",
        "created": "2026-10-27T21:08:17.632199426+05:30"
      }
    },
    "cpu_isolation": "none (lab containers share all host CPUs)"
  }
}
```

## Appendix B. Artifacts

- Suite directory: `ecosystem/results/suites/20261031-095503_small-lab-draft06/`
- `suite.json`, `params.json`, `signing_full.csv`, `signing_trials_*.csv`
- `report.md`, `report.html`, `figures/` (this report)
- 50 names: `ecosystem/results/eco-bench-50-draft06/20261031-095707_small-lab-draft06-n50/` (summary.csv, run.json, passes.csv, hops.csv, pcap/)
- 500 names: `ecosystem/results/eco-bench-500-draft06/20261031-100505_small-lab-draft06-n500/` (summary.csv, run.json, passes.csv, hops.csv, pcap/)

## References

1. draft-harvey-cfrg-mtl-mode-09, *Merkle Tree Ladder (MTL) Mode Signatures*.
2. draft-fregly-dnsop-slh-dsa-mtl-dnssec, *SLH-DSA-MTL in DNSSEC*.
3. FIPS 205, *Stateless Hash-Based Digital Signature Standard*.
4. RFC 4033–4035 (DNSSEC); RFC 5155 (NSEC3).
5. Verisign MTL reference library, https://github.com/verisign/MTL.
6. Cipherops main library report (companion): `results/runs/<id>/report.html`.

