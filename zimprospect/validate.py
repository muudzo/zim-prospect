"""Model validation: how well does a score map rank known deposits?"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ValidationResult:
    n_points: int
    auc: float                 # P(score at deposit > score at random cell); 0.5 = random
    area_for_50pct: float      # fraction of area (top-ranked) needed to capture 50% of deposits
    area_for_80pct: float
    captured_in_top10pct: float  # fraction of deposits inside the top 10% of area
    curve_area: np.ndarray       # success-rate curve x (area fraction)
    curve_captured: np.ndarray   # success-rate curve y (deposit fraction)


def point_percentiles(scores: np.ndarray, point_scores: np.ndarray) -> np.ndarray:
    """Percentile rank (0-1, ties counted half) of each point score within all valid cells."""
    cells = np.sort(scores[np.isfinite(scores)])
    if cells.size == 0:
        raise ValueError("score raster has no valid cells")
    below = np.searchsorted(cells, point_scores, side="left")
    at_or_below = np.searchsorted(cells, point_scores, side="right")
    return (below + at_or_below) / (2.0 * cells.size)


def success_rate(scores: np.ndarray, point_scores: np.ndarray) -> ValidationResult:
    """Success-rate statistics for deposit locations against a score raster."""
    point_scores = point_scores[np.isfinite(point_scores)]
    if point_scores.size == 0:
        raise ValueError("no deposit points fall on valid cells")
    pct = point_percentiles(scores, point_scores)
    # Area fraction (from the top) needed to include each deposit.
    area_needed = np.sort(1.0 - pct)
    captured = np.arange(1, area_needed.size + 1) / area_needed.size

    def area_for(frac: float) -> float:
        k = int(np.ceil(frac * area_needed.size)) - 1
        return float(area_needed[max(k, 0)])

    return ValidationResult(
        n_points=int(point_scores.size),
        auc=float(pct.mean()),
        area_for_50pct=area_for(0.5),
        area_for_80pct=area_for(0.8),
        captured_in_top10pct=float((area_needed <= 0.10).mean()),
        curve_area=np.concatenate([[0.0], area_needed, [1.0]]),
        curve_captured=np.concatenate([[0.0], captured, [1.0]]),
    )
