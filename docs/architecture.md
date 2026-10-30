# Cipherops: architecture and end-to-end flow

This document explains the whole project from the first command to the final
report: what each file is for, why each function exists, and what every
important variable means. Read it top to bottom once; afterwards use the
section headings as a reference.

Contents

1. The project in one paragraph
2. Background you need (DNS, DNSSEC, MTL) in plain words
3. Architecture at a glance
4. Repository map
5. The main flow, step by step (`run_benchmark.sh` or `.ps1` to report)
6. File-by-file reference
7. Live mode flow (real server, real packets, Wireshark)
8. NSD cross-check flow
9. Data formats and output files
10. Glossary of variables, config keys and columns
11. Measured vs analytical
12. Design decisions and why
13. Five-minute explanation for a teammate

---

## 1. The project in one paragraph

PS-036 asks: *if DNSSEC switched to a post-quantum signature (SLH-DSA), how
much does MTL mode help?* We answer it by benchmarking five signing
algorithms on the same DNS zones: three classical ones (RSA-2048, ECDSA P-256,
Ed25519), SLH-DSA-SHA2-128s signing every record directly, and
SLH-DSA-SHA2-128s in MTL mode using the supplied Verisign MTL reference
library. For each one we measure **signing time, verification time,
transmitted bytes, and cache usage**, on three zone sizes, with repeated
trials, in a pinned Docker environment. We write no cryptography ourselves:
all signing and verification is done by Verisign `libmtl`, `liboqs` and
OpenSSL. Our code builds the zones, drives those libraries, measures, and
reports.

---

## 2. Background you need

### 2.1 DNS

DNS is the Internet's phone book. A client asks a question such as
"what is the address (type `A`) of `host000002.bench.example`?" and a server
answers "192.0.0.2".

- **Record**: one line of DNS data, e.g. `host000002.bench.example. A 192.0.0.2`.
- **RRset**: all records with the same name and type (e.g. 4 AAAA addresses of
  one host). DNSSEC signs per RRset, so **1 RRset = 1 signature**.
- **Zone**: the full list of RRsets one owner controls, e.g. everything
  under `bench.example`. It is the thing that gets signed and served.
- **Authoritative server**: the server that holds the zone and answers
  questions about it (we use NSD in live mode).
- **Resolver**: the client side that asks questions and caches answers.

### 2.2 DNSSEC

DNSSEC stops fake answers. The zone owner signs every RRset in advance with a
private key; the public key is published in the zone itself.

| Record type | Meaning |
|---|---|
| `RRSIG` | The signature attached to an RRset. The **size of this is what PQC makes big.** |
| `DNSKEY` | The zone's public key. The resolver fetches it to check RRSIGs. |
| `NSEC3` | Pre-signed proof that a name does **not** exist ("no names between hash X and hash Y"). Used for NXDOMAIN answers. |
| `NSEC3PARAM`, `SOA`, `NS` | Zone housekeeping records at the top (the "apex") of the zone. |

What exactly is signed: for each RRset, RFC 4034 section 3.1.8.1 defines a
byte string = RRSIG header fields (without the signature) + the RRset in
canonical form. We call that string the **signing input** or **message**.

Transport limits:

- DNS answers normally go over **UDP**. The client says how big a UDP answer it
  accepts (the **EDNS buffer size**). The recommended value today is **1232
  bytes**; we also test **4096**.
- If the answer is bigger, the server sends a cut-down reply with the
  **TC (truncated) flag** set, and the client asks again over **TCP**. That
  costs an extra round trip and more bytes.

### 2.3 Why post-quantum hurts DNSSEC

| Algorithm | Signature size |
|---|---|
| Ed25519 / ECDSA P-256 | 64 B |
| RSA-2048 | 256 B |
| SLH-DSA-SHA2-128s | **7,856 B** |

With SLH-DSA, almost no answer fits in 1232 bytes, so almost every lookup
needs TCP. Signing is also slow (~0.6-0.8 s per signature on one core).

### 2.4 MTL mode (Merkle Tree Ladder)

MTL avoids signing each RRset with SLH-DSA:

1. **Leaf**: a hash of one RRset's signing input. One leaf per RRset.
2. **Nodes**: hashes of two children, built up into a binary tree.
3. **Rungs**: the top nodes that together cover all leaves.
4. **Ladder**: the list of rungs. The ladder is signed **once** with SLH-DSA
   (one slow signature, about 8 KB).
5. **Condensed signature** (per RRset): the path of sibling hashes from that
   RRset's leaf up to a rung. About 150-200 bytes.
6. **Full signature** = condensed signature + signed ladder.

A resolver fetches the signed ladder once (first contact), caches it, and
afterwards checks every answer with only the small condensed signature.

How the DNS draft (draft-fregly) carries this:

- The RRSIG signature field starts with an **MTL-Type byte**: `0` =
  condensed, `1` = full.
- A resolver that needs the ladder sends an EDNS option **`mtl-mode-full`**
  (we use lab code 65001) over TCP and gets the full signature.

---

## 3. Architecture at a glance

```
            YOU (shell or PowerShell)
                 |
                 |  scripts/run_benchmark.sh  or  .\scripts\run_benchmark.ps1   (or docker compose run ...)
                 v
 +------------------------------------------------------------------+
 |  Docker container  (Fedora 42, pinned libraries, no network)    |
 |                                                                  |
 |  python3 -m cipherops.runner run --config configs/...yaml        |
 |        |                                                         |
 |        |  1. preflight.py   go / no-go gate                      |
 |        |  2. workload.py    build zones + signing inputs         |
 |        |  3. harness.py --> harness/mtlbench (C)                 |
 |        |                     |-- Verisign libmtl  (MTL)          |
 |        |                     |-- liboqs           (SLH-DSA)      |
 |        |                     '-- OpenSSL          (classical)    |
 |        |  4. dnswire.py     build real DNS answers, count bytes  |
 |        |  5. cache.py       cache memory: measured + analytical  |
 |        |     analytical.py  draft section 12 size formulas       |
 |        |  6. stats.py       medians, CIs of the median           |
 |        |  7. report.py      report.md / .html / figures          |
 |        v                                                         |
 |   results/runs/<timestamp>-<run_name>/   (on the host)           |
 +------------------------------------------------------------------+
                 |
                 +--> scripts/make_pdf.ps1 turns report.html into a PDF
                 +--> optional: runner live / crosscheck (real NSD server)
```

Three layers, each with one job:

| Layer | Language | Job | Files |
|---|---|---|---|
| Crypto | C | Sign, verify, time, check tampering. Only calls libraries. | `harness/mtlbench.c` |
| Orchestration and DNS | Python | Build zones, run trials, build DNS answers, count bytes, statistics | `src/cipherops/*.py` |
| Presentation | Python | Report and figures | `report.py`, `results.py` |

---

## 4. Repository map

