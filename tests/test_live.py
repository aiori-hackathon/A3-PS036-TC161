import os
import shutil
import socket
import struct

import pytest

from cipherops import dnswire, pcap
from cipherops.codepoints import MODES
from cipherops.workload import build_zone

CLIENT, SERVER = "127.0.0.1", "127.0.0.2"


def _ip(src, dst, proto, body, ip_id=1, frag=0):
    hdr = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(body), ip_id, frag, 64, proto, 0,
                      socket.inet_aton(src), socket.inet_aton(dst))
    return b"\x00" * 12 + b"\x08\x00" + hdr + body


def _udp(src, sport, dst, dport, payload, ip_id=1, frag=0, total=None):
    body = struct.pack(">HHHH", sport, dport, 8 + (total or len(payload)), 0) + payload
    return _ip(src, dst, 17, body, ip_id, frag)


def _tcp(src, sport, dst, dport, seq, flags, payload=b""):
    body = struct.pack(">HHIIBBHHH", sport, dport, seq, 0, 5 << 4, flags, 65535, 0, 0) + payload
    return _ip(src, dst, 6, body)


def _dns(qid, response, tc=False, size=12):
    flags = (0x8000 if response else 0) | (0x0200 if tc else 0)
    return struct.pack(">HH", qid, flags) + b"\x00" * (size - 4)


def _write(path, frames):
    out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    for i, f in enumerate(frames):
        out += struct.pack("<IIII", 1000, i * 1000, len(f), len(f)) + f
    path.write_bytes(out)


def test_pcap_follows_udp_truncation_tcp_retry_and_fragments(tmp_path):
    q = _dns(0x1234, False, size=40)
    tc = _dns(0x1234, True, tc=True, size=60)
    resp = _dns(0x1234, True, size=3000)
    stream = struct.pack(">H", len(resp)) + resp
    big = _dns(0x7777, True, size=2000)
    frames = [
        _udp(CLIENT, 4000, SERVER, 53, q),
        _udp(SERVER, 53, CLIENT, 4000, tc),
        _tcp(CLIENT, 4001, SERVER, 53, 100, 0x02),
        _tcp(SERVER, 53, CLIENT, 4001, 500, 0x12),
        _tcp(CLIENT, 4001, SERVER, 53, 101, 0x18, struct.pack(">H", len(q)) + q),
        _tcp(SERVER, 53, CLIENT, 4001, 501, 0x18, stream[:1448]),
        _tcp(SERVER, 53, CLIENT, 4001, 501 + 1448, 0x18, stream[1448:]),
        _tcp(CLIENT, 4001, SERVER, 53, 101 + 2 + len(q), 0x11),
        _udp(SERVER, 53, CLIENT, 4002, big[:1472], ip_id=9, frag=0x2000, total=len(big)),
        _ip(SERVER, CLIENT, 17, big[1472:], ip_id=9, frag=(8 + 1472) // 8),
    ]
    path = tmp_path / "t.pcap"
    _write(path, frames)
    df = pcap.read(path)
    assert len(df) == 10
    look = pcap.conversation(df, 0x1234)
    assert list(look.no) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert "TC=1" in look.iloc[1].detail
    seg = look[look.payload > 0].iloc[-2:]
    assert seg.dns_bytes.tolist() == [3000, 3000] and seg.detail.str.contains("part").all()
    frag = pcap.conversation(df, 0x7777)
    assert frag.proto.tolist() == ["UDP", "IP fragment"]
    assert frag.iloc[1].dport == 4002
    b = pcap.payload_bytes(df)
    assert b["udp_bytes"] == len(q) + len(tc) + len(big) and b["tcp_bytes"] == 2 + len(q) + len(stream)


@pytest.mark.harness
@pytest.mark.skipif(shutil.which("nsd") is None, reason="nsd not installed (run inside Docker)")
def test_live_exchange_against_nsd_matches_capture(tmp_path):
    from cipherops import live
    from cipherops.crosscheck import NSD, signed_zone_text

    zone = build_zone("lv", 15, 3)
    sigs = dnswire.Signatures(MODES["slh-dsa"], [os.urandom(2000) for _ in zone.items])
    b = dnswire.ResponseBuilder(zone, sigs)
    it = next(i for i in zone.items if i.record_class == "small")
    with NSD(signed_zone_text(zone, sigs), zone.origin.to_text()), live.Capture(tmp_path / "x.pcap", quiet=True):
        trace = []
        res = live.exchange(b.query(it.owner, it.rdtype, 1232, False).to_wire(), trace=trace)
        big = live.exchange(b.query(it.owner, it.rdtype, 4096, False).to_wire())
    assert res["transport"] == "udp+tcp" and not res["failed"]
    assert [s["event"] for s in trace] == ["send", "recv", "connect", "send", "recv"]
    assert big["transport"] == "udp" and big["response_bytes"] > 2000
    if shutil.which("tcpdump"):
        df = pcap.read(tmp_path / "x.pcap")
        conv = pcap.conversation(df, res["qid"], res["t_start"], res["t_end"])
        sent = conv[conv.proto.isin(["UDP", "TCP"])].payload.sum()
        assert sent == res["dns_bytes"]
