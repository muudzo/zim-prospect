"""Unit tests for the target-zoom module (no network)."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from zimprospect.grid import cell_centres_lonlat, local_grid
from zimprospect.zoom import (RecipeTerm, anomaly_clusters, load_targets, parse_recipe,
                              recipe_score, robust_z)


def test_local_grid_is_centred_square_and_unmasked():
    grid = local_grid(-17.3241, 31.5659, box_km=8, res_m=20)
    assert grid.shape == (400, 400)
    assert grid.mask.all()
    lon, lat = cell_centres_lonlat(np.array([199.5]), np.array([199.5]), grid)
    assert lon[0] == pytest.approx(31.5659, abs=1e-4)
    assert lat[0] == pytest.approx(-17.3241, abs=1e-4)


def test_local_grid_rejects_bad_input():
    with pytest.raises(ValueError):
        local_grid(-17.3, 31.5, box_km=0, res_m=20)
    with pytest.raises(ValueError):
        local_grid(-117.3, 31.5, box_km=8, res_m=20)


def test_robust_z_centres_on_median_and_respects_mask():
    values = np.arange(1.0, 401.0).reshape(20, 20)
    valid = np.ones_like(values, dtype=bool)
    valid[0, 0] = False
    z = robust_z(values, valid)
    assert np.isnan(z[0, 0])
    median = np.median(values[valid])
    row, col = np.argwhere(values == np.floor(median))[0]
    assert abs(z[row, col]) < 0.05
    assert np.nanmax(z) > 1.2 and np.nanmin(z) < -1.2


def test_robust_z_constant_input_gives_zero_not_nan():
    values = np.full((12, 12), 3.0)
    z = robust_z(values, np.ones_like(values, dtype=bool))
    assert np.all(z == 0)


def test_robust_z_too_few_cells_is_all_nan():
    values = np.ones((3, 3))
    assert np.isnan(robust_z(values, np.ones_like(values, dtype=bool))).all()


def test_parse_recipe_validates_entries():
    recipe = parse_recipe([{"index": "clay", "direction": "high", "weight": 1.0},
                           {"index": "albedo", "direction": "low"}])
    assert recipe == (RecipeTerm("clay", 1, 1.0), RecipeTerm("albedo", -1, 1.0))
    with pytest.raises(ValueError):
        parse_recipe([{"index": "clay", "direction": "up"}])
    with pytest.raises(ValueError):
        parse_recipe([{"index": "gold_detector", "direction": "high"}])
    with pytest.raises(ValueError):
        parse_recipe([])


def test_recipe_score_uses_direction_and_skips_missing_index():
    rng = np.random.default_rng(0)
    clay = rng.normal(size=(30, 30))
    albedo = rng.normal(size=(30, 30))
    clay[5, 5], albedo[5, 5] = 6.0, -6.0          # strong on both terms
    zs = {"clay": clay, "albedo": albedo, "slope": np.full((30, 30), np.nan)}
    recipe = (RecipeTerm("clay", 1, 1.0), RecipeTerm("albedo", -1, 1.0), RecipeTerm("slope", 1, 1.0))
    score = recipe_score(zs, recipe)
    assert np.isfinite(score).all()
    assert np.unravel_index(np.nanargmax(score), score.shape) == (5, 5)
    assert score[5, 5] > 3.0   # unclipped, so the strongest anomaly ranks first


def test_anomaly_clusters_keeps_blobs_and_drops_specks():
    grid = local_grid(-17.3241, 31.5659, box_km=2, res_m=20)    # 100 x 100
    score = np.zeros(grid.shape)
    score[40:43, 40:43] = 2.5
    score[41, 41] = 3.0
    score[80, 10] = 3.0                                          # single cell: dropped
    ndvi = np.full(grid.shape, 0.2)
    df = anomaly_clusters(score, grid, ndvi, threshold=2.0, min_cells=3, max_n=10)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["rank"] == 1 and row["peak_z"] == 3.0
    assert row["area_m2"] == pytest.approx(9 * 20 * 20)
    lon, lat = cell_centres_lonlat(np.array([41]), np.array([41]), grid)
    assert row["lat"] == pytest.approx(lat[0], abs=1e-5)
    assert row["lon"] == pytest.approx(lon[0], abs=1e-5)


def test_anomaly_clusters_empty_when_nothing_passes():
    grid = local_grid(-17.3, 31.5, box_km=1, res_m=20)
    df = anomaly_clusters(np.zeros(grid.shape), grid, np.zeros(grid.shape), 2.0, 3, 10)
    assert df.empty and "lat" in df.columns


def test_load_targets_missing_file_has_clear_message(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="zimprospect run"):
        load_targets(tmp_path / "targets_gold.csv", top=3)


def test_load_targets_takes_top_ranks(tmp_path: Path):
    path = tmp_path / "targets_gold.csv"
    pd.DataFrame({"rank": [2, 1, 3], "peak_lat": [-17.0, -18.0, -19.0], "peak_lon": [30.0, 31.0, 32.0],
                  "status": ["greenfield"] * 3}).to_csv(path, index=False)
    df = load_targets(path, top=2)
    assert list(df["rank"]) == [1, 2]
