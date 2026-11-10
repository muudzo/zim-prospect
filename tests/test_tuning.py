"""Tests for fuzzy weight tuning (U7) on small synthetic stacks: no network, no real data."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import yaml
from affine import Affine

from zimprospect import models, tuning
from zimprospect.features import FeatureStack
from zimprospect.grid import ZIM_CRS, Grid
from zimprospect.validate import success_rate

RES_M = 10_000.0          # 10 km cells: a 50 km block is 5 x 5 cells
SIZE = 60                 # 60 x 60 cells -> 144 blocks over 5 folds
SIGNAL_NOISE = 0.15
SIGNAL_THRESHOLD = 1.05   # ~3% of cells become deposits, all driven by the signal layer
COVER_CLASS = 4           # index of "cover" in sources.GEOLOGY_CLASSES
COVER_STRIP_COLS = 6
N_TIN = 5                 # below min_tune_points, so tin is skipped

CFG_YAML = """
gamma: 0.8
spatial_block_km: 50
min_tune_points: 15
noise_terrain: &noise
  - {layer: noise, direction: high, weight: 1.0}
commodities:
  gold:
    label: Gold
    match: [gold]
    evidence:
      - {layer: geo, weights: {basement: 1.0, others: 0.1}}
      - {layer: signal, direction: high, weight: 0.3}
      - *noise
  tin:
    label: Tin
    match: [tin]
    evidence:
      - {layer: signal, direction: high, weight: 0.5}
      - *noise
