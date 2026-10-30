"""Algorithm and run comparison shared by the offline harness and the live lab."""

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "dashboard"), str(ROOT / "src")]

from cipherops import results  # noqa: E402
from live_lab import compare, compare_hub as hub  # noqa: E402


def _offline_run(sign_scale: float = 1.0, stack: str = "latest") -> results.Run:
    trials = []
    for t in range(6):
        for mode, sign, verify, sig_b, limited in (
                ("slh-dsa-mtl", 1.0, 0.1, 156, False), ("slh-dsa", 10.0, 1.0, 7856, True), ("ed25519", 0.01, 0.02, 64, False)):
            row = {"dataset": "small", "mode": mode, "trial": t, "n_signed": 10 if limited else 100,
                   "dataset_rrsets": 100, "limited": limited, "exit_code": 0, "failures_total": 0,
                   "tamper_all_rejected": True, "sign_total_ns": sign * 1e9 * sign_scale * (1 + 0.01 * t),
                   "verify_warm_total_ns": verify * 1e9, "sign_per_rrset_ns": 1.0, "verify_per_rrset_ns": 1.0,
                   "signature_bytes_median": sig_b, "signature_material_bytes": 100 * 156 + 8000}
            if limited:
                row["sign_total_extrapolated_ns"] = row["sign_total_ns"] * 10
                row["verify_warm_total_extrapolated_ns"] = verify * 1e10
            trials.append(row)
    frames = {
        "trials": pd.DataFrame(trials),
        "datasets": pd.DataFrame([{"dataset": "small", "rrsets": 100}]),
        "workload": pd.DataFrame([{"dataset": "small", "mode": m, "udp_limit": 1232, "dns_bytes": b, "truncations": n,
                                   "tcp_exchanges": n} for m, b, n in (("slh-dsa-mtl", 5e4, 3), ("slh-dsa", 1.5e6, 100), ("ed25519", 5e4, 2))]),
        "cache": pd.DataFrame([{"dataset": "small", "mode": m, "scope": "resolver", "kind": "measured",
                                "signature_material_bytes": b} for m, b in (("slh-dsa-mtl", 4e4), ("slh-dsa", 1.4e6), ("ed25519", 1e4))]),
    }
    config = {"modes": ["slh-dsa-mtl", "slh-dsa", "ed25519"], "datasets": [{"name": "small", "n_names": 50}], "trials": 6}
    env = {"stack": stack, "software": {"mtl_reference_commit": "7cef019aaaa", "liboqs_tag": "0.16.0"},
           "hardware": {"cpu_model": "Test CPU", "cpu_affinity": [2]}}
    return results.Run(Path("."), config, env, {}, {"noise_floor": [
        {"dataset": "small", "metric": "signing", "band": 0.03}]}, frames)


def test_offline_frame_uses_the_extrapolated_total_for_a_capped_direct_run():
    f = hub.offline_frame(_offline_run(), "small").set_index("key")
    assert f.loc["slh-dsa", "sign_basis"] == "extrapolated" and f.loc["slh-dsa-mtl", "sign_basis"] == "measured"
    assert f.loc["slh-dsa", "sign_s"] == pytest.approx(10 * 10.0 * 1.025, rel=0.02)
    assert f.loc["slh-dsa", "size_bytes"] == 7856 * 100  # fixed-length signature times every RRset
    assert f.loc["slh-dsa-mtl", "size_bytes"] == 100 * 156 + 8000
    assert f.loc["slh-dsa", "wire_bytes"] == 1.5e6 and f.loc["slh-dsa-mtl", "cache_bytes"] == 4e4
    assert list(f.index) == ["ed25519", "slh-dsa", "slh-dsa-mtl"]  # classical first, MTL last


def test_algorithm_table_reports_the_paired_ratio_for_timings_and_ratios_of_values_otherwise():
    run = _offline_run()
    frame = hub.offline_frame(run, "small")
    paired = hub.offline_paired(run, "small", "slh-dsa")
    t = hub.algorithm_table(frame, "slh-dsa", paired).set_index("Algorithm")
    mtl = t.loc["SLH-DSA + MTL"]
    assert "lower" in mtl["Signing time: vs SLH-DSA"] and "[" in mtl["Signing time: vs SLH-DSA"]
    assert mtl["Response size: vs SLH-DSA"] == "30× lower"
    assert t.loc["SLH-DSA  (baseline)", "Signing time: vs SLH-DSA"] == ""


def test_paired_ratios_point_the_right_way():
    p = hub.offline_paired(_offline_run(), "small", "slh-dsa").set_index(["key", "col"])
    assert p.loc[("slh-dsa-mtl", "sign_s"), "ratio"] == pytest.approx(10.0 * 10)  # extrapolated direct / MTL
    assert p.loc[("ed25519", "sign_s"), "extrapolated"]


