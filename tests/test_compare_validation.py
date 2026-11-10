import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "compare_validation", Path(__file__).resolve().parent.parent / "scripts" / "compare_validation.py")
cv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cv)


def test_identical_files_pass(tmp_path):
    data = {"gold": {"fuzzy": {"auc": 0.6}, "ensemble_cv": {"auc": 0.72}}, "coal": {"fuzzy": {"auc": 0.9}}}
    path = tmp_path / "v.json"
    path.write_text(json.dumps(data))
    assert cv.main([str(path), str(path)]) == 0


def test_drop_beyond_tolerance_fails():
    base = {"gold": {"ensemble_cv": {"auc": 0.72}}}
    new = {"gold": {"ensemble_cv": {"auc": 0.69}}}
    lines, ok = cv.compare(base, new, 0.02)
    assert not ok and "worse" in lines[-1]


def test_small_drop_within_tolerance_passes():
    _, ok = cv.compare({"c": {"fuzzy": {"auc": 0.9}}}, {"c": {"fuzzy": {"auc": 0.89}}}, 0.02)
    assert ok
