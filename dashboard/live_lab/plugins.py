"""Adoption: build a lab stack from GitHub pins (Verisign-compatible MTL-mode trees).

Writes ``ecosystem/stacks/<name>.env`` and relies on ``docker/build.yml`` + ``image_job`` to compile
images. Query and Benchmark then use that stack like ``draft06`` / ``latest``. This is for forks of the
same layout (MTL + mtl-mode-ldns/nsd/unbound). PowerDNS/BIND need a separate recipe later.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from live_lab import driver, images
from live_lab import minimal as _minimal

STACKS_DIR = driver.ECO / "stacks"
SHIPPED_STACKS = frozenset({"draft06", "latest"})
_STACK_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
_HTTPS_GIT = re.compile(r"^https://[^\s]+\.git$", re.I)

# Defaults match the shipped Verisign pins (draft06). Operators paste forks/commits over these.
DEFAULTS = {
    "OPENSSL_TAG": "openssl-3.6.0",
    "LIBOQS_TAG": "0.14.0",
    "MTL_REPO": "https://github.com/verisign/MTL.git",
    "MTL_COMMIT": "93b05ef73dab53b953cc4e262401a2ce9bc9e431",
    "LDNS_REPO": "https://github.com/Verisign/mtl-mode-ldns.git",
    "LDNS_COMMIT": "64906672ae0d80ae03933240daab27e19ceccf70",
    "NSD_REPO": "https://github.com/verisign/mtl-mode-nsd.git",
    "NSD_COMMIT": "09ebb4e551b5029f7ddf7e381b7bb8a966fa1320",
    "UNBOUND_REPO": "https://github.com/Verisign/mtl-mode-unbound.git",
    "UNBOUND_COMMIT": "fab43ad682301633fa119158ecd22924f042cb26",
}

PIN_KEYS = (
    "OPENSSL_TAG", "LIBOQS_TAG",
    "MTL_REPO", "MTL_COMMIT",
    "LDNS_REPO", "LDNS_COMMIT",
    "NSD_REPO", "NSD_COMMIT",
    "UNBOUND_REPO", "UNBOUND_COMMIT",
)

AIORI_NOTE = (
    "AIORI / IMN can treat each stack profile as a job input: pass ``ecosystem/stacks/<name>.env``, "
    "build or pull the four images, run prepare/suite, ingest ``suite.json`` and CSV. "
    "Custom stacks from GitHub pins use the same contract as draft06 and latest."
)


def read_pins(stack: str) -> dict[str, str]:
    path = STACKS_DIR / f"{stack}.env"
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def list_stacks() -> list[dict]:
    """Every stack profile on disk, with image readiness for the simulated lab."""
    rows = []
    for path in sorted(STACKS_DIR.glob("*.env")):
        pins = read_pins(path.stem)
        stack = path.stem
        try:
            img_rows = images.status(stack, "simulated")
            images_ok = bool(img_rows) and all(r.state == "ok" for r in img_rows)
            missing = [r.ref for r in img_rows if r.state != "ok"]
        except Exception as e:  # noqa: BLE001
            images_ok, missing = False, [str(e)[:120]]
        rows.append({
            "stack": stack,
            "shipped": stack in SHIPPED_STACKS,
            "pins": pins,
            "images_ok": images_ok,
            "missing": missing,
            "algorithms": [a for a in (pins.get("ADOPT_ALGORITHMS") or "").split(",") if a.strip()],
            "draft": pins.get("ADOPT_DRAFT", ""),
            "notes": pins.get("ADOPT_NOTES", ""),
        })
    return rows


def _require_https_git(url: str, field: str) -> str:
    url = (url or "").strip()
    if not _HTTPS_GIT.match(url):
        raise ValueError(f"{field} must be an https://…*.git URL (got {url!r}).")
    return url


def _require_commit(value: str, field: str) -> str:
    value = (value or "").strip().lower()
    if not _COMMIT_RE.match(value):
        raise ValueError(f"{field} must be a git commit (7–40 hex chars).")
    return value


def validate_stack_name(name: str, *, allow_overwrite_shipped: bool = False) -> str:
    name = (name or "").strip().lower()
    if not _STACK_RE.match(name):
        raise ValueError("Stack name: 2–32 chars, lowercase letter then letters/digits/hyphens.")
    if name in SHIPPED_STACKS and not allow_overwrite_shipped:
        raise ValueError(f"{name!r} is a shipped profile. Pick another name (e.g. my-mtl-fork).")
    return name


def write_stack_env(
    stack: str,
    *,
    pins: dict[str, str],
    algorithms: list[str] | None = None,
    draft: str = "",
    notes: str = "",
    overwrite: bool = False,
) -> Path:
    """Create or replace ``ecosystem/stacks/<stack>.env``. Does not build images."""
    _minimal.refuse("Writing a custom stack profile")
    stack = validate_stack_name(stack, allow_overwrite_shipped=False)
    path = STACKS_DIR / f"{stack}.env"
    if path.is_file() and not overwrite:
        raise ValueError(f"{path.name} already exists. Enable overwrite to replace it.")

    merged = dict(DEFAULTS)
    for k in PIN_KEYS:
        if pins.get(k):
            merged[k] = pins[k].strip()

    merged["MTL_REPO"] = _require_https_git(merged["MTL_REPO"], "MTL_REPO")
    merged["LDNS_REPO"] = _require_https_git(merged["LDNS_REPO"], "LDNS_REPO")
    merged["NSD_REPO"] = _require_https_git(merged["NSD_REPO"], "NSD_REPO")
    merged["UNBOUND_REPO"] = _require_https_git(merged["UNBOUND_REPO"], "UNBOUND_REPO")
    merged["MTL_COMMIT"] = _require_commit(merged["MTL_COMMIT"], "MTL_COMMIT")
    merged["LDNS_COMMIT"] = _require_commit(merged["LDNS_COMMIT"], "LDNS_COMMIT")
    merged["NSD_COMMIT"] = _require_commit(merged["NSD_COMMIT"], "NSD_COMMIT")
    merged["UNBOUND_COMMIT"] = _require_commit(merged["UNBOUND_COMMIT"], "UNBOUND_COMMIT")
    if not (merged.get("OPENSSL_TAG") or "").strip():
        raise ValueError("OPENSSL_TAG is required.")
    if not (merged.get("LIBOQS_TAG") or "").strip():
        raise ValueError("LIBOQS_TAG is required.")

    algos = [a.strip() for a in (algorithms or []) if a and str(a).strip()]
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"# Custom stack created by CipherOps Adoption ({when}).",
        "# Verisign-compatible layout: MTL + mtl-mode-ldns / nsd / unbound at the commits below.",
        f"STACK={stack}",
        f"OPENSSL_TAG={merged['OPENSSL_TAG']}",
        f"LIBOQS_TAG={merged['LIBOQS_TAG']}",
        f"MTL_REPO={merged['MTL_REPO']}",
        f"MTL_COMMIT={merged['MTL_COMMIT']}",
        f"LDNS_REPO={merged['LDNS_REPO']}",
        f"LDNS_COMMIT={merged['LDNS_COMMIT']}",
        f"NSD_REPO={merged['NSD_REPO']}",
        f"NSD_COMMIT={merged['NSD_COMMIT']}",
        f"UNBOUND_REPO={merged['UNBOUND_REPO']}",
        f"UNBOUND_COMMIT={merged['UNBOUND_COMMIT']}",
    ]
    if draft.strip():
        lines.append(f"ADOPT_DRAFT={draft.strip()}")
    if notes.strip():
        lines.append(f"ADOPT_NOTES={notes.strip().replace(chr(10), ' ')}")
    if algos:
        lines.append("ADOPT_ALGORITHMS=" + ",".join(algos))
    lines.append("")

    STACKS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def delete_custom_stack(stack: str) -> None:
    _minimal.refuse("Deleting a custom stack profile")
    stack = validate_stack_name(stack, allow_overwrite_shipped=False)
    if stack in SHIPPED_STACKS:
        raise ValueError("Cannot delete a shipped stack.")
    path = STACKS_DIR / f"{stack}.env"
    if not path.is_file():
        raise ValueError(f"No stack file for {stack!r}.")
    path.unlink()
