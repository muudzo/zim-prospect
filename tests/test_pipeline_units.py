import geopandas as gpd
import numpy as np
from shapely.geometry import Point, box

from zimprospect.features import FeatureStack, smooth_nan
from zimprospect.grid import make_grid
from zimprospect.models import ensemble, evidence_layer, percentile_rank
from zimprospect.occurrences import select
from zimprospect.targets import extract_targets


def _grid():
    return make_grid(gpd.GeoDataFrame(geometry=[box(29.0, -19.5, 29.5, -19.0)], crs=4326), 1000, 0)


def test_select_matches_whole_words_only():
    occ = gpd.GeoDataFrame({"commodities": ["Platinum, Palladium", "Tin, Tantalum", "Gold"]},
                           geometry=[Point(0, 0)] * 3, crs=4326)
    assert list(select(occ, ["tin"])["commodities"]) == ["Tin, Tantalum"]
    assert len(select(occ, ["platinum", "gold"])) == 2


def test_percentile_rank_and_ensemble_are_bounded_and_nan_safe():
    a = np.array([[0.1, 0.5], [0.9, np.nan]])
    r = percentile_rank(a)
    assert r[1, 0] == 1.0 and r[0, 0] == 0.0 and np.isnan(r[1, 1])
    e = ensemble(a, 1 - a)
    assert np.allclose(e[np.isfinite(e)], 0.5) and np.isnan(e[1, 1])


def test_evidence_layer_low_direction_inverts():
    g = _grid()
    data = np.where(g.mask, np.arange(g.mask.size).reshape(g.shape), np.nan).astype("float32")
    stack = FeatureStack(grid=g, layers={"s2_albedo": data})
    hi = evidence_layer({"layer": "s2_albedo", "direction": "high"}, stack)
    lo = evidence_layer({"layer": "s2_albedo", "direction": "low"}, stack)
    assert np.allclose(hi[g.mask] + lo[g.mask], 1.0)


def test_smooth_nan_ignores_gaps():
    a = np.ones((5, 5), dtype="float32")
    a[2, 2] = np.nan
    assert np.allclose(smooth_nan(a, 3), 1.0)


def test_extract_targets_finds_hotspot_and_flags_greenfield():
    g = _grid()
    score = np.where(g.mask, 0.1, np.nan)
    score[10:13, 10:13] = 0.9          # hotspot with a known deposit
    score[40:42, 40:42] = 0.8          # hotspot far from any known deposit
    t = extract_targets(score, g, np.array([11]), np.array([11]), top_frac=0.01)
    assert list(t["status"]) == ["known district", "greenfield"]
    assert t.loc[0, "peak_score"] == 0.9 and t.loc[0, "area_km2"] == 9.0


def test_percentile_rank_ties_share_rank():
    r = percentile_rank(np.array([0.0, 0.0, 0.0, 1.0]))
    assert np.allclose(r[:3], r[0]) and r[3] == 1.0
