"""Full DNS packet decode for live-dashboard pcaps (convincing panel view)."""

from __future__ import annotations

import socket
import struct
import sys
from pathlib import Path

import dns.flags
import dns.message
import dns.rdatatype
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cipherops.pcap import _records  # noqa: E402

NAMES = {
    "10.53.0.100": "client",
    "10.53.0.53": "unbound",
    "10.53.0.2": "root",
}
MTL_FULL = 65050

PHASE_LABELS = {
    "client_query": "Client asks Unbound",
    "referral_root": "Root referral (NS/DS)",
    "referral_tld": "TLD referral (NS/DS)",
    "auth_answer": "Authoritative answer",
    "dnskey_fetch": "DNSKEY / trust material",
    "mtl_ladder_tcp": "MTL full ladder (TCP)",
    "client_answer": "Unbound answers client",
    "tcp_noise": "TCP handshake / ACK",
    "other": "Other",
}


def _name(ip: str) -> str:
    if ip in NAMES:
        return NAMES[ip]
    if ip.startswith("10.53.0."):
        last = int(ip.rsplit(".", 1)[1])
        if 11 <= last < 20:
            return f"tld-{last}"
        if 21 <= last < 50:
            return f"sld-{last}"
    return ip


def _role(label: str) -> str:
    if label == "client":
        return "client"
    if label == "unbound":
        return "unbound"
    if label == "root":
        return "root"
    if label.startswith("tld"):
        return "tld"
    if label.startswith("sld"):
        return "sld"
    return "other"


def _describe(wire: bytes) -> dict:
    try:
        m = dns.message.from_wire(wire, ignore_trailing=True)
    except Exception as e:  # noqa: BLE001
        return {"summary": f"(undecodable: {e})", "qname": "", "qtype": "", "rcode": "",
                "flags": "", "ad": False, "aa": False, "tc": False, "mtl_full": False,
                "rrsigs": "", "an": 0, "ns": 0, "is_resp": False}
    q = m.question[0] if m.question else None
    opts = [o.otype for o in (m.options or [])]
    sigs = []
    for sec in (m.answer, m.authority):
        for rrset in sec:
            if rrset.rdtype != dns.rdatatype.RRSIG:
                continue
            for rd in rrset:
                kind = ""
                if rd.algorithm == 130 and rd.signature:
                    kind = {0: "condensed", 1: "FULL"}.get(rd.signature[0], "?")
                sigs.append(
                    f"{dns.rdatatype.to_text(rd.type_covered)} alg{rd.algorithm} "
                    f"{len(rd.signature)}B{('/' + kind) if kind else ''}"
                )
    is_resp = bool(m.flags & dns.flags.QR)
    summary = ("RESP" if is_resp else "QUERY")
    if q is not None:
        summary += f" {q.name} {dns.rdatatype.to_text(q.rdtype)}"
    if is_resp:
        summary += f" {dns.rcode.to_text(m.rcode())}"
    if MTL_FULL in opts:
        summary += " [MTL-FULL]"
    if sigs:
        summary += " | " + "; ".join(sigs[:4])
    return {
        "summary": summary,
        "qname": q.name.to_text() if q is not None else "",
        "qtype": dns.rdatatype.to_text(q.rdtype) if q is not None else "",
        "rcode": dns.rcode.to_text(m.rcode()) if is_resp else "",
        "flags": dns.flags.to_text(m.flags),
        "ad": bool(m.flags & dns.flags.AD),
        "aa": bool(m.flags & dns.flags.AA),
        "tc": bool(m.flags & dns.flags.TC),
        "mtl_full": MTL_FULL in opts,
        "rrsigs": "; ".join(sigs),
        "an": sum(len(r) for r in m.answer),
        "ns": sum(len(r) for r in m.authority),
        "is_resp": is_resp,
    }


def _phase_for_row(hop: str, info: dict, proto: str) -> str:
    summary = str(info.get("summary") or "")
    if proto == "TCP" and summary.startswith("TCP[") and "QUERY" not in summary and "RESP" not in summary:
        return "tcp_noise"
    if "(segment of" in summary and "RESP" not in summary and "QUERY" not in summary:
        return "tcp_noise"

    parts = hop.split(" → ")
    if len(parts) != 2:
        return "other"
    src, dst = _role(parts[0]), _role(parts[1])
    qtype = info.get("qtype") or ""
    is_resp = bool(info.get("is_resp"))
    aa = bool(info.get("aa"))
    mtl_full = bool(info.get("mtl_full"))
    rrsigs = str(info.get("rrsigs") or "")
    has_full = "FULL" in rrsigs

    if src == "client" and dst == "unbound" and not is_resp:
        return "client_query"
    if src == "unbound" and dst == "client" and is_resp:
        return "client_answer"

    if qtype == "DNSKEY" or mtl_full or has_full:
        if proto == "TCP" or has_full or mtl_full:
            return "mtl_ladder_tcp" if (mtl_full or has_full or proto == "TCP") else "dnskey_fetch"
        return "dnskey_fetch"

    if not is_resp:
        if dst == "root":
            return "referral_root"
        if dst == "tld":
            return "referral_tld"
        if dst == "sld":
            return "auth_answer"
        return "other"

    # responses
    if src == "root":
        return "referral_root"
    if src == "tld" and not aa:
        return "referral_tld"
    if src == "tld" and aa and qtype == "DNSKEY":
        return "dnskey_fetch"
    if src == "tld":
        return "referral_tld"
    if src == "sld" and aa:
        return "auth_answer" if qtype != "DNSKEY" else "dnskey_fetch"
    if src == "sld":
        return "auth_answer"
    return "other"


