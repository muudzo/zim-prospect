"""Build the national evidence-layer stack on the analysis grid (cached as .npz)."""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import Window, from_bounds
from scipy import ndimage

from . import sources
from .grid import Grid, contact_cells, distance_km, make_grid, rasterize_values
from .s2 import DRY_SEASON, S2Settings, scene_records

log = logging.getLogger(__name__)

CACHE = sources.DATA_DIR / "cache"
S2_BANDS = ["blue", "green", "red", "nir", "swir16", "swir22"]
S2_VALID_SCL = (4, 5)  # vegetation, bare soil (drops cloud, shadow, water, burnt/dark)
S2_READ_WORKERS = 8
S2_PROGRESS_EVERY = 50     # log reading progress every N scenes
S2_GAP_FILL_M = 15_000     # residual gaps are filled from this neighbourhood
DEM_READ_WORKERS = 8
COP30_NATIVE_M = 30.0      # grids finer than the DEM are upsampled bilinearly (no stair-step slopes)
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


def grid_lonlat_bounds(grid: Grid) -> tuple[float, float, float, float]:
    """(west, south, east, north) in degrees enclosing the whole grid."""
    return transform_bounds(grid.crs, "EPSG:4326",
                            *rasterio.transform.array_bounds(grid.height, grid.width, grid.transform))


def _read_dem_tile(url: str, grid: Grid, resampling: Resampling) -> np.ndarray | None:
    try:
        return warp_to_grid(url, grid, resampling)
    except (rasterio.errors.RasterioIOError, rasterio.errors.WindowError):
        log.debug("DEM tile missing or outside the grid: %s", url)
        return None  # ocean/missing tile


def terrain_layers(grid: Grid) -> dict[str, np.ndarray]:
    """Elevation, slope and roughness from the Copernicus DEM tiles intersecting any grid.

    GLO-30 below 200 m cells, GLO-90 otherwise; only the window covering the grid is read.
    """
    urls = sources.cop_dem_urls(*grid_lonlat_bounds(grid), res_m=grid.res_m)
    resampling = Resampling.bilinear if grid.res_m < COP30_NATIVE_M else Resampling.average
    with ThreadPoolExecutor(max_workers=DEM_READ_WORKERS) as pool:
        tiles = [t for t in pool.map(lambda u: _read_dem_tile(u, grid, resampling), urls) if t is not None]
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
    """Per-cell median of overlapping cropped arrays (robust to seams, burn scars, haze).

    At most `max_depth` values per cell are used, in piece order. The stack is only as deep as
    the largest actual overlap, so a generous `max_depth` costs no memory.
    """
    coverage = np.zeros(shape, dtype=np.int32)
    for win, arr in pieces:
        coverage[win.toslices()] += np.isfinite(arr)
    levels = max(1, min(max_depth, int(coverage.max(initial=0))))
    stack = np.full((levels, *shape), np.nan, dtype="float32")
    depth = np.zeros(shape, dtype=np.int16)
    for win, arr in pieces:
        sl = win.toslices()
        r, c = np.nonzero(np.isfinite(arr) & (depth[sl] < levels))
        rr, cc = r + sl[0].start, c + sl[1].start
        stack[depth[rr, cc], rr, cc] = arr[r, c]
        depth[rr, cc] += 1
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(stack, axis=0).astype("float32")


