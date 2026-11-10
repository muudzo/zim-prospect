"""Sentinel-2 mosaic settings and the tile-seam diagnostic.

`S2Settings` controls which scenes go into the dry-season median. Its default reproduces the
2026-11-10 baseline (one 2025 season, 2 scenes per MGRS tile and orbit, < 5 % cloud).

`seam_score` measures rectangular brightness steps: the mean absolute difference between
neighbouring cells that lie in different scene-footprint regions, divided by the same statistic
for neighbours inside one region. 1.0 means no seams; larger is worse.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import geopandas as gpd
import numpy as np
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from . import sources
from .grid import Grid, rasterize_presence

DRY_SEASON = ("2025-07-15", "2025-10-15")
DRY_SEASON_MONTH_DAYS = ("07-15", "10-15")
# Most tile/orbit groups that can cover one cell (MGRS overlaps x neighbouring orbits).
# 12 x the baseline per_tile of 2 gives the original median depth of 24.
MAX_GROUPS_PER_CELL = 12
MAX_CLOUD_PCT = 100.0
HASH_CHARS = 10


def _check_window(start: str, end: str) -> None:
    try:
        first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError(f"season dates must be YYYY-MM-DD, got {(start, end)}") from exc
    if first > last:
        raise ValueError(f"season starts after it ends: {(start, end)}")


@dataclass(frozen=True)
class S2Settings:
    """Which Sentinel-2 scenes feed the median mosaic."""

    seasons: tuple[tuple[str, str], ...] = (DRY_SEASON,)
    per_tile: int = 2
    max_cloud: float = 5.0

    def __post_init__(self) -> None:
        # Normalise list input (e.g. from YAML) so equality and hashing are stable.
        object.__setattr__(self, "seasons", tuple((str(s), str(e)) for s, e in self.seasons))
        object.__setattr__(self, "per_tile", int(self.per_tile))
        object.__setattr__(self, "max_cloud", float(self.max_cloud))
        if not self.seasons:
            raise ValueError("at least one season window is required")
        for start, end in self.seasons:
            _check_window(start, end)
        if self.per_tile < 1:
            raise ValueError("per_tile must be at least 1")
        if not 0 < self.max_cloud <= MAX_CLOUD_PCT:
            raise ValueError("max_cloud must be in (0, 100]")

    @classmethod
    def from_mapping(cls, cfg: Mapping[str, Any]) -> "S2Settings":
        """Build from a config block such as {seasons: [[start, end], ...], per_tile: 6, max_cloud: 5}."""
        unknown = set(cfg) - {"seasons", "per_tile", "max_cloud"}
        if unknown:
            raise ValueError(f"unknown sentinel2 settings: {sorted(unknown)}")
        return cls(**dict(cfg))

    @property
    def max_depth(self) -> int:
        """Median-stack depth: enough for every covering tile/orbit group's `per_tile` scenes."""
        return self.per_tile * MAX_GROUPS_PER_CELL

    def as_dict(self) -> dict[str, Any]:
        return {"seasons": [list(s) for s in self.seasons], "per_tile": self.per_tile,
                "max_cloud": self.max_cloud}

    def cache_tag(self) -> str:
        """'' for the baseline settings (keeps the existing cache valid), else a settings hash."""
        if self == LEGACY_S2:
            return ""
        digest = hashlib.sha1(json.dumps(self.as_dict(), sort_keys=True).encode()).hexdigest()
        return f"_s2-{digest[:HASH_CHARS]}"


LEGACY_S2 = S2Settings()


def dry_seasons(years: Iterable[int], month_days: tuple[str, str] = DRY_SEASON_MONTH_DAYS
                ) -> tuple[tuple[str, str], ...]:
    """Dry-season windows (15 Jul - 15 Oct by default) for each year."""
    return tuple((f"{y}-{month_days[0]}", f"{y}-{month_days[1]}") for y in years)


# ---------- scene footprints ----------

def scene_records(scenes: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Compact, JSON-safe description of selected STAC items (id, tile/orbit group, date, footprint)."""
    return [{"id": s["id"], "group": sources.s2_group_key(dict(s)),
             "datetime": s.get("properties", {}).get("datetime"), "geometry": s["geometry"]}
            for s in scenes]


def group_footprints(records: Iterable[Mapping[str, Any]]) -> dict[str, BaseGeometry]:
    """Union of scene footprints (EPSG:4326) per MGRS tile and orbit."""
    parts: dict[str, list[BaseGeometry]] = {}
    for rec in records:
        if not rec.get("geometry"):
            raise ValueError(f"scene {rec.get('id')} has no footprint geometry")
        parts.setdefault(str(rec["group"]), []).append(shape(rec["geometry"]))
    return {group: unary_union(geoms) for group, geoms in parts.items()}


def footprint_ids(footprints: Sequence[BaseGeometry], grid: Grid, seed: int = 0) -> np.ndarray:
    """Label every cell by the set of footprints (EPSG:4326) covering it.

    Labels change exactly where a footprint edge crosses the grid, which is where a median
    mosaic's input set changes and seams can appear.
    """
    keys = np.random.default_rng(seed).integers(1, 2 ** 63, size=len(footprints), dtype=np.uint64)
    code = np.zeros(grid.shape, dtype=np.uint64)
    for key, geom in zip(keys, footprints):
        inside = rasterize_presence(gpd.GeoDataFrame(geometry=[geom], crs=4326), grid, all_touched=False)
        code = code ^ np.where(inside, key, np.uint64(0))
    _, labels = np.unique(code, return_inverse=True)
    return labels.reshape(grid.shape).astype("int32")


# ---------- seam metric ----------

def _neighbour_steps(layer: np.ndarray, tile_ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Absolute steps between 4-neighbours, split into (across a boundary, within a region)."""
    across, within = [], []
    values = layer.astype("float64")
    for a, b, ia, ib in ((values[1:], values[:-1], tile_ids[1:], tile_ids[:-1]),
                         (values[:, 1:], values[:, :-1], tile_ids[:, 1:], tile_ids[:, :-1])):
        step = np.abs(a - b)
        ok = np.isfinite(step)
        boundary = ia != ib
        across.append(step[ok & boundary])
        within.append(step[ok & ~boundary])
    return np.concatenate(across), np.concatenate(within)


def seam_score(layer: np.ndarray, tile_ids: np.ndarray) -> float:
    """Mean |step| across tile boundaries / mean |step| within tiles (NaN cells ignored).

    Returns NaN when there is no boundary or no within-tile pair to compare.
    """
    if layer.shape != tile_ids.shape:
        raise ValueError(f"layer {layer.shape} and tile_ids {tile_ids.shape} differ in shape")
    across, within = _neighbour_steps(layer, tile_ids)
    if across.size == 0 or within.size == 0:
        return float("nan")
    base = float(within.mean())
    return float(across.mean()) / base if base > 0 else float("nan")


def layer_seam_score(layer: np.ndarray, records: Iterable[Mapping[str, Any]], grid: Grid) -> float:
    """Seam score of `layer` against the tile/orbit footprints of the scenes in `records`."""
    ids = footprint_ids(list(group_footprints(records).values()), grid)
    return seam_score(layer, ids)