```
Cipherops/
├── .devcontainer/          GitHub Codespaces setup (Python, Docker-in-Docker, port 8501)
├── hackathon/              submission material: project page, readout, feedback, frozen results, decks
├── DELIVERABLES.md         what is submitted; the PS-036 requirements
├── THIRD_PARTY_NOTICES.md  licences of the software built on
├── configs/
│   ├── scenarios.yaml      full PS-036 run (50 / 500 / 5000 names, 10 trials)
│   └── quick.yaml          fast demo run (20 / 60 / 200 names, 3 trials)
├── docker/
│   ├── Dockerfile          pinned build; OpenSSL, liboqs and MTL come from the stack profile
│   └── compose.yml         one CPU core, 4 GB RAM, no network, mounts results/
├── harness/
│   ├── mtlbench.c          the C benchmark program (crypto layer)
│   └── Makefile            builds mtlbench against libmtl, liboqs, OpenSSL
├── src/cipherops/          the Python package
│   ├── runner.py           ENTRY POINT: command line, the whole pipeline
│   ├── codepoints.py       the 5 algorithms and their DNSSEC numbers
│   ├── workload.py         builds zones and RFC 4034 signing inputs
│   ├── harness.py          runs mtlbench, reads its output files
│   ├── dnswire.py          builds real DNS answers, counts bytes, UDP/TCP model
│   ├── cache.py            cache usage, measured and analytical
│   ├── analytical.py       MTL draft section 12 size formulas
│   ├── evolution.py        evolving-zone experiment and its tables (dashboard: Zone updates)
│   ├── stats.py            summary statistics, median CIs
│   ├── preflight.py        go/no-go gate (PS-036 "defer if unavailable")
│   ├── envinfo.py          records hardware/software versions
│   ├── results.py          loads a finished run (for the report)
│   ├── report.py           writes report.md, report.html, figures/
│   ├── crosscheck.py       NSD server helper + byte-model cross-check
│   └── live.py             live mode: real client, real NSD, pcap capture
├── dashboard/              Streamlit app (app.py launches live_lab/app.py)
├── scripts/
│   ├── run_benchmark.sh    Linux/macOS: build image + run (start here)
│   ├── run_benchmark.ps1   Windows equivalent
│   ├── run_live.sh         live validation of a finished run (Linux/macOS)
│   ├── run_live.ps1        Windows equivalent
│   ├── show_fixes.py       compare an earlier commit with the working tree
│   ├── make_pdf.ps1        report.html -> report.pdf via Edge/Chrome
│   ├── probe.py            one quick trial per mode (debugging)
│   ├── own.py              learning trace: every variable of stages 1-7 on tiny zones
│   └── pcap_bytes.py       sums bytes in live-mode pcaps, compares with client
├── tests/                  pytest unit tests (see section 6.16)
├── docs/
│   ├── methodology.md      measurement method in detail
│   ├── architecture.md     this file
│   ├── end-to-end-flow.md  what each of the 7 stages takes in and puts out
│   ├── outputs-reference.md  every output file and column
│   ├── getting-started.md, codespaces.md, scope-and-limits.md, README.md (index)
│   └── learn/              background and two animated walkthroughs
└── results/runs/<run_id>/  every run's outputs (section 9)
```

---

## 5. The main flow, step by step

This is what happens when you type:

```bash
bash scripts/run_benchmark.sh configs/quick.yaml        # Windows: .\scripts\run_benchmark.ps1 -Config configs/quick.yaml
```

### Step 1. `scripts/run_benchmark.sh` (Windows: `run_benchmark.ps1`)

