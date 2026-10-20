"""Cipherops dashboard: PS-036 DNSSEC signing benchmark, with and without MTL.

    streamlit run dashboard/app.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import dns.message
import dns.rdatatype
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cipherops import dnswire, live, pcap, results  # noqa: E402
from cipherops.codepoints import MODES  # noqa: E402
from cipherops.harness import load_signatures  # noqa: E402
from cipherops.workload import build_zone  # noqa: E402

st.set_page_config(page_title="Cipherops · MTL DNSSEC benchmark", layout="wide")

COLORS = {"rsa-2048": "#9aa5b1", "ecdsa-p256": "#6b7c8f", "ed25519": "#3e4c59",
          "slh-dsa": "#d64545", "slh-dsa-mtl": "#2f9e44"}
PSEUDOCODE = """\
# Sign a zone once with MTL mode (reference library), serve condensed RRSIGs
key = mtllib_key_new("SLH-DSA-SHA2-128s-MTL-SHA2-128")
for rrset in canonical_order(zone):
    data   = rrsig_rdata_without_sig(rrset) || canonical(rrset)   # RFC 4034 3.1.8.1
    leaf   = mtllib_sign_append(key, data)                        # hash only, no SLH-DSA
ladder = mtllib_sign_get_signed_ladder(key)                       # ONE SLH-DSA signature
for rrset, leaf in signed:
    rrsig.signature = 0x00 || mtllib_sign_get_condensed_sig(key, leaf)   # ~100-250 B

# Validator
if ladder_cache.empty():                       # first contact only
    full = query(qname, qtype, edns_option=MTL_MODE_FULL, transport=TCP)
    ladder_cache = mtllib_verify_signed_ladder(pubkey, full.ladder)   # one SLH-DSA verify
