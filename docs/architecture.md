# Cipherops: architecture

How the code is organised: the components, the repository, a reference for each source file, and the live and
cross-check flows. For what a run does stage by stage, with example values, see
[end-to-end flow](end-to-end-flow.md); for every output file and column, the [outputs reference](outputs-reference.md);
for how it is measured, the [methodology](methodology.md); for why, the [design decisions](design-decisions.md). Background
on DNS, DNSSEC and MTL is in [Learn](learn/README.md).

Contents: 1 The project in one paragraph · 2 Architecture at a glance · 3 Repository map · 4 File-by-file reference ·
5 Live mode flow · 6 NSD cross-check flow · 7 Binary file format


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

## 2. Architecture at a glance

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

## 3. Repository map

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
├── tests/                  pytest unit tests (see section 4.16)
├── docs/
│   ├── methodology.md      measurement method in detail
│   ├── architecture.md     this file
│   ├── end-to-end-flow.md  what each of the 7 stages takes in and puts out
│   ├── outputs-reference.md  every output file and column
│   ├── getting-started.md, codespaces.md, scope-and-limits.md, README.md (index)
│   └── learn/              background and three animated walkthroughs
└── results/runs/<run_id>/  every run's outputs ([outputs reference](outputs-reference.md))
```

---

## 4. File-by-file reference

### 4.1 `src/cipherops/runner.py`: entry point and pipeline

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
| `cmd_run(args)` | The main pipeline ([end-to-end flow](end-to-end-flow.md)). |
| `cmd_preflight`, `cmd_report`, `cmd_crosscheck`, `cmd_live` | The other sub-commands. |
| `main(argv)` | Command-line parser. |

Constants: `CLASSICAL_SIG_BYTES` (64/64/256, used by analytical cache
estimate), `OP_ARRAYS` (names of the per-operation timing arrays in the
harness JSON).

### 4.2 `src/cipherops/codepoints.py`: the five algorithms

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

### 4.3 `src/cipherops/workload.py`: zones and signing inputs

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
| `write_messages`, `read_blob_set` | The binary file format shared with the C harness (section 7). |
| `zone_text(zone, mode)` | Human-readable zone file (`datasets/<ds>/zone.txt`). |

### 4.4 `harness/mtlbench.c`: the crypto layer

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

### 4.5 `src/cipherops/harness.py`: Python ↔ C bridge

| Name | Why it exists |
|---|---|
| `HARNESS` | Path to the `mtlbench` binary (`/work/harness/mtlbench` in Docker). |
| `run_trial(mode, messages, out_dir, full_verify)` | Builds the command line, runs `mtlbench`, parses JSON, adds exit code and stderr tail. |
| `load_signatures(mode, out_dir, limited)` | Reads saved signatures into a `Signatures` object. For MTL: condensed sigs + ladder. If the run was capped, `reused=True` (sample signatures are reused for byte counting; their size is fixed so the byte totals are still exact). |

### 4.6 `src/cipherops/dnswire.py`: DNS answers and byte accounting

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
| `response_catalog(...)` | Measures every answer type (stage 5 of the [end-to-end flow](end-to-end-flow.md)). |
| `query_workload(...)` | The lookup sequence. DNSKEY first, then Zipf-weighted hosts and NXDOMAINs. |
| `workload_accounting(...)` | Replays the sequence with cache rules; MTL fetches the full signature once over TCP. |

Byte formula used everywhere (model and live):

- UDP answer fits: `query + response`
- Too big: `query + tc_reply + (2 + query) + (2 + response)` (TCP adds a
  2-byte length prefix to each message)
- Forced TCP (MTL full fetch): `(2 + query) + (2 + response)`

### 4.7 `src/cipherops/cache.py`: cache usage

PS-036 requires measured cache usage kept separate from analytical estimates.

| Function | Kind | What it gives |
|---|---|---|
| `resolver_cache_measured(responses, ladder)` | measured | Puts the workload's answers into dnspython's real `LRUCache`, measures Python heap with `tracemalloc`, counts signature bytes; adds the one ladder for MTL. |
| `resolver_cache_analytical(mode, n_rrsigs, ...)` | analytical | Same quantity from formulas (signature size × count + ladder). |
| `signer_state_analytical(zone_rrsets)` | analytical | Hash storage of a binary node set: `(2N-1)·16 + N·16` bytes. |

### 4.8 `src/cipherops/analytical.py`: draft formulas

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

### 4.9 `src/cipherops/stats.py`

| Function | Why |
|---|---|
| `summarize(values)` | n, mean, median, std, p05, p95, min, max |
| `median_ci(values)` | Exact distribution-free interval for the median from sorted trial values (widest pair covering at least 95 %, or the full range if there are too few trials). |
| `median_ci_coverage(n)` | The real coverage of that interval for `n` trials (93.8 % for 5, 75 % for 3). |
| `ci_label(n)` | Text for tables and figure titles that states the coverage. |

### 4.10 `src/cipherops/preflight.py`

The go/no-go gate (stage 2 of the [end-to-end flow](end-to-end-flow.md)). `critical=False` checks (fixture scheme,
classical smoke tests) are reported but cannot block the run.

### 4.11 `src/cipherops/envinfo.py`

`collect()` records CPU model, CPU affinity, requested cpuset, governor,
memory, OS, kernel, Python, GCC, OpenSSL version, liboqs tag, MTL commit, MTL
compiler flags, harness SHA-256, Python package versions, git SHA, image ID.
That is the "pinned environment" evidence in the report appendix.

### 4.12 `src/cipherops/results.py`

Loads a finished run for the report.

| Name | Why |
|---|---|
| `Run` | All tables and JSON of one run in memory. |
| `list_runs()` | Run folders that have a `summary.json` (finished runs only). |
| `load(root)` | Reads config, environment, preflight, summary and all CSV/Parquet tables. |
| `timing_table(trials)` | Median + CI per dataset × mode, including extrapolated columns. |
| `fit_table(responses)` | Percentage of answers fitting in 1232 bytes. |
| `fmt_seconds`, `fmt_bytes` | Human-readable formatting. |

### 4.13 `src/cipherops/report.py`

Writes the report. Text such as the headline and discussion is **generated
from the data** (`_headline`, `_discussion`), so it cannot drift from the
numbers. `figures()` draws the charts (extrapolated bars are hatched).
`TRACEABILITY` maps each PS-036 requirement to the report section that
satisfies it. `build_html()` embeds images so the HTML is one file.

### 4.14 `dashboard/`

One Streamlit app (`streamlit run dashboard/live_lab/app.py`; `dashboard/app.py` launches it). Home shows two tracks, **Offline benchmark**
(Run → Results) and **Simulated lab** (Prepare → Query → Benchmark), then Compare, Learn, Reset and the Node images
page (Manage images). It drives the multi-zone lab in `ecosystem/` and starts the
offline harness as a background job.

| Module | Role |
|---|---|
| `live_lab/app.py` | entry point: page chrome, the sidebar and page tables, `main()` |
| `live_lab/ui/` | the pages: `home` (node status and the two tracks), `track_offline` and `track_simulated` (a prerequisite panel from `setup`, then numbered steps reusing `benchmark_offline`, `lab`, `live` and `benchmark_simulated`), `setup` (the prerequisite panel and the Node images page), `compare_page`, `learn`, `reset`, plus `common` (shared state, navigation, the step bar and the help-text switch) and `activity` (the activity log) |
| `live_lab/glossary.py` | the one list of labels and their explanations (the ⓘ icons) |
| `live_lab/driver.py` | job and suite status, the rule that nothing disturbs a running benchmark, stack pins and image pulls; it re-exports `lab.py` (lab handle and Docker Compose operations), `shapes.py` (zone sizes and estimates) and `artifacts.py` (manifests, saved runs, session, reset) |
| `live_lab/suite.py`, `suite_report.py` | the simulated benchmark and its report |
| `live_lab/offline_job.py` | runs `docker compose build` and the harness (`cipherops.runner run`) as a background job; state in `ecosystem/jobs/_offline/` |
| `live_lab/build_job.py`, `prepare_job.py` | the image build and the signing as background jobs with live logs |
| `live_lab/compare.py`, `compare_hub.py` | results of both paths reduced to one shape for the Compare tile; the offline side reads `results/runs/` |

Offline and simulated results are never drawn together. See [the dashboard guide](../dashboard/live_lab/README.md).

### 4.15 `src/cipherops/crosscheck.py` and `src/cipherops/live.py`

Covered in sections 7 and 8. `crosscheck.py` also holds the shared NSD
helpers (`NSD`, `_udp`, `_tcp`, `signed_zone_text`, `load_run_zone`) that
`live.py` reuses.

### 4.16 `tests/`

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

### 4.17 Docker files

- `docker/Dockerfile`: one build graph for every image of a stack. The `toolchain` stage builds OpenSSL, liboqs and
  Verisign MTL at the versions in the stack profile (`ecosystem/stacks/<profile>.env`), from source (MTL compiled with
  `-O2` because upstream forces `-O0`; source unmodified). Four targets share it: `bench` (the offline harness image:
  libraries, NSD, tcpdump, pinned Python packages, `mtlbench`, the package) and the simulated lab's `nsd`, `unbound`
  and `tools` (ldns signer, resolver and client). Each image records the pins it was built from as `io.cipherops.*`
  labels, which the Node images page checks against the stack profile.
- `docker/build.yml`: builds those targets (`docker compose -f docker/build.yml --env-file <profile>.env build [bench |
  nsd unbound tools]`) under the names `cipherops-bench:<stack>` and `cipherops-eco-{nsd,unbound,tools}:<stack>`.
  `scripts/publish_images.sh` pushes the built images to a registry for the dashboard's "Pull from registry", and
  `.github/workflows/ci.yml` runs the tests and lints these Docker files when started by hand from the Actions tab.
- `docker/compose.yml`: service `bench` (runs the prebuilt image); `cpuset` pins one core;
  `mem_limit: 4g`; `network_mode: none` (no Internet, no noise); mounts
  `results/` and `configs/` from the host. Service `live` is the same plus
  `NET_ADMIN` (tc-netem on the container's own loopback).

---

## 5. Live mode flow (real server, real packets)

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

## 6. NSD cross-check flow

```powershell
docker compose --env-file ecosystem/stacks/latest.env -f docker/compose.yml run --rm bench "python3 -m cipherops.runner crosscheck --run results/runs/<run>"
```

`crosscheck.run()` serves the signed zone with NSD and, for a sample of
queries, compares the model's answer size with NSD's answer over TCP and UDP
4096, and whether both agree on TC at 1232. Writes `crosscheck.csv`; the
report gets an Appendix D.

---

## 7. Binary file format


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
