"""Command line: `python -m zimprospect run [--res 500] [--only gold,chromite]`."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import features, occurrences, report
from .models import ensemble, fuzzy_model, percentile_rank, random_forest
from .targets import extract_targets
from .validate import success_rate

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("zimprospect")


def _validate(score: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> dict | None:
    if len(rows) < 3:
        return None
    res = success_rate(score, score[rows, cols])
    return {"n": res.n_points, "auc": round(res.auc, 3), "area_for_50pct": round(res.area_for_50pct, 3),
            "area_for_80pct": round(res.area_for_80pct, 3),
            "captured_in_top10pct": round(res.captured_in_top10pct, 3),
            "_curve": (res.curve_area, res.curve_captured, res.auc)}


def run(res_m: float, only: list[str] | None, config_path: Path, out_dir: Path) -> None:
    cfg = yaml.safe_load(config_path.read_text())
    stack = features.build(res_m)
    grid = stack.grid
    occ = occurrences.load_all()
    log.info("%d known occurrences loaded", len(occ))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "maps").mkdir(exist_ok=True)

    finals, labels, targets, summary, curves, known = {}, {}, {}, {}, {}, {}
    for key, spec in cfg["commodities"].items():
        if only and key not in only:
            continue
        labels[key] = spec["label"]
        pts = occurrences.select(occ, spec["match"])
        rows, cols = occurrences.decluster(pts, grid)
        fz = fuzzy_model(spec, stack, cfg["gamma"])
        entry: dict = {"label": spec["label"], "occurrences": int(len(pts)), "cells": int(len(rows)),
                       "fuzzy": _validate(fz, rows, cols)}
        if len(rows) >= cfg["min_training_points"]:
            rf = random_forest(stack, rows, cols, cfg["spatial_block_km"], cfg["background_ratio"])
            final = ensemble(fz, rf.full_map)
            entry["rf_cv"] = _validate(rf.oof_map, rows, cols)
            entry["ensemble_cv"] = _validate(ensemble(fz, rf.oof_map), rows, cols)
            entry["rf_top_features"] = dict(list(rf.importances.items())[:6])
            entry["method"] = "ensemble (fuzzy + random forest)"
            report.write_geotiff(out_dir / "maps" / f"{key}_rf.tif", rf.full_map, grid)
        else:
            final = percentile_rank(fz)
            entry["method"] = "fuzzy only (too few training points)"
        best = entry.get("ensemble_cv") or entry["fuzzy"]
        if best:
            curves[spec["label"]] = best["_curve"]
        report.write_geotiff(out_dir / "maps" / f"{key}_fuzzy.tif", fz, grid)
        report.write_geotiff(out_dir / "maps" / f"{key}_final.tif", final, grid)
        finals[key] = final
        known[key] = pts
        targets[key] = extract_targets(final, grid, rows, cols)
        targets[key].to_csv(out_dir / f"targets_{key}.csv", index=False)
        summary[key] = entry
        log.info("%-13s %s | %s", key, entry["method"], {k: v for k, v in (best or {}).items() if k != "_curve"})

    clean = {k: {kk: ({x: y for x, y in vv.items() if x != "_curve"} if isinstance(vv, dict) else vv)
                 for kk, vv in v.items()} for k, v in summary.items()}
    (out_dir / "validation.json").write_text(json.dumps(clean, indent=2))
    report.success_curves(out_dir / "success_curves.png", curves)
    all_targets = pd.concat([t.assign(commodity=k) for k, t in targets.items() if not t.empty],
                            ignore_index=True)
    all_targets.to_csv(out_dir / "targets_all.csv", index=False)
    report.overview_figure(out_dir / "overview.png", finals, labels, grid, known)
    report.interactive_map(out_dir / "prospectivity_map.html", finals, labels, grid, occ, targets)
    log.info("outputs written to %s", out_dir)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="zimprospect", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="build features, run models, write maps/targets/report")
    p.add_argument("--res", type=float, default=500.0, help="grid resolution in metres (default 500)")
    p.add_argument("--only", type=str, default=None, help="comma-separated commodity keys")
    p.add_argument("--config", type=Path, default=ROOT / "config" / "commodities.yaml")
    p.add_argument("--out", type=Path, default=ROOT / "outputs")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("rasterio").setLevel(logging.ERROR)
    only = [s.strip() for s in args.only.split(",")] if args.only else None
    run(args.res, only, args.config, args.out)
