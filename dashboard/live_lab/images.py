"""The container images a stack needs, whether they are on this host, and how to get them.

Setup is only about this: make the pinned images available on the benchmark node, either by building them here or by
pulling them from a registry, and prove they match the stack profile. Signing, querying and measuring are benchmark
stages and live elsewhere.

Two ways of benchmarking need two sets of images, all built from docker/Dockerfile for one stack profile
(ecosystem/stacks/<stack>.env):

    offline    cipherops-bench:<stack>                                    the harness, one pinned core, no network
    simulated  cipherops-eco-{nsd,unbound,tools}:<stack>                  root/TLD/leaf servers + validating resolver

An image is "ok" only when the pins it was built from (its ``io.cipherops.*`` labels, or the ENV the older images
carry) equal the stack profile of this checkout. A pulled image that does not match is therefore never used.

Command line (same as the dashboard's Setup)::

    python scripts/images.py status --stack latest
    python scripts/images.py build  --stack latest --mode offline
    python scripts/images.py pull   --stack latest --mode all --registry ghcr.io/org/benchmark
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ECO = REPO / "ecosystem"
BUILD_FILE = REPO / "docker" / "build.yml"
DEFAULT_REGISTRY = os.environ.get("CIPHEROPS_REGISTRY", "ghcr.io/anithapriyanatarajan/benchmark")

# role -> image repository (compose service name == role)
REPOS = {
    "bench": "cipherops-bench",
    "nsd": "cipherops-eco-nsd",
    "unbound": "cipherops-eco-unbound",
    "tools": "cipherops-eco-tools",
}
ROLE_TITLE = {
    "bench": "Offline harness",
    "nsd": "Authoritative server (NSD)",
    "unbound": "Validating resolver (Unbound)",
    "tools": "Signer + client (ldns, dig, Python)",
}
MODES = {
    "offline": ("bench",),
    "simulated": ("nsd", "unbound", "tools"),
    "all": ("bench", "nsd", "unbound", "tools"),
}
MODE_TITLE = {"offline": "Offline benchmark", "simulated": "Simulated lab", "all": "Both"}
# The stack-profile keys each image is built from.
ROLE_PINS = {
    "bench": ("OPENSSL_TAG", "LIBOQS_TAG", "MTL_COMMIT"),
    "nsd": ("LIBOQS_TAG", "MTL_COMMIT", "NSD_COMMIT"),
    "unbound": ("LIBOQS_TAG", "MTL_COMMIT", "UNBOUND_COMMIT"),
    "tools": ("LIBOQS_TAG", "MTL_COMMIT", "LDNS_COMMIT", "NSD_COMMIT", "UNBOUND_COMMIT"),
}
# Images built before the labels existed carry the same pins as ENV variables.
ENV_PREFIX = {"bench": "CIPHEROPS_", "nsd": "ECO_", "unbound": "ECO_", "tools": "ECO_"}
ENV_STACK = {"bench": "CIPHEROPS_STACK", "nsd": "ECO_STACK", "unbound": "ECO_STACK", "tools": "ECO_STACK"}


class ImagesNotReady(RuntimeError):
    """A benchmark needs images that are missing or do not match the stack profile (see Setup)."""


@dataclass
class ImageStatus:
    role: str
    ref: str
    present: bool = False
    image_id: str = ""
    created: str = ""
    size_mb: float = 0.0
    digest: str = ""          # content digest of the image; for a pulled image this is the registry's
    state: str = "missing"    # missing | ok | mismatch | unverified
    problems: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return ROLE_TITLE[self.role]


def roles_for(mode: str) -> tuple[str, ...]:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose one of {', '.join(MODES)}")
    return MODES[mode]


def ref(role: str, stack: str) -> str:
    return f"{REPOS[role]}:{stack}"


def remote_ref(registry: str, role: str, stack: str) -> str:
    return f"{normalize_registry(registry)}/{ref(role, stack)}"


def normalize_registry(registry: str | None) -> str:
    value = (registry or "").strip().rstrip("/")
    for scheme in ("https://", "http://"):
        value = value.removeprefix(scheme)
    if not value:
        raise RuntimeError("registry is empty: set a namespace like ghcr.io/org/benchmark")
    return value


def read_pins(stack: str) -> dict[str, str]:
    """The pins of ``ecosystem/stacks/<stack>.env``."""
    path = ECO / "stacks" / f"{stack}.env"
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def _label_name(key: str) -> str:
    return "io.cipherops." + key.lower().replace("_", "-")


def _docker_inspect(image_ref: str) -> dict | None:
    cp = subprocess.run(["docker", "image", "inspect", "--format", "{{json .}}", image_ref],
                        capture_output=True, text=True, timeout=30)
    if cp.returncode != 0:
        return None
    try:
        return json.loads(cp.stdout)
    except json.JSONDecodeError:
        return None


def check(role: str, stack: str, info: dict | None, pins: dict[str, str]) -> ImageStatus:
    """Compare what an image says it was built from with the stack profile. ``info`` is ``docker image inspect``."""
    st = ImageStatus(role=role, ref=ref(role, stack))
    if not info:
        st.problems.append("not on this host")
        return st
    cfg = info.get("Config") or {}
    labels = cfg.get("Labels") or {}
    env = dict(e.partition("=")[::2] for e in (cfg.get("Env") or []))
    st.present = True
    st.image_id = str(info.get("Id", "")).removeprefix("sha256:")[:12]
    st.created = str(info.get("Created", ""))
    st.size_mb = round(float(info.get("Size") or 0) / 1e6, 1)
    digests = info.get("RepoDigests") or []
    st.digest = digests[0].partition("@")[2] if digests else ""

    observed_stack = labels.get("io.cipherops.stack") or env.get(ENV_STACK[role], "")
    if observed_stack and observed_stack != stack:
        st.problems.append(f"built for stack {observed_stack}, not {stack}")
    unknown = []
    for key in ROLE_PINS[role]:
        expected = pins.get(key, "")
        seen = labels.get(_label_name(key)) or env.get(ENV_PREFIX[role] + key, "")
        if not seen:
            unknown.append(key)
        elif expected and seen != expected:
            st.problems.append(f"{key} is {seen[:12]}, the stack profile pins {expected[:12]}")
    if st.problems:
        st.state = "mismatch"
    elif unknown:
        st.state = "unverified"
        st.problems.append("does not record: " + ", ".join(unknown))
    else:
        st.state = "ok"
    return st


def status(stack: str, mode: str = "all", *, inspect=_docker_inspect) -> list[ImageStatus]:
    pins = read_pins(stack)
    return [check(role, stack, inspect(ref(role, stack)), pins) for role in roles_for(mode)]


def ready(statuses: list[ImageStatus]) -> bool:
    return bool(statuses) and all(s.state == "ok" for s in statuses)


def explain(statuses: list[ImageStatus], mode: str) -> str:
    """One line for the Benchmark tile when its images are not usable; empty when they are."""
    bad = [s for s in statuses if s.state != "ok"]
    if not bad:
        return ""
    names = ", ".join(s.ref for s in bad)
    return (f"{MODE_TITLE[mode]} needs {names}: " + "; ".join(sorted({p for s in bad for p in s.problems})) +
            ". Open Setup to build or pull them.")


def build_command(stack: str, mode: str = "all") -> list[str]:
    """``docker compose build`` for the roles of ``mode``; the shared OpenSSL/liboqs/MTL layers are built once."""
    env_file = ECO / "stacks" / f"{stack}.env"
    if not env_file.is_file():
        raise RuntimeError(f"missing stack env {env_file}")
    if not BUILD_FILE.is_file():
        raise RuntimeError(f"missing {BUILD_FILE}")
    return ["docker", "compose", "-f", str(BUILD_FILE), "--env-file", str(env_file), "build", *roles_for(mode)]


def pull_plan(registry: str, stack: str, mode: str = "all") -> list[tuple[str, str]]:
    """(remote reference, local reference) for each image of ``mode``."""
    return [(remote_ref(registry, role, stack), ref(role, stack)) for role in roles_for(mode)]


def pull_commands(registry: str, stack: str, mode: str = "all") -> list[list[str]]:
    cmds: list[list[str]] = []
    for remote, local in pull_plan(registry, stack, mode):
        cmds.append(["docker", "pull", remote])
        cmds.append(["docker", "tag", remote, local])
    return cmds


# ───────────────────────────── command line ─────────────────────────────

def _print_status(stack: str, mode: str) -> int:
    rows = status(stack, mode)
    for s in rows:
        detail = "; ".join(s.problems)
        print(f"{s.state:<10} {s.ref:<36} {s.image_id or '-':<12} {s.size_mb or '':>8} MB  {detail}")
    return 0 if ready(rows) else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python scripts/images.py", description=__doc__.split("\n\n")[0])
    p.add_argument("action", choices=["status", "build", "pull"])
    p.add_argument("--stack", default="latest", help="stack profile in ecosystem/stacks/ (default latest)")
    p.add_argument("--mode", choices=list(MODES), default="all", help="which images (default all)")
    p.add_argument("--registry", default=DEFAULT_REGISTRY, help="for pull: registry namespace")
    args = p.parse_args(argv)
    if args.action == "status":
        return _print_status(args.stack, args.mode)
    cmds = [build_command(args.stack, args.mode)] if args.action == "build" else \
        pull_commands(args.registry, args.stack, args.mode)
    for cmd in cmds:
        print("$ " + " ".join(cmd), flush=True)
        if subprocess.run(cmd).returncode != 0:
            return 1
    return _print_status(args.stack, args.mode)


if __name__ == "__main__":
    sys.exit(main())
