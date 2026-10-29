#!/usr/bin/env python3
"""Paired comparison of two cells on the SAME test items.

Why: every cell evaluates the identical 280-item test split, so the right
statistic is the *paired* difference — its variance is far smaller than the
variance of two independent proportions. Reporting "0.532 vs 0.507" as if the
two numbers were independent overstates the uncertainty.

What it prints, for A (reference, e.g. baseline arm) vs B (treatment, e.g. CAM):

    n_pairs        items scored in both cells
    hardA / hardB  per-arm accuracy on the matched subset
    b / c          discordant cells: b = A correct & B wrong, c = A wrong & B correct
    delta          hardB - hardA (paired)
    95% CI         paired bootstrap over items (20000 resamples)
    McNemar        exact two-sided binomial test on (b, c)

Usage:
    python -X utf8 scripts/paired_compare.py \
        --a outputs/matrix/spreadsheetbench/glm/baseline_seed42 \
        --b outputs/matrix/spreadsheetbench/glm/cam_full_seed42
    # also useful: compare a cell's best skill against its own initial skill
    python -X utf8 scripts/paired_compare.py --a <cell> --a-phase test_eval_baseline --b <cell> --b-phase test_eval
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

_PHASES = ("test_eval_baseline", "test_eval", "test_eval_final")


def load(cell: Path, phase: str) -> dict[str, int]:
    """Load complete binary evidence; duplicates and invalid rows are errors."""
    try:
        from scripts.experiment_integrity import phase_rows
    except ModuleNotFoundError:
        from experiment_integrity import phase_rows
    return {item_id: int(row["hard"]) for item_id, row in phase_rows(cell, phase).items()}


def bootstrap_ci(diffs: list[int], n_boot: int = 20000, seed: int = 42) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(diffs)
    means = []
    for _ in range(n_boot):
        s = 0
        for _ in range(n):
            s += diffs[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[int(0.975 * n_boot) - 1]
    return lo, hi


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact binomial test on the discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    # two-sided p = 2 * P(X <= k) with X ~ Bin(n, 0.5), capped at 1
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="reference cell dir")
    ap.add_argument("--a-phase", default="test_eval", choices=_PHASES)
    ap.add_argument("--b", required=True, help="treatment cell dir")
    ap.add_argument("--b-phase", default="test_eval", choices=_PHASES)
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--allow-intersection", action="store_true", help="Explicit diagnostic-only partial comparison")
    args = ap.parse_args()

    A = load(Path(args.a), args.a_phase)
    B = load(Path(args.b), args.b_phase)
    if set(A) != set(B) and not args.allow_intersection:
        raise SystemExit("Test ID sets differ; refusing a partial comparison (use --allow-intersection for diagnostics)")
    ids = sorted(set(A) & set(B))
    if not ids:
        raise SystemExit("no overlapping ids")

    hard_a = sum(A[i] for i in ids) / len(ids)
    hard_b = sum(B[i] for i in ids) / len(ids)
    b = sum(1 for i in ids if A[i] == 1 and B[i] == 0)   # A only
    c = sum(1 for i in ids if A[i] == 0 and B[i] == 1)   # B only
    diffs = [B[i] - A[i] for i in ids]
    lo, hi = bootstrap_ci(diffs)

    print(f"matched items        : {len(ids)}  (A has {len(A)}, B has {len(B)})")
    print(f"{args.label_a:<20} : hard={hard_a:.4f}")
    print(f"{args.label_b:<20} : hard={hard_b:.4f}")
    print(f"delta (B - A)        : {hard_b - hard_a:+.4f}  ({100 * (hard_b - hard_a):+.1f} pp)")
    print(f"95% CI (paired boot) : [{lo:+.4f}, {hi:+.4f}]  ({100 * lo:+.1f} .. {100 * hi:+.1f} pp)")
    print(f"discordant           : only-{args.label_a}={b}  only-{args.label_b}={c}")
    p = mcnemar_exact(b, c)
    print(f"McNemar exact p      : {p:.4f}  -> {'DIFFERENT' if p < 0.05 else 'not distinguishable'} at alpha=0.05")
    if len(A) != len(B):
        print(f"NOTE: row counts differ (A={len(A)}, B={len(B)}); compared on the intersection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
