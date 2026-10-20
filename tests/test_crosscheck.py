import os
import shutil

import dns.rdatatype
import dns.zone
import pytest

from cipherops import dnswire
from cipherops.codepoints import MODES
from cipherops.crosscheck import signed_zone_text
from cipherops.workload import build_zone


@pytest.mark.parametrize("key,size", [("slh-dsa", 7856), ("slh-dsa-mtl", 204), ("ecdsa-p256", 64)])
def test_signed_zone_parses_and_every_rrset_is_signed(key, size):
    zone = build_zone("cx", 15, 3)
    ladder = os.urandom(8000) if key == "slh-dsa-mtl" else None
    sigs = dnswire.Signatures(MODES[key], [os.urandom(size) for _ in zone.items], ladder)
    z = dns.zone.from_text(signed_zone_text(zone, sigs), origin=zone.origin, relativize=False,
                           check_origin=True)
    signed = sum(1 for _, rds in z.iterate_rdatasets() if rds.rdtype == dns.rdatatype.RRSIG for _ in rds)
    assert signed == len(zone.items)
    lengths = {len(rd.signature) for _, _, rd in z.iterate_rdatas() if rd.rdtype == dns.rdatatype.RRSIG}
    assert lengths == {size + (1 if key == "slh-dsa-mtl" else 0)}


@pytest.mark.harness
@pytest.mark.skipif(shutil.which("nsd") is None, reason="nsd not installed (run inside Docker)")
def test_nsd_serves_model_sized_responses(tmp_path):
    from cipherops.crosscheck import NSD, _tcp

    zone = build_zone("cx", 15, 3)
    sigs = dnswire.Signatures(MODES["ecdsa-p256"], [os.urandom(64) for _ in zone.items])
    b = dnswire.ResponseBuilder(zone, sigs)
    it = next(i for i in zone.items if i.record_class == "small")
    with NSD(signed_zone_text(zone, sigs), zone.origin.to_text()):
        q = b.query(it.owner, it.rdtype, 4096, False)
        _, r = b.response("positive", it.owner, it.rdtype, 4096)
        assert len(_tcp(q.to_wire())) == dnswire.wire_len(r)
