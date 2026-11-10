import numpy as np
import pytest

from zimprospect.fuzzy import class_lookup, fuzzy_gamma, linear_decay, rescale_percentile
from zimprospect.validate import success_rate


def test_linear_decay_full_inside_d0_and_floor_beyond_d1():
    d = np.array([0.0, 1.0, 5.5, 10.0, 50.0, np.nan])
    out = linear_decay(d, d0=1, d1=10, floor=0.2)
    assert out[0] == 1.0 and out[1] == 1.0
    assert out[2] == pytest.approx(0.6)
    assert out[3] == pytest.approx(0.2) and out[4] == pytest.approx(0.2)
    assert np.isnan(out[5])


def test_linear_decay_rejects_bad_params():
    with pytest.raises(ValueError):
        linear_decay(np.array([1.0]), d0=5, d1=2, floor=0.2)


def test_class_lookup_maps_classes_fallback_and_nodata():
    classes = ["greenstone", "granitoid", "kalahari"]
    r = np.array([[0, 1], [2, -1]])
    out = class_lookup(r, classes, {"greenstone": 0.9, "others": 0.05})
    assert out[0, 0] == 0.9 and out[0, 1] == 0.05 and out[1, 0] == 0.05
    assert np.isnan(out[1, 1])


def test_class_lookup_rejects_unknown_class():
    with pytest.raises(ValueError):
        class_lookup(np.array([0]), ["a"], {"b": 1.0})


def test_fuzzy_gamma_bounds_and_nan_handling():
    a = np.array([1.0, 0.5, np.nan, np.nan])
    b = np.array([1.0, 0.5, 0.4, np.nan])
    out = fuzzy_gamma([a, b], gamma=0.8)
    assert out[0] == pytest.approx(1.0)
    # gamma result lies between product (0.25) and sum (0.75)
    assert 0.25 < out[1] < 0.75
    assert out[2] == pytest.approx(0.4)  # single valid layer -> its own value
    assert np.isnan(out[3])


def test_fuzzy_gamma_is_monotonic_in_evidence():
    low = fuzzy_gamma([np.array([0.3]), np.array([0.5])])
    high = fuzzy_gamma([np.array([0.6]), np.array([0.5])])
    assert high[0] > low[0]


def test_rescale_percentile_range():
    out = rescale_percentile(np.arange(100.0))
    assert np.nanmin(out) == 0.0 and np.nanmax(out) == 1.0


def test_success_rate_perfect_and_random_models():
    rng = np.random.default_rng(0)
    scores = rng.random(10_000)
    top = np.sort(scores)[-20:]  # deposits at the highest-scoring cells
    res = success_rate(scores, top)
    assert res.auc > 0.99 and res.area_for_80pct < 0.01 and res.captured_in_top10pct == 1.0

    rand = success_rate(scores, rng.random(2_000))
    assert 0.45 < rand.auc < 0.55
    assert 0.7 < rand.area_for_80pct < 0.9
