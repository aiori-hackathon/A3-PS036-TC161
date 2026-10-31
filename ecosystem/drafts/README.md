# Ecosystem Internet-Drafts

Working text for an **Informational** IETF Internet-Draft based on the Cipherops
`ecosystem/` multi-zone MTL DNSSEC lab. These files are markdown suitable for
later conversion with [kramdown-rfc](https://github.com/cabo/kramdown-rfc) or
xml2rfc. They are **not** RFCs and are not on the Standards Track.

> **Status: working text, not a PS-036 deliverable, and not regenerated.** Its numbers come from an earlier lab run
> that is not in this repository, taken with an earlier method (3 trials, bootstrap intervals, failed lookups not
> excluded). They have **not** been reproduced with the current code and must not be quoted as current results; the
> current method is in [methodology](../../docs/methodology.md), and frozen runs are in
> [hackathon/results](../../hackathon/results/README.md).

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
3. Section 6 cites numbers from that earlier run (see the status note).
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
