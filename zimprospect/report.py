"""Outputs: GeoTIFFs, interactive map, success-rate curves and a markdown report."""
from __future__ import annotations

import base64
import io
from pathlib import Path

import folium
import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import Resampling, calculate_default_transform, reproject

from .grid import Grid

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

WEB_MERCATOR = "EPSG:3857"
CMAP = plt.get_cmap("inferno")


def write_geotiff(path: Path, arr: np.ndarray, grid: Grid) -> None:
    profile = {"driver": "GTiff", "height": grid.height, "width": grid.width, "count": 1,
               "dtype": "float32", "crs": grid.crs, "transform": grid.transform,
               "nodata": np.nan, "compress": "deflate", "tiled": True}
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr.astype("float32"), 1)


def _overlay_png(arr: np.ndarray, grid: Grid, min_visible: float = 0.6) -> tuple[str, list]:
    """Reproject to Web Mercator and render a transparent PNG (data URI) + lat/lon bounds."""
    bounds = rasterio.transform.array_bounds(grid.height, grid.width, grid.transform)
    dst_tf, w, h = calculate_default_transform(grid.crs, WEB_MERCATOR, grid.width, grid.height,
                                               *bounds, resolution=grid.res_m * 3)
    out = np.full((h, w), np.nan, dtype="float32")
    reproject(arr.astype("float32"), out, src_transform=grid.transform, src_crs=grid.crs,
              src_nodata=np.nan, dst_transform=dst_tf, dst_crs=WEB_MERCATOR, dst_nodata=np.nan,
              resampling=Resampling.bilinear)
    rgba = CMAP(np.nan_to_num(out, nan=0.0))
    alpha = np.clip((np.nan_to_num(out, nan=0.0) - min_visible) / (1 - min_visible), 0, 1)
    rgba[..., 3] = np.where(np.isfinite(out), 0.15 + 0.75 * alpha, 0.0) * (out >= min_visible)
    buf = io.BytesIO()
    plt.imsave(buf, rgba, format="png")
    west, north = dst_tf * (0, 0)
    east, south = dst_tf * (w, h)
    corners = gpd.GeoSeries(gpd.points_from_xy([west, east], [south, north]), crs=WEB_MERCATOR).to_crs(4326)
    latlon = [[corners.y[0], corners.x[0]], [corners.y[1], corners.x[1]]]
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), latlon


def interactive_map(path: Path, maps: dict[str, np.ndarray], labels: dict[str, str], grid: Grid,
                    occurrences: gpd.GeoDataFrame, targets: dict[str, pd.DataFrame]) -> None:
    m = folium.Map(location=[-19.0, 29.8], zoom_start=7, tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="Streets").add_to(m)
    folium.TileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                     attr="Esri World Imagery", name="Satellite").add_to(m)
    for i, (key, arr) in enumerate(maps.items()):
        uri, bounds = _overlay_png(arr, grid)
        folium.raster_layers.ImageOverlay(uri, bounds=bounds, name=f"{labels[key]} — prospectivity",
                                          show=(i == 0)).add_to(m)
        tg = folium.FeatureGroup(name=f"{labels[key]} — top targets", show=(i == 0))
        for row in targets.get(key, pd.DataFrame()).itertuples():
            colour = "#1f9e89" if row.status == "greenfield" else "#3b528b"
            folium.CircleMarker(
                [row.peak_lat, row.peak_lon], radius=6 + min(row.area_km2, 200) ** 0.5 / 2,
                color=colour, fill=True, fill_opacity=0.8,
                popup=folium.Popup(f"<b>{labels[key]} target #{row.rank}</b><br>score {row.peak_score:.2f} · "
                                   f"{row.area_km2:.0f} km²<br>{row.status} · nearest known "
                                   f"{row.nearest_known_km:.1f} km<br>{row.peak_lat:.4f}, {row.peak_lon:.4f}",
                                   max_width=260)).add_to(tg)
        tg.add_to(m)
    occ = folium.FeatureGroup(name="Known occurrences (MRDS + OSM)", show=True)
    for row in occurrences.itertuples():
        folium.CircleMarker([row.geometry.y, row.geometry.x], radius=3, color="#ffffff", weight=1,
                            fill=True, fill_color="#222222", fill_opacity=0.9,
                            tooltip=f"{row.name} — {row.commodities} ({row.source}, {row.status})").add_to(occ)
    occ.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(str(path))


def success_curves(path: Path, curves: dict[str, tuple[np.ndarray, np.ndarray, float]]) -> None:
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    ax.plot([0, 1], [0, 1], ls="--", color="grey", lw=1, label="random (AUC 0.50)")
    for label, (x, y, auc) in curves.items():
        ax.step(x, y, where="post", lw=1.6, label=f"{label} (AUC {auc:.2f})")
    ax.set_xlabel("Fraction of Zimbabwe flagged (highest scores first)")
    ax.set_ylabel("Fraction of known deposits captured")
    ax.set_title("Success-rate curves (spatially cross-validated where data-driven)")
    ax.legend(fontsize=7, loc="lower right")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def overview_figure(path: Path, maps: dict[str, np.ndarray], labels: dict[str, str],
                    grid: Grid, occurrences_by_key: dict[str, gpd.GeoDataFrame]) -> None:
    """Small-multiples PNG of every commodity's final map with its known occurrences."""
    n = len(maps)
    cols = min(4, n)
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(3.6 * cols, 3.4 * rows), squeeze=False)
    extent = rasterio.transform.array_bounds(grid.height, grid.width, grid.transform)
    extent = (extent[0], extent[2], extent[1], extent[3])
    for ax, (key, arr) in zip(axes.flat, maps.items()):
        ax.imshow(arr, cmap=CMAP, vmin=0, vmax=1, extent=extent, interpolation="nearest")
        pts = occurrences_by_key.get(key)
        if pts is not None and len(pts):
            p = pts.to_crs(grid.crs)
            ax.scatter(p.geometry.x, p.geometry.y, s=7, c="#39d0ff", edgecolors="black", linewidths=0.3)
        ax.set_title(labels[key], fontsize=8)
        ax.set_axis_off()
    for ax in list(axes.flat)[n:]:
        ax.set_axis_off()
    fig.suptitle("Zimbabwe mineral prospectivity (percentile rank; blue = known occurrences)", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
