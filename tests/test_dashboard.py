"""Headless smoke test of the Streamlit dashboard against the newest completed run."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_renders_without_exceptions():
    testing = pytest.importorskip("streamlit.testing.v1")
    from cipherops import results

    if not results.list_runs(ROOT / "results"):
        pytest.skip("no completed run under results/runs")
    at = testing.AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=180).run()
    assert not at.exception, [e.message for e in at.exception]
    assert len(at.tabs) == 6 + 3

    for metric in ("sign_total_ns", "verify_warm_total_ns", "sign_per_rrset_ns", "verify_per_rrset_ns",
                   "verify_full_median_ns"):
        at.radio(key="metric").set_value(metric).run()
        assert not at.exception, (metric, [e.message for e in at.exception])

    query = at.selectbox(key="demo_query")
    for choice in query.options[:2] + query.options[-1:]:
        for limit in (1232, 4096):
            at.selectbox(key="demo_query").set_value(choice)
            at.radio(key="demo_limit").set_value(limit).run()
            assert not at.exception, (choice, limit, [e.message for e in at.exception])


def test_dashboard_packet_viewer_on_a_recorded_live_run():
    testing = pytest.importorskip("streamlit.testing.v1")
    from cipherops import results

    live_runs = [p for p in results.list_runs(ROOT / "results") if (p / "live.json").exists()]
    if not live_runs:
        pytest.skip("no run with a recorded live run")
    at = testing.AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=180).run()
    at.sidebar.selectbox[0].set_value(live_runs[0]).run()
    assert not at.exception, [e.message for e in at.exception]
    for i in range(len(at.selectbox(key="pv_mode").options)):
        at.selectbox(key="pv_mode").select_index(i)
        at.radio(key="pv_axis").set_value("time").run()
        assert not at.exception, (i, [e.message for e in at.exception])
        last = len(at.selectbox(key="pv_lookup").options) - 1
        at.selectbox(key="pv_lookup").select_index(last).run()
        assert not at.exception, (i, [e.message for e in at.exception])
        assert at.dataframe, "packet table missing"


def test_dashboard_sends_real_queries_to_a_running_live_server():
    testing = pytest.importorskip("streamlit.testing.v1")
    if not (ROOT / "results" / "live-server" / "server.json").exists():
        pytest.skip("no live server running (scripts/live_server.ps1)")
    at = testing.AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=180).run()
    for first in (False, True):
        at.checkbox(key="send_first").set_value(first)
        at.selectbox(key="send_query").select_index(2)
        next(b for b in at.button if b.label == "Send real queries").click().run()
        assert not at.exception, [e.message for e in at.exception]
        for i in range(len(at.selectbox(key="send_pick").options)):
            at.selectbox(key="send_pick").select_index(i)
            at.radio(key="send_axis").set_value("time").run()
            assert not at.exception, (first, i, [e.message for e in at.exception])
