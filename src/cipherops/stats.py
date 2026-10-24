"""Summary statistics and bootstrap confidence intervals."""

from __future__ import annotations

import numpy as np


def summarize(values) -> dict:
    a = np.asarray(values, dtype=float)
    if a.size == 0:
        return {"n": 0}
    return {
        "n": int(a.size),
        "mean": float(a.mean()),
        "median": float(np.median(a)),
        "std": float(a.std(ddof=1)) if a.size > 1 else 0.0,
        "p05": float(np.percentile(a, 5)),
        "p95": float(np.percentile(a, 95)),
        "min": float(a.min()),
        "max": float(a.max()),
    }


def bootstrap_ci(values, stat=np.median, n_boot: int = 2000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float]:
    a = np.asarray(values, dtype=float)
    if a.size == 0:
        return (float("nan"), float("nan"))
    if a.size == 1:
        return (float(a[0]), float(a[0]))
    rng = np.random.default_rng(seed)
    samples = rng.choice(a, size=(n_boot, a.size), replace=True)
    stats = np.apply_along_axis(stat, 1, samples)
    return (float(np.percentile(stats, 100 * alpha / 2)), float(np.percentile(stats, 100 * (1 - alpha / 2))))
