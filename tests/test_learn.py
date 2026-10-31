"""The Learn explainer stays self-contained and its size figures agree with the draft formulas in the code."""

import re
from pathlib import Path

from cipherops import analytical
from cipherops.codepoints import MTL_SECURITY_PARAMETER_N, SLH_DSA_SHA2_128S_SIG_BYTES

PAGE = Path(__file__).resolve().parents[1] / "docs" / "learn" / "why-dnssec-needs-mtl.html"
N_RRSETS = 10_005  # the large dataset of the full run
DNS_OVERHEAD = 113  # one signed A answer, before the signature


def test_page_needs_no_network():
    html = PAGE.read_text(encoding="utf-8")
    assert not re.search(r"""(?:src|href)\s*=\s*["']?https?://""", html)
    assert "@import" not in html and "fonts.googleapis" not in html


def test_size_figures_match_the_formulas():
    html = PAGE.read_text(encoding="utf-8")
    cond = analytical.max_condensed_signature(MTL_SECURITY_PARAMETER_N, N_RRSETS)
    full = cond + analytical.max_signed_ladder(MTL_SECURITY_PARAMETER_N, N_RRSETS, SLH_DSA_SHA2_128S_SIG_BYTES)
    assert cond == 284 and f"{cond} bytes in all" in html and f"condensed ≈ {cond} B" in html
    assert f"≈{DNS_OVERHEAD + cond} B per signed answer" in html
    assert f"'SLH-DSA + MTL','condensed',{DNS_OVERHEAD + cond}" in html
    assert f"'Plain SLH-DSA','per answer',{DNS_OVERHEAD + SLH_DSA_SHA2_128S_SIG_BYTES}" in html
    assert f"{full / 1000:.1f} KB" in html
    assert "14 hashes" in html and 60 + 14 * MTL_SECURITY_PARAMETER_N == cond


def test_companion_text_agrees_with_the_page():
    text = (PAGE.parent / "README.md").read_text(encoding="utf-8")
    assert "284 bytes" in text and "7,856" in text and "8.7 KB" in text and "why-dnssec-needs-mtl.html" in text


def test_ops_walkthrough_is_self_contained():
    html = (PAGE.parent / "mtl-dnssec-ops-walkthrough.html").read_text(encoding="utf-8")
    assert not re.search(r"""<(?:link|script|img|iframe)[^>]+(?:src|href)\s*=\s*["']?https?://""", html)
    assert "fonts.googleapis" not in html and "@import" not in html
    assert "7,856" in html  # the SLH-DSA-SHA2-128s signature size, as in the main explainer
    # external references are links to sources only, and open outside the dashboard frame
    assert all('target="_blank"' in a for a in re.findall(r"<a [^>]*href=\"https?://[^>]*>", html))


def test_explainer_has_the_ietf_status_scene_with_dated_drafts():
    html = PAGE.read_text(encoding="utf-8")
    for draft in ("draft-harvey-cfrg-mtl-mode-09", "draft-fregly-dnsop-slh-dsa-mtl-dnssec-06",
                  "draft-kaizer-dnsop-ml-dsa-mtl-dnssec-03", "draft-sheth-pqc-dnssec-strategy-02"):
        assert draft in html
    assert "30 October 2026" in html and "individual" in html.lower()


MEASURE = PAGE.parent / "what-we-measure.html"


def test_measure_page_is_self_contained():
    html = MEASURE.read_text(encoding="utf-8")
    assert not re.search(r"""(?:src|href)\s*=\s*["']?https?://""", html)
    assert "@import" not in html and "fonts.googleapis" not in html


def test_measure_page_sizes_match_the_formulas():
    html = MEASURE.read_text(encoding="utf-8")
    cond = analytical.max_condensed_signature(MTL_SECURITY_PARAMETER_N, N_RRSETS)
    full = cond + analytical.max_signed_ladder(MTL_SECURITY_PARAMETER_N, N_RRSETS, SLH_DSA_SHA2_128S_SIG_BYTES)
    rows = dict(re.findall(r"\['(\w+)','[^']*','[^']*',(\d+),", html))
    assert int(rows["slh"]) == DNS_OVERHEAD + SLH_DSA_SHA2_128S_SIG_BYTES
    assert int(rows["mtl"]) == DNS_OVERHEAD + cond
    assert int(rows["full"]) == DNS_OVERHEAD + full
    assert int(rows["ed"]) == DNS_OVERHEAD + 64 and int(rows["rsa"]) == DNS_OVERHEAD + 256
    assert f"≤ {cond} B" in html and f"{full / 1000:.1f} KB" in html and f"{full:,} B" in html


def test_measure_page_states_both_stacks_and_its_limits():
    html = MEASURE.read_text(encoding="utf-8")
    for needle in ("latest", "draft06", "FIPS 205", "SERVFAIL", "extrapolated", "never on one chart",
                   "contains no benchmark results"):
        assert needle in html, needle
    assert "what-we-measure.html" in (PAGE.parent / "README.md").read_text(encoding="utf-8")
