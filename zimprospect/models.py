"""Per-commodity prospectivity models: fuzzy (knowledge-driven) and random forest (data-driven)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.stats import rankdata
from sklearn.ensemble import RandomForestClassifier

from . import sources
from .features import FeatureStack
from .fuzzy import class_lookup, fuzzy_gamma, linear_decay, rescale_percentile
from .userdata import LITH_LAYER, PREFIX as USER_PREFIX

log = logging.getLogger(__name__)

from .features import CONTEXT_KM, CONTEXT_LAYERS  # noqa: E402

CONTINUOUS = (["dist_karoo_margin", "elev", "slope", "rough",
               "s2_ferric", "s2_ferrous", "s2_clay", "s2_ndvi", "s2_albedo"]
              + [f"{n}_ctx{km}" for n in CONTEXT_LAYERS for km in CONTEXT_KM])
RNG_SEED = 42
GEO_CLASS_LAYER = "geo_class"
# User layers that hold class codes; they are one-hot encoded even without a class list.
USER_CLASS_LAYERS = frozenset({LITH_LAYER})
EMPTY_FEATURE_FILL = 0.0   # an all-NaN user layer carries no information but must not break the RF

ClassNames = Mapping[str, Sequence[str]]


@dataclass(frozen=True)
class RFResult:
    full_map: np.ndarray       # model fitted on all points, predicted everywhere
    oof_map: np.ndarray        # out-of-fold predictions (each cell predicted by a model blind to its block)
    importances: dict[str, float]
    n_positive: int


# ---------- knowledge-driven ----------

def _flatten(entries: list[Any]) -> list[dict]:
    out: list[dict] = []
    for e in entries:
        out.extend(_flatten(e) if isinstance(e, list) else [e])
    return out


def _all_class_names(class_names: ClassNames | None) -> dict[str, Sequence[str]]:
    return {GEO_CLASS_LAYER: sources.GEOLOGY_CLASSES, **(class_names or {})}


def _class_membership(name: str, data: np.ndarray, weights: Mapping[str, float],
                      class_names: ClassNames | None) -> np.ndarray:
    names = _all_class_names(class_names).get(name)
    if names is None:
        raise KeyError(f"layer '{name}' has class weights but no class list; pass class_names "
                       "(e.g. userdata.class_names(cfg)) to evidence_layer / fuzzy_model")
    codes = np.nan_to_num(data, nan=-1).astype(int)
    top = int(codes.max(initial=-1))
    if top >= len(names):
        raise ValueError(f"layer '{name}' holds class code {top} but only {len(names)} class names are known")
    return class_lookup(codes, list(names), weights)


def evidence_layer(entry: dict, stack: FeatureStack,
                   class_names: ClassNames | None = None) -> np.ndarray:
    """Turn one config evidence entry into a 0-1 membership raster.

    `{layer: <class layer>, weights: {...}}` works for `geo` (alias of geo_class) and for any
    layer listed in `class_names` ({layer: [class name per code]}).
    """
    name = entry["layer"]
    if name == "geo":
        classes = np.nan_to_num(stack.layers["geo_class"], nan=-1).astype(int)
        return class_lookup(classes, sources.GEOLOGY_CLASSES, entry["weights"])
    if name not in stack.layers:
        raise KeyError(f"unknown evidence layer '{name}'")
    data = stack.layers[name]
    if "weights" in entry:
        return _class_membership(name, data, entry["weights"], class_names)
    if "d0" in entry:
        return linear_decay(data, entry["d0"], entry["d1"], entry["floor"])
    direction = entry.get("direction", "high")
    if direction not in ("high", "low"):
        raise ValueError(f"direction must be 'high' or 'low', got {direction!r}")
    scaled = rescale_percentile(np.where(stack.grid.mask, data, np.nan))
    return scaled if direction == "high" else 1.0 - scaled


def _available(entry: dict, stack: FeatureStack) -> bool:
    """False for an `optional: true` entry whose layer is not in the stack (e.g. no user data)."""
    name = entry["layer"]
    if entry.get("optional") and name != "geo" and name not in stack.layers:
        log.info("optional evidence layer '%s' is not in the stack; skipped", name)
        return False
    return True


def fuzzy_model(spec: dict, stack: FeatureStack, gamma: float,
                class_names: ClassNames | None = None) -> np.ndarray:
    entries = [e for e in _flatten(spec["evidence"]) if _available(e, stack)]
    layers = [evidence_layer(e, stack, class_names) for e in entries]
    weights = [float(e.get("weight", 1.0)) for e in entries]
    score = fuzzy_gamma(layers, gamma=gamma, weights=weights)
    return np.where(stack.grid.mask, score, np.nan)


# ---------- data-driven ----------

def _class_layers(stack: FeatureStack, class_names: ClassNames | None) -> list[tuple[str, list[str]]]:
    """(layer, class names) to one-hot encode: geo_class first, then user class layers."""
    known = _all_class_names(class_names)
    user = sorted(n for n in stack.layers if n.startswith(USER_PREFIX)
                  and (n in known or n in USER_CLASS_LAYERS))
    out = [(GEO_CLASS_LAYER, list(sources.GEOLOGY_CLASSES))]
    for name in user:
        if name in known:
            out.append((name, list(known[name])))
            continue
        codes = stack.layers[name][stack.grid.mask]
        top = int(np.nanmax(codes)) if np.isfinite(codes).any() else -1
        out.append((name, [str(i) for i in range(top + 1)]))
    return out


def _user_continuous(stack: FeatureStack, class_layers: list[tuple[str, list[str]]]) -> list[str]:
    categorical = {name for name, _ in class_layers}
    return sorted(n for n in stack.layers if n.startswith(USER_PREFIX) and n not in categorical)


def feature_matrix(stack: FeatureStack,
                   class_names: ClassNames | None = None) -> tuple[np.ndarray, list[str]]:
    """(n_cells_in_mask, n_features) matrix.

    Columns: CONTINUOUS, then every continuous `user_*` layer in the stack (sorted), then one-hot
    geology, then one-hot user class layers. With no user layers this is the original layout.
    """
    mask = stack.grid.mask
    class_layers = _class_layers(stack, class_names)
    cols, names = [], []
    for name in CONTINUOUS + _user_continuous(stack, class_layers):
        v = stack.layers[name][mask]
        fill = np.nanmedian(v) if np.isfinite(v).any() else EMPTY_FEATURE_FILL
        cols.append(np.where(np.isfinite(v), v, fill))
        names.append(name)
    for layer, classes in class_layers:
        codes = stack.layers[layer][mask]
        prefix = "geo" if layer == GEO_CLASS_LAYER else layer
        for i, cls in enumerate(classes):
            cols.append((codes == i).astype("float32"))
            names.append(f"{prefix}_{cls}")
    return np.column_stack(cols).astype("float32"), names


def _block_ids(stack: FeatureStack, block_km: float) -> np.ndarray:
    rows, cols = np.nonzero(stack.grid.mask)
    cells = max(int(block_km * 1000 / stack.grid.res_m), 1)
    return (rows // cells) * 10_000 + (cols // cells)


def random_forest(stack: FeatureStack, pos_rows: np.ndarray, pos_cols: np.ndarray,
                  block_km: float = 50, background_ratio: int = 20, folds: int = 5, *,
                  class_names: ClassNames | None = None) -> RFResult:
    """Presence/background random forest with spatial-block cross-validation.

    Features come from `feature_matrix(stack, class_names)`, so user layers join automatically.
    """
    X, names = feature_matrix(stack, class_names)
    mask = stack.grid.mask
    flat_index = np.full(mask.shape, -1, dtype=np.int64)
    flat_index[mask] = np.arange(mask.sum())
    pos = np.unique(flat_index[pos_rows, pos_cols])
    pos = pos[pos >= 0]
    rng = np.random.default_rng(RNG_SEED)
    candidates = np.setdiff1d(np.arange(X.shape[0]), pos)
    bg = rng.choice(candidates, size=min(len(pos) * background_ratio, len(candidates)), replace=False)
    train_idx = np.concatenate([pos, bg])
    y = np.concatenate([np.ones(len(pos)), np.zeros(len(bg))])

    blocks = _block_ids(stack, block_km)
    unique_blocks = np.unique(blocks)
    fold_of_block = dict(zip(unique_blocks, rng.permutation(len(unique_blocks)) % folds))
    cell_fold = np.vectorize(fold_of_block.get)(blocks)

    def new_rf() -> RandomForestClassifier:
        return RandomForestClassifier(n_estimators=300, min_samples_leaf=3, max_features="sqrt",
                                      class_weight="balanced_subsample", n_jobs=-1,
                                      random_state=RNG_SEED)

    oof = np.full(X.shape[0], np.nan)
    for k in range(folds):
        train_k = train_idx[cell_fold[train_idx] != k]
        y_k = y[cell_fold[train_idx] != k]
        if y_k.sum() < 3:
            continue
        model = new_rf().fit(X[train_k], y_k)
        held = np.nonzero(cell_fold == k)[0]
        oof[held] = model.predict_proba(X[held])[:, 1]

    final = new_rf().fit(X[train_idx], y)
    full = final.predict_proba(X)[:, 1]
    return RFResult(full_map=_unflatten(full, mask), oof_map=_unflatten(oof, mask),
                    importances=dict(sorted(zip(names, final.feature_importances_.round(4)),
                                            key=lambda kv: -kv[1])),
                    n_positive=int(len(pos)))


def _unflatten(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = np.full(mask.shape, np.nan, dtype="float32")
    out[mask] = values
    return out


# ---------- ensemble ----------

def percentile_rank(score: np.ndarray) -> np.ndarray:
    """Map scores to their 0-1 percentile rank among valid cells (ties share their average rank)."""
    out = np.full(score.shape, np.nan, dtype="float32")
    valid = np.isfinite(score)
    n = int(valid.sum())
    if n == 0:
        return out
    ranks = rankdata(score[valid], method="average")
    out[valid] = (ranks - 1) / max(n - 1, 1)
    return out


def ensemble(*maps: np.ndarray) -> np.ndarray:
    """Mean percentile rank of several maps (cells valid in no map stay NaN)."""
    ranked = np.stack([percentile_rank(m) for m in maps])
    count = np.isfinite(ranked).sum(axis=0)
    total = np.nansum(ranked, axis=0)
    return np.where(count > 0, total / np.maximum(count, 1), np.nan).astype("float32")
