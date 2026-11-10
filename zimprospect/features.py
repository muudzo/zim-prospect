"""Build the national evidence-layer stack on the analysis grid (cached as .npz)."""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import from_bounds
from scipy import ndimage

from . import sources
from .grid import Grid, contact_cells, distance_km, make_grid, rasterize_values

log = logging.getLogger(__name__)

CACHE = sources.RAW.parent / "cache"
S2_BANDS = ["blue", "green", "red", "nir", "swir16", "swir22"]
S2_VALID_SCL = (4, 5)  # vegetation, bare soil (drops cloud, shadow, water, burnt/dark)
DRY_SEASON = ("2025-07-15", "2025-10-15")
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "VSI_CACHE": "TRUE",
}


@dataclass(frozen=True)
class FeatureStack:
    grid: Grid
    layers: dict[str, np.ndarray]


# ---------- generic raster helpers ----------

def _overview_level(ds: rasterio.DatasetReader, target_m: float) -> int | None:
    """Coarsest overview whose pixel is still <= target_m (None = full resolution)."""
    native = abs(ds.res[0]) * (111_000 if ds.crs and ds.crs.is_geographic else 1)
    level = None
    for i, factor in enumerate(ds.overviews(1) or []):
        if native * factor <= target_m:
            level = i
    return level


def warp_to_grid(url: str, grid: Grid, resampling: Resampling = Resampling.average,
                 target_m: float | None = None, scale: float = 1.0, offset: float = 0.0
                 ) -> np.ndarray:
    """Read a (remote) raster window covering the grid and resample it onto the grid."""
    target_m = target_m if target_m is not None else grid.res_m / 2
    out = np.full(grid.shape, np.nan, dtype="float32")
    with rasterio.Env(**GDAL_ENV):
        with rasterio.open(url) as probe:
            level = _overview_level(probe, target_m)
        with rasterio.open(url, overview_level=level) as ds:
            west, south, east, north = transform_bounds(
                grid.crs, ds.crs, *rasterio.transform.array_bounds(grid.height, grid.width, grid.transform))
            win = from_bounds(west, south, east, north, ds.transform).round_offsets().round_lengths()
            win = win.intersection(rasterio.windows.Window(0, 0, ds.width, ds.height))
            data = ds.read(1, window=win, out_dtype="float32")
            if ds.nodata is not None:
                data[data == ds.nodata] = np.nan
            data = data * scale + offset
            reproject(data, out, src_transform=ds.window_transform(win), src_crs=ds.crs,
                      src_nodata=np.nan, dst_transform=grid.transform, dst_crs=grid.crs,
                      dst_nodata=np.nan, resampling=resampling)
    return out


def _mosaic(arrays: list[np.ndarray]) -> np.ndarray:
    """Mean of overlapping arrays, ignoring NaN."""
    stack = np.stack(arrays)
    count = np.isfinite(stack).sum(axis=0)
    total = np.nansum(stack, axis=0)
    return np.where(count > 0, total / np.maximum(count, 1), np.nan).astype("float32")


# ---------- layer builders ----------

def geology_layers(grid: Grid) -> dict[str, np.ndarray]:
    geo = sources.geology()
    idx = geo["geo_class"].map({c: i for i, c in enumerate(sources.GEOLOGY_CLASSES)})
    classes = rasterize_values(geo, idx, grid)
    basement = sources.GEOLOGY_CLASSES.index("basement")
    karoo = [sources.GEOLOGY_CLASSES.index(c) for c in ("karoo_lower", "karoo_upper")]
    margin = contact_cells(classes, karoo, [basement])
    return {"geo_class": classes.astype("float32"),
            "dist_karoo_margin": distance_km(margin, grid).astype("float32")}


