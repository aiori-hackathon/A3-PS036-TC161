"""What the lab leaves on disk: manifests, saved runs, the live session and activity log, prefs, and Reset.

``live_lab.driver`` re-exports these names at its end, so ``driver.<name>`` keeps working.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from live_lab import minimal as _minimal
from live_lab.driver import ECO, _run, jobs_dir, list_stacks, require_no_suite, require_not_signing
from live_lab.lab import LabHandle, compose_cmd
from live_lab.shapes import ZONE_SIZE_PRESETS


def remove_tree(path: Path, stack: str = "draft06") -> bool:
    """Delete a folder under ecosystem/, including files Docker created as root.

    A plain delete fails silently on those, so the folder would look removed while its contents stayed.
    Falls back to ``rm`` inside the tools image, which runs as root. Returns True if the folder is gone.
    """
    _minimal.refuse('Deleting lab files')
    import shutil

    path = Path(path)
    if not path.exists():
        return True
    shutil.rmtree(path, ignore_errors=True)
    if not path.exists():
        return True
    try:
        rel = path.resolve().relative_to(ECO.resolve())
    except ValueError:
        return False  # never delete outside ecosystem/
    if not rel.parts:
        return False
    _run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "rm", "-v", f"{ECO}:/eco:z",
          f"cipherops-eco-tools:{stack}", "-rf", "--", f"/eco/{rel.as_posix()}"], check=False, timeout=300)
    return not path.exists()


def _read_manifest(path: Path) -> dict:
    """Load a Prepare manifest, with every zone's ``rrsig_count`` corrected to the real RRSIG count.

    Before the fix in ``ecolab.sign._rrsig_count`` the manifest also counted NSEC3 records (their type
    bitmap lists RRSIG), about 1.5x too many. Older manifests stay on disk untouched; here the count is
    taken again from the signed file (cached by mtime) and the stored figure is kept as
    ``rrsig_count_manifest``. Manifests written by the fixed signer already match and are left as they are.
    """
    from live_lab import prepare_story

    man = json.loads(path.read_text(encoding="utf-8"))
    for z in man.get("zones") or []:
        prof = prepare_story.rrsig_profile(path.parent / str(z.get("signed_file") or ""))
        if prof is not None and prof["total"] != z.get("rrsig_count"):
            z["rrsig_count_manifest"] = z.get("rrsig_count")
            z["rrsig_count"] = prof["total"]
    return man


def load_manifest(h: LabHandle, scenario: str) -> dict | None:
    path = h.work / "zones" / scenario / "manifest.json"
    if not path.is_file():
        return None
    return _read_manifest(path)


def list_manifests(h: LabHandle, tag: str | None = None) -> dict[str, dict]:
    zones = (ECO / "work" / tag if tag else h.work) / "zones"
    if not zones.is_dir():
        return {}
    out: dict[str, dict] = {}
    for d in sorted(zones.iterdir()):
        m = d / "manifest.json"
        if d.is_dir() and m.is_file():
            out[d.name] = _read_manifest(m)
    return out


def manifest_rows(h: LabHandle, tag: str | None = None) -> list[dict]:
    """Flat rows for Metrics tables: one per zone per scenario."""
    rows = []
    for scenario, man in list_manifests(h, tag).items():
        for z in man.get("zones") or []:
            rows.append({
                "scenario": scenario,
                "origin": z.get("origin", ""),
                "tier": z.get("tier", ""),
                "algorithm": z.get("algorithm", ""),
                "sign_seconds": z.get("sign_seconds"),
                "verify_seconds": z.get("verify_seconds"),
                "signed_bytes": z.get("signed_bytes"),
                "rrsig_count": z.get("rrsig_count"),
                "n_names": z.get("n_names"),
            })
    return rows


def prepare_summary(h: LabHandle, *, wall_s: float | None = None,
                    scenarios: list[str] | None = None) -> dict:
    """Human-facing summary of what Prepare produced on disk."""
    zones_root = h.work / "zones"
    scen = scenarios or ([p.name for p in zones_root.iterdir() if p.is_dir()] if zones_root.is_dir() else [])
    rows = []
    for s in scen:
        man = load_manifest(h, s)
        if not man:
            continue
        zs = man.get("zones") or []
        rows.append({
            "scenario": s,
            "zones": len(zs),
            "sign_seconds": round(sum(float(z.get("sign_seconds") or 0) for z in zs), 3),
            "verify_seconds": round(sum(float(z.get("verify_seconds") or 0) for z in zs), 3),
            "signed_bytes": sum(int(z.get("signed_bytes") or 0) for z in zs),
            "path": str(zones_root / s),
        })
    return {
        "lab": h.lab,
        "tag": h.tag,
        "config": h.config,
        "wall_s": wall_s,
        "work_dir": str(h.work),
        "zones_dir": str(zones_root),
        "active_dir": str(h.work / "active"),
        "compose": str(h.compose),
        "active_scenario": h.active_scenario(),
        "scenarios": rows,
    }


def list_labs() -> list[dict]:
    """Every lab folder that has signing data and/or saved benchmark runs, newest activity first."""
    tags: dict[str, float] = {}
    for p in (ECO / "work").glob("*/zones/*/manifest.json"):
        t = p.parents[2].name
        tags[t] = max(tags.get(t, 0.0), p.stat().st_mtime)
    for p in (ECO / "results").glob("*/*/summary.csv"):
        t = p.parents[1].name
        tags[t] = max(tags.get(t, 0.0), p.stat().st_mtime)
    return [{"tag": t, "mtime": m} for t, m in sorted(tags.items(), key=lambda kv: kv[1], reverse=True)]


def list_run_dirs(h: LabHandle, tag: str | None = None) -> list[Path]:
    root = ECO / "results" / (tag or h.tag)
    if not root.is_dir():
        return []
    dirs = [p for p in root.iterdir() if p.is_dir() and p.name != "live-dashboard"]
    return sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)


def list_runs(h: LabHandle, tag: str | None = None) -> list[dict]:
    """Saved benchmark runs, newest first, with a friendly name, timestamp and what was measured."""
    out: list[dict] = []
    for p in list_run_dirs(h, tag):
        meta: dict = {}
        mj = p.joinpath("run.json")
        if mj.is_file():
            try:
                meta = json.loads(mj.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
        stamp, _, tail = p.name.partition("_")
        label = meta.get("run_label") or tail
        try:
            when = datetime.strptime(stamp, "%Y%m%d-%H%M%S").strftime("%d %b %Y, %H:%M:%S")
        except ValueError:
            when = datetime.fromtimestamp(p.stat().st_mtime).strftime("%d %b %Y, %H:%M:%S")
        out.append({
            "path": p,
            "folder": p.name,
            "label": label,
            "title": f"{label} - {when}" if label else when,
            "when": when,
            "scenarios": meta.get("scenarios") or [],
            "profiles": meta.get("profiles") or [],
            "trials": meta.get("trials"),
            "cfg": meta.get("lab") or {},
            "has_results": any(p.glob("*.csv")),
        })
    return out


def session_path(h: LabHandle) -> Path:
    return h.capture_dir / "session.json"


def _empty_session(h: LabHandle, *, show_prepare_metrics: bool = False) -> dict:
    """Fresh session: Metrics stay empty until Prepare (or an explicit clear→Prepare)."""
    return {
        "started": datetime.now(timezone.utc).isoformat(),
        "queries": [],
        "prepare_wall_s": None,
        "cold_warm": None,
        "show_prepare_metrics": show_prepare_metrics,
        "activity_file": str(h.capture_dir / "activity.log"),
    }


def load_session(h: LabHandle) -> dict:
    p = session_path(h)
    if not p.is_file():
        return _empty_session(h)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _empty_session(h)
    data.setdefault("queries", [])
    data.setdefault("prepare_wall_s", None)
    data.setdefault("cold_warm", None)
    # Missing key → hidden (same as fresh session after results wipe)
    data.setdefault("show_prepare_metrics", False)
    return data


def save_session(h: LabHandle, session: dict) -> Path:
    if _minimal.enabled():  # a visitor's actions are not written to the team's files
        return session_path(h)
    h.ensure_capture_dir()
    p = session_path(h)
    p.write_text(json.dumps(session, indent=2, default=str), encoding="utf-8")
    return p


def append_session_query(h: LabHandle, row: dict) -> None:
    if _minimal.enabled():  # a visitor's actions are not written to the team's files
        return
    sess = load_session(h)
    sess.setdefault("queries", []).append({**row, "ts": datetime.now(timezone.utc).isoformat()})
    save_session(h, sess)


def append_activity_file(h: LabHandle, line: str) -> None:
    if _minimal.enabled():  # a visitor's actions are not written to the team's files
        return
    path = h.ensure_capture_dir() / "activity.log"
    with path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def chunk_log(text: str, max_chunk: int = 1200) -> list[str]:
    """Split long command output into readable chunks for the activity feed."""
    text = (text or "").strip()
    if not text:
        return []
    lines = text.splitlines()
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for ln in lines:
        if size + len(ln) + 1 > max_chunk and buf:
            chunks.append("\n".join(buf))
            buf, size = [], 0
        buf.append(ln)
        size += len(ln) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def clear_demo_data(
    h: LabHandle,
    *,
    session: bool = True,
    activity: bool = True,
    pcaps: bool = True,
    hide_prepare_metrics: bool = True,
    work: bool = False,
    benchmarks: bool = False,
    suites: bool = False,
) -> dict[str, int]:
    """Wipe live-dashboard artifacts; optionally wipe signed work/ and saved benchmark runs.

    When ``work`` is True, removes ``ecosystem/work/<tag>/`` so Prepare must run again.
    When ``benchmarks`` is True, removes every saved run folder under ``results/<tag>/``
    (the live-dashboard folder is handled by the other scopes).
    Does not remove Docker images.
    """
    _minimal.refuse('Reset')
    if work or benchmarks or suites:
        require_no_suite()
    if work:
        require_not_signing(h)
    d = h.capture_dir
    d.mkdir(parents=True, exist_ok=True)
    removed = {"session": 0, "activity": 0, "pcaps": 0, "work": 0, "benchmarks": 0, "suites": 0}
    if suites:
        removed["suites"] = clear_suites()
    if benchmarks:
        for run_dir in list_run_dirs(h):
            remove_tree(run_dir, h.stack)
            removed["benchmarks"] += 1
    if session:
        p = session_path(h)
        if p.is_file():
            p.unlink()
            removed["session"] = 1
        save_session(h, _empty_session(h, show_prepare_metrics=not hide_prepare_metrics))
    if activity:
        ap = d / "activity.log"
        if ap.is_file():
            ap.unlink()
            removed["activity"] = 1
        (d / "activity.log").write_text("", encoding="utf-8")
    if pcaps:
        for pcap in d.glob("*.pcap"):
            try:
                pcap.unlink()
                removed["pcaps"] += 1
            except OSError:
                pass
        cur = d / ".current"
        if cur.is_file():
            cur.unlink(missing_ok=True)
    if work:
        # Stop this lab's compose if present, then delete work/<tag>
        try:
            if h.compose.is_file():
                _run(compose_cmd(h, "down"), timeout=120, check=False)
        except Exception:  # noqa: BLE001
            pass
        if h.work.is_dir():
            if not remove_tree(h.work, h.stack):
                raise RuntimeError(f"could not delete {h.work}; remove it with: sudo rm -rf {h.work}")
            removed["work"] = 1
        remove_tree(jobs_dir() / h.tag, h.stack)
    return removed


def _suite_dirs() -> list[Path]:
    """Everything the Benchmark tile creates: suite records, per-size run folders, per-size signed zones."""
    out: list[Path] = []
    suites = ECO / "results" / "suites"
    if suites.is_dir():
        out += [p for p in suites.iterdir() if p.is_dir()]
    out += [p for p in (ECO / "results").glob("eco-bench-*") if p.is_dir()]
    out += [p for p in (ECO / "work").glob("eco-bench-*") if p.is_dir()]
    return out


def suite_footprint() -> dict:
    dirs = _suite_dirs()
    return {"suites": len([p for p in dirs if p.parent.name == "suites"]),
            "bytes": sum(_dir_size(p) for p in dirs)}


def clear_suites() -> int:
    """Delete benchmark suites, their per-size runs and signed zones; returns the number of suites removed."""
    _minimal.refuse('Deleting benchmarks')
    require_no_suite()
    for compose in (ECO / "work").glob("eco-bench-*/compose.yml"):
        _run(["docker", "compose", "-f", str(compose), "down"], check=False, timeout=120)
    n = suite_footprint()["suites"]
    for p in _suite_dirs():
        remove_tree(p)
    for cfgf in (ECO / "configs").glob("_bench-*.yaml"):
        cfgf.unlink(missing_ok=True)
    return n


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def reset_preview(h: LabHandle) -> dict:
    """What each Reset scope would remove right now (counts and bytes), without touching anything."""
    d = h.capture_dir
    pcaps = list(d.glob("*.pcap"))
    session = session_path(h)
    session_queries_n = len(load_session(h).get("queries") or []) if session.is_file() else 0
    activity_log = d / "activity.log"
    runs = list_run_dirs(h)
    sf = suite_footprint()
    return {
        "suites": sf["suites"],
        "suite_bytes": sf["bytes"],
        "session_queries": session_queries_n,
        "activity_bytes": activity_log.stat().st_size if activity_log.is_file() else 0,
        "pcaps": len(pcaps),
        "pcap_bytes": sum(p.stat().st_size for p in pcaps if p.is_file()),
        "runs": len(runs),
        "run_bytes": sum(_dir_size(r) for r in runs),
        "work_exists": h.work.is_dir(),
        "work_bytes": _dir_size(h.work) if h.work.is_dir() else 0,
    }


def session_queries(h: LabHandle, ui_log: list | None) -> list:
    """Merge UI log with on-disk session so a browser refresh does not wipe Queries."""
    disk = list(load_session(h).get("queries") or [])
    if ui_log is None:
        return disk
    if not ui_log:
        return disk
    # Prefer the longer/fresher view: UI may have rows not yet flushed, disk survives refresh.
    if len(disk) > len(ui_log):
        return disk
    return list(ui_log)


# ── UI prefs (survive Streamlit browser refresh; session_state does not) ──

UI_PREFS_PATH = ECO / "results" / ".dashboard_prefs.json"


def load_ui_prefs() -> dict:
    if not UI_PREFS_PATH.is_file():
        return {}
    try:
        data = json.loads(UI_PREFS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_ui_prefs(prefs: dict) -> None:
    if _minimal.enabled():  # a visitor's actions are not written to the team's files
        return
    UI_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    cur = load_ui_prefs()
    cur.update({k: v for k, v in prefs.items() if v is not None})
    UI_PREFS_PATH.write_text(json.dumps(cur, indent=2), encoding="utf-8")


def split_lab_tag(tag: str) -> tuple[str, str] | None:
    """eco-ui-small-latest → (eco-ui-small, latest)."""
    stacks = list_stacks()
    for s in sorted(stacks, key=len, reverse=True):
        suf = f"-{s}"
        if tag.endswith(suf) and len(tag) > len(suf):
            return tag[: -len(suf)], s
    return None


def zone_size_for_lab_name(lab: str) -> str | None:
    for key, preset in ZONE_SIZE_PRESETS.items():
        if preset.get("name") == lab:
            return key
    if lab.startswith("eco-ui-custom-"):
        return "custom"
    return None


def live_capture_inventory() -> list[dict]:
    """All live-dashboard folders that still have .pcap files (newest first)."""
    root = ECO / "results"
    rows: list[dict] = []
    if not root.is_dir():
        return rows
    for d in root.glob("*/live-dashboard"):
        pcaps = sorted(d.glob("*.pcap"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not pcaps:
            continue
        tag = d.parent.name
        parts = split_lab_tag(tag)
        newest = pcaps[0].stat().st_mtime
        rows.append({
            "tag": tag,
            "lab": parts[0] if parts else tag,
            "stack": parts[1] if parts else "",
            "zone_size": zone_size_for_lab_name(parts[0]) if parts else None,
            "count": len(pcaps),
            "newest": newest,
            "dir": str(d),
            "names": [p.name for p in pcaps[:12]],
        })
    rows.sort(key=lambda r: r["newest"], reverse=True)
    return rows


def infer_ui_from_disk() -> dict:
    """Best-effort stack/zone after a hard refresh: prefs, else newest live captures."""
    prefs = load_ui_prefs()
    out: dict = {}
    if prefs.get("stack") in list_stacks():
        out["stack"] = prefs["stack"]
    if prefs.get("zone_size") in ZONE_SIZE_PRESETS or prefs.get("zone_size") == "custom":
        out["zone_size"] = prefs["zone_size"]
    if prefs.get("scenarios"):
        out["scenarios"] = list(prefs["scenarios"])

    inv = live_capture_inventory()
    if not inv:
        return out

    pref_tag = None
    z = out.get("zone_size")
    if z in ZONE_SIZE_PRESETS and out.get("stack"):
        pref_tag = f"{ZONE_SIZE_PRESETS[z]['name']}-{out['stack']}"
    pref_has_pcaps = pref_tag is not None and any(r["tag"] == pref_tag for r in inv)

    # Keep saved lab when it still has captures. Otherwise (fresh browser / empty lab) follow newest.
    if pref_has_pcaps:
        return out
    top = inv[0]
    if top.get("stack"):
        out["stack"] = top["stack"]
    if top.get("zone_size"):
        out["zone_size"] = top["zone_size"]
    return out


def list_pcaps(h: LabHandle) -> list[Path]:
    d = h.capture_dir
    if not d.is_dir():
        return []
    return sorted(d.glob("*.pcap"), key=lambda p: p.stat().st_mtime, reverse=True)