def decode_pcap(path: Path | str) -> pd.DataFrame:
    path = Path(path)
    if not path.exists() or path.stat().st_size < 24:
        return pd.DataFrame()
    recs = list(_records(path.read_bytes()))
    if not recs:
        return pd.DataFrame()
    t0 = recs[0][0]
    streams: dict = {}
    rows = []
    for no, (ts, ip) in enumerate(recs, 1):
        if len(ip) < 20 or ip[0] >> 4 != 4:
            continue
        ihl = (ip[0] & 0x0F) * 4
        total = struct.unpack(">H", ip[2:4])[0]
        body = ip[ihl:total]
        src, dst = socket.inet_ntoa(ip[12:16]), socket.inet_ntoa(ip[16:20])
        hop = f"{_name(src)} → {_name(dst)}"
        ms = round((ts - t0) * 1000, 2)
        if ip[9] == 17 and len(body) >= 8:
            ulen = struct.unpack(">H", body[4:6])[0]
            dns_len = max(ulen - 8, 0)
            info = _describe(body[8:8 + dns_len]) if dns_len >= 4 else {
                "summary": "(short)", "qname": "", "qtype": "", "rcode": "", "flags": "",
                "ad": False, "aa": False, "tc": False, "mtl_full": False, "rrsigs": "",
                "an": 0, "ns": 0, "is_resp": False,
            }
            phase = _phase_for_row(hop, info, "UDP")
            rows.append({
                "#": no, "ms": ms, "hop": hop, "proto": "UDP", "bytes": dns_len,
                "phase": phase, "phase_label": PHASE_LABELS.get(phase, phase), **info,
            })
        elif ip[9] == 6 and len(body) >= 20:
            sport, dport, seq = struct.unpack(">HHI", body[:8])
            off = (body[12] >> 4) * 4
            flags = "".join(n for b, n in ((2, "S"), (16, "."), (8, "P"), (1, "F"), (4, "R")) if body[13] & b)
            data = body[off:]
            key = (src, sport, dst, dport)
            st = streams.setdefault(key, {"buf": b""})
            summary = f"TCP[{flags}]"
            info = {"summary": summary, "qname": "", "qtype": "", "rcode": "", "flags": "",
                    "ad": False, "aa": False, "tc": False, "mtl_full": False, "rrsigs": "",
                    "an": 0, "ns": 0, "is_resp": False}
            if data:
                st["buf"] += data
                parts = []
                while len(st["buf"]) >= 2:
                    ln = struct.unpack(">H", st["buf"][:2])[0]
                    if len(st["buf"]) < 2 + ln:
                        parts.append(f"(segment of {ln} B DNS)")
                        break
                    info = _describe(st["buf"][2:2 + ln])
                    parts.append(f"len={ln} {info['summary']}")
                    st["buf"] = st["buf"][2 + ln:]
                info = {**info, "summary": " | ".join(parts) if parts else summary}
            phase = _phase_for_row(hop, info, "TCP")
            rows.append({
                "#": no, "ms": ms, "hop": hop, "proto": "TCP", "bytes": len(data),
                "phase": phase, "phase_label": PHASE_LABELS.get(phase, phase), **info,
            })
    return pd.DataFrame(rows)


def wire_summary(df: pd.DataFrame) -> dict:
    """Client↔unbound vs upstream byte totals and MTL FULL count."""
    if df is None or df.empty:
        return {"client_bytes": 0, "upstream_bytes": 0, "packets": 0, "mtl_full_packets": 0}
    client_mask = df["hop"].astype(str).str.contains(r"client|unbound", regex=True) & (
        df["hop"].astype(str).str.contains("client")
    )
    # hops that include "client"
    is_client = df["hop"].astype(str).str.contains("client")
    client_bytes = int(df.loc[is_client, "bytes"].sum())
    upstream_bytes = int(df.loc[~is_client, "bytes"].sum())
    mtl_full = int(
        ((df["mtl_full"] == True) | df["rrsigs"].astype(str).str.contains("FULL", na=False)).sum()  # noqa: E712
    )
    return {
        "client_bytes": client_bytes,
        "upstream_bytes": upstream_bytes,
        "packets": len(df),
        "mtl_full_packets": mtl_full,
    }


