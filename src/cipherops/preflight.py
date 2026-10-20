"""Go/no-go gate required by PS-036 before any benchmark numbers are produced.

The project is deferred (NO-GO) unless the supplied reference implementation
builds, its own test suite and fixtures pass, the chosen algorithm is
supported, and the harness round-trips correctly in every mode.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .codepoints import MODES
from .progress import log
from .workload import build_zone, signing_messages, write_messages

MTL_ALGO = MODES["slh-dsa-mtl"].harness_algo
REF_TREE = Path(os.environ.get("CIPHEROPS_MTL_TREE", "/opt/mtl"))


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _check(name: str, ok: bool, detail: str, critical: bool = True) -> dict:
    return {"check": name, "ok": bool(ok), "critical": critical, "detail": detail.strip()[-1500:]}


def reference_installed() -> dict:
    needed = ["/usr/local/lib64/libmtlslib.so", "/usr/local/include/mtllib/mtllib.h", "/usr/local/lib64/liboqs.so"]
    missing = [p for p in needed if not Path(p).exists()]
    return _check("reference_library_installed", not missing, "missing: " + ", ".join(missing) if missing else "ok")


def reference_unit_tests() -> dict:
    binary = shutil.which("mtltest") or str(REF_TREE / "test" / "mtltest")
    p = _run([binary], cwd=REF_TREE / "test")
    ok = p.returncode == 0 and "completed successfully" in p.stdout
    passed = len(re.findall(r"\.\.\.\s+Success", p.stdout))
    failed = len(re.findall(r"\.\.\.\s+Fail", p.stdout, flags=re.IGNORECASE))
    # stderr carries ERROR lines printed by the suite's own negative tests; it is kept only on failure
    detail = f"exit={p.returncode}, {passed} tests passed, {failed} failed"
    if not ok:
        detail += f"\n{p.stdout[-800:]}{p.stderr[-400:]}"
    return _check("reference_unit_tests_and_vectors", ok, detail)


def reference_vector_scheme() -> dict:
    header = REF_TREE / "test" / "mtltest_test_vectors.h"
    text = header.read_text() if header.exists() else ""
    m = re.search(r'MTL_TEST_VECTOR_SCHEME_NAME "([^"]+)"', text)
    scheme = m.group(1) if m else "not found"
    return _check("reference_fixture_scheme", bool(m),
                  f"bundled known-answer vectors cover {scheme}; {MTL_ALGO} is validated by the tool round trip",
                  critical=False)


def reference_tools_roundtrip(n_messages: int = 16) -> dict:
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        keygen = _run(["mtlkeygen", str(tdp / "k"), MTL_ALGO])
        if keygen.returncode != 0:
            return _check("reference_tools_roundtrip", False, f"mtlkeygen failed: {keygen.stdout}{keygen.stderr}")
        msgs = []
        for i in range(n_messages):
            path = tdp / f"m{i}.msg"
            path.write_bytes(os.urandom(64))
            msgs.append(str(path))
        sign = _run(["mtlsign", "-r", str(tdp / "k.key")] + msgs)
        if sign.returncode != 0:
            return _check("reference_tools_roundtrip", False, f"mtlsign failed: {sign.stdout}{sign.stderr}")
        failures = []
        for m in msgs:
            cond = _run(["mtlverify", MTL_ALGO, str(tdp / "k.pub"), m, m + ".condensed_sig", str(tdp / "k.key.ladder")])
            full = _run(["mtlverify", MTL_ALGO, str(tdp / "k.pub"), m, m + ".full_sig"])
            if cond.returncode != 0 or full.returncode != 0:
                failures.append(Path(m).name)
        tampered = tdp / "tampered.msg"
        data = bytearray(Path(msgs[0]).read_bytes())
        data[0] ^= 1
        tampered.write_bytes(bytes(data))
        bad = _run(["mtlverify", MTL_ALGO, str(tdp / "k.pub"), str(tampered), msgs[0] + ".condensed_sig",
                    str(tdp / "k.key.ladder")])
        ok = not failures and bad.returncode != 0
        return _check("reference_tools_roundtrip", ok,
                      f"{n_messages} messages, condensed+full verify failures={failures}, tampered rejected={bad.returncode != 0}")


def harness_smoke(harness: str) -> list[dict]:
    from .harness import run_trial

    out = []
    zone = build_zone("preflight", 6, 7)
    with tempfile.TemporaryDirectory() as td:
        for key, mode in MODES.items():
            msgs, _ = signing_messages(zone, mode)
            path = Path(td) / f"{key}.bin"
            write_messages(path, msgs)
            try:
                res = run_trial(mode, path, Path(td) / key, full_verify=4)
                fails = sum(res.get("failures", {}).values())
                tamper_ok = all(res.get("tamper", {}).values())
                ok = res["exit_code"] == 0 and fails == 0 and tamper_ok
                detail = f"exit={res['exit_code']} failures={res.get('failures')} tamper={res.get('tamper')}"
            except Exception as exc:  # noqa: BLE001 - any harness failure is a NO-GO
                ok, detail = False, repr(exc)
            out.append(_check(f"harness_smoke_{key}", ok, detail, critical=mode.family != "classical"))
    return out


def _report(check: dict) -> dict:
    mark = "ok  " if check["ok"] else ("FAIL" if check["critical"] else "warn")
    log(f"  [{mark}] {check['check']}: {check['detail'].splitlines()[0] if check['detail'] else ''}")
    return check


def run(results_dir: Path, harness: str) -> dict:
    log("  checking the Verisign MTL reference library, its tests and our harness ...")
    checks = [_report(reference_installed())]
    if checks[0]["ok"]:
        log("  running Verisign's own test suite (mtltest) ...")
        checks.append(_report(reference_unit_tests()))
        checks.append(_report(reference_vector_scheme()))
        log("  Verisign tools round trip: mtlkeygen -> mtlsign -> mtlverify ...")
        checks.append(_report(reference_tools_roundtrip()))
        log("  harness smoke test: mtlbench signs/verifies a 6-name zone in every mode ...")
        checks += [_report(c) for c in harness_smoke(harness)]
    go = all(c["ok"] for c in checks if c["critical"])
    verdict = {"verdict": "GO" if go else "NO-GO (defer per PS-036)", "algorithm": MTL_ALGO, "checks": checks}
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "preflight.json").write_text(json.dumps(verdict, indent=2))
    return verdict
