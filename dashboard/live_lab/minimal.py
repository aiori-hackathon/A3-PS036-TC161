"""Minimal mode (``CIPHEROPS_MINIMAL=1``): a read-mostly dashboard for evaluators on a shared, running node.

Visitors can explore what has already been run (offline results, simulated-lab benchmarks, Compare, Learn) and send
DNS lookups to the running lab through a restricted dig terminal. They cannot change the lab: building or pulling
images, signing zones, starting or stopping containers, switching the active algorithm, flushing the resolver,
capturing packets, starting or cancelling benchmarks, and Reset are all refused here, in the code that performs them,
not only hidden in the UI. Nothing a visitor does is written to the team's shared files (lab config, saved
preferences, query session, activity log).

This module has no dashboard imports, so the low-level modules (lab, artifacts, jobs) can use it.
"""

from __future__ import annotations

import ipaddress
import os
import threading
import time

ENV = "CIPHEROPS_MINIMAL"
PASSWORD_ENV = "CIPHEROPS_MINIMAL_PASSWORD"

# Pages a visitor may open, and the steps of each track they may see.
ALLOWED_MODES = ("home", "offline", "simulated", "compare", "learn")
ALLOWED_STEPS = {"offline": ("results",), "simulated": ("query", "benchmark")}

# The lab's private network (ecolab.config SUBNET). dig may only be pointed at it.
LAB_NETWORK = ipaddress.ip_network("10.53.0.0/24")

# One lookup per visitor every few seconds, and at most a few at once across all visitors.
COOLDOWN_S = 3.0
MAX_CONCURRENT = 3
DIG_TIMEOUT_S = 10

NOTICE = ("**Minimal mode.** You are exploring a running CipherOps node: the results already produced, "
          "Compare, Learn, and DNS lookups against the live lab. Changing the lab (images, signing, starting or "
          "stopping, switching algorithms, flushing the cache, running benchmarks, Reset) is turned off here so "
          "the shared environment stays as it is.")
FULL_EXPERIENCE = ("To use every feature, deploy your own copy by following the "
                   "[README](https://github.com/aiori-hackathon/A3-PS036-TC161#readme): on your machine, in GitHub "
                   "Codespaces, or on AWS with `deploy/aws`.")

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)


class MinimalModeRefused(RuntimeError):
    """An action that would change the shared lab was attempted in minimal mode."""


def enabled() -> bool:
    """On for 1/true/yes/on. Quotes and spaces are ignored: Windows `set X=1 && ...` stores "1 " and `set X="1"` keeps the quotes."""
    return os.environ.get(ENV, "").strip().strip("\"'").strip().lower() in ("1", "true", "yes", "on")


def password() -> str:
    return os.environ.get(PASSWORD_ENV, "")


def refuse(what: str) -> None:
    """Raise if minimal mode is on. Call it first thing in any function that changes the lab or its files."""
    if enabled():
        raise MinimalModeRefused(f"{what} is turned off in minimal mode, so the shared lab stays as it is. "
                                 "Deploy your own copy (see the README) to use it.")


def mode_allowed(mode: str) -> bool:
    return not enabled() or mode in ALLOWED_MODES


def steps(track: str, all_steps: tuple) -> tuple:
    """The (key, label) steps of a track that may be shown."""
    if not enabled():
        return all_steps
    allowed = ALLOWED_STEPS.get(track, ())
    return tuple(s for s in all_steps if s[0] in allowed)


def check_lab_server(server: str) -> str:
    """In minimal mode, a lookup may only go to an address on the lab's private network."""
    if not enabled():
        return server
    try:
        ip = ipaddress.ip_address(server)
    except ValueError:
        raise MinimalModeRefused(f"Use one of the lab's addresses ({LAB_NETWORK}), not {server!r}.") from None
    if ip not in LAB_NETWORK:
        raise MinimalModeRefused(f"Only the lab's own servers ({LAB_NETWORK}) can be queried in minimal mode.")
    return server


class lookup_slot:
    """Context manager: in minimal mode, wait briefly for one of the few shared lookup slots, else refuse."""

    def __enter__(self):
        self._held = False
        if enabled():
            if not _slots.acquire(timeout=15):
                raise MinimalModeRefused("The lab is busy with other visitors' lookups. Try again in a moment.")
            self._held = True
        return self

    def __exit__(self, *exc):
        if self._held:
            _slots.release()
        return False


def cooldown(store: dict, key: str = "_minimal_last_lookup") -> float:
    """Seconds this visitor still has to wait before the next lookup (0 when they may go). ``store`` is the
    visitor's session state. Records the attempt when it is allowed."""
    if not enabled():
        return 0.0
    now = time.monotonic()
    wait = COOLDOWN_S - (now - float(store.get(key, 0.0)))
    if wait > 0:
        return wait
    store[key] = now
    return 0.0