"""


def _grid() -> Grid:
    mask = np.ones((SIZE, SIZE), dtype=bool)
    mask[:4, :4] = False  # a corner outside the study area
    transform = Affine(RES_M, 0.0, 0.0, 0.0, -RES_M, SIZE * RES_M)
    return Grid(ZIM_CRS, transform, SIZE, SIZE, RES_M, mask)


def _stack(seed: int = 0) -> tuple[FeatureStack, np.ndarray, np.ndarray]:
    """A signal layer that predicts the deposits, an uninformative noise layer, and the deposit cells."""
    grid = _grid()
    rng = np.random.default_rng(seed)
    signal, noise = rng.random(grid.shape), rng.random(grid.shape)
    hit = (signal + SIGNAL_NOISE * rng.standard_normal(grid.shape) > SIGNAL_THRESHOLD) & grid.mask
    geo = np.where(np.arange(SIZE)[None, :] < COVER_STRIP_COLS, COVER_CLASS, 0) * np.ones((SIZE, 1))
    layers = {"signal": np.where(grid.mask, signal, np.nan).astype("float32"),
              "noise": np.where(grid.mask, noise, np.nan).astype("float32"),
              "geo_class": np.where(grid.mask, geo, np.nan).astype("float32")}
    rows, cols = np.nonzero(hit)
    return FeatureStack(grid=grid, layers=layers), rows, cols


def _spec(signal_w: float, noise_w: float) -> dict:
    """Two-entry evidence list; the noise entry is nested like a `*mafic` alias."""
    return {"label": "Test", "match": ["gold"],
            "evidence": [{"layer": "signal", "direction": "high", "weight": signal_w},
                         [{"layer": "noise", "direction": "high", "weight": noise_w}]]}


def _occurrences(grid: Grid, rows: np.ndarray, cols: np.ndarray) -> gpd.GeoDataFrame:
    """Gold at every deposit cell centre, plus a few tin points sharing the first gold cells."""
    xs, ys = grid.transform @ (cols + 0.5, rows + 0.5)
    all_x = np.concatenate([xs, xs[:N_TIN] + 1.0])
    all_y = np.concatenate([ys, ys[:N_TIN] - 1.0])
    names = ["Gold"] * len(xs) + ["Tin, Tantalum"] * N_TIN
    return gpd.GeoDataFrame({"commodities": names}, geometry=gpd.points_from_xy(all_x, all_y), crs=ZIM_CRS)


# ---------- folds and fast AUC ----------

def test_block_folds_keep_each_block_in_one_fold_and_are_deterministic():
    stack, _, _ = _stack()
    fold = tuning.block_folds(stack, 50, 5)
    blocks = models._block_ids(stack, 50)
    assert fold.shape == blocks.shape
    assert all(np.unique(fold[blocks == b]).size == 1 for b in np.unique(blocks))
    assert set(np.unique(fold).tolist()) == set(range(5))
    assert np.array_equal(fold, tuning.block_folds(stack, 50, 5))


def test_fold_aucs_match_success_rate_on_every_subset():
    rng = np.random.default_rng(1)
    n, folds = 2000, 5
    scores = np.round(rng.random(n), 2)                       # coarse values -> many ties
    scores[rng.choice(n, 50, replace=False)] = np.nan
    fold = rng.integers(0, folds, n)
    points = rng.choice(n, 60, replace=False)
    res = tuning.fold_aucs(scores, fold, points, folds)
    valid = points[np.isfinite(scores[points])]
    for k in range(folds):
        held, train = valid[fold[valid] == k], valid[fold[valid] != k]
        assert res.test[k] == pytest.approx(success_rate(scores[fold == k], scores[held]).auc)
        assert res.train[k] == pytest.approx(success_rate(scores[fold != k], scores[train]).auc)
    assert res.overall == pytest.approx(success_rate(scores, scores[points]).auc)


def test_fold_aucs_leave_folds_without_points_undefined():
    scores = np.array([0.1, 0.9, 0.5, 0.7])
    res = tuning.fold_aucs(scores, np.array([0, 0, 1, 1]), np.array([1]), 2)
    assert res.test[0] == pytest.approx(0.75) and math.isnan(res.test[1])
    assert math.isnan(res.train[0]) and res.train[1] == pytest.approx(0.75)
    assert res.overall == pytest.approx(0.875)
    empty = tuning.fold_aucs(np.array([np.nan, 0.2]), np.array([0, 1]), np.array([0]), 2)
    assert np.isnan(empty.test).all() and np.isnan(empty.train).all() and math.isnan(empty.overall)


# ---------- search pieces ----------

def test_candidate_weights_start_at_the_prior_and_stay_in_range():
    cands = tuning.candidate_weights((0.2, 1.0, 0.7), 50, np.random.default_rng(0))
    assert cands.shape == (50, 3)
    assert np.array_equal(cands[0], [0.2, 1.0, 0.7])
    rest = cands[1:]
    assert rest.min() >= tuning.WEIGHT_MIN and rest.max() <= tuning.WEIGHT_MAX
    assert np.allclose(rest, np.round(rest, tuning.WEIGHT_DECIMALS))


def test_accepts_needs_the_minimum_gain():
    assert tuning.accepts(0.72 - 0.70)       # float error lands just under 0.02
    assert not tuning.accepts(0.019)
    assert not tuning.accepts(math.nan)


# ---------- tune_commodity ----------

def test_tune_commodity_improves_a_badly_weighted_prior():
    stack, rows, cols = _stack()
    res = tuning.tune_commodity(_spec(0.3, 1.0), stack, rows, cols, gamma=0.8)
    assert res.accepted and res.gain >= tuning.MIN_GAIN
    assert res.tuned_auc > res.prior_auc
    assert res.prior_weights == (0.3, 1.0)
    assert res.weights == res.refit_weights != res.prior_weights
    assert all(tuning.WEIGHT_MIN <= w <= tuning.WEIGHT_MAX for w in res.weights)
    assert res.weights[0] > res.prior_weights[0] and res.weights[1] < res.prior_weights[1]
    assert len(res.fold_prior_auc) == len(res.fold_tuned_auc) == 5
    assert res.n_points == len(rows)


def test_tune_commodity_keeps_prior_when_weights_cannot_change_the_ranking():
    stack, rows, cols = _stack()
    spec = {"evidence": [{"layer": "signal", "direction": "high", "weight": 0.55}]}
    res = tuning.tune_commodity(spec, stack, rows, cols, gamma=0.8)
    assert not res.accepted
    assert res.weights == res.prior_weights == (0.55,)
    assert res.gain == pytest.approx(0.0, abs=1e-12)


def test_tune_commodity_is_deterministic_and_leaves_inputs_untouched():
    stack, rows, cols = _stack()
    spec = _spec(0.3, 1.0)
    spec_before = copy.deepcopy(spec)
    layers_before = {k: v.copy() for k, v in stack.layers.items()}
    first = tuning.tune_commodity(spec, stack, rows, cols, gamma=0.8)
    assert tuning.tune_commodity(spec, stack, rows, cols, gamma=0.8) == first
    assert spec == spec_before
    assert all(np.array_equal(stack.layers[k], v, equal_nan=True) for k, v in layers_before.items())


def test_search_sample_does_not_change_held_out_scoring(monkeypatch):
    stack, rows, cols = _stack()
    full = tuning.tune_commodity(_spec(0.3, 1.0), stack, rows, cols, gamma=0.8)
    monkeypatch.setattr(tuning, "MAX_SEARCH_CELLS", 500)
    sampled = tuning.tune_commodity(_spec(0.3, 1.0), stack, rows, cols, gamma=0.8)
    assert sampled.prior_auc == pytest.approx(full.prior_auc)   # held-out AUCs use every cell
    assert sampled.accepted


@pytest.mark.parametrize("kwargs", [{"max_evals": 0}, {"folds": 1}, {"block_km": 0}])
def test_tune_commodity_rejects_bad_arguments(kwargs):
    stack, rows, cols = _stack()
    with pytest.raises(ValueError):
        tuning.tune_commodity(_spec(0.3, 1.0), stack, rows, cols, gamma=0.8, **kwargs)
    with pytest.raises(ValueError, match="same length"):
        tuning.tune_commodity(_spec(0.3, 1.0), stack, rows, cols[:-1], gamma=0.8)


# ---------- tuned config ----------

def test_tuned_config_maps_flattened_weights_without_touching_shared_anchors():
    cfg = yaml.safe_load(CFG_YAML)
    before = copy.deepcopy(cfg)
    out = tuning.tuned_config(cfg, {"gold": (0.9, 0.8, 0.4)})
    gold = out["commodities"]["gold"]["evidence"]
    assert [e["layer"] for e in gold] == ["geo", "signal", "noise"]
    assert [e["weight"] for e in gold] == [0.9, 0.8, 0.4]
    assert gold[0]["weights"] == {"basement": 1.0, "others": 0.1}
    assert models._flatten(out["commodities"]["tin"]["evidence"])[1]["weight"] == 1.0
    assert out["noise_terrain"][0]["weight"] == 1.0
    assert cfg == before


def test_tuned_config_rejects_mismatched_weights_and_unknown_commodities():
    cfg = yaml.safe_load(CFG_YAML)
    with pytest.raises(ValueError, match="3 evidence entries"):
        tuning.tuned_config(cfg, {"gold": (0.9, 0.8)})
    with pytest.raises(ValueError, match="unknown"):
        tuning.tuned_config(cfg, {"silver": (0.5,)})


# ---------- run_tuning ----------

def test_run_tuning_writes_report_and_tuned_config(tmp_path: Path):
    stack, rows, cols = _stack()
    cfg = yaml.safe_load(CFG_YAML)
    before = copy.deepcopy(cfg)
    config_out = tmp_path / "config" / "commodities.tuned.yaml"
    report = tuning.run_tuning(cfg, stack, _occurrences(stack.grid, rows, cols), stack.grid,
                               tmp_path / "out", config_out)
    gold, tin = report["commodities"]["gold"], report["commodities"]["tin"]
    assert gold["status"] == "accepted" and gold["tuned_auc"] > gold["prior_auc"]
    assert gold["points"] == len(rows) and gold["folds_scored"] == 5
    assert tin["status"] == "skipped" and tin["points"] == N_TIN
    assert json.loads((tmp_path / "out" / tuning.REPORT_NAME).read_text()) == report
    text = config_out.read_text()
    assert "&id" not in text and "*id" not in text
    tuned = yaml.safe_load(text)
    gold_entries = tuned["commodities"]["gold"]["evidence"]
    assert [e["layer"] for e in gold_entries] == [w["layer"] for w in gold["weights"]]
    assert [e["weight"] for e in gold_entries] == [w["final"] for w in gold["weights"]]
    assert [e["weight"] for e in models._flatten(tuned["commodities"]["tin"]["evidence"])] == [0.5, 1.0]
    assert tuned["gamma"] == 0.8 and cfg == before


def test_run_tuning_filters_commodities_and_rejects_unknown_keys(tmp_path: Path):
    stack, rows, cols = _stack()
    occ = _occurrences(stack.grid, rows, cols)
    cfg = yaml.safe_load(CFG_YAML)
    report = tuning.run_tuning(cfg, stack, occ, stack.grid, tmp_path, tmp_path / "t.yaml", only=["tin"])
    assert list(report["commodities"]) == ["tin"]
    with pytest.raises(ValueError, match="unknown commodity"):
        tuning.run_tuning(cfg, stack, occ, stack.grid, tmp_path, tmp_path / "t.yaml", only=["gld"])


def test_run_tuning_never_overwrites_the_expert_config(tmp_path: Path, monkeypatch):
    assert tuning.PROTECTED_CONFIG.name == "commodities.yaml"
    assert tuning.DEFAULT_CONFIG_OUT != tuning.PROTECTED_CONFIG
    protected = tmp_path / "commodities.yaml"
    protected.write_text("expert: true\n")
    monkeypatch.setattr(tuning, "PROTECTED_CONFIG", protected)
    stack, rows, cols = _stack()
    with pytest.raises(ValueError, match="overwrite"):
        tuning.run_tuning(yaml.safe_load(CFG_YAML), stack, _occurrences(stack.grid, rows, cols),
                          stack.grid, tmp_path / "out", protected)
    assert protected.read_text() == "expert: true\n"
    assert not (tmp_path / "out").exists()
