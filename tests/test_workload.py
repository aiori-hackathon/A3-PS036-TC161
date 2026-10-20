import base64

import dns.dnssec
import dns.rdatatype
import dns.rrset
import pytest

from cipherops import workload
from cipherops.codepoints import MODES
from cipherops.workload import build_zone, rrsig_template, signing_messages


def test_zone_is_deterministic_and_sized():
    a = build_zone("t", 40, 11)
    b = build_zone("t", 40, 11)
    assert [it.rrset.to_text() if it.rrset else "DNSKEY" for it in a.items] == \
           [it.rrset.to_text() if it.rrset else "DNSKEY" for it in b.items]
    assert len(a.items) == 2 * 40 + 5


def test_items_in_canonical_order():
    z = build_zone("t", 30, 3)
    keys = [(it.owner, it.rdtype) for it in z.items]
    assert keys == sorted(keys)


def test_record_classes_present():
    z = build_zone("t", 400, 5)
    classes = {it.record_class for it in z.items}
    assert {"small", "medium", "large", "nsec3", "apex"} <= classes


def test_nsec3_chain_is_closed():
    z = build_zone("t", 25, 9)
    hashes = z.nsec3_sorted_hashes()
    assert len(hashes) == 26
    nexts = {base64.b32hexencode(rr[0].next).decode().rstrip("=") for rr in z.nsec3_by_hash.values()}
    assert nexts == set(hashes)


def test_signing_input_validates_with_standard_validator(monkeypatch):
    """Our RFC 4034 signing input is exactly what dnspython's validator checks."""
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    from cryptography.hazmat.primitives import serialization

    key = ed.Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(workload, "placeholder_public_key", lambda seed, mode: pub)

    mode = MODES["ed25519"]
    z = build_zone("t", 12, 4)
    msgs, _ = signing_messages(z, mode)
    keys = {z.origin: z.dnskey_rrset(mode)}
    tag = z.key_tag(mode)
    for msg, (_, rrset) in zip(msgs, z.rrsets(mode)):
        rrsig = rrsig_template(rrset, mode, tag, z.origin, key.sign(msg))
        dns.dnssec.validate_rrsig(rrset, rrsig, keys, now=workload.INCEPTION + 10)


def test_messages_differ_only_by_mode_header():
    z = build_zone("t", 10, 2)
    a, _ = signing_messages(z, MODES["slh-dsa"])
    b, _ = signing_messages(z, MODES["slh-dsa-mtl"])
    assert len(a) == len(b)
    non_dnskey = [i for i, (it, _) in enumerate(z.rrsets(MODES["slh-dsa"])) if it.rdtype != dns.rdatatype.DNSKEY]
    for i in non_dnskey:
        assert a[i][18:] == b[i][18:]
        assert a[i][2] != b[i][2]  # algorithm field