def test_fmt_x_and_the_interval_wording():
    assert hub.fmt_x(12.0) == "12× lower" and hub.fmt_x(0.25) == "4.0× higher" and hub.fmt_x(1.02) == "same"
    assert hub.fmt_x(float("nan")) == "—"
    assert hub._interval(11.0, 13.0) == "[11×, 13×]"
    assert hub._interval(0.4, 0.5) == "[2.0×, 2.5×]"
    assert "no difference" in hub._interval(0.9, 1.2)


def test_live_frame_maps_a_suite_frame_onto_the_shared_columns():
    df = pd.DataFrame({"scenario": ["slh-dsa", "mtl"], "sign_s": [100.0, 5.0], "sign_lo_s": [90.0, 4.0],
                       "sign_hi_s": [110.0, 6.0], "sign_trials": [3, 3], "verify_s": [1.0, 0.5],
                       "signed_bytes": [4e6, 4e5], "upstream_bytes": [4e6, 3e5], "truncated": [10, 1],
                       "tcp_conns": [10, 1], "cache_rrset": [2e6, 2e5], "cold_ms": [9.0, 8.0], "warm_ms": [1.0, 1.0]})
    f = hub.live_frame(df)
    assert list(f.columns) == hub.STD_COLUMNS and set(f.sign_basis) == {"estimated"}
    x = hub.vs_baseline(f, "slh-dsa").set_index("key")
    assert x.loc["mtl", "sign_s"] == pytest.approx(20.0) and x.loc["mtl", "wire_bytes"] == pytest.approx(4e6 / 3e5)
    assert hub.live_frame(pd.DataFrame()).empty


def test_run_ratios_flag_what_stays_within_the_noise_band():
    a = hub.offline_frame(_offline_run(1.0), "small")
    b = hub.offline_frame(_offline_run(1.02), "small")
    r = hub.run_ratios(a, b, {"sign_s": 0.03}).set_index(["key", "col"])
    assert r.loc[("slh-dsa-mtl", "sign_s"), "ratio"] == pytest.approx(1.02)
    assert bool(r.loc[("slh-dsa-mtl", "sign_s"), "within_noise"])
    assert r.loc[("slh-dsa-mtl", "size_bytes"), "within_noise"] is None
    far = hub.run_ratios(a, hub.offline_frame(_offline_run(1.5), "small"), {"sign_s": 0.03}).set_index(["key", "col"])
    assert not bool(far.loc[("slh-dsa-mtl", "sign_s"), "within_noise"])


def test_comparability_notes_name_what_differs():
    a = hub.offline_facts(_offline_run().config, _offline_run().environment)
    same = hub.comparability_notes(a, dict(a))
    assert len(same) == 1 and "reproducibility" in same[0]
    other = hub.offline_facts(_offline_run(stack="draft06").config, {**_offline_run().environment, "stack": "draft06"})
    notes = " ".join(hub.comparability_notes(a, other))
    assert "Different stacks" in notes and "latest" in notes and "draft06" in notes
    diff = hub.facts_diff(a, other).set_index("Item")
    assert not diff.loc["Stack profile", "Same"] and diff.loc["CPU", "Same"]


def test_live_facts_read_the_suite_provenance():
    suite = {"trials": 5, "sizes": [50, 500], "provenance": {
        "stack": "draft06", "stack_pins": {"MTL_COMMIT": "93b05ef73dab", "LIBOQS_TAG": "0.14.0"},
        "cpu_model": "X", "git_commit": "abcdef1234567890", "git_dirty": True, "cpu_governor": "powersave"}}
    f = hub.live_facts(suite)
    assert f["Stack profile"] == "draft06" and f["MTL commit"] == "93b05ef73d" and f["liboqs"] == "0.14.0"
    assert f["Code version"].endswith("(dirty)") and f["Zone sizes"] == "50, 500"


def test_catalogue_lists_offline_runs_newest_first(tmp_path):
    for name, stack in (("20260101T000000Z-a", "latest"), ("20260102T000000Z-b", "draft06")):
        d = tmp_path / "runs" / name
        d.mkdir(parents=True)
        (d / "summary.json").write_text("{}")
        (d / "config.yaml").write_text("run_name: r\ntrials: 4\ndatasets: [{name: small, n_names: 5}]\n")
        (d / "environment.json").write_text('{"stack": "%s"}' % stack)
    cat = hub.offline_catalogue(tmp_path)
    assert [c["id"] for c in cat] == ["20260102T000000Z-b", "20260101T000000Z-a"]
    assert cat[0]["stack"] == "draft06" and cat[0]["trials"] == 4 and "4 trials" in cat[0]["title"]
    assert compare.order_scenarios(["mtl", "rsa"]) == ["rsa", "mtl"]