1. `docker compose --env-file ecosystem/stacks/<stack>.env -f docker/build.yml build bench` builds the pinned image
   for the stack profile (default `latest`; set `CIPHEROPS_STACK`)
   (only slow the first time; later builds use the cache). Set `CIPHEROPS_SKIP_BUILD=1` to run the image already on
   the host instead (built earlier, or pulled in the dashboard's Setup); the script stops if it is missing.
2. Records the git commit (`CIPHEROPS_GIT_SHA`), the image ID
   (`CIPHEROPS_HOST_IMAGE_ID`) and the CPU core (`CIPHEROPS_CPUSET`) so they
   end up in `environment.json`.
3. Runs `docker compose run --rm bench "python3 -m cipherops.runner run --config configs/quick.yaml"`.

(On Windows, `run_benchmark.ps1` does the same through `cmd /c` inside
`Invoke-Native`, because Windows PowerShell 5.1 treats Docker's progress
messages on stderr as errors; failures are detected from the exit code.)

### Step 2. Inside Docker: `runner.py` → `main()` → `cmd_run()`

`main()` reads the sub-command (`run`, `preflight`, `report`, `crosscheck`,
`live`) and calls the matching `cmd_*` function. For `run`:

1. `load_config()` reads the YAML and applies command-line overrides
   (`--trials`, `--modes`, `--datasets`).
2. Creates the run folder `results/runs/<UTC time>-<run_name>/`, e.g.
   `20261005T140642Z-quick-demo`.
3. Saves `config.yaml` (exact copy of the settings used) and
   `environment.json` (from `envinfo.collect()`).

### Step 3. Go/no-go gate: `preflight.run()`

PS-036 says: defer if the implementation or fixtures are unavailable. So
before any numbers are produced we check:

| Check | Function | What it proves |
|---|---|---|
| Libraries installed | `reference_installed()` | libmtl, its header, liboqs exist |
| Verisign's own tests | `reference_unit_tests()` | their `mtltest` suite passes (89 tests) |
| Fixture scheme | `reference_vector_scheme()` | which algorithm the bundled test vectors cover (informational) |
| Verisign's tools | `reference_tools_roundtrip()` | `mtlkeygen` → `mtlsign` → `mtlverify` works, tampered message rejected |
| Our harness | `harness_smoke()` | every mode signs/verifies a tiny 6-name zone; every negative case rejected |
| Independent verifiers | `mtl_reference_tool_check()`, `slh_dsa_openssl_check()` | `mtlverify` and OpenSSL accept the harness's signature bytes and reject a changed message |

Result goes to `preflight.json`. If the verdict is not `GO`, the run stops
(exit code 3).

### Step 4. Build the zones: `workload.build_zone()` + `signing_messages()`

For each dataset in the config (`small`, `medium`, `large`):

```python
zone = build_zone(ds["name"], ds["n_names"], cfg["seed"] + i)
```

`build_zone` creates `bench.example.` with:

- 4 apex RRsets: SOA, NS, DNSKEY, NSEC3PARAM
- `n_names` hosts `host000000` … each with one RRset, chosen by a seeded
  random number:
  - 60 % small: one A or one AAAA record
  - 30 % medium: MX with 3 mail servers, or 4 AAAA records
  - 10 % large: one TXT of 1-2 KB
- one NSEC3 per name (+1 for the apex), forming a closed chain

Total RRsets = `2 × n_names + 5`. Quick config: 45 / 125 / 405. Full
config: 105 / 1,005 / 10,005.

Then for each mode, `signing_messages(zone, mode)` produces one RFC 4034
signing input per RRset (the bytes that will be signed), plus a manifest row
describing it. These are written to
`datasets/<dataset>/<mode>/messages.bin` and `manifest.csv`.

Why per mode? The signing input includes the algorithm number and the DNSKEY
key tag, which differ per algorithm, exactly as in real DNSSEC.

If direct SLH-DSA has a cap for this dataset (`direct_sign_limit`), only an
evenly spaced sample (`sample_indices()`) is written to
`messages_sample.bin`, and `manifest.csv` marks `signed_in_trials`.

### Step 5. Trials: `run_trials()` → `harness.run_trial()` → `mtlbench`

1. **Warm-up**: each mode runs once on the smallest dataset; the result is
   discarded (loads libraries, warms CPU caches).
2. **Measured trials**: for `t` in `0 … trials-1`, for each dataset, the modes
   are shuffled with a seeded RNG (so slow drift does not favour one mode),
   and each mode runs as a **separate process**:

```
mtlbench <mtl|direct|classical> --messages messages.bin --algo <name>
         --full-verify 64 [--out-dir artifacts/<dataset>/<mode>]
```

3. `mtlbench` prints one JSON object (timings, sizes, failures, tamper
   results, memory). `run_trial()` parses it.
4. The raw JSON is saved gzipped in `raw/<dataset>__<mode>__t<t>.json.gz`.
5. `trial_metrics()` turns it into one row of `trials.csv`; `op_rows()` turns
   each per-RRset timing into rows of `ops.parquet`.
6. On trial 0 only, `--out-dir` is passed so the real signatures are saved in
   `artifacts/` for the byte analysis.

What `mtlbench` does per mode (details in 6.4):

| Mode | Library | Steps timed |
|---|---|---|
| `mtl` | Verisign libmtl | keygen, append each RRset (leaf), sign ladder once, extract each condensed signature, verify ladder, verify each condensed sig, verify N full sigs |
| `direct` | liboqs | keygen, sign each RRset, verify each |
| `classical` | OpenSSL | keygen, sign each RRset, verify each |

Every mode also flips one bit in a message and a signature and checks the
verifier rejects them (`tamper` fields).

### Step 6. Bytes and cache: `analyse()`

For each dataset and mode:

1. `load_signatures()` reads the real signatures from `artifacts/`.
2. `dnswire.response_catalog()` builds a **real DNS answer** for every host
   RRset, the DNSKEY, and `nx_sample` non-existent names, at EDNS 1232 and
   4096 (and for MTL also the full-signature variant). Each answer is encoded
   with dnspython and its exact byte length recorded → `responses.csv`.
3. `dnswire.query_workload()` generates the lookup sequence: DNSKEY first, then
   `n_queries` lookups; `nx_fraction` of them for non-existent names, the rest
   picked with Zipf popularity `zipf_s`.
4. `dnswire.workload_accounting()` replays that sequence with resolver-cache
   rules (only the first lookup of each name/type is sent), decides UDP vs
   TC+TCP for each answer, and adds up bytes → `workload.csv`.
5. `cache.resolver_cache_measured()` stores those answers in dnspython's own
   resolver cache and measures memory; `resolver_cache_analytical()` gives
   the formula estimate → `cache.csv`.
6. For MTL, measured signature/ladder sizes are put next to the draft
   formulas (`analytical.py`) → `sizes.csv`.

### Step 7. Statistics: `summarise()` → `summary.json`

For each dataset × mode: n, mean, median, std, p05, p95, min, max of each
timing, plus a **distribution-free confidence interval of the median**
(`stats.median_ci()`, from order statistics) and its real coverage
(`stats.median_ci_coverage()`), plus whether all correctness checks passed, plus
transmitted bytes and UDP-fit percentages.

### Step 8. Report: `report.write()`

Reads the run folder through `results.load()` and writes:

- `figures/*.png` (signing, verification, response sizes, workload bytes, cache)
- `report.md` (abstract, background, method, results, discussion,
  limitations, conclusion, reproduction, appendices)
- `report.html` (same content, images embedded)

### Step 9. After the run (optional)

| Command | What it does |
|---|---|
| `.\scripts\make_pdf.ps1 results/runs/<run>` | `report.pdf` from `report.html` |
| `python3 -m cipherops.runner live --run <run>` | Real client vs real NSD, pcaps (section 7) |
| `python3 -m cipherops.runner crosscheck --run <run>` | Compare byte model with NSD (section 8) |
| `python3 -m cipherops.runner report --run <run>` | Regenerate the report only |

### The whole flow as one call chain

```
run_benchmark.ps1
 └─ docker compose run bench "python3 -m cipherops.runner run --config X"
     └─ runner.main()
         └─ runner.cmd_run()
             ├─ load_config()
             ├─ envinfo.collect()                       -> environment.json
             ├─ preflight.run()                         -> preflight.json
             │    └─ harness_smoke() -> harness.run_trial() -> mtlbench
             ├─ for each dataset:
             │    ├─ workload.build_zone()
             │    └─ for each mode:
             │         ├─ workload.signing_messages()
             │         └─ workload.write_messages()      -> datasets/.../messages.bin
             ├─ run_trials()
             │    └─ harness.run_trial() -> mtlbench     -> raw/*.json.gz, artifacts/
             │         trial_metrics(), op_rows()        -> trials.csv, ops.parquet
             ├─ analyse()
             │    ├─ harness.load_signatures()
             │    ├─ dnswire.response_catalog()          -> responses.csv
             │    ├─ dnswire.query_workload()
             │    ├─ dnswire.workload_accounting()       -> workload.csv
             │    ├─ cache.*                              -> cache.csv
             │    └─ analytical.*                         -> sizes.csv
             ├─ summarise() -> stats.*                    -> summary.json
             └─ report.write() -> results.load()          -> report.md/html, figures/
```

---

## 6. File-by-file reference

### 6.1 `src/cipherops/runner.py`: entry point and pipeline

Why it exists: one place that runs the whole benchmark in a fixed order and
writes every output.

| Function | Why it exists |
|---|---|
| `log(msg)` | Timestamped progress lines on stderr. |
| `load_config(path, overrides)` | Reads YAML; lets the command line restrict trials/modes/datasets; rejects unknown mode names. |
| `trial_metrics(res, dataset, mode_key, trial, n_total)` | Converts one `mtlbench` JSON result into one flat row. Computes totals (e.g. MTL sign total = all appends + ladder sign + all condensed extractions), per-RRset averages, and extrapolated totals when the run was capped. |
| `op_rows(res, ...)` | One row per individual operation (per RRset timing), for distributions. |
| `direct_limit(cfg, dataset, mode_key)` | Returns the cap for direct SLH-DSA on this dataset, or `None`. Only applies to the `pqc-direct` family. |
| `sample_indices(n, limit)` | Evenly spaced indices so a capped sample covers every record type (not just the first N, which would be biased). |
| `run_trials(cfg, root, paths, sizes)` | Warm-up, then the interleaved, seeded, repeated trials. Saves raw JSON and artifacts. |
| `analyse(cfg, root, zones, trials)` | Bytes, workload, cache and size analysis from the saved artifacts. |
| `summarise(trials, frames)` | Builds `summary.json` with stats and CIs. |
| `write_frame(df, root, name)` | Saves a table as Parquet and CSV (`ops` only Parquet because it is large). |
| `cmd_run(args)` | The main pipeline (section 5). |
| `cmd_preflight`, `cmd_report`, `cmd_crosscheck`, `cmd_live` | The other sub-commands. |
| `main(argv)` | Command-line parser. |

Constants: `CLASSICAL_SIG_BYTES` (64/64/256, used by analytical cache
estimate), `OP_ARRAYS` (names of the per-operation timing arrays in the
harness JSON).

### 6.2 `src/cipherops/codepoints.py`: the five algorithms

Why it exists: one table describes each algorithm, so nothing else hard-codes
algorithm details.

`Mode` fields:

| Field | Meaning | Example (MTL) |
|---|---|---|
| `key` | Name used in configs and results | `slh-dsa-mtl` |
| `harness_mode` | First argument to `mtlbench` | `mtl` |
| `harness_algo` | `--algo` passed to `mtlbench` | `SLH-DSA-SHA2-128s-MTL-SHA2-128` |
| `dnssec_alg` | Algorithm number put in RRSIG/DNSKEY | `50` (lab placeholder) |
| `public_key_len` | DNSKEY public key length in bytes | `32` |
| `family` | `mtl`, `pqc-direct` or `classical` | `mtl` |
| `label` | Human-readable name | `SLH-DSA-SHA2-128s with MTL` |

| key | family | DNSSEC alg | Library |
|---|---|---|---|
| `slh-dsa-mtl` | mtl | 50 (lab) | Verisign libmtl |
| `slh-dsa` | pqc-direct | 249 (lab) | liboqs |
| `ed25519` | classical | 15 | OpenSSL |
| `ecdsa-p256` | classical | 13 | OpenSSL |
| `rsa-2048` | classical | 8 | OpenSSL |

Other constants: `EDNS_MTL_MODE_FULL = 65001` (lab EDNS option code),
`MTL_TYPE_CONDENSED = 0`, `MTL_TYPE_FULL = 1`,
`SLH_DSA_SHA2_128S_SIG_BYTES = 7856`, `MTL_SECURITY_PARAMETER_N = 16` (hash
length in bytes used by MTL formulas). Numbers 50 and 249 are **not assigned
by IANA**; lab use only.

### 6.3 `src/cipherops/workload.py`: zones and signing inputs

Why it exists: produce deterministic, realistic zones and the exact bytes
DNSSEC would sign.

| Name | Why it exists |
|---|---|
| `TTL`, `INCEPTION`, `EXPIRATION` | Fixed RRSIG times so signing inputs are byte-identical across runs. |
| `NSEC3_ITERATIONS = 0` | RFC 9276 recommendation. |
| `Item` | One RRset in the zone: `owner`, `rdtype`, `record_class` (`apex`, `small`, `medium`, `large`, `nsec3`), `rrset`. DNSKEY's `rrset` is `None` because it depends on the mode. |
| `Zone` | The whole zone: `dataset`, `origin` (`bench.example.`), `seed`, `items`, `data_names`, `nsec3_by_hash`. |
| `Zone.dnskey_rrset(mode)` | Builds the DNSKEY RRset for that algorithm. |
| `Zone.rrsets(mode)` | All RRsets in canonical order with DNSKEY filled in. **The index in this list is the RRset's ID everywhere else** (signature `i` belongs to RRset `i`). |
| `Zone.key_tag(mode)` | DNSKEY key tag, part of every RRSIG. |
| `placeholder_public_key(seed, mode)` | DNSKEY needs a key field of the right length; the real key is created inside each trial. Only the length matters for byte counts. |
| `_data_rrset(rng, owner, i)` | Implements the 60/30/10 record-size mix. |
| `build_zone(dataset, n_names, seed)` | Builds the zone + NSEC3 chain. |
| `rrsig_template(...)` | An RRSIG with all fields filled; signature empty for signing input, real for answers. |
| `signing_messages(zone, mode)` | RFC 4034 signing input per RRset + manifest rows. |
| `write_messages`, `read_blob_set` | The binary file format shared with the C harness (section 9). |
| `zone_text(zone, mode)` | Human-readable zone file (`datasets/<ds>/zone.txt`). |

### 6.4 `harness/mtlbench.c`: the crypto layer

Why it exists: accurate timing needs C and direct library calls; one process
per trial keeps trials independent. It contains **no cryptography**; it only
calls libraries.

| Function | Why it exists |
|---|---|
| `main()` | Parses `MODE --messages --algo --out-dir --full-verify`, loads messages, dispatches. |
| `load_messages()` | Reads `messages.bin`. |
| `now_ns()` | Monotonic nanosecond clock for timing. |
| `rss_bytes()`, `maxrss_bytes()` | Process memory, for signer-side cache usage. |
| `run_mtl()` | MTL via Verisign libmtl (below). |
| `run_direct()` | SLH-DSA via liboqs `OQS_SIG_sign` / `OQS_SIG_verify`. |
| `run_classical()` | OpenSSL `EVP_DigestSign` / `EVP_DigestVerify`. ECDSA DER is converted to the raw 64-byte DNSSEC form (`ecdsa_der_to_raw`). |
| `dnskey_pubkey_len()` | Public key length as DNSSEC encodes it (RFC 8080 / 6605 / 3110). |
| `write_set()`, `write_raw()` | Save signatures, ladder, public key to `--out-dir`. |
| `json_u64_array()` etc. | Print the JSON result. |

`run_mtl()` step by step (Verisign library calls in bold):

1. **`mtllib_key_new`**: create key (SLH-DSA underneath) → `keygen_ns`
2. For each RRset: **`mtllib_sign_append`**: add leaf, update nodes → `append_ns[i]`
3. **`mtllib_sign_get_signed_ladder`**: compute rungs, sign ladder once → `ladder_sign_ns`
4. For each RRset: **`mtllib_sign_get_condensed_sig`** → `condensed_ns[i]`, `condensed_bytes[i]`
5. Verifier setup from public key only (as a resolver would have)
6. **`mtllib_verify_signed_ladder`** → `ladder_verify_ns`
7. For each RRset: **`mtllib_verify`** with the condensed sig and cached ladder → `verify_condensed_ns[i]`
8. For `full_n` RRsets: verify condensed + ladder together (first contact) → `verify_full_ns[i]`
9. Tamper checks: altered message, altered signature, altered ladder must be rejected
10. Write `condensed.bin`, `signed_ladder.bin`, `public_key.bin`; print JSON

### 6.5 `src/cipherops/harness.py`: Python ↔ C bridge

| Name | Why it exists |
|---|---|
| `HARNESS` | Path to the `mtlbench` binary (`/work/harness/mtlbench` in Docker). |
| `run_trial(mode, messages, out_dir, full_verify)` | Builds the command line, runs `mtlbench`, parses JSON, adds exit code and stderr tail. |
| `load_signatures(mode, out_dir, limited)` | Reads saved signatures into a `Signatures` object. For MTL: condensed sigs + ladder. If the run was capped, `reused=True` (sample signatures are reused for byte counting; their size is fixed so the byte totals are still exact). |

### 6.6 `src/cipherops/dnswire.py`: DNS answers and byte accounting

Why it exists: transmitted bytes must come from real encoded DNS messages
carrying the real signatures, not from guesses.

| Name | Why it exists |
|---|---|
| `UDP_LIMITS = (1232, 4096)` | The two EDNS buffer sizes studied. |
| `Signatures` | Holds per-RRset signatures (`sigs`), the MTL `signed_ladder`, and `reused`. |
| `Signatures.rrsig_field(index, full)` | Bytes that go into the RRSIG signature field. Classical/direct: the signature. MTL: `0x00 + condensed` or `0x01 + condensed + ladder`. |
| `ResponseBuilder` | Builds query and answer messages for a zone + signature set. `index` maps (name, type) → RRset ID. |
| `ResponseBuilder.query(...)` | A DNS query with EDNS, DO bit, buffer size, and optional `mtl-mode-full` option. |
| `ResponseBuilder._nsec3_covering(name)` | Finds the NSEC3 record that matches or covers a name's hash. |
| `ResponseBuilder.response(kind, qname, qtype, payload, mtl_full)` | Positive / DNSKEY answer: RRset + RRSIG. NXDOMAIN: SOA + up to 3 NSEC3 (closest encloser, next closer, wildcard), each with RRSIG. |
| `wire_len(msg)` | Exact encoded size (with name compression). |
| `truncated_len(q)` | Size of an empty TC=1 reply (the model's assumption, see 7.4). |
| `exchange(query_bytes, response_bytes, tc_bytes, udp_limit, force_tcp)` | Decides UDP vs TC+TCP and returns DNS bytes, plus estimated round trips and packets. |
| `rrsig_bytes(msg)` | How many bytes of the answer are signatures. |
| `nx_names(zone, count, seed)` | Random non-existent names like `nx3fa2….bench.example.` |
| `response_catalog(...)` | Measures every answer type (section 5 step 6). |
| `query_workload(...)` | The lookup sequence. DNSKEY first, then Zipf-weighted hosts and NXDOMAINs. |
| `workload_accounting(...)` | Replays the sequence with cache rules; MTL fetches the full signature once over TCP. |

Byte formula used everywhere (model and live):

- UDP answer fits: `query + response`
- Too big: `query + tc_reply + (2 + query) + (2 + response)` (TCP adds a
  2-byte length prefix to each message)
- Forced TCP (MTL full fetch): `(2 + query) + (2 + response)`

### 6.7 `src/cipherops/cache.py`: cache usage

PS-036 requires measured cache usage kept separate from analytical estimates.

| Function | Kind | What it gives |
|---|---|---|
| `resolver_cache_measured(responses, ladder)` | measured | Puts the workload's answers into dnspython's real `LRUCache`, measures Python heap with `tracemalloc`, counts signature bytes; adds the one ladder for MTL. |
| `resolver_cache_analytical(mode, n_rrsigs, ...)` | analytical | Same quantity from formulas (signature size × count + ladder). |
| `signer_state_analytical(zone_rrsets)` | analytical | Hash storage of a binary node set: `(2N-1)·16 + N·16` bytes. |

### 6.8 `src/cipherops/analytical.py`: draft formulas

From draft-harvey-cfrg-mtl-mode section 12 (`n` = 16, `N` = number of RRsets):

| Function | Formula |
|---|---|
| `max_condensed_signature(n, N)` | `28 + 3n + n·floor(log2 N)` |
| `max_signed_ladder(n, N, sig)` | `8 + 4n + (16+n)·ceil(log2 N) + sig` |
| `max_full_signature` | sum of the two above |
| `rung_count(N)` | number of 1-bits in N |
| `zone_signature_storage` | total signature bytes for a whole zone, direct vs MTL |
| `udp_fragments`, `tcp_segments` | packet-count estimates from header arithmetic |

Used to check the library's measured sizes against the draft (they match: the
measured condensed sizes equal the formula maximum; the ladder is below its
maximum).

### 6.9 `src/cipherops/stats.py`

| Function | Why |
|---|---|
| `summarize(values)` | n, mean, median, std, p05, p95, min, max |
| `median_ci(values)` | Exact distribution-free interval for the median from sorted trial values (widest pair covering at least 95 %, or the full range if there are too few trials). |
| `median_ci_coverage(n)` | The real coverage of that interval for `n` trials (93.8 % for 5, 75 % for 3). |
| `ci_label(n)` | Text for tables and figure titles that states the coverage. |

### 6.10 `src/cipherops/preflight.py`

The go/no-go gate (section 5 step 3). `critical=False` checks (fixture scheme,
classical smoke tests) are reported but cannot block the run.

### 6.11 `src/cipherops/envinfo.py`

`collect()` records CPU model, CPU affinity, requested cpuset, governor,
memory, OS, kernel, Python, GCC, OpenSSL version, liboqs tag, MTL commit, MTL
compiler flags, harness SHA-256, Python package versions, git SHA, image ID.
That is the "pinned environment" evidence in the report appendix.

### 6.12 `src/cipherops/results.py`

Loads a finished run for the report.

| Name | Why |
|---|---|
| `Run` | All tables and JSON of one run in memory. |
| `list_runs()` | Run folders that have a `summary.json` (finished runs only). |
| `load(root)` | Reads config, environment, preflight, summary and all CSV/Parquet tables. |
| `timing_table(trials)` | Median + CI per dataset × mode, including extrapolated columns. |
| `fit_table(responses)` | Percentage of answers fitting in 1232 bytes. |
| `fmt_seconds`, `fmt_bytes` | Human-readable formatting. |

### 6.13 `src/cipherops/report.py`

Writes the report. Text such as the headline and discussion is **generated
from the data** (`_headline`, `_discussion`), so it cannot drift from the
numbers. `figures()` draws the charts (extrapolated bars are hatched).
`TRACEABILITY` maps each PS-036 requirement to the report section that
satisfies it. `build_html()` embeds images so the HTML is one file.

### 6.14 `dashboard/`

One Streamlit app (`streamlit run dashboard/live_lab/app.py`; `dashboard/app.py` launches it). It has six tiles:
Setup, Live, Benchmark, Compare, Learn and Reset. It drives the multi-zone lab in `ecosystem/` and starts the
offline harness as a background job.

| Module | Role |
|---|---|
| `live_lab/app.py` | entry point: page chrome, the sidebar and page tables, `main()` |
| `live_lab/ui/` | the pages: `home` (the two tracks), `track_offline` and `track_simulated` (one numbered-step page each, reusing `setup` for the image step, `benchmark_offline`, `lab`, `live` and `benchmark_simulated` for the others), `setup` (the Images page and the image cards), `compare_page`, `learn`, `reset`, plus `common` (shared state, navigation and the step bar) and `activity` (the activity log) |
| `live_lab/glossary.py` | the one list of labels and their explanations (the ⓘ icons) |
| `live_lab/driver.py` | job and suite status, the rule that nothing disturbs a running benchmark, stack pins and image pulls; it re-exports `lab.py` (lab handle and Docker Compose operations), `shapes.py` (zone sizes and estimates) and `artifacts.py` (manifests, saved runs, session, reset) |
| `live_lab/suite.py`, `suite_report.py` | the simulated benchmark and its report |
| `live_lab/offline_job.py` | runs `docker compose build` and the harness (`cipherops.runner run`) as a background job; state in `ecosystem/jobs/_offline/` |
| `live_lab/build_job.py`, `prepare_job.py` | the image build and the signing as background jobs with live logs |
| `live_lab/compare.py`, `compare_hub.py` | results of both paths reduced to one shape for the Compare tile; the offline side reads `results/runs/` |

Offline and simulated results are never drawn together. See [the dashboard guide](../dashboard/live_lab/README.md).

### 6.15 `src/cipherops/crosscheck.py` and `src/cipherops/live.py`

Covered in sections 7 and 8. `crosscheck.py` also holds the shared NSD
helpers (`NSD`, `_udp`, `_tcp`, `signed_zone_text`, `load_run_zone`) that
`live.py` reuses.

### 6.16 `tests/`

| File | What it checks |
|---|---|
| `test_workload.py` | Zone is deterministic, has `2N+5` RRsets, canonical order, all record classes, closed NSEC3 chain, and real Ed25519 signatures over our signing inputs pass dnspython's standard DNSSEC validator. |
| `test_dnswire.py` | Positive answers carry an RRSIG; MTL full adds the type byte and ladder; NXDOMAIN has SOA + NSEC3 proof with exactly one full signature; UDP/TC/TCP byte accounting; MTL fetches the ladder once per workload. |
| `test_analytical.py` | Formula values. |
| `test_cache.py` | Measured cache counts exact signature bytes; MTL counts the ladder once; analytical rows per family. |
| `test_harness.py` | (Docker only) Real signing in every mode: all signatures verify, every negative check rejected (including cross-message, wrong key, truncation), ECDSA wire form verifies, `mtlverify` and OpenSSL accept the artifacts, SLH-DSA signature is 7,856 B, MTL sizes within draft bounds, public key lengths match the DNSKEY field, artifacts round-trip. |
| `test_crosscheck.py` | Signed zone file parses, every RRset signed; (Docker) NSD serves it. |
| `test_dashboard.py` | Every tile, both Benchmark modes and each Compare view render without an exception. |
| `test_stats.py` | The order-statistic confidence interval of the median: exact coverage figures, narrowing with more trials, empirical coverage matches the formula, labels warn when trials are too few. |
| `test_lab_run.py` | Live-lab pass validity: SERVFAIL and timeouts are not latency; invalid passes are left out of the summary but their failures stay counted. |
| `test_lab_guard.py` | Demo safety: lab operations are refused while a benchmark runs, a launching suite counts as active, `dig` accepts only allow-listed options, signing starts when `work/` is root-owned, root-owned folders are removed, the image build is a one-at-a-time background job. |
| `test_evolution.py` | The evolving-zone tables over synthetic frames with known answers, and the config checks. The harness gate for it is in `test_harness.py`. |
| `test_results.py`, `test_control.py` | Paired per-trial ratios, the A/A control and its noise band, the timer-calibration summary. |
| `test_offline_job.py` | The offline-harness job: command, progress parsing, run pick-up, and that it blocks other CPU-heavy work. No Docker. |
| `test_learn.py` | The Learn pages need no network, and their size figures and draft versions agree with the formulas and the code. |
| `test_compare_hub.py` | The shared algorithm frame for offline runs and live-lab suites, ratios to a baseline, run-against-run ratios and the comparability notes. |
| `test_compare.py`, `test_live.py`, `test_prepare_job.py`, `test_prepare_story.py` | Dashboard helpers, packet decoding, the signing job and its replay (no Docker). |

```bash
PYTHONPATH=src python3 -m pytest -q -m "not harness"                      # host: tests that need no Docker
docker run --rm --network none cipherops-bench:latest \
  "python3 -m pytest tests/test_harness.py tests/test_crosscheck.py -q"   # harness gates, inside the image
```

### 6.17 Docker files

- `docker/Dockerfile`: one build graph for every image of a stack. The `toolchain` stage builds OpenSSL, liboqs and
  Verisign MTL at the versions in the stack profile (`ecosystem/stacks/<profile>.env`), from source (MTL compiled with
  `-O2` because upstream forces `-O0`; source unmodified). Four targets share it: `bench` (the offline harness image:
  libraries, NSD, tcpdump, pinned Python packages, `mtlbench`, the package) and the simulated lab's `nsd`, `unbound`
  and `tools` (ldns signer, resolver and client). Each image records the pins it was built from as `io.cipherops.*`
  labels, which Setup checks against the stack profile.
- `docker/build.yml`: builds those targets (`docker compose -f docker/build.yml --env-file <profile>.env build [bench |
  nsd unbound tools]`) under the names `cipherops-bench:<stack>` and `cipherops-eco-{nsd,unbound,tools}:<stack>`.
  `scripts/publish_images.sh` pushes the built images to a registry for the dashboard's "Pull from registry", and
  `.github/workflows/ci.yml` runs the tests and lints these Docker files when started by hand from the Actions tab.
- `docker/compose.yml`: service `bench` (runs the prebuilt image); `cpuset` pins one core;
  `mem_limit: 4g`; `network_mode: none` (no Internet, no noise); mounts
  `results/` and `configs/` from the host. Service `live` is the same plus
  `NET_ADMIN` (tc-netem on the container's own loopback).

---

## 7. Live mode flow (real server, real packets)

Command (service `live`: no outside network, but `NET_ADMIN` for tc-netem):

```bash
bash scripts/run_live.sh <run-folder>                    # all profiles, both UDP sizes, 3 repeats
bash scripts/run_live.sh <run-folder> lan 1232 1         # quick look
# Windows: .\scripts\run_live.ps1 -Run <run-folder> [-Profiles lan -Udp 1232 -Repeats 1]
```

Who is who (all inside the container):

| Role | What | Address |
|---|---|---|
| Asker (resolver) | `live.py` | client socket |
| Server | NSD with the run's signed zone | `127.0.0.1:53` |
| MTL full-signature server | second NSD whose zone carries the full MTL signature for the first-contact RRset | `127.0.0.2:53` |
| Network | `tc qdisc ... netem` on the loopback, MTU 1500 | `lo` |
| Recorder | `tcpdump` on the loopback interface (DNS ports and IP fragments) | writes `pcap/*.pcap` |

Network profiles (`live.PROFILES`). On loopback every packet crosses netem
once, so the per-packet delay is half the round trip:

| Profile | Round trip | Loss | UDP resend after |
|---|---|---|---|
| `lan` | none added | 0 | 1 s |
| `wan` | 50 ms | 0 | 400 ms |
| `lossy` | 50 ms | 1% per packet | 400 ms |

Flow of `live.run()`:

1. `Network` sets the loopback MTU to 1500, so 4096-byte UDP answers are
   IP-fragmented and TCP answers are segmented as on Ethernet. One lost
   fragment loses the whole datagram.
2. For each dataset: `load_run_zone()` rebuilds exactly the zone the run
   signed (same seed); `query_workload()` makes the same lookup list as the model.
3. For each mode:
   1. `load_signatures()` reads the real signatures from `artifacts/`.
   2. `signed_zone_text()` writes a zone file with an RRSIG per RRset.
   3. `NSD(...)` runs `nsd-checkzone`, starts `nsd`, waits until it answers.
      NSD is configured with `ipv4-edns-size: 4096` so the client's
      advertised buffer decides (NSD's own default caps UDP at 1232).
   4. For each repeat, profile and EDNS buffer (1232, 4096): `net.apply()`
      switches netem; `Capture(...)` records repeat 0 only.
   5. For each lookup not already cached, `exchange()` sends a real UDP
      query, resends it when no answer arrives in time (up to 4 tries), and
      on TC=1 opens a real TCP connection and asks again. Bytes = lengths of
      the actual DNS messages, resends included; time = `perf_counter_ns`.
   6. MTL only, first lookup: one TCP query with the `mtl-mode-full` option
      to `127.0.0.2` to fetch the ladder.
4. Writes `live.csv` (one row per exchange, with the DNS id and wall-clock
   start/end used to find its packets), `live_summary.csv` (one row per
   dataset × mode × profile × buffer: medians across repeats, min–max of the
   workload time, model bytes), `live.json` (settings, NSD version) and
   `pcap/live-<dataset>-<mode>-<profile>-<udp>.pcap`, then rewrites the report
   (Appendix E, `figures/live.png`).

Check the capture against the client's own count:

```powershell
python scripts/pcap_bytes.py results/runs/<run-folder>
```

Open a `.pcap` in Wireshark and filter `dns` to see each question, answer,
RRSIG and TC flag. `pcap.py` does the parsing in pure Python. The live lab
(`dashboard/live_lab`) draws one lookup's packets (UDP, IP fragments, TCP
handshake and segments) as a sequence diagram from its own captures.

Why live bytes differ from the model for MTL and RSA: when an NXDOMAIN
answer does not fit in 1232 bytes, NSD fills the truncated UDP reply with as
many whole records as fit (about 1 KB), while the model assumes an empty TC
reply (~53 B). Ed25519 and ECDSA match exactly. This is a server behaviour
difference, documented rather than hidden.

NSD does not implement `mtl-mode-full`; the second NSD instance stands in for
a server that does. NSD also does not validate signatures, which is why it
accepts the lab algorithm numbers.

---

## 8. NSD cross-check flow

```powershell
docker compose --env-file ecosystem/stacks/latest.env -f docker/compose.yml run --rm bench "python3 -m cipherops.runner crosscheck --run results/runs/<run>"
```

`crosscheck.run()` serves the signed zone with NSD and, for a sample of
queries, compares the model's answer size with NSD's answer over TCP and UDP
4096, and whether both agree on TC at 1232. Writes `crosscheck.csv`; the
report gets an Appendix D.

---

## 9. Data formats and output files

### 9.1 Binary message / signature files

`messages.bin`, `messages_sample.bin`, `condensed.bin`, `signatures.bin`:

```
u32 big-endian  count
repeat count times:
    u32 big-endian  length
    length bytes    data
```

Same format both ways between Python (`write_messages`, `read_blob_set`) and
C (`load_messages`, `write_set`). Item `i` always belongs to RRset `i` of
`Zone.rrsets(mode)`.

`signed_ladder.bin`, `public_key.bin`: raw bytes.

### 9.2 Run folder `results/runs/<run_id>/`

| Path | Content |
|---|---|
| `config.yaml` | Exact settings used |
| `environment.json` | Pinned environment evidence |
| `preflight.json` | Go/no-go checks and verdict |
| `datasets/<ds>/zone.txt` | Human-readable zone |
| `datasets/<ds>/<mode>/messages.bin` | Signing inputs |
| `datasets/<ds>/<mode>/manifest.csv` | One row per RRset: `index, owner, rdtype, record_class, rr_count, rrset_wire_bytes, message_bytes, signed_in_trials` |
| `raw/<ds>__<mode>__t<k>.json.gz` | Untouched harness output per trial |
| `artifacts/<ds>/<mode>/` | Real signatures from trial 0 (`condensed.bin` + `signed_ladder.bin` + `public_key.bin` for MTL; `signatures.bin` otherwise) |
| `datasets.csv` | Per dataset: names, RRsets, signing-input bytes, record class counts |
| `trials.csv` | One row per trial × dataset × mode (section 10.3) |
| `ops.parquet` | One row per individual operation timing |
| `responses.csv` | One row per measured DNS answer |
| `workload.csv` | Workload byte totals per dataset × mode × UDP limit |
| `cache.csv` | Cache usage rows, `kind` = measured or analytical |
| `sizes.csv` | MTL measured vs draft formula sizes |
| `summary.json` | Statistics and CIs |
| `report.md`, `report.html`, `report.pdf`, `figures/` | The report |
| `live.csv`, `live_summary.csv`, `pcap/` | Live mode outputs |
| `crosscheck.csv` | NSD cross-check |

---

## 10. Glossary of variables, config keys and columns

### 10.1 Config keys (`configs/*.yaml`)

| Key | Meaning | Full / quick |
|---|---|---|
| `run_name` | Suffix of the run folder name | `ps036-pinned` / `quick-demo` |
| `seed` | Master random seed. Zone *i* uses `seed + i`; workload, NX names and mode order derive from it. Same seed → same zones, same queries. | 20261005 |
| `trials` | Measured repetitions | 5 / 3 |
| `warmup_trials` | Discarded runs per mode before measuring | 1 |
| `modes` | Algorithms to compare (keys from `codepoints.MODES`) | all 5 |
| `datasets[].name` | Dataset label | small, medium, large |
| `datasets[].n_names` | Number of hosts in the zone. RRsets = `2·n_names + 5`. | 50/500/5000 ; 20/60/200 |
| `full_verify_sample` | How many MTL full signatures (condensed + ladder) are verified per trial: the cold-cache case | 64 / 16 |
| `direct_sign_limit.<ds>` | Cap on RRsets signed by direct SLH-DSA per trial. `null` = all. Capped totals are extrapolated and labelled. | large: 1000 ; medium 60, large 100 |
| `wire.nx_sample` | Non-existent names measured in `response_catalog` | 200 / 50 |
| `wire.workload.n_queries` | Lookups in the simulated resolver workload | 2000 / 300 |
| `wire.workload.nx_fraction` | Share of lookups for names that do not exist | 0.10 |
| `wire.workload.zipf_s` | Popularity skew: weight of rank *r* is `1/(r+1)^s`. Higher = a few names dominate. | 1.1 |

### 10.2 Internal variables you will see in the code

| Variable | Where | Meaning |
|---|---|---|
| `cfg` | runner, live | The loaded config dict |
| `root` / `run_dir` | runner, live | Run folder path |
| `ds`, `name` | runner | One dataset entry / its name |
| `mk` | everywhere | Mode key, e.g. `slh-dsa-mtl` |
| `mode` | everywhere | The `Mode` object for `mk` |
| `zone` | everywhere | A `Zone` object |
| `n_total`, `sizes[name]` | runner | Number of RRsets in the zone |
| `msgs`, `messages` | runner, workload | List of signing inputs (bytes) |
| `manifest` | runner, workload | List of dicts describing each RRset |
| `paths[(ds, mk)]` | runner | Path of the messages file a trial signs |
| `limit` | runner | Direct SLH-DSA cap or `None` |
| `sample` | runner | Indices signed when capped |
| `limited` | runner, trials.csv | True if fewer RRsets were signed than the zone has |
| `res` | runner | Parsed JSON from one `mtlbench` run |
| `t` | runner | Trial number (0-based) |
| `order` | runner | Shuffled mode order for one trial |
| `sigs` | dnswire, live | `Signatures` object for one dataset × mode |
| `builder`, `b` | dnswire, live | `ResponseBuilder` |
| `wl`, `workload` | runner, live | Lookup list of `(kind, qname, qtype)` |
| `kind` | dnswire, live | `dnskey`, `positive` or `nxdomain` |
| `qname`, `qtype` | dnswire, live | Name and type asked |
| `payload`, `udp_limit` | dnswire, live | EDNS buffer size (1232 or 4096) |
| `mtl_full`, `full` | dnswire | Build the MTL full-signature variant |
| `seen` | dnswire, live | (name, type) pairs already answered (resolver cache) |
| `have_ladder` | dnswire, live | Whether the MTL resolver already holds the ladder |
| `idx` | dnswire, crosscheck | RRset ID (index into `Zone.rrsets`) |
| `full_for` | crosscheck | RRset IDs whose RRSIG gets the full MTL signature |
| `first_idx` | live | RRset ID of the first lookup (where MTL first contact happens) |
| `wire` | live | Bytes of the query packet sent |
| `udp`, `resp` | live | Bytes of the UDP reply / TCP reply received |
| `tc` | live | Parsed UDP reply, to read the TC flag |
| `hits` | live | Lookups answered from the client's own cache |

### 10.3 Important columns

`trials.csv`

| Column | Meaning |
|---|---|
| `n_signed`, `dataset_rrsets`, `limited` | RRsets signed vs in zone; capped or not |
| `sign_total_ns` | Total signing time. MTL = appends + ladder sign + condensed extraction |
| `ladder_sign_ns` | MTL: the one SLH-DSA signature |
| `verify_warm_total_ns` | Verify all RRsets with the ladder already verified/cached (MTL includes one ladder verify) |
| `verify_full_median_ns` | Median time to verify one full signature (MTL cold start) or one signature (others) |
| `sign_per_rrset_ns`, `verify_per_rrset_ns` | Totals divided by `n_signed` |
| `sign_total_extrapolated_ns` | Only when `limited`: per-RRset × zone RRsets |
| `signature_bytes_median`, `signature_bytes_max` | Per-RRset signature size (MTL: condensed) |
| `signed_ladder_bytes` | MTL ladder size |
| `public_key_bytes` | DNSKEY public key length |
| `failures_total`, `tamper_all_rejected`, `exit_code` | Correctness evidence |
| `node_set_rss_growth_bytes`, `maxrss_bytes` | Signer memory |

`responses.csv`: `kind`, `record_class`, `variant` (`default` or
`mtl_full`), `udp_limit`, `query_bytes`, `response_bytes`,
`truncated_response_bytes`, `rrsig_signature_bytes`, `fits_udp`,
`transport`, `dns_bytes`.

`workload.csv`: `lookups`, `transmitted_responses`, `dns_bytes`,
`response_bytes`, `rrsig_signature_bytes`, `truncations`, `tcp_exchanges`,
`mtl_full_fetches`, `round_trips_est`, `packets_est`.

`live_summary.csv` (one row per `dataset`, `mode`, `profile`, `udp_limit`):
`repeats`, `lookups`, `cache_hits`, `sent`, `udp_only`, `tc_then_tcp`,
`ladder_fetches`, `udp_retries` (summed over repeats), `failed` (summed),
`live_bytes`, `model_bytes`, `total_ms` (median over repeats),
`total_ms_min`, `total_ms_max`, `median_ms`, `p95_ms` (per lookup, median
over repeats).

`live.csv`: one row per exchange with `dataset`, `mode`, `profile`,
`udp_limit`, `repeat`, `seq`, `kind`, `qname`, `qtype`, `ladder_fetch`,
`qid` (DNS id), `t_start`/`t_end` (epoch seconds), `transport`,
`udp_retries`, `retry_bytes`, `tc_bytes`, `truncated`, `failed`,
`response_bytes`, `dns_bytes`, `ms`.

Units: `_ns` = nanoseconds, `_bytes` = bytes, `_ms` = milliseconds.

---

## 11. Measured vs analytical

PS-036 requires the two to be kept apart. Rule in this project: **every number
is labelled with where it came from.**

| Quantity | Measured (from real output) | Analytical (formula) |
|---|---|---|
| Sign / verify time | `mtlbench` timings | none |
| Signature, ladder, key sizes | Library output lengths | draft section 12 maxima (`sizes.csv`, `kind=analytical_max`) |
| DNS answer sizes | dnspython-encoded messages with real signatures; NSD packets in live mode | none |
| UDP/TCP decision and bytes | Real sizes vs buffer limit; live mode real TC | packet and fragment counts (`_est` columns) |
| Resolver cache | dnspython `LRUCache` + `tracemalloc` | signature size × count + ladder |
| Signer state | libmtl RSS growth | node-set hash arithmetic |
| Large-zone direct SLH-DSA totals when capped | per-signature measurements | × zone size, labelled *extrapolated* |

---

## 12. Design decisions and why

| Decision | Reason |
|---|---|
| No crypto written by us | PS-036: no new cryptography. All crypto is Verisign libmtl, liboqs, OpenSSL. |
| C harness, one process per trial | Accurate timing without Python overhead; trials cannot affect each other. |
| Synthetic zone `bench.example` | Real zones are not PQC-signed; we need controlled sizes and identical inputs every run. `.example` is reserved for testing. |
| Fixed seed and fixed RRSIG times | Byte-identical inputs → reproducible results. |
| Same messages for all modes (apart from alg number / key tag) | Fair comparison. |
| Interleaved, shuffled mode order | Thermal or background drift does not favour one mode. |
| Bootstrap CI of median | Robust with few trials; no normality assumption. |
| Pinned Docker image, one core, no network | Pinned environment requirement; removes network and scheduler noise. |
| Preflight gate | PS-036: defer if implementation or fixtures unavailable. |
| Direct SLH-DSA cap on the large zone | 10,005 signatures × ~0.6 s ≈ 100 min per trial. Evenly spaced sample + clearly labelled extrapolation; set `null` for a fully measured overnight run. |
| Lab algorithm numbers 50 / 249, EDNS 65001 | No IANA assignments exist; documented as lab-only. |
| Message-level byte model + live NSD check | The model gives exact, repeatable numbers for every answer; live mode proves them with a real server and real packets. |
| Verisign MTL compiled with -O2 | Upstream build forces -O0 (debug). Source unchanged; flag recorded in environment. |

---

## 13. Five-minute explanation for a teammate

1. **Question**: does MTL make post-quantum DNSSEC practical? SLH-DSA
   signatures are 7,856 bytes and slow; MTL signs once and gives each record a
   ~200-byte proof.
2. **Setup**: a pinned Docker image with Verisign's MTL library, liboqs and
   OpenSSL. One command runs everything.
3. **Zones**: we generate a test domain `bench.example` in three sizes. Every
   RRset is turned into the exact bytes DNSSEC signs.
4. **Signing**: a small C program signs and verifies every RRset with each of
   the five algorithms, timing each step and checking tampered data is
   rejected. Repeated several times in random order.
5. **Bytes**: we build real DNS answers with the real signatures and count
   their size, see which fit in 1232-byte UDP, and add up the bytes a
   resolver would transfer for 2,000 lookups.
6. **Cache**: we store those answers in a real resolver cache and measure
   memory, and separately show the formula estimate.
7. **Proof with real packets**: live mode serves the zone from a real DNS
   server (NSD), a client sends real queries, and tcpdump captures the
   packets. The capture totals equal the client's counts byte for byte.
8. **Output**: CSV/Parquet tables, `summary.json` with confidence intervals,
   a report (MD/HTML/PDF).

Key result shape (quick-demo, large zone, live): MTL answers are ~320-415 bytes
and mostly fit in UDP; direct SLH-DSA answers are ~8,000 bytes and every one
needs TCP; MTL needs one 8 KB ladder fetch at first contact.
