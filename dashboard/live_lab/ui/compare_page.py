"""Compare tile: algorithms side by side, run against run, and the cost of zone updates."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from pathlib import Path

from cipherops import evolution
from live_lab import compare, compare_hub, driver
from live_lab import glossary as gl
from live_lab import suite as suite_mod
from live_lab.ui.benchmark_simulated import _signing_stats_table
from live_lab.ui.common import ECO_DIR, ROOT, _go, _page_header, _sidebar_brand, sb

CMP_VIEWS = {"algorithms": "Algorithms side by side", "runs": "Run against run", "evolution": "Zone updates"}
CMP_SOURCES = {"offline": "Offline (harness)", "live": "Simulated (Verisign stack)", "lab": "Saved lab run"}
CMP_SOURCE_NOTE = {
    "offline": "Pinned image, one dedicated core, repeated trials: reference timings (FIPS 205 SLH-DSA on the `latest` stack).",
    "live": "Real Unbound and NSD in containers (a finished simulated benchmark): real packets and cache behaviour, indicative timings (not core-pinned).",
    "lab": "One lab's last signing run plus one saved run, for older data. A benchmark is easier to compare.",
}
CMP_BADGE = {"measured": ":green-badge[Measured]", "estimated": ":blue-badge[Estimated]",
             "extrapolated": ":orange-badge[Extrapolated]"}


def _cmp_summary(tag: str | None, folder: str | None) -> pd.DataFrame | None:
    if not tag or not folder:
        return None
    f = ECO_DIR / "results" / tag / folder / "summary.csv"
    if not f.is_file():
        return None
    try:
        return pd.read_csv(f)
    except Exception:  # noqa: BLE001
        return None


def _cmp_catalogue(source: str, h: driver.LabHandle) -> list[dict]:
    """The runs one source can offer, newest first, each with the facts used to judge comparability."""
    if source == "offline":
        return compare_hub.offline_catalogue(ROOT / "results")
    if source == "live":
        return [compare_hub.live_suite_entry(s) for s in suite_mod.list_suites() if s["complete"]]
    out = []
    for lab in driver.list_labs():
        for r in driver.list_runs(h, lab["tag"]):
            if (r["path"] / "summary.csv").is_file():
                out.append({"id": f"{lab['tag']}|{r['folder']}", "title": f"{lab['tag']} · {r['title']}",
                            "source": "lab", "tag": lab["tag"], "folder": r["folder"], "stack": "—", "trials": None,
                            "facts": {"Lab": lab["tag"]}, "datasets": [lab["tag"]]})
    return out


@st.cache_resource(show_spinner=False)
def _cmp_offline_run(path: str, mtime: float):
    return compare_hub.load_offline(Path(path))


@st.cache_resource(show_spinner=False)
def _cmp_suite(path: str, mtime: float):
    return compare.load_suite(Path(path))


def _cmp_ds_label(source: str):
    return (lambda d: f"{d:,} names") if source == "live" else (lambda d: str(d))


def _cmp_data(entry: dict, ds, prof, udp, h: driver.LabHandle) -> dict:
    """One run reduced to the shared frame, plus whatever the detailed views need."""
    src = entry["source"]
    if src == "offline":
        run = _cmp_offline_run(str(entry["path"]), (entry["path"] / "summary.json").stat().st_mtime)
        return {"frame": compare_hub.offline_frame(run, ds), "run": run, "summary": None, "raw": None,
                "man_rows": [], "sd": None}
    if src == "live":
        sd = _cmp_suite(str(entry["path"]), (entry["path"] / "suite.json").stat().st_mtime)
        raw = compare.suite_frame(sd, int(ds), prof, udp)
        full = sd["full"][sd["full"]["dataset"] == ds] if not sd["full"].empty else sd["full"]
        return {"frame": compare_hub.live_frame(raw), "run": None, "summary": sd["bench"].get(int(ds)), "raw": raw,
                "man_rows": full.to_dict("records"), "sd": sd}
    man_rows = driver.manifest_rows(h, entry["tag"])
    summary = _cmp_summary(entry["tag"], entry["folder"])
    bench = compare.bench_by_scenario(summary, prof, udp) if summary is not None else compare.bench_by_scenario(pd.DataFrame())
    raw = compare.merge(compare.signing_by_scenario(man_rows), bench, compare.analytical_by_scenario(man_rows))
    return {"frame": compare_hub.live_frame(raw), "run": None, "summary": summary, "raw": raw, "man_rows": man_rows,
            "sd": None}


def _cmp_net_choices(entry: dict, ds) -> tuple[list, list]:
    """Network profiles and UDP limits a live run has numbers for."""
    summary = None
    if entry["source"] == "live":
        sd = _cmp_suite(str(entry["path"]), (entry["path"] / "suite.json").stat().st_mtime)
        summary = sd["bench"].get(int(ds)) if sd else None
    elif entry["source"] == "lab":
        summary = _cmp_summary(entry["tag"], entry["folder"])
    if summary is None or summary.empty:
        return [], []
    profiles = [str(p) for p in summary["profile"].dropna().unique()] if "profile" in summary else []
    udps = [int(u) for u in summary["udp_limit"].dropna().unique()] if "udp_limit" in summary else []
    return profiles, udps


def sidebar_compare(h: driver.LabHandle, stt: dict) -> None:
    _sidebar_brand("compare")  # the pickers are drawn by compare_page once it knows the view and the source


def _cmp_pickers(view: str, source: str, cat: list[dict], h: driver.LabHandle) -> dict | None:
    """Sidebar: which run(s), which dataset, which pair. Returns the choices with the data loaded."""
    by = {e["id"]: e for e in cat}
    ids = list(by)
    st.sidebar.markdown("### Compare")
    a_id = sb(st.sidebar.selectbox, f"cmp_a_{source}", ids[0], "Run A" if view == "runs" else "Run",
              options=ids, format_func=lambda i: by[i]["title"])
    ea, eb = by[a_id], None
    if view == "runs":
        b_id = sb(st.sidebar.selectbox, f"cmp_b_{source}", ids[1] if len(ids) > 1 else ids[0], "Run B",
                  options=ids, format_func=lambda i: by[i]["title"])
        eb = by[b_id]
    datasets = [d for d in ea["datasets"] if eb is None or d in eb["datasets"]]
    if not datasets:
        st.sidebar.warning("These two runs have no dataset size in common.")
        return None
    ds = sb(st.sidebar.selectbox, f"cmp_ds_{source}", datasets[len(datasets) // 2], gl.label("zone_size"),
            options=datasets, format_func=_cmp_ds_label(source), help=gl.tip("zone_size"))
    prof = udp = None
    if source != "offline":
        profiles, udps = _cmp_net_choices(ea, ds)
        if profiles:
            prof = sb(st.sidebar.selectbox, f"cmp_profile_{source}", profiles[0], gl.label("network_profile"), options=profiles,
                      help=gl.tip("network_profile"))
        if udps:
            udp = sb(st.sidebar.selectbox, f"cmp_udp_{source}", udps[0], gl.label("edns_buffer"), options=udps,
                      help=gl.tip("edns_buffer"))
    a = _cmp_data(ea, ds, prof, udp, h)
    b = _cmp_data(eb, ds, prof, udp, h) if eb else None
    out = {"ea": ea, "eb": eb, "ds": ds, "prof": prof, "udp": udp, "a": a, "b": b}
    if view == "algorithms":
        keys = list(a["frame"].key)
        if len(keys) >= 2:
            st.sidebar.markdown("---")
            st.sidebar.markdown("**Focus pair**", help="Pick the algorithm to evaluate and what to compare it with.")
            cand = sb(st.sidebar.selectbox, f"cmp_cand_{source}", next((k for k in ("slh-dsa-mtl", "mtl") if k in keys), keys[-1]),
                      "Candidate", options=keys, format_func=compare_hub.short,
                      help="The algorithm being evaluated.")
            others = [k for k in keys if k != cand]
            base = sb(st.sidebar.selectbox, f"cmp_base_{source}", "slh-dsa" if "slh-dsa" in others else others[0],
                      "against baseline", options=others, format_func=compare_hub.short,
                      help="The algorithm the candidate is compared with. Every ratio is relative to it; lower is better.")
            out.update(cand=cand, base=base)
    st.sidebar.markdown("---")
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_cmp"):
        _cmp_offline_run.clear()
        _cmp_suite.clear()
        st.rerun()
    return out


def _plot_cfg(fig, height: int = 360):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=50, b=10), legend_title_text="")
    fig.for_each_xaxis(lambda a: a.update(title_text=gl.label("algorithm")) if a.title.text == "scenario" else None)
    return fig


def _cmp_reference_line(fig, categories: list[str], y: float, name: str) -> None:
    """A dotted reference line as a trace: a shape on a log axis is placed inconsistently between plotly versions."""
    fig.add_scatter(x=categories, y=[y] * len(categories), mode="lines", name=name, showlegend=False,
                    line=dict(dash="dot", color="#555", width=1), hoverinfo="skip")


def _cmp_bar(frame: pd.DataFrame, col: str, *, height: int = 330, err: bool = False):
    d = frame.dropna(subset=[col]).copy()
    if d.empty:
        return None
    title, unit = compare_hub.METRICS[col]
    d["text"] = [compare_hub.fmt_value(v, unit) + (" (extrapolated)" if col == "sign_s" and b == "extrapolated" else "")
                 for v, b in zip(d[col], d["sign_basis"])]
    kw = {}
    lo, hi = {"sign_s": ("sign_lo", "sign_hi"), "verify_s": ("verify_lo", "verify_hi")}.get(col, (None, None))
    if err and lo and d[lo].notna().all() and d[hi].notna().all():
        d["err_hi"], d["err_lo"] = (d[hi] - d[col]).clip(lower=0), (d[col] - d[lo]).clip(lower=0)
        kw = {"error_y": "err_hi", "error_y_minus": "err_lo"}
    log = unit != "n" and (d[col] > 0).all()
    fig = px.bar(d, x="label", y=col, color="key", color_discrete_map=compare_hub.COLORS, text="text", log_y=log,
                 category_orders={"label": list(frame.label)}, title=f"{title}" + (" (log scale)" if log else ""), **kw)
    fig.update_layout(showlegend=False, xaxis_title=None, yaxis_title={"s": "seconds", "B": "bytes", "n": "count"}[unit])
    fig.update_traces(textposition="outside", cliponaxis=False)
    return _plot_cfg(fig, height)


METRIC_TERM = {"sign_s": "signing_time", "verify_s": "verification_time", "size_bytes": "signature_size",
               "wire_bytes": "response_size", "truncations": "tc_responses", "cache_bytes": "cache_size"}


def _cmp_cards(frame: pd.DataFrame, base: str, cand: str, paired: pd.DataFrame | None) -> None:
    x = compare_hub.vs_baseline(frame, base).set_index("key")
    fr = frame.set_index("key")
    cols = st.columns(len(compare_hub.METRICS))
    for c, (col, (title, unit)) in zip(cols, compare_hub.METRICS.items()):
        v, b = fr.loc[cand, col], fr.loc[base, col]
        what = gl.tip(METRIC_TERM[col])
        if pd.isna(v) or pd.isna(b):
            c.metric(title, "—", help=f"{what}\n\nNo data for this metric.")
            continue
        text, help_ = compare_hub.fmt_x(x.loc[cand, col]), what
        if paired is not None and not paired.empty:
            p = paired[(paired.key == cand) & (paired.col == col)]
            if len(p):
                text = compare_hub.fmt_x(p.iloc[0].ratio)
                help_ = (f"{what}\n\nMedian of the per-trial ratios over {int(p.iloc[0].trials)} runs; interval "
                         f"{p.iloc[0].lo:.3g} to {p.iloc[0].hi:.3g} (baseline ÷ candidate).")
        c.metric(title, text, f"{compare_hub.fmt_value(v, unit)} vs {compare_hub.fmt_value(b, unit)}",
                 delta_color="off", delta_arrow="off", help=help_)


def _cmp_scaling(source: str, a: dict, prof, udp) -> pd.DataFrame:
    if source == "offline":
        return compare_hub.offline_scaling(a["run"])
    if source == "live":
        parts = [compare_hub.live_frame(compare.suite_frame(a["sd"], d, prof, udp)).assign(dataset=d, rrsets=d)
                 for d in a["sd"]["datasets"]]
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return pd.DataFrame()


def _cmp_live_detail(df: pd.DataFrame, sd, ds, use_suite: bool, man_rows: list, name: str) -> None:
    scen_order, colors = list(df.scenario), compare.COLORS
    signing, wire, cache, allnums = st.tabs(["Signing & size", "Response size by tier", "Cache & latency", "All numbers"])
    with signing:
        if use_suite:
            st.markdown(":blue-badge[Estimated] sign and verify time from the repeated sample trials · "
                        ":green-badge[Measured] signed size and the single full signing run")
        else:
            st.markdown(":green-badge[Measured] from the last signing run")
        sg = df.dropna(subset=["sign_s"]) if "sign_s" in df else df
        if sg.empty:
            st.info("No signing data: sign the zones in Simulated lab ① Prepare.")
        else:
            c1, c2 = st.columns(2)
            long = sg.melt(id_vars="scenario", value_vars=["sign_s", "verify_s"], var_name="what", value_name="seconds")
            long["what"] = long["what"].map({"sign_s": "Signing time", "verify_s": "Verification time"})
            if use_suite:
                long = long.merge(sg[["scenario", "sign_lo_s", "sign_hi_s"]], on="scenario", how="left")
                is_sign = long["what"] == "Signing time"
                long["err_hi"] = (long["sign_hi_s"] - long["seconds"]).where(is_sign)
                long["err_lo"] = (long["seconds"] - long["sign_lo_s"]).where(is_sign)
                f1 = px.bar(long, x="scenario", y="seconds", color="what", barmode="group", log_y=True,
                            error_y="err_hi", error_y_minus="err_lo", category_orders={"scenario": scen_order},
                            title="Signing and verification time, estimated (s, log; bars = confidence interval of the median)")
            else:
                f1 = px.bar(long, x="scenario", y="seconds", color="what", barmode="group", log_y=True,
                            category_orders={"scenario": scen_order}, title="Signing and verification time (s, log)")
            c1.plotly_chart(_plot_cfg(f1), width="stretch")
            sz = sg.assign(MB=sg.signed_bytes / 1024 ** 2)
            f2 = px.bar(sz, x="scenario", y="MB", color="scenario", color_discrete_map=colors,
                        category_orders={"scenario": scen_order}, title="Signed zone size (MB)", text_auto=".2f")
            f2.update_layout(showlegend=False)
            c2.plotly_chart(_plot_cfg(f2), width="stretch")
            f3 = px.bar(sg, x="scenario", y="sign_ms_per_rrsig", color="scenario", color_discrete_map=colors,
                        category_orders={"scenario": scen_order}, log_y=True,
                        title="Signing time per RRSIG (ms, log)", text_auto=".3g")
            f3.update_layout(showlegend=False)
            st.plotly_chart(_plot_cfg(f3, 320), width="stretch")
            if use_suite:
                stt_ = compare.signing_trial_stats(sd["trials"], sd["full"])
                stt_ = stt_[stt_["dataset"] == int(ds)]
                st.markdown("**Signing repeats in detail**")
                st.dataframe(_signing_stats_table(stt_), width="stretch", hide_index=True)
                st.caption("Estimated: each trial signs two sample zones; a line through them gives a fixed cost per "
                           "zone and a cost per RRSIG, applied to every zone of the dataset. Measured once: the full "
                           "zones the lab served, signed one time. Compare the two to judge the extrapolation.")
            st.caption("Verification here is `ldns-verify-zone` over every signed zone (an offline check). "
                       "How long a resolver takes to validate is part of the cold-pass latency under Cache & latency.")

    with wire:
        st.markdown(":green-badge[Measured] from packet counts in the saved run")
        bw = df.dropna(subset=["upstream_bytes"]) if "upstream_bytes" in df else df
        if bw.empty:
            st.info("No benchmark data: run an Offline benchmark or a Simulated lab benchmark.")
        else:
            c1, c2 = st.columns(2)
            hops = bw.melt(id_vars="scenario", value_vars=["root_bytes", "tld_bytes", "sld_bytes"],
                           var_name="hop", value_name="bytes")
            hops["hop"] = hops["hop"].map({"root_bytes": "Root", "tld_bytes": "TLD", "sld_bytes": "Leaf"})
            hops["KB"] = hops["bytes"] / 1024
            f1 = px.bar(hops, x="scenario", y="KB", color="hop", barmode="stack",
                        category_orders={"scenario": scen_order},
                        title="Resolver→authoritative bytes by tier, cold cache (KB)")
            c1.plotly_chart(_plot_cfg(f1), width="stretch")
            tr = bw.melt(id_vars="scenario", value_vars=["truncated", "tcp_conns", "upstream_queries"],
                         var_name="what", value_name="count")
            tr["what"] = tr["what"].map({"truncated": "Truncated (TC) responses", "tcp_conns": "TCP connections",
                                         "upstream_queries": "Resolver→authoritative queries"})
            f2 = px.bar(tr, x="scenario", y="count", color="what", barmode="group",
                        category_orders={"scenario": scen_order}, title="Truncation, TCP fallback and upstream queries")
            c2.plotly_chart(_plot_cfg(f2), width="stretch")
            cb = bw["client_bytes"].dropna()
            if not cb.empty:
                same = "the same for every algorithm" if cb.nunique() == 1 else "shown in the table"
                st.caption(f"Client ↔ resolver traffic is {same} (about {compare.fmt_bytes(cb.median())}). "
                           "Warm-cache passes are answered from the cache and send no upstream traffic.")

    with cache:
        cc = df.dropna(subset=["cache_rrset"]) if "cache_rrset" in df else df
        st.markdown("##### Measured :green-badge[Measured]")
        if cc.empty:
            st.info("No benchmark data: run an Offline benchmark or a Simulated lab benchmark.")
        else:
            c1, c2 = st.columns(2)
            mem = cc.melt(id_vars="scenario", value_vars=["cache_rrset", "cache_message"],
                          var_name="cache", value_name="bytes")
            mem["cache"] = mem["cache"].map({"cache_rrset": "RRset cache", "cache_message": "Message cache"})
            mem["MB"] = mem["bytes"] / 1024 ** 2
            f1 = px.bar(mem, x="scenario", y="MB", color="cache", barmode="group",
                        category_orders={"scenario": scen_order}, title="Cache size after the cold-cache pass (MB)")
            c1.plotly_chart(_plot_cfg(f1), width="stretch")
            lat = cc.melt(id_vars="scenario", value_vars=["cold_ms", "warm_ms"], var_name="pass", value_name="ms")
            lat["pass"] = lat["pass"].map({"cold_ms": "Cold cache", "warm_ms": "Warm cache"})
            f2 = px.bar(lat, x="scenario", y="ms", color="pass", barmode="group",
                        category_orders={"scenario": scen_order}, title=gl.label("latency_p50"))
            c2.plotly_chart(_plot_cfg(f2), width="stretch")
            st.caption("Cold is the first pass after flushing the resolver; warm repeats the same lookups from cache. "
                       "Cache memory is Unbound's RRset and message cache after the cold pass.")

        st.markdown("---")
        st.markdown("##### Analytical :orange-badge[Analytical]")
        an = df.dropna(subset=["analytic_sig_bytes"])
        per = compare.analytical_per_signature(man_rows)
        if an.empty:
            st.info("No analytical estimate for these algorithms (needs signed zones for SLH-DSA or MTL).")
        else:
            c1, c2 = st.columns([3, 2])
            ax = an.assign(MB=an.analytic_sig_bytes / 1024 ** 2)
            f = px.bar(ax, x="scenario", y="MB", color="scenario", color_discrete_map=colors,
                       category_orders={"scenario": scen_order}, text_auto=".2f",
                       title="Signature storage for the whole zone set, draft §12 formulas (MB)")
            f.update_layout(showlegend=False)
            c1.plotly_chart(_plot_cfg(f, 320), width="stretch")
            if per:
                c2.markdown(f"**One signature, maximum size** (largest zone: {per['n_messages']} RRsets)")
                c2.markdown(
                    f"- Direct SLH-DSA: **{per['direct']:,} B**  \n"
                    f"- MTL condensed: **{per['condensed']:,} B**  \n"
                    f"- MTL signed ladder (once per zone): **{per['ladder']:,} B**  \n"
                    f"- MTL full (condensed + ladder): **{per['full']:,} B**"
                )
        st.caption("Analytical figures are computed from the draft's size formulas and the zone's RRSIG counts. "
                   "They are never read from the lab and are kept apart from the measured cache above. "
                   "They describe signature material for the whole zone set, not what the resolver actually cached.")


    with allnums:
        t = compare.table(df)
        st.dataframe(t, width="stretch", hide_index=True)
        st.download_button("Download comparison (CSV)", data=df.to_csv(index=False), key="cmp_live_dl",
                           file_name=f"comparison-{name}.csv", mime="text/csv")



def _cmp_algorithms(source: str, pick: dict, h: driver.LabHandle) -> None:
    a, ea, ds = pick["a"], pick["ea"], pick["ds"]
    frame = a["frame"]
    if frame.empty or len(frame) < 2:
        st.info("This run has fewer than two algorithms with data for the chosen dataset.")
        return
    base, cand = pick.get("base"), pick.get("cand")
    paired = compare_hub.offline_paired(a["run"], ds, base) if a["run"] is not None and base else None
    facts = ea["facts"]
    bits = [f"**{ea['title']}**", f"dataset **{_cmp_ds_label(source)(ds)}**"]
    if source == "offline":
        bits.append(f"stack {facts.get('Stack profile')} · {facts.get('Runs')} runs · one pinned core")
    elif pick["prof"]:
        bits.append(f"network {pick['prof']}, UDP {pick['udp']}")
    st.caption(" · ".join(bits))
    for note in compare.lookup_problems(a["summary"]):
        st.warning(note + ". Do not compare this algorithm until the failures are explained.")
    bands = [r["band"] for r in (a["run"].summary.get("noise_floor", []) if a["run"] is not None else [])
             if r["dataset"] == ds]
    if bands:
        band = max(bands)
        st.caption(f"Measurement noise floor on this run (A/A control): ±{band * 100:.1f}%. A timing difference "
                   "smaller than that is not a difference.")

    st.markdown(f"#### {compare_hub.short(cand)} vs {compare_hub.short(base)}")
    _cmp_cards(frame, base, cand, paired)

    tabs = st.tabs(["At a glance", "Signing & verification", "Response size & truncation", "Cache & latency",
                    "Across zone sizes", "All numbers"])
    glance, signing, wire, cache, across, allnums = tabs
    notes = compare_hub.METRIC_NOTES["offline" if source == "offline" else "live"]
    colors = compare_hub.COLORS

    with glance:
        rel = []
        fr = frame.set_index("key")
        for k in frame.key:
            for col, (title, _u) in compare_hub.METRICS.items():
                b, v = fr.loc[base, col], fr.loc[k, col]
                if pd.notna(b) and pd.notna(v) and b > 0 and v > 0:
                    rel.append({"Metric": title, "Algorithm": compare_hub.short(k), "% of baseline": 100 * v / b})
        if rel:
            fig = px.bar(pd.DataFrame(rel), x="Metric", y="% of baseline", color="Algorithm", barmode="group", log_y=True,
                         color_discrete_map={compare_hub.short(k): c for k, c in colors.items()},
                         category_orders={"Algorithm": [compare_hub.short(k) for k in frame.key]},
                         title=f"Everything relative to {compare_hub.short(base)} (= 100%, log scale; lower is better)")
            _cmp_reference_line(fig, [m[0] for m in compare_hub.METRICS.values()], 100, compare_hub.short(base))
            st.plotly_chart(_plot_cfg(fig, 420), width="stretch")
        st.markdown("##### All algorithms")
        alg = compare_hub.algorithm_table(frame, base, paired)
        st.dataframe(alg, width="stretch", hide_index=True, column_config=gl.column_config(alg.columns))
        st.caption("'× lower' means the value is that many times below the baseline (every metric here: lower is "
                   "better). Brackets show the interval of the per-trial ratio where trials are paired.")

    with signing:
        if source == "offline":
            what = {"measured": "timed library calls", "extrapolated": "direct SLH-DSA total, from a sample of the zone"}
            st.markdown(" · ".join(f"{CMP_BADGE[b]} {what[b]}" for b in what if b in set(frame.sign_basis)))
        else:
            st.markdown(f"{CMP_BADGE['estimated']} {notes['sign_s']}")
        c1, c2 = st.columns(2)
        for col_, w in (("sign_s", c1), ("verify_s", c2)):
            fig = _cmp_bar(frame, col_, err=True)
            if fig is not None:
                w.plotly_chart(fig, width="stretch")
        fig = _cmp_bar(frame, "size_bytes")
        if fig is not None:
            st.plotly_chart(fig, width="stretch")
        st.caption(f"Signing: {notes['sign_s']}  \nVerification: {notes['verify_s']}  \nSize: {notes['size_bytes']}  \n"
                   "Bars show the interval of the median where one exists.")
        if source == "offline":
            st.caption(compare_hub.stats_label(a["run"]))

    with wire:
        st.markdown(":green-badge[Measured] " + ("DNS messages of the replayed workload" if source == "offline"
                                                 else "packet counts of the saved run"))
        c1, c2 = st.columns(2)
        for col_, w in (("wire_bytes", c1), ("truncations", c2)):
            fig = _cmp_bar(frame, col_)
            if fig is not None:
                w.plotly_chart(fig, width="stretch")
        if frame[["wire_bytes", "truncations"]].isna().all().all():
            st.info("No bytes-on-the-wire data in this run. Run a benchmark (live) or the full harness (offline).")
        st.caption(f"Bytes: {notes['wire_bytes']}  \nTruncations: {notes['truncations']}")

    with cache:
        st.markdown(":green-badge[Measured] " + notes["cache_bytes"])
        c1, c2 = st.columns(2)
        fig = _cmp_bar(frame, "cache_bytes")
        if fig is not None:
            c1.plotly_chart(fig, width="stretch")
        lat = frame.dropna(subset=["cold_ms"])
        if not lat.empty:
            m = lat.melt(id_vars=["key", "label"], value_vars=["cold_ms", "warm_ms"], var_name="pass", value_name="ms")
            m["pass"] = m["pass"].map({"cold_ms": "Cold cache", "warm_ms": "Warm cache"})
            f2 = px.bar(m, x="label", y="ms", color="pass", barmode="group", category_orders={"label": list(lat.label)},
                        title=gl.label("latency_p50"))
            f2.update_layout(xaxis_title=None)
            c2.plotly_chart(_plot_cfg(f2), width="stretch")
        elif source == "offline":
            c2.info("Lookup latency is measured only in Simulated mode, where a real resolver answers.")
        if fig is None and lat.empty:
            st.info("No cache or latency data in this run.")

    with across:
        sc = _cmp_scaling(source, a, pick["prof"], pick["udp"])
        if sc.empty or sc.dataset.nunique() < 2:
            st.info("Only one dataset size is available for this run.")
        else:
            x_title = "RRsets in the zone" if source == "offline" else "Data names"
            left, right = st.columns(2)
            for i, (col_, (title, unit)) in enumerate(compare_hub.METRICS.items()):
                data = sc.dropna(subset=[col_])
                data = data[data[col_] > 0]
                if data.empty:
                    continue
                f = px.line(data, x="rrsets", y=col_, color="key", markers=True, log_x=True, log_y=True,
                            color_discrete_map=colors, category_orders={"key": list(frame.key)}, title=title)
                f.update_xaxes(title=x_title)
                f.update_layout(legend_title_text="")
                (left if i % 2 == 0 else right).plotly_chart(_plot_cfg(f, 320), width="stretch")
            st.caption("Both axes are logarithmic: a straight line is power-law growth and a constant gap between two "
                       "lines is a constant ratio.")

    with allnums:
        st.dataframe(frame.drop(columns=["label"]), width="stretch", hide_index=True)
        st.download_button("Download this comparison (CSV)", data=frame.to_csv(index=False), key="cmp_alg_dl",
                           file_name=f"algorithms-{ea['id']}-{ds}.csv", mime="text/csv")

    if source != "offline" and a["raw"] is not None and not a["raw"].empty:
        with st.expander("Live-lab detail: bytes by tier, latency, analytical sizes"):
            _cmp_live_detail(a["raw"], a["sd"], ds, source == "live", a["man_rows"], ea["id"])


def _cmp_runs(source: str, pick: dict) -> None:
    ea, eb, ds, a, b = pick["ea"], pick["eb"], pick["ds"], pick["a"], pick["b"]
    if ea["id"] == eb["id"]:
        st.info("Run A and Run B are the same run. Pick two different runs in the sidebar.")
        return
    st.markdown("#### What differs between the two runs")
    for note in compare_hub.comparability_notes(ea["facts"], eb["facts"]):
        (st.info if note.startswith("Same") else st.warning)(note)
    diff = compare_hub.facts_diff(ea["facts"], eb["facts"])
    diff["Same"] = diff["Same"].map({True: "", False: "differs"})
    st.dataframe(diff, width="stretch", hide_index=True)

    bands = {}
    if source == "offline":
        for col_, name in (("sign_s", "signing"), ("verify_s", "verification (warm)")):
            vals = [x for x in (compare_hub.noise_band(a["run"], ds, name), compare_hub.noise_band(b["run"], ds, name))
                    if x is not None]
            if vals:
                bands[col_] = max(vals)
    ratios = compare_hub.run_ratios(a["frame"], b["frame"], bands)
    if ratios.empty:
        st.info("The two runs have no algorithm and metric in common for this dataset.")
        return
    st.markdown(f"#### Run B relative to Run A · dataset {_cmp_ds_label(source)(ds)}")
    near = int((abs(ratios.ratio - 1) <= 0.05).sum())
    st.caption(f"{near} of {len(ratios)} values are within 5% of each other. A ratio of 1 means identical; "
               "above 1 means Run B is higher (all metrics here: lower is better).")
    ratios = ratios.assign(**{"Algorithm": ratios["label"]})
    fig = px.bar(ratios, x="metric", y="ratio", color="Algorithm", barmode="group", log_y=True,
                 color_discrete_map={compare_hub.short(k): c for k, c in compare_hub.COLORS.items()},
                 category_orders={"Algorithm": [compare_hub.short(k) for k in compare_hub.order_keys(ratios.key)]},
                 title="Run B ÷ Run A (log scale)")
    _cmp_reference_line(fig, list(dict.fromkeys(ratios["metric"])), 1, "identical")
    st.plotly_chart(_plot_cfg(fig, 400), width="stretch")
    show = pd.DataFrame({
        "Algorithm": ratios["label"], "Metric": ratios["metric"],
        "Run A": [compare_hub.fmt_value(v, u) for v, u in zip(ratios.a, ratios.unit)],
        "Run B": [compare_hub.fmt_value(v, u) for v, u in zip(ratios.b, ratios.unit)],
        "B ÷ A": [f"{r:.3g}" for r in ratios.ratio],
        "Against the noise floor": ["" if w is None else ("within noise" if w else "outside noise")
                                    for w in ratios.within_noise],
    })
    st.dataframe(show, width="stretch", hide_index=True)
    st.download_button("Download this comparison (CSV)", data=ratios.drop(columns=["Algorithm"]).to_csv(index=False),
                       key="cmp_runs_dl", file_name=f"runs-{ea['id']}-vs-{eb['id']}-{ds}.csv", mime="text/csv")
    if source == "offline" and bands:
        st.caption("Noise floor: the larger A/A band of the two runs, for signing and verification only.")


def _ladder_figure(leaves: int, cached_leaves: int, age: int):
    """The signer's ladder and a resolver's older one on the same leaf axis: a rung is a complete binary subtree, and
    the leaves appended since the cached ladder have no rung in it."""
    fig = go.Figure()
    palette = ["#2e9e6f", "#4c78a8"]
    rows = [("Signer's ladder now", leaves), (f"Resolver's cached ladder ({age} round(s) old)", cached_leaves)]
    for i, (name, count) in enumerate(rows):
        for size, a, b in evolution.rungs_of(count):
            fig.add_trace(go.Bar(y=[name], x=[size], base=[a], orientation="h", marker_color=palette[i],
                                 marker_line=dict(color="white", width=1.5), text=[f"{size:,}"],
                                 textposition="inside", insidetextanchor="middle", showlegend=False,
                                 hovertemplate=f"rung of {size:,} leaves ({a:,} to {b:,})<extra></extra>"))
    if leaves > cached_leaves:
        fig.add_trace(go.Bar(y=[rows[1][0]], x=[leaves - cached_leaves], base=[cached_leaves], orientation="h",
                             marker_color="#e4572e", marker_line=dict(color="white", width=1.5),
                             text=[f"{leaves - cached_leaves:,} new"], textposition="inside", showlegend=False,
                             hovertemplate="appended since: no rung in the cached ladder<extra></extra>"))
    fig.update_layout(barmode="overlay", height=230, margin=dict(l=10, r=10, t=30, b=10), showlegend=False,
                      xaxis_title="leaves of the series (rungs are complete binary subtrees)",
                      yaxis=dict(autorange="reversed"))
    return fig


def _cmp_evolution(h: driver.LabHandle) -> None:
    cat = compare_hub.evolution_catalogue(ROOT / "results")
    if not cat:
        st.info("No offline run includes the zone-updates experiment yet. Every offline run whose config has an "
                "`evolution:` block includes it (the default configs do). Start one from the Offline benchmark.")
        st.button("Go to the Offline benchmark", on_click=_go, args=("offline", "run"), key="cmp_ev_to_bench")
        return
    by = {e["id"]: e for e in cat}
    st.sidebar.markdown("### Zone updates")
    rid = sb(st.sidebar.selectbox, "cmp_ev_run", next(iter(by)), "Run", options=list(by),
             format_func=lambda i: by[i]["title"])
    path = by[rid]["path"]
    run = _cmp_offline_run(str(path), (path / "summary.json").stat().st_mtime)
    mtl, direct = run.frames["evolution"], run.frames["evolution_direct"]
    datasets = [d for d in run.dataset_order if d in set(mtl.dataset)]
    ds = sb(st.sidebar.selectbox, "cmp_ev_ds", datasets[-1], "Zone size", options=datasets)
    ks = sorted(int(k) for k in mtl.k.unique())
    k = sb(st.sidebar.selectbox, "cmp_ev_k", ks[len(ks) // 2], "Records changed per update", options=ks,
                help=gl.tip("records_changed"))
    if st.sidebar.button("Refresh", width="stretch", key="sb_refresh_ev"):
        _cmp_offline_run.clear()
        st.rerun()

    n_trials = int(run.config.get("trials", 0))
    st.caption(f"**{by[rid]['title']}** · zone **{ds}** · {n_trials} trials · measured with the reference library on "
               f"one pinned core. An update changes records chosen at random, keeping their size; "
               f"{stats_note(n_trials)}")
    st.markdown(":green-badge[Measured] re-sign cost, growth and checks · :violet-badge[Derived] response size")

    cost = evolution.update_cost(mtl, direct)
    cost = cost[cost.dataset == ds].set_index("k")
    c = cost.loc[k]
    m = st.columns(4)
    m[0].metric("Re-sign after an update · MTL", compare.fmt_seconds(c.mtl_round_ns / 1e9),
                f"1 signature, {k} record(s) changed", delta_color="off", delta_arrow="off",
                help=gl.tip("resign_time"))
    m[1].metric("Re-sign after an update · SLH-DSA", compare.fmt_seconds(c.direct_round_ns / 1e9),
                f"{k} signature(s), one per changed record", delta_color="off", delta_arrow="off",
                help=gl.tip("resign_time"))
    ratio = compare_hub.fmt_x(c.ratio)  # direct ÷ MTL: above 1 means MTL needs less time
    m[2].metric("MTL re-sign time vs SLH-DSA", ratio.replace("lower", "less time").replace("higher", "more time"),
                f"per-run ratio {c.ratio:.3g} [{c.ratio_lo:.3g}, {c.ratio_hi:.3g}]", delta_color="off",
                delta_arrow="off", help="How many times longer SLH-DSA takes than MTL to re-sign after one update, "
                "with the interval over the runs. Above 1 means MTL needs less time.")
    m[3].metric("Resolver validation after an update", compare.fmt_seconds(c.mtl_resolver_ns / 1e9),
                f"vs {compare.fmt_seconds(c.direct_resolver_ns / 1e9)} for {k} signature(s)", delta_color="off",
                delta_arrow="off", help="Time the resolver needs to validate the updated data (one signed ladder for "
                "MTL, the changed signatures for SLH-DSA).")
    if c.ratio_hi < 1.5 and c.ratio_lo > 0.67:
        st.warning(f"With {k} changed record(s) per update, MTL is **not faster to re-sign**: it still makes one "
                   "SLH-DSA signature per update, the same as SLH-DSA needs for a single changed record. Its gain "
                   "here is in response size and verification.")

    t_cost, t_bytes, t_cov, t_stale, t_growth, t_ladder = st.tabs(
        [gl.label("resign_time").replace("time", "cost"), "Response size", gl.label("stale_cache"), "Earlier signatures",
         "Zone growth", gl.label("mtl_internals")])
    g = evolution.growth(mtl)
    g = g[(g.dataset == ds) & (g.k == k)].sort_values("round")
    rounds = [int(r) for r in g["round"]]
    ages = [a for a in evolution.ages_in(mtl) if a > 0]

    with t_ladder:
        st.caption(gl.tip("mtl_internals"), help="Skip this tab unless you are reading the MTL draft.")
        c1, c2 = st.columns(2)
        r = c1.select_slider("Update", options=rounds, value=rounds[-1], key="cmp_ev_round")
        avail = [a for a in ages if a <= r] or [0]
        age = c2.select_slider("Updates since the resolver's cached ladder was signed", options=avail, value=avail[0],
                               key="cmp_ev_age")
        row = g[g["round"] == r].iloc[0]
        leaves = int(row.leaf_count)
        cached = int(g[g["round"] == r - age].iloc[0].leaf_count) if age else leaves
        parts = " + ".join(f"{s:,}" for s, _a, _b in evolution.rungs_of(leaves))
        st.markdown(f"**Update {r}:** {leaves:,} leaves = {parts} · {int(row.rung_count)} rung(s) · signed ladder "
                    f"**{compare_hub.fmt_value(row.ladder_bytes, 'B')}** · largest condensed signature "
                    f"**{int(row.condensed_bytes_max)} B**")
        st.plotly_chart(_ladder_figure(leaves, cached, age), width="stretch")
        st.caption("A ladder lists one hash per rung, and each rung is a complete binary subtree, so the rungs are the "
                   "binary breakdown of the leaf count. An RRset whose leaf lies inside a rung of the resolver's "
                   "cached ladder verifies with a condensed signature. A leaf appended after that ladder was signed "
                   "(orange) has no rung in it: that answer needs a fresh ladder, that is, a full signature.")

    with t_cov:
        st.caption(gl.tip("stale_cache"))
        cov = evolution.coverage_table(mtl)
        cov = cov[(cov.dataset == ds) & (cov.age > 0)]
        if cov.empty:
            st.info("This run has no stale-cache data.")
        else:
            piv = cov.pivot_table(index="k", columns="age", values="share") * 100
            fig = px.imshow(piv, text_auto=".1f", aspect="auto", color_continuous_scale="RdYlGn", zmin=0, zmax=100,
                            labels=dict(x="updates since the resolver last refreshed", y="records changed per update",
                                        color="% still short"),
                            title=f"Share of answers a stale resolver can still validate with a short signature, "
                                  f"{ds} zone")
            fig.update_yaxes(type="category")
            fig.update_xaxes(type="category")
            st.plotly_chart(_plot_cfg(fig, 340), width="stretch")
            st.caption("Measured for every record of the zone after each update. The missing share is the records changed "
                       "since the resolver's last refresh: those answers carry a full signature (about 8 KB, usually "
                       "over TCP). If changes are independent of popularity, the share of queries affected is the "
                       "same in expectation.")

    with t_cost:
        d = mtl[(mtl.dataset == ds) & (mtl["round"] >= 1)].assign(
            seconds=lambda x: (x.append_ns + x.ladder_sign_ns) / 1e9)
        a = d.groupby(["k", "round"]).seconds.median().reset_index().assign(method="MTL: 1 signature per update")
        dd = direct[direct["round"] >= 1].groupby(["k", "round"]).sign_ns.median().reset_index()
        b = dd.assign(seconds=dd.sign_ns / 1e9, method="SLH-DSA: 1 signature per changed record")
        both = pd.concat([a, b[["k", "round", "seconds", "method"]]], ignore_index=True)
        both["k"] = both["k"].map(lambda v: f"{int(v)} changed")
        fig = px.line(both, x="round", y="seconds", color="k", line_dash="method", markers=True, log_y=True,
                      title=f"Re-sign time per update, {ds} zone (log scale)")
        fig.update_layout(legend_title_text="", xaxis_title="update")
        st.plotly_chart(_plot_cfg(fig, 400), width="stretch")
        tab = cost.reset_index()
        st.dataframe(pd.DataFrame({
            "Records changed": tab.k, "MTL re-sign": [compare.fmt_seconds(v / 1e9) for v in tab.mtl_round_ns],
            "SLH-DSA re-sign": [compare.fmt_seconds(v / 1e9) for v in tab.direct_round_ns],
            "SLH-DSA ÷ MTL": [f"{r:.3g} [{lo:.3g}, {hi:.3g}]" for r, lo, hi in zip(tab.ratio, tab.ratio_lo, tab.ratio_hi)],
            "Interval coverage": [f"{x * 100:.0f}%" for x in tab.coverage]}), width="stretch", hide_index=True)
        st.caption("MTL makes one SLH-DSA signature per update however many records changed; SLH-DSA makes one per "
                   "changed record. The ratio is the median of the per-trial ratios with its interval. Producing the "
                   "short per-answer signatures is not part of the re-sign time.")

    with t_bytes:
        eb = evolution.effective_bytes(mtl)
        eb = eb[(eb.dataset == ds) & (eb.age > 0)]
        if eb.empty:
            st.info("This run has no stale-cache data.")
        else:
            eb = eb.assign(k=eb.k.map(lambda v: f"{int(v)} changed"), age=eb.age.map(lambda v: f"{int(v)} update(s) old"))
            fig = px.bar(eb, x="age", y="effective_bytes", color="k", barmode="group", text_auto=",.0f",
                         title=f"Signature bytes per answer, {ds} zone (derived)")
            fig.add_hline(y=7856, line_dash="dot", annotation_text="plain SLH-DSA: 7,856 B")
            fig.update_layout(xaxis_title="age of the resolver's cached data", yaxis_title="bytes per answer")
            st.plotly_chart(_plot_cfg(fig, 400), width="stretch")
            st.caption(":violet-badge[Derived] Measured short-signature size plus the stale share times a full signature. "
                       "An upper bound: a real resolver refreshes once and then covers the rest.")

    with t_stale:
        s = evolution.stale_by_round(mtl)
        s = s[(s.dataset == ds) & (s.k == k)]
        if s.empty or s.stale_tested.sum() == 0:
            st.info("No data.")
        else:
            fig = px.line(s, x="round", y=s.share * 100, markers=True, title=f"Earlier signatures that still validate "
                          f"against the newest data, {ds} zone, {k} changed per update")
            fig.update_layout(yaxis_title="% still valid", xaxis_title="update", yaxis_range=[0, 102])
            st.plotly_chart(_plot_cfg(fig, 360), width="stretch")
            st.caption("As the zone grows, signatures issued earlier no longer match the newest MTL ladder and must be "
                       "reissued for resolvers that fetch it. When the record count reaches a power of two, every "
                       "earlier signature is affected.")

    with t_growth:
        gg = evolution.growth(mtl)
        gg = gg[(gg.dataset == ds)].assign(k=lambda x: x.k.map(lambda v: f"{int(v)} changed"))
        c1, c2 = st.columns(2)
        c1.plotly_chart(_plot_cfg(px.line(gg, x="round", y="leaf_count", color="k", markers=True,
                                          title="Records in the series"), 320), width="stretch")
        c2.plotly_chart(_plot_cfg(px.line(gg, x="round", y="ladder_bytes", color="k", markers=True,
                                          title="Signed ladder (bytes)"), 320), width="stretch")
        c1.plotly_chart(_plot_cfg(px.line(gg, x="round", y="condensed_bytes_max", color="k", markers=True,
                                          title="Largest short signature (bytes)"), 320), width="stretch")
        c2.plotly_chart(_plot_cfg(px.line(gg, x="round", y="rung_count", color="k", markers=True,
                                          title="Rungs in the ladder"), 320), width="stretch")
        st.caption("Every change adds an entry and none leave, so the series only grows. The signed ladder follows the "
                   "number of rungs (the 1 bits in the entry count); the short signature grows by 16 bytes each "
                   "time the count reaches a power of two.")


def stats_note(n_trials: int) -> str:
    return evolution.stats.ci_label(n_trials) + "."


def compare_page(h: driver.LabHandle, stt: dict) -> None:
    _page_header("compare", h, stt)
    sources = {s: len(_cmp_catalogue(s, h)) for s in CMP_SOURCES}
    available = [s for s, n in sources.items() if n]
    view = sb(st.segmented_control, "cmp_view", "algorithms", "What to compare", options=list(CMP_VIEWS),
              format_func=CMP_VIEWS.get) or "algorithms"
    if view == "evolution":
        st.caption("What a zone change costs: records are added or modified, the zone is re-signed, and resolvers may "
                   "still hold earlier signatures. MTL against signing every changed record. Offline data only.")
        _cmp_evolution(h)
        return
    default_src = "offline" if sources["offline"] else ("live" if sources["live"] else "offline")
    source = sb(st.segmented_control, "cmp_src", default_src, "Data from", options=list(CMP_SOURCES),
                format_func=lambda s: f"{CMP_SOURCES[s]} ({sources[s]})") or default_src
    st.caption(f"{CMP_SOURCE_NOTE[source]} Offline and simulated results use different libraries and versions, "
               "so they are never drawn together.")
    cat = _cmp_catalogue(source, h)
    if not cat:
        others = [CMP_SOURCES[s] for s in available if s != source]
        if source == "offline":
            st.info("No offline run yet. Start one in the Offline benchmark.")
            st.button("Go to the Offline benchmark", on_click=_go, args=("offline", "run"), key="cmp_to_bench_off")
        elif source == "live":
            st.info("No finished simulated benchmark yet. Run one in step 4 of the Simulated lab.")
            st.button("Go to the Simulated benchmark", on_click=_go, args=("simulated", "benchmark"),
                      key="cmp_to_bench")
        else:
            st.info("No saved lab run with results yet. Querying the lab (step 3) and the benchmark (step 4) "
                    "create them.")
        if others:
            st.caption("Data is available from: " + ", ".join(others) + ".")
        return
    if view == "runs" and len(cat) < 2:
        st.info("Comparing runs needs at least two runs from this source. Repeat the benchmark (a reproducibility "
                "check), or run it on another stack profile or machine.")
        view = "algorithms"
    pick = _cmp_pickers(view, source, cat, h)
    if pick is None:
        return
    if view == "runs":
        _cmp_runs(source, pick)
    else:
        _cmp_algorithms(source, pick, h)