ok = mtllib_verify(pubkey, data, condensed_sig, ladder_cache)         # hashes only
"""


def mode_label(mk: str) -> str:
    return MODES[mk].label


@st.cache_data(show_spinner=False)
def load_run(path: str) -> results.Run:
    return results.load(Path(path))


@st.cache_resource(show_spinner=False)
def demo_zone(path: str, dataset: str):
    run = results.load(Path(path))
    idx, ds = next((i, d) for i, d in enumerate(run.config["datasets"]) if d["name"] == dataset)
    return build_zone(ds["name"], ds["n_names"], run.config["seed"] + idx)


@st.cache_resource(show_spinner=False)
def demo_sigs(path: str, dataset: str, mk: str):
    run = results.load(Path(path))
    limited = bool((run.config.get("direct_sign_limit") or {}).get(dataset)) and MODES[mk].family == "pqc-direct"
    return load_signatures(MODES[mk], Path(path) / "artifacts" / dataset / mk, limited)


@st.cache_data(show_spinner=False)
def load_live(path: str, mtime: float) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    root = Path(path)
    meta = root / "live.json"
    return (pd.read_csv(root / "live_summary.csv"), pd.read_csv(root / "live.csv"),
            json.loads(meta.read_text()) if meta.exists() else {})


@st.cache_data(show_spinner=False, max_entries=8)
def load_pcap(path: str, mtime: float, size: int) -> pd.DataFrame:
    return pcap.read(path)


PROTO_COLORS = {"UDP": "#1971c2", "TCP": "#e8590c", "IP fragment": "#9c36b5"}


def sequence_figure(arrows: list[dict], lanes: list[str], by_time: bool) -> go.Figure:
    """Sequence diagram: one arrow per packet or step, top to bottom."""
    fig = go.Figure()
    lanes = list(dict.fromkeys(lanes + [a[k] for a in arrows for k in ("src", "dst")]))
    xs = {lane: i for i, lane in enumerate(lanes)}
    ys = [a["t"] for a in arrows] if by_time else list(range(len(arrows)))
    bottom = (max(ys) if ys else 1) * 1.05 + (0 if by_time else 1)
    for lane, x in xs.items():
        fig.add_shape(type="line", x0=x, x1=x, y0=-0.02 * bottom, y1=bottom, line=dict(color="#adb5bd", width=2))
        fig.add_annotation(x=x, y=-0.04 * bottom, text=f"<b>{lane}</b>", showarrow=False, yanchor="bottom")
    for a, y in zip(arrows, ys):
        x0, x1 = xs[a["src"]], xs[a["dst"]]
        color = a.get("color", "#495057")
        if x0 == x1:
            fig.add_annotation(x=x0, y=y, text=a["label"], showarrow=False, xanchor="left", xshift=8,
                               font=dict(size=11, color=color), bgcolor="rgba(255,255,255,0.85)")
            continue
        fig.add_annotation(x=x1, y=y, ax=x0, ay=y, xref="x", yref="y", axref="x", ayref="y", text="",
                           showarrow=True, arrowhead=2, arrowsize=1.2, arrowwidth=2, arrowcolor=color)
        fig.add_annotation(x=(x0 + x1) / 2, y=y, text=a["label"], showarrow=False, yshift=9,
                           font=dict(size=11, color=color), bgcolor="rgba(255,255,255,0.85)")
        fig.add_annotation(x=min(x0, x1), y=y, text=f"{a['t']:.2f} ms", showarrow=False, xanchor="right",
                           xshift=-6, font=dict(size=9, color="#868e96"))
    fig.update_xaxes(visible=False, range=[-0.6, len(lanes) - 0.4])
    fig.update_yaxes(autorange="reversed", visible=by_time, title="ms since the query" if by_time else None)
    fig.update_layout(height=max(260, 34 * len(arrows) + 120), margin=dict(l=10, r=10, t=10, b=10),
                      plot_bgcolor="white", showlegend=False)
    return fig


def packet_arrows(packets: pd.DataFrame, lane_of) -> list[dict]:
    t0 = packets.ts.min()
    out = []
    for r in packets.itertuples():
        size = f" · {int(r.ip_bytes):,} B IP" if r.proto != "TCP" or r.payload else ""
        out.append({"t": (r.ts - t0) * 1e3, "src": lane_of(r.src, r.sport), "dst": lane_of(r.dst, r.dport),
                    "label": (r.detail if r.proto == "IP fragment" else f"{r.proto}: {r.detail}") + size,
                    "color": PROTO_COLORS.get(r.proto, "#495057") if r.payload or r.proto != "TCP" else "#868e96"})
    return out


def packet_table(packets: pd.DataFrame) -> pd.DataFrame:
    t0 = packets.ts.min()
    out = packets.assign(ms=((packets.ts - t0) * 1e3).round(3),
                         **{"from": packets.src + ":" + packets.sport.map(lambda p: "" if pd.isna(p) else str(int(p))),
                            "to": packets.dst + ":" + packets.dport.map(lambda p: "" if pd.isna(p) else str(int(p)))})
    return out[["no", "ms", "from", "to", "proto", "tcp_flags", "ip_bytes", "payload", "detail"]]


def trace_arrows(trace: list[dict], server: str) -> list[dict]:
    out = []
    for s in trace:
        color = PROTO_COLORS.get(s["transport"], "#495057")
        label = f"{s['transport']} {s['info']}" + (f" · {s['bytes']:,} B" if s["bytes"] else "")
        if s["event"] == "send":
            out.append({"t": s["ms"], "src": "Resolver (this PC)", "dst": server, "label": label, "color": color})
        elif s["event"] == "recv":
            out.append({"t": s["ms"], "src": server, "dst": "Resolver (this PC)", "label": label, "color": color})
        elif s["event"] == "connect":
            out.append({"t": s["ms"], "src": "Resolver (this PC)", "dst": server,
                        "label": "TCP connect: SYN, SYN-ACK, ACK", "color": "#868e96"})
        else:
            out.append({"t": s["ms"], "src": "Resolver (this PC)", "dst": "Resolver (this PC)",
                        "label": f"✗ {s['info']}", "color": "#c92a2a"})
    return out


runs = results.list_runs(ROOT / "results")
st.sidebar.title("Cipherops")
st.sidebar.caption("PS-036 · PQC DNSSEC MTL benchmark using the Verisign MTL reference implementation")
if not runs:
    st.warning("No completed runs under results/runs. Run `scripts/run_benchmark.ps1` first.")
    st.stop()
run_path = st.sidebar.selectbox("Run", runs, format_func=lambda p: p.name)
run = load_run(str(run_path))
F = run.frames
datasets = run.dataset_order
modes = run.mode_order
verdict = run.preflight.get("verdict", "missing")
st.sidebar.markdown(f"**Go/no-go gate:** {'🟢' if verdict == 'GO' else '🔴'} {verdict}")
st.sidebar.markdown(f"**Trials:** {run.config['trials']} · **Seed:** {run.config['seed']}")
hw = run.environment.get("hardware", {})
st.sidebar.markdown(f"**CPU:** {hw.get('cpu_model', '?')}  \n**Pinned to:** {hw.get('cpu_affinity', '?')}")
sw = run.environment.get("software", {})
st.sidebar.markdown(f"**MTL ref:** `{str(sw.get('mtl_reference_commit', '?'))[:10]}` · "
                    f"**liboqs:** {sw.get('liboqs_tag', '?')} · **OpenSSL:** {sw.get('openssl_tag', '?')}")

timing = results.timing_table(F["trials"])
fit = results.fit_table(F["responses"])

st.title("DNSSEC with post-quantum signatures: direct SLH-DSA vs MTL mode")
largest = datasets[-1]
t_large = timing[timing.dataset == largest].set_index("mode")
fit_large = fit[fit.dataset == largest]

c1, c2, c3, c4 = st.columns(4)
if {"slh-dsa", "slh-dsa-mtl"} <= set(modes):
    d = t_large.loc["slh-dsa"]
    m = t_large.loc["slh-dsa-mtl"]
    d_sign = d.get("sign_total_extrapolated_ns") if d.limited else d.sign_total_ns
    c1.metric(f"Zone signing ({largest}, {int(m.dataset_rrsets):,} RRsets)", results.fmt_seconds(m.sign_total_ns),
              f"{d_sign / m.sign_total_ns:,.0f}× faster than direct SLH-DSA", delta_color="normal")
    nx = F["responses"]
    nx = nx[(nx.dataset == largest) & (nx.kind == "nxdomain") & (nx.variant == "default") & (nx.udp_limit == 1232)]
    nxm = nx.groupby("mode").response_bytes.median()
    c2.metric("Median NXDOMAIN response", results.fmt_bytes(nxm["slh-dsa-mtl"]),
              f"vs {results.fmt_bytes(nxm['slh-dsa'])} direct", delta_color="off")
    fl = fit_large[fit_large.record_class != "apex"].assign(fits=lambda d: d.fit_pct * d.responses)
    fl = fl.groupby("mode").fits.sum() / fl.groupby("mode").responses.sum()
    c3.metric("Responses fitting 1232-byte UDP", f"{fl['slh-dsa-mtl']:.0f}% with MTL",
              f"{fl['slh-dsa']:.0f}% direct SLH-DSA", delta_color="off")
    w = F["workload"]
    w = w[(w.dataset == largest) & (w.udp_limit == 1232)].set_index("mode")
    c4.metric("Workload bytes on the wire", results.fmt_bytes(w.loc["slh-dsa-mtl", "dns_bytes"]),
              f"{w.loc['slh-dsa', 'dns_bytes'] / w.loc['slh-dsa-mtl', 'dns_bytes']:.1f}× less than direct",
              delta_color="normal")
st.caption("All headline figures are directly measured on the pinned environment; estimates appear only in "
           "panels labelled *analytical*.")

tabs = st.tabs(["Signing & verification", "Transmitted bytes", "Cache usage", "MTL sizes vs draft",
                "Live demo", "Reproducibility"])

# ---------------------------------------------------------------- timing
with tabs[0]:
    metric = st.radio("Metric", ["sign_total_ns", "verify_warm_total_ns", "sign_per_rrset_ns",
                                 "verify_per_rrset_ns", "verify_full_median_ns"], horizontal=True, key="metric",
                      format_func={"sign_total_ns": "Sign whole zone", "verify_warm_total_ns": "Verify whole zone (warm)",
                                   "sign_per_rrset_ns": "Sign per RRset", "verify_per_rrset_ns": "Verify per RRset",
                                   "verify_full_median_ns": "Verify one signature, cold (MTL: full sig)"}.get)
    t = timing.copy()
    t["Mode"] = t["mode"].map(mode_label)
    value, lo, hi = t[metric], t[metric + "_lo"], t[metric + "_hi"]
    ex_col = metric.replace("_ns", "_extrapolated_ns")
    t["basis"] = "measured"
    if ex_col in t and t.limited.any():
        factor = t.dataset_rrsets / t.n_signed
        value = value.where(~t.limited, t[ex_col])
        lo = lo.where(~t.limited, lo * factor)
        hi = hi.where(~t.limited, hi * factor)
        t.loc[t.limited, "basis"] = "extrapolated from sample"
    t["seconds"] = value / 1e9
    t["err_plus"] = (hi - value) / 1e9
    t["err_minus"] = (value - lo) / 1e9
    fig = px.bar(t, x="dataset", y="seconds", color="mode", barmode="group", log_y=True, pattern_shape="basis",
                 pattern_shape_map={"measured": "", "extrapolated from sample": "/"},
                 category_orders={"dataset": datasets, "mode": modes}, color_discrete_map=COLORS,
                 error_y="err_plus", error_y_minus="err_minus",
                 hover_data={"n_signed": True, "trials": True, "basis": True}, labels={"seconds": "seconds (log)"})
    fig.for_each_trace(lambda tr: tr.update(name=mode_label(tr.name.split(",")[0]) +
                                            (" (extrapolated)" if "extrapolated" in tr.name else "")))
    st.plotly_chart(fig, width="stretch")
    st.caption("Bars: median across trials. Error bars: bootstrap 95% CI of the median. One process per trial, "
               "modes interleaved in seeded random order, CLOCK_MONOTONIC_RAW around each library call. "
               "Hatched bars are whole-zone totals extrapolated from a measured per-RRset sample.")
    if t.limited.any():
        st.info("Direct SLH-DSA was capped by `direct_sign_limit` on some datasets: zone totals for those cells "
                "are extrapolated from the measured per-RRset time (see table).")
    show = t[["dataset", "Mode", "trials", "n_signed", "dataset_rrsets", "checks_passed"]].copy()
    for col, name in (("sign_total_ns", "sign zone"), ("verify_warm_total_ns", "verify zone"),
                      ("sign_per_rrset_ns", "sign/RRset"), ("verify_per_rrset_ns", "verify/RRset"),
                      ("verify_full_median_ns", "cold verify/sig")):
        show[name] = t[col].map(results.fmt_seconds)
    if "sign_total_extrapolated_ns" in t:
        show["sign zone (extrapolated)"] = t["sign_total_extrapolated_ns"].map(results.fmt_seconds)
    st.dataframe(show, width="stretch", hide_index=True)

    mtl = F["trials"][F["trials"]["mode"] == "slh-dsa-mtl"]
    if len(mtl):
        st.subheader("Where MTL signing time goes")
        parts = mtl.groupby("dataset")[["sign_append_total_ns", "ladder_sign_ns", "condensed_extract_total_ns"]].median()
        parts = parts.reindex(datasets).reset_index().melt(id_vars="dataset", var_name="part", value_name="ns")
        parts["seconds"] = parts.ns / 1e9
        parts["part"] = parts.part.map({"sign_append_total_ns": "append RRsets to node set (hashing)",
                                        "ladder_sign_ns": "sign ladder (1 SLH-DSA signature)",
                                        "condensed_extract_total_ns": "extract condensed signatures"})
        st.plotly_chart(px.bar(parts, x="dataset", y="seconds", color="part", category_orders={"dataset": datasets}),
                        width="stretch")

# ---------------------------------------------------------------- bytes
with tabs[1]:
    limit = st.radio("EDNS UDP buffer", list(dnswire.UDP_LIMITS), horizontal=True)
    ds_sel = st.selectbox("Dataset", datasets, index=len(datasets) - 1, key="bytes_ds")
    r = F["responses"]
    r = r[(r.dataset == ds_sel) & (r.udp_limit == limit) & (r.variant == "default")].copy()
    r["Mode"] = r["mode"].map(mode_label)
    fig = px.box(r, x="record_class", y="response_bytes", color="mode", log_y=True, points=False,
                 category_orders={"record_class": ["apex", "small", "medium", "large", "nxdomain"], "mode": modes},
                 color_discrete_map=COLORS, labels={"response_bytes": "DNS response bytes (log)"})
    fig.for_each_trace(lambda tr: tr.update(name=mode_label(tr.name)))
    for y, txt in ((1232, "1232 B (DNS flag day)"), (4096, "4096 B")):
        fig.add_hline(y=y, line_dash="dot", annotation_text=txt)
    st.plotly_chart(fig, width="stretch")
    st.caption("Measured: real DNS messages encoded by dnspython (name compression on) carrying the real "
               "signatures produced by the reference library / liboqs / OpenSSL in trial 0.")
    f = results.fit_table(F["responses"], limit)
    f = f[f.dataset == ds_sel].copy()
    f["Mode"] = f["mode"].map(mode_label)
    piv = f.pivot_table(index="record_class", columns="Mode", values="fit_pct").round(1)
    st.markdown(f"**% of responses that fit a {limit}-byte UDP buffer (no TC, no TCP retry)**")
    st.dataframe(piv, width="stretch")

    w = F["workload"]
    w = w[(w.udp_limit == limit)].copy()
    w["Mode"] = w["mode"].map(mode_label)
    fig = px.bar(w, x="dataset", y="dns_bytes", color="mode", barmode="group", log_y=True,
                 category_orders={"dataset": datasets, "mode": modes}, color_discrete_map=COLORS,
                 hover_data=["transmitted_responses", "truncations", "tcp_exchanges", "mtl_full_fetches"],
                 labels={"dns_bytes": "total DNS bytes for the lookup workload (log)"})
    fig.for_each_trace(lambda tr: tr.update(name=mode_label(tr.name)))
    st.plotly_chart(fig, width="stretch")
    wl_cfg = run.config["wire"]["workload"]
    st.caption(f"Workload: {wl_cfg['n_queries']} Zipf(s={wl_cfg['zipf_s']}) lookups, {wl_cfg['nx_fraction']:.0%} "
               "NXDOMAIN, cached answers not re-sent; oversize answers cost UDP query + TC=1 reply + TCP retry; "
               "MTL fetches one full signature over TCP on first contact. Packet / round-trip counts are analytical.")
    st.dataframe(w[["dataset", "Mode", "transmitted_responses", "dns_bytes", "rrsig_signature_bytes", "truncations",
                    "tcp_exchanges", "mtl_full_fetches", "round_trips_est", "packets_est"]],
                 width="stretch", hide_index=True)

# ---------------------------------------------------------------- cache
with tabs[2]:
    c = F["cache"].copy()
    c["Mode"] = c["mode"].map(mode_label)
    left, right = st.columns(2)
    res_m = c[(c.scope == "resolver") & (c.kind == "measured")]
    res_a = c[(c.scope == "resolver") & (c.kind == "analytical")]
    with left:
        st.subheader("Resolver cache · measured")
        fig = px.bar(res_m, x="dataset", y="signature_material_bytes", color="mode", barmode="group", log_y=True,
                     category_orders={"dataset": datasets, "mode": modes}, color_discrete_map=COLORS,
                     hover_data=["cached_responses", "cached_rrsigs", "python_heap_bytes", "ladder_bytes"])
        fig.for_each_trace(lambda tr: tr.update(name=mode_label(tr.name)))
        st.plotly_chart(fig, width="stretch")
        st.caption("Signature + ladder bytes held after the workload, counted on responses parsed from wire into "
                   "dnspython's LRUCache; python_heap_bytes is tracemalloc-retained heap for the same population.")
    with right:
        st.subheader("Resolver cache · analytical")
        fig = px.bar(res_a, x="dataset", y="signature_material_bytes", color="mode", barmode="group", log_y=True,
                     category_orders={"dataset": datasets, "mode": modes}, color_discrete_map=COLORS,
                     hover_data=["per_signature_bytes", "ladder_bytes"])
        fig.for_each_trace(lambda tr: tr.update(name=mode_label(tr.name)))
        st.plotly_chart(fig, width="stretch")
        st.caption("Same RRSIG count × maximum signature size from draft-harvey-cfrg-mtl-mode section 12.")
    st.subheader("MTL signer state")
    sig = c[c.scope == "signer"]
    st.dataframe(sig.dropna(axis=1, how="all"), width="stretch", hide_index=True)

# ---------------------------------------------------------------- sizes
with tabs[3]:
    s = F["sizes"]
    for q, title in (("condensed_signature", "Condensed signature"), ("signed_ladder", "Signed ladder")):
        sub = s[s.quantity == q]
        fig = px.bar(sub, x="dataset", y="bytes", color="kind", barmode="group", title=f"{title}: measured vs formula",
                     category_orders={"dataset": datasets})
        st.plotly_chart(fig, width="stretch")
    st.dataframe(s, width="stretch", hide_index=True)

# ---------------------------------------------------------------- live demo
def pick_query(zone, column, key: str, seed: int):
    names = [it for it in zone.items if it.record_class in ("small", "medium", "large")][:300]
    labels = [f"{it.owner.relativize(zone.origin)} {it.rdtype.name} · {it.record_class}" for it in names]
    choice = column.selectbox("Query", ["DNSKEY (apex)", "NXDOMAIN (random name)"] + labels, key=key)
    if choice.startswith("DNSKEY"):
        return "dnskey", zone.origin, 48
    if choice.startswith("NXDOMAIN"):
        return "nxdomain", dnswire.nx_names(zone, 1, seed)[0], 1
    it = names[labels.index(choice)]
    return "positive", it.owner, it.rdtype


def shorten(text: str) -> str:
    out = []
    for line in text.splitlines():
        if " RRSIG " in line and len(line) > 220:
            line = line[:200] + f" … [{len(line) - 200} more chars]"
        out.append(line)
    return "\n".join(out)


@st.cache_resource(show_spinner=False)
def demo_builder(path: str, dataset: str, mk: str):
    return dnswire.ResponseBuilder(demo_zone(path, dataset), demo_sigs(path, dataset, mk))


SERVER_DIR = ROOT / "results" / "live-server"

with tabs[4]:
    live_tabs = st.tabs(["Response sizes (model)", "Recorded live run (packets)", "Send a real query"])

# ---- model: the response each mode would send
with live_tabs[0]:
    st.markdown("Build the **actual DNS response** each mode would send, from the signatures stored by this run.")
    a, b, cc = st.columns(3)
    ds_demo = a.selectbox("Dataset", datasets, key="demo_ds")
    zone = demo_zone(str(run_path), ds_demo)
    kind, qname, qtype = pick_query(zone, b, "demo_query", run.config["seed"])
    limit_demo = cc.radio("UDP buffer", list(dnswire.UDP_LIMITS), horizontal=True, key="demo_limit")
    rows, texts = [], {}
    for mk in modes:
        sigs = demo_sigs(str(run_path), ds_demo, mk)
        builder = dnswire.ResponseBuilder(zone, sigs)
        variants = [False, True] if MODES[mk].family == "mtl" else [False]
        for full in variants:
            q, resp = builder.response(kind, qname, qtype, limit_demo, mtl_full=full)
            qb, rb = dnswire.wire_len(q), dnswire.wire_len(resp)
            ex = dnswire.exchange(qb, rb, dnswire.truncated_len(q), limit_demo, force_tcp=full)
            label = mode_label(mk) + (" · full sig (first contact, TCP)" if full else "")
            rows.append({"mode": label, "response bytes": rb, "RRSIG signature bytes": dnswire.rrsig_bytes(resp),
                         "fits UDP": rb <= limit_demo, "transport": ex["transport"],
                         "bytes on wire": ex["dns_bytes"], "round trips (est.)": ex["round_trips_est"]})
            texts[label] = resp.to_text()
    demo = pd.DataFrame(rows)
    fig = go.Figure(go.Bar(x=demo["response bytes"], y=demo["mode"], orientation="h",
                           marker_color=["#2f9e44" if f else "#d64545" for f in demo["fits UDP"]]))
    fig.add_vline(x=limit_demo, line_dash="dot", annotation_text=f"{limit_demo} B")
    fig.update_layout(xaxis_title="response bytes", height=320, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")
    st.dataframe(demo, width="stretch", hide_index=True)
    pick = st.selectbox("Show response (dig-style)", list(texts))
    st.code(shorten(texts[pick]), language="text")
    with st.expander("Pseudocode of the measured flow"):
        st.code(PSEUDOCODE, language="python")

# ---- recorded live run: real packets captured with tcpdump
with live_tabs[1]:
    live_path = run_path / "live_summary.csv"
    if not live_path.exists() or "profile" not in pd.read_csv(live_path, nrows=1):
        st.info("No live run recorded for this run yet. Record one (real NSD, real client, tc-netem profiles, "
                f"pcaps):\n\n`.\\scripts\\run_live.ps1 -Run {run_path.name}`")
    else:
        ls, lv, meta = load_live(str(run_path), live_path.stat().st_mtime)
        prof_info = meta.get("profiles", {})
        st.markdown("A real client sent the lookup workload to a real NSD serving this run's signed zone: UDP "
                    "first, a resend when no answer came in time, TCP when the answer was truncated. Profiles: "
                    + "; ".join(f"**{k}** = {v['label']}" for k, v in prof_info.items())
                    + f". Loopback MTU {meta.get('mtu') or 'default'}; {meta.get('repeats', '?')} repeats per cell.")
        a, b = st.columns(2)
        live_ds = [d for d in datasets if d in set(ls.dataset)]
        ds_live = a.selectbox("Dataset", live_ds, index=len(live_ds) - 1, key="live_ds")
        udp_live = b.radio("EDNS UDP buffer", sorted(ls.udp_limit.unique()), horizontal=True, key="live_udp")
        cell = ls[(ls.dataset == ds_live) & (ls.udp_limit == udp_live)].copy()
        cell["seconds"] = cell.total_ms / 1e3
        cell["err_plus"] = (cell.total_ms_max - cell.total_ms) / 1e3
        cell["err_minus"] = (cell.total_ms - cell.total_ms_min) / 1e3
        profiles_live = list(dict.fromkeys(ls.profile))
        fig = px.bar(cell, x="profile", y="seconds", color="mode", barmode="group", log_y=True,
                     error_y="err_plus", error_y_minus="err_minus", color_discrete_map=COLORS,
                     category_orders={"profile": profiles_live, "mode": modes},
                     hover_data=["sent", "udp_only", "tc_then_tcp", "udp_retries", "failed", "live_bytes"],
                     labels={"seconds": "seconds for the whole workload (log)"})
        fig.for_each_trace(lambda tr: tr.update(name=mode_label(tr.name)))
        st.plotly_chart(fig, width="stretch")
        st.caption("Bars: median across repeats; error bars: min–max. Each lookup waits for its answer before the "
                   "next is sent, so delay adds up per round trip: UDP answers cost one, TC→TCP costs three or more.")
        show = cell[["mode", "profile", "sent", "udp_only", "tc_then_tcp", "ladder_fetches", "udp_retries", "failed",
                     "live_bytes", "model_bytes", "total_ms", "median_ms", "p95_ms"]].copy()
        show["mode"] = show["mode"].map(mode_label)
        st.dataframe(show.round(2), width="stretch", hide_index=True)

        st.subheader("Packet viewer")
        if "qid" not in lv:
            st.warning("This live run predates packet matching; re-run it to use the viewer.")
        else:
            c1, c2, c3 = st.columns(3)
            prof_v = c1.selectbox("Profile", profiles_live, key="pv_prof")
            mode_by_label = {mode_label(m): m for m in modes if m in set(ls["mode"])}
            mk_v = mode_by_label[c2.selectbox("Mode", list(mode_by_label), key="pv_mode")]
            by_time = c3.radio("Vertical axis", ["packet order", "time"], horizontal=True, key="pv_axis") == "time"
            look = lv[(lv.dataset == ds_live) & (lv["mode"] == mk_v) & (lv.profile == prof_v) &
                      (lv.udp_limit == udp_live) & (lv.repeat == 0)]
            fmt = {i: f"#{r.seq} {r.qname} {r.qtype} · {r.transport}{' (ladder fetch)' if r.ladder_fetch else ''} · "
                      f"{int(r.response_bytes):,} B · {r.ms:.1f} ms" + (" · resent" if r.udp_retries else "")
                   for i, r in look.iterrows()}
            only = st.checkbox("Only lookups that needed TCP or a resend", key="pv_only")
            by_label = {fmt[i]: i for i, r in look.iterrows() if not only or r.transport != "udp" or r.udp_retries}
            if not by_label:
                st.info("No such lookups in this cell.")
            else:
                sel = look.loc[by_label[st.selectbox("Lookup", list(by_label), key="pv_lookup")]]
                pfile = run_path / "pcap" / f"live-{ds_live}-{mk_v}-{prof_v}-{udp_live}.pcap"
                if not pfile.exists():
                    st.warning(f"{pfile.name} not found.")
                else:
                    packets = load_pcap(str(pfile), pfile.stat().st_mtime, pfile.stat().st_size)
                    conv = pcap.conversation(packets, int(sel.qid), sel.t_start, sel.t_end)

                    def lane_of(ip, port):
                        if port == 53:
                            return "NSD (full signatures) 127.0.0.2" if ip == live.FULL_HOST else "NSD 127.0.0.1"
                        return "Resolver (client)"

                    lanes = ["Resolver (client)", "NSD 127.0.0.1"] + (
                        ["NSD (full signatures) 127.0.0.2"] if sel.ladder_fetch else [])
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Packets on the wire", len(conv))
                    m2.metric("Transport", sel.transport)
                    m3.metric("Answer", f"{int(sel.response_bytes):,} B")
                    m4.metric("Client time", f"{sel.ms:.2f} ms")
                    if conv.empty:
                        st.warning("No packets matched this lookup in the pcap.")
                    else:
                        st.plotly_chart(sequence_figure(packet_arrows(conv, lane_of), lanes, by_time),
                                        width="stretch")
                        st.dataframe(packet_table(conv), width="stretch", hide_index=True)
                    st.download_button(f"Download {pfile.name} (open in Wireshark)", pfile.read_bytes(),
                                       file_name=pfile.name, key="pv_dl")

# ---- send a real query to the live server
with live_tabs[2]:
    info_path = SERVER_DIR / "server.json"
    if not info_path.exists():
        st.info("No live server is running. Start one in a terminal (it keeps NSD running, one port per mode, on "
                "127.0.0.1 only), then reload this page:\n\n"
                f"`.\\scripts\\live_server.ps1 -Run {run_path.name} -Profile wan`\n\n"
                "Profiles: lan, wan (50 ms round trip), lossy (50 ms, 1% loss). Stop it with Ctrl+C.")
    else:
        info = json.loads(info_path.read_text())
        srv_path = ROOT / "results" / "runs" / info["run"]
        prof = live.PROFILES[info["profile"]]
        st.markdown(f"Live server: **{info['run']}**, {info['dataset']} zone, {prof['label']}, "
                    f"{info.get('nsd_version', 'NSD')}. Queries go from this PC to NSD in Docker as real packets.")
        probe = live.exchange(dns.message.make_query("bench.example.", "SOA").to_wire(), host=info["host"],
                              port=next(iter(info["ports"].values())), udp_timeout=1.0, udp_tries=1)
        if probe["failed"]:
            st.warning("server.json exists but the server does not answer. Is `live_server.ps1` still running?")
        zone_s = demo_zone(str(srv_path), info["dataset"])
        a, b, c = st.columns([2, 1, 1])
        kind_s, qname_s, qtype_s = pick_query(zone_s, a, "send_query", 0)
        udp_s = b.radio("EDNS UDP buffer", list(dnswire.UDP_LIMITS), horizontal=True, key="send_udp")
        first = c.checkbox("MTL: first contact (fetch full signature)", key="send_first",
                           disabled=not info.get("full"))
        if st.button("Send real queries", type="primary", disabled=probe["failed"]):
            sent = []
            for mk in info["modes"]:
                builder = demo_builder(str(srv_path), info["dataset"], mk)
                if MODES[mk].family == "mtl" and first and info.get("full"):
                    trace = []
                    res = live.exchange(builder.query(qname_s, qtype_s, udp_s, True).to_wire(), host=info["host"],
                                        port=info["full"]["port"], force_tcp=True, trace=trace)
                    sent.append({"mode": mk, "full": True, "port": info["full"]["port"], "res": res, "trace": trace})
                trace = []
                res = live.exchange(builder.query(qname_s, qtype_s, udp_s, False).to_wire(), host=info["host"],
                                    port=info["ports"][mk], udp_timeout=prof["udp_timeout_s"], trace=trace)
                sent.append({"mode": mk, "full": False, "port": info["ports"][mk], "res": res, "trace": trace})
            time.sleep(0.6)
            st.session_state["sent"] = {"query": f"{qname_s} {dns.rdatatype.to_text(qtype_s)}", "udp": udp_s,
                                        "items": sent}
        sent = st.session_state.get("sent")
        if sent:
            st.markdown(f"**Last query:** `{sent['query']}` with a {sent['udp']}-byte EDNS buffer")
            tab = pd.DataFrame([{
                "mode": mode_label(s["mode"]) + (" · full signature (first contact)" if s["full"] else ""),
                "server port": s["port"], "transport": s["res"]["transport"],
                "answer bytes": s["res"]["response_bytes"], "bytes on the wire": s["res"]["dns_bytes"],
                "time (ms)": round(s["res"]["ms"], 2), "UDP resends": s["res"]["udp_retries"],
                "failed": s["res"]["failed"]} for s in sent["items"]])
            fig = go.Figure(go.Bar(x=tab["time (ms)"], y=tab["mode"], orientation="h",
                                   marker_color=[COLORS.get(s["mode"], "#495057") for s in sent["items"]],
                                   text=[f"{t:.1f} ms · {b:,} B" for t, b in zip(tab["time (ms)"], tab["answer bytes"])],
                                   textposition="auto"))
            fig.update_layout(xaxis_title="time for the lookup (ms, measured on this PC)",
                              height=60 * len(tab) + 80, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, width="stretch")
            st.dataframe(tab, width="stretch", hide_index=True)
            pick_s = st.selectbox("Show the packets of", list(tab["mode"]), key="send_pick")
            item = sent["items"][list(tab["mode"]).index(pick_s)]
            by_time = st.radio("Vertical axis", ["packet order", "time"], horizontal=True, key="send_axis") == "time"
            server_lane = f"NSD :{item['port']}"
            left, right = st.columns(2)
            with left:
                st.markdown("**Client view** (steps timed on this PC)")
                st.plotly_chart(sequence_figure(trace_arrows(item["trace"], server_lane),
                                                ["Resolver (this PC)", server_lane], by_time), width="stretch")
            with right:
                st.markdown("**Server view** (packets captured by tcpdump on the container's network interface)")
                spcap = SERVER_DIR / info.get("pcap", "server.pcap")
                conv = pd.DataFrame()
                if spcap.exists():
                    packets = load_pcap(str(spcap), spcap.stat().st_mtime, spcap.stat().st_size)
                    conv = pcap.conversation(packets, item["res"]["qid"])
                if conv.empty:
                    st.info("No packets for this query in server.pcap yet; click Send again or reload.")
                else:
                    srv_ports = set(info["ports"].values()) | ({info["full"]["port"]} if info.get("full") else set())

                    def srv_lane(ip, port):
                        return f"NSD :{int(port)}" if port in srv_ports else "Resolver (via Docker)"

                    st.plotly_chart(sequence_figure(packet_arrows(conv, srv_lane),
                                                    ["Resolver (via Docker)", server_lane], by_time),
                                    width="stretch")
            if not conv.empty:
                st.dataframe(packet_table(conv), width="stretch", hide_index=True)
            if "response_wire" in item["res"]:
                with st.expander("Answer (dig-style)"):
                    st.code(shorten(dns.message.from_wire(item["res"]["response_wire"]).to_text()), language="text")

# ---------------------------------------------------------------- reproducibility
with tabs[5]:
    st.subheader("Go/no-go gate (PS-036: defer if the reference implementation or fixtures fail)")
    st.dataframe(pd.DataFrame(run.preflight.get("checks", [])), width="stretch", hide_index=True)
    st.subheader("Datasets")
    st.dataframe(F["datasets"], width="stretch", hide_index=True)
    left, right = st.columns(2)
    left.subheader("Configuration")
    left.code(yaml.safe_dump(run.config, sort_keys=False), language="yaml")
    right.subheader("Pinned environment")
    right.json(run.environment)
    st.caption(f"Run directory: {run_path}")
