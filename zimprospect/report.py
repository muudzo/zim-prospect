"""File outputs: GeoTIFFs, zoom images, success-rate curves and the overview figure."""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import rasterio

from .grid import Grid

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

CMAP = plt.get_cmap("inferno")


def write_geotiff(path: Path, arr: np.ndarray, grid: Grid) -> None:
    profile = {"driver": "GTiff", "height": grid.height, "width": grid.width, "count": 1,
               "dtype": "float32", "crs": grid.crs, "transform": grid.transform,
               "nodata": np.nan, "compress": "deflate", "tiled": True}
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr.astype("float32"), 1)


def write_multiband(path: Path, layers: dict[str, np.ndarray], grid: Grid) -> None:
    """One float32 band per layer, with the layer name as the band description."""
    profile = {"driver": "GTiff", "height": grid.height, "width": grid.width, "count": len(layers),
               "dtype": "float32", "crs": grid.crs, "transform": grid.transform,
               "nodata": np.nan, "compress": "deflate", "tiled": True}
    with rasterio.open(path, "w", **profile) as dst:
        for i, (name, arr) in enumerate(layers.items(), start=1):
            dst.write(arr.astype("float32"), i)
            dst.set_band_description(i, name)


def zoom_pngs(folder: Path, rgb: np.ndarray | None, score: np.ndarray) -> None:
    """True-colour image and anomaly-strength image for one zoom box."""
    if rgb is not None:
        plt.imsave(folder / "rgb.png", rgb)
    plt.imsave(folder / "score.png", np.nan_to_num(score, nan=0.0), cmap="RdBu_r", vmin=-3, vmax=3)


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
