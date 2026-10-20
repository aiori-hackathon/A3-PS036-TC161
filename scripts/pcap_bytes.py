"""Sum DNS payload bytes in the live-mode pcaps and compare with what the client counted.

Usage: python scripts/pcap_bytes.py results/runs/<run>
Pcaps hold repeat 0 of each cell: live-<dataset>-<mode>-<profile>-<udp>.pcap
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cipherops import pcap  # noqa: E402


def main(run: str) -> None:
    run_dir = Path(run)
    live = pd.read_csv(run_dir / "live.csv")
    live = live[live.repeat == 0].groupby(["dataset", "mode", "profile", "udp_limit"]).dns_bytes.sum()
    for path in sorted((run_dir / "pcap").glob("live-*.pcap")):
        stem = path.stem[len("live-"):]
        dataset, rest = stem.split("-", 1)
        mode, profile, udp = rest.rsplit("-", 2)
        b = pcap.payload_bytes(pcap.read(path))
        client = live.get((dataset, mode, profile, int(udp)), float("nan"))
        print(f"{dataset:<7} {mode:<12} {profile:<6} {udp:>5}  packets {b['packets']:>6} (fragments {b['fragments']:>4})"
              f"  udp {b['udp_bytes']:>9,}  tcp {b['tcp_bytes']:>9,}  pcap {b['udp_bytes'] + b['tcp_bytes']:>10,}"
              f"  client {client:>10,.0f}")


if __name__ == "__main__":
    main(sys.argv[1])
