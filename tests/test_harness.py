"""Correctness gates on the compiled harness + reference library (run in Docker)."""

import os
from pathlib import Path

import pytest

from cipherops import analytical
from cipherops.codepoints import MODES, MTL_TYPE_CONDENSED, SLH_DSA_SHA2_128S_SIG_BYTES
from cipherops.harness import HARNESS, load_signatures, run_trial
from cipherops.workload import build_zone, read_blob_set, signing_messages, write_messages

pytestmark = [
    pytest.mark.harness,
    pytest.mark.skipif(not os.path.exists(HARNESS), reason="mtlbench not built (run inside Docker)"),
]


@pytest.fixture(scope="module")
def zone():
    return build_zone("gate", 20, 99)


def _run(zone, key, tmp_path, full_verify=8):
    mode = MODES[key]
    msgs, _ = signing_messages(zone, mode)
    path = tmp_path / f"{key}.bin"
    write_messages(path, msgs)
    return msgs, run_trial(mode, path, tmp_path / key, full_verify)


@pytest.mark.parametrize("key", list(MODES))
def test_every_signature_verifies_and_tampering_is_rejected(zone, key, tmp_path):
    msgs, res = _run(zone, key, tmp_path)
    assert res["exit_code"] == 0, res["stderr_tail"]
    assert res["n_messages"] == len(msgs)
    assert sum(res["failures"].values()) == 0
    assert all(res["tamper"].values())


def test_direct_signature_length(zone, tmp_path):
    _, res = _run(zone, "slh-dsa", tmp_path)
    assert set(res["signature_bytes"]) == {SLH_DSA_SHA2_128S_SIG_BYTES}


def test_mtl_sizes_against_draft_formulas(zone, tmp_path):
    msgs, res = _run(zone, "slh-dsa-mtl", tmp_path)
    n = len(msgs)
    assert max(res["condensed_bytes"]) <= analytical.max_condensed_signature(16, n)
    assert res["signed_ladder_bytes"] > SLH_DSA_SHA2_128S_SIG_BYTES
    assert res["signed_ladder_bytes"] <= analytical.max_signed_ladder(16, n, SLH_DSA_SHA2_128S_SIG_BYTES)


@pytest.mark.parametrize("key", list(MODES))
def test_public_key_matches_dnskey_field_length(zone, key, tmp_path):
    _, res = _run(zone, key, tmp_path)
    assert res["public_key_bytes"] == MODES[key].public_key_len


def test_artifacts_round_trip(zone, tmp_path):
    msgs, res = _run(zone, "slh-dsa-mtl", tmp_path)
    sigs = load_signatures(MODES["slh-dsa-mtl"], tmp_path / "slh-dsa-mtl", False)
    assert len(sigs.sigs) == len(msgs)
    assert len(sigs.signed_ladder) == res["signed_ladder_bytes"]
    assert sigs.rrsig_field(0)[0] == MTL_TYPE_CONDENSED
    assert [len(s) for s in read_blob_set(tmp_path / "slh-dsa-mtl" / "condensed.bin")] == res["condensed_bytes"]
