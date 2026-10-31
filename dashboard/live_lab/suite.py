"""Benchmark suite: the PS-036 benchmark run end to end from a handful of parameters.

For every dataset size the suite builds a private hierarchy of that size, signs it with each chosen
scenario (timed), repeats signing and verification on a sample leaf zone (several trials), starts the
lab, replays the same seeded workload with repeated cold and warm trials, and stops the lab again.
It needs only the verified lab images from Setup: no Prepare or running lab. Everything a reader needs to repeat
or audit the run is stored in ``ecosystem/results/suites/<id>/``.

It runs as a detached process (``python -m live_lab.suite params.json``) so a long run survives a
browser refresh, and writes ``status.json`` and ``progress.log`` for the dashboard to poll.
"""

from __future__ import annotations

import contextlib
import csv
import json
import os
import shutil
import platform
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):          # `python dashboard/live_lab/suite.py ...` from any folder
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live_lab import minimal as _minimal
from live_lab import driver, jobs

SUITES = driver.SUITES_DIR
BENCH_SIZES = (50, 500, 5000)         # data names in the dataset, as in the main Cipherops benchmark
SMOKE_SIZE = 20                       # quick check that the pipeline works
DEFAULT_SIGN_TRIALS = 5
DEFAULT_SAMPLE_NAMES = 100
STEPS_PER_SIZE = 5
LONG_RUN_S = 12 * 3600                # the command line asks for --yes above this


def bench_shape(n_names: int) -> dict:
    """Same hierarchy for every size (one TLD, up to four leaf zones, one leaf server); only names change."""
    zones = max(1, min(4, int(n_names)))
    base, rem = divmod(int(n_names), zones)
    return {
        "label": f"{n_names} names", "name": f"eco-bench-{n_names}", "tlds": ["test"], "sld_count": zones,
        "sld_sizes": [base + (1 if i < rem else 0) for i in range(zones)], "sld_servers": 1,
        "n_queries_default": 500,
    }


