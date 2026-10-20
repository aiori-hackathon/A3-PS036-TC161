# Dashboard guide

What every part of the Cipherops dashboard shows, what each option changes, and how to read it.
The example numbers are from the full run `20261010T130246Z-ps036-pinned`.

Start the dashboard from the repository root:

```powershell
streamlit run dashboard/app.py
```

It reads the run folders under `results/runs/`. Nothing is computed from scratch in the browser except the
"Response sizes (model)" sub-tab, which rebuilds DNS answers from the signatures saved by the run.

---

## Quick vocabulary

| Word | Meaning |
|---|---|
| **Mode** | One of the five signing algorithms: RSA-2048, ECDSA P-256, Ed25519, SLH-DSA without MTL ("direct"), SLH-DSA with MTL. |
| **Dataset** | Zone size: small (50 names, 105 RRsets), medium (500 names, 1,005 RRsets), large (5,000 names, 10,005 RRsets). |
| **RRset** | A group of records with the same name and type. Each RRset gets one signature (one RRSIG). |
| **Trial** | One repeat of the signing/verification measurement. The full run has 5 trials. |
| **Median** | The middle value of the trials. Less affected by one slow trial than an average. |
| **95% CI** | Confidence interval: the range the true median most likely sits in. Shown as error bars. |
| **EDNS UDP buffer** | The largest answer the resolver accepts over UDP: 1232 bytes (recommended) or 4096 (older default). Bigger answers are truncated and retried over TCP. |
| **TC / TCP retry** | Answer too big for UDP: the server replies "truncated" (TC=1), the resolver asks again over TCP. |
| **NXDOMAIN** | "This name does not exist" answer. It carries the SOA record plus up to three NSEC3 proof records, each with its own signature, so it is often the biggest answer. |
| **Condensed signature** | MTL's short per-RRset signature (a few sibling hashes). 172 / 220 / 284 B for small / medium / large. |
| **Ladder** | The top of the MTL tree, signed once with SLH-DSA (about 8 KB). The resolver fetches it once and caches it. |
| **Measured** | Read from a real run: real signatures, real timings, real bytes. |
| **Analytical** | Calculated from the formulas in the MTL draft. Always labelled as such. |
| **Extrapolated** | Measured on a sample, then scaled up to the whole zone (only direct SLH-DSA on the large zone). |

---

## Sidebar (left)

