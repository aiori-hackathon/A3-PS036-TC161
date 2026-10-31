"""Prepare as a background job, so the dashboard can show the signing while it happens.

``driver.prepare`` blocks until everything is signed. This module runs the same command in a detached
worker instead. The signer prints ``@@EVENT {json}`` lines as each zone starts and finishes
(``ecolab.sign.emit``); the worker splits them out of the Docker output:

    ecosystem/jobs/<lab>/prepare-live/
        params.json    what was asked for
        status.json    state: starting | running | done | failed | cancelled | stopped
        events.jsonl   one event per line, in the order the signer printed them
        progress.log   everything else the signer printed

The Setup page polls these files. ``live_state`` and ``live_dot`` are pure functions over the events,
so the monitor can be tested without Docker.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from live_lab import driver
from live_lab import jobs as jb

EVENT_PREFIX = "@@EVENT "
TIER_ORDER = ("sld", "tld", "root")
TIER_LABEL = {"sld": "Leaf zones", "tld": "TLD zones", "root": "Root"}
ACTIVE_STATES = jb.ACTIVE_STATES


# ───────────────────────────── files ─────────────────────────────

def job_dir(h: driver.LabHandle) -> Path:
    return driver.jobs_dir() / h.tag / "prepare-live"


def status(h: driver.LabHandle) -> dict:
    """Current job status ({} if none). A worker that vanished without finishing reads as 'stopped'."""
    st = jb.read_json(job_dir(h) / "status.json")
    if st.get("state") in ACTIVE_STATES:
        # 'starting' has no worker pid of its own for a moment: allow a short grace period.
        if st.get("pid") and not driver.pid_alive(st["pid"]):
            st["state"] = "stopped"
            st["error"] = st.get("error") or "The signing process ended before finishing."
        elif not st.get("pid") and time.time() - float(st.get("started") or 0) > 60:
            st["state"] = "stopped"
            st["error"] = "The signing process never started."
    return st


def is_running(h: driver.LabHandle) -> bool:
    return status(h).get("state") in ACTIVE_STATES


def events(h: driver.LabHandle) -> list[dict]:
    out: list[dict] = []
    try:
        text = (job_dir(h) / "events.jsonl").read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a line cut short while it was being written
    return out


def log_text(h: driver.LabHandle, tail: int = 12000) -> str:
    return jb.log_tail(job_dir(h), tail)


def mark_handled(h: driver.LabHandle) -> None:
    jb.mark_handled(job_dir(h))


# ───────────────────────────── control ─────────────────────────────

def start(h: driver.LabHandle, *, jobs: int, scenarios: list[str]) -> None:
    """Launch Prepare as a detached worker. Returns at once."""
    if is_running(h):
        raise RuntimeError("Signing is already running for this lab.")
    driver.require_no_suite()
    driver.require_no_offline()
    driver.require_no_build(h.stack)
    d = job_dir(h)
    d.mkdir(parents=True, exist_ok=True)
    for name in ("events.jsonl", "progress.log"):
        (d / name).write_text("", encoding="utf-8")
    # The dashboard rewrites one shared config file whenever a sidebar setting changes, and the lab's name
    # (so its work folder) comes from that file. The job signs from its own copy, so changing Zone size
    # while it runs cannot send its output to a different lab.
    shutil.copyfile(driver.ECO / h.config, d / "config.yaml")
    pinned = (d / "config.yaml").relative_to(driver.ECO).as_posix()
    params = {"config": pinned, "lab_config": h.config, "stack": h.stack, "jobs": int(jobs),
              "scenarios": list(scenarios), "started": time.time()}
    jb.write_status(d, {"state": "starting", "started": params["started"], "jobs": params["jobs"],
                          "scenarios": params["scenarios"], "updated": params["started"]})
    jb.spawn_worker("live_lab.prepare_job", d, params)


def cancel(h: driver.LabHandle) -> None:
    """Stop the worker and the signing container."""
    st = jb.read_json(job_dir(h) / "status.json")
    jb.stop_worker(st.get("pid"), alive=driver.pid_alive)
    subprocess.run(["docker", "rm", "-f", driver.prepare_container_name(h)], capture_output=True,
                   creationflags=driver.NO_WINDOW)
    jb.mark_cancelled(job_dir(h), st)


# ───────────────────────────── worker ─────────────────────────────

def run(params: dict, *, popen=subprocess.Popen) -> int:
    """Worker body: run Prepare, route its output, and save the session summary when it succeeds."""
    h = driver.LabHandle(config=params["config"], stack=params["stack"])
    d = job_dir(h)
    scenarios = list(params["scenarios"])
    st = {"state": "running", "pid": os.getpid(), "started": params["started"], "jobs": params["jobs"],
          "scenarios": scenarios, "updated": time.time()}
    jb.write_status(d, st)
    subprocess.run(["docker", "rm", "-f", driver.prepare_container_name(h)], capture_output=True,
                   creationflags=driver.NO_WINDOW)  # a stale one

    cmd = driver.prepare_command(h, params["jobs"], scenarios, events=True)
    env = os.environ.copy()
    env.setdefault("CIPHEROPS_GIT_SHA", "live-dashboard")
    t0 = time.perf_counter()
    rc = 1
    try:
        proc = popen(cmd, cwd=driver.ECO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                     encoding="utf-8", errors="replace", bufsize=1, env=env, creationflags=driver.NO_WINDOW)
        with (d / "events.jsonl").open("a", encoding="utf-8") as ev, \
                (d / "progress.log").open("a", encoding="utf-8") as lg:
            for line in proc.stdout:
                if line.startswith(EVENT_PREFIX):
                    ev.write(line[len(EVENT_PREFIX):].strip() + "\n")
                    ev.flush()
                else:
                    lg.write(line)
                    lg.flush()
        rc = proc.wait()
    except Exception as e:  # noqa: BLE001
        with (d / "progress.log").open("a", encoding="utf-8") as lg:
            lg.write(f"\n{type(e).__name__}: {e}\n")
    wall = time.perf_counter() - t0

    if jb.read_json(d / "status.json").get("state") == "cancelled":
        return rc
    st.update(updated=time.time(), finished=time.time(), wall_s=wall)
    if rc == 0:
        try:
            summary = driver.prepare_summary(h, wall_s=wall, scenarios=scenarios)
            summary["config"] = params.get("lab_config") or summary["config"]  # not the job's private copy
            sess = driver.load_session(h)
            sess.update(prepare_wall_s=wall, show_prepare_metrics=True, prepare_summary=summary)
            driver.save_session(h, sess)
            st.update(state="done", summary=summary)
        except Exception as e:  # noqa: BLE001
            st.update(state="failed", error=f"Signed, but the summary failed: {e}")
    else:
        st.update(state="failed", error=log_text(h, 3000) or f"The signing container exited with code {rc}.")
    jb.write_status(d, st)
    return rc


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    params = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    raise SystemExit(run(params))


# ───────────────────────────── live state (pure) ─────────────────────────────

def live_state(evts: list[dict], expected: list[str] | None = None, now: float | None = None) -> dict:
    """Fold the signer's events into what the monitor draws.

    ``expected`` is the scenario list that was requested, so scenarios that have not started yet still
    show as waiting. Per zone: waiting -> signing (zone_start) -> done (zone_done, with the measured
    sign/verify seconds and exact RRSIG count). ``elapsed`` of a signing zone is live (now - start).
    """
    now = time.time() if now is None else now
    by: dict[str, dict] = {}
    order: list[str] = []
    finished = False
    for e in evts:
        kind, s = e.get("event"), e.get("scenario")
        if kind == "prepare_done":
            finished = True
        elif kind == "scenario_start":
            order.append(s)
            by[s] = {"scenario": s, "algorithms": e.get("algorithms") or {}, "t0": e.get("t"), "t1": None,
                     "zones": {z["origin"]: {**z, "state": "waiting", "t0": None, "t1": None}
                               for z in e.get("zones") or []}}
        elif s in by:
            sc = by[s]
            if kind == "scenario_done":
                sc["t1"] = e.get("t")
                continue
            z = sc["zones"].get(e.get("origin"))
            if z is None:
                continue
            if kind == "zone_start":
                z.update(state="signing", t0=e.get("t"))
            elif kind == "zone_done":
                z.update(state="done", t1=e.get("t"), rrsigs=e.get("rrsigs"), sign_s=e.get("sign_s"),
                         verify_s=e.get("verify_s"), signed_bytes=e.get("signed_bytes"),
                         algorithm=e.get("algorithm"))

    scenarios = []
    for s in order:
        sc = by[s]
        zones = []
        for z in sc["zones"].values():
            z = dict(z)
            z["elapsed"] = (now - z["t0"]) if z["state"] == "signing" and z.get("t0") else None
            zones.append(z)
        tiers = {t: {"total": 0, "signing": 0, "done": 0} for t in TIER_ORDER}
        for z in zones:
            c = tiers.setdefault(z["tier"], {"total": 0, "signing": 0, "done": 0})
            c["total"] += 1
            if z["state"] in ("signing", "done"):
                c[z["state"]] += 1
        n_done = sum(1 for z in zones if z["state"] == "done")
        state = "done" if sc["t1"] else ("signing" if any(z["state"] != "waiting" for z in zones) else "waiting")
        scenarios.append({"scenario": s, "algorithms": sc["algorithms"], "state": state, "zones": zones,
                          "tiers": tiers, "done": n_done, "total": len(zones),
                          "rrsigs": sum(int(z.get("rrsigs") or 0) for z in zones if z["state"] == "done"),
                          "t0": sc["t0"], "t1": sc["t1"]})

    per_scenario = max((x["total"] for x in scenarios), default=0)
    names = list(expected or order)
    for s in order:
        if s not in names:
            names.append(s)
    total = per_scenario * len(names)
    done = sum(x["done"] for x in scenarios)
    pending = [s for s in names if s not in order]
    current = next((x for x in scenarios if x["state"] != "done"), scenarios[-1] if scenarios else None)
    return {"scenarios": scenarios, "pending": pending, "current": current["scenario"] if current else None,
            "zones_done": done, "zones_total": total,
            "fraction": (done / total) if total else 0.0,
            "finished": finished, "per_scenario": per_scenario}


_DOT_FILL = {"waiting": "#2b3140", "signing": "#b7791f", "done": "#2f855a"}
_DOT_TEXT = {"waiting": "#9aa4b2", "signing": "#ffffff", "done": "#ffffff"}


def _node(name: str, label: str, state: str) -> str:
    return (f'"{name}" [label="{label}", fillcolor="{_DOT_FILL[state]}", fontcolor="{_DOT_TEXT[state]}"];')


def _group_state(states: list[str]) -> str:
    if all(s == "done" for s in states):
        return "done"
    if all(s == "waiting" for s in states):
        return "waiting"
    return "signing"


def live_dot(scenario: dict, collapse_over: int = 40) -> str:
    """Graphviz source for the hierarchy of one scenario, coloured by signing state.

    The text never contains a running clock, so the picture only changes when a zone changes state.
    Above ``collapse_over`` zones the leaf zones are folded into one node per parent.
    """
    zones = scenario["zones"]
    lines = ['digraph G {', 'rankdir=TB; bgcolor="transparent"; nodesep=0.15; ranksep=0.3;',
             'node [shape=box, style="rounded,filled", color="#00000000", fontname="Helvetica", fontsize=8, '
             'margin="0.1,0.05"];', 'edge [color="#6b7686", arrowsize=0.5];']
    if len(zones) <= collapse_over:
        for z in zones:
            sub = ""
            if z["state"] == "done" and z.get("rrsigs") is not None:
                sub = f"\\n{int(z['rrsigs']):,} RRSIGs · {float(z.get('sign_s') or 0):.1f} s"
            elif z["state"] == "signing":
                sub = "\\nsigning…"
            origin = z["origin"]
            lines.append(_node(origin, f"{origin}{sub}", z["state"]))
        known = {z["origin"] for z in zones}
        for z in zones:
            if z.get("parent") in known:
                lines.append(f'"{z["parent"]}" -> "{z["origin"]}";')
    else:
        leaves: dict[str, list[dict]] = {}
        for z in zones:
            if z["tier"] == "sld":
                leaves.setdefault(z.get("parent") or "", []).append(z)
            else:
                lines.append(_node(z["origin"], z["origin"], z["state"]))
        for parent, group in leaves.items():
            n_done = sum(1 for z in group if z["state"] == "done")
            gid = f"leaves of {parent}"
            lines.append(_node(gid, f"{len(group)} leaf zones\\n{n_done} signed",
                               _group_state([z["state"] for z in group])))
            lines.append(f'"{parent}" -> "{gid}";')
        known = {z["origin"] for z in zones if z["tier"] != "sld"}
        for z in zones:
            if z["tier"] != "sld" and z.get("parent") in known:
                lines.append(f'"{z["parent"]}" -> "{z["origin"]}";')
    lines.append("}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
