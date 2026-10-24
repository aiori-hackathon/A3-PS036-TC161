# Ecosystem Internet-Drafts

Working text for an **Informational** IETF Internet-Draft based on the Cipherops
`ecosystem/` multi-zone MTL DNSSEC lab. These files are markdown suitable for
later conversion with [kramdown-rfc](https://github.com/cabo/kramdown-rfc) or
xml2rfc. They are **not** RFCs and are not on the Standards Track.

## Documents

| File | Docname | Intended status |
|---|---|---|
| [draft-joshi-dnsop-mtl-dnssec-bench-00.md](./draft-joshi-dnsop-mtl-dnssec-bench-00.md) | `draft-joshi-dnsop-mtl-dnssec-bench-00` | Informational |

**Title:** Measuring and Operating SLH-DSA-MTL DNSSEC in a Multi-Zone Hierarchy

## How to read the draft

1. Start with the Abstract and Section 1 (scope: measurement / operations, not a
   new crypto algorithm).
2. Section 3–4 describe the Docker lab, CSK key scheme, cold = Unbound process
   restart, and metric sources (pcap / client log / unbound-control).
3. Section 6 cites verified numbers from the full run
   `eco-default-draft06/20261014-142918`.
4. Section 7 covers the DS/ladder KSK+ZSK interop issue, draft06 vs kaizer-02 vs
   SigTag, and cold-start full-ladder observations.
5. Section 9 states what the lab does **not** prove about Internet deployment.

## What this draft does NOT claim

* It is **not** an RFC and does not request Standards Track status.
* It does **not** invent or standardize a signature scheme; it uses Verisign’s
  reference SLH-DSA-MTL stack as published.
* Size wins are for **draft-fregly-06 condensed-by-default** semantics (EDNS
  65050 for full ladders), not for always-full (kaizer-02) encodings.
* Results are from a **synthetic** hierarchy: Docker, `tc` delays, frozen zones,
  CSK signing, single stub client.
* SigTag support is phrased carefully: not found in the public Unbound branches
  examined at the pinned commit—not proven absent everywhere.
* Multiple root full-signature fetches are an **observation**, not a proven bug
  unless separately demonstrated.

## Result CSVs and supporting artifacts

Primary run used in the draft:

```
ecosystem/results/eco-default-draft06/20261014-142918/
  summary.csv      # trial aggregates (Section 6 tables)
  passes.csv       # per-trial cold/warm + capture_dropped
  workload.csv     # seeded query list
  run.json         # config + image digests + manifests
  pcap/            # per-pass captures on Unbound eth0
```

Related:

```
ecosystem/configs/lab.yaml
ecosystem/stacks/draft06.env
ecosystem/reports/verisign-unbound-ds-ladder-issue.md
```

A smaller smoke run also exists under
`ecosystem/results/eco-smoke-draft06/` but is **not** the source of the draft’s
headline numbers.

## Next steps (individual I-D submission)

These steps are for the author; they are not done by the lab tooling.

1. **Fill author contact** in the markdown front matter (`email: TBD`).
2. **Convert** markdown → XML, e.g. with kramdown-rfc, then `xml2rfc` to text/HTML;
   or author directly in xml2rfc v3 XML using the same section structure.
3. **Check nits**: `idnits` on the generated `.txt`; fix boilerplate, references,
   and line length as needed.
4. **Post as individual submission** via the [IETF Datatracker](https://datatracker.ietf.org/)
   (create draft, upload, submit). Filename should match
   `draft-joshi-dnsop-mtl-dnssec-bench-00`.
5. **Notify dnsop** (optional but usual): send a short note to dnsop@ietf.org with
   the Datatracker link after posting.
6. **Revise** (`-01`, …) as reviews arrive; keep numbers tied to a dated lab run
   directory or regenerate CSVs and update Section 6 + Appendix B together.

Do not present the markdown in this folder as an approved IETF consensus document.
