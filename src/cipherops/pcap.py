"""Read tcpdump pcap files and label every packet of DNS over UDP and TCP.

Pure Python so the dashboard can read captures on the host. IPv4 only, which
is all the live mode and the live server produce. IP fragments are tied to
their first fragment (same source, destination and IP id); TCP streams are
reassembled per direction so each segment knows which DNS message it carries.
"""

from __future__ import annotations

import socket
import struct
from pathlib import Path

import pandas as pd

LINK_HEADER = {0: 4, 1: 14, 101: 0, 113: 16, 276: 20}
MAGIC = {b"\xd4\xc3\xb2\xa1": ("<", 1e6), b"\xa1\xb2\xc3\xd4": (">", 1e6),
         b"\x4d\x3c\xb2\xa1": ("<", 1e9), b"\xa1\xb2\x3c\x4d": (">", 1e9)}
TCP_FLAGS = ((0x02, "SYN"), (0x10, "ACK"), (0x08, "PSH"), (0x01, "FIN"), (0x04, "RST"))


def _records(data: bytes):
    if data[:4] not in MAGIC:
        raise ValueError("not a pcap file (pcapng is not supported)")
    end, scale = MAGIC[data[:4]]
    link = struct.unpack(end + "I", data[20:24])[0] & 0x0FFFFFFF
    if link not in LINK_HEADER:
        raise ValueError(f"unsupported link type {link}")
    l2, off = LINK_HEADER[link], 24
    while off + 16 <= len(data):
        sec, frac, incl, _ = struct.unpack(end + "IIII", data[off:off + 16])
        if off + 16 + incl > len(data):
            break  # last record still being written by tcpdump
        yield sec + frac / scale, data[off + 16 + l2:off + 16 + incl]
        off += 16 + incl


def _dns_head(msg: bytes) -> dict:
    if len(msg) < 4:
        return {}
    qid, flags = struct.unpack(">HH", msg[:4])
    return {"dns_id": qid, "dns_qr": bool(flags & 0x8000), "dns_tc": bool(flags & 0x0200)}


def read(path: Path | str) -> pd.DataFrame:
    """One row per IPv4 packet: addresses, transport, flags, sizes and the DNS message it carries."""
    rows = []
    for no, (ts, ip) in enumerate(_records(Path(path).read_bytes()), 1):
        if len(ip) < 20 or ip[0] >> 4 != 4:
            continue
        ihl = (ip[0] & 0x0F) * 4
        total, ip_id, frag = struct.unpack(">HHH", ip[2:8])
        proto = ip[9]
        body = ip[ihl:total]
        r = {"no": no, "ts": ts, "src": socket.inet_ntoa(ip[12:16]), "dst": socket.inet_ntoa(ip[16:20]),
             "sport": None, "dport": None, "proto": {6: "TCP", 17: "UDP"}.get(proto, str(proto)),
             "ip_id": ip_id, "frag_offset": (frag & 0x1FFF) * 8, "more_frags": bool(frag & 0x2000),
             "ip_bytes": total, "payload": 0, "tcp_flags": "", "seq": None, "dns_id": None, "dns_qr": None,
             "dns_tc": None, "dns_bytes": None, "detail": ""}
        if r["frag_offset"]:
            r["proto"] = "IP fragment"
            r["payload"] = len(body)
        elif proto == 17 and len(body) >= 8:
            r["sport"], r["dport"], ulen = struct.unpack(">HHH", body[:6])
            r["payload"] = ulen - 8
            r.update(_dns_head(body[8:]), dns_bytes=ulen - 8)
        elif proto == 6 and len(body) >= 20:
            r["sport"], r["dport"], r["seq"] = struct.unpack(">HHI", body[:8])
            r["tcp_flags"] = " ".join(name for bit, name in TCP_FLAGS if body[13] & bit)
            r["payload"] = len(body) - (body[12] >> 4) * 4
            r["raw"] = body[(body[12] >> 4) * 4:]
        rows.append(r)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    _attach_fragments(df)
    _annotate_tcp(df)
    _describe(df)
    return df.drop(columns="raw", errors="ignore")


def _attach_fragments(df: pd.DataFrame) -> None:
    first = df[(df.proto == "UDP") & df.more_frags]
    owner = {(r.src, r.dst, r.ip_id): r for r in first.itertuples()}
    for i in df.index[df.proto == "IP fragment"]:
        f = owner.get((df.at[i, "src"], df.at[i, "dst"], df.at[i, "ip_id"]))
        if f is not None:
            for col in ("sport", "dport", "dns_id", "dns_qr", "dns_tc"):
                df.at[i, col] = getattr(f, col)


