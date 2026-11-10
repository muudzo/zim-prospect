"""Turn a prospectivity map into a ranked table of target zones."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import ndimage

from .grid import Grid, cell_centres_lonlat, distance_km

GREENFIELD_KM = 5.0  # zones farther than this from any known occurrence are "greenfield"
MIN_TARGET_AREA_KM2 = 0.5  # = 2 cells at 500 m, 8 at 250 m: the same physical size at any resolution
_AREA_ROUNDING = 9  # decimals kept before rounding up, so 0.5 / 0.25 is 2 cells, not 3


def min_cells_for_area(min_area_km2: float, res_m: float) -> int:
    """Smallest number of `res_m` cells whose area reaches `min_area_km2` (at least 1)."""
    if res_m <= 0 or min_area_km2 < 0:
        raise ValueError("res_m must be positive and min_area_km2 non-negative")
    cell_km2 = (res_m / 1000) ** 2
    return max(1, int(np.ceil(round(min_area_km2 / cell_km2, _AREA_ROUNDING))))


def extract_targets(score: np.ndarray, grid: Grid, known_rows: np.ndarray, known_cols: np.ndarray,
                    top_frac: float = 0.03, max_targets: int = 25,
                    min_area_km2: float = MIN_TARGET_AREA_KM2) -> pd.DataFrame:
    """Connected zones within the top `top_frac` of the study area, ranked by peak then size.

    Zones smaller than `min_area_km2` are dropped (converted to cells with `grid.res_m`).
    """
    if not 0 < top_frac < 1:
        raise ValueError("top_frac must be between 0 and 1")
    min_cells = min_cells_for_area(min_area_km2, grid.res_m)
    valid = np.isfinite(score) & grid.mask
    threshold = np.percentile(score[valid], 100 * (1 - top_frac))
    hot = valid & (score >= threshold)
    if hot.sum() > 2 * top_frac * valid.sum():  # many ties at the threshold: keep only cells above it
        hot = valid & (score > threshold)
    labels, n = ndimage.label(hot, structure=np.ones((3, 3)))
    if n == 0:
        return pd.DataFrame()
    ids = np.arange(1, n + 1)
    sizes = ndimage.sum(np.ones_like(labels), labels, ids)
    peaks = ndimage.maximum(score, labels, ids)
    means = ndimage.mean(score, labels, ids)
    peak_pos = np.array(ndimage.maximum_position(score, labels, ids))

    known = np.zeros(grid.shape, dtype=bool)
    known[known_rows, known_cols] = True
    dist_known = distance_km(known, grid) if known.any() else np.full(grid.shape, np.inf)
    min_dist = ndimage.minimum(np.where(np.isfinite(dist_known), dist_known, np.inf), labels, ids)

    lon, lat = cell_centres_lonlat(peak_pos[:, 0], peak_pos[:, 1], grid)
    df = pd.DataFrame({
        "peak_score": np.round(peaks, 4), "mean_score": np.round(means, 4),
        "area_km2": np.round(sizes * (grid.res_m / 1000) ** 2, 1),
        "peak_lat": np.round(lat, 4), "peak_lon": np.round(lon, 4),
        "nearest_known_km": np.round(min_dist, 1),
    })
    df["status"] = np.where(df["nearest_known_km"] <= GREENFIELD_KM, "known district", "greenfield")
    # Sort on the unrounded peak: near the top of a percentile map many peaks round to 1.0.
    order = np.lexsort((-df["area_km2"].to_numpy(), -peaks))
    df = df.iloc[order]
    df = df[(sizes >= min_cells)[order]]
    df = df.head(max_targets).reset_index(drop=True)
    df.insert(0, "rank", np.arange(1, len(df) + 1))
    return df
