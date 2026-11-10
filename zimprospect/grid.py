"""Analysis grid: projection, rasterisation, distance surfaces and point sampling."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import geopandas as gpd
import numpy as np
from affine import Affine
from rasterio import features
from scipy import ndimage

# Albers equal-area centred on Zimbabwe: areas (for success-rate curves) and
# distances (for structural buffers) stay accurate across the whole country.
ZIM_CRS = ("+proj=aea +lat_1=-16.5 +lat_2=-21.5 +lat_0=-19 +lon_0=29.5 "
           "+datum=WGS84 +units=m +no_defs")


@dataclass(frozen=True)
class Grid:
    crs: str
    transform: Affine
    width: int
    height: int
    res_m: float
    mask: np.ndarray  # True inside the study area

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)


def make_grid(boundary: gpd.GeoDataFrame, res_m: float = 1000.0, pad_m: float = 5000.0) -> Grid:
    """Build a grid covering `boundary` (any CRS) at `res_m` metres."""
    if res_m <= 0:
        raise ValueError("res_m must be positive")
    proj = boundary.to_crs(ZIM_CRS)
    minx, miny, maxx, maxy = proj.total_bounds
    minx, miny, maxx, maxy = minx - pad_m, miny - pad_m, maxx + pad_m, maxy + pad_m
    width = int(np.ceil((maxx - minx) / res_m))
    height = int(np.ceil((maxy - miny) / res_m))
    transform = Affine(res_m, 0, minx, 0, -res_m, maxy)
    mask = features.rasterize(
        ((g, 1) for g in proj.geometry if g is not None and not g.is_empty),
        out_shape=(height, width), transform=transform, fill=0, dtype="uint8",
    ).astype(bool)
    return Grid(ZIM_CRS, transform, width, height, float(res_m), mask)


def rasterize_values(gdf: gpd.GeoDataFrame, values: Iterable[int], grid: Grid,
                     fill: int = -1) -> np.ndarray:
    """Burn integer values of polygons into the grid (later rows win on overlap)."""
    proj = gdf.to_crs(grid.crs)
    shapes = ((g, int(v)) for g, v in zip(proj.geometry, values)
              if g is not None and not g.is_empty)
    out = features.rasterize(shapes, out_shape=grid.shape, transform=grid.transform,
                             fill=fill, dtype="int32")
    return np.where(grid.mask, out, fill)


def rasterize_presence(gdf: gpd.GeoDataFrame, grid: Grid, all_touched: bool = True) -> np.ndarray:
    """Boolean raster of cells touched by any geometry (lines, points, polygons)."""
    if gdf.empty:
        return np.zeros(grid.shape, dtype=bool)
    proj = gdf.to_crs(grid.crs)
    burned = features.rasterize(
        ((g, 1) for g in proj.geometry if g is not None and not g.is_empty),
        out_shape=grid.shape, transform=grid.transform, fill=0, dtype="uint8",
        all_touched=all_touched,
    )
    return burned.astype(bool)


def distance_km(features_mask: np.ndarray, grid: Grid) -> np.ndarray:
    """Euclidean distance (km) from every cell to the nearest True cell.

    Returns NaN everywhere if there are no features, and NaN outside the study area.
    """
    if not features_mask.any():
        return np.full(grid.shape, np.nan)
    dist = ndimage.distance_transform_edt(~features_mask) * grid.res_m / 1000.0
    return np.where(grid.mask, dist, np.nan)


def contact_cells(class_raster: np.ndarray, side_a: Iterable[int], side_b: Iterable[int]) -> np.ndarray:
    """Cells of class set A that touch (8-neighbourhood) a cell of class set B."""
    a = np.isin(class_raster, list(side_a))
    b = np.isin(class_raster, list(side_b))
    b_grown = ndimage.binary_dilation(b, structure=np.ones((3, 3), dtype=bool))
    return a & b_grown


def point_rowcol(points: gpd.GeoDataFrame, grid: Grid) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Row/col indices for points, plus a boolean of which fall inside the grid."""
    proj = points.to_crs(grid.crs)
    inv = ~grid.transform
    cols, rows = inv * (proj.geometry.x.to_numpy(), proj.geometry.y.to_numpy())
    rows = np.floor(rows).astype(int)
    cols = np.floor(cols).astype(int)
    inside = (rows >= 0) & (rows < grid.height) & (cols >= 0) & (cols < grid.width)
    return rows, cols, inside


def sample(raster: np.ndarray, points: gpd.GeoDataFrame, grid: Grid) -> np.ndarray:
    """Raster values at point locations (NaN for points outside the grid)."""
    rows, cols, inside = point_rowcol(points, grid)
    out = np.full(len(points), np.nan)
    out[inside] = raster[rows[inside], cols[inside]]
    return out


def cell_centres_lonlat(rows: np.ndarray, cols: np.ndarray, grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    """Longitude/latitude of cell centres."""
    xs, ys = grid.transform * (cols + 0.5, rows + 0.5)
    pts = gpd.GeoSeries(gpd.points_from_xy(xs, ys), crs=grid.crs).to_crs(4326)
    return pts.x.to_numpy(), pts.y.to_numpy()