def _annotate_tcp(df: pd.DataFrame) -> None:
    if "raw" not in df:
        return
    tcp = df[df.proto == "TCP"]
    for (src, sport, dst, dport), seg in tcp.groupby(["src", "sport", "dst", "dport"]):
        syn = seg[seg.tcp_flags.str.contains("SYN")]
        base = int(syn.seq.iloc[0]) + 1 if len(syn) else int(seg.seq.min())
        stream, spans = bytearray(), []
        for i, r in seg[seg.payload > 0].iterrows():
            rel = (int(r.seq) - base) % 2**32
            if rel == len(stream):
                stream += r["raw"]
            elif rel + r.payload > len(stream) and rel < len(stream):
                stream += r["raw"][len(stream) - rel:]
            spans.append((i, rel, rel + r.payload))
        msgs, pos = [], 0
        while pos + 2 <= len(stream):
            (ln,) = struct.unpack(">H", stream[pos:pos + 2])
            msgs.append((pos, pos + 2 + ln, ln, _dns_head(bytes(stream[pos + 2:pos + 6]))))
            pos += 2 + ln
        for i, lo, hi in spans:
            for m_lo, m_hi, ln, head in msgs:
                if lo < m_hi and hi > m_lo:
                    df.loc[i, list(head)] = list(head.values())
                    df.at[i, "dns_bytes"] = ln
                    parts = [s for s in spans if s[1] < m_hi and s[2] > m_lo]
                    df.at[i, "detail"] = f"part {parts.index((i, lo, hi)) + 1}/{len(parts)}" if len(parts) > 1 else ""
                    break


def _describe(df: pd.DataFrame) -> None:
    def text(r) -> str:
        kind = "" if pd.isna(r.dns_qr) else ("response" if r.dns_qr else "query")
        if r.proto == "IP fragment":
            return f"IP fragment @ {r.frag_offset} of UDP DNS {kind}".rstrip()
        if r.proto == "UDP":
            s = f"DNS {kind} {int(r.dns_bytes):,} B" + (" TC=1 (truncated)" if pd.notna(r.dns_tc) and r.dns_tc else "")
            return s + (" · first IP fragment" if r.more_frags else "")
        if r.proto == "TCP":
            if r.payload == 0:
                return r.tcp_flags
            s = f"DNS {kind} {int(r.dns_bytes):,} B" if not pd.isna(r.dns_bytes) else "data"
            return f"{s} {r.detail}".strip()
        return r.proto
    df["detail"] = df.apply(text, axis=1)


def conversation(df: pd.DataFrame, qid: int, t0: float | None = None, t1: float | None = None,
                 margin: float = 0.25) -> pd.DataFrame:
    """Every packet of one lookup: UDP datagrams and fragments with this DNS id, and whole TCP connections
    that carried it. Without a time window, the most recent lookup with this id is taken."""
    if df.empty:
        return df
    hit = df[df.dns_id == qid]
    if hit.empty:
        return hit
    if t0 is None:
        t1 = float(hit.ts.max())
        t0 = t1 - 30
    hit = hit[(hit.ts >= t0 - margin) & (hit.ts <= t1 + margin)]
    keep = df.index.isin(hit.index)
    tcp = hit[hit.proto == "TCP"]
    for r in tcp.itertuples():
        same = (((df.src == r.src) & (df.sport == r.sport) & (df.dst == r.dst) & (df.dport == r.dport)) |
                ((df.src == r.dst) & (df.sport == r.dport) & (df.dst == r.src) & (df.dport == r.sport)))
        keep |= same & (df.ts >= t0 - margin) & (df.ts <= t1 + margin) & (df.proto == "TCP")
    return df[keep].sort_values("ts")


def payload_bytes(df: pd.DataFrame) -> dict:
    """DNS bytes seen on the wire: UDP payload (counted once per datagram) and TCP payload."""
    udp = df[df.proto == "UDP"].payload.sum()
    tcp = df[df.proto == "TCP"].payload.sum()
    return {"packets": len(df), "udp_bytes": int(udp), "tcp_bytes": int(tcp), "fragments": int((df.proto == "IP fragment").sum())}