def sample_sizes(n_names: int, sample_names: int) -> list[int]:
    """Two sample zone sizes: the chosen one (capped by the dataset) and a quarter of it."""
    big = max(1, min(int(n_names), int(sample_names)))
    small = max(2, big // 4)
    return [big] if small >= big else [big, small]


def estimate_seconds(sizes: list[int], scenarios: list[str], jobs: int = 2, sign_trials: int = 0,
                     sample_names: int = DEFAULT_SAMPLE_NAMES) -> float:
    """Rough signing wall time (the model in driver.shape_stats) plus the sign trials; measuring adds a few
    minutes per size on top. Only meant to warn about long runs."""
    total = 0.0
    for n in sizes:
        total += driver.shape_stats(bench_shape(n), scenarios, jobs)["est_prepare_s"]
        rr = sum(driver.RRSIG_PER_NAME * s + driver.RRSIG_PER_ZONE for s in sample_sizes(n, sample_names))
        total += sign_trials * rr * sum(driver.SIGN_SECONDS_PER_RRSIG.get(s, 0.01) for s in scenarios)
    return total


# ─────────────────────────── status files ───────────────────────────

def _read(path: Path) -> dict:
    return jobs.read_json(path)


def _write_status(d: Path, st: dict) -> None:
    """Atomic: the launcher, the suite process and the dashboard all touch this file."""
    jobs.write_status(d, st)


def _pid_alive(pid: int | None) -> bool:
    return driver.pid_alive(pid)


def status(suite_id: str) -> dict:
    """Current status; a run whose process vanished without finishing is reported as 'stopped'."""
    return driver.effective_job_state(_read(SUITES / suite_id / "status.json"))


def list_suites() -> list[dict]:
    """Saved suites, newest first, with their parameters and status."""
    out = []
    if not SUITES.is_dir():
        return out
    for d in SUITES.iterdir():
        if not d.is_dir() or not (d / "params.json").is_file():
            continue
        params = _read(d / "params.json")
        st = status(d.name)
        try:
            when = datetime.strptime(params.get("stamp", ""), "%Y%m%d-%H%M%S").strftime("%d %b %Y, %H:%M")
        except ValueError:
            when = datetime.fromtimestamp(d.stat().st_mtime).strftime("%d %b %Y, %H:%M")
        label = params.get("label") or ""
        out.append({"id": d.name, "path": d, "params": params, "state": st.get("state", "unknown"), "when": when,
                    "label": label, "title": f"{label or 'benchmark'} - {when}",
                    "complete": st.get("state") == "done" and (d / "suite.json").is_file(),
                    "mtime": d.stat().st_mtime})
    return sorted(out, key=lambda s: s["mtime"], reverse=True)


def running_suite() -> dict | None:
    """The suite that is starting or running, if any."""
    return next((s for s in list_suites() if s["state"] in driver.ACTIVE_STATES), None)


@contextlib.contextmanager
def _start_lock(timeout_s: float = 10.0):
    """Make 'check nothing is running, then launch' atomic across dashboard sessions."""
    SUITES.mkdir(parents=True, exist_ok=True)
    lock = SUITES / ".start.lock"
    deadline = time.time() + timeout_s
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 30:  # left behind by a crashed launcher
                    shutil.rmtree(lock, ignore_errors=True)
                    continue
            except OSError:
                continue
            if time.time() > deadline:
                raise driver.LabBusy("Another benchmark is being started. Try again in a moment.")
            time.sleep(0.1)
    try:
        yield
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def create(params: dict) -> tuple[str, Path]:
    """Make the suite folder and write its parameters. Returns (suite id, folder)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    label = driver.sanitize_label(params.get("label"))[:30]
    suite_id = f"{stamp}_{label}" if label else stamp
    d = SUITES / suite_id
    d.mkdir(parents=True, exist_ok=False)
    params = {**params, "suite_id": suite_id, "stamp": stamp, "label": label}
    (d / "params.json").write_text(json.dumps(params, indent=2), encoding="utf-8")
    # 'started' lets a launch that never produces a process age out instead of blocking everything.
    _write_status(d, {"state": "starting", "step": "starting", "done": 0, "started": time.time(),
                      "total": len(params["sizes"]) * STEPS_PER_SIZE})
    return suite_id, d


def start(params: dict) -> str:
    """Write the parameters and launch the suite as a detached process. Returns the suite id."""
    _minimal.refuse('Starting a benchmark')
    with _start_lock():
        driver.require_no_offline()
        live = running_suite()
        if live:
            raise driver.LabBusy(f"Benchmark {live['id']} is already {live['state']}.")
        building = driver.active_build_stack()
        if building:
            raise driver.LabBusy(f"Images for {building} are still being built. Wait for the build to finish or "
                                 "cancel it first: it would distort the timings.")
        busy = driver.active_prepare_tags()
        if busy:
            raise driver.LabBusy(f"Signing is running for lab {', '.join(busy)}. Wait for it to finish or cancel it "
                                 "first: it would share the CPU and distort the timings.")
        suite_id, d = create(params)
        log = open(d / "progress.log", "ab")
        proc = subprocess.Popen([sys.executable, "-m", "live_lab.suite", str(d / "params.json")],
                                cwd=Path(__file__).resolve().parents[1], stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                **driver.background_process_kwargs())
        st = _read(d / "status.json")
        if st.get("state") == "starting" and not st.get("pid"):
            _write_status(d, {**st, "pid": proc.pid})
        return suite_id


def cancel(suite_id: str) -> str:
    """Stop the suite process and take down the lab it was running."""
    _minimal.refuse('Cancelling a benchmark')
    st = _read(SUITES / suite_id / "status.json")
    jobs.stop_worker(st.get("pid"), wait_s=10, alive=_pid_alive)
    st = _read(SUITES / suite_id / "status.json") or st
    st.update(state="cancelled", step="cancelled by the user", updated=time.time())
    _write_status(SUITES / suite_id, st)
    if st.get("config"):
        try:
            driver.down(driver.LabHandle(config=st["config"], stack=st.get("stack", driver.DEFAULT_STACK)),
                        force=True)
        except Exception:  # noqa: BLE001
            pass
    return "cancelled"


# ─────────────────────────── the run itself ───────────────────────────

class _Run:
    def __init__(self, params: dict):
        self.p = params
        self.dir = SUITES / params["suite_id"]
        self.t0 = time.time()
        self.st = {"state": "running", "pid": os.getpid(), "started": self.t0, "done": 0,
                   "total": len(params["sizes"]) * STEPS_PER_SIZE, "step": "starting", "size": None}

    def log(self, msg: str) -> None:
        print(f"{time.strftime('%H:%M:%S')}  {msg}", flush=True)

    def step(self, msg: str, **extra) -> None:
        self.st.update(step=msg, updated=time.time(), **extra)
        self.write()
        self.log(msg)

    def tick(self) -> None:
        self.st["done"] += 1
        self.write()

    def write(self) -> None:
        _write_status(self.dir, self.st)


def _signing_rows(h: driver.LabHandle, size: int) -> list[dict]:
    rows = driver.manifest_rows(h)
    return [{"size": size, **{k: r.get(k) for k in ("scenario", "origin", "tier", "algorithm", "n_names",
             "rrsig_count", "signed_bytes", "sign_seconds", "verify_seconds")}} for r in rows]


def _check_reusable(h: driver.LabHandle, size: int, scenarios: list[str], reuse: str) -> Path:
    """Make sure the zones on disk are still the ones ``reuse`` signed. Returns its signing-trials file.

    Signing is not repeatable to the second, so the per-zone sign times stored with the zones must equal
    the ones that run recorded; if another run re-signed this size since, they will not.
    """
    old = SUITES / reuse
    trials = old / f"signing_trials_{size}.csv"
    full = old / "signing_full.csv"
    fix = ". Run it once without --reuse."
    missing = [s for s in scenarios if driver.load_manifest(h, s) is None]
    if missing:
        raise RuntimeError(f"cannot reuse {reuse} for {size} names: no signed zones for {', '.join(missing)}{fix}")
    if not trials.is_file() or not full.is_file():
        raise RuntimeError(f"cannot reuse {reuse}: its signing files for {size} names are missing{fix}")
    with full.open(encoding="utf-8") as f:
        before = {(r["scenario"], r["origin"]): float(r["sign_seconds"])
                  for r in csv.DictReader(f) if int(r["size"]) == size}
    now = {(r["scenario"], r["origin"]): float(r["sign_seconds"]) for r in _signing_rows(h, size)}
    changed = sorted({s for (s, o), t in now.items() if s in scenarios and abs(before.get((s, o), -1) - t) > 0.0005})
    if changed:
        raise RuntimeError(f"the zones for {size} names were signed again after {reuse} ({', '.join(changed)}), "
                           f"so its signing numbers no longer match{fix}")
    return trials


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def run_suite(p: dict) -> None:
    os.environ[driver.SUITE_OWNER_ENV] = p["suite_id"]  # lets this process use the lab it is guarding
    r = _Run(p)
    sizes, scenarios = [int(n) for n in p["sizes"]], list(p["scenarios"])
    trials, udp = int(p["trials"]), int(p["udp"])
    networks = list(p["networks"])
    detail: dict[str, dict] = {}
    full_rows: list[dict] = []
    try:
        for i, n in enumerate(sizes, 1):
            tag = f"[{i}/{len(sizes)}] {n} names"
            cfgpath = driver.write_ui_config(
                shape=bench_shape(n), scenarios=scenarios, n_queries=int(p["n_queries"]), trials=trials,
                network_run=networks, udp_limits=[udp], workload=p.get("workload"), profiles=p.get("profiles"),
                out_config=f"configs/_bench-{n}.yaml")
            h = driver.LabHandle(config=cfgpath, stack=p["stack"])
            r.st.update(size=n, config=cfgpath, stack=p["stack"])

            reuse = p.get("reuse")
            if reuse:
                src = _check_reusable(h, n, scenarios, reuse)
                r.step(f"{tag}: reusing the zones already signed in {reuse}")
                shutil.copyfile(src, r.dir / src.name)
                r.tick()
                r.tick()
            else:
                r.step(f"{tag}: signing every zone with {', '.join(scenarios)}")
                r.log(driver.prepare(h, jobs=int(p.get("jobs", 1)), scenarios=scenarios)[-1500:])
                r.tick()

                sample = sample_sizes(n, int(p["sample_names"]))
                r.step(f"{tag}: repeating sign and verify {p['sign_trials']} times on samples of "
                       f"{' and '.join(str(s) for s in sample)} names")
                out = f"/eco/results/suites/{p['suite_id']}/signing_trials_{n}.csv"
                r.log(driver.sign_trials(h, scenarios, int(p["sign_trials"]), sample, out)[-1500:])
                r.tick()

            r.step(f"{tag}: starting the lab")
            driver.up(h)
            r.tick()

            r.step(f"{tag}: measuring {trials} trial(s) of {p['n_queries']} lookups per scenario and network")
            label = driver.sanitize_label(f"{p.get('label') or 'bench'}-n{n}")
            r.log(driver.run_benchmark(h, scenarios=scenarios, profiles=networks, trials=trials, udp=udp,
                                       run_label=label)[-1500:])
            newest = driver.list_runs(h)
            if not newest:
                raise RuntimeError(f"no benchmark run was saved for {n} names")
            full_rows += _signing_rows(h, n)
            detail[str(n)] = {"tag": h.tag, "run_folder": newest[0]["folder"], "names": n,
                              "zones": 1 + 1 + bench_shape(n)["sld_count"], "config": cfgpath}
            r.tick()

            r.step(f"{tag}: stopping the lab")
            driver.down(h)
            r.tick()

        _write_csv(r.dir / "signing_full.csv", full_rows)
        suite = {**p, "finished": time.strftime("%Y%m%d-%H%M%S"), "seconds": round(time.time() - r.t0),
                 "sizes_detail": detail,
                 "host": {"platform": platform.platform(), "cpus": os.cpu_count(), "python": platform.python_version()},
                 "provenance": driver.provenance(p["stack"])}
        (r.dir / "suite.json").write_text(json.dumps(suite, indent=2), encoding="utf-8")
        try:
            from live_lab import suite_report
            report = suite_report.write(r.dir)
            r.log(f"report: {report}")
        except Exception as e:  # noqa: BLE001 — measurements already saved; report is additive
            r.log(f"report generation failed: {e}")
        r.st.update(state="done", step="finished", updated=time.time())
        r.write()
        r.log(f"suite finished in {suite['seconds']} s")
    except Exception as e:  # noqa: BLE001
        r.st.update(state="failed", step="failed", error=str(e)[-1500:], updated=time.time())
        r.write()
        r.log("FAILED\n" + traceback.format_exc())
        try:
            if r.st.get("config"):
                driver.down(driver.LabHandle(config=r.st["config"], stack=p["stack"]))
        except Exception:  # noqa: BLE001
            pass
        raise SystemExit(1)


class _Tee:
    """Copy everything printed to the console into the suite's progress.log as well."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for s in self.streams:
            s.write(text)
        return len(text)

    def flush(self):
        for s in self.streams:
            s.flush()


def _cli_parser():
    import argparse
    p = argparse.ArgumentParser(
        prog="suite",
        description="Run a benchmark from the command line. The result is saved like a dashboard run and "
                    "appears in the dashboard's Benchmark tab. Example: "
                    "python dashboard/live_lab/suite.py --sizes 10000 --trials 5 --name full-10000")
    p.add_argument("params", nargs="?", help="a params.json written by the dashboard (internal use)")
    p.add_argument("--sizes", default=",".join(str(n) for n in BENCH_SIZES), help="dataset sizes in names, "
                   "comma separated (default 50,500,5000)")
    p.add_argument("--scenarios", default="ecdsa,slh-dsa,mtl,rsa,ed25519",
                   help="comma separated (default ecdsa,slh-dsa,mtl,rsa,ed25519)")
    p.add_argument("--trials", type=int, default=3, help="measurement trials (cold then warm)")
    p.add_argument("--sign-trials", type=int, default=DEFAULT_SIGN_TRIALS, help="repeated sign/verify trials")
    p.add_argument("--sample", type=int, default=DEFAULT_SAMPLE_NAMES, help="names in the signing sample zone")
    p.add_argument("--queries", type=int, default=500, help="lookups per cold or warm pass")
    p.add_argument("--networks", default="lan", help="lan, wan, lossy (comma separated)")
    p.add_argument("--udp", type=int, default=1232, choices=[1232, 4096])
    p.add_argument("--jobs", type=int, default=1, help="zones signed in parallel (1 = clean timings)")
    p.add_argument("--stack", default=driver.DEFAULT_STACK)
    p.add_argument("--name", default="", help="name shown in the dashboard")
    p.add_argument("--reuse", default="", metavar="SUITE", help="measure again on the zones an earlier run "
                   "already signed (name of its folder under ecosystem/results/suites); skips signing. "
                   "Needs the same sizes and algorithms, and nothing else must have re-signed them since")
    p.add_argument("--yes", action="store_true", help="allow a run that needs more than 12 hours")
    p.add_argument("--plan", action="store_true", help="only show what would run and the time estimate")
    return p


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    args = _cli_parser().parse_args(argv)
    if args.params:                      # started by the dashboard
        run_suite(json.loads(Path(args.params).read_text(encoding="utf-8")))
        return
    split = lambda s: [t.strip() for t in s.split(",") if t.strip()]  # noqa: E731
    sizes = sorted(int(n) for n in split(args.sizes))
    if not sizes or sizes[0] < 1:
        raise SystemExit("--sizes needs whole numbers of 1 or more, e.g. --sizes 10000")
    scenarios, networks = split(args.scenarios), split(args.networks)
    bad = [s for s in scenarios if s not in driver.SCENARIO_ALGS]
    if bad:
        raise SystemExit(f"unknown scenario(s) {bad}; choose from {', '.join(driver.SCENARIO_ALGS)}")
    bad = [n for n in networks if n not in driver.PROFILE_DEFAULTS]
    if bad:
        raise SystemExit(f"unknown network(s) {bad}; choose from {', '.join(driver.PROFILE_DEFAULTS)}")
    if args.reuse and not (SUITES / args.reuse / "suite.json").is_file():
        raise SystemExit(f"--reuse: no finished run named {args.reuse!r} in {SUITES}")
    est = 0.0 if args.reuse else estimate_seconds(sizes, scenarios, args.jobs, args.sign_trials, args.sample)
    print(f"Benchmark: sizes {sizes} · {', '.join(scenarios)} · {args.trials} measurement trial(s), "
          f"{args.sign_trials} signing trial(s) · networks {', '.join(networks)}")
    if args.reuse:
        print(f"Reusing the zones signed in {args.reuse}: no signing, just measuring (a few minutes per network).")
    else:
        print(f"Signing and sign trials about {driver.format_duration(est)}, plus measuring a few minutes per size.")
    if args.plan:
        return
    if est > LONG_RUN_S and not args.yes:
        raise SystemExit(f"That is more than {LONG_RUN_S // 3600} hours of signing alone. If you really mean it, "
                         "add --yes (use --plan to preview).")
    params = {
        "label": args.name, "stack": args.stack, "reuse": args.reuse, "sizes": sizes, "scenarios": scenarios,
        "trials": args.trials, "sign_trials": args.sign_trials, "sample_names": args.sample, "n_queries": args.queries,
        "networks": networks, "udp": args.udp, "jobs": args.jobs,
        "workload": dict(driver.WORKLOAD_DEFAULTS),
        "profiles": {k: dict(v) for k, v in driver.PROFILE_DEFAULTS.items() if k != "lan"},
    }
    suite_id, d = create(params)
    print(f"Saved as {suite_id}; it shows up in the dashboard's Benchmark tab. Keep this window open.\n")
    with open(d / "progress.log", "a", encoding="utf-8") as log:
        sys.stdout = _Tee(sys.__stdout__, log)
        try:
            run_suite(json.loads((d / "params.json").read_text(encoding="utf-8")))
        finally:
            sys.stdout = sys.__stdout__


if __name__ == "__main__":
    main()
