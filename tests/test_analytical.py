from cipherops import analytical


def test_rung_counts_match_draft_table():
    # binary rung strategy: one rung per set bit of N, N = 1..19
    expected = [1, 1, 2, 1, 2, 2, 3, 1, 2, 2, 3, 2, 3, 3, 4, 1, 2, 2, 3]
    assert [analytical.rung_count(n) for n in range(1, 20)] == expected


def test_condensed_formula():
    assert analytical.max_condensed_signature(16, 1) == 28 + 48
    assert analytical.max_condensed_signature(16, 1024) == 28 + 48 + 16 * 10
    assert analytical.max_condensed_signature(16, 1025) == 28 + 48 + 16 * 10


def test_ladder_formula():
    assert analytical.max_signed_ladder(16, 8, 7856) == 8 + 64 + 32 * 3 + 7856
    assert analytical.max_full_signature(16, 8, 7856) == \
        analytical.max_condensed_signature(16, 8) + analytical.max_signed_ladder(16, 8, 7856)


def test_log_helpers():
    assert [analytical.floor_log2(n) for n in (1, 2, 3, 4, 1023, 1024)] == [0, 1, 1, 2, 9, 10]
    assert [analytical.ceil_log2(n) for n in (1, 2, 3, 4, 1023, 1024, 1025)] == [0, 1, 2, 2, 10, 10, 11]


def test_fragments_and_segments():
    assert analytical.udp_fragments(1232) == 1
    assert analytical.udp_fragments(1473) == 2
    assert analytical.udp_fragments(4096) == 3
    assert analytical.tcp_segments(1460) == 1
    assert analytical.tcp_segments(1461) == 2
