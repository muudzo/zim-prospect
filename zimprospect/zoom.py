"""Target zoom: full-resolution Sentinel-2 anomaly maps over the top national targets.

The national maps say *which district* to look at (500 m). This module drills into the best
targets at 10-20 m and points to surface features worth walking to and sampling: alteration,
iron-oxide staining, mafic/ultramafic soils, ridges. Satellites only see the top millimetre
of the ground, so an anomaly is a place to sample, never a deposit.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
import rasterio
from scipy import ndimage

from . import features, sources
from .grid import Grid, cell_centres_lonlat, local_grid, lonlat_bounds

log = logging.getLogger(__name__)

ZOOM_SEASONS = (("2024-07-15", "2024-10-15"), ("2025-07-15", "2025-10-15"), ("2026-07-15", "2026-10-15"))
ZOOM_PER_TILE = 4          # best scenes per MGRS tile/orbit per season
ZOOM_MAX_CLOUD = 10.0      # % scene cloud cover allowed (pixel-level SCL masking does the rest)
SCENE_WORKERS = 8
NDVI_MAX = 0.45            # denser vegetation hides the soil/rock signal
MIN_VALID_CELLS = 100      # fewer clean cells than this: no statistics for the box
MAD_TO_SD = 1.4826         # median absolute deviation -> standard deviation (normal data)
Z_CLIP = 3.0
ANOMALY_Z = 2.0
MIN_CLUSTER_CELLS = 3
MAX_ANOMALIES = 20
INDEX_NAMES = ("ferric", "ferrous", "clay", "ndvi", "albedo", "slope")
DIRECTIONS = {"high": 1, "low": -1}
ANOMALY_COLUMNS = ["rank", "peak_z", "mean_z", "area_m2", "lat", "lon", "ndvi_mean"]
DISCLAIMER = ("Surface spectral anomaly (soil/rock colour, alteration or relief) to check and "
              "sample in the field. It is not a deposit.")


@dataclass(frozen=True)
class RecipeTerm:
    index: str
    direction: int   # +1 = high values anomalous, -1 = low values anomalous
    weight: float


@dataclass(frozen=True)
class ZoomResult:
    rank: int
    lat: float
    lon: float
    grid: Grid
    indices: dict[str, np.ndarray]
    rgb: np.ndarray | None
    score: np.ndarray
    anomalies: pd.DataFrame
    n_scenes: int


# ---------- pure functions (unit tested) ----------

def parse_recipe(entries: Iterable[Mapping]) -> tuple[RecipeTerm, ...]:
    """Validate a config `zoom:` recipe into RecipeTerms."""
    terms = []
    for entry in entries:
        index, direction = entry.get("index"), entry.get("direction", "high")
        if index not in INDEX_NAMES:
            raise ValueError(f"unknown zoom index {index!r}; choose from {INDEX_NAMES}")
        if direction not in DIRECTIONS:
            raise ValueError(f"zoom direction must be 'high' or 'low', got {direction!r}")
        weight = float(entry.get("weight", 1.0))
        if weight <= 0:
            raise ValueError("zoom weights must be positive")
        terms.append(RecipeTerm(index, DIRECTIONS[direction], weight))
    if not terms:
        raise ValueError("zoom recipe is empty")
    return tuple(terms)


def robust_z(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """(x - median) / (1.4826 * MAD) over the valid cells; NaN elsewhere."""
    use = valid & np.isfinite(values)
    if use.sum() < MIN_VALID_CELLS:
        return np.full(values.shape, np.nan)
    sample = values[use]
    median = np.median(sample)
    spread = MAD_TO_SD * np.median(np.abs(sample - median))
    if spread <= 0:
        return np.where(use, 0.0, np.nan)
    return np.where(use, (values - median) / spread, np.nan)


def recipe_score(zs: Mapping[str, np.ndarray], recipe: Sequence[RecipeTerm]) -> np.ndarray:
    """Weighted mean of signed z-scores, re-standardised so 2.0 means 2 robust SDs (unclipped)."""
    shape = next(iter(zs.values())).shape
    num, den = np.zeros(shape), np.zeros(shape)
    for term in recipe:
        z = zs.get(term.index)
        if z is None:
            continue
        ok = np.isfinite(z)
        num = num + np.where(ok, term.weight * term.direction * z, 0.0)
        den = den + np.where(ok, term.weight, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        combined = np.where(den > 0, num / den, np.nan)
    return robust_z(combined, np.isfinite(combined))


def anomaly_clusters(score: np.ndarray, grid: Grid, ndvi: np.ndarray, threshold: float = ANOMALY_Z,
                     min_cells: int = MIN_CLUSTER_CELLS, max_n: int = MAX_ANOMALIES) -> pd.DataFrame:
    """Connected patches at or above `threshold`, ranked by peak then size; peak cell lat/lon."""
    hot = np.isfinite(score) & (score >= threshold)
    labels, n = ndimage.label(hot, structure=np.ones((3, 3)))
    if n == 0:
        return pd.DataFrame(columns=ANOMALY_COLUMNS)
    ids = np.arange(1, n + 1)
    sizes = ndimage.sum(np.ones_like(labels), labels, ids)
    keep = sizes >= min_cells
    if not keep.any():
        return pd.DataFrame(columns=ANOMALY_COLUMNS)
    ids, sizes = ids[keep], sizes[keep]
    peaks = np.array(ndimage.maximum(score, labels, ids))
    peak_pos = np.array(ndimage.maximum_position(score, labels, ids)).reshape(-1, 2)
    lon, lat = cell_centres_lonlat(peak_pos[:, 0], peak_pos[:, 1], grid)
    df = pd.DataFrame({
        "peak_z": np.round(peaks, 2),
        "mean_z": np.round(ndimage.mean(score, labels, ids), 2),
        "area_m2": np.round(sizes * grid.res_m ** 2, 0),
        "lat": np.round(lat, 5), "lon": np.round(lon, 5),
        "ndvi_mean": np.round(ndimage.mean(np.nan_to_num(ndvi), labels, ids), 2),
    }).sort_values(["peak_z", "area_m2"], ascending=False).head(max_n).reset_index(drop=True)
    df.insert(0, "rank", np.arange(1, len(df) + 1))
    return df


def load_targets(path: Path, top: int) -> pd.DataFrame:
    """Top `top` rows of a targets CSV written by `python -m zimprospect run`."""
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run `python -m zimprospect run` first")
    df = pd.read_csv(path)
    missing = {"rank", "peak_lat", "peak_lon"} - set(df.columns)
    if missing:
        raise ValueError(f"{path} is missing columns {sorted(missing)}")
    return df.sort_values("rank").head(top).reset_index(drop=True)


# ---------- imagery ----------

def _scenes(grid: Grid) -> list[dict]:
    bbox = lonlat_bounds(grid)
    found: dict[str, dict] = {}
    for start, end in ZOOM_SEASONS:
        for scene in sources.s2_scenes(bbox, start, end, max_cloud=ZOOM_MAX_CLOUD, per_tile=ZOOM_PER_TILE):
            found[scene["id"]] = scene
    return list(found.values())


def s2_composite(grid: Grid) -> tuple[dict[str, np.ndarray], int]:
    """Per-band median of every clean dry-season scene over the box, plus the scene count."""
    scenes = _scenes(grid)
    if not scenes:
        raise RuntimeError("no Sentinel-2 scenes cover this target")

    def read(scene: dict):
        try:
            return features._s2_scene_bands(scene, grid)
        except (rasterio.errors.RasterioIOError, KeyError) as exc:
            log.warning("skipping scene %s: %s", scene.get("id"), exc)
            return None

    with ThreadPoolExecutor(max_workers=SCENE_WORKERS) as pool:
        pieces = [p for p in pool.map(read, scenes) if p is not None]
    if not pieces:
        raise RuntimeError("every Sentinel-2 scene failed to read for this target")
    bands = {b: features._median_mosaic([(w, s[b]) for w, s in pieces], grid.shape, max_depth=len(pieces))
             for b in features.S2_BANDS}
    return bands, len(pieces)


def _slope(grid: Grid) -> np.ndarray:
    try:
        return features.terrain_layers(grid)["slope"]
    except (rasterio.errors.RasterioIOError, KeyError, ValueError, RuntimeError) as exc:
        log.warning("terrain unavailable for this box (%s); slope terms are skipped", exc)
        return np.full(grid.shape, np.nan)


def _true_colour(bands: Mapping[str, np.ndarray]) -> np.ndarray:
    rgb = np.dstack([bands["red"], bands["green"], bands["blue"]])
    lo, hi = np.nanpercentile(rgb, [2, 98])
    return np.clip((np.nan_to_num(rgb, nan=lo) - lo) / max(hi - lo, 1e-6), 0, 1)


def zoom_target(rank: int, lat: float, lon: float, recipe: Sequence[RecipeTerm],
                box_km: float, res_m: float) -> ZoomResult:
    grid = local_grid(lat, lon, box_km, res_m)
    bands, n_scenes = s2_composite(grid)
    indices = {k.removeprefix("s2_"): v for k, v in features.s2_indices(bands).items()}
    indices = {**indices, "slope": _slope(grid)}
    clean = np.isfinite(indices["ndvi"]) & (indices["ndvi"] <= NDVI_MAX)
    zs = {name: robust_z(arr, clean) for name, arr in indices.items()}
    strength = recipe_score(zs, recipe)
    anomalies = anomaly_clusters(strength, grid, indices["ndvi"])   # rank on the unclipped strength
    score = np.clip(strength, -Z_CLIP, Z_CLIP)                       # clipped for maps and colour scales
    return ZoomResult(rank, lat, lon, grid, indices, _true_colour(bands), score, anomalies, n_scenes)


# ---------- orchestration ----------

def _write(result: ZoomResult, folder: Path) -> None:
    from . import report  # local import: report pulls in matplotlib/folium
    folder.mkdir(parents=True, exist_ok=True)
    report.write_multiband(folder / "indices.tif", result.indices, result.grid)
    report.write_geotiff(folder / "score.tif", result.score, result.grid)
    report.zoom_pngs(folder, result.rgb, result.score)
    result.anomalies.assign(n_scenes=result.n_scenes).to_csv(folder / "anomalies.csv", index=False)


def run_zoom(commodity: str, cfg: Mapping, targets_dir: Path, out_dir: Path, top: int = 10,
             box_km: float = 8.0, res_m: float = 20.0) -> pd.DataFrame:
    """Zoom into the top targets of one commodity; returns the summary table."""
    spec = cfg["commodities"].get(commodity)
    if spec is None:
        raise KeyError(f"unknown commodity {commodity!r}")
    if "zoom" not in spec:
        log.info("%s has no zoom recipe: no useful surface signature at this scale", commodity)
        return pd.DataFrame()
    recipe = parse_recipe(spec["zoom"])
    targets = load_targets(targets_dir / f"targets_{commodity}.csv", top)
    folder = out_dir / commodity
    results, rows = [], []
    for t in targets.itertuples():
        try:
            res = zoom_target(int(t.rank), float(t.peak_lat), float(t.peak_lon), recipe, box_km, res_m)
        except RuntimeError as exc:
            log.warning("target %s skipped: %s", t.rank, exc)
            rows.append({"target": int(t.rank), "status": f"skipped: {exc}"})
            continue
        _write(res, folder / f"t{res.rank:02d}")
        results.append(res)
        top_hit = res.anomalies.iloc[0] if len(res.anomalies) else None
        rows.append({"target": res.rank, "status": "ok", "n_scenes": res.n_scenes,
                     "n_anomalies": len(res.anomalies),
                     "best_lat": None if top_hit is None else top_hit["lat"],
                     "best_lon": None if top_hit is None else top_hit["lon"],
                     "best_z": None if top_hit is None else top_hit["peak_z"]})
        log.info("%s target %d: %d scenes, %d anomalies", commodity, res.rank, res.n_scenes, len(res.anomalies))
    summary = pd.DataFrame(rows)
    folder.mkdir(parents=True, exist_ok=True)
    summary.to_csv(folder / "zoom_summary.csv", index=False)
    if results:
        from . import webmap
        webmap.zoom_map(folder / "zoom_map.html", spec["label"], results, DISCLAIMER)
    return summary