def _synthetic_suite(tmp_path, monkeypatch, stack="draft06", sign_scale=1.0):
    """A finished suite on disk: signing trials, full signing, one saved resolver run, and suite.json."""
    import json

    eco = tmp_path / "eco"
    monkeypatch.setattr(compare, "ECO", eco)
    run_dir = eco / "results" / "tag" / "run1"
    run_dir.mkdir(parents=True, exist_ok=True)
    scenarios = {"slh-dsa": (7856, 100.0, 4e6), "mtl": (156, 5.0, 3e5)}
    summary = []
    for sc, (_sig, _sign, up) in scenarios.items():
        for pas, ms in (("cold", 9.0), ("warm", 1.0)):
            summary.append({"scenario": sc, "profile": "lan", "udp_limit": 1232, "pass": pas, "median_ms": ms,
                            "upstream_bytes": up, "upstream_queries": 10, "upstream_truncated": 3,
                            "upstream_tcp_connections": 3, "mem.cache.rrset": up / 2, "trials": 3, "valid_trials": 3,
                            "failed": 0})
    pd.DataFrame(summary).to_csv(run_dir / "summary.csv", index=False)
    suite_dir = tmp_path / f"suite-{stack}"
    suite_dir.mkdir()
    rows, full = [], []
    for t in (1, 2, 3):
        for sc, (_sig, sign, _up) in scenarios.items():
            for names, rr in ((20, 50), (5, 14)):
                rows.append({"scenario": sc, "trial": t, "names": names, "rrsigs": rr, "sign_s": sign * rr * sign_scale / 50,
                             "verify_s": 0.2, "algorithm": sc, "signed_bytes": 1})
    for sc, (_sig, sign, _up) in scenarios.items():
        full.append({"size": 50, "scenario": sc, "rrsig_count": 64, "sign_seconds": sign, "verify_seconds": 0.2,
                     "signed_bytes": 9e5, "algorithm": "SLH-DSA-MTL" if sc == "mtl" else "SLH-DSA", "origin": "z."})
    pd.DataFrame(rows).to_csv(suite_dir / "signing_trials_50.csv", index=False)
    pd.DataFrame(full).to_csv(suite_dir / "signing_full.csv", index=False)
    (suite_dir / "suite.json").write_text(json.dumps({
        "trials": 3, "sizes": [50], "stack": stack, "sizes_detail": {"50": {"tag": "tag", "run_folder": "run1"}},
        "provenance": {"stack": stack, "stack_pins": {"MTL_COMMIT": "93b05ef73dab", "LIBOQS_TAG": "0.14.0"},
                       "cpu_model": "Test CPU", "git_commit": "abc123", "git_dirty": False}}))
    return suite_dir


def test_live_suite_reduces_to_the_same_frame_and_table_as_an_offline_run(tmp_path, monkeypatch):
    d = _synthetic_suite(tmp_path, monkeypatch)
    entry = hub.live_suite_entry({"id": "s1", "path": d, "title": "synthetic"})
    assert entry["stack"] == "draft06" and entry["datasets"] == [50] and entry["facts"]["liboqs"] == "0.14.0"
    sd = compare.load_suite(d)
    f = hub.live_frame(compare.suite_frame(sd, 50, "lan", 1232))
    assert list(f.columns) == hub.STD_COLUMNS and set(f.key) == {"slh-dsa", "mtl"}
    row = f.set_index("key")
    assert row.loc["mtl", "wire_bytes"] == 3e5 and row.loc["slh-dsa", "cache_bytes"] == 2e6
    assert row.loc["mtl", "cold_ms"] == 9.0 and set(f.sign_basis) == {"estimated"}
    t = hub.algorithm_table(f, "slh-dsa").set_index("Algorithm")
    assert t.loc["SLH-DSA + MTL", "Signing time: vs SLH-DSA"].endswith("lower")
    assert "Cache size" in t.columns


def test_offline_bundle_packs_the_report_and_tables(tmp_path):
    import io
    import zipfile
    run = tmp_path / "demo"
    run.mkdir()
    (run / "report.html").write_text("<html>report</html>", encoding="utf-8")
    (run / "report.md").write_text("# report", encoding="utf-8")
    (run / "trials.csv").write_text("mode\nslh-dsa\n", encoding="utf-8")
    (run / "ops.parquet").write_bytes(b"too-large-to-offer")
    (run / "figures").mkdir()
    (run / "figures" / "signing.png").write_bytes(b"\x89PNG")
    with zipfile.ZipFile(io.BytesIO(hub.offline_bundle(run))) as zf:
        assert set(zf.namelist()) == {"report.html", "report.md", "trials.csv", "figures/signing.png"}
        assert zf.read("report.html") == b"<html>report</html>"


def test_two_live_suites_on_different_stacks_are_flagged(tmp_path, monkeypatch):
    a = hub.live_suite_entry({"id": "a", "path": _synthetic_suite(tmp_path, monkeypatch, "draft06"), "title": "a"})
    b = hub.live_suite_entry({"id": "b", "path": _synthetic_suite(tmp_path, monkeypatch, "latest"), "title": "b"})
    assert "Different stacks" in " ".join(hub.comparability_notes(a["facts"], b["facts"]))
