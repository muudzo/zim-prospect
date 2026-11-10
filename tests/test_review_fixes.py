"""Regression tests for the code-review findings of 2026-11-10."""
import json

import geopandas as gpd
import numpy as np
from shapely.geometry import Point

from zimprospect.occurrences import drop_near
from zimprospect.webmap import _json, confidence


def _pts(rows):
    return gpd.GeoDataFrame({"commodities": [r[2] for r in rows]},
                            geometry=[Point(r[1], r[0]) for r in rows], crs=4326)


def test_drop_near_keeps_a_different_commodity_at_the_same_spot():
    existing = _pts([(-17.5, 30.5, "Chromium")])
    new = _pts([(-17.5, 30.5, "gold"), (-17.5, 30.5, "chromite;chromium")])
    kept = drop_near(existing, new, radius_m=100)
    assert list(kept["commodities"]) == ["gold"]


def test_json_payload_cannot_break_out_of_the_script_element():
    text = _json({"name": "<!--<script>alert(1)</script>"})
    assert "<" not in text
    assert json.loads(text)["name"] == "<!--<script>alert(1)</script>"


def test_tuned_runs_show_an_optimistic_badge():
    entry = {"optimistic": True, "fuzzy": {"auc": 0.9, "n": 40, "captured_in_top10pct": 0.5}}
    badge, _ = confidence(entry)
    assert "Optimistic" in badge["text"]


def test_targets_rank_by_unrounded_peak_not_area():
    from zimprospect.grid import Grid, make_grid  # noqa: F401
    from affine import Affine
    from zimprospect.targets import extract_targets
    grid = Grid("+proj=aea +lat_1=-16.5 +lat_2=-21.5 +lat_0=-19 +lon_0=29.5 +datum=WGS84 +units=m +no_defs",
                Affine(500, 0, 0, 0, -500, 0), 60, 60, 500.0, np.ones((60, 60), dtype=bool))
    score = np.linspace(0, 0.9, 3600).reshape(60, 60)
    score[5:7, 5:7] = 0.999995          # small zone, strongest peak
    score[30:40, 30:40] = 0.99998       # big zone, weaker peak (both round to 1.0)
    df = extract_targets(score, grid, np.array([0]), np.array([0]), top_frac=0.05)
    assert df.iloc[0]["area_km2"] == 1.0
