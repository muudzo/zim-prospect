"""Known mineral occurrences (MRDS + OpenStreetMap, plus optional user and Mindat points)."""
from __future__ import annotations

import logging
import re
from typing import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from shapely.geometry.base import BaseGeometry

from . import mindat, sources, userdata
from .grid import ZIM_CRS, Grid, point_rowcol

log = logging.getLogger(__name__)

_TOKEN = re.compile(r"[a-z]+")
DUPLICATE_RADIUS_M = 100.0   # a new point this close to a kept point is the same occurrence


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(str(text).lower()))


def _open_occurrences(border: BaseGeometry) -> gpd.GeoDataFrame:
    """MRDS + OSM occurrences inside the border (the original, baseline point set)."""
    mrds = sources.mrds()
    mrds_rows = pd.DataFrame({
        "source": "MRDS",
        "name": mrds["site_name"].astype(str),
        "commodities": mrds[["commod1", "commod2", "commod3"]].fillna("").astype(str).agg(", ".join, axis=1),
        "status": mrds["dev_stat"].fillna("Unknown").astype(str),
        "lon": pd.to_numeric(mrds["longitude"], errors="coerce"),
        "lat": pd.to_numeric(mrds["latitude"], errors="coerce"),
    })
    osm_records = []
    for el in sources.osm_mines():
        tags = el.get("tags", {})
        lon = el.get("lon", el.get("center", {}).get("lon"))
        lat = el.get("lat", el.get("center", {}).get("lat"))
        text = " ".join(filter(None, [tags.get("resource", ""), tags.get("name", "")]))
        osm_records.append({"source": "OSM", "name": tags.get("name", "(unnamed)"),
                            "commodities": text, "status": tags.get("landuse") or tags.get("man_made")
                            or tags.get("industrial") or "mine", "lon": lon, "lat": lat})
    df = pd.concat([mrds_rows, pd.DataFrame(osm_records)], ignore_index=True).dropna(subset=["lon", "lat"])
    gdf = gpd.GeoDataFrame(df.drop(columns=["lon", "lat"]),
                           geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs=4326)
    return gdf[gdf.within(border)].reset_index(drop=True)


def _user_occurrences(ucfg: userdata.UserDataConfig) -> gpd.GeoDataFrame:
    path = ucfg.occurrences_file
    return userdata.read_user_occurrences(path) if path.is_file() else userdata.points_frame([])


def _xy(points: gpd.GeoDataFrame) -> np.ndarray:
    proj = points.geometry.to_crs(ZIM_CRS)
    return np.column_stack([proj.x.to_numpy(), proj.y.to_numpy()])


def _commodity_words(df: gpd.GeoDataFrame) -> list[frozenset[str] | None]:
    """Lower-case commodity words per row; None (matches anything) when the column is absent or empty."""
    if "commodities" not in df.columns:
        return [None] * len(df)
    return [frozenset(re.findall(r"[a-z]+", v.lower())) or None if isinstance(v, str) else None
            for v in df["commodities"]]


def _same_commodity(a: frozenset[str] | None, b: frozenset[str] | None) -> bool:
    return a is None or b is None or bool(a & b)


def drop_near(existing: gpd.GeoDataFrame, new: gpd.GeoDataFrame,
              radius_m: float = DUPLICATE_RADIUS_M) -> gpd.GeoDataFrame:
    """Rows of `new` that are not within `radius_m` of an existing or earlier kept point sharing a commodity."""
    if new.empty:
        return new.reset_index(drop=True)
    new_xy, new_words = _xy(new), _commodity_words(new)
    keep = np.ones(len(new), dtype=bool)
    if not existing.empty:
        old_words = _commodity_words(existing)
        for i, near in enumerate(cKDTree(_xy(existing)).query_ball_point(new_xy, r=radius_m)):
            keep[i] = not any(_same_commodity(new_words[i], old_words[j]) for j in near)
    # Pairs come sorted by their first index, so keep[i] is final before any (i, j) is visited.
    for i, j in sorted(cKDTree(new_xy).query_pairs(radius_m)):
        if keep[i] and keep[j] and _same_commodity(new_words[i], new_words[j]):
            keep[j] = False
    return new[keep].reset_index(drop=True)


def _merge(base: gpd.GeoDataFrame, extra: gpd.GeoDataFrame, label: str,
           border: BaseGeometry) -> gpd.GeoDataFrame:
    """`base` plus the `extra` points inside the border that are not near-duplicates."""
    if extra.empty:
        return base
    inside = extra[extra.within(border)]
    kept = drop_near(base, inside)
    log.info("%s occurrences: %d merged, %d outside the border, %d within %.0f m of a known point",
             label, len(kept), len(extra) - len(inside), len(inside) - len(kept), DUPLICATE_RADIUS_M)
    if kept.empty:
        return base
    merged = pd.concat([base, kept[base.columns]], ignore_index=True)
    return gpd.GeoDataFrame(merged, geometry="geometry", crs=base.crs)


def load_all(ucfg: userdata.UserDataConfig | None = None, *, refresh: bool = False) -> gpd.GeoDataFrame:
    """All occurrences inside Zimbabwe with columns: source, name, commodities, status, geometry.

    MRDS + OSM first (unchanged), then `<data>/user/occurrences.csv` (source "user"), then Mindat
    localities (only with MINDAT_API_KEY; `refresh` re-downloads them). Added points within
    100 m of an earlier point are dropped.
    """
    border = sources.boundary().union_all()
    cfg = ucfg if ucfg is not None else userdata.load_config()
    merged = _merge(_open_occurrences(border), _user_occurrences(cfg), "user", border)
    return _merge(merged, mindat.load_localities(refresh=refresh), "Mindat", border)


def select(occ: gpd.GeoDataFrame, match: Iterable[str]) -> gpd.GeoDataFrame:
    """Occurrences whose commodity text contains any of the `match` words (whole-word match)."""
    wanted = frozenset(m.lower() for m in match)
    hit = occ["commodities"].map(lambda t: bool(_tokens(t) & wanted))
    return occ[hit].reset_index(drop=True)


def decluster(points: gpd.GeoDataFrame, grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    """Unique in-grid (row, col) cells holding at least one point (one sample per cell)."""
    rows, cols, inside = point_rowcol(points, grid)
    rows, cols = rows[inside], cols[inside]
    keep = grid.mask[rows, cols]
    cells = np.unique(np.stack([rows[keep], cols[keep]], axis=1), axis=0)
    return cells[:, 0], cells[:, 1]