_SEQ_ACTORS = ("Client", "Unbound", "Root", "TLD", "SLD")


def _hop_actor(label: str) -> str | None:
    lab = str(label)
    if lab == "client":
        return "Client"
    if lab == "unbound":
        return "Unbound"
    if lab == "root":
        return "Root"
    if lab.startswith("tld"):
        return "TLD"
    if lab.startswith("sld"):
        return "SLD"
    return None


def _seq_label(row) -> str:
    qname = str(row.get("qname") or "")
    qtype = str(row.get("qtype") or "")
    phase = str(row.get("phase") or "")
    if qname.lower() in {"nan", "none"}:
        qname = ""
    if qtype.lower() in {"nan", "none"}:
        qtype = ""
    disp = qname.rstrip(".") if qname and qname != "." else ("root" if qname == "." else "")
    label = f"{qtype} {disp}".strip() if (qtype or disp) else (phase or "msg")
    if bool(row.get("is_resp")):
        note = "OK"
        if row.get("ad"):
            note = "AD"
        elif phase.startswith("referral"):
            note = "referral"
        elif "FULL" in str(row.get("rrsigs") or "") or row.get("mtl_full"):
            note = "MTL-FULL"
        label = f"{label} [{note}]".strip()
    return " ".join(str(label).replace("\n", " ").split())[:56] or "msg"


def reference_sequence_steps() -> list[dict]:
    """Teaching sequence for a typical Through-Unbound path."""
    return [
        {"src": "Client", "dst": "Unbound", "label": "Query name A", "response": False},
        {"src": "Unbound", "dst": "Root", "label": "Ask for TLD", "response": False},
        {"src": "Root", "dst": "Unbound", "label": "Referral NS/DS", "response": True},
        {"src": "Unbound", "dst": "TLD", "label": "Ask for SLD", "response": False},
        {"src": "TLD", "dst": "Unbound", "label": "Referral NS/DS", "response": True},
        {"src": "Unbound", "dst": "SLD", "label": "Ask for name A", "response": False},
        {"src": "SLD", "dst": "Unbound", "label": "Answer + RRSIGs", "response": True},
        {"src": "Unbound", "dst": "TLD", "label": "DNSKEY / MTL ladder", "response": False},
        {"src": "TLD", "dst": "Unbound", "label": "Keys / FULL ladder", "response": True},
        {"src": "Unbound", "dst": "Client", "label": "Validated A AD=1", "response": True},
    ]


def build_sequence_steps(df: pd.DataFrame) -> list[dict]:
    """Deduped Client/Unbound/Root/TLD/SLD message steps from a capture decode."""
    if df is None or df.empty:
        return reference_sequence_steps()
    interesting = df[df["phase"] != "tcp_noise"].copy() if "phase" in df.columns else df
    if interesting.empty:
        interesting = df

    steps: list[dict] = []
    seen: set[tuple] = set()
    for _, r in interesting.iterrows():
        hop = str(r.get("hop") or "")
        if "→" not in hop and "->" not in hop:
            continue
        sep = "→" if "→" in hop else "->"
        src_l, dst_l = [x.strip() for x in hop.split(sep, 1)]
        src, dst = _hop_actor(src_l), _hop_actor(dst_l)
        if not src or not dst:
            continue
        is_resp = bool(r.get("is_resp"))
        label = _seq_label(r)
        key = (src, dst, label, is_resp)
        if key in seen:
            continue
        seen.add(key)
        steps.append({"src": src, "dst": dst, "label": label, "response": is_resp})
        if len(steps) >= 24:
            break
    return steps or reference_sequence_steps()


