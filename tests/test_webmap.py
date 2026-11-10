"""Map payload contracts: geographic context and the displayed analysis resolution."""
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

from zimprospect import webmap
from zimprospect.grid import make_grid


def test_geography_exports_wgs84_boundary_without_source_attributes():
    border = gpd.GeoDataFrame({"private_note": ["not for display"]},
                              geometry=[box(29, -20, 30, -19)], crs=4326).to_crs(32736)
    data = webmap.geography(border)
    feature = data["country"]["features"][0]
    assert feature["properties"] == {}
    coordinates = np.asarray(feature["geometry"]["coordinates"][0])
    assert np.allclose(coordinates.min(axis=0), [29, -20], atol=0.001)
    assert np.allclose(coordinates.max(axis=0), [30, -19], atol=0.001)
    assert np.allclose(data["countryBounds"], [[-20, 29], [-19, 30]], atol=0.001)


def test_national_map_includes_boundary_and_actual_resolution(tmp_path, monkeypatch):
    border = gpd.GeoDataFrame(geometry=[box(29, -20, 30, -19)], crs=4326)
    grid = make_grid(border, res_m=250, pad_m=0)
    captured = {}
    monkeypatch.setattr(webmap, "overlay_png", lambda *args: ("data:image/png;base64,", [[-20, 29], [-19, 30]]))
    monkeypatch.setattr(webmap, "write_page", lambda path, data: captured.update(data))
    points = gpd.GeoDataFrame({"name": [], "commodities": [], "source": []}, geometry=[], crs=4326)
    webmap.national_map(tmp_path / "map.html", {"gold": np.ones(grid.shape)}, {"gold": "Gold"},
                       grid, points, {"gold": pd.DataFrame()}, {}, {"gold": points}, boundary=border)
    assert captured["country"]["features"]
    assert "250 m" in captured["subtitle"]
    assert captured["initialBounds"] == captured["countryBounds"]
    assert captured["groups"][0]["points"] == []


def test_target_comparison_preserves_numeric_units_and_zone_distance():
    frame = pd.DataFrame([{"rank": 1, "status": "greenfield", "nearest_known_km": 12.3,
                           "peak_score": 0.9999, "mean_score": 0.9856, "area_km2": 14.5,
                           "peak_lat": -18.2, "peak_lon": 30.1}])
    point = webmap.target_points(frame)[0]
    assert point["metrics"] == {"areaKm2": 14.5, "meanRank": 98.56, "zoneDistanceKm": 12.3}
    assert point["lat"] == -18.2 and point["lon"] == 30.1
    assert "zone" in point["sub"]