def terrain_layers(grid: Grid) -> dict[str, np.ndarray]:
    urls = sources.cop90_tile_urls(25, -23, 34, -15)

    def read(url: str) -> np.ndarray | None:
        try:
            return warp_to_grid(url, grid)
        except rasterio.errors.RasterioIOError:
            return None  # ocean/missing tile

    with ThreadPoolExecutor(max_workers=8) as pool:
        tiles = [t for t in pool.map(read, urls) if t is not None]
    if not tiles:
        raise RuntimeError("no Copernicus DEM tiles could be read")
    elev = _mosaic(tiles)
    filled = np.where(np.isfinite(elev), elev, np.nanmean(elev))
    gy, gx = np.gradient(filled, grid.res_m)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    mean = ndimage.uniform_filter(filled, 3)
    rough = np.sqrt(np.maximum(ndimage.uniform_filter(filled ** 2, 3) - mean ** 2, 0))
    return {"elev": elev,
            "slope": np.where(grid.mask, slope, np.nan).astype("float32"),
            "rough": np.where(grid.mask, rough, np.nan).astype("float32")}


def _sub_grid(grid: Grid, url: str) -> tuple[rasterio.windows.Window, Grid] | None:
    """Window of `grid` covered by the raster at `url`, and a Grid for just that window."""
    with rasterio.Env(**GDAL_ENV), rasterio.open(url) as ds:
        bounds = transform_bounds(ds.crs, grid.crs, *ds.bounds)
    win = from_bounds(*bounds, transform=grid.transform).round_offsets().round_lengths()
    try:
        win = win.intersection(rasterio.windows.Window(0, 0, grid.width, grid.height))
    except rasterio.errors.WindowError:
        return None
    sl = win.toslices()
    return win, Grid(grid.crs, rasterio.windows.transform(win, grid.transform),
                     int(win.width), int(win.height), grid.res_m, grid.mask[sl])


def _s2_scene_bands(scene: dict, grid: Grid) -> tuple[rasterio.windows.Window, dict[str, np.ndarray]] | None:
    """Masked reflectance bands for one scene, cropped to the scene footprint on the grid."""
    props = scene["properties"]
    baseline = float(props.get("s2:processing_baseline", "0") or 0)
    # Earth Search already removes the +1000 DN offset (baseline >= 04.00) on most items.
    already = bool(props.get("earthsearch:boa_offset_applied", False))
    offset = -0.1 if baseline >= 4.0 and not already else 0.0
    sub = _sub_grid(grid, scene["assets"]["scl"]["href"])
    if sub is None:
        return None
    win, sgrid = sub
    valid = np.isin(warp_to_grid(scene["assets"]["scl"]["href"], sgrid, Resampling.mode), S2_VALID_SCL)
    bands = {}
    for band in S2_BANDS:
        refl = warp_to_grid(scene["assets"][band]["href"], sgrid, scale=1e-4, offset=offset)
        bands[band] = np.where(valid & (refl > 0.001), refl, np.nan).astype("float32")
    return win, bands


def _median_mosaic(pieces: list[tuple[rasterio.windows.Window, np.ndarray]], shape: tuple[int, int],
                   max_depth: int = 24) -> np.ndarray:
    """Per-cell median of overlapping cropped arrays (robust to seams, burn scars, haze)."""
    stack = np.full((max_depth, *shape), np.nan, dtype="float32")
    depth = np.zeros(shape, dtype=np.int16)
    for win, arr in pieces:
        sl = win.toslices()
        r, c = np.nonzero(np.isfinite(arr) & (depth[sl] < max_depth))
        rr, cc = r + sl[0].start, c + sl[1].start
        stack[depth[rr, cc], rr, cc] = arr[r, c]
        depth[rr, cc] += 1
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return np.nanmedian(stack, axis=0).astype("float32")