| Item | What it is |
|---|---|
| **Run** (dropdown) | Which run folder to show. Every tab follows this choice. |
| **Go/no-go gate** | Result of the preflight checks (Verisign's own tests, our harness round-trip, tamper checks). Green GO means the reference implementation worked, so the results are valid. |
| **Trials / Seed** | Number of measured repeats (5) and the fixed random seed (20261005) that makes the zones and workload identical every run. |
| **CPU / Pinned to** | Processor model and the CPU cores the container was restricted to. |
| **MTL ref / liboqs / OpenSSL** | Exact library versions used (MTL commit `7cef019`, liboqs 0.16.0, OpenSSL 3.6.0). |

---

## Headline cards (top of the page)

All four cards use the **large** dataset (10,005 RRsets) and compare SLH-DSA **with** MTL against SLH-DSA **without** MTL.

### 1. Zone signing (large, 10,005 RRsets): 581.96 ms, "9,534× faster than direct SLH-DSA"

- 581.96 ms is the median time to sign the whole large zone with MTL: hash all 10,005 RRsets into the tree, sign the
  ladder once, extract 10,005 condensed signatures.
- 9,534× compares it with direct SLH-DSA, which needs one full SLH-DSA signature per RRset (about 0.55 s each).
- **Caveat:** for the large zone, direct SLH-DSA signed a sample of 1,000 RRsets per trial (`direct_sign_limit`
  in the config), because signing all 10,005 would take about 93 minutes per trial. The direct figure behind
  "9,534×" (5,548 s) is therefore **extrapolated** from that measured sample. The small and medium zones are fully
  measured (58 s and 558 s for direct versus 0.54 s and 0.57 s for MTL, about 100× and 1,000×).
  The caption under the cards says "all headline figures are directly measured"; this card is the exception.

### 2. Median NXDOMAIN response: 1.59 KB, "vs 31.98 KB direct"

- The typical size of a "name does not exist" answer.
- With direct SLH-DSA it holds about four 7,856-byte signatures, so about 32 KB. With MTL the same four signatures are
  condensed (284 B each), so about 1.6 KB.

### 3. Responses fitting 1232-byte UDP: 87% with MTL, "0% direct SLH-DSA"

- Out of every possible answer from the large zone (every positive name plus a 200-answer NXDOMAIN sample), the
  share that fits in one 1232-byte UDP packet, so no truncation and no TCP retry.
- MTL: all small and medium answers fit; large TXT answers (1-2 KB of data) and NXDOMAIN answers do not.
  (3,042 + 1,504) / (3,042 + 1,504 + 454 + 200) = 87%.
- Direct SLH-DSA: 0%. One signature (7,856 B) is already bigger than 1232 (and than 4096).

### 4. Workload bytes on the wire: 697.71 KB, "15.9× less than direct"

- Total DNS bytes (both directions) for the 2,000-lookup workload against the large zone at 1232 bytes:
  697.71 KB with MTL versus 11.08 MB direct.
- This card is the **calculated** value from step 5 of the run. The live run measured 923.5 KB for MTL
  (NSD fills truncated replies with records), which is still about 12× less than direct (11.08 MB).

---

## Tab 1: Signing & verification

### Metric (radio buttons)

| Option | What the bars show | Example, large zone |
|---|---|---|
| **Sign whole zone** | Time to sign every RRset in the zone. For MTL: append all leaves + sign the ladder once + extract all condensed signatures. | MTL 0.58 s; ECDSA 0.29 s; Ed25519 0.37 s; RSA 6.83 s; direct SLH-DSA 5,548 s (extrapolated) |
| **Verify whole zone (warm)** | Time for a resolver to check every signature, assuming it already has what it needs. For MTL: verify the ladder once + every condensed signature. | MTL 72 ms; RSA 226 ms; ECDSA 874 ms; Ed25519 1.17 s; direct 541 ms (sample) |
| **Sign per RRset** | Sign whole zone ÷ number of RRsets. | MTL 58 µs; ECDSA 29 µs; RSA 683 µs; direct 555 ms |
| **Verify per RRset** | Verify whole zone ÷ number of RRsets. | MTL 7 µs; RSA 23 µs; ECDSA 87 µs; direct 541 µs |
| **Verify one signature, cold (MTL: full sig)** | Checking one signature with nothing cached. For MTL this means the full signature: verify the ladder's SLH-DSA signature, then the condensed part. Median of 64 samples. | MTL 490 µs; direct 480 µs; ECDSA 73 µs; RSA 20 µs |

How to read the chart:

- **X axis:** dataset (small, medium, large). **Bars:** one per mode, colour-coded (green = MTL, red = direct SLH-DSA, greys = classical).
- **Y axis is logarithmic:** each grid step is 10× more. Needed because values range from microseconds to over an hour.
- **Error bars:** 95% confidence interval of the median across the 5 trials. Short bars mean stable measurements.
- **Hatched (striped) bars:** extrapolated from a sample (direct SLH-DSA on the large zone), not fully measured.
- **Hover:** shows how many RRsets were signed, the number of trials, and whether the bar is measured or extrapolated.

What it tells you:

- MTL signing is roughly constant (about 0.55 s) whatever the zone size, because the single ladder signature dominates.
  Direct SLH-DSA grows with the number of RRsets.
- Warm MTL verification is the fastest of all five modes, because it is just hashing.
- Cold MTL verification costs about the same as one direct SLH-DSA verification; it is paid once per zone.

### Table under the chart

One row per dataset and mode. Columns: `trials`, `n_signed` (RRsets actually signed per trial), `dataset_rrsets`
(RRsets in the zone), `checks_passed` (every signature verified and every tampered input rejected), then the five
times from the metric list, and `sign zone (extrapolated)` where a sample was used.

### "Where MTL signing time goes"

Stacked bars per dataset, split into:

- **append RRsets to node set (hashing):** adding each RRset as a leaf. Grows with zone size but stays small.
- **sign ladder (1 SLH-DSA signature):** the one expensive step, about 0.5 s every time.
- **extract condensed signatures:** reading each RRset's sibling hashes out of the tree.

---

## Tab 2: Transmitted bytes

### Options

- **EDNS UDP buffer:** 1232 or 4096. Changes the "does it fit" line and the workload totals.
- **Dataset:** which zone the first chart and the fit table show (defaults to large).

### Chart 1: response size by record class (box plot)

- X axis: kind of answer. `apex` (zone-top records such as DNSKEY), `small` (A/AAAA), `medium` (MX, several AAAA),
  `large` (1-2 KB TXT), `nxdomain`.
- Y axis (log): DNS answer size in bytes. Each box shows the spread of sizes for that kind and mode.
- Dotted lines at 1232 and 4096: anything above the chosen line needs TCP.
- These are real DNS messages built with the real signatures from trial 1.

### Table: "% of responses that fit a N-byte UDP buffer"

Rows: answer kind. Columns: mode. Value: share of answers that fit without truncation. Large zone at 1232:

| | ECDSA | Ed25519 | RSA | Direct SLH-DSA | MTL |
|---|---|---|---|---|---|
| apex | 100 | 100 | 100 | 0 | 100 |
| small | 100 | 100 | 100 | 0 | 100 |
| medium | 100 | 100 | 100 | 0 | 100 |
| large | 2.6 | 2.6 | 0 | 0 | 0 |
| nxdomain | 100 | 100 | 0 | 0 | 0 |

### Chart 2: total DNS bytes for the lookup workload

- One bar per dataset and mode: total bytes to answer the 2,000-lookup workload (cached answers are not re-sent).
- Hover shows how many answers were sent, truncated, sent over TCP, and MTL full-signature fetches.

### Workload table

| Column | Meaning |
|---|---|
| `transmitted_responses` | Questions actually sent to the server (251 / 508 / 762). The rest came from the resolver cache. |
| `dns_bytes` | All DNS bytes, both directions, including TC replies and TCP retries. |
| `rrsig_signature_bytes` | The part of those bytes that is signature data. |
| `truncations` | Answers that did not fit UDP and were retried over TCP. |
| `tcp_exchanges` | Lookups that used TCP (truncations + MTL's one full-signature fetch). |
| `mtl_full_fetches` | 1 for MTL (the first-contact ladder fetch), 0 otherwise. |
| `round_trips_est`, `packets_est` | **Analytical** estimates: UDP = 1 round trip; truncated = 3 (UDP, TCP handshake, TCP query). Not measured. |

---

## Tab 3: Cache usage

### Left chart: "Resolver cache · measured"

- After the workload, the answers the resolver received are parsed from wire format and stored in a real dnspython
  resolver cache. The bar is the signature bytes held in that cache, plus the ladder for MTL.
- Large zone: MTL 376 KB (368 KB condensed signatures + 8 KB ladder); direct 10.7 MB; ECDSA/Ed25519 87 KB; RSA 349 KB.
- Hover: `cached_responses` (answers stored), `cached_rrsigs` (signatures inside them; an NXDOMAIN holds up to 4),
  `python_heap_bytes` (memory the cache actually took in Python), `ladder_bytes`.

### Right chart: "Resolver cache · analytical"

- Same number of signatures × the maximum signature size from the MTL draft's formulas (section 12).
  Used as a cross-check of the measured value. It is a little higher than measured because it uses the maximum size.

### "MTL signer state" table

What the signing side has to keep in memory for MTL:

| Column | Meaning |
|---|---|
| `node_set_rss_growth_bytes` (measured) | How much the signer's memory grew while appending all RRsets to the tree. Large: about 1 MB. |
| `maxrss_bytes` (measured) | Peak memory of the signing process. Large: about 13.9 MB. |
| `node_set_hash_bytes` (analytical) | Draft formula for storing all tree hashes: (2N−1)·16 + N·16 bytes. Large: about 480 KB. |

---

## Tab 4: MTL sizes vs draft

Checks that the reference library produces the sizes the MTL draft predicts.

- **Chart "Condensed signature: measured vs formula":** largest measured condensed signature next to the formula's
  maximum. They match exactly: 172 / 220 / 284 B.
- **Chart "Signed ladder: measured vs formula":** measured ladder (8,024 / 8,152 / 8,120 B) is at or below the formula's
  maximum (8,152 / 8,248 / 8,376 B). Below is expected: the formula assumes the most rungs possible.
- **Table rows:**
  - `rungs`: number of rungs in the ladder (4 / 8 / 7).
  - `zone_signature_storage_mtl`: bytes of all signatures for the whole zone with MTL (large: 2.7 MB measured).
  - `zone_signature_storage_direct`: same for direct SLH-DSA (large: 78.6 MB). On the large zone this is
    `measured_per_sig_x_n` because only 1,000 RRsets were signed.

---

## Tab 5: Live demo

Three sub-tabs.

### 5a. Response sizes (model)

Builds the actual DNS answer each mode would send for one question, using this run's saved signatures. No network.

| Option | Effect |
|---|---|
| **Dataset** | Which zone. |
| **Query** | `DNSKEY (apex)`, `NXDOMAIN (random name)`, or one of the first 300 names (shows name, type, size class). |
| **UDP buffer** | 1232 or 4096. |

Output:

- **Bar chart:** answer size per mode. Green = fits the UDP buffer, red = needs TCP. MTL appears twice:
  the normal answer, and "full sig (first contact, TCP)" (condensed signature + ladder).
- **Table:** response bytes, signature bytes inside it, fits UDP or not, transport (udp or udp+tcp), total bytes
  on the wire for the exchange, estimated round trips.
- **Show response (dig-style):** the full answer as text, like the `dig` tool prints it. Long signatures are shortened.
- **Pseudocode of the measured flow:** how MTL signing and validation work, in a few lines.

### 5b. Recorded live run (packets)

Results of `scripts/run_live.ps1`: a real client sent the workload to a real NSD server inside Docker, over three
simulated networks, and every packet of repeat 0 was captured.

| Option | Effect |
|---|---|
| **Dataset** | Which zone. |
| **EDNS UDP buffer** | 1232 or 4096. |

**Bar chart:** total time for the whole workload (about 250-760 lookups, one after another), per network profile and
mode. Y axis is logarithmic. Error bars: fastest and slowest of the 3 repeats.

- `lan`: no added delay. Whole batch takes milliseconds.
- `wan`: 50 ms round trip. Each UDP lookup costs about 50 ms; a truncated one about 150 ms (UDP, then TCP setup, then TCP query).
- `lossy`: 50 ms round trip and 1% packet loss. Lost UDP packets wait 0.4 s and are resent.

Large zone, wan, whole batch: MTL 38.7 s at 4096 and 63.5 s at 1232; direct SLH-DSA 125.6 s; ECDSA 38.6 s / 42.9 s.

**Table:**

| Column | Meaning |
|---|---|
| `sent` | Lookups sent to the server (cache misses). |
| `udp_only` | Answered over UDP alone. |
| `tc_then_tcp` | Truncated over UDP, then retried over TCP. |
| `ladder_fetches` | MTL first-contact full-signature fetch (1 for MTL). |
| `udp_retries` | UDP queries resent because no answer arrived in time (only in `lossy`). |
| `failed` | Lookups that never got an answer (0 everywhere). |
| `live_bytes` | Bytes measured by the client (both directions). |
| `model_bytes` | The calculated value from step 5, for comparison. Equal except where NSD fills truncated replies. |
| `total_ms` | Time for the whole batch (median of 3 repeats). |
| `median_ms`, `p95_ms` | Time for a typical lookup, and for the slowest 5% of lookups. |

**Packet viewer** (under the table): pick one lookup and see its real packets from the capture file.

| Option | Effect |
|---|---|
| **Profile** | lan, wan or lossy. |
| **Mode** | Which algorithm. |
| **Vertical axis** | `packet order` (evenly spaced) or `time` (real spacing in milliseconds; shows the 50 ms delays). |
| **Only lookups that needed TCP or a resend** | Hides the plain UDP lookups. |
| **Lookup** | The specific lookup: sequence number, name, type, transport, answer size, time. "(ladder fetch)" marks MTL's first-contact fetch. |

Output:

- Four cards: packets on the wire, transport, answer size, client time.
- **Sequence diagram:** vertical lines are the resolver and NSD (127.0.0.1; for MTL's ladder fetch also the
  full-signature NSD at 127.0.0.2). Each arrow is one packet. Blue = UDP, orange = TCP with data, grey = TCP control
  (handshake, acknowledgements), purple = IP fragment (a 4096-byte UDP answer split into pieces).
- **Packet table:** number, time, from, to, protocol, TCP flags, IP size, payload size, DNS detail.
- **Download button:** saves the `.pcap` file to open in Wireshark.

### 5c. Send a real query

Send one question yourself from this PC to NSD in Docker, for all five modes at once.

It needs a running server first:

```powershell
.\scripts\live_server.ps1 -Run 20261010T130246Z-ps036-pinned -Profile wan
```

Leave that terminal open (Ctrl+C stops it) and reload the page.

| Option | Effect |
|---|---|
| **Query** | Same list as 5a. |
| **EDNS UDP buffer** | 1232 or 4096. |
| **MTL: first contact (fetch full signature)** | Also performs MTL's ladder fetch over TCP, as a resolver with an empty cache would. |
| **Send real queries** | Sends the question to each mode's NSD (one port per mode). |

Output:

- Bar chart and table: time, answer size, bytes on the wire, transport, resends, per mode.
- **Show the packets of:** pick a mode; the left diagram is the client's view (timed on this PC), the right diagram is
  the server's view (captured by tcpdump inside the container).
- **Answer (dig-style):** the real answer received.

---

## Tab 6: Reproducibility

Everything needed to check or repeat the run.

- **Go/no-go gate:** each preflight check with its status and details (Verisign's 89 unit tests, our harness
  round-trip, tamper rejection, liboqs and OpenSSL checks).
- **Datasets:**

| dataset | names | RRsets | small | medium | large | NSEC3 | apex | signing input bytes |
|---|---|---|---|---|---|---|---|---|
| small | 50 | 105 | 35 | 10 | 5 | 51 | 4 | 20 KB |
| medium | 500 | 1,005 | 303 | 147 | 50 | 501 | 4 | 207 KB |
| large | 5,000 | 10,005 | 3,042 | 1,504 | 454 | 5,001 | 4 | 1.9 MB |

  RRsets = 2 × names + 5: one data RRset and one NSEC3 per name, plus SOA, NS, DNSKEY, NSEC3PARAM and the apex's NSEC3.
- **Configuration:** the exact `config.yaml` used (trials, seed, modes, datasets, workload settings, sampling cap).
- **Pinned environment:** operating system, CPU, core pinning, compiler, library versions and commits.

---

## Where each part's data comes from

| Dashboard part | File in the run folder | Produced by |
|---|---|---|
| Signing & verification | `trials.csv`, `summary.json` | main run, step 4 |
| Transmitted bytes | `responses.csv`, `workload.csv` | main run, step 5 |
| Cache usage | `cache.csv` | main run, step 5 |
| MTL sizes vs draft | `sizes.csv` | main run, step 5 |
| Response sizes (model) | `artifacts/` (saved signatures) | main run, step 4 |
| Recorded live run | `live_summary.csv`, `live.csv`, `live.json`, `pcap/` | `run_live.ps1` |
| Send a real query | `results/live-server/` | `live_server.ps1` |
| Reproducibility | `preflight.json`, `datasets.csv`, `config.yaml`, `environment.json` | main run, steps 1-3 |