def sequence_png_bytes(steps: list[dict] | None = None) -> bytes:
    """Render a high-contrast sequence diagram (white bg, dark ink) via matplotlib."""
    import io

    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    steps = steps or reference_sequence_steps()
    actors = list(_SEQ_ACTORS)
    xpos = {a: i for i, a in enumerate(actors)}
    n = max(len(steps), 1)
    fig_h = max(3.5, 1.2 + 0.55 * n)
    fig, ax = plt.subplots(figsize=(10.5, fig_h), dpi=140)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")
    ax.set_xlim(-0.35, len(actors) - 0.65)
    ax.set_ylim(n + 0.85, -0.55)
    ax.axis("off")

    # Lifelines + participant boxes
    for a, x in xpos.items():
        ax.plot([x, x], [-0.15, n + 0.35], color="#444444", lw=1.2, zorder=1)
        for y in (-0.35, n + 0.55):
            box = FancyBboxPatch(
                (x - 0.38, y - 0.18),
                0.76,
                0.36,
                boxstyle="round,pad=0.02,rounding_size=0.05",
                linewidth=1.2,
                edgecolor="#111111",
                facecolor="#eef3ff",
                zorder=3,
            )
            ax.add_patch(box)
            ax.text(x, y, a, ha="center", va="center", fontsize=10, fontweight="bold",
                    color="#111111", zorder=4)

    for i, step in enumerate(steps):
        y = i + 0.35
        x0, x1 = xpos[step["src"]], xpos[step["dst"]]
        style = "arc3,rad=0"
        color = "#111111"
        ls = "--" if step.get("response") else "-"
        arrow = FancyArrowPatch(
            (x0, y),
            (x1, y),
            arrowstyle="-|>",
            mutation_scale=12,
            linewidth=1.4,
            linestyle=ls,
            color=color,
            connectionstyle=style,
            zorder=2,
        )
        ax.add_patch(arrow)
        ax.text(
            (x0 + x1) / 2,
            y - 0.18,
            step["label"],
            ha="center",
            va="bottom",
            fontsize=8.5,
            color="#111111",
            bbox=dict(boxstyle="round,pad=0.15", facecolor="#ffffff", edgecolor="#cccccc", linewidth=0.6),
            zorder=5,
        )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, facecolor="#ffffff", bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return buf.getvalue()


def build_story(df: pd.DataFrame) -> list[str]:
    """Numbered plain-English steps from phase-tagged packets (DNS only)."""
    if df is None or df.empty:
        return []
    interesting = df[df["phase"] != "tcp_noise"].copy()
    if interesting.empty:
        interesting = df
    steps: list[str] = []
    seen: set[tuple] = set()

    for _, r in interesting.iterrows():
        phase = r.get("phase") or "other"
        qname = r.get("qname") or ""
        qtype = r.get("qtype") or ""
        hop = r.get("hop") or ""
        is_resp = bool(r.get("is_resp"))
        key = (phase, hop, qname, qtype, is_resp, bool(r.get("mtl_full")), "FULL" in str(r.get("rrsigs") or ""))
        # allow a few repeats for distinct hops but collapse identical
        if key in seen:
            continue
        seen.add(key)

        if phase == "client_query":
            steps.append(
                f"Client asks Unbound for `{qname} {qtype}`. Unbound does not know the answer yet."
            )
        elif phase == "referral_root" and not is_resp:
            steps.append(
                f"Unbound asks the **root** for `{qname} {qtype}` "
                f"(often just the TLD label — root does not store the full hostname)."
            )
        elif phase == "referral_root" and is_resp:
            steps.append(
                "Root answers with a **referral**: NS/DS for the next zone (not the final A record). "
                "That DS starts the DNSSEC chain toward the TLD."
            )
        elif phase == "referral_tld" and not is_resp:
            steps.append(
                f"Unbound asks the **TLD** for `{qname} {qtype}` — walking one label deeper."
            )
        elif phase == "referral_tld" and is_resp:
            steps.append(
                "TLD returns another **referral** (NS/DS) toward the second-level zone."
            )
        elif phase == "auth_answer" and not is_resp:
            steps.append(
                f"Unbound asks the **SLD** authoritative server for `{qname} {qtype}`."
            )
        elif phase == "auth_answer" and is_resp:
            aa = " (AA=authoritative)" if r.get("aa") else ""
            steps.append(
                f"SLD answers `{qname} {qtype}`{aa}. "
                + ("RRSIGs present for Unbound to check." if r.get("rrsigs") else "")
            )
        elif phase == "dnskey_fetch" and not is_resp:
            steps.append(
                f"Unbound fetches `{qname} {qtype}` to obtain keys for validation."
            )
        elif phase == "dnskey_fetch" and is_resp:
            steps.append(
                f"DNSKEY answer for `{qname}` arrives"
                + (" with condensed MTL RRSIG." if "condensed" in str(r.get("rrsigs")) else ".")
            )
        elif phase == "mtl_ladder_tcp" and not is_resp:
            steps.append(
                f"Unbound requests **MTL-FULL** for `{qname}` "
                f"(ladder too large for UDP → TCP)."
            )
        elif phase == "mtl_ladder_tcp" and is_resp:
            steps.append(
                f"Full MTL ladder received for `{qname}` "
                f"(~{int(r.get('bytes') or 0):,} B on this segment)."
            )
        elif phase == "client_answer":
            ad = "AD=1 (validated)" if r.get("ad") else "AD=0"
            steps.append(
                f"Unbound returns `{qname} {qtype}` to the client — {ad}, "
                f"{int(r.get('bytes') or 0)} B on the wire to the stub."
            )
        else:
            if qname:
                steps.append(f"{hop}: {r.get('summary')}")

    # number them
    return [f"{i}. {s}" for i, s in enumerate(steps, 1)]
