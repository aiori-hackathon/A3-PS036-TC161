"""Analytical size estimates from draft-harvey-cfrg-mtl-mode-09 section 12.

Everything in this module is a formula-derived estimate. Results produced from
it are always tagged kind="analytical" and reported separately from bytes that
were directly measured on library output or encoded DNS messages.
"""

from __future__ import annotations

import math


def floor_log2(n: int) -> int:
    return n.bit_length() - 1 if n > 0 else 0


def ceil_log2(n: int) -> int:
    return (n - 1).bit_length() if n > 1 else 0


def max_condensed_signature(n_sec: int, n_messages: int) -> int:
    return 28 + 3 * n_sec + n_sec * floor_log2(n_messages)


def max_signed_ladder(n_sec: int, n_messages: int, underlying_sig: int) -> int:
    return 8 + 4 * n_sec + (16 + n_sec) * ceil_log2(n_messages) + underlying_sig


def max_full_signature(n_sec: int, n_messages: int, underlying_sig: int) -> int:
    return max_condensed_signature(n_sec, n_messages) + max_signed_ladder(n_sec, n_messages, underlying_sig)


def rung_count(n_messages: int) -> int:
    """Binary rung strategy: one rung per 1-bit of N (draft section 6.6)."""
    return bin(n_messages).count("1")


def zone_signature_storage(n_messages: int, direct_sig: int, n_sec: int) -> dict[str, int]:
    """Bytes of signature material a signer or cache holds for a whole zone."""
    return {
        "direct": n_messages * direct_sig,
        "mtl": n_messages * max_condensed_signature(n_sec, n_messages)
        + max_signed_ladder(n_sec, n_messages, direct_sig),
    }


def udp_fragments(payload: int, mtu: int = 1500, ip_header: int = 20, udp_header: int = 8) -> int:
    """IPv4 fragments needed for one UDP datagram carrying `payload` DNS bytes."""
    per_fragment = (mtu - ip_header) // 8 * 8
    return max(1, math.ceil((payload + udp_header) / per_fragment))


def tcp_segments(payload: int, mss: int = 1460) -> int:
    return max(1, math.ceil(payload / mss))
