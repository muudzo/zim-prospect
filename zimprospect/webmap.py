"""Interactive HTML maps: a Leaflet scaffold (folium) plus the zim-prospect panel in web/map.{css,js}.

Python shapes plain JSON (`ZP_DATA`); the browser code builds every label with textContent, so
names from open data (OSM, MRDS) are never interpreted as HTML.
"""
from __future__ import annotations

import base64
import html
import io
import json
import math
from pathlib import Path
from typing import Mapping, Sequence

import folium
import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import rasterio
from branca.element import Element
from rasterio.warp import Resampling, calculate_default_transform, reproject

from . import sources
from .grid import Grid, ZIM_CRS

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent / "web"
WEB_MERCATOR = "EPSG:3857"
CMAP = plt.get_cmap("inferno")
ZIM_BOUNDS = [[-22.5, 25.2], [-15.5, 33.1]]
NATIONAL_VISIBLE = 0.6            # show the top 40% of each national map
ZOOM_VISIBLE, ZOOM_TOP = 1.0, 3.0  # show anomaly strength 1..3 in zoom maps
CONFIDENCE_STRONG, CONFIDENCE_MODERATE, MIN_SITES = 0.80, 0.70, 10
CONTAINS_KNOWN_KM = 0.5
NATIONAL_TEXT = {
    "title": "zim-prospect",
    "groupLabel": "Mineral", "pointLabel": "Targets", "emptyText": "No targets for this mineral.",
    "disclaimer": ("Screening tool built from open data. A target is a place to investigate, not a proven "
                   "deposit. Check prospecting licences, land access and protected areas before any field work."),
    "popupNote": "A target is an area to investigate, not a deposit.",
    "allContextLabel": "Show known sites for every mineral",
}


# ---------- raster overlay ----------

def overlay_png(arr: np.ndarray, grid: Grid, lo: float, hi: float, c0: float, c1: float,
                scale: float = 3.0) -> tuple[str, list[list[float]]]:
    """Web Mercator PNG (data URI) of values >= lo, coloured c0..c1 of the colormap, plus lat/lon bounds."""
    bounds = rasterio.transform.array_bounds(grid.height, grid.width, grid.transform)
    dst_tf, w, h = calculate_default_transform(grid.crs, WEB_MERCATOR, grid.width, grid.height,
                                               *bounds, resolution=grid.res_m * scale)
    out = np.full((h, w), np.nan, dtype="float32")
    reproject(arr.astype("float32"), out, src_transform=grid.transform, src_crs=grid.crs, src_nodata=np.nan,
              dst_transform=dst_tf, dst_crs=WEB_MERCATOR, dst_nodata=np.nan, resampling=Resampling.bilinear)
    t = np.clip((np.nan_to_num(out, nan=lo) - lo) / (hi - lo), 0, 1)
    rgba = CMAP(c0 + t * (c1 - c0))
    rgba[..., 3] = np.where(np.isfinite(out) & (out >= lo), 0.15 + 0.75 * t, 0.0)
    buf = io.BytesIO()
    plt.imsave(buf, rgba, format="png")
    west, north = dst_tf * (0, 0)
    east, south = dst_tf * (w, h)
    corners = gpd.GeoSeries(gpd.points_from_xy([west, east], [south, north]), crs=WEB_MERCATOR).to_crs(4326)
    latlon = [[float(corners.y[0]), float(corners.x[0])], [float(corners.y[1]), float(corners.x[1])]]
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), latlon


def ramp_css(c0: float, c1: float, stops: int = 6) -> str:
    colours = [CMAP(c0 + (c1 - c0) * i / (stops - 1)) for i in range(stops)]
    parts = [f"rgb({int(r * 255)},{int(g * 255)},{int(b * 255)}) {100 * i / (stops - 1):.0f}%"
             for i, (r, g, b, _) in enumerate(colours)]
    return f"linear-gradient(90deg, {', '.join(parts)})"


# ---------- page assembly ----------

def _num(value: float, digits: int) -> float | None:
    return round(float(value), digits) if value is not None and math.isfinite(float(value)) else None


