"""Fuzzy-logic evidence transforms and combination operators.

All functions are pure: they take arrays and return new arrays. NaN marks
"no data" and is ignored when layers are combined, so a missing evidence
layer (e.g. under Kalahari sand) does not drive the score to zero.
"""
from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np


def linear_decay(dist_km: np.ndarray, d0: float, d1: float, floor: float) -> np.ndarray:
    """Membership 1.0 within d0 km, falling linearly to `floor` at d1 km."""
    if d1 <= d0:
        raise ValueError(f"d1 ({d1}) must be greater than d0 ({d0})")
    if not 0.0 <= floor <= 1.0:
        raise ValueError(f"floor must be in [0, 1], got {floor}")
    frac = np.clip((dist_km - d0) / (d1 - d0), 0.0, 1.0)
    out = 1.0 - frac * (1.0 - floor)
    return np.where(np.isnan(dist_km), np.nan, out)


def class_lookup(class_raster: np.ndarray, class_names: Sequence[str],
                 weights: Mapping[str, float], default: float = 0.01) -> np.ndarray:
    """Map an integer class raster (index into class_names, -1 = nodata) to memberships."""
    unknown = set(weights) - set(class_names) - {"others", "rest"}
    if unknown:
        raise ValueError(f"weights reference unknown classes: {sorted(unknown)}")
    fallback = float(weights.get("others", weights.get("rest", default)))
    table = np.array([float(weights.get(n, fallback)) for n in class_names] + [np.nan])
    idx = np.where(class_raster < 0, len(class_names), class_raster)
    return table[idx]


def rescale_percentile(values: np.ndarray, lo: float = 2.0, hi: float = 98.0) -> np.ndarray:
    """Linearly rescale to [0, 1] between the lo/hi percentiles (robust to outliers)."""
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return np.full(values.shape, np.nan)
    p_lo, p_hi = np.percentile(finite, [lo, hi])
    if p_hi <= p_lo:
        return np.where(np.isfinite(values), 0.5, np.nan)
    return np.clip((values - p_lo) / (p_hi - p_lo), 0.0, 1.0)


def fuzzy_gamma(layers: Sequence[np.ndarray], gamma: float = 0.8,
                weights: Sequence[float] | None = None) -> np.ndarray:
    """Fuzzy gamma operator: (fuzzy algebraic sum)^g * (fuzzy algebraic product)^(1-g).

    Optional per-layer weights in (0, 1] soften a layer by raising it to that
    power (weight 1 = full influence, 0.5 = square-root, i.e. weaker veto).
    NaN cells in a layer are treated as absent evidence for that cell.
    A cell with no evidence in any layer returns NaN.
    """
    if not layers:
        raise ValueError("at least one layer is required")
    if not 0.0 <= gamma <= 1.0:
        raise ValueError(f"gamma must be in [0, 1], got {gamma}")
    weights = list(weights) if weights is not None else [1.0] * len(layers)
    if len(weights) != len(layers):
        raise ValueError("weights and layers must have the same length")

    prod = np.ones(layers[0].shape)
    comp = np.ones(layers[0].shape)  # running product of (1 - mu)
    n_valid = np.zeros(layers[0].shape, dtype=np.int16)
    for layer, w in zip(layers, weights):
        valid = np.isfinite(layer)
        mu = np.where(valid, np.clip(layer, 1e-6, 1.0) ** w, 1.0)
        prod = prod * mu
        comp = comp * np.where(valid, 1.0 - mu, 1.0)
        n_valid = n_valid + valid
    fsum = 1.0 - comp
    out = (fsum ** gamma) * (prod ** (1.0 - gamma))
    return np.where(n_valid > 0, out, np.nan)
