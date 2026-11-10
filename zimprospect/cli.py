"""Command line: `python -m zimprospect run [--res 500] [--only gold,chromite]`."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import features, occurrences, report, tuning, userdata, webmap, zoom
from .features import FeatureStack
from .s2 import S2Settings
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


def _stack(cfg: dict, ucfg: userdata.UserDataConfig, res_m: float, refresh: bool) -> FeatureStack:
    """Cached open-data layers plus any user layers (user layers are never cached)."""
    s2 = S2Settings.from_mapping(cfg["sentinel2"]) if cfg.get("sentinel2") else None
    stack = features.build(res_m, refresh=refresh, s2=s2)
    extra = userdata.user_layers(stack.grid, ucfg)
    if extra:
        log.info("user layers: %s", ", ".join(sorted(extra)))
    return FeatureStack(stack.grid, {**stack.layers, **extra})


def run(res_m: float, only: list[str] | None, config_path: Path, out_dir: Path,
        refresh: bool = False) -> None:
    cfg = yaml.safe_load(config_path.read_text())
    ucfg = userdata.load_config(None)
    names = userdata.class_names(ucfg)
    stack = _stack(cfg, ucfg, res_m, refresh)
    grid = stack.grid
    occ = occurrences.load_all(ucfg, refresh=refresh)
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
        fz = fuzzy_model(spec, stack, cfg["gamma"], class_names=names)
        entry: dict = {"label": spec["label"], "occurrences": int(len(pts)), "cells": int(len(rows)),
                       "fuzzy": _validate(fz, rows, cols)}
        if len(rows) >= cfg["min_training_points"]:
            rf = random_forest(stack, rows, cols, cfg["spatial_block_km"], cfg["background_ratio"],
                               class_names=names)
            final = percentile_rank(ensemble(fz, rf.full_map))  # a true 0-1 percentile, like fuzzy-only maps
            entry["rf_cv"] = _validate(rf.oof_map, rows, cols)
            entry["ensemble_cv"] = _validate(ensemble(fz, rf.oof_map), rows, cols)
            entry["rf_top_features"] = dict(list(rf.importances.items())[:6])
            entry["method"] = "ensemble (fuzzy + random forest)"
            report.write_geotiff(out_dir / "maps" / f"{key}_rf.tif", rf.full_map, grid)
        else:
            final = percentile_rank(fz)
            entry["method"] = "fuzzy only (too few training points)"
        if spec.get(tuning.TUNED_MARKER):
            entry["optimistic"] = True  # fuzzy weights were fitted on these points; see tuning_report.json
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
    frames = [t.assign(commodity=k) for k, t in targets.items() if not t.empty]
    all_targets = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    all_targets.to_csv(out_dir / "targets_all.csv", index=False)
    report.overview_figure(out_dir / "overview.png", finals, labels, grid, known)
    webmap.national_map(out_dir / "prospectivity_map.html", finals, labels, grid, occ, targets, summary, known)
    log.info("outputs written to %s", out_dir)


def _out_default() -> Path:
    return Path(os.environ.get("ZIMPROSPECT_OUT_DIR", ROOT / "outputs"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zimprospect", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="build features, run models, write maps/targets/report")
    p.add_argument("--res", type=float, default=500.0, help="grid resolution in metres (default 500)")
    p.add_argument("--only", type=str, default=None, help="comma-separated commodity keys")
    p.add_argument("--config", type=Path, default=ROOT / "config" / "commodities.yaml")
    p.add_argument("--out", type=Path, default=_out_default())
    p.add_argument("--refresh", action="store_true", help="rebuild cached feature layers")
    t = sub.add_parser("tune", help="tune fuzzy weights with spatial-block CV (writes a separate config)")
    t.add_argument("--res", type=float, default=500.0)
    t.add_argument("--only", type=str, default=None, help="comma-separated commodity keys")
    t.add_argument("--config", type=Path, default=ROOT / "config" / "commodities.yaml")
    t.add_argument("--config-out", type=Path, default=tuning.DEFAULT_CONFIG_OUT)
    t.add_argument("--out", type=Path, default=_out_default())
    z = sub.add_parser("zoom", help="10-20 m Sentinel-2 anomalies inside the top targets")
    z.add_argument("--commodity", required=True, help="commodity key, e.g. gold")
    z.add_argument("--top", type=int, default=10, help="number of targets to zoom into (default 10)")
    z.add_argument("--box-km", type=float, default=8.0, help="box side in km (default 8)")
    z.add_argument("--res", type=float, default=20.0, help="pixel size in metres (default 20)")
    z.add_argument("--config", type=Path, default=ROOT / "config" / "commodities.yaml")
    z.add_argument("--out", type=Path, default=_out_default(), help="folder holding targets_*.csv")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("rasterio").setLevel(logging.ERROR)
    if args.cmd == "run":
        only = [s.strip() for s in args.only.split(",")] if args.only else None
        run(args.res, only, args.config, args.out, refresh=args.refresh)
    elif args.cmd == "tune":
        only = [s.strip() for s in args.only.split(",")] if args.only else None
        cfg = yaml.safe_load(args.config.read_text())
        ucfg = userdata.load_config(None)
        stack = _stack(cfg, ucfg, args.res, refresh=False)
        args.out.mkdir(parents=True, exist_ok=True)
        report_ = tuning.run_tuning(cfg, stack, occurrences.load_all(ucfg), stack.grid, args.out, args.config_out,
                                    only=only, class_names=userdata.class_names(ucfg))
        log.info("tuning finished for %d commodities -> %s", len(report_), args.config_out)
    elif args.cmd == "zoom":
        if args.top < 1 or args.box_km <= 0 or args.res <= 0:
            raise SystemExit("--top, --box-km and --res must be positive")
        cfg = yaml.safe_load(args.config.read_text())
        summary = zoom.run_zoom(args.commodity, cfg, args.out, args.out / "zoom", args.top, args.box_km, args.res)
        log.info("zoom finished: %d targets -> %s", len(summary), args.out / "zoom" / args.commodity)
