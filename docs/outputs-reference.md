# Cipherops outputs reference

Every file a run writes, every column in it, what unit it is in, how it is calculated and an
example value. The examples show the shape of the data for `configs/quick.yaml` (zones of 20 / 60 / 200 names,
3 trials) and are **not results**; every run's own numbers are in its `report.html`.

To watch these values being produced one by one, run `scripts/own.py` (see its header).

## Units and words used everywhere

| Term | Meaning |
|---|---|
| `_ns` | nanoseconds. 1 µs = 1,000 ns, 1 ms = 1,000,000 ns, 1 s = 1,000,000,000 ns. Measured in `mtlbench.c` with `clock_gettime(CLOCK_MONOTONIC_RAW)` immediately before and after one library call. |
| `_bytes`, B | bytes. KB = 1,000 B, MB = 1,000,000 B. |
| dataset | one zone size: `small`, `medium`, `large` (20 / 60 / 200 names in the quick run, 50 / 500 / 5,000 in the full run). |
| RRset | all records with the same owner name and type. Each RRset gets exactly one signature. A zone of *n* names has 2*n* + 5 RRsets (4 apex + *n* data + *n* + 1 NSEC3). |
| mode | `slh-dsa-mtl`, `slh-dsa`, `ed25519`, `ecdsa-p256`, `rsa-2048`. |
| family | `mtl` (SLH-DSA in MTL mode), `pqc-direct` (SLH-DSA signing every RRset), `classical` (Ed25519, ECDSA, RSA). |
| record class | `apex` (SOA, NS, DNSKEY, NSEC3PARAM), `small` (one A or AAAA), `medium` (3 × MX or 4 × AAAA), `large` (TXT of 1,024–2,048 characters), `nsec3`; in DNS answers also `nxdomain`. |
| trial | one complete sign + verify of one zone in one mode, in a fresh `mtlbench` process with a fresh key pair. |
| condensed signature | MTL: the hash path from one RRset's leaf to a rung. Small (156–204 B here). |
| signed ladder | MTL: the rungs plus one SLH-DSA signature over them. One per zone (≈ 8 KB). |
| full signature | MTL: condensed signature + signed ladder. What a resolver needs when it has no ladder yet. |
| warm / cold verify | warm: the resolver already holds a verified ladder, so only hashes are checked. cold: first contact, the ladder's SLH-DSA signature is checked too. |
| limited | direct SLH-DSA signs only a sample of RRsets on medium/large (`direct_sign_limit`: 60 and 100); the zone total is extrapolated. |
| median, CI | middle value over the trials; confidence interval of the median from order statistics (exact, any distribution): the widest pair of sorted trial values that covers the median with at least 95% probability, or the full range if there are too few trials. Coverage depends only on the trial count: 75% for 3, 93.8% for 5, 96.9% for 6. |

## Where each file comes from

