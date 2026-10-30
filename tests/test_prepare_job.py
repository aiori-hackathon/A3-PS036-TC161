"""Tests for the live Prepare view: the exact RRSIG count, the signer's events, the background job and
the monitor's state. No Docker and no Streamlit."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "ecosystem" / "src"))

from ecolab import sign  # noqa: E402
from ecolab.config import ZoneSpec  # noqa: E402
from live_lab import driver, prepare_job as pj, prepare_story  # noqa: E402

SIGNED = """\
$ORIGIN lab.
lab.\t3600\tIN\tSOA\tns1.lab. h.lab. 1 7200 3600 1209600 3600
lab.\t3600\tIN\tRRSIG\tSOA 15 1 3600 20300101000000 20200101000000 1234 lab. AAAA
lab.\t3600\tIN\tNS\tns1.lab.
lab.\t3600\tIN\tRRSIG\tNS 15 1 3600 20300101000000 20200101000000 1234 lab. BBBB
abc.lab.\t3600\tIN\tNSEC3\t1 0 5 - NEXT NS SOA RRSIG DNSKEY NSEC3PARAM
abc.lab.\t3600\tIN\tRRSIG\tNSEC3 15 1 3600 20300101000000 20200101000000 1234 lab. CCCC
x.lab.\t300\tIN\tTXT\t"the word RRSIG in a text record"
"""


def _signed_file(tmp_path, text=SIGNED):
    p = tmp_path / "lab.zone.signed"
    p.write_text(text, encoding="utf-8")
    return p


# ───────────── exact RRSIG count ─────────────

def test_rrsig_count_ignores_nsec3_bitmaps_and_text(tmp_path):
    p = _signed_file(tmp_path)
    assert sign._rrsig_count(p) == 3  # not 4 (NSEC3 bitmap) and not 5 (TXT)


def test_ecolab_and_dashboard_count_the_same(tmp_path):
    p = _signed_file(tmp_path)
    assert sign._rrsig_count(p) == prepare_story.rrsig_profile(p)["total"]


def test_old_manifest_is_recounted_from_the_signed_file(tmp_path):
    zdir = tmp_path / "mtl"
    zdir.mkdir()
    _signed_file(zdir)
    (zdir / "manifest.json").write_text(json.dumps(
        {"zones": [{"origin": "lab.", "signed_file": "lab.zone.signed", "rrsig_count": 5},
                   {"origin": "gone.", "signed_file": "missing.signed", "rrsig_count": 9}]}), encoding="utf-8")
    man = driver._read_manifest(zdir / "manifest.json")
    fixed, missing = man["zones"]
    assert fixed["rrsig_count"] == 3 and fixed["rrsig_count_manifest"] == 5
    assert missing["rrsig_count"] == 9 and "rrsig_count_manifest" not in missing  # no file: left as stored


def test_new_manifest_is_left_alone(tmp_path):
    zdir = tmp_path / "mtl"
    zdir.mkdir()
    _signed_file(zdir)
    (zdir / "manifest.json").write_text(json.dumps(
        {"zones": [{"origin": "lab.", "signed_file": "lab.zone.signed", "rrsig_count": 3}]}), encoding="utf-8")
    z = driver._read_manifest(zdir / "manifest.json")["zones"][0]
    assert z["rrsig_count"] == 3 and "rrsig_count_manifest" not in z


@pytest.mark.parametrize("preset, total", [("small", 55), ("medium", 353), ("large", 1698)])
def test_planned_rrsigs_use_the_exact_model(preset, total):
    # small/medium totals were measured on real signed zones (17/17/11/10 and 57/107/57/107 + 15 + 10)
    assert driver.shape_stats(driver.ZONE_SIZE_PRESETS[preset], ["mtl"])["est_rrsigs"] == total


def test_exact_rrsigs_formula_by_tier():
    # 2 leaf zones of 5 names under one TLD: leaf 2*5+7 each, TLD 2*2+7, root 2*1+8
    assert driver.exact_rrsigs(10, 4) == 17 + 17 + 11 + 10


# ───────────── the signer's events ─────────────

def _fake_lab(tmp_path):
    zones = [ZoneSpec("a.test.", "sld", "s1", "test.", 5), ZoneSpec("b.test.", "sld", "s1", "test.", 5),
             ZoneSpec("test.", "tld", "t1", ".", 0), ZoneSpec(".", "root", "r", None, 0)]
    return SimpleNamespace(cfg={"scenarios": {"mtl": {"sld": "A", "tld": "A", "root": "A"}}, "signing": {}},
                           zones=zones, work=tmp_path)


def _fake_sign_zone(lab, scenario, z, child_ds, out):
    return {"origin": z.origin, "tier": z.tier, "algorithm": "A", "n_names": z.n_names, "ds": ["ds"],
            "signed_file": "x", "signed_bytes": 1000, "rrsig_count": 3, "sign_seconds": 0.5,
            "verify_seconds": 0.1}


def _scenario_events(tmp_path, monkeypatch, capsys, on):
    monkeypatch.setenv("ECOLAB_EVENTS", "1" if on else "0")
    monkeypatch.setattr(sign, "sign_zone", _fake_sign_zone)
    sign.sign_scenario(_fake_lab(tmp_path), "mtl", jobs=2, log=lambda *_: None)
    lines = capsys.readouterr().out.splitlines()
    return [json.loads(x[len(pj.EVENT_PREFIX):]) for x in lines if x.startswith(pj.EVENT_PREFIX)]


def test_sign_scenario_emits_events_in_order(tmp_path, monkeypatch, capsys):
    evs = _scenario_events(tmp_path, monkeypatch, capsys, on=True)
    kinds = [e["event"] for e in evs]
    assert kinds[0] == "scenario_start" and kinds[-1] == "scenario_done"
    assert kinds.count("zone_start") == kinds.count("zone_done") == 4
    plan = evs[0]["zones"]
    assert {z["origin"] for z in plan} == {"a.test.", "b.test.", "test.", "."}
    assert next(z for z in plan if z["origin"] == "test.")["parent"] == "."
    # bottom-up: both leaf zones finish before the TLD starts, and the TLD before the root
    pos = {(e["event"], e.get("origin")): i for i, e in enumerate(evs)}
    assert max(pos[("zone_done", "a.test.")], pos[("zone_done", "b.test.")]) < pos[("zone_start", "test.")]
    assert pos[("zone_done", "test.")] < pos[("zone_start", ".")]
    done = next(e for e in evs if e["event"] == "zone_done")
    assert done["rrsigs"] == 3 and done["sign_s"] == 0.5 and done["signed_bytes"] == 1000


def test_sign_scenario_is_silent_without_the_switch(tmp_path, monkeypatch, capsys):
    assert _scenario_events(tmp_path, monkeypatch, capsys, on=False) == []


# ───────────── live state ─────────────

PLAN = [{"origin": "a.test.", "tier": "sld", "parent": "test.", "n_names": 5},
        {"origin": "b.test.", "tier": "sld", "parent": "test.", "n_names": 5},
        {"origin": "test.", "tier": "tld", "parent": ".", "n_names": 0},
        {"origin": ".", "tier": "root", "parent": None, "n_names": 0}]


def _ev(kind, t, **kw):
    return {"event": kind, "t": t, **kw}


def _start(s="mtl", t=100.0):
    return _ev("scenario_start", t, scenario=s, algorithms={"sld": "A", "tld": "A", "root": "A"}, zones=PLAN)


def test_live_state_before_any_zone_starts():
    st = pj.live_state([_start()], expected=["mtl", "ecdsa"], now=101)
    assert st["zones_total"] == 8 and st["zones_done"] == 0 and st["fraction"] == 0
    assert st["pending"] == ["ecdsa"] and st["current"] == "mtl"
    assert st["scenarios"][0]["state"] == "waiting"


def test_live_state_tracks_signing_and_done_zones():
    evs = [_start(), _ev("zone_start", 101, scenario="mtl", origin="a.test.", tier="sld"),
           _ev("zone_start", 101, scenario="mtl", origin="b.test.", tier="sld"),
           _ev("zone_done", 105, scenario="mtl", origin="a.test.", tier="sld", rrsigs=17, sign_s=4.0,
               verify_s=0.1, signed_bytes=9, algorithm="A")]
    st = pj.live_state(evs, expected=["mtl"], now=107)
    sc = st["scenarios"][0]
    zones = {z["origin"]: z for z in sc["zones"]}
    assert zones["a.test."]["state"] == "done" and zones["a.test."]["rrsigs"] == 17
    assert zones["b.test."]["state"] == "signing" and zones["b.test."]["elapsed"] == 6  # live clock
    assert zones["test."]["state"] == "waiting" and zones["test."]["elapsed"] is None
    assert sc["state"] == "signing" and sc["tiers"]["sld"] == {"total": 2, "signing": 1, "done": 1}
    assert st["zones_done"] == 1 and st["zones_total"] == 4 and st["fraction"] == 0.25


def test_live_state_moves_to_the_next_scenario_and_finishes():
    evs = [_start("ecdsa", 100), _ev("scenario_done", 110, scenario="ecdsa"), _start("mtl", 111)]
    st = pj.live_state(evs, expected=["ecdsa", "mtl", "slh-dsa"], now=112)
    assert [x["state"] for x in st["scenarios"]] == ["done", "waiting"]
    assert st["current"] == "mtl" and st["pending"] == ["slh-dsa"] and st["finished"] is False
    assert pj.live_state(evs + [_ev("prepare_done", 120)], now=121)["finished"] is True


def test_live_state_ignores_unknown_zones_and_empty_input():
    assert pj.live_state([])["scenarios"] == [] and pj.live_state([])["fraction"] == 0
    evs = [_start(), _ev("zone_start", 101, scenario="mtl", origin="nope.", tier="sld"),
           _ev("zone_start", 101, scenario="other", origin="a.test.", tier="sld")]
    assert pj.live_state(evs, now=102)["scenarios"][0]["state"] == "waiting"


def test_live_dot_colours_follow_state_and_hold_no_clock():
    evs = [_start(), _ev("zone_start", 101, scenario="mtl", origin="a.test.", tier="sld"),
           _ev("zone_done", 103, scenario="mtl", origin="b.test.", tier="sld", rrsigs=17, sign_s=2.0,
               verify_s=0.1, signed_bytes=9, algorithm="A")]
    cur = pj.live_state(evs, now=110)["scenarios"][0]
    dot = pj.live_dot(cur)
    assert '"test." -> "a.test."' in dot and '"." -> "test."' in dot
    assert "17 RRSIGs · 2.0 s" in dot and "signing…" in dot
    assert pj._DOT_FILL["done"] in dot and pj._DOT_FILL["waiting"] in dot
    assert dot == pj.live_dot(pj.live_state(evs, now=999)["scenarios"][0])  # same picture later


def test_live_dot_folds_large_hierarchies():
    plan = [{"origin": f"z{i}.test.", "tier": "sld", "parent": "test.", "n_names": 1} for i in range(60)]
    plan += [{"origin": "test.", "tier": "tld", "parent": ".", "n_names": 0},
             {"origin": ".", "tier": "root", "parent": None, "n_names": 0}]
    evs = [_ev("scenario_start", 1, scenario="mtl", algorithms={}, zones=plan),
           _ev("zone_done", 2, scenario="mtl", origin="z0.test.", tier="sld", rrsigs=9, sign_s=1, verify_s=0,
               signed_bytes=1, algorithm="A")]
    dot = pj.live_dot(pj.live_state(evs, now=3)["scenarios"][0])
    assert "60 leaf zones" in dot and "1 signed" in dot and "z7.test." not in dot


# ───────────── the background worker ─────────────

class _FakeProc:
    def __init__(self, lines, rc):
        self.stdout, self._rc = iter(lines), rc

    def wait(self):
        return self._rc


@pytest.fixture
def job(tmp_path, monkeypatch):
    h = driver.LabHandle(config="configs/lab.yaml")
    monkeypatch.setattr(pj, "job_dir", lambda _h: tmp_path)
    monkeypatch.setattr(pj.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=""))
    saved = {}
    monkeypatch.setattr(driver, "prepare_summary", lambda *_a, **_k: {"config": "pinned.yaml", "scenarios": []})
    monkeypatch.setattr(driver, "load_session", lambda _h: {})
    monkeypatch.setattr(driver, "save_session", lambda _h, s: saved.update(s))
    (tmp_path / "status.json").write_text("{}", encoding="utf-8")
    params = {"config": "configs/lab.yaml", "lab_config": "configs/lab.yaml", "stack": h.stack, "jobs": 2,
              "scenarios": ["mtl"], "started": 1.0}
    return SimpleNamespace(h=h, dir=tmp_path, params=params, saved=saved)


def test_worker_splits_events_from_log_and_saves_the_session(job):
    out = ["[mtl] a.test. ...\n", pj.EVENT_PREFIX + json.dumps(_start()) + "\n", "Compose file: x\n"]
    rc = pj.run(job.params, popen=lambda *a, **k: _FakeProc(out, 0))
    assert rc == 0
    assert [json.loads(x)["event"] for x in (job.dir / "events.jsonl").read_text().splitlines()] == ["scenario_start"]
    log = (job.dir / "progress.log").read_text()
    assert "a.test." in log and "@@EVENT" not in log
    st = json.loads((job.dir / "status.json").read_text())
    assert st["state"] == "done" and st["wall_s"] >= 0 and st["summary"]["config"] == "configs/lab.yaml"
    assert job.saved["show_prepare_metrics"] is True and job.saved["prepare_wall_s"] == st["wall_s"]


def test_worker_reports_a_failed_signing(job):
    rc = pj.run(job.params, popen=lambda *a, **k: _FakeProc(["boom: ldns-signzone failed\n"], 2))
    st = json.loads((job.dir / "status.json").read_text())
    assert rc == 2 and st["state"] == "failed" and "ldns-signzone failed" in st["error"]
    assert job.saved == {}  # a failed Prepare must not mark the lab as prepared


def test_worker_survives_docker_missing(job):
    def no_docker(*a, **k):
        raise FileNotFoundError("docker")
    pj.run(job.params, popen=no_docker)
    st = json.loads((job.dir / "status.json").read_text())
    assert st["state"] == "failed" and "FileNotFoundError" in st["error"]


def test_cancelled_job_is_not_overwritten_by_the_worker(job):
    def cancelling_popen(*a, **k):
        (job.dir / "status.json").write_text(json.dumps({"state": "cancelled"}), encoding="utf-8")
        return _FakeProc([], 1)
    pj.run(job.params, popen=cancelling_popen)
    assert json.loads((job.dir / "status.json").read_text())["state"] == "cancelled"


def test_status_flags_a_worker_that_died(job, monkeypatch):
    (job.dir / "status.json").write_text(json.dumps({"state": "running", "pid": 424242}), encoding="utf-8")
    monkeypatch.setattr(pj.driver, "pid_alive", lambda pid: False)
    st = pj.status(job.h)
    assert st["state"] == "stopped" and "ended before finishing" in st["error"]
    assert not pj.is_running(job.h)


def test_mark_handled_is_remembered(job):
    (job.dir / "status.json").write_text(json.dumps({"state": "done"}), encoding="utf-8")
    pj.mark_handled(job.h)
    assert pj.status(job.h)["handled"] is True


def test_events_skip_a_line_cut_short(job):
    (job.dir / "events.jsonl").write_text('{"event": "zone_start"}\n{"event": "zon', encoding="utf-8")
    assert pj.events(job.h) == [{"event": "zone_start"}]


def test_prepare_command_adds_events_only_when_asked():
    h = driver.LabHandle(config="configs/lab.yaml")
    plain = driver.prepare_command(h, 2, ["mtl"])
    live = driver.prepare_command(h, 2, ["mtl"], events=True)
    assert "ECOLAB_EVENTS=1" not in plain and "--name" not in plain
    assert "ECOLAB_EVENTS=1" in live and driver.prepare_container_name(h) in live
    assert plain[-1] == live[-1] and plain[-1].endswith("--jobs 2 --scenarios mtl")


def test_background_workers_have_a_hidden_console_not_none():
    # DETACHED_PROCESS (0x8) gives a worker no console, so each docker call it makes opens a visible window.
    kw = driver.background_process_kwargs()
    if sys.platform == "win32":
        flags = kw["creationflags"]
        assert flags & 0x08000000 and not flags & 0x00000008  # CREATE_NO_WINDOW yes, DETACHED_PROCESS no
        assert driver.NO_WINDOW == 0x08000000
    else:
        assert kw == {"start_new_session": True} and driver.NO_WINDOW == 0
