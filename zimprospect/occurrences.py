"""Known mineral occurrences (MRDS + OpenStreetMap) as a tidy point layer."""
from __future__ import annotations

import re
from typing import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd

from . import sources
from .grid import Grid, point_rowcol

_TOKEN = re.compile(r"[a-z]+")


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(str(text).lower()))


def load_all() -> gpd.GeoDataFrame:
    """All occurrences inside Zimbabwe with columns: source, name, commodities, status, geometry."""
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
    border = sources.boundary().union_all()
    return gdf[gdf.within(border)].reset_index(drop=True)


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