def s2_indices(bands: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Spectral indices from surface-reflectance bands (shared by the national and zoom layers)."""
    missing = [band for band in S2_BANDS if band not in bands]
    if missing:
        raise ValueError(f"missing Sentinel-2 bands: {missing}")
    b = bands
    with np.errstate(divide="ignore", invalid="ignore"):
        return {
            "s2_ferric": b["red"] / b["blue"],                              # iron oxides, gossans, laterite
            "s2_ferrous": b["swir22"] / b["nir"] + b["green"] / b["red"],   # ferrous silicates (mafic rocks)
            "s2_clay": b["swir16"] / b["swir22"],                            # Al-OH / Mg-OH clays, sericite
            "s2_ndvi": (b["nir"] - b["red"]) / (b["nir"] + b["red"]),
            "s2_albedo": np.mean([b[k] for k in S2_BANDS], axis=0),          # pale granite sands vs dark mafic soils
        }


def select_s2_scenes(grid: Grid, settings: S2Settings) -> list[dict]:
    """Best `per_tile` scenes per MGRS tile/orbit, pooled over all of the settings' seasons."""
    scenes = sources.s2_scenes_multi(grid_lonlat_bounds(grid), settings.seasons,
                                     max_cloud=settings.max_cloud, per_tile=settings.per_tile)
    log.info("Sentinel-2: %d scenes from %d season(s), up to %d per tile/orbit, cloud < %.0f%%",
             len(scenes), len(settings.seasons), settings.per_tile, settings.max_cloud)
    if not scenes:
        raise RuntimeError("no Sentinel-2 scenes found for the dry-season window(s)")
    return scenes


def _read_scene(scene: dict, grid: Grid) -> tuple[Window, dict[str, np.ndarray]] | None:
    try:
        return _s2_scene_bands(scene, grid)
    except (rasterio.errors.RasterioIOError, KeyError) as exc:
        log.warning("skipping scene %s: %s", scene.get("id"), exc)
        return None


def _read_scenes(scenes: list[dict], grid: Grid) -> list[tuple[Window, dict[str, np.ndarray]]]:
    """Read every scene in parallel, keeping scene order (it decides which values a full stack keeps)."""
    start = time.monotonic()
    done: list[tuple[Window, dict[str, np.ndarray]]] = []
    with ThreadPoolExecutor(max_workers=S2_READ_WORKERS) as pool:
        for i, result in enumerate(pool.map(lambda sc: _read_scene(sc, grid), scenes), start=1):
            if result is not None:
                done.append(result)
            if i % S2_PROGRESS_EVERY == 0:
                log.info("Sentinel-2: %d/%d scenes read (%.0f s)", i, len(scenes), time.monotonic() - start)
    log.info("Sentinel-2: %d of %d scenes read in %.0f s", len(done), len(scenes), time.monotonic() - start)
    return done


def _fill_gaps(layers: Mapping[str, np.ndarray], grid: Grid) -> dict[str, np.ndarray]:
    """Fill residual gaps (swath slivers, cloud) from the 15 km neighbourhood so missing
    spectral evidence does not silently inflate fuzzy scores; mask outside the study area."""
    gap_cells = int(round(S2_GAP_FILL_M / grid.res_m))
    filled = {k: np.where(np.isfinite(v), v, smooth_nan(v, gap_cells)) for k, v in layers.items()}
    return {k: np.where(grid.mask & np.isfinite(v), v, np.nan).astype("float32") for k, v in filled.items()}


def sentinel2_layers(grid: Grid, season: tuple[str, str] | None = None,
                     settings: S2Settings | None = None, scenes: list[dict] | None = None
                     ) -> dict[str, np.ndarray]:
    """Dry-season median spectral indices on `grid`.

    `season` (a single window, kept for older callers) replaces `settings.seasons`. Pass `scenes`
    (from `select_s2_scenes`) to skip the catalogue query.
    """
    settings = settings or S2Settings()
    if season is not None:
        settings = dataclasses.replace(settings, seasons=(tuple(season),))
    if scenes is None:
        scenes = select_s2_scenes(grid, settings)
    per_scene = _read_scenes(scenes, grid)
    if not per_scene:
        raise RuntimeError("none of the selected Sentinel-2 scenes could be read")
    bands = {band: _median_mosaic([(w, s[band]) for w, s in per_scene], grid.shape,
                                  max_depth=settings.max_depth) for band in S2_BANDS}
    return _fill_gaps(s2_indices(bands), grid)


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


def cache_path(name: str, res_m: float, s2: S2Settings | None = None) -> Path:
    """Cache file of one layer group. Sentinel-2 files carry a settings tag unless baseline."""
    tag = (s2 or S2Settings()).cache_tag() if name == "sentinel2" else ""
    return CACHE / f"{name}_{int(res_m)}m{tag}.npz"


def _sidecar_matches(path: Path, settings: S2Settings) -> bool:
    """False when the cache's sidecar records different Sentinel-2 settings (or is unreadable)."""
    side = path.with_suffix(".json")
    if not side.exists():
        return True  # pre-sidecar cache: the file name already encodes the settings
    try:
        recorded = S2Settings.from_mapping(json.loads(side.read_text())["settings"])
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("unreadable Sentinel-2 sidecar %s (%s); rebuilding", side, exc)
        return False
    return recorded == settings


def _build_sentinel2(grid: Grid, settings: S2Settings) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    scenes = select_s2_scenes(grid, settings)
    part = sentinel2_layers(grid, settings=settings, scenes=scenes)
    return part, {"settings": settings.as_dict(), "n_scenes": len(scenes), "scenes": scene_records(scenes)}


def build_layers(name: str, grid: Grid, res_m: float, refresh: bool = False,
                 s2: S2Settings | None = None) -> dict[str, np.ndarray]:
    """Load one layer group (`geology`, `terrain`, `sentinel2`) from the cache, or build and cache it."""
    settings = s2 or S2Settings()
    path = cache_path(name, res_m, settings)
    if path.exists() and not refresh and (name != "sentinel2" or _sidecar_matches(path, settings)):
        with np.load(path) as npz:
            return {k: npz[k] for k in npz.files}
    log.info("building %s layers at %d m", name, res_m)
    path.parent.mkdir(parents=True, exist_ok=True)
    if name != "sentinel2":
        part = BUILDERS[name](grid)
        np.savez_compressed(path, **part)
        return part
    part, sidecar = _build_sentinel2(grid, settings)
    np.savez_compressed(path, **part)
    path.with_suffix(".json").write_text(json.dumps(sidecar))
    return part


def build(res_m: float = 500.0, refresh: bool = False, s2: S2Settings | None = None) -> FeatureStack:
    """Build (or load from cache) the full feature stack at `res_m` metres.

    `s2` selects the Sentinel-2 scenes (default: the baseline settings); changing it uses a
    separate cache file, so other settings never reuse a stale mosaic.
    """
    for key, val in GDAL_ENV.items():
        os.environ.setdefault(key, val)
    grid = make_grid(sources.boundary(), res_m=res_m)
    CACHE.mkdir(parents=True, exist_ok=True)
    layers: dict[str, np.ndarray] = {}
    for name in BUILDERS:
        layers.update(build_layers(name, grid, res_m, refresh, s2))
    (CACHE / f"grid_{int(res_m)}m.json").write_text(json.dumps(
        {"crs": grid.crs, "transform": list(grid.transform)[:6], "shape": grid.shape}))
    base = FeatureStack(grid=grid, layers=layers)
    return FeatureStack(grid=grid, layers={**layers, **context_layers(base)})
