"""Algorithm identifiers and code points used by the benchmark.

LAB ONLY. IANA has not assigned DNSSEC algorithm numbers for SLH-DSA or
SLH-DSA-MTL, nor an EDNS option code for mtl-mode-full. The values below are
local placeholders for an isolated benchmark and must never be used on the
Internet. Value 50 for SLH-DSA-MTL-SHA2-128s mirrors the illustration in
draft-fregly-dnsop-slh-dsa-mtl-dnssec-05 Appendix A.
"""

from dataclasses import dataclass

EDNS_MTL_MODE_FULL = 65001  # RFC 6891 local/experimental range 65001-65534


@dataclass(frozen=True)
class Mode:
    key: str            # identifier used in configs and results
    harness_mode: str   # mtlbench MODE argument
    harness_algo: str   # --algo value passed to mtlbench
    dnssec_alg: int     # RRSIG/DNSKEY algorithm field
    public_key_len: int # DNSKEY public key field length in bytes
    family: str         # "mtl" | "pqc-direct" | "classical"
    label: str


MODES: dict[str, Mode] = {
    m.key: m
    for m in (
        Mode("slh-dsa-mtl", "mtl", "SLH-DSA-SHA2-128s-MTL-SHA2-128", 50, 32, "mtl",
             "SLH-DSA-SHA2-128s with MTL"),
        Mode("slh-dsa", "direct", "SLH_DSA_PURE_SHA2_128S", 249, 32, "pqc-direct",
             "SLH-DSA-SHA2-128s without MTL"),
        Mode("ed25519", "classical", "ed25519", 15, 32, "classical", "Ed25519 (alg 15)"),
        Mode("ecdsa-p256", "classical", "ecdsa-p256", 13, 64, "classical", "ECDSA P-256 (alg 13)"),
        Mode("rsa-2048", "classical", "rsa-2048", 8, 260, "classical", "RSA-2048/SHA-256 (alg 8)"),
    )
}

MTL_TYPE_CONDENSED = 0x00
MTL_TYPE_FULL = 0x01

SLH_DSA_SHA2_128S_SIG_BYTES = 7856
MTL_SECURITY_PARAMETER_N = 16