def sentinel2_layers(grid: Grid, season: tuple[str, str] = DRY_SEASON) -> dict[str, np.ndarray]:
    bbox = transform_bounds(grid.crs, "EPSG:4326",
                            *rasterio.transform.array_bounds(grid.height, grid.width, grid.transform))
    scenes = sources.s2_scenes(bbox, *season, per_tile=2)
    log.info("Sentinel-2: %d scenes (%s to %s)", len(scenes), *season)
    if not scenes:
        raise RuntimeError("no Sentinel-2 scenes found for the dry-season window")

    def read(scene: dict):
        try:
            return _s2_scene_bands(scene, grid)
        except (rasterio.errors.RasterioIOError, KeyError) as exc:
            log.warning("skipping scene %s: %s", scene.get("id"), exc)
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        per_scene = [s for s in pool.map(read, scenes) if s is not None]
    log.info("Sentinel-2: %d scenes read", len(per_scene))
    b = {band: _median_mosaic([(w, s[band]) for w, s in per_scene], grid.shape) for band in S2_BANDS}
    with np.errstate(divide="ignore", invalid="ignore"):
        layers = {
            "s2_ferric": b["red"] / b["blue"],                              # iron oxides, gossans, laterite
            "s2_ferrous": b["swir22"] / b["nir"] + b["green"] / b["red"],   # ferrous silicates (mafic rocks)
            "s2_clay": b["swir16"] / b["swir22"],                            # Al-OH / Mg-OH clays, sericite
            "s2_ndvi": (b["nir"] - b["red"]) / (b["nir"] + b["red"]),
            "s2_albedo": np.mean([b[k] for k in S2_BANDS], axis=0),          # pale granite sands vs dark mafic soils
        }
    # Fill residual gaps (swath slivers, cloud) from the 15 km neighbourhood so missing
    # spectral evidence does not silently inflate fuzzy scores.
    gap_cells = int(round(15_000 / grid.res_m))
    filled = {k: np.where(np.isfinite(v), v, smooth_nan(v, gap_cells)) for k, v in layers.items()}
    return {k: np.where(grid.mask & np.isfinite(v), v, np.nan).astype("float32") for k, v in filled.items()}


# ---------- multi-scale context ----------

CONTEXT_LAYERS = ["s2_ferric", "s2_ferrous", "s2_clay", "s2_ndvi", "s2_albedo", "rough"]
CONTEXT_KM = (5, 15)  # greenstone belts are 10-50 km wide: regional context beats single pixels


def smooth_nan(arr: np.ndarray, size_cells: int) -> np.ndarray:
    """NaN-aware moving average over a square window."""
    valid = np.isfinite(arr)
    num = ndimage.uniform_filter(np.where(valid, arr, 0.0).astype("float64"), size_cells)
    den = ndimage.uniform_filter(valid.astype("float64"), size_cells)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0.05, num / den, np.nan).astype("float32")


def context_layers(stack: "FeatureStack") -> dict[str, np.ndarray]:
    out = {}
    for name in CONTEXT_LAYERS:
        for km in CONTEXT_KM:
            cells = max(int(round(km * 1000 / stack.grid.res_m)), 3)
            out[f"{name}_ctx{km}"] = np.where(stack.grid.mask, smooth_nan(stack.layers[name], cells), np.nan)
    return out


# ---------- orchestration + cache ----------

BUILDERS = {"geology": geology_layers, "terrain": terrain_layers, "sentinel2": sentinel2_layers}


def build(res_m: float = 500.0, refresh: bool = False) -> FeatureStack:
    """Build (or load from cache) the full feature stack at `res_m` metres."""
    for key, val in GDAL_ENV.items():
        os.environ.setdefault(key, val)
    grid = make_grid(sources.boundary(), res_m=res_m)
    CACHE.mkdir(parents=True, exist_ok=True)
    layers: dict[str, np.ndarray] = {}
    for name, builder in BUILDERS.items():
        path = CACHE / f"{name}_{int(res_m)}m.npz"
        if path.exists() and not refresh:
            with np.load(path) as npz:
                layers.update({k: npz[k] for k in npz.files})
            continue
        log.info("building %s layers at %d m", name, res_m)
        part = builder(grid)
        np.savez_compressed(path, **part)
        layers.update(part)
    (CACHE / f"grid_{int(res_m)}m.json").write_text(json.dumps(
        {"crs": grid.crs, "transform": list(grid.transform)[:6], "shape": grid.shape}))
    base = FeatureStack(grid=grid, layers=layers)
    return FeatureStack(grid=grid, layers={**layers, **context_layers(base)})