| File | Stage | Written by |
|---|---|---|
| `config.yaml` | 1 | `runner.cmd_run` (the loaded config) |
| `environment.json` | 1 | `envinfo.collect`, `timer.calibrate` |
| `preflight.json` | 2 | `preflight.run` |
| `datasets/<ds>/zone.txt` | 3 | `workload.zone_text` |
| `datasets/<ds>/<mode>/messages.bin`, `messages_sample.bin`, `manifest.csv` | 3 | `workload.signing_messages`, `write_messages` |
| `datasets.csv` / `.parquet` | 3 | `runner.cmd_run` |
| `raw/<ds>__<mode>__t<n>.json.gz` | 4 | `runner.run_trials` (the JSON `mtlbench` printed) |
| `artifacts/<ds>/<mode>/*.bin` | 4 | `mtlbench` (trial 0 only) |
| `trials.csv` / `.parquet` | 4 | `runner.trial_metrics` |
| `control.csv` / `.parquet` | 4 | `runner.cmd_run` (the A/A copy's rows, split from `trials`) |
| `evolution.csv`, `evolution_direct.csv` (+ `.parquet`) | 4 | `evolution.run` (`mtlbench evolve`, `evolve-direct`) |
| `ops.parquet` | 4 | `runner.op_rows` |
| `responses.csv`, `workload.csv`, `cache.csv`, `sizes.csv` (+ `.parquet`) | 5 | `runner.analyse` |
| `summary.json` | 6 | `runner.summarise` |
| `report.md`, `report.html`, `figures/*.png` | 7 | `report.write` |

Every table is written twice: `.csv` to read by eye, `.parquet` for the report and dashboard. `ops` is parquet only.

---

## Stage 1 and 2 files

### `config.yaml`
The configuration actually used, after command-line overrides (`--trials`, `--modes`, `--datasets`).

| Key | Quick demo | Meaning |
|---|---|---|
| `seed` | 20261005 | seeds zone content (`seed + dataset index`), trial order and the query workload |
| `trials` / `warmup_trials` | 3 / 1 | measured trials per (dataset, mode); unmeasured warm-up runs per mode |
| `modes` | all 5 | |
| `control` | ed25519 | the mode that also runs as an A/A copy inside every trial; `null` = none |
| `evolution` | k 1 and 10, 4 rounds each | evolving-zone experiment (**Zone updates** in the dashboard): `k` (changed RRsets per round), `rounds`, cached-ladder `ages`; absent = skipped |
| `datasets` | small 20, medium 60, large 200 | names per zone |
| `full_verify_sample` | 16 | MTL cold verifications per trial (`--full-verify`) |
| `direct_sign_limit` | small none, medium 60, large 100 | SLH-DSA direct signs this many evenly spaced RRsets |
| `wire.nx_sample` | 50 | NXDOMAIN answers measured in `responses.csv` |
| `wire.workload` | 300 queries, 10% non-existent names, Zipf 1.1 | the lookup stream replayed in `workload.csv` |

### `environment.json`
Proof of what the numbers were measured on.

| Field | Quick demo |
|---|---|
| `git_sha` | `75e0809…-dirty` (`-dirty` = uncommitted changes) |
| `host_image_id` | Docker image `sha256:b01f41ed…` |
| `hardware.cpu_model` | Intel Core Ultra 7 155U |
| `hardware.cpu_affinity` | `[2]` – the one core every trial ran on |
| `hardware.mem_total` | memory visible to the container |
| `software.kernel` | `6.18.33.2-microsoft-standard-WSL2` (Docker Desktop on Windows) |
| `software.openssl`, `openssl_tag`, `liboqs_tag` | OpenSSL 3.6.0, liboqs 0.16.0 (profile `latest`) |
| `software.mtl_reference_commit`, `mtl_reference_cflags` | `7cef0191…` (profile `latest`), `-fPIC -Wall -Wextra -O2` |
| `software.harness_sha256` | hash of the compiled `mtlbench`, so a changed harness is visible |
| `software.python_packages` | dnspython, numpy, pandas, … versions |
| `timer` | timer calibration written at the start of the run (`timer.calibrate`, from `mtlbench calibrate`): the cost of an empty timed region, the smallest clock step, and per-call versus 1,000-call batch timing of Ed25519 verification and one condensed MTL verification. `per_call_timing_reliable` is true when an empty timed region costs at most 5% of the fastest operation. Absent or `error` if the calibration could not run. |

### `preflight.json`
`verdict` (`GO` or `NO-GO (defer per PS-036)`), `algorithm`, and `checks`: a list of
`{check, ok, critical, detail}`. A run continues only if every critical check is ok.

| Check | Critical | What it runs | Quick demo detail |
|---|---|---|---|
| `reference_library_installed` | yes | files exist: `libmtlslib.so`, `mtllib.h`, `liboqs.so` | ok |
| `reference_unit_tests_and_vectors` | yes | Verisign's `mtltest` | 89 tests passed, 0 failed |
| `reference_fixture_scheme` | no | reads the scheme of the bundled test vectors | ML-DSA-44-MTL-SHAKE-128 |
| `reference_tools_roundtrip` | yes | `mtlkeygen` → `mtlsign` → `mtlverify` on 16 messages + 1 tampered | all verified, tamper rejected |
| `harness_smoke_<mode>` × 5 | MTL and direct only | `mtlbench` on a 6-name zone | exit 0, no failures |
| `mtl_artifacts_verified_by_reference_tool` | yes | `mtlverify` on the harness's own MTL signatures; a changed message must fail | accepted, tamper rejected |
| `slh_dsa_artifacts_verified_by_openssl` | if OpenSSL has SLH-DSA | OpenSSL on the harness's own non-MTL signatures; a changed message must fail (interoperability, not a known-answer test) | accepted, tamper rejected |

---

## Stage 3 files

### `datasets/<ds>/<mode>/messages.bin`
The unsigned inputs `mtlbench` signs. Format: 4-byte count, then for each message a 4-byte length and the
bytes. One message per RRset = RRSIG fields with an empty signature (33 B here) + the RRset in canonical form
(RFC 4034 §3.1.8.1). Small zone, `slh-dsa-mtl`: 45 messages, 9,311 B of messages, file 9,495 B
(= 4 + 45 × 4 + 9,311). Modes differ only in the algorithm byte, key tag and the DNSKEY message length,
so ECDSA's file is 9,527 B and RSA's 9,723 B.

`messages_sample.bin` exists only where `direct_sign_limit` caps direct SLH-DSA (medium: 60 of 125, large:
100 of 405). Indices: `round(i × (n − 1) / (limit − 1))`, evenly spread so every record class is covered.

### `datasets/<ds>/<mode>/manifest.csv`
One row per message.

| Column | Meaning | Example (small, `host000000 A`) |
|---|---|---|
| `index` | position in `messages.bin` (canonical order) | 15 |
| `owner`, `rdtype` | the RRset | `host000000.bench.example.`, `A` |
| `record_class` | see glossary | `small` |
| `rr_count` | records in the RRset | 1 |
| `rrset_wire_bytes` | RRset encoded on its own | 40 |
| `message_bytes` | signing input length = 33 + canonical RRset | 73 |
| `signed_in_trials` | False if left out of the direct SLH-DSA sample | True |

### `datasets/<ds>/zone.txt`
The zone in text form (first mode's DNSKEY). For reading only.

### `datasets.csv`
| Column | small | medium | large |
|---|---|---|---|
| `n_names` | 20 | 60 | 200 |
| `rrsets` | 45 | 125 | 405 |
| `signing_input_bytes` | 9,311 | 33,941 | 69,840 |
| `apex` / `small` / `medium` / `large` / `nsec3` | 4 / 14 / 3 / 3 / 21 | 4 / 32 / 17 / 11 / 61 | 4 / 117 / 70 / 13 / 201 |

---

## Stage 4 files

### `raw/<ds>__<mode>__t<n>.json.gz`
Exactly what `mtlbench` printed, plus `exit_code`, `stderr_tail` and `command` added by `harness.run_trial`.
45 files (3 trials × 3 datasets × 5 modes). Kept for audit; later stages use `trials.csv` and `artifacts/`.

| Key | Mode | Meaning | Small, trial 0 |
|---|---|---|---|
| `n_messages`, `message_bytes` | all | how many inputs were signed, their total size | 45, 9,311 |
| `keygen_ns` | all | key pair generation | MTL 57.9 ms |
| `append_ns[]` | MTL | add each message to the Merkle tree (hashing) | median 1,182 ns |
| `ladder_sign_ns` | MTL | sign the ladder once with SLH-DSA | 436.4 ms |
| `condensed_ns[]` | MTL | extract each condensed signature | median 224 ns |
| `verifier_setup_ns` | MTL | build a verifier from the public key only | 43.8 µs |
| `ladder_verify_ns` | MTL | verify the signed ladder once | 440.9 µs |
| `verify_condensed_ns[]` | MTL | warm verify of each message | median 2,441 ns |
| `verify_full_ns[]` | MTL | cold verify, first `full_verify_sample` messages | median 436.6 µs (16 values) |
| `condensed_bytes[]`, `signed_ladder_bytes` | MTL | sizes | 156 each, 8,024 |
| `sign_ns[]`, `verify_ns[]`, `signature_bytes[]` | direct, classical | one sign and one verify per message | Ed25519: 26.9 µs, 90.3 µs, 64 B |
| `public_key_bytes` | all | DNSKEY public key field size | 32 (64 ECDSA, 260 RSA) |
| `failures` | all | verifications that failed (must be 0) | `{ladder: 0, verify_condensed: 0, verify_full: 0}` |
| `tamper` | all | negative cases, each must be rejected: changed message, damaged and truncated signature, signature for another message, wrong key; MTL also a damaged ladder, changed series id, full signature with damaged ladder. Cases that do not apply are omitted | all present values `true` |
| `observations` | MTL | measured library behaviour, not pass/fail: `condensed_header_bytes_unauthenticated` = leading condensed-signature bytes the library does not authenticate (0 to 2) | 2 |
| `failures.verify_wire` | classical | ECDSA verified again in the r‖s form sent on the wire | 0 |
| `memory` | all | `rss_*` from `/proc/self/statm`, `maxrss` from `VmHWM` in `/proc/self/status` | see note below |
| `stderr_tail` | all | last 2,000 chars of stderr. For MTL it holds library `ERROR` lines from the tamper tests: expected | |

### `artifacts/<ds>/<mode>/`
The real signatures from trial 0, used in stage 5 to build DNS answers.

| File | Mode | Small | Large |
|---|---|---|---|
| `condensed.bin` (blob set) | MTL | 6,676 B (45 signatures) | 80,660 B (405) |
| `signed_ladder.bin` | MTL | 8,024 B | 8,056 B |
| `signatures.bin` (blob set) | direct | 353,704 B (45 × 7,856 + headers) | 786,004 B (100 sampled) |
| `signatures.bin` | Ed25519 / ECDSA / RSA | 3,064 / 3,064 / 11,704 B | 27,544 / 27,544 / 105,304 B |
| `public_key.bin` | MTL, direct | 32 B | 32 B |

### `trials.csv` (45 rows: one per trial)
Built by `runner.trial_metrics(res, …)`. Examples: small/`slh-dsa-mtl`/trial 0, and large/`slh-dsa`/trial 0 for
the extrapolated columns.

| Column | Unit | How it is calculated | Example |
|---|---|---|---|
| `dataset`, `mode`, `family`, `algorithm`, `trial` | | identifiers | small, slh-dsa-mtl, mtl, SLH-DSA-SHA2-128s-MTL-SHA2-128, 0 |
| `n_signed` | count | `res.n_messages` | 45 (large direct: 100) |
| `dataset_rrsets` | count | RRsets in the zone | 45 (large: 405) |
| `limited` | bool | `n_signed < dataset_rrsets` | False (large direct: True) |
| `keygen_ns` | ns | `res.keygen_ns` | 57,946,322 (57.9 ms) |
| `exit_code` | | `mtlbench` exit code, 0 = all checks passed | 0 |
| `failures_total` | count | sum of `res.failures` | 0 |
| `tamper_all_rejected` | bool | all values of `res.tamper` true | True |
| `public_key_bytes` | B | `res.public_key_bytes` | 32 |
| `maxrss_bytes` | B | `res.memory.maxrss`: peak resident memory of `mtlbench` (`VmHWM`). | ~7.7 MB |
| `sign_total_ns` | ns | MTL: Σ`append_ns` + `ladder_sign_ns` + Σ`condensed_ns`; others: Σ`sign_ns` | 436,544,224 (0.437 s) |
| `sign_append_total_ns` | ns | MTL: Σ`append_ns` | 164,657 |
| `ladder_sign_ns` | ns | MTL: one SLH-DSA signature over the ladder | 436,368,764 |
| `condensed_extract_total_ns` | ns | MTL: Σ`condensed_ns` | 10,803 |
| `ladder_verify_ns` | ns | MTL: one ladder verification | 440,936 |
| `verify_condensed_total_ns` | ns | MTL: Σ`verify_condensed_ns` | 132,718 |
| `verify_warm_total_ns` | ns | MTL: `ladder_verify_ns` + Σ`verify_condensed_ns`; others: Σ`verify_ns`. Time to verify every signed RRset once | 573,654 (574 µs) |
| `verify_condensed_median_ns` | ns | MTL: median of `verify_condensed_ns` – one warm answer | 2,441 |
| `verify_full_median_ns` | ns | MTL: median of `verify_full_ns` (cold); others: median of `verify_ns` | 436,552 |
| `signature_bytes_median`, `signature_bytes_max` | B | median / max of `condensed_bytes` (MTL) or `signature_bytes` | 156, 156 |
| `signed_ladder_bytes` | B | MTL ladder size | 8,024 |
| `signature_material_bytes` | B | MTL: Σ`condensed_bytes` + ladder; others: Σ`signature_bytes` | 6,492 + 8,024 = 14,516 |
| `node_set_rss_growth_bytes` | B | MTL: `rss_after_append − rss_after_keygen`, memory the signer's tree took | 114,688 |
| `sign_per_rrset_ns` | ns | `sign_total_ns / n_signed` | 9,700,983 (9.7 ms – mostly the one ladder signature spread over 45) |
| `verify_per_rrset_ns` | ns | `verify_warm_total_ns / n_signed` | 12,748 |
| `sign_total_extrapolated_ns` | ns | only when limited: `sign_per_rrset_ns × dataset_rrsets` | large direct: 559.8 ms × 405 = 226.7 s |
| `verify_warm_total_extrapolated_ns` | ns | only when limited: `verify_per_rrset_ns × dataset_rrsets` | large direct: 238.1 ms |

Columns that do not apply to a mode are empty (NaN).

### `control.csv` (the A/A control)
Same columns as `trials.csv`, for the control copy of one mode (`control:` in `config.yaml`, e.g. `ed25519-aa`). The
copy runs the same harness call on the same input as its mode, in a random position inside every trial, so the true
ratio between the two is 1. `results.noise_floor_table` takes the per-trial ratio of the two and reports the widest
deviation from 1 inside its interval as the noise `band`. The report uses it to mark a paired ratio as clearly
outside the noise, or not distinguishable from it. The control never enters `responses`, `workload`, `cache` or
`sizes`, and not `ops.parquet`.

### `ops.parquet` (16,899 rows)
One row per individual timed library call, for distribution plots.

| Column | Meaning |
|---|---|
| `dataset`, `mode`, `trial` | which trial |
| `op` | `append`, `condensed`, `verify_condensed`, `verify_full` (MTL) or `sign`, `verify` (others) |
| `index` | message index inside the trial |
| `ns` | the time of that one call |

### `evolution.csv` and `evolution_direct.csv` (the evolving zone, section 6.4 of the methodology)
Written when the config has an `evolution:` block. Every row is `kind = measured`.

`evolution.csv`: one row per trial, dataset, k and round (round 0 is the initial full-zone ladder).

| Column | Meaning |
|---|---|
| `trial`, `dataset`, `k`, `round` | identifiers; `changed` is k (the whole zone in round 0) |
| `leaf_count`, `rung_count`, `ladder_bytes` | the series after the round: leaves, rungs (the 1 bits of the leaf count) and the signed ladder's size |
| `append_ns`, `ladder_sign_ns`, `ladder_verify_ns` | total time to append the changed leaves; the one SLH-DSA ladder signature; one ladder verification (what a resolver pays to take in the update) |
| `condensed_extract_ns` | time to extract a fresh condensed signature for every RRset of the zone (a serving cost, not part of the update) |
| `condensed_bytes_median`, `_max`, `_mean` | condensed signature size over the zone |
| `tested_age_<a>`, `ok_age_<a>` | RRsets tested and RRsets whose fresh condensed signature verifies against the ladder `a` rounds old; empty when no ladder that old exists yet |
| `stale_tested`, `stale_ok` | signatures issued when each leaf was created, and how many still verify against the newest ladder |
| `rss_bytes`, `leaf_index_mismatch`, `exit_code` | signer memory; times the library disagreed with the leaf-index expectation (must be 0); harness exit code |

`evolution_direct.csv`: one row per trial, k and round: `signed` (= k), `sign_ns` and `verify_ns` for signing and
verifying the k changed RRsets with plain SLH-DSA.

---

## Stage 5 files

### `responses.csv` (5,196 rows)
Every possible answer built once with dnspython around the real signatures and measured. Per dataset and mode:
1 DNSKEY answer + one answer per data name + `nx_sample` (50) NXDOMAIN answers, each at UDP limits 1232 and 4096;
MTL also has the `mtl_full` variant. Small: (1 + 20 + 50) × 2 = 142 rows, MTL 284.

Example: small, `host000000.bench.example. A`, limit 1232.

| Column | Meaning | Ed25519 | MTL default | MTL full | SLH-DSA |
|---|---|---|---|---|---|
| `kind` | `dnskey`, `positive` or `nxdomain` | positive | positive | positive | positive |
| `record_class` | class of the answered RRset (`nxdomain` for NXDOMAIN) | small | small | small | small |
| `qname` | the name asked for | host000000… | | | |
| `variant` | `default`; MTL also `mtl_full` (query carries EDNS option 65001, answer carries the full signature) | default | default | mtl_full | default |
| `udp_limit` | client's EDNS buffer size, 1232 or 4096 | 1232 | 1232 | 1232 | 1232 |
| `query_bytes` | encoded query (EDNS, DO bit) | 53 | 53 | 57 | 53 |
| `response_bytes` | encoded answer: RRset + RRSIG (NXDOMAIN: SOA + up to 3 NSEC3, each signed) | 178 | 271 | 8,295 | 7,970 |
| `truncated_response_bytes` | the empty TC=1 reply sent when the answer does not fit | 53 | 53 | 53 | 53 |
| `rrsig_signature_bytes` | signature bytes inside the answer (MTL: +1 type byte per signature) | 64 | 157 | 8,181 | 7,856 |
| `fits_udp` | `response_bytes ≤ udp_limit` | True | True | False | False |
| `ip_fragments_est` | if UDP: ⌈(response + 8) / 1,480⌉ IPv4 fragments, else 0 | 1 | 1 | 0 | 0 |
| `transport` | `udp`, `udp+tcp` (truncated then retried over TCP), `tcp` (MTL full goes straight to TCP) | udp | udp | tcp | udp+tcp |
| `dns_bytes` | DNS bytes both ways, see formula below | 231 | 324 | 8,356 | 8,133 |
| `truncated` | answer was truncated (TC=1) | False | False | False | True |
| `round_trips_est` | udp 1, tcp 2, udp+tcp 3 | 1 | 1 | 2 | 3 |
| `packets_est` | see formula below | 2 | 2 | 14 | 16 |

**`dns_bytes`** (`dnswire.exchange`):

```
fits in UDP:   query + response                                        53 + 178 = 231
too big:       query + truncated + (2 + query) + (2 + response)        53 + 53 + 2 + 53 + 2 + 7,970 = 8,133
MTL full:      (2 + query) + (2 + response)                            2 + 57 + 2 + 8,295 = 8,356
```
The 2s are the TCP length prefix in front of every DNS message on TCP. IP, UDP and TCP headers are not in `dns_bytes`.

**`packets_est`** (header arithmetic, not captured packets):
`udp` = 1 query + fragments; `tcp` = 3 handshake + 1 query + ⌈(response + 2) / 1,460⌉ + 4 teardown;
`udp+tcp` = 2 (UDP query and TC reply) + the TCP count.

### `workload.csv` (30 rows: 3 datasets × 5 modes × 2 UDP limits)
The 301-lookup stream (1 DNSKEY + 300) replayed through a resolver with a cache. Rules: an answer already
received is served from cache (nothing sent); NXDOMAIN names are all different; MTL's very first answer is
followed by one `mtl-mode-full` fetch over TCP to get the ladder.

| Column | Meaning | small, 1232: Ed25519 | MTL | SLH-DSA |
|---|---|---|---|---|
| `signatures_reused_from_sample` | direct SLH-DSA on a capped zone reuses sampled signatures (all 7,856 B, so sizes are exact) | False | False | False |
| `lookups` | queries the client made | 301 | 301 | 301 |
| `transmitted_responses` | lookups not answered from cache | 48 | 48 | 48 |
| `dns_bytes` | Σ `dns_bytes` of the transmitted exchanges | 30,376 | **48,196** | **955,504** |
| `response_bytes` | Σ response sizes | 27,405 | 45,175 | 947,389 |
| `rrsig_signature_bytes` | Σ signature bytes in those responses | 7,552 | 25,187 | 927,008 |
| `truncations` | answers that did not fit and were retried over TCP | 3 | 3 | 48 |
| `tcp_exchanges` | truncations + MTL full fetches | 3 | 4 | 48 |
| `mtl_full_fetches` | ladder fetches (0 or 1) | 0 | 1 | 0 |
| `round_trips_est`, `packets_est` | Σ of the per-exchange estimates | 54, 125 | 56, 140 | 144, 1,148 |

Large zone at 1232: Ed25519 53,292 B (7 TCP), MTL 88,895 B (33 TCP), SLH-DSA 1,522,371 B (106 TCP).

### `cache.csv` (36 rows)
What a resolver (and the MTL signer) holds in memory. `scope` = `resolver` or `signer`; `kind` = `measured`
or `analytical` (draft §12 formulas). Measured resolver rows: the transmitted answers at 1232 are parsed back
from wire into dnspython's `LRUCache` and measured with `tracemalloc`.

| Column | Meaning | small, MTL measured | small, MTL analytical |
|---|---|---|---|
| `cached_responses` | answers in the cache | 48 | |
| `cached_rrsigs` | RRSIG records in them | 118 | 118 |
| `cached_wire_bytes` | Σ encoded size of the cached answers | 36,859 | |
| `cached_signature_bytes` | Σ signature bytes in them | 17,006 | 118 × 157 = 18,526 |
| `per_signature_bytes` | analytical bytes per signature (MTL: 1 + max condensed) | | 157 |
| `ladder_bytes` | the signed ladder the resolver keeps (MTL) | 8,024 | 8,120 (maximum) |
| `signature_material_bytes` | signatures + ladder | 25,030 | 26,646 |
| `python_heap_bytes`, `python_heap_peak_bytes` | heap the cache objects occupy (tracemalloc) | 484,575, 488,455 | |
| `node_set_rss_growth_bytes` | signer scope, measured: median of the trials column | 114,688 | |
| `maxrss_bytes` | signer scope: median `maxrss_bytes` of the trials (see `trials.csv`) | ~7.7 MB | |
| `node_set_hash_bytes` | signer scope, analytical: (2N − 1) × 16 + N × 16 | | 2,144 |

Large zone, resolver measured: Ed25519 11,968 B, MTL 36,175 + 8,056 = 44,231 B, SLH-DSA 1,469,072 B.

### `sizes.csv` (27 rows)
MTL sizes measured against the draft's maximum formulas (16-byte hashes, N = RRsets).

| `quantity` | `kind` | Formula | small (N 45) | medium (125) | large (405) |
|---|---|---|---|---|---|
| `condensed_signature` | `measured_max` | largest `condensed_bytes` | 156 | 172 | 204 |
| | `analytical_max` | 28 + 3·16 + 16·⌊log₂N⌋ | 156 | 172 | 204 |
| `signed_ladder` | `measured` | `signed_ladder_bytes` | 8,024 | 8,088 | 8,056 |
| | `analytical_max` | 8 + 4·16 + 32·⌈log₂N⌉ + 7,856 | 8,120 | 8,152 | 8,216 |
| `rungs` | `analytical` | popcount(N) – a count, not bytes | 4 | 6 | 5 |
| `zone_signature_storage_mtl` | `analytical` | N × max condensed + max ladder | 15,140 | 29,652 | 90,836 |
| | `measured` | `signature_material_bytes` | 14,516 | 27,828 | 87,092 |
| `zone_signature_storage_direct` | `analytical` | N × 7,856 | 353,520 | 982,000 | 3,181,680 |
| | `measured` / `measured_per_sig_x_n` | Σ signatures, or median signature × N when limited | 353,520 | 982,000 | 3,181,680 |

---

## Stage 6: `summary.json`

```
trials[dataset][mode][metric] = {n, mean, median, std, p05, p95, min, max, median_ci: [lo, hi], ci_coverage}
    metrics: sign_total_ns, sign_per_rrset_ns, verify_warm_total_ns, verify_per_rrset_ns,
             verify_full_median_ns, keygen_ns
trials[dataset][mode].all_checks_passed     every trial exit 0, no failures, tamper rejected
trials[dataset][mode].limited               signing sample used
transmitted[dataset][mode][udp_limit]       dns_bytes, response_bytes, rrsig_signature_bytes, truncations,
                                            tcp_exchanges, mtl_full_fetches, transmitted_responses
response_fit[dataset][mode][record_class]   fit_1232_pct, median_bytes   (default answers at 1232)
evolution                                   checks_passed, update_cost[], coverage[], stale_min_share{}
paired_ratios[]                             one row per dataset and metric: direct SLH-DSA / MTL taken per trial
                                            (ratio = median, ratio_lo, ratio_hi, coverage, extrapolated)
```

`ci_coverage` is the real coverage of `median_ci` for `n` trials (0.9375 for 5).

## Stage 7: report

`report.md` / `report.html`: the written report (problem, method, every table, discussion, appendices with
config, environment and preflight). Figures:

| Figure | Shows | From |
|---|---|---|
| `sign.png` | whole-zone signing time per mode and zone size (log scale) | `trials` |
| `verify.png` | whole-zone warm verification time (log scale) | `trials` |
| `responses.png` | answer sizes by record class | `responses` |
| `workload.png` | total DNS bytes of the lookup stream at 1232 | `workload` |
| `cache.png` | resolver signature material, measured vs analytical | `cache` |