def _json(data: Mapping) -> str:
    text = json.dumps(data, separators=(",", ":"), allow_nan=False)
    # Escape every '<' so no string can open </script> or <!-- inside the <script> element.
    return text.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def write_page(path: Path, data: Mapping) -> None:
    """Folium map scaffold + panel CSS/JS + data, as one self-contained HTML file."""
    m = folium.Map(location=[-19.0, 29.8], zoom_start=7, tiles=None, control_scale=True, zoom_snap=0.25)
    root = m.get_root()
    root.header.add_child(Element(f"<title>{html.escape(data['title'])}</title>"))
    root.header.add_child(Element(f"<style>{(WEB_DIR / 'map.css').read_text()}</style>"))
    script = (f"var ZP_DATA = {_json(data)};\n"
              f"document.addEventListener('DOMContentLoaded', function () {{\n"
              f"var ZP_MAP = {m.get_name()};\n{(WEB_DIR / 'map.js').read_text()}\n}});")
    root.html.add_child(Element(f"<script>{script}</script>"))
    m.save(str(path))


# ---------- national map ----------

def geography(boundary: gpd.GeoDataFrame) -> dict:
    """Embed a simplified national outline, with no source attributes or remote fetch in the viewer."""
    border = boundary[["geometry"]].to_crs(ZIM_CRS)
    border = border.set_geometry(border.geometry.simplify(250, preserve_topology=True)).to_crs(4326)
    west, south, east, north = border.total_bounds
    return {"country": json.loads(border.to_json(drop_id=True)),
            "countryBounds": [[float(south), float(west)], [float(north), float(east)]]}


def split_label(label: str) -> tuple[str, str]:
    """'Gold (orogenic, greenstone-hosted)' -> ('Gold', 'Orogenic, greenstone-hosted')."""
    name, _, rest = label.partition(" (")
    desc = rest.rstrip(")")
    return name.strip(), desc[:1].upper() + desc[1:]


def confidence(entry: Mapping | None) -> tuple[dict | None, list[list[str]]]:
    """Badge + stats from a validation entry (spatial-CV ensemble if present, else fuzzy)."""
    best = (entry or {}).get("ensemble_cv") or (entry or {}).get("fuzzy")
    if not best:
        return None, [["Known sites", "too few to test"]]
    auc, n = float(best["auc"]), int(best["n"])
    if entry.get("optimistic"):
        badge = {"text": "Optimistic: weights tuned on these sites", "tone": "moderate"}
    elif n < MIN_SITES:
        badge = {"text": f"Low confidence: only {n} known sites", "tone": "weak"}
    elif auc >= CONFIDENCE_STRONG:
        badge = {"text": "Strong track record", "tone": "good"}
    elif auc >= CONFIDENCE_MODERATE:
        badge = {"text": "Moderate track record", "tone": "moderate"}
    else:
        badge = {"text": "Weak track record", "tone": "weak"}
    stats = [["Accuracy (AUC)", f"{auc:.2f}"], ["Known sites in top 10%", f"{100 * best['captured_in_top10pct']:.0f}%"],
             ["Known sites tested", str(n)]]
    return badge, stats


def target_points(df: pd.DataFrame) -> list[dict]:
    points = []
    for row in df.itertuples():
        known = row.status != "greenfield"
        dist = _num(row.nearest_known_km, 1)
        if dist is None:
            dist_text = "no known site recorded"
        elif dist < CONTAINS_KNOWN_KM:
            dist_text = "zone contains a recorded site"
        else:
            dist_text = f"{dist:.1f} km from zone to recorded site"
        top = max(100 * (1 - float(row.peak_score)), 0.1)
        mean_rank = _num(100 * row.mean_score, 2)
        mean_text = "–" if mean_rank is None else f"{mean_rank:.2f}"
        points.append({
            "rank": int(row.rank), "lat": float(row.peak_lat), "lon": float(row.peak_lon),
            "tone": "known" if known else "greenfield",
            "metrics": {"areaKm2": _num(row.area_km2, 1), "meanRank": mean_rank,
                        "zoneDistanceKm": dist},
            "title": f"{'Known district' if known else 'Greenfield'} · {row.area_km2:,.0f} km²",
            "sub": f"Mean rank {mean_text}/100 · {dist_text}",
            "lines": [f"Peak score in the top {top:.1f}% of Zimbabwe", f"Zone area {row.area_km2:,.0f} km²",
                      f"Zone mean rank {mean_text}/100", dist_text[:1].upper() + dist_text[1:]],
        })
    return points


def context_points(gdf: gpd.GeoDataFrame | None) -> list[list]:
    if gdf is None or gdf.empty:
        return []
    out = []
    for row in gdf.to_crs(4326).itertuples():
        name = row.name if isinstance(row.name, str) and row.name.strip() else "Unnamed site"
        out.append([round(row.geometry.y, 5), round(row.geometry.x, 5), f"{name} · {row.commodities} ({row.source})"])
    return out


