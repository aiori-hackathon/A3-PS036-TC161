#!/usr/bin/env python3
"""Command line for the images a stack needs: status, build, pull (what the dashboard's Setup does).

    python scripts/images.py status --stack latest
    python scripts/images.py build  --stack latest --mode offline
    python scripts/images.py pull   --stack latest --mode all --registry ghcr.io/org/benchmark
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from live_lab.images import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