def national_map(path: Path, maps: Mapping[str, np.ndarray], labels: Mapping[str, str], grid: Grid,
                 occurrences: gpd.GeoDataFrame, targets: Mapping[str, pd.DataFrame],
                 validation: Mapping[str, Mapping], known: Mapping[str, gpd.GeoDataFrame],
                 boundary: gpd.GeoDataFrame | None = None) -> None:
    groups = []
    for key, arr in maps.items():
        name, desc = split_label(labels[key])
        badge, stats = confidence(validation.get(key))
        image, bounds = overlay_png(arr, grid, NATIONAL_VISIBLE, 1.0, NATIONAL_VISIBLE, 1.0)
        groups.append({"key": key, "short": name, "name": name, "desc": desc, "badge": badge, "stats": stats,
                       "image": image, "bounds": bounds, "points": target_points(targets.get(key, pd.DataFrame())),
                       "context": context_points(known.get(key))})
    geo = geography(boundary if boundary is not None else sources.boundary())
    data = {**NATIONAL_TEXT, **geo, "subtitle": f"Zimbabwe · {grid.res_m:g} m screening map",
            "fitOnSelect": False, "focusZoom": 10, "focusRadiusM": 6000,
            "initialBounds": geo["countryBounds"], "groups": groups, "allContext": context_points(occurrences),
            "legend": {"title": "Prospectivity", "gradient": ramp_css(NATIONAL_VISIBLE, 1.0),
                       "lo": "Top 40% of Zimbabwe", "hi": "Top 1%",
                       "keys": [{"tone": "greenfield", "label": "Zone over 5 km from a recorded site"},
                                {"tone": "known", "label": "Zone near a recorded site"},
                                {"tone": "context", "label": "Known mine or quarry (USGS MRDS, OpenStreetMap)"}]}}
    write_page(path, data)


# ---------- zoom map ----------

def anomaly_points(df: pd.DataFrame, box_km: float) -> list[dict]:
    return [{
        "rank": int(r.rank), "lat": float(r.lat), "lon": float(r.lon), "tone": "anomaly",
        "title": f"Anomaly · {r.area_m2:,.0f} m²",
        "sub": f"Strength {r.peak_z:.1f} · vegetation {r.ndvi_mean:.2f}",
        "lines": [f"Strength {r.peak_z:.1f} (2 = clearly unusual for this {box_km:g} km box)",
                  f"Patch area {r.area_m2:,.0f} m²", f"Vegetation index {r.ndvi_mean:.2f} (lower = barer ground)"],
    } for r in df.itertuples()]


def zoom_map(path: Path, label: str, results: Sequence, disclaimer: str) -> None:
    name, _ = split_label(label)
    groups = []
    for res in results:
        image, bounds = overlay_png(res.score, res.grid, ZOOM_VISIBLE, ZOOM_TOP, 0.45, 1.0, scale=1.0)
        box_km = res.grid.width * res.grid.res_m / 1000
        best = f"{res.anomalies['peak_z'].max():.1f}" if len(res.anomalies) else "–"
        groups.append({
            "key": f"t{res.rank:02d}", "short": f"#{res.rank}", "name": f"{name} target #{res.rank}",
            "desc": f"{box_km:g} km box · {res.grid.res_m:g} m pixels · dry-season Sentinel-2 median",
            "badge": None, "stats": [["Anomalies", str(len(res.anomalies))], ["Strongest", best],
                                     ["Images used", str(res.n_scenes)]],
            "image": image, "bounds": bounds, "points": anomaly_points(res.anomalies, box_km),
            "context": [[round(res.lat, 5), round(res.lon, 5), f"Peak of national target #{res.rank}"]]})
    data = {**geography(sources.boundary()), "title": f"{name}: target zoom", "subtitle": "Surface anomalies · target-scale view",
            "groupLabel": "Target", "pointLabel": "Anomalies to check",
            "emptyText": "No patch stands out from its surroundings here.", "disclaimer": disclaimer,
            "popupNote": "Check on the ground and sample; this is not a deposit.", "fitOnSelect": True,
            "focusZoom": 16, "focusRadiusM": 150, "initialBounds": ZIM_BOUNDS, "groups": groups, "allContext": [],
            "legend": {"title": "Anomaly strength", "gradient": ramp_css(0.45, 1.0), "lo": "1 (slightly unusual)",
                       "hi": "3+ (very unusual)",
                       "keys": [{"tone": "anomaly", "label": "Surface patch to check and sample (ranked)"},
                                {"tone": "context", "label": "Peak of the national target"}]}}
    write_page(path, data)
